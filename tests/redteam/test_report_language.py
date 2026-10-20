"""Enforces the report wording rules (blueprint §5.8, coding standards)."""

from pathlib import Path

import pytest
from mre_security import assert_clean, has_hedge, lint_finding_text

BAD = [
    ("This product will succeed with young families.", "banned:will-outcome"),
    ("Budget shoppers will likely buy at $29.", "banned:will-outcome"),
    ("We predict strong adoption in the South.", "banned:predict"),
    ("The model's prediction is positive.", "banned:predict"),
    ("Our forecast shows 12% conversion.", "banned:forecast"),
    ("Guaranteed uplift for variant B.", "banned:guarantee"),
    ("It is certain that price is the main objection.", "banned:certain"),
    ("Variant A is proven to resonate.", "banned:proven"),
    ("Segment 3 won't convert at this price.", "banned:will-outcome"),
]

GOOD = [
    "Simulated reception leaned positive among young families.",
    "In simulation, personas tended to question the battery-life claim.",
    "This suggests price may be a barrier for value-seeking segments.",
    "Responses were uncertain about the subscription terms.",
    "Personas will need clearer pricing information to evaluate the offer.",
]


@pytest.mark.parametrize(("text", "rule"), BAD)
def test_banned_wording_is_flagged(text: str, rule: str) -> None:
    rules = {v.rule for v in lint_finding_text(text)}
    assert rule in rules


@pytest.mark.parametrize("text", GOOD)
def test_directional_wording_passes(text: str) -> None:
    assert lint_finding_text(text) == []


def test_hedge_requirement() -> None:
    assert has_hedge("Personas leaned skeptical.")
    assert not has_hedge("Personas disliked the price.")
    rules = {v.rule for v in lint_finding_text("Personas disliked it.", require_hedge=True)}
    assert rules == {"missing-hedge"}


def test_assert_clean_raises_with_details() -> None:
    with pytest.raises(ValueError, match="banned:forecast"):
        assert_clean("A forecast of demand.")
    assert_clean("Reception leaned positive in simulation.", require_hedge=True)


def test_report_templates_finding_text_is_clean() -> None:
    """Any finding/partial template text we ship must pass (disclaimer files are exempt)."""
    root = Path(__file__).resolve().parents[2] / "templates" / "report"
    for path in root.rglob("*.md"):
        if path.name.startswith("disclaimer"):
            continue
        assert lint_finding_text(path.read_text(encoding="utf-8")) == [], path
