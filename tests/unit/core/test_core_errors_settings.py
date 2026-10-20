"""Error taxonomy, problem details and settings."""

from __future__ import annotations

import pytest
from mre_core.errors import (
    PROBLEM_TYPE_BASE,
    BudgetError,
    DependencyUnavailableError,
    EngineBugError,
    EngineValidationError,
    ErrorCode,
    ForbiddenPurposeError,
    InputError,
    MissingTenantContext,
    MREError,
    PIIBlockedError,
    QuotaExhaustedError,
    TransientError,
)
from mre_core.settings import Settings, get_settings
from pydantic import SecretStr


@pytest.mark.parametrize(
    ("exc", "code", "category", "retryable", "status"),
    [
        (TransientError(), ErrorCode.TRANSIENT, "transient", True, 503),
        (DependencyUnavailableError(), ErrorCode.DEPENDENCY_UNAVAILABLE, "transient", True, 503),
        (InputError(), ErrorCode.VALIDATION_FAILED, "input", False, 422),
        (PIIBlockedError(), ErrorCode.PII_BLOCKED, "input", False, 422),
        (ForbiddenPurposeError(), ErrorCode.FORBIDDEN_PURPOSE, "input", False, 403),
        (BudgetError(), ErrorCode.BUDGET_EXCEEDED, "budget", False, 402),
        (QuotaExhaustedError(), ErrorCode.QUOTA_EXHAUSTED, "budget", False, 429),
        (EngineBugError(), ErrorCode.ENGINE_ERROR, "engine_bug", False, 502),
        (EngineValidationError(), ErrorCode.ENGINE_VALIDATION_FAILED, "engine_bug", False, 502),
        (MissingTenantContext(), ErrorCode.MISSING_TENANT_CONTEXT, "internal", False, 500),
    ],
)
def test_taxonomy(
    exc: MREError, code: ErrorCode, category: str, retryable: bool, status: int
) -> None:
    assert exc.code is code
    assert exc.category == category
    assert exc.retryable is retryable
    assert exc.status == status


def test_problem_details() -> None:
    err = BudgetError("job cap reached", run_id="r1")
    p = err.to_problem(instance="/v1/jobs/1")
    assert p.type == PROBLEM_TYPE_BASE + "BUDGET_EXCEEDED"
    assert p.status == 402
    assert p.detail == "job cap reached"
    assert p.model_dump()["run_id"] == "r1"
    assert str(err) == "job cap reached"
    assert InputError(code=ErrorCode.NOT_FOUND).to_problem().detail is None


def test_settings_have_no_secret_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DATABASE_URL", "MIGRATOR_DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.database_url is None
    assert s.migrator_database_url is None
    assert s.redis_url is None


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("MRE_ENV", "test")
    s = Settings()
    assert isinstance(s.database_url, SecretStr)
    assert "p@h" not in repr(s)
    assert s.env == "test"
    get_settings.cache_clear()
    assert get_settings().env == "test"
    get_settings.cache_clear()
