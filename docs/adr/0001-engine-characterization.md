# ADR 0001: MiroFish engine characterization (Phase 0)

- Status: **in progress** — mock-only part done; live budget approved 2026-10-21; `make live-run`
  is ready but **blocked: Zep Cloud rejects the dev key (HTTP 401)**. No LLM spend so far.
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

## Live run harness (2026-10-22)
`make live-run` (`infra/compose/phase0-live-run.sh` + `infra/live/run_live.py`) runs one
end-to-end run: 10 agents x 3 rounds, reddit only, through `MiroFishEngine`
(prepare, start, poll, interview, collect, cleanup), then asks Zep directly whether the
graph is gone (A6). `make live-preflight` does the no-spend checks only.

- Layout: engine, `llm-gateway` and `egress-proxy` share an `--internal` network; only the
  egress proxy also joins a normal bridge. Same hardening as the mock spike (read-only
  rootfs, `--cap-drop ALL`, `no-new-privileges`, UID 10001, tmpfs mounts).
- Engine gets `LLM_BASE_URL=http://llm-gateway:4000/v1` with a per-run dummy key, and
  `HTTPS_PROXY=http://egress-proxy:3128` (`NO_PROXY=llm-gateway`). The gateway holds the
  real key, pins the model, caps spend (`BUDGET_USD=5`, `MAX_REQUESTS`) and logs tokens.
  It also goes out through the egress proxy. Allow-list: `api.anthropic.com`, `api.getzep.com`.
- Route: `claude-haiku-5-5` on Anthropic's OpenAI-compatible endpoint, priced at
  $0.10 in / $0.50 out per million tokens.
- Outputs (in `.live-runs/<ts>/`, git-ignored): spec, handle, status trace, artifacts,
  cleanup report, Zep check, summary, egress log, gateway log, engine logs.
- Pre-flight stops before any spend unless: `import torch, oasis` works in the image, Zep
  accepts the key, and the LLM key lists models.

### What was verified without spend
| Check | Result |
|---|---|
| `import torch, oasis` in the image | OK (torch 2.9.1+cu128) with the sandbox CUDA layer below. |
| Host -> engine by container IP on the internal network | OK. |
| Engine -> `api.getzep.com`, `api.anthropic.com` via egress proxy | Allowed, logged; TLS works with `EXTRA_CA_BUNDLE`. |
| Engine -> `huggingface.co`, `pypi.org`, plain HTTP | Denied (403), logged. |
| Engine -> internet without the proxy | No route (no external DNS on the internal network). |
| Driver end-to-end against `FakeMiroFish` | OK, all output files written. |
| Zep key | **401 on every call**, including with the official `zep-cloud==3.25.0` SDK; same response as a made-up key. |

### Blocker
Zep Cloud rejects `ZEP_API_KEY` (HTTP 401 on `users-ordered`, `graph/list-all`,
`projects/info`). The graph build needs Zep, so a run would fail after the ontology step.
Next step: Peter issues a new Zep dev-project key, then `make live-preflight && make live-run`.

### New findings
- **Anthropic's OpenAI-compatible endpoint rejects `temperature` and
  `response_format: json_object`.** MiroFish sends both (`simulation_config_generator.py`,
  `oasis_profile_generator.py`, `llm_client.py`). The gateway drops sampling params and turns
  `json_object` into a JSON-only instruction (plus code-fence stripping). Requests without
  `max_tokens` work. The production gateway (§2.4) needs the same shim for this route.
- **`oasis` writes `./log` at import time** (`oasis/social_platform/platform.py`). The engine
  launches simulations with `cwd` in `uploads/` (tmpfs), so it works; anything else that
  imports `oasis` on the read-only rootfs needs a writable `cwd`.
- **Recommender model downloads.** The reddit recommender needs no model. The twitter one loads
  `paraphrase-MiniLM-L6-v2` from HuggingFace at run time, which the egress allow-list blocks.
  Before running twitter: bake the model into the image (preferred) or allow-list the hub.
- **torch.** `download.pytorch.org` is still blocked in this sandbox. The image was built with
  `TORCH_INDEX_URL=https://pypi.org/simple` (CUDA torch without its deps), then
  `make engine-image-sandbox` adds torch's locked `nvidia-*`/`triton` wheels
  (`infra/live/Dockerfile.sandbox-cuda`, sandbox only, never shipped). The CPU-index build is
  still unverified.

## Assumption register status
| ID | Status | Evidence / next step |
|---|---|---|
| A1 agent count | **Open, at risk.** | Saved profiles carry no entity type and the engine pads ontologies. The adapter maps agents to persona cards by entity name (`"<name> (<card_id>)"`). Needs the 10× prepare study at 25/50/100/200 (live). |
| A2 routes stable | **Mostly confirmed** by reading code; contract suite runs against a fake built from it. | Differences: `prepare/status` and `report/generate/status` are POST; `GET /api/report/<id>` returns JSON with `markdown_content`; graph task returns `result.graph_id`; posts/comments are raw OASIS rows keyed by DB user id; `interview/batch` results keyed `<platform>_<agent_id>`; no route deletes a simulation; error bodies include Python tracebacks (adapter strips them). Live contract tests still to run against the image. |
| A3 interview/batch | Open. | Route exists; needs a live environment (`env-status` → `env_alive`) and upstream adds its own prompt prefix, which affects price-probe wording. |
| A4 all LLM traffic via `LLM_BASE_URL` | **Partly supported.** | Ontology call reached the fake gateway; no egress existed to go elsewhere. OASIS subprocess calls not yet exercised (need Zep stage). Live harness routes the engine through the gateway with `api.anthropic.com` reachable only via the egress proxy, and `egress.jsonl` records the client IP of every tunnel: any `api.anthropic.com` tunnel from the engine's IP (not the gateway's) means a call bypassed `LLM_BASE_URL`. |
| A5 Zep hostnames | **Candidate:** `api.getzep.com` (only Zep URL in `zep-cloud==3.25.0`, `ZEP_CLOUD_BASE_URL` in `app/utils/zep.py`). | Egress proxy now logs every host. Confirm from `egress.jsonl` in the live run before pinning the allowlist. |
| A6 graph deletion | Open. | `DELETE project/<id>` deletes its Zep graph; `graph/delete` refuses unreferenced graphs. Verify on Zep's side (live). The driver now checks `GET /api/v2/graph/<id>` returns 404 after cleanup. |
| A7 no mid-round resume | Open. | Kill tests need a live run. |
| A8 vendor terms | Open (legal). | `docs/compliance/vendors.md`. |
| A9 cross-model variance | Open. | Needs two live model families. |

## Decision needed from Peter
**Approved by Peter on 2026-10-21.** A small, capped live budget for: a Zep Cloud key (dev project) and one LLM route,
to run (a) one end-to-end run at 10 agents / 3 rounds behind a deny-all egress proxy, and
(b) the A1 agent-count study. Nothing live runs until approved.
