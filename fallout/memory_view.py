"""Read-only, lossless summary projections. No UI or verification side effects."""

from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any, Dict, Optional


def _plain(content):
    return [] if not content else [{"text": content, "label": "current"}]


def _diff(left, right, removed="removed"):
    """Return ordered segments on both sides of a line-level comparison."""
    left_lines = left.splitlines(keepends=True)
    right_lines = right.splitlines(keepends=True)
    left_segments, right_segments = [], []
    matcher = SequenceMatcher(None, left_lines, right_lines, autojunk=False)
    for tag, i, j, k, end in matcher.get_opcodes():
        if i != j:
            left_segments.append({"text": "".join(left_lines[i:j]),
                                  "label": "retained" if tag == "equal" else removed})
        if k != end:
            right_segments.append({"text": "".join(right_lines[k:end]),
                                   "label": "retained" if tag == "equal" else "added"})
    return left_segments, right_segments


def _view(summary):
    if summary is not None and not isinstance(summary, dict):
        raise TypeError("summary must be a dict or None")
    content = summary.get("content") if summary is not None else None
    if content is not None and not isinstance(content, str):
        raise TypeError("summary content must be a string or None")
    return {"summary": deepcopy(summary), "missing": summary is None,
            "content_missing": content is None, "content": content,
            "segments": _plain(content)}


def memory_comparison(before: Optional[dict], current: Optional[dict],
                      expected_content: Optional[str] = None) -> Dict[str, Any]:
    """Project snapshot changes separately from an optional content reference.

    ``before`` / ``current`` contain a detached ``summary``, ``missing`` (record
    is None), ``content_missing`` (absent/None content), exact ``content``, and
    ``segments`` of {text, label}. Empty strings are present content, not missing
    records. Missing content stays None, with no fabricated text or segments.

    ``compared`` is true only when both snapshot contents exist. Otherwise any
    available text is plain ``current``: absence is not evidence of an edit.
    Comparisons label equal lines retained, old lines removed, new lines added.
    Partial-line edits replace the entire line, including its original ending.

    ``verified_reference`` means expected_content was supplied (even ""), NOT
    that verification passed. The caller must obtain a valid, current verifier
    reference; this helper cannot establish its provenance or freshness.
    ``reference`` is None without it; otherwise it holds ``actual_content``,
    ``expected_content``, ``actual_missing``, ``compared``, ``actual_segments``
    and ``expected_segments``. This independent actual-to-expected comparison
    labels only actual delete/replace chunks stale, equal chunks retained, and
    expected insert/replace chunks added. Missing actual content is not treated
    as empty: reference.compared is false and expected text stays plain current.

    Nonempty segment texts concatenate exactly to each available original.
    No escaping, normalization, input mutation, or status-based inference occurs.
    """
    if expected_content is not None and not isinstance(expected_content, str):
        raise TypeError("expected_content must be a string or None")
    prior, actual = _view(before), _view(current)
    compared = not prior["content_missing"] and not actual["content_missing"]
    if compared:
        prior["segments"], actual["segments"] = _diff(prior["content"], actual["content"])

    reference = None
    if expected_content is not None:
        actual_segments = _plain(actual["content"])
        expected_segments = _plain(expected_content)
        if not actual["content_missing"]:
            actual_segments, expected_segments = _diff(actual["content"], expected_content,
                                                       removed="stale")
        reference = {"actual_content": actual["content"], "expected_content": expected_content,
                     "actual_missing": actual["content_missing"],
                     "compared": not actual["content_missing"],
                     "actual_segments": actual_segments, "expected_segments": expected_segments}
    return {"before": prior, "current": actual, "compared": compared,
            "verified_reference": expected_content is not None, "reference": reference}