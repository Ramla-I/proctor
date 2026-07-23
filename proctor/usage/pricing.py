"""Cost computation from the config's pricing table.

Config shape (``[llm.pricing."provider/model"]``, $ per million tokens):

    [llm.pricing."anthropic/claude-opus-4-8"]
    input = 15.0
    cached_input = 1.5
    output = 75.0

Unknown models cost ``None`` — surfaced once as a warning, never a
silent zero (plan §5.2).
"""

from __future__ import annotations

import sys
from typing import Any

from proctor.llm.types import Usage


class PricingTable:
    def __init__(self, table: dict[str, dict[str, float]]) -> None:
        self._table = table
        self._warned: set[str] = set()

    @classmethod
    def from_config(cls, llm_config: dict[str, Any]) -> PricingTable:
        raw = llm_config.get("pricing", {})
        table: dict[str, dict[str, float]] = {}
        if isinstance(raw, dict):
            for key, rates in raw.items():
                if isinstance(rates, dict):
                    table[key] = {
                        name: float(value)
                        for name, value in rates.items()
                        if isinstance(value, (int, float))
                    }
        return cls(table)

    def cost(self, provider: str, model: str, usage: Usage) -> float | None:
        key = f"{provider}/{model}"
        rates = self._table.get(key)
        if rates is None:
            if key not in self._warned:
                self._warned.add(key)
                print(
                    f"warning: no pricing for {key}; cost_usd will be null",
                    file=sys.stderr,
                )
            return None
        uncached = usage.input_tokens - usage.cached_input_tokens
        cost = (
            uncached * rates.get("input", 0.0)
            + usage.cached_input_tokens * rates.get("cached_input", 0.0)
            + usage.output_tokens * rates.get("output", 0.0)
        ) / 1_000_000
        return round(cost, 6)
