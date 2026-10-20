# Coreset Market Response Engine (MRE)

A multi-tenant service that runs the open-source MiroFish simulation engine (in an
isolated container, behind an adapter) and produces white-labeled **Market Response
Reports**: directional, qualitative research from simulated personas, with confidence
grades, ensemble variance and standard caveats. It never produces forecasts or
guarantees.

The full spec is the engineering blueprint (`MiroFish_Platform_Blueprint.md`, v1.0,
Oct 20 2026). Section references below (§N) point into it.

## Build status

| Step | What | Status |
|---|---|---|
| 0 | Phase 0 engine spike | Mock-only part in progress; live run waits on Peter's spend approval. See `docs/adr/0001-engine-characterization.md` |
| 1 | Repo skeleton, tooling, CI, Compose | Done |
| 2 | `packages/core`: config, tenant schema, DB, RLS | Done |
| 3 | `packages/engine_adapter` (+ minimal `packages/net`) | Done |
| 4–13 | Gateway, seed builder, personas, worker, analysis, reporting, API, portal, test suites, ops | Not started |

## Layout

As in §2.7. Python packages use an `mre_` module prefix (`mre_core`, `mre_net`, …)
except `engine_adapter`, which keeps the blueprint's name (see ADR 0002).

## Quick start

```bash
make install          # uv sync + pre-commit hooks
make lint typecheck   # ruff + mypy --strict
make test             # pytest; DB tests start a throwaway Postgres 16 or use DATABASE_URL
make engine-image     # build the pinned MiroFish engine image
cp infra/compose/.env.example infra/compose/.env   # then edit placeholders
make up               # Postgres, Redis, MinIO, Vault (dev), OTel collector
make spike-up         # Phase 0: engine + fake LLM (no spend)
```

Tests marked `live` (real LLM / Zep) never run by default.

## Ground rules

- The engine is reached **only over HTTP** through the adapter. No MiroFish imports,
  no copied MiroFish code or prompts (AGPL boundary, §3.12). `tests/license/` enforces it.
- No secrets in code, fixtures, YAML or history; tenant YAML holds `vault://` refs only.
- Report wording never says "predict", "forecast", "guarantee" or "will <outcome>";
  `tests/redteam/test_report_language.py` enforces it.
- Ask Peter before: sending anything externally, deploying, spending on live LLM/Zep,
  pushing to main or merging, touching the licensed consumer file or `enclave/`,
  publishing the engine fork, or changing security controls.
