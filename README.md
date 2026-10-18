# FALLOUT

**Failure-Aware Lineage and Memory Repair for AI Agents**

FALLOUT traces source dependencies, repairs affected synthetic memory after
explicit approval, and independently compares the saved result with a clean
reconstruction. **A repair's completion claim is not proof of correctness.**
This is a local workbench with a real, opt-in **FalkorDB backend** and an offline
in-memory backend—not a production deployment or autonomous live-model repair system.

## FalkorDB integration — added 18 October 2026

Real source/fact/summary nodes and lineage relationships are persisted in FalkorDB.
The adapter uses versioned graph snapshots, atomic revision-checked publication,
reconnection and same-operation recovery. The independent verifier reads the actual
stored graph and still reconstructs expected state from original fixture evidence.

**Current database preview: http://127.0.0.1:8518 (FalkorDB on, AI off).**
See [FalkorDB setup, evidence and limits](docs/FALKORDB.md). The earlier recorded
video remains an **in-memory** demonstration and must not be relabeled as FalkorDB.

## Current status — 18 October 2026

| Evidence | Current status |
|---|---|
| Offline regression suite | **257 tests PASS** (14.996 seconds), including adapter round trips, stale-write fencing, connection identity, reconnect recovery, uncertain publication and outage handling |
| Graph design checks | **PASS**: word wrapping, routing, progressive disclosure, geometry, table, palette and component protocol |
| Local application | Successfully starts at **http://127.0.0.1:8516**, with **AI disabled** |
| Browser layout validation | **36 layouts PASS** on historical in-memory 8516: both scenarios × three pages × 1920 / 1440 / 1280 / 1024 / 768 / 390 widths |
| Database-native browser story | **PASS**, both scenarios through actual UI approval, false-success FAIL and same-operation recovery PASS; zero browser errors; [executed report](.runtime/browser-validation/run-1792334154142-85006/result.json) |
| Both scenarios and false-success recovery | Already implemented and covered by the passing offline tests |
| Real FalkorDB, final RDB configuration | **PASS**: both datasets, graph counts, false-success FAIL, same-operation recovery PASS, reconnect, CAS/reset fencing and direct graph tampering; [executed report](.runtime/falkordb-validation/run-3910c814f358424e9a9a26b2f661d7da/result.json) |
| Graceful database restart | **PASS**, exact snapshots and pending operations survive RDB restart; both same-operation recoveries PASS; [executed report](.runtime/falkordb-validation/restart_f52d862436974eacac5d30b72dbc5a9b.json). AOF replay failed on this image and is disabled; no crash-durability claim. |
| Live Gemini | User reports working; no live Gemini request made during database validation |

Earlier browser milestones are not acceptance evidence for this UI. The current
interface uses one graphite instrument surface, a 28px embedded SVG logo,
aligned header controls, dark native fields and a quieter footer. Painted inner
summary text—not just its wrapper—is explicitly styled and measured: recorded
summary text reached **12.75:1** contrast in Chrome. These are targeted DOM checks,
not an axe scan or a full accessibility audit; screenshots are not manually reviewed.

The [workbench](fallout/workbench.py) keeps execution and independent verification
separate. Amber graph highlighting requires a current computed preview, bound to
dataset, generation, revision and withdrawal target. Inspecting another entity
updates the mint selection and caption without changing that target. Idle and
historical graphs are neutral. The [graph component](fallout/graph_component/index.html)
wraps natural language at spaces and assigns separate edge rails. It provides the
single Graph/Relationships switch, with relationships as the narrow-screen default.
Snapshot selection is a separate segmented toolbar. A native relationship fallback
appears if graph rendering raises an error. No engine or verifier semantics changed.

Historical pricing screenshots from native browser actions (prior presentation, not mocked states):
- [Reported completion with independent FAIL](.runtime/browser-validation/run-1792312952617-35347/story-pricing-04-fail-top.png)
- [Exact stale-summary mismatch](.runtime/browser-validation/run-1792312952617-35347/story-pricing-05-exact-mismatch.png)
- [Same-operation recovery PASS with earlier FAIL retained](.runtime/browser-validation/run-1792312952617-35347/story-pricing-06-recovery-top.png)

The browser harness offers separate `FALLOUT_BROWSER_LAYOUT_ONLY=true`,
`FALLOUT_BROWSER_VISUAL_ONLY=true` and `FALLOUT_BROWSER_STORY_ONLY=true` modes.
Select one at a time and set `FALLOUT_BROWSER_TEST_URL=http://127.0.0.1:8516`.
Layout mode is read-only; story mode explicitly confirms synthetic repairs inside
its own browser session. Neither uses live AI. Full mode remains a broader,
separate acceptance run; a layout/story PASS does not imply full acceptance.

## Run locally

The current UI uses **Impact → Repair → Verify → Recover**, with explicit
approval within Repair and fault injection under Advanced. The inspector shows
essential dependencies first; lineage, diagnostics, isolated experiments and
verification explanations start collapsed. Relationship labels and verbose
support are revealed by explicit node activation or relationship details.

Recorded memory is the primary saved before/after experience. Removed and retained
lines come from real snapshots; **Left stale** needs valid current independent
evidence, never a fault flag. Exact text remains available in expandable details.
The lossless projection is tested in [tests/test_memory_view.py](tests/test_memory_view.py).
Transitions are limited to state-driven colors and respect reduced motion.
No engine or verifier algorithm changed.

Open the existing **http://127.0.0.1:8516** preview. Do not launch a duplicate or
stop an existing session. For a later launch when that port is free,
these commands use no environment-file loading:

```sh
cd /Users/deog/demo/graphql
FALLOUT_AI_ENABLED=false FALLOUT_BACKEND=in-memory .venv/bin/python launch.py --port 8516
```

[launch.py](launch.py) binds loopback only, disables telemetry/file watching,
and uses [fallout/runtime.py](fallout/runtime.py) to confine runtime caches and
configuration and prevent Git executable discovery. Without `--port`, the default
is **8501**, not the current preview port. Without `--load-env`, the launcher
does not open a private environment file; process settings still apply.
Choose a free port for a separate server. Stop only your known server with Ctrl+C;
old processes can retain old modules and reports.

### Fresh Mac setup

Use an existing compatible Python installation (code retains Python 3.9
compatibility). Do not upgrade system Python or replace an existing environment.
For a fresh copy, with permission to download the pinned dependencies:

```sh
cd /Users/deog/demo/graphql
python3 --version
python3 -m venv .venv
.venv/bin/python -c "import sys; print(sys.executable); print(sys.prefix != sys.base_prefix)"
mkdir -p .tmp
TMPDIR="$PWD/.tmp" .venv/bin/python -m pip --isolated install --disable-pip-version-check --no-cache-dir -r requirements.txt
.venv/bin/python -m pip --isolated check
```

The interpreter check should show the project-local environment and `True`.
[requirements.txt](requirements.txt) pins the dependencies; Streamlit and redis
are direct dependencies. Fresh-machine installation, Windows compatibility and a
dependency vulnerability audit are not established by the current test results.
Never copy a Mac virtual environment or private runtime artifacts to another host.

## Two scenarios, one schema and repair algorithm

Choose a scenario in the header. These are real datasets loaded by
[fallout/scenarios.py](fallout/scenarios.py), not alternate canned verdicts.

| Scenario | Withdrawal | Expected normal repair |
|---|---|---|
| Pricing withdrawal — [data/demo.json](data/demo.json) | `sales` | Keep `phone_support` via `pricing`; invalidate `discount_20`; rebuild `customer_summary` to “Phone support is available.” |
| API documentation — [data/documentation.json](data/documentation.json) | `obsolete_bulletin` | Keep `cursor_pagination` via `api_reference`; invalidate `legacy_query_tokens`; rebuild `api_overview` and `migration_notes`; leave `installation_summary` unchanged |

Pricing contains **two sources, two facts and one summary**: no extra unaffected
summary. Documentation has **two affected summaries** plus an unaffected installation
summary; tests compare its entire record, including `build_count`. Generic test
fixtures also exercise broader noninterference.

Both use **OR-support**: a fact stays valid while at least one listed source
remains active. Edge directions are exactly:

`(Source)-[:SUPPORTED_BY]->(Fact)-[:COMPOSED_OF]->(Summary)`

Original lineage remains after revocation. `active_support` records current
support; summary `members` contain only valid facts from that summary's own
dependencies. Content is original fact text joined with newlines in **sorted
fact-ID order**, not dependency-list order or LLM prose. Input-record reordering
does not change the result; arbitrary ID renaming can change lexical ordering.
An empty rebuilt summary has empty content/membership and is fresh.

This fixed source → fact → summary schema implements **no arbitrary multihop
traversal, nested summaries, supersession, conflict resolution, or semantic
entailment**. Version/archive labels are descriptive; only explicit withdrawal
changes source validity.

## Workbench and 150-second demonstration script

The workflow guide is navigation, not simulated execution progress:
inspect source → preview impact → approve repair → independently verify →
inject failure → recover. This is a suggested **150-second presentation**, not
a measured timing or completed browser acceptance claim.

| Time | Action and evidence |
|---|---|
| 0–20 s | Open 8509, confirm AI OFF, choose **Pricing withdrawal**, then **Investigate source change**. Explain shared phone support versus the sales-only discount. |
| 20–45 s | In **Investigate & Repair**, select `sales` and **Preview changes**. Show computed preserved/invalidated facts and the affected summary. Preview is read-only. |
| 45–70 s | Click **Confirm removal and repair**. Inspect actual saved facts/summary and the independent result. A normal run should produce PASS; inspect evidence, not just operation status. |
| 70–105 s | Open **Verify & Test → Verification Lab → Run false-success proof**. Compare isolated normal repair with reported completion that leaves a summary stale. Inspect the executed FAIL and exact content mismatch. |
| 105–125 s | Show recovery's same operation ID and separate verification ID. Expected recovery is PASS; the original failed evidence remains visible. |
| 125–150 s | Switch to **API documentation**. Preview `obsolete_bulletin`: two affected summaries, preserved pagination, untouched installation. Switch back to demonstrate preserved pricing state. |

Start with unused scenario state. If its source is already revoked, explicitly
preview, acknowledge and confirm reset before replaying; never reset silently.
For the full second-scenario demonstration, repeat preview/confirm/check and the
false-success proof outside this short presentation window.

### Graph, verification and session behavior

- **Overview**, **Investigate & Repair**, and **Verify & Test** separate context,
	authorization and evidence. Actual results are distinct from preview predictions.
- The local SVG graph represents recorded snapshots. **Click a node** or focus
	it and press Enter/Space to inspect. **Inspect entity** and relationship records
	remain alternatives. Fit, zoom and scroll are camera actions, not mutations.
	Selection-only redraws preserve camera state; changing graph context can reset
	it. Actual new-run graph checks remain incomplete; implemented controls are
	not a browser-success claim.
- Before/current views use recorded snapshots; original input is separately
	identified. Mismatch styling requires current validated evidence.
- UI states distinguish **NOT RUN**, **RUNNING**, **PASS**, **FAIL**, and **ERROR**.
	Malformed/blocked verification maps to ERROR, never PASS. The collapsed ledger
	exposes paths, exact values, mismatch navigation and the complete report.
- Switching scenarios banks each engine, investigation evidence and selections
	in the browser session. It authorizes neither repair nor reset. Reset requires
	preview, acknowledgment and confirmation and clears **only the active scenario's
	working state/history**. Other scenario banks and isolated lab history persist.
- Lab actions create separate fresh normal and fault engines for the selected
	scenario, independent of working memory. False-success recovery retries the
	fault engine's same operation, not a third fresh engine. Runs append scenario-bound
	evidence; initial FAIL and recovery evidence are not overwritten.

## Repair contract and independent verification

[fallout/engine.py](fallout/engine.py) binds approval to source, dataset, generation,
revision and state digest. Only the trusted confirmation path mints a grant;
changed previews need renewed approval. Revocation records an event and marks
affected summaries stale. Repair recomputes support, invalidates unsupported facts
and rebuilds affected summaries using revision-checked snapshot commits, in memory
or via atomic FalkorDB head publication. Computation failures leave approved revocation
in place and record partial failure; stale writes and uncertain publication never
overwrite a newer outcome. Completed ordinary
repairs are idempotent; pending repairs block new revocations.

| Mode | Operation and expected verifier behavior |
|---|---|
| `none` | All stages complete; `repaired`; normal fixture result PASS |
| `skip_invalidation` / `skip_summary` | `repair_incomplete`; mismatched memory and separate incomplete-operation checks yield FAIL |
| `raise_during_repair` | `partial_failure`; revocation remains; FAIL until recovery |
| `false_success` | Deliberately reports **`repaired`** while skipping one affected summary; content comparisons yield FAIL in both supplied scenarios |

For `false_success`, the engine chooses the first sorted affected summary needing
a content change (falling back to the first affected summary if none changes).
`skipped_summary` identifies the omission; `false_success_pending` enables
same-operation retry and blocks new revocations despite the completion claim.
Steps retain SKIPPED/PARTIAL evidence. A successful fault-free retry rebuilds
affected summaries, clears pending metadata and is then idempotent. No affected
summaries means no injected omission.

[fallout/verifier.py](fallout/verifier.py) does not reuse engine repair functions.
It reconstructs expected memory from canonical original input and explicit
source-revocation history, comparing node sets, fields, support, original edges
(including duplicates), summary content/membership/freshness and operation/history
consistency. **It ignores fault flags and retry metadata when judging content.**
Ordinary `operations.incomplete` checks remain separate and inspect operation status;
false-success passes that check but fails content comparison. Tests remove fault
diagnostics and correct freshness/membership and still obtain FAIL from content
alone. `build_count` is diagnostic; preservation is tested separately.

PASS requires a complete, nonempty, independently derived comparison schedule,
consistent results and execution timestamps bound to the current snapshot.
Original data, snapshots and history live in the **same Python process**:
logical independence is not a secured external verifier or tamper-proof ledger.

## Reproduce offline checks (not executed for this edit)

These commands do not load environment files. The full suite clears inherited
process settings before importing tests; AI tests use mocks. With installed UI
dependencies, the supplied baseline is 189 tests. `-S` disables site packages for
the standard-library subset; skipped UI tests would not prove UI behavior.

```sh
cd /Users/deog/demo/graphql
.venv/bin/python -c 'import os, unittest; os.environ.clear(); r=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover("tests")); raise SystemExit(not r.wasSuccessful())'
env -i PATH="$PATH" .venv/bin/python -S -m unittest tests.test_core tests.test_tools tests.test_regressions tests.test_scenarios tests.test_false_success tests.test_lab -q
env -i PATH="$PATH" .venv/bin/python -m tests.acceptance
env -i PATH="$PATH" .venv/bin/python -S -m fallout.cli demo --fault false_success --expect FAIL
```

[tests/test_scenarios.py](tests/test_scenarios.py),
[tests/test_false_success.py](tests/test_false_success.py), and
[tests/test_final_ui.py](tests/test_final_ui.py) cover both datasets, exact stale
content, recovery, unaffected records and scenario preservation. Offline UI tests
do not establish real-browser layout or interaction success.

[fallout/cli.py](fallout/cli.py) uses disposable **pricing-only** memory, not the
browser session; it has no scenario selector. `--expect FAIL` makes an expected
fault verdict a successful harness exit. [tests/acceptance.py](tests/acceptance.py)
runs four modes (`none`, `skip_invalidation`, `skip_summary`, `raise_during_repair`)
and reset/clean-rerun checks; it omits false-success and documentation.
Its optional `--health` is hardcoded to **8501**, so it does not validate 8509.

Only after the current browser run finishes, an optional new run against the
already-running AI-off preview can be requested with:

```sh
FALLOUT_BROWSER_TEST_URL=http://127.0.0.1:8509 node tests/browser_check.mjs
```

[tests/browser_check.mjs](tests/browser_check.mjs) requires existing compatible
Node (Node 24 was used historically) and local Chrome; it uses Node built-ins,
loopback restrictions and per-run artifacts under
[.runtime/browser-validation/](.runtime/browser-validation/). The explicit URL
overrides its older default port. Do not install browsers/packages automatically
or overlap validation runs. Screenshots are not a human visual/accessibility audit;
the harness is separate from the Python test count.

## Optional Gemini — existing integration, not required

The legacy optional panel offers explanations and structured proposals, not
execution authority. **No Gemini requests were made in this work.** The user
historically reported **one live explanation**; that is not full live acceptance,
account/quota validation, or current browser-success evidence.

[fallout/ai_assistant.py](fallout/ai_assistant.py) pins the original **pricing-only**
fingerprint and whitelists transmitted values. Documentation/custom datasets are
blocked before provider use. Plans must exactly match a fresh deterministic
preview; review never replaces explicit repair confirmation. Model text is
unverified explanation, not a verdict. Requests require single-use consent and
a request action; navigation alone makes no request.

For user-controlled setup, supply `FALLOUT_AI_ENABLED=true`,
`FALLOUT_AI_PROVIDER=gemini`, an explicitly chosen `FALLOUT_AI_MODEL`, a private
`GEMINI_API_KEY`, and `FALLOUT_AI_FREE_TIER_CONFIRMED=true` only after checking
project eligibility. There is no default model. The acknowledgement is **not a
spending cap**; use an unbilled project and review provider data terms. Keep keys
out of chat, source, URLs, screenshots and shell-history literals. Use a separate
fresh server/unused port; leave the current preview AI-disabled.

Process variables work without file loading. For optional private-file setup,
[launch.py](launch.py) supports explicit `--load-env`: it reads the workspace
[.env](.env), validates the complete file, then overrides matching process settings.
Use literal `KEY=value`, optional surrounding quotes and full-line comments;
shell expansion/inline comments are unsupported. Loading settings is not request
consent. Neither that file nor [.env.example](.env.example) was inspected for this
edit. Exclude private files manually from archives; ignore rules are not an upload
filter. Restart your own optional server after changing its settings.

The bounded HTTPS adapter has no automatic retry/paid fallback. The separate
six-tool dispatcher and bounded agent loop are not connected to Gemini; its
explanation path exposes no executable tools or confirmation grants.

## Export and safety limits

- **Download verification evidence** exports a current validated report and
	verification ID—not a session backup, engine snapshot or lab archive. It excludes
	session/config dumps, AI prose and grants. Its guard reuses pricing-only
	`safe_graph`, so **documentation export is unavailable**. This is a constrained
	synthetic-data check, not general secret redaction; review artifacts before sharing.
- In-memory state is volatile. FalkorDB graph snapshots and recovery metadata are
	persisted; reconnect using an explicit stable scope. UI selections, verification
	display history and scenario-bank objects remain session-local. Reset publishes a
	new generation, not physical erasure. Exports are not immutable audit evidence.
- Use synthetic data only. Loopback is not authentication against local users or
	processes. Python/runtime, original input, UI and backend are trusted. There are
	no distributed-write, production multi-user or compliance guarantees.
- Real FalkorDB validation is separate from the offline suite; see the linked
	executed reports. No live AI request, Windows validation, cluster failover or
	external security audit was performed during this integration. No automatic
	backend fallback occurs.