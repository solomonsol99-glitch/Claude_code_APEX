#!/usr/bin/env bash
# Phase 0 live run (ADR 0001, blueprint §8.1): one small end-to-end run against the pinned
# engine with a real LLM route and Zep Cloud, behind a deny-all egress proxy.
# SPENDS MONEY (capped by BUDGET_USD at the gateway). Approved by Peter on 2026-10-21.
#
# Layout:
#   mre-live-int (--internal): engine, llm-gateway, egress-proxy
#   mre-live-ext (bridge):     egress-proxy only
#   engine -> llm-gateway (dummy key) for LLM; engine and gateway -> egress-proxy for HTTPS.
#   The egress proxy allows only $EGRESS_ALLOW (default api.anthropic.com,api.getzep.com).
#   The host driver (infra/live/run_live.py) reaches the engine by its container IP.
#
# Required env: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL_NAME, ZEP_API_KEY
# Optional env: IMAGE, OUT, AGENTS (10), ROUNDS (3), BUDGET_USD (5), MAX_REQUESTS (2000),
#   PRICE_IN_PER_MTOK (0.10), PRICE_OUT_PER_MTOK (0.50), EXTRA_CA_BUNDLE (PEM file, e.g.
#   a sandbox TLS proxy CA, mounted read-only), EGRESS_UPSTREAM_PROXY, REGISTRY_PREFIX,
#   PREFLIGHT_ONLY=1 (checks only, no spend), STACK_ONLY=1 (bring the stack up, probe the
#   wiring and egress allow/deny, then stop; makes no LLM calls, so no spend).
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${LLM_BASE_URL:?set LLM_BASE_URL}" "${LLM_API_KEY:?set LLM_API_KEY}"
: "${LLM_MODEL_NAME:?set LLM_MODEL_NAME}" "${ZEP_API_KEY:?set ZEP_API_KEY}"
IMAGE="${IMAGE:-coreset/mirofish-engine:dev}"
PY_IMAGE="${PY_IMAGE:-${REGISTRY_PREFIX:-}python:3.12-slim-bookworm}"
OUT="${OUT:-.live-runs/$(date -u +%Y%m%dT%H%M%SZ)}"
AGENTS="${AGENTS:-10}" ROUNDS="${ROUNDS:-3}"
BUDGET_USD="${BUDGET_USD:-5}" MAX_REQUESTS="${MAX_REQUESTS:-2000}"
PRICE_IN_PER_MTOK="${PRICE_IN_PER_MTOK:-0.10}" PRICE_OUT_PER_MTOK="${PRICE_OUT_PER_MTOK:-0.50}"
EGRESS_ALLOW="${EGRESS_ALLOW:-api.anthropic.com,api.getzep.com}"
INT=mre-live-int EXT=mre-live-ext
ENGINE=mre-live-engine GATEWAY=mre-live-llm-gateway PROXY=mre-live-egress-proxy
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
log() { echo "[live-run] $*" >&2; }

# Common hardening (same flags as phase0-mock-spike.sh).
HARDEN=(--read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001)
CA_ARGS=()
if [ -n "${EXTRA_CA_BUNDLE:-}" ]; then
  CA_ARGS=(-v "$(realpath "$EXTRA_CA_BUNDLE"):/etc/mre/extra-ca.pem:ro"
           -e SSL_CERT_FILE=/etc/mre/extra-ca.pem -e REQUESTS_CA_BUNDLE=/etc/mre/extra-ca.pem)
fi

# ---- 1. Pre-flight (no spend) ------------------------------------------------------------
log "pre-flight: torch + oasis import in $IMAGE"
docker run --rm --network none "${HARDEN[@]}" --tmpfs /tmp:size=256m,uid=10001,gid=10001 -w /tmp \
  "$IMAGE" python -c "import torch, oasis; print('torch', torch.__version__, 'oasis ok')" \
  | tee "$OUT/preflight.txt"

log "pre-flight: Zep key"
zep_code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Api-Key $ZEP_API_KEY" \
  "https://api.getzep.com/api/v2/users-ordered?pageSize=1" || echo 000)"
echo "zep users-ordered: HTTP $zep_code" | tee -a "$OUT/preflight.txt"
if [ "$zep_code" != 200 ]; then
  log "Zep rejected the key (HTTP $zep_code). Fix ZEP_API_KEY before spending."
  [ "${STACK_ONLY:-0}" = 1 ] || exit 2
fi

log "pre-flight: LLM key (model list, free)"
llm_code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $LLM_API_KEY" \
  -H "x-api-key: $LLM_API_KEY" -H 'anthropic-version: 2023-06-01' \
  "${LLM_BASE_URL%/}/models" || echo 000)"
echo "llm models: HTTP $llm_code" | tee -a "$OUT/preflight.txt"
[ "$llm_code" = 200 ] || { log "LLM route rejected the key (HTTP $llm_code); stopping."; exit 2; }
if [ "${PREFLIGHT_ONLY:-0}" = 1 ]; then log "pre-flight passed (PREFLIGHT_ONLY=1)"; exit 0; fi

# ---- 2. Stack -------------------------------------------------------------------------------
collect() {
  log "collecting logs into $OUT"
  docker exec "$PROXY" cat /tmp/egress.jsonl > "$OUT/egress.jsonl" 2>/dev/null || true
  docker exec "$GATEWAY" cat /tmp/gateway.jsonl > "$OUT/gateway.jsonl" 2>/dev/null || true
  docker exec "$GATEWAY" python -c "import urllib.request as u;print(u.urlopen('http://127.0.0.1:4000/health').read().decode())" \
    > "$OUT/gateway_state.json" 2>/dev/null || true
  docker logs "$ENGINE" > "$OUT/engine.log" 2>&1 || true
  docker exec "$ENGINE" tar -C /opt/mirofish/backend -czf - logs > "$OUT/engine-logs.tgz" 2>/dev/null || true
  docker logs "$PROXY" > "$OUT/egress-proxy.log" 2>&1 || true
  docker logs "$GATEWAY" > "$OUT/llm-gateway.log" 2>&1 || true
}
teardown() { docker rm -f "$ENGINE" "$GATEWAY" "$PROXY" >/dev/null 2>&1 || true
             docker network rm "$INT" "$EXT" >/dev/null 2>&1 || true; }
trap 'collect; teardown' EXIT
teardown
docker network create --internal "$INT" >/dev/null
docker network create "$EXT" >/dev/null

log "starting egress proxy (allow: $EGRESS_ALLOW)"
docker run -d --name "$PROXY" --network "$EXT" "${HARDEN[@]}" \
  --tmpfs /tmp:size=64m,uid=10001,gid=10001 --memory 256m --cpus 0.5 \
  -v "$PWD/infra/live/egress_proxy.py:/app/egress_proxy.py:ro" \
  -e EGRESS_ALLOW="$EGRESS_ALLOW" -e UPSTREAM_PROXY="${EGRESS_UPSTREAM_PROXY:-}" \
  "$PY_IMAGE" python /app/egress_proxy.py >/dev/null
docker network connect --alias egress-proxy "$INT" "$PROXY"

GATEWAY_ENGINE_KEY="$(openssl rand -hex 16)"
log "starting LLM gateway (model $LLM_MODEL_NAME, budget \$$BUDGET_USD)"
docker run -d --name "$GATEWAY" --network "$INT" --network-alias llm-gateway "${HARDEN[@]}" \
  --tmpfs /tmp:size=64m,uid=10001,gid=10001 --memory 256m --cpus 0.5 "${CA_ARGS[@]}" \
  -v "$PWD/infra/live/llm_gateway.py:/app/llm_gateway.py:ro" \
  -e UPSTREAM_LLM_BASE_URL="$LLM_BASE_URL" -e UPSTREAM_LLM_API_KEY="$LLM_API_KEY" \
  -e UPSTREAM_MODEL="$LLM_MODEL_NAME" -e GATEWAY_ENGINE_KEY="$GATEWAY_ENGINE_KEY" \
  -e BUDGET_USD="$BUDGET_USD" -e MAX_REQUESTS="$MAX_REQUESTS" \
  -e PRICE_IN_PER_MTOK="$PRICE_IN_PER_MTOK" -e PRICE_OUT_PER_MTOK="$PRICE_OUT_PER_MTOK" \
  -e HTTPS_PROXY=http://egress-proxy:3128 -e https_proxy=http://egress-proxy:3128 \
  "$PY_IMAGE" python /app/llm_gateway.py >/dev/null

log "starting engine"
docker run -d --name "$ENGINE" --network "$INT" "${HARDEN[@]}" \
  --tmpfs /opt/mirofish/backend/uploads:size=2g,uid=10001,gid=10001,mode=0700 \
  --tmpfs /opt/mirofish/backend/logs:size=256m,uid=10001,gid=10001,mode=0700 \
  --tmpfs /tmp:size=1g,uid=10001,gid=10001 \
  --memory 6g --cpus 2 "${CA_ARGS[@]}" \
  -e LLM_BASE_URL=http://llm-gateway:4000/v1 -e LLM_API_KEY="$GATEWAY_ENGINE_KEY" \
  -e LLM_MODEL_NAME="$LLM_MODEL_NAME" -e ZEP_API_KEY="$ZEP_API_KEY" \
  -e HTTPS_PROXY=http://egress-proxy:3128 -e HTTP_PROXY=http://egress-proxy:3128 \
  -e https_proxy=http://egress-proxy:3128 -e http_proxy=http://egress-proxy:3128 \
  -e NO_PROXY=llm-gateway,localhost,127.0.0.1 -e no_proxy=llm-gateway,localhost,127.0.0.1 \
  -e OASIS_DB_PATH=/tmp/oasis-followers.db \
  -e HF_HUB_OFFLINE=1 -e SECRET_KEY="$(openssl rand -hex 32)" -e FLASK_DEBUG=false \
  "$IMAGE" >/dev/null

ENGINE_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$INT\").IPAddress}}" "$ENGINE")"
URL="http://$ENGINE_IP:5001"
for _ in $(seq 1 120); do
  [ "$(docker inspect -f '{{.State.Running}}' "$ENGINE")" = true ] || {
    log "engine exited during startup"; docker logs --tail 40 "$ENGINE"; exit 1; }
  curl -sf --noproxy '*' -o /dev/null "$URL/health" && break
  sleep 1
done
curl -sf --noproxy '*' "$URL/health" >/dev/null || { log "engine not reachable at $URL"; exit 1; }
docker image inspect "$IMAGE" --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}' \
  > "$OUT/image.txt"

if [ "${STACK_ONLY:-0}" = 1 ]; then
  log "stack check: host -> engine $URL/health OK"
  probe() { docker exec "$ENGINE" python -c "
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=15); print('reachable')
except urllib.error.HTTPError as e: print('HTTP', e.code, '(see egress.jsonl: allow or deny)')
except Exception as e: print('blocked', type(e).__name__, str(e)[:120])" "$1"; }
  for u in https://api.getzep.com/healthz https://api.anthropic.com/ https://huggingface.co/ \
           https://pypi.org/ http://example.com/; do
    echo "engine -> $u: $(probe "$u")" | tee -a "$OUT/stack_check.txt"
  done
  echo "engine -> llm-gateway: $(probe http://llm-gateway:4000/health)" | tee -a "$OUT/stack_check.txt"
  echo "engine -> internet without proxy: $(docker exec -e HTTPS_PROXY= -e https_proxy= "$ENGINE" \
    python -c "import socket
try: socket.create_connection(('api.getzep.com',443),timeout=5); print('REACHABLE (bad)')
except Exception as e: print('blocked', type(e).__name__)")" | tee -a "$OUT/stack_check.txt"
  exit 0
fi

# ---- 3. Drive the run ---------------------------------------------------------------------
log "driving $AGENTS agents x $ROUNDS rounds (reddit) at $URL; output: $OUT"
rc=0
NO_PROXY="$ENGINE_IP,${NO_PROXY:-}" no_proxy="$ENGINE_IP,${no_proxy:-}" \
  uv run python infra/live/run_live.py --url "$URL" --out "$OUT" \
  --agents "$AGENTS" --rounds "$ROUNDS" || rc=$?

collect
log "egress hosts:"
GATEWAY_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$INT\").IPAddress}}" "$GATEWAY" 2>/dev/null || true)"
python3 - "$OUT/egress.jsonl" "$ENGINE_IP" "$GATEWAY_IP" <<'PY' | tee "$OUT/egress_summary.txt"
import collections, json, re, sys
names = {sys.argv[2]: "engine", sys.argv[3]: "llm-gateway"}
c = collections.Counter()
for line in open(sys.argv[1]):
    r = json.loads(line)
    m = re.search(r"'([0-9.]+)'", r.get("client", ""))
    who = names.get(m.group(1), m.group(1)) if m else "?"
    c[(r["event"], r.get("host") or r.get("target"), who)] += 1
for (ev, host, who), n in sorted(c.items()):
    print(f"  {ev:6} {host:24} from {who:12} x{n}")
PY
log "gateway: $(cat "$OUT/gateway_state.json" 2>/dev/null)"
log "done (driver exit $rc); results in $OUT"
exit "$rc"
