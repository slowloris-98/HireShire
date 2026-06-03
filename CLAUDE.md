# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HireShire is a four-phase automated job search pipeline:
1. **Scraper** — fetches job listings from the Greenhouse Job Board API
2. **Matcher** — scores jobs against a resume via LLM (Gemini / OpenAI / Anthropic)
3. **Applier** — fills out and submits job applications via browser automation (Playwright)
4. **Tuner** — two-pass LLM resume optimizer (evaluator critique → LaTeX optimizer → PDF compile)

Each phase is fully independent: its own entrypoint script, `hireshire/<phase>/` subpackage, `config/<phase>.yaml`, and `data/<phase-output>/` directory.

## Commands

```bash
# Setup
pip install -r requirements.txt
playwright install chromium   # required for Phase 3

# Phase 1: Scrape job listings
python scraper.py

# Phase 2: Score jobs against resume (auto-reads latest scraper run)
python matcher.py

# Phase 3: Fill and submit applications (auto-reads latest matcher run)
python applier.py
python applier.py --run-id <id>   # specific matcher run
python applier.py --dry-run       # override config, never submit

# Phase 4: Optimize resume per job
python tuner.py                                    # pipeline mode (latest matcher run)
python tuner.py --run-id <id>                      # specific matcher run
python tuner.py --jd-file path/to/job.txt          # standalone (single job description)
python tuner.py --jd-file path/to/job.txt --resume-tex path/to/resume.tex
```

## Architecture

### Data Flow

Each phase writes output that the next phase reads automatically (always picks the latest timestamped directory):

```
Greenhouse API → data/runs/<ts>/{manifest,company1,...}.json   [Job[]]
              → data/matches/<ts>/{manifest,shortlisted,rejected}.json  [MatchResult[]]
              → data/applied/{applied.json, screenshots/}   [ApplyRecord[]]
              → data/tuned/<ts>/<job_id>/{critique.json, resume.tex, resume.pdf}
```

### Phase Internals

**Phases 1–3** all use `asyncio.run(main())` with semaphore-controlled concurrency. The concurrency and rate-limiting knobs live in each phase's YAML config.

**Phase 2 (Matcher)** uses a `LLMBackend` Protocol in [hireshire/matcher/scorer.py](hireshire/matcher/scorer.py). Adding a new provider means implementing `score(job, resume_text) -> ScoringSchema`. The active backend is selected by the `LLM_PROVIDER` env var (`gemini` / `openai` / `anthropic`).

**Phase 4 (Tuner)** runs two sequential LLM passes per job:
1. `ResumeEvaluator` — recruiter-perspective critique → `Critique` Pydantic model
2. `ResumeOptimizer` — takes critique + resume LaTeX + optional projects pool → optimized LaTeX

After optimization, [hireshire/tuner/store.py](hireshire/tuner/store.py) compiles with `xelatex` and loops up to 2 trim retries if the PDF exceeds one page. The tuner supports separate `evaluator_provider`/`optimizer_provider` backends (defaults to `LLM_PROVIDER`).

### Key Models

| Model | Defined in | Used by |
|-------|-----------|---------|
| `Job` | `hireshire/models/job.py` | Scraper → Matcher |
| `MatchResult` | `hireshire/matcher/scorer.py` | Matcher → Applier / Tuner |
| `ScoringSchema` | `hireshire/matcher/scorer.py` | Matcher (LLM output) |
| `Critique` | `hireshire/tuner/evaluator.py` | Tuner Pass 1 → Pass 2 |
| `ApplyRecord` | `hireshire/applier/store.py` | Applier output |

`Job.content_text` auto-strips HTML via a Pydantic validator on ingest.

### Environment Variables

Configured in `.env` (see `.env.example`):
- `LLM_PROVIDER` — `gemini` / `openai` / `anthropic`
- `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — whichever provider(s) are used
- Tuner can use different providers for evaluator vs optimizer via `evaluator_provider` / `optimizer_provider` in `config/tuner.yaml`

### Rate Limiting

Each phase manages its own throttling:
- **Scraper**: semaphore via `settings.concurrency` in `config/companies.yaml` (default 10)
- **Matcher**: semaphore + `request_interval_s` delay (default 13 s ≈ 4.6 RPM, safe for free-tier Gemini)
- **Applier**: sequential, one form at a time, `inter_job_delay_s` between jobs
- **Tuner**: sequential per job, `request_interval_s` between LLM calls

The shared HTTP client ([hireshire/http_client.py](hireshire/http_client.py)) handles exponential-backoff retries for 429/5xx automatically.
