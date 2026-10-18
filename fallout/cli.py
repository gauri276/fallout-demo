"""A reproducible disposable in-memory demo, never a database maintenance CLI."""

import argparse
import json

from .config import Config, create_backend
from .engine import Engine, FAULTS
from .models import Dataset, FalloutError


def demo(fault="none"):
    dataset = Dataset.load()
    # This automatically authorizing harness must never inherit a database target.
    engine = Engine(dataset, create_backend(dataset, Config()))
    initial = engine.verify_rebuild()
    preview = engine.preview_impact("sales")
    # This CLI is a test harness over newly allocated memory, not a UI authorization path.
    token = engine.confirm_preview(preview)
    operation = engine.revoke_source("sales", token)
    operation = engine.repair_memory(operation["id"], fault)
    verification = engine.verify_rebuild()
    state = engine.backend.read().state
    engine.reset_demo(engine.preview_reset(), confirmed=True)
    reset = engine.verify_rebuild()
    rerun_token = engine.confirm_preview(engine.preview_impact("sales"))
    rerun_operation = engine.revoke_source("sales", rerun_token)
    engine.repair_memory(rerun_operation["id"])
    rerun = engine.verify_rebuild()
    return {"backend": engine.backend.name, "target": engine.backend.target, "fault": fault,
            "initial_verification": initial, "impact_preview": preview, "operation": operation,
            "after": {"sources": state.sources, "facts": state.facts, "summaries": state.summaries},
            "verification": verification, "reset_verification": reset, "rerun_verification": rerun}


def main():
    parser = argparse.ArgumentParser(description="Run FALLOUT on newly allocated synthetic in-memory data only.")
    parser.add_argument("command", choices=["demo"])
    parser.add_argument("--fault", choices=FAULTS, default="none")
    parser.add_argument("--expect", choices=["PASS", "FAIL"], default="PASS", help="Expected verification outcome; mismatch exits 1.")
    args = parser.parse_args()
    try:
        report = demo(args.fault)
    except FalloutError as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    checks = [report[key]["status"] == "PASS" for key in ("initial_verification", "reset_verification", "rerun_verification")]
    return 0 if all(checks) and report["verification"]["status"] == args.expect else 1


if __name__ == "__main__":
    raise SystemExit(main())