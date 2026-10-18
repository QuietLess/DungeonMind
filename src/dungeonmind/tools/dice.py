"""Dice expressions: parse, roll with an audit trail, and compute exact distributions.

Grammar (case-insensitive, whitespace ignored)::

    expr  := term (("+" | "-") term)*
    term  := dice | INT
    dice  := [INT] "d" (INT | "%") [("kh" | "kl") INT]

Examples: ``1d20+7``, ``8d6``, ``4d6kh3``, ``2d20kl1 + 5``, ``d%``.
The LLM never does this arithmetic; it calls these functions.
"""

from __future__ import annotations

import itertools
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction

MAX_LENGTH = 64  # characters in an expression
MAX_TERMS = 10  # dice terms plus constants
MAX_DICE = 100  # dice in the whole expression
MAX_SIDES = 1000
MAX_CONSTANT = 10_000  # a modifier bigger than this is a typo, and overflows storage
# Exact-odds bounds (about a second of arithmetic at most): total support Σ count × sides, and
# the brute-force enumeration for keep-highest/lowest terms.
MAX_SUPPORT = 1_000
MAX_KEEP_OUTCOMES = 100_000

_TERM = re.compile(r"([+-])?(?:(\d*)d(\d+|%)(?:(kh|kl)(\d+))?|(\d+))")


class DiceError(ValueError):
    """Raised for malformed or out-of-bounds dice expressions."""


@dataclass(frozen=True)
class DiceTerm:
    count: int
    sides: int
    sign: int = 1
    keep: tuple[str, int] | None = None  # ("kh"|"kl", n)

    def __str__(self) -> str:
        keep = f"{self.keep[0]}{self.keep[1]}" if self.keep else ""
        return f"{self.count}d{self.sides}{keep}"


@dataclass(frozen=True)
class DiceExpression:
    dice: tuple[DiceTerm, ...]
    constant: int
    source: str

    def __str__(self) -> str:
        out = ""
        for t in self.dice:
            out += ("-" if t.sign < 0 else ("+" if out else "")) + str(t)
        if self.constant or not out:
            out += f"{self.constant:+d}" if out else str(self.constant)
        return out


@dataclass(frozen=True)
class TermRoll:
    term: str
    rolls: list[int]
    kept: list[int]
    subtotal: int


@dataclass(frozen=True)
class RollResult:
    expression: str
    terms: list[TermRoll]
    constant: int
    total: int
    seed: int | None = field(default=None)


def parse(expression: str) -> DiceExpression:
    # Whitespace is only allowed around operators: "1d20 7" is a typo, not 1d207.
    text = re.sub(r"\s*([+-])\s*", r"\1", expression.lower().strip())
    if not text:
        raise DiceError("empty dice expression")
    if len(expression) > MAX_LENGTH:
        raise DiceError(f"dice expression is longer than {MAX_LENGTH} characters")
    if re.search(r"\s", text):
        raise DiceError(f"unexpected whitespace in {expression!r}")
    dice: list[DiceTerm] = []
    constant = 0
    pos = 0
    terms = 0
    while pos < len(text):
        m = _TERM.match(text, pos)
        if not m or m.end() == pos or (pos > 0 and not m.group(1)):
            raise DiceError(f"cannot parse {expression!r} at position {pos}")
        terms += 1
        if terms > MAX_TERMS:
            raise DiceError(f"dice expression has more than {MAX_TERMS} terms")
        sign = -1 if m.group(1) == "-" else 1
        if m.group(6) is not None:
            if len(m.group(6)) > 6 or int(m.group(6)) > MAX_CONSTANT:
                raise DiceError(f"constants must be at most {MAX_CONSTANT}")
            constant += sign * int(m.group(6))
        else:
            if (m.group(2) and len(m.group(2)) > 4) or len(m.group(3)) > 5:
                raise DiceError("dice count or sides too large")
            count = int(m.group(2)) if m.group(2) else 1
            sides = 100 if m.group(3) == "%" else int(m.group(3))
            if not 1 <= count <= MAX_DICE:
                raise DiceError(f"dice count must be 1–{MAX_DICE}, got {count}")
            if not 2 <= sides <= MAX_SIDES:
                raise DiceError(f"die sides must be 2–{MAX_SIDES}, got {sides}")
            keep = None
            if m.group(4):
                n = int(m.group(5))
                if not 1 <= n <= count:
                    raise DiceError(f"cannot keep {n} of {count} dice")
                keep = (m.group(4), n)
            dice.append(DiceTerm(count=count, sides=sides, sign=sign, keep=keep))
        pos = m.end()
    if sum(t.count for t in dice) > MAX_DICE:
        raise DiceError(f"dice expression has more than {MAX_DICE} dice in total")
    return DiceExpression(dice=tuple(dice), constant=constant, source=expression)


def _keep(rolls: list[int], keep: tuple[str, int] | None) -> list[int]:
    if keep is None:
        return list(rolls)
    mode, n = keep
    return sorted(rolls, reverse=(mode == "kh"))[:n]


def roll(expression: str | DiceExpression, rng: random.Random | None = None) -> RollResult:
    """Roll server-side and return every die for auditability."""
    expr = parse(expression) if isinstance(expression, str) else expression
    rng = rng or random.SystemRandom()
    terms: list[TermRoll] = []
    total = expr.constant
    for t in expr.dice:
        rolls = [rng.randint(1, t.sides) for _ in range(t.count)]
        kept = _keep(rolls, t.keep)
        subtotal = t.sign * sum(kept)
        total += subtotal
        terms.append(TermRoll(term=str(t), rolls=rolls, kept=kept, subtotal=subtotal))
    return RollResult(expression=str(expr), terms=terms, constant=expr.constant, total=total)


def _too_large(what: str) -> DiceError:
    return DiceError(f"{what} is too large for exact odds")


def _term_counts(t: DiceTerm) -> tuple[dict[int, int], int]:
    """Ways to reach each (unsigned) total, and the number of equally likely outcomes."""
    if t.keep is None:
        counts = {0: 1}
        die = dict.fromkeys(range(1, t.sides + 1), 1)
        for _ in range(t.count):
            counts = _convolve(counts, die)
        return counts, t.sides**t.count
    outcomes = t.sides**t.count
    if outcomes > MAX_KEEP_OUTCOMES:
        raise _too_large(f"{t} ({outcomes:,} outcomes to enumerate)")
    counts = Counter(
        sum(_keep(list(r), t.keep))
        for r in itertools.product(range(1, t.sides + 1), repeat=t.count)
    )
    return dict(counts), outcomes


def _term_distribution(t: DiceTerm) -> dict[int, Fraction]:
    counts, outcomes = _term_counts(t)
    return {t.sign * k: Fraction(n, outcomes) for k, n in counts.items()}


def _convolve(a: dict[int, int], b: dict[int, int]) -> dict[int, int]:
    # Integer counts, not Fractions: exact and far faster; divide once at the end.
    out: dict[int, int] = {}
    for x, nx in a.items():
        for y, ny in b.items():
            out[x + y] = out.get(x + y, 0) + nx * ny
    return out


def distribution(expression: str | DiceExpression) -> dict[int, Fraction]:
    """Exact probability mass function of the expression's total."""
    expr = parse(expression) if isinstance(expression, str) else expression
    support = sum(t.count * t.sides for t in expr.dice)
    if support > MAX_SUPPORT:
        raise _too_large(f"{expr} (support {support:,} > {MAX_SUPPORT:,})")
    counts, outcomes = {expr.constant: 1}, 1
    for t in expr.dice:
        term, n = _term_counts(t)
        counts = _convolve(counts, {t.sign * k: c for k, c in term.items()})
        outcomes *= n
    return {k: Fraction(c, outcomes) for k, c in sorted(counts.items())}


def mean(expression: str | DiceExpression) -> Fraction:
    expr = parse(expression) if isinstance(expression, str) else expression
    total = Fraction(expr.constant)
    for t in expr.dice:
        if t.keep is None:
            total += t.sign * t.count * Fraction(t.sides + 1, 2)
        else:
            total += sum(k * p for k, p in _term_distribution(t).items())
    return total


def prob_at_least(expression: str | DiceExpression, target: int) -> Fraction:
    return sum((p for k, p in distribution(expression).items() if k >= target), Fraction(0))
