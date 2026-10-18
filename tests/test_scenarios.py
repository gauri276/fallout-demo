"""Scenario contracts exercised through the real in-memory engine and verifier."""

from collections import Counter
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
import random
import unittest
from unittest.mock import patch

from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.models import Dataset, FalloutError, ROOT
from fallout.scenarios import SCENARIOS, Scenario, get_scenario
from fallout.verifier import report_problem, verify


def renamed(dataset):
    """Rename every graph identifier, preserving lexical order (and thus rendering).

    Summary text is rendered in sorted fact-ID order, NOT dependency-list order.
    An arbitrary permutation of IDs would legitimately reorder content and the
    first sorted false-success victim. Fixed-width IDs preserve both semantics.
    Evidence text is not an identifier and must not be rewritten.
    """
    data = dataset.original()
    identifiers = sorted({row["id"] for rows in data.values() for row in rows})
    mapping = {key: "node_%03d" % index for index, key in enumerate(identifiers)}
    for group, rows in data.items():
        for row in rows:
            row["id"] = mapping[row["id"]]
            relation = {"facts": "sources", "summaries": "facts"}.get(group)
            if relation:
                row[relation] = [mapping[key] for key in row[relation]]
    return Dataset.from_dict(data), mapping


def shuffled(dataset, seed):
    data = dataset.original()
    rng = random.Random(seed)
    for rows in data.values():
        rng.shuffle(rows)
        for row in rows:
            for relation in ("sources", "facts"):
                if relation in row:
                    rng.shuffle(row[relation])
    return Dataset.from_dict(data)


def normalized(state, inverse):
    """Compare semantic state including build counts, retaining exact text.

    Membership/support are sets rendered as sorted IDs. Edges are multisets so
    input ordering is irrelevant but an accidental duplicate is NOT hidden.
    Snapshot fingerprints, UUIDs and timestamps are intentionally not state.
    """
    result = {}
    for group in ("sources", "facts", "summaries"):
        result[group] = {}
        for key, row in getattr(state, group).items():
            row = deepcopy(row)
            for relation in ("members", "active_support"):
                if relation in row:
                    row[relation] = sorted(inverse.get(item, item) for item in row[relation])
            result[group][inverse.get(key, key)] = row
    for relation in ("support_edges", "composition_edges"):
        result[relation] = Counter(tuple(inverse.get(key, key) for key in edge)
                                   for edge in getattr(state, relation))
    return result


class ScenarioTests(unittest.TestCase):
    def test_configuration_and_validation(self):
        self.assertEqual(set(SCENARIOS), {"pricing", "documentation"})
        self.assertIs(get_scenario(), SCENARIOS["pricing"])
        self.assertEqual([field.name for field in fields(Scenario)],
                         ["id", "label", "path", "withdraw_source", "description"])
        for key, scenario in SCENARIOS.items():
            with self.subTest(key=key):
                self.assertIs(get_scenario(key), scenario)
                self.assertEqual(scenario.id, key)
                self.assertTrue(scenario.label and scenario.description)
                self.assertEqual(scenario.path, ROOT / "data" / (
                    "demo.json" if key == "pricing" else "documentation.json"))
                with self.assertRaises(FrozenInstanceError):
                    scenario.label = "Changed"
                with patch.object(Dataset, "load", wraps=Dataset.load) as load:
                    dataset = scenario.load()
                load.assert_called_once_with(scenario.path)
                self.assertIsInstance(dataset, Dataset)
                with self.assertRaises(FalloutError):
                    replace(scenario, withdraw_source="missing_source").load()
                with self.assertRaises(FalloutError):
                    replace(scenario, withdraw_source="Invalid ID").load()
        for key in (None, [], {}, 42, "", "Pricing", "documentation ", "../demo"):
            with self.subTest(key=key), self.assertRaises(FalloutError):
                get_scenario(key)

    def test_pricing_retains_pinned_dataset_and_counts(self):
        scenario = get_scenario()
        dataset = scenario.load()
        self.assertEqual(scenario.withdraw_source, "sales")
        self.assertEqual(dataset.canonical, Dataset.load().canonical)
        # Literal pin avoids importing AI configuration or reading environment files.
        self.assertEqual(dataset.fingerprint,
                         "261752e4d54772614be07847c2ea7ded7335c9127195d34087bd18959acc1068")
        self.assertEqual({key: len(rows) for key, rows in dataset.original().items()},
                         {"sources": 2, "facts": 2, "summaries": 1})

    def test_explicit_domain_semantics(self):
        cases = (
            ("pricing", ["phone_support"], ["discount_20"], ["customer_summary"],
             {"phone_support": ["pricing"], "discount_20": []},
             {"customer_summary": "Phone support is available."}),
            ("documentation", ["cursor_pagination"], ["legacy_query_tokens"],
             ["api_overview", "migration_notes"],
             {"cursor_pagination": ["api_reference"], "legacy_query_tokens": []},
             {"api_overview": "Cursor pagination is supported.\nThe API allows 100 requests per minute.",
              "migration_notes": "Cursor pagination is supported.",
              "installation_summary": "Installation requires Python 3.11 or later."}),
        )
        for key, preserved, invalidated, affected, support, content in cases:
            with self.subTest(key=key):
                scenario = get_scenario(key)
                dataset = scenario.load()
                engine = Engine(dataset, InMemoryBackend(dataset))
                initial = engine.backend.read()
                self.assertTrue(all(row["status"] == "active" for row in initial.state.sources.values()))
                self.assertTrue(all(row["status"] == "valid" for row in initial.state.facts.values()))
                preview = engine.preview_impact(scenario.withdraw_source)
                self.assertEqual(preview["preserved_facts"], preserved)
                self.assertEqual(preview["invalidated_facts"], invalidated)
                self.assertEqual(preview["affected_summaries"], affected)
                self.assertEqual(preview["remaining_support"], support)
                operation = engine.revoke_source(scenario.withdraw_source, engine.confirm_preview(preview))
                engine.repair_memory(operation["id"])
                self.assertEqual({key: row["content"] for key, row in engine.backend.read().state.summaries.items()}, content)
                self.assert_verdict(engine, "PASS")
        documentation = get_scenario("documentation")
        self.assertEqual(documentation.withdraw_source, "obsolete_bulletin")
        data = documentation.load().original()
        self.assertEqual({row["id"] for row in data["sources"]},
                         {"obsolete_bulletin", "api_reference", "installation_guide"})
        self.assertGreaterEqual(len(data["facts"]), 4)
        # Archived/version labels alone never revoke a source or invalidate a fact.
        self.assertIn("v1, archived", next(row["text"] for row in data["sources"]
                                         if row["id"] == "obsolete_bulletin"))

    def assert_verdict(self, engine, status):
        before = engine.backend.read()
        report = engine.verify_rebuild()
        self.assertEqual(report["status"], status)
        self.assertTrue(report["complete"])
        self.assertIsNone(report_problem(report, engine.dataset, before))
        self.assertEqual(engine.backend.read(), before)
        return report

    def exercise(self, dataset, source, fault):
        """Independent expected writes from input definitions, never verifier output."""
        engine = Engine(dataset, InMemoryBackend(dataset))
        initial = engine.backend.read()
        self.assert_verdict(engine, "PASS")
        data = dataset.original()
        facts = {row["id"]: row for row in data["facts"]}
        definitions = {row["id"]: row for row in data["summaries"]}
        affected_facts = {key for key, row in facts.items() if source in row["sources"]}
        affected_summaries = {key for key, row in definitions.items()
                              if affected_facts.intersection(row["facts"])}
        preview = engine.preview_impact(source)
        self.assertEqual(preview["affected_facts"], sorted(affected_facts))
        self.assertEqual(preview["affected_summaries"], sorted(affected_summaries))
        token = engine.confirm_preview(preview)
        self.assertEqual(engine.backend.read(), initial)
        operation = engine.revoke_source(source, token)
        revoked = engine.backend.read()
        expected_revoked = deepcopy(initial.state)
        expected_revoked.sources[source]["status"] = "revoked"
        for key in affected_summaries:
            expected_revoked.summaries[key]["status"] = "stale"
        self.assertEqual(revoked.state, expected_revoked)
        self.assert_verdict(engine, "FAIL")

        expected = deepcopy(expected_revoked)
        for key in affected_facts:
            remaining = sorted(set(facts[key]["sources"]) - {source})
            expected.facts[key].update(active_support=remaining,
                                       status="valid" if remaining else "invalid")
        for key in affected_summaries:
            members = sorted(fact for fact in definitions[key]["facts"]
                             if set(facts[fact]["sources"]) - {source})
            expected.summaries[key] = {
                "members": members, "content": "\n".join(facts[fact]["text"] for fact in members),
                "status": "fresh", "build_count": 1,
            }
        # Full state comparisons cover every unaffected field and both edge lists,
        # including summary build_count (which the verifier deliberately ignores).
        operation = engine.repair_memory(operation["id"], fault)
        self.assertEqual(operation["status"], "repaired")
        self.assertEqual(operation["attempts"], 1)
        stages = [initial.state, revoked.state]
        if fault == "false_success":
            changed = sorted(key for key in affected_summaries
                             if expected.summaries[key]["content"] != revoked.state.summaries[key]["content"])
            self.assertTrue(changed)
            skipped = changed[0]
            self.assertEqual(operation["skipped_summary"], skipped)
            self.assertTrue(operation["false_success_pending"])
            rebuilt = sorted(affected_summaries - {skipped})
            step = operation["steps"]["rebuild_summaries"]
            self.assertEqual(step["status"], "PARTIAL" if rebuilt else "SKIPPED")
            self.assertEqual(step["rebuilt_summaries"], rebuilt)
            stale = deepcopy(expected)
            stale.summaries[skipped] = deepcopy(revoked.state.summaries[skipped])
            self.assertEqual(engine.backend.read().state, stale)
            report = self.assert_verdict(engine, "FAIL")
            self.assertEqual(report["mismatches"], [
                {"path": "summaries." + skipped + "." + field,
                 "expected": expected.summaries[skipped][field],
                 "actual": stale.summaries[skipped][field]}
                for field in ("content", "members", "status")
            ])
            # Content alone disproves completion, even without fault diagnostics,
            # a stale status, or stale membership as hints to the real verifier.
            disguised = engine.backend.read()
            disguised.state.summaries[skipped].update(
                status="fresh", members=expected.summaries[skipped]["members"])
            for field in ("fault", "steps", "skipped_summary", "false_success_pending"):
                disguised.operations[operation["id"]].pop(field)
            disguised.events = [event for event in disguised.events if event["type"] == "source_revoked"]
            content_report = verify(dataset, disguised)
            self.assertEqual(content_report["status"], "FAIL")
            self.assertEqual(content_report["mismatches"], report["mismatches"][:1])
            self.assertIsNone(report_problem(content_report, dataset, disguised))
            stages.append(stale)
            # Recreate the engine to prove recovery uses durable operation state.
            engine = Engine(dataset, engine.backend)
            recovered = engine.repair_memory(operation["id"])
            self.assertEqual(recovered["id"], operation["id"])
            self.assertEqual(recovered["attempts"], 2)
            self.assertFalse(recovered["false_success_pending"])
            self.assertIsNone(recovered["skipped_summary"])
            for key in rebuilt:
                expected.summaries[key]["build_count"] += 1
            operation = recovered
        self.assertEqual(operation["status"], "repaired")
        self.assertTrue(all(step["status"] == "COMPLETED" for step in operation["steps"].values()))
        final = engine.backend.read()
        self.assertEqual(final.state, expected)
        self.assert_verdict(engine, "PASS")
        for retry_fault in ("none", "false_success"):
            self.assertEqual(engine.repair_memory(operation["id"], retry_fault), operation)
            self.assertEqual(engine.backend.read(), final)
        stages.append(final.state)
        return stages

    def test_normal_repair_full_noninterference(self):
        for key, scenario in SCENARIOS.items():
            with self.subTest(key=key):
                self.exercise(scenario.load(), scenario.withdraw_source, "none")

    def test_false_success_exact_stale_content_and_recovery(self):
        for key, scenario in SCENARIOS.items():
            with self.subTest(key=key):
                self.exercise(scenario.load(), scenario.withdraw_source, "false_success")

    def test_all_identifiers_renamed_and_input_order_invariant(self):
        for key, scenario in SCENARIOS.items():
            dataset = scenario.load()
            changed, mapping = renamed(dataset)
            self.assertEqual(sorted(mapping.values()), [mapping[key] for key in sorted(mapping)])
            self.assertTrue(set(mapping).isdisjoint(mapping.values()))
            inverse = {value: key for key, value in mapping.items()}
            for fault in ("none", "false_success"):
                baseline = [normalized(state, {}) for state in
                            self.exercise(dataset, scenario.withdraw_source, fault)]
                variants = [(changed, mapping[scenario.withdraw_source], inverse)]
                for seed in (7, 23, 91):
                    variants.extend([
                        (shuffled(dataset, seed), scenario.withdraw_source, {}),
                        (shuffled(changed, seed), mapping[scenario.withdraw_source], inverse),
                    ])
                for index, (variant, source, undo) in enumerate(variants):
                    with self.subTest(key=key, fault=fault, variant=index):
                        actual = self.exercise(variant, source, fault)
                        self.assertEqual([normalized(state, undo) for state in actual], baseline)


if __name__ == "__main__":
    unittest.main()