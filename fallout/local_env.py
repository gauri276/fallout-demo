"""Explicit private .env loading. Never imported by the UI or AI request path."""

import os

from .models import ROOT


ALLOWED = frozenset({"FALLOUT_BACKEND", "FALLOUT_GRAPH", "FALLOUT_AI_ENABLED",
                     "FALLOUT_AI_PROVIDER", "FALLOUT_AI_MODEL", "GEMINI_API_KEY",
                     "FALLOUT_AI_FREE_TIER_CONFIRMED", "FALLOUT_DB_HOST", "FALLOUT_DB_PORT",
                     "FALLOUT_DB_TLS", "FALLOUT_DB_USERNAME", "FALLOUT_DB_PASSWORD", "FALLOUT_DB_SCOPE"})
ERROR = "Could not load the private .env. Use unique supported KEY=value entries, with no shell commands or interpolation. No values were logged or applied."


def parse_environment(text):
    """Small literal format: optional surrounding quotes, no execution/expansion."""
    values = {}
    try:
        if len(text) > 16384:
            raise ValueError()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or key not in ALLOWED or key in values:
                raise ValueError()
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError()
                value = value[1:-1]
            if len(value) > 256 or any(not (c.isascii() and (c.isalnum() or c in "-_.")) for c in value):
                raise ValueError()
            values[key] = value
        return values
    except Exception:
        raise ValueError(ERROR) from None


def load_private_environment():
    """Only the user's explicit --load-env launch reads the workspace .env.

    File values override stale shell values. Parse everything before applying
    anything. No filesystem search, shell evaluation, logging or file writes.
    """
    path = ROOT / ".env"
    try:
        if path.is_symlink():
            raise ValueError()
        with path.open("r", encoding="utf-8") as handle:
            values = parse_environment(handle.read(16385))
    except Exception:
        raise ValueError(ERROR) from None
    os.environ.update(values)