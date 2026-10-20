# engines/mirofish (AGPL-3.0 zone)

Builds the MiroFish backend from the pinned upstream commit. Nothing in this folder is
imported by Coreset code; the engine adapter reaches it over HTTP only.

```bash
docker build -t coreset/mirofish-engine:0.1.2-git7657031-coreset.0 engines/mirofish
```

Runtime hardening (set by Compose / k8s, see infra/compose): non-root UID 10001,
read-only root filesystem, `backend/uploads` as a 2 GB tmpfs, all capabilities dropped,
`no-new-privileges`, internal-only network. Env injected per run by the worker:
`LLM_BASE_URL`, `LLM_API_KEY` (per-run virtual key), `LLM_MODEL_NAME`, `ZEP_API_KEY`,
`SECRET_KEY` (random), `FLASK_DEBUG=false`.
