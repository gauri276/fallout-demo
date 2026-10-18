"""Explicitly authorized, isolated synthetic experiments; standard library only."""

from copy import deepcopy
from dataclasses import asdict
from uuid import uuid4

from .database import InMemoryBackend
from .engine import Engine, timestamp
from .models import Dataset, FalloutError
from .presentation import verification_state
from .scenarios import get_scenario


def _verify(result, engine, dataset: Dataset):
    """Keep each executed check bound to its own snapshot and unique ID."""
    snapshot = engine.backend.read()
    result["after"] = asdict(snapshot)
    result["verification_id"] = uuid4().hex
    report = engine.verify_rebuild()
    state, reason = verification_state(report, snapshot, dataset)
    if state in {"PASS", "FAIL"}:
        result.update(verification=deepcopy(report), verdict=state, reason=reason)
    else:
        result["reason"] = "Verification evidence is unavailable, invalid or out of date. No verified verdict is available."


def _recover(engine, operation_id):
    """Retry the existing isolated operation; never replace its failed evidence."""
    result = {
        "id": uuid4().hex, "operation_id": operation_id, "fault": "none",
        "started_at": timestamp(), "finished_at": None, "operation": None,
        "before": None, "after": None, "verification": None,
        "verification_id": None, "verdict": "ERROR",
        "reason": "Recovery could not complete. No verified verdict is available.",
    }
    try:
        result["before"] = asdict(engine.backend.read())
        result["operation"] = engine.repair_memory(operation_id)
        _verify(result, engine, engine.dataset)
    except Exception:
        result.update(verdict="ERROR", verification=None,
                      reason="Recovery could not complete. No verified verdict is available.")
        try:
            result["after"] = asdict(engine.backend.read())
            result["operation"] = engine.get_operation_report(operation_id)
        except Exception:
            pass
    finally:
        result["finished_at"] = timestamp()
    return result


def _scenario(name, fault, scenario_id):
    scenario = get_scenario(scenario_id)
    result = {
        "id": uuid4().hex, "name": name, "fault": fault,
        "scenario_id": scenario.id, "source_id": scenario.withdraw_source,
        "started_at": timestamp(), "finished_at": None,
        "preview": None, "operation": None, "verification": None,
        "verification_id": None, "verdict": "ERROR",
        "reason": "Lab scenario could not complete. No verified verdict is available.",
        "before": None, "after": None, "recovery": None,
    }
    engine = None
    operation_id = None
    try:
        # Each scenario loads the canonical input and owns its entire engine.
        dataset = scenario.load()
        engine = Engine(dataset, InMemoryBackend(dataset))
        result["before"] = asdict(engine.backend.read())
        result["preview"] = engine.preview_impact(result["source_id"])
        # The caller's explicit lab button authorizes only this synthetic run.
        confirmation = engine.confirm_preview(result["preview"])
        result["operation"] = engine.revoke_source(result["source_id"], confirmation)
        operation_id = result["operation"]["id"]
        result["operation"] = engine.repair_memory(operation_id, fault=fault)
        _verify(result, engine, dataset)
    except Exception:
        # Do not copy exception messages, types, reprs or tracebacks into results.
        result.update(verdict="ERROR", verification=None,
                      reason="Lab scenario could not complete. No verified verdict is available.")
        # Retain real partial state and operation history when still readable.
        if engine is not None:
            try:
                result["after"] = asdict(engine.backend.read())
                if operation_id is not None:
                    result["operation"] = engine.get_operation_report(operation_id)
            except Exception:
                pass  # Previously captured evidence is retained, not fabricated.
    finally:
        if fault == "false_success" and engine is not None and operation_id is not None:
            # The explicit proof action also authorizes recovery on this engine.
            # Initial after/operation/report remain evidence of the FIRST attempt.
            result["recovery"] = _recover(engine, operation_id)
        result["finished_at"] = timestamp()
    return result


def run_lab(scenario_id="pricing", fault="skip_summary"):
    """Run ONLY after the caller's explicit lab-button permission/action.

    Never call on rendering, automatic reruns, or from an agent. That action
    authorizes preview/confirmation/revoke/repair of the selected scenario's
    configured withdrawal source only. A false-success proof also authorizes
    a fault-free retry of that same isolated operation and a separate check.
    No user engine, configuration, environment, network or history is accessed.
    Each invocation returns a new result; the caller must append/store it without
    silently replacing prior runs. This module neither stores nor clears history.

    Schema: {id, scenario_id, fault, started_at, finished_at, scenarios}.
    IDs are UUID4 hex strings;
    timestamps are timezone-aware UTC ISO 8601 strings. Scenarios, in order, are
    normal/none and the selected fault (skip_summary by default).
    Each has {id, name, fault, scenario_id,
    source_id, started_at, finished_at, preview, operation, verification,
    verification_id, verdict, reason, before, after, recovery}. Verdict is PASS/FAIL/ERROR,
    determined by executed, validated evidence, never by the expected fault.

    Preview and operation are engine dictionaries, or None if unavailable.
    Verification is the complete validated verifier report, or None on ERROR;
    verification_id identifies an attempted verification, or is None if not
    attempted. Before/after are dataclasses.asdict(Snapshot) dictionaries (state,
    dataset_id, generation, revision, operations, events), or None if unavailable.
    After can represent partial progress on ERROR. No confirmation token escapes.
    Recovery is None except for an attempted false-success recovery. It has
    {id, operation_id, fault, started_at, finished_at, operation, before, after,
    verification, verification_id, verdict, reason}. Its operation ID is the
    original ID; its verification ID and revision are separate. Initial evidence
    is never overwritten by recovery. Invalid scenario/fault arguments raise
    FalloutError before any engine is created.
    """
    scenario = get_scenario(scenario_id)
    if fault not in {"skip_summary", "false_success"}:
        raise FalloutError("Choose skip_summary or false_success for the isolated comparison.")
    result = {"id": uuid4().hex, "scenario_id": scenario.id, "fault": fault,
              "started_at": timestamp(), "finished_at": None,
              "scenarios": []}
    for name, mode in (("normal", "none"), (fault, fault)):
        result["scenarios"].append(_scenario(name, mode, scenario.id))
    result["finished_at"] = timestamp()
    return result