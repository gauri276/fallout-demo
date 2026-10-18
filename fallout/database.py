"""Backend contract and offline storage; FalkorDB is imported only on demand."""

from abc import ABC, abstractmethod
from copy import deepcopy
from threading import RLock
from uuid import uuid4

from .models import Dataset, FalloutError, Snapshot, initial_state


class Backend(ABC):
    name = "abstract"
    target = "undefined"

    @abstractmethod
    def read(self) -> Snapshot:
        pass

    @abstractmethod
    def commit(self, snapshot: Snapshot, expected_revision: int) -> None:
        """Atomically compare generation/revision and replace the snapshot."""

    @abstractmethod
    def reset(self, dataset: Dataset, expected_generation: str, expected_revision: int) -> None:
        """Reset only the identified backend after confirmation in the service."""


class InMemoryBackend(Backend):
    name = "in-memory"
    supports_demo_reset = True
    target = "session-local synthetic memory (no database)"

    def __init__(self, dataset: Dataset):
        self._lock = RLock()
        self._snapshot = Snapshot(dataset.fingerprint, initial_state(dataset), generation=uuid4().hex)

    def read(self) -> Snapshot:
        with self._lock:
            return deepcopy(self._snapshot)

    def commit(self, snapshot: Snapshot, expected_revision: int) -> None:
        with self._lock:
            if self._snapshot.generation != snapshot.generation or self._snapshot.revision != expected_revision:
                raise FalloutError("Memory changed; refresh the preview before continuing.")
            if snapshot.dataset_id != self._snapshot.dataset_id:
                raise FalloutError("Dataset identity cannot change during an operation.")
            result = deepcopy(snapshot)
            result.revision = expected_revision + 1
            self._snapshot = result

    def reset(self, dataset: Dataset, expected_generation: str, expected_revision: int) -> None:
        with self._lock:
            if self._snapshot.generation != expected_generation or self._snapshot.revision != expected_revision:
                raise FalloutError("Reset target changed; preview reset again.")
            self._snapshot = Snapshot(dataset.fingerprint, initial_state(dataset), generation=uuid4().hex)


def __getattr__(name):
    if name == "FalkorDBBackend":
        from .falkordb_backend import FalkorDBBackend
        return FalkorDBBackend
    raise AttributeError(name)