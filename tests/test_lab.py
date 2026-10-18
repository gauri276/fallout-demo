import json
import unittest
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from unittest.mock import patch
from uuid import UUID

from fallout import verifier
from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.lab import run_lab
from fallout.models import Dataset, Snapshot
from fallout.scenarios import get_scenario


class LabTests(unittest.TestCase):
    def test_real_normal_and_fault_evidence(self):
        run = run_lab()
        normal, fault = run["scenarios"]
        self.assertEqual((normal["name"], normal["fault"]), ("normal", "none"))
        self.assertEqual((fault["name"], fault["fault"]), ("skip_summary", "skip_summary"))
        self.assertEqual(normal["verdict"], "PASS")
        self.assertEqual(fault["verdict"], "FAIL")
        for scenario in run["scenarios"]:
            report = scenario["verification"]
            self.assertEqual(len(report["comparisons"]), 30)
            self.assertEqual(report["status"], scenario["verdict"])
            self.assertIsNone(verifier.report_problem(report, Dataset.load(),
                                                      Snapshot(**scenario["after"])))
            before, after = scenario["before"], scenario["after"]
            self.assertEqual(before["revision"], 0)
            self.assertEqual(before["operations"], {})
            self.assertEqual(before["events"], [])
            self.assertEqual(after["revision"], 3)
            self.assertEqual(before["generation"], after["generation"])
            self.assertEqual(before["state"]["sources"]["sales"]["status"], "active")
            self.assertEqual(after["state"]["sources"]["sales"]["status"], "revoked")
            operation = scenario["operation"]
            self.assertEqual(after["operations"], {operation["id"]: operation})
            self.assertEqual(operation["preview"], scenario["preview"])
            self.assertEqual(operation["revocation"]["status"], "APPLIED")
            self.assertEqual(operation["attempts"], 1)
            self.assertEqual(operation["fault"], scenario["fault"])
            self.assertEqual(report["revoked_sources"], ["sales"])
            self.assertEqual(report["generation"], after["generation"])
            self.assertEqual(report["revision"], after["revision"])
        self.assertEqual(normal["operation"]["status"], "repaired")
        self.assertTrue(all(step["status"] == "COMPLETED"
                            for step in normal["operation"]["steps"].values()))
        self.assertEqual(normal["verification"]["mismatches"], [])
        self.assertEqual(normal["after"]["state"]["summaries"]["customer_summary"]["content"],
                         "Phone support is available.")
        self.assertEqual(fault["operation"]["steps"]["rebuild_summaries"]["status"], "SKIPPED")
        mismatch = next(row for row in fault["verification"]["mismatches"]
                        if row["path"] == "summaries.customer_summary.content")
        self.assertEqual(mismatch["expected"], "Phone support is available.")
        self.assertEqual(mismatch["actual"],
                         fault["before"]["state"]["summaries"]["customer_summary"]["content"])
        json.dumps(run)  # Directly usable by JSON-oriented UI components.

    def test_fresh_loads_engines_ids_timestamps_and_retained_history(self):
        with patch("fallout.lab.Dataset.load", wraps=Dataset.load) as load:
            first = run_lab()
            saved = deepcopy(first)
            second = run_lab()
        self.assertEqual(load.call_count, 4)
        self.assertTrue(all(call.args == (get_scenario("pricing").path,) and call.kwargs == {}
                    for call in load.call_args_list))
        self.assertEqual(first, saved)
        ids, generations, operations = [], [], []
        for run in (first, second):
            ids.append(run["id"])
            start, finish = self.assert_timestamps(run)
            for scenario in run["scenarios"]:
                a, b = self.assert_timestamps(scenario)
                self.assertLessEqual(start, a)
                self.assertLessEqual(b, finish)
                ids.extend([scenario["id"], scenario["verification_id"]])
                generations.append(scenario["before"]["generation"])
                operations.append(scenario["operation"]["id"])
                self.assertEqual(scenario["before"]["revision"], 0)
                self.assertEqual(scenario["before"]["state"], first["scenarios"][0]["before"]["state"])
        self.assertEqual(len(set(generations)), 4)
        self.assertEqual(len(set(operations)), 4)
        ids += generations + operations
        self.assertEqual(len(set(ids)), len(ids))
        for value in ids:
            self.assertEqual(UUID(hex=value).version, 4)

    def assert_timestamps(self, record):
        start, finish = (datetime.fromisoformat(record[key])
                         for key in ("started_at", "finished_at"))
        self.assertIsNotNone(start.tzinfo)
        self.assertIsNotNone(finish.tzinfo)
        self.assertLessEqual(start, finish)
        return start, finish

    def test_unrelated_engine_and_confirmation_are_untouched(self):
        dataset = Dataset.load()
        engine = Engine(dataset, InMemoryBackend(dataset))
        preview = engine.preview_impact("pricing")
        grant = engine.confirm_preview(preview)
        before = asdict(engine.backend.read())
        grants = deepcopy(engine._grants)
        run_lab()
        self.assertEqual(asdict(engine.backend.read()), before)
        self.assertEqual(engine._grants, grants)
        self.assertEqual(engine.revoke_source("pricing", grant)["source_id"], "pricing")

    def test_verifier_exception_is_sanitized_and_other_scenario_runs(self):
        real_verify = verifier.verify
        calls = []

        def fail_once(dataset, snapshot):
            calls.append(snapshot.generation)
            if len(calls) == 1:
                raise RuntimeError("private-exception-marker")
            return real_verify(dataset, snapshot)

        with patch("fallout.verifier.verify", side_effect=fail_once):
            run = run_lab()
        broken, fault = run["scenarios"]
        self.assertEqual(broken["verdict"], "ERROR")
        self.assertIsNone(broken["verification"])
        self.assertIsNotNone(broken["verification_id"])
        self.assertEqual(broken["operation"]["status"], "repaired")
        self.assertEqual(broken["after"]["revision"], 3)
        self.assertEqual(fault["verdict"], "FAIL")
        self.assertEqual(len(set(calls)), 2)
        self.assertNotIn("private-exception-marker", json.dumps(run))
        self.assertNotIn("RuntimeError", json.dumps(run))

    def test_blocked_empty_contradictory_and_stale_reports_never_pass(self):
        real_verify = verifier.verify
        for kind in ("blocked", "empty", "contradictory", "stale", "missing"):
            def invalid(dataset, snapshot):
                report = real_verify(dataset, snapshot)
                if kind == "blocked":
                    return verifier.blocked_report(dataset, snapshot, "private-report-marker",
                                                   error_type="private-type-marker")
                if kind == "empty":
                    report.update(status="PASS", comparisons=[], mismatches=[], required_paths=[])
                elif kind == "contradictory":
                    report["status"] = "FAIL" if report["status"] == "PASS" else "PASS"
                elif kind == "stale":
                    report["generation"] = "another-generation"
                elif kind == "missing":
                    return None
                return report

            with self.subTest(kind=kind), patch("fallout.verifier.verify", side_effect=invalid):
                run = run_lab()
                for scenario in run["scenarios"]:
                    self.assertEqual(scenario["verdict"], "ERROR")
                    self.assertIsNone(scenario["verification"])
                    self.assertIsNotNone(scenario["after"])
                self.assertNotIn("private-report-marker", json.dumps(run))
                self.assertNotIn("private-type-marker", json.dumps(run))

    def test_verdicts_follow_actual_evidence_not_scenario_expectations(self):
        repair = Engine.repair_memory

        def swap_fault(engine, operation_id, fault="none"):
            return repair(engine, operation_id, fault="skip_summary" if fault == "none" else "none")

        with patch.object(Engine, "repair_memory", new=swap_fault):
            normal, fault = run_lab()["scenarios"]
        self.assertEqual(normal["verdict"], "FAIL")
        self.assertEqual(fault["verdict"], "PASS")
        self.assertEqual(len(fault["verification"]["comparisons"]), 30)

    def test_setup_failure_preserves_prior_scenario(self):
        dataset = Dataset.load()
        with patch("fallout.lab.Dataset.load", side_effect=[dataset, RuntimeError("private-load-marker")]):
            run = run_lab()
        normal, broken = run["scenarios"]
        self.assertEqual(normal["verdict"], "PASS")
        self.assertEqual(broken["verdict"], "ERROR")
        for key in ("before", "after", "preview", "operation", "verification", "verification_id"):
            self.assertIsNone(broken[key])
        self.assert_timestamps(broken)
        self.assertNotIn("private-load-marker", json.dumps(run))

    def test_repair_exception_retains_real_revoked_state(self):
        with patch.object(Engine, "repair_memory", side_effect=RuntimeError("private-repair-marker")):
            run = run_lab()
        for scenario in run["scenarios"]:
            self.assertEqual(scenario["verdict"], "ERROR")
            self.assertEqual(scenario["operation"]["status"], "revoked")
            self.assertEqual(scenario["after"]["revision"], 1)
            self.assertEqual(scenario["after"]["state"]["sources"]["sales"]["status"], "revoked")
            self.assertIsNone(scenario["verification_id"])
        self.assertNotIn("private-repair-marker", json.dumps(run))


if __name__ == "__main__":
    unittest.main()