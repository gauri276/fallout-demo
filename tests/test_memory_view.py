"""Lossless memory views against real verifier evidence; stdlib only."""

from copy import deepcopy
from itertools import product
from pathlib import Path
import unittest

from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.memory_view import memory_comparison
from fallout.models import Dataset
from fallout.verifier import verify


def summary(content, **metadata):
    return {"content": content, **metadata}


def text(segments):
    return "".join(segment["text"] for segment in segments)


class MemoryViewTests(unittest.TestCase):
    def assert_lossless(self, result):
        for side in ("before", "current"):
            view = result[side]
            if view["content_missing"]:
                self.assertIsNone(view["content"])
                self.assertEqual(view["segments"], [])
            else:
                self.assertEqual(text(view["segments"]), view["content"])
        reference = result["reference"]
        if reference is not None:
            if reference["actual_missing"]:
                self.assertEqual(reference["actual_segments"], [])
            else:
                self.assertEqual(text(reference["actual_segments"]), reference["actual_content"])
            self.assertEqual(text(reference["expected_segments"]), reference["expected_content"])

    def test_both_fixtures_false_success_and_same_operation_recovery(self):
        root = Path(__file__).resolve().parents[1]
        for fixture, source in (("demo.json", "sales"),
                                ("documentation.json", "obsolete_bulletin")):
            with self.subTest(fixture=fixture):
                dataset = Dataset.load(root / "data" / fixture)
                engine = Engine(dataset, InMemoryBackend(dataset))
                before = engine.backend.read()
                token = engine.confirm_preview(engine.preview_impact(source))
                operation = engine.revoke_source(source, token)
                failed = engine.repair_memory(operation["id"], fault="false_success")
                failure = engine.backend.read()
                saved = deepcopy(failure)
                report = verify(dataset, failure)
                self.assertEqual(report["status"], "FAIL")
                self.assertTrue(report["complete"])
                key = failed["skipped_summary"]
                path = "summaries." + key + ".content"
                evidence = next(row for row in report["comparisons"] if row["path"] == path)
                self.assertFalse(evidence["match"])
                prior, actual = before.state.summaries[key], failure.state.summaries[key]
                result = memory_comparison(prior, actual, evidence["expected"])
                self.assert_lossless(result)
                self.assertTrue(result["compared"])
                self.assertTrue(result["verified_reference"])
                self.assertEqual(result["current"]["content"], evidence["actual"])
                # The failed repair kept actual content: do not rewrite its diff.
                self.assertEqual({s["label"] for s in result["current"]["segments"]}, {"retained"})
                self.assertIn("stale", {s["label"] for s in result["reference"]["actual_segments"]})
                self.assertEqual(failure, saved)
                self.assertEqual(engine.backend.read(), saved)

                recovered = engine.repair_memory(operation["id"])
                self.assertEqual(recovered["id"], operation["id"])
                self.assertEqual(recovered["attempts"], 2)
                after = engine.backend.read()
                report = verify(dataset, after)
                self.assertEqual(report["status"], "PASS")
                expected = next(row["expected"] for row in report["comparisons"] if row["path"] == path)
                recovery = memory_comparison(actual, after.state.summaries[key], expected)
                self.assert_lossless(recovery)
                self.assertIn("removed", {s["label"] for s in recovery["before"]["segments"]})
                self.assertEqual({s["label"] for s in recovery["reference"]["actual_segments"]}, {"retained"})
                self.assertEqual(recovery["current"]["content"], expected)
                self.assertEqual(engine.backend.read(), after)

    def test_initial_and_unchanged_do_not_infer_staleness_from_status(self):
        record = summary("<b>same</b>\n", status="stale")
        initial = memory_comparison(None, record)
        self.assertFalse(initial["compared"])
        self.assertFalse(initial["verified_reference"])
        self.assertIsNone(initial["reference"])
        self.assertEqual(initial["current"]["segments"], [{"text": record["content"], "label": "current"}])
        unchanged = memory_comparison(record, record)
        self.assertTrue(unchanged["compared"])
        for side in ("before", "current"):
            self.assertEqual(unchanged[side]["segments"], [{"text": record["content"], "label": "retained"}])
        self.assert_lossless(initial)
        self.assert_lossless(unchanged)

    def test_missing_records_and_missing_content_are_not_empty_records(self):
        for before, current in product((None, {}, summary(None), summary(""), summary("actual\n")), repeat=2):
            for expected in (None, "", "expected\n"):
                with self.subTest(before=before, current=current, expected=expected):
                    result = memory_comparison(before, current, expected)
                    self.assert_lossless(result)
                    available = [row is not None and row.get("content") is not None for row in (before, current)]
                    self.assertEqual(result["compared"], all(available))
                    self.assertEqual(result["verified_reference"], expected is not None)
                    for side, row in (("before", before), ("current", current)):
                        self.assertEqual(result[side]["missing"], row is None)
                        self.assertEqual(result[side]["summary"], row)
                        if not result["compared"]:
                            self.assertTrue(all(s["label"] == "current" for s in result[side]["segments"]))
                    if expected is not None:
                        self.assertEqual(result["reference"]["compared"], available[1])
        empty_reference = memory_comparison(None, summary("actual"), "")
        self.assertEqual(empty_reference["reference"]["actual_segments"], [{"text": "actual", "label": "stale"}])

    def test_additions_are_not_stale_without_expected_and_diff_is_preserved(self):
        before, current = summary("keep\n"), summary("keep\nnew\n")
        ordinary = memory_comparison(before, current)
        self.assertEqual(ordinary["current"]["segments"], [
            {"text": "keep\n", "label": "retained"}, {"text": "new\n", "label": "added"}])
        for expected in ("keep\n", "keep\nnew\n", "keep\nnew\nmissing\n"):
            checked = memory_comparison(before, current, expected)
            self.assertEqual(checked["before"], ordinary["before"])
            self.assertEqual(checked["current"], ordinary["current"])
            self.assert_lossless(checked)
            self.assertEqual(any(s["label"] == "stale" for s in checked["reference"]["actual_segments"]),
                             expected == "keep\n")
        missing_expected = memory_comparison(None, summary("keep\n"), "keep\nneeded\n")
        self.assertEqual(missing_expected["reference"]["expected_segments"][-1],
                         {"text": "needed\n", "label": "added"})

    def test_partial_edits_are_whole_line_replacements_and_not_escaped(self):
        before = summary('<b>old & "quoted"</b>\r\nkeep\nlast')
        current = summary('<b>new & "quoted"</b>\r\nkeep\nlast\n')
        result = memory_comparison(before, current, before["content"])
        self.assert_lossless(result)
        self.assertEqual(result["before"]["segments"], [
            {"text": '<b>old & "quoted"</b>\r\n', "label": "removed"},
            {"text": "keep\n", "label": "retained"}, {"text": "last", "label": "removed"}])
        self.assertEqual(result["current"]["segments"], [
            {"text": '<b>new & "quoted"</b>\r\n', "label": "added"},
            {"text": "keep\n", "label": "retained"}, {"text": "last\n", "label": "added"}])
        self.assertEqual([s["label"] for s in result["reference"]["actual_segments"]],
                         ["stale", "retained", "stale"])

    def test_duplicate_lines_disable_autojunk(self):
        repeated = "duplicate\n" * 250
        result = memory_comparison(summary("old\n" + repeated), summary("new\n" + repeated), repeated)
        self.assert_lossless(result)
        self.assertEqual(result["current"]["segments"], [
            {"text": "new\n", "label": "added"}, {"text": repeated, "label": "retained"}])
        self.assertEqual(result["reference"]["actual_segments"], [
            {"text": "new\n", "label": "stale"}, {"text": repeated, "label": "retained"}])

    def test_lossless_combinations_of_duplicates_blank_lines_and_endings(self):
        samples = ("", "a", "a\n", "\n\n", "a\r\na\rb\n", "a\na\nb\na\n", "b\na\na", "λ<script>&\u2028tail")
        for before, current, expected in product(samples, repeat=3):
            self.assert_lossless(memory_comparison(summary(before), summary(current), expected))

    def test_inputs_are_unchanged_and_returned_records_are_detached(self):
        before = summary("old\n", members=["a"], status="fresh")
        current = summary("new\n", members=["b"], metadata={"nested": [1]})
        saved = deepcopy((before, current))
        result = memory_comparison(before, current, "expected\n")
        self.assertEqual((before, current), saved)
        result["before"]["summary"]["members"].append("other")
        result["current"]["summary"]["metadata"]["nested"].append(2)
        self.assertEqual((before, current), saved)
        result["reference"]["actual_segments"][0]["text"] = "changed"
        self.assertEqual(text(result["current"]["segments"]), current["content"])

    def test_invalid_content_is_not_coerced_into_fabricated_text(self):
        for args in (([], None), (summary(123), None), (None, summary(False)), (None, None, 123)):
            with self.subTest(args=args), self.assertRaises(TypeError):
                memory_comparison(*args)


if __name__ == "__main__":
    unittest.main()