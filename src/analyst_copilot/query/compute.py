"""The calculator - Decimal in, Decimal out (D5, §20.5).

⚠️ THE MODEL NEVER DOES ARITHMETIC. It may identify WHICH formula applies; the
numbers are evaluated here. `FinAgent-RAG` measured that program-of-thought
execution removes **88.0% of arithmetic errors**, which is why D5 exists.

⚠️ NEVER `eval()` MODEL-SUPPLIED TEXT. A formula reaches this module as a
string, and at level 3 of the precedence ladder that string was written by an
LLM. It is parsed to an AST and every node outside the whitelist is rejected, so
a formula can compute a number and nothing else - no attribute access, no calls
to anything but the named helpers, no imports, no comprehensions.

`Decimal`, never `float`: a binary float cannot represent 0.1, and a cent of
drift on a ratio is the difference between matching gold and scoring -1.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any, Callable

# The ONLY operators a formula may use.
_ALLOWED_BINOPS: dict[type, Callable[[Decimal, Decimal], Decimal]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a ** b,
}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.USub, ast.UAdd,
    ast.Name, ast.Load, ast.Call, ast.Constant, ast.Tuple,
    *_ALLOWED_BINOPS,
)


class FormulaError(ValueError):
    """A formula that cannot be evaluated safely or completely.

    Always terminal: the caller abstains rather than guessing an operand.
    """


@dataclass
class Operand:
    name: str
    value: Decimal
    unit: str | None = None
    scale: str | None = None
    period: str | None = None
    citation: dict[str, Any] = field(default_factory=dict)


@dataclass
class Computation:
    formula: str
    result: Decimal
    operands: dict[str, Decimal]
    unit: str | None = None
    render: str | None = None
    dp: int | None = None

    def rendered(self) -> str:
        """Format for display. A ratio asked for as a percentage is multiplied
        HERE, once, rather than in a prompt where it can silently not happen."""
        value = self.result
        if self.render == "percent":
            value = value * 100
        if self.dp is not None:
            value = value.quantize(Decimal(1).scaleb(-self.dp), rounding=ROUND_HALF_UP)
        text = format(value, "f")
        if self.render == "percent":
            return f"{text}%"
        return text


# ---------------------------------------------------------------------------
# Period helpers - the named functions a formula may call
# ---------------------------------------------------------------------------
def _to_decimal(x: Any) -> Decimal:
    if isinstance(x, Decimal):
        return x
    if isinstance(x, bool):
        raise FormulaError("boolean is not a number")
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, float):
        # Never trust a float literal: route it through str so 0.1 stays 0.1.
        return Decimal(str(x))
    raise FormulaError(f"not a number: {x!r}")


class _Helpers:
    """`avg`, `delta`, `prev`, `sum_range`, `rank`, `compare` (§20.5 whitelist).

    Period-aware helpers resolve against the operand table, so
    `avg(inventory, prev, current)` needs `inventory` and `inventory__prev`.
    A missing prior period raises rather than silently averaging one value -
    that would produce a plausible number from incomplete evidence.
    """

    def __init__(self, values: dict[str, Decimal]) -> None:
        self._values = values

    def _lookup(self, name: str, period: str) -> Decimal:
        key = name if period == "current" else f"{name}__{period}"
        if key not in self._values:
            raise FormulaError(
                f"operand {key!r} is not available; "
                f"cannot evaluate the {period} period"
            )
        return self._values[key]

    def avg(self, name: str, *periods: str) -> Decimal:
        periods = periods or ("prev", "current")
        vals = [self._lookup(name, p) for p in periods]
        return sum(vals, Decimal(0)) / Decimal(len(vals))

    def delta(self, name: str, start: str = "prev", end: str = "current") -> Decimal:
        return self._lookup(name, end) - self._lookup(name, start)

    def prev(self, name: str) -> Decimal:
        return self._lookup(name, "prev")

    def sum_range(self, name: str, *periods: str) -> Decimal:
        return sum((self._lookup(name, p) for p in periods), Decimal(0))

    def rank(self, *values: Any) -> Decimal:
        """1-based position of the largest value. Used by "which segment ..."."""
        nums = [_to_decimal(v) for v in values]
        if not nums:
            raise FormulaError("rank() needs at least one value")
        return Decimal(nums.index(max(nums)) + 1)

    def compare(self, a: Any, b: Any) -> Decimal:
        """-1, 0 or 1. Keeps a yes/no judgement deterministic."""
        x, y = _to_decimal(a), _to_decimal(b)
        return Decimal(0) if x == y else (Decimal(1) if x > y else Decimal(-1))


# `prev` and the bare operand names are resolved by the evaluator, so the
# helper table only needs the callables.
_HELPER_NAMES = {"avg", "delta", "prev", "sum_range", "rank", "compare"}


def _bare_name(node: ast.AST) -> str:
    """A helper's first argument is an operand NAME, not its value."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise FormulaError("expected an operand name")


def evaluate(formula: str, values: dict[str, Decimal]) -> Decimal:
    """Evaluate `formula` over `values`. Raises FormulaError on anything unsafe."""
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"unparseable formula {formula!r}: {exc}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(
                f"disallowed syntax {type(node).__name__} in formula {formula!r}"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _HELPER_NAMES:
                raise FormulaError("only avg/delta/prev/sum_range/rank/compare may be called")
            if node.keywords:
                raise FormulaError("keyword arguments are not allowed in a formula")

    helpers = _Helpers(values)

    def visit(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            return _to_decimal(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise FormulaError(f"operand {node.id!r} is not available")
            return values[node.id]
        if isinstance(node, ast.UnaryOp):
            v = visit(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            op = _ALLOWED_BINOPS.get(type(node.op))
            if op is None:
                raise FormulaError(f"disallowed operator {type(node.op).__name__}")
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise FormulaError("division by zero")
            return op(left, right)
        if isinstance(node, ast.Call):
            name = node.func.id  # type: ignore[union-attr]
            fn = getattr(helpers, name)
            if name in ("avg", "delta", "prev", "sum_range"):
                args: list[Any] = [_bare_name(node.args[0])]
                args += [_bare_name(a) for a in node.args[1:]]
                return fn(*args)
            return fn(*[visit(a) for a in node.args])
        raise FormulaError(f"disallowed node {type(node).__name__}")

    # 28 digits is ample for financial magnitudes and keeps ** stable.
    with localcontext() as ctx:
        ctx.prec = 28
        try:
            return visit(tree)
        except (DivisionByZero, InvalidOperation) as exc:
            raise FormulaError(f"arithmetic error: {exc}") from exc


# ---------------------------------------------------------------------------
# Scale normalisation - gate G5 depends on this
# ---------------------------------------------------------------------------
_SCALE_FACTOR = {
    None: Decimal(1),
    "units": Decimal(1),
    "thousands": Decimal(10) ** 3,
    "millions": Decimal(10) ** 6,
    "billions": Decimal(10) ** 9,
}


def to_base_units(value: Decimal, scale: str | None) -> Decimal:
    """Bring an operand to absolute units before arithmetic.

    Mixing a figure reported in millions with one in thousands produces an
    answer wrong by 1000x that still looks like a number - the exact failure
    gate G5 exists to catch.
    """
    factor = _SCALE_FACTOR.get((scale or "").lower() or None)
    if factor is None:
        raise FormulaError(f"unknown scale {scale!r}")
    return value * factor


def units_compatible(operands: list[Operand]) -> bool:
    """G5: every operand must reduce to the same dimension."""
    units = {(o.unit or "").lower() for o in operands if o.unit}
    return len(units) <= 1


def compute(
    formula: str,
    operands: list[Operand],
    *,
    unit: str | None = None,
    render: str | None = None,
    dp: int | None = None,
    normalise_scale: bool = True,
) -> Computation:
    """Evaluate a formula over typed operands, normalising scale first."""
    values: dict[str, Decimal] = {}
    for o in operands:
        values[o.name] = to_base_units(o.value, o.scale) if normalise_scale else o.value
    result = evaluate(formula, values)
    return Computation(
        formula=formula,
        result=result,
        operands=dict(values),
        unit=unit,
        render=render,
        dp=dp,
    )


_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def recomputes(stated: str, computation: Computation, tolerance: Decimal) -> bool:
    """G6: re-evaluating the formula must reproduce the stated answer.

    This catches the case where the model computed correctly, then wrote a
    different number into its prose.
    """
    m = _NUM.search(stated or "")
    if not m:
        return False
    try:
        claimed = Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return False
    actual = computation.result
    if computation.render == "percent":
        actual = actual * 100
    if actual == 0:
        return abs(claimed) <= tolerance
    return abs(actual - claimed) / abs(actual) <= tolerance
