"""Opt-in, real local FalkorDB acceptance; never imported by unittest discovery.

Usage (only after the local database is available)::

    python -m tests.falkordb_acceptance --approve
    python -m tests.falkordb_acceptance --host 127.0.0.1 --port 6380 --approve

Approval authorizes this harness's revocations, repairs, scoped resets, and one
deliberate graph corruption, exclusively in newly allocated acceptance scopes.
No environment files, credentials, fallback backend, server startup, deletion,
or cleanup are used. All graph versions remain for inspection. Persistence here
means reattachment through a new client, not survival of a server restart.
Import and --help need only the standard library; redis-py is loaded on execution.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
CONFLICT = "FalkorDB memory changed; refresh before continuing."
APPROVAL_REQUIRED = "Explicit UI confirmation for this exact source and preview is required."
RESET_APPROVAL_REQUIRED = "Explicit confirmation of the current demo reset target is required."
STALE_PREVIEW = "Preview changed or was altered. Review a fresh preview."


class AcceptanceFailure(Exception):
    """No dynamic exception details are ever placed in the result or console."""


def loopback_host(value):
    # Resolve no arbitrary DNS names (including potentially rebound localhost).
    if value.lower() == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Use a literal loopback IP or localhost.") from None
    if not address.is_loopback or "%" in value:
        raise argparse.ArgumentTypeError("Only loopback addresses are permitted.")
    return str(address)


def local_port(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Port must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be an integer from 1 to 65535.")
    return port


def text(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


class Acceptance:
    def __init__(self, args, result):
        # Keep optional/application imports out of import-time and --help paths.
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        from fallout.engine import Engine
        from fallout.falkordb_backend import FalkorDBBackend
        from fallout.models import FalloutError
        from fallout.scenarios import get_scenario
        from fallout.verifier import report_problem

        self.Engine = Engine
        self.Backend = FalkorDBBackend
        self.FalloutError = FalloutError
        self.get_scenario = get_scenario
        self.report_problem = report_problem
        self.result = result
        self.clients = []
        self.owned_roots = set()

        def connect():
            client = redis.Redis(
                host=args.host, port=args.port, db=0, username=None,
                password=None, ssl=False, decode_responses=False,
                socket_connect_timeout=5, socket_timeout=10,
                retry=Retry(NoBackoff(), 0), retry_on_timeout=False,
                retry_on_error=[],
            )
            self.clients.append(client)
            return client

        self.connect = connect
        self.client = connect()

    def check(self, condition, name):
        self.result["checks"].append({"name": name, "status": "PASS" if condition else "FAIL"})
        if not condition:
            raise AcceptanceFailure()

    def reject(self, action, message, evidence, name):
        try:
            action()
        except self.FalloutError as exc:
            # Compare only against a fixed allowlisted message; never serialize exc.
            self.check(str(exc) == message, name + ": exact rejection")
            evidence[name] = {"rejected": True, "reason": message}
        else:
            self.check(False, name + ": expected rejection")

    def reserve_scope(self, scope):
        self.check(
            next(self.client.scan_iter(match="{fallout_demo:" + scope + ":*", count=100), None) is None,
            "scope contains no preexisting keys: " + scope,
        )

    def backend(self, dataset, scope, entry):
        root = "{fallout_demo:" + scope + ":" + dataset.fingerprint + "}"
        self.owned_roots.add(root)
        entry.update(scope=scope, dataset_id=dataset.fingerprint, root_key=root, head_key=root + ":head")
        return self.Backend(dataset, scope=scope, client=self.connect())

    def head(self, backend):
        head = json.loads(self.client.get(backend.head_key))
        self.check(backend.root_key in self.owned_roots and
                   head["owner"] == backend.root_key and
                   head["dataset_id"] == backend.dataset_id and
                   head["graph"].startswith(backend.root_key + ":graph:"),
                   "current graph belongs to this acceptance dataset")
        return head

    def query(self, graph, query, parameters, width, evidence, write=False):
        self.check(any(graph.startswith(root + ":graph:") for root in self.owned_roots),
                   "query is restricted to an owned acceptance graph")
        prefix = " ".join(key + "=" + json.dumps(value, ensure_ascii=False, allow_nan=False)
                          for key, value in parameters.items())
        command = "GRAPH.QUERY" if write else "GRAPH.RO_QUERY"
        executed = {"command": command, "graph_key": graph, "query": query,
                    "parameters": parameters, "status": "STARTED"}
        evidence.append(executed)
        response = self.client.execute_command(command, graph, ("CYPHER " + prefix + " " if prefix else "") + query)
        self.check(isinstance(response, (list, tuple)) and len(response) == 3 and
                   len(response[0]) == width and
                   all(len(row) == width for row in response[1]),
                   "real graph response has expected shape")
        rows = [[text(value) for value in row] for row in response[1]]
        executed.update(status="EXECUTED", rows=rows)
        return rows

    def inspect(self, backend, dataset, entry, phase):
        head = self.head(backend)
        evidence = entry.setdefault("graph_inspections", {})[phase] = {"head": head, "queries": []}
        labels = self.query(head["graph"],
                            "MATCH (n) RETURN labels(n)[0], count(n)", {}, 2, evidence["queries"])
        edges = self.query(head["graph"],
                           "MATCH ()-[e]->() RETURN type(e), count(e)", {}, 2, evidence["queries"])
        original = dataset.original()
        expected_labels = {"Snapshot": 1, "Source": len(original["sources"]),
                           "Fact": len(original["facts"]), "Summary": len(original["summaries"])}
        expected_edges = {"SUPPORTED_BY": sum(len(row["sources"]) for row in original["facts"]),
                          "COMPOSED_OF": sum(len(row["facts"]) for row in original["summaries"])}
        evidence.update(expected_labels=expected_labels, expected_edges=expected_edges)
        self.check(sorted(labels) == sorted([key, value] for key, value in expected_labels.items()),
                   phase + ": actual graph label counts")
        self.check(sorted(edges) == sorted([key, value] for key, value in expected_edges.items()),
                   phase + ": actual graph edge counts")
        return head

    def verify(self, engine, entry, phase, expected):
        report = engine.verify_rebuild()
        entry.setdefault("verification_reports", {})[phase] = report
        self.check(self.report_problem(report, engine.dataset, engine.backend.read()) is None,
                   phase + ": full executed verifier evidence")
        self.check(report["status"] == expected, phase + ": verifier " + expected)
        return report

    def inventory(self):
        # Inventory only our UUID namespaces; no database-wide dump or cleanup.
        self.result["graph_keys"] = sorted(
            text(key) for root in self.owned_roots
            for key in self.client.scan_iter(match=root + ":graph:*", count=100)
        )

    def exercise(self, scenario, dataset, backend, engine, entry):
        source = scenario.withdraw_source
        initial = backend.read()
        before_head = self.head(backend)
        preview = engine.preview_impact(source)
        entry["preview"] = preview
        self.check(preview == engine.preview_impact(source), "repeated previews are exactly equal")
        self.check(backend.read() == initial and self.head(backend) == before_head,
                   "preview does not mutate snapshot or head")
        self.reject(lambda: engine.revoke_source(source), APPROVAL_REQUIRED, entry, "unapproved revocation")
        self.check(backend.read() == initial, "unapproved revocation does not mutate")

        unaffected = {key: deepcopy(value) for key, value in initial.state.summaries.items()
                      if key not in preview["affected_summaries"]}
        entry["unaffected_summaries"] = {"before": unaffected}
        if scenario.id == "documentation":
            self.check("installation_summary" in unaffected, "documentation installation summary is unaffected")

        # --approve is the explicit operator action for this scripted trusted boundary.
        token = engine.confirm_preview(preview)
        self.check(backend.read() == initial, "confirmation alone does not mutate persisted memory")
        entry["approval"] = {"explicit_cli_approval": True, "preview_id": preview["preview_id"]}
        revoked = engine.revoke_source(source, confirmation=token)
        entry["operation_id"] = operation_id = revoked["id"]
        entry["operation_reports"] = {"revoked": revoked}
        self.verify(engine, entry, "revoked", "FAIL")
        false_report = engine.repair_memory(operation_id, fault="false_success")
        entry["operation_reports"]["false_success"] = false_report
        self.check(false_report["id"] == operation_id and false_report["status"] == "repaired" and
                   false_report["attempts"] == 1 and false_report["false_success_pending"] is True,
                   "false_success claims repaired on first attempt")
        failed = self.verify(engine, entry, "false_success", "FAIL")
        skipped = false_report["skipped_summary"]
        # Compute exact expected content directly from the original definitions.
        original = dataset.original()
        facts = {row["id"]: row for row in original["facts"]}
        summary = next(row for row in original["summaries"] if row["id"] == skipped)
        members = sorted(key for key in summary["facts"] if set(facts[key]["sources"]) - {source})
        mismatch = {"path": "summaries." + skipped + ".content",
                    "expected": "\n".join(facts[key]["text"] for key in members),
                    "actual": initial.state.summaries[skipped]["content"]}
        entry["expected_false_success_content_mismatch"] = mismatch
        self.check(mismatch["expected"] != mismatch["actual"] and mismatch in failed["mismatches"],
                   "false_success fails exact summary content comparison")
        stale = backend.read()
        entry["unaffected_summaries"]["after_false_success"] = {
            key: deepcopy(stale.state.summaries[key]) for key in unaffected}
        self.check(entry["unaffected_summaries"]["after_false_success"] == unaffected,
                   "unaffected summaries exactly preserved after false_success")
        self.inspect(backend, dataset, entry, "false_success")

        # New backend AND Engine, with a fresh real redis client and identical scope.
        reopened = self.backend(dataset, entry["scope"], entry)
        restarted = self.Engine(dataset, reopened)
        persisted = restarted.get_operation_report(operation_id)
        entry["operation_reports"]["reattached"] = persisted
        self.check(reopened.read() == stale and persisted == false_report,
                   "snapshot and operation persist across backend and engine recreation")
        self.verify(restarted, entry, "reattached_false_success", "FAIL")
        recovered = restarted.repair_memory(operation_id, fault="none")
        entry["operation_reports"]["retry"] = recovered
        self.check(recovered["id"] == operation_id and recovered["status"] == "repaired" and
                   recovered["attempts"] == false_report["attempts"] + 1 and
                   recovered.get("false_success_pending") is False and recovered["error"] is None and
                   all(step["status"] == "COMPLETED" for step in recovered["steps"].values()),
                   "same operation clean retry increments attempt exactly once")
        self.verify(restarted, entry, "retry", "PASS")
        repaired = reopened.read()
        entry["unaffected_summaries"]["after_repair"] = {
            key: deepcopy(repaired.state.summaries[key]) for key in unaffected}
        self.check(entry["unaffected_summaries"]["after_repair"] == unaffected,
                   "unaffected summaries exactly preserved after repair including build_count")
        repaired_head = self.inspect(reopened, dataset, entry, "repaired")
        entry["stale_snapshot"] = {"generation": stale.generation, "revision": stale.revision,
                                   "current_revision": repaired.revision}
        self.check(stale.generation == repaired.generation and stale.revision < repaired.revision,
                   "stale revision CAS test has a newer committed revision")
        self.reject(lambda: reopened.commit(stale, stale.revision), CONFLICT, entry, "stale snapshot CAS")
        self.check(reopened.read() == repaired and self.head(reopened) == repaired_head,
                   "stale CAS leaves current snapshot and graph head untouched")

        reset_preview = restarted.preview_reset()
        entry["reset_preview"] = reset_preview
        self.check(reopened.read() == repaired, "reset preview does not mutate")
        self.reject(lambda: restarted.reset_demo(reset_preview), RESET_APPROVAL_REQUIRED,
                    entry, "unconfirmed reset")
        self.check(reopened.read() == repaired, "unconfirmed reset does not mutate")
        restarted.reset_demo(reset_preview, confirmed=True)
        entry["reset_approval"] = {"explicit_cli_approval": True, "confirmed": True}
        reset = reopened.read()
        self.check(reset.generation != initial.generation and reset.revision == initial.revision == 0 and
                   reset.state == initial.state and reset.operations == {} and reset.events == [],
                   "confirmed reset publishes original dataset in a new generation")
        self.verify(restarted, entry, "reset", "PASS")
        reset_head = self.inspect(reopened, dataset, entry, "reset")
        # Both revisions are zero: rejection specifically tests generation fencing.
        self.reject(lambda: backend.commit(initial, initial.revision), CONFLICT, entry, "stale generation CAS")
        self.reject(lambda: restarted.confirm_preview(preview), STALE_PREVIEW, entry, "stale generation preview")
        self.check(reopened.read() == reset and self.head(reopened) == reset_head,
                   "stale generation attempts leave reset state untouched")
        entry["status"] = "PASS"

    def tamper(self, scope):
        self.result["phase"] = "dedicated graph tampering"
        entry = self.result["tampering"] = {"scope": scope, "status": "STARTED"}
        self.reserve_scope(scope)
        dataset = self.get_scenario("documentation").load()
        backend = self.backend(dataset, scope, entry)
        engine = self.Engine(dataset, backend)
        self.verify(engine, entry, "before_tampering", "PASS")
        head = self.inspect(backend, dataset, entry, "before_tampering")
        queries = entry["queries"] = []
        query = "MATCH (s:Summary {id: $id}) RETURN s.payload"
        rows = self.query(head["graph"], query, {"id": "installation_summary"}, 1, queries)
        self.check(len(rows) == 1, "tampering selects exactly one real Summary node")
        # Payload is the JSON node property, NOT a fabricated snapshot JSON blob.
        before = json.loads(rows[0][0])
        after = deepcopy(before)
        after["content"] = "Deliberately altered acceptance-only summary content."
        entry.update(summary_id="installation_summary", payload_before=before, payload_after=after)
        self.check(before["content"] != after["content"], "tampering changes content only")
        encoded = json.dumps(after, ensure_ascii=False, allow_nan=False, sort_keys=True)
        written = self.query(head["graph"],
                             "MATCH (s:Summary {id: $id}) SET s.payload = $payload RETURN s.payload",
                             {"id": "installation_summary", "payload": encoded}, 1, queries, write=True)
        reread = self.query(head["graph"], query, {"id": "installation_summary"}, 1, queries)
        self.check(written == reread == [[encoded]], "parameterized graph mutation is read back from real node")
        self.check(self.head(backend) == head and
                   backend.read().state.summaries["installation_summary"] == after,
                   "backend reads tampered payload from the unchanged current graph")
        failed = self.verify(engine, entry, "after_tampering", "FAIL")
        self.check(failed["mismatches"] == [{"path": "summaries.installation_summary.content",
                                            "expected": before["content"], "actual": after["content"]}],
                   "real graph tampering produces exactly the content mismatch, not BLOCKED")
        self.inspect(backend, dataset, entry, "after_tampering")
        entry["status"] = "PASS"

    def run(self):
        self.result["phase"] = "module version query"
        evidence = self.result["module_version_query"] = {"command": "MODULE LIST", "status": "STARTED"}
        modules = self.client.execute_command("MODULE", "LIST")
        versions = []
        for row in modules:
            # Accept raw RESP pairs and redis-py's parsed MODULE LIST mapping.
            pairs = row.items() if isinstance(row, dict) else zip(row[::2], row[1::2])
            fields = {text(key): value for key, value in pairs}
            name = text(fields.get("name"))
            # Do not persist module paths, arguments, or other server configuration.
            if name in ("graph", "falkordb", "FalkorDB") and type(fields.get("ver")) is int:
                versions.append({"name": name, "version": fields["ver"]})
        evidence.update(status="EXECUTED", modules=versions,
                        fields_recorded=["name", "ver"])
        self.check(bool(versions), "actual graph module version returned by server")
        scope = self.result["scope"]
        self.reserve_scope(scope)
        cases = []
        for name in ("pricing", "documentation"):
            self.result["phase"] = name + " baseline"
            scenario = self.get_scenario(name)
            dataset = scenario.load()
            entry = self.result["datasets"][name] = {"status": "STARTED"}
            backend = self.backend(dataset, scope, entry)
            engine = self.Engine(dataset, backend)
            self.verify(engine, entry, "baseline", "PASS")
            self.inspect(backend, dataset, entry, "baseline")
            cases.append((scenario, dataset, backend, engine, entry))
        self.check(cases[0][2].target == cases[1][2].target and
                   cases[0][2].dataset_id != cases[1][2].dataset_id and
                   cases[0][2].root_key != cases[1][2].root_key,
                   "same logical scope isolates both datasets by distinct fingerprints")
        for index, case in enumerate(cases):
            self.result["phase"] = case[0].id + " repair and fencing"
            other = cases[1 - index]
            other_snapshot, other_head = other[2].read(), self.head(other[2])
            self.exercise(*case)
            self.check(other[2].read() == other_snapshot and self.head(other[2]) == other_head,
                       "repair and reset preserve other dataset snapshot and head exactly")
            self.verify(other[3], other[4], "after_other_dataset_exercise", "PASS")
        protected = [(case[2].read(), self.head(case[2])) for case in cases]
        self.tamper(self.result["tamper_scope"])
        for case, (snapshot, head) in zip(cases, protected):
            self.check(case[2].read() == snapshot and self.head(case[2]) == head,
                       "dedicated tampering does not affect main acceptance datasets")
            self.verify(case[3], case[4], "final", "PASS")
        self.result["phase"] = "graph key inventory"
        self.inventory()
        self.check(bool(self.result["graph_keys"]), "owned graph keys recorded")

    def close(self):
        for client in self.clients:
            try:
                client.close()
            except Exception:
                pass  # Never echo transport details or mask the acceptance outcome.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", type=loopback_host, default="127.0.0.1")
    parser.add_argument("--port", type=local_port, default=6380)
    parser.add_argument("--approve", action="store_true",
                        help="Explicitly authorize mutations in new UUID acceptance scopes only.")
    args = parser.parse_args(argv)
    if not args.approve:
        parser.error("--approve is required; no database connection was attempted.")
    run_id = uuid4().hex
    destination = ROOT / ".runtime" / "falkordb-validation" / ("run-" + run_id) / "result.json"
    result = {
        "schema_version": 1, "run_id": run_id, "status": "FAIL", "phase": "initialization",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": {"host": args.host, "port": args.port, "db": 0},
        "scope": "acceptance_" + run_id, "tamper_scope": "acceptance_" + uuid4().hex,
        "explicit_approval": True, "datasets": {}, "checks": [], "graph_keys": [],
        "limitations": ["No server-restart durability test or performance claim.",
                        "All acceptance keys and graph versions are retained; nothing is deleted."],
    }
    harness = None
    try:
        # Refuse to overwrite any prior run artifact, before opening a connection.
        destination.parent.mkdir(parents=True, exist_ok=False)
        harness = Acceptance(args, result)
        harness.run()
        result.update(status="PASS", phase="complete")
    except Exception:
        result["failure"] = {
            "phase": result["phase"],
            "message": "Acceptance did not complete successfully. Exception details are withheld.",
        }
        if harness is not None:
            try:
                harness.inventory()
            except Exception:
                result["inventory_incomplete"] = True
    finally:
        if harness is not None:
            harness.close()
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with destination.open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.write("\n")
    except Exception:
        print(json.dumps({"status": "FAIL", "message": "Could not write acceptance result; details withheld."}))
        return 1
    print(json.dumps({"status": result["status"], "result": str(destination.relative_to(ROOT))}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    # Also support direct execution without importing site/environment loaders.
    if __package__ in (None, ""):
        sys.path.insert(0, str(ROOT))
    raise SystemExit(main())