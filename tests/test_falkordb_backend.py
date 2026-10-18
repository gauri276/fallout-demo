"""Offline raw-command contract tests; these do not emulate a Cypher server."""

import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from fallout import falkordb_backend as storage
from fallout.models import Dataset, FalloutError, MemoryState, Snapshot, initial_state


WatchError = type("WatchError", (Exception,), {"__module__": "redis.exceptions"})


def split_query(text):
    """Require JSON parameter literals followed by an exact known static query."""
    parameters = {}
    if text.startswith("CYPHER "):
        text = text[7:]
        while text.split("=", 1)[0] in {"id", "payload", "records", "edges"}:
            name, text = text.split("=", 1)
            value, end = json.JSONDecoder().raw_decode(text)
            parameters[name] = value
            text = text[end:].lstrip()
    return parameters, text


class FakeClient:
    def __init__(self):
        self.strings = {}
        self.graphs = {}
        self.commands = []
        self.sets = []
        self.versions = {}
        self.before_nx = None
        self.before_execute = None
        self.execute_failure = None
        self.nx_timeout = False
        self.fail_query = False
        self.corrupt_roundtrip = False
        self.transactions = []

    def get(self, key):
        return self.strings.get(key)

    def exists(self, key):
        return key in self.graphs or key in self.strings

    def set(self, key, value, nx=False):
        self.sets.append((key, value, nx))
        if nx and self.before_nx:
            hook, self.before_nx = self.before_nx, None
            hook()
        if nx and key in self.strings:
            return None
        self.strings[key] = value.encode() if isinstance(value, str) else value
        self.versions[key] = self.versions.get(key, 0) + 1
        if nx and self.nx_timeout:
            raise TimeoutError("password=PRIVATE_SECRET")
        return True

    def pipeline(self, transaction=True):
        assert transaction
        pipe = FakePipeline(self)
        self.transactions.append(pipe)
        return pipe

    def execute_command(self, command, key, text):
        self.commands.append((command, key, text))
        if self.fail_query:
            raise RuntimeError("redis://user:PRIVATE_SECRET@host")
        parameters, query = split_query(text)
        assert command in ("GRAPH.QUERY", "GRAPH.RO_QUERY")
        if query == "RETURN 1":
            assert command == "GRAPH.QUERY"
            return [[b"1"], [[1]], []]
        if command == "GRAPH.RO_QUERY":
            graph = self.graphs[key]
            if query == storage._READ_NODES:
                rows = deepcopy(graph["nodes"])
                if self.corrupt_roundtrip:
                    rows = rows[1:]
                width = 5
            else:
                assert query == storage._READ_EDGES
                rows = deepcopy(graph["edges"])
                width = 6
            # Simulate redis-py's byte strings and unspecified query ordering.
            rows = [[cell.encode() if isinstance(cell, str) else cell for cell in row]
                    for row in reversed(rows)]
            return [[b"scalar"] * width, rows, [b"Query internal execution time: 0"]]
        if query == storage._CREATE_META:
            assert key not in self.graphs
            self.graphs[key] = {"nodes": [["Snapshot", 1, parameters["id"], parameters["payload"], "false"]], "edges": []}
        elif query == storage._SEAL:
            self.graphs[key]["nodes"][0][4] = "true"
        elif query in dict(storage._CREATE_NODES).values():
            label = next(label for label in ("Source", "Fact", "Summary") if ":" + label + " " in query)
            for identifier, payload in parameters["records"]:
                self.graphs[key]["nodes"].append([label, 1, identifier, payload, None])
        else:
            assert query in dict(storage._CREATE_EDGES).values()
            relation, source, target = ("SUPPORTED_BY", "Source", "Fact") if "SUPPORTED_BY" in query else ("COMPOSED_OF", "Fact", "Summary")
            for src, dst, ordinal in parameters["edges"]:
                self.graphs[key]["edges"].append([relation, source, src, target, dst, ordinal])
        return [[b"Properties set: 1"]]


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.queued = []
        self.closed = False
        self.executions = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def watch(self, key):
        self.key = key
        self.version = self.client.versions.get(key, 0)

    def get(self, key):
        assert key == self.key
        return self.client.get(key)

    def multi(self):
        pass

    def set(self, key, value):
        self.queued.append((key, value))

    def execute(self):
        self.executions += 1
        if self.client.before_execute:
            hook, self.client.before_execute = self.client.before_execute, None
            hook()
        if self.client.versions.get(self.key, 0) != self.version:
            raise WatchError("Watched variable changed.")
        if self.client.execute_failure == "before":
            raise TimeoutError("PRIVATE_SECRET")
        results = [self.client.set(key, value) for key, value in self.queued]
        if self.client.execute_failure == "after":
            raise TimeoutError("PRIVATE_SECRET")
        if self.client.execute_failure == "watch_after":
            raise WatchError("A ConnectionError occurred PRIVATE_SECRET")
        return results


class FalkorDBBackendTests(unittest.TestCase):
    def setUp(self):
        self.dataset = Dataset.from_dict({
            "sources": [{"id": "s", "text": "Source café"}, {"id": "t", "text": "Source 東京"}],
            "facts": [{"id": "f", "text": "Fact", "sources": ["s", "t"]}],
            "summaries": [{"id": "summary", "facts": ["f"]}],
        })
        self.client = FakeClient()

    def backend(self, **kwargs):
        return storage.FalkorDBBackend(self.dataset, client=self.client, **kwargs)

    def graph(self, backend):
        return self.client.graphs[json.loads(self.client.get(backend.head_key))["graph"]]

    def test_initialization_namespace_attach_and_health(self):
        backend = self.backend(scope="test_12")
        self.assertEqual(backend.name, "falkordb")
        self.assertTrue(backend.supports_demo_reset)
        self.assertEqual(backend.target, "fallout_demo:test_12")
        self.assertEqual(backend.root_key, "{fallout_demo:test_12:" + self.dataset.fingerprint + "}")
        original = backend.read()
        self.assertEqual(original.state, initial_state(self.dataset))
        self.assertEqual(original.revision, 0)
        self.assertTrue(original.generation)
        self.assertEqual(self.backend(scope="test_12").read(), original)
        self.assertEqual(len(self.client.graphs), 1)
        self.assertTrue(any(cmd == "GRAPH.QUERY" and text == "RETURN 1" for cmd, _, text in self.client.commands))
        self.assertTrue(all(key.startswith(backend.root_key) for _, key, _ in self.client.commands))
        self.assertTrue(self.client.sets[0][2])

    def test_scope_validation_does_not_contact_client(self):
        for scope in ("", "a" * 65, "UPPER", "test-1", "{}", "a\n", None):
            with self.subTest(scope=scope), self.assertRaises(FalloutError):
                self.backend(scope=scope)
        with self.assertRaises(FalloutError):
            self.backend(graph="production")
        self.assertEqual(self.client.commands, [])
        self.assertEqual(self.client.sets, [])

    def test_roundtrip_unicode_empty_lists_events_and_edge_order(self):
        backend = self.backend()
        snapshot = backend.read()
        snapshot.state.sources["s"]["text"] = '東京 café " \\ \n CYPHER payload="x" MATCH (n) DELETE n'
        snapshot.state.facts["f"]["active_support"] = []
        snapshot.state.support_edges.reverse()
        snapshot.state.summaries["summary"].update(members=[], content="", status="stale")
        snapshot.operations = {"op": {"empty": [], "nested": {"ok": True, "value": None}}}
        snapshot.events = [{"order": 2, "text": "先"}, {"order": 1}, {"order": 2}]
        expected = deepcopy(snapshot)
        expected.revision += 1
        backend.commit(snapshot, 0)
        self.assertEqual(snapshot.revision, 0)
        self.assertEqual(backend.read(), expected)
        self.assertEqual(len(self.client.graphs), 2)
        transaction = self.client.transactions[-1]
        self.assertEqual(len(transaction.queued), 1)
        self.assertEqual(transaction.queued[0][0], backend.head_key)
        self.assertTrue(transaction.closed)

    def test_empty_graph_groups_and_edges(self):
        backend = self.backend()
        snapshot = backend.read()
        snapshot.state = MemoryState()
        backend.commit(snapshot, 0)
        self.assertEqual(backend.read().state, MemoryState())

    def test_graph_payload_and_edges_are_authoritative_not_cached(self):
        backend = self.backend()
        graph = self.graph(backend)
        fact = next(row for row in graph["nodes"] if row[0] == "Fact")
        payload = json.loads(fact[3])
        payload["status"] = "stale"
        fact[3] = json.dumps(payload)
        graph["edges"] = [row for row in graph["edges"] if row[0] != "SUPPORTED_BY"]
        actual = backend.read()
        self.assertEqual(actual.state.facts["f"]["status"], "stale")
        self.assertEqual(actual.state.support_edges, [])

    def test_reset_changes_generation_not_dataset_and_retains_versions(self):
        backend = self.backend()
        before = backend.read()
        changed = deepcopy(before)
        changed.state.facts["f"]["status"] = "invalid"
        backend.commit(changed, 0)
        backend.reset(self.dataset, before.generation, 1)
        after = backend.read()
        self.assertEqual(after.state, initial_state(self.dataset))
        self.assertEqual(after.revision, 0)
        self.assertNotEqual(after.generation, before.generation)
        self.assertEqual(len(self.client.graphs), 3)
        with self.assertRaisesRegex(FalloutError, "changed"):
            backend.commit(before, 0)
        with self.assertRaisesRegex(FalloutError, "changed"):
            backend.reset(self.dataset, before.generation, 0)
        data = self.dataset.original()
        data["sources"][0]["text"] = "another dataset"
        with self.assertRaisesRegex(FalloutError, "Dataset identity"):
            backend.reset(Dataset.from_dict(data), after.generation, 0)

    def test_stale_and_wrong_identity_commits_do_not_stage(self):
        backend = self.backend()
        stale = backend.read()
        backend.commit(stale, 0)
        with self.assertRaisesRegex(FalloutError, "changed"):
            backend.commit(stale, 0)
        current = backend.read()
        with self.assertRaisesRegex(FalloutError, "changed"):
            backend.commit(current, 0)
        current.dataset_id = "wrong"
        with self.assertRaisesRegex(FalloutError, "Dataset identity"):
            backend.commit(current, 1)
        self.assertEqual(len(self.client.graphs), 2)

    def test_racing_initialization_attaches_winner(self):
        winners = []
        self.client.before_nx = lambda: winners.append(self.backend())
        backend = self.backend()
        self.assertEqual(backend.read(), winners[0].read())
        self.assertEqual(len(self.client.graphs), 2)
        self.assertTrue(all(nx for _, _, nx in self.client.sets))

    def test_initialization_timeout_reconciles_own_head(self):
        self.client.nx_timeout = True
        backend = self.backend()
        self.assertEqual(backend.read().state, initial_state(self.dataset))
        self.assertEqual(len(self.client.sets), 1)

    def test_cas_conflict_retains_stage_without_retry(self):
        backend = self.backend()
        before = backend.read()
        def compete():
            head = json.loads(self.client.get(backend.head_key))
            head["revision"] += 1
            self.client.set(backend.head_key, json.dumps(head))
        self.client.before_execute = compete
        with self.assertRaisesRegex(FalloutError, "changed"):
            backend.commit(before, 0)
        self.assertEqual(len(self.client.graphs), 2)
        self.assertEqual(self.client.transactions[-1].executions, 1)

    def test_head_changes_during_staging_are_rejected_before_execute(self):
        backend = self.backend()
        snapshot = backend.read()
        stage = backend._stage
        def racing_stage(candidate):
            result = stage(candidate)
            head = json.loads(self.client.get(backend.head_key))
            head["generation"] = "another-generation"
            self.client.set(backend.head_key, json.dumps(head))
            return result
        with patch.object(backend, "_stage", side_effect=racing_stage):
            with self.assertRaisesRegex(FalloutError, "changed"):
                backend.commit(snapshot, 0)
        self.assertEqual(self.client.transactions[-1].executions, 0)

    def test_publish_transport_failures_are_reconciled_without_retry(self):
        for mode in ("before", "after", "watch_after"):
            with self.subTest(mode=mode):
                self.client = FakeClient()
                backend = self.backend()
                before = backend.read()
                self.client.execute_failure = mode
                if mode == "before":
                    with self.assertRaisesRegex(FalloutError, "outcome is unknown") as caught:
                        backend.commit(before, 0)
                    self.assertNotIn("PRIVATE_SECRET", str(caught.exception))
                    self.assertTrue(caught.exception.__suppress_context__)
                    self.assertEqual(backend.read(), before)
                else:
                    backend.commit(before, 0)
                    self.assertEqual(backend.read().revision, 1)
                self.assertEqual(len(self.client.graphs), 2)
                self.assertEqual(self.client.transactions[-1].executions, 1)

    def test_missing_module_and_query_failures_are_safe(self):
        self.client.fail_query = True
        with self.assertRaisesRegex(FalloutError, "storage request failed") as caught:
            self.backend(password="PRIVATE_SECRET")
        self.assertNotIn("PRIVATE_SECRET", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(self.client.sets, [])

    def test_roundtrip_failure_never_publishes_and_keeps_stage(self):
        backend = self.backend()
        before = backend.read()
        old_head = self.client.get(backend.head_key)
        self.client.corrupt_roundtrip = True
        with self.assertRaises(FalloutError):
            backend.commit(before, 0)
        self.assertEqual(self.client.get(backend.head_key), old_head)
        self.assertEqual(len(self.client.graphs), 2)

    def test_malformed_head_rejected_without_graph_query(self):
        backend = self.backend()
        original = json.loads(self.client.get(backend.head_key))
        for field, value in (("owner", "foreign"), ("dataset_id", "foreign"),
                             ("revision", True), ("revision", -1), ("generation", ""),
                             ("graph", "production"), ("format", True)):
            with self.subTest(field=field):
                head = dict(original, **{field: value})
                self.client.set(backend.head_key, json.dumps(head))
                count = len(self.client.commands)
                with self.assertRaises(FalloutError):
                    backend.read()
                self.assertEqual(len(self.client.commands), count)
        self.client.set(backend.head_key, '{"format":1,"format":1}')
        with self.assertRaises(FalloutError):
            backend.read()
        del self.client.strings[backend.head_key]
        with self.assertRaisesRegex(FalloutError, "head is missing"):
            backend.read()

    def test_malformed_graph_nodes_metadata_and_edges_rejected(self):
        mutations = [
            lambda g: g["nodes"].append(deepcopy(g["nodes"][1])),
            lambda g: g["nodes"].append(deepcopy(g["nodes"][0])),
            lambda g: g["nodes"][0].__setitem__(4, "false"),
            lambda g: g["nodes"][0].__setitem__(3, "{}"),
            lambda g: g["nodes"][1].__setitem__(0, "Unknown"),
            lambda g: g["nodes"][1].__setitem__(1, 2),
            lambda g: g["nodes"][1].__setitem__(3, "[]"),
            lambda g: g["nodes"][1].__setitem__(3, '{"x":1,"x":2}'),
            lambda g: g["nodes"][1].__setitem__(3, '{"x":NaN}'),
            lambda g: g["edges"][0].__setitem__(0, "UNKNOWN"),
            lambda g: g["edges"][0].__setitem__(1, "Fact"),
            lambda g: g["edges"][0].__setitem__(2, "missing"),
            lambda g: g["edges"][0].__setitem__(5, True),
            lambda g: g["edges"][0].__setitem__(5, 100),
            lambda g: g["edges"].append(deepcopy(g["edges"][0])),
            lambda g: g["edges"][1].__setitem__(2, g["edges"][0][2]),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                self.client = FakeClient()
                backend = self.backend()
                mutate(self.graph(backend))
                with self.assertRaises(FalloutError):
                    backend.read()

    def test_json_coercions_and_dangling_edges_rejected_before_stage(self):
        backend = self.backend()
        mutations = [
            lambda s: s.state.sources["s"].update(value=(1, 2)),
            lambda s: s.state.sources["s"].update(value=float("nan")),
            lambda s: s.state.sources["s"].update(value={1: "bad key"}),
            lambda s: s.state.support_edges.append(("missing", "f")),
            lambda s: s.state.support_edges.append(("s", "f")),
            lambda s: s.events.append("not an object"),
        ]
        for mutate in mutations:
            snapshot = backend.read()
            mutate(snapshot)
            with self.assertRaises(FalloutError):
                backend.commit(snapshot, 0)
        self.assertEqual(len(self.client.graphs), 1)

    def test_bad_raw_responses_are_not_silently_accepted(self):
        backend = self.backend()
        for response in (None, [], [[], [], []], [[b"x"] * 5, [[1]], []]):
            with self.subTest(response=response):
                with patch.object(self.client, "execute_command", return_value=response):
                    with self.assertRaises(FalloutError):
                        backend.read()

    def test_attach_requires_graph_query_not_just_graph_ro_query(self):
        backend = self.backend()
        original = backend.read()
        execute = self.client.execute_command
        def read_only(command, *args):
            if command == "GRAPH.QUERY":
                raise RuntimeError("NOPERM PRIVATE_SECRET")
            return execute(command, *args)
        with patch.object(self.client, "execute_command", side_effect=read_only):
            with self.assertRaisesRegex(FalloutError, "storage request failed"):
                self.backend()
        self.assertEqual(backend.read(), original)
        self.assertEqual(len(self.client.graphs), 1)

    def test_initializer_unknown_outcome_never_retries_set(self):
        with patch.object(self.client, "set", side_effect=TimeoutError("PRIVATE_SECRET")) as setter:
            with self.assertRaisesRegex(FalloutError, "outcome is unknown"):
                self.backend()
            self.assertEqual(setter.call_count, 1)
        self.assertEqual(len(self.client.graphs), 1)
        self.assertEqual(self.client.strings, {})

    def test_reconciliation_read_failure_is_safe_unknown(self):
        backend = self.backend()
        snapshot = backend.read()
        self.client.execute_failure = "after"
        getter = self.client.get
        def maybe_get(key):
            if len(self.client.sets) > 1:
                raise ConnectionError("PRIVATE_SECRET")
            return getter(key)
        with patch.object(self.client, "get", side_effect=maybe_get):
            with self.assertRaisesRegex(FalloutError, "outcome is unknown"):
                backend.commit(snapshot, 0)
        self.assertEqual(backend.read().revision, 1)
        self.assertEqual(self.client.transactions[-1].executions, 1)

    def test_metadata_must_match_head_exactly(self):
        for field, value in (("dataset_id", "other"), ("owner", "other"),
                             ("generation", "other"), ("revision", True),
                             ("revision", 100), ("events", {}), ("operations", [])):
            with self.subTest(field=field, value=value):
                self.client = FakeClient()
                backend = self.backend()
                row = self.graph(backend)["nodes"][0]
                metadata = json.loads(row[3])
                metadata[field] = value
                row[3] = json.dumps(metadata)
                with self.assertRaises(FalloutError):
                    backend.read()

    def test_existing_version_key_is_never_modified(self):
        backend = self.backend()
        snapshot = backend.read()
        graph_key = json.loads(self.client.get(backend.head_key))["graph"]
        old_graph = deepcopy(self.client.graphs[graph_key])
        class Collision:
            hex = graph_key.rsplit(":", 1)[1]
        with patch.object(storage, "uuid4", return_value=Collision()):
            with self.assertRaisesRegex(FalloutError, "changed"):
                backend.commit(snapshot, 0)
        self.assertEqual(self.client.graphs[graph_key], old_graph)
        self.assertEqual(backend.read(), snapshot)

    def test_redis_import_is_not_required_for_offline_client(self):
        with patch.dict("sys.modules", {"redis": None}):
            self.assertEqual(self.backend().read().state, initial_state(self.dataset))
            with self.assertRaisesRegex(FalloutError, "requires an available redis-py client"):
                storage.FalkorDBBackend(self.dataset)


if __name__ == "__main__":
    unittest.main()