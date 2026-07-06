"""
End-to-end pipeline probe: run the real orchestrator wiring (scraper → matcher →
tuner → tracker) on a SMALL set of companies from every job board, and log every
item as it enters each phase's input queue.

It mirrors orchestrate.run_pipeline exactly — same phase entrypoints, same queue
contract — but swaps each asyncio.Queue for a LoggingQueue that prints/records a
summary of each item on `put()`. That shows, in order:

    q1  scraper → matcher   (board_token, list[Job])
    q2  matcher → tuner      (MatchResult, Job)
    q3  tuner   → tracker     result dict

Side effects are isolated to a temp dir (bad_slugs + seen-store) so re-runs are
repeatable and your real data/ is untouched. cwd stays at the project root so the
real config/matcher.yaml and config/tuner.yaml load normally.

Usage:
    python scripts/probe_pipeline.py                     # scraper+matcher+bypass (no LLM, no pdflatex)
    python scripts/probe_pipeline.py --llm               # real matcher LLM scoring
    python scripts/probe_pipeline.py --tuner             # real tuner phase (needs API key + pdflatex)
    python scripts/probe_pipeline.py --cap-per-company 1 # trim each company to N jobs (default 3)
    python scripts/probe_pipeline.py --config-dir path/  # use your own {board}_companies.json set
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matcher
import scraper
import tuner
from hireshire.matcher.seen import SeenStore as _RealSeenStore
from orchestrate import _bypass_tuner, _track_results

# A small, verified-live company from each board. Override with --config-dir.
_DEFAULT_SLUGS = {
    "greenhouse": ["stripe"],
    "ashby": ["ramp"],
    "lever": ["leverdemo"],
    "bamboohr": ["10web"],
    "workday": ["7eleven|wd3|7eleven"],
}


# --------------------------------------------------------------------------- #
# Per-queue item summarizers (what to log for each phase's input)
# --------------------------------------------------------------------------- #
def _summ_q1(item) -> dict:
    token, jobs = item
    return {
        "queue": "q1 scraper→matcher",
        "board_token": token,
        "job_count": len(jobs),
        "sample_titles": [j.title[:50] for j in jobs[:3]],
    }


def _summ_q2(item) -> dict:
    mr, job = item
    return {
        "queue": "q2 matcher→tuner",
        "job_id": job.job_id,
        "source": job.source,
        "board_token": job.board_token,
        "title": job.title[:60],
        "relevance_score": getattr(mr, "relevance_score", None),
    }


def _summ_q3(item: dict) -> dict:
    return {
        "queue": "q3 tuner→tracker",
        "company": item.get("company"),
        "title": (item.get("title") or "")[:60],
        "relevance_score": item.get("relevance_score"),
        "tuner_status": item.get("tuner_status"),
        "resume_pdf": item.get("resume_pdf"),
    }


class LoggingQueue(asyncio.Queue):
    """asyncio.Queue that logs a summary of every item as it is enqueued."""

    def __init__(self, label: str, summarize, log_fh, transform=None):
        super().__init__()
        self._label = label
        self._summarize = summarize
        self._log_fh = log_fh
        self._transform = transform
        self.count = 0

    async def put(self, item) -> None:
        if item is None:
            self._emit({"queue": self._label, "sentinel": True})
        else:
            if self._transform is not None:
                item = self._transform(item)
            self.count += 1
            self._emit(self._summarize(item))
        await super().put(item)

    def _emit(self, record: dict) -> None:
        print(f"  {record.get('queue', self._label):<20} " + json.dumps({k: v for k, v in record.items() if k != "queue"}))
        self._log_fh.write(json.dumps(record) + "\n")
        self._log_fh.flush()


def _write_temp_config(tmp: Path, slugs: dict[str, list[str]]) -> Path:
    cfg = tmp / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "scraper.yaml").write_text(
        "settings:\n  concurrency: 20\n  max_age_hours: null\n  location_filter: []\n", encoding="utf-8"
    )
    for board in ("greenhouse", "ashby", "lever", "bamboohr", "workday"):
        (cfg / f"{board}_companies.json").write_text(json.dumps(slugs.get(board, [])), encoding="utf-8")
    return cfg


async def run(args) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="hireshire_probe_"))
    run_id = datetime.now(timezone.utc).strftime("probe-%Y%m%dT%H%M%SZ")

    # --- resolve the small company set ---
    def _read_n(base: Path, n: int | None) -> dict[str, list[str]]:
        out = {}
        for b in _DEFAULT_SLUGS:
            f = base / f"{b}_companies.json"
            vals = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
            out[b] = vals[:n] if n else vals
        return out

    if args.from_config:
        slugs = _read_n(ROOT / "config", args.from_config)
    elif args.config_dir:
        slugs = _read_n(Path(args.config_dir), None)
    else:
        slugs = _DEFAULT_SLUGS
    cfg_dir = _write_temp_config(tmp, slugs)

    # --- isolate side effects; keep cwd at project root so matcher/tuner configs load ---
    import hireshire.config as C
    _orig_load = C.load_config
    scraper.load_config = lambda p="config/scraper.yaml": _orig_load(cfg_dir / "scraper.yaml")
    scraper.BAD_SLUGS_PATH = tmp / "bad_slugs.json"
    seen_path = tmp / "seen_jobs.json"
    matcher.SeenStore = lambda _p, _sp=seen_path: _RealSeenStore(_sp)  # dedup store → temp, repeatable

    results_dir = tmp / "pipeline" / run_id
    results_dir.mkdir(parents=True, exist_ok=True)

    cap = args.cap_per_company
    cap_q1 = (lambda item: (item[0], item[1][:cap])) if cap else None

    log_path = tmp / "queue_items.jsonl"
    print(f"Probe run {run_id}")
    print(f"  companies: " + ", ".join(f"{b}:{len(v)}" for b, v in slugs.items() if v))
    print(f"  matcher LLM: {'on' if args.llm else 'off (skip_llm)'} | tuner: {'REAL' if args.tuner else 'bypass'}"
          f" | cap/company: {cap or 'none'}")
    print(f"  queue log: {log_path}\n")

    with log_path.open("w", encoding="utf-8") as fh:
        q1 = LoggingQueue("q1 scraper→matcher", _summ_q1, fh, transform=cap_q1)
        q2 = LoggingQueue("q2 matcher→tuner", _summ_q2, fh)
        q3 = LoggingQueue("q3 tuner→tracker", _summ_q3, fh)

        tasks = [
            scraper.main(out_queue=q1, quiet=True, run_id=run_id),
            matcher.main(in_queue=q1, out_queue=q2, quiet=True, run_id=run_id, skip_llm=not args.llm),
            tuner.main(in_queue=q2, out_queue=q3, quiet=True) if args.tuner else _bypass_tuner(q2, q3),
            _track_results(q3, results_dir),
        ]
        await asyncio.gather(*tasks)

    print(f"\nSUMMARY  q1 batches={q1.count}  q2 tuner-inputs={q2.count}  q3 results={q3.count}")
    print(f"Full per-item log: {log_path}")
    print(f"Pipeline results:  {results_dir / 'pipeline_results.json'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Log each phase's input-queue items on a small multi-board set")
    p.add_argument("--llm", action="store_true", help="use real matcher LLM scoring (needs provider key)")
    p.add_argument("--tuner", action="store_true", help="run the real tuner phase (needs API key + pdflatex)")
    p.add_argument("--cap-per-company", type=int, default=3, help="trim each company's jobs to N (0 = no cap)")
    p.add_argument("--from-config", type=int, metavar="N", help="read the first N slugs from the real config/{board}_companies.json for each board")
    p.add_argument("--config-dir", help="dir with {board}_companies.json to use instead of the built-in sample")
    asyncio.run(run(p.parse_args()))
