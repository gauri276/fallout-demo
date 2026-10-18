# FalkorDB integration

The application now has a real FalkorDB backend. The older in-memory video is
historical evidence only; it does not demonstrate this integration.

## What is stored in FalkorDB

- `Source`, `Fact`, and `Summary` nodes, each with an ID and lossless JSON payload.
- `(Source)-[:SUPPORTED_BY]->(Fact)-[:COMPOSED_OF]->(Summary)` relationships.
- Relationship ordinals preserving the exact original lineage order.
- A `Snapshot` metadata node containing dataset identity, generation, revision,
  operations, attempts and ordered events.
- A Redis head key pointing to the current immutable graph version.

These are real graph nodes and relationships, not only one cached snapshot blob.
Reads execute `GRAPH.RO_QUERY` over the nodes and edges. Parameterized
`GRAPH.QUERY` commands write each candidate version. Deterministic impact and
repair logic still runs in Python over that graph snapshot; this implementation
does not claim server-side traversal acceleration or benchmark improvements.

## Atomic publication and recovery

A write builds an unpublished graph version, seals it, and checks a lossless
storage round trip. Only then does a WATCH/MULTI/SET operation compare the old
head and publish the new one. Stale revisions or generations cannot overwrite
newer work. A partial staging failure does not publish the incomplete graph.

The storage check is **not** the independent verifier. The verifier still rebuilds
expected state from the original dataset and withdrawal history. It can detect
stale content even if the operation claims completion or a stored graph payload
is changed directly.

Repair attempts are fenced to their starting generation and revision. An old
failure cannot mark another worker's later repair as failed. Uncertain publication
is surfaced rather than silently retried. Reads must be refreshed/reconciled.

## Current local deployment

- Server: FalkorDB v4.12.0 image pinned by digest in the Compose file.
- Task-created active container: `fallout-falkordb-rdb-20261018`.
- Endpoint: `127.0.0.1:6380` (loopback only, no public database port).
- Dedicated named volume: `fallout-falkordb-persistent-20261018`, mounted at `/data`.
- RDB snapshots enabled (`save 60 1`); graceful shutdown saves state. AOF disabled.
- New application preview: http://127.0.0.1:8518, **FalkorDB on, AI off**.
- Existing in-memory and AI sessions were not terminated or modified.

The initial task-created `fallout-falkordb-demo-20261018` container was stopped
after detecting that the image entrypoint ignored command-line persistence flags.
It was not deleted. The replacement explicitly invokes `redis-server`.

The subsequent AOF-enabled `fallout-falkordb-persistent-20261018` container
crashed during parameterized graph-command replay on restart. It remains stopped,
and its AOF files are retained. The active RDB-configured container uses the same
dedicated volume and loads the shutdown RDB snapshot; no graph files were deleted.
Do not re-enable AOF on this tested image without resolving and retesting that
failure. Passing write/reconnect tests alone did not catch it.

## Reproduce on a new machine

Install the pinned project requirements, including `redis==5.2.1`. The adapter
uses FalkorDB's Redis protocol directly; it does not require the FalkorDB Python SDK.
With Docker running and port 6380 free:

```sh
docker compose -f compose.falkordb.yml up -d
FALLOUT_BACKEND=falkordb FALLOUT_DB_HOST=127.0.0.1 FALLOUT_DB_PORT=6380 FALLOUT_AI_ENABLED=false .venv/bin/python launch.py --port 8518
```

Do not run a duplicate Compose deployment while the current task-created database
already occupies port 6380. Do not stop unrelated containers or delete volumes.

The UI creates a separate database scope per browser session by default. Scenario
datasets are separated by fingerprint. For intentionally reconnectable demo state,
set `FALLOUT_DB_SCOPE` to a stable lowercase alphanumeric/underscore value before
launch. Using the same scope intentionally shares saved state between sessions.
Persisted operations are available for explicit recovery after reconnect; UI
selection, original before-view snapshot and verification display history are not
all persisted, so run a fresh check after attaching.

Supported process settings:

| Setting | Default |
|---|---|
| `FALLOUT_BACKEND` | `in-memory`; explicitly select `falkordb` |
| `FALLOUT_GRAPH` | fixed namespace `fallout_demo` |
| `FALLOUT_DB_HOST` | `127.0.0.1` |
| `FALLOUT_DB_PORT` | `6380` |
| `FALLOUT_DB_TLS` | `false` |
| `FALLOUT_DB_USERNAME`, `FALLOUT_DB_PASSWORD` | empty; never displayed |
| `FALLOUT_DB_SCOPE` | blank: UI allocates a browser-specific scope |

No private environment file is auto-read. `--load-env` explicitly loads the user's
private settings and overrides process variables. Its deliberately restrictive
literal parser supports only alphanumeric/underscore/dot/hyphen values; use
process environment settings for passwords with other characters. Never place
credentials in scripts, chat, screenshots or committed examples. An enabled remote
database receives synthetic state; AI-off does not mean no database networking.

## Real acceptance

Observed on 18 October 2026:

- 257 offline regression tests PASS (14.996 seconds).
- [Real database acceptance on final RDB configuration](../.runtime/falkordb-validation/run-3910c814f358424e9a9a26b2f661d7da/result.json): PASS.
- [Native browser story](../.runtime/browser-validation/run-1792334154142-85006/result.json): PASS, actual FalkorDB backend asserted, both scenarios, zero browser errors. Captured before the server's persistence mode changed; no UI or adapter code changed afterward.
- [Graceful RDB restart](../.runtime/falkordb-validation/restart_f52d862436974eacac5d30b72dbc5a9b.json): PASS, exact snapshots preserved and same-operation recovery after restart in both datasets.
- Dependencies pass `pip check`; Compose configuration validates.

Restart acceptance is in `tests/falkordb_restart_check.py`. It targets only the
task-created `fallout-falkordb-rdb-20261018` container and requires the explicit
`--approve-dedicated-restart` flag. Do not run while another user is using that
database. Failed AOF restart is not represented as a PASS.

```sh
.venv/bin/python -m tests.falkordb_acceptance --host 127.0.0.1 --port 6380 --approve
```

The explicit approval authorizes mutations only in new UUID-scoped synthetic test
graphs. It exercises both datasets, read-only preview, denied unapproved mutation,
false success, exact FAIL, new-client reconnection, same-operation recovery PASS,
stale-write rejection, confirmed reset generation fencing, and direct graph
tampering detection. It does not delete graphs or mutate existing user scopes.
Reports are written under `.runtime/falkordb-validation/run-*/result.json`.

The native browser task **FALLOUT: FalkorDB native browser story** targets 8518 and
asserts the rendered backend is actually FalkorDB. It creates its own browser
session and uses real UI confirmation controls. The ordinary offline CLI and
isolated verification lab intentionally remain in-memory.

## Boundaries

- Whole-snapshot versioning suits these small fixtures, not an established
  production scaling solution. Old and failed versions are retained indefinitely.
- Reset publishes a new generation; it is not physical deletion or privacy erasure.
- Immutability assumes other clients cannot edit published graph versions. Use
  authentication, ACLs, TLS and network isolation for non-local deployment.
- No garbage collector, cluster routing, migration or automatic fallback.
- RDB persistence is not a guarantee against every failure: recent writes since
  the last saved snapshot can be lost after a crash. No failover, AOF-replay or
  power-loss durability claim is made. This setup tests graceful restart only.
- UI/API approval boundaries are application controls, not database access control.
- GraphQL and FalkorDB are distinct technologies. This adds FalkorDB graph storage;
  it does not introduce a GraphQL API.