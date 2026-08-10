# /scrape-direct — HireShire Direct Portal Scraper

Scrape job listings from the two career portals that cannot be read over plain HTTP:
**Microsoft** and **Meta**. Everything else runs without a browser.

## Overview

Apple, Google and Intuit used to be handled here. They are now plain-HTTP scrapers that
run inside `python scraper.py` with no Claude involvement at all — see
`hireshire/scrapers/handlers/` and `config/direct_companies.json`. **Do not scrape them
here.** They are present in `config/direct_boards.yaml` only as `enabled: false` fallbacks
in case a portal ever starts blocking httpx.

Only two boards genuinely need a browser:

- **Microsoft** — Eightfold AI; its `/api/apply/v2/jobs` endpoint returns
  `403 {"message": "Not authorized for PCSX"}` without a real browser session.
- **Meta** — 400s on every plain HTTP request, including the bare HTML page.

**Scope: scrape and ingest only.** Do NOT run `matcher.py`, `tuner.py`, or any export.
When the orchestrator triggers this skill it feeds the ingested jobs into the pipeline
itself; running those phases here would double-process the run.

---

## Three rules that keep this cheap

These exist because an earlier version of this skill burned a large amount of context per
run. Follow them exactly.

1. **Never rediscover a selector.** `config/direct_boards.yaml` stores a verified
   `card_selector` / `link_selector` for each board. Use them. Only probe the DOM if an
   extraction returns **0 rows** (Step 4).
2. **Every `browser_evaluate` MUST pass `filename:`.** Write to
   `data/direct/<run_id>/raw/<company>.json`. Job payloads must never be returned into
   the conversation — descriptions alone ran to thousands of tokens per page. You are
   moving data from the browser to disk, not reading it.
3. **Do not paginate Meta.** Its list is virtualised and will not advance; page 1 is all
   you get. This is a documented cap, not a bug to retry.

---

## Step 1 — Load config

Read `config/direct_boards.yaml`. For each board where `enabled` is true, take
`list_url`, `page_size`, `card_selector`, `link_selector`, `date_style`, and
`max_pages` (falling back to `settings.max_pages_per_company`).

Read `config/scraper.yaml` for `settings.max_age_hours` and `settings.location_filter`.

If no board is enabled, say so and stop.

---

## Step 2 — Resolve the run_id

**If the prompt supplied a run_id** (the orchestrator appends a line saying which one to
use): use it exactly, and do NOT pass `--finalise` at ingest — the orchestrator's scraper
finalises that run itself.

**Otherwise** (standalone `/scrape-direct`):

```
Bash: python scripts/direct_cli.py new-run
```

stdout is the run_id. Remember to pass `--finalise` at ingest in this case.

---

## Step 3 — Extract, one board at a time

Substitute `{page}` (1-based) or `{start}` (`page_size * (page - 1)`) into `list_url`, then:

```
mcp__playwright__browser_navigate(url=<list_url for this page>)
```

Then run the board's extractor with `browser_evaluate`, **always with `filename:`**.

### Microsoft

Cards are `div[data-test-id="job-listing"]`, each wrapping `a[href^="/careers/job/<id>"]`.
Card `innerText` splits into `[title, location, "Posted N hours ago"]`.

```js
() => {
  const out = [];
  for (const card of document.querySelectorAll('div[data-test-id="job-listing"]')) {
    const a = card.querySelector('a[href^="/careers/job/"]');
    if (!a) continue;
    const id = (a.getAttribute('href').match(/job\/(\d+)/) || [])[1];
    if (!id) continue;
    const lines = (a.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    const posted = lines.find(l => /^Posted/i.test(l)) || '';
    const rest = lines.filter(l => !/^Posted/i.test(l));
    out.push({
      native_id: id, title: rest[0] || '',
      url: 'https://apply.careers.microsoft.com/careers/job/' + id,
      posted_raw: posted, location: rest.slice(1).join(' | ') || 'N/A'
    });
  }
  return out;
}
```

Eightfold auto-opens the first job as a detail pane; that is expected and harmless — the
card list is still fully present.

### Meta

```js
() => {
  const seen = new Map();
  for (const a of document.querySelectorAll('a[href^="/profile/job_details/"]')) {
    const href = a.getAttribute('href');
    const id = (href.match(/job_details\/(\d+)/) || [])[1];
    if (!id || seen.has(id)) continue;
    const lines = (a.innerText || '').split('\n').map(s => s.trim()).filter(s => s && s !== '⋅');
    seen.set(id, {
      native_id: id, title: lines[0] || '',
      url: 'https://www.metacareers.com' + href,
      posted_raw: '', location: lines[1] || 'N/A', department: lines[2] || null
    });
  }
  return [...seen.values()];
}
```

Meta's results load asynchronously — wait ~3s after navigating before extracting.

### Pagination

Microsoft only, and stop at whichever comes first:
- all jobs on the page are older than `max_age_hours`
- `max_pages` is reached
- a page yields 0 rows

---

## Step 4 — Self-heal (only on 0 rows)

If and only if an extractor returns an empty array:

1. Take **one** `mcp__playwright__browser_snapshot()`.
2. Work out the selector that actually matches the job cards.
3. Retry the extraction once, still writing to `filename:`.
4. **Report the corrected selector in the Step 6 summary** so `card_selector` /
   `link_selector` in `config/direct_boards.yaml` can be updated.

If it still returns 0 rows, record the board as failed and move on. Never abort the run
for one site.

---

## Step 5 — Normalise and ingest

Hand the raw files to the CLI, which applies the age cutoff, the location filter (with
country inference — these portals print "Redmond, WA" with no country, so a naive
substring match against "united states" would drop every US job), relative-date parsing,
dedupe, and the `direct:<company>:<native_id>` id prefix:

```
Bash: python scripts/direct_cli.py normalize --run-id "<run_id>"
Bash: python scripts/direct_cli.py ingest --run-id "<run_id>"
```

Add `--finalise` to the ingest call **only** in the standalone case from Step 2.

`normalize` reads `data/direct/<run_id>/raw/*.json` and writes the staged
`data/direct/<run_id>/<company>.json` files. It prints per-company counts; you do not need
to read the job data yourself.

Descriptions are deliberately **not** fetched here. Microsoft and Meta jobs are ingested
list-only and the matcher funnel hydrates the ones that survive its relevance gate.

---

## Step 6 — Summary table

| Company | Pages | Found | Ingested | Dropped (age/location) | Selector healed |
|---------|-------|-------|----------|------------------------|-----------------|

Then the total ingested. If any selector needed healing, print it verbatim.

**Stop here.** Do not run the matcher, the tuner, or any export.

---

## Error handling

If a board throws or the browser returns an unexpected state:
- record it as failed with the error message
- still run `normalize` + `ingest` for whatever other boards succeeded
- continue to the next board — never abort the whole run

If a site is unreachable (hard bot-block, captcha, login wall), note it in the summary.

---

## Notes

- `native_id` must be the portal's raw id. `scripts/direct_cli.py` rewrites it to
  `direct:<company>:<native_id>`, because `seen_jobs` is keyed on `job_id` alone across
  every platform — an unprefixed id would collide with a BambooHR job of the same number
  and be silently dropped as already-seen.
- Do not open the SQLite database directly. `scripts/direct_cli.py` is the only writer,
  the same way `/apply` goes through `scripts/applied_cli.py`.
- Re-running ingest for the same run_id is safe: `jobs` is keyed on `(run_id, job_id)` and
  upserts.
- These companies are in `exclude_companies` in `config/applier.yaml` — their forms sit
  behind account logins, so `/apply` skips them. Tuned resumes are still produced.
