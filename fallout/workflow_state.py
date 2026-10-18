"""Read-only presentation states, derived from revision-bound evidence."""

from .presentation import verification_state


def workflow_state(dataset, snapshot, report=None, preview=None, source_id=None,
                   operation_id=None, history=()):
    verdict, reason = verification_state(report, snapshot, dataset)
    verdict = "ERROR" if verdict == "BLOCKED" else verdict
    operation = snapshot.operations.get(operation_id)
    current_preview = bool(preview and preview.get("dataset_id") == snapshot.dataset_id
                           and preview.get("generation") == snapshot.generation
                           and preview.get("revision") == snapshot.revision
                           and preview.get("source_id") == source_id)
    events = [e for e in snapshot.events if operation and e.get("operation_id") == operation_id]
    injections = [e for e in events if e["type"] == "repair_started" and e.get("fault", "none") != "none"]
    completed = sum(s["status"] == "COMPLETED" for s in operation["steps"].values()) if operation else 0
    total = len(operation["steps"]) if operation else 0
    repaired = bool(operation and total and completed == total and operation["status"] == "repaired"
                    and not operation.get("false_success_pending"))
    recovered = bool(repaired and operation["attempts"] > 1 and any(
        e.get("attempt", 0) < operation["attempts"] and
        (e["type"] == "repair_failed" or e.get("outcome") in {"SKIPPED", "PARTIAL", "FAILED", "ROLLED_BACK"})
        for e in events))
    earlier_failures = [h for h in history if operation and h.get("generation") == snapshot.generation
                        and operation_id in h.get("operation_ids", []) and h.get("status") == "FAIL"
                        and h.get("revision", snapshot.revision) < snapshot.revision]
    labels = ["Impact", "Repair", "Verify", "Recover"]
    # Approval belongs to Repair; only an execution attempt completes that stage.
    done = [current_preview or bool(operation), bool(operation and operation["attempts"] > 0),
            verdict in {"PASS", "FAIL"}, recovered]
    current = 1 if current_preview else (2 if operation else 0)
    if operation and not repaired and verdict in {"PASS", "FAIL", "ERROR"}:
        current = 3
    if recovered and verdict not in {"PASS", "FAIL"}:
        current = 2
    banners = []
    if current_preview:
        banners.append(("pending", "PREVIEW · NOTHING CHANGED", "Impact only. Explicit approval is still required."))
    if operation:
        claim = "reported complete" if operation["status"] == "repaired" else operation["status"].replace("_", " ")
        banners.append(("neutral", "REPAIR EXECUTED · " + claim,
                        f"{completed} of {total} steps completed in attempt {operation['attempts']}. Execution is not verification."))
        if operation.get("false_success_pending"):
            banners.append(("fail" if verdict == "FAIL" else "pending", "FAILURE INJECTED · repair skipped a rebuild",
                            "Skipped summary: " + str(operation.get("skipped_summary")) + ". See the independent result separately."))
        elif injections and not recovered and operation.get("fault", "none") != "none":
            banners.append(("fail" if verdict == "FAIL" else "pending", "FAILURE INJECTED · " + operation["fault"],
                            "Recorded diagnostic fault; not itself a verifier verdict."))
        if recovered:
            banners.append(("neutral", "RECOVERY · missed work repaired", "Same operation ID · " + operation_id + ". Verification remains a separate claim."))
    return {"verdict": verdict, "reason": reason, "banners": banners,
            "steps": [{"label": label, "done": bool(done[i]), "current": i == current and not done[i]}
                      for i, label in enumerate(labels)],
            "compared": len(report["comparisons"]) if verdict in {"PASS", "FAIL"} else 0,
            "required": len(report["required_paths"]) if verdict in {"PASS", "FAIL"} else 0,
            "earlier_failures": earlier_failures}