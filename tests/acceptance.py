"""Standalone acceptance harness. Optional HTTP checks target loopback only."""

import argparse
import json
import subprocess
import sys
import urllib.request

from fallout.models import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true", help="Check an already-running localhost UI.")
    args = parser.parse_args()
    for fault in ("none", "skip_invalidation", "skip_summary", "raise_during_repair"):
        expected = "PASS" if fault == "none" else "FAIL"
        process = subprocess.run(
            [sys.executable, "-m", "fallout.cli", "demo", "--fault", fault, "--expect", expected],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        if process.returncode != 0:
            print("FAIL: CLI acceptance did not complete for " + fault)
            return 1
        report = json.loads(process.stdout)
        summary = {
            "fault": fault, "backend": report["backend"], "initial": report["initial_verification"]["status"],
            "repair_status": report["operation"]["status"], "verification": report["verification"]["status"],
            "mismatches": report["verification"]["mismatches"],
            "reset": report["reset_verification"]["status"], "rerun": report["rerun_verification"]["status"],
        }
        print(json.dumps(summary, indent=2))
        assert summary["verification"] == expected
        assert all(summary[key] == "PASS" for key in ("initial", "reset", "rerun"))
    if args.health:
        # Do not inherit a corporate HTTP proxy for a strictly local check.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path in ("/_stcore/health", "/"):
            with opener.open("http://127.0.0.1:8501" + path, timeout=5) as response:
                assert response.status == 200
                print("PASS: localhost HTTP " + path + " returned 200")
    print("PASS: acceptance harness; real FalkorDB, Windows and live LLM NOT TESTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())