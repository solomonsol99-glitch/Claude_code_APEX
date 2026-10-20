"""License boundary (§3.12): Coreset code never imports, links or vendors MiroFish.

MiroFish is AGPL-3.0 and is reached only over HTTP through engine_adapter.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = [ROOT / "packages", ROOT / "apps", ROOT / "tests"]
ENGINE_ZONE = ROOT / "engines" / "mirofish"
THIS_FILE = Path(__file__).resolve()

# Top-level package names used inside the MiroFish backend.
FORBIDDEN_MODULES = {"app", "backend", "mirofish", "MiroFish"}

# File names that only exist in the upstream backend.
UPSTREAM_FILENAMES = {
    "simulation_runner.py",
    "simulation_manager.py",
    "oasis_profile_generator.py",
    "simulation_config_generator.py",
    "report_agent.py",
    "zep_tools.py",
    "zep_entity_reader.py",
    "graph_builder.py",
    "ontology_generator.py",
    "run_parallel_simulation.py",
    "run_twitter_simulation.py",
    "run_reddit_simulation.py",
}

# Strings that indicate copied upstream source rather than our own code.
UPSTREAM_MARKERS = [
    "mirofish-secret-key",
    "class SimulationRunner",
    "class OasisProfileGenerator",
    "class ReportAgent",
    "def optimize_interview_prompt",
    "from app.services",
    "from app.models",
    "from ..models.task",
]
CJK = re.compile(r"[一-鿿]")  # upstream comments and prompts are largely Chinese


def _python_files() -> list[Path]:
    files: list[Path] = []
    for base in SCANNED:
        files.extend(
            p for p in base.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts
        )
    return files


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in {"import_module", "__import__"} and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    roots.add(arg.value.split(".")[0])
    return roots


def test_scan_finds_files() -> None:
    assert len(_python_files()) > 10


def test_no_engine_imports() -> None:
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = _imported_roots(tree) & FORBIDDEN_MODULES
        if bad:
            offenders.append(f"{path.relative_to(ROOT)}: imports {sorted(bad)}")
    assert not offenders, "MiroFish must be reached over HTTP only:\n" + "\n".join(offenders)


def test_no_engine_path_hacks() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for p in _python_files()
        if p != THIS_FILE
        and re.search(r"engines[/\\.]mirofish", p.read_text(encoding="utf-8"))
        and "sys.path" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


def test_no_vendored_upstream_files() -> None:
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ENGINE_ZONE in path.parents:
            continue
        if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in path.parts):
            continue
        if path.name in UPSTREAM_FILENAMES:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, "upstream MiroFish files outside engines/mirofish:\n" + "\n".join(
        offenders
    )


def test_no_upstream_source_markers() -> None:
    offenders = []
    for path in _python_files():
        if path == THIS_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        hits = [m for m in UPSTREAM_MARKERS if m in text]
        if CJK.search(text):
            hits.append("CJK text")
        if hits:
            offenders.append(f"{path.relative_to(ROOT)}: {hits}")
    assert not offenders, "possible copied MiroFish source:\n" + "\n".join(offenders)


def test_detector_catches_violations(tmp_path: Path) -> None:
    sample = "import app.services.simulation_runner\nfrom mirofish import x\n"
    tree = ast.parse(sample)
    assert _imported_roots(tree) & FORBIDDEN_MODULES == {"app", "mirofish"}
    dyn = ast.parse("import importlib\nimportlib.import_module('backend.run')\n")
    assert "backend" in _imported_roots(dyn)
