"""Local dataset configuration, independent of the UI and repair machinery.

Select with get_scenario(key="pricing"), then call scenario.load() for a Dataset
and use scenario.withdraw_source for the ordinary confirmed revocation flow.
Version labels are descriptive evidence text only: no supersession is inferred.
"""

from dataclasses import dataclass
from pathlib import Path

from .models import Dataset, FalloutError, ROOT, validate_id


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    path: Path
    withdraw_source: str
    description: str

    def load(self) -> Dataset:
        """Load the standard schema and validate the configured withdrawal target."""
        dataset = Dataset.load(self.path)
        validate_id(self.withdraw_source)
        if self.withdraw_source not in {row["id"] for row in dataset.original()["sources"]}:
            raise FalloutError("Scenario withdrawal source is not in its dataset.")
        return dataset


SCENARIOS = {
    "pricing": Scenario(
        id="pricing",
        label="Pricing and sales",
        path=ROOT / "data" / "demo.json",
        withdraw_source="sales",
        description="Withdraw the sales offer: retain independently supported phone support and remove the unsupported discount.",
    ),
    "documentation": Scenario(
        id="documentation",
        label="API documentation",
        path=ROOT / "data" / "documentation.json",
        withdraw_source="obsolete_bulletin",
        description="Withdraw the obsolete bulletin: retain independently supported pagination, remove unsupported legacy behavior, and leave installation untouched. Version labels are descriptive only; no supersession logic applies.",
    ),
}


def get_scenario(key: str = "pricing") -> Scenario:
    """Return an exact registered key; reject unknown keys rather than fall back."""
    if not isinstance(key, str) or key not in SCENARIOS:
        raise FalloutError("Unknown scenario. Choose pricing or documentation.")
    return SCENARIOS[key]