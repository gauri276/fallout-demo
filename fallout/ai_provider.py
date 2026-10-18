"""Opt-in Gemini REST adapter. No SDK, retries, redirects or provider fallback."""

import json
import os
import re
import socket
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class AIError(ValueError):
    """Only fixed, credential-free messages may cross the provider boundary."""


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


@dataclass(frozen=True)
class AISettings:
    enabled: bool = False
    provider: str = "gemini"
    model: str = ""
    api_key: str = field(default="", repr=False)
    free_tier_confirmed: bool = False

    @classmethod
    def from_environment(cls):
        # No .env discovery and no credential reads while disabled.
        enabled = os.environ.get("FALLOUT_AI_ENABLED", "false").lower() == "true"
        provider = os.environ.get("FALLOUT_AI_PROVIDER", "gemini")
        model = os.environ.get("FALLOUT_AI_MODEL", "")
        return cls(enabled, provider, model,
                   os.environ.get("GEMINI_API_KEY", "") if enabled else "",
                   os.environ.get("FALLOUT_AI_FREE_TIER_CONFIRMED", "false").lower() == "true")

    @property
    def problem(self):
        if not self.enabled:
            return "AI is off. No external request will be made. The local demo works without AI."
        if self.provider != "gemini":
            return "Unsupported AI provider. No automatic provider switching is enabled."
        if not re.fullmatch(r"gemini-[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}", self.model):
            return "Set FALLOUT_AI_MODEL to a Gemini model available in your account's free tier. No model is selected automatically."
        if not self.api_key or self.api_key == "YOUR_GEMINI_API_KEY" or not re.fullmatch(r"[A-Za-z0-9_.-]{10,256}", self.api_key):
            return "Set a valid GEMINI_API_KEY in the local process environment. Never paste it into chat."
        if not self.free_tier_confirmed:
            return "Confirm an unbilled project and free-tier model, then set FALLOUT_AI_FREE_TIER_CONFIRMED=true. The app cannot verify your billing tier."
        return None

    @property
    def public_model(self):
        return self.model if re.fullmatch(r"gemini-[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}", self.model) else "not selected"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def response_text(raw):
    """Parse final text only; never display reasoning, signatures or raw errors."""
    if len(raw) > 65536:
        raise AIError("Gemini response exceeded the local size limit. No output was accepted.")
    try:
        document = strict_json(raw.decode("utf-8"))
    except Exception:
        raise AIError("Gemini response was not valid JSON. No output was accepted.") from None
    if not isinstance(document, dict):
        raise AIError("Gemini response had an unexpected structure. No output was accepted.")
    feedback = document.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise AIError("Gemini blocked the prompt. No output was accepted; no automatic retry was attempted.")
    candidates = document.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
        raise AIError("Gemini did not return exactly one answer candidate. No output was accepted.")
    candidate = candidates[0]
    reason = candidate.get("finishReason")
    if reason == "MAX_TOKENS":
        raise AIError("Gemini stopped at the output-token limit (MAX_TOKENS). The answer is incomplete and was rejected. No automatic retry or token-budget increase was attempted.")
    if reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}:
        raise AIError("Gemini blocked the answer under its content policies. No output was accepted.")
    if reason != "STOP":
        # Do not echo arbitrary API-provided finish messages or unknown enum values.
        raise AIError("Gemini did not report a completed answer (STOP). No output was accepted.")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts:
        raise AIError("Gemini completed without answer text. No output was accepted.")
    texts = []
    for part in parts:
        if (not isinstance(part, dict) or not set(part) <= {"text", "thought", "thoughtSignature"}
                or not isinstance(part.get("text"), str)
                or ("thought" in part and type(part["thought"]) is not bool)
                or ("thoughtSignature" in part and not isinstance(part["thoughtSignature"], str))):
            raise AIError("Gemini returned an unsupported response part. Only final text is accepted; tool calls and non-text content are not allowed.")
        # Metadata may accompany text. It is not an instruction or an answer.
        if part.get("thought") is not True:
            texts.append(part["text"])
    text = "".join(texts)
    if not text.strip():
        raise AIError("Gemini completed without final answer text. No output was accepted.")
    if len(text) > 12000:
        raise AIError("Gemini answer exceeded the local text limit. No output was accepted.")
    return text


class GeminiProvider:
    """Implements agent.Provider.complete with NO exposed tools.

    An injected transport is a test fixture and is always labelled mock.
    The production transport sends the key only in the HTTPS header.
    """

    def __init__(self, settings, transport=None):
        self.settings = settings
        self._transport = transport

    def complete(self, messages, tools):
        problem = self.settings.problem
        if problem:
            raise AIError(problem)
        if tools:
            raise AIError("The AI explanation adapter does not accept executable tools.")
        try:
            if (not isinstance(messages, list) or len(messages) != 2
                    or [message["role"] for message in messages] != ["system", "user"]
                    or any(not isinstance(message["content"], str) for message in messages)):
                raise ValueError()
            payload = {
                "systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
                "contents": [{"role": "user", "parts": [{"text": messages[1]["content"]}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 2048, "responseMimeType": "application/json"},
            }
            body = json.dumps(payload).encode("utf-8")
            if len(body) > 24000:
                raise ValueError()
        except Exception:
            raise AIError("AI request is invalid or exceeds the context limit.") from None
        endpoint = "https://generativelanguage.googleapis.com/v1beta/models/" + self.settings.model + ":generateContent"
        request = Request(endpoint, data=body, method="POST", headers={
            "Content-Type": "application/json", "x-goog-api-key": self.settings.api_key,
        })
        try:
            transport = self._transport or build_opener(ProxyHandler({}), NoRedirect())
            with transport.open(request, timeout=15) as response:
                raw = response.read(65537)
                if response.status != 200:
                    raise AIError("Gemini returned an unexpected HTTP status. No output was accepted.")
            text = response_text(raw)
            # Defensive echo redaction, even though the key is never in the prompt.
            text = text.replace(self.settings.api_key, "[REDACTED]")
            return {"content": text, "tool_calls": [],
                    "provenance": "mock" if self._transport is not None else "live"}
        except HTTPError as exc:
            code = exc.code
            exc.close()
            message = {400: "Gemini rejected the request or model configuration.",
                       401: "Gemini authentication failed. Check the local API key.",
                       403: "Gemini access denied. Check account and model permissions.",
                       404: "Gemini model is unavailable. Choose a model available to your free-tier account.",
                       429: "Gemini quota exhausted or rate limited. No retry or paid fallback was attempted."}.get(
                           code, "Gemini service request failed. No retry or fallback was attempted.")
            raise AIError(message) from None
        except (TimeoutError, socket.timeout):
            raise AIError("Gemini request timed out. No automatic retry was attempted.") from None
        except (URLError, OSError):
            raise AIError("Gemini network connection failed. The local demo is still available.") from None
        except AIError:
            raise
        except Exception:
            raise AIError("Gemini response processing failed unexpectedly. No output was accepted; local repair and verification remain available.") from None


def create_provider(settings):
    """Explicit factory: add future providers here, never as automatic fallback."""
    if settings.problem:
        raise AIError(settings.problem)
    return GeminiProvider(settings)