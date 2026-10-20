ARG REGISTRY_PREFIX=
FROM ${REGISTRY_PREFIX}python:3.12-slim-bookworm
COPY server.py /app/server.py
USER 10001:10001
ENV FAKE_LLM_LOG=/tmp/requests.jsonl
EXPOSE 4000
CMD ["python", "/app/server.py"]
