# Deny-all egress proxy for the engine zone (§3.7). Stdlib only, no packages installed.
ARG REGISTRY_PREFIX=
FROM ${REGISTRY_PREFIX}python:3.12-slim-bookworm
COPY proxy.py /app/proxy.py
USER 10001:10001
ENV EGRESS_PORT=3128 EGRESS_LOG=/tmp/egress.jsonl PYTHONUNBUFFERED=1
EXPOSE 3128
CMD ["python", "/app/proxy.py"]
