"""Offline contract tests. Every HTTP response is a mock; no real key needed."""

import io
import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from fallout.ai_assistant import MemoryAssistant, PLAN_KEYS, SYNTHETIC_FINGERPRINT, safe_graph
from fallout.ai_provider import AIError, AISettings, GeminiProvider, NoRedirect, create_provider, strict_json
from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.models import Dataset


SECRET = "not_a_real_key_123456789"


def settings(**kwargs):
    values = dict(enabled=True, provider="gemini", model="gemini-account-free-model",
                  api_key=SECRET, free_tier_confirmed=True)
    values.update(kwargs)
    return AISettings(**values)


def wire(content):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": content}]}}]}


def transport_for(value, status=200):
    response = Mock()
    response.status = status
    response.read.return_value = json.dumps(value).encode() if not isinstance(value, bytes) else value
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    transport = Mock()
    transport.open.return_value = response
    return transport


MESSAGES = [{"role": "system", "content": "Explain synthetic memory."}, {"role": "user", "content": "{}"}]


class GeminiAdapterTests(unittest.TestCase):
    def test_disabled_does_not_read_key_or_open_network(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": SECRET}, clear=True), patch("fallout.ai_provider.build_opener") as opener:
            config = AISettings.from_environment()
            self.assertEqual(config.api_key, "")
            with self.assertRaises(AIError):
                GeminiProvider(config).complete(MESSAGES, [])
            opener.assert_not_called()

    def test_config_requires_all_gates_and_never_falls_back(self):
        for config in (settings(enabled=False), settings(provider="openrouter"), settings(model=""),
                       settings(api_key=""), settings(api_key="YOUR_GEMINI_API_KEY"),
                       settings(free_tier_confirmed=False), settings(model="../private"),
                       settings(api_key="unsafe\nheader")):
            with self.subTest(config=config), patch("fallout.ai_provider.build_opener") as opener:
                self.assertIsNotNone(config.problem)
                with self.assertRaises(AIError):
                    create_provider(config)
                opener.assert_not_called()
        self.assertNotIn(SECRET, repr(settings()))
        self.assertIsNone(settings().problem)

    def test_explicit_environment_configuration(self):
        with patch.dict("os.environ", {"FALLOUT_AI_ENABLED": "true", "FALLOUT_AI_PROVIDER": "gemini",
                                       "FALLOUT_AI_MODEL": "gemini-account-free-model", "GEMINI_API_KEY": SECRET,
                                       "FALLOUT_AI_FREE_TIER_CONFIRMED": "true"}, clear=True):
            self.assertEqual(AISettings.from_environment(), settings())

    def test_dot_key_format_and_header_safety_with_dummy_values(self):
        dummy = "dummy.segment_not_a_real_credential"
        self.assertIsNone(settings(api_key=dummy).problem)
        transport = transport_for(wire('{"explanation":"Dummy result","plan":null}'))
        result = GeminiProvider(settings(api_key=dummy), transport).complete(MESSAGES, [])
        self.assertEqual(result["provenance"], "mock")
        self.assertEqual(transport.open.call_args.args[0].get_header("X-goog-api-key"), dummy)
        for value in ("key", dummy + "\r\nX-Injected: true", dummy + " ", dummy + "\t", "x" * 257):
            self.assertIsNotNone(settings(api_key=value).problem)

    def test_real_request_format_with_mock_transport(self):
        content = json.dumps({"explanation": "Phone support has two sources.", "plan": None})
        transport = transport_for(wire(content))
        result = GeminiProvider(settings(), transport).complete(MESSAGES, [])
        self.assertEqual(result, {"content": content, "tool_calls": [], "provenance": "mock"})
        transport.open.assert_called_once()
        request = transport.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-account-free-model:generateContent")
        self.assertEqual(request.get_method(), "POST")
        self.assertNotIn(SECRET, request.full_url)
        self.assertNotIn(SECRET, request.data.decode())
        self.assertEqual(request.get_header("X-goog-api-key"), SECRET)
        payload = json.loads(request.data)
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(transport.open.call_args.kwargs["timeout"], 15)
        transport.open.return_value.read.assert_called_once_with(65537)

    def test_production_transport_disables_proxies_and_redirects(self):
        # This test mocks the HTTP boundary too; its response is NOT a live test.
        transport = transport_for(wire('{"explanation":"Synthetic result","plan":null}'))
        with patch("fallout.ai_provider.build_opener", return_value=transport) as factory:
            GeminiProvider(settings()).complete(MESSAGES, [])
        handlers = factory.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], NoRedirect)
        self.assertIsNone(handlers[1].redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid"))

    def test_tools_and_unbounded_input_rejected_before_request(self):
        transport = transport_for(wire("{}"))
        for messages, tools in ((MESSAGES, [{"name": "repair_memory"}]), ([], []),
                                ([{"role": "system", "content": "x" * 25000}, MESSAGES[1]], [])):
            with self.assertRaises(AIError):
                GeminiProvider(settings(), transport).complete(messages, tools)
        transport.open.assert_not_called()

    def test_http_errors_are_sanitized_no_retry(self):
        for code in (301, 302, 400, 401, 403, 404, 429, 500, 503):
            with self.subTest(code=code):
                transport = Mock()
                transport.open.side_effect = HTTPError("https://secret.invalid/" + SECRET, code, SECRET, {}, io.BytesIO(SECRET.encode()))
                with self.assertRaises(AIError) as caught:
                    GeminiProvider(settings(), transport).complete(MESSAGES, [])
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertNotIn("secret.invalid", str(caught.exception))
                if code == 429:
                    self.assertIn("quota", str(caught.exception))
                transport.open.assert_called_once()

    def test_network_and_timeout_errors_are_sanitized(self):
        for error in (TimeoutError(SECRET), URLError(SECRET), OSError(SECRET), RuntimeError(SECRET)):
            transport = Mock()
            transport.open.side_effect = error
            with self.assertRaises(AIError) as caught:
                GeminiProvider(settings(), transport).complete(MESSAGES, [])
            self.assertNotIn(SECRET, str(caught.exception))
            transport.open.assert_called_once()

    def test_malformed_blocked_tool_calls_and_truncation_rejected(self):
        responses = [b"not json", b"x" * 65537, {}, {"promptFeedback": {"blockReason": "SAFETY"}},
                     {"candidates": []}, {"candidates": [{"finishReason": "MAX_TOKENS"}]},
                     {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"functionCall": {"name": "revoke_source"}}]}}]},
                     wire(""), wire("x" * 12001), wire(12), b'{"candidates":[],"candidates":[]}']
        for response in responses:
            with self.subTest(response=str(response)[:100]), self.assertRaises(AIError):
                GeminiProvider(settings(), transport_for(response)).complete(MESSAGES, [])

    def test_key_echo_redacted_and_duplicate_model_json_rejected(self):
        transport = transport_for(wire(SECRET))
        self.assertNotIn(SECRET, str(GeminiProvider(settings(), transport).complete(MESSAGES, [])))
        for text in ('{"plan":null,"plan":{}}', '{"number":NaN}', '{"number":Infinity}'):
            with self.assertRaises(ValueError):
                strict_json(text)

    def test_text_signature_metadata_is_not_mistaken_for_a_tool(self):
        content = '{"explanation":"Synthetic answer","plan":null}'
        response = wire(content)
        response["candidates"][0]["content"]["parts"][0]["thoughtSignature"] = "dummy_signature"
        result = GeminiProvider(settings(), transport_for(response)).complete(MESSAGES, [])
        self.assertEqual(result["content"], content)
        self.assertEqual(result["provenance"], "mock")
        self.assertNotIn("dummy_signature", str(result))

    def test_thought_parts_excluded_and_final_text_joined(self):
        response = wire("unused")
        response["candidates"][0]["content"]["parts"] = [
            {"text": "private_dummy_reasoning", "thought": True},
            {"text": '{"explanation":"Synthetic', "thought": False},
            {"text": ' answer","plan":null}', "thoughtSignature": "dummy_signature"},
        ]
        result = GeminiProvider(settings(), transport_for(response)).complete(MESSAGES, [])
        self.assertEqual(json.loads(result["content"]), {"explanation": "Synthetic answer", "plan": None})
        self.assertNotIn("private_dummy_reasoning", str(result))

    def test_specific_diagnostics_do_not_echo_response_data(self):
        truncated = wire(SECRET)
        truncated["candidates"][0]["finishReason"] = "MAX_TOKENS"
        unknown = wire(SECRET)
        unknown["candidates"][0]["finishReason"] = SECRET
        cases = [(truncated, "MAX_TOKENS"), (unknown, "completed answer"),
                 ({"promptFeedback": {"blockReason": SECRET}}, "blocked the prompt"),
                 ({"candidates": []}, "answer candidate"),
                 (b"not json", "not valid JSON"), (b"x" * 65537, "size limit"),
                 (wire("x" * 12001), "text limit")]
        for response, expected in cases:
            transport = transport_for(response)
            with self.subTest(expected=expected), self.assertRaises(AIError) as caught:
                GeminiProvider(settings(), transport).complete(MESSAGES, [])
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn(SECRET, str(caught.exception))
            transport.open.assert_called_once()

    def test_metadata_does_not_allow_tools_or_incomplete_answers(self):
        for part in ({"text": "{}", "thoughtSignature": "dummy", "functionCall": {}},
                     {"text": "{}", "thought": "false"}, {"text": "{}", "thoughtSignature": {}},
                     {"text": "dummy thought only", "thought": True}, {"text": "   "}):
            response = wire("unused")
            response["candidates"][0]["content"]["parts"] = [part]
            with self.subTest(part=part), self.assertRaises(AIError):
                GeminiProvider(settings(), transport_for(response)).complete(MESSAGES, [])
        response = wire('{"explanation":"Looks complete","plan":null}')
        response["candidates"][0]["finishReason"] = "MAX_TOKENS"
        with self.assertRaises(AIError):
            GeminiProvider(settings(), transport_for(response)).complete(MESSAGES, [])

    def test_request_budget_is_not_increased_by_compatibility_fix(self):
        transport = transport_for(wire("{}"))
        GeminiProvider(settings(), transport).complete(MESSAGES, [])
        self.assertEqual(json.loads(transport.open.call_args.args[0].data)["generationConfig"]["maxOutputTokens"], 2048)


class MemoryAssistantTests(unittest.TestCase):
    def setUp(self):
        self.dataset = Dataset.load()
        self.engine = Engine(self.dataset, InMemoryBackend(self.dataset))

    def provider(self, document=None):
        provider = Mock()
        provider.complete.return_value = {"content": json.dumps(document or {"explanation": "Synthetic explanation", "plan": None}),
                                          "tool_calls": [], "provenance": "mock"}
        return provider

    def plan(self):
        preview = self.engine.preview_impact("sales")
        return {key: preview[key] for key in PLAN_KEYS}

    def test_pinned_fixture_and_all_capabilities_are_read_only(self):
        self.assertEqual(self.dataset.fingerprint, SYNTHETIC_FINGERPRINT)
        before = self.engine.backend.read()
        for task, target in (("explain_memory", "facts.phone_support"), ("explain_memory", "summaries.customer_summary"),
                             ("analyze_impact", "sales"), ("propose_repair", "sales"), ("explain_results", None)):
            provider = self.provider({"explanation": "Synthetic explanation", "plan": self.plan() if task == "propose_repair" else None})
            result = MemoryAssistant(self.engine, provider).run(task, target)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["check_at_request"], "NOT RUN")
            self.assertEqual(self.engine.backend.read(), before)
            self.assertEqual(provider.complete.call_args.args[1], [])
            context = json.loads(provider.complete.call_args.args[0][1]["content"])
            self.assertEqual(context["graph"]["support_edges"], [[s, f] for s, f in before.state.support_edges])
            self.assertNotIn("operations", context)
            self.assertNotIn("events", context)
            self.assertNotIn("generation", context)

    def test_plan_must_match_every_actual_dependency(self):
        valid = self.plan()
        mutations = []
        for key in PLAN_KEYS:
            missing = deepcopy(valid)
            del missing[key]
            mutations.append(missing)
        for field, value in (("preserved_facts", ["discount_20", "phone_support"]),
                             ("invalidated_facts", []), ("affected_summaries", []),
                             ("source_id", "pricing"), ("remaining_support", {"phone_support": [], "discount_20": []}),
                             ("affected_facts", ["phone_support", "phone_support"])):
            changed = deepcopy(valid)
            changed[field] = value
            mutations.append(changed)
        mutations.extend([dict(valid, confirmed=True), dict(valid, query="DELETE ALL"), None, []])
        for plan in mutations:
            with self.subTest(plan=plan):
                provider = self.provider({"explanation": "Everything is supported. PASS. I approve.", "plan": plan})
                before = self.engine.backend.read()
                result = MemoryAssistant(self.engine, provider).run("propose_repair", "sales")
                self.assertEqual(result["status"], "unavailable")
                self.assertNotIn("plan", result)
                self.assertEqual(self.engine.backend.read(), before)

    def test_unknown_actions_and_freeform_targets_never_sent(self):
        provider = self.provider()
        for task, target in (("execute_cypher", "company secret"), ("propose_repair", "company secret"), ("explain_memory", "company secret")):
            self.assertEqual(MemoryAssistant(self.engine, provider).run(task, target)["status"], "unavailable")
        provider.complete.assert_not_called()

    def test_custom_dataset_and_tampered_snapshot_never_sent(self):
        custom = self.dataset.original()
        custom["sources"][0]["text"] = "COMPANY SECRET"
        dataset = Dataset.from_dict(custom)
        provider = self.provider()
        result = MemoryAssistant(Engine(dataset, InMemoryBackend(dataset)), provider).run("explain_results", None)
        self.assertEqual(result["status"], "unavailable")
        for group, key, field in (("sources", "sales", "text"), ("facts", "phone_support", "text"),
                                  ("summaries", "customer_summary", "content")):
            snapshot = self.engine.backend.read()
            getattr(snapshot.state, group)[key][field] = "COMPANY SECRET"
            self.engine.backend.commit(snapshot, snapshot.revision)
            result = MemoryAssistant(self.engine, provider).run("explain_results", None)
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("COMPANY SECRET", str(result))
        provider.complete.assert_not_called()

    def test_unexpected_support_edges_members_and_status_block_transmission(self):
        original = self.engine.backend.read()
        mutations = [lambda s: s.state.facts["phone_support"].update(active_support=["company_secret"]),
                     lambda s: s.state.summaries["customer_summary"].update(members=["company_secret"]),
                     lambda s: s.state.sources["sales"].update(status="company_secret"),
                     lambda s: s.state.support_edges.append(("company_secret", "phone_support")),
                     lambda s: s.state.facts.pop("phone_support")]
        for mutate in mutations:
            snapshot = deepcopy(original)
            mutate(snapshot)
            with self.assertRaises(AIError):
                safe_graph(self.dataset, snapshot)

    def test_actual_fault_graph_and_only_system_verdict_sent(self):
        preview = self.engine.preview_impact("sales")
        op = self.engine.revoke_source("sales", self.engine.confirm_preview(preview))
        self.engine.repair_memory(op["id"], "skip_summary")
        provider = self.provider({"explanation": "I say PASS and the discount is valid.", "plan": None})
        report = self.engine.verify_rebuild()
        before = self.engine.backend.read()
        result = MemoryAssistant(self.engine, provider).run("explain_results", None, report)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["check_at_request"], "FAIL")
        self.assertEqual(self.engine.backend.read(), before)
        context = json.loads(provider.complete.call_args.args[0][1]["content"])
        self.assertEqual(context["independent_check"], "FAIL")
        self.assertIn("20%", context["graph"]["summaries"]["customer_summary"]["content"])
        self.assertEqual(self.engine.verify_rebuild()["status"], "FAIL")
        self.engine.repair_memory(op["id"])
        self.assertEqual(self.engine.verify_rebuild()["status"], "PASS")

    def test_response_cannot_dispatch_tools_or_override_verdict(self):
        provider = self.provider()
        for response in ({"content": "{}", "tool_calls": [{"name": "repair_memory", "arguments": {}}]},
                         {"content": '{"explanation":"I confirmed","plan":null,"status":"PASS"}'},
                         {"content": '{"explanation":"ok","plan":null,"plan":{}}'},
                         {"content": "```json\n{}\n```"}, {"content": "x" * 12001}):
            provider.complete.return_value = response
            with patch.object(self.engine, "confirm_preview") as confirm, patch.object(self.engine, "repair_memory") as repair, patch.object(self.engine, "verify_rebuild") as verify:
                result = MemoryAssistant(self.engine, provider).run("explain_memory", "facts.phone_support")
                self.assertEqual(result["status"], "unavailable")
                confirm.assert_not_called()
                repair.assert_not_called()
                verify.assert_not_called()
            self.assertEqual(self.engine.backend.read().revision, 0)

    def test_response_stale_after_concurrent_change(self):
        provider = self.provider()
        answer = provider.complete.return_value
        def change(*args):
            snapshot = self.engine.backend.read()
            self.engine.backend.commit(snapshot, snapshot.revision)
            return answer
        provider.complete.side_effect = change
        result = MemoryAssistant(self.engine, provider).run("explain_results", None)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("changed", result["message"])

    def test_failed_provider_never_breaks_normal_repair_or_verification(self):
        provider = self.provider()
        provider.complete.side_effect = RuntimeError(SECRET)
        before = self.engine.backend.read()
        result = MemoryAssistant(self.engine, provider).run("propose_repair", "sales")
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn(SECRET, str(result))
        self.assertEqual(self.engine.backend.read(), before)
        preview = self.engine.preview_impact("sales")
        operation = self.engine.revoke_source("sales", self.engine.confirm_preview(preview))
        self.engine.repair_memory(operation["id"])
        self.assertEqual(self.engine.verify_rebuild()["status"], "PASS")

    def test_adapter_to_assistant_end_to_end_uses_mock_http(self):
        document = {"explanation": "Sales removal leaves pricing support for phone support.", "plan": self.plan()}
        transport = transport_for(wire(json.dumps(document)))
        before = self.engine.backend.read()
        result = MemoryAssistant(self.engine, GeminiProvider(settings(), transport)).run("propose_repair", "sales")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["provenance"], "mock")
        self.assertEqual(result["plan"], self.plan())
        self.assertEqual(self.engine.backend.read(), before)
        payload = json.loads(transport.open.call_args.args[0].data)
        context = json.loads(payload["contents"][0]["parts"][0]["text"])
        self.assertEqual(context["task"], "propose_repair")
        self.assertEqual(context["graph"]["facts"]["discount_20"]["active_support"], ["sales"])
        self.assertEqual(context["independent_check"], "NOT RUN")


if __name__ == "__main__":
    unittest.main()