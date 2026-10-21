#!/usr/bin/env bash
# Phase 0 live run (ADR 0001, approved budget): one end-to-end run of the pinned engine
# against a real LLM route and Zep Cloud, with the engine behind the deny-all egress proxy.
#
# Required env (never commit values):
#   ZEP_API_KEY     Zep Cloud key for the dev project
#   LLM_BASE_URL    OpenAI-compatible base URL, https (e.g. https://host/v1)
#   LLM_API_KEY     key for that route
#   LLM_MODEL_NAME  model id on that route
# Optional: LIVE_AGENTS (10), LIVE_ROUNDS (3), IMAGE, OUT, REGISTRY_PREFIX,
#   EXTRA_CA (CA file the engine must trust, for TLS-intercepting sandboxes),
#   UPSTREAM_PROXY (host:port the egress proxy chains through).
#
# Outputs in $OUT: egress.jsonl (every host the engine tried, allowed or denied: A4/A5),
# zep-graphs-{before,after}.json (A6), engine.log, pytest.txt, summary.txt.
set -euo pipefail
cd "$(dirname "$0")/../.."
IMAGE="${IMAGE:-coreset/mirofish-engine:dev}"
OUT="${OUT:-$(mktemp -d)}"
NET=mre-live-net
ZEP_HOST=api.getzep.com
export LIVE_AGENTS="${LIVE_AGENTS:-10}" LIVE_ROUNDS="${LIVE_ROUNDS:-3}"

for v in ZEP_API_KEY LLM_BASE_URL LLM_API_KEY LLM_MODEL_NAME; do
  [ -n "${!v:-}" ] || { echo "missing $v (see header of $0)"; exit 2; }
done
case "$LLM_BASE_URL" in https://*) ;; *) echo "LLM_BASE_URL must be https (egress proxy is CONNECT-only)"; exit 2;; esac
export ZEP_API_KEY LLM_BASE_URL LLM_API_KEY LLM_MODEL_NAME
LLM_HOST="$(python3 -c 'import sys,urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).hostname)' "$LLM_BASE_URL")"

zep() { curl -sS -m 30 -H "Authorization: Api-Key $ZEP_API_KEY" "https://$ZEP_HOST/api/v2/$1"; }

# --- preflight: fail before any spend if a credential is wrong ---------------------------
code="$(curl -sS -m 30 -o /dev/null -w '%{http_code}' -H "Authorization: Api-Key $ZEP_API_KEY" "https://$ZEP_HOST/api/v2/projects/info")"
[ "$code" = 200 ] || { echo "preflight: Zep key rejected (HTTP $code)"; exit 3; }
code="$(curl -sS -m 30 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $LLM_API_KEY" "${LLM_BASE_URL%/}/models")"
[ "$code" = 200 ] || { echo "preflight: LLM route rejected GET /models (HTTP $code)"; exit 3; }
zep "graph/list-all?pageSize=1000" > "$OUT/zep-graphs-before.json"

cleanup() {
  docker logs mre-live-engine > "$OUT/engine.log" 2>&1 || true
  docker cp mre-live-egress:/tmp/egress.jsonl "$OUT/egress.jsonl" >/dev/null 2>&1 || true
  docker rm -f mre-live-engine mre-live-egress >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker rm -f mre-live-engine mre-live-egress >/dev/null 2>&1 || true
docker network rm "$NET" >/dev/null 2>&1 || true

docker build -q -t coreset/egress-proxy:dev ${REGISTRY_PREFIX:+--build-arg REGISTRY_PREFIX=$REGISTRY_PREFIX} infra/egress-proxy >/dev/null
docker network create --internal "$NET" >/dev/null
docker run -d --name mre-live-egress --network bridge \
  --read-only --tmpfs /tmp:size=64m,uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges \
  -e EGRESS_ALLOW="$ZEP_HOST,$LLM_HOST" -e UPSTREAM_PROXY="${UPSTREAM_PROXY:-}" \
  coreset/egress-proxy:dev >/dev/null
docker network connect --alias egress-proxy "$NET" mre-live-egress

ca_args=()
if [ -n "${EXTRA_CA:-}" ]; then
  ca_args=(-v "$EXTRA_CA:/etc/mre/extra-ca.crt:ro" -e SSL_CERT_FILE=/etc/mre/extra-ca.crt -e REQUESTS_CA_BUNDLE=/etc/mre/extra-ca.crt)
fi
SECRET_KEY="$(openssl rand -hex 32)" docker run -d --name mre-live-engine --network "$NET" \
  --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
  --tmpfs /opt/mirofish/backend/uploads:size=2g,uid=10001,gid=10001,mode=0700 \
  --tmpfs /opt/mirofish/backend/logs:size=256m,uid=10001,gid=10001,mode=0700 \
  --tmpfs /tmp:size=256m,uid=10001,gid=10001 \
  --memory 4g --cpus 2 \
  -e HTTPS_PROXY=http://egress-proxy:3128 -e HTTP_PROXY=http://egress-proxy:3128 \
  -e https_proxy=http://egress-proxy:3128 -e http_proxy=http://egress-proxy:3128 \
  -e NO_PROXY=127.0.0.1,localhost -e no_proxy=127.0.0.1,localhost \
  -e LLM_BASE_URL -e LLM_API_KEY -e LLM_MODEL_NAME -e ZEP_API_KEY -e SECRET_KEY \
  -e FLASK_DEBUG=false "${ca_args[@]}" \
  "$IMAGE" >/dev/null

ENGINE_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" mre-live-engine)"
export MIROFISH_URL="http://$ENGINE_IP:5001" MIROFISH_LIVE_FULL=1
for _ in $(seq 1 120); do
  [ "$(docker inspect -f '{{.State.Running}}' mre-live-engine)" = true ] || { echo "engine exited"; exit 1; }
  curl -sS --noproxy '*' -m 2 -o /dev/null "$MIROFISH_URL/health" 2>/dev/null && break; sleep 1
done

started=$(date +%s)
set +e
NO_PROXY="$ENGINE_IP,${NO_PROXY:-}" uv run pytest -m live -s -v tests/contract/mirofish/test_mirofish_live.py 2>&1 | tee "$OUT/pytest.txt"
rc=${PIPESTATUS[0]}
set -e
elapsed=$(( $(date +%s) - started ))
zep "graph/list-all?pageSize=1000" > "$OUT/zep-graphs-after.json" || true
cleanup; trap - EXIT

{
  echo "image: $(docker image inspect "$IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  echo "agents/rounds: $LIVE_AGENTS/$LIVE_ROUNDS  model: $LLM_MODEL_NAME  pytest rc: $rc  wall: ${elapsed}s"
  echo "== egress decisions (host, decision, count)"
  python3 - "$OUT/egress.jsonl" <<'PY'
import collections, json, sys
c = collections.Counter()
for line in open(sys.argv[1]):
    e = json.loads(line)
    if e["decision"] != "start":
        c[(e.get("host"), e["decision"], e.get("reason", ""))] += 1
for (h, d, r), n in sorted(c.items()):
    print(f"  {h:40} {d:6} {r:18} {n}")
PY
  echo "== Zep graphs left behind by this run (A6; expect none)"
  python3 - "$OUT/zep-graphs-before.json" "$OUT/zep-graphs-after.json" <<'PY'
import json, sys
ids = lambda p: {g.get("graph_id") for g in (json.load(open(p)).get("graphs") or [])}
try:
    left = ids(sys.argv[2]) - ids(sys.argv[1])
    print("  " + (", ".join(sorted(map(str, left))) or "none"))
except Exception as exc:  # noqa: BLE001
    print(f"  could not compare: {exc}")
PY
} | tee "$OUT/summary.txt"
echo "saved: $OUT"
exit "$rc"
