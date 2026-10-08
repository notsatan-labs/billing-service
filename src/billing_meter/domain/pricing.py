"""Deterministic proof-of-concept pricing and cost calculation.

Unit prices come from a SHA-256 digest of the resource type, so the same name
always gets the same price across calls, restarts and Python versions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from billing_meter.domain.events import MICROS

CURRENCY = "USD"
# Unit prices are whole ten-thousandths of a dollar: 1 → $0.0001, 100 → $0.0100.
PRICE_SCALE = 10_000
CENTS = 100


def unit_price_ticks(resource_type: str) -> int:
    digest = sha256(resource_type.encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100 + 1


def format_price(ticks: int) -> str:
    whole, fraction = divmod(ticks, PRICE_SCALE)
    return f"{whole}.{fraction:04d}"


def format_cents(cents: int) -> str:
    whole, fraction = divmod(cents, CENTS)
    return f"{whole}.{fraction:02d}"


def cost_cents(quantity_micros: int, price_ticks: int) -> int:
    """quantity × unit price, rounded half-up to the cent, in exact integers."""
    divisor = MICROS * PRICE_SCALE // CENTS
    whole, remainder = divmod(quantity_micros * price_ticks, divisor)
    return whole + (1 if remainder * 2 >= divisor else 0)


@dataclass(frozen=True, slots=True)
class CostLine:
    resource_type: str
    quantity_micros: int
    price_ticks: int
    cost_cents: int


@dataclass(frozen=True, slots=True)
class Costing:
    lines: list[CostLine]
    total_cents: int

    @property
    def pricing_table(self) -> dict[str, str]:
        return {
            line.resource_type: format_price(line.price_ticks) for line in self.lines
        }


def price_usage(totals: Mapping[str, int]) -> Costing:
    """Cost each resource on its aggregated quantity; total = sum of rounded lines."""
    lines = []
    for resource_type in sorted(totals):
        micros = totals[resource_type]
        ticks = unit_price_ticks(resource_type)
        lines.append(CostLine(resource_type, micros, ticks, cost_cents(micros, ticks)))
    return Costing(lines, sum(line.cost_cents for line in lines))
