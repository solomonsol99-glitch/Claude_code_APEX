"""Export the RunArtifacts JSON schema: ``python -m engine_adapter.schema``."""

from __future__ import annotations

import json
from pathlib import Path

from engine_adapter.base import RunArtifacts

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "run_artifacts.v1.json"


def run_artifacts_schema() -> dict[str, object]:
    schema = RunArtifacts.model_json_schema()
    schema["$id"] = "https://schemas.coreset.example/mre/run_artifacts.v1.json"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def render() -> str:
    return json.dumps(run_artifacts_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    print(SCHEMA_PATH)


if __name__ == "__main__":
    main()
