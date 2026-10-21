#!/usr/bin/env bash
# Phase 0 live run (ADR 0001, approved budget): one end-to-end engine run with a real LLM
# and Zep Cloud, behind a deny-all egress proxy. SPENDS MONEY on the LLM and Zep.
#
# Needs in the environment (never printed, passed to the engine by name only):
#   LLM_BASE_URL, LLM_API_KEY, LLM_MODEL_NAME, ZEP_API_KEY
# Optional:
#   IMAGE               engine image (default coreset/mirofish-engine:dev)
#   EGRESS_ALLOW        extra hosts to allow, comma-separated (LLM host + api.getzep.com
#                       are always allowed; A5 candidate)
#   UPSTREAM_PROXY      host:port of a proxy the egress proxy must chain through
#   LOOPBACK_PROXY      host:port of a proxy that is only on this host's loopback (e.g. a
#                       sandbox agent proxy); relayed so the egress proxy can use it
#   EXTRA_CA            CA bundle the engine must trust (TLS-intercepting upstream proxy)
#   MIROFISH_LIVE_AGENTS / MIROFISH_LIVE_ROUNDS   default 10 / 3
#   LIVE_DRY=1          start everything and run the free checks, then stop (no spend)
#   OUT                 output dir (default .tmp/live-run-<timestamp>)
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${LLM_BASE_URL:?set LLM_BASE_URL}" "${LLM_API_KEY:?set LLM_API_KEY}"
: "${LLM_MODEL_NAME:?set LLM_MODEL_NAME}" "${ZEP_API_KEY:?set ZEP_API_KEY}"
IMAGE="${IMAGE:-coreset/mirofish-engine:dev}"
OUT="${OUT:-.tmp/live-run-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
NET=mre-live-net
OUTNET=mre-live-out
LLM_HOST="$(python3 -c 'import os,urllib.parse as u;print(u.urlparse(os.environ["LLM_BASE_URL"]).hostname)')"
ALLOW="${LLM_HOST},api.getzep.com${EGRESS_ALLOW:+,$EGRESS_ALLOW}"
RELAY_PID=
# Sandbox convenience: if this host's own HTTPS proxy is on loopback, chain through it.
if [ -z "${LOOPBACK_PROXY:-}${UPSTREAM_PROXY:-}" ]; then
  case "${HTTPS_PROXY:-}" in
    http://127.0.0.1:*|http://localhost:*)
      LOOPBACK_PROXY="${HTTPS_PROXY#http://}"; LOOPBACK_PROXY="${LOOPBACK_PROXY%/}"
      EXTRA_CA="${EXTRA_CA:-${SSL_CERT_FILE:-}}" ;;
  esac
fi

cleanup() {
  [ -n "$RELAY_PID" ] && kill "$RELAY_PID" 2>/dev/null || true
  docker rm -f mre-live-engine mre-live-egress >/dev/null 2>&1 || true
  docker network rm "$NET" "$OUTNET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

docker image inspect "$IMAGE" >/dev/null || { echo "missing $IMAGE: run make engine-image"; exit 1; }
docker build -q -t coreset/egress-proxy:dev ${REGISTRY_PREFIX:+--build-arg REGISTRY_PREFIX=$REGISTRY_PREFIX} \
  infra/compose/egress-proxy >/dev/null
docker network create --internal "$NET" >/dev/null
docker network create "$OUTNET" >/dev/null

if [ -n "${LOOPBACK_PROXY:-}" ]; then
  # Relay <OUTNET gateway>:3129 -> LOOPBACK_PROXY so the egress container can chain to it.
  GW="$(docker network inspect -f '{{(index .IPAM.Config 0).Gateway}}' "$OUTNET")"
  python3 - "$GW" "$LOOPBACK_PROXY" <<'PY' &
import asyncio, sys
gw, target = sys.argv[1], sys.argv[2]
th, _, tp = target.rpartition(":")
async def pipe(r, w):
    try:
        while d := await r.read(65536):
            w.write(d); await w.drain()
    except ConnectionError:
        pass
    finally:
        w.close()
async def handle(cr, cw):
    ur, uw = await asyncio.open_connection(th, int(tp))
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
async def main():
    async with await asyncio.start_server(handle, gw, 3129) as s:
        await s.serve_forever()
asyncio.run(main())
PY
  RELAY_PID=$!
  UPSTREAM_PROXY="$GW:3129"
fi

docker run -d --name mre-live-egress --network "$OUTNET" \
  --read-only --tmpfs /tmp:size=64m,uid=10001,gid=10001 --cap-drop ALL \
  --security-opt no-new-privileges \
  -e EGRESS_ALLOW="$ALLOW" -e UPSTREAM_PROXY="${UPSTREAM_PROXY:-}" \
  coreset/egress-proxy:dev >/dev/null
docker network connect --alias egress-proxy "$NET" mre-live-egress

CA_ARGS=()
if [ -n "${EXTRA_CA:-}" ]; then
  CA_ARGS=(-v "$EXTRA_CA:/etc/mre/extra-ca.crt:ro"
           -e SSL_CERT_FILE=/etc/mre/extra-ca.crt -e REQUESTS_CA_BUNDLE=/etc/mre/extra-ca.crt)
fi
docker run -d --name mre-live-engine --network "$NET" \
  --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
  --tmpfs /opt/mirofish/backend/uploads:size=2g,uid=10001,gid=10001,mode=0700 \
  --tmpfs /opt/mirofish/backend/logs:size=256m,uid=10001,gid=10001,mode=0700 \
  --tmpfs /tmp:size=256m,uid=10001,gid=10001 \
  --memory 4g --cpus 2 \
  -e LLM_BASE_URL -e LLM_API_KEY -e LLM_MODEL_NAME -e ZEP_API_KEY \
  -e SECRET_KEY="$(openssl rand -hex 32)" -e FLASK_DEBUG=false \
  -e HTTPS_PROXY=http://egress-proxy:3128 -e HTTP_PROXY=http://egress-proxy:3128 \
  -e https_proxy=http://egress-proxy:3128 -e http_proxy=http://egress-proxy:3128 \
  -e NO_PROXY=127.0.0.1,localhost -e no_proxy=127.0.0.1,localhost \
  "${CA_ARGS[@]}" "$IMAGE" >/dev/null

ENGINE_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" mre-live-engine)"
MIROFISH_URL="http://$ENGINE_IP:5001"
for _ in $(seq 1 120); do
  if [ "$(docker inspect -f '{{.State.Running}}' mre-live-engine)" != "true" ]; then
    echo "engine exited during startup:"; docker logs --tail 40 mre-live-engine; exit 1
  fi
  curl -sf --noproxy '*' "$MIROFISH_URL/health" >/dev/null && break
  sleep 1
done
curl -sf --noproxy '*' "$MIROFISH_URL/health" >/dev/null || { echo "engine not healthy"; exit 1; }

# Deny-all check: a host outside the allowlist must be refused before we spend anything.
docker exec mre-live-engine python -c "
import urllib.request, sys
try:
    urllib.request.urlopen('https://example.com', timeout=10); sys.exit('egress NOT denied')
except Exception as e:
    print('deny-all check ok:', type(e).__name__)"

# Free preflight through the egress proxy: both credentials must work before we spend.
# Prints status codes only, never key material.
docker exec -i mre-live-engine python - <<'PY'
import os, sys, urllib.error, urllib.request
base = os.environ["LLM_BASE_URL"].rstrip("/")
llm_key, zep_key = os.environ["LLM_API_KEY"], os.environ["ZEP_API_KEY"]
checks = {
    "llm  GET /models": (base + "/models", {
        "Authorization": f"Bearer {llm_key}", "x-api-key": llm_key,
        "anthropic-version": "2023-06-01"}),
    "zep  GET /graph/list-all": ("https://api.getzep.com/api/v2/graph/list-all?pageSize=1",
        {"Authorization": f"Api-Key {zep_key}"}),
}
bad = []
for name, (url, headers) in checks.items():
    try:
        code = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20).status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        code = type(e).__name__
    print(f"preflight {name}: {code}")
    if code != 200:
        bad.append(name)
if bad:
    sys.exit(f"preflight failed ({', '.join(bad)}); nothing was spent")
PY
if [ "${LIVE_DRY:-0}" = "1" ]; then echo "LIVE_DRY=1: stopping before the paid run"; exit 0; fi

echo "live run: agents=${MIROFISH_LIVE_AGENTS:-10} rounds=${MIROFISH_LIVE_ROUNDS:-3} model=$LLM_MODEL_NAME allow=$ALLOW"
set +e
MIROFISH_URL="$MIROFISH_URL" MIROFISH_LIVE_FULL=1 MIROFISH_LIVE_OUT="$OUT" \
  NO_PROXY="$ENGINE_IP,${NO_PROXY:-}" no_proxy="$ENGINE_IP,${no_proxy:-}" \
  ${UV:-uv} run pytest -m live -p no:cacheprovider -v tests/contract/mirofish/test_mirofish_live.py \
  2>&1 | tee "$OUT/pytest.txt"
rc=${PIPESTATUS[0]}
set -e

redact() { sed -e "s|${LLM_API_KEY}|<LLM_API_KEY>|g" -e "s|${ZEP_API_KEY}|<ZEP_API_KEY>|g"; }
docker exec mre-live-egress cat /tmp/egress.jsonl 2>/dev/null | redact > "$OUT/egress.jsonl" || true
docker logs mre-live-engine 2>&1 | redact > "$OUT/engine.log" || true
python3 - "$OUT/egress.jsonl" <<'PY' | tee "$OUT/egress-summary.txt"
import collections, json, sys
c = collections.Counter()
for line in open(sys.argv[1]):
    e = json.loads(line)
    if e["event"] != "start":
        c[(e["event"], e.get("host") or e.get("target"))] += 1
print("egress (event, host): count")
for k, v in sorted(c.items()):
    print(f"  {k[0]:5} {k[1]}: {v}")
PY
echo "saved: $OUT (exit $rc)"
exit "$rc"
