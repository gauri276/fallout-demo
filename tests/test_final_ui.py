"""Final workbench contracts, exercised offline through real AppTest actions.

Reuse the existing UI harness without inheriting/discovering its tests twice.
The full suite also retains its NOT RUN/stale/malformed ERROR, graph event,
render-failure fallback, authorization and provider-boundary regression tests.
No repair, verifier, scenario loader or graph projection is mocked here.
"""

from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
from html import escape
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from fallout import verifier
from fallout.ai_provider import AISettings
from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.graph_component import render_graph
from fallout.lab import run_lab
from fallout.models import MemoryState, Snapshot, initial_state
from fallout.scenarios import get_scenario
from tests import test_ui as ui_tests


INTRO = (
    "When evidence changes, AI memory can retain facts and summaries that are no longer supported. "
    "FALLOUT traces the dependencies, repairs affected memory, and independently checks whether "
    "the result matches the expected state."
)
CASES = {
    "pricing": {
        "source": "sales", "next_source": "pricing",
        "preserved": "phone_support", "invalidated": "discount_20",
        "support": "pricing", "skipped": "customer_summary",
        "affected": ["customer_summary"], "unrelated": [],
        "content": {"customer_summary": "Phone support is available."},
        "counts": (2, 2, 1),
    },
    "documentation": {
        "source": "obsolete_bulletin", "next_source": "api_reference",
        "preserved": "cursor_pagination", "invalidated": "legacy_query_tokens",
        "support": "api_reference", "skipped": "api_overview",
        "affected": ["api_overview", "migration_notes"],
        "unrelated": ["installation_summary"],
        "content": {
            "api_overview": "Cursor pagination is supported.\nThe API allows 100 requests per minute.",
            "migration_notes": "Cursor pagination is supported.",
            "installation_summary": "Installation requires Python 3.11 or later.",
        },
        "counts": (3, 4, 3),
    },
}


@unittest.skipUnless(ui_tests.HAS_STREAMLIT, "Streamlit is not installed.")
class FinalUITests(unittest.TestCase):
    setUp = ui_tests.UITests.setUp
    app = ui_tests.UITests.app
    navigate = ui_tests.UITests.navigate
    read_only = ui_tests.UITests.read_only
    html = ui_tests.UITests.html
    memory_lines = ui_tests.UITests.memory_lines
    graph_payload = ui_tests.UITests.graph_payload
    assert_no_pass = ui_tests.UITests.assert_no_pass

    def recorded_app(self, scenario_id="pricing", fault="none"):
        """Exercise the real renderer and real evidence without unrelated page widgets."""
        def page():
            import streamlit as st
            from fallout.workbench import recorded_memory
            recorded_memory(st.session_state.engine, st.session_state.engine.backend.read())

        dataset = get_scenario(scenario_id).load()
        engine = Engine(dataset, InMemoryBackend(dataset))
        before = engine.backend.read()
        source = CASES[scenario_id]["source"]
        token = engine.confirm_preview(engine.preview_impact(source))
        operation = engine.revoke_source(source, token)
        engine.repair_memory(operation["id"], fault=fault)
        app = ui_tests.AppTest.from_function(page, default_timeout=20)
        app.session_state.engine = engine
        app.session_state.before_snapshot = before
        app.session_state.verification = engine.verify_rebuild()
        with self.read_only(app):
            app.run()
        self.clean(app)
        return app

    def test_recorded_memory_exact_removed_retained_and_verified_stale(self):
        removed = {
            "pricing": "A 20% discount is available.\n",
            "documentation": "Legacy query-string access tokens are accepted.\n",
        }
        for scenario_id, case in CASES.items():
            for fault in ("none", "false_success"):
                with self.subTest(scenario=scenario_id, fault=fault):
                    app = self.recorded_app(scenario_id, fault)
                    snapshot = app.session_state.engine.backend.read()
                    before = app.session_state.before_snapshot
                    key = case["skipped"]
                    current = snapshot.state.summaries[key]["content"]
                    original = before.state.summaries[key]["content"]
                    report = app.session_state.verification
                    self.assert_report(report, app.session_state.engine.dataset, snapshot,
                                       "PASS" if fault == "none" else "FAIL")
                    self.assertEqual([item.value for item in app.caption][0],
                                     key + (" · FRESH" if fault == "none" else " · STALE"))
                    retained = [("retained", "Retained", "Phone support is available.")]
                    if scenario_id == "documentation":
                        retained = [("retained", "Retained", "Cursor pagination is supported.\n"),
                                    ("retained", "Retained", "The API allows 100 requests per minute.")]
                    offset = 0 if scenario_id == "pricing" else 1
                    changed = retained[:offset] + [("removed", "Removed", removed[scenario_id])] + retained[offset:]
                    if fault == "none":
                        # Preserve the retained segments on either side of the removal.
                        self.assertEqual(self.memory_lines(app)[:2], [changed, retained])
                        self.assertEqual(current, case["content"][key])
                    else:
                        stale = retained[:offset] + [("stale", "Left stale", removed[scenario_id])] + retained[offset:]
                        self.assertEqual(self.memory_lines(app)[:2], [
                            [("retained", "Retained", original)], stale])
                        self.assertEqual(current, original)
                    self.assertEqual(len(self.memory_lines(app)), 2 * len(snapshot.state.summaries))
                    exact = next(item for item in app.expander if item.label == "Exact saved text · " + key)
                    self.assertFalse(exact.proto.expanded)
                    self.assertEqual([item.value for item in exact.caption], ["BEFORE", "NOW"])
                    self.assertEqual([item.value for item in exact.text], [original, current])

    def test_recorded_memory_never_labels_stale_without_valid_current_report(self):
        app = self.recorded_app(fault="false_success")
        valid = deepcopy(app.session_state.verification)
        original = app.session_state.before_snapshot.state.summaries["customer_summary"]["content"]
        self.assertIn('class="fo-memory-line stale"', self.html(app))
        for defect in ("absent", "malformed", "truncated", "revision", "generation", "dataset_id"):
            with self.subTest(defect=defect), self.read_only(app):
                report = deepcopy(valid)
                if defect == "absent":
                    report = None
                elif defect == "malformed":
                    report = []
                elif defect == "truncated":
                    report["comparisons"].pop()
                elif defect == "revision":
                    report["revision"] -= 1
                else:
                    report[defect] = "other-binding"
                app.session_state.verification = report
                app.run()
                self.clean(app)
                self.assertNotIn('class="fo-memory-line stale"', self.html(app))
                self.assertEqual(self.memory_lines(app), [[("retained", "Retained", original)]] * 2)
                self.assertEqual(app.session_state.engine.backend.read().state.summaries["customer_summary"]["status"], "stale")
        with self.read_only(app):
            app.session_state.verification = valid
            app.run()
            self.clean(app)
            self.assertIn('class="fo-memory-line stale"', self.html(app))

    def test_recorded_memory_escapes_exact_diff_text_but_preserves_raw_evidence(self):
        app = self.recorded_app()
        engine = app.session_state.engine
        old = '<b>old & "quoted"</b>\n'
        extra = '<script>alert("&")</script>\n'
        retained = "Phone support is available."
        before = deepcopy(app.session_state.before_snapshot)
        before.state.summaries["customer_summary"]["content"] = old + retained
        snapshot = engine.backend.read()
        snapshot.state.summaries["customer_summary"]["content"] = extra + retained
        engine.backend.commit(snapshot, snapshot.revision)
        app.session_state.before_snapshot = before
        app.session_state.verification = engine.verify_rebuild()
        with self.read_only(app):
            app.run()
            self.clean(app)
            self.assert_report(app.session_state.verification, engine.dataset, engine.backend.read(), "FAIL")
            self.assertEqual(self.memory_lines(app), [
                [("removed", "Removed", old), ("retained", "Retained", retained)],
                [("stale", "Left stale", extra), ("retained", "Retained", retained)],
            ])
            html = self.html(app)
            for text in (old, extra):
                self.assertIn('<span class="fo-diff-text">' + escape(text) + '</span>', html)
                self.assertNotIn(text, html)
            exact = app.expander[0]
            self.assertFalse(exact.proto.expanded)
            self.assertEqual([item.value for item in exact.text], [old + retained, extra + retained])

    def test_technical_expanders_are_closed_through_preview_repair_and_verification(self):
        app = self.app()
        def assert_closed(required):
            self.clean(app)
            labels = {item.label for item in app.expander}
            self.assertTrue(set(required) <= labels, (required, labels))
            for item in app.expander:
                self.assertFalse(item.proto.expanded, item.label)

        assert_closed(["Source details", "Inspection help · read only", "Advanced details · environment"])
        self.preview(app, "sales")
        assert_closed(["About this source", "Advanced details · exact preview"])
        self.confirm(app)
        assert_closed(["Repair details · facts, lineage and execution", "Exact saved text · customer_summary",
                       "How this result was established", "Verification execution details",
                       "Advanced details · operation and audit evidence"])
        with self.read_only(app):
            self.navigate(app, "Verify & Test")
            assert_closed(["Advanced details · environment"])

    def clean(self, app):
        self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])

    def assert_workflow(self, app, done, current=None):
        labels = ["Impact", "Repair", "Verify", "Recover"]
        self.assertEqual(len(done), 4)
        expected = '<ol class="fo-workflow" aria-label="Recorded workflow progress">'
        for index, label in enumerate(labels):
            active = index == current and not done[index]
            style = "done" if done[index] else "current" if active else ""
            expected += '<li class="' + style + '"' + (' aria-current="step"' if active else '') + '><span>'
            expected += ("✓" if done[index] else str(index + 1)) + '</span>' + label + '</li>'
        self.assertIn(expected + '</ol>', self.html(app))

    def test_scenario_highlight_and_readable_inspector(self):
        app = self.app()
        for scenario_id in CASES:
            self.switch(app, scenario_id)
            self.assertIn('class="fo-scenario-label">ACTIVE SCENARIO', self.html(app))
            self.assertIn('class="fo-snapshot-note"', self.html(app))
            self.assertIn("Current revision 0", self.html(app))
            with self.read_only(app):
                source = "sources." + CASES[scenario_id]["source"]
                self.assertEqual(app.session_state.source_id, CASES[scenario_id]["source"])
                self.assertEqual(app.selectbox(key="entity_id").value, source)
                self.assertEqual(self.graph_payload(app)["selected"], source)
                self.assertFalse(any(node["affected"] for node in self.graph_payload(app)["nodes"]))
                self.clean(app)
            self.assertIn('class="fo-identifiers"', self.html(app))
            self.assertTrue(any(item.label == "Inspection help · read only" for item in app.expander))
            self.assertEqual(app.session_state.engine.backend.read().operations, {})

    def test_graph_impact_requires_current_bound_preview_independent_of_inspection(self):
        for scenario_id, case in CASES.items():
            with self.subTest(scenario=scenario_id):
                app = self.app()
                self.switch(app, scenario_id)
                engine = app.session_state.engine
                before = engine.backend.read()
                expected_affected = {"sources." + case["source"],
                                     "facts." + case["preserved"], "facts." + case["invalidated"]}
                expected_affected.update("summaries." + key for key in case["affected"])
                with self.read_only(app), patch("fallout.workbench.render_graph", wraps=render_graph) as render:
                    def assert_impact(source, affected):
                        self.clean(app)
                        self.assertEqual(render.call_args.args[2], source)
                        graph = self.graph_payload(app)
                        self.assertEqual({node["entity"] for node in graph["nodes"] if node["affected"]}, affected)
                        self.assertEqual(graph["selected"], app.selectbox(key="entity_id").value)
                        self.assert_graph(app, before.state)
                        return graph["context"]

                    self.navigate(app, "Investigate & Repair")
                    self.assertEqual(app.radio(key="graph_view").options,
                                     ["Current state", "Before last operation", "Original dataset"])
                    self.assertEqual(app.radio(key="graph_view").value, "Current state")
                    idle_context = assert_impact("", set())
                    # Every source can be inspected without implicitly previewing it.
                    for inspected in sorted(before.state.sources):
                        app.selectbox(key="entity_id").select("sources." + inspected).run()
                        self.assertEqual(assert_impact("", set()), idle_context)
                        self.assertEqual(app.session_state.source_id, case["source"])
                        self.assertNotIn("impact_preview", app.session_state)
                    self.assertNotIn("activity_history", app.session_state)

                    preview = self.preview(app, case["source"])
                    preview_context = assert_impact(case["source"], expected_affected)
                    self.assertNotEqual(preview_context, idle_context)
                    activity = deepcopy(app.session_state.activity_history)
                    # In particular, inspecting a different source must not retarget amber.
                    for inspected in sorted(before.state.sources):
                        app.selectbox(key="entity_id").select("sources." + inspected).run()
                        self.assertEqual(render.call_args.args[1], "sources." + inspected)
                        self.assertEqual(assert_impact(case["source"], expected_affected), preview_context)
                        self.assertEqual(app.session_state.source_id, case["source"])
                        self.assertEqual(app.session_state.impact_preview, preview)
                        self.assertFalse(app.button(key="confirm").disabled)

                    for view in ("Before last operation", "Original dataset"):
                        app.radio(key="graph_view").set_value(view).run()
                        assert_impact("", set())
                        self.assertEqual(app.session_state.impact_preview, preview)
                    app.radio(key="graph_view").set_value("Current state").run()
                    self.assertEqual(assert_impact(case["source"], expected_affected), preview_context)

                    # Break each binding independently; never approve these test fixtures.
                    for field, value in (("revision", preview["revision"] + 1),
                                         ("generation", "stale-generation"),
                                         ("dataset_id", "other-dataset"),
                                         ("source_id", case["next_source"])):
                        with self.subTest(binding=field):
                            app.session_state.impact_preview = {**deepcopy(preview), field: value}
                            app.run()
                            assert_impact("", set())
                            self.assertTrue(app.button(key="confirm").disabled)
                            app.session_state.impact_preview = deepcopy(preview)
                            app.run()
                            self.assertEqual(assert_impact(case["source"], expected_affected), preview_context)
                            self.assertFalse(app.button(key="confirm").disabled)

                    # Changing the withdrawal control, unlike inspection, invalidates the preview.
                    app.selectbox(key="source_choice").select(case["next_source"]).run()
                    assert_impact("", set())
                    self.assertEqual(app.session_state.impact_preview, preview)
                    self.assertTrue(app.button(key="confirm").disabled)
                    self.assertEqual(app.session_state.activity_history, activity)
                    self.assertNotIn("operation_id", app.session_state)
                    self.assertNotIn("verification", app.session_state)

    def click(self, app, key):
        self.assertFalse(app.button(key=key).disabled, key)
        app.button(key=key).click().run()
        self.clean(app)

    def switch(self, app, scenario_id):
        with self.read_only(app):
            app.selectbox(key="scenario_choice").select(scenario_id).run()
            self.clean(app)
        self.assertEqual(app.session_state.active_scenario, scenario_id)
        self.assertEqual(app.selectbox(key="scenario_choice").value, scenario_id)
        self.assertEqual(app.session_state.engine.dataset.canonical,
                         get_scenario(scenario_id).load().canonical)

    def preview(self, app, source):
        with self.read_only(app):
            self.navigate(app, "Investigate & Repair")
            app.selectbox(key="source_choice").select(source).run()
            self.clean(app)
            self.click(app, "preview")
        self.assertEqual(app.session_state.impact_preview["source_id"], source)
        self.assertFalse(app.button(key="confirm").disabled)
        self.assertIn("Preview only — nothing has changed. These are expected outcomes, not repair results.",
                      [item.value for item in app.info])
        return deepcopy(app.session_state.impact_preview)

    def prepare(self, scenario_id, fault="none"):
        app = self.app()
        self.switch(app, scenario_id)
        before = app.session_state.engine.backend.read()
        with self.read_only(app):
            self.navigate(app, "Verify & Test")
            app.selectbox(key="fault_choice").select(fault).run()
            self.clean(app)
        case = CASES[scenario_id]
        preview = self.preview(app, case["source"])
        self.assert_workflow(app, [True, False, False, False], current=1)
        self.assertEqual(preview["preserved_facts"], [case["preserved"]])
        self.assertEqual(preview["invalidated_facts"], [case["invalidated"]])
        self.assertEqual(preview["affected_facts"], sorted([case["preserved"], case["invalidated"]]))
        self.assertEqual(preview["affected_summaries"], case["affected"])
        self.assertEqual(preview["remaining_support"], {
            case["preserved"]: [case["support"]], case["invalidated"]: [],
        })
        self.assertEqual(app.session_state.engine.backend.read(), before)
        self.assertNotIn("operation_id", app.session_state)
        self.assertNotIn("verification", app.session_state)
        self.assert_graph(app, before.state)
        return app, before, preview

    def confirm(self, app):
        engine = app.session_state.engine
        with ExitStack() as stack:
            spies = {name: stack.enter_context(patch.object(engine, name, wraps=getattr(engine, name)))
                     for name in ("confirm_preview", "revoke_source", "repair_memory", "verify_rebuild")}
            self.click(app, "confirm")
            for spy in spies.values():
                self.assertEqual(spy.call_count, 1)
        self.assertNotIn("impact_preview", app.session_state)
        self.assertEqual(len(engine.backend.read().operations), 1)
        return engine.get_operation_report(app.session_state.operation_id)

    def assert_graph(self, app, state, mismatched=()):
        """Check the serialized component, not the projection against itself."""
        graph = self.graph_payload(app)
        expected = {group + "." + key: row for group in ("sources", "facts", "summaries")
                    for key, row in getattr(state, group).items()}
        self.assertEqual(Counter(node["entity"] for node in graph["nodes"]), Counter(expected.keys()))
        self.assertEqual(set(app.selectbox(key="entity_id").options), set(expected))
        self.assertEqual(graph["selected"], app.selectbox(key="entity_id").value)
        self.assertTrue(graph["context"])
        for node in graph["nodes"]:
            self.assertEqual(node["status"], expected[node["entity"]]["status"])
            self.assertEqual("MISMATCH" in node["note"], node["entity"] in mismatched)
            self.assertEqual(node["selected"], node["entity"] == graph["selected"])
            if node["entity"].startswith("facts."):
                self.assertEqual(node["recorded_active_support"], state.facts[node["id"]]["active_support"])
                support = sorted({a for a, b in state.support_edges if b == node["id"]})
                self.assertEqual(node["support_sources"], support)
                self.assertEqual(node["active_edge_sources"],
                                 [a for a in support if state.sources[a]["status"] == "active"])
        edges = []
        for source, fact in state.support_edges:
            edges.append(("sources." + source, "facts." + fact, "SUPPORTED_BY",
                          state.sources[source]["status"] == "active"))
        for fact, summary in state.composition_edges:
            edges.append(("facts." + fact, "summaries." + summary, "COMPOSED_OF",
                          state.facts[fact]["status"] == "valid" and fact in state.summaries[summary]["members"]))
        self.assertEqual(Counter((edge["source_entity"], edge["target_entity"], edge["relation"], edge["active"])
                                 for edge in graph["edges"]), Counter(edges))

    def expected_state(self, before, case, skipped=None, recovered=False):
        """Independent literal domain expectations; include every unchanged field."""
        expected = deepcopy(before)
        expected.sources[case["source"]]["status"] = "revoked"
        expected.facts[case["preserved"]]["active_support"] = [case["support"]]
        expected.facts[case["invalidated"]].update(status="invalid", active_support=[])
        for key in case["affected"]:
            row = expected.summaries[key]
            if key == skipped and not recovered:
                row["status"] = "stale"
            else:
                row.update(status="fresh", content=case["content"][key],
                           members=[fact for fact in row["members"] if fact != case["invalidated"]],
                           build_count=row["build_count"] + (2 if recovered and key != skipped else 1))
        return expected

    def assert_report(self, report, dataset, snapshot, status):
        self.assertEqual(report["status"], status)
        self.assertTrue(report["complete"])
        self.assertTrue(report["comparisons"])
        self.assertIsNone(verifier.report_problem(report, dataset, snapshot))
        self.assertEqual(Counter(row["path"] for row in report["comparisons"]),
                         Counter(report["required_paths"]))
        self.assertEqual((report["dataset_id"], report["generation"], report["revision"]),
                         (snapshot.dataset_id, snapshot.generation, snapshot.revision))
        self.assertIn({"path": "operations.incomplete", "expected": [], "actual": [], "match": True},
                      report["comparisons"])
        if status == "PASS":
            self.assertEqual(report["mismatches"], [])

    def assert_false_evidence(self, report, before, case):
        key = case["skipped"]
        correct = self.expected_state(before, case).summaries[key]
        actual = {**before.summaries[key], "status": "stale"}
        self.assertNotEqual(correct["content"], actual["content"])
        self.assertEqual(report["mismatches"], [
            {"path": "summaries." + key + "." + field, "expected": correct[field], "actual": actual[field]}
            for field in ("content", "members", "status")
        ])

    def test_required_paragraph_and_selector_load_real_datasets_without_mutation(self):
        app = self.app()
        self.assertEqual(app.selectbox(key="scenario_choice").label, "Scenario")
        self.assertEqual(app.selectbox(key="scenario_choice").options,
                         ["Pricing withdrawal", "Technical documentation withdrawal"])
        engines = []
        for scenario_id, case in CASES.items():
            self.switch(app, scenario_id)
            with self.read_only(app):
                self.navigate(app, "Overview")
                self.assertIn("<p>" + INTRO + "</p>", self.html(app))
                self.assertIn("consistency with original evidence and withdrawal history—not proof of real-world truth",
                              "\n".join(item.value for item in app.caption))
                engine = app.session_state.engine
                engines.append(engine)
                snapshot = engine.backend.read()
                self.assertEqual(snapshot.state, initial_state(get_scenario(scenario_id).load()))
                self.assertEqual(snapshot.revision, 0)
                self.assertEqual(snapshot.operations, {})
                self.assertEqual(snapshot.events, [])
                self.assertEqual(tuple(len(getattr(snapshot.state, group)) for group in
                                       ("sources", "facts", "summaries")), case["counts"])
                self.assert_graph(app, snapshot.state)
                self.assertIn("VERIFIER / NOT RUN", self.html(app))
                self.assert_workflow(app, [False] * 4, current=0)
                self.click(app, "try_demo")
                self.assertEqual(app.selectbox(key="source_choice").value, case["source"])
                about = next(item for item in app.expander if item.label == "About this source")
                self.assertFalse(about.proto.expanded)
                self.assertEqual([item.value for item in about.caption], [get_scenario(scenario_id).description])
                self.assertEqual([item.value for item in about.text],
                                 [snapshot.state.sources[case["source"]]["text"]])
        self.assertIsNot(engines[0], engines[1])
        self.assertNotEqual(engines[0].dataset.fingerprint, engines[1].dataset.fingerprint)

    def test_complete_normal_workflow_both_scenarios(self):
        for scenario_id, case in CASES.items():
            with self.subTest(scenario=scenario_id):
                app, before, preview = self.prepare(scenario_id)
                operation = self.confirm(app)
                engine = app.session_state.engine
                after = engine.backend.read()
                self.assertEqual(operation["preview"], preview)
                self.assertEqual(operation["status"], "repaired")
                self.assertEqual(operation["attempts"], 1)
                self.assertFalse(operation.get("false_success_pending", False))
                self.assertTrue(all(step["status"] == "COMPLETED" for step in operation["steps"].values()))
                self.assertEqual(after.state, self.expected_state(before.state, case))
                self.assertEqual(after.revision, 3)
                self.assertEqual(after.generation, before.generation)
                self.assertEqual(app.session_state.before_snapshot, before)
                self.assert_report(app.session_state.verification, engine.dataset, after, "PASS")
                self.assert_workflow(app, [True, True, True, False])
                self.assertIn("PASS — The saved memory matches the independently rebuilt result.",
                              [item.value for item in app.success])
                self.assert_graph(app, after.state)
                for key, content in case["content"].items():
                    self.assertEqual(after.state.summaries[key]["content"], content)
                for key in case["unrelated"]:
                    evidence = next(json.loads(item.value) for item in app.json
                                    if json.loads(item.value).get("summary_id") == key)
                    self.assertEqual(evidence, {"summary_id": key, "before": before.state.summaries[key],
                                               "after": before.state.summaries[key], "unchanged": True})
                with self.read_only(app):
                    self.navigate(app, "Verify & Test")
                    self.assertNotIn("retry", [button.key for button in app.button])
                    self.click(app, "verify")
                self.assertEqual([row["status"] for row in app.session_state.verification_history], ["PASS", "PASS"])

    def test_false_success_exact_fail_and_same_operation_retry_both_scenarios(self):
        for scenario_id, case in CASES.items():
            with self.subTest(scenario=scenario_id):
                app, before, preview = self.prepare(scenario_id, "false_success")
                operation = self.confirm(app)
                engine = app.session_state.engine
                failed = engine.backend.read()
                report = deepcopy(app.session_state.verification)
                check_id = app.session_state.verification_id
                self.assertEqual(operation["preview"], preview)
                self.assertEqual(operation["status"], "repaired")
                self.assertTrue(operation["false_success_pending"])
                self.assertEqual(operation["skipped_summary"], case["skipped"])
                rebuilt = [key for key in case["affected"] if key != case["skipped"]]
                step = operation["steps"]["rebuild_summaries"]
                self.assertEqual(step["status"], "PARTIAL" if rebuilt else "SKIPPED")
                self.assertEqual(step["rebuilt_summaries"], rebuilt)
                self.assertEqual(failed.state, self.expected_state(before.state, case, case["skipped"]))
                self.assert_report(report, engine.dataset, failed, "FAIL")
                self.assert_false_evidence(report, before.state, case)
                self.assert_no_pass(app)
                self.assertIn('<div class="fo-stage neutral" role="status"><b>REPAIR EXECUTED · reported complete</b>',
                              self.html(app))
                self.assertIn('<div class="fo-stage fail" role="status"><b>FAILURE INJECTED · repair skipped a rebuild</b>',
                              self.html(app))
                self.assertIn("VERIFIER / FAIL", self.html(app))
                self.assert_workflow(app, [True, True, True, False], current=3)
                self.assertTrue(any(case["skipped"] in item.value and "unverified, not proof of repair" in item.value
                                    for item in app.warning))
                self.assert_graph(app, failed.state, {"summaries." + case["skipped"]})
                with self.read_only(app):
                    self.navigate(app, "Verify & Test")
                    app.selectbox(key="comparison_path").select("summaries." + case["skipped"] + ".content").run()
                    self.clean(app)
                    self.assertEqual(json.loads(app.json[0].value), {"value": case["content"][case["skipped"]]})
                    self.assertEqual(json.loads(app.json[1].value),
                                     {"value": before.state.summaries[case["skipped"]]["content"]})
                    rendered = [json.loads(item.value) for item in app.json]
                    for mismatch in report["mismatches"]:
                        self.assertIn(mismatch, rendered)
                    self.assertTrue(any("FAIL — The saved memory does not match" in item.value for item in app.error))
                with patch.object(engine, "revoke_source", wraps=engine.revoke_source) as revoke, \
                        patch.object(engine, "repair_memory", wraps=engine.repair_memory) as repair, \
                        patch.object(engine, "verify_rebuild", wraps=engine.verify_rebuild) as verify:
                    self.click(app, "retry")
                    revoke.assert_not_called()
                    repair.assert_called_once_with(operation["id"])
                    verify.assert_called_once_with()
                after = engine.backend.read()
                recovered = after.operations[operation["id"]]
                self.assertEqual(list(after.operations), [operation["id"]])
                self.assertEqual(app.session_state.operation_id, operation["id"])
                self.assertEqual(recovered["attempts"], 2)
                self.assertEqual(recovered["status"], "repaired")
                self.assertEqual(recovered["fault"], "none")
                self.assertFalse(recovered["false_success_pending"])
                self.assertIsNone(recovered["skipped_summary"])
                self.assertTrue(all(step["status"] == "COMPLETED" for step in recovered["steps"].values()))
                self.assertEqual(after.state, self.expected_state(before.state, case, case["skipped"], recovered=True))
                self.assertEqual(after.revision, failed.revision + 2)
                self.assertEqual(after.events[:len(failed.events)], failed.events)
                self.assert_report(app.session_state.verification, engine.dataset, after, "PASS")
                self.assertNotEqual(app.session_state.verification_id, check_id)
                self.assertEqual([(row["id"], row["status"], row["operation_ids"])
                                  for row in app.session_state.verification_history],
                                 [(check_id, "FAIL", [operation["id"]]),
                                  (app.session_state.verification_id, "PASS", [operation["id"]])])
                self.assertEqual(failed.operations[operation["id"]], operation)
                self.assert_false_evidence(report, before.state, case)
                self.assertNotIn("retry", [button.key for button in app.button])
                with self.read_only(app):
                    self.navigate(app, "Investigate & Repair")
                    self.assert_graph(app, after.state)
                    self.assertIn("VERIFIER / PASS", self.html(app))
                    self.assertNotIn("FAILURE INJECTED", self.html(app))
                    self.assertIn("RECOVERY · missed work repaired", self.html(app))
                    self.assert_workflow(app, [True] * 4)
                    self.assertIn("EARLIER VERIFICATION · FAIL", self.html(app))

    def save_bank(self, app):
        keys = ("source_id", "fault", "section", "operation_id", "before_snapshot", "impact_preview",
                "verification", "verification_id", "verification_history", "activity_history", "lab_history",
                "lab_run", "graph_view", "entity_id", "evidence_query", "mismatches_only", "comparison_path",
                "reset_preview")
        return {key: deepcopy(app.session_state[key]) for key in keys}

    def test_switching_preserves_banks_and_reset_affects_only_selected_scenario(self):
        app = self.app()
        banks, engines, snapshots, grants = {}, {}, {}, {}
        for scenario_id, case in CASES.items():
            self.switch(app, scenario_id)
            engine = app.session_state.engine
            engines[scenario_id] = engine
            self.assertEqual(engine.backend.read().revision, 0)
            for key in ("verification", "operation_id", "impact_preview", "lab_history"):
                self.assertNotIn(key, app.session_state)
            self.preview(app, case["source"])
            self.confirm(app)
            self.navigate(app, "Verify & Test")
            self.click(app, "false_success_lab")
            app.text_input(key="evidence_query").set_value("summaries." + case["skipped"] + ".content").run()
            app.selectbox(key="fault_choice").select("false_success").run()
            self.preview(app, case["next_source"])
            app.radio(key="graph_view").set_value("Before last operation").run()
            app.selectbox(key="entity_id").select("sources." + case["source"]).run()
            with self.read_only(app):
                self.click(app, "preview_reset")
                app.checkbox(key="reset_ack").check().run()
                self.clean(app)
            banks[scenario_id] = self.save_bank(app)
            snapshots[scenario_id] = engine.backend.read()
            grants[scenario_id] = deepcopy(engine._grants)
        self.assertIsNot(engines["pricing"], engines["documentation"])
        for key in ("operation_id", "verification_id"):
            self.assertNotEqual(banks["pricing"][key], banks["documentation"][key])
        self.assertNotEqual(banks["pricing"]["impact_preview"]["preview_id"],
                            banks["documentation"]["impact_preview"]["preview_id"])
        for scenario_id in ("pricing", "documentation", "pricing"):
            self.switch(app, scenario_id)
            self.assertIs(app.session_state.engine, engines[scenario_id])
            self.assertEqual(self.save_bank(app), banks[scenario_id])
            self.assertFalse(app.checkbox(key="reset_ack").value)
            self.assertTrue(app.button(key="confirm_reset").disabled)
            self.assertFalse(app.button(key="confirm").disabled)
            self.assert_graph(app, banks[scenario_id]["before_snapshot"].state)
            for key, engine in engines.items():
                self.assertEqual(engine.backend.read(), snapshots[key])
                self.assertEqual(engine._grants, grants[key])
        # Reset each selected bank in turn; the sibling's complete snapshot and
        # grant store must remain byte-for-byte equivalent, even if already reset.
        for scenario_id in CASES:
            self.switch(app, scenario_id)
            sibling = next(key for key in CASES if key != scenario_id)
            other_before = engines[sibling].backend.read()
            other_grants = deepcopy(engines[sibling]._grants)
            engine = engines[scenario_id]
            before = engine.backend.read()
            with self.read_only(app):
                self.click(app, "preview_reset")
                app.checkbox(key="reset_ack").check().run()
            self.click(app, "confirm_reset")
            after = engine.backend.read()
            self.assertEqual(after.state, initial_state(engine.dataset))
            self.assertEqual(after.revision, 0)
            self.assertNotEqual(after.generation, before.generation)
            self.assertEqual(after.operations, {})
            self.assertEqual(after.events, [])
            self.assertEqual(engine._grants, {})
            self.assertEqual(app.session_state.lab_history, banks[scenario_id]["lab_history"])
            for key in ("operation_id", "before_snapshot", "impact_preview", "verification", "verification_id",
                        "verification_history", "activity_history", "reset_preview"):
                self.assertNotIn(key, app.session_state)
            self.assertEqual(app.session_state.source_id, CASES[scenario_id]["source"])
            self.assertEqual(app.session_state.fault, "none")
            self.assert_graph(app, after.state)
            self.assertIn("VERIFIER / NOT RUN", self.html(app))
            self.assertEqual(engines[sibling].backend.read(), other_before)
            self.assertEqual(engines[sibling]._grants, other_grants)
            self.switch(app, sibling)
            if sibling == "documentation":
                self.assertEqual(self.save_bank(app), banks[sibling])
            self.assertEqual(app.session_state.lab_history, banks[sibling]["lab_history"])
            self.assertEqual(app.session_state.engine.backend.read(), other_before)

    def test_documentation_ai_is_blocked_before_provider_creation(self):
        settings = AISettings(True, "gemini", "gemini-offline-fixture", "offline_fixture_not_a_real_key", True)
        with patch("fallout.ai_ui.AISettings.from_environment", return_value=settings):
            app = self.app()
            self.navigate(app, "Investigate & Repair")
            with self.read_only(app):
                app.checkbox(key="ai_consent").check().run()
                self.clean(app)
                self.assertFalse(app.button(key="ai_request").disabled)
            self.switch(app, "documentation")
            with self.read_only(app):
                for section in ("Investigate & Repair", "Verify & Test"):
                    self.navigate(app, section)
                    self.assertIn("External AI is restricted to the original reviewed synthetic demo. Customized datasets stay local.",
                                  [item.value for item in app.warning])
                    self.assertNotIn("ai_request", [item.key for item in app.button])
                    self.assertNotIn("ai_review", [item.key for item in app.button])
                    self.assertNotIn("ai_task", [item.key for item in app.selectbox])
                    self.assertNotIn("ai_result", app.session_state)
                    self.assertFalse(app.session_state.ai_consent)
            self.preview(app, CASES["documentation"]["source"])
            self.confirm(app)
            self.assert_report(app.session_state.verification, app.session_state.engine.dataset,
                               app.session_state.engine.backend.read(), "PASS")
            self.switch(app, "pricing")
            self.assertFalse(app.checkbox(key="ai_consent").value)
            self.assertTrue(app.button(key="ai_request").disabled)
        # setUp guards assert that neither provider creation nor transport ran.

    def test_lab_false_success_proof_retains_initial_fail_and_recovery_pass_both_datasets(self):
        for scenario_id, case in CASES.items():
            with self.subTest(scenario=scenario_id):
                app, _, _ = self.prepare(scenario_id)
                self.confirm(app)
                self.preview(app, case["next_source"])
                self.navigate(app, "Verify & Test")
                engine = app.session_state.engine
                saved = {key: deepcopy(app.session_state[key]) for key in (
                    "impact_preview", "operation_id", "before_snapshot", "verification", "verification_id",
                    "verification_history", "activity_history")}
                with self.read_only(app), patch("fallout.workbench.run_lab", wraps=run_lab) as execute:
                    app.run()
                    execute.assert_not_called()
                    self.click(app, "false_success_lab")
                    execute.assert_called_once_with(scenario_id=scenario_id, fault="false_success")
                    first = deepcopy(app.session_state.lab_history[0])
                    self.assertEqual(first["scenario_id"], scenario_id)
                    normal, fault = first["scenarios"]
                    self.assertEqual([normal["verdict"], fault["verdict"]], ["PASS", "FAIL"])
                    self.assertIsNone(normal["recovery"])
                    recovery = fault["recovery"]
                    self.assertEqual(recovery["verdict"], "PASS")
                    self.assertEqual(recovery["operation_id"], fault["operation"]["id"])
                    self.assertEqual(recovery["before"], fault["after"])
                    self.assertEqual(fault["operation"]["status"], "repaired")
                    self.assertTrue(fault["operation"]["false_success_pending"])
                    self.assertFalse(recovery["operation"]["false_success_pending"])
                    self.assertEqual(recovery["operation"]["attempts"], 2)
                    self.assertEqual(recovery["after"]["revision"], fault["after"]["revision"] + 2)
                    for result, status in ((normal, "PASS"), (fault, "FAIL"), (recovery, "PASS")):
                        self.assert_report(result["verification"], engine.dataset, Snapshot(**result["after"]), status)
                    before = MemoryState(**fault["before"]["state"])
                    self.assertEqual(before, initial_state(engine.dataset))
                    self.assertEqual(normal["after"]["state"], asdict(self.expected_state(before, case)))
                    self.assertEqual(fault["after"]["state"], asdict(self.expected_state(before, case, case["skipped"])))
                    self.assertEqual(recovery["after"]["state"],
                                     asdict(self.expected_state(before, case, case["skipped"], recovered=True)))
                    self.assert_false_evidence(fault["verification"], before, case)
                    ids = [first["id"], normal["id"], fault["id"], recovery["id"], normal["verification_id"],
                           fault["verification_id"], recovery["verification_id"], normal["operation"]["id"],
                           fault["operation"]["id"], normal["before"]["generation"], fault["before"]["generation"]]
                    self.assertEqual(len(ids), len(set(ids)))
                    for value in ids:
                        self.assertEqual(UUID(hex=value).version, 4)
                    self.assertNotIn(engine.backend.read().generation, ids)
                    self.assertEqual(recovery["after"]["generation"], fault["after"]["generation"])
                    markdown = "\n".join(item.value for item in app.markdown)
                    for text in ("Normal repair · PASS", "Reported completion with a skipped rebuild · FAIL",
                                 "Recovery · PASS", "Stale-content diagnosis"):
                        self.assertIn(text, markdown)
                    rendered = [json.loads(item.value) for item in app.json]
                    self.assertIn(json.loads(json.dumps(fault)), rendered)
                    self.assertIn("Same operation ID: " + fault["operation"]["id"], [item.value for item in app.caption])
                    self.click(app, "false_success_lab")
                    self.assertEqual(execute.call_count, 2)
                    self.assertEqual(len(app.session_state.lab_history), 2)
                    self.assertEqual(app.session_state.lab_history[0], first)
                    self.assertNotEqual(app.session_state.lab_history[1]["id"], first["id"])
                    app.selectbox(key="lab_run").select(0).run()
                    app.run()
                    self.clean(app)
                    self.assertEqual(execute.call_count, 2)
                    for key, value in saved.items():
                        self.assertEqual(app.session_state[key], value, key)
                    self.assertIn("VERIFIER / PASS", self.html(app))
                self.navigate(app, "Investigate & Repair")
                self.assertFalse(app.button(key="confirm").disabled)
                self.assertEqual(app.session_state.impact_preview, saved["impact_preview"])
                self.assert_graph(app, engine.backend.read().state)

    def test_empty_actual_graph_has_safe_overview_fallback_and_real_fail(self):
        for scenario_id in CASES:
            with self.subTest(scenario=scenario_id):
                app = self.app()
                self.switch(app, scenario_id)
                engine = app.session_state.engine
                snapshot = engine.backend.read()
                # Deliberate test-only corruption; do not replace the canonical dataset.
                snapshot.state = MemoryState()
                engine.backend.commit(snapshot, snapshot.revision)
                with self.read_only(app):
                    app.run()
                    self.clean(app)
                    self.assertIn("No entities exist in this snapshot. Run verification to inspect missing nodes.",
                                  [item.value for item in app.warning])
                    self.assertEqual(len(app.get("component_instance")), 0)
                    self.assertNotIn("entity_id", [item.key for item in app.selectbox])
                    self.assert_no_pass(app)
                    self.navigate(app, "Verify & Test")
                    self.click(app, "verify")
                    self.assert_report(app.session_state.verification, engine.dataset, engine.backend.read(), "FAIL")
                    paths = {row["path"] for row in app.session_state.verification["mismatches"]}
                    self.assertTrue({"sources.ids", "facts.ids", "summaries.ids"} <= paths)
                    self.assertIn("VERIFIER / FAIL", self.html(app))
                    self.assert_no_pass(app)


if __name__ == "__main__":
    unittest.main()