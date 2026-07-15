# HireShire — Design Decisions

Why the code is structured the way it is — the significant architectural choices, with the reasoning
and tradeoffs behind each. For *what* the system does and how each piece works, see
[design.md](design.md).

Each entry is a lightweight ADR: **Context → Decision → Rationale → Tradeoffs**, one statement each.

---

## Quick Reference

| # | Decision | Primary Files |
|---|----------|--------------|
| 1 | Five-phase independent pipeline | `scraper.py`, `matcher.py`, `tuner.py`, `.claude/skills/apply.md`, `run_web.py` |
| 2 | Asyncio + sentinel queue streaming | `orchestrate.py` |
| 3 | Protocol-based LLM backends | `hireshire/matcher/scorer.py`, `hireshire/tuner/optimizer.py` |
| 4 | Provider-specific retry with dynamic delay | `hireshire/matcher/scorer.py`, `hireshire/tuner/optimizer.py` |
| 5 | Title filter before LLM scoring | `hireshire/matcher/title_filter.py`, `config/matcher.yaml` |
| 6 | Crash-survivable matcher writes (`matches` table) | `hireshire/matcher/store.py` |
| 7 | Seen-jobs dedup across runs (`seen_jobs` table) | `hireshire/matcher/seen.py` |
| 8 | Two-pass tuner (Evaluator → Optimizer) | `hireshire/tuner/evaluator.py`, `hireshire/tuner/optimizer.py` |
| 9 | LaTeX resume with template substitution | `hireshire/tuner/assembler.py`, `data/resume_projects/` |
| 10 | Two-directional code-only page fit | `tuner.py` |
| 11 | Keyword adjustment by bullet index | `hireshire/tuner/assembler.py` |
| 12 | `ClaudeCodeOptimizerBackend` via CLI subprocess | `hireshire/tuner/optimizer.py` |
| 13 | Applier as a Claude Code skill | `.claude/skills/apply.md` |
| 14 | Orchestrator scheduling via `asyncio.sleep` | `orchestrate.py` |
| 15 | Shared HTTP client + opt-in retry decorator | `hireshire/http_client.py` |
| 16 | Company slugs as bulk JSON lists | `hireshire/config.py`, `config/*_companies.json` |
| 17 | Self-healing bad-slug tracking | `config/bad_slugs.json`, `hireshire/scrapers/exceptions.py`, `scripts/verify_bad_slugs.py` |
| 18 | Skip-LLM matcher mode | `matcher.py`, `config/matcher.yaml` |
| 19 | Single SQLite datastore for all phases | `hireshire/storage/db.py`, `scripts/prune_runs.py` |
| 20 | Encoder-based relevance funnel | `hireshire/funnel/`, `config/matcher.yaml` |
| 21 | Deferred list→detail fetch | `config/scraper.yaml`, `hireshire/funnel/detail_fetcher.py` |
| 22 | Two-tier scraper throttling | `config/scraper.yaml`, `hireshire/rate_limit.py` |
| 23 | Dashboard as a read-only layer | `hireshire/webapp/deps.py`, `hireshire/webapp/config_spec.py` |
| 24 | Phase gates in config, not just CLI flags | `orchestrate.py`, `config/tuner.yaml`, `config/applier.yaml` |

---

## 1. Five-Phase Independent Pipeline

**Context:** Job automation spans genuinely distinct concerns — discovering listings, scoring them against a resume, tailoring the resume, submitting the form, and observing all of it — and an early prototype wired them into one script.
**Decision:** Split into five independent phases (Scraper, Matcher, Tuner, Applier, and the Phase 5 dashboard), each with its own entrypoint, `config/<phase>.yaml`, `hireshire/<phase>/` subpackage, and output tables, handing off only via `db.latest_run(<phase>)`.
**Rationale:** A bad scoring run never forces a re-scrape, and any phase can be iterated, tested, or replaced without touching the others.
**Tradeoffs:** Five entrypoints and configs to keep in sync, the handoff convention is implicit and unvalidated, and #21's deferred detail fetch has since punched a real hole in the independence claim.

## 2. Asyncio + Sentinel Queue Streaming

**Context:** Running phases sequentially leaves the tuner idle for the ~30 minutes the scraper and matcher take.
**Decision:** `orchestrate.py` wires the phases through `asyncio.Queue`s under a single `asyncio.gather()`, each phase signalling completion by putting a `None` sentinel on its output queue.
**Rationale:** The pipeline is almost entirely I/O-bound, so downstream phases start on the first batch and backpressure comes free when a consumer falls behind.
**Tradeoffs:** The single-`None` protocol is correct only while each phase has exactly one producer — adding intra-phase parallelism would require a counted sentinel (introduced in `74869c3`).

## 3. Protocol-Based LLM Backends

**Context:** The project targets Gemini, OpenAI, Anthropic, and a local Claude CLI, and early versions hardcoded Gemini calls into the scorer.
**Decision:** `LLMBackend` is a `@runtime_checkable` Protocol with one async `call(prompt, system_prompt) → ScoringSchema`, resolved by `make_backend()` in the order `settings.provider` → `LLM_PROVIDER` env var → `"gemini"`.
**Rationale:** Duck typing makes adding a provider purely additive, and per-phase config keys let the evaluator run a cheap model while the optimizer runs a better one.
**Tradeoffs:** Retry and throttling logic is duplicated across the backends in `scorer.py` and `optimizer.py` rather than living in one shared decorator.

## 4. Provider-Specific Retry with Dynamic Delay

**Context:** Every provider signals backoff differently — Gemini returns `retryDelay` inside the JSON error body, OpenAI and Anthropic set a `Retry-After` header.
**Decision:** Each backend carries its own `tenacity` decorator whose `wait` function reads that provider's native delay field, all with `stop=stop_never`.
**Rationale:** Reading the API's own recommendation beats a fixed backoff that either overshoots or hammers a still-limited endpoint, and unbounded retries are worth it when one legitimate run takes 45+ minutes on free-tier limits.
**Tradeoffs:** The Gemini wait function regexes `retryDelay` out of a stringified error, which silently breaks if that message format ever changes.

## 5. Title Filter Before LLM Scoring

**Context:** A run surfaces hundreds of listings, many obviously out of scope (iOS Engineer, VP of Sales), and LLM scoring costs both quota and wall-clock time.
**Decision:** `config/matcher.yaml`'s `title_filter.include_keywords` / `exclude_keywords` are substring-matched case-insensitively against titles before any LLM call, with failures recorded as `skipped` / `skip_reason: "title_excluded"`.
**Rationale:** A deterministic, zero-API-cost pre-filter removes more than half the candidate set before any paid work happens.
**Tradeoffs:** Substring matching is blunt — a title like "SDE II" never matches `engineer` — and #20's funnel has since demoted this to the fallback path used only when the encoder is disabled.

## 6. Crash-Survivable Matcher Writes via the `matches` Table

**Context:** Scoring 200 jobs at a 13-second free-tier interval takes ~45 minutes, and a crash or interrupt mid-run must not discard the work already done.
**Decision:** Each `MatchResult` is `INSERT OR REPLACE`d into the `matches` table the moment it is scored, and `MatchStore.load_progress` reads those rows back on restart for any run whose `runs` row is not yet finalised.
**Rationale:** The committed rows *are* the final result set (queried by the `shortlisted` flag), so there is no promote-progress-to-final step that could itself be interrupted.
**Tradeoffs:** Resume correctness now rests on the runs-row-as-completion-marker convention rather than the presence or absence of a temp file.

## 7. Seen-Jobs Dedup Across Runs

**Context:** The orchestrator re-runs every 4 hours against boards that keep the same listings live for weeks.
**Decision:** `hireshire/matcher/seen.py` persists every scored `job_id` to the `seen_jobs` table and skips known IDs before the title filter or any LLM call.
**Rationale:** Makes the matcher idempotent across runs — a job is scored exactly once, ever.
**Tradeoffs:** The set is deliberately cross-run and never pruned (`prune_runs.py` won't touch it), and a job reposted under the same ID is silently skipped forever.

## 8. Two-Pass Tuner (Evaluator → Optimizer)

**Context:** Tailoring a resume in a single pass needs one enormous prompt — full LaTeX, full job description, every candidate project — which hallucinates and returns generic results.
**Decision:** Pass 1 (`ResumeEvaluator`) returns a structured recruiter critique as `EvaluatorResult`, and Pass 2 (`ResumeOptimizer`) reads that critique plus a titles-only project roster and returns a `SelectionResult`.
**Rationale:** Each pass gets a tight prompt and a well-defined output schema, and the two can run on different, cheaper models via `evaluator_provider` / `optimizer_provider`.
**Tradeoffs:** Two round-trips per job roughly doubles tuner latency, and Pass 2's selection quality is gated entirely on Pass 1's critique quality.

## 9. LaTeX Resume with Template Substitution

**Context:** The resume must fit exactly one page and accept keyword injection at bullet granularity, neither of which plain text or Markdown can guarantee.
**Decision:** A fixed `resume_template.tex` carries a single `%{{EXPERIENCE_SECTIONS}}` marker that `assembler.py` fills from pre-authored bullets in `projects_bullets.yaml`, compiled with `pdflatex`.
**Rationale:** LaTeX gives deterministic layout and a queryable page count (which enables #10), while pre-authored bullets keep the LLM selecting and adjusting rather than writing prose from scratch.
**Tradeoffs:** Requires a working `pdflatex` install, and any LaTeX syntax error in an LLM-generated keyword override breaks the compile.

## 10. Two-Directional Code-Only Page Fit

**Context:** After assembly the PDF is often either longer than one page or a sparse single page with a large empty bottom margin — both look unpolished.
**Decision:** A code-only loop reads page count and bottom margin after each compile, dropping the summary → least-relevant projects → bottom bullets on overflow, and re-enabling the summary when the bottom margin exceeds `SPARSE_MARGIN_PT = 45.0` (reverting if that overflows).
**Rationale:** An LLM trimming pass would be slow, non-deterministic, and hard to debug, whereas each `pdflatex` compile takes ~1s and the optimizer's ranking already encodes what to drop first.
**Tradeoffs:** Trimming is greedy (whole projects go before individual bullets), a pathological input triggers many recompiles, and the 45 pt threshold is a hand-tuned constant.

## 11. Keyword Adjustment by Bullet Index

**Context:** The optimizer needs to swap individual bullets for keyword-enriched variants without discarding the quality pre-authored language around them.
**Decision:** `SelectionResult.keyword_adjustments` is a `dict[project_id, list[str | None]]` that the assembler applies positionally, where `None` preserves the original bullet.
**Rationale:** Index addressing lets the LLM inject a keyword at exactly one bullet and keeps the response compact, since only non-`None` entries carry meaning.
**Tradeoffs:** Reordering or inserting a bullet in `projects_bullets.yaml` silently invalidates every existing override index — addressing by a stable bullet ID would be more robust but complicates both the schema and the prompt.

## 12. `ClaudeCodeOptimizerBackend` via CLI Subprocess

**Context:** The optimizer pass can benefit from Claude Code capabilities — tool use, extended thinking, skills — that the REST API doesn't expose.
**Decision:** `ClaudeCodeOptimizerBackend` spawns `claude -p --permission-mode auto`, pipes the prompt through stdin, and parses stdout with the same helpers as the API backends, activated by `optimizer_provider: claude_code`.
**Rationale:** Lets a pure-Python pipeline reach Claude Code-only capabilities without waiting for an API equivalent.
**Tradeoffs:** ~2–5s of cold-start per call, no structured-output guarantee (stdout is parsed heuristically), and it needs Claude Code installed, authenticated, and on `PATH` — experimental, not a default.

## 13. Applier as a Claude Code Skill

**Context:** Application forms differ per board with custom questions and multi-step flows, so a programmatic approach would need fragile per-platform selectors requiring endless maintenance.
**Decision:** `.claude/skills/apply.md` instructs a Claude agent to drive Playwright MCP tools, reading identity from `config/applier.yaml`, job context from the exported pipeline results, and recording state through `scripts/applied_cli.py`.
**Rationale:** Delegating form reasoning to Claude handles label-based field detection, cover letters, and form variants without hardcoded selectors — and Playwright MCP isn't available as a plain Python dependency anyway.
**Tradeoffs:** Non-deterministic and hard to unit-test, and it only runs inside Claude Code — the standalone `applier.py` browser-use entrypoint covers running outside it, sharing `config/applier.yaml` and the `applied` table (introduced in `b4afe87`).

## 14. Orchestrator Scheduling via `asyncio.sleep`

**Context:** The pipeline needs to re-run every few hours, which could mean OS cron, a scheduler library, or a self-managed loop.
**Decision:** `orchestrate.py` schedules with a `while True` loop around `asyncio.sleep(interval)`, with `--now` for an immediate first run and `--once` to exit after one.
**Rationale:** Self-contained — no crontab, no systemd unit, no extra dependency — and changing the cadence is a `--interval` flag rather than an edit somewhere outside the project.
**Tradeoffs:** The schedule lives and dies with the process, with no missed-run recovery and no locking against a second instance — acceptable for a personal tool, not for a service.

## 15. Shared HTTP Client + Opt-In Retry Decorator

**Context:** All five scrapers hit the same categories of transient failure — 429 rate limits, 5xx errors, connection timeouts.
**Decision:** `hireshire/http_client.py` splits the concern in two: `build_client(timeout_s)` returns an `httpx.AsyncClient` (`User-Agent: HireShire/0.1 (job scraper)`, `follow_redirects=True`), while a separate `make_retry_decorator(attempts)` that each scraper opts into honours the server's `Retry-After` (capped at 120s) before falling back to `wait_exponential(multiplier=2, min=1, max=60)`, both plus ≤0.5s of jitter and bounded by `retry_attempts`.
**Rationale:** Keeping transport config DRY while leaving retry opt-in lets each board choose its own posture, and honouring the server's own `Retry-After` beats guessing at a backoff.
**Tradeoffs:** Because retry is opt-in rather than baked into the client, a new scraper that forgets the decorator silently gets no retries at all.

## 16. Company Slugs as Bulk JSON Lists

**Context:** `scraper.yaml` originally listed each company inline as YAML, which stops being readable, diffable, or fast to parse once the corpus is 40,065 slugs across five boards.
**Decision:** Slugs live in five flat JSON arrays — `config/{greenhouse,ashby,lever,workday,bamboohr}_companies.json` (8,333 / 3,163 / 4,369 / 12,884 / 11,316) — which `hireshire/config.py` loads into `CompanyConfig` objects, leaving `scraper.yaml` to hold `settings` only.
**Rationale:** A flat array of strings is the most compact, diffable, machine-generatable form for a large homogeneous corpus, and separating it from human-edited settings keeps each file's purpose obvious.
**Tradeoffs:** Per-company display names are lost (`name == slug`), nothing validates that a slug belongs to the board file it sits in, and Workday breaks the flat-token rule with a compound `company|wd<N>|site_id` token whose malformed form raises `SlugNotFoundError` — a parse error wearing a not-found costume.

## 17. Self-Healing Bad-Slug Tracking

**Context:** Bulk-sourced slug lists inevitably contain thousands of dead entries, and re-requesting them every run wastes time on guaranteed failures.
**Decision:** Scrapers raise a typed `SlugNotFoundError` on a genuine not-found — Greenhouse/Ashby 404, Lever `{"ok": false}`, Workday `404/410/422` or a malformed token, BambooHR a 302 redirect to the marketing site (it fetches the list with `follow_redirects=False`), `403/404/410`, or non-JSON — which `scraper.py` persists to `config/bad_slugs.json` and filters out before any HTTP call on later runs.
**Rationale:** The list accretes automatically during normal runs while `scripts/verify_bad_slugs.py --prune` recovers slugs that became reachable again, so runs get monotonically faster and only a genuine not-found signal — never a transient error — can mark a slug bad.
**Tradeoffs:** A wrongly-flagged slug stays dead until the verify script is run by hand, `bad_slugs.json` is committed so it drifts between branches, and the taxonomy is now three-way rather than binary — `BoardBlockedError` (Workday `401/403`, a WAF or IP-reputation block) must deliberately *not* prune the slug.

## 18. Skip-LLM Matcher Mode

**Context:** LLM scoring is the slowest and only paid step in Phases 1–2, and it is pure waste when smoke-testing the pipeline or running with no API budget.
**Decision:** `skip_llm: true` (or the orchestrator's `--no-llm`) constructs no backend at all and emits every title-passing job as a synthetic `MatchResult` with `relevance_score: 100` and `skip_reason: "llm_skipped"`.
**Rationale:** Reuses the entire existing pipeline — filter, dedup, incremental storage, queue forwarding — with only the scoring step swapped for a constant, leaving the free, deterministic title filter as the sole selectivity.
**Tradeoffs:** `threshold` becomes meaningless and everything title-passing flows to the tuner, and the auto-assigned score of 100 misleads any consumer that sorts or gates on relevance.

## 19. Single SQLite Datastore for All Phases

**Context:** The original file-per-run layout created up to ~40k tiny JSON files per scrape (most of them `[]`), duplicated "latest run" logic in four places, and rewrote the whole pipeline JSON on every record (O(n²)).
**Decision:** One WAL-mode SQLite database, `data/hireshire.db` (`hireshire/storage/db.py`), holds nine tables — `meta`, `runs`, `run_companies`, `jobs`, `matches`, `seen_jobs`, `pipeline_results`, `tuned_jobs`, `applied` — keyed by a shared `run_id` behind one lock-guarded connection with `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, and `foreign_keys=ON`, leaving only true binaries on disk.
**Rationale:** Collapsing to one indexed file removes the per-company filesystem metadata cost that dominated at 40k companies, bounds inodes to O(1) per run, and turns crash-resume, dedup, and applied-tracking into plain table reads.
**Tradeoffs:** One file is a single point of contention and corruption risk (mitigated by WAL plus one-writer serialisation), and retention is manual and opt-in — runs accumulate until `scripts/prune_runs.py --keep N` is run, relying on the `foreign_keys=ON` cascades to clean up.

## 20. Encoder-Based Relevance Funnel

**Context:** The keyword title filter (#5) is blunt in both directions — it misses "SDE II" and waves through "Solutions Engineer, Sales" — but an LLM classifier per title would defeat the point of a cheap pre-filter.
**Decision:** `hireshire/funnel/` gates the matcher's entry in four stages — code exclude → code include fast-pass → MiniLM cosine similarity against configurable `encoder.targets` at `threshold: 0.35` → lazy detail hydration — as a drop-in for `apply_title_filter` returning the same `(to_score, filtered_results)` shape.
**Rationale:** A local sentence-transformer costs no API quota and catches semantic matches keywords miss, the include fast-pass keeps obvious hits cheap and immune to encoder mistuning, and `encoder.targets` is a retargeting seam that repoints the entire hunt with no code change.
**Tradeoffs:** It pulls `sentence-transformers` (and torch) into the dependency tree for one cosine comparison, `threshold: 0.35` is flagged in-config as needing empirical tuning on real titles, and an empty `targets` list silently falls back to the classic include rule.

## 21. Deferred List→Detail Fetch

**Context:** Workday and BambooHR need a second HTTP call per job just to get the description, which is enormous waste when most of those jobs are about to be dropped on title alone.
**Decision:** `scrape_details: false` makes those boards scrape list-only (`content_text` left NULL) and defers hydration to the funnel, which fetches descriptions only for titles that survive the gate and upserts them back into `jobs`; `greenhouse_fetch_questions: false` makes the same trade for Greenhouse's questions-only detail call.
**Rationale:** Detail traffic collapses from the full corpus to the survivor set, which is the single largest saving available on the two list→detail boards.
**Tradeoffs:** This couples the scraper to `funnel.enabled: true` across two separate config files and fails *silently wrong* when the funnel is off — those jobs reach the scorer with no content and are simply skipped — which is the concrete hole in #1's independence claim; `greenhouse_fetch_questions: false` likewise costs Phase 4 its application questions.

## 22. Two-Tier Scraper Throttling

**Context:** 40k companies across five boards need per-board throttling, but a naive per-company timeout burns its budget while the company sits waiting in a queue.
**Decision:** `company_concurrency` sets per-board counts of long-lived workers draining an **untimed** queue, `rate_limits` caps in-flight calls per board with an optional `min_interval_s`, `request_timeout_s: 30` is the real per-call bound, and `company_timeout_s: 600` is a rarely-firing backstop whose clock starts at worker pickup.
**Rationale:** Separating the company-level pool from the per-call limiter keeps queue-wait untimed, so a company's timeout budget only ever measures real network time.
**Tradeoffs:** The `rate_limits` block replaces the code defaults wholesale with no merge, so omitting a board silently downgrades it to global concurrency with no spacing, and per-tenant `detail_concurrency` / `detail_jitter_s` is configured twice — in `scraper.yaml` and again in `matcher.yaml`'s `funnel.detail_fetch` — for one concern.

## 23. Dashboard as a Read-Only Layer

**Context:** A UI sitting over a live pipeline must never corrupt the datastore or block the single writer, and the shipped database is multi-gigabyte.
**Decision:** `hireshire/webapp/` reaches the database only through `get_readdb()` → `ReadDB`, which opens its own connection with `PRAGMA query_only=ON` instead of reusing `get_db()` (whose first open writes schema), while config edits round-trip through ruamel against the `config_spec.py` whitelist and re-validate via each phase's own pydantic settings model.
**Rationale:** `query_only` makes mutation structurally impossible rather than merely unintended, WAL lets that reader run concurrently with a pipeline write without either side blocking, and re-validating through the phases' own models means the UI can never write a config the pipeline would reject.
**Tradeoffs:** `ReadDB` duplicates a handful of SELECT helpers that already exist on `Database`, and the read-only guarantee holds only as long as no future router imports `get_db()` directly.

## 24. Phase Gates in Config, Not Just CLI Flags

**Context:** `--no-tuner` and `--apply` were CLI-only, which the Phase 5 dashboard cannot use — it starts runs over HTTP and has no way to pass flags to them.
**Decision:** `enable_tuner` (`config/tuner.yaml`) and `enable_applier` (`config/applier.yaml`) hold the defaults, and `orchestrate.py` ORs each with its flag — `skip_tuner = args.no_tuner or not enable_tuner`, `apply = args.apply or enable_applier`.
**Rationale:** Config becomes the single source of truth that the CLI and the dashboard both read, with each flag acting as a one-way override in its own direction.
**Tradeoffs:** The override is asymmetric by construction — `--no-tuner` can only disable and `--apply` can only enable — so there is no CLI way to force the tuner *on* or the applier *off* against config, and inverting either gate means editing the YAML.
