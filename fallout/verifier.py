"""Independent oracle: no imports from engine and no use of repaired success flags."""

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from .models import Dataset, MemoryState, Snapshot


REPORT_VERSION = 2


def required_paths(dataset, snapshot):
    """Schedule from original input and operation history, not generated comparisons."""
    paths = {"dataset_id", "support_edges", "composition_edges", "operations.incomplete"}
    fields = {"sources": ("text", "status"), "facts": ("text", "status", "active_support"),
              "summaries": ("members", "content", "status")}
    for group, names in fields.items():
        paths.add(group + ".ids")
        for row in dataset.original()[group]:
            prefix = group + "." + row["id"] + "."
            paths.update(prefix + name for name in ("fields",) + names)
    for index, event in enumerate(snapshot.events):
        if event.get("type") == "source_revoked":
            paths.update("history.%s.%s" % (index, name) for name in
                         ("known_source", "unique_source", "unique_operation", "operation_source"))
    paths.update("operations." + key + ".revocation_recorded" for key in snapshot.operations)
    return sorted(paths)


def blocked_report(dataset, snapshot, reason, error_type=None, started_at=None):
    return {"status": "BLOCKED", "report_version": REPORT_VERSION, "complete": False,
            "reason": reason, "error_type": error_type, "comparisons": [], "mismatches": [],
            "required_paths": [], "revoked_sources": [], "dataset_id": dataset.fingerprint,
            "revision": snapshot.revision if snapshot else None,
            "generation": snapshot.generation if snapshot else None,
            "started_at": started_at or datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat()}


def report_problem(report, dataset=None, snapshot=None):
    """Reject legacy, empty, truncated, contradictory or unexecuted evidence."""
    if not isinstance(report, dict) or report.get("report_version") != REPORT_VERSION:
        return "Legacy or unsupported verification report. Run verification with the current runtime."
    if report.get("status") == "BLOCKED":
        return report.get("reason") or "Verification could not complete."
    if report.get("complete") is not True:
        return "Verification did not complete all required checks."
    try:
        rows = report["comparisons"]
        schedule = report["required_paths"]
        if not isinstance(rows, list) or not rows or not isinstance(schedule, list) or not schedule:
            return "Verification has no executed comparison evidence."
        if dataset is not None and snapshot is not None and schedule != required_paths(dataset, snapshot):
            return "Verification schedule does not match the original input and operation history."
        paths = [row["path"] for row in rows]
        if len(paths) != len(set(paths)) or set(paths) != set(schedule):
            return "Verification evidence is incomplete or contains duplicate checks."
        failures = []
        for row in rows:
            same = row["expected"] == row["actual"]
            if row["match"] is not same:
                return "Comparison outcome contradicts its expected and actual values."
            if not same:
                failures.append({key: row[key] for key in ("path", "expected", "actual")})
        if report["mismatches"] != failures:
            return "Mismatch records do not agree with executed comparisons."
        if report["status"] != ("FAIL" if failures else "PASS"):
            return "Verification verdict contradicts executed comparisons."
        if datetime.fromisoformat(report["finished_at"]) < datetime.fromisoformat(report["started_at"]):
            return "Invalid verification execution timestamps."
    except (KeyError, TypeError, ValueError):
        return "Malformed verification evidence. Run verification again."
    return None


def reconstruct(dataset: Dataset, revoked_sources: Iterable[str]) -> MemoryState:
    """Compute the complete expected projection from original input only."""
    original = dataset.original()
    revoked = set(revoked_sources)
    expected = MemoryState()
    for row in original["sources"]:
        expected.sources[row["id"]] = {"text": row["text"], "status": "revoked" if row["id"] in revoked else "active"}
    for row in original["facts"]:
        remaining = sorted(source for source in row["sources"] if source not in revoked)
        expected.facts[row["id"]] = {"text": row["text"], "status": "valid" if remaining else "invalid", "active_support": remaining}
        for source in row["sources"]:
            expected.support_edges.append((source, row["id"]))
    for row in original["summaries"]:
        members = sorted(fact for fact in row["facts"] if expected.facts[fact]["status"] == "valid")
        expected.summaries[row["id"]] = {
            "members": members, "content": "\n".join(expected.facts[fact]["text"] for fact in members), "status": "fresh",
        }
        for fact in row["facts"]:
            expected.composition_edges.append((fact, row["id"]))
    return expected


def _compare_state(dataset: Dataset, snapshot: Snapshot) -> Dict[str, Any]:
    mismatches = []
    comparisons = []

    def compare(path, expected, actual):
        comparisons.append({"path": path, "expected": expected, "actual": actual, "match": expected == actual})
        if expected != actual:
            mismatches.append({"path": path, "expected": expected, "actual": actual})

    compare("dataset_id", dataset.fingerprint, snapshot.dataset_id)
    # The ledger records authorized revocation independently of mutable graph nodes.
    known_sources = {row["id"] for row in dataset.original()["sources"]}
    revoked = []
    revocation_operations = set()
    for index, event in enumerate(snapshot.events):
        if event.get("type") != "source_revoked":
            continue
        source = event.get("source_id")
        operation_id = event.get("operation_id")
        compare("history.%s.known_source" % index, True, source in known_sources)
        compare("history.%s.unique_source" % index, False, source in revoked)
        compare("history.%s.unique_operation" % index, False, operation_id in revocation_operations)
        operation = snapshot.operations.get(operation_id, {})
        compare("history.%s.operation_source" % index, source, operation.get("source_id"))
        if source in known_sources:
            revoked.append(source)
        revocation_operations.add(operation_id)
    expected = reconstruct(dataset, revoked)
    for group in ("sources", "facts", "summaries"):
        wanted, actual = getattr(expected, group), getattr(snapshot.state, group)
        compare(group + ".ids", sorted(wanted), sorted(actual))
        for key in sorted(wanted):
            fields = set(wanted[key])
            record = actual.get(key, {})
            compare(group + "." + key + ".fields", sorted(fields), sorted(set(record) - ({"build_count"} if group == "summaries" else set())))
            for field in sorted(fields):
                compare(group + "." + key + "." + field, wanted[key][field], record.get(field))
    for relation in ("support_edges", "composition_edges"):
        wanted = Counter(getattr(expected, relation))
        actual = Counter(getattr(snapshot.state, relation))
        compare(relation, sorted(wanted.elements()), sorted(actual.elements()))
    pending = sorted(key for key, op in snapshot.operations.items() if op.get("status") != "repaired")
    compare("operations.incomplete", [], pending)
    for key, op in snapshot.operations.items():
        compare("operations." + key + ".revocation_recorded", True, key in revocation_operations)
    return {
        "status": "FAIL" if mismatches else "PASS", "mismatches": mismatches,
        "comparisons": comparisons,
        "revoked_sources": sorted(set(revoked)), "revision": snapshot.revision,
        "generation": snapshot.generation, "dataset_id": dataset.fingerprint,
        "backend_scope": "State verification only; not a database compatibility or physical-erasure proof.",
    }


def verify(dataset: Dataset, snapshot: Snapshot) -> Dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    try:
        schedule = required_paths(dataset, snapshot)
        report = _compare_state(dataset, snapshot)
        report.update(report_version=REPORT_VERSION, complete=True, required_paths=schedule,
                      started_at=started, finished_at=datetime.now(timezone.utc).isoformat())
        problem = report_problem(report, dataset, snapshot)
        if problem:
            return blocked_report(dataset, snapshot, problem, started_at=started)
        return report
    except Exception as exc:
        return blocked_report(dataset, snapshot, "Independent verification could not complete. No PASS is available.",
                              error_type=type(exc).__name__, started_at=started)