"""Typed error taxonomy (§4.3) and RFC 9457 problem details (Appendix A)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    """Stable problem ``type`` codes. Never rename; add new ones only."""

    PII_BLOCKED = "PII_BLOCKED"
    FORBIDDEN_PURPOSE = "FORBIDDEN_PURPOSE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    ENGINE_VALIDATION_FAILED = "ENGINE_VALIDATION_FAILED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    TENANT_CONFIG_INVALID = "TENANT_CONFIG_INVALID"
    MISSING_TENANT_CONTEXT = "MISSING_TENANT_CONTEXT"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    ENGINE_ERROR = "ENGINE_ERROR"
    TRANSIENT = "TRANSIENT"
    INTERNAL = "INTERNAL"


PROBLEM_TYPE_BASE = "https://errors.coreset.example/mre/"  # placeholder domain


class ProblemDetails(BaseModel):
    """RFC 9457 problem details. ``detail`` must never contain tenant content."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int = Field(ge=100, le=599)
    detail: str | None = None
    instance: str | None = None
    code: ErrorCode


class MREError(Exception):
    """Base class. ``retryable`` drives retry policy; ``status`` maps to HTTP."""

    category: ClassVar[str] = "internal"
    retryable: ClassVar[bool] = False
    default_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL
    status: ClassVar[int] = 500
    title: ClassVar[str] = "Internal error"

    def __init__(self, detail: str = "", *, code: ErrorCode | None = None, **extra: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.code = code or self.default_code
        self.extra = extra

    def to_problem(self, instance: str | None = None) -> ProblemDetails:
        return ProblemDetails(
            type=PROBLEM_TYPE_BASE + self.code.value,
            title=self.title,
            status=self.status,
            detail=self.detail or None,
            instance=instance,
            code=self.code,
            **self.extra,
        )


class TransientError(MREError):
    """Timeouts, 429, 5xx, resets: retry with backoff."""

    category = "transient"
    retryable = True
    default_code = ErrorCode.TRANSIENT
    status = 503
    title = "Temporary failure"


class DependencyUnavailableError(TransientError):
    default_code = ErrorCode.DEPENDENCY_UNAVAILABLE
    title = "Dependency unavailable"


class InputError(MREError):
    """Bad customer input: fail fast and tell the customer."""

    category = "input"
    default_code = ErrorCode.VALIDATION_FAILED
    status = 422
    title = "Invalid input"


class PIIBlockedError(InputError):
    default_code = ErrorCode.PII_BLOCKED
    title = "Personal data detected"


class ForbiddenPurposeError(InputError):
    default_code = ErrorCode.FORBIDDEN_PURPOSE
    status = 403
    title = "Forbidden purpose"


class TenantConfigError(InputError):
    default_code = ErrorCode.TENANT_CONFIG_INVALID
    title = "Invalid tenant configuration"


class InvalidStateTransitionError(InputError):
    default_code = ErrorCode.INVALID_STATE_TRANSITION
    status = 409
    title = "Invalid state transition"


class BudgetError(MREError):
    """Cost cap or quota hit: stop cleanly, never retry."""

    category = "budget"
    default_code = ErrorCode.BUDGET_EXCEEDED
    status = 402
    title = "Budget exceeded"


class QuotaExhaustedError(BudgetError):
    default_code = ErrorCode.QUOTA_EXHAUSTED
    status = 429
    title = "Quota exhausted"


class EngineBugError(MREError):
    """Engine misbehaved: fail run, open incident, feed debug agent."""

    category = "engine_bug"
    default_code = ErrorCode.ENGINE_ERROR
    status = 502
    title = "Engine error"


class EngineValidationError(EngineBugError):
    default_code = ErrorCode.ENGINE_VALIDATION_FAILED
    title = "Engine output failed validation"


class MissingTenantContext(MREError):  # noqa: N818 - name fixed by blueprint
    """Raised when DB access is attempted without a tenant id."""

    default_code = ErrorCode.MISSING_TENANT_CONTEXT
    status = 500
    title = "Missing tenant context"
