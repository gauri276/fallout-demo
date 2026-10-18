"""Graph/evidence projections validated independently of a browser."""

import importlib.util
import json
import unittest

from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.models import Dataset
from fallout.presentation import current_report, entity_options, evidence_rows, graph_records, graph_spec, mismatch_entity


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.dataset = Dataset.load()
        self.engine = Engine(self.dataset, InMemoryBackend(self.dataset))

    def repair(self, fault="none"):
        token = self.engine.confirm_preview(self.engine.preview_impact("sales"))
        operation = self.engine.revoke_source("sales", token)
        self.engine.repair_memory(operation["id"], fault)

    def test_graph_uses_exact_nodes_and_directed_edges(self):
        state = self.engine.backend.read().state
        data = graph_records(state, "facts.phone_support", "sales")
        self.assertEqual(len(data["nodes"]), 5)
        self.assertEqual(len(data["edges"]), 5)
        self.assertEqual({node["entity"] for node in data["nodes"]}, set(entity_options(state)))
        self.assertTrue(all(edge["x2"] > edge["x"] for edge in data["edges"]))
        self.assertEqual({node["shape"] for node in data["nodes"]}, {"square", "circle", "diamond"})
        self.assertEqual([node["entity"] for node in data["nodes"] if node["selected"]], ["facts.phone_support"])

    def test_revocation_changes_actual_graph_not_original(self):
        before = self.engine.backend.read()
        self.repair()
        after = graph_records(self.engine.backend.read().state, "sources.sales", "sales")
        self.assertEqual(sum(not edge["active"] for edge in after["edges"]), 3)
        nodes = {node["id"]: node for node in after["nodes"]}
        self.assertEqual(nodes["discount_20"]["status"], "invalid")
        self.assertEqual(nodes["phone_support"]["status"], "valid")
        self.assertEqual(before.state.facts["discount_20"]["status"], "valid")

    def test_impact_highlight_changes_with_source(self):
        state = self.engine.backend.read().state
        data = graph_records(state, "sources.pricing", "pricing")
        affected = {node["id"] for node in data["nodes"] if node["affected"]}
        self.assertEqual(affected, {"pricing", "phone_support", "customer_summary"})

    def test_mismatch_graph_flags_require_evidence(self):
        self.repair("skip_summary")
        snapshot = self.engine.backend.read()
        result = self.engine.verify_rebuild()
        unverified = graph_records(snapshot.state, "sources.sales", "sales")
        verified = graph_records(snapshot.state, "sources.sales", "sales", result["mismatches"])
        self.assertFalse(any("MISMATCH" in node["note"] for node in unverified["nodes"]))
        flagged = [node["id"] for node in verified["nodes"] if "MISMATCH" in node["note"]]
        self.assertEqual(flagged, ["customer_summary"])

    def test_evidence_complete_and_independent(self):
        self.repair("skip_invalidation")
        report = self.engine.verify_rebuild()
        self.assertEqual(len(evidence_rows(report)), len(report["comparisons"]))
        failures = evidence_rows(report, mismatches_only=True)
        self.assertEqual(len(failures), len(report["mismatches"]))
        discount = evidence_rows(report, "facts.discount_20.status", True)[0]
        self.assertEqual(discount["expected"], "invalid")
        self.assertEqual(discount["actual"], "valid")
        self.assertEqual(evidence_rows(report, "nonexistent"), [])

    def test_evidence_freshness_binds_revision_and_generation(self):
        report = self.engine.verify_rebuild()
        self.assertTrue(current_report(report, self.engine.backend.read()))
        self.engine.reset_demo(self.engine.preview_reset(), confirmed=True)
        self.assertFalse(current_report(report, self.engine.backend.read()))
        self.assertFalse(current_report(None, self.engine.backend.read()))

    def test_mismatch_routing_validates_entity(self):
        state = self.engine.backend.read().state
        self.assertEqual(mismatch_entity("facts.discount_20.status", state), "facts.discount_20")
        for path in ("support_edges", "facts.unknown.status", "facts.ids", "__dict__.x.status"):
            self.assertIsNone(mismatch_entity(path, state))

    def test_graph_has_no_fake_click_control_or_external_resources(self):
        spec = graph_spec(self.engine.backend.read().state, "sources.sales", "sales")
        encoded = json.dumps(spec)
        self.assertNotIn('"url"', encoded)
        self.assertNotIn("https://", encoded)
        self.assertNotIn('"cursor": "pointer"', encoded)
        self.assertNotIn('"params"', encoded)

    @unittest.skipUnless(importlib.util.find_spec("altair"), "Optional installed UI schema validator unavailable")
    def test_vega_lite_schema_locally_valid(self):
        import altair as alt
        spec = graph_spec(self.engine.backend.read().state, "sources.sales", "sales")
        alt.LayerChart.from_dict(spec, validate=True)


if __name__ == "__main__":
    unittest.main()