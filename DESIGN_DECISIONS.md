# HireShire — Design Decisions

This document records the significant architectural and implementation choices made while building HireShire, along with the reasoning and tradeoffs behind each one. It is written for contributors who want to understand *why* the code is structured the way it is — not just *what* it does.

Each entry follows a lightweight ADR structure: **Context → Decision → Rationale → Tradeoffs**.

---

## Quick Reference

| # | Decision | Primary Files |
|---|----------|--------------|
| 1 | Four-phase independent pipeline | `scraper.py`, `matcher.py`, `tuner.py`, `.claude/skills/apply.md` |
| 2 | Asyncio + sentinel queue streaming | `orchestrate.py` |
| 3 | Protocol-based LLM backends | `hireshire/matcher/scorer.py`, `hireshire/tuner/optimizer.py` |
| 4 | Provider-specific retry with dynamic delay | `hireshire/matcher/scorer.py`, `hireshire/tuner/optimizer.py` |
| 5 | Title filter before LLM scoring | `hireshire/matcher/title_filter.py`, `config/matcher.yaml` |
| 6 | Streaming storage via `progress.jsonl` | `hireshire/matcher/` |
| 7 | Seen jobs deduplication across runs | `hireshire/matcher/seen.py` |
| 8 | Two-pass tuner (Evaluator → Optimizer) | `hireshire/tuner/evaluator.py`, `hireshire/tuner/optimizer.py` |
| 9 | LaTeX resume with template substitution | `hireshire/tuner/assembler.py`, `data/resume_projects/` |
| 10 | Two-phase code-only PDF trimming | `tuner.py` |
| 11 | Keyword adjustment by bullet index | `hireshire/tuner/assembler.py` |
| 12 | `ClaudeCodeOptimizerBackend` via CLI subprocess | `hireshire/tuner/optimizer.py` |
| 13 | Applier as a Claude Code skill | `.claude/skills/apply.md` |
| 14 | Orchestrator scheduling via `asyncio.sleep` | `orchestrate.py` |
| 15 | Shared HTTP client with exponential backoff | `hireshire/http_client.py` |

---

## 1. Four-Phase Independent Pipeline

**Context:** Job automation spans four genuinely distinct concerns — discovering listings, scoring them against a resume, tailoring the resume per job, and filling out the application form. An early prototype wired these together inside a single script.

**Decision:** Split into four fully independent phases (Scraper, Matcher, Tuner, Applier), each with its own entrypoint script, `config/<phase>.yaml`, `hireshire/<phase>/` subpackage, and `data/<phase-output>/` directory. Each phase can be run in isolation.

**Rationale:** Independence means a bad LLM scoring run doesn't force a re-scrape. A scraper update doesn't break the matcher interface. Each phase can be iterated on, tested, or replaced without touching the others. Data directories act as version-controlled checkpoints — you can re-run the tuner against a previous matcher run with `--run-id`.

**Tradeoffs:** Four entrypoints and four config files to keep in sync. The data-directory handoff convention (`always pick the latest timestamped run`) is implicit — a future schema change in one phase could silently break downstream phases that don't validate input.

---

## 2. Asyncio + Sentinel Queue Streaming

**Context:** Running phases sequentially (scrape everything, then score everything, then tune everything) means the tuner sits idle for the ~30 minutes the scraper and matcher take. Jobs scraped early in the run don't start tuning until all scoring is done.

**Decision:** `orchestrate.py` wires phases via `asyncio.Queue`. The scraper puts `(board_token, list[Job])` batches into `q1` as it finishes each company; the matcher consumes from `q1` and puts scored results into `q2`; the tuner consumes from `q2`. Each phase signals completion by putting a `None` sentinel into its output queue. All phases run concurrently under `asyncio.gather()`.

**Rationale:** The pipeline is almost entirely I/O-bound (HTTP scraping, LLM API calls, `pdflatex` compilation). Queues allow downstream phases to start processing the moment the first batch arrives, cutting total wall-clock time significantly. Backpressure is implicit — if the tuner falls behind, the matcher blocks on `q2.put()`.

**Tradeoffs:** Every consumer must handle the `None` sentinel and propagate it downstream. The current implementation uses a single `None` per queue, which works correctly only if each phase has exactly one producer. Adding parallelism within a phase would require a counted sentinel or an explicit close signal. Introduced in commit `74869c3`.

---

## 3. Protocol-Based LLM Backends

**Context:** The project targets Gemini, OpenAI, Anthropic, and a local Claude CLI subprocess as LLM providers. Early versions hardcoded Gemini calls directly in the scorer.

**Decision:** `LLMBackend` is defined as a `@runtime_checkable` Protocol with a single async `call(prompt, system_prompt) → ScoringSchema` method. A `make_backend()` factory reads the `LLM_PROVIDER` env var and returns the appropriate implementation. The same pattern is replicated in the tuner's optimizer (`optimizer.py`).

**Rationale:** Protocol-based duck typing means no inheritance hierarchy. Adding a new provider is purely additive — implement `call()`, register in `make_backend()`, done. Core scorer logic never changes. Providers can be swapped per-phase via environment variables, so the evaluator can use a cheap model while the optimizer uses a better one.

**Tradeoffs:** Retry and throttling logic is duplicated across backends in `scorer.py` and `optimizer.py` — the `tenacity` decorator patterns are nearly identical. A shared retry mixin or decorator factory would eliminate this duplication.

---

## 4. Provider-Specific Retry with Dynamic Delay

**Context:** LLM APIs rate-limit requests, but each provider exposes its backoff guidance differently. Gemini returns a `retryDelay` field inside the JSON error body; OpenAI and Anthropic set a `Retry-After` HTTP header.

**Decision:** Each backend has its own `tenacity` retry decorator with a custom `wait` function that reads the provider's native delay field. All backends use `stop=never` — infinite retries.

**Rationale:** A fixed backoff would either overshoot (wasting minutes when the API only needs seconds) or undershoot (hammering a still-rate-limited endpoint). Reading the API's own recommendation is both more efficient and more respectful. Infinite retries reflect the reality that a single scoring run can take 45+ minutes on free-tier limits — worth waiting out rather than losing.

**Tradeoffs:** The Gemini wait function uses a regex to extract `retryDelay` from the stringified error — fragile if the error message format ever changes. The Anthropic and OpenAI implementations parse the `Retry-After` header from exception metadata, which is marginally more stable.

---

## 5. Title Filter Before LLM Scoring

**Context:** A single scraper run across 60+ companies can surface 300–500 job listings. LLM scoring costs API quota and time. Many of those jobs are obviously out-of-scope (iOS Engineer, VP of Sales, Principal Architect).

**Decision:** `config/matcher.yaml` exposes `title_filter.include_keywords` and `exclude_keywords`. The matcher applies a fast, case-insensitive substring match against job titles *before* any LLM call. Jobs that fail the title filter are recorded as `skipped` with `skip_reason: "title_excluded"`.

**Rationale:** Cheap, deterministic, zero-API-cost pre-filter. In practice it eliminates more than half the candidate set before any scoring occurs, keeping free-tier quota focused on genuinely relevant jobs.

**Tradeoffs:** Keyword matching has false negatives — "Founding Engineer" won't match `include_keywords: ["engineer"]` unless configured correctly. The filter is entirely user-controlled in YAML, so misconfigurations are easy to make and inspect. A title classifier LLM call would be more accurate but defeats the purpose of the pre-filter.

---

## 6. Streaming Storage via `progress.jsonl`

**Context:** On free-tier Gemini with a 13-second inter-request interval, scoring 200 jobs takes roughly 45 minutes. A crash, keyboard interrupt, or API outage mid-run discards all work completed so far.

**Decision:** The matcher appends each `MatchResult` as a single JSON line to `data/matches/<run_id>/progress.jsonl` immediately after scoring. On startup for the same `run_id`, it loads the progress file and skips any job IDs already present.

**Rationale:** Crash-safe incremental writes. A resume is trivial — re-run the matcher with the same `run_id` and it picks up where it left off. No re-scoring already-processed jobs.

**Tradeoffs:** `progress.jsonl` must be cleaned up at the end of a successful run (it is — replaced by `shortlisted.json` + `rejected.json`). If the cleanup step is interrupted, a stale progress file could interfere with a fresh run using a recycled `run_id`. The current implementation generates unique timestamped run IDs so this is unlikely in practice.

---

## 7. Seen Jobs Deduplication Across Runs

**Context:** The orchestrator runs on a recurring schedule (default: every 4 hours). Job boards keep listings live for weeks. Without deduplication, every pipeline run would re-score and potentially re-apply to the same jobs.

**Decision:** `hireshire/matcher/seen.py` maintains a persistent JSON set at `data/matches/seen_jobs.json`. The matcher adds each scored job's ID to this set. On subsequent runs, any job ID already in the set is skipped before reaching the title filter or LLM.

**Rationale:** Once a job has been scored and acted on (tuned + applied), there is no value in re-processing it. The seen set makes the matcher idempotent across runs.

**Tradeoffs:** The seen set grows unboundedly — there is no TTL or expiry. A job that is taken down and reposted with a new ID will be re-processed (correct behavior), but a job reposted with the same ID will be silently skipped. There is also no mechanism to force re-evaluation of a specific job without manually editing `seen_jobs.json`.

---

## 8. Two-Pass Tuner (Evaluator → Optimizer)

**Context:** Optimizing a resume for a specific job in a single LLM pass requires one enormous prompt: full LaTeX source (~200 lines), the full job description, and a roster of all candidate projects. This tends to hallucinate, exceed context limits, and produce generic results.

**Decision:** The tuner runs two sequential, narrowly-scoped LLM passes per job:
- **Pass 1 — `ResumeEvaluator`:** Given the job description and full resume LaTeX, produce a structured `EvaluatorResult` (shortcomings, missing keywords, experience gaps, weak sections). Acts as a recruiter critique.
- **Pass 2 — `ResumeOptimizer`:** Given the JD, the critique, and a compact project roster (titles + descriptions only, no bullets), return a `SelectionResult` (which projects to include, which work entry to feature, per-bullet keyword overrides, section order).

**Rationale:** Each pass has a tighter, cheaper prompt with a well-defined output schema. The evaluator focuses on diagnosis; the optimizer focuses on selection. The two passes can use different (cheaper) models independently via `evaluator_provider` / `optimizer_provider` in `config/tuner.yaml`.

**Tradeoffs:** Two LLM round-trips per job roughly doubles tuner latency. Pass 2 quality is gated on Pass 1 quality — a weak critique leads to poor project selection. The critique step also passes the full LaTeX source, which is verbose; a structured resume representation might be cheaper.

---

## 9. LaTeX Resume with Template Substitution

**Context:** Resume output must fit on exactly one page — a hard requirement for most ATS systems and recruiters. Keyword injection must be precise (at the bullet level, not the section level). Early versions generated plain text or Markdown, which gave no layout control.

**Decision:** The canonical resume source is a `.tex` file. A fixed template (`data/resume_projects/resume_template.tex`) contains a single substitution marker `%{{EXPERIENCE_SECTIONS}}`. The assembler (`hireshire/tuner/assembler.py`) builds the LaTeX for the selected projects and work entries from pre-authored bullet lists in `data/resume_projects/projects_bullets.yaml`, then replaces the marker. Compilation uses `pdflatex`.

**Rationale:** LaTeX gives deterministic, pixel-perfect page layout. Pre-authored bullets in `projects_bullets.yaml` preserve prose quality — the LLM selects and adjusts bullets rather than writing them from scratch, reducing hallucination. The `pdflatex` page count is queryable after each compile, enabling the trimming loop (decision #10).

**Tradeoffs:** Requires a working `pdflatex` installation. Any LaTeX syntax error in an LLM-generated keyword override will break the compile. Markdown or HTML would be easier to generate and inspect, but they offer no reliable single-page guarantee. The `%{{...}}` marker syntax was chosen specifically to avoid conflicts with LaTeX's own `%` comment character.

---

## 10. Two-Phase Code-Only PDF Trimming

**Context:** After assembling the LaTeX, `pdflatex` may produce a PDF longer than one page depending on which projects were selected and how many bullets each has.

**Decision:** Trimming is purely code-based with no additional LLM call. Two phases run in sequence:
1. **Project removal:** Pop the least-relevant project (last in the optimizer's ranked list), reassemble, recompile. Repeat until one page or only two projects remain.
2. **Bullet removal (fallback):** If still over one page, decrement bullet counts one at a time across projects (least relevant first) and recompile after each change.

**Rationale:** An LLM trimming pass would be slow, non-deterministic, and hard to debug. The code loop is fast (each `pdflatex` compile takes ~1 second), fully deterministic, and produces the same output for the same input. The optimizer's project ranking already encodes relevance order, so dropping from the tail is semantically sound.

**Tradeoffs:** The trimming is greedy — it drops entire projects before removing individual bullets. A more sophisticated approach might interleave the two strategies. In practice, Phase 1 (project removal) handles most over-page cases, and Phase 2 is rarely needed.

---

## 11. Keyword Adjustment by Bullet Index

**Context:** The optimizer may want to substitute specific bullets in selected projects with keyword-enriched variants tailored to a particular job. Replacing the entire project section would risk losing quality pre-authored language.

**Decision:** `SelectionResult.keyword_adjustments` is a `dict[project_id, list[str | None]]`. Each list position corresponds to a bullet index in that project. A `str` value overrides that bullet; `None` preserves the pre-authored original. The assembler (`hireshire/tuner/assembler.py`) applies overrides index-by-index.

**Rationale:** Surgical, index-based overrides let the LLM inject keywords at exactly the right bullet without touching the rest of the section. The optimizer's prompt only needs to provide non-None entries, keeping the response compact.

**Tradeoffs:** Index-based addressing is fragile — if `projects_bullets.yaml` is reordered or a bullet is inserted, every existing override index becomes incorrect. A named-bullet scheme (override by a stable bullet ID rather than position) would be more robust but adds complexity to both the YAML schema and the prompt.

---

## 12. `ClaudeCodeOptimizerBackend` via CLI Subprocess

**Context:** For the tuner's optimizer pass, there is value in routing the call through a local Claude Code CLI process rather than the API — for example, to leverage tool use, extended thinking, or skills not yet exposed via the REST API.

**Decision:** `ClaudeCodeOptimizerBackend` in `hireshire/tuner/optimizer.py` spawns a `claude -p --permission-mode auto` subprocess, passes the optimizer prompt via stdin, and captures stdout as the LaTeX or JSON response. The output is parsed with the same `_strip_fences()` and `_validate_latex()` helpers used by other backends.

**Rationale:** Enables capabilities that only exist in Claude Code (MCP tools, skill invocation) to be used from a pure Python pipeline context. Treated as an experimental backend activated via `optimizer_provider: claude_code` in `config/tuner.yaml`.

**Tradeoffs:** Each call incurs a cold-start subprocess overhead (~2–5 seconds). There is no structured output guarantee — the subprocess returns plain text that must be parsed heuristically. Requires Claude Code to be installed, authenticated, and on `PATH`. Not suitable as a default backend.

---

## 13. Applier as a Claude Code Skill

**Context:** Filling out job application forms requires browser automation. Application forms on Greenhouse, Lever, and Ashby all have different field layouts, custom questions, and multi-step flows. A purely programmatic approach would need per-platform selectors, custom question logic, and cover letter generation — all of which are fragile and labor-intensive.

**Decision:** The applier is implemented as `.claude/skills/apply.md` — a markdown skill file that instructs a Claude agent to use Playwright MCP tools to navigate, fill, and submit forms. It reads `config/applier.yaml` for identity fields, loads the job-specific `resume_pdf` from the pipeline results, and reasons over open-ended form fields using the resume and job description as context.

**Rationale:** Playwright MCP tools are available inside Claude Code but not as a standalone Python library dependency. Delegating form reasoning to Claude handles label-based field detection, generates cover letters, answers open-ended questions conservatively ("Prefer not to answer" for demographics, no fabrication), and adapts to form variants without hardcoded selectors.

**Tradeoffs:** Non-deterministic behavior — different runs may navigate the same form slightly differently. Harder to unit-test than a Python Playwright script. Requires running inside Claude Code (cannot be invoked from a plain Python subprocess without wrapping it in a `claude -p` call, which is what the orchestrator's `--apply` flag does). Introduced in commit `b4afe87`.

---

## 14. Orchestrator Scheduling via `asyncio.sleep`

**Context:** The pipeline needs to run on a recurring schedule (default: every 4 hours) to catch new job listings as they appear. Options include OS cron, a Python scheduler library (APScheduler, `rocketry`), or a self-managed loop.

**Decision:** `orchestrate.py` implements scheduling as a `while True` loop with `asyncio.sleep(interval_seconds)` between runs. No external scheduler or OS dependency. The `--now` flag triggers an immediate run before the first sleep; `--once` exits after a single run.

**Rationale:** Self-contained — no cron entry, no systemd unit, no external library. Starting and stopping the schedule is as simple as starting and stopping the process. Interval reconfiguration is a `--interval` flag, not a crontab edit.

**Tradeoffs:** The schedule lives and dies with the process. If `orchestrate.py` crashes, the schedule stops. There is no persistent retry on startup, no missed-run recovery, and no distributed locking if multiple instances are accidentally run. For a personal job search tool, this is acceptable. For a production service, a proper scheduler would be warranted.

---

## 15. Shared HTTP Client with Exponential Backoff

**Context:** All three scrapers (Greenhouse, Lever, Ashby) make HTTP calls to job board APIs. Each faced the same categories of transient failure: 429 rate limits, 5xx server errors, connection timeouts.

**Decision:** `hireshire/http_client.py` exposes a `make_client()` factory that returns a pre-configured `httpx.AsyncClient`. It wraps requests in a `tenacity` retry loop: retryable on 429, 500, 502, 503, 504, and connection/timeout errors; exponential backoff starting at 1s, doubling, capped at 60s. The client sets `User-Agent: HireShire/0.1 (job scraper)` globally.

**Rationale:** DRY — no retry boilerplate per scraper. The `User-Agent` header transparently identifies the tool to job board operators. `follow_redirects=True` handles board redirects without per-scraper handling.

**Tradeoffs:** A shared client means all scrapers get identical retry behavior, which may not be optimal — some boards tolerate higher request rates, others need longer backoffs. Per-scraper configuration would be more precise but adds complexity to the factory interface.
