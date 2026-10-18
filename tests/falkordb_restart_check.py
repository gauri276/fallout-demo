"""Explicit restart proof for the dedicated task-created database only.

Not discovered by unittest. Run after browser validation finishes, before users
start working on this database. Never targets an arbitrary container or host.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from fallout.engine import Engine
from fallout.falkordb_backend import FalkorDBBackend
from fallout.models import ROOT, digest
from fallout.scenarios import get_scenario


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-dedicated-restart", action="store_true")
    args = parser.parse_args()
    if not args.approve_dedicated_restart:
        parser.error("Explicit approval of the dedicated database restart is required")
    scope = "restart_" + uuid4().hex
    before = {}
    for name in ("pricing", "documentation"):
        scenario = get_scenario(name)
        dataset = scenario.load()
        engine = Engine(dataset, FalkorDBBackend(dataset, scope=scope))
        token = engine.confirm_preview(engine.preview_impact(scenario.withdraw_source))
        op = engine.revoke_source(scenario.withdraw_source, token)
        engine.repair_memory(op["id"], "false_success")
        assert engine.verify_rebuild()["status"] == "FAIL"
        before[name] = {"snapshot": engine.backend.read(), "operation_id": op["id"]}
    # Only the new container created for this integration; no service-wide restart.
    subprocess.run(["docker", "restart", "fallout-falkordb-rdb-20261018"], check=True)
    import redis
    client = redis.Redis(host="127.0.0.1", port=6380, socket_timeout=2)
    deadline = time.monotonic() + 60
    while True:
        try:
            if client.ping():
                break
        except redis.RedisError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("Dedicated database did not return after restart")
        time.sleep(1)
    result = {"status": "PASS", "scope": scope, "kind": "graceful dedicated-container restart, not crash/failover test", "scenarios": {}}
    for name, saved in before.items():
        dataset = get_scenario(name).load()
        engine = Engine(dataset, FalkorDBBackend(dataset, scope=scope))
        assert engine.backend.read() == saved["snapshot"]
        assert engine.verify_rebuild()["status"] == "FAIL"
        op = engine.repair_memory(saved["operation_id"])
        assert op["id"] == saved["operation_id"] and op["attempts"] == 2
        report = engine.verify_rebuild()
        assert report["status"] == "PASS"
        result["scenarios"][name] = {"snapshot_digest_preserved": digest(asdict(saved["snapshot"])),
                                     "operation_id": op["id"], "verification_after_recovery": report}
    path = ROOT / ".runtime" / "falkordb-validation" / (scope + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps({"status": "PASS", "evidence": str(path.relative_to(ROOT))}))


if __name__ == "__main__":
    main()