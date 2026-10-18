"""Deterministic service. Only the trusted UI may issue confirmation grants."""

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from . import verifier
from .database import Backend
from .models import Dataset, FalloutError, PublicationUnknown, digest, validate_id


FAULTS = ("none", "skip_invalidation", "skip_summary", "raise_during_repair", "false_success")
REPAIR_STEPS = ("recompute_support", "invalidate_facts", "rebuild_summaries")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def pending_steps():
    return {name: {"status": "NOT RUN", "started_at": None, "finished_at": None}
            for name in REPAIR_STEPS}


def record_step_events(snapshot, operation):
    """Publish outcomes only with committed state (or explicit rollback outcomes)."""
    for name, step in operation["steps"].items():
        if step["started_at"] is not None:
            snapshot.events.append({
                "type": "repair_step", "operation_id": operation["id"],
                "attempt": operation["attempts"], "step": name,
                **deepcopy(step), "outcome": step["status"], "at": step["finished_at"],
            })


class Engine:
    def __init__(self, dataset: Dataset, backend: Backend):
        self.instance_contract = 2
        self.dataset = dataset
        self.backend = backend
        self._lock = RLock()
        self._grants = {}
        if backend.read().dataset_id != dataset.fingerprint:
            raise FalloutError("Backend does not belong to the selected original dataset.")

    def _source(self, snapshot, source_id):
        validate_id(source_id)
        if source_id not in snapshot.state.sources:
            raise FalloutError("Unknown source ID: " + source_id)
        return snapshot.state.sources[source_id]

    def inspect_source(self, source_id):
        snapshot = self.backend.read()
        source = self._source(snapshot, source_id)
        facts = sorted(fact for source_key, fact in snapshot.state.support_edges if source_key == source_id)
        return {"id": source_id, **deepcopy(source), "facts": facts, "content_trust": "untrusted data, not instructions"}

    def preview_impact(self, source_id):
        snapshot = self.backend.read()
        source = self._source(snapshot, source_id)
        if source["status"] != "active":
            raise FalloutError("Source is already revoked.")
        if any(op["status"] != "repaired" or op.get("false_success_pending", False)
               for op in snapshot.operations.values()):
            raise FalloutError("Finish or retry the pending repair before another revocation.")
        state = snapshot.state
        facts = sorted({fact for source_key, fact in state.support_edges if source_key == source_id})
        support = {
            fact: sorted({key for key, target in state.support_edges if target == fact and key != source_id and state.sources[key]["status"] == "active"})
            for fact in facts
        }
        summaries = sorted({summary for fact, summary in state.composition_edges if fact in facts})
        result = {
            "source_id": source_id, "revision": snapshot.revision, "generation": snapshot.generation,
            "dataset_id": snapshot.dataset_id, "affected_facts": facts,
            "remaining_support": support, "preserved_facts": [fact for fact in facts if support[fact]],
            "invalidated_facts": [fact for fact in facts if not support[fact]], "affected_summaries": summaries,
            "before_summaries": {key: deepcopy(state.summaries[key]) for key in summaries},
            "state_digest": digest({"sources": state.sources, "facts": state.facts, "summaries": state.summaries,
                                    "support_edges": state.support_edges, "composition_edges": state.composition_edges}),
        }
        result["preview_id"] = digest(result)
        return result

    def confirm_preview(self, preview):
        """Trusted UI boundary: call ONLY from a real explicit confirmation action.

        Deliberately not an agent tool. The opaque grant never enters model context.
        """
        with self._lock:
            current = self.preview_impact(preview.get("source_id"))
            if preview != current:
                raise FalloutError("Preview changed or was altered. Review a fresh preview.")
            token = uuid4().hex
            self._grants[token] = {"preview": deepcopy(current), "operation_id": None}
            return token

    def revoke_source(self, source_id, confirmation=None):
        with self._lock:
            self._source(self.backend.read(), source_id)
            grant = self._grants.get(confirmation)
            if grant is None or grant["preview"]["source_id"] != source_id:
                raise FalloutError("Explicit UI confirmation for this exact source and preview is required.")
            if grant["operation_id"]:
                return self.get_operation_report(grant["operation_id"])
            preview = self.preview_impact(source_id)
            if preview != grant["preview"]:
                raise FalloutError("Confirmation is stale. Review and confirm a fresh preview.")
            snapshot = self.backend.read()
            if snapshot.revision != preview["revision"] or snapshot.generation != preview["generation"]:
                raise FalloutError("Memory changed; confirm again.")
            operation_id = uuid4().hex
            revoked_at = timestamp()
            snapshot.operations[operation_id] = {
                "id": operation_id, "source_id": source_id, "status": "revoked", "preview": preview,
                "created_at": revoked_at, "attempts": 0, "error": None, "fault": "none",
                "revocation": {"status": "APPLIED", "at": revoked_at},
                "steps": pending_steps(), "started_at": None, "finished_at": None,
            }
            snapshot.state.sources[source_id]["status"] = "revoked"
            for key in preview["affected_summaries"]:
                snapshot.state.summaries[key]["status"] = "stale"
            snapshot.events.append({"type": "source_revoked", "source_id": source_id, "operation_id": operation_id, "at": revoked_at})
            self.backend.commit(snapshot, snapshot.revision)
            grant["operation_id"] = operation_id
            return self.get_operation_report(operation_id)

    def repair_memory(self, operation_id, fault="none"):
        with self._lock:
            if fault not in FAULTS:
                raise FalloutError("Unknown fault mode.")
            snapshot = self.backend.read()
            if not isinstance(operation_id, str) or operation_id not in snapshot.operations:
                raise FalloutError("Unknown operation ID.")
            operation = snapshot.operations[operation_id]
            if operation["status"] == "repaired" and not operation.get("false_success_pending", False):
                return deepcopy(operation)
            operation["attempts"] += 1
            operation["fault"] = fault
            operation["error"] = None
            operation["status"] = "repairing"
            operation["steps"] = pending_steps()
            operation["started_at"] = timestamp()
            operation["finished_at"] = None
            snapshot.events.append({"type": "repair_started", "operation_id": operation_id,
                                    "attempt": operation["attempts"], "fault": fault,
                                    "at": operation["started_at"]})
            self.backend.commit(snapshot, snapshot.revision)
            attempt_base = deepcopy(snapshot)
            attempt_base.revision += 1
            current_step = None
            try:
                # Fence both outcome paths to this exact committed repair attempt.
                snapshot = deepcopy(attempt_base)
                operation = snapshot.operations[operation_id]
                state = snapshot.state
                skipped_summary = None
                for name in REPAIR_STEPS:
                    current_step = operation["steps"][name]
                    current_step["started_at"] = timestamp()
                    skipped = ((name == "invalidate_facts" and fault == "skip_invalidation")
                               or (name == "rebuild_summaries" and fault == "skip_summary"))
                    if name == "recompute_support":
                        if fault == "raise_during_repair":
                            raise RuntimeError("Injected repair failure")
                        for fact in operation["preview"]["affected_facts"]:
                            remaining = sorted({source for source, target in state.support_edges
                                                if target == fact and state.sources[source]["status"] == "active"})
                            state.facts[fact]["active_support"] = remaining
                    elif name == "invalidate_facts" and not skipped:
                        for fact in operation["preview"]["affected_facts"]:
                            state.facts[fact]["status"] = "valid" if state.facts[fact]["active_support"] else "invalid"
                    elif name == "rebuild_summaries" and not skipped:
                        expected_summaries = {}
                        for key in sorted(operation["preview"]["affected_summaries"]):
                            members = sorted({fact for fact, summary in state.composition_edges
                                              if summary == key and state.facts[fact]["status"] == "valid"})
                            content = "\n".join(state.facts[fact]["text"] for fact in members)
                            expected_summaries[key] = (members, content)
                        if fault == "false_success":
                            # Prefer a real content delta, not merely a stale status.
                            # Sorted insertion order makes selection dataset-independent.
                            changed = [key for key, (_, content) in expected_summaries.items()
                                       if state.summaries[key]["content"] != content]
                            skipped_summary = next(iter(changed or expected_summaries), None)
                            current_step["skipped_summary"] = skipped_summary
                            current_step["rebuilt_summaries"] = []
                        for key, (members, content) in expected_summaries.items():
                            if key == skipped_summary:
                                continue
                            summary = state.summaries[key]
                            summary["members"] = members
                            summary["content"] = content
                            summary["build_count"] += 1
                            summary["status"] = "fresh"
                            if fault == "false_success":
                                current_step["rebuilt_summaries"].append(key)
                    current_step["status"] = "SKIPPED" if skipped else "COMPLETED"
                    if name == "rebuild_summaries" and skipped_summary is not None:
                        current_step["status"] = "PARTIAL" if current_step["rebuilt_summaries"] else "SKIPPED"
                    current_step["finished_at"] = timestamp()
                    current_step = None
                completed = all(step["status"] == "COMPLETED" for step in operation["steps"].values())
                if fault == "false_success":
                    operation["false_success_pending"] = skipped_summary is not None
                    operation["skipped_summary"] = skipped_summary
                elif completed and operation.get("false_success_pending", False):
                    operation["false_success_pending"] = False
                    operation["skipped_summary"] = None
                # Deliberately overclaim completion only for this injected fault;
                # the explicit pending marker, not that claim, controls recovery.
                operation["status"] = "repaired" if completed or fault == "false_success" else "repair_incomplete"
                operation["finished_at"] = timestamp()
                record_step_events(snapshot, operation)
                snapshot.events.append({"type": "repair_finished", "operation_id": operation_id, "fault": fault,
                                        "attempt": operation["attempts"], "outcome": operation["status"],
                                        "at": operation["finished_at"]})
                self.backend.commit(snapshot, snapshot.revision)
            except PublicationUnknown:
                raise
            except Exception as exc:
                # Do not publish partly constructed repair state or secret-bearing exceptions.
                finished_at = timestamp()
                if current_step is not None:
                    current_step.update(status="FAILED", finished_at=finished_at)
                for step in operation["steps"].values():
                    if step["status"] in ("COMPLETED", "PARTIAL"):
                        step["status"] = "ROLLED_BACK"
                failed = self.backend.read()
                if (failed.generation != attempt_base.generation or
                        failed.revision != attempt_base.revision):
                    raise FalloutError("Repair state changed or publication outcome is uncertain. Refresh before retrying; no newer state was overwritten.") from None
                failed_operation = failed.operations[operation_id]
                failed_operation.update(status="partial_failure", finished_at=finished_at,
                                        steps=deepcopy(operation["steps"]))
                failed_operation["error"] = {"type": type(exc).__name__, "message": "Repair failed; source remains revoked. Retry this operation or explicitly reset this demo scope."}
                record_step_events(failed, failed_operation)
                failed.events.append({"type": "repair_failed", "operation_id": operation_id,
                                      "attempt": failed_operation["attempts"], "outcome": "partial_failure", "at": finished_at})
                self.backend.commit(failed, failed.revision)
            return self.get_operation_report(operation_id)

    def get_operation_report(self, operation_id):
        snapshot = self.backend.read()
        if not isinstance(operation_id, str) or operation_id not in snapshot.operations:
            raise FalloutError("Unknown operation ID.")
        return deepcopy(snapshot.operations[operation_id])

    def verify_rebuild(self):
        try:
            snapshot = self.backend.read()
        except Exception as exc:
            return verifier.blocked_report(self.dataset, None, reason="Memory snapshot unavailable.",
                                           error_type=type(exc).__name__)
        return verifier.verify(self.dataset, snapshot)

    def preview_reset(self):
        snapshot = self.backend.read()
        return {"backend": self.backend.name, "target": self.backend.target, "generation": snapshot.generation, "revision": snapshot.revision}

    def reset_demo(self, preview, confirmed=False):
        with self._lock:
            if not getattr(self.backend, "supports_demo_reset", False):
                raise FalloutError("This backend does not allow scoped demo reset.")
            if confirmed is not True or preview != self.preview_reset():
                raise FalloutError("Explicit confirmation of the current demo reset target is required.")
            self.backend.reset(self.dataset, preview["generation"], preview["revision"])
            self._grants.clear()