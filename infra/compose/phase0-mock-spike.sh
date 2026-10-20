#!/usr/bin/env bash
# Phase 0 mock spike (blueprint §8.1, step 0): drive the pinned engine with a fake LLM
# and a placeholder Zep key. No spend. Records what works before Zep is needed.
# Usage: infra/compose/phase0-mock-spike.sh [image]
set -euo pipefail
IMAGE="${1:-coreset/mirofish-engine:dev}"
NET=mre-spike-net
OUT="${OUT:-$(mktemp -d)}"
cleanup() { docker rm -f mre-spike-engine mre-spike-llm >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
docker network create --internal "$NET" >/dev/null
docker build -q -t coreset/fake-llm:dev ${REGISTRY_PREFIX:+--build-arg REGISTRY_PREFIX=$REGISTRY_PREFIX} tests/fakes/fake_llm >/dev/null
docker run -d --name mre-spike-llm --network "$NET" --network-alias llm-gateway \
  --read-only --tmpfs /tmp:size=64m,uid=10001,gid=10001 --cap-drop ALL \
  --security-opt no-new-privileges coreset/fake-llm:dev >/dev/null
docker run -d --name mre-spike-engine --network "$NET" \
  --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
  --tmpfs /opt/mirofish/backend/uploads:size=2g,uid=10001,gid=10001,mode=0700 \
  --tmpfs /tmp:size=256m,uid=10001,gid=10001 \
  --memory 4g --cpus 2 \
  -e LLM_BASE_URL=http://llm-gateway:4000/v1 -e LLM_API_KEY=fake -e LLM_MODEL_NAME=fake-model \
  -e ZEP_API_KEY=placeholder-not-a-key -e SECRET_KEY="$(openssl rand -hex 32)" -e FLASK_DEBUG=false \
  "$IMAGE" >/dev/null

# All requests are made from inside the internal network (a throwaway curl-capable container = the engine itself).
req() { docker exec mre-spike-engine python - "$@" <<'PY'
import json, sys, urllib.request
method, path = sys.argv[1], sys.argv[2]
data = sys.argv[3].encode() if len(sys.argv) > 3 else None
r = urllib.request.Request("http://127.0.0.1:5001" + path, data=data, method=method,
                           headers={"Content-Type": "application/json"} if data else {})
try:
    with urllib.request.urlopen(r, timeout=60) as resp:
        print(resp.status, resp.read().decode()[:2000])
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:2000])
PY
}
for i in $(seq 1 60); do
  if req GET /health 2>/dev/null | grep -q '^200'; then break; fi; sleep 1
done
{
  echo "== image"; docker image inspect "$IMAGE" --format '{{.Id}} {{.Config.User}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
  echo "== whoami / rootfs"; docker exec mre-spike-engine sh -c 'id; touch /opt/mirofish/x 2>&1 | head -1'
  echo "== GET /health"; req GET /health
  echo "== GET /api/simulation/list"; req GET /api/simulation/list
  echo "== ontology/generate (multipart, fake LLM)"
  docker exec mre-spike-engine python - <<'PY'
import json, urllib.request, uuid
b = uuid.uuid4().hex
seed = b"# Seed\nA new product is being introduced. Persona dossier: Dana (Consumer), Lee (Consumer).\n"
parts = []
def field(n, v): parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'.encode())
field("simulation_requirement", "Simulate how the consumers react to this product.")
field("project_name", "spike-run-1")
parts.append(f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="seed.md"\r\nContent-Type: text/markdown\r\n\r\n'.encode() + seed + b"\r\n")
parts.append(f"--{b}--\r\n".encode())
r = urllib.request.Request("http://127.0.0.1:5001/api/graph/ontology/generate", data=b"".join(parts),
                           method="POST", headers={"Content-Type": f"multipart/form-data; boundary={b}"})
try:
    with urllib.request.urlopen(r, timeout=120) as resp: body = resp.read().decode(); print(resp.status, body[:1500])
except urllib.error.HTTPError as e: body = e.read().decode(); print(e.code, body[:1500])
PY
  echo "== fake LLM request log"; docker exec mre-spike-llm sh -c 'wc -l < /tmp/requests.jsonl; head -3 /tmp/requests.jsonl' 2>&1
  echo "== engine log tail"; docker logs --tail 40 mre-spike-engine 2>&1
} | tee "$OUT/spike.txt"
echo "saved: $OUT/spike.txt"
