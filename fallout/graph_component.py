"""Local, optional Streamlit v1 graph; projections and event validation stay pure.

Integration contract (no workbench integration is performed here):
* Use a stable widget key, never a revision/context-derived key.
* Supply a context string identifying dataset, generation, displayed snapshot/view,
  and revision (also source/evidence identity if those change the displayed graph).
* Call render_graph BEFORE the entity selectbox. Validate its raw return against
  that same context and entity_options(displayed_state). None means no selection.
* Streamlit retains component values: consume each (context, sequence) only once
  in session state, BEFORE assigning entity_id. Reject older sequences in the same
  context too. Rerun after a changed selection to echo it back into the graph.
* Selection only changes inspection, never the engine or authorization state.

The frontend preserves camera/local selection while its iframe stays mounted;
changing context resets both. It emits values only for user node activation.
"""

from pathlib import Path

from .presentation import entity_options, graph_records


_component = None
_MAX_SEQUENCE = 2**53 - 1  # JSON integers exactly representable by JavaScript.


def graph_payload(state, selected, source_id, mismatches=(), context=""):
    """Copy the real presentation projection, with explicit endpoint/support IDs.

    Mismatches must already be current, independently verified evidence supplied
    by the caller. This layer neither verifies evidence nor fabricates statuses.
    Active edge counts and recorded active_support are deliberately separate:
    corrupted records must not be silently repaired by a presentation projection.
    """
    if not isinstance(context, str):
        raise TypeError("Graph context must be a string")
    records = graph_records(state, selected, source_id, mismatches)
    for edge in records["edges"]:
        start, end = (("sources", "facts") if edge["relation"] == "SUPPORTED_BY"
                      else ("facts", "summaries"))
        edge["source_entity"] = start + "." + edge["from"]
        edge["target_entity"] = end + "." + edge["to"]
    for node in records["nodes"]:
        if node["kind"] == "FACT":
            support = [edge for edge in records["edges"]
                       if edge["relation"] == "SUPPORTED_BY" and edge["target_entity"] == node["entity"]]
            node["support_sources"] = sorted({edge["from"] for edge in support})
            node["active_edge_sources"] = sorted({edge["from"] for edge in support if edge["active"]})
            node["recorded_active_support"] = list(state.facts[node["id"]]["active_support"])
    return {**records, "context": context,
            "selected": selected if selected in entity_options(state) else None,
            "height": max(330, records["height"])}


def validated_selection(value, context, options):
    """Return an allowed entity ID or None; never mutate or consume the event.

    The caller owns context identity and replay protection. This rejects malformed
    events, stale contexts and entities outside the currently displayed snapshot.
    A positive safe integer sequence is required (bool is not an integer here).
    """
    if not isinstance(value, dict) or set(value) != {"entity", "context", "sequence"}:
        return None
    if not isinstance(context, str) or not isinstance(value["context"], str) or value["context"] != context:
        return None
    if not isinstance(value["entity"], str):
        return None
    sequence = value["sequence"]
    if type(sequence) is not int or not 1 <= sequence <= _MAX_SEQUENCE:
        return None
    return value["entity"] if value["entity"] in options else None


def render_graph(state, selected, source_id, mismatches=(), context="", key="lineage_graph"):
    """Return the raw component value (initially None); no callback/session edits.

    Optional Streamlit import and local declaration happen only on first render.
    Pure/core-only imports do not need Streamlit or open a component server.
    """
    payload = graph_payload(state, selected, source_id, mismatches, context)
    global _component
    if _component is None:
        from streamlit.components.v1 import declare_component

        _component = declare_component("fallout_lineage_graph", path=str(Path(__file__).with_suffix("")))
    return _component(payload=payload, key=key, default=None)