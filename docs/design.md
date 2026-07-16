# HireShire — Design Reference

The deep technical reference: per-phase internals, job-board API response shapes, the storage schema,
and every configuration key. For what HireShire is and how to run it, see the [README](../README.md).

Automated job search pipeline in four phases — **Scrape → Match → Tune → Apply** — plus a web
dashboard (Phase 5) layered on top.

## Architecture

Each phase is fully independent — its own entrypoint, module, config, and output directory.

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|
| **Run** | `python scraper.py` | `python matcher.py` | `python tuner.py` | `/apply` (Claude skill) | `python run_web.py` |
| **Module** | `hireshire/scrapers/` | `hireshire/matcher/` | `hireshire/tuner/` | `hireshire/applier/` | `hireshire/webapp/` + `frontend/` |
| **Config** | `config/scraper.yaml` | `config/matcher.yaml` | `config/tuner.yaml` | `config/applier.yaml` | `config/frontend.yaml` |
| **Output** | `jobs` / `run_companies` tables | `matches` table | `tuned_jobs` table + PDFs on disk | `applied` table | — (reads only) |

All tabular data lives in one SQLite database, **`data/hireshire.db`** (WAL mode), keyed by a shared `run_id` — see [Storage](#storage). Only binary artifacts (tuned resume `.tex`/`.pdf`, applier screenshots) stay on disk. The orchestrator summary (all shortlisted jobs + tuning status) lands in the `pipeline_results` table plus a per-run CSV/JSON under `data/pipeline/<run_id>/`.

Shared across all phases: `hireshire/models/`, `hireshire/storage/` (the SQLite layer, `hireshire/storage/db.py`).

## Data Flow

```
config/scraper.yaml  +  config/{greenhouse,ashby,lever}_companies.json  −  config/bad_slugs.json
        │
        ▼
python scraper.py  →  jobs + run_companies tables            (data/hireshire.db, keyed by run_id)
                                │
                                ▼
        resume.pdf + config/matcher.yaml
                                │
                                ▼
python matcher.py  →  matches table (shortlisted flag per row)
                                │
                                ▼
                       config/tuner.yaml
                                │
                                ▼
python tuner.py    →  tuned_jobs table + data/tuned/<run_id>/<job_id>/{<Name>_Resume.tex,.pdf}
                                │
                                ▼
                      config/applier.yaml
                                │
                                ▼
python applier.py  →  applied table  (+ screenshots under data/applied/screenshots/)
   or  /apply skill
```

Each phase reads the previous phase's output via `db.latest_run(<phase>)`. A **zero-job company writes only a `run_companies` metadata row — no `jobs` rows and no `[]` file.**

---

## Storage

All tabular data lives in a single **SQLite** database, `data/hireshire.db` (WAL mode, stdlib — no server), managed by [`hireshire/storage/db.py`](../hireshire/storage/db.py). One connection per DB path is shared process-wide and guarded by a lock; blocking writes are offloaded with `asyncio.to_thread`, and batched one transaction per company — so at 40k+ companies the scraper writes far faster than the old file-per-company layout while the pipeline stays responsive. The path is configurable per phase via the `db_path` config key (default `data/hireshire.db`).

| Table | Written by | Holds |
|---|---|---|
| `runs` | every phase | one row per (run_id, phase) with summary stats |
| `run_companies` | scraper | per-company status/job_count (incl. zero-job rows) |
| `jobs` | scraper | scraped `Job` rows (raw JSON + indexed columns) |
| `matches` | matcher | scored `MatchResult` rows with a `shortlisted` flag |
| `seen_jobs` | matcher | cross-run set of already-scored job IDs |
| `tuned_jobs` | tuner | per-job tune status + resume artifact paths |
| `pipeline_results` | orchestrator | per-run summary of shortlisted jobs |
| `applied` | applier / `/apply` | application records (dedup + audit) |

Only binaries stay on disk: tuned resume `.tex`/`.pdf` under `data/tuned/<run_id>/<job_id>/` and screenshots under `data/applied/screenshots/`. Runs accumulate (no auto-prune); reclaim space manually:

```bash
python scripts/prune_runs.py --keep 10          # keep the 10 most-recent runs
python scripts/prune_runs.py --before 2026-06-01 # drop runs older than a date
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

Put your resume PDF where `resume_path` in `config/matcher.yaml` and `config/applier.yaml` points, or update those keys to your own location.

For Phase 3 (Tuner), the pipeline needs three files under `data/resume_projects/`:
- `Your_Resume.tex` — your full resume LaTeX source (read by the Evaluator)
- `resume_template.tex` — a template with a `%{{EXPERIENCE_SECTIONS}}` placeholder (filled by the Assembler)
- `projects_bullets.yaml` — pre-authored LaTeX bullets for each project/work entry

Update the paths in `config/tuner.yaml` if you store these elsewhere.

> Note: `data/resume_projects/` and the pipeline output directories are gitignored — they hold personal data. See [Project Structure](#project-structure) for the full list.

---

## Phase 1: Scraper

Fetches open job listings from five job board APIs: **Greenhouse**, **Ashby**, **Lever**, **Workday**, and **BambooHR**. The scraper auto-detects which backend to use based on which JSON slug list a company came from.

### Company slug lists — `config/{ashby,greenhouse,lever,workday,bamboohr}_companies.json`

Company slugs are **not** stored in `scraper.yaml`. Each board has its own JSON file holding a flat array of board tokens (slugs), loaded by `hireshire/config.py`:

```json
// config/greenhouse_companies.json  →  job-boards.greenhouse.io/{slug}/jobs
["stripe", "anthropic", "figma", ...]
```

```json
// config/ashby_companies.json       →  jobs.ashbyhq.com/{slug}
// config/lever_companies.json        →  jobs.lever.co/{slug}
// config/bamboohr_companies.json     →  {slug}.bamboohr.com/careers
```

Workday slugs are **compound** — `company|wd#|site_id` — because the tenant host and career-site path can't be derived from a single token:

```json
// config/workday_companies.json      →  {company}.wd{#}.myworkdayjobs.com/{site_id}
["23andme|wd5|23", "7eleven|wd3|7eleven", ...]
```

The lists are large (currently ~8.3k Greenhouse, ~4.4k Lever, ~3.2k Ashby slugs, plus Workday and BambooHR). Add a company by appending its slug to the matching file.

### API response shapes

Each board has a different JSON schema. The scraper maps the fields below into the shared `Job` model (`hireshire/models/job.py`); fields not listed are ignored. Only the keys actually consumed are shown.

**Greenhouse** — `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` returns a `jobs` array (paginated via the RFC-5988 `Link` header). A second call per job, `.../jobs/{id}?questions=true`, fetches the application questions:

```jsonc
// list endpoint: /boards/{slug}/jobs?content=true
{
  "jobs": [
    {
      "id": 4012345,                                  // → job_id
      "internal_job_id": 4008888,                      // → internal_job_id
      "title": "Software Engineer, Backend",           // → title
      "updated_at": "2026-05-28T14:03:11-04:00",       // → updated_at
      "requisition_id": "ENG-1234",                    // → requisition_id
      "location": { "name": "San Francisco, CA" },     // → location.name
      "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/4012345",  // → absolute_url
      "content": "<p>We are looking for…</p>",          // → content_html / content_text (HTML-stripped)
      "departments": [
        { "id": 101, "name": "Engineering", "parent_id": null }  // → departments[]
      ],
      "offices": [
        { "id": 55, "name": "San Francisco", "location": "San Francisco, CA" }  // → offices[]
      ]
    }
  ],
  "meta": { "total": 1 }
}
```

```jsonc
// detail endpoint: /boards/{slug}/jobs/{id}?questions=true
{
  "id": 4012345,
  "content": "<p>Full HTML description…</p>",           // preferred over list `content` when present
  "questions": [
    {
      "label": "Are you legally authorized to work in the US?",  // → questions[].label
      "required": true,                                  // → questions[].required
      "type": "yes_no",                                  // → questions[].field_type
      "values": [ { "label": "Yes" }, { "label": "No" } ]  // → questions[].values[]
    }
  ]
}
```

**Ashby** — `GET https://api.ashbyhq.com/posting-api/job-board/{slug}` returns everything in one `jobs` array (no per-job detail call):

```jsonc
{
  "jobs": [
    {
      "id": "d8f9c0a1-2b34-4c56-8d90-abcdef123456",     // → job_id
      "title": "Full Stack Engineer",                    // → title
      "location": "Remote, US",                          // → location.name
      "secondaryLocations": [
        { "location": "New York, NY" }                   // → offices[]
      ],
      "department": "Engineering",                       // → departments[]
      "publishedAt": "2026-05-27T09:00:00.000Z",         // → updated_at
      "jobUrl": "https://jobs.ashbyhq.com/acme/d8f9c0a1", // → absolute_url
      "descriptionHtml": "<p>About the role…</p>",        // → content_html
      "descriptionPlain": "About the role…"              // → content_text
    }
  ]
}
```

**Lever** — `GET https://api.lever.co/v0/postings/{slug}?mode=json&limit=100&skip=0` returns a bare array (paginated by `skip`). An unknown company returns `{ "ok": false, "error": "…" }` instead:

```jsonc
[
  {
    "id": "a1b2c3d4-uuid",                              // → job_id
    "text": "Senior Software Engineer",                 // → title
    "hostedUrl": "https://jobs.lever.co/acme/a1b2c3d4", // → absolute_url
    "createdAt": 1716825600000,                          // epoch ms → updated_at
    "categories": {
      "location": "Remote",                              // → location.name
      "allLocations": ["Remote - US", "Remote - Canada"], // → offices[]
      "team": "Engineering",                             // → departments[] (falls back to `department`)
      "commitment": "Full-time"
    },
    "opening": "<div>…</div>",                            // ┐
    "description": "<div>Role description…</div>",        // ├ concatenated → content_html / content_text
    "additional": "<div>Benefits…</div>"                 // ┘
  }
]
```

**Workday** — the noisiest board. A `POST` to the tenant's CXS `jobs` endpoint (with a browser `User-Agent` and a JSON body) returns a paginated `jobPostings` list; a per-job `GET` on each `externalPath` returns the full `jobPostingInfo`. The compound slug `company|wd#|site_id` (e.g. `23andme|wd5|23`) encodes the tenant host and career-site path:

```jsonc
// POST /wday/cxs/{company}/{site}/jobs   body: {"appliedFacets":{},"limit":20,"offset":0,"searchText":""}
{
  "total": 137,
  "jobPostings": [
    {
      "title": "Software Engineer II",                  // → title (fallback if detail missing)
      "externalPath": "/job/San-Francisco/Software-Engineer-II_R-12345",  // → detail path & url fallback
      "locationsText": "San Francisco, CA",             // → location.name (fallback)
      "postedOn": "Posted 2 Days Ago",                  // → updated_at (relative, fallback)
      "bulletFields": ["R-12345"]                       // → job_id / requisition_id (fallback)
    }
  ]
}
```

```jsonc
// GET /wday/cxs/{company}/{site}{externalPath}
{
  "jobPostingInfo": {
    "jobReqId": "R-12345",                              // → job_id / requisition_id
    "title": "Software Engineer II",                    // → title
    "location": "San Francisco, CA",                    // → location.name
    "startDate": "2026-05-28",                          // → updated_at (ISO, preferred over postedOn)
    "jobDescription": "<p>…</p>",                        // → content_html / content_text
    "externalUrl": "https://acme.wd1.myworkdayjobs.com/en-US/site/job/…"  // → absolute_url
  }
}
```

**BambooHR** — a `GET` on the public careers `list` feed returns `{ "result": [...] }`; a per-job `GET` on `.../careers/{id}/detail` returns `{ "result": { "jobOpening": {...}, "formFields": {...} } }`. A dead board 302-redirects to the marketing site (treated as slug-not-found):

```jsonc
// GET https://{slug}.bamboohr.com/careers/list
{
  "result": [
    {
      "id": 42,                                         // → job_id
      "jobOpeningName": "Backend Engineer",             // → title
      "departmentLabel": "Engineering",                 // → departments[] (fallback)
      "location": { "city": "Austin", "state": "TX" }   // → location.name (fallback → "Austin, TX")
    }
  ]
}
```

```jsonc
// GET https://{slug}.bamboohr.com/careers/{id}/detail
{
  "result": {
    "jobOpening": {
      "jobOpeningName": "Backend Engineer",
      "description": "<p>…</p>",                          // → content_html / content_text
      "location": { "city": "Austin", "state": "TX" },   // → location.name
      "departmentLabel": "Engineering",                  // → departments[]
      "datePosted": "2026-05-28",                         // → updated_at
      "jobOpeningShareUrl": "https://acme.bamboohr.com/careers/42"  // → absolute_url
    },
    "formFields": {
      "resume": {                                         // key → questions[].field_type
        "label": "Resume",                                // → questions[].label
        "isRequired": true,                               // → questions[].required
        "options": [ { "id": 1, "text": "Option A" } ]    // → questions[].values[]
      }
    }
  }
}
```

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

### Output — `data/hireshire.db` (`jobs` + `run_companies` tables)

Each scraped `Job` is inserted into the `jobs` table (keyed by `run_id, job_id`); every company — including errored and **zero-job** ones — gets one row in `run_companies` (status, job_count, fetch time). A company that returned zero jobs therefore writes a single metadata row and **no `jobs` rows** (no more per-company `[]` files). The run summary lands in the `runs` table when the run finalises; `db.latest_run("scrape")` resolves the newest completed run.

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
  resume_path: data/resume_projects/Your_Resume.pdf
  projects_path: data/resume_projects/projects.md   # optional extra context appended to profile
  db_path: data/hireshire.db                         # shared SQLite datastore (all phases)

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

Reads automatically from the most recent scraper run (`db.latest_run("scrape")`). The `title_filter` pre-filters jobs by title before sending to the LLM, saving API calls. Each scored result is committed to the `matches` table immediately, so a mid-run crash can be resumed (`MatchStore.load_progress` reads back rows for a run whose `runs` row isn't yet finalised).

**Skip-LLM mode** — set `skip_llm: true` (or pass `--no-llm` to the orchestrator) to bypass scoring entirely: every job that passes the title filter is shortlisted with `relevance_score: 100` and `skip_reason: "llm_skipped"`. Useful for a zero-cost dry run of the full pipeline, or when the title filter alone is selective enough.

**Cross-run dedup** — scored job IDs are persisted to the `seen_jobs` table. On later runs any job ID already in that set is skipped before the title filter or LLM, so recurring pipeline runs never re-score the same listing.

### Output — `data/hireshire.db` (`matches` table)

Every scored job is written to the `matches` table with a `shortlisted` flag (`relevance_score >= threshold` and not skipped), the full `MatchResult` in `raw_json`, and the flat columns used for querying. Rejected/skipped jobs stay in the table (flag `0`) rather than a separate file. The tuner and applier read survivors via `db.load_shortlisted(run_id)`.

A shortlisted `MatchResult` (as stored in `raw_json`):

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
  resume_tex_path: data/resume_projects/Your_Resume.tex         # full resume for evaluator (Pass 1)
  resume_template_path: data/resume_projects/resume_template.tex  # template for assembler (Pass 2)
  projects_bullets_path: data/resume_projects/projects_bullets.yaml  # pre-authored LaTeX bullets
  projects_path: data/resume_projects/projects.md                 # legacy narrative (unused by optimizer)
  tuned_dir: data/tuned          # on-disk home for the assembled .tex/.pdf (binaries)
  db_path: data/hireshire.db     # shared SQLite datastore (all phases)

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

### Output — `data/tuned/<run_id>/` (binaries) + `tuned_jobs` table

The tuner reuses the orchestrator's `run_id`, so `data/tuned/<run_id>/` lines up with the scrape/matches/pipeline run. Per-job status, artifact paths, and the critique are recorded in the `tuned_jobs` table; the resume files themselves stay on disk (they're binaries the applier uploads):

```
data/tuned/2026-05-29T23-24-43Z/
└── 5101378008/                        # one directory per job
    ├── job_description.txt
    ├── critique.json                  # structured critique from Pass 1
    ├── <Name>_Resume.tex       # assembled LaTeX
    └── <Name>_Resume.pdf       # compiled PDF
```

---

## Phase 4: Applier

Phase 4 has two interchangeable implementations that share `config/applier.yaml`:

1. **`/apply` Claude Code skill** (recommended) — reads the latest pipeline results (`data/pipeline/<latest>/pipeline_results.json`) and fills + submits forms using Playwright MCP tools. Only processes jobs with `tuner_status == "tuned"` and a valid `resume_pdf`. Reads/records applied state through `scripts/applied_cli.py` (the `applied` table).
2. **`python applier.py`** — a standalone `browser-use` agent entrypoint that reads the latest **matches** run directly. Useful outside Claude Code.

Both share the `applied` table, so a job applied via one is skipped by the other.

Run either after the Tuner so the optimized resume PDFs are ready.

> **Safety:** `dry_run` gates live submission — when `true`, the applier fills forms but never clicks submit. Verify the value in `config/applier.yaml` before running.

### Configuration — `config/applier.yaml`

```yaml
settings:
  dry_run: false             # SAFETY: set true so forms are filled but never submitted
  headless: false            # show browser window while running
  inter_job_delay_s: 10      # seconds between applications
  max_steps: 40              # max browser-use agent steps per job (applier.py only)
  applied_dir: data/applied  # on-disk home for screenshots/ (records go to the DB)
  db_path: data/hireshire.db # shared SQLite datastore (all phases)
  resume_path: data/resume_projects/Your_Resume.pdf
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

Invoke the skill in Claude Code. It reads the latest `data/pipeline/*/pipeline_results.json`, skips jobs already recorded in the `applied` table (via `python scripts/applied_cli.py list`), and processes the remaining tuned jobs sequentially.

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

### Output — `applied` table (+ `data/applied/screenshots/`)

Application records go to the `applied` table (upserted by `job_id`); only the browser screenshots stay on disk under `data/applied/screenshots/`. Both `/apply` and `python applier.py` write here, so they share dedup state.

A row in the `applied` table:

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

### Phase gates — `enable_tuner` / `enable_applier`

Whether the tuner and applier phases run is driven by **config**, not just CLI flags:

| Key | File | Default | Effect |
|---|---|---|---|
| `enable_tuner` | `config/tuner.yaml` | `true` | `false` → the orchestrator replaces the tuner with the passthrough (all jobs `tuner_status: "skipped"`) |
| `enable_applier` | `config/applier.yaml` | `false` | `true` → the orchestrator invokes `/apply` after each pipeline run |

`main()` in `orchestrate.py` reads both loaders and computes the effective values, so the CLI flags act
as **explicit overrides** rather than the only control:

```python
skip_tuner = args.no_tuner or not load_tuner_config().settings.enable_tuner
apply      = args.apply    or load_applier_config().settings.enable_applier
```

Both keys are editable from the dashboard (Phase 5), which is why they live in config — the UI has no
way to pass CLI flags to a scheduled run.

Logs are written to `logs/orchestrate.log` (rotates at 5 MB, keeps 5 files).

### Output — `pipeline_results` table (+ `data/pipeline/<run_id>/`)

Each result streams into the `pipeline_results` table (one `INSERT` per row) and is appended to a per-run CSV as it lands; at the end of the run the rows are exported once to JSON (read by the `/apply` skill):

```
data/pipeline/2026-06-04T02-01-50Z/
├── pipeline_results.json   # full records, exported once from the table at run end
└── pipeline_results.csv    # same data, appended live during the run
```

Every shortlisted job appears regardless of tuner outcome. The `tuner_status` field indicates what happened (`"tuned"` / `"skipped"` / `"error"`), and `resume_tex`/`resume_pdf` are `null` when tuning didn't complete. With `--no-matcher` the export is written but empty (`[]`).

With `--apply`, the orchestrator invokes the `/apply` skill via `claude -p --permission-mode auto` after each run completes. The skill reads this file and applies only tuned jobs. This flag is ignored when `--no-tuner` or `--no-matcher` are set.

---

## Phase 5: Web Dashboard

A local single-user dashboard: **FastAPI backend** ([`hireshire/webapp/`](../hireshire/webapp/)) + a
**React/TypeScript SPA** ([`frontend/`](../frontend/)), served together by
[`run_web.py`](../run_web.py). Built additively — it imports `db.py`, the phase config loaders, and the
phase entrypoint scripts rather than reimplementing them; the only pipeline-code change it required was
the `enable_tuner` / `enable_applier` keys documented above.

Layout: a full-height **chat** panel on the left; an **editable config** panel (top) and a **job list**
(bottom) on the right. The chat and the config panel both write the job list's filter state.

```
hireshire/webapp/
├── app.py            # FastAPI app: CORS, router mounts, static mount for frontend/dist
├── config.py         # config/frontend.yaml loader (chat provider/model, host/port, cors)
├── deps.py           # ReadDB (read-only sqlite) + cached settings singletons
├── jobs_query.py     # the unified job-list query shared by /api/jobs and the chat tools
├── models.py         # request/response schemas (JobRow, ConfigResponse, RunState, ...)
├── config_spec.py    # whitelist of dashboard-editable fields + docs + validation hooks
├── runner.py         # per-phase subprocess registry (run/stop/status)
├── routers/{data,config_api,runs,chat}.py
└── agent/{graph,tools,providers}.py   # LangGraph chat agent
```

### Configuration — `config/frontend.yaml`

```yaml
chat:
  provider: openai           # anthropic / openai / gemini
  model: gpt-4o-mini         # model id for the chosen provider (key comes from .env)
  max_tokens: 4096
  temperature: 0.0           # openai/gemini only — see the Anthropic note below
server:
  host: 127.0.0.1
  port: 8000
  cors_origins: ["http://localhost:5173", "http://127.0.0.1:5173"]   # Vite dev server
db_path: data/hireshire.db   # opened READ-ONLY by the dashboard
```

### Read-only data access

[`hireshire/webapp/deps.py`](../hireshire/webapp/deps.py) defines `ReadDB`, which opens its **own**
sqlite connection with `PRAGMA query_only=ON`. Two reasons this is a separate connection rather than
reusing `get_db()`:

1. `query_only` makes it structurally impossible for the dashboard to mutate the datastore.
2. `get_db()` runs `_init_schema()` on first open — a write. WAL already allows concurrent readers
   alongside the pipeline's single writer, so a dedicated read connection never blocks or is blocked.

`jobs_query.py` is the single source both `/api/jobs` and the chat's search tools use: it reads
`pipeline_results` for a run (falling back to shortlisted `matches` when the run has no pipeline rows),
layers on applied status from the cross-run `applied` table, and applies the filters.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/runs` | all run ids + latest run per phase |
| `GET` | `/api/jobs` | unified job list — `run_id`, `min_score`, `applied`, `q`, `location`, `job_ids`, `limit` |
| `GET` | `/api/applied` | full applied history |
| `GET` | `/api/runs/{run_id}/summary` | per-phase rows + counts for a run |
| `GET` | `/api/resume/{run_id}/{job_id}` | streams the tuned resume PDF from `tuned_jobs` |
| `GET`/`PUT` | `/api/config/{phase}` | read / write whitelisted config fields |
| `POST` | `/api/runs/{phase}` · `/{phase}/stop` | start / stop a phase subprocess |
| `GET` | `/api/runs/status` · `/api/runs/{phase}/logs` | status for all phases · SSE log tail |
| `POST` | `/api/chat` | SSE stream from the LangGraph agent |

### Config editor — comment-preserving YAML round-trip

[`routers/config_api.py`](../hireshire/webapp/routers/config_api.py) edits `config/*.yaml` **in place**
with `ruamel.yaml`, never PyYAML — the config files carry extensive inline comments that a
`yaml.safe_dump()` round-trip would silently destroy. Two details make the diff minimal:

- `_yaml.indent(mapping=2, sequence=4, offset=2)` matches the existing list style, so editing one key
  doesn't reflow every sequence item.
- `_dump_doc()` re-applies the file's original newline style — ruamel always emits LF, but these files
  are CRLF on Windows. Without this, a one-key edit rewrites all ~84 lines.

A `PUT` is: whitelist-check every key → apply to the parsed doc → **re-validate the whole file against
that phase's pydantic settings model** → only then write. Unknown keys → `400`; validation failure →
`422` with field-level errors.

[`config_spec.py`](../hireshire/webapp/config_spec.py) is the whitelist. Only these are exposed
(everything else — concurrency, timeouts, intervals, encoder model, `scrape_details`, `headless`,
`max_steps` — stays hand-edited):

| Phase | Fields |
|---|---|
| scraper | `location_filter`, `max_age_hours` |
| matcher | `threshold`, `provider`, `model`, `skip_llm`, `title_filter.include_keywords`, `title_filter.exclude_keywords` |
| funnel | `funnel.enabled`, `funnel.encoder.threshold`, `funnel.encoder.targets` |
| tuner | `enable_tuner`, `resume_tex_path`, `resume_template_path`, `projects_bullets_path`, `evaluator_provider`/`_model`, `optimizer_provider`/`_model` |
| applier | `enable_applier`, `dry_run`, `first_name`, `last_name`, `email`, `phone`, `generate_cover_letter` |

Each `FieldSpec` carries a `type` (`bool`/`int`/`float`/`str`/`str_list`/`enum`), a `doc` string, and
optional `options` — the frontend renders the whole form from that, and the chat's `explain_config`
tool reads the same `doc` strings. matcher and funnel share `config/matcher.yaml`.

### Run control

[`runner.py`](../hireshire/webapp/runner.py) holds a module-level registry of `subprocess.Popen`
handles keyed by phase, launching the existing entrypoints (`scraper.py`, `matcher.py`, `tuner.py`,
`applier.py`, `orchestrate.py`) with `sys.executable` and redirecting output to `logs/<phase>.log`.
`_build_argv()` translates a flags dict into CLI args per phase. One process per phase at a time;
starting a phase that's already running returns `409`. Stop is graceful (`terminate`, then `kill`
after 8 s). `/api/runs/{phase}/logs` primes with the last 80 lines then polls for appends.

### Chat agent

[`agent/graph.py`](../hireshire/webapp/agent/graph.py) builds a LangGraph `create_react_agent` over the
model from `config/frontend.yaml`. Tools ([`agent/tools.py`](../hireshire/webapp/agent/tools.py)) split
in two:

- **Read tools** run directly against `ReadDB`: `search_jobs`, `get_top_matches`, `run_stats`,
  `list_runs`, `explain_config`.
- **Run tools** (`run_phase`, `stop_phase`) are **confirmation-gated** — they never touch a subprocess.
  They return a `{action, phase, flags}` proposal; the UI renders a Confirm card, and only a click
  fires `POST /api/runs/{phase}`. The system prompt tells the agent to say it has prepared the run and
  ask the user to confirm.

`POST /api/chat` consumes `agent.astream(stream_mode=["messages", "updates"])` and translates it into
an SSE contract:

| Event | Payload | Consumed by |
|---|---|---|
| `token` | assistant text delta | chat bubble |
| `tool_call` | `{name}` | tool chip |
| `job_results` | `{job_ids, count, jobs}` | **loads the job-list panel** |
| `run_proposal` | `{action, phase, flags}` | Confirm/Cancel card |
| `done` / `error` | — | stream terminators |

Token emission is filtered to `AIMessageChunk` only — in `messages` mode LangGraph also surfaces
`ToolMessage`s, and without the filter a tool's raw JSON leaks into the chat bubble.

### Frontend

Vite + React + TS under `frontend/`. `src/state/store.ts` (Zustand) holds the shared job-list filter;
both the config panel and the chat's `job_results` event write it, and `JobListPanel` is a pure
function of it — that's the whole chat→panel wiring. `components/config/PhaseForm.tsx` is generic: it
renders any phase's form from the `types`/`docs`/`options` in the `GET /api/config/{phase}` response,
so adding a field to `config_spec.py` needs no frontend change.

`npm run dev` proxies `/api` → `127.0.0.1:8000`; `npm run build` emits `frontend/dist`, which
`app.py` mounts at `/` when present, so `python run_web.py` alone serves API + UI.

### Two gotchas worth recording

- **`sse-starlette` emits CRLF.** Frames terminate with `\r\n\r\n`, not `\n\n`. A client splitting on
  `"\n\n"` silently parses nothing and renders an empty response with no error. `frontend/src/lib/sse.ts`
  strips `\r` from each decoded chunk before framing.
- **Anthropic Opus 4.8 / Sonnet 5 reject `temperature`.** Sampling params were removed on those models
  and return `400 — temperature is deprecated for this model`. `agent/providers.py` therefore omits
  sampling params for the `anthropic` branch and passes `temperature` only for openai/gemini.

---

## Project Structure

```
HireShire/
├── scraper.py              # Phase 1 entrypoint
├── matcher.py              # Phase 2 entrypoint
├── tuner.py                # Phase 3 entrypoint
├── applier.py              # Phase 4 entrypoint (standalone browser-use agent)
├── orchestrate.py          # Pipeline orchestrator (runs phases 1–3 automatically)
├── run_web.py              # Phase 5 entrypoint (uvicorn → FastAPI + built SPA)
├── requirements.txt
├── .env.example
├── config/
│   ├── scraper.yaml               # Phase 1 run settings (no companies)
│   ├── greenhouse_companies.json  # Greenhouse slug list
│   ├── ashby_companies.json       # Ashby slug list
│   ├── lever_companies.json       # Lever slug list
│   ├── workday_companies.json     # Workday slug list (compound: company|wd#|site_id)
│   ├── bamboohr_companies.json    # BambooHR slug list
│   ├── bad_slugs.json             # known-404 slugs, auto-skipped and appended each run
│   ├── matcher.yaml               # Phase 2 config (+ title_filter, funnel)
│   ├── tuner.yaml                 # Phase 3 config
│   ├── applier.yaml               # Phase 4 config
│   └── frontend.yaml              # Phase 5 config (chat provider/model, host/port)
├── scripts/
│   ├── verify_bad_slugs.py # re-validate config/bad_slugs.json (--prune / --platform)
│   ├── prune_runs.py       # manual retention: drop old runs from the DB (--keep / --before)
│   ├── applied_cli.py      # list/record applied jobs in the DB (used by the /apply skill)
│   └── db_stats.py         # inspect the DB: tables + row counts + latest run per phase
├── tests/
│   ├── test_projects_bullets.py   # pytest wrapper around the tuner bullet lint
│   └── test_db.py                 # SQLite storage layer unit tests
├── data/
│   ├── hireshire.db        # SQLite datastore — ALL tabular data, all phases (gitignored)
│   ├── tuned/              # Phase 3 binaries: assembled resume .tex/.pdf per job (gitignored)
│   ├── applied/screenshots/ # Phase 4 browser screenshots (gitignored)
│   ├── pipeline/           # per-run pipeline_results.{csv,json} export (gitignored)
│   └── resume_projects/    # Resume .tex / template / bullets (gitignored — personal)
├── logs/                   # Orchestrator logs (gitignored)
├── .claude/
│   ├── skills/apply.md     # Phase 4 as a Claude Code skill (/apply)
│   └── commands/apply.md   # prompt the orchestrator's --apply flag feeds to `claude -p`
└── hireshire/
    ├── config.py            # Scraper config loader (settings + slug JSON files → AppConfig)
    ├── http_client.py       # Shared HTTP client with retry/backoff
    ├── models/job.py        # Shared Job data model
    ├── storage/
    │   ├── db.py            # SQLite datastore: schema, connection, all read/write helpers
    │   └── json_store.py    # scraper's RunStore facade over db.py
    ├── scrapers/
    │   ├── base.py
    │   ├── exceptions.py    # SlugNotFoundError (drives bad-slug tracking)
    │   ├── greenhouse.py
    │   ├── ashby.py
    │   ├── lever.py
    │   ├── workday.py       # list→detail board (compound slug)
    │   └── bamboohr.py      # list→detail board
    ├── funnel/
    │   ├── config.py
    │   ├── funnel.py        # title filter → MiniLM semantic gate → lazy detail fetch
    │   ├── relevance.py     # encoder-based title/target cosine similarity
    │   └── detail_fetcher.py # hydrates descriptions for surviving list-only jobs
    ├── matcher/
    │   ├── config.py
    │   ├── resume.py        # PDF text extraction (pdfplumber)
    │   ├── loader.py
    │   ├── prompts.py       # scorer system prompt (rubric)
    │   ├── scorer.py        # Gemini/OpenAI/Anthropic backends + MatchResult model
    │   ├── seen.py          # cross-run job-ID dedup (seen_jobs table)
    │   ├── title_filter.py  # keyword pre-filter before LLM scoring
    │   └── store.py         # MatchStore: writes the matches table
    ├── tuner/
    │   ├── config.py
    │   ├── evaluator.py     # Pass 1: recruiter critique → EvaluatorResult
    │   ├── optimizer.py     # Pass 2: JSON project selector → SelectionResult
    │   ├── assembler.py     # code-assembles LaTeX from template + pre-authored bullets
    │   ├── lint.py          # validates projects_bullets.yaml corpus
    │   ├── prompts.py       # system prompts for evaluator and selector
    │   ├── loader.py
    │   └── store.py         # PDF compilation + tuned_jobs table rows
    ├── applier/
    │   ├── config.py
    │   ├── answerer.py      # LLM-based question answering
    │   ├── filler.py        # browser-use form filling
    │   ├── loader.py
    │   └── store.py         # AppliedStore: writes the applied table
    └── webapp/              # Phase 5 backend (see Phase 5: Web Dashboard)
        ├── app.py           # FastAPI app + static mount for frontend/dist
        ├── config.py        # config/frontend.yaml loader
        ├── deps.py          # ReadDB: read-only (query_only) sqlite accessor
        ├── jobs_query.py    # unified job-list query (shared by /api/jobs + chat tools)
        ├── config_spec.py   # whitelist of dashboard-editable config fields + docs
        ├── runner.py        # per-phase subprocess registry
        ├── models.py
        ├── routers/         # data.py, config_api.py, runs.py, chat.py
        └── agent/           # graph.py, tools.py, providers.py (LangGraph chat agent)

frontend/                    # Phase 5 SPA (Vite + React + TS)
├── package.json
├── vite.config.ts           # dev server proxies /api → 127.0.0.1:8000
├── dist/                    # `npm run build` output, served by app.py (gitignored)
└── src/
    ├── App.tsx              # 2-pane shell
    ├── components/          # ChatPanel, ConfigPanel, JobListPanel, config/*
    ├── lib/                 # api.ts, sse.ts (CRLF-safe SSE reader), types.ts
    └── state/store.ts       # shared job-list filter (chat + config both write it)
```
