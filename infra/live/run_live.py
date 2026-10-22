"""Host-side driver for the Phase 0 live run (ADR 0001).

Drives the pinned MiroFish engine through engine_adapter's MiroFishEngine:
prepare -> start -> poll status -> interview -> collect -> cleanup, then asks Zep
directly whether the run's graph is gone (A6). Writes everything to --out:

  spec.json, handle.json, status_trace.jsonl, artifacts.json, cleanup.json,
  zep_check.json, summary.json (and error.txt on failure).

Usage: uv run python infra/live/run_live.py --url http://<engine-ip>:5001 --out DIR
Needs ZEP_API_KEY in the environment only for the Zep-side deletion check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from engine_adapter.base import RunHandle, StageTimeouts
from engine_adapter.documents import mapping_loader
from engine_adapter.mirofish import MiroFishEngine
from engine_adapter.testing import SEED_URI, make_persona_cards, make_run_spec, seed_document

ZEP_BASE = "https://api.getzep.com/api/v2"
TERMINAL = ("completed", "failed", "cancelled")


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n")


def zep_graph_status(graph_id: str) -> dict[str, Any]:
    """GET the graph from Zep. 404 after cleanup means it was deleted on Zep's side."""
    key = os.environ.get("ZEP_API_KEY", "")
    if not key:
        return {"checked": False, "reason": "ZEP_API_KEY not set"}
    req = urllib.request.Request(  # noqa: S310 (fixed https URL)
        f"{ZEP_BASE}/graph/{graph_id}", headers={"Authorization": f"Api-Key {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https URL)
            return {"checked": True, "http_status": r.status, "exists": True}
    except urllib.error.HTTPError as e:
        return {"checked": True, "http_status": e.code, "exists": e.code != 404}
    except OSError as e:
        return {"checked": False, "reason": str(e)[:200]}


async def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    cards = make_persona_cards(args.agents)
    spec = make_run_spec(
        cards,
        run_id=f"live_{time.strftime('%Y%m%d%H%M%S')}",
        target_agents=args.agents,
        # Characterization run: record the agent count the engine produces (A1)
        # instead of failing on it.
        agent_tolerance=1.0,
        max_rounds=args.rounds,
        platforms=["reddit"],
        timeouts=StageTimeouts(poll_interval_s=5),
    )
    _dump(out / "spec.json", spec.model_dump(mode="json"))
    engine = MiroFishEngine(
        base_url=args.url,
        load_document=mapping_loader({SEED_URI: seed_document(cards)}),
        min_agents=1,
    )
    summary: dict[str, Any] = {"run_id": spec.run_id, "agents_target": args.agents}
    timings: dict[str, float] = {}
    handle: RunHandle | None = None
    rc = 0
    t_all = time.monotonic()
    try:
        if not await engine.health():
            raise RuntimeError(f"engine at {args.url} is not healthy")
        t0 = time.monotonic()
        handle = await engine.prepare(spec)
        timings["prepare_s"] = time.monotonic() - t0
        _dump(out / "handle.json", handle.model_dump(mode="json"))
        summary["agents_actual"] = handle.agents_actual

        t0 = time.monotonic()
        handle = await engine.start(handle)
        with (out / "status_trace.jsonl").open("w") as trace:
            deadline = time.monotonic() + args.sim_timeout
            while True:
                st = await engine.status(handle)
                trace.write(json.dumps({"t": time.time(), **st.model_dump(mode="json")}) + "\n")
                trace.flush()
                if st.state in TERMINAL:
                    summary["final_state"] = st.state
                    summary["final_error"] = st.error
                    break
                if time.monotonic() > deadline:
                    summary["final_state"] = "timeout"
                    await engine.cancel(handle)
                    break
                await asyncio.sleep(5)
        timings["simulate_s"] = time.monotonic() - t0

        if summary["final_state"] == "completed":
            t0 = time.monotonic()
            answers = await engine.interview(handle, spec.interview_questions[:1])
            timings["interview_s"] = time.monotonic() - t0
            summary["interview_answers"] = len(answers)

        t0 = time.monotonic()
        art = await engine.collect(handle)
        timings["collect_s"] = time.monotonic() - t0
        _dump(out / "artifacts.json", art.model_dump(mode="json"))
        summary["stats"] = art.stats.model_dump(mode="json")
        summary["warnings"] = art.engine_meta.warnings
    except Exception as exc:
        rc = 1
        # A failed prepare() attaches its partial handle so we can still clean up.
        handle = handle or getattr(exc, "handle", None)
        summary["error"] = f"{type(exc).__name__}: {exc}"[:1000]
        (out / "error.txt").write_text(traceback.format_exc())
    finally:
        if handle is not None:
            _dump(out / "handle.json", handle.model_dump(mode="json"))
            try:
                report = await engine.cleanup(handle)
                _dump(
                    out / "cleanup.json",
                    {**report.model_dump(mode="json"), "complete": report.complete},
                )
                summary["cleanup_complete"] = report.complete
            except Exception as exc:
                rc = 1
                summary["cleanup_error"] = f"{type(exc).__name__}: {exc}"[:500]
            graph_id = handle.engine_ids.get("graph_id")
            if graph_id:
                zep = zep_graph_status(graph_id)
                _dump(out / "zep_check.json", {"graph_id": graph_id, **zep})
                summary["zep_graph_deleted"] = not zep["exists"] if zep.get("checked") else None
        await engine.client.aclose()
        timings["total_s"] = time.monotonic() - t_all
        summary["timings_s"] = {k: round(v, 1) for k, v in timings.items()}
        _dump(out / "summary.json", summary)
        print(json.dumps(summary, indent=2, default=str))
    return rc


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", required=True, help="engine base URL, e.g. http://172.20.0.3:5001")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--agents", type=int, default=10)
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--sim-timeout", type=float, default=30 * 60, help="seconds")
    args = p.parse_args()
    Path(args.out).mkdir(parents=True, exist_ok=True)
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
