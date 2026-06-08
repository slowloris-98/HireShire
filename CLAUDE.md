# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HireShire is a four-phase automated job search pipeline:
1. **Scraper** — fetches job listings from Greenhouse, Ashby, and Lever job board APIs
2. **Matcher** — scores jobs against a resume via LLM (Gemini / OpenAI / Anthropic)
3. **Tuner** — two-pass resume optimizer (evaluator critique → JSON project selector → code assembler → PDF compile)
4. **Applier** — fills out and submits job applications via browser automation (Playwright)

Each phase is fully independent: its own entrypoint script, `hireshire/<phase>/` subpackage, `config/<phase>.yaml`, and `data/<phase-output>/` directory.

## Commands

```bash
# Setup
pip install -r requirements.txt
playwright install chromium   # required for Phase 4 (Applier)

# Phase 1: Scrape job listings
python scraper.py

# Phase 2: Score jobs against resume (auto-reads latest scraper run)
python matcher.py

# Phase 3: Optimize resume per job (auto-reads latest matcher run)
python tuner.py                                    # pipeline mode (latest matcher run)
python tuner.py --run-id <id>                      # specific matcher run
python tuner.py --jd-file path/to/job.txt          # standalone (single job description)
python tuner.py --jd-file path/to/job.txt --resume-tex path/to/resume.tex

# Phase 4: Fill and submit applications (auto-reads latest matcher run)
python applier.py
python applier.py --run-id <id>   # specific matcher run
python applier.py --dry-run       # override config, never submit

# Orchestrator: runs phases 1–3 as a streaming pipeline on a schedule
python orchestrate.py --now        # run immediately, then every 4h
python orchestrate.py --once       # run exactly once
python orchestrate.py --interval 2 # custom interval in hours
python orchestrate.py --no-tuner   # scraper + matcher only (skip resume tuning)
python orchestrate.py --no-matcher # scraper only (skip scoring and tuning)
```

## Architecture

### Data Flow

Each phase writes output that the next phase reads automatically (always picks the latest timestamped directory):

```
Greenhouse API → data/scraped/<ts>/{manifest,company1,...}.json   [Job[]]
              → data/matches/<ts>/{manifest,shortlisted,rejected}.json  [MatchResult[]]
              → data/tuned/<ts>/<job_id>/{job_description.txt, critique.json, <Name>_Resume.tex, <Name>_Resume.pdf}
              → data/applied/{applied.json, screenshots/}   [ApplyRecord[]]

Orchestrator  → data/pipeline/<ts>/{pipeline_results.json, pipeline_results.csv}
                  (all shortlisted jobs; resume fields null when tuner was skipped/errored)
```

### Phase Internals

**All four phases** use `asyncio.run(main())`. Concurrency and rate-limiting knobs live in each phase's YAML config.

**Phase 2 (Matcher)** uses a `LLMBackend` Protocol in [hireshire/matcher/scorer.py](hireshire/matcher/scorer.py). Adding a new provider means implementing `score(job, resume_text) -> ScoringSchema`. The active backend is selected by the `LLM_PROVIDER` env var (`gemini` / `openai` / `anthropic`). A `title_filter` in `config/matcher.yaml` pre-filters jobs by title before LLM scoring (saves API calls). An optional `projects_path` markdown file is appended to the candidate profile for richer context (this key is also present in `config/tuner.yaml` but is no longer used by the tuner's optimizer — it has been superseded by `projects_bullets_path`). Results are written incrementally to `progress.jsonl` so a mid-run crash can be resumed without re-scoring already-processed jobs.

**Phase 3 (Tuner)** runs two sequential LLM passes per job:
1. `ResumeEvaluator` — recruiter-perspective critique against the full resume LaTeX → `EvaluatorResult` Pydantic model
2. `ResumeOptimizer` — compact JSON selector: reads critique + a project roster (titles/descriptions only) and returns a `SelectionResult` (which projects to include + per-bullet keyword adjustments). Then `Assembler` ([hireshire/tuner/assembler.py](hireshire/tuner/assembler.py)) substitutes selected entries into a LaTeX template via pure code using pre-authored bullets from `projects_bullets.yaml`.

After assembly, [hireshire/tuner/store.py](hireshire/tuner/store.py) compiles with `pdflatex`. If the PDF exceeds one page, a code-based bullet-removal loop (in `tuner.py`) decrements bullet counts one at a time and reassembles until it fits — no LLM call involved in trimming. The tuner supports separate `evaluator_provider`/`optimizer_provider` backends (defaults to `LLM_PROVIDER`). Setting `optimizer_provider: claude_code` routes the selector call through a local Claude CLI subprocess instead of the API.

**Phase 4 (Applier)** drives a real browser via the **browser-use** library (Playwright). Each job goes through `QuestionAnswerer` (LLM generates answers to application questions) then `FormFiller` (browser-use agent fills and submits the form). Screenshots are saved per application.

### Key Models

| Model | Defined in | Used by |
|-------|-----------|---------|
| `Job` | `hireshire/models/job.py` | Scraper → Matcher |
| `MatchResult` | `hireshire/matcher/scorer.py` | Matcher → Applier / Tuner |
| `ScoringSchema` | `hireshire/matcher/scorer.py` | Matcher (LLM output) |
| `EvaluatorResult` | `hireshire/tuner/evaluator.py` | Tuner Pass 1 → Pass 2 |
| `SelectionResult` | `hireshire/tuner/optimizer.py` | Tuner Pass 2 → Assembler |
| `ApplyRecord` | `hireshire/applier/store.py` | Applier output |

`Job.content_text` auto-strips HTML via a Pydantic validator on ingest.

### Environment Variables

Configured in `.env` (see `.env.example`):
- `LLM_PROVIDER` — `gemini` / `openai` / `anthropic`
- `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — whichever provider(s) are used
- Tuner can use different providers for evaluator vs optimizer via `evaluator_provider` / `optimizer_provider` in `config/tuner.yaml`

### Scraper

`config/scraper.yaml` supports three company entry types — use exactly one token key per company:
- `greenhouse_token` → Greenhouse Job Board API
- `ashby_token` → Ashby Job Board API
- `lever_token` → Lever Job Postings API

The `location_filter` list does a case-insensitive substring match against `job.location.name` and `job.offices[].location`. All three APIs lack server-side location filtering, so this is applied client-side after fetching. Empty list = no filter.

### Rate Limiting

Each phase manages its own throttling:
- **Scraper**: semaphore via `settings.concurrency` in `config/scraper.yaml` (default 10)
- **Matcher**: semaphore + `request_interval_s` delay (default 13 s ≈ 4.6 RPM, safe for free-tier Gemini)
- **Applier**: sequential, one form at a time, `inter_job_delay_s` between jobs
- **Tuner**: sequential per job, `request_interval_s` between LLM calls

The shared HTTP client ([hireshire/http_client.py](hireshire/http_client.py)) handles exponential-backoff retries for 429/5xx automatically.

### Orchestrator

`orchestrate.py` wires Phases 1–3 as a streaming pipeline using asyncio queues:

```
scraper.main(out_queue=q1) ──► q1[(board_token, list[Job])] ──► matcher.main(in_queue=q1, out_queue=q2)
                                                                          │
                                                              q2[(MatchResult, Job)] ──► tuner.main(in_queue=q2, out_queue=q3)
                                                                                                   │
                                                                             q3[result dict] ──► _track_results → data/pipeline/<run_id>/
```

Each script's `main()` accepts optional `in_queue`, `out_queue`, and `quiet` parameters. When `quiet=True` (set by orchestrator), all Rich UI is suppressed and Python `logging` is used instead. This allows the orchestrator to configure logging once (console + rotating file at `logs/orchestrate.log`) without conflict.

**Pipeline results** are written to `data/pipeline/<run_id>/pipeline_results.{json,csv}` each run. Every shortlisted job appears in the results regardless of tuner outcome — the `tuner_status` field (`"tuned"` / `"skipped"` / `"error"`) indicates what happened, and `resume_tex`/`resume_pdf` are `null` when tuning didn't complete.

**Skip flags** — `--no-tuner` replaces the tuner with a passthrough that marks all jobs `tuner_status: "skipped"` and writes results with null resume fields. `--no-matcher` runs the scraper only and writes an empty results file.

Phase 4 (Applier) is intentionally excluded from the orchestrator — it remains manual.
