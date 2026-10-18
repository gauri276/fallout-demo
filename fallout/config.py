"""Explicit backend selection with no fallback and no implicit environment-file reads."""

import os
import re
from dataclasses import dataclass, field

from .database import FalkorDBBackend, InMemoryBackend
from .models import Dataset, FalloutError


@dataclass(frozen=True)
class Config:
    backend: str = "in-memory"
    graph: str = "fallout_demo"
    host: str = "127.0.0.1"
    port: int = 6380
    tls: bool = False
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    scope: str = ""

    @property
    def identity(self):
        # Process-local comparison only; never render credentials or their hashes.
        return (self.backend, self.graph, self.host, self.port, self.tls,
                hash((self.username, self.password)), self.scope)

    @classmethod
    def from_environment(cls):
        backend = os.environ.get("FALLOUT_BACKEND", "in-memory")
        graph = os.environ.get("FALLOUT_GRAPH", "fallout_demo")
        if graph != "fallout_demo":
            raise FalloutError("Only the reserved graph namespace fallout_demo is allowed.")
        if backend not in {"in-memory", "falkordb"}:
            raise FalloutError("FALLOUT_BACKEND must be in-memory or falkordb.")
        if backend == "in-memory":
            return cls(backend, graph)
        host = os.environ.get("FALLOUT_DB_HOST", "127.0.0.1")
        scope = os.environ.get("FALLOUT_DB_SCOPE", "")
        tls = os.environ.get("FALLOUT_DB_TLS", "false")
        try:
            port = int(os.environ.get("FALLOUT_DB_PORT", "6380"))
        except ValueError:
            raise FalloutError("FALLOUT_DB_PORT must be a valid port.") from None
        if not 1 <= port <= 65535 or tls not in {"true", "false"}:
            raise FalloutError("Invalid FalkorDB port or TLS setting.")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,253}", host):
            raise FalloutError("Invalid FalkorDB host; use a hostname, not a URL.")
        if scope and not re.fullmatch(r"[a-z0-9_]{1,64}", scope):
            raise FalloutError("FALLOUT_DB_SCOPE must contain 1–64 lowercase letters, digits or underscores.")
        return cls(backend, graph, host, port, tls == "true",
                   os.environ.get("FALLOUT_DB_USERNAME", ""),
                   os.environ.get("FALLOUT_DB_PASSWORD", ""), scope)


def create_backend(dataset: Dataset, config: Config, scope=None):
    if config.graph != "fallout_demo":
        raise FalloutError("Graph target must be fallout_demo.")
    if config.backend == "in-memory":
        return InMemoryBackend(dataset)
    if config.backend == "falkordb":
        target_scope = scope or config.scope
        if not target_scope:
            raise FalloutError("Explicit FalkorDB demo scope required. No connection was attempted.")
        return FalkorDBBackend(dataset, host=config.host, port=config.port,
                              username=config.username or None, password=config.password or None,
                              tls=config.tls, scope=target_scope, graph=config.graph)
    raise FalloutError("Unknown backend; no automatic fallback is allowed.")