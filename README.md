# HireShire

Automated job search pipeline in four phases: **Scrape → Match → Tune → Apply**

## Architecture

Each phase is fully independent — its own entrypoint, module, config, and output directory.

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| **Run** | `python scraper.py` | `python matcher.py` | `python tuner.py` | `/apply` (Claude skill) |
| **Module** | `hireshire/scrapers/` | `hireshire/matcher/` | `hireshire/tuner/` | `hireshire/applier/` |
| **Config** | `config/scraper.yaml` | `config/matcher.yaml` | `config/tuner.yaml` | `config/applier.yaml` |
| **Output** | `data/scraped/<id>/` | `data/matches/<id>/` | `data/tuned/<id>/` | `data/applied/` |

Orchestrator summary (all shortlisted jobs + tuning status) is written to `data/pipeline/<run_id>/` each run.

Shared across all phases: `hireshire/models/`, `hireshire/storage/`.

## Data Flow

```
config/scraper.yaml  +  config/{greenhouse,ashby,lever}_companies.json  −  config/bad_slugs.json
        │
        ▼
python scraper.py  →  data/scraped/<run_id>/{company}.json
                                │
                                ▼
        resume.pdf + config/matcher.yaml
                                │
                                ▼
python matcher.py  →  data/matches/<run_id>/shortlisted.json
                                │
                                ▼
                       config/tuner.yaml
                                │
                                ▼
python tuner.py    →  data/tuned/<run_id>/<job_id>/
                                │
                                ▼
                      config/applier.yaml
                                │
                                ▼
python applier.py  →  data/applied/applied.json
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium   # required for Phase 4 (Applier)
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in the API key for your chosen LLM provider:

| Provider | Key | Get one at |
|---|---|---|
| Gemini (default) | `GOOGLE_API_KEY` | [aistudio.google.com](https://aistudio.google.com) |
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| Anthropic | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |

Also set `LLM_PROVIDER` to the provider you want to use (defaults to `gemini`).

### 3. Place your resume

Put your resume PDF where `resume_path` in `config/matcher.yaml` and `config/applier.yaml` points (the shipped configs use `data/resume_projects/Udayan_Resume.pdf`), or update those keys to your own location.

For Phase 3 (Tuner), the pipeline needs three files under `data/resume_projects/`:
- `Udayan_Resume.tex` — your full resume LaTeX source (read by the Evaluator)
- `resume_template.tex` — a template with a `%{{EXPERIENCE_SECTIONS}}` placeholder (filled by the Assembler)
- `projects_bullets.yaml` — pre-authored LaTeX bullets for each project/work entry

Update the paths in `config/tuner.yaml` if you store these elsewhere.

> Note: `data/resume_projects/` and the pipeline output directories are gitignored — they hold personal data. See [Project Structure](#project-structure) for the full list.

---

## Phase 1: Scraper

Fetches open job listings from three job board APIs: **Greenhouse**, **Ashby**, and **Lever**. The scraper auto-detects which backend to use based on which JSON slug list a company came from.

### Company slug lists — `config/{ashby,greenhouse,lever}_companies.json`

Company slugs are **not** stored in `scraper.yaml`. Each board has its own JSON file holding a flat array of board tokens (slugs), loaded by `hireshire/config.py`:

```json
// config/greenhouse_companies.json  →  job-boards.greenhouse.io/{slug}/jobs
["stripe", "anthropic", "figma", ...]
```

```json
// config/ashby_companies.json       →  jobs.ashbyhq.com/{slug}
// config/lever_companies.json        →  jobs.lever.co/{slug}
```

The lists are large (currently ~8.3k Greenhouse, ~4.4k Lever, ~3.2k Ashby slugs). Add a company by appending its slug to the matching file.

### Bad-slug tracking — `config/bad_slugs.json`

Because the slug lists are scraped in bulk, many entries are stale or invalid. When a slug returns a genuine 404 (Greenhouse/Ashby) or `{"ok": false}` (Lever), the scraper raises `SlugNotFoundError` and records the slug in `config/bad_slugs.json`, keyed by platform:

```json
{ "ashby": ["11x", ...], "greenhouse": [...], "lever": [...] }
```

On every subsequent run these known-bad slugs are filtered out **before** any HTTP call, so the bad list is self-healing and the run gets faster over time. Re-validate the list with the helper script:

```bash
python scripts/verify_bad_slugs.py                 # report only (no writes)
python scripts/verify_bad_slugs.py --prune         # remove slugs that are reachable again
python scripts/verify_bad_slugs.py --platform greenhouse   # limit to one board
```

A slug stays bad only if it still raises `SlugNotFoundError`; if `fetch_all()` succeeds (even with zero jobs) it is reported as *recoverable* and pruned with `--prune`. Transient errors (timeout/5xx/network) are reported as *inconclusive* and never pruned.

### Configuration — `config/scraper.yaml`

`scraper.yaml` now holds only run settings — no companies:

```yaml
settings:
  concurrency: 20          # parallel company fetches (semaphore)
  request_timeout_s: 3000  # per-HTTP-request timeout
  retry_attempts: 3        # per-request retries (on top of the shared client's backoff)
  company_timeout_s: 30000 # max seconds to wait for one company before skipping it
  max_age_hours: 24        # only keep jobs updated in the last N hours; remove to fetch all
  location_filter:         # case-insensitive substring match; remove or leave empty for all
    - "united states"
    - "remote"
    - "india"
```

The `location_filter` does a substring match against each job's location fields — all three APIs lack server-side location filtering, so it is applied client-side. `max_age_hours` prunes jobs not updated recently (Lever also applies it server-side where possible). Leave the list empty (or remove the key) to include all locations.

### Run

```bash
python scraper.py
```

### Output — `data/scraped/<timestamp>/`

```
data/scraped/2026-05-29T23-24-43Z/
├── manifest.json       # run summary: total jobs, per-company status, fetch times
├── anthropic.json      # array of Job objects
└── stripe.json
```

Newly discovered bad slugs are appended to `config/bad_slugs.json` at the end of the run.

---

## Phase 2: Matcher

Scores every scraped job against your resume using an LLM, then shortlists jobs above a relevance threshold. Supports **Gemini**, **OpenAI**, and **Anthropic** — controlled by the `LLM_PROVIDER` env var.

### LLM Provider

The provider can be set two ways (the config key wins when present):

- `provider` in `config/matcher.yaml` — `gemini` / `openai` / `anthropic`
- `LLM_PROVIDER` in `.env` — used as the fallback when the config key is omitted/null

```
LLM_PROVIDER=gemini      # default — requires GOOGLE_API_KEY
LLM_PROVIDER=openai      # requires OPENAI_API_KEY
LLM_PROVIDER=anthropic   # requires ANTHROPIC_API_KEY
```

Then set `model` in `config/matcher.yaml` to a model name for that provider (e.g. `gemini-2.0-flash`, `gpt-5-nano`, `claude-haiku-4-5-20251001`).

### Configuration — `config/matcher.yaml`

```yaml
settings:
  threshold: 80              # min score (0–100) to shortlist
  concurrency: 8             # parallel LLM calls (free tier: keep at 1)
  provider: openai           # gemini / openai / anthropic (omit/null → LLM_PROVIDER env var)
  model: gpt-5-nano          # model name for the chosen provider
  request_interval_s: 2      # seconds between requests (13s ≈ 4.6 RPM; set to 0 on paid tier)
  max_content_chars: 8000    # truncate job description before sending
  skip_llm: false            # true = skip scoring; all title-passing jobs are auto-shortlisted
  resume_path: data/resume_projects/Udayan_Resume.pdf
  projects_path: data/resume_projects/projects.md   # optional extra context appended to profile
  runs_dir: data/scraped
  matches_dir: data/matches

title_filter:
  include_keywords:          # title must contain at least one (leave empty to skip)
    - engineer
    - developer
    - software
  exclude_keywords:          # title must contain none of these
    - principal
    - staff
    - senior
```

### Run

```bash
python matcher.py
```

Reads automatically from the most recent scraper run in `data/scraped/`. The `title_filter` pre-filters jobs by title before sending to the LLM, saving API calls. Writes results incrementally to `progress.jsonl` so a mid-run crash can be resumed.

**Skip-LLM mode** — set `skip_llm: true` (or pass `--no-llm` to the orchestrator) to bypass scoring entirely: every job that passes the title filter is shortlisted with `relevance_score: 100` and `skip_reason: "llm_skipped"`. Useful for a zero-cost dry run of the full pipeline, or when the title filter alone is selective enough.

**Cross-run dedup** — scored job IDs are persisted to `data/seen_jobs.json`. On later runs any job ID already in that set is skipped before the title filter or LLM, so recurring pipeline runs never re-score the same listing.

### Output — `data/matches/<run_id>/`

```
data/matches/2026-05-29T23-24-43Z/
├── manifest.json       # scoring stats: threshold, model, counts
├── shortlisted.json    # jobs above threshold with match_reasons + disqualifiers
└── rejected.json       # jobs below threshold or skipped (no content)
```

Each entry in `shortlisted.json`:

```json
{
  "job_id": "5101378008",
  "board_token": "anthropic",
  "title": "Software Engineer, Platform",
  "location": "San Francisco, CA",
  "absolute_url": "https://job-boards.greenhouse.io/...",
  "relevance_score": 84,
  "years_experience_required": 3.0,
  "match_reasons": ["5 years Python experience matches requirement", "..."],
  "disqualifiers": [],
  "recommend": true,
  "scored_at": "2026-05-29T23:30:00Z",
  "source_run_id": "2026-05-29T23-24-43Z"
}
```

---

## Phase 3: Tuner

Two-pass LLM resume optimizer. For each shortlisted job:
1. **Evaluator** — LLM critiques your full resume LaTeX against the job description → structured `EvaluatorResult` (missing keywords, experience gaps, overall assessment)
2. **Selector** — LLM reads the critique + a compact project roster (titles + descriptions only) and returns a lightweight `SelectionResult` JSON: which 2–3 projects to include and per-bullet keyword adjustments
3. **Assembler** — pure Python substitutes the selected entries into a LaTeX template using pre-authored bullets from `projects_bullets.yaml` — no LLM generates LaTeX

Compiles with `pdflatex`, then a code-based **two-directional fit** loop (no extra LLM calls) makes the resume fill exactly one page: on overflow it drops the summary, then the least-relevant projects, then bottom bullets one at a time; on a sparse single page it re-enables the summary to fill the space (reverting if that overflows).

### Configuration — `config/tuner.yaml`

```yaml
settings:
  resume_tex_path: data/resume_projects/Udayan_Resume.tex         # full resume for evaluator (Pass 1)
  resume_template_path: data/resume_projects/resume_template.tex  # template for assembler (Pass 2)
  projects_bullets_path: data/resume_projects/projects_bullets.yaml  # pre-authored LaTeX bullets
  projects_path: data/resume_projects/projects.md                 # legacy narrative (unused by optimizer)
  matches_dir: data/matches
  runs_dir: data/scraped
  tuned_dir: data/tuned

  model: gpt-4o-mini              # shared fallback when a per-pass override is unset

  # Per-pass overrides (fall back to model above / LLM_PROVIDER env var if unset)
  evaluator_provider: openai
  evaluator_model: gpt-5-nano
  optimizer_provider: anthropic   # gemini / openai / anthropic / claude_code (local Claude CLI)
  optimizer_model: claude-sonnet-4-6

  max_jd_chars: 12000
  max_tex_chars: 15000
  request_interval_s: 5.0
  claude_cli_timeout_s: 600       # subprocess timeout when optimizer_provider: claude_code
```

**Linting the bullet corpus** — the pre-authored bullets in `projects_bullets.yaml` are emitted verbatim, so they are validated once (no dash-as-punctuation, unique Accenture opening verbs, hard numbers) rather than per run:

```bash
python -m hireshire.tuner.lint                     # lint the default corpus
pytest tests/test_projects_bullets.py              # same check, as a test
```

### Run

```bash
python tuner.py                                                          # pipeline mode (latest matcher run)
python tuner.py --run-id <id>                                            # specific matcher run
python tuner.py --job-id <job_id>                                        # tune a single job only
python tuner.py --force                                                  # re-tune already-processed jobs
python tuner.py --jd-file path/to/job.txt                                # standalone (single job description)
python tuner.py --jd-file path/to/job.txt --resume-tex path/to/resume.tex
python tuner.py --jd-file path/to/job.txt --title "Senior Engineer" --company "Acme"
```

### Output — `data/tuned/<run_id>/`

```
data/tuned/2026-05-29T23-24-43Z/
├── manifest.json
└── 5101378008/                        # one directory per job
    ├── job_description.txt
    ├── critique.json                  # structured critique from Pass 1
    ├── Udayan_Atreya_Resume.tex       # assembled LaTeX
    └── Udayan_Atreya_Resume.pdf       # compiled PDF
```

---

## Phase 4: Applier

Phase 4 has two interchangeable implementations that share `config/applier.yaml`:

1. **`/apply` Claude Code skill** (recommended) — reads the latest pipeline results and fills + submits forms using Playwright MCP tools. Only processes jobs with `tuner_status == "tuned"` and a valid `resume_pdf`.
2. **`python applier.py`** — a standalone `browser-use` agent entrypoint that reads the latest **matches** run directly. Useful outside Claude Code.

Run either after the Tuner so the optimized resume PDFs are ready.

> **Safety:** `dry_run` gates live submission — when `true`, the applier fills forms but never clicks submit. Verify the value in `config/applier.yaml` before running.

### Configuration — `config/applier.yaml`

```yaml
settings:
  dry_run: false             # SAFETY: set true so forms are filled but never submitted
  headless: false            # show browser window while running
  inter_job_delay_s: 10      # seconds between applications
  max_steps: 40              # max browser-use agent steps per job (applier.py only)
  matches_dir: data/matches
  applied_dir: data/applied
  runs_dir: data/scraped
  resume_path: data/resume_projects/Udayan_Resume.pdf
  generate_cover_letter: true
  model: gpt-4o-mini         # LLM for answer generation / browser agent (applier.py only)

  # Personal info filled into application forms
  first_name: Your
  last_name: Name
  email: you@example.com
  phone: "1234567890"
```

### Run

```
/apply
```

Invoke the skill in Claude Code. It reads the latest `data/pipeline/*/pipeline_results.json`, skips jobs that are already in `applied.json`, and processes the remaining tuned jobs sequentially.

Or run the standalone `browser-use` entrypoint (reads the latest matches run instead of pipeline results):

```bash
python applier.py                    # apply from the latest matches run (dry_run from config)
python applier.py --run-id <run_id>  # use a specific matches run
python applier.py --dry-run          # override config — fill forms but never submit
```

The orchestrator can also trigger the `/apply` skill automatically after each pipeline run:

```bash
python orchestrate.py --apply
```

### Output — `data/applied/`

```
data/applied/
├── applied.json        # all application records (appended across runs)
└── screenshots/        # browser screenshots per application
```

Each entry in `applied.json`:

```json
{
  "job_id": "5101378008",
  "board_token": "anthropic",
  "title": "Software Engineer, Platform",
  "absolute_url": "https://job-boards.greenhouse.io/...",
  "status": "submitted",
  "dry_run": false,
  "applied_at": "2026-05-29T23:45:00Z",
  "screenshot": "data/applied/screenshots/5101378008.png",
  "error": null
}
```

Status values: `"submitted"` | `"dry_run"` | `"error"`

---

## Orchestrator

`orchestrate.py` runs Phases 1–3 automatically as a streaming pipeline. Phase 4 (Applier) can be triggered automatically with `--apply`, or run manually as the `/apply` Claude Code skill — review the tuned resumes first.

The three phases run concurrently using asyncio queues:
- Each company's jobs are queued for the matcher as soon as they're fetched
- Each shortlisted result is queued for the tuner as soon as it's scored

```bash
python orchestrate.py --now        # run immediately, then every 4h
python orchestrate.py --once       # run exactly once
python orchestrate.py --interval 2 # every 2 hours instead of 4
python orchestrate.py --no-tuner   # scraper + matcher only (skip resume tuning)
python orchestrate.py --no-matcher # scraper only (skip scoring and tuning)
python orchestrate.py --no-llm     # matcher shortlists all title-passing jobs (no LLM scoring)
python orchestrate.py --apply      # invoke /apply skill after each run
```

Logs are written to `logs/orchestrate.log` (rotates at 5 MB, keeps 5 files).

### Output — `data/pipeline/<run_id>/`

After each run, the orchestrator writes a summary of all shortlisted jobs to:

```
data/pipeline/2026-06-04T02-01-50Z/
├── pipeline_results.json   # full records (array)
└── pipeline_results.csv    # same data in CSV
```

Every shortlisted job appears regardless of tuner outcome. The `tuner_status` field indicates what happened (`"tuned"` / `"skipped"` / `"error"`), and `resume_tex`/`resume_pdf` are `null` when tuning didn't complete. With `--no-matcher` the file is written but empty (`[]`).

With `--apply`, the orchestrator invokes the `/apply` skill via `claude -p --permission-mode auto` after each run completes. The skill reads this file and applies only tuned jobs. This flag is ignored when `--no-tuner` or `--no-matcher` are set.

---

## Project Structure

```
HireShire/
├── scraper.py              # Phase 1 entrypoint
├── matcher.py              # Phase 2 entrypoint
├── tuner.py                # Phase 3 entrypoint
├── applier.py              # Phase 4 entrypoint (standalone browser-use agent)
├── orchestrate.py          # Pipeline orchestrator (runs phases 1–3 automatically)
├── requirements.txt
├── .env.example
├── config/
│   ├── scraper.yaml               # Phase 1 run settings (no companies)
│   ├── greenhouse_companies.json  # Greenhouse slug list
│   ├── ashby_companies.json       # Ashby slug list
│   ├── lever_companies.json       # Lever slug list
│   ├── bad_slugs.json             # known-404 slugs, auto-skipped and appended each run
│   ├── matcher.yaml               # Phase 2 config
│   ├── tuner.yaml                 # Phase 3 config
│   └── applier.yaml               # Phase 4 config
├── scripts/
│   └── verify_bad_slugs.py # re-validate config/bad_slugs.json (--prune / --platform)
├── tests/
│   └── test_projects_bullets.py   # pytest wrapper around the tuner bullet lint
├── data/
│   ├── scraped/            # Phase 1 output (gitignored)
│   ├── matches/            # Phase 2 output (gitignored)
│   ├── tuned/              # Phase 3 output (gitignored)
│   ├── applied/            # Phase 4 output (gitignored)
│   ├── pipeline/           # Orchestrator run summaries (gitignored)
│   ├── resume_projects/    # Resume .tex / template / bullets (gitignored — personal)
│   └── seen_jobs.json      # cross-run scored-job dedup set (gitignored)
├── logs/                   # Orchestrator logs (gitignored)
├── .claude/
│   ├── skills/apply.md     # Phase 4 as a Claude Code skill (/apply)
│   └── commands/apply.md   # prompt the orchestrator's --apply flag feeds to `claude -p`
└── hireshire/
    ├── config.py            # Scraper config loader (settings + slug JSON files → AppConfig)
    ├── http_client.py       # Shared HTTP client with retry/backoff
    ├── models/job.py        # Shared Job data model
    ├── storage/json_store.py
    ├── scrapers/
    │   ├── base.py
    │   ├── exceptions.py    # SlugNotFoundError (drives bad-slug tracking)
    │   ├── greenhouse.py
    │   ├── ashby.py
    │   └── lever.py
    ├── matcher/
    │   ├── config.py
    │   ├── resume.py        # PDF text extraction (pdfplumber)
    │   ├── loader.py
    │   ├── prompts.py       # scorer system prompt (rubric)
    │   ├── scorer.py        # Gemini/OpenAI/Anthropic backends + MatchResult model
    │   ├── seen.py          # cross-run job-ID dedup (data/seen_jobs.json)
    │   ├── title_filter.py  # keyword pre-filter before LLM scoring
    │   └── store.py
    ├── tuner/
    │   ├── config.py
    │   ├── evaluator.py     # Pass 1: recruiter critique → EvaluatorResult
    │   ├── optimizer.py     # Pass 2: JSON project selector → SelectionResult
    │   ├── assembler.py     # code-assembles LaTeX from template + pre-authored bullets
    │   ├── lint.py          # validates projects_bullets.yaml corpus
    │   ├── prompts.py       # system prompts for evaluator and selector
    │   ├── loader.py
    │   └── store.py         # PDF compilation with pdflatex
    └── applier/
        ├── config.py
        ├── answerer.py      # LLM-based question answering
        ├── filler.py        # browser-use form filling
        ├── loader.py
        └── store.py
```
