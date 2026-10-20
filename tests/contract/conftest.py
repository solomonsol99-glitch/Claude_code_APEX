"""Shared engine contract fixtures: every adapter must pass the same suite."""

import pytest
from engine_adapter.testing.contract import ENGINE_NAMES, EngineCase, build_case


@pytest.fixture(params=ENGINE_NAMES)
def engine_case(request: pytest.FixtureRequest) -> EngineCase:
    return build_case(request.param)
