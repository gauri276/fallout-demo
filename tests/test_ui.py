"""Headless Streamlit interaction tests; skipped if optional UI dependency is absent."""

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from html import unescape
import importlib.util
import json
import os
import re
import unittest
from unittest.mock import patch

from fallout import verifier
from fallout.ai_provider import AISettings
from fallout.lab import run_lab
from fallout.models import ROOT, Snapshot
from fallout.presentation import verification_state
from fallout.runtime import local_runtime


HAS_STREAMLIT = importlib.util.find_spec("streamlit") is not None
if HAS_STREAMLIT:
    with patch.dict(os.environ, {}, clear=True):
        local_runtime()
        from streamlit.testing.v1 import AppTest


@unittest.skipUnless(HAS_STREAMLIT, "Streamlit is not installed; core tests remain usable without it.")
class UITests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"FALLOUT_AI_ENABLED": "false"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        local_runtime()
        for target in ("fallout.ai_ui.create_provider", "fallout.ai_provider.GeminiProvider.complete",
                       "fallout.ai_provider.build_opener", "socket.create_connection", "socket.socket.connect"):
            guard = patch(target, side_effect=AssertionError("Network/AI requests forbidden in UI tests"))
            mocked = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mocked.assert_not_called)

    @contextmanager
    def read_only(self, app):
        engine = app.session_state.engine
        before, grants = engine.backend.read(), deepcopy(engine._grants)
        with ExitStack() as stack:
            spies = [stack.enter_context(patch.object(engine, name, wraps=getattr(engine, name)))
                     for name in ("confirm_preview", "revoke_source", "repair_memory", "reset_demo")]
            spies.append(stack.enter_context(patch.object(engine.backend, "commit", wraps=engine.backend.commit)))
            try:
                yield
            finally:
                for spy in spies:
                    spy.assert_not_called()
                self.assertEqual(engine.backend.read(), before)
                self.assertEqual(engine._grants, grants)

    def html(self, app):
        return "\n".join(item.proto.body for item in app.get("html"))

    def memory_lines(self, app):
        """Read exact labelled text, grouped by rendered before/current panel."""
        return [[(style, label, unescape(text)) for style, label, text in re.findall(
            r'<div class="fo-memory-line ([^"]+)"><span class="fo-diff-label">([^<]+)</span>'
            r'<span class="fo-diff-text">(.*?)</span></div>', item.proto.body, re.DOTALL)]
            for item in app.get("html") if item.proto.body.startswith('<div class="fo-memory-lines">')]

    def app(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        self.assertEqual(len(app.exception), 0)
        return app

    def navigate(self, app, section):
        app.radio(key="section").set_value(section).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.radio(key="section").value, section)
        return app

    def choose_fault(self, app, fault):
        self.navigate(app, "Verify & Test")
        app.selectbox(key="fault_choice").select(fault).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.fault, fault)
        return self.navigate(app, "Investigate & Repair")

    def graph_payload(self, app):
        components = app.get("component_instance")
        self.assertEqual(len(components), 1)
        return json.loads(components[0].proto.json_args)["payload"]

    def graph_nodes(self, app):
        return self.graph_payload(app)["nodes"]

    def assert_no_pass(self, app):
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.success), 0)
        self.assertFalse(any("VERIFIER / PASS" in item.proto.body for item in app.get("html")))

    def preview_sales(self, app):
        self.navigate(app, "Investigate & Repair")
        before = app.session_state.engine.backend.read()
        app.selectbox(key="source_choice").select("sales").run()
        self.assertEqual(app.session_state.source_id, "sales")
        app.button(key="preview").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.engine.backend.read(), before)
        self.assertEqual(app.session_state.impact_preview["source_id"], "sales")
        self.assertFalse(app.button(key="confirm").disabled)
        return app

    def test_initial_screen_and_verification(self):
        app = self.app()
        captions = "\n".join(item.value for item in app.caption)
        self.assertIn("In-memory only", self.html(app))
        self.assertEqual(app.session_state.engine.backend.name, "in-memory")
        self.assertIn("Backend: in-memory · Target: " + app.session_state.engine.backend.target, captions)
        self.assertEqual(app.radio(key="section").options, ["Overview", "Repair", "Verify"])
        self.assertEqual(app.radio(key="section").value, "Overview")
        self.assertEqual(len(app.tabs), 0)
        self.assertIn('<b>2</b> sources<small>2 active</small>', self.html(app))
        self.assertIn('<b>2</b> facts<small>2 valid</small>', self.html(app))
        self.assertIn('<b>1</b> summaries<small>1 fresh</small>', self.html(app))
        self.assertEqual(len(self.graph_nodes(app)), 5)
        self.assertIn("VERIFIER / NOT RUN", self.html(app))
        self.assertEqual(len(app.success), 0)
        self.assertEqual([button.key for button in app.button], ["try_demo"])
        self.navigate(app, "Verify & Test")
        self.assertTrue(app.button(key="confirm_reset").disabled)
        before = app.session_state.engine.backend.read()
        app.button(key="verify").click().run()
        self.assertIn("PASS", app.success[0].value)
        report = app.session_state.verification
        self.assertEqual(report["status"], "PASS")
        self.assertGreater(len(report["comparisons"]), 0)
        self.assertEqual({row["path"] for row in report["comparisons"]}, set(report["required_paths"]))
        self.assertEqual(app.session_state.engine.backend.read(), before)
        self.assertEqual(len(app.exception), 0)

    def test_workbench_overview_has_primary_graph_inspector_and_recorded_memory(self):
        app = self.app()
        html = self.html(app)
        self.assertIn("A completed repair can still be wrong.", html)
        self.assertIn("Trace the change. Repair the memory. Check the result independently.", html)
        self.assertIn("<p>When evidence changes, AI memory can retain facts and summaries that are no longer supported. FALLOUT traces the dependencies, repairs affected memory, and independently checks whether the result matches the expected state.</p>", html)
        self.assertIn("VERIFIER / NOT RUN", html)
        self.assertEqual([item.label for item in app.expander], [
            "Source details",
            "Inspection help · read only",
            "What FALLOUT proves · scope and limits",
            "Session activity · analysis, approval and execution", "Advanced details · environment"])
        self.assertEqual([item.key for item in app.radio], ["section", "graph_view"])
        self.assertEqual(app.radio(key="graph_view").options,
                         ["Current state", "Before last operation", "Original dataset"])
        self.assertEqual([item.key for item in app.selectbox], ["scenario_choice", "entity_id"])
        self.assertEqual(len(app.checkbox), 0)
        graph = self.graph_payload(app)
        self.assertEqual(len(graph["nodes"]), 5)
        self.assertEqual(len(graph["edges"]), 5)
        self.assertEqual(app.selectbox(key="entity_id").value, "sources.sales")
        self.assertEqual(graph["selected"], app.selectbox(key="entity_id").value)
        self.assertFalse(any(node["affected"] for node in graph["nodes"]))
        self.assertEqual(len(app.json), 0)
        self.assertIn('<ul class="fo-identifiers"><li>discount_20</li><li>phone_support</li></ul>', html)
        for kind in ("code", "text_input", "chat_input", "chat_message"):
            self.assertEqual(len(app.get(kind)), 0, kind)
        self.assertIn("A 20% discount is available.\nPhone support is available.", [item.value for item in app.text])
        explanation = "\n".join(item.value for item in app.markdown)
        captions = "\n".join(item.value for item in app.caption)
        self.assertIn("Recorded state · not a proposed repair", html)
        self.assertIn("Inspecting: sources.sales · No active preview. Inspection does not change memory.", captions)
        self.assertTrue(any("No operation yet" in item.value for item in app.info))
        self.assertIn("No live AI model is configured", explanation)
        before = app.session_state.engine.backend.read()
        with self.read_only(app):
            app.selectbox(key="entity_id").select("facts.phone_support").run()
            self.assertEqual(self.graph_payload(app)["selected"], "facts.phone_support")
            self.assertIn("Phone support is available.", [item.value for item in app.text])
            app.button(key="try_demo").click().run()
        self.assertEqual(app.radio(key="section").value, "Investigate & Repair")
        self.assertEqual(app.selectbox(key="source_choice").value, "sales")
        about = next(item for item in app.expander if item.label == "About this source")
        self.assertFalse(about.proto.expanded)
        self.assertEqual([item.value for item in about.caption], [
            "Withdraw the sales offer: retain independently supported phone support and remove the unsupported discount."])
        self.assertIn("preview", [button.key for button in app.button])
        self.assertNotIn("confirm", [button.key for button in app.button])
        self.assertEqual(app.session_state.engine.backend.read(), before)
        self.assertEqual(len(app.exception), 0)

    def test_real_confirmation_workflow(self):
        app = self.preview_sales(self.app())
        self.assertEqual(app.session_state.engine.backend.read().state.sources["sales"]["status"], "active")
        app.button(key="confirm").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("PASS", app.success[0].value)
        state = app.session_state.engine.backend.read().state
        self.assertEqual(state.facts["phone_support"]["status"], "valid")
        self.assertEqual(state.facts["discount_20"]["status"], "invalid")
        self.assertEqual(state.summaries["customer_summary"]["members"], ["phone_support"])

    def test_fault_injection_fails_then_reset_rerun_passes(self):
        app = self.preview_sales(self.app())
        self.choose_fault(app, "skip_summary")
        app.button(key="confirm").click().run()
        self.assertIn("FAIL", app.error[0].value)
        self.navigate(app, "Verify & Test")
        self.assertTrue(any("summaries.customer_summary.content" in element.value for element in app.json))
        before_reset = app.session_state.engine.backend.read()
        app.button(key="preview_reset").click().run()
        self.assertTrue(app.button(key="confirm_reset").disabled)
        self.assertEqual(app.session_state.engine.backend.read(), before_reset)
        app.checkbox(key="reset_ack").check().run()
        self.assertFalse(app.button(key="confirm_reset").disabled)
        self.assertEqual(app.session_state.engine.backend.read(), before_reset)
        app.button(key="confirm_reset").click().run()
        self.assertEqual(app.session_state.engine.backend.read().revision, 0)
        app = self.preview_sales(app)
        app.button(key="confirm").click().run()
        self.assertIn("PASS", app.success[0].value)
        self.assertEqual(len(app.exception), 0)

    def test_no_chat_or_implicit_mutation(self):
        app = self.preview_sales(self.app())
        engine = app.session_state.engine
        before = engine.backend.read()
        with patch.object(engine, "confirm_preview", wraps=engine.confirm_preview) as confirm, \
                patch.object(engine, "revoke_source", wraps=engine.revoke_source) as revoke, \
                patch.object(engine, "repair_memory", wraps=engine.repair_memory) as repair:
            for section in ("Overview", "Verify & Test", "Investigate & Repair"):
                self.navigate(app, section)
                self.assertEqual(len(app.chat_input), 0)
                self.assertEqual(len(app.get("chat_message")), 0)
                self.assertEqual(engine.backend.read(), before)
            app.button(key="preview").click().run()
            confirm.assert_not_called()
            revoke.assert_not_called()
            repair.assert_not_called()
        self.assertEqual(engine.backend.read(), before)
        self.assertNotIn("operation_id", app.session_state)
        self.assertNotIn("verification", app.session_state)

    def test_changed_source_disables_confirmation(self):
        app = self.preview_sales(self.app())
        app.selectbox(key="source_choice").select("pricing").run()
        self.assertEqual(app.session_state.source_id, "pricing")
        self.assertTrue(app.button(key="confirm").disabled)
        self.navigate(app, "Overview")
        self.navigate(app, "Investigate & Repair")
        self.assertEqual(app.selectbox(key="source_choice").value, "pricing")
        self.assertEqual(app.session_state.impact_preview["source_id"], "sales")
        self.assertTrue(app.button(key="confirm").disabled)
        self.assertEqual(app.session_state.engine.backend.read().revision, 0)

    def test_partial_failure_retry(self):
        app = self.preview_sales(self.app())
        self.choose_fault(app, "raise_during_repair")
        app.button(key="confirm").click().run()
        self.assertEqual(app.session_state.verification["status"], "FAIL")
        self.assertNotIn("retry", [button.key for button in app.button])
        self.navigate(app, "Verify & Test")
        app.button(key="retry").click().run()
        self.assertEqual(app.session_state.verification["status"], "PASS")
        self.assertEqual(len(app.exception), 0)

    def test_entity_selection_is_read_only(self):
        app = self.navigate(self.app(), "Investigate & Repair")
        app.selectbox(key="entity_id").select("facts.phone_support").run()
        self.assertEqual(app.session_state.entity_id, "facts.phone_support")
        nodes = self.graph_nodes(app)
        self.assertEqual([node["entity"] for node in nodes if node["selected"]], ["facts.phone_support"])
        self.assertEqual(app.session_state.engine.backend.read().revision, 0)
        self.assertTrue(any("ACTIVE SUPPORT" in element.value for element in app.caption))
        self.assertEqual(len(app.exception), 0)

    def test_before_and_current_graph_snapshots(self):
        app = self.preview_sales(self.app())
        app.button(key="confirm").click().run()
        self.assertEqual(app.session_state.before_snapshot.state.sources["sales"]["status"], "active")
        app.radio(key="graph_view").set_value("Before last operation").run()
        app.selectbox(key="entity_id").select("sources.sales").run()
        self.assertEqual(next(node["status"] for node in self.graph_nodes(app) if node["id"] == "sales"), "active")
        self.assertTrue(any(element.value == "SOURCES / ACTIVE" for element in app.caption))
        app.radio(key="graph_view").set_value("Current state").run()
        self.assertEqual(next(node["status"] for node in self.graph_nodes(app) if node["id"] == "sales"), "revoked")
        self.assertTrue(any(element.value == "SOURCES / REVOKED" for element in app.caption))
        app.radio(key="graph_view").set_value("Original dataset").run()
        self.assertTrue(any(element.value == "SOURCES / ACTIVE" for element in app.caption))
        self.assertEqual(len(app.exception), 0)

    def test_evidence_filter_and_mismatch_entity_route(self):
        app = self.preview_sales(self.app())
        self.choose_fault(app, "skip_invalidation")
        app.button(key="confirm").click().run()
        before = app.session_state.engine.backend.read()
        self.navigate(app, "Verify & Test")
        app.checkbox(key="mismatches_only").check().run()
        app.text_input(key="evidence_query").set_value("facts.discount_20").run()
        self.assertEqual(app.selectbox(key="comparison_path").options, ["facts.discount_20.status"])
        self.assertEqual(json.loads(app.json[0].value), {"value": "invalid"})
        self.assertEqual(json.loads(app.json[1].value), {"value": "valid"})
        app.button(key="inspect_comparison").click().run()
        self.assertEqual(app.session_state.pending_entity, "facts.discount_20")
        self.assertEqual(app.radio(key="section").value, "Verify & Test")
        app.text_input(key="evidence_query").set_value("no-matching-record").run()
        self.assertTrue(any("No comparisons match" in element.value for element in app.info))
        self.navigate(app, "Investigate & Repair")
        self.assertEqual(app.selectbox(key="entity_id").value, "facts.discount_20")
        self.assertEqual(app.radio(key="graph_view").value, "Current state")
        self.assertEqual([node["entity"] for node in self.graph_nodes(app) if node["selected"]], ["facts.discount_20"])
        discount = next(node for node in self.graph_nodes(app) if node["id"] == "discount_20")
        self.assertEqual(discount["status"], "valid")
        self.assertIn("MISMATCH", discount["note"])
        self.assertEqual(app.session_state.engine.backend.read(), before)
        self.assertEqual(len(app.exception), 0)

    def test_stale_verification_never_shows_pass(self):
        app = self.navigate(self.app(), "Verify & Test")
        app.button(key="verify").click().run()
        engine = app.session_state.engine
        snapshot = engine.backend.read()
        engine.backend.commit(snapshot, snapshot.revision)
        app.run()
        self.assertEqual(len(app.success), 0)
        self.assertTrue(any("OUT OF DATE" in item.value for item in app.warning))
        self.assertEqual(len(app.exception), 0)

    def test_before_graph_empty_state(self):
        app = self.navigate(self.app(), "Investigate & Repair")
        app.radio(key="graph_view").set_value("Before last operation").run()
        self.assertTrue(any("No confirmed operation yet" in element.value for element in app.info))
        self.assertEqual(len(app.exception), 0)

    def test_legacy_or_empty_pass_is_error_in_rendered_ui(self):
        for legacy in (True, False):
            app = self.navigate(self.app(), "Verify & Test")
            report = app.session_state.engine.verify_rebuild()
            if legacy:
                report.pop("comparisons")
            else:
                report["comparisons"] = []
            app.session_state.verification = report
            app.run()
            self.assertEqual(len(app.success), 0)
            self.assertTrue(any("ERROR" in item.value for item in app.error))
            self.assertIn("VERIFIER / ERROR", self.html(app))
            self.assertEqual(verification_state(report, app.session_state.engine.backend.read(),
                                                app.session_state.engine.dataset)[0], "BLOCKED")
            self.assertEqual(len(app.exception), 0)

    def test_runtime_verification_error_is_error_in_ui(self):
        app = self.navigate(self.app(), "Verify & Test")
        with patch("fallout.verifier.reconstruct", side_effect=RuntimeError("sensitive details")):
            app.button(key="verify").click().run()
        self.assertEqual(app.session_state.verification["status"], "BLOCKED")
        self.assertEqual(len(app.success), 0)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("ERROR" in item.value for item in app.error))
        self.assertIn("VERIFIER / ERROR", self.html(app))
        self.assertNotIn("sensitive details", str(app.session_state.verification))
        self.assertNotIn("sensitive details", "\n".join(item.value for item in app.error))

    def test_graph_serialization_geometry_and_fallback(self):
        app = self.navigate(self.app(), "Investigate & Repair")
        graph = self.graph_payload(app)
        self.assertGreaterEqual(graph["height"], 330)
        self.assertTrue(graph["context"])
        entities = {node["entity"] for node in graph["nodes"]}
        self.assertEqual(entities, set(app.selectbox(key="entity_id").options))
        for node in graph["nodes"]:
            self.assertIsInstance(node["x"], (int, float))
            self.assertGreater(node["y"], 0)
            self.assertLess(node["y"], graph["height"])
        for edge in graph["edges"]:
            self.assertIn(edge["source_entity"], entities)
            self.assertIn(edge["target_entity"], entities)
        self.assertNotIn("graph_fallback", [item.key for item in app.checkbox])
        self.assertNotIn("Relationship records / accessible graph alternative", [item.label for item in app.expander])
        with self.read_only(app), patch("fallout.workbench.render_graph", side_effect=ValueError("internal graph problem")):
            app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("Graph view unavailable" in item.value for item in app.warning))
        fallback = next(item for item in app.expander if item.label == "Relationship records / accessible graph alternative")
        self.assertTrue(fallback.proto.expanded)
        self.assertIn("facts.phone_support", app.selectbox(key="entity_id").options)
        self.assertEqual(len(app.get("component_instance")), 0)
        self.assertNotIn("internal graph problem", "\n".join(item.value for item in app.warning))
        records = next(item.value for item in app.dataframe if "relationship" in item.value.columns)
        self.assertEqual(len(records), 5)
        with self.read_only(app):
            app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.get("component_instance")), 1)
        self.assertNotIn("Relationship records / accessible graph alternative", [item.label for item in app.expander])

    def test_skipped_summary_timeline_and_verification_record(self):
        app = self.preview_sales(self.app())
        self.choose_fault(app, "skip_summary")
        app.button(key="confirm").click().run()
        engine = app.session_state.engine
        operation = engine.get_operation_report(app.session_state.operation_id)
        self.assertEqual(operation["status"], "repair_incomplete")
        self.assertEqual(operation["steps"]["rebuild_summaries"]["status"], "SKIPPED")
        history = app.session_state.verification_history
        self.assertEqual(history[-1]["status"], "FAIL")
        self.assertIn(operation["id"], history[-1]["operation_ids"])
        self.assertTrue(history[-1]["finished_at"])
        timeline = next(item.value for item in app.dataframe if "stage" in item.value.columns)
        stages = timeline.to_dict("records")
        self.assertTrue(any(row["stage"] == "rebuild_summaries" and row["outcome"] == "SKIPPED" for row in stages))
        self.assertTrue(any(row["stage"] == "independent_verification" and row["outcome"].startswith("FAIL / revision ") for row in stages))
        self.navigate(app, "Verify & Test")
        app.button(key="retry").click().run()
        self.assertEqual(app.session_state.verification["status"], "PASS")
        self.assertEqual(len(app.session_state.verification_history), 2)
        self.assertEqual(len(app.exception), 0)

    def test_old_engine_session_is_blocked_without_reset(self):
        app = self.app()
        engine = app.session_state.engine
        before = engine.backend.read()
        engine.instance_contract = 1
        app.run()
        self.assertTrue(any("BLOCKED" in item.value for item in app.error))
        self.assertEqual(before, engine.backend.read())
        self.assertEqual(len(app.exception), 0)

    def test_fault_labels_and_choice_persist_through_navigation(self):
        app = self.app()
        before = app.session_state.engine.backend.read()
        labels = {
            "none": "No deliberate mistake",
            "skip_invalidation": "Leave an unsupported fact usable",
            "skip_summary": "Leave the old summary unchanged",
            "raise_during_repair": "Interrupt the repair",
            "false_success": "Skip one rebuild but report completion",
        }
        for fault, label in labels.items():
            with self.subTest(fault=fault):
                self.navigate(app, "Verify & Test")
                self.assertEqual(app.selectbox(key="fault_choice").options, list(labels.values()))
                app.selectbox(key="fault_choice").select(fault).run()
                self.assertEqual(app.session_state.fault, fault)
                self.navigate(app, "Overview")
                self.assertEqual([item.key for item in app.selectbox], ["scenario_choice", "entity_id"])
                self.assertEqual(len(self.graph_nodes(app)), 5)
                self.navigate(app, "Investigate & Repair")
                self.assertNotIn("fault_choice", [item.key for item in app.selectbox])
                self.assertEqual(app.session_state.fault, fault)
                if fault != "none":
                    self.assertTrue(any(label in item.value for item in app.warning))
                self.navigate(app, "Verify & Test")
                self.assertEqual(app.selectbox(key="fault_choice").value, fault)
                self.assertEqual(app.session_state.engine.backend.read(), before)

    def test_verify_and_retry_controls_only_exist_in_verify_section(self):
        app = self.preview_sales(self.app())
        self.choose_fault(app, "skip_summary")
        app.button(key="confirm").click().run()
        for section in ("Investigate & Repair", "Overview", "Verify & Test"):
            self.navigate(app, section)
            keys = {item.key for item in app.button}
            self.assertEqual("verify" in keys, section == "Verify & Test")
            self.assertEqual("retry" in keys, section == "Verify & Test")
        app.button(key="retry").click().run()
        self.assertEqual(app.session_state.verification["status"], "PASS")
        self.assertNotIn("retry", [item.key for item in app.button])
        self.assertEqual(len(app.exception), 0)

    def test_fault_results_describe_actual_saved_memory(self):
        phone = "Phone support is available."
        discount = "A 20% discount is available."
        original = discount + "\n" + phone
        for fault in ("skip_summary", "skip_invalidation"):
            with self.subTest(fault=fault):
                app = self.preview_sales(self.app())
                self.choose_fault(app, fault)
                app.button(key="confirm").click().run()
                self.assert_no_pass(app)
                self.assertTrue(any(item.value == "What actually happened" for item in app.subheader))
                self.assertTrue(any("repair is incomplete" in item.value for item in app.warning))
                self.assertTrue(any("FAIL — The saved memory does not match" in item.value for item in app.error))
                state = app.session_state.engine.backend.read().state
                self.assertEqual(state.sources["sales"]["status"], "revoked")
                self.assertEqual(state.facts["phone_support"]["active_support"], ["pricing"])
                self.assertEqual(state.facts["discount_20"]["active_support"], [])
                self.assertEqual(state.summaries["customer_summary"]["content"], original)
                # Legacy fact/lineage evidence is intentionally collapsed, not the primary result.
                details = next(item for item in app.expander
                               if item.label == "Repair details · facts, lineage and execution")
                self.assertFalse(details.proto.expanded)
                texts = [item.value for item in details.text]
                markdown = "\n".join(item.value for item in app.markdown)
                self.assertIn("**What stayed**", markdown)
                self.assertIn("**What was removed from usable memory**", markdown)
                self.assertIn("**What was updated**", markdown)
                self.assertNotIn("The saved summary now reads:", markdown)
                if fault == "skip_summary":
                    self.assertEqual(texts, [phone, discount, original, original])
                    self.assertEqual(state.facts["discount_20"]["status"], "invalid")
                    self.assertEqual(state.summaries["customer_summary"]["status"], "stale")
                    self.assertTrue(any("It has not been successfully updated" in item.value for item in app.warning))
                    self.assertIn("Why: the saved summary's content", markdown)
                else:
                    self.assertEqual(texts, [discount, phone, original, original])
                    self.assertEqual(state.facts["discount_20"]["status"], "valid")
                    self.assertEqual(state.summaries["customer_summary"]["status"], "fresh")
                    self.assertIn("No affected facts were marked unusable.", markdown)
                    self.assertIn("The summary's wording is unchanged:", markdown)
                    self.assertTrue(any("none — this needs attention" in item.value for item in app.caption))
                    self.assertIn("Why: a saved fact or its support differs", markdown)

    def test_completed_repair_without_verification_shows_not_run(self):
        app = self.preview_sales(self.app())
        # Simulate absent evidence, without substituting a fabricated verifier verdict.
        with patch("fallout.workbench.run_verification") as verify:
            app.button(key="confirm").click().run()
        verify.assert_called_once_with(app.session_state.engine)
        snapshot = app.session_state.engine.backend.read()
        self.assertEqual(snapshot.operations[app.session_state.operation_id]["status"], "repaired")
        self.assertEqual(snapshot.state.facts["discount_20"]["status"], "invalid")
        self.assertNotIn("verification", app.session_state)
        self.assertNotIn("verification_history", app.session_state)
        for section in ("Investigate & Repair", "Verify & Test"):
            self.navigate(app, section)
            self.assert_no_pass(app)
            self.assertTrue(any("NOT RUN — This version of the memory has not been independently checked" in item.value for item in app.info))
        app.button(key="verify").click().run()
        self.assertEqual(app.session_state.verification["status"], "PASS")
        self.assertTrue(any("PASS" in item.value for item in app.success))
        self.assertEqual(app.session_state.engine.backend.read(), snapshot)

    def test_reset_clears_results_history_and_widget_choices(self):
        app = self.app()
        initial = app.session_state.engine.backend.read()
        self.preview_sales(app)
        self.choose_fault(app, "skip_summary")
        app.button(key="confirm").click().run()
        app.selectbox(key="source_choice").select("pricing").run()
        app.radio(key="graph_view").set_value("Original dataset").run()
        self.navigate(app, "Verify & Test")
        app.checkbox(key="mismatches_only").check().run()
        app.text_input(key="evidence_query").set_value("summaries.customer_summary.content").run()
        app.button(key="inspect_comparison").click().run()
        self.assertIn("pending_entity", app.session_state)
        before = app.session_state.engine.backend.read()
        self.assertTrue(before.operations)
        self.assertTrue(app.session_state.verification_history)
        self.assertTrue(app.button(key="confirm_reset").disabled)
        app.button(key="preview_reset").click().run()
        self.assertTrue(app.button(key="confirm_reset").disabled)
        app.checkbox(key="reset_ack").check().run()
        self.assertFalse(app.button(key="confirm_reset").disabled)
        self.assertEqual(app.session_state.engine.backend.read(), before)
        app.button(key="confirm_reset").click().run()
        after = app.session_state.engine.backend.read()
        self.assertEqual(after.state, initial.state)
        self.assertEqual(after.revision, 0)
        self.assertNotEqual(after.generation, before.generation)
        self.assertEqual(after.operations, {})
        self.assertEqual(after.events, [])
        for key in ("reset_preview", "impact_preview", "operation_id", "verification", "verification_history", "chat", "before_snapshot", "pending_entity"):
            self.assertNotIn(key, app.session_state, key)
        self.assertEqual(app.session_state.fault, "none")
        self.assertEqual(app.selectbox(key="fault_choice").value, "none")
        self.assertFalse(app.checkbox(key="reset_ack").value)
        self.assertTrue(app.checkbox(key="reset_ack").disabled)
        self.assertTrue(app.button(key="confirm_reset").disabled)
        self.assert_no_pass(app)
        self.navigate(app, "Investigate & Repair")
        self.assertEqual(app.selectbox(key="source_choice").value, "sales")
        self.assertEqual(app.selectbox(key="entity_id").value, "sources.sales")
        self.assertEqual(app.radio(key="graph_view").value, "Current state")
        self.assertNotIn("confirm", [item.key for item in app.button])
        self.navigate(app, "Verify & Test")
        app.button(key="verify").click().run()
        self.assertEqual(app.text_input(key="evidence_query").value, "")
        self.assertFalse(app.checkbox(key="mismatches_only").value)
        self.assertEqual(len(app.session_state.verification_history), 1)
        self.assertEqual(app.session_state.verification_history[0]["operation_ids"], [])

    def test_preview_is_read_only_and_tampering_cannot_authorize(self):
        app = self.app()
        engine = app.session_state.engine
        before = engine.backend.read()
        with patch.object(engine, "confirm_preview", wraps=engine.confirm_preview) as confirm:
            self.preview_sales(app)
            confirm.assert_not_called()
        self.assertEqual(engine.backend.read(), before)
        self.assertTrue(any("Preview only — nothing has changed" in item.value for item in app.info))
        self.assertFalse(any(item.value == "What actually happened" for item in app.subheader))
        self.assert_no_pass(app)
        preview = app.session_state.impact_preview
        self.assertEqual(preview["preserved_facts"], ["phone_support"])
        self.assertEqual(preview["invalidated_facts"], ["discount_20"])
        preview["invalidated_facts"] = []
        app.session_state.impact_preview = preview
        app.button(key="confirm").click().run()
        self.assertTrue(any("Preview changed or was altered" in item.value for item in app.error))
        self.assertEqual(engine.backend.read(), before)
        self.assertNotIn("operation_id", app.session_state)
        self.assertNotIn("verification", app.session_state)
        self.assertEqual(len(app.exception), 0)

    def test_stale_preview_disables_confirmation(self):
        app = self.preview_sales(self.app())
        engine = app.session_state.engine
        snapshot = engine.backend.read()
        engine.backend.commit(snapshot, snapshot.revision)
        before = engine.backend.read()
        app.run()
        self.assertTrue(app.button(key="confirm").disabled)
        self.assertTrue(any("preview is out of date" in item.value for item in app.warning))
        self.assertEqual(engine.backend.read(), before)
        self.assertEqual(before.operations, {})
        self.assertEqual(len(app.exception), 0)

    def test_truncated_and_contradictory_reports_are_blocked(self):
        app = self.preview_sales(self.app())
        app.button(key="confirm").click().run()
        before = app.session_state.engine.backend.read()
        for defect in ("truncated", "duplicate", "schedule", "match", "verdict", "incomplete", "timestamps"):
            with self.subTest(defect=defect):
                report = app.session_state.engine.verify_rebuild()
                self.assertEqual(report["status"], "PASS")
                if defect == "truncated":
                    report["comparisons"].pop()
                elif defect == "duplicate":
                    report["comparisons"].append(report["comparisons"][0].copy())
                elif defect == "schedule":
                    removed = report["comparisons"].pop()
                    report["required_paths"].remove(removed["path"])
                elif defect == "match":
                    report["comparisons"][0]["actual"] = "forged actual value"
                elif defect == "verdict":
                    report["status"] = "FAIL"
                elif defect == "incomplete":
                    report["complete"] = False
                else:
                    report["finished_at"] = "not a timestamp"
                app.session_state.verification = report
                for section in ("Investigate & Repair", "Verify & Test"):
                    self.navigate(app, section)
                    self.assert_no_pass(app)
                    self.assertTrue(any("ERROR" in item.value for item in app.error))
                    self.assertIn("VERIFIER / ERROR", self.html(app))
                    self.assertEqual(verification_state(report, before, app.session_state.engine.dataset)[0], "BLOCKED")
                    self.assertEqual(app.session_state.engine.backend.read(), before)

    def test_relationship_evidence_routes_to_graph_without_mutation(self):
        app = self.navigate(self.app(), "Verify & Test")
        before = app.session_state.engine.backend.read()
        app.button(key="verify").click().run()
        app.text_input(key="evidence_query").set_value("support_edges").run()
        self.assertEqual(app.selectbox(key="comparison_path").options, ["support_edges"])
        self.assertEqual(app.selectbox(key="relationship_entity").options,
                         ["facts.discount_20", "facts.phone_support", "sources.pricing", "sources.sales"])
        app.selectbox(key="relationship_entity").select("sources.pricing").run()
        app.button(key="inspect_relationship").click().run()
        self.assertEqual(app.session_state.pending_entity, "sources.pricing")
        self.navigate(app, "Investigate & Repair")
        self.assertEqual(app.selectbox(key="entity_id").value, "sources.pricing")
        self.assertEqual([node["entity"] for node in self.graph_nodes(app) if node["selected"]], ["sources.pricing"])
        with self.read_only(app), patch("fallout.workbench.render_graph", side_effect=ValueError("internal graph problem")):
            app.run()
        self.assertTrue(any("Graph view unavailable" in item.value for item in app.warning))
        self.assertIn("Relationship records / accessible graph alternative", [item.label for item in app.expander])
        self.assertEqual(app.selectbox(key="entity_id").value, "sources.pricing")
        self.assertEqual(len(app.get("component_instance")), 0)
        records = next(item.value for item in app.dataframe if "relationship" in item.value.columns)
        self.assertEqual(len(records), 5)
        self.assertEqual(set(records["relationship"]), {"SUPPORTED_BY", "COMPOSED_OF"})
        self.assertEqual(app.session_state.engine.backend.read(), before)
        self.assertNotIn("confirm", [item.key for item in app.button])
        self.assertEqual(len(app.exception), 0)

    def test_overview_counts_and_graph_follow_verified_saved_state(self):
        app = self.preview_sales(self.app())
        app.button(key="confirm").click().run()
        with self.read_only(app):
            self.navigate(app, "Overview")
            html = self.html(app)
            self.assertIn('<b>2</b> sources<small>1 active</small>', html)
            self.assertIn('<b>2</b> facts<small>1 valid</small>', html)
            self.assertIn('<b>1</b> summaries<small>1 fresh</small>', html)
            self.assertIn("VERIFIER / PASS", html)
            self.assertIn("sales · REPAIRED", [item.value for item in app.markdown])
            nodes = {node["entity"]: node for node in self.graph_nodes(app)}
            self.assertEqual(nodes["sources.sales"]["status"], "revoked")
            self.assertEqual(nodes["facts.phone_support"]["status"], "valid")
            self.assertEqual(nodes["facts.discount_20"]["status"], "invalid")
            self.assertEqual(nodes["summaries.customer_summary"]["status"], "fresh")
            self.assertEqual(self.memory_lines(app), [
                [("removed", "Removed", "A 20% discount is available.\n"),
                 ("retained", "Retained", "Phone support is available.")],
                [("retained", "Retained", "Phone support is available.")],
            ])
            captions = [item.value for item in app.caption]
            self.assertIn("BEFORE / saved before withdrawal", captions)
            self.assertIn("NOW / actual recorded memory", captions)
            exact = next(item for item in app.expander
                         if item.label == "Exact saved text · customer_summary")
            self.assertFalse(exact.proto.expanded)
            self.assertEqual([item.value for item in exact.caption], ["BEFORE", "NOW"])
            self.assertEqual([item.value for item in exact.text], [
                "A 20% discount is available.\nPhone support is available.",
                app.session_state.engine.backend.read().state.summaries["customer_summary"]["content"],
            ])

    def test_isolated_lab_executes_and_appends_without_changing_main_state_or_grants(self):
        app = self.navigate(self.app(), "Verify & Test")
        app.button(key="verify").click().run()
        self.preview_sales(app)
        engine = app.session_state.engine
        # A nonempty, test-owned approval proves the lab cannot consume or replace grants.
        engine.confirm_preview(app.session_state.impact_preview)
        self.navigate(app, "Verify & Test")
        saved = {key: deepcopy(app.session_state[key]) for key in (
            "impact_preview", "verification", "verification_id", "verification_history", "activity_history")}
        self.assertNotIn("lab_history", app.session_state)
        with self.read_only(app), patch("fallout.workbench.run_lab", wraps=run_lab) as execute:
            app.run()
            execute.assert_not_called()
            self.assertTrue(any("NOT RUN · No lab scenarios" in item.value for item in app.info))
            first = None
            generations = set()
            for index in range(2):
                app.button(key="run_lab").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(execute.call_count, index + 1)
                history = app.session_state.lab_history
                self.assertEqual(len(history), index + 1)
                if first is not None:
                    self.assertEqual(history[0], first)
                    self.assertNotEqual(history[1]["id"], first["id"])
                first = deepcopy(history[0])
                result = history[index]
                normal, fault = result["scenarios"]
                self.assertEqual([normal["verdict"], fault["verdict"]], ["PASS", "FAIL"])
                for scenario in result["scenarios"]:
                    report, after = scenario["verification"], scenario["after"]
                    self.assertIsNone(verifier.report_problem(report, engine.dataset, Snapshot(**after)))
                    self.assertEqual(report["status"], scenario["verdict"])
                    self.assertEqual(len(report["comparisons"]), 30)
                    self.assertEqual(set(report["required_paths"]), {row["path"] for row in report["comparisons"]})
                    self.assertEqual((report["generation"], report["revision"]),
                                     (after["generation"], after["revision"]))
                    self.assertEqual(scenario["before"]["revision"], 0)
                    self.assertEqual(after["revision"], 3)
                    self.assertNotEqual(after["generation"], engine.backend.read().generation)
                    generations.add(after["generation"])
                    self.assertEqual(after["state"]["sources"]["sales"]["status"], "revoked")
                    self.assertEqual(after["state"]["facts"]["phone_support"]["active_support"], ["pricing"])
                    self.assertEqual(after["state"]["facts"]["discount_20"]["status"], "invalid")
                    self.assertTrue(scenario["verification_id"])
                    self.assertLessEqual(datetime.fromisoformat(scenario["started_at"]),
                                         datetime.fromisoformat(scenario["finished_at"]))
                self.assertEqual(normal["verification"]["mismatches"], [])
                self.assertEqual(normal["operation"]["status"], "repaired")
                self.assertEqual(normal["after"]["state"]["summaries"]["customer_summary"]["content"],
                                 "Phone support is available.")
                self.assertEqual(fault["operation"]["steps"]["rebuild_summaries"]["status"], "SKIPPED")
                mismatch = next(row for row in fault["verification"]["mismatches"]
                                if row["path"] == "summaries.customer_summary.content")
                self.assertEqual(mismatch["expected"], "Phone support is available.")
                self.assertEqual(mismatch["actual"], "A 20% discount is available.\nPhone support is available.")
                app.selectbox(key="lab_run").select(index).run()
                markdown = "\n".join(item.value for item in app.markdown)
                self.assertIn("Normal repair · PASS", markdown)
                self.assertIn("Skipped summary rebuild · FAIL", markdown)
                self.assertIn("Stale-content diagnosis", markdown)
                self.assertTrue(any(result["id"] in item.value for item in app.caption))
                self.assertIn("VERIFIER / PASS", self.html(app))
                self.assertNotIn("VERIFIER / FAIL", self.html(app))
                for key, value in saved.items():
                    self.assertEqual(app.session_state[key], value, key)
                self.assertNotIn("operation_id", app.session_state)
                self.assertNotIn("before_snapshot", app.session_state)
                app.run()
                self.assertEqual(execute.call_count, index + 1)
            self.assertEqual(len(generations), 4)
        lab_history = deepcopy(app.session_state.lab_history)
        self.navigate(app, "Investigate & Repair")
        self.assertEqual(app.session_state.impact_preview, saved["impact_preview"])
        self.assertFalse(app.button(key="confirm").disabled)
        with self.read_only(app):
            app.button(key="preview_reset").click().run()
            app.checkbox(key="reset_ack").check().run()
        app.button(key="confirm_reset").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(engine.backend.read().revision, 0)
        self.assertEqual(engine._grants, {})
        for key in saved:
            self.assertNotIn(key, app.session_state)
        self.assertEqual(app.session_state.lab_history, lab_history)
        self.navigate(app, "Verify & Test")
        app.selectbox(key="lab_run").select(0).run()
        self.assertTrue(any(lab_history[0]["id"] in item.value for item in app.caption))
        self.assertIn("VERIFIER / NOT RUN", self.html(app))

    def test_activity_records_real_action_times_and_recovery_not_navigation(self):
        app = self.app()
        engine = app.session_state.engine
        with self.read_only(app):
            self.navigate(app, "Investigate & Repair")
            app.selectbox(key="entity_id").select("facts.phone_support").run()
            self.navigate(app, "Verify & Test")
            self.assertNotIn("activity_history", app.session_state)
        bounds = []
        started = datetime.now(timezone.utc)
        self.preview_sales(app)
        bounds.append((started, datetime.now(timezone.utc)))
        self.choose_fault(app, "skip_summary")
        preview_id = app.session_state.impact_preview["preview_id"]
        started = datetime.now(timezone.utc)
        app.button(key="confirm").click().run()
        bounds.append((started, datetime.now(timezone.utc)))
        self.assertEqual(app.session_state.verification["status"], "FAIL")
        self.navigate(app, "Verify & Test")
        started = datetime.now(timezone.utc)
        app.button(key="retry").click().run()
        bounds.append((started, datetime.now(timezone.utc)))
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.verification["status"], "PASS")
        history = deepcopy(app.session_state.activity_history)
        self.assertEqual([row["stage"] for row in history], [
            "impact_analysis", "explicit_approval", "independent_verification",
            "recovery_requested", "independent_verification"])
        self.assertEqual([row["outcome"] for row in history], ["PREVIEW ONLY", "APPROVED", "FAIL", "APPROVED", "PASS"])
        self.assertEqual(len({row["id"] for row in history}), 5)
        for row, window in zip(history, (bounds[0], bounds[1], bounds[1], bounds[2], bounds[2])):
            at = datetime.fromisoformat(row["at"])
            self.assertIsNotNone(at.tzinfo)
            self.assertLessEqual(window[0], at)
            self.assertLessEqual(at, window[1])
            self.assertEqual(row["generation"], engine.backend.read().generation)
        self.assertEqual(history[0]["preview_id"], preview_id)
        self.assertEqual(history[1]["preview_id"], preview_id)
        self.assertEqual(history[0]["revision"], 0)
        self.assertEqual(history[1]["revision"], 0)
        self.assertEqual(history[3]["operation_id"], app.session_state.operation_id)
        checks = app.session_state.verification_history
        self.assertEqual(len(checks), 2)
        for row, check in zip((history[2], history[4]), checks):
            self.assertEqual(row["verification_id"], check["id"])
            self.assertEqual(row["revision"], check["revision"])
            self.assertLessEqual(datetime.fromisoformat(check["started_at"]), datetime.fromisoformat(check["finished_at"]))
            self.assertLessEqual(datetime.fromisoformat(check["finished_at"]), datetime.fromisoformat(row["at"]))
        timeline = next(item.value.to_dict("records") for item in app.dataframe
                        if {"at", "stage", "reference"} <= set(item.value.columns))
        self.assertEqual(len(timeline), len(history) + len(engine.backend.read().events))
        self.assertEqual([row["at"] for row in timeline], sorted(row["at"] for row in timeline))
        for row in history:
            actual = next(item for item in timeline if item["id"] == row["id"])
            self.assertEqual((actual["at"], actual["stage"], actual["outcome"]),
                             (row["at"], row["stage"], row["outcome"]))
        for event in engine.backend.read().events:
            self.assertTrue(any(row["at"] == event["at"] and row["reference"] == event["operation_id"]
                                and row["stage"] == event.get("step", event["type"]) for row in timeline))
        with self.read_only(app):
            app.run()
            self.navigate(app, "Overview")
            self.assertEqual(app.session_state.activity_history, history)
            self.assertEqual(app.session_state.verification_history, checks)

    def test_header_distinguishes_configured_mock_live_and_failure_without_requests(self):
        app = self.app()
        self.assertIn("AI OFF", self.html(app))
        settings = AISettings(True, "gemini", "gemini-offline-fixture", "offline_fixture_not_a_real_key", True)
        signature = (settings.enabled, settings.provider, settings.public_model,
                     settings.free_tier_confirmed, hash(settings.api_key))
        with self.read_only(app), patch("fallout.workbench.AISettings.from_environment", return_value=settings):
            # Session markers are fixtures only; this test never performs a live request.
            cases = (
                ({}, False, "AI CONFIGURED · NOT LIVE-TESTED"),
                ({"status": "complete", "provenance": "mock"}, False, "AI MOCK RESULT"),
                ({"status": "complete", "provenance": "mock"}, True, "AI MOCK RESULT"),
                ({"status": "complete", "provenance": "live"}, True, "AI LIVE REQUEST SUCCEEDED"),
                ({"status": "unavailable"}, True, "AI LAST REQUEST FAILED"),
            )
            labels = {case[2] for case in cases}
            for result, live_tested, label in cases:
                with self.subTest(label=label, live_tested=live_tested):
                    app.session_state.ai_configuration = signature
                    app.session_state.ai_result = result
                    app.session_state.ai_live_tested = live_tested
                    app.run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertIn(label, self.html(app))
                    for other in labels - {label}:
                        self.assertNotIn(other, self.html(app))
                    self.assertNotIn('<div class="fo-stage ', self.html(app))
                    self.assertEqual(app.session_state.engine.backend.read().operations, {})
                    self.assertIn("No operation yet. Selecting nodes and previewing impact do not change memory.",
                                  [item.value for item in app.info])
                    self.assertIn("VERIFIER / NOT RUN", self.html(app))
                    self.assertNotIn(settings.api_key, self.html(app))
            app.session_state.ai_configuration = ("old configuration",)
            app.run()
            self.assertIn("AI CONFIGURED · NOT LIVE-TESTED", self.html(app))
            self.assertNotIn("AI LAST REQUEST FAILED", self.html(app))
        self.assertNotIn("verification", app.session_state)

    def test_graph_events_select_historical_inspector_reject_stale_invalid_and_replay(self):
        app = self.preview_sales(self.app())
        app.button(key="confirm").click().run()
        engine = app.session_state.engine
        app.selectbox(key="source_choice").select("pricing").run()
        app.button(key="preview").click().run()
        preview = deepcopy(app.session_state.impact_preview)
        report = deepcopy(app.session_state.verification)
        current_context = self.graph_payload(app)["context"]
        with self.read_only(app):
            for view in ("Before last operation", "Original dataset"):
                app.radio(key="graph_view").set_value(view).run()
                context = self.graph_payload(app)["context"]
                self.assertNotEqual(context, current_context)
                event = {"entity": "sources.sales", "context": context, "sequence": 3}
                with patch("fallout.workbench.render_graph", return_value=event) as render:
                    app.run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(app.selectbox(key="entity_id").value, "sources.sales")
                    self.assertEqual(app.radio(key="graph_view").value, view)
                    self.assertIn("SOURCES / ACTIVE", [item.value for item in app.caption])
                    self.assertEqual(render.call_args.kwargs["context"], context)
                    self.assertEqual(render.call_args.kwargs["key"], "lineage_graph")
                    self.assertEqual(render.call_args.args[0].sources["sales"]["status"], "active")
                    self.assertEqual(render.call_args.args[1], "sources.sales")
                    self.assertEqual(app.session_state.lineage_graph_seen[context], 3)
                    # Retained component values cannot override a later selector choice.
                    app.selectbox(key="entity_id").select("facts.phone_support").run()
                    self.assertEqual(app.selectbox(key="entity_id").value, "facts.phone_support")
                    seen = deepcopy(app.session_state.lineage_graph_seen)
                    invalid = [
                        {**event, "context": current_context, "sequence": 4},
                        {**event, "entity": "facts.missing", "sequence": 4},
                        {**event, "entity": ["sources.sales"], "sequence": 4},
                        {**event, "sequence": 1}, {**event, "sequence": 0},
                        {**event, "sequence": -1}, {**event, "sequence": True},
                        {**event, "sequence": "4"}, {**event, "sequence": 2**53},
                        {**event, "sequence": 4, "authorize": True},
                        {"entity": "sources.sales", "context": context}, None, [],
                    ]
                    for value in invalid:
                        with self.subTest(view=view, event=value):
                            render.return_value = value
                            app.run()
                            self.assertEqual(len(app.exception), 0)
                            self.assertEqual(app.selectbox(key="entity_id").value, "facts.phone_support")
                            self.assertEqual(app.radio(key="graph_view").value, view)
                            self.assertEqual(app.session_state.lineage_graph_seen, seen)
                    render.return_value = {**event, "sequence": 4, "entity": "summaries.customer_summary"}
                    app.run()
                    self.assertEqual(app.selectbox(key="entity_id").value, "summaries.customer_summary")
                    self.assertEqual(app.radio(key="graph_view").value, view)
                    self.assertIn("A 20% discount is available.\nPhone support is available.",
                                  [item.value for item in app.text])
                    self.assertEqual(app.session_state.lineage_graph_seen[context], 4)
                app.run()
                self.assertEqual(self.graph_payload(app)["selected"], "summaries.customer_summary")
            historical_event = {"entity": "sources.sales", "context": context, "sequence": 5}
            app.radio(key="graph_view").set_value("Current state").run()
            with patch("fallout.workbench.render_graph", return_value=historical_event):
                app.run()
                self.assertEqual(app.selectbox(key="entity_id").value, "summaries.customer_summary")
                self.assertEqual(app.radio(key="graph_view").value, "Current state")
            self.assertEqual(app.session_state.impact_preview, preview)
            self.assertEqual(app.session_state.verification, report)
            self.assertFalse(app.button(key="confirm").disabled)
        self.assertEqual(engine.backend.read().state.sources["sales"]["status"], "revoked")

    def test_malformed_and_zero_comparison_reports_render_error_without_mutation(self):
        app = self.navigate(self.app(), "Verify & Test")
        engine = app.session_state.engine
        valid = engine.verify_rebuild()
        reports = [[], "invalid report", {}, {**valid, "comparisons": None},
                   {**valid, "comparisons": [None]},
                   {**valid, "comparisons": [], "required_paths": [], "mismatches": []}]
        with self.read_only(app):
            for report in reports:
                with self.subTest(report_type=type(report).__name__, report=report):
                    app.session_state.verification = report
                    app.run()
                    self.assert_no_pass(app)
                    self.assertIn("VERIFIER / ERROR", self.html(app))
                    self.assertTrue(any("ERROR" in item.value for item in app.error))
                    self.assertEqual(verification_state(report, engine.backend.read(), engine.dataset)[0], "BLOCKED")
                    self.assertEqual(len(app.get("download_button")), 0)
                    self.assertNotIn("comparison_path", [item.key for item in app.selectbox])

    def test_verification_action_handles_malformed_and_zero_evidence_as_error(self):
        for defect in ("non_mapping", "null_comparisons", "zero_comparisons"):
            with self.subTest(defect=defect):
                app = self.navigate(self.app(), "Verify & Test")
                engine = app.session_state.engine
                report = engine.verify_rebuild()
                if defect == "non_mapping":
                    report = []
                elif defect == "null_comparisons":
                    report["comparisons"] = None
                else:
                    report.update(comparisons=[], required_paths=[], mismatches=[])
                with self.read_only(app), patch.object(engine, "verify_rebuild", return_value=report) as execute:
                    app.button(key="verify").click().run()
                    execute.assert_called_once_with()
                    self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
                    self.assert_no_pass(app)
                    self.assertIn("VERIFIER / ERROR", self.html(app))
                    self.assertTrue(any("ERROR" in item.value for item in app.error))
                    self.assertEqual(len(app.get("download_button")), 0)
                    self.assertEqual(app.session_state.verification_history[-1]["status"], "ERROR")
                    self.assertEqual(app.session_state.verification_history[-1]["checks_executed"], 0)
                    self.assertEqual(verification_state(app.session_state.verification,
                                                        engine.backend.read(), engine.dataset)[0], "BLOCKED")

    def test_export_only_for_current_valid_synthetic_evidence(self):
        import streamlit as st

        app = self.navigate(self.app(), "Verify & Test")
        self.assertEqual(len(app.get("download_button")), 0)
        engine = app.session_state.engine
        with self.read_only(app), patch("fallout.workbench.st.download_button", wraps=st.download_button) as export:
            app.button(key="verify").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.get("download_button")), 1)
            payload = json.loads(export.call_args.args[1])
            self.assertEqual(set(payload), {"scope", "verification_id", "verification"})
            self.assertEqual(payload["verification"], json.loads(json.dumps(app.session_state.verification)))
            self.assertEqual(payload["verification_id"], app.session_state.verification_id)
            self.assertIn("not an immutable audit", payload["scope"])
            self.assertEqual(payload["verification"]["status"], "PASS")
            self.assertTrue(payload["verification"]["comparisons"])
            self.assertEqual(export.call_args.kwargs["mime"], "application/json")
        snapshot = engine.backend.read()
        engine.backend.commit(snapshot, snapshot.revision)
        with self.read_only(app):
            app.run()
            self.assertEqual(len(app.get("download_button")), 0)
            self.assert_no_pass(app)
        # Synthetic test-only corruption, not private input: a valid FAIL report
        # must still be refused by the real export boundary when text is unreviewed.
        snapshot = engine.backend.read()
        snapshot.state.sources["sales"]["text"] = "UNREVIEWED SYNTHETIC TEST FIXTURE"
        engine.backend.commit(snapshot, snapshot.revision)
        with self.read_only(app), patch("fallout.workbench.st.download_button", wraps=st.download_button) as export:
            app.button(key="verify").click().run()
            self.assertEqual(len(app.exception), 0)
            report = app.session_state.verification
            self.assertEqual(report["status"], "FAIL")
            self.assertIsNone(verifier.report_problem(report, engine.dataset, engine.backend.read()))
            self.assertEqual(len(app.get("download_button")), 0)
            export.assert_not_called()
            self.assertTrue(any("Export unavailable" in item.value for item in app.caption))


if __name__ == "__main__":
    unittest.main()