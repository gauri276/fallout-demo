"""Offline config/service/UI integration; no private files or database sockets.

The real adapter is exercised with the raw-command FakeClient, not a Cypher
server. Race hooks schedule independent Engines at deterministic publication
boundaries; they do not rely on threads, sleeps, or a shared Engine's RLock.
"""

import importlib.util
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from fallout import cli, config, falkordb_backend as storage
from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.models import Dataset, FalloutError, PublicationUnknown, ROOT, initial_state
from tests.test_falkordb_backend import FakeClient


class OfflineCase(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"FALLOUT_AI_ENABLED": "false"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        for target in ("socket.create_connection", "socket.socket.connect",
                       "socket.socket.connect_ex", "socket.getaddrinfo",
                       "fallout.local_env.load_private_environment"):
            guard = patch(target, side_effect=AssertionError("Offline integration must not read private files or connect"))
            mocked = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mocked.assert_not_called)
        self.dataset = Dataset.load()


class DatabaseConfigIntegrationTests(OfflineCase):
    def test_memory_config_does_not_even_read_database_credentials_or_settings(self):
        for backend in (None, "in-memory"):
            with self.subTest(backend=backend):
                values = {"FALLOUT_BACKEND": backend} if backend else {}
                reads = []

                def guarded_get(key, default=None):
                    reads.append(key)
                    if key not in {"FALLOUT_BACKEND", "FALLOUT_GRAPH"}:
                        raise AssertionError("Memory config read an unrelated setting: " + key)
                    return values.get(key, default)

                with patch.object(config.os.environ, "get", side_effect=guarded_get), \
                        patch("pathlib.Path.open", side_effect=AssertionError("No config files")) as opened, \
                        patch("fallout.config.FalkorDBBackend") as remote:
                    settings = config.Config.from_environment()
                    backend_instance = config.create_backend(self.dataset, settings)
                self.assertEqual(settings, config.Config())
                self.assertEqual(reads, ["FALLOUT_BACKEND", "FALLOUT_GRAPH"])
                self.assertIsInstance(backend_instance, InMemoryBackend)
                self.assertEqual(backend_instance.read().state, initial_state(self.dataset))
                opened.assert_not_called()
                remote.assert_not_called()

    def test_explicit_scope_factory_forwards_validated_connection_settings(self):
        values = {"FALLOUT_BACKEND": "falkordb", "FALLOUT_DB_HOST": "db.example.invalid",
                  "FALLOUT_DB_PORT": "16380", "FALLOUT_DB_TLS": "true",
                  "FALLOUT_DB_USERNAME": "dummy_user", "FALLOUT_DB_PASSWORD": "dummy_password",
                  "FALLOUT_DB_SCOPE": "configured_scope"}
        with patch.dict(os.environ, values, clear=True), patch("fallout.config.FalkorDBBackend") as remote:
            settings = config.Config.from_environment()
            for override, expected in ((None, "configured_scope"), ("browser_scope", "browser_scope")):
                with self.subTest(scope=expected):
                    remote.reset_mock()
                    result = config.create_backend(self.dataset, settings, scope=override)
                    self.assertIs(result, remote.return_value)
                    remote.assert_called_once_with(self.dataset, host="db.example.invalid", port=16380,
                                                   username="dummy_user", password="dummy_password",
                                                   tls=True, scope=expected, graph="fallout_demo")

    def test_factory_requires_scope_and_never_falls_back_on_connection_failure(self):
        with patch("fallout.config.FalkorDBBackend") as remote, \
                patch("fallout.config.InMemoryBackend") as memory:
            with self.assertRaisesRegex(FalloutError, "Explicit.*scope"):
                config.create_backend(self.dataset, config.Config(backend="falkordb"))
            remote.assert_not_called()
            remote.side_effect = FalloutError("offline storage unavailable")
            with self.assertRaisesRegex(FalloutError, "offline storage unavailable"):
                config.create_backend(self.dataset, config.Config(backend="falkordb"), scope="explicit")
            remote.assert_called_once_with(self.dataset, host="127.0.0.1", port=6380,
                                           username=None, password=None, tls=False,
                                           scope="explicit", graph="fallout_demo")
            memory.assert_not_called()

    def test_invalid_environment_is_rejected_before_factory_connection(self):
        for key, value in (("FALLOUT_BACKEND", "automatic"), ("FALLOUT_GRAPH", "production"),
                           ("FALLOUT_DB_PORT", "0"), ("FALLOUT_DB_PORT", "65536"),
                           ("FALLOUT_DB_PORT", "not_a_port"), ("FALLOUT_DB_TLS", "yes"),
                           ("FALLOUT_DB_HOST", "redis://dummy:secret@host"),
                           ("FALLOUT_DB_SCOPE", "other{namespace}")):
            with self.subTest(key=key, value=value), \
                    patch.dict(os.environ, {"FALLOUT_BACKEND": "falkordb", key: value}, clear=True), \
                    patch("fallout.config.FalkorDBBackend") as remote:
                with self.assertRaises(FalloutError):
                    config.create_backend(self.dataset, config.Config.from_environment())
                remote.assert_not_called()

    def test_config_repr_and_str_mask_both_credentials_without_losing_values(self):
        settings = config.Config(backend="falkordb", username="private_user_sentinel",
                                 password="private_password_sentinel", scope="test_scope")
        for rendered in (repr(settings), str(settings), repr({"config": settings})):
            self.assertNotIn("private_user_sentinel", rendered)
            self.assertNotIn("private_password_sentinel", rendered)
            self.assertIn("test_scope", rendered)
        self.assertEqual(settings.username, "private_user_sentinel")
        self.assertEqual(settings.password, "private_password_sentinel")

    def test_cli_main_ignores_hostile_inherited_database_and_ai_environment(self):
        inherited = {"FALLOUT_BACKEND": "falkordb", "FALLOUT_GRAPH": "production",
                     "FALLOUT_DB_HOST": "do-not-connect.example.invalid", "FALLOUT_DB_PORT": "invalid",
                     "FALLOUT_DB_SCOPE": "shared_scope", "FALLOUT_DB_TLS": "true",
                     "FALLOUT_DB_USERNAME": "private_user_sentinel",
                     "FALLOUT_DB_PASSWORD": "private_password_sentinel",
                     "FALLOUT_AI_ENABLED": "true", "GEMINI_API_KEY": "private_ai_sentinel"}
        with patch.dict(os.environ, inherited, clear=True), \
                patch.object(config.Config, "from_environment", side_effect=AssertionError("CLI must ignore environment")) as reader, \
                patch("fallout.config.FalkorDBBackend") as remote:
            for fault, expected in (("none", "PASS"), ("skip_summary", "FAIL"), ("false_success", "FAIL")):
                with self.subTest(fault=fault), patch("sys.argv", ["fallout", "demo", "--fault", fault, "--expect", expected]):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(cli.main(), 0)
                    report = json.loads(output.getvalue())
                    self.assertEqual(report["backend"], "in-memory")
                    self.assertEqual(report["verification"]["status"], expected)
                    for key in ("initial_verification", "reset_verification", "rerun_verification"):
                        self.assertEqual(report[key]["status"], "PASS")
                    for secret in ("private_user_sentinel", "private_password_sentinel", "private_ai_sentinel"):
                        self.assertNotIn(secret, output.getvalue())
                    self.assertEqual(dict(os.environ), inherited)
            reader.assert_not_called()
            remote.assert_not_called()


class RepairFenceIntegrationTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.client = FakeClient()
        self.backend = storage.FalkorDBBackend(self.dataset, scope="race_test", client=self.client)
        self.engine = Engine(self.dataset, self.backend)
        other_backend = storage.FalkorDBBackend(self.dataset, scope="race_test", client=self.client)
        self.other = Engine(self.dataset, other_backend)
        token = self.engine.confirm_preview(self.engine.preview_impact("sales"))
        self.operation_id = self.engine.revoke_source("sales", token)["id"]

    def test_failed_attempt_keeps_revocation_and_retries_same_operation(self):
        before = self.backend.read()
        failed = self.engine.repair_memory(self.operation_id, "raise_during_repair")
        after = self.backend.read()
        self.assertEqual(failed["status"], "partial_failure")
        self.assertEqual(after.state, before.state)
        self.assertEqual(after.state.sources["sales"]["status"], "revoked")
        self.assertEqual(self.engine.verify_rebuild()["status"], "FAIL")
        repaired = self.other.repair_memory(self.operation_id)
        self.assertEqual(repaired["id"], self.operation_id)
        self.assertEqual(repaired["attempts"], 2)
        self.assertEqual(self.engine.verify_rebuild()["status"], "PASS")

    def race_after_start(self, winner, fault):
        commit = self.backend.commit
        saved = []
        counts = []

        def interleave(snapshot, expected_revision):
            commit(snapshot, expected_revision)
            if snapshot.operations[self.operation_id]["status"] == "repairing":
                if winner == "repair":
                    self.other.repair_memory(self.operation_id)
                else:
                    self.other.reset_demo(self.other.preview_reset(), confirmed=True)
                    if winner == "reset_same_revision":
                        # Recreate revision 2 in another generation: revision alone
                        # is insufficient to fence the old attempt (ABA).
                        for _ in range(expected_revision + 1):
                            current = self.other.backend.read()
                            self.other.backend.commit(current, current.revision)
                saved.append(self.other.backend.read())
                counts.append(len(self.client.sets))

        with patch.object(self.backend, "commit", side_effect=interleave):
            with self.assertRaisesRegex(FalloutError, "no newer state was overwritten"):
                self.engine.repair_memory(self.operation_id, fault)
        self.assertEqual(len(saved), 1)
        self.assertEqual(self.backend.read(), saved[0])
        self.assertEqual(len(self.client.sets), counts[0])
        self.assertFalse(any(event["type"] == "repair_failed" for event in saved[0].events))
        self.assertEqual(self.other.verify_rebuild()["status"], "PASS")

    def test_failed_attempt_cannot_overwrite_newer_successful_snapshot(self):
        self.race_after_start("repair", "raise_during_repair")

    def test_failed_attempt_cannot_overwrite_reset(self):
        self.race_after_start("reset", "raise_during_repair")

    def test_failed_attempt_fences_generation_even_when_reset_revision_matches(self):
        self.race_after_start("reset_same_revision", "raise_during_repair")

    def test_successful_stale_attempt_cannot_overwrite_newer_success(self):
        self.race_after_start("repair", "none")

    def test_successful_stale_attempt_cannot_overwrite_reset(self):
        self.race_after_start("reset", "none")

    def test_failure_publication_cas_closes_race_after_fence_read(self):
        stage = self.backend._stage
        saved = []
        counts = []

        def interleave(candidate):
            staged = stage(candidate)
            if candidate.operations[self.operation_id]["status"] == "partial_failure":
                self.other.repair_memory(self.operation_id)
                saved.append(self.other.backend.read())
                counts.append(len(self.client.sets))
            return staged

        with patch.object(self.backend, "_stage", side_effect=interleave):
            with self.assertRaisesRegex(FalloutError, "changed"):
                self.engine.repair_memory(self.operation_id, "raise_during_repair")
        self.assertEqual(len(saved), 1)
        self.assertEqual(self.backend.read(), saved[0])
        self.assertEqual(len(self.client.sets), counts[0])
        self.assertEqual(self.other.verify_rebuild()["status"], "PASS")

    def test_unknown_publication_of_success_is_not_overwritten_by_failure(self):
        stage = self.backend._stage

        def lose_ack(candidate):
            staged = stage(candidate)
            if candidate.operations[self.operation_id]["status"] == "repaired":
                self.client.execute_failure = "after"
            return staged

        with patch.object(self.backend, "_stage", side_effect=lose_ack), \
                patch.object(self.backend, "_reconcile", side_effect=FalloutError(storage._UNKNOWN)):
            with self.assertRaisesRegex(FalloutError, "no newer state was overwritten"):
                self.engine.repair_memory(self.operation_id)
        snapshot = self.backend.read()
        self.assertEqual(snapshot.revision, 3)
        self.assertEqual(snapshot.operations[self.operation_id]["status"], "repaired")
        self.assertFalse(any(event["type"] == "repair_failed" for event in snapshot.events))
        self.assertEqual(len(self.client.sets), 4)  # initialization, revoke, start, finish
        self.assertEqual(self.engine.verify_rebuild()["status"], "PASS")

    def assert_unknown_final_commit(self, mode):
        before = self.backend.read()
        stage = self.backend._stage
        reconcile = self.backend._reconcile
        unknown = PublicationUnknown(storage._UNKNOWN)

        def lose_final_ack(candidate):
            staged = stage(candidate)
            if candidate.operations[self.operation_id]["status"] == "repaired":
                self.client.execute_failure = mode
            return staged

        def reconcile_final(staged):
            if mode == "after":
                # Publication succeeded, but the reconciliation read is unavailable.
                raise unknown
            return reconcile(staged)

        with patch.object(self.backend, "_stage", side_effect=lose_final_ack), \
                patch.object(self.backend, "_reconcile", side_effect=reconcile_final) as checked, \
                patch.object(self.backend, "commit", wraps=self.backend.commit) as committed:
            with self.assertRaises(PublicationUnknown) as caught:
                self.engine.repair_memory(self.operation_id)
        checked.assert_called_once()
        self.assertEqual(str(caught.exception), storage._UNKNOWN)
        if mode == "after":
            self.assertIs(caught.exception, unknown)
        self.assertEqual(committed.call_count, 2)  # start and finish, never partial_failure
        self.assertEqual([call.args[0].operations[self.operation_id]["status"]
                          for call in committed.call_args_list], ["repairing", "repaired"])
        snapshot = self.backend.read()
        self.assertEqual(snapshot.generation, before.generation)
        self.assertEqual(snapshot.revision, 2 if mode == "before" else 3)
        self.assertEqual(snapshot.operations[self.operation_id]["status"],
                         "repairing" if mode == "before" else "repaired")
        self.assertEqual(snapshot.operations[self.operation_id]["attempts"], 1)
        self.assertEqual(snapshot.state.sources["sales"]["status"], "revoked")
        self.assertFalse(any(event["type"] == "repair_failed" for event in snapshot.events))
        self.assertEqual(len(self.client.sets), 3 if mode == "before" else 4)
        if mode == "before":
            self.assertEqual(snapshot.state, before.state)
        self.assertEqual(self.engine.verify_rebuild()["status"], "FAIL" if mode == "before" else "PASS")

    def test_unknown_final_commit_before_publication_is_rethrown_without_partial_failure(self):
        self.assert_unknown_final_commit("before")

    def test_unknown_final_commit_after_publication_is_rethrown_without_partial_failure(self):
        self.assert_unknown_final_commit("after")


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Optional Streamlit dependency is not installed")
class FalkorDBWorkbenchIntegrationTests(OfflineCase):
    def setUp(self):
        super().setUp()
        from fallout.runtime import local_runtime

        local_runtime()
        from streamlit.testing.v1 import AppTest

        self.app_test = AppTest
        client = self.client = FakeClient()
        constructions = self.constructions = []

        class OfflineFalkorDB(storage.FalkorDBBackend):
            def __init__(self, *args, **kwargs):
                constructions.append(dict(kwargs))
                super().__init__(*args, **kwargs, client=client)

        self.backend_class = OfflineFalkorDB
        os.environ.update(FALLOUT_BACKEND="falkordb", FALLOUT_DB_SCOPE="ui_test")
        factory = patch("fallout.config.FalkorDBBackend", OfflineFalkorDB)
        factory.start()
        self.addCleanup(factory.stop)

    def app(self):
        return self.app_test.from_file(str(ROOT / "app.py"), default_timeout=20).run()

    def test_config_identity_mismatch_blocks_ui_without_reading_or_redirecting_storage(self):
        app = self.app()
        self.assertEqual(len(app.exception), 0)
        engine = app.session_state.engine
        before = engine.backend.read()
        identity = app.session_state.database_config_identity
        writes = len(self.client.sets)
        for key, value in (("FALLOUT_BACKEND", "in-memory"),
                           ("FALLOUT_DB_HOST", "changed.example.invalid"),
                           ("FALLOUT_DB_PORT", "16380"), ("FALLOUT_DB_TLS", "true"),
                           ("FALLOUT_DB_SCOPE", "other_scope"),
                           ("FALLOUT_DB_USERNAME", "synthetic_username_sentinel"),
                           ("FALLOUT_DB_PASSWORD", "synthetic_password_sentinel")):
            with self.subTest(setting=key), patch.dict(os.environ, {key: value}), \
                    patch.object(engine.backend, "read", wraps=engine.backend.read) as read:
                app.run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any("Database configuration changed" in item.value for item in app.error))
                self.assertFalse(app.button)
                self.assertIs(app.session_state.engine, engine)
                self.assertEqual(app.session_state.database_config_identity, identity)
                self.assertNotIn("synthetic_username_sentinel", str(app))
                self.assertNotIn("synthetic_password_sentinel", str(app))
                read.assert_not_called()
            self.assertEqual(engine.backend.read(), before)
            self.assertEqual(len(self.client.sets), writes)
            self.assertEqual(len(self.constructions), 1)
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_reconnect_restores_pending_operation_and_only_explicit_retry_repairs(self):
        for fault in (None, "publication_unknown", "raise_during_repair", "skip_summary", "false_success"):
            with self.subTest(fault=fault), patch.dict(os.environ, {"FALLOUT_DB_SCOPE": "reconnect_" + str(fault).lower()}):
                backend = storage.FalkorDBBackend(self.dataset, scope=os.environ["FALLOUT_DB_SCOPE"], client=self.client)
                original_engine = Engine(self.dataset, backend)
                token = original_engine.confirm_preview(original_engine.preview_impact("sales"))
                operation_id = original_engine.revoke_source("sales", token)["id"]
                if fault == "publication_unknown":
                    # Leave a real persisted 'repairing' attempt, as after a
                    # process disconnect with an unacknowledged final commit.
                    stage = backend._stage

                    def lose_final_ack(candidate):
                        staged = stage(candidate)
                        if candidate.operations[operation_id]["status"] == "repaired":
                            self.client.execute_failure = "before"
                        return staged

                    try:
                        with patch.object(backend, "_stage", side_effect=lose_final_ack), \
                                self.assertRaises(PublicationUnknown):
                            original_engine.repair_memory(operation_id)
                    finally:
                        self.client.execute_failure = None
                elif fault:
                    original_engine.repair_memory(operation_id, fault)
                before = backend.read()
                self.assertEqual(before.operations[operation_id]["status"], {
                    None: "revoked", "publication_unknown": "repairing",
                    "raise_during_repair": "partial_failure", "skip_summary": "repair_incomplete",
                    "false_success": "repaired",
                }[fault])
                if fault == "false_success":
                    self.assertTrue(before.operations[operation_id]["false_success_pending"])
                writes, graphs = len(self.client.sets), len(self.client.graphs)
                app = self.app()  # Fresh browser session, same actual adapter namespace.
                engine = app.session_state.engine
                self.assertIsInstance(engine.backend, self.backend_class)
                self.assertIsNot(engine, original_engine)
                self.assertIsNot(engine.backend, backend)
                self.assertEqual(engine.backend.head_key, backend.head_key)
                self.assertEqual(app.session_state.operation_id, operation_id)
                self.assertFalse(engine._grants)
                with patch.object(engine, "repair_memory", wraps=engine.repair_memory) as repair, \
                        patch.object(engine, "revoke_source", wraps=engine.revoke_source) as revoke:
                    app.radio(key="section").set_value("Verify & Test").run()
                    app.run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(app.session_state.operation_id, operation_id)
                    self.assertFalse(app.button(key="retry").disabled)
                    self.assertEqual(backend.read(), before)
                    self.assertEqual(len(self.client.sets), writes)
                    self.assertEqual(len(self.client.graphs), graphs)
                    repair.assert_not_called()
                    revoke.assert_not_called()
                    app.button(key="retry").click().run()
                    repair.assert_called_once_with(operation_id)
                    revoke.assert_not_called()
                self.assertEqual(len(app.exception), 0)
                repaired = backend.read()
                self.assertEqual(list(repaired.operations), [operation_id])
                self.assertEqual(repaired.operations[operation_id]["status"], "repaired")
                self.assertEqual(repaired.operations[operation_id]["fault"], "none")
                self.assertFalse(repaired.operations[operation_id].get("false_success_pending", False))
                self.assertEqual(repaired.operations[operation_id]["attempts"],
                                 before.operations[operation_id]["attempts"] + 1)
                self.assertEqual(repaired.revision, before.revision + 2)
                self.assertEqual(app.session_state.verification["status"], "PASS")
                self.assertFalse(any(button.key == "retry" for button in app.button))

    def test_database_outage_during_verification_stores_and_renders_blocked(self):
        from fallout import workbench

        for phase in ("verifier_read", "followup_read"):
            with self.subTest(phase=phase):
                app = self.app()
                app.radio(key="section").set_value("Verify & Test").run()
                app.button(key="verify").click().run()
                self.assertEqual(app.session_state.verification["status"], "PASS")
                previous_id = app.session_state.verification_id
                engine = app.session_state.engine
                before = engine.backend.read()
                writes = len(self.client.sets)
                verify = engine.verify_rebuild

                def verify_with_outage():
                    if phase == "verifier_read":
                        self.client.fail_query = True
                    report = verify()
                    self.client.fail_query = True
                    return report

                # Rendering's initial snapshot succeeds; fail only inside the
                # actual run_verification call, including its follow-up read.
                try:
                    with patch.object(engine, "verify_rebuild", side_effect=verify_with_outage) as checked, \
                            patch.object(workbench, "run_verification", wraps=workbench.run_verification) as run:
                        app.button(key="verify").click().run()
                    run.assert_called_once_with(engine)
                    checked.assert_called_once_with()
                finally:
                    self.client.fail_query = False
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any("Database unavailable; no current PASS" in item.value for item in app.error))
                report = app.session_state.verification
                self.assertEqual(report["status"], "BLOCKED")
                self.assertFalse(report["complete"])
                self.assertEqual(report["comparisons"], [])
                self.assertEqual(report["dataset_id"], engine.dataset.fingerprint)
                self.assertIsNone(report["revision"])
                self.assertIsNone(report["generation"])
                self.assertNotEqual(app.session_state.verification_id, previous_id)
                self.assertNotIn("PRIVATE_SECRET", str(report) + str(app))
                self.assertEqual(engine.backend.read(), before)
                self.assertEqual(len(self.client.sets), writes)
                app.run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.success), 0)
                self.assertTrue(any("ERROR" in item.value for item in app.error))
                self.assertEqual(app.session_state.verification, report)
                self.assertEqual(engine.backend.read(), before)

    def test_real_factory_adapter_ui_repair_scenario_banking_and_scoped_reset(self):
        app = self.app()
        self.assertEqual(len(app.exception), 0)
        engine = app.session_state.engine
        self.assertIsInstance(engine.backend, self.backend_class)
        self.assertEqual(self.constructions[0]["scope"], "ui_test")
        original = engine.backend.read()
        app.button(key="try_demo").click().run()
        app.button(key="preview").click().run()
        self.assertEqual(engine.backend.read(), original)
        app.button(key="confirm").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.verification["status"], "PASS")
        repaired = engine.backend.read()
        self.assertEqual(repaired.state.sources["sales"]["status"], "revoked")
        self.assertEqual(repaired.revision, 3)
        app.selectbox(key="scenario_choice").select("documentation").run()
        self.assertEqual(len(app.exception), 0)
        other = app.session_state.engine
        other_snapshot = other.backend.read()
        self.assertNotEqual(other.backend.head_key, engine.backend.head_key)
        self.assertEqual(self.constructions[1]["scope"], "ui_test")
        app.selectbox(key="scenario_choice").select("pricing").run()
        self.assertIs(app.session_state.engine, engine)
        self.assertEqual(engine.backend.read(), repaired)
        app.button(key="preview_reset").click().run()
        self.assertTrue(app.button(key="confirm_reset").disabled)
        app.checkbox(key="reset_ack").check().run()
        self.assertEqual(engine.backend.read(), repaired)
        app.button(key="confirm_reset").click().run()
        self.assertEqual(len(app.exception), 0)
        reset = engine.backend.read()
        self.assertNotEqual(reset.generation, original.generation)
        self.assertEqual(reset.revision, 0)
        self.assertEqual(reset.state, initial_state(engine.dataset))
        self.assertEqual(other.backend.read(), other_snapshot)
        self.assertEqual(len(self.constructions), 2)


if __name__ == "__main__":
    unittest.main()