"""Faithfulness + safety checks for model-written "Reason for Trade" text.

The model is never trusted: its text is shown only if every number in it is
traceable to a number the engine computed, and it contains nothing that reads
as an instruction to trade or a guarantee. Anything else falls back to the
engine's template text. Pure functions, so the same checks run at runtime and
in evals/reason_eval.py.
"""

import math
import re
from typing import Dict, Iterable, List, Set

MAX_LEN = 700
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
_URL = re.compile(r"(https?://|www\.|\]\(|<[a-z/][^>]*>)", re.IGNORECASE)
# Language that would turn an explanation into an instruction or a promise.
_FORBIDDEN_PHRASES = [
    "place the order", "place an order", "place this order", "execute", "buy now", "sell now", "you should buy", "you should sell",
    "guarantee", "risk-free", "risk free", "can't lose", "cannot lose", "no risk", "sure thing", "certain profit", "ignore previous",
    "system prompt", "api key", "password", "token",
]


def allowed_numbers(facts: Dict[str, object]) -> Set[float]:
    """Every number the text may legitimately mention: the facts themselves,
    plus the obvious renderings (percentages of probabilities, integer strikes)."""
    nums: Set[float] = set()

    def add(x: object) -> None:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            return
        nums.add(round(float(x), 2))
        nums.add(round(float(x), 0))
        nums.add(round(float(x), 1))

    def walk(v: object) -> None:
        if isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
        else:
            add(v)

    walk(facts)
    for key in ("pop", "pop_put_side", "pop_call_side"):
        value = facts.get(key)
        if isinstance(value, (int, float)):
            add(value * 100)
    return nums


def _numbers_in(text: str) -> List[float]:
    out = []
    for raw in _NUMBER.findall(text):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def check_reason(text: str, facts: Dict[str, object]) -> List[str]:
    """Returns violations; empty means the text is safe to show."""
    problems: List[str] = []
    if not text or not text.strip():
        return ["empty"]
    if len(text) > MAX_LEN:
        problems.append("too long")
    if _URL.search(text):
        problems.append("contains a link or markup")
    lowered = text.lower()
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lowered:
            problems.append(f"forbidden phrase: {phrase!r}")
    allowed = allowed_numbers(facts)
    for number in _numbers_in(text):
        if not any(math.isclose(number, a, abs_tol=0.051) for a in allowed):
            problems.append(f"number not in facts: {number:g}")
    return problems


def unknown_numbers(text: str, facts: Dict[str, object]) -> Iterable[float]:
    allowed = allowed_numbers(facts)
    return [n for n in _numbers_in(text) if not any(math.isclose(n, a, abs_tol=0.051) for a in allowed)]
