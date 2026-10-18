"""Optional AI explanation panel; never authorizes or performs a mutation."""

import streamlit as st

from .ai_assistant import MemoryAssistant, TASKS, safe_graph, snapshot_identity
from .ai_provider import AIError, AISettings, create_provider
from .models import FalloutError
from .presentation import verification_state


def review_ai_preview(engine):
    """A click opens an ordinary exact preview; it does NOT mint a grant."""
    result = st.session_state.get("ai_result", {})
    try:
        if result.get("status") != "complete" or not result.get("plan"):
            raise FalloutError("No validated AI proposal is available.")
        if st.session_state.get("ai_task") != result["task"] or st.session_state.get("ai_source") != result["target"]:
            raise FalloutError("The selected AI task or source changed.")
        if result["identity"] != snapshot_identity(engine.backend.read()):
            raise FalloutError("This AI proposal is out of date. Request a fresh plan.")
        preview = engine.preview_impact(result["plan"]["source_id"])
        if preview != result["preview"]:
            raise FalloutError("The graph preview changed. Request a fresh plan.")
        st.session_state.source_id = preview["source_id"]
        st.session_state.impact_preview = preview
        st.session_state.section = "Investigate & Repair"
        st.session_state.pop("ai_review_error", None)
    except (FalloutError, KeyError):
        st.session_state.ai_review_error = "The proposal is unavailable or out of date. Request a fresh plan; nothing was applied."


def render_ai(engine, snapshot):
    settings = AISettings.from_environment()
    # Do not retain the API key in UI state. Forget output when provider config changes.
    signature = (settings.enabled, settings.provider, settings.public_model,
                 settings.free_tier_confirmed, hash(settings.api_key))
    if st.session_state.get("ai_configuration") != signature:
        for key in ("ai_result", "ai_live_tested", "ai_review_error"):
            st.session_state.pop(key, None)
        st.session_state.ai_configuration = signature
        st.session_state.ai_consent = False
    if st.session_state.pop("ai_clear_consent", False):
        st.session_state.ai_consent = False
    with st.expander("AI assistance · optional explanations and proposals"):
        st.caption("Provider: " + ("Gemini API · Google AI Studio" if settings.provider == "gemini" else "unsupported") +
                   " · Model: " + settings.public_model)
        st.write("AI text is a suggestion, not verified evidence. Only the engine repairs memory; only the independent checker determines the result. No graph or database tools are exposed to this AI.")
        problem = settings.problem
        if problem:
            st.info(problem)
            st.caption("Configure the process environment using the README. AI is optional; local controls remain available.")
            return
        st.warning("Free-tier eligibility depends on your account and model. This app cannot check billing or guarantee zero charges. Use an unbilled project; there are no automatic retries, paid fallbacks or provider switches.")
        if st.session_state.get("ai_live_tested"):
            st.caption("Live request succeeded in this session for this configuration. This does not verify memory correctness or future quota availability.")
        else:
            st.info("Configured, but not yet live-tested in this session. No successful live AI use is claimed.")
        try:
            safe_graph(engine.dataset, snapshot)
        except AIError as exc:
            st.warning(str(exc))
            return
        task = st.selectbox("What should AI explain?", list(TASKS), format_func=lambda value: TASKS[value], key="ai_task")
        if task == "explain_memory":
            choices = [group + "." + key for group in ("facts", "summaries") for key in sorted(getattr(snapshot.state, group))]
            target = st.selectbox("Memory to explain", choices, key="ai_memory")
        elif task in {"analyze_impact", "propose_repair"}:
            target = st.selectbox("Source to analyze", sorted(snapshot.state.sources), key="ai_source")
        else:
            target = None
        consent = st.checkbox("Send only this synthetic demo's graph and check status to Gemini for this request", key="ai_consent")
        requested = st.button("Request AI assistance", key="ai_request", disabled=not consent)
        # Enforce permission in server-side execution, not just disabled styling.
        if requested and consent:
            st.session_state.pop("ai_result", None)
            try:
                with st.spinner("Waiting for Gemini; no memory changes are being made…"):
                    result = MemoryAssistant(engine, create_provider(settings)).run(task, target, st.session_state.get("verification"))
            except Exception:
                result = {"status": "unavailable", "message": "AI configuration or request failed. Nothing was applied; the local demo still works."}
            st.session_state.ai_result = result
            st.session_state.ai_clear_consent = True
            if result.get("status") == "complete" and result.get("provenance") == "live":
                st.session_state.ai_live_tested = True
            st.rerun()
        result = st.session_state.get("ai_result")
        if not result:
            return
        if result["status"] != "complete":
            st.warning(result["message"])
            st.caption("This request did not produce an accepted AI result. No generated explanation or proposal is shown.")
            return
        verdict, _ = verification_state(st.session_state.get("verification"), snapshot, engine.dataset)
        if result["identity"] != snapshot_identity(snapshot) or result["check_at_request"] != verdict:
            st.info("Previous AI response is out of date for this memory or check. Request a fresh explanation; the old proposal is not actionable.")
            return
        if (result.get("task"), result.get("target")) != (task, target):
            st.info("The AI task or target changed. Request a new explanation for this selection; the previous proposal is not actionable.")
            return
        st.markdown("**AI explanation — unverified language, not a system verdict**")
        st.caption("Live Gemini response" if result.get("provenance") == "live" else "MOCK / test fixture — not a live AI response")
        # Plain text, not Markdown/HTML: no executable markup or remote images.
        st.text(result["explanation"].replace(settings.api_key, "[REDACTED]"))
        if result.get("plan"):
            st.markdown("**Proposed scope — checked against the deterministic graph preview**")
            plan = result["plan"]
            st.write("Source to remove: " + plan["source_id"])
            st.write("Facts that retain support: " + (", ".join(plan["preserved_facts"]) or "none"))
            st.write("Facts that lose all support: " + (", ".join(plan["invalidated_facts"]) or "none"))
            st.write("Summaries to rebuild: " + (", ".join(plan["affected_summaries"]) or "none"))
            st.caption("Scope and support were matched exactly. The prose above is not validated. Review opens a preview only; explicit confirmation is still required to apply anything.")
            st.button("Review proposed changes", key="ai_review", on_click=review_ai_preview, args=(engine,))
            if st.session_state.get("ai_review_error"):
                st.warning(st.session_state.ai_review_error)