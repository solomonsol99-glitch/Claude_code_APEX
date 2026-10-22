# ADR 0001: MiroFish engine characterization (Phase 0)

- Status: **in progress** — mock-only part done; the live part waits on Peter's spend approval
- Date: 2026-10-20
- Blueprint sections: §8.1, §2.4, assumption register A1–A9
- Pinned upstream commit: `7657031ac01184afe2cb220f5ee3545573b5e843` (upstream HEAD still matched on 2026-10-20)

## What was run (no spend)
1. Read the upstream route code at the pinned commit for field names only (no code copied).
2. Built `engines/mirofish` (backend only, non-root UID 10001, read-only rootfs).
3. `infra/compose/phase0-mock-spike.sh`: engine + fake OpenAI-compatible LLM on an
   `--internal` Docker network, placeholder Zep key.

## Spike results
| Check | Result |
|---|---|
| Image builds from pinned commit, runs as UID 10001 | Yes. Runtime image 4.1 GB (sandbox build; see torch note). |
| Read-only root filesystem | Works once `backend/uploads` **and `backend/logs`** are tmpfs mounts. |
| Repo-root files needed at runtime | `locales/` (read at import by `app/utils/locale.py`); now copied into the image. |
| `GET /health` | 200 `{"service":"MiroFish Backend","status":"ok"}` — upstream route, no patch needed. |
| `GET /api/simulation/list` | 200, empty list. |
| `POST /api/graph/ontology/generate` | 200 with `project_id`; 1 LLM call, received by the fake gateway with `response_format` set. |
| Ontology steering | We supplied 2 entity types; the engine returned **4** (it pads to a minimum count). Relevant to A1: `entity_types` filtering at `/prepare` must name ours explicitly. |
| Graph build onward | **Not run.** Needs a real Zep Cloud key (upstream rejects `ZEP_API_URL`, so Zep cannot be stubbed by config). |

### Build findings
- The upstream lock pulls **CUDA PyTorch** through `sentence-transformers` (~36 `nvidia-*`
  wheels, many GB). A verbatim install filled the build disk twice. The Dockerfile now
  installs every locked package at its locked version except torch/triton/nvidia, then the
  same torch version from the CPU wheel index (`TORCH_INDEX_URL`, default
  `https://download.pytorch.org/whl/cpu`).
- In this sandbox the CPU index is blocked, so the test image used PyPI torch without CUDA
  libs; `import torch` fails there. Startup and ontology generation do not import torch, so
  torch is used later (simulation/recsys). **The CPU-index build is unverified** and must be
  checked in CI or on a machine that can reach the index.
- Docker Hub rate-limited the sandbox; `REGISTRY_PREFIX` lets builds use a mirror.

## Live run attempts (`make live-run`, 2026-10-22)
Harness: `infra/compose/phase0-live-run.sh`. Engine and an LLM compatibility shim
(`infra/compose/llm_compat.py`) sit on an `--internal` network. The only way out is an
allowlisting CONNECT proxy (`infra/compose/egress_proxy.py`) that logs every attempt.
The shim holds the LLM key, so the engine only sees a placeholder.

| Attempt | Route | Result |
|---|---|---|
| 1 | Anthropic OpenAI-compatible endpoint, `claude-haiku-5-5` | Ontology call rejected (400). The endpoint refuses `temperature` ("deprecated for this model") and `response_format: json_object` (only strict `json_schema`). MiroFish sends both on every call. |
| 2 | Same, through the shim (drops `temperature` and `json_object`) | Ontology generated (1 call, 2.8k prompt + 3.3k completion tokens). Graph build then failed: Zep Cloud answered **401 unauthorized** to the configured key, from the engine and from the host alike. |

The egress log shows the engine reached only `api.anthropic.com` (through the shim) and
`api.getzep.com`. Nothing was denied. This supports A5, but graph build onward is still unexercised.
**Blocked on a valid Zep Cloud key** (dev project).

## Assumption register status
| ID | Status | Evidence / next step |
|---|---|---|
| A1 agent count | **Open, at risk.** | Saved profiles carry no entity type and the engine pads ontologies. The adapter maps agents to persona cards by entity name (`"<name> (<card_id>)"`). Needs the 10× prepare study at 25/50/100/200 (live). |
| A2 routes stable | **Mostly confirmed** by reading code; contract suite runs against a fake built from it. | Differences: `prepare/status` and `report/generate/status` are POST; `GET /api/report/<id>` returns JSON with `markdown_content`; graph task returns `result.graph_id`; posts/comments are raw OASIS rows keyed by DB user id; `interview/batch` results keyed `<platform>_<agent_id>`; no route deletes a simulation; error bodies include Python tracebacks (adapter strips them). Live contract tests still to run against the image. |
| A3 interview/batch | Open. | Route exists; needs a live environment (`env-status` → `env_alive`) and upstream adds its own prompt prefix, which affects price-probe wording. |
| A4 all LLM traffic via `LLM_BASE_URL` | **Partly supported.** | Ontology call reached the fake gateway; no egress existed to go elsewhere. OASIS subprocess calls not yet exercised (need Zep stage). |
| A5 Zep hostnames | **Candidate:** `api.getzep.com` (only Zep URL in `zep-cloud==3.25.0`, `ZEP_CLOUD_BASE_URL` in `app/utils/zep.py`). | Confirm from egress-proxy logs in the live run before pinning the allowlist. |
| A6 graph deletion | Open. | `DELETE project/<id>` deletes its Zep graph; `graph/delete` refuses unreferenced graphs. Verify on Zep's side (live). |
| A7 no mid-round resume | Open. | Kill tests need a live run. |
| A8 vendor terms | Open (legal). | `docs/compliance/vendors.md`. |
| A9 cross-model variance | Open. | Needs two live model families. |

## Decision needed from Peter
**Approved by Peter on 2026-10-21.** A small, capped live budget for: a Zep Cloud key (dev project) and one LLM route,
to run (a) one end-to-end run at 10 agents / 3 rounds behind a deny-all egress proxy, and
(b) the A1 agent-count study. Nothing live runs until approved.
