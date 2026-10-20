"""MiroFish adapter (§2.4.3). Talks to one engine container over HTTP only.

License boundary (§3.12): nothing here imports or copies MiroFish code. Route and field
names were confirmed by reading upstream at commit 7657031 (see ADR 0001).
"""

from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from pathlib import PurePosixPath
from typing import Any, Literal

import httpx
from mre_net import CircuitBreaker, RetryPolicy, Timeouts

from engine_adapter.base import (
    Action,
    Agent,
    CleanupReport,
    Comment,
    EngineCapabilities,
    EngineMeta,
    InterviewAnswer,
    InterviewQuestion,
    PersonaCard,
    Platform,
    PlatformProfile,
    Post,
    RunArtifacts,
    RunHandle,
    RunSpec,
    RunStats,
    RunStatus,
    TimelineEntry,
    check_agent_bounds,
    handle_from_spec,
    utc_now_iso,
)
from engine_adapter.documents import DocumentLoader, missing_loader
from engine_adapter.errors import (
    EngineError,
    EngineResponseInvalidError,
    EngineStageFailedError,
    EngineStageTimeoutError,
)
from engine_adapter.mirofish.client import MiroFishClient

ENGINE_VERSION = "0.1.2+git7657031"
PINNED_COMMIT = "7657031ac01184afe2cb220f5ee3545573b5e843"

RunState = Literal["pending", "running", "completed", "failed", "cancelled"]
_RUNNER_STATE: dict[str, RunState] = {
    "idle": "pending",
    "starting": "pending",
    "running": "running",
    "paused": "running",
    "stopping": "running",
    "stopped": "cancelled",
    "completed": "completed",
    "failed": "failed",
}
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def dossier_entity_name(card: PersonaCard) -> str:
    """Entity name the seed builder must use for a persona so profiles map back to cards."""
    return f"{card.display_name} ({card.id})"


def match_personas(
    profiles: Sequence[dict[str, Any]], cards: Sequence[PersonaCard]
) -> dict[str, str | None]:
    """Map engine agent id (profile ``user_id``) to persona card id.

    Saved profiles carry no entity type, so we match on the card id embedded in the entity
    name (``dossier_entity_name``), falling back to a unique display-name match.
    """
    by_name: dict[str, list[str]] = {}
    for c in cards:
        by_name.setdefault(c.display_name.strip().lower(), []).append(c.id)
    ids = [c.id for c in cards]
    out: dict[str, str | None] = {}
    for idx, p in enumerate(profiles):
        agent_id = str(p.get("user_id", idx))
        text = " ".join(
            str(p.get(k) or "")
            for k in ("name", "username", "user_name", "bio", "persona", "description", "user_char")
        )
        hit = next((cid for cid in ids if re.search(rf"\b{re.escape(cid)}\b", text)), None)
        if hit is None:
            same = by_name.get(str(p.get("name") or "").strip().lower(), [])
            hit = same[0] if len(same) == 1 else None
        out[agent_id] = hit
    return out


class MiroFishEngine:
    def __init__(
        self,
        *,
        base_url: str = "http://engine-1:5001",
        engine_instance: str | None = None,
        load_document: DocumentLoader = missing_loader,
        transport: httpx.AsyncBaseTransport | None = None,
        timeouts: Timeouts | None = None,
        retry_policy: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        min_agents: int = 20,
        entity_types: Sequence[str] = ("Consumer",),
        parallel_profile_count: int = 5,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        generate_engine_report: bool = True,
        interview_platform: Platform | None = None,
        interview_timeout_s: int = 120,
        interview_batch_size: int = 50,
        page_size: int = 200,
    ) -> None:
        self.client = MiroFishClient(
            base_url,
            timeouts=timeouts,
            transport=transport,
            retry_policy=retry_policy,
            breaker=breaker,
            sleep=sleep,
        )
        self.engine_instance = engine_instance or base_url
        self._load = load_document
        self._sleep = sleep
        self._clock = clock
        self.min_agents = min_agents
        self.entity_types = list(entity_types)
        self.parallel_profile_count = parallel_profile_count
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.generate_engine_report = generate_engine_report
        self.interview_platform = interview_platform
        self.interview_timeout_s = interview_timeout_s
        self.interview_batch_size = interview_batch_size
        self.page_size = page_size
        self._timings: dict[str, dict[str, float]] = {}

    # --- protocol ----------------------------------------------------------------------

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            name="mirofish",
            version=ENGINE_VERSION,
            platforms=["twitter", "reddit"],
            supports_interviews=True,
            supports_agent_count_control="approximate",  # [ASSUMPTION A1]
            supports_resume=False,  # [ASSUMPTION A7]
            max_agents_tested=0,  # set from Phase 0 characterization
            memory_backend="zep-cloud",
        )

    async def health(self) -> bool:
        try:
            resp = await self.client.raw_get("/health")
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    async def prepare(self, spec: RunSpec) -> RunHandle:
        handle = handle_from_spec(spec, engine_instance=self.engine_instance, stage="LEASED")
        try:
            await self._prepare(spec, handle)
        except EngineError as exc:
            exc.handle = handle
            raise
        return handle

    async def _prepare(self, spec: RunSpec, handle: RunHandle) -> None:
        t = self._timings.setdefault(spec.run_id, {})
        t0 = self._clock()

        # 1. upload seed docs + ontology
        files = []
        for uri in spec.seed_documents:
            content = await self._load(uri)
            name = PurePosixPath(uri).name or "seed.md"
            if not name.lower().endswith((".md", ".markdown", ".txt", ".pdf")):
                name += ".md"
            mime = mimetypes.guess_type(name)[0] or "text/markdown"
            files.append(("files", (name, content, mime)))
        data = await self.client.call(
            "POST",
            "/api/graph/ontology/generate",
            data={
                "simulation_requirement": spec.simulation_requirement,
                "project_name": spec.run_id,
                "additional_context": "",
            },
            files=files,
            stage="GRAPH_BUILT",
            retry=False,  # not idempotent: a retry would create a second project
        )
        handle.engine_ids["project_id"] = _req(data, "project_id", "ontology/generate")

        # 2. graph build (async task)
        data = await self.client.call(
            "POST",
            "/api/graph/build",
            json={
                "project_id": handle.engine_ids["project_id"],
                "graph_name": f"mre-{spec.run_id}",
                "chunk_size": self.chunk_size,
                "chunk_overlap": self.chunk_overlap,
            },
            stage="GRAPH_BUILT",
        )
        if data.get("graph_id"):
            handle.engine_ids["graph_id"] = str(data["graph_id"])
        task_id = _req(data, "task_id", "graph/build")
        task = await self._poll_task(
            lambda: self.client.call("GET", f"/api/graph/task/{task_id}", stage="GRAPH_BUILT"),
            done=("completed",),
            timeout_s=spec.timeouts.graph_s,
            interval_s=spec.timeouts.poll_interval_s,
            stage="GRAPH_BUILT",
        )
        result = task.get("result") or {}
        handle.engine_ids["graph_id"] = _req(result, "graph_id", "graph task result")
        handle.stage = "GRAPH_BUILT"
        t["graph_s"] = self._clock() - t0

        # 3. simulation create
        t0 = self._clock()
        data = await self.client.call(
            "POST",
            "/api/simulation/create",
            json={
                "project_id": handle.engine_ids["project_id"],
                "graph_id": handle.engine_ids["graph_id"],
                "enable_twitter": "twitter" in spec.platforms,
                "enable_reddit": "reddit" in spec.platforms,
            },
            stage="PROFILES_READY",
            retry=False,
        )
        sim_id = _req(data, "simulation_id", "simulation/create")
        handle.engine_ids["simulation_id"] = sim_id

        # 4. profiles (async task)
        data = await self.client.call(
            "POST",
            "/api/simulation/prepare",
            json={
                "simulation_id": sim_id,
                "entity_types": self.entity_types,
                "use_llm_for_profiles": True,
                "parallel_profile_count": self.parallel_profile_count,
            },
            stage="PROFILES_READY",
        )
        if not data.get("already_prepared"):
            prep_task = _req(data, "task_id", "simulation/prepare")
            await self._poll_task(
                lambda: self.client.call(
                    "POST",
                    "/api/simulation/prepare/status",
                    json={"task_id": prep_task, "simulation_id": sim_id},
                    stage="PROFILES_READY",
                ),
                done=("completed", "ready"),
                timeout_s=spec.timeouts.profiles_s,
                interval_s=spec.timeouts.poll_interval_s,
                stage="PROFILES_READY",
            )

        profiles = await self._profiles(sim_id, spec.platforms[0])
        handle.agent_personas = match_personas(profiles, spec.persona_cards)
        handle.agents_actual = len(profiles)
        handle.stage = "PROFILES_READY"
        t["profiles_s"] = self._clock() - t0
        check_agent_bounds(
            actual=len(profiles),
            target=spec.target_agents,
            tolerance=spec.agent_tolerance,
            min_agents=self.min_agents,
        )

    async def start(self, handle: RunHandle) -> RunHandle:
        sim_id = _sim(handle)
        platform = "parallel" if len(handle.platforms) > 1 else handle.platforms[0]
        await self.client.call(
            "POST",
            "/api/simulation/start",
            json={
                "simulation_id": sim_id,
                "platform": platform,
                "max_rounds": handle.max_rounds,
                "enable_graph_memory_update": False,
                "force": False,
            },
            stage="SIMULATING",
            retry=False,
        )
        self._timings.setdefault(handle.run_id, {})["sim_started"] = self._clock()
        return handle.model_copy(update={"stage": "SIMULATING"})

    async def status(self, handle: RunHandle) -> RunStatus:
        data = await self.client.call(
            "GET", f"/api/simulation/{_sim(handle)}/run-status", stage=handle.stage
        )
        runner = str(data.get("runner_status", "idle"))
        state = _RUNNER_STATE.get(runner, "running")
        current = _int(data.get("current_round"))
        total = _int(data.get("total_rounds")) or handle.max_rounds
        progress = 1.0 if state == "completed" else min(1.0, (current or 0) / max(1, total))
        return RunStatus(
            stage=handle.stage,
            state=state,
            progress=progress,
            current_round=current,
            agents_actual=handle.agents_actual,
            last_heartbeat_at=str(data.get("updated_at") or utc_now_iso()),
            error=str(data["error"])[:300] if data.get("error") else None,
        )

    async def interview(
        self, handle: RunHandle, qs: list[InterviewQuestion]
    ) -> list[InterviewAnswer]:
        """One batch call per question: results are keyed ``<platform>_<agent_id>``, so two
        questions for the same agent in one batch would collide. [ASSUMPTION A3]"""
        sim_id = _sim(handle)
        env = await self.client.call(
            "POST",
            "/api/simulation/env-status",
            json={"simulation_id": sim_id},
            stage="INTERVIEWED",
        )
        if not env.get("env_alive", False):
            raise EngineStageFailedError(
                "simulation environment is not alive; cannot interview", stage="INTERVIEWED"
            )
        platform: Platform = self.interview_platform or handle.platforms[0]
        agent_ids = list(handle.agent_personas)
        answers: list[InterviewAnswer] = []
        for q in qs:
            for i in range(0, len(agent_ids), self.interview_batch_size):
                batch = agent_ids[i : i + self.interview_batch_size]
                data = await self.client.call(
                    "POST",
                    "/api/simulation/interview/batch",
                    json={
                        "simulation_id": sim_id,
                        "interviews": [
                            {"agent_id": _agent_int(a), "prompt": q.text} for a in batch
                        ],
                        "platform": platform,
                        "timeout": self.interview_timeout_s,
                    },
                    stage="INTERVIEWED",
                    retry=False,
                )
                results = (data.get("result") or {}).get("results") or {}
                for item in results.values():
                    agent_id = str(item.get("agent_id"))
                    answers.append(_to_answer(q, agent_id, item, handle))
        handle.stage = "INTERVIEWED"
        return answers

    async def collect(self, handle: RunHandle) -> RunArtifacts:
        sim_id = _sim(handle)
        t0 = self._clock()
        profiles: dict[Platform, list[dict[str, Any]]] = {}
        for p in handle.platforms:
            profiles[p] = await self._profiles(sim_id, p)
        raw_actions = await self._paged(
            f"/api/simulation/{sim_id}/actions", "actions", total_key=None
        )
        actions = [_to_action(a) for a in raw_actions]
        posts: list[Post] = []
        comments: list[Comment] = []
        for p in handle.platforms:
            raw_posts = await self._paged(
                f"/api/simulation/{sim_id}/posts", "posts", params={"platform": p}
            )
            raw_comments = await self._paged(
                f"/api/simulation/{sim_id}/comments",
                "comments",
                params={"platform": p},
                total_key=None,
            )
            plat_actions = [a for a in actions if a.platform == p]
            posts.extend(_to_posts(raw_posts, plat_actions, p))
            comments.extend(_to_comments(raw_comments, plat_actions, p))
        tl = await self.client.call("GET", f"/api/simulation/{sim_id}/timeline", stage="COLLECTED")
        timeline = [_to_timeline(r) for r in (tl.get("timeline") or [])]

        report_md: str | None = None
        warnings: list[str] = []
        if self.generate_engine_report:
            try:
                report_md = await self._engine_report(handle, sim_id)
            except EngineError as exc:
                warnings.append(f"engine report unavailable: {exc}")

        agents = _agents(profiles, handle)
        unmatched = sum(1 for a in agents if a.persona_card_id is None)
        if unmatched:
            warnings.append(f"{unmatched} agents not matched to a persona card")
        orphan_posts = sum(1 for p in posts if p.agent_id is None)
        if orphan_posts:
            warnings.append(f"{orphan_posts} posts not attributable to an agent")
        rounds_completed = max((e.round for e in timeline), default=0)
        timings = self._timings.get(handle.run_id, {})
        timings_s = {k: v for k, v in timings.items() if k.endswith("_s")}
        if "sim_started" in timings:
            timings_s["simulation_s"] = t0 - timings["sim_started"]
        timings_s["collect_s"] = self._clock() - t0
        handle.stage = "COLLECTED"
        return RunArtifacts(
            tenant_id=handle.tenant_id,
            job_id=handle.job_id,
            run_id=handle.run_id,
            arm_id=handle.arm_id,
            agents=agents,
            posts=posts,
            comments=comments,
            actions=actions,
            engine_report_markdown=report_md,
            timeline=timeline,
            stats=RunStats(
                agents_target=handle.target_agents,
                agents_actual=len(agents),
                rounds_planned=handle.max_rounds,
                rounds_completed=rounds_completed,
                posts=len(posts),
                comments=len(comments),
                actions=len(actions),
                truncated=rounds_completed < handle.max_rounds,
            ),
            engine_meta=EngineMeta(
                engine="mirofish",
                version=ENGINE_VERSION,
                model_route=handle.model_route,
                random_seed=handle.random_seed,
                rounds=rounds_completed,
                timings_s=timings_s,
                warnings=warnings,
            ),
        )

    async def cancel(self, handle: RunHandle) -> None:
        sim_id = handle.engine_ids.get("simulation_id")
        if not sim_id:
            return
        for path, body in (
            ("/api/simulation/stop", {"simulation_id": sim_id}),
            ("/api/simulation/close-env", {"simulation_id": sim_id, "timeout": 30}),
        ):
            # Best effort: stop fails on a finished run, and the container is recycled anyway.
            with contextlib.suppress(EngineError):
                await self.client.call("POST", path, json=body, stage="CANCELLED", retry=False)

    async def cleanup(self, handle: RunHandle) -> CleanupReport:
        """Delete report, Zep graph and project. Order matters: the graph route refuses a
        graph no project references, and deleting the project also deletes its graph.
        The container is recycled afterwards to wipe ``uploads`` (no simulation delete route).
        """
        report = CleanupReport(run_id=handle.run_id, engine_instance=handle.engine_instance)
        ids = handle.engine_ids
        await self.cancel(handle)

        report_id = ids.get("report_id")
        if report_id is None and ids.get("simulation_id"):
            try:
                found = await self.client.call(
                    "GET", f"/api/report/by-simulation/{ids['simulation_id']}", stage="CLEANED"
                )
                report_id = found.get("report_id") if isinstance(found, dict) else None
            except EngineError:
                report_id = None
        if report_id:
            report.deleted["report"] = await self._delete(
                f"/api/report/{report_id}", report, missing_ok=True
            )
        if ids.get("graph_id"):
            report.deleted["graph"] = await self._delete(
                f"/api/graph/delete/{ids['graph_id']}", report, missing_ok=True
            )
        if ids.get("project_id"):
            report.deleted["project"] = await self._delete(
                f"/api/graph/project/{ids['project_id']}", report, missing_ok=True
            )
        # Independent Zep-side verification happens in the watchdog (needs the Zep API key,
        # which the adapter does not hold). [ASSUMPTION A6]
        report.memory_deleted_verified = None
        self._timings.pop(handle.run_id, None)
        handle.stage = "CLEANED"
        return report

    # --- helpers -----------------------------------------------------------------------

    async def _delete(self, path: str, report: CleanupReport, *, missing_ok: bool) -> bool:
        try:
            await self.client.call("DELETE", path, stage="CLEANED")
            return True
        except EngineStageFailedError as exc:
            if missing_ok and exc.status_code == 404:
                return True
            report.errors.append(str(exc))
            return False
        except EngineError as exc:
            report.errors.append(str(exc))
            return False

    async def _poll_task(
        self,
        fetch: Callable[[], Awaitable[Any]],
        *,
        done: tuple[str, ...],
        timeout_s: float,
        interval_s: float,
        stage: str,
    ) -> dict[str, Any]:
        deadline = self._clock() + timeout_s
        while True:
            task = await fetch()
            if not isinstance(task, dict):
                raise EngineResponseInvalidError("task status is not an object", stage=stage)
            status = str(task.get("status", ""))
            if status in done:
                return task
            if status == "failed":
                msg = str(task.get("error") or task.get("message") or "task failed")[:300]
                raise EngineStageFailedError(msg, stage=stage)
            if self._clock() >= deadline:
                raise EngineStageTimeoutError(f"{stage} exceeded {timeout_s:.0f}s", stage=stage)
            await self._sleep(interval_s)

    async def _profiles(self, sim_id: str, platform: Platform) -> list[dict[str, Any]]:
        data = await self.client.call(
            "GET",
            f"/api/simulation/{sim_id}/profiles",
            params={"platform": platform},
            stage="PROFILES_READY",
        )
        profiles = data.get("profiles")
        if not isinstance(profiles, list):
            raise EngineResponseInvalidError("profiles missing", stage="PROFILES_READY")
        return [p for p in profiles if isinstance(p, dict)]

    async def _paged(
        self,
        path: str,
        key: str,
        *,
        params: dict[str, Any] | None = None,
        total_key: str | None = "total",
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            q = {**(params or {}), "limit": self.page_size, "offset": offset}
            data = await self.client.call("GET", path, params=q, stage="COLLECTED")
            items = data.get(key)
            if not isinstance(items, list):
                raise EngineResponseInvalidError(f"{path}: '{key}' missing", stage="COLLECTED")
            out.extend(i for i in items if isinstance(i, dict))
            offset += len(items)
            total = _int(data.get(total_key)) if total_key else None
            if len(items) < self.page_size or (total is not None and offset >= total):
                return out

    async def _engine_report(self, handle: RunHandle, sim_id: str) -> str | None:
        data = await self.client.call(
            "POST",
            "/api/report/generate",
            json={"simulation_id": sim_id},
            stage="COLLECTED",
            retry=False,
        )
        report_id = data.get("report_id")
        if report_id:
            handle.engine_ids["report_id"] = str(report_id)
        task_id = data.get("task_id")
        if task_id and data.get("status") != "completed":
            task = await self._poll_task(
                lambda: self.client.call(
                    "POST",
                    "/api/report/generate/status",
                    json={"task_id": task_id, "simulation_id": sim_id},
                    stage="COLLECTED",
                ),
                done=("completed",),
                timeout_s=15 * 60,
                interval_s=5,
                stage="COLLECTED",
            )
            report_id = report_id or (task.get("result") or {}).get("report_id")
            if report_id:
                handle.engine_ids["report_id"] = str(report_id)
        if not report_id:
            return None
        rep = await self.client.call("GET", f"/api/report/{report_id}", stage="COLLECTED")
        md = rep.get("markdown_content")
        return str(md) if md else None


# --- pure mapping functions ----------------------------------------------------------


def _req(data: Any, key: str, where: str) -> str:
    if not isinstance(data, dict) or not data.get(key):
        raise EngineResponseInvalidError(f"{where}: missing '{key}'")
    return str(data[key])


def _sim(handle: RunHandle) -> str:
    sim = handle.engine_ids.get("simulation_id")
    if not sim:
        raise EngineStageFailedError("handle has no simulation_id", stage=handle.stage)
    return sim


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _agent_int(agent_id: str) -> int | str:
    n = _int(agent_id)
    return n if n is not None else agent_id


def _platform(v: Any, default: Platform = "reddit") -> Platform:
    return "twitter" if str(v).lower() == "twitter" else ("reddit" if v else default)


def _to_action(raw: dict[str, Any]) -> Action:
    args = raw.get("action_args") or {}
    target = None
    for k in ("post_id", "comment_id", "quoted_id", "follow_id"):
        if args.get(k) is not None:
            target = str(args[k])
            break
    return Action(
        type=str(raw.get("action_type", "UNKNOWN")).upper(),
        actor=str(raw.get("agent_id")),
        target=target,
        round=_int(raw.get("round_num")) or 0,
        platform=_platform(raw.get("platform")),
        args={k: v for k, v in args.items() if k in ("content", "post_id", "comment_id")},
        success=bool(raw.get("success", True)),
    )


def _content_index(actions: Sequence[Action], types: set[str]) -> dict[str, Action]:
    idx: dict[str, Action] = {}
    for a in actions:
        content = a.args.get("content")
        if a.type in types and isinstance(content, str) and content.strip():
            idx.setdefault(content.strip(), a)
    return idx


def _attribute(
    rows: Sequence[dict[str, Any]], actions: Sequence[Action], types: set[str]
) -> list[tuple[dict[str, Any], str | None, int | None]]:
    """Attribute OASIS rows to agents. Rows carry the DB ``user_id``, which is not the
    agent id, so we match row content to the agent's create action, then reuse the learned
    user_id -> agent_id mapping for rows whose content did not match."""
    idx = _content_index(actions, types)
    user_map: dict[str, str] = {}
    matched: list[tuple[dict[str, Any], Action | None]] = []
    for r in rows:
        act = idx.get(str(r.get("content") or "").strip())
        matched.append((r, act))
        if act is not None and r.get("user_id") is not None:
            user_map.setdefault(str(r["user_id"]), act.actor)
    out = []
    for r, act in matched:
        agent = act.actor if act else user_map.get(str(r.get("user_id")))
        out.append((r, agent, act.round if act else None))
    return out


def _to_posts(rows: Sequence[dict[str, Any]], actions: Sequence[Action], p: Platform) -> list[Post]:
    out = []
    for r, agent, rnd in _attribute(rows, actions, {"CREATE_POST", "QUOTE_POST"}):
        out.append(
            Post(
                post_id=f"{p}:{r.get('post_id')}",
                agent_id=agent,
                platform=p,
                round=rnd,
                content=str(r.get("content") or ""),
                original_post_id=(
                    f"{p}:{r['original_post_id']}" if r.get("original_post_id") else None
                ),
                quote_content=r.get("quote_content") or None,
                likes=_int(r.get("num_likes")) or 0,
                dislikes=_int(r.get("num_dislikes")) or 0,
                shares=_int(r.get("num_shares")) or 0,
            )
        )
    return out


def _to_comments(
    rows: Sequence[dict[str, Any]], actions: Sequence[Action], p: Platform
) -> list[Comment]:
    out = []
    for r, agent, rnd in _attribute(rows, actions, {"CREATE_COMMENT"}):
        out.append(
            Comment(
                comment_id=f"{p}:{r.get('comment_id')}",
                post_id=f"{p}:{r.get('post_id')}",
                agent_id=agent,
                platform=p,
                round=rnd,
                content=str(r.get("content") or ""),
                likes=_int(r.get("num_likes")) or 0,
                dislikes=_int(r.get("num_dislikes")) or 0,
            )
        )
    return out


def _to_timeline(r: dict[str, Any]) -> TimelineEntry:
    tw = _int(r.get("twitter_actions")) or 0
    rd = _int(r.get("reddit_actions")) or 0
    active = r.get("active_agents")
    return TimelineEntry(
        round=_int(r.get("round_num")) or 0,
        actions_total=_int(r.get("total_actions")) or tw + rd,
        active_agents=len(active) if isinstance(active, list) else (_int(active) or 0),
        by_platform={"twitter": tw, "reddit": rd},
        action_types={str(k): int(v) for k, v in (r.get("action_types") or {}).items()},
    )


def _agents(profiles: dict[Platform, list[dict[str, Any]]], handle: RunHandle) -> list[Agent]:
    agents: dict[str, Agent] = {}
    for platform, rows in profiles.items():
        for idx, row in enumerate(rows):
            aid = str(row.get("user_id", idx))
            agent = agents.setdefault(aid, Agent(agent_id=aid))
            agent.profiles[platform] = PlatformProfile(
                username=_opt(row.get("username") or row.get("user_name")),
                name=_opt(row.get("name")),
                bio=_opt(row.get("bio") or row.get("description")),
            )
    for aid, agent in agents.items():
        agent.persona_card_id = handle.agent_personas.get(aid)
    return list(agents.values())


def _opt(v: Any) -> str | None:
    return str(v) if v not in (None, "") else None


def _to_answer(
    q: InterviewQuestion, agent_id: str, item: dict[str, Any], handle: RunHandle
) -> InterviewAnswer:
    text = str(item.get("response") or "")
    likert = None
    price = None
    nums = [float(n.replace(",", ".")) for n in _NUMBER.findall(text)]
    if q.kind == "purchase_likelihood":
        likert = next((int(n) for n in nums if 1 <= n <= 5 and n == int(n)), None)
    elif q.kind.startswith("vw_") and nums:
        price = nums[0] if nums[0] >= 0 else None
    return InterviewAnswer(
        question_id=q.id,
        agent_id=agent_id,
        persona_card_id=handle.agent_personas.get(agent_id),
        platform=_platform(item.get("platform"), handle.platforms[0]),
        answer_text=text,
        likert=likert,
        price_value=price,
    )


def action_type_counts(actions: Sequence[Action]) -> dict[str, int]:
    return dict(Counter(a.type for a in actions))
