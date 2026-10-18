"""Validated original input and mutable graph snapshots; no external packages."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parent.parent
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class FalloutError(ValueError):
    """A safe, user-facing validation or state error."""


class PublicationUnknown(FalloutError):
    """A write may have committed; do not turn uncertainty into a normal retry."""


def validate_id(value: Any) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise FalloutError("ID must be 1–64 lowercase letters, digits or underscores, starting with a letter.")
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Dataset:
    """Canonical JSON preserves original input independently of mutable state."""

    canonical: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Dataset":
        if not isinstance(data, dict) or set(data) != {"sources", "facts", "summaries"}:
            raise FalloutError("Dataset must contain only sources, facts and summaries.")
        fields = {"sources": {"id", "text"}, "facts": {"id", "text", "sources"}, "summaries": {"id", "facts"}}
        ids = {}
        for group, expected in fields.items():
            rows = data[group]
            if not isinstance(rows, list) or not rows or len(rows) > 100:
                raise FalloutError("Each dataset group needs 1–100 records.")
            ids[group] = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != expected:
                    raise FalloutError("Invalid fields in " + group)
                key = validate_id(row["id"])
                if key in ids[group]:
                    raise FalloutError("Duplicate ID in " + group + ": " + key)
                ids[group].add(key)
                if "text" in row and (not isinstance(row["text"], str) or not 1 <= len(row["text"]) <= 4000):
                    raise FalloutError("Text must contain 1–4000 characters.")
        for group, relation, target in (("facts", "sources", "sources"), ("summaries", "facts", "facts")):
            for row in data[group]:
                links = row[relation]
                if not isinstance(links, list) or not links or any(not isinstance(x, str) for x in links):
                    raise FalloutError("Dependencies must be a nonempty list of IDs.")
                if len(set(links)) != len(links) or not set(links) <= ids[target]:
                    raise FalloutError("Unknown or duplicate dependency in " + row["id"])
        return cls(json.dumps(data, sort_keys=True))

    @classmethod
    def load(cls, path: Path = ROOT / "data" / "demo.json") -> "Dataset":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def original(self) -> Dict[str, Any]:
        return json.loads(self.canonical)

    @property
    def fingerprint(self) -> str:
        return digest(self.original())


@dataclass
class MemoryState:
    sources: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    facts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summaries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    support_edges: List[Tuple[str, str]] = field(default_factory=list)
    composition_edges: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class Snapshot:
    dataset_id: str
    state: MemoryState
    revision: int = 0
    generation: str = ""
    operations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)


def initial_state(dataset: Dataset) -> MemoryState:
    data = dataset.original()
    state = MemoryState()
    for source in data["sources"]:
        state.sources[source["id"]] = {"text": source["text"], "status": "active"}
    for fact in data["facts"]:
        state.facts[fact["id"]] = {"text": fact["text"], "status": "valid", "active_support": sorted(fact["sources"])}
        state.support_edges.extend((source, fact["id"]) for source in fact["sources"])
    for summary in data["summaries"]:
        members = sorted(summary["facts"])
        state.summaries[summary["id"]] = {
            "members": members, "content": "\n".join(state.facts[key]["text"] for key in members),
            "status": "fresh", "build_count": 0,
        }
        state.composition_edges.extend((fact, summary["id"]) for fact in summary["facts"])
    return state