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

MAX_DICE = 100
MAX_SIDES = 1000
MAX_KEEP_OUTCOMES = 2_000_000  # brute-force bound for keep-highest/lowest distributions

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
    if re.search(r"\s", text):
        raise DiceError(f"unexpected whitespace in {expression!r}")
    dice: list[DiceTerm] = []
    constant = 0
    pos = 0
    while pos < len(text):
        m = _TERM.match(text, pos)
        if not m or m.end() == pos or (pos > 0 and not m.group(1)):
            raise DiceError(f"cannot parse {expression!r} at position {pos}")
        sign = -1 if m.group(1) == "-" else 1
        if m.group(6) is not None:
            constant += sign * int(m.group(6))
        else:
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


def _term_distribution(t: DiceTerm) -> dict[int, Fraction]:
    if t.keep is None:
        dist = {0: Fraction(1)}
        die = {face: Fraction(1, t.sides) for face in range(1, t.sides + 1)}
        for _ in range(t.count):
            dist = _convolve(dist, die)
    else:
        outcomes = t.sides**t.count
        if outcomes > MAX_KEEP_OUTCOMES:
            raise DiceError(f"{t} has {outcomes:,} outcomes; too many to enumerate exactly")
        counts = Counter(
            sum(_keep(list(r), t.keep))
            for r in itertools.product(range(1, t.sides + 1), repeat=t.count)
        )
        dist = {total: Fraction(n, outcomes) for total, n in counts.items()}
    return {t.sign * k: p for k, p in dist.items()}


def _convolve(a: dict[int, Fraction], b: dict[int, Fraction]) -> dict[int, Fraction]:
    out: dict[int, Fraction] = {}
    for x, px in a.items():
        for y, py in b.items():
            out[x + y] = out.get(x + y, Fraction(0)) + px * py
    return out


def distribution(expression: str | DiceExpression) -> dict[int, Fraction]:
    """Exact probability mass function of the expression's total."""
    expr = parse(expression) if isinstance(expression, str) else expression
    dist = {expr.constant: Fraction(1)}
    for t in expr.dice:
        dist = _convolve(dist, _term_distribution(t))
    return dict(sorted(dist.items()))


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
