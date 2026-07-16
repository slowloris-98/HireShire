# HireShire — System Architecture (AI Engineering View)

Companion to `sys_arch.excalidraw`. These diagrams focus on the AI-engineering
surface: where tokens get spent, where structured output is enforced, where the
LLM decides vs. where code executes, and where a human stays in the loop.

---

## 1. The cost cascade — cheapest filter first, tokens last

The central design idea: a scrape yields thousands of jobs, but only tens are
worth an LLM call. Every stage below is ordered by cost per job, and each one
only sees what survived the stage above it.

```mermaid
flowchart TB
    subgraph EXT["External job boards"]
        direction LR
        GH["Greenhouse API"]
        ASH["Ashby API"]
        LEV["Lever API"]
        WD["Workday / BambooHR<br/>list-only, no description"]
    end

    GH --> SCR
    ASH --> SCR
    LEV --> SCR
    WD --> SCR

    SCR["Phase 1 · Scraper<br/>no AI — pure fetch<br/>bad_slugs.json self-heals"]

    SCR -->|"Job[] — thousands"| SEEN

    SEEN{"seen_jobs table<br/>scored in a prior run?"}
    SEEN -->|yes| D1["dropped — never re-scored"]
    SEEN -->|no| F1

    subgraph FUNNEL["Phase 2a · Funnel — relevance gate (cost-ordered)"]
        direction TB
        F1{"1 · code exclude filter<br/>keyword blocklist<br/>cost: ~0"}
        F1 -->|match| D2["title_excluded"]
        F1 -->|no match| F2

        F2{"2 · code include fast-pass<br/>keyword allowlist<br/>cost: ~0 — and immune<br/>to encoder mistuning"}
        F2 -->|match| HYD
        F2 -->|no match| F3

        F3{"3 · encoder relevance<br/>MiniLM-L6-v2, local CPU<br/>cosine sim vs target anchors<br/>cost: compute, no tokens"}
        F3 -->|"max cos-sim &lt; threshold"| D3["title_low_relevance"]
        F3 -->|"max cos-sim &gt;= threshold"| HYD

        HYD["4 · detail hydration<br/>fetch content_text for<br/>surviving list-only rows<br/>cost: 1 HTTP call each"]
    end

    HYD -->|"survivors — tens"| SCORE

    subgraph MATCH["Phase 2b · Matcher — the first paid tokens"]
        SCORE["ResumeScorer<br/>rubric system prompt<br/>resume + projects + JD"]
        SCORE --> SCHEMA["structured output<br/>ScoringSchema (pydantic)"]
    end

    SCHEMA --> GATE{"is_shortlisted<br/>score &gt;= threshold<br/>null score also passes"}
    GATE -->|no| D4["rejected"]
    GATE -->|yes| TUNE["Phase 3 · Tuner<br/>see diagram 2"]
    TUNE --> APPLY["Phase 4 · Applier<br/>see diagram 4"]

    SKIP["skip_llm / --no-llm"] -.->|"bypasses scoring —<br/>score null, all survivors<br/>shortlisted"| GATE
```

**Why it's built this way:** stages 1 and 2 are string comparisons, stage 3 is a
local embedding model with no API cost, and stage 4 spends one HTTP call. Only
then does a job cost tokens. Retargeting the whole hunt to a different role is a
config edit — the `encoder.targets` anchor list — not a code change.

---

## 2. Tuner — the LLM/code boundary

Two LLM passes, then the LLM stops. Everything downstream of the selector is
deterministic code, so a hallucinated bullet can't reach the PDF: the model picks
from a pre-authored corpus, it doesn't write prose.

```mermaid
flowchart TB
    IN["shortlisted job<br/>JD + full resume LaTeX"] --> EVAL

    subgraph LLM_ZONE["LLM decides"]
        EVAL["Pass 1 · ResumeEvaluator<br/>recruiter-perspective critique<br/>→ EvaluatorResult (pydantic)"]
        EVAL --> OPT["Pass 2 · ResumeOptimizer<br/>compact JSON selector<br/>input: critique + project roster<br/>(titles/descriptions only —<br/>never the full LaTeX)<br/>→ SelectionResult (pydantic)"]
    end

    ROSTER[("projects_bullets.yaml<br/>pre-authored bullet corpus<br/>linted + unit-tested")] --> OPT

    OPT -->|"which projects,<br/>which bullets,<br/>keyword adjustments"| ASM

    subgraph CODE_ZONE["Code executes — no LLM past this line"]
        ASM["Assembler<br/>substitute selected entries<br/>into LaTeX template"]
        ASM --> PDF["pdflatex compile"]
        PDF --> FIT{"two-directional fit loop<br/>read page count + bottom margin"}
        FIT -->|"overflow"| TRIM["drop summary →<br/>drop least-relevant project →<br/>drop bottom bullets one at a time"]
        TRIM --> ASM
        FIT -->|"sparse single page<br/>bottom margin &gt; ~45pt"| FILL["re-enable summary<br/>revert if it overflows"]
        FILL --> ASM
        FIT -->|"fits"| OUT
    end

    OUT["data/tuned/run_id/job_id/<br/>Name_Resume.pdf + .tex<br/>+ critique.json"]
```

**Why it's built this way:** the expensive, unreliable step (judgement) is given
the smallest possible input and asked for the smallest possible output — a JSON
selection, not a document. Layout is a solved problem in code, so the fit loop
never burns a token.

---

## 3. Provider abstraction

Every LLM call site sits behind a Protocol, so a provider is a config key, not a
refactor. Structured output is enforced natively per provider, then re-validated
against the same pydantic model regardless of which path it came through.

```mermaid
flowchart LR
    subgraph CALLERS["Call sites"]
        M["Matcher scorer<br/>config: provider"]
        E["Tuner evaluator<br/>config: evaluator_provider"]
        O["Tuner optimizer<br/>config: optimizer_provider"]
        A["Webapp chat agent<br/>config/frontend.yaml"]
    end

    M --> P
    E --> P
    O --> P
    A --> P

    P{{"LLMBackend / EvaluatorBackend /<br/>OptimizerBackend Protocol<br/>fallback: LLM_PROVIDER env var"}}

    P --> G["Gemini<br/>response_schema"]
    P --> OA["OpenAI<br/>response_format"]
    P --> AN["Anthropic<br/>tool input_schema"]
    P --> CC["claude_code<br/>local CLI subprocess<br/>optimizer only"]

    G --> V["pydantic validate<br/>same model, every path"]
    OA --> V
    AN --> V
    CC --> V

    R["per-provider resilience<br/>tenacity retry + backoff<br/>retryable-error predicates<br/>semaphore + request_interval_s"] -.-> G
    R -.-> OA
    R -.-> AN
```

---

## 4. Human in the loop — chat agent and applier

The two places the system can act on the world are both gated: the agent's run
tools return a *proposal*, and the applier honours a `dry_run` flag.

```mermaid
flowchart TB
    U(["User"]) --> UI["React SPA"]
    UI -->|"POST /api/chat"| AG

    subgraph AGENT["LangGraph ReAct agent"]
        AG["agent loop"]
        AG --> RT["read tools — execute directly<br/>search_jobs · get_top_matches<br/>run_stats · list_runs · explain_config"]
        AG --> WT["run tools — confirmation-gated<br/>run_phase · stop_phase<br/>return a proposal, never a subprocess"]
    end

    RT --> RDB[("ReadDB<br/>PRAGMA query_only=ON<br/>separate connection — never<br/>contends with the pipeline writer")]
    RT -->|"SSE: job_results"| UI
    WT -->|"SSE: run_proposal"| CARD["Confirm card"]
    CARD -->|"user clicks confirm"| RUNNER["runner.py<br/>subprocess.Popen"]
    RUNNER --> PHASES["scraper / matcher / tuner<br/>applier / orchestrate"]

    PHASES --> APP

    subgraph APP["Phase 4 · Applier"]
        SK["/apply Claude Code skill<br/>Playwright MCP<br/>reads pipeline_results.json"]
        BU["applier.py<br/>browser-use agent<br/>reads matches directly"]
    end

    SK --> DRY{"dry_run?"}
    BU --> DRY
    DRY -->|true| LOG["record only"]
    DRY -->|false| SUBMIT["submit form to<br/>real job site"]
```

---

## Storage note

`data/hireshire.db` (SQLite, WAL) holds every tabular row, keyed by a shared
`run_id` — each phase writes under it, the next reads via `db.latest_run(phase)`.
Genuine artifacts (tuned PDFs/tex, applier screenshots) live on disk and are
referenced by path from the DB. Results are committed per row, so a mid-run crash
resumes without re-spending tokens on already-scored jobs.
