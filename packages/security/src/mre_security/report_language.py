"""Report-language lint (blueprint §5.8).

Finding text must read as directional research, never as an outcome claim. The standard
disclaimer is exempt because it names these words in order to deny them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Word stems that are banned in finding text in any inflection.
_BANNED_STEMS: dict[str, str] = {
    "guarantee": r"guarantee[sd]?|guaranteeing",
    "predict": r"predict(?:s|ed|ing|ion|ions|ive|ively|able|ably)?",
    "forecast": r"forecast(?:s|ed|ing|er|ers)?",
    "certain": r"certain(?:ly|ty)?",
    "proven": r"proven|prove[sd]?",
}

# "will" followed by an outcome verb ("will buy", "will likely convert", ...).
_OUTCOME_VERBS = (
    "buy|purchase|succeed|fail|convert|sell|win|lose|adopt|subscribe|sign up|"
    "churn|outperform|underperform|grow|increase|decrease|drive|generate|double|triple|"
    "love|hate|reject|embrace|switch|pay"
)
_WILL_OUTCOME = re.compile(
    rf"\b(?:will|won't|will not)\s+(?:\w+ly\s+)?(?:{_OUTCOME_VERBS})\b", re.IGNORECASE
)

HEDGES: tuple[str, ...] = (
    "leaned",
    "tended",
    "suggests",
    "suggested",
    "in simulation",
    "in the simulation",
    "simulated",
    "appeared",
    "may",
    "might",
)

_BANNED = {
    word: re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)
    for word, pattern in _BANNED_STEMS.items()
}


@dataclass(frozen=True, slots=True)
class LanguageViolation:
    rule: str
    match: str
    start: int

    def __str__(self) -> str:
        return f"{self.rule}: {self.match!r} at {self.start}"


def lint_finding_text(text: str, *, require_hedge: bool = False) -> list[LanguageViolation]:
    """Return every wording violation in a piece of finding text (empty list = clean)."""
    violations: list[LanguageViolation] = []
    for word, pattern in _BANNED.items():
        violations.extend(
            LanguageViolation(f"banned:{word}", m.group(0), m.start())
            for m in pattern.finditer(text)
        )
    violations.extend(
        LanguageViolation("banned:will-outcome", m.group(0), m.start())
        for m in _WILL_OUTCOME.finditer(text)
    )
    if require_hedge and not has_hedge(text):
        violations.append(LanguageViolation("missing-hedge", "", 0))
    return sorted(violations, key=lambda v: v.start)


def has_hedge(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(h)}\b", lowered) for h in HEDGES)


def assert_clean(text: str, *, require_hedge: bool = False) -> None:
    """Raise ValueError listing violations; used by the report QA gate."""
    found = lint_finding_text(text, require_hedge=require_hedge)
    if found:
        raise ValueError("report language violations: " + "; ".join(map(str, found)))
