# /apply — HireShire Job Applier

Apply to shortlisted jobs using pipeline results and Playwright browser automation.

## Overview

This skill replaces `applier.py`. It reads the latest `data/pipeline/*/pipeline_results.json`,
opens each job's application URL in a real browser via Playwright MCP, fills the form using
reasoning over the job-specific tuned resume, and records the result in the shared SQLite
datastore (`data/hireshire.db`, the `applied` table) via `scripts/applied_cli.py` — the same
table `python applier.py` uses, so application state is never split across tools.

---

## Step 1 — Load config and job queue

Read `config/applier.yaml`. Extract:
- `settings.dry_run` (bool)
- `settings.first_name`, `settings.last_name`, `settings.email`, `settings.phone`
- `settings.inter_job_delay_s` (default 10)
- `settings.applied_dir` (default `data/applied`) — used only for the `screenshots/` subdirectory

Find the latest pipeline run: list all directories under `data/pipeline/`, sort by name
(they are ISO timestamps like `2026-06-09T12-00-00Z`), take the last one.
Read `data/pipeline/<latest>/pipeline_results.json`.

Get the set of already-applied `job_id` values from the `applied` table:

```
Bash: python scripts/applied_cli.py list
```

Each line of stdout is one applied `job_id` (empty output means nothing has been applied yet).

Filter the pipeline results to jobs that are **not yet applied** AND:
- `tuner_status == "tuned"`
- `resume_pdf` is not null and the path exists on disk

Print a summary of the queue (title, company, URL) before starting.
If the queue is empty, say so and stop.

---

## Step 2 — Read the resume

For the first job's `resume_pdf`, use the **Read tool** to load the PDF content.
You will use this text throughout as the source of truth for answering form questions.
(All jobs in the queue share the same candidate — re-reading per job is unnecessary unless
the resume paths differ across jobs.)

---

## Step 3 — For each job in the queue

### 3a. Navigate and snapshot the form

```
playwright_navigate(url=job_url)
playwright_snapshot()
```

Inspect the DOM snapshot. Identify all visible form fields: text inputs, dropdowns,
radio buttons, checkboxes, file inputs, and textareas. Note their labels and selectors.

If the page redirects or shows a "Sign in to apply" gate rather than a direct form,
note this as an error and skip to step 3f.

**Location check:** Look for a location field, label, or visible text on the page that
indicates the job's location. If a location is found and it does NOT (case-insensitively)
contain any of `"united states"`, `"us"`, `"remote"`, `"india"`, `"worldwide"`, or
`"anywhere"`, skip this job: print a message like
`"Skipping <title> at <company> — location '<found_location>' is outside target regions"`
and continue to the next job without recording it.
If no location text is found on the page, continue (treat as unknown/remote).

### 3b. Fill standard identity fields

Use `playwright_fill` for these — no reasoning needed:
- First name → `settings.first_name`
- Last name → `settings.last_name`
- Email → `settings.email`
- Phone → `settings.phone`

### 3c. Upload the resume

Find the resume/CV file input. Upload the job-specific tuned resume:

```
playwright_upload_file(selector=<file_input_selector>, paths=[resume_pdf])
```

### 3d. Generate cover letter if requested

If `settings.generate_cover_letter` is true (or not present in config) and the form has a
cover letter field, write a 3-paragraph professional cover letter:
- Paragraph 1: enthusiasm for the specific role and company
- Paragraph 2: 2–3 relevant experiences from the resume that match the job
- Paragraph 3: forward-looking close

### 3e. Fill remaining custom fields

For any other application questions (dropdowns, free-text, yes/no, numeric), reason from:
1. The resume PDF text loaded in Step 2
2. The job title and company (`record["title"]`, `record["company"]`)
3. The job URL (infer board type and likely question intent)

Key rules:
- Do **not** fabricate experience or qualifications not in the resume
- For "years of experience" questions, estimate conservatively from resume dates
- For demographic/EEO questions, select "Prefer not to answer" / "Decline to self-identify"
- For "how did you hear about us" → "Job board"
- For sponsorship/authorization → answer based on what you know about the candidate
  (default: "Yes" for work authorization, "No" for sponsorship requirement)

For multi-page forms: after completing visible fields, look for a "Next" or "Continue"
button, click it, snapshot the new page, and continue filling.

### 3f. Screenshot and submit decision

```
playwright_screenshot()
```

Save the screenshot path for the record.

**If `dry_run=true`**: Do NOT click any submit, apply, or send button. Status = `"dry_run"`.

**If `dry_run=false`**: Click the Submit / Apply / Send Application button.
Confirm the submission succeeded (look for a confirmation message or page change).
Status = `"submitted"` on success, `"error"` on failure.

### 3g. Write the apply record

Record the result in the `applied` table via the helper CLI (it upserts by `job_id`
and stamps `applied_at` for you):

```
Bash: python scripts/applied_cli.py record \
  --job-id "<record.job_id>" \
  --board-token "<record.company>" \
  --title "<record.title>" \
  --url "<record.job_url>" \
  --status "dry_run" | "submitted" | "error" \
  --dry-run "true" | "false" \
  --screenshot "<absolute path to screenshot>" \   # omit if none
  --error "<error message>"                          # omit if none
```

Omit `--screenshot` / `--error` when there is no value.

### 3h. Inter-job delay

If there are more jobs remaining, wait `settings.inter_job_delay_s` seconds before
proceeding to the next job. Use `Bash(sleep <n>)` for this.

---

## Step 4 — Summary table

After all jobs are processed, print a Markdown table:

| Company | Title | Status | Screenshot |
|---------|-------|--------|------------|
| ...     | ...   | ✓ dry_run / ✓ submitted / ✗ error | path |

Print total counts: submitted, dry_run, error.

---

## Error handling

If any per-job step throws or the browser returns an unexpected state:
- Set `status = "error"`, `error = <exception message>`
- Take a screenshot if possible for debugging
- Record the error via `python scripts/applied_cli.py record ... --status error --error "<message>"`
- Continue to the next job (do not abort the entire run)

---

## Notes

- The `resume_pdf` in each pipeline record is the **job-specific tuned resume** — always
  prefer it over any base resume path from config.
- Greenhouse, Ashby, and Lever forms all differ in structure. Always snapshot before filling
  and use label text to identify fields rather than hardcoded selectors.
- After uploading a resume on Lever/Ashby, the form may auto-populate name/email fields —
  verify they are correct before moving on.
