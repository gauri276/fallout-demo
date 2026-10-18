"""Opt-in FalkorDB snapshot storage, using redis-py 5.2.1, not the SDK.

Only this module's clients treat published versions as immutable: database ACLs
must prevent other writers from changing them. Each stage uses several graph
commands, seals its metadata, and verifies a storage round trip before publishing
one Redis head string. This is NOT semantic verification, a distributed graph
transaction, or a durability guarantee beyond the server's Redis configuration.

Keys share the hash tag ``{fallout_demo:<scope>:<dataset fingerprint>}``.
``graph`` is a compatibility argument restricted to ``fallout_demo``; it cannot
select an arbitrary existing graph. Reset is restricted to the original dataset.
Old versions and failed/unpublished stages are deliberately never deleted. There
is no garbage collector, migration, retry, fallback, or application wiring here.
Injected clients must provide redis-py's synchronous command/pipeline contract
and disable automatic command retries. Redis Cluster routing is not implemented.
Importing this module neither imports redis nor opens a connection.
"""

import json
import math
import re
from copy import deepcopy
from uuid import uuid4

from .database import Backend
from .models import Dataset, FalloutError, PublicationUnknown, MemoryState, Snapshot, initial_state


_INVALID = "FalkorDB snapshot storage is malformed or inconsistent."
_IO = "FalkorDB storage request failed; no fallback was attempted."
_CONFLICT = "FalkorDB memory changed; refresh before continuing."
_UNKNOWN = "FalkorDB publication outcome is unknown; read and refresh before any further write."
_SCOPE = re.compile(r"[a-z0-9_]{1,64}\Z")
_UUID = re.compile(r"[0-9a-f]{32}\Z")
_HEAD_FIELDS = {"format", "owner", "dataset_id", "generation", "revision", "graph"}
_META_FIELDS = {"format", "owner", "dataset_id", "generation", "revision", "operations", "events"}

# All Cypher structure is static. Only parameter values and Redis keys vary.
_CREATE_META = "CREATE (:Snapshot {id: $id, payload: $payload, sealed: false})"
_CREATE_NODES = (
    ("sources", "UNWIND $records AS r CREATE (:Source {id: r[0], payload: r[1]})"),
    ("facts", "UNWIND $records AS r CREATE (:Fact {id: r[0], payload: r[1]})"),
    ("summaries", "UNWIND $records AS r CREATE (:Summary {id: r[0], payload: r[1]})"),
)
_CREATE_EDGES = (
    ("support_edges", "UNWIND $edges AS e MATCH (a:Source {id: e[0]}), (b:Fact {id: e[1]}) CREATE (a)-[:SUPPORTED_BY {ordinal: e[2]}]->(b)"),
    ("composition_edges", "UNWIND $edges AS e MATCH (a:Fact {id: e[0]}), (b:Summary {id: e[1]}) CREATE (a)-[:COMPOSED_OF {ordinal: e[2]}]->(b)"),
)
_SEAL = "MATCH (m:Snapshot {id: $id}) SET m.sealed = true"
_READ_NODES = "MATCH (n) RETURN labels(n)[0], size(labels(n)), n.id, n.payload, n.sealed"
_READ_EDGES = "MATCH (a)-[e]->(b) RETURN type(e), labels(a)[0], a.id, labels(b)[0], b.id, e.ordinal"


def _invalid():
    raise FalloutError(_INVALID)


def _text(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeError:
            _invalid()
    if not isinstance(value, str):
        _invalid()
    return value


def _json_value(value):
    """Reject coercions (tuple/list, non-string keys, NaN) before persistence."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    _invalid()


def _dump(value):
    try:
        _json_value(value)
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise FalloutError(_INVALID) from None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _load(value):
    try:
        result = json.loads(_text(value), object_pairs_hook=_pairs)
        _json_value(result)
        return result
    except (TypeError, ValueError, RecursionError):
        raise FalloutError(_INVALID) from None


def _revision(value):
    if type(value) is not int or value < 0:
        _invalid()


def _identifier(value):
    # IDs are storage identifiers, not a truth check against the original input.
    if not isinstance(value, str) or not value:
        _invalid()


def _validate_snapshot(snapshot):
    if not isinstance(snapshot, Snapshot) or not isinstance(snapshot.state, MemoryState):
        _invalid()
    _revision(snapshot.revision)
    _identifier(snapshot.dataset_id)
    _identifier(snapshot.generation)
    if type(snapshot.operations) is not dict or type(snapshot.events) is not list:
        _invalid()
    if any(type(item) is not dict for item in snapshot.operations.values()):
        _invalid()
    if any(type(item) is not dict for item in snapshot.events):
        _invalid()
    _dump(snapshot.operations)
    _dump(snapshot.events)
    state = snapshot.state
    for group in (state.sources, state.facts, state.summaries):
        if type(group) is not dict:
            _invalid()
        for key, record in group.items():
            _identifier(key)
            if type(record) is not dict:
                _invalid()
            _dump(record)
    for edges, source, target in (
        (state.support_edges, state.sources, state.facts),
        (state.composition_edges, state.facts, state.summaries),
    ):
        if type(edges) is not list:
            _invalid()
        seen = set()
        for edge in edges:
            if type(edge) is not tuple or len(edge) != 2:
                _invalid()
            for key in edge:
                _identifier(key)
            if edge[0] not in source or edge[1] not in target or edge in seen:
                _invalid()
            seen.add(edge)


def _watch_error(exc):
    # No redis import is needed for an injected/offline client. Subclasses of the
    # real redis.exceptions.WatchError (including connection-loss variants) work.
    return any(cls.__name__ == "WatchError" and cls.__module__ == "redis.exceptions"
               for cls in type(exc).__mro__)


class FalkorDBBackend(Backend):
    """Real, explicit opt-in backend. Constructor initializes or attaches safely."""

    name = "falkordb"
    supports_demo_reset = True

    def __init__(self, dataset=None, host="127.0.0.1", port=6380, username=None,
                 password=None, tls=False, scope="demo", graph="fallout_demo", client=None):
        if not isinstance(scope, str) or not _SCOPE.fullmatch(scope) or graph != "fallout_demo":
            raise FalloutError("FalkorDB requires a safe demo scope and the fixed fallout_demo namespace.")
        if not isinstance(dataset, Dataset):
            raise FalloutError("An explicit original dataset is required. No connection was attempted.")
        try:
            self.dataset_id = dataset.fingerprint
        except Exception:
            raise FalloutError(_INVALID) from None
        self.target = "fallout_demo:" + scope
        self.root_key = "{fallout_demo:" + scope + ":" + self.dataset_id + "}"
        self.head_key = self.root_key + ":head"
        self._version_prefix = self.root_key + ":graph:"
        self._owner = self.root_key
        if client is None:
            try:
                import redis
                from redis.backoff import NoBackoff
                from redis.retry import Retry

                client = redis.Redis(
                    host=host, port=port, username=username, password=password,
                    ssl=tls, decode_responses=False, socket_connect_timeout=5,
                    socket_timeout=10, retry=Retry(NoBackoff(), 0),
                    retry_on_timeout=False, retry_on_error=[],
                )
            except Exception:
                raise FalloutError("FalkorDB requires an available redis-py client and valid connection settings.") from None
        self._client = client
        head = self._head()
        if head is None:
            candidate = Snapshot(self.dataset_id, initial_state(dataset), generation=uuid4().hex)
            staged = self._stage(candidate)
            try:
                won = self._client.set(self.head_key, _dump(staged), nx=True)
            except Exception:
                self._reconcile(staged)
                return
            if won:
                return
            # Another initializer owns the head. Attach its version, never overwrite.
            head = self._head()
            if head is None:
                raise FalloutError(_CONFLICT)
        self._read_version(head)
        # Even attaching requires the writable GRAPH.QUERY command to be present
        # and authorized. RETURN 1 does not mutate the published graph.
        rows = self._query("GRAPH.QUERY", head["graph"], "RETURN 1", {}, 1)
        if rows != [[1]]:
            _invalid()

    def _call(self, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            raise FalloutError(_IO) from None

    def _query(self, command, graph, query, parameters, width=None):
        prefix = " ".join(key + "=" + _dump(value) for key, value in parameters.items())
        text = ("CYPHER " + prefix + " " if prefix else "") + query
        result = self._call(self._client.execute_command, command, graph, text)
        if not isinstance(result, (list, tuple)):
            _invalid()
        if width is None:
            return result  # Write responses can contain only statistics.
        if len(result) != 3 or not isinstance(result[0], (list, tuple)) or len(result[0]) != width:
            _invalid()
        rows = result[1]
        if not isinstance(rows, (list, tuple)):
            _invalid()
        if any(not isinstance(row, (list, tuple)) or len(row) != width for row in rows):
            _invalid()
        return [list(row) for row in rows]

    def _parse_head(self, raw):
        if raw is None:
            return None
        head = _load(raw)
        if type(head) is not dict or set(head) != _HEAD_FIELDS:
            _invalid()
        if type(head["format"]) is not int or head["format"] != 1:
            _invalid()
        if head["owner"] != self._owner or head["dataset_id"] != self.dataset_id:
            _invalid()
        _revision(head["revision"])
        _identifier(head["generation"])
        key = head["graph"]
        if not isinstance(key, str) or not key.startswith(self._version_prefix):
            _invalid()
        if not _UUID.fullmatch(key[len(self._version_prefix):]):
            _invalid()
        return head

    def _head(self):
        return self._parse_head(self._call(self._client.get, self.head_key))

    def _stage(self, snapshot):
        _validate_snapshot(snapshot)
        if snapshot.dataset_id != self.dataset_id:
            raise FalloutError("Dataset identity cannot change in this FalkorDB scope.")
        key = self._version_prefix + uuid4().hex
        # UUIDs are never deliberately reused. Refuse even a coincidental existing
        # key; there is no overwrite/delete path for published or failed versions.
        if self._call(self._client.exists, key):
            raise FalloutError(_CONFLICT)
        metadata = {
            "format": 1, "owner": self._owner, "dataset_id": snapshot.dataset_id,
            "generation": snapshot.generation, "revision": snapshot.revision,
            "operations": snapshot.operations, "events": snapshot.events,
        }
        head = {key_: metadata[key_] for key_ in _HEAD_FIELDS - {"graph"}}
        head["graph"] = key
        self._query("GRAPH.QUERY", key, _CREATE_META, {"id": "__snapshot__", "payload": _dump(metadata)})
        for group, query in _CREATE_NODES:
            # Arrays avoid JSON object keys, which are not Cypher map syntax.
            records = [[identifier, _dump(record)]
                       for identifier, record in getattr(snapshot.state, group).items()]
            if records:
                self._query("GRAPH.QUERY", key, query, {"records": records})
        for group, query in _CREATE_EDGES:
            edges = [[source, target, index]
                     for index, (source, target) in enumerate(getattr(snapshot.state, group))]
            # Preserve endpoint/ordinal correlation across FalkorDB execution
            # plans that reorder repeated endpoints in a batched UNWIND + MATCH.
            for edge in edges:
                self._query("GRAPH.QUERY", key, query, {"edges": [edge]})
        self._query("GRAPH.QUERY", key, _SEAL, {"id": "__snapshot__"})
        if self._read_version(head) != snapshot:
            raise FalloutError("FalkorDB snapshot storage round trip failed; head was not published.")
        return head

    def _read_version(self, head):
        nodes = self._query("GRAPH.RO_QUERY", head["graph"], _READ_NODES, {}, 5)
        edges = self._query("GRAPH.RO_QUERY", head["graph"], _READ_EDGES, {}, 6)
        state = MemoryState()
        groups = {"Source": state.sources, "Fact": state.facts, "Summary": state.summaries}
        metadata = None
        for label, label_count, identifier, payload, sealed in nodes:
            label, identifier = _text(label), _text(identifier)
            if type(label_count) is not int or label_count != 1:
                _invalid()
            _identifier(identifier)
            record = _load(payload)
            if type(record) is not dict:
                _invalid()
            if label == "Snapshot":
                # RESP non-compact graph scalars encode booleans as text.
                if metadata is not None or identifier != "__snapshot__" or not (
                    sealed is True or sealed == "true" or sealed == b"true"
                ):
                    _invalid()
                metadata = record
            else:
                if label not in groups or identifier in groups[label] or sealed is not None:
                    _invalid()
                groups[label][identifier] = record
        if metadata is None or set(metadata) != _META_FIELDS:
            _invalid()
        if type(metadata["format"]) is not int or type(metadata["revision"]) is not int:
            _invalid()
        if any(metadata[field] != head[field] for field in _HEAD_FIELDS - {"graph"}):
            _invalid()
        ordered = {"SUPPORTED_BY": {}, "COMPOSED_OF": {}}
        endpoints = {"SUPPORTED_BY": ("Source", "Fact"), "COMPOSED_OF": ("Fact", "Summary")}
        for relation, source_label, source, target_label, target, ordinal in edges:
            relation, source_label, source, target_label, target = map(
                _text, (relation, source_label, source, target_label, target))
            if relation not in endpoints or (source_label, target_label) != endpoints[relation]:
                _invalid()
            if source not in groups[source_label] or target not in groups[target_label]:
                _invalid()
            if type(ordinal) is not int or ordinal < 0 or ordinal in ordered[relation]:
                _invalid()
            ordered[relation][ordinal] = (source, target)
        for relation, field in (("SUPPORTED_BY", "support_edges"), ("COMPOSED_OF", "composition_edges")):
            positions = ordered[relation]
            if set(positions) != set(range(len(positions))):
                _invalid()
            setattr(state, field, [positions[index] for index in range(len(positions))])
        snapshot = Snapshot(metadata["dataset_id"], state, metadata["revision"],
                            metadata["generation"], metadata["operations"], metadata["events"])
        _validate_snapshot(snapshot)
        return snapshot

    def read(self):
        """Read the actual nodes and edges of one immutable head version."""
        head = self._head()
        if head is None:
            raise FalloutError("FalkorDB snapshot head is missing; no fallback was attempted.")
        return self._read_version(head)

    def _expected_head(self, generation, revision):
        _revision(revision)
        head = self._head()
        if head is None or head["generation"] != generation or head["revision"] != revision:
            raise FalloutError(_CONFLICT)
        return head

    def _reconcile(self, staged):
        try:
            if self._head() == staged:
                return
        except Exception:
            pass
        raise PublicationUnknown(_UNKNOWN) from None

    def _publish(self, previous, staged):
        attempted = False
        try:
            with self._client.pipeline(transaction=True) as pipe:
                pipe.watch(self.head_key)
                if self._parse_head(pipe.get(self.head_key)) != previous:
                    raise FalloutError(_CONFLICT)
                pipe.multi()
                pipe.set(self.head_key, _dump(staged))
                attempted = True
                result = pipe.execute()
                if result != [True]:
                    raise RuntimeError("Unconfirmed publication")
        except Exception as exc:
            if attempted:
                # redis-py may raise WatchError on a lost watched connection, not
                # only a server CAS abort. Equality with our unique graph is the
                # only positive acknowledgement after a transport error.
                if _watch_error(exc) and str(exc) == "Watched variable changed.":
                    raise FalloutError(_CONFLICT) from None
                self._reconcile(staged)
            elif isinstance(exc, FalloutError):
                raise FalloutError(str(exc) if str(exc) in (_CONFLICT, _INVALID) else _IO) from None
            elif _watch_error(exc):
                raise FalloutError(_CONFLICT) from None
            else:
                raise FalloutError(_IO) from None

    def commit(self, snapshot, expected_revision):
        """Publish revision expected_revision + 1 without mutating the caller."""
        candidate = deepcopy(snapshot)
        _validate_snapshot(candidate)
        if candidate.dataset_id != self.dataset_id:
            raise FalloutError("Dataset identity cannot change in this FalkorDB scope.")
        if candidate.revision != expected_revision:
            raise FalloutError(_CONFLICT)
        previous = self._expected_head(candidate.generation, expected_revision)
        candidate.revision = expected_revision + 1
        self._publish(previous, self._stage(candidate))

    def reset(self, dataset, expected_generation, expected_revision):
        """CAS reset of this dataset only, with a fresh generation and revision 0."""
        if not isinstance(dataset, Dataset) or dataset.fingerprint != self.dataset_id:
            raise FalloutError("Dataset identity cannot change in this FalkorDB scope.")
        previous = self._expected_head(expected_generation, expected_revision)
        candidate = Snapshot(self.dataset_id, initial_state(dataset), generation=uuid4().hex)
        self._publish(previous, self._stage(candidate))