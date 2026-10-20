"""The only place for outbound HTTP: timeouts, retries, breakers, SSRF guard (§4.3, §4.4)."""

from mre_net.breaker import (
    BreakerConfig,
    BreakerState,
    BreakerStateStore,
    CircuitBreaker,
    CircuitOpenError,
    InMemoryBreakerStore,
)
from mre_net.client import Timeouts, make_async_client
from mre_net.retry import (
    CallType,
    RetryPolicy,
    backoff_delay,
    is_retryable,
    raise_for_retryable_status,
    retry_async,
)
from mre_net.ssrf import ResolvedTarget, SSRFBlockedError, check_ip, resolve_and_check

__all__ = [
    "BreakerConfig",
    "BreakerState",
    "BreakerStateStore",
    "CallType",
    "CircuitBreaker",
    "CircuitOpenError",
    "InMemoryBreakerStore",
    "ResolvedTarget",
    "RetryPolicy",
    "SSRFBlockedError",
    "Timeouts",
    "backoff_delay",
    "check_ip",
    "is_retryable",
    "make_async_client",
    "raise_for_retryable_status",
    "resolve_and_check",
    "retry_async",
]
