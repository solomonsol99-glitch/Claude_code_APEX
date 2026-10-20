"""Job and run state machines (§1.4)."""

from __future__ import annotations

import itertools

import pytest
from mre_core.errors import InvalidStateTransitionError
from mre_core.states import (
    JOB_TERMINAL,
    JOB_TRANSITIONS,
    RUN_TERMINAL,
    JobStatus,
    RunStatus,
    assert_job_transition,
    assert_run_transition,
    can_transition_job,
    can_transition_run,
)

J, R = JobStatus, RunStatus

HAPPY_PATH = [
    J.SUBMITTED, J.VALIDATING, J.SEEDING, J.AWAITING_REVIEW, J.ESTIMATING,
    J.AWAITING_APPROVAL, J.QUEUED, J.RUNNING, J.ANALYZING, J.RENDERING, J.QA,
    J.ANALYST_REVIEW, J.DELIVERED, J.CALIBRATED,
]  # fmt: skip


def test_happy_path() -> None:
    for src, dst in itertools.pairwise(HAPPY_PATH):
        assert_job_transition(src, dst)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (J.VALIDATING, J.REJECTED), (J.ESTIMATING, J.QUEUED), (J.QA, J.RUNNING),
        (J.QA, J.DELIVERED), (J.SUBMITTED, J.CANCELLED), (J.RUNNING, J.CANCELLED),
        (J.RUNNING, J.FAILED), (J.SEEDING, J.FAILED),
    ],
)  # fmt: skip
def test_diagram_and_watchdog_edges(src: JobStatus, dst: JobStatus) -> None:
    assert can_transition_job(src, dst)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (J.SUBMITTED, J.RUNNING), (J.QUEUED, J.DELIVERED), (J.DELIVERED, J.CANCELLED),
        (J.DELIVERED, J.RUNNING), (J.REJECTED, J.VALIDATING), (J.ESTIMATING, J.RUNNING),
    ],
)  # fmt: skip
def test_illegal_job_edges(src: JobStatus, dst: JobStatus) -> None:
    with pytest.raises(InvalidStateTransitionError):
        assert_job_transition(src, dst)


def test_terminal_states_have_no_exits() -> None:
    for s in JOB_TERMINAL:
        assert JOB_TRANSITIONS[s] == frozenset()
    for r in RUN_TERMINAL:
        for dst in RunStatus:
            assert not can_transition_run(r, dst)


def test_run_path_and_restarts() -> None:
    path = [R.PENDING, R.LEASED, R.GRAPH_BUILT, R.PROFILES_READY, R.SIMULATING,
            R.COLLECTED, R.INTERVIEWED, R.CLEANED]  # fmt: skip
    for src, dst in itertools.pairwise(path):
        assert_run_transition(src, dst)
    assert can_transition_run(R.SIMULATING, R.PROFILES_READY)
    assert can_transition_run(R.GRAPH_BUILT, R.PENDING)
    assert can_transition_run(R.COLLECTED, R.CLEANED)
    with pytest.raises(InvalidStateTransitionError):
        assert_run_transition(R.PENDING, R.SIMULATING)
