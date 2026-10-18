"""Pure presentation projections; never authorize, mutate, or decide verification."""

import json

from .verifier import report_problem


GROUPS = (("sources", "SOURCE", "square"), ("facts", "FACT", "circle"), ("summaries", "SUMMARY", "diamond"))


def entity_options(state):
    return [group + "." + key for group, _, _ in GROUPS for key in sorted(getattr(state, group))]


def verification_state(report, snapshot, dataset=None):
    if report is None:
        return "NOT RUN", "No independent verification has executed for this state."
    problem = report_problem(report, dataset, snapshot)
    if problem:
        return "BLOCKED", problem
    if (report.get("revision"), report.get("generation"), report.get("dataset_id")) != (
            snapshot.revision, snapshot.generation, snapshot.dataset_id):
        return "NOT RUN", "OUT OF DATE: prior evidence is not for this snapshot. Run verification again."
    return report["status"], "%s required checks executed; %s mismatches." % (len(report["comparisons"]), len(report["mismatches"]))


def current_report(report, snapshot, dataset=None):
    return verification_state(report, snapshot, dataset)[0] in {"PASS", "FAIL"}


def mismatch_entity(path, state):
    parts = path.split(".")
    if len(parts) >= 3 and parts[0] in {group for group, _, _ in GROUPS}:
        if parts[1] in getattr(state, parts[0]):
            return ".".join(parts[:2])
    return None


def graph_records(state, selected, source_id, mismatches=()):
    """Project the actual snapshot into fixed lineage lanes, including inactive edges."""
    counts = [len(getattr(state, group)) for group, _, _ in GROUPS]
    height = max(280, max(counts, default=0) * 115 + 40)
    affected_facts = {fact for source, fact in state.support_edges if source == source_id}
    affected_summaries = {summary for fact, summary in state.composition_edges if fact in affected_facts}
    affected = {"sources." + source_id} | {"facts." + key for key in affected_facts} | {"summaries." + key for key in affected_summaries}
    mismatch_ids = {mismatch_entity(item["path"], state) for item in mismatches}
    nodes, positions = [], {}
    for column, (group, kind, shape) in enumerate(GROUPS):
        records = getattr(state, group)
        for row, key in enumerate(sorted(records)):
            entity = group + "." + key
            x, y = 90 + 280 * column, (row + 0.5) * (height - 50) / len(records) + 20
            positions[entity] = (x, y)
            status = records[key]["status"]
            flagged = entity in mismatch_ids
            nodes.append({
                "entity": entity, "id": key, "kind": kind, "shape": shape, "x": x, "y": y,
                "status": status, "affected": entity in affected, "selected": entity == selected,
                "note": status + (" / MISMATCH" if flagged else ""),
                "fill": "#fae9e7" if flagged else ("#e2e5df" if status in {"invalid", "revoked"} else "#ffffff"),
                "stroke": "#a42b27" if flagged else ("#8a5700" if status == "stale" else "#42554d"),
            })
    edges = []
    for kind, relationships, start_group, end_group in (
        ("SUPPORTED_BY", state.support_edges, "sources", "facts"),
        ("COMPOSED_OF", state.composition_edges, "facts", "summaries"),
    ):
        for start, end in relationships:
            a, b = start_group + "." + start, end_group + "." + end
            if a not in positions or b not in positions:
                continue  # Missing endpoints remain visible in the verifier's relationship evidence.
            x, y = positions[a]
            x2, y2 = positions[b]
            active = (state.sources[start]["status"] == "active" if kind == "SUPPORTED_BY" else
                      state.facts[start]["status"] == "valid" and start in state.summaries[end]["members"])
            edges.append({"from": start, "to": end, "relation": kind, "x": x + 24, "y": y,
                          "x2": x2 - 28, "y2": y2, "active": active,
                          "color": "#8a5700" if a in affected and b in affected and active else "#87948b"})
    return {"nodes": nodes, "edges": edges, "height": height}


def graph_spec(state, selected, source_id, mismatches=()):
    """Native Vega-Lite driven by the entity selector; no scripts or external URLs."""
    records = graph_records(state, selected, source_id, mismatches)
    height = records["height"]
    x = {"field": "x", "type": "quantitative", "scale": {"domain": [0, 740]}, "axis": None}
    y = {"field": "y", "type": "quantitative", "scale": {"domain": [height, 0]}, "axis": None}
    position = {"x": x, "y": y}
    return {
        "height": min(height, 530), "background": "#ffffff",
        # Streamlit's frontend mutates padding.bottom; numeric Vega-Lite padding
        # is legal in the schema but incompatible with this installed adapter.
        "padding": {"top": 12, "right": 12, "bottom": 12, "left": 12},
        "config": {"view": {"stroke": None}, "font": "monospace"},
        "layer": [
            {"data": {"values": records["edges"]}, "mark": {"type": "rule", "strokeWidth": 1.5},
             "encoding": {**position, "x2": {"field": "x2"}, "y2": {"field": "y2"},
                          "color": {"field": "color", "scale": None},
                          "strokeDash": {"condition": {"test": "datum.active", "value": [1, 0]}, "value": [5, 4]},
                          "tooltip": [{"field": field, "type": "nominal"} for field in ("from", "relation", "to", "active")]}},
            {"data": {"values": records["edges"]}, "mark": {"type": "point", "shape": "triangle-right", "filled": True, "size": 55},
             "encoding": {"x": {**x, "field": "x2"}, "y": {**y, "field": "y2"}, "color": {"field": "color", "scale": None}}},
            {"data": {"values": records["nodes"]}, "transform": [{"filter": "datum.affected || datum.selected"}],
             "mark": {"type": "point", "filled": False, "size": 2300, "strokeWidth": 1.5},
             "encoding": {**position, "shape": {"field": "shape", "scale": None},
                          "stroke": {"condition": {"test": "datum.selected", "value": "#202924"}, "value": "#b27819"}}},
            {"data": {"values": records["nodes"]},
             "mark": {"type": "point", "filled": True, "size": 1100, "strokeWidth": 2, "opacity": 1},
             "encoding": {**position, "shape": {"field": "shape", "scale": None}, "fill": {"field": "fill", "scale": None},
                          "stroke": {"field": "stroke", "scale": None},
                          "tooltip": [{"field": field, "type": "nominal"} for field in ("id", "kind", "note")]}},
            {"data": {"values": records["nodes"]}, "mark": {"type": "text", "dy": 40, "fontSize": 12, "fontWeight": 600, "limit": 185, "color": "#202924"},
             "encoding": {**position, "text": {"field": "id"}}},
            {"data": {"values": records["nodes"]}, "mark": {"type": "text", "dy": 58, "fontSize": 11, "color": "#56615b", "limit": 190},
             "encoding": {**position, "text": {"field": "note"}}},
        ],
    }


def evidence_rows(report, query="", mismatches_only=False):
    def display(value):
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    result = []
    for comparison in report.get("comparisons", []):
        row = {"comparison": comparison["path"], "result": "MATCH" if comparison["match"] else "MISMATCH",
               "expected": display(comparison["expected"]), "actual": display(comparison["actual"])}
        if mismatches_only and comparison["match"]:
            continue
        if query.casefold() not in " ".join(row.values()).casefold():
            continue
        result.append(row)
    return result