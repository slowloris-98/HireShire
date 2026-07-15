# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HireShire is a four-phase automated job search pipeline:
1. **Scraper** — fetches job listings from Greenhouse, Ashby, and Lever job board APIs
2. **Matcher** — scores jobs against a resume via LLM (Gemini / OpenAI / Anthropic)
3. **Tuner** — two-pass resume optimizer (evaluator critique → JSON project selector → code assembler → PDF compile)
4. **Applier** — fills out and submits job applications via Claude Code's `/apply` skill (Playwright MCP)

A **web dashboard** (Phase 5, `hireshire/webapp/` + `frontend/`) sits on top: a LangGraph "talk to your data" chat, an editable config panel, run controls, and a filtered job list. See the Web Dashboard section below.

Each phase is fully independent: its own entrypoint script, `hireshire/<phase>/` subpackage, `config/<phase>.yaml`, and `data/<phase-output>/` directory.

## Commands

```bash
# Setup
pip install -r requirements.txt
playwright install chromium   # required for Phase 4 (Applier)

# Phase 1: Scrape job listings (company slugs live in config/*_companies.json)
python scraper.py
python scripts/verify_bad_slugs.py --prune   # re-validate config/bad_slugs.json, drop recovered slugs
python scripts/prune_runs.py --keep 10       # manual retention: delete all but the 10 most-recent runs from data/hireshire.db
python scripts/db_stats.py                   # inspect the DB: tables + row counts + latest finalised run per phase
python scripts/backfill_from_json.py --pipeline --dry-run   # import legacy data/pipeline/<run>/*.json into the DB

# Phase 2: Score jobs against resume (auto-reads latest scraper run)
python matcher.py

# Phase 3: Optimize resume per job (auto-reads latest matcher run)
python tuner.py                                    # pipeline mode (latest matcher run)
python tuner.py --run-id <id>                      # specific matcher run
python tuner.py --job-id <job_id>                  # tune a single job only
python tuner.py --force                            # re-tune already-processed jobs
python tuner.py --jd-file path/to/job.txt          # standalone (single job description)
python tuner.py --jd-file path/to/job.txt --resume-tex path/to/resume.tex
python tuner.py --jd-file path/to/job.txt --title "Senior Engineer" --company "Acme"

# Phase 4: Fill and submit applications (Claude Code skill — reads latest pipeline run)
# Run via Claude Code: /apply
# Or trigger automatically from the orchestrator with --apply

# Orchestrator: runs phases 1–3 as a streaming pipeline on a schedule
python orchestrate.py --now        # run immediately, then every 4h
python orchestrate.py --once       # run exactly once
python orchestrate.py --interval 2 # custom interval in hours
python orchestrate.py --no-tuner   # scraper + matcher only (skip resume tuning)
python orchestrate.py --no-matcher # scraper only (skip scoring and tuning)
python orchestrate.py --no-llm     # matcher auto-shortlists all title-passing jobs (no LLM scoring)
python orchestrate.py --apply      # invoke /apply skill after each pipeline run

# Standalone applier (browser-use agent; reads latest matches run)
python applier.py --dry-run

# Lint the pre-authored resume bullet corpus
python -m hireshire.tuner.lint
pytest tests/test_projects_bullets.py

# Phase 5: Web dashboard (FastAPI backend + React/TS SPA)
cd frontend && npm install && npm run build && cd ..   # build the SPA once (emits frontend/dist)
python run_web.py                                       # serve API + built SPA (config/frontend.yaml host/port)
python run_web.py --reload                              # dev backend with auto-reload
# Frontend dev with hot-reload (separate terminal): cd frontend && npm run dev  (proxies /api to :8000)
```

## Architecture

### Data Flow

All tabular data lives in a single **SQLite database** (`data/hireshire.db`, WAL mode) — see [hireshire/storage/db.py](hireshire/storage/db.py). Every phase writes rows keyed by a shared `run_id`; the next phase reads them via `db.latest_run(<phase>)`. Genuine binary artifacts (tuned PDFs/tex, applier screenshots) stay on disk and are referenced by path from the DB. A per-run pipeline CSV/JSON is exported for convenience and the `/apply` skill.

```
Greenhouse API → jobs, run_companies tables            (keyed by run_id)   [Job[]]
              → matches table (shortlisted flag per row)                   [MatchResult[]]
              → tuned_jobs table + data/tuned/<run_id>/<job_id>/{job_description.txt, critique.json, <Name>_Resume.tex, <Name>_Resume.pdf}
              → applied table + data/applied/screenshots/                  [ApplyRecord[]]

Orchestrator  → pipeline_results table
                + data/pipeline/<run_id>/{pipeline_results.json, pipeline_results.csv}
                  (all shortlisted jobs; resume fields null when tuner was skipped/errored)
```

`db.py` exposes one connection per DB path (`get_db`, process-wide cached, guarded by a `threading.Lock`); async callers offload blocking writes with `asyncio.to_thread`. A **zero-job company writes only a `run_companies` metadata row, no `jobs` rows** — no more per-company `[]` files. Tables: `runs`, `run_companies`, `jobs`, `matches`, `seen_jobs`, `pipeline_results`, `tuned_jobs`, `applied`. Runs accumulate (no auto-prune); reclaim space manually with `python scripts/prune_runs.py --keep N` (or `--before YYYY-MM-DD`). The `db_path` config key (default `data/hireshire.db`) is present in every phase's settings. **`config/bad_slugs.json` and `config/*_companies.json` remain flat JSON files** (scraper inputs, not run output).

### Phase Internals

**All four phases** use `asyncio.run(main())`. Concurrency and rate-limiting knobs live in each phase's YAML config.

**Phase 2 (Matcher)** uses a `LLMBackend` Protocol in [hireshire/matcher/scorer.py](hireshire/matcher/scorer.py). Adding a new provider means implementing `score(job, resume_text) -> ScoringSchema`. The active backend is selected by the `provider` key in `config/matcher.yaml`, falling back to the `LLM_PROVIDER` env var when that key is null/omitted (`gemini` / `openai` / `anthropic`). The scorer system prompt (the rubric) lives in [hireshire/matcher/prompts.py](hireshire/matcher/prompts.py). A `title_filter` in `config/matcher.yaml` pre-filters jobs by title before LLM scoring (saves API calls). Setting `skip_llm: true` (or the orchestrator's `--no-llm` flag) bypasses scoring entirely — every title-passing job is shortlisted with `relevance_score: null` (never scored, rendered as `—` in the UI) and `skip_reason: "llm_skipped"`. A null score *passes* the threshold gate; `is_shortlisted()` in [hireshire/matcher/store.py](hireshire/matcher/store.py) is the single predicate for that rule. `scripts/backfill_null_scores.py` retrofits null scores onto `llm_skipped` rows written before this behaviour. An optional `projects_path` markdown file is appended to the candidate profile for richer context (this key is also present in `config/tuner.yaml` but is no longer used by the tuner's optimizer — it has been superseded by `projects_bullets_path`). Each result is committed immediately to the `matches` table so a mid-run crash can be resumed (`MatchStore.load_progress` reads back rows for a run whose `runs` row isn't yet finalised) without re-scoring already-processed jobs. Scored job IDs persist across runs in the `seen_jobs` table ([hireshire/matcher/seen.py](hireshire/matcher/seen.py)) so recurring pipeline runs never re-score the same listing.

**Phase 3 (Tuner)** runs two sequential LLM passes per job:
1. `ResumeEvaluator` — recruiter-perspective critique against the full resume LaTeX → `EvaluatorResult` Pydantic model
2. `ResumeOptimizer` — compact JSON selector: reads critique + a project roster (titles/descriptions only) and returns a `SelectionResult` (which projects to include + per-bullet keyword adjustments). Then `Assembler` ([hireshire/tuner/assembler.py](hireshire/tuner/assembler.py)) substitutes selected entries into a LaTeX template via pure code using pre-authored bullets from `projects_bullets.yaml`.

After assembly, [hireshire/tuner/store.py](hireshire/tuner/store.py) compiles with `pdflatex`. A code-based **two-directional fit** loop (in `tuner.py`, no LLM involved) reads the PDF's page count and bottom margin and adjusts: on overflow it drops the summary, then the least-relevant projects, then bottom bullets one at a time; on a sparse single page (bottom margin > ~45 pt) it re-enables the summary to fill the page and reverts if that overflows. The tuner supports separate `evaluator_provider`/`optimizer_provider` backends (defaults to `LLM_PROVIDER`). Setting `optimizer_provider: claude_code` routes the selector call through a local Claude CLI subprocess instead of the API.

**Phase 4 (Applier)** has two interchangeable implementations sharing `config/applier.yaml`:
- **`/apply` Claude Code skill** (`.claude/skills/apply.md`, invoked as `/apply`) — reads `data/pipeline/<latest>/pipeline_results.json` and processes only jobs with `tuner_status == "tuned"` and a valid `resume_pdf`. For each job it uses Playwright MCP tools to navigate the form, fill identity fields from `config/applier.yaml`, upload the job-specific tuned resume PDF, optionally generate a cover letter, answer custom questions by reasoning from the resume, and submit (or skip if `dry_run: true`).
- **`python applier.py`** — a standalone `browser-use` agent entrypoint ([hireshire/applier/filler.py](hireshire/applier/filler.py)) that reads the latest **matches** run directly and applies from there (`--run-id`, `--dry-run` flags). Runs without Claude Code.

Both append records to the `applied` table (screenshots stay under `data/applied/screenshots/`).

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
- `LLM_PROVIDER` — `gemini` / `openai` / `anthropic`. This is the fallback; the matcher's `provider` key and the tuner's `evaluator_provider` / `optimizer_provider` keys override it per phase/pass.
- `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — whichever provider(s) are used
- Tuner can use different providers for evaluator vs optimizer via `evaluator_provider` / `optimizer_provider` in `config/tuner.yaml`

### Scraper

Company slugs are **not** in `config/scraper.yaml`. They live in three flat JSON arrays loaded by [hireshire/config.py](hireshire/config.py):
- `config/greenhouse_companies.json` → Greenhouse Job Board API (`job-boards.greenhouse.io/{slug}/jobs`)
- `config/ashby_companies.json` → Ashby Job Board API (`jobs.ashbyhq.com/{slug}`)
- `config/lever_companies.json` → Lever Job Postings API (`jobs.lever.co/{slug}`)

`load_config()` reads these into `CompanyConfig` objects (one token field set per company) and exposes `greenhouse_companies` / `ashby_companies` / `lever_companies` properties. `config/scraper.yaml` now holds only `settings` (`concurrency`, `request_timeout_s`, `retry_attempts`, `company_timeout_s`, `max_age_hours`, `location_filter`, `db_path`).

The lists are large (~8k Greenhouse, ~4k Lever, ~3k Ashby) and bulk-sourced, so many slugs are invalid. When a slug 404s (Greenhouse/Ashby) or returns `{"ok": false}` (Lever), the scraper raises `SlugNotFoundError` ([hireshire/scrapers/exceptions.py](hireshire/scrapers/exceptions.py)) and records it in `config/bad_slugs.json`, keyed by platform. Known-bad slugs are filtered out **before** any HTTP call on every subsequent run, and newly discovered ones are appended when the run finishes — the list is self-healing. `scripts/verify_bad_slugs.py` re-validates the file against the live APIs (`--prune` removes slugs that are reachable again; `--platform` limits to one board; a slug stays bad only if it still raises `SlugNotFoundError`).

The `location_filter` list does a case-insensitive substring match against `job.location.name` and `job.offices[].location`. All three APIs lack server-side location filtering, so this is applied client-side after fetching. Empty list = no filter.

### Rate Limiting

Each phase manages its own throttling:
- **Scraper**: semaphore via `settings.concurrency` in `config/scraper.yaml` (default 10; currently 20), plus a per-company `company_timeout_s` watchdog
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

**Pipeline results** stream into the `pipeline_results` table via `_track_results` (one `INSERT` per row — no O(n²) rewrite) and are appended to `data/pipeline/<run_id>/pipeline_results.csv` live. At the end of each run `_finalise_pipeline` exports the run's rows once to `data/pipeline/<run_id>/pipeline_results.json` (read by the `/apply` skill) and records the `runs`/pipeline summary row. Every shortlisted job appears regardless of tuner outcome — the `tuner_status` field (`"tuned"` / `"skipped"` / `"error"`) indicates what happened, and `resume_tex`/`resume_pdf` are `null` when tuning didn't complete. The tuner reuses the orchestrator's `run_id`, so `data/tuned/<run_id>/` now aligns with the scrape/matches/pipeline run.

**Skip flags** — `--no-tuner` replaces the tuner with a passthrough that marks all jobs `tuner_status: "skipped"` and writes results with null resume fields. `--no-matcher` runs the scraper only and writes an empty results file. `--no-llm` keeps the matcher in the pipeline but skips its LLM scoring, so every title-passing job is shortlisted (`relevance_score: null` — never scored) and forwarded to the tuner.

**`--apply` flag** — after each pipeline run completes, the orchestrator invokes the `/apply` Claude Code skill by running `claude -p --permission-mode auto` with the skill prompt from `.claude/commands/apply.md`. This is skipped when `--no-tuner` or `--no-matcher` are active (since tuned resumes are a prerequisite). Phase 4 can also be run manually by invoking `/apply` in Claude Code at any time.

### Web Dashboard (Phase 5)

A local, single-user dashboard: **FastAPI backend** ([hireshire/webapp/](hireshire/webapp/)) + **React/TypeScript SPA** ([frontend/](frontend/)), launched together by [run_web.py](run_web.py). Built additively — it imports `db.py`, the phase config loaders, and the phase entrypoint scripts; the only pipeline-code change is the `enable_tuner` / `enable_applier` config keys (below). Configured by [config/frontend.yaml](config/frontend.yaml) (chat provider/model, host/port, CORS origins). Layout: a full-height **chat** panel on the left; a **config editor** (top) and **job list** (bottom) on the right.

- **Read-only data access** — [hireshire/webapp/deps.py](hireshire/webapp/deps.py) `ReadDB` opens its own `PRAGMA query_only=ON` sqlite connection so the dashboard never contends with the pipeline's writer (WAL allows concurrent readers). Endpoints: `GET /api/runs`, `GET /api/jobs` (the unified job-list query in [hireshire/webapp/jobs_query.py](hireshire/webapp/jobs_query.py) — pipeline_results, falling back to shortlisted matches, joined with applied status), `GET /api/resume/{run_id}/{job_id}` (streams the tuned PDF). `run_id=all` (the `ALL_RUNS` sentinel) spans **every run to date** instead of one: it reads `pipeline_results` across all runs and collapses each recurring `job_id` to its *most informative* run (has-resume, then has-score, then newest) so a later skip-LLM/no-tuner run can't mask a real score or a tuned PDF earned earlier. It sorts newest-first (score-first would bury recent unscored jobs past the row limit) and takes `location` from `matches.raw_json` — never from the multi-GB `jobs` table, which has no index on `job_id` alone.
- **Config editor** — [hireshire/webapp/routers/config_api.py](hireshire/webapp/routers/config_api.py) reads/writes `config/*.yaml` with **ruamel.yaml** (comment- and CRLF-preserving; `sequence=4, offset=2` indent to match the files). Only the whitelisted fields in [hireshire/webapp/config_spec.py](hireshire/webapp/config_spec.py) are exposed (scraper location/age; matcher threshold/provider/model/skip_llm/title keywords; funnel enabled/threshold/targets; tuner enable+paths+providers; applier enable/dry_run+identity); a `PUT` to any other key is rejected, and the patched file is re-validated against the phase's pydantic settings model before writing.
- **Run control** — [hireshire/webapp/runner.py](hireshire/webapp/runner.py) launches each phase's entrypoint (`scraper.py` / `matcher.py` / `tuner.py` / `applier.py` / `orchestrate.py`) as a tracked `subprocess.Popen`, output redirected to `logs/<phase>.log`. Endpoints: `POST /api/runs/{phase}`, `POST /api/runs/{phase}/stop`, `GET /api/runs/status`, `GET /api/runs/{phase}/logs` (SSE tail).
- **Chat agent** — a LangGraph ReAct agent ([hireshire/webapp/agent/](hireshire/webapp/agent/)) built from `config/frontend.yaml`'s provider/model. Read tools (`search_jobs`, `get_top_matches`, `run_stats`, `list_runs`, `explain_config`) query `ReadDB` directly; run tools (`run_phase`, `stop_phase`) are **confirmation-gated** — they return a proposal, not an action. `POST /api/chat` streams SSE events: `token`, `tool_call`, `job_results` (search results → the frontend loads them into the job-list panel), and `run_proposal` (→ a Confirm card that calls `POST /api/runs/{phase}`). Note: Anthropic Opus 4.8 / Sonnet 5 reject `temperature`, so the anthropic provider omits sampling params ([hireshire/webapp/agent/providers.py](hireshire/webapp/agent/providers.py)).

**`enable_tuner` / `enable_applier` config keys** — new booleans in `config/tuner.yaml` (`TunerSettings`) and `config/applier.yaml` (`ApplierSettings`). `orchestrate.py` reads them as the defaults for skipping the tuner / running the applier; the `--no-tuner` and `--apply` CLI flags still act as explicit overrides.
