"""Tenant YAML validation (§6, §7.1 'Config')."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from mre_core.errors import TenantConfigError
from mre_core.tenant_config import (
    config_hash,
    find_inline_secrets,
    generate_json_schema,
    load_all_tenant_configs,
    load_tenant_config,
    parse_tenant_config,
    render_json_schema,
    write_json_schema,
)

REPO = Path(__file__).resolve().parents[3]
TENANTS = REPO / "config/tenants"
SCHEMA = REPO / "config/schema/tenant.schema.json"


@pytest.fixture
def base() -> dict[str, Any]:
    data = yaml.safe_load((TENANTS / "acme-outdoors.yaml").read_text())
    assert isinstance(data, dict)
    return data


def _set(data: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(data)
    node = out
    *parents, last = dotted.split(".")
    for p in parents:
        node = node[p]
    node[last] = value
    return out


def _del(data: dict[str, Any], dotted: str) -> dict[str, Any]:
    out = copy.deepcopy(data)
    node = out
    *parents, last = dotted.split(".")
    for p in parents:
        node = node[p]
    del node[last]
    return out


def test_committed_schema_matches_model() -> None:
    assert SCHEMA.read_text() == render_json_schema(), (
        "regenerate with: uv run python -m mre_core.tenant_config"
    )


def test_write_json_schema(tmp_path: Path) -> None:
    target = tmp_path / "s" / "tenant.schema.json"
    write_json_schema(target)
    assert json.loads(target.read_text()) == generate_json_schema()


def test_all_example_tenants_validate() -> None:
    loaded = load_all_tenant_configs(TENANTS, schema_path=SCHEMA)
    assert {"acme-outdoors", "coreset-internal"} <= set(loaded)
    for item in loaded.values():
        assert len(item.version_hash) == 64
        assert item.config.notifications.enabled is False


def test_hash_is_stable_and_sensitive(base: dict[str, Any]) -> None:
    a = parse_tenant_config(base)
    b = parse_tenant_config(copy.deepcopy(base))
    c = parse_tenant_config(_set(base, "simulation.max_rounds", 20))
    assert a.version_hash == b.version_hash == config_hash(a.config)
    assert a.version_hash != c.version_hash


def test_defaults_for_optional_sections(base: dict[str, Any]) -> None:
    cfg = parse_tenant_config(_del(_del(base, "notifications"), "features")).config
    assert cfg.notifications.enabled is False
    assert cfg.notifications.channels == []
    assert cfg.features.portal_persona_chat is False


BAD_CASES: list[tuple[str, Any, str]] = [
    ("personas.mode", "cohort", "cohort_entitlement"),
    ("tenant.contract.permitted_uses", ["credit_scoring"], "permitted_uses"),
    ("tenant.contract.permitted_uses", [], "permitted_uses"),
    ("tenant.contract.permitted_uses", ["market_research", "market_research"], "duplicates"),
    ("tenant.contract.forbidden_uses_acknowledged", False, "forbidden_uses_acknowledged"),
    ("simulation.ensemble.min_successful_runs", 6, "min_successful_runs"),
    ("personas.panel_size", {"target": 300, "min": 25, "max": 200, "tolerance": 0.1}, "min"),
    ("personas.panel_size", {"target": 100, "min": 25, "max": 900, "tolerance": 0.1}, "max"),
    ("limits.cost.per_job_usd_cap", 5000, "per_job_usd_cap"),
    ("limits.cost.require_admin_approval_above_usd", 151, "require_admin_approval"),
    ("memory.delete_after_run", False, "delete_after_run"),
    ("review.auto_rerun_budget_fraction", 0.9, "auto_rerun_budget_fraction"),
    ("calibration.min_outcomes", 2, "min_outcomes"),
    ("schema_version", 2, "schema_version"),
    ("tenant.id", "not-a-uuid", "id"),
    ("tenant.slug", "Bad Slug", "slug"),
    ("branding.colors.primary", "blue", "primary"),
    ("intake.scraping.allowed_domains", [], "allowed_domain"),
    ("intake.scraping.max_pages", 100, "max_pages"),
    ("intake.max_copy_variants", 9, "max_copy_variants"),
    ("auth.api_keys.ip_allowlist", ["10.0.0.0/33"], "CIDR"),
    ("auth.oidc.issuer", "http://insecure.example/", "issuer"),
    ("auth.oidc.require_mfa_for_admins", False, "require_mfa_for_admins"),
    ("simulation.engine", "other", "engine"),
    ("simulation.max_rounds", "24", "max_rounds"),
    ("tenant.unexpected", 1, "unexpected"),
]


@pytest.mark.parametrize(("path", "value", "needle"), BAD_CASES)
def test_invalid_configs_fail_clearly(
    base: dict[str, Any], path: str, value: Any, needle: str
) -> None:
    with pytest.raises(TenantConfigError, match=needle):
        parse_tenant_config(_set(base, path, value))


def test_missing_required_section(base: dict[str, Any]) -> None:
    with pytest.raises(TenantConfigError, match="limits"):
        parse_tenant_config(_del(base, "limits"))


def test_non_mapping_rejected() -> None:
    with pytest.raises(TenantConfigError, match="mapping"):
        parse_tenant_config(["a"])


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("memory.api_key_ref", "zep_live_1234567890abcdef"),
        ("auth.oidc.client_id_ref", "plain-client-id"),
        ("branding.footer_text", "sk-abcdefghijklmnopqrstuvwxyz"),
        ("branding.footer_text", "AKIAABCDEFGHIJKLMNOP"),
        ("branding.disclaimer_addendum", "-----BEGIN RSA PRIVATE KEY-----"),
    ],
)
def test_inline_secrets_rejected(base: dict[str, Any], path: str, value: str) -> None:
    with pytest.raises(TenantConfigError, match="inline secrets"):
        parse_tenant_config(_set(base, path, value))


def test_secret_key_names_and_notification_refs() -> None:
    assert find_inline_secrets({"zep": {"api_key": "abc"}}) == ["zep.api_key"]
    assert find_inline_secrets({"password": "vault://x/y#p"}) == []
    assert find_inline_secrets({"list": [{"token": "t"}]}) == ["list[0].token"]
    assert find_inline_secrets({"agreement_ref": "MSA-1"}) == []


def test_notification_channel_requires_vault_ref(base: dict[str, Any]) -> None:
    good = _set(
        base,
        "notifications",
        {"enabled": True, "channels": [{"type": "webhook", "url_ref": "vault://t/a#hook"}]},
    )
    assert parse_tenant_config(good).config.notifications.enabled
    bad = _set(
        base,
        "notifications",
        {"enabled": True, "channels": [{"type": "webhook", "url_ref": "https://hooks.example"}]},
    )
    with pytest.raises(TenantConfigError):
        parse_tenant_config(bad)


def test_load_file_errors(tmp_path: Path, base: dict[str, Any]) -> None:
    bad_yaml = tmp_path / "broken.yaml"
    bad_yaml.write_text("a: [unclosed")
    with pytest.raises(TenantConfigError, match="invalid YAML"):
        load_tenant_config(bad_yaml)

    wrong_name = tmp_path / "other-name.yaml"
    wrong_name.write_text(yaml.safe_dump(base))
    with pytest.raises(TenantConfigError, match="file name"):
        load_tenant_config(wrong_name)


def test_duplicate_tenant_ids(tmp_path: Path, base: dict[str, Any]) -> None:
    (tmp_path / "acme-outdoors.yaml").write_text(yaml.safe_dump(base))
    twin = _set(base, "tenant.slug", "acme-twin")
    (tmp_path / "acme-twin.yaml").write_text(yaml.safe_dump(twin))
    with pytest.raises(TenantConfigError, match="duplicate tenant id"):
        load_all_tenant_configs(tmp_path)
