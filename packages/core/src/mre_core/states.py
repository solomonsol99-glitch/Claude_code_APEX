"""Job and run state machines (§1.4) as pure transition tables."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from mre_core.errors import InvalidStateTransitionError


class JobStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    VALIDATING = "VALIDATING"
    REJECTED = "REJECTED"
    SEEDING = "SEEDING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    ESTIMATING = "ESTIMATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    ANALYZING = "ANALYZING"
    RENDERING = "RENDERING"
    QA = "QA"
    ANALYST_REVIEW = "ANALYST_REVIEW"
    DELIVERED = "DELIVERED"
    CALIBRATED = "CALIBRATED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    GRAPH_BUILT = "GRAPH_BUILT"
    PROFILES_READY = "PROFILES_READY"
    SIMULATING = "SIMULATING"
    COLLECTED = "COLLECTED"
    INTERVIEWED = "INTERVIEWED"
    CLEANED = "CLEANED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


J = JobStatus
JOB_TERMINAL: frozenset[JobStatus] = frozenset({J.REJECTED, J.FAILED, J.CANCELLED, J.CALIBRATED})

# Edges from the §1.4 diagram.
_JOB_EDGES: dict[JobStatus, set[JobStatus]] = {
    J.SUBMITTED: {J.VALIDATING},
    J.VALIDATING: {J.REJECTED, J.SEEDING},
    J.SEEDING: {J.AWAITING_REVIEW},
    J.AWAITING_REVIEW: {J.ESTIMATING},
    J.ESTIMATING: {J.AWAITING_APPROVAL, J.QUEUED},
    J.AWAITING_APPROVAL: {J.QUEUED},
    J.QUEUED: {J.RUNNING},
    J.RUNNING: {J.ANALYZING},
    J.ANALYZING: {J.RENDERING},
    J.RENDERING: {J.QA},
    J.QA: {J.RUNNING, J.ANALYST_REVIEW, J.DELIVERED},
    J.ANALYST_REVIEW: {J.DELIVERED},
    J.DELIVERED: {J.CALIBRATED},
}
# Watchdog (§4.5) can fail any non-terminal job; customers can cancel any job not yet delivered.
for _s in JobStatus:
    if _s in JOB_TERMINAL or _s is J.DELIVERED:
        continue
    _JOB_EDGES.setdefault(_s, set()).update({J.FAILED, J.CANCELLED})

JOB_TRANSITIONS: Mapping[JobStatus, frozenset[JobStatus]] = {
    s: frozenset(_JOB_EDGES.get(s, set())) for s in JobStatus
}

R = RunStatus
RUN_TERMINAL: frozenset[RunStatus] = frozenset({R.CLEANED, R.FAILED, R.CANCELLED})
_RUN_EDGES: dict[RunStatus, set[RunStatus]] = {
    R.PENDING: {R.LEASED},
    R.LEASED: {R.GRAPH_BUILT},
    R.GRAPH_BUILT: {R.PROFILES_READY},
    R.PROFILES_READY: {R.SIMULATING},
    # A crashed simulation restarts from PROFILES_READY with the same seed (§4.5).
    R.SIMULATING: {R.COLLECTED, R.PROFILES_READY},
    R.COLLECTED: {R.INTERVIEWED, R.CLEANED},
    R.INTERVIEWED: {R.CLEANED},
}
# Any live run may fail, be cancelled, or be restarted on a fresh engine instance.
for _r in RunStatus:
    if _r not in RUN_TERMINAL:
        _RUN_EDGES.setdefault(_r, set()).update({R.FAILED, R.CANCELLED})
        if _r is not R.PENDING:
            _RUN_EDGES[_r].add(R.PENDING)

RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    s: frozenset(_RUN_EDGES.get(s, set())) for s in RunStatus
}


def can_transition_job(src: JobStatus, dst: JobStatus) -> bool:
    return dst in JOB_TRANSITIONS[src]


def can_transition_run(src: RunStatus, dst: RunStatus) -> bool:
    return dst in RUN_TRANSITIONS[src]


def assert_job_transition(src: JobStatus, dst: JobStatus) -> None:
    if not can_transition_job(src, dst):
        raise InvalidStateTransitionError(f"job: {src} -> {dst} is not allowed")


def assert_run_transition(src: RunStatus, dst: RunStatus) -> None:
    if not can_transition_run(src, dst):
        raise InvalidStateTransitionError(f"run: {src} -> {dst} is not allowed")
