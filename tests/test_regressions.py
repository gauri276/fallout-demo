"""Regressions for the reported PDF defects; no network or database required."""

from copy import deepcopy
from datetime import datetime
import unittest
from unittest.mock import patch

from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.models import Dataset, FalloutError
from fallout.presentation import current_report, graph_spec
from fallout.verifier import verify


class ReliabilityRegressions(unittest.TestCase):
    def setUp(self):
        self.dataset = Dataset.load()
        self.engine = Engine(self.dataset, InMemoryBackend(self.dataset))

    def revoke(self):
        token = self.engine.confirm_preview(self.engine.preview_impact("sales"))
        return self.engine.revoke_source("sales", token)

    def test_streamlit_padding_is_explicit_sided_object(self):
        spec = graph_spec(self.engine.backend.read().state, "sources.sales", "sales")
        self.assertEqual(spec["padding"], {"top": 12, "right": 12, "bottom": 12, "left": 12})

    def test_legacy_pass_without_comparisons_is_rejected(self):
        snapshot = self.engine.backend.read()
        report = verify(self.dataset, snapshot)
        report.pop("comparisons", None)
        self.assertFalse(current_report(report, snapshot))

    def test_pass_with_zero_or_omitted_comparisons_is_rejected(self):
        snapshot = self.engine.backend.read()
        for trim in (lambda rows: [], lambda rows: rows[1:]):
            report = verify(self.dataset, snapshot)
            report["comparisons"] = trim(report["comparisons"])
            self.assertFalse(current_report(report, snapshot))

    def test_pass_requires_completed_nonzero_schedule(self):
        report = self.engine.verify_rebuild()
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report.get("complete"))
        self.assertGreater(len(report.get("required_paths", [])), 0)
        self.assertEqual(set(report["required_paths"]), {row["path"] for row in report["comparisons"]})
        self.assertLessEqual(datetime.fromisoformat(report["started_at"]), datetime.fromisoformat(report["finished_at"]))

    def test_runtime_failure_is_blocked_not_pass_or_exception(self):
        with patch("fallout.verifier.reconstruct", side_effect=RuntimeError("private diagnostic")):
            report = self.engine.verify_rebuild()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["complete"])
        self.assertNotIn("private diagnostic", str(report))

    def test_skipped_summary_is_incomplete_and_retryable(self):
        operation = self.revoke()
        before = deepcopy(self.engine.backend.read().state.summaries["customer_summary"])
        repaired = self.engine.repair_memory(operation["id"], "skip_summary")
        self.assertEqual(repaired["status"], "repair_incomplete")
        self.assertEqual(self.engine.backend.read().state.summaries["customer_summary"], before)
        self.assertEqual(repaired["steps"]["rebuild_summaries"]["status"], "SKIPPED")
        report = self.engine.verify_rebuild()
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("summaries.customer_summary.content", [item["path"] for item in report["mismatches"]])
        with self.assertRaises(FalloutError):
            self.engine.preview_impact("pricing")
        retry = self.engine.repair_memory(operation["id"])
        self.assertEqual(retry["status"], "repaired")
        self.assertEqual(retry["attempts"], 2)
        self.assertEqual(self.engine.verify_rebuild()["status"], "PASS")

    def test_skipped_invalidation_is_not_claimed_completed(self):
        operation = self.revoke()
        report = self.engine.repair_memory(operation["id"], "skip_invalidation")
        self.assertEqual(report["status"], "repair_incomplete")
        self.assertEqual(report["steps"]["invalidate_facts"]["status"], "SKIPPED")
        self.assertEqual(self.engine.verify_rebuild()["status"], "FAIL")

    def test_repair_execution_has_actual_steps_and_no_implied_verification(self):
        operation = self.revoke()
        self.assertEqual(operation["revocation"]["status"], "APPLIED")
        operation = self.engine.repair_memory(operation["id"])
        self.assertEqual(operation["status"], "repaired")
        self.assertNotIn("verified", operation)
        self.assertEqual(set(operation["steps"]), {"recompute_support", "invalidate_facts", "rebuild_summaries"})
        for step in operation["steps"].values():
            self.assertEqual(step["status"], "COMPLETED")
            self.assertLessEqual(datetime.fromisoformat(step["started_at"]), datetime.fromisoformat(step["finished_at"]))

    def test_dataset_and_reset_are_reproducible(self):
        original = self.engine.backend.read()
        operation = self.revoke()
        self.engine.repair_memory(operation["id"])
        self.engine.reset_demo(self.engine.preview_reset(), confirmed=True)
        restored = self.engine.backend.read()
        self.assertEqual(original.state, restored.state)
        self.assertEqual(original.dataset_id, restored.dataset_id)
        self.assertNotEqual(original.generation, restored.generation)


if __name__ == "__main__":
    unittest.main()