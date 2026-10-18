"""Pure, offline workflow presentation contracts over both real fixture engines.

No Streamlit, config, providers or mocked engine methods. Only malformed report
and history metadata cases alter copied evidence; execution always stays real.
"""

from copy import deepcopy
import unittest

from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.models import initial_state
from fallout.scenarios import get_scenario
from fallout.workflow_state import workflow_state


CASES = {
    "pricing": {
        "skipped": "customer_summary", "invalidated": "discount_20",
        "members": ["phone_support"], "content": "Phone support is available.",
        "next_source": "pricing", "counts": (2, 2, 1),
    },
    "documentation": {
        "skipped": "api_overview", "invalidated": "legacy_query_tokens",
        "members": ["cursor_pagination", "rate_limit"],
        "content": "Cursor pagination is supported.\nThe API allows 100 requests per minute.",
        "next_source": "api_reference", "counts": (3, 4, 3),
    },
}
LABELS = ["Impact", "Repair", "Verify", "Recover"]


class WorkflowStateTests(unittest.TestCase):
    def engine(self, scenario_id):
        scenario = get_scenario(scenario_id)
        dataset = scenario.load()
        return Engine(dataset, InMemoryBackend(dataset)), scenario.withdraw_source

    def project(self, engine, **kwargs):
        """Every projection must leave all inputs, saved memory and grants alone."""
        snapshot = engine.backend.read()
        before = deepcopy((snapshot, kwargs, engine._grants))
        original = engine.dataset.canonical
        result = workflow_state(engine.dataset, snapshot, **kwargs)
        self.assertEqual((snapshot, kwargs, engine._grants), before)
        self.assertEqual(engine.backend.read(), before[0])
        self.assertEqual(engine.dataset.canonical, original)
        self.assertEqual([step["label"] for step in result["steps"]], LABELS)
        self.assertLessEqual(sum(step["current"] for step in result["steps"]), 1)
        self.assertTrue(result["reason"])
        return result

    def repair(self, engine, source, fault="none"):
        preview = engine.preview_impact(source)
        operation = engine.revoke_source(source, engine.confirm_preview(preview))
        return engine.repair_memory(operation["id"], fault)

    def verify(self, engine):
        before, grants = engine.backend.read(), deepcopy(engine._grants)
        report = engine.verify_rebuild()
        self.assertEqual(engine.backend.read(), before)
        self.assertEqual(engine._grants, grants)
        self.assertTrue(report["complete"])
        self.assertTrue(report["comparisons"])
        return report

    def history_entry(self, engine, report):
        return {"status": report["status"], "revision": report["revision"],
                "generation": report["generation"], "operation_ids": list(engine.backend.read().operations),
                "mismatch_count": len(report["mismatches"]), "finished_at": report["finished_at"]}

    def assert_verdict(self, state, verdict, report=None):
        self.assertEqual(state["verdict"], verdict)
        checked = verdict in {"PASS", "FAIL"}
        self.assertEqual(state["steps"][2]["done"], checked)
        self.assertEqual(state["compared"], len(report["comparisons"]) if checked else 0)
        self.assertEqual(state["required"], len(report["required_paths"]) if checked else 0)

    def test_both_actual_fixtures_initial_not_run(self):
        fingerprints = set()
        for scenario_id, case in CASES.items():
            with self.subTest(scenario=scenario_id):
                engine, source = self.engine(scenario_id)
                snapshot = engine.backend.read()
                fingerprints.add(snapshot.dataset_id)
                self.assertEqual(snapshot.state, initial_state(engine.dataset))
                self.assertEqual(tuple(len(getattr(snapshot.state, group)) for group in
                                       ("sources", "facts", "summaries")), case["counts"])
                self.assertEqual(snapshot.operations, {})
                self.assertEqual(snapshot.events, [])
                self.assertEqual(snapshot.revision, 0)
                state = self.project(engine, source_id=source)
                self.assert_verdict(state, "NOT RUN")
                self.assertEqual(state["banners"], [])
                self.assertEqual(state["earlier_failures"], [])
                self.assertEqual([s["done"] for s in state["steps"]], [False] * 4)
                self.assertTrue(state["steps"][0]["current"])
        self.assertEqual(len(fingerprints), 2)

    def test_current_and_stale_preview_are_read_only(self):
        for scenario_id, case in CASES.items():
            with self.subTest(scenario=scenario_id):
                engine, source = self.engine(scenario_id)
                before = engine.backend.read()
                preview = engine.preview_impact(source)
                self.assertEqual(engine.backend.read(), before)
                state = self.project(engine, preview=preview, source_id=source)
                self.assert_verdict(state, "NOT RUN")
                self.assertEqual(state["banners"], [("pending", "PREVIEW · NOTHING CHANGED",
                                                    "Impact only. Explicit approval is still required.")])
                self.assertEqual([s["done"] for s in state["steps"]], [True, False, False, False])
                self.assertTrue(state["steps"][1]["current"])
                self.assertEqual(engine._grants, {})
                # A selected-source mismatch needs no write to invalidate the preview.
                changed = self.project(engine, preview=preview, source_id=case["next_source"])
                self.assertEqual(changed["banners"], [])
                self.assertFalse(changed["steps"][0]["done"])
                # Real execution advances revision; reset advances generation.
                self.repair(engine, source)
                stale = self.project(engine, preview=preview, source_id=source)
                self.assert_verdict(stale, "NOT RUN")
                self.assertEqual(stale["banners"], [])
                engine.reset_demo(engine.preview_reset(), confirmed=True)
                self.assertNotEqual(engine.backend.read().generation, preview["generation"])
                self.assertEqual(engine.backend.read().revision, preview["revision"])
                self.assertEqual(self.project(engine, preview=preview, source_id=source)["banners"], [])
                other, _ = self.engine(next(key for key in CASES if key != scenario_id))
                self.assertEqual(self.project(other, preview=preview, source_id=source)["banners"], [])

    def test_approval_alone_does_not_complete_repair(self):
        for scenario_id in CASES:
            with self.subTest(scenario=scenario_id):
                engine, source = self.engine(scenario_id)
                preview = engine.preview_impact(source)
                before = engine.backend.read()
                grant = engine.confirm_preview(preview)
                self.assertEqual(engine.backend.read(), before)
                approved = self.project(engine, preview=preview, source_id=source)
                self.assertEqual([s["done"] for s in approved["steps"]], [True, False, False, False])
                self.assertTrue(approved["steps"][1]["current"])
                operation = engine.revoke_source(source, grant)
                self.assertEqual(operation["attempts"], 0)
                pending = self.project(engine, operation_id=operation["id"])
                self.assert_verdict(pending, "NOT RUN")
                self.assertEqual([s["done"] for s in pending["steps"]], [True, False, False, False])
                self.assertTrue(pending["steps"][2]["current"])

    def test_normal_repair_is_not_pass_until_independent_verifier(self):
        for scenario_id in CASES:
            with self.subTest(scenario=scenario_id):
                engine, source = self.engine(scenario_id)
                operation = self.repair(engine, source)
                self.assertEqual(operation["status"], "repaired")
                self.assertTrue(all(s["status"] == "COMPLETED" for s in operation["steps"].values()))
                state = self.project(engine, operation_id=operation["id"])
                self.assert_verdict(state, "NOT RUN")
                self.assertEqual([s["done"] for s in state["steps"]], [True, True, False, False])
                self.assertTrue(state["steps"][2]["current"])
                self.assertEqual(len(state["banners"]), 1)
                self.assertEqual(state["banners"][0][:2], ("neutral", "REPAIR EXECUTED · reported complete"))
                self.assertIn("Execution is not verification.", state["banners"][0][2])
                report = self.verify(engine)
                self.assertEqual(report["mismatches"], [])
                checked = self.project(engine, operation_id=operation["id"], report=report)
                self.assert_verdict(checked, "PASS", report)
                self.assertEqual([s["done"] for s in checked["steps"]], [True, True, True, False])
                self.assertEqual(checked["banners"], state["banners"])

    def test_false_success_amber_then_red_requires_semantic_mismatches(self):
        for scenario_id, case in CASES.items():
            with self.subTest(scenario=scenario_id):
                engine, source = self.engine(scenario_id)
                before = engine.backend.read()
                operation = self.repair(engine, source, "false_success")
                self.assertEqual(operation["status"], "repaired")
                self.assertTrue(operation["false_success_pending"])
                self.assertEqual(operation["skipped_summary"], case["skipped"])
                self.assertEqual(operation["steps"]["rebuild_summaries"]["status"],
                                 "SKIPPED" if scenario_id == "pricing" else "PARTIAL")
                actual = engine.backend.read().state.summaries[case["skipped"]]
                self.assertEqual(actual, {**before.state.summaries[case["skipped"]], "status": "stale"})
                self.assertIn(case["invalidated"], actual["members"])
                self.assertNotEqual(actual["content"], case["content"])
                amber = self.project(engine, operation_id=operation["id"])
                self.assert_verdict(amber, "NOT RUN")
                self.assertEqual(amber["banners"][1][:2], ("pending", "FAILURE INJECTED · repair skipped a rebuild"))
                self.assertIn(case["skipped"], amber["banners"][1][2])
                self.assertTrue(any(e["type"] == "repair_started" and e.get("fault") == "false_success"
                                    and e.get("operation_id") == operation["id"]
                                    for e in engine.backend.read().events))
                self.assertEqual([s["done"] for s in amber["steps"]], [True, True, False, False])
                self.assertTrue(amber["steps"][2]["current"])
                self.assertFalse(amber["steps"][3]["done"])
                report = self.verify(engine)
                self.assertEqual(report["mismatches"], [
                    {"path": "summaries." + case["skipped"] + "." + field,
                     "expected": expected, "actual": actual[field]}
                    for field, expected in (("content", case["content"]), ("members", case["members"]), ("status", "fresh"))
                ])
                self.assertIn({"path": "operations.incomplete", "expected": [], "actual": [], "match": True},
                              report["comparisons"])
                red = self.project(engine, operation_id=operation["id"], report=report)
                self.assert_verdict(red, "FAIL", report)
                self.assertEqual(red["banners"][0], amber["banners"][0])
                self.assertEqual(red["banners"][1], ("fail", *amber["banners"][1][1:]))
                self.assertEqual([s["done"] for s in red["steps"]], [True, True, True, False])
                self.assertTrue(red["steps"][3]["current"])
                malformed = self.project(engine, operation_id=operation["id"], report={**report, "complete": False})
                self.assert_verdict(malformed, "ERROR")
                self.assertEqual([s["done"] for s in malformed["steps"]], [True, True, False, False])
                self.assertTrue(malformed["steps"][3]["current"])
                self.assertEqual(malformed["banners"], amber["banners"])

    def test_same_operation_recovery_not_run_then_pass_retains_earlier_fail(self):
        for scenario_id, case in CASES.items():
            with self.subTest(scenario=scenario_id):
                engine, source = self.engine(scenario_id)
                operation = self.repair(engine, source, "false_success")
                failed_report = self.verify(engine)
                history = [self.history_entry(engine, failed_report)]
                saved_history, saved_report = deepcopy(history), deepcopy(failed_report)
                self.assertEqual(self.project(engine, operation_id=operation["id"], report=failed_report,
                                              history=history)["earlier_failures"], [])
                failed = engine.backend.read()
                recovered = engine.repair_memory(operation["id"])
                after = engine.backend.read()
                self.assertEqual(list(after.operations), [operation["id"]])
                self.assertEqual(recovered["id"], operation["id"])
                self.assertEqual(recovered["attempts"], 2)
                self.assertFalse(recovered["false_success_pending"])
                self.assertEqual(after.revision, failed.revision + 2)
                self.assertEqual(after.events[:len(failed.events)], failed.events)
                self.assertEqual(after.state.summaries[case["skipped"]]["content"], case["content"])
                for report in (None, failed_report):
                    state = self.project(engine, operation_id=operation["id"], report=report, history=history)
                    self.assert_verdict(state, "NOT RUN")
                    self.assertTrue(state["steps"][2]["current"])
                    self.assertTrue(state["steps"][3]["done"])
                    self.assertEqual(state["banners"][-1], ("neutral", "RECOVERY · missed work repaired",
                        "Same operation ID · " + operation["id"] + ". Verification remains a separate claim."))
                    self.assertFalse(any(title.startswith("FAILURE INJECTED") for _, title, _ in state["banners"]))
                    self.assertEqual(state["earlier_failures"], saved_history)
                report = self.verify(engine)
                history.append(self.history_entry(engine, report))
                state = self.project(engine, operation_id=operation["id"], report=report, history=history)
                self.assert_verdict(state, "PASS", report)
                self.assertEqual([s["done"] for s in state["steps"]], [True] * 4)
                self.assertFalse(any(s["current"] for s in state["steps"]))
                self.assertEqual(state["earlier_failures"], saved_history)
                self.assertEqual([h["status"] for h in history], ["FAIL", "PASS"])
                self.assertEqual(history[0], saved_history[0])
                self.assertEqual(failed_report, saved_report)

    def test_malformed_empty_and_stale_reports_never_pass(self):
        for scenario_id in CASES:
            with self.subTest(scenario=scenario_id):
                engine, source = self.engine(scenario_id)
                operation = self.repair(engine, source)
                valid = self.verify(engine)
                defects = [[], "not a report", {}, {**valid, "comparisons": None},
                           {**valid, "comparisons": [None]}, {**valid, "comparisons": []},
                           {**valid, "comparisons": [], "required_paths": [], "mismatches": []},
                           {**valid, "comparisons": valid["comparisons"][:-1]},
                           {**valid, "complete": False}, {**valid, "status": "FAIL"}]
                for report in defects:
                    with self.subTest(report=report):
                        self.assert_verdict(self.project(engine, operation_id=operation["id"], report=report), "ERROR")
                # A new operation also changes the required comparison schedule:
                # old evidence is ERROR rather than a current-memory PASS.
                next_operation = self.repair(engine, CASES[scenario_id]["next_source"])
                state = self.project(engine, operation_id=next_operation["id"], report=valid)
                self.assert_verdict(state, "ERROR")
                self.assertIn("schedule does not match", state["reason"])
                engine.reset_demo(engine.preview_reset(), confirmed=True)
                self.assert_verdict(self.project(engine, report=valid), "ERROR")
                # Equal schedules/revisions but a real new generation: NOT RUN.
                initial_report = self.verify(engine)
                engine.reset_demo(engine.preview_reset(), confirmed=True)
                stale = self.project(engine, report=initial_report)
                self.assert_verdict(stale, "NOT RUN")
                self.assertIn("OUT OF DATE", stale["reason"])

    def test_other_operation_and_generation_history_excluded(self):
        for scenario_id, case in CASES.items():
            with self.subTest(scenario=scenario_id):
                engine, source = self.engine(scenario_id)
                first = self.repair(engine, source, "false_success")
                first_report = self.verify(engine)
                history = [self.history_entry(engine, first_report)]
                engine.repair_memory(first["id"])
                second = self.repair(engine, case["next_source"])
                self.assertLess(history[0]["revision"], engine.backend.read().revision)
                self.assertEqual(history[0]["generation"], engine.backend.read().generation)
                self.assertEqual(self.project(engine, operation_id=second["id"], history=history)["earlier_failures"], [])
                self.assertEqual(self.project(engine, operation_id=first["id"], history=history)["earlier_failures"], history)
                self.assertEqual(self.project(engine, operation_id="absent", history=history)["earlier_failures"], [])
                engine.reset_demo(engine.preview_reset(), confirmed=True)
                current = self.repair(engine, source, "false_success")
                engine.repair_memory(current["id"])
                # Even a misassociated operation ID must not admit old-generation history.
                foreign = [{**history[0], "operation_ids": [current["id"]]}]
                self.assertNotEqual(foreign[0]["generation"], engine.backend.read().generation)
                self.assertLess(foreign[0]["revision"], engine.backend.read().revision)
                self.assertEqual(self.project(engine, operation_id=current["id"], history=foreign)["earlier_failures"], [])


if __name__ == "__main__":
    unittest.main()