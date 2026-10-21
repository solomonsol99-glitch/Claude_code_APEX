"""Allow/deny rules of the engine egress proxy (infra/egress-proxy, §3.7)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

PROXY = Path(__file__).resolve().parents[3] / "infra" / "egress-proxy" / "proxy.py"


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("EGRESS_ALLOW", "api.getzep.com, LLM.example.com")
    spec = importlib.util.spec_from_file_location("egress_proxy_under_test", PROXY)
    assert spec
    assert spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("method", "target", "allowed", "reason"),
    [
        ("CONNECT", "api.getzep.com:443", True, "allowed"),
        ("CONNECT", "llm.example.com:443", True, "allowed"),
        ("CONNECT", "API.GETZEP.COM:443", True, "allowed"),
        ("CONNECT", "api.openai.com:443", False, "host_not_allowed"),
        ("CONNECT", "evil.api.getzep.com:443", False, "host_not_allowed"),
        ("CONNECT", "api.getzep.com:80", False, "port_not_allowed"),
        ("CONNECT", "api.getzep.com", False, "bad_target"),
        ("CONNECT", "1.2.3.4:443", False, "host_not_allowed"),
        ("GET", "http://api.getzep.com/", False, "not_connect"),
    ],
)
def test_decide(proxy: ModuleType, method: str, target: str, allowed: bool, reason: str) -> None:
    ok, why, _, _ = proxy.decide(method, target)
    assert (ok, why) == (allowed, reason)
