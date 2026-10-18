"""Guided Streamlit presentation; backend and independent verifier remain authoritative."""

from html import escape
from base64 import b64encode
import json
from uuid import uuid4

import streamlit as st

from . import verifier
from .ai_assistant import safe_graph
from .ai_provider import AISettings
from .ai_ui import render_ai
from .config import Config, create_backend
from .engine import Engine, FAULTS, timestamp
from .graph_component import render_graph, validated_selection
from .lab import run_lab
from .memory_view import memory_comparison
from .models import FalloutError, ROOT, digest, initial_state
from .presentation import current_report, entity_options, evidence_rows, mismatch_entity, verification_state
from .scenarios import get_scenario
from .workflow_state import workflow_state


VERSION = "0.4.0"
INVESTIGATE = "Investigate & Repair"
SCENARIO_LABELS = {"pricing": "Pricing withdrawal",
                   "documentation": "Technical documentation withdrawal"}

# Durable UI selections as well as engine evidence belong to a scenario. Button
# pulses and component messages are deliberately not replayable state.
SCENARIO_KEYS = {
    "engine", "source_id", "source_choice", "fault", "fault_choice", "section",
    "verification", "verification_history", "verification_id", "impact_preview",
    "before_snapshot", "activity_history", "operation_id", "reset_preview",
    "reset_ack", "reset_error", "reset_widgets", "pending_entity", "entity_id",
    "graph_view", "graph_fallback", "lineage_graph_seen", "evidence_query",
    "mismatches_only", "comparison_path", "relationship_entity", "audit_operation",
    "history_operation", "lab_history", "lab_run", "chat",
}
AI_ACTION_KEYS = {"ai_request", "ai_review"}

LOGO = '<svg viewBox="0 0 28 28"><path d="M4 6L14 14 4 23M14 14L24 5M14 14L24 23" fill="none" stroke="#A9B5AE" stroke-width="1.75"/><g fill="#171C1A" stroke="#E8EDE9" stroke-width="1.75"><circle cx="4" cy="6" r="2.5"/><circle cx="4" cy="23" r="2.5"/><circle cx="24" cy="5" r="2.5"/><circle cx="24" cy="23" r="2.5"/></g><circle cx="14" cy="14" r="3.5" fill="#7FD1A0"/></svg>'
# Streamlit sanitizes inline SVG out of st.html; an embedded image is supported.
LOGO = '<img class="fo-logo" role="img" alt="FALLOUT connected dependency logo" width="28" height="28" src="data:image/svg+xml;base64,' + b64encode(LOGO.replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ', 1).encode()).decode() + '">'


def presentation_state(engine, snapshot):
    return workflow_state(engine.dataset, snapshot, st.session_state.get("verification"),
                          st.session_state.get("impact_preview"), st.session_state.source_id,
                          st.session_state.get("operation_id"), st.session_state.get("verification_history", []))


def workflow(engine, snapshot):
    state = presentation_state(engine, snapshot)
    st.html('<ol class="fo-workflow" aria-label="Recorded workflow progress">' + ''.join(
        '<li class="' + ('done' if step['done'] else 'current' if step['current'] else '') + '"' +
        (' aria-current="step"' if step['current'] else '') + '><span>' +
        ('✓' if step['done'] else str(index + 1)) + '</span>' + escape(step['label']) + '</li>'
        for index, step in enumerate(state['steps'])) + '</ol>')


def state_banners(engine, snapshot):
    state = presentation_state(engine, snapshot)
    for tone, title, detail in state["banners"]:
        st.html('<div class="fo-stage ' + tone + '" role="status"><b>' + escape(title) +
                '</b><span>' + escape(detail) + '</span></div>')
    if state["banners"]:
        status_strip(snapshot, engine.dataset)
    for failure in state["earlier_failures"][-1:]:
        st.html('<div class="fo-stage historical"><b>EARLIER VERIFICATION · FAIL</b><span>Revision ' +
                str(failure["revision"]) + ' · ' + str(failure["mismatch_count"]) +
                ' mismatches. Preserved in session history; not the current verdict.</span></div>')


def scenario_keys():
    return SCENARIO_KEYS | {key for key in st.session_state
                            if key.startswith("ai_") and key not in AI_ACTION_KEYS}


def active_scenario():
    return get_scenario(st.session_state.get("active_scenario", "pricing"))


def switch_scenario():
    """Bank the old session before Streamlit renders any new-dataset widgets.

    Keep engines by reference (locks/grants must not be copied), and retain all
    evidence and UI selections. No reset or repair is authorized by switching.
    Consent and reset acknowledgment always require a fresh explicit action.
    """
    selected = get_scenario(st.session_state.scenario_choice).id
    previous = st.session_state.get("active_scenario", "pricing")
    if selected == previous:
        return
    banks = st.session_state.setdefault("scenario_banks", {})
    keys = scenario_keys()
    banks[previous] = {key: st.session_state[key] for key in keys if key in st.session_state}
    for key in keys | {"lineage_graph"}:
        st.session_state.pop(key, None)
    for key, value in banks.get(selected, {}).items():
        st.session_state[key] = value
    st.session_state.active_scenario = selected
    st.session_state.ai_consent = False
    st.session_state.ai_clear_consent = True
    st.session_state.reset_ack = False


def repair_pending(operation):
    return operation["status"] != "repaired" or operation.get("false_success_pending", False)


def repair_progress(progress, operation):
    if operation.get("false_success_pending", False):
        progress.update(label="REPAIR / REPORTED COMPLETION · SKIPPED REBUILD · UNVERIFIED", state="error")
    else:
        progress.update(label="REPAIR / " + operation["status"].upper(),
                        state="error" if repair_pending(operation) else "complete")


def completion_warning(operation):
    if operation.get("false_success_pending", False):
        st.warning("The operation reports completion, but a summary rebuild was skipped: " +
                   str(operation.get("skipped_summary")) +
                   ". That completion claim is unverified, not proof of repair. Check the independent result and retry without a mistake.")


def record_activity(stage, snapshot, outcome, source_id=None, **details):
    """Actual UI actions only; session-local, not an immutable security audit."""
    history = st.session_state.setdefault("activity_history", [])
    history.append({"id": uuid4().hex, "at": timestamp(), "stage": stage,
                    "outcome": outcome, "source": source_id,
                    "generation": snapshot.generation, "revision": snapshot.revision, **details})


def ui_verdict(report, snapshot, dataset):
    verdict, reason = verification_state(report, snapshot, dataset)
    return ("ERROR" if verdict == "BLOCKED" else verdict), reason


def ai_status():
    settings = AISettings.from_environment()
    if not settings.enabled:
        return "AI OFF"
    if settings.problem:
        return "AI NOT READY"
    signature = (settings.enabled, settings.provider, settings.public_model,
                 settings.free_tier_confirmed, hash(settings.api_key))
    if st.session_state.get("ai_configuration") == signature:
        result = st.session_state.get("ai_result", {})
        if result.get("status") == "unavailable":
            return "AI LAST REQUEST FAILED"
        if result.get("provenance") == "mock":
            return "AI MOCK RESULT"
        if st.session_state.get("ai_live_tested"):
            return "AI LIVE REQUEST SUCCEEDED"
    return "AI CONFIGURED · NOT LIVE-TESTED"


def header(engine, snapshot):
    brand, navigation, scenario, status = st.columns([1, 1.4, 2.25, 1.4], gap="small", vertical_alignment="top")
    with brand:
        st.html('<div class="fo-mast"><div class="fo-brand">' + LOGO + '<div><h1>FALLOUT</h1></div></div></div>')
    with navigation:
        st.radio("Main navigation", ["Overview", INVESTIGATE, "Verify & Test"], horizontal=True,
                 format_func=lambda value: {INVESTIGATE: "Repair", "Verify & Test": "Verify"}.get(value, value),
                 key="section", label_visibility="collapsed")
    with scenario:
        with st.container(key="scenario_bar"):
            st.html('<div class="fo-scenario-label">ACTIVE SCENARIO</div>')
            st.selectbox("Scenario", list(SCENARIO_LABELS), format_func=SCENARIO_LABELS.__getitem__,
                         key="scenario_choice", on_change=switch_scenario, label_visibility="collapsed")
    with status:
        with st.container(key="header_status_group"):
            status_strip(snapshot, engine.dataset, compact=True)
            st.html('<div class="fo-header-state">' + escape(ai_status()) + '</div>')


def section(number, label):
    st.html('<div class="fo-section">' + escape(number + " / " + label.upper()) + '</div>')


def select_entity(entity):
    """UI-only selection; never changes graph state or mutation authorization."""
    st.session_state.pending_entity = entity
    st.session_state.graph_view = "Current state"


def reset_session(engine):
    """Button callback runs before rendering, avoiding stale widget trees on reset."""
    try:
        engine.reset_demo(st.session_state.reset_preview, confirmed=st.session_state.reset_ack)
        for key in ("reset_preview", "impact_preview", "operation_id", "verification", "verification_history", "verification_id", "activity_history", "chat", "before_snapshot", "pending_entity", "ai_result", "ai_review_error", "lineage_graph_seen", "comparison_path", "relationship_entity", "audit_operation", "history_operation", "ai_source", "ai_memory"):
            st.session_state.pop(key, None)
        st.session_state.ai_consent = False
        st.session_state.ai_clear_consent = True
        st.session_state.reset_widgets = True
        st.session_state.pop("reset_error", None)
    except FalloutError as exc:
        st.session_state.reset_error = str(exc)


def run_verification(engine):
    """Record real verifier executions separately from repair events and graph state."""
    verification_id = uuid4().hex
    with st.status("RUNNING · independent verification", expanded=True) as progress:
        st.write("Reconstructing expected memory from original data and revocation history.")
        try:
            report = engine.verify_rebuild()
            snapshot = engine.backend.read()
        except Exception:
            report = verifier.blocked_report(engine.dataset, None,
                                            reason="Memory unavailable. Independent verification could not complete. No PASS is available.")
            st.session_state.verification = report
            st.session_state.verification_id = verification_id
            progress.update(label="VERIFICATION / ERROR", state="error")
            st.error("Database unavailable; no current PASS can be established. Refresh after restoring the connection.")
            st.stop()
        verdict, reason = ui_verdict(report, snapshot, engine.dataset)
        if verdict not in {"PASS", "FAIL"}:
            # Failed execution evidence must have a safe, countable shape too.
            # Never let malformed comparisons crash bookkeeping or retain PASS.
            report = verifier.blocked_report(engine.dataset, snapshot,
                                            reason="Independent verification did not produce complete, valid evidence. No PASS is available.")
            verdict, reason = ui_verdict(report, snapshot, engine.dataset)
        progress.update(label="VERIFICATION / " + verdict, state="complete" if verdict == "PASS" else "error")
    st.session_state.verification = report
    st.session_state.verification_id = verification_id
    history = st.session_state.get("verification_history", [])
    history.append({"id": verification_id, "status": verdict, "reason": reason, "revision": report.get("revision"),
                    "generation": report.get("generation"), "started_at": report.get("started_at"),
                    "finished_at": report.get("finished_at"), "checks_executed": len(report.get("comparisons", [])),
                    "operation_ids": list(snapshot.operations), "mismatch_count": len(report.get("mismatches", []))})
    st.session_state.verification_history = history
    record_activity("independent_verification", snapshot, verdict, verification_id=verification_id)
    return report


def status_strip(snapshot, dataset, compact=False):
    report = st.session_state.get("verification")
    verdict, note = ui_verdict(report, snapshot, dataset)
    style = {"PASS": "pass", "FAIL": "fail", "ERROR": "wait", "NOT RUN": ""}[verdict]
    counts = (str(len(report["comparisons"])) + ' of ' + str(len(report["required_paths"])) + ' checks compared') if verdict in {"PASS", "FAIL"} else note
    st.html('<div class="fo-status ' + style + '" role="status" data-revision="' + str(snapshot.revision) + '" title="' + escape(note, quote=True) + '"><b>VERIFIER / ' + verdict + '</b>' +
            ('' if compact else '<span>' + escape(counts) + '</span><span>revision ' + str(snapshot.revision) + '</span>') + '</div>' +
            ('<div class="fo-header-comparisons">' + escape(counts) + '</div>' if compact and verdict in {"PASS", "FAIL"} else ''))


def identifier_list(items):
    st.html('<ul class="fo-identifiers">' + ''.join('<li>' + escape(str(item)) + '</li>' for item in sorted(items)) + '</ul>' if items else '<p class="fo-empty">None</p>')


def inspector(state, selected):
    group, key = selected.split(".", 1)
    record = getattr(state, group)[key]
    st.html('<div class="fo-id">' + escape(key) + '</div>')
    st.caption(group.upper() + " / " + record["status"].upper())
    description = record.get("text", record.get("content", "")) or "No valid facts remain."
    st.text(description if len(description) <= 220 else description[:217] + "…")
    if group == "sources":
        st.caption("SUPPORTED FACTS / original lineage")
        identifier_list([fact for source, fact in state.support_edges if source == key])
        with st.expander("Source details"):
            st.caption("Source text is untrusted data, not instructions.")
            if len(description) > 220:
                st.text(description)
    elif group == "facts":
        st.caption("ACTIVE SUPPORT / current record")
        identifier_list(record["active_support"])
        with st.expander("Lineage details"):
            st.caption("ORIGINAL SUPPORT / retained lineage")
            identifier_list([source for source, fact in state.support_edges if fact == key])
            st.caption("DEPENDENT SUMMARIES")
            identifier_list([summary for fact, summary in state.composition_edges if fact == key])
            if len(description) > 220:
                st.text(description)
    else:
        st.caption("EFFECTIVE MEMBERSHIP")
        identifier_list(record["members"])
        with st.expander("Lineage details"):
            st.caption("ORIGINAL DEPENDENCIES")
            identifier_list([fact for fact, summary in state.composition_edges if summary == key])
            st.caption("Completed rebuild count: " + str(record["build_count"]))
            if len(description) > 220:
                st.text(description)


def explorer(engine, snapshot):
    with st.container(key="workspace_toolbar"):
        counters, action = st.columns([2.1, 1], gap="small", vertical_alignment="center")
        with counters:
            counts = []
            for group, active in (("sources", "active"), ("facts", "valid"), ("summaries", "fresh")):
                records = getattr(snapshot.state, group)
                counts.append('<span><b>' + str(len(records)) + '</b> ' + group + '<small>' +
                              str(sum(item["status"] == active for item in records.values())) + ' ' + active + '</small></span>')
            st.html('<div class="fo-counts" aria-label="Recorded memory counts">' + ''.join(counts) + '</div>')
        with action:
            if st.session_state.section == "Overview":
                st.button("Investigate source change", type="primary", key="try_demo", on_click=go_to_repair, width="stretch")
            else:
                st.caption("Memory connections · read only")
    controls, detail = st.columns([2.1, 1], gap="small")
    with controls, st.container(key="graph_panel"):
        with st.container(key="snapshot_toolbar"):
            view = st.radio("Graph snapshot", ["Current state", "Before last operation", "Original dataset"], horizontal=True, key="graph_view", label_visibility="collapsed")
        state = snapshot.state
        label = "Current revision " + str(snapshot.revision)
        if view == "Before last operation":
            before = st.session_state.get("before_snapshot")
            if before and before.generation == snapshot.generation and before.dataset_id == snapshot.dataset_id:
                state = before.state
                label = "Captured before confirmation · revision " + str(before.revision)
            else:
                st.info("No confirmed operation yet. Showing current state, not a fabricated before snapshot.")
        elif view == "Original dataset":
            state = initial_state(engine.dataset)
            label = "Original input projection · not the last-operation snapshot"
        options = entity_options(state)
        pending = st.session_state.pop("pending_entity", None)
        if pending in options:
            st.session_state.entity_id = pending
        if not options:
            st.warning("No entities exist in this snapshot. Run verification to inspect missing nodes.")
            return
        if st.session_state.get("entity_id") not in options:
            preferred = "sources." + st.session_state.source_id
            st.session_state.entity_id = preferred if preferred in options else options[0]
        selected = st.session_state.entity_id
        report = st.session_state.get("verification")
        mismatches = report["mismatches"] if view == "Current state" and current_report(report, snapshot, engine.dataset) else []
        preview = st.session_state.get("impact_preview")
        current_preview = bool(preview and view == "Current state" and
                               preview["source_id"] == st.session_state.source_id and
                               preview["revision"] == snapshot.revision and
                               preview["generation"] == snapshot.generation and
                               preview["dataset_id"] == snapshot.dataset_id)
        impact_source = preview["source_id"] if current_preview else ""
        st.html('<div class="fo-snapshot-note"><b>' + escape(label) + '</b><span>Recorded state · not a proposed repair</span></div>')
        graph_failed = False
        try:
            context = digest([snapshot.dataset_id, snapshot.generation, snapshot.revision,
                              view, label, impact_source, mismatches])
            value = render_graph(state, selected, impact_source, mismatches,
                                 context=context, key="lineage_graph")
            entity = validated_selection(value, context, options)
            if entity is not None:
                seen = st.session_state.setdefault("lineage_graph_seen", {})
                if value["sequence"] > seen.get(context, 0):
                    seen[context] = value["sequence"]
                    if entity != selected:
                        st.session_state.entity_id = entity
                        st.rerun()
        except Exception:
            graph_failed = True
            st.warning("Graph view unavailable. Use the relationship records and entity inspector below. Memory has not changed.")
        if graph_failed:
            with st.expander("Relationship records / accessible graph alternative", expanded=True):
                st.dataframe([{"from": source, "relationship": "SUPPORTED_BY", "to": fact} for source, fact in state.support_edges] +
                             [{"from": fact, "relationship": "COMPOSED_OF", "to": summary} for fact, summary in state.composition_edges],
                             hide_index=True, width="stretch")
    with detail:
        with st.container(key="entity_inspector"):
            st.caption("ENTITY INSPECTOR / READ ONLY")
            st.selectbox("Inspect entity", options, key="entity_id", help="Search by typing a source, fact or summary ID. Also available when graph rendering fails.")
            inspector(state, st.session_state.entity_id)
            st.caption("Inspecting: " + st.session_state.entity_id +
                       (" · Amber previews withdrawal of " + impact_source + ". Inspection does not change the preview target." if current_preview else
                        " · No active preview. Inspection does not change memory."))
            with st.expander("Inspection help · read only"):
                st.caption("Inspector follows the selected graph snapshot.")
                st.caption("Click a node or use the searchable selector. Tab + Enter also selects a node. Inspection never authorizes revocation.")


def analyze(engine, snapshot):
    st.header(INVESTIGATE)
    st.write("See what depends on a source before approving its withdrawal.")
    st.selectbox("Source", sorted(snapshot.state.sources), key="source_choice",
                 on_change=remember_source)
    source = st.session_state.source_id
    with st.expander("About this source"):
        st.text(snapshot.state.sources[source]["text"])
        st.caption(active_scenario().description)
    if st.session_state.fault != "none":
        st.warning("A deliberate mistake is enabled: " + FAULT_LABELS[st.session_state.fault] +
                   ". Change it in Verify & Test before confirming if you want a normal repair.")
    if st.button("Preview changes", key="preview", type="primary"):
        try:
            st.session_state.impact_preview = engine.preview_impact(source)
            record_activity("impact_analysis", snapshot, "PREVIEW ONLY", source,
                            preview_id=st.session_state.impact_preview["preview_id"])
            st.rerun()
        except FalloutError as exc:
            st.error(str(exc))
            record_activity("impact_analysis", snapshot, "REJECTED", source)
    explorer(engine, snapshot)
    if st.session_state.get("operation_id"):
        repair_result(engine, snapshot)
    preview = st.session_state.get("impact_preview")
    if preview:
        current = (preview["source_id"] == source and preview["revision"] == snapshot.revision
                   and preview["generation"] == snapshot.generation and preview["dataset_id"] == snapshot.dataset_id)
        st.subheader("Impact · proposed changes")
        st.info("Preview only — nothing has changed. These are expected outcomes, not repair results.")
        st.html('<div class="fo-impact"><b>PROPOSED IMPACT</b><span>' + str(len(preview["preserved_facts"])) +
            ' facts retain support</span><span>' + str(len(preview["invalidated_facts"])) +
            ' lose support</span><span>' + str(len(preview["affected_summaries"])) + ' summaries to rebuild</span></div>')
        rows = [{"Memory": snapshot.state.facts[fact]["text"],
                 "After removing " + preview["source_id"]: "Stay" if preview["remaining_support"][fact] else "Remove from usable memory",
                 "Why": "Still supported by " + ", ".join(preview["remaining_support"][fact])
                 if preview["remaining_support"][fact] else "No supporting source would remain"}
                for fact in preview["affected_facts"]]
        with st.expander("Impact details · facts and proposed summaries"):
            st.dataframe(rows, hide_index=True, width="stretch")
            if not rows:
                st.write("No facts depend on this source.")
            for summary in preview["affected_summaries"]:
                st.caption("Summary ID: " + summary)
                proposed = sorted({fact for fact, target in snapshot.state.composition_edges if target == summary
                                   and snapshot.state.facts[fact]["status"] == "valid" and fact not in preview["invalidated_facts"]})
                st.markdown("**The summary would be rebuilt using only the remaining usable facts:**")
                st.text("\n".join(snapshot.state.facts[fact]["text"] for fact in proposed) or "Empty summary — no supported facts remain.")
            if not preview["affected_summaries"]:
                st.write("No summary needs to change.")
        with st.expander("Advanced details · exact preview"):
            st.json(preview)
        st.subheader("Repair · review and approve")
        st.write("Withdraw **" + preview["source_id"] + "**, repair affected memory, then independently verify.")
        if not current:
            st.warning("This preview is out of date or the selected source changed. Preview changes again before confirming.")
        if st.button("Confirm removal and repair", disabled=not current, type="primary", key="confirm") and current:
            try:
                before = engine.backend.read()
                token = engine.confirm_preview(preview)
                record_activity("explicit_approval", before, "APPROVED", preview["source_id"], preview_id=preview["preview_id"])
                with st.status("RUNNING · authorized repair", expanded=True) as progress:
                    operation = engine.revoke_source(preview["source_id"], token)
                    st.session_state.before_snapshot = before
                    st.session_state.operation_id = operation["id"]
                    st.write("Source revocation committed · operation " + operation["id"])
                    operation = engine.repair_memory(operation["id"], st.session_state.fault)
                    st.dataframe([{"step": key, "outcome": value["status"]} for key, value in operation["steps"].items()], hide_index=True)
                    repair_progress(progress, operation)
                run_verification(engine)
                st.session_state.pop("impact_preview", None)
                st.rerun()
            except FalloutError as exc:
                st.error(str(exc))
                record_activity("repair_request", engine.backend.read(), "REJECTED", source)
    with st.expander("Advanced details · operation and audit evidence"):
        operations(engine, snapshot)


FAULT_LABELS = {"none": "No deliberate mistake", "skip_invalidation": "Leave an unsupported fact usable",
                "skip_summary": "Leave the old summary unchanged", "raise_during_repair": "Interrupt the repair",
                "false_success": "Skip one rebuild but report completion"}


def remember_source():
    st.session_state.source_id = st.session_state.source_choice


def remember_fault():
    # Keep the chosen experiment across navigation: Streamlit removes hidden widget keys.
    st.session_state.fault = st.session_state.fault_choice


def go_to_repair():
    st.session_state.section = INVESTIGATE


def overview(engine, snapshot):
    st.html('<div class="fo-intro"><h2>A completed repair can still be wrong.</h2><p>Trace the change. Repair the memory. Check the result independently.</p></div>')
    explorer(engine, snapshot)
    memory, recent = st.columns([2.1, 1], gap="small")
    with memory, st.container(key="recorded_memory"):
        recorded_memory(engine, snapshot)
    with recent, st.container(key="recent_operation"):
        st.subheader("Recent operation")
        if snapshot.operations:
            operation = list(snapshot.operations.values())[-1]
            st.write(operation["source_id"] + " · " + operation["status"].upper())
            completion_warning(operation)
            with st.expander("Operation details"):
                st.code(operation["id"], language=None)
                st.caption("Created " + operation["created_at"] + " · attempts " + str(operation["attempts"]))
        else:
            st.info("No operation yet. Selecting nodes and previewing impact do not change memory.")
    with st.expander("What FALLOUT proves · scope and limits"):
        st.html('<p>When evidence changes, AI memory can retain facts and summaries that are no longer supported. FALLOUT traces the dependencies, repairs affected memory, and independently checks whether the result matches the expected state.</p>')
        st.caption("Here, verification means consistency with original evidence and withdrawal history—not proof of real-world truth. Source withdrawal is supported; general source editing is not.")


def memory_segments(segments):
    """Escape exact saved text; labels describe comparisons, never verification."""
    labels = {"retained": "Retained", "removed": "Removed", "added": "Added", "stale": "Left stale", "current": "Recorded"}
    st.html('<div class="fo-memory-lines">' + ''.join(
        '<div class="fo-memory-line ' + part["label"] + '"><span class="fo-diff-label">' + labels[part["label"]] +
        '</span><span class="fo-diff-text">' + escape(part["text"]) + '</span></div>' for part in segments) + '</div>')


def recorded_memory(engine, snapshot):
    """Before/after uses saved snapshots; stale markings need current verification."""
    st.subheader("Recorded memory")
    before = st.session_state.get("before_snapshot")
    if before and (before.dataset_id != snapshot.dataset_id or before.generation != snapshot.generation):
        before = None
    report = st.session_state.get("verification")
    references = {row["path"]: row["expected"] for row in report["comparisons"]} if current_report(report, snapshot, engine.dataset) else {}
    keys = sorted(set(snapshot.state.summaries) | (set(before.state.summaries) if before else set()))
    for key in keys:
        summary = snapshot.state.summaries.get(key)
        old = before.state.summaries.get(key) if before else None
        diff = memory_comparison(old, summary, references.get("summaries." + key + ".content"))
        st.caption(key + " · " + (summary["status"].upper() if summary else "MISSING"))
        if before:
            left, right = st.columns(2, gap="small")
            with left:
                st.caption("BEFORE / saved before withdrawal")
                if old is None:
                    st.write("No summary in the earlier snapshot.")
                else:
                    memory_segments(diff["before"]["segments"])
                    if old["content"] == "":
                        st.write("Empty summary")
            with right:
                st.caption("NOW / actual recorded memory")
                if summary is None:
                    st.warning("Summary missing from recorded memory.")
                else:
                    reference = diff["reference"]
                    segments = reference["actual_segments"] if reference and any(p["label"] == "stale" for p in reference["actual_segments"]) else diff["current"]["segments"]
                    memory_segments(segments)
                    if summary["content"] == "":
                        st.write("Empty summary")
            with st.expander("Exact saved text · " + key):
                st.caption("BEFORE")
                st.text(old["content"] if old else "Missing summary")
                st.caption("NOW")
                st.text(summary["content"] if summary else "Missing summary")
        elif summary:
            st.text(summary["content"] or "No facts in this summary.")
        if summary and summary["status"] != "fresh":
            st.warning("This saved summary is out of date. Investigate the incomplete repair.")
    if before:
        st.caption("Before/after compares saved text. Left stale is shown only when current independent evidence identifies extra or differing content.")


def check_result(engine, snapshot):
    report = st.session_state.get("verification")
    verdict, reason = ui_verdict(report, snapshot, engine.dataset)
    st.markdown("**Independent check**")
    if verdict in {"PASS", "FAIL"}:
        st.html('<div class="fo-verification ' + verdict.lower() + '"><strong>' + verdict +
                '</strong><span><b>' + str(len(report["comparisons"])) + '</b> executed comparisons</span><span><b>' +
                str(len(report["mismatches"])) + '</b> mismatches</span><small>Original evidence → independent reconstruction → recorded state</small></div>')
    if verdict == "PASS":
        st.success("PASS — The saved memory matches the independently rebuilt result.")
        with st.expander("How this result was established"):
            st.write("The checker started again from the original sources and the record of removals, without trusting the repair's success message. All " +
                     str(len(report["comparisons"])) + " required checks matched, including support, usable facts, summary contents and repair records.")
    elif verdict == "FAIL":
        st.error("FAIL — The saved memory does not match the independently rebuilt result.")
        paths = [item["path"] for item in report["mismatches"]]
        with st.expander("Why this result failed"):
            st.write("The checker found " + str(len(report["mismatches"])) + " differences. A repair attempt is not proof that the memory is correct.")
            if any(path.startswith("summaries.") for path in paths):
                st.write("Why: the saved summary's content, included facts or freshness differs from what the remaining sources require.")
            if any(path.startswith("facts.") for path in paths):
                st.write("Why: a saved fact or its support differs from what the remaining sources justify.")
            if any(path.startswith("operations.") for path in paths):
                st.write("Why: the recorded repair has not completed all required steps.")
        for mismatch in report["mismatches"]:
            if mismatch["path"].startswith("summaries.") and mismatch["path"].endswith(".content"):
                with st.container(key="content_mismatch_" + mismatch["path"]):
                    st.caption("STALE CONTENT / " + mismatch["path"])
                    expected, actual = st.columns(2)
                    with expected:
                        st.caption("EXPECTED / independent reference")
                        st.text(str(mismatch["expected"]) or "Empty summary")
                    with actual:
                        st.caption("ACTUAL / recorded memory")
                        st.text(str(mismatch["actual"]) or "Empty summary")
    elif verdict == "ERROR":
        st.error("ERROR — The checker could not establish a trustworthy result. " + reason)
        st.caption("No PASS is inferred. Retry the independent check; preserve the report if the problem persists. Core evidence status: BLOCKED.")
    else:
        st.info("NOT RUN — This version of the memory has not been independently checked.")
        if report:
            st.warning(reason)
    if verdict in {"PASS", "FAIL"}:
        st.caption(str(len(report["comparisons"])) + " comparisons · " + str(len(report["mismatches"])) +
                   " mismatches · verified revision " + str(report["revision"]))
    if st.session_state.get("verification_id"):
        with st.expander("Verification execution details"):
            st.caption("Verification ID: " + st.session_state.verification_id)


def repair_result(engine, snapshot):
    recorded_memory(engine, snapshot)
    check_result(engine, snapshot)
    with st.expander("Repair details · facts, lineage and execution"):
        repair_details(engine, snapshot)


def repair_details(engine, snapshot):
    operation = snapshot.operations.get(st.session_state.get("operation_id"))
    if not operation:
        return
    st.caption("4 / EXECUTE AND VERIFY · ACTUAL RESULT")
    st.subheader("What actually happened")
    st.write("Source **" + operation["source_id"] + "** is " +
             ("no longer used as evidence." if snapshot.state.sources[operation["source_id"]]["status"] == "revoked" else "still active."))
    completion_warning(operation)
    if repair_pending(operation):
        st.warning("The repair is incomplete. The facts and summary below show the actual saved state, including any mistakes.")
    state = snapshot.state
    st.markdown("**What stayed**")
    stayed = [state.facts[key] for key in operation["preview"]["affected_facts"] if key in state.facts and state.facts[key]["status"] == "valid"]
    for fact in stayed:
        st.text(fact["text"])
        st.caption("Recorded as usable · supporting sources: " + (", ".join(fact["active_support"]) or "none — this needs attention"))
    if not stayed:
        st.write("No affected facts are recorded as usable.")
    st.markdown("**What was removed from usable memory**")
    removed = [state.facts[key] for key in operation["preview"]["affected_facts"] if key in state.facts and state.facts[key]["status"] == "invalid"]
    for fact in removed:
        st.text(fact["text"])
    if not removed:
        st.write("No affected facts were marked unusable.")
    st.caption("Removed facts remain in the history for inspection; they are not erased.")
    st.markdown("**What was updated**")
    for key in operation["preview"]["affected_summaries"]:
        st.caption("Summary ID: " + key)
        summary = state.summaries.get(key)
        if summary is None:
            st.warning("An affected summary is missing from the saved memory.")
            continue
        old = operation["preview"]["before_summaries"][key]
        st.caption("BEFORE / " + old["status"] + " · members: " + ", ".join(old["members"]))
        st.text(old["content"] or "Empty summary — no facts included.")
        st.caption("AFTER / " + summary["status"] + " · members: " + ", ".join(summary["members"]))
        if summary["status"] != "fresh":
            st.warning("The summary is still out of date. It has not been successfully updated.")
        elif summary["content"] != old["content"] or summary["members"] != old["members"]:
            st.write("The saved summary now reads:")
        else:
            st.write("The summary's wording is unchanged:")
        st.text(summary["content"] or "Empty summary — no facts included.")
    if not operation["preview"]["affected_summaries"]:
        st.write("No summaries were affected.")
    before = st.session_state.get("before_snapshot")
    if before and before.dataset_id == snapshot.dataset_id and before.generation == snapshot.generation:
        unaffected = sorted(set(before.state.summaries) - set(operation["preview"]["affected_summaries"]))
        if unaffected:
            st.markdown("**Unaffected summaries · recorded before / after evidence**")
            for key in unaffected:
                old, new = before.state.summaries[key], state.summaries.get(key)
                st.caption("Summary ID: " + key + (" · UNCHANGED" if old == new else " · CHANGED / needs investigation"))
                st.json({"summary_id": key, "before": old, "after": new, "unchanged": old == new})
def verify_and_test(engine, snapshot):
    st.header("Verify & Test")
    st.write("Compare saved memory with an independent reconstruction—not the repair's completion claim.")
    if st.button("Check current memory", key="verify", type="primary"):
        run_verification(engine)
        st.rerun()
    check_result(engine, snapshot)
    operation = snapshot.operations.get(st.session_state.get("operation_id"))
    if operation:
        completion_warning(operation)
    if operation and repair_pending(operation):
        if st.button("Retry repair without a mistake", key="retry"):
            try:
                record_activity("recovery_requested", snapshot, "APPROVED", operation["source_id"], operation_id=operation["id"])
                with st.status("RUNNING · retrying incomplete repair", expanded=True) as progress:
                    retried = engine.repair_memory(operation["id"])
                    repair_progress(progress, retried)
                run_verification(engine)
                st.rerun()
            except FalloutError as exc:
                st.error(str(exc))
    if operation:
        recorded_memory(engine, snapshot)
    with st.expander("Advanced details · technical verification report", expanded=False):
        evidence(engine, snapshot)
    with st.expander("Advanced · isolated verification lab"):
        verification_lab()
    with st.expander("Advanced · fault injection"):
        fault_controls()


def fault_controls():
    st.subheader("Can the checker catch a mistake?")
    st.write("Test whether the checker catches a deliberately introduced mistake.")
    st.selectbox("Mistake to introduce on the next repair", FAULTS, format_func=lambda value: FAULT_LABELS[value],
                 key="fault_choice", on_change=remember_fault)
    explanations = {
        "none": "The next repair will run normally. It still needs an independent check.",
        "skip_invalidation": "The repair will leave a fact marked usable even when its only source is removed.",
        "skip_summary": "The repair will leave the old summary in place, even after its source is removed.",
        "raise_during_repair": "The repair will stop after the source is removed. You can retry the unfinished repair.",
        "false_success": "The operation will report completion while skipping one affected summary rebuild. The independent checker must examine the saved content, not trust that claim. Retry without a mistake to recover the same operation.",
    }
    st.caption(explanations[st.session_state.fault])
    st.write("These advanced fault controls apply to your next explicitly approved investigation. Use the isolated Verification Lab above to preserve working memory. Otherwise, reset deliberately before reusing a revoked source.")
    st.caption("A FAIL is expected when the mistake leaves memory wrong, but only actual execution can establish the result. Retry without a mistake to see whether the check passes.")
    st.button("Investigate a source", on_click=go_to_repair)


def verification_lab():
    st.subheader("Verification Lab")
    scenario_config = active_scenario()
    st.write("Run normal repair and a deliberately skipped summary rebuild against two fresh graphs of the selected scenario. Your working memory, pending preview and approval remain untouched.")
    st.caption(SCENARIO_LABELS[scenario_config.id] + " · " + scenario_config.description)
    st.caption("Expected outcomes: normal PASS; skipped rebuild FAIL. Results below come only from actual execution. No AI requests.")
    compare = st.button("Run isolated comparison", key="run_lab")
    proof = st.button("Run false-success proof", key="false_success_lab")
    st.caption("The false-success proof also retries the same isolated operation without a mistake, then records a separate recovery verification. Expected: FAIL before recovery, PASS after; neither verdict is assumed.")
    if compare or proof:
        with st.status("RUNNING · isolated verification experiments", expanded=True) as progress:
            result = run_lab(scenario_id=scenario_config.id, fault="false_success" if proof else "skip_summary")
            st.session_state.setdefault("lab_history", []).append(result)
            st.session_state.lab_run = sum(run.get("scenario_id", "pricing") == scenario_config.id
                                           for run in st.session_state.lab_history) - 1
            errors = any(s["verdict"] == "ERROR" or (s.get("recovery") and s["recovery"]["verdict"] == "ERROR")
                         for s in result["scenarios"])
            progress.update(label="Lab execution recorded · not a working-memory repair", state="error" if errors else "complete")
    # Older session records without a scenario_id were pricing-only. Retain them
    # without ever presenting them as documentation evidence.
    history = [run for run in st.session_state.get("lab_history", [])
               if run.get("scenario_id", "pricing") == scenario_config.id]
    if not history:
        st.info("NOT RUN · No lab scenarios have executed in this session.")
        return
    if st.session_state.get("lab_run", len(history)-1) not in range(len(history)):
        st.session_state.pop("lab_run", None)
    selected = st.selectbox("Recorded lab run", range(len(history)), format_func=lambda index: history[index]["started_at"] + " · " + history[index]["id"][:8], key="lab_run", index=len(history)-1)
    result = history[selected]
    st.caption("ISOLATED LAB · " + result["id"] + " · not the current-memory verdict")
    for column, scenario in zip(st.columns(2, gap="large"), result["scenarios"]):
        with column:
            title = {"none": "Normal repair", "skip_summary": "Skipped summary rebuild",
                     "false_success": "Reported completion with a skipped rebuild"}[scenario["fault"]]
            st.markdown("**" + title + " · " + scenario["verdict"] + "**")
            st.caption(scenario["reason"])
            if scenario.get("operation"):
                st.caption("Operation ID: " + scenario["operation"]["id"])
                completion_warning(scenario["operation"])
            report = scenario["verification"]
            if report:
                preview = scenario["preview"] or {}
                paths = {"facts." + key + ".active_support" for key in preview.get("preserved_facts", [])}
                paths.update("facts." + key + ".status" for key in preview.get("invalidated_facts", []))
                paths.update("summaries." + key + "." + field
                             for key in (scenario.get("after") or {}).get("state", {}).get("summaries", {})
                             for field in ("content", "status"))
                rows = [row for row in evidence_rows(report) if row["comparison"] in paths]
                st.dataframe(rows, hide_index=True, width="stretch")
                if scenario["fault"] in {"skip_summary", "false_success"} and scenario["verdict"] == "FAIL":
                    st.write("Stale-content diagnosis: compare the exact saved summary content with the independent reference. A completion claim cannot override mismatched evidence.")
            st.caption("Verification ID: " + (scenario["verification_id"] or "not attempted"))
            lab_summary_evidence(scenario)
            recovery = scenario.get("recovery")
            if recovery:
                st.markdown("**Recovery · " + recovery["verdict"] + "**")
                st.caption("Same operation ID: " + recovery["operation_id"])
                st.caption("Separate verification ID: " + (recovery["verification_id"] or "not attempted"))
                st.caption(recovery["reason"])
                if recovery["verification"]:
                    st.dataframe(evidence_rows(recovery["verification"]), hide_index=True, width="stretch")
                lab_summary_evidence(recovery)
            with st.expander("Lab evidence · " + scenario["name"]):
                st.json(scenario)
    st.caption("Lab history is session-only, banked by scenario and separate from the working-memory reset. A new run appends evidence; it never replaces a prior run.")


def lab_summary_evidence(result):
    before, after = result.get("before"), result.get("after")
    if before is None or after is None:
        return
    affected = set((result.get("operation") or {}).get("preview", {}).get("affected_summaries", []))
    rows = []
    for key in sorted(set(before["state"]["summaries"]) | set(after["state"]["summaries"])):
        old = before["state"]["summaries"].get(key, {})
        new = after["state"]["summaries"].get(key, {})
        rows.append({"summary_id": key, "scope": "affected" if key in affected else "unaffected",
                     "unchanged": old == new, "before": old.get("content", "missing"),
                     "after": new.get("content", "missing"), "status before": old.get("status", "missing"),
                     "status after": new.get("status", "missing"),
                     "members before": old.get("members", []), "members after": new.get("members", []),
                     "builds before": old.get("build_count"), "builds after": new.get("build_count")})
    st.caption("Recorded summary IDs and before / after · unchanged compares the entire record, including build count")
    st.dataframe(rows, hide_index=True, width="stretch")


def operations(engine, snapshot):
    section("03", "Execution record")
    operation_id = st.session_state.get("operation_id")
    if not operation_id:
        st.caption("NO OPERATIONS / Execution evidence appears only after explicit confirmation.")
        return
    operation = engine.get_operation_report(operation_id)
    st.code(operation_id, language=None)
    st.caption("SOURCE " + operation["source_id"] + " · STATUS " + operation["status"] + " · ATTEMPTS " + str(operation["attempts"]))
    st.caption("Revocation, committed repair steps and independent verification are separate records. A completed execution is not proof of correctness.")
    events = [event for event in snapshot.events if event.get("operation_id") == operation_id]
    timeline = [{"recorded at (UTC)": event["at"], "stage": event.get("step", event["type"]),
                 "outcome": event.get("outcome", "APPLIED" if event["type"] == "source_revoked" else "STARTED"),
                 "attempt": str(event.get("attempt", "—")), "operation": operation_id} for event in events]
    for execution in st.session_state.get("verification_history", []):
        if operation_id in execution["operation_ids"] and execution["generation"] == snapshot.generation:
            timeline.append({"recorded at (UTC)": execution["finished_at"], "stage": "independent_verification",
                             "outcome": execution["status"] + " / revision " + str(execution["revision"]),
                             "attempt": "—", "operation": operation_id})
    st.dataframe([{"step": key, "outcome": value["status"]} for key, value in operation.get("steps", {}).items()], hide_index=True, width="stretch")
    with st.expander("Timestamped operation timeline"):
        st.dataframe(sorted(timeline, key=lambda event: event["recorded at (UTC)"] or ""), hide_index=True, width="stretch")
        st.json(operation.get("steps", {}))
    report = st.session_state.get("verification")
    verdict, reason = verification_state(report, snapshot, engine.dataset)
    st.caption("INDEPENDENT VERIFICATION / " + verdict + " · " + reason)
    if operation["error"]:
        st.error(operation["error"]["message"])
    completion_warning(operation)
    if repair_pending(operation):
        st.caption("Open Verify & Test to retry without a deliberate mistake.")


def evidence(engine, snapshot):
    section("04", "Independent comparison ledger")
    st.caption("REFERENCE / original synthetic input + revocation history. ACTUAL / recorded memory at the verified revision. No repair success flag determines this verdict.")
    report = st.session_state.get("verification")
    verdict, reason = ui_verdict(report, snapshot, engine.dataset)
    if verdict == "NOT RUN":
        if report:
            st.warning("NOT RUN / " + reason)
        else:
            st.info("NOT RUN / " + reason)
    elif verdict == "ERROR":
        st.error("ERROR / " + reason)
        st.caption("No success is inferred. Run verification again; if this persists, use a fresh compatible server process. Existing memory is preserved.")
    else:
        if report["status"] == "PASS":
            st.success("PASS / All required comparisons match the independent reference.")
        else:
            st.error("FAIL / " + str(len(report["mismatches"])) + " mismatches require investigation.")
        filter_column, toggle_column = st.columns([3, 1])
        query = filter_column.text_input("Filter evidence by path or value", key="evidence_query", placeholder="summary ID, active_support, composition_edges…")
        only = toggle_column.checkbox("Mismatches only", key="mismatches_only")
        rows = evidence_rows(report, query, only)
        st.caption(str(len(rows)) + " of " + str(len(report.get("comparisons", []))) + " comparisons shown · revision " + str(report["revision"]))
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch", height=350)
            paths = [row["comparison"] for row in rows]
            if st.session_state.get("comparison_path") not in paths:
                st.session_state.comparison_path = paths[0]
            selected_path = st.selectbox("Inspect exact comparison", paths, key="comparison_path")
            selected = next(item for item in report["comparisons"] if item["path"] == selected_path)
            expected, actual = st.columns(2)
            with expected:
                st.caption("EXPECTED / clean reference")
                st.json(selected["expected"] if not isinstance(selected["expected"], str) else {"value": selected["expected"]})
            with actual:
                st.caption("ACTUAL / recorded memory")
                st.json(selected["actual"] if not isinstance(selected["actual"], str) else {"value": selected["actual"]})
            entity = mismatch_entity(selected_path, snapshot.state)
            if entity:
                st.button("Select entity in Memory lineage", key="inspect_comparison", on_click=select_entity, args=(entity,))
                st.caption("Open Investigate & Repair to follow this selection in the graph.")
            elif selected_path in {"support_edges", "composition_edges"}:
                st.caption("RELATIONSHIP EVIDENCE / Every pair is directed from the first ID to the second; repeated pairs indicate duplicate edges.")
                pairs = selected["expected"] + selected["actual"]
                first, second = ("sources", "facts") if selected_path == "support_edges" else ("facts", "summaries")
                candidates = sorted({group + "." + key for pair in pairs for group, key in zip((first, second), pair)
                                     if key in getattr(snapshot.state, group)})
                if candidates:
                    if st.session_state.get("relationship_entity") not in candidates:
                        st.session_state.relationship_entity = candidates[0]
                    relation_entity = st.selectbox("Inspect relationship endpoint", candidates, key="relationship_entity")
                    st.button("Select relationship endpoint in graph", key="inspect_relationship", on_click=select_entity, args=(relation_entity,))
        else:
            st.info("No comparisons match these filters. Clear the filter or disable Mismatches only.")
        if report["mismatches"]:
            with st.expander("Exact mismatch records", expanded=True):
                for mismatch in report["mismatches"]:
                    st.json(mismatch)
        with st.expander("Complete verifier report / dataset identity"):
            st.json(report)
        # No session/config dump, AI prose, environment, or authorization grants.
        try:
            safe_graph(engine.dataset, snapshot)
        except Exception:
            st.caption("Export unavailable: this state is outside the reviewed synthetic-data boundary.")
        else:
            payload = {"scope": "Session-only synthetic verification evidence; not an immutable audit",
                       "verification_id": st.session_state.get("verification_id"), "verification": report}
            st.download_button("Download verification evidence", json.dumps(payload, indent=2),
                               file_name="fallout-verification.json", mime="application/json", key="export_evidence")

    section("05", "Before / after memory")
    before = st.session_state.get("before_snapshot")
    if not before:
        st.caption("No confirmed operation yet. No before/after comparison is implied.")
    else:
        st.caption("Actual snapshots / before the last confirmed operation versus current state; not an expected-state reconstruction.")
        rows = []
        for key in sorted(set(before.state.facts) | set(snapshot.state.facts)):
            old, new = before.state.facts.get(key, {}), snapshot.state.facts.get(key, {})
            rows.append({"fact": key, "before": old.get("status", "missing"), "after": new.get("status", "missing"),
                         "support before": ", ".join(old.get("active_support", [])) or "NONE",
                         "support after": ", ".join(new.get("active_support", [])) or "NONE"})
        st.dataframe(rows, hide_index=True, width="stretch")
        for key, summary in snapshot.state.summaries.items():
            st.code(key, language=None)
            old = before.state.summaries.get(key, {})
            left, right = st.columns(2)
            with left:
                st.caption("BEFORE / " + old.get("status", "missing"))
                st.text(old.get("content") or "No valid members")
                st.caption("Members: " + ", ".join(old.get("members", [])))
            with right:
                st.caption("CURRENT / " + summary["status"])
                st.text(summary["content"] or "No valid members")
                st.caption("Members: " + ", ".join(summary["members"]))
    section("06", "Audit trail")
    if st.session_state.get("verification_history"):
        st.caption("Independent verification executions / session-local, revision-bound evidence")
        st.dataframe(st.session_state.verification_history, hide_index=True, width="stretch")
    if snapshot.events:
        st.dataframe(snapshot.events, hide_index=True, width="stretch")
        selected_op = st.selectbox("Recorded operation", list(snapshot.operations), key="audit_operation")
        with st.expander("Operation detail"):
            st.json(engine.get_operation_report(selected_op))
    else:
        st.caption("EMPTY / No operation events have been recorded in this session.")


def utilities(engine):
    with st.expander("Start over · reset the local demo"):
        st.write("Restore this scenario's original dataset. " + active_scenario().description)
        st.caption("This discards only the active scenario's working memory and investigation history after you confirm. Other scenario banks and isolated lab history are preserved.")
        if engine.backend.name == "falkordb":
            st.caption("Target: " + engine.backend.target + ". Reset publishes a new generation; previous graph versions remain stored. This is not physical erasure.")
        if st.button("Preview reset", key="preview_reset"):
            st.session_state.reset_preview = engine.preview_reset()
        reset = st.session_state.get("reset_preview")
        if reset:
            st.caption("Ready to restore this demo scope. Nothing has changed yet.")
        acknowledged = st.checkbox("Discard this session's demo state and audit history", key="reset_ack", disabled=not bool(reset))
        st.button("Confirm in-memory reset" if engine.backend.name == "in-memory" else "Confirm FalkorDB scope reset", disabled=not (acknowledged and reset), key="confirm_reset",
                  on_click=reset_session, args=(engine,))
        if st.session_state.get("reset_error"):
            st.error(st.session_state.reset_error)


def activity(snapshot):
    with st.expander("Session activity · analysis, approval and execution"):
        st.caption("Actual events only. Volatile session history—not immutable audit evidence. Working-memory reset clears this timeline; isolated lab runs remain separate.")
        rows = []
        for event in st.session_state.get("activity_history", []):
            rows.append({"at": event["at"], "stage": event["stage"], "outcome": event["outcome"],
                         "source": event.get("source") or "—", "id": event["id"],
                         "reference": event.get("operation_id", event.get("verification_id", event.get("preview_id", "—")))})
        for event in snapshot.events:
            op = snapshot.operations.get(event.get("operation_id"), {})
            rows.append({"at": event["at"], "stage": event.get("step", event["type"]),
                         "outcome": event.get("outcome", "APPLIED" if event["type"] == "source_revoked" else "STARTED"),
                         "source": op.get("source_id", "—"), "id": "engine event",
                         "reference": event.get("operation_id", "—")})
        if rows:
            st.dataframe(sorted(rows, key=lambda row: row["at"]), hide_index=True, width="stretch")
        else:
            st.info("No analysis, approvals, repairs or verification executions have been recorded.")
        if snapshot.operations:
            chosen = st.selectbox("Operation details", list(snapshot.operations), key="history_operation")
            st.json(snapshot.operations[chosen])


def render():
    st.set_page_config(page_title="FALLOUT / Memory reliability", layout="wide", initial_sidebar_state="collapsed")
    st.html("<style>" + (ROOT / "fallout" / "workbench.css").read_text(encoding="utf-8") + "</style>")
    # Adopt pre-selector sessions as pricing without replacing their engine or
    # evidence. Keep hidden selections alive across navigation widget cleanup.
    st.session_state.setdefault("active_scenario", "pricing")
    for key in scenario_keys():
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    st.session_state.scenario_choice = st.session_state.active_scenario
    try:
        scenario = active_scenario()
        config = Config.from_environment()
        identity = st.session_state.get("database_config_identity")
        if identity is not None and identity != config.identity:
            raise FalloutError("Database configuration changed. Open a fresh session; existing state has not been reset or redirected.")
        st.session_state.database_config_identity = config.identity
        if "engine" not in st.session_state:
            dataset = scenario.load()
            # Per-browser scope by default; an explicit scope permits reconnecting
            # to persisted state. Dataset fingerprint separates the two scenarios.
            if "database_scope" not in st.session_state:
                st.session_state.database_scope = config.scope or "session_" + uuid4().hex
            st.session_state.engine = Engine(dataset, create_backend(dataset, config, scope=st.session_state.database_scope))
        engine = st.session_state.engine
        if type(engine) is not Engine or getattr(engine, "instance_contract", None) != 2:
            raise FalloutError("BLOCKED / This session belongs to an older engine runtime. Open the newly launched server or restart deliberately. Existing memory has not been reset.")
        if engine.dataset.fingerprint != scenario.load().fingerprint:
            raise FalloutError("BLOCKED / Original dataset changed since this session began. Open a fresh session; existing state has been preserved.")
        if engine.backend.name != config.backend:
            raise FalloutError("Backend configuration changed. Restart the app; no fallback is allowed.")
    except FalloutError as exc:
        st.error(str(exc))
        st.stop()
    if st.session_state.pop("reset_widgets", False):
        for key, value in {"fault": "none", "source_id": scenario.withdraw_source, "reset_ack": False, "entity_id": "sources." + scenario.withdraw_source,
                           "graph_view": "Current state", "evidence_query": "", "mismatches_only": False}.items():
            st.session_state[key] = value
    for key, value in {"source_id": scenario.withdraw_source, "fault": "none", "section": "Overview"}.items():
        if key not in st.session_state:
            st.session_state[key] = value
    st.session_state.source_choice = st.session_state.source_id
    st.session_state.fault_choice = st.session_state.fault
    try:
        snapshot = engine.backend.read()
    except FalloutError as exc:
        st.error("Memory unavailable. No current PASS can be established. " + str(exc))
        st.stop()
    if engine.backend.name == "falkordb" and not st.session_state.get("operation_id") and snapshot.operations:
        # Reconnecting restores access to pending work without minting approval.
        # Retry remains a separate explicit button action.
        pending = [op for op in snapshot.operations.values() if repair_pending(op)]
        candidates = pending or list(snapshot.operations.values())
        operation = max(candidates, key=lambda op: (op["created_at"], op["id"]))
        st.session_state.operation_id = operation["id"]
    if st.session_state.section == "Repair Memory":
        st.session_state.section = INVESTIGATE
    with st.container(key="instrument"):
        with st.container(key="workbench_header"):
            header(engine, snapshot)
        workflow(engine, snapshot)
        with st.container(key="workflow_evidence"):
            state_banners(engine, snapshot)
        if st.session_state.section == "Overview":
            overview(engine, snapshot)
        elif st.session_state.section == INVESTIGATE:
            analyze(engine, snapshot)
            utilities(engine)
        else:
            verify_and_test(engine, snapshot)
            utilities(engine)
        if st.session_state.section != "Overview":
            render_ai(engine, snapshot)
        with st.container(key="workbench_footer"):
            activity(snapshot)
            with st.expander("Advanced details · environment"):
                st.caption("FALLOUT " + VERSION + " · MEMORY RELIABILITY / RECOVERY")
                ai_enabled = AISettings.from_environment().enabled
                storage_description = ("FalkorDB stores source, fact and summary nodes, lineage edges and versioned operation records. "
                                       if engine.backend.name == "falkordb" else "No FalkorDB database is connected. ")
                st.write("This prototype uses a deterministic repair engine and an independent checker. " + storage_description +
                         ("Optional Gemini assistance is enabled; see its panel for configuration and live-test status." if ai_enabled else "No live AI model is configured."))
                st.caption("Backend: " + engine.backend.name + " · Target: " + engine.backend.target)
                status_strip(snapshot, engine.dataset)
            ai_enabled = AISettings.from_environment().enabled
            storage_notice = "FalkorDB persistence · Synthetic state sent to configured database" if engine.backend.name == "falkordb" else "In-memory only"
            st.html('<div class="fo-privacy-note">Synthetic data · ' + storage_notice + ' · ' +
                    ('Optional Gemini requests send synthetic data only after consent' if ai_enabled else
                     ('AI off · No AI requests' if engine.backend.name == "falkordb" else 'AI off · Nothing leaves this local demo')) + '</div>')