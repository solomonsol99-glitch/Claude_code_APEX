ARG REGISTRY_PREFIX=
FROM ${REGISTRY_PREFIX}python:3.12-slim-bookworm
COPY proxy.py /app/proxy.py
USER 10001:10001
ENV EGRESS_LOG=/tmp/egress.jsonl
EXPOSE 3128
CMD ["python", "/app/proxy.py"]
