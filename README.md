# HireShire

Automated job search pipeline in three phases: **Scrape → Match → Apply**

## Architecture

Each phase is fully independent — its own entrypoint, module, config, and output directory.

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| **Run** | `python scraper.py` | `python matcher.py` | `python applier.py` |
| **Module** | `hireshire/scrapers/` | `hireshire/matcher/` | `hireshire/applier/` |
| **Config** | `config/companies.yaml` | `config/matcher.yaml` | `config/applier.yaml` |
| **Output** | `data/runs/<id>/` | `data/matches/<id>/` | `data/applied/<id>/` |

Shared across all phases: `hireshire/models/`, `hireshire/storage/`.

## Data Flow

```
config/companies.yaml
        │
        ▼
python scraper.py  →  data/runs/<run_id>/{company}.json
                                │
                                ▼
        resume.pdf + config/matcher.yaml
                                │
                                ▼
python matcher.py  →  data/matches/<run_id>/shortlisted.json
                                │
                                ▼
        config/applier.yaml     │  (Phase 3 — coming soon)
                                ▼
python applier.py  →  data/applied/<run_id>/results.json
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
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

Put your resume as `resume.pdf` in the project root.

---

## Phase 1: Scraper

Fetches open job listings from company career pages via the **Greenhouse Job Board API**.

### Configuration — `config/companies.yaml`

```yaml
settings:
  concurrency: 10          # parallel requests
  max_age_hours: 6         # only keep jobs updated in the last N hours (remove for all)

companies:
  - name: Anthropic
    greenhouse_token: anthropic
  - name: Stripe
    greenhouse_token: stripe
```

To add a company, find their Greenhouse board token (usually visible in their careers URL: `job-boards.greenhouse.io/{token}/jobs`) and add it to the list.

### Run

```bash
python scraper.py
```

### Output — `data/runs/<timestamp>/`

```
data/runs/2026-05-29T23-24-43Z/
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
  resume_path: resume.pdf
  runs_dir: data/runs
  matches_dir: data/matches
```

### Run

```bash
python matcher.py
```

Reads automatically from the most recent scraper run in `data/runs/`.

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

## Phase 3: Applier

*Coming soon.* Will read `data/matches/<id>/shortlisted.json` and automatically submit applications.

---

## Project Structure

```
HireShire/
├── scraper.py              # Phase 1 entrypoint
├── matcher.py              # Phase 2 entrypoint
├── requirements.txt
├── .env.example
├── config/
│   ├── companies.yaml      # Phase 1 config
│   └── matcher.yaml        # Phase 2 config
├── data/
│   ├── runs/               # Phase 1 output (gitignored)
│   └── matches/            # Phase 2 output (gitignored)
└── hireshire/
    ├── models/job.py        # Shared Job data model
    ├── storage/json_store.py
    ├── scrapers/
    │   ├── base.py
    │   └── greenhouse.py
    └── matcher/
        ├── config.py
        ├── resume.py
        ├── loader.py
        ├── scorer.py        # Gemini/OpenAI/Anthropic backends + MatchResult model
        └── store.py
```
