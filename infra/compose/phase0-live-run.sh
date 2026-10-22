#!/usr/bin/env bash
# Phase 0 live run (blueprint §8.1, ADR 0001; budget approved by Peter 2026-10-21).
# One end-to-end run, 10 agents / 3 rounds, real LLM + Zep Cloud, engine behind a
# deny-all egress proxy. Spends money: run it on purpose, not from CI.
#
#   engine --(LLM)--> llm-compat shim --\
#   engine --(Zep)------------------------> egress proxy (allowlist) --> internet
# The engine and the shim sit on an internal network with no route out. The shim
# holds the real LLM key; the engine only gets a placeholder.
#
# Needs in the environment: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL_NAME, ZEP_API_KEY.
# Optional:
#   LIVE_AGENTS / LIVE_ROUNDS   default 10 / 3 (the approved size)
#   LIVE_TOKEN_BUDGET           total LLM tokens the shim allows (default 3000000)
#   COMPAT_DROP                 request fields the shim drops (default: temperature)
#   EGRESS_UPSTREAM             parent proxy the egress proxy tunnels through
#   EGRESS_UPSTREAM_BYPASS      hosts that skip that parent proxy
#   EXTRA_CA_BUNDLE             CA bundle the engine must trust (TLS-intercepting proxy)
#   REGISTRY_PREFIX             image mirror for the proxy's base image
#   OUT                         output directory (default: a new temp dir)
# Usage: infra/compose/phase0-live-run.sh [engine-image]
set -euo pipefail
cd "$(dirname "$0")/../.."
IMAGE="${1:-coreset/mirofish-engine:dev}"
AGENTS="${LIVE_AGENTS:-10}"
ROUNDS="${LIVE_ROUNDS:-3}"
NET=mre-live-net
OUT="${OUT:-$(mktemp -d)}"
mkdir -p "$OUT"
PROXY_IMAGE="${REGISTRY_PREFIX:-}python:3.12-slim-bookworm"

for v in LLM_BASE_URL LLM_API_KEY LLM_MODEL_NAME ZEP_API_KEY; do
  [ -n "${!v:-}" ] || { echo "missing $v in the environment" >&2; exit 2; }
done
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "no image $IMAGE; run make engine-image" >&2; exit 2; }

LLM_HOST="$(python3 -c 'import sys,urllib.parse as u;print(u.urlsplit(sys.argv[1]).hostname)' "$LLM_BASE_URL")"
ALLOW="${LLM_HOST},api.getzep.com"   # A5 candidate; the egress log confirms or refutes it

cleanup() {
  docker logs mre-live-engine >"$OUT/engine.log" 2>&1 || true
  docker rm -f mre-live-engine mre-live-llm mre-live-egress >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker rm -f mre-live-engine mre-live-llm mre-live-egress >/dev/null 2>&1 || true
docker network rm "$NET" >/dev/null 2>&1 || true

# Internal network: no default route. The engine's only way out is the egress proxy,
# which listens on the network's gateway address on the host side.
docker network create --internal "$NET" >/dev/null
GW="$(docker network inspect "$NET" -f '{{(index .IPAM.Config 0).Gateway}}')"

docker run -d --name mre-live-egress --network host \
  --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
  -v "$PWD/infra/compose/egress_proxy.py:/app/egress_proxy.py:ro" \
  -v "$OUT:/out" \
  -e EGRESS_LISTEN="$GW:3128" -e EGRESS_ALLOW="$ALLOW" -e EGRESS_LOG=/out/egress.jsonl \
  -e EGRESS_UPSTREAM="${EGRESS_UPSTREAM:-}" -e EGRESS_UPSTREAM_BYPASS="${EGRESS_UPSTREAM_BYPASS:-}" \
  "$PROXY_IMAGE" python /app/egress_proxy.py >/dev/null
chmod 0777 "$OUT"

CA_ARGS=()
if [ -n "${EXTRA_CA_BUNDLE:-}" ]; then
  install -m 0644 "$EXTRA_CA_BUNDLE" "$OUT/ca-bundle.crt"
  CA_ARGS=(-v "$OUT/ca-bundle.crt:/etc/mre/ca-bundle.crt:ro"
           -e SSL_CERT_FILE=/etc/mre/ca-bundle.crt -e REQUESTS_CA_BUNDLE=/etc/mre/ca-bundle.crt)
fi

docker run -d --name mre-live-llm --network "$NET" --network-alias llm-gateway \
  --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
  -v "$PWD/infra/compose/llm_compat.py:/app/llm_compat.py:ro" -v "$OUT:/out" \
  -e COMPAT_UPSTREAM="$LLM_BASE_URL" -e COMPAT_API_KEY="$LLM_API_KEY" \
  -e COMPAT_DROP="${COMPAT_DROP:-temperature}" -e COMPAT_TOKEN_BUDGET="${LIVE_TOKEN_BUDGET:-3000000}" \
  -e COMPAT_LOG=/out/llm.jsonl \
  -e HTTPS_PROXY="http://$GW:3128" -e https_proxy="http://$GW:3128" \
  "${CA_ARGS[@]}" "$PROXY_IMAGE" python /app/llm_compat.py >/dev/null

docker run -d --name mre-live-engine --network "$NET" \
  --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
  --tmpfs /opt/mirofish/backend/uploads:size=2g,uid=10001,gid=10001,mode=0700 \
  --tmpfs /opt/mirofish/backend/logs:size=256m,uid=10001,gid=10001,mode=0700 \
  --tmpfs /tmp:size=256m,uid=10001,gid=10001 \
  --memory 4g --cpus 2 \
  -e LLM_BASE_URL=http://llm-gateway:4000/v1 -e LLM_API_KEY=held-by-llm-compat-shim \
  -e LLM_MODEL_NAME -e ZEP_API_KEY \
  -e SECRET_KEY="$(openssl rand -hex 32)" -e FLASK_DEBUG=false \
  -e HTTPS_PROXY="http://$GW:3128" -e https_proxy="http://$GW:3128" \
  -e NO_PROXY=localhost,127.0.0.1,llm-gateway -e no_proxy=localhost,127.0.0.1,llm-gateway \
  "${CA_ARGS[@]}" "$IMAGE" >/dev/null

ENGINE_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" mre-live-engine)"
URL="http://$ENGINE_IP:5001"
for _ in $(seq 1 120); do
  [ "$(docker inspect -f '{{.State.Running}}' mre-live-engine)" = "true" ] || {
    echo "engine exited during startup:"; docker logs --tail 40 mre-live-engine; exit 1; }
  curl -fsS -m 3 --noproxy '*' "$URL/health" >/dev/null 2>&1 && break
  sleep 1
done

echo "live run: $AGENTS agents / $ROUNDS rounds, model $LLM_MODEL_NAME, egress allow=$ALLOW, out=$OUT"
set +e
MIROFISH_URL="$URL" MIROFISH_LIVE_FULL=1 MIROFISH_LIVE_AGENTS="$AGENTS" MIROFISH_LIVE_ROUNDS="$ROUNDS" \
MIROFISH_LIVE_OUT="$OUT" NO_PROXY="$ENGINE_IP,${NO_PROXY:-}" no_proxy="$ENGINE_IP,${no_proxy:-}" \
  uv run pytest -m live tests/contract/mirofish/test_mirofish_live.py -v -s -p no:cacheprovider \
  2>&1 | tee "$OUT/pytest.txt"
rc=${PIPESTATUS[0]}
set -e

{
  echo "== egress summary (host, event, count)"
  python3 - "$OUT/egress.jsonl" <<'PY'
import collections, json, sys
c = collections.Counter()
for line in open(sys.argv[1]):
    e = json.loads(line)
    if e["event"] != "start":
        c[(e.get("host") or e.get("target", "?"), e["event"], e.get("reason", ""))] += 1
for (host, ev, why), n in sorted(c.items()):
    print(f"{host:40} {ev:6} {why:16} {n}")
PY
  echo "== LLM usage (via shim)"
  python3 - "$OUT/llm.jsonl" <<'PY'
import collections, json, sys
calls = [json.loads(x) for x in open(sys.argv[1])]
calls = [c for c in calls if c["event"] == "call"]
by_status = collections.Counter(c["status"] for c in calls)
print("calls:", len(calls), "by status:", dict(by_status))
print("prompt tokens:", sum(c["prompt_tokens"] for c in calls),
      "completion tokens:", sum(c["completion_tokens"] for c in calls))
errors = collections.Counter(c["error"] for c in calls if c.get("error"))
for msg, n in errors.most_common(5):
    print(f"  {n}x {msg}")
PY
} | tee "$OUT/egress-summary.txt"
echo "saved: $OUT"
exit "$rc"
