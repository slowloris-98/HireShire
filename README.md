# HireShire

Automated job search pipeline in four phases: **Scrape → Match → Tune → Apply**

## Architecture

Each phase is fully independent — its own entrypoint, module, config, and output directory.

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| **Run** | `python scraper.py` | `python matcher.py` | `python tuner.py` | `python applier.py` |
| **Module** | `hireshire/scrapers/` | `hireshire/matcher/` | `hireshire/tuner/` | `hireshire/applier/` |
| **Config** | `config/scraper.yaml` | `config/matcher.yaml` | `config/tuner.yaml` | `config/applier.yaml` |
| **Output** | `data/scraped/<id>/` | `data/matches/<id>/` | `data/tuned/<id>/` | `data/applied/` |

Orchestrator summary (all shortlisted jobs + tuning status) is written to `data/pipeline/<run_id>/` each run.

Shared across all phases: `hireshire/models/`, `hireshire/storage/`.

## Data Flow

```
config/scraper.yaml
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

Put your resume PDF at `data/resume.pdf` and update `resume_path` in `config/matcher.yaml` and `config/applier.yaml`.

For Phase 3 (Tuner), the pipeline needs three files under `data/resume_projects/`:
- `Udayan_Resume.tex` — your full resume LaTeX source (read by the Evaluator)
- `resume_template.tex` — a template with a `%{{EXPERIENCE_SECTIONS}}` placeholder (filled by the Assembler)
- `projects_bullets.yaml` — pre-authored LaTeX bullets for each project/work entry

Update the paths in `config/tuner.yaml` if you store these elsewhere.

---

## Phase 1: Scraper

Fetches open job listings from three job board APIs: **Greenhouse**, **Ashby**, and **Lever**. The scraper auto-detects which backend to use based on the token key present in each company entry.

### Configuration — `config/scraper.yaml`

```yaml
settings:
  concurrency: 10          # parallel requests
  max_age_hours: 6         # only keep jobs updated in the last N hours (remove for all)
  location_filter:         # case-insensitive substring match; remove or leave empty for all locations
    - "united states"
    - "remote"
    - "india"

companies:
  - name: Stripe
    greenhouse_token: stripe       # Greenhouse: job-boards.greenhouse.io/{token}/jobs
  - name: Linear
    ashby_token: linear            # Ashby: jobs.ashbyhq.com/{token}
  - name: Spotify
    lever_token: spotify           # Lever: jobs.lever.co/{token}
```

Each entry uses exactly one of `greenhouse_token`, `ashby_token`, or `lever_token`. The `location_filter` does a substring match against each job's location fields — all three APIs lack server-side location filtering, so it is applied client-side. Leave the list empty (or remove the key) to include all locations.

### Run

```bash
python scraper.py
```

### Output — `data/scraped/<timestamp>/`

```
data/scraped/2026-05-29T23-24-43Z/
├── manifest.json       # run summary: total jobs, per-company status
├── anthropic.json      # array of Job objects
└── stripe.json
```

---

## Phase 2: Matcher

Scores every scraped job against your resume using an LLM, then shortlists jobs above a relevance threshold. Supports **Gemini**, **OpenAI**, and **Anthropic** — controlled by the `LLM_PROVIDER` env var.

### LLM Provider

Set `LLM_PROVIDER` in your `.env` to switch backends:

```
LLM_PROVIDER=gemini      # default — requires GOOGLE_API_KEY
LLM_PROVIDER=openai      # requires OPENAI_API_KEY
LLM_PROVIDER=anthropic   # requires ANTHROPIC_API_KEY
```

Then set `model` in `config/matcher.yaml` to a model name for that provider (e.g. `gemini-2.0-flash`, `gpt-4o-mini`, `claude-haiku-4-5-20251001`).

### Configuration — `config/matcher.yaml`

```yaml
settings:
  threshold: 70              # min score (0–100) to shortlist
  concurrency: 1             # parallel LLM calls (free tier: keep at 1)
  model: gemini-2.0-flash    # model name for the chosen LLM_PROVIDER
  request_interval_s: 13     # seconds between requests (13s ≈ 4.6 RPM; set to 0 on paid tier)
  max_content_chars: 8000    # truncate job description before sending
  resume_path: data/resume.pdf
  projects_path: data/projects.md   # optional extra context appended to candidate profile
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
```

### Run

```bash
python matcher.py
```

Reads automatically from the most recent scraper run in `data/scraped/`. The `title_filter` pre-filters jobs by title before sending to the LLM, saving API calls. Writes results incrementally to `progress.jsonl` so a mid-run crash can be resumed.

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

Compiles with `pdflatex`. If the output exceeds one page, a code-based bullet-removal loop trims one bullet at a time from the project with the most bullets and reassembles until it fits — no extra LLM calls.

### Configuration — `config/tuner.yaml`

```yaml
settings:
  resume_tex_path: data/resume_projects/Udayan_Resume.tex         # full resume for evaluator (Pass 1)
  resume_template_path: data/resume_projects/resume_template.tex  # template for assembler (Pass 2)
  projects_bullets_path: data/resume_projects/projects_bullets.yaml  # pre-authored LaTeX bullets
  matches_dir: data/matches
  tuned_dir: data/tuned

  model: gpt-4o-mini

  # Per-pass overrides (fall back to LLM_PROVIDER env var if unset)
  evaluator_provider: openai
  evaluator_model: gpt-5-nano
  optimizer_provider: openai      # use "claude_code" to route through the local Claude CLI
  optimizer_model: gpt-5-nano

  max_jd_chars: 12000
  max_tex_chars: 15000
  request_interval_s: 5.0
```

### Run

```bash
python tuner.py                                          # pipeline mode (latest matcher run)
python tuner.py --run-id <id>                            # specific matcher run
python tuner.py --jd-file path/to/job.txt                # standalone (single job description)
python tuner.py --jd-file path/to/job.txt --resume-tex path/to/resume.tex
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

Reads shortlisted jobs and fills out + submits applications using browser automation powered by **browser-use** (Playwright). Run after the Tuner so the optimized resume PDFs are ready.

> **Safety:** `dry_run: true` is the default. Set it to `false` in `config/applier.yaml` only when ready to submit live applications.

### Configuration — `config/applier.yaml`

```yaml
settings:
  dry_run: true              # SAFETY: never submits when true
  headless: false            # show browser window while running
  inter_job_delay_s: 10      # seconds between applications
  max_steps: 40              # max browser-use steps per application
  resume_path: data/resume.pdf
  matches_dir: data/matches
  applied_dir: data/applied
  generate_cover_letter: true
  model: gpt-4o-mini         # LLM for question answering and browser agent

  # Personal info filled into application forms
  first_name: Your
  last_name: Name
  email: you@example.com
  phone: "1234567890"
```

### Run

```bash
python applier.py                    # reads latest matcher run
python applier.py --run-id <id>      # specific matcher run
python applier.py --dry-run          # override config, never submit
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
  "status": "submitted",
  "applied_at": "2026-05-29T23:45:00Z"
}
```

Status values: `"submitted"` | `"dry_run"` | `"error"` | `"skipped"`

---

## Orchestrator

`orchestrate.py` runs Phases 1–3 automatically as a streaming pipeline. Phase 4 (Applier) remains manual — review the tuned resumes before submitting.

The three phases run concurrently using asyncio queues:
- Each company's jobs are queued for the matcher as soon as they're fetched
- Each shortlisted result is queued for the tuner as soon as it's scored

```bash
python orchestrate.py --now        # run immediately, then every 4h
python orchestrate.py --once       # run exactly once
python orchestrate.py --interval 2 # every 2 hours instead of 4
python orchestrate.py --no-tuner   # scraper + matcher only (skip resume tuning)
python orchestrate.py --no-matcher # scraper only (skip scoring and tuning)
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

---

## Project Structure

```
HireShire/
├── scraper.py              # Phase 1 entrypoint
├── matcher.py              # Phase 2 entrypoint
├── tuner.py                # Phase 3 entrypoint
├── applier.py              # Phase 4 entrypoint
├── orchestrate.py          # Pipeline orchestrator (runs phases 1–3 automatically)
├── requirements.txt
├── .env.example
├── config/
│   ├── scraper.yaml        # Phase 1 config
│   ├── matcher.yaml        # Phase 2 config
│   ├── tuner.yaml          # Phase 3 config
│   └── applier.yaml        # Phase 4 config
├── data/
│   ├── scraped/            # Phase 1 output (gitignored)
│   ├── matches/            # Phase 2 output (gitignored)
│   ├── tuned/              # Phase 3 output (gitignored)
│   ├── applied/            # Phase 4 output (gitignored)
│   └── pipeline/           # Orchestrator run summaries (gitignored)
├── logs/                   # Orchestrator logs (gitignored)
└── hireshire/
    ├── models/job.py        # Shared Job data model
    ├── storage/json_store.py
    ├── http_client.py       # Shared HTTP client with retry/backoff
    ├── scrapers/
    │   ├── base.py
    │   ├── greenhouse.py
    │   ├── ashby.py
    │   └── lever.py
    ├── matcher/
    │   ├── config.py
    │   ├── resume.py        # PDF text extraction (pdfplumber)
    │   ├── loader.py
    │   ├── scorer.py        # Gemini/OpenAI/Anthropic backends + MatchResult model
    │   └── store.py
    ├── tuner/
    │   ├── config.py
    │   ├── evaluator.py     # Pass 1: recruiter critique → EvaluatorResult
    │   ├── optimizer.py     # Pass 2: JSON project selector → SelectionResult
    │   ├── assembler.py     # code-assembles LaTeX from template + pre-authored bullets
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
