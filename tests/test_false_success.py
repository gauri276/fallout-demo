"""False completion claims must fail independent content checks; stdlib only."""

import unittest
from unittest.mock import patch

from fallout.database import InMemoryBackend
from fallout.engine import Engine, FAULTS
from fallout.models import Dataset, FalloutError
from fallout.verifier import verify


def generic_dataset():
    # Deliberately unsorted: the first sorted affected summary needs no content
    # change, and two later summaries do. No demo-domain IDs are reused.
    return Dataset.from_dict({
        "sources": [
            {"id": "origin", "text": "Original evidence."},
            {"id": "reserve", "text": "Independent evidence."},
            {"id": "detached", "text": "Unrelated evidence."},
            {"id": "idle", "text": "Evidence without summary consumers."},
        ],
        "facts": [
            {"id": "gone_z", "text": "Retired Z.", "sources": ["origin"]},
            {"id": "kept", "text": "Retained evidence.", "sources": ["origin", "reserve"]},
            {"id": "gone_a", "text": "Retired A.", "sources": ["origin"]},
            {"id": "isolated", "text": "Unaffected evidence.", "sources": ["detached"]},
            {"id": "orphan", "text": "No summary uses this fact.", "sources": ["idle"]},
        ],
        "summaries": [
            {"id": "z_delta", "facts": ["gone_z", "kept"]},
            {"id": "unrelated", "facts": ["isolated"]},
            {"id": "b_delta", "facts": ["gone_a", "kept"]},
            {"id": "a_same", "facts": ["kept"]},
        ],
    })


class FalseSuccessTests(unittest.TestCase):
    def cases(self):
        return (
            (Dataset.load(), "sales", "pricing", "customer_summary", "Phone support is available."),
            (generic_dataset(), "origin", "reserve", "b_delta", "Retained evidence."),
        )

    def engine(self, dataset):
        return Engine(dataset, InMemoryBackend(dataset))

    def revoke(self, engine, source):
        token = engine.confirm_preview(engine.preview_impact(source))
        return engine.revoke_source(source, token)

    def assert_content_failure(self, report, summary, expected, actual):
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["complete"])
        self.assertNotEqual(expected, actual)
        self.assertIn({"path": "summaries." + summary + ".content",
                       "expected": expected, "actual": actual}, report["mismatches"])
        self.assertIn({"path": "operations.incomplete", "expected": [],
                       "actual": [], "match": True}, report["comparisons"])

    def assert_summary_writes(self, engine, before, operation, skipped=None):
        """Check every summary, including unchanged-content affected summaries."""
        after = engine.backend.read()
        affected = set(operation["preview"]["affected_summaries"])
        original = engine.dataset.original()
        facts = {row["id"]: row for row in original["facts"]}
        definitions = {row["id"]: row for row in original["summaries"]}
        revoked = {key for key, row in after.state.sources.items() if row["status"] == "revoked"}
        for key, prior in before.state.summaries.items():
            with self.subTest(summary=key):
                if key not in affected or key == skipped:
                    self.assertEqual(after.state.summaries[key], prior)
                    continue
                members = sorted(fact for fact in definitions[key]["facts"]
                                 if set(facts[fact]["sources"]) - revoked)
                self.assertEqual(after.state.summaries[key], {
                    "members": members,
                    "content": "\n".join(facts[fact]["text"] for fact in members),
                    "status": "fresh", "build_count": prior["build_count"] + 1,
                })
        self.assertEqual(after.state.sources, before.state.sources)
        self.assertEqual(after.state.support_edges, before.state.support_edges)
        self.assertEqual(after.state.composition_edges, before.state.composition_edges)
        for key in set(before.state.facts) - set(operation["preview"]["affected_facts"]):
            self.assertEqual(after.state.facts[key], before.state.facts[key])

    def test_fault_registration_preserves_existing_modes(self):
        self.assertEqual(FAULTS, ("none", "skip_invalidation", "skip_summary",
                                  "raise_during_repair", "false_success"))

    def test_false_success_has_exact_content_mismatch_and_truthful_steps(self):
        for dataset, source, _, skipped, expected in self.cases():
            with self.subTest(source=source):
                engine = self.engine(dataset)
                operation = self.revoke(engine, source)
                before = engine.backend.read()
                operation = engine.repair_memory(operation["id"], fault="false_success")
                self.assertEqual(operation["status"], "repaired")
                self.assertTrue(operation["false_success_pending"])
                self.assertEqual(operation["skipped_summary"], skipped)
                self.assertEqual(operation["attempts"], 1)
                self.assertIsNone(operation["error"])
                self.assert_summary_writes(engine, before, operation, skipped)
                steps = operation["steps"]
                self.assertEqual(steps["recompute_support"]["status"], "COMPLETED")
                self.assertEqual(steps["invalidate_facts"]["status"], "COMPLETED")
                rebuilt = sorted(set(operation["preview"]["affected_summaries"]) - {skipped})
                self.assertEqual(steps["rebuild_summaries"]["status"], "PARTIAL" if rebuilt else "SKIPPED")
                self.assertEqual(steps["rebuild_summaries"]["skipped_summary"], skipped)
                self.assertEqual(steps["rebuild_summaries"]["rebuilt_summaries"], rebuilt)
                snapshot = engine.backend.read()
                events = [event for event in snapshot.events if event["type"] == "repair_step"]
                self.assertEqual(len(events), 3)
                for event in events:
                    self.assertEqual(event["outcome"], steps[event["step"]]["status"])
                    self.assertIsNotNone(event["started_at"])
                    self.assertGreaterEqual(event["finished_at"], event["started_at"])
                self.assertEqual(snapshot.events[-1]["outcome"], "repaired")
                self.assert_content_failure(engine.verify_rebuild(), skipped, expected,
                                            before.state.summaries[skipped]["content"])
                self.assertEqual(engine.backend.read(), snapshot)

    def test_content_detection_survives_fresh_status_and_removed_diagnostics(self):
        for dataset, source, _, skipped, expected in self.cases():
            with self.subTest(source=source):
                engine = self.engine(dataset)
                operation = self.revoke(engine, source)
                engine.repair_memory(operation["id"], "false_success")
                snapshot = engine.backend.read()
                summary = snapshot.state.summaries[skipped]
                summary["status"] = "fresh"
                for field in ("fault", "steps", "skipped_summary", "false_success_pending"):
                    snapshot.operations[operation["id"]].pop(field)
                snapshot.events = [event for event in snapshot.events if event["type"] == "source_revoked"]
                self.assert_content_failure(verify(dataset, snapshot), skipped, expected, summary["content"])
                # Even fix the member list: content alone must still defeat the
                # success claim, with no fault marker or stale status as a hint.
                summary["members"] = [key for key in summary["members"]
                                      if snapshot.state.facts[key]["status"] == "valid"]
                report = verify(dataset, snapshot)
                self.assert_content_failure(report, skipped, expected, summary["content"])
                self.assertEqual(report["mismatches"], [{
                    "path": "summaries." + skipped + ".content",
                    "expected": expected, "actual": summary["content"],
                }])

    def test_same_id_recovery_rebuilds_all_affected_then_is_idempotent(self):
        for dataset, source, next_source, _, _ in self.cases():
            with self.subTest(source=source):
                engine = self.engine(dataset)
                operation = self.revoke(engine, source)
                engine.repair_memory(operation["id"], "false_success")
                before = engine.backend.read()
                with patch.object(engine.backend, "commit", wraps=engine.backend.commit) as commit:
                    recovered = engine.repair_memory(operation["id"])
                self.assertEqual(commit.call_count, 2)
                self.assertEqual(commit.call_args_list[0].args[0].state, before.state)
                self.assertEqual(recovered["id"], operation["id"])
                self.assertEqual(recovered["status"], "repaired")
                self.assertEqual(recovered["attempts"], 2)
                self.assertFalse(recovered["false_success_pending"])
                self.assertIsNone(recovered["skipped_summary"])
                self.assertEqual(recovered["fault"], "none")
                self.assertTrue(all(step["status"] == "COMPLETED" for step in recovered["steps"].values()))
                self.assert_summary_writes(engine, before, recovered)
                self.assertEqual(engine.verify_rebuild()["status"], "PASS")
                after = engine.backend.read()
                self.assertEqual(after.events[:len(before.events)], before.events)
                self.assertEqual(engine.repair_memory(operation["id"]), recovered)
                self.assertEqual(engine.backend.read(), after)
                self.assertEqual(engine.verify_rebuild()["status"], "PASS")
                following = self.revoke(engine, next_source)
                engine.repair_memory(following["id"])
                self.assertEqual(engine.verify_rebuild()["status"], "PASS")

    def test_normal_success_writes_all_affected_and_stays_idempotent(self):
        for dataset, source, _, _, _ in self.cases():
            with self.subTest(source=source):
                engine = self.engine(dataset)
                operation = self.revoke(engine, source)
                before = engine.backend.read()
                repaired = engine.repair_memory(operation["id"])
                self.assertEqual(repaired["status"], "repaired")
                self.assertFalse(repaired.get("false_success_pending", False))
                self.assert_summary_writes(engine, before, repaired)
                self.assertEqual(engine.verify_rebuild()["status"], "PASS")
                after = engine.backend.read()
                for fault in FAULTS:
                    self.assertEqual(engine.repair_memory(operation["id"], fault), repaired)
                    self.assertEqual(engine.backend.read(), after)

    def test_explicit_pending_marker_blocks_revocation_without_diagnostics(self):
        for dataset, source, next_source, _, _ in self.cases():
            with self.subTest(source=source):
                engine = self.engine(dataset)
                next_preview = engine.preview_impact(next_source)
                next_token = engine.confirm_preview(next_preview)
                operation = self.revoke(engine, source)
                engine.repair_memory(operation["id"], "false_success")
                snapshot = engine.backend.read()
                op = snapshot.operations[operation["id"]]
                for field in ("fault", "steps", "skipped_summary"):
                    op.pop(field)
                engine.backend.commit(snapshot, snapshot.revision)
                before = engine.backend.read()
                self.assertEqual(op["status"], "repaired")
                for action in (lambda: engine.preview_impact(next_source),
                               lambda: engine.confirm_preview(next_preview),
                               lambda: engine.revoke_source(next_source, next_token)):
                    with self.assertRaisesRegex(FalloutError, "pending repair"):
                        action()
                    self.assertEqual(engine.backend.read(), before)
                # Recovery is durable across Engine instances, not tied to grants
                # or the removed diagnostic fields.
                engine = Engine(dataset, engine.backend)
                recovered = engine.repair_memory(operation["id"])
                self.assertFalse(recovered["false_success_pending"])
                self.assertEqual(engine.verify_rebuild()["status"], "PASS")
                engine.preview_impact(next_source)

    def test_false_success_without_affected_summaries_is_safe(self):
        engine = self.engine(generic_dataset())
        operation = self.revoke(engine, "idle")
        self.assertEqual(operation["preview"]["affected_summaries"], [])
        before = engine.backend.read()
        repaired = engine.repair_memory(operation["id"], "false_success")
        self.assertEqual(repaired["status"], "repaired")
        self.assertFalse(repaired["false_success_pending"])
        self.assertIsNone(repaired["skipped_summary"])
        self.assertEqual(repaired["steps"]["rebuild_summaries"]["status"], "COMPLETED")
        self.assertEqual(repaired["steps"]["rebuild_summaries"]["rebuilt_summaries"], [])
        self.assertEqual(engine.backend.read().state.facts["orphan"]["status"], "invalid")
        self.assert_summary_writes(engine, before, repaired)
        self.assertEqual(engine.verify_rebuild()["status"], "PASS")
        after = engine.backend.read()
        self.assertEqual(engine.repair_memory(operation["id"]), repaired)
        self.assertEqual(engine.backend.read(), after)
        engine.preview_impact("origin")

    def test_no_content_delta_falls_back_to_first_sorted_affected_summary(self):
        engine = self.engine(generic_dataset())
        operation = self.revoke(engine, "reserve")
        before = engine.backend.read()
        repaired = engine.repair_memory(operation["id"], "false_success")
        self.assertEqual(repaired["status"], "repaired")
        self.assertEqual(repaired["skipped_summary"], "a_same")
        self.assertTrue(repaired["false_success_pending"])
        self.assert_summary_writes(engine, before, repaired, "a_same")
        self.assertEqual(engine.verify_rebuild()["mismatches"], [{
            "path": "summaries.a_same.status", "expected": "fresh", "actual": "stale",
        }])
        engine.repair_memory(operation["id"])
        self.assertEqual(engine.verify_rebuild()["status"], "PASS")

    def test_failed_or_incomplete_recovery_keeps_pending_until_corrected(self):
        for fault in ("raise_during_repair", "skip_summary", "skip_invalidation"):
            with self.subTest(fault=fault):
                engine = self.engine(generic_dataset())
                operation = self.revoke(engine, "origin")
                engine.repair_memory(operation["id"], "false_success")
                retry = engine.repair_memory(operation["id"], fault)
                self.assertTrue(retry["false_success_pending"])
                self.assertEqual(retry["status"], "partial_failure" if fault == "raise_during_repair"
                                 else "repair_incomplete")
                with self.assertRaisesRegex(FalloutError, "pending repair"):
                    engine.preview_impact("reserve")
                recovered = engine.repair_memory(operation["id"])
                self.assertEqual(recovered["attempts"], 3)
                self.assertFalse(recovered["false_success_pending"])
                self.assertEqual(engine.verify_rebuild()["status"], "PASS")

    def test_failed_recovery_commit_does_not_clear_pending(self):
        engine = self.engine(generic_dataset())
        operation = self.revoke(engine, "origin")
        engine.repair_memory(operation["id"], "false_success")
        before = engine.backend.read()
        commit = engine.backend.commit

        def reject_completion(snapshot, revision):
            if snapshot.operations[operation["id"]]["status"] == "repaired":
                raise RuntimeError("Rejected commit")
            commit(snapshot, revision)

        with patch.object(engine.backend, "commit", side_effect=reject_completion):
            failed = engine.repair_memory(operation["id"])
        self.assertEqual(failed["status"], "partial_failure")
        self.assertTrue(failed["false_success_pending"])
        self.assertEqual(failed["skipped_summary"], "b_delta")
        self.assertEqual(engine.backend.read().state, before.state)
        self.assertFalse(engine.repair_memory(operation["id"])["false_success_pending"])
        self.assertEqual(engine.verify_rebuild()["status"], "PASS")

    def test_failed_injection_commit_reports_partial_writes_as_rolled_back(self):
        engine = self.engine(generic_dataset())
        operation = self.revoke(engine, "origin")
        before = engine.backend.read()
        commit = engine.backend.commit

        def reject_completion(snapshot, revision):
            if snapshot.operations[operation["id"]]["status"] == "repaired":
                raise RuntimeError("Rejected commit")
            commit(snapshot, revision)

        with patch.object(engine.backend, "commit", side_effect=reject_completion):
            failed = engine.repair_memory(operation["id"], "false_success")
        self.assertEqual(failed["status"], "partial_failure")
        self.assertEqual(engine.backend.read().state, before.state)
        self.assertTrue(all(step["status"] == "ROLLED_BACK" for step in failed["steps"].values()))
        self.assertEqual(engine.repair_memory(operation["id"])["status"], "repaired")
        self.assertEqual(engine.verify_rebuild()["status"], "PASS")


if __name__ == "__main__":
    unittest.main()