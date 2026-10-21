# ADR 0001: MiroFish engine characterization (Phase 0)

- Status: **in progress** — mock-only part done; live part approved, blocked on keys and egress (see Live run log)
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

## Live run log
### 2026-10-21 — pre-flight check (blocked, no spend)
- **Keys:** no Zep or LLM API key in the session environment.
- **Egress:** the sandbox proxy has a Zep entry for `app.getzep.com`, but that host is Zep's
  web dashboard (Next.js, every `/api/v2/*` path returns an HTML 404). The SDK's API host
  `api.getzep.com` is refused by the proxy (CONNECT 403). All LLM provider hosts tried
  (`api.openai.com`, `openrouter.ai`, `api.deepseek.com`, `dashscope.aliyuncs.com`) are
  refused, as is `download.pytorch.org` (CPU torch index).
- **Needed before retrying:** `api.getzep.com` and one LLM provider host allowed in the
  environment's network settings, and `ZEP_API_KEY` plus `LLM_API_KEY` (and `LLM_BASE_URL`,
  `LLM_MODEL_NAME` if not OpenAI) set as environment secrets. Nothing live was run.
