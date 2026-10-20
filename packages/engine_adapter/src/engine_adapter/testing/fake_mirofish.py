"""In-process fake of the MiroFish HTTP API, written from the route contract (ADR 0001).

It mimics reply shapes, the success/data envelope, async tasks that need polling, and
OASIS rows whose ``user_id`` differs from the agent id. Fault flags support chaos tests.
"""

from __future__ import annotations

import itertools
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

_ENTITY = re.compile(r"## ([^\n(]+?) \((pc_[A-Za-z0-9]+)\)")


@dataclass
class FakeMiroFish:
    polls_until_done: int = 2
    rounds: int = 4
    agent_shortfall: int = 0  # drop this many profiles
    graph_task_fails: bool = False
    env_alive: bool = True
    report_fails: bool = False
    fail_once: dict[str, int] = field(default_factory=dict)  # "METHOD path" -> status
    calls: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    _ids: itertools.count[int] = field(default_factory=itertools.count)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    sims: dict[str, dict[str, Any]] = field(default_factory=dict)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # --- helpers ---

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._ids):04d}"

    @staticmethod
    def ok(data: Any, status: int = 200) -> httpx.Response:
        return httpx.Response(status, json={"success": True, "data": data})

    @staticmethod
    def err(msg: str, status: int) -> httpx.Response:
        return httpx.Response(
            status, json={"success": False, "error": msg, "traceback": "Traceback (secret)"}
        )

    def _task(self, kind: str, result: dict[str, Any], fail: bool = False) -> str:
        tid = self._id("task")
        self.tasks[tid] = {"kind": kind, "polls": 0, "result": result, "fail": fail}
        return tid

    def _task_view(self, tid: str) -> dict[str, Any]:
        t = self.tasks[tid]
        t["polls"] += 1
        if t["fail"]:
            status = "failed"
        elif t["polls"] >= self.polls_until_done:
            status = "completed"
        else:
            status = "processing"
        return {
            "task_id": tid,
            "task_type": t["kind"],
            "status": status,
            "progress": 100 if status == "completed" else 40,
            "message": "",
            "result": t["result"] if status == "completed" else None,
            "error": "zep build failed" if status == "failed" else None,
        }

    # --- router ---

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        key = f"{method} {path}"
        self.calls.append(key)
        if key in self.fail_once:
            status = self.fail_once.pop(key)
            return httpx.Response(status, json={"success": False, "error": "injected"})
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        body: dict[str, Any] = {}
        ctype = request.headers.get("content-type", "")
        if request.content and "application/json" in ctype:
            body = json.loads(request.content)

        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "MiroFish Backend"})
        if path == "/api/graph/ontology/generate" and method == "POST":
            return self._ontology(request)
        if path == "/api/graph/build" and method == "POST":
            return self._build(body)
        if m := re.fullmatch(r"/api/graph/task/(\w+)", path):
            if m.group(1) not in self.tasks:
                return self.err("task not found", 404)
            return self.ok(self._task_view(m.group(1)))
        if m := re.fullmatch(r"/api/graph/delete/(\w+)", path):
            gid = m.group(1)
            if not any(p.get("graph_id") == gid for p in self.projects.values()):
                return self.err("No local project references this graph", 404)
            for p in self.projects.values():
                if p.get("graph_id") == gid:
                    p["graph_id"] = None
            self.deleted.append(f"graph:{gid}")
            return httpx.Response(200, json={"success": True, "message": "deleted"})
        if m := re.fullmatch(r"/api/graph/project/(\w+)", path):
            pid = m.group(1)
            if method == "DELETE":
                if self.projects.pop(pid, None) is None:
                    return self.err("project not found", 404)
                self.deleted.append(f"project:{pid}")
                return httpx.Response(200, json={"success": True, "message": "deleted"})
            return self.ok(self.projects[pid])
        if path == "/api/simulation/create":
            return self._create(body)
        if path == "/api/simulation/prepare":
            sim = self.sims[body["simulation_id"]]
            if body.get("entity_types") != ["Consumer"]:
                return self.err("unexpected entity types", 400)
            tid = self._task("simulation_prepare", {"simulation_id": sim["id"]})
            return self.ok(
                {
                    "simulation_id": sim["id"],
                    "task_id": tid,
                    "status": "preparing",
                    "already_prepared": False,
                    "expected_entities_count": len(sim["profiles"]),
                    "entity_types": ["Consumer"],
                }
            )
        if path == "/api/simulation/prepare/status":
            view = self._task_view(body["task_id"])
            view["already_prepared"] = False
            if view["status"] == "completed":
                self.sims[body["simulation_id"]]["prepared"] = True
            return self.ok(view)
        if m := re.fullmatch(r"/api/simulation/(\w+)/profiles", path):
            sim = self.sims[m.group(1)]
            platform = q.get("platform", "reddit")
            profiles = sim["profiles"]
            if platform == "twitter":  # CSV-derived rows: all strings, different columns
                profiles = [
                    {
                        "user_id": str(p["user_id"]),
                        "name": p["name"],
                        "username": p["username"],
                        "description": p["bio"],
                    }
                    for p in profiles
                ]
            return self.ok({"platform": platform, "count": len(profiles), "profiles": profiles})
        if path == "/api/simulation/start":
            sim = self.sims.get(body.get("simulation_id", ""), {})
            if not sim:
                return self.err("simulation not found", 404)
            if not sim.get("prepared"):
                return self.err("simulation not ready", 400)
            sim.update(runner="running", polls=0, max_rounds=body.get("max_rounds"))
            return self.ok({"simulation_id": sim["id"], "runner_status": "running"})
        if m := re.fullmatch(r"/api/simulation/(\w+)/run-status", path):
            return self._run_status(self.sims[m.group(1)])
        if m := re.fullmatch(r"/api/simulation/(\w+)/actions", path):
            acts = self._actions(self.sims[m.group(1)])
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 100))
            page = acts[off : off + lim]
            return self.ok({"count": len(page), "actions": page})
        if m := re.fullmatch(r"/api/simulation/(\w+)/posts", path):
            rows = self._rows(self.sims[m.group(1)], q.get("platform", "reddit"), "CREATE_POST")
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 50))
            page = rows[off : off + lim]
            return self.ok(
                {
                    "platform": q.get("platform"),
                    "total": len(rows),
                    "count": len(page),
                    "posts": page,
                }
            )
        if m := re.fullmatch(r"/api/simulation/(\w+)/comments", path):
            rows = self._rows(self.sims[m.group(1)], q.get("platform", "reddit"), "CREATE_COMMENT")
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 50))
            page = rows[off : off + lim]
            return self.ok({"count": len(page), "comments": page})
        if m := re.fullmatch(r"/api/simulation/(\w+)/timeline", path):
            return self.ok(self._timeline(self.sims[m.group(1)]))
        if path == "/api/simulation/env-status":
            return self.ok({"simulation_id": body["simulation_id"], "env_alive": self.env_alive})
        if path == "/api/simulation/interview/batch":
            return self._interview(body)
        if path in ("/api/simulation/stop", "/api/simulation/close-env"):
            target = self.sims.get(body.get("simulation_id", ""))
            if target is None:
                return self.err("not found", 404)
            if path.endswith("stop"):
                if target.get("runner") != "running":
                    return self.err("not running", 400)
                target["runner"] = "stopped"
            return self.ok({"simulation_id": target["id"]})
        if path == "/api/report/generate":
            if self.report_fails:
                return self.err("report agent failed", 500)
            rid = self._id("report")
            self.reports[rid] = {"simulation_id": body["simulation_id"]}
            tid = self._task("report_generate", {"report_id": rid})
            return self.ok(
                {
                    "simulation_id": body["simulation_id"],
                    "report_id": rid,
                    "task_id": tid,
                    "status": "generating",
                    "already_generated": False,
                }
            )
        if path == "/api/report/generate/status":
            return self.ok(self._task_view(body["task_id"]))
        if m := re.fullmatch(r"/api/report/by-simulation/(\w+)", path):
            for rid, r in self.reports.items():
                if r["simulation_id"] == m.group(1):
                    return self.ok({"report_id": rid, "simulation_id": m.group(1)})
            return self.err("report not found", 404)
        if m := re.fullmatch(r"/api/report/(\w+)", path):
            rid = m.group(1)
            if method == "DELETE":
                if self.reports.pop(rid, None) is None:
                    return self.err("report not found", 404)
                self.deleted.append(f"report:{rid}")
                return httpx.Response(200, json={"success": True, "message": "deleted"})
            if rid not in self.reports:
                return self.err("report not found", 404)
            return self.ok(
                {"report_id": rid, "status": "completed", "markdown_content": "# Engine report\n"}
            )
        return self.err(f"no route {key}", 404)

    # --- route bodies ---

    def _ontology(self, request: httpx.Request) -> httpx.Response:
        raw = request.content.decode("utf-8", errors="replace")
        if 'name="files"' not in raw:
            return self.err("no files", 400)
        if 'name="simulation_requirement"' not in raw:
            return self.err("simulation_requirement required", 400)
        entities = [(n.strip(), cid) for n, cid in _ENTITY.findall(raw)]
        pid = self._id("proj")
        self.projects[pid] = {
            "project_id": pid,
            "status": "ontology_generated",
            "entities": entities,
            "graph_id": None,
        }
        return self.ok({"project_id": pid, "project_name": "x", "ontology": {}, "files": []})

    def _build(self, body: dict[str, Any]) -> httpx.Response:
        proj = self.projects.get(body.get("project_id", ""))
        if proj is None:
            return self.err("project not found", 404)
        gid = self._id("mirofish")
        proj["graph_id"] = gid
        tid = self._task(
            "graph_build",
            {"project_id": proj["project_id"], "graph_id": gid},
            fail=self.graph_task_fails,
        )
        return self.ok({"project_id": proj["project_id"], "task_id": tid, "message": "started"})

    def _create(self, body: dict[str, Any]) -> httpx.Response:
        proj = self.projects[body["project_id"]]
        sid = self._id("sim")
        ents = proj["entities"]
        if self.agent_shortfall:
            ents = ents[: max(0, len(ents) - self.agent_shortfall)]
        profiles = [
            {
                "user_id": i,
                "username": f"user_{i}",
                "name": f"{name} ({cid})",
                "bio": f"Consumer persona {name}",
                "persona": f"{name} is a consumer.",
                "karma": 1000,
                "age": 30,
            }
            for i, (name, cid) in enumerate(ents)
        ]
        self.sims[sid] = {
            "id": sid,
            "project_id": proj["project_id"],
            "profiles": profiles,
            "runner": "idle",
            "twitter": body.get("enable_twitter", True),
            "reddit": body.get("enable_reddit", True),
        }
        return self.ok(
            {"simulation_id": sid, "project_id": proj["project_id"], "status": "created"}
        )

    def _run_status(self, sim: dict[str, Any]) -> httpx.Response:
        if sim["runner"] == "running":
            sim["polls"] += 1
            if sim["polls"] >= self.polls_until_done:
                sim["runner"] = "completed"
        total = self.rounds
        cur = total if sim["runner"] == "completed" else min(total, sim.get("polls", 0))
        return self.ok(
            {
                "simulation_id": sim["id"],
                "runner_status": sim["runner"],
                "current_round": cur,
                "total_rounds": total,
                "updated_at": "2026-10-20T12:00:00",
                "error": None,
            }
        )

    def _platforms(self, sim: dict[str, Any]) -> list[str]:
        return [p for p in ("twitter", "reddit") if sim[p]]

    def _actions(self, sim: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rnd in range(1, self.rounds + 1):
            for prof in sim["profiles"]:
                aid = prof["user_id"]
                for platform in self._platforms(sim):
                    if (aid + rnd) % 2 == 0:
                        out.append(
                            {
                                "round_num": rnd,
                                "timestamp": "2026-10-20T12:00:00",
                                "platform": platform,
                                "agent_id": aid,
                                "agent_name": prof["name"],
                                "action_type": "create_post",
                                "action_args": {"content": f"post r{rnd} a{aid} on {platform}"},
                                "success": True,
                            }
                        )
                    elif platform == "reddit" and rnd > 1:
                        out.append(
                            {
                                "round_num": rnd,
                                "platform": platform,
                                "agent_id": aid,
                                "agent_name": prof["name"],
                                "action_type": "create_comment",
                                "action_args": {
                                    "content": f"comment r{rnd} a{aid}",
                                    "post_id": 1,
                                },
                                "success": True,
                            }
                        )
                    else:
                        out.append(
                            {
                                "round_num": rnd,
                                "platform": platform,
                                "agent_id": aid,
                                "action_type": "like_post",
                                "action_args": {"post_id": 1},
                                "success": True,
                            }
                        )
        return out

    def _rows(self, sim: dict[str, Any], platform: str, kind: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for a in self._actions(sim):
            if a["platform"] != platform or a["action_type"].upper() != kind:
                continue
            row = {
                "user_id": a["agent_id"] + 1,  # OASIS DB user_id is not the agent id
                "content": a["action_args"]["content"],
                "created_at": "2026-10-20 12:00:00",
                "num_likes": 1,
                "num_dislikes": 0,
            }
            if kind == "CREATE_POST":
                row.update(post_id=len(rows) + 1, num_shares=0, original_post_id=None)
            else:
                row.update(comment_id=len(rows) + 1, post_id=1)
            rows.append(row)
        return rows

    def _timeline(self, sim: dict[str, Any]) -> dict[str, Any]:
        acts = self._actions(sim)
        rounds: dict[int, list[dict[str, Any]]] = {}
        for a in acts:
            rounds.setdefault(a["round_num"], []).append(a)
        tl = []
        for rnd, items in sorted(rounds.items()):
            tw = sum(1 for a in items if a["platform"] == "twitter")
            rd = len(items) - tw
            tl.append(
                {
                    "round_num": rnd,
                    "twitter_actions": tw,
                    "reddit_actions": rd,
                    "total_actions": tw + rd,
                    "active_agents_count": len({a["agent_id"] for a in items}),
                    "active_agents": sorted({a["agent_id"] for a in items}),
                    "action_types": dict(Counter(a["action_type"] for a in items)),
                }
            )
        return {"rounds_count": len(tl), "timeline": tl}

    def _interview(self, body: dict[str, Any]) -> httpx.Response:
        if not self.env_alive:
            return self.err("env not running", 400)
        platform = body.get("platform") or "reddit"
        results = {}
        for item in body["interviews"]:
            aid = item["agent_id"]
            prompt = item["prompt"]
            if "1 to 5" in prompt:
                answer = f"I would say {(aid % 5) + 1} out of 5."
            elif "price" in prompt.lower():
                answer = f"Probably around {60 + aid} dollars."
            else:
                answer = "It looks interesting but I need to know more."
            results[f"{platform}_{aid}"] = {
                "agent_id": aid,
                "response": answer,
                "platform": platform,
            }
        return self.ok(
            {
                "success": True,
                "interviews_count": len(results),
                "result": {"interviews_count": len(results), "results": results},
                "timestamp": "2026-10-20T12:00:00",
            }
        )
