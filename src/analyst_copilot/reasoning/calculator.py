"""Deterministic numeric execution. The LLM may identify which formula
applies, but it must never be the calculator (design doc: "Execute ...
in Python Decimal or DuckDB. Store the formula, operands, normalized
units, and source evidence IDs in the answer record.")."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal


@dataclass(frozen=True)
class CalculationResult:
    formula: str
    operands: dict[str, Decimal]
    result: Decimal
    unit: str | None
    evidence_ids: list[str]


def percent_change(old: Decimal, new: Decimal, evidence_ids: list[str]) -> CalculationResult:
    if old == 0:
        raise ValueError("percent_change: division by zero (old value is 0)")
    result = ((new - old) / old * Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    return CalculationResult(
        formula="(new - old) / old * 100",
        operands={"old": old, "new": new},
        result=result,
        unit="percent",
        evidence_ids=evidence_ids,
    )


def cagr(
    begin_value: Decimal, end_value: Decimal, periods: int, evidence_ids: list[str]
) -> CalculationResult:
    if begin_value <= 0 or periods <= 0:
        raise ValueError("cagr: begin_value must be > 0 and periods must be > 0")
    ratio = end_value / begin_value
    result = (ratio ** (Decimal(1) / Decimal(periods)) - 1) * Decimal(100)
    result = result.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return CalculationResult(
        formula="((end / begin) ** (1 / periods) - 1) * 100",
        operands={"begin_value": begin_value, "end_value": end_value, "periods": Decimal(periods)},
        result=result,
        unit="percent",
        evidence_ids=evidence_ids,
    )


def difference(a: Decimal, b: Decimal, evidence_ids: list[str], unit: str | None = None) -> CalculationResult:
    return CalculationResult(
        formula="a - b",
        operands={"a": a, "b": b},
        result=a - b,
        unit=unit,
        evidence_ids=evidence_ids,
    )


def ratio(
    numerator: Decimal, denominator: Decimal, evidence_ids: list[str]
) -> CalculationResult:
    if denominator == 0:
        raise ValueError("ratio: denominator is 0")
    result = (numerator / denominator).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
    return CalculationResult(
        formula="numerator / denominator",
        operands={"numerator": numerator, "denominator": denominator},
        result=result,
        unit="ratio",
        evidence_ids=evidence_ids,
    )
