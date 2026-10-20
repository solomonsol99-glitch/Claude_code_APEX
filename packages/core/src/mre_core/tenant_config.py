"""Per-tenant YAML configuration (§6): models, secret checks, loading and hashing."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

import jsonschema
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mre_core.errors import TenantConfigError

SCHEMA_VERSION = 1
SCHEMA_ID = "https://coreset.example/mre/schema/tenant.schema.json"  # placeholder domain

VaultRef = Annotated[str, Field(pattern=r"^vault://[A-Za-z0-9_\-./]+(#[A-Za-z0-9_\-.]+)?$")]
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
Domain = Annotated[
    str,
    Field(max_length=253, pattern=r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"),
]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
PosInt = Annotated[int, Field(ge=1)]
NonNegInt = Annotated[int, Field(ge=0)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TenantStatus(StrEnum):
    active = "active"
    suspended = "suspended"
    offboarding = "offboarding"


class PermittedUse(StrEnum):
    market_research = "market_research"
    messaging_pretest = "messaging_pretest"
    pricing_research = "pricing_research"


class TenantRole(StrEnum):
    tenant_viewer = "tenant_viewer"
    tenant_member = "tenant_member"
    tenant_admin = "tenant_admin"


class Contract(_Model):
    permitted_uses: list[PermittedUse] = Field(min_length=1)
    # Must be explicitly true: credit/insurance/employment/housing/eligibility are forbidden.
    forbidden_uses_acknowledged: Literal[True]
    agreement_ref: str = Field(min_length=1, max_length=100)

    @field_validator("permitted_uses")
    @classmethod
    def _unique(cls, v: list[PermittedUse]) -> list[PermittedUse]:
        if len(set(v)) != len(v):
            raise ValueError("permitted_uses contains duplicates")
        return v


class Contacts(_Model):
    admin_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TenantInfo(_Model):
    id: UUID
    slug: Slug
    display_name: str = Field(min_length=1, max_length=200)
    status: TenantStatus
    contract: Contract
    data_region: Literal["us", "eu", "uk", "ca", "au"]
    contacts: Contacts


class OIDC(_Model):
    issuer: str = Field(pattern=r"^https://")
    client_id_ref: VaultRef
    allowed_email_domains: list[Domain] = Field(default_factory=list)
    require_mfa_for_admins: Literal[True]


class ApiKeys(_Model):
    enabled: bool
    max_active_keys: int = Field(ge=0, le=50)
    default_ttl_days: int = Field(ge=1, le=365)
    ip_allowlist: list[str] = Field(default_factory=list)

    @field_validator("ip_allowlist")
    @classmethod
    def _cidrs(cls, v: list[str]) -> list[str]:
        for cidr in v:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid CIDR {cidr!r}") from exc
        return v


class Auth(_Model):
    oidc: OIDC
    api_keys: ApiKeys
    roles_default: TenantRole


class Colors(_Model):
    primary: HexColor
    secondary: HexColor
    accent: HexColor
    text: HexColor


class Fonts(_Model):
    heading: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=100)


class Branding(_Model):
    white_label: bool
    brand_name: str = Field(min_length=1, max_length=100)
    show_powered_by_coreset: bool
    logo_uri: str = Field(pattern=r"^s3://")
    colors: Colors
    fonts: Fonts
    report_title: str = Field(min_length=1, max_length=120)
    footer_text: str = Field(max_length=200)
    # Appended to the standard disclaimer; it can never replace it (§5.7).
    disclaimer_addendum: str = Field(default="", max_length=4000)
    locale: str = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")
    formats: list[Literal["docx", "pdf"]] = Field(min_length=1)
    pdf_archival: bool


class Scraping(_Model):
    enabled: bool
    allowed_domains: list[Domain] = Field(default_factory=list)
    exclude_paths: list[Annotated[str, Field(pattern=r"^/")]] = Field(default_factory=list)
    max_pages: int = Field(ge=1, le=25)
    use_headless_browser: bool

    @model_validator(mode="after")
    def _domains_when_enabled(self) -> Scraping:
        if self.enabled and not self.allowed_domains:
            raise ValueError("scraping.enabled requires at least one allowed_domain")
        return self


class Intake(_Model):
    allowed_categories: list[Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]] = Field(min_length=1)
    max_copy_variants: int = Field(ge=1, le=5)
    max_alt_price_points: int = Field(ge=0, le=5)
    max_upload_mb: int = Field(ge=0, le=20)
    scraping: Scraping


class Market(_Model):
    country: str = Field(pattern=r"^[A-Z]{2}$")
    regions: list[str] = Field(default_factory=list)
    age_bands: list[str] = Field(default_factory=list)
    income_bands: list[str] = Field(default_factory=list)


class Segments(_Model):
    count: int = Field(ge=3, le=12)
    min_cards_per_segment: int = Field(ge=1, le=50)


class PanelSize(_Model):
    target: int = Field(ge=20, le=500)
    min: int = Field(ge=20, le=500)
    max: int = Field(ge=20, le=500)
    tolerance: float = Field(gt=0, le=0.25)

    @model_validator(mode="after")
    def _ordered(self) -> PanelSize:
        if not self.min <= self.target <= self.max:
            raise ValueError("panel_size requires min <= target <= max")
        return self


class Personas(_Model):
    mode: Literal["synthetic", "cohort"]
    cohort_entitlement: bool
    default_market: Market
    segments: Segments
    panel_size: PanelSize
    marginal_tolerance_pp: float = Field(gt=0, le=10)

    @model_validator(mode="after")
    def _cohort_needs_entitlement(self) -> Personas:
        if self.mode == "cohort" and not self.cohort_entitlement:
            raise ValueError("personas.mode 'cohort' requires cohort_entitlement: true")
        return self


class Ensemble(_Model):
    runs_per_arm: int = Field(ge=1, le=20)
    min_successful_runs: int = Field(ge=1, le=20)
    model_families: list[str] = Field(min_length=1)
    random_seed_base: NonNegInt

    @model_validator(mode="after")
    def _min_le_runs(self) -> Ensemble:
        if self.min_successful_runs > self.runs_per_arm:
            raise ValueError("ensemble.min_successful_runs must be <= runs_per_arm")
        return self


class Baseline(_Model):
    enabled: bool
    route: str = Field(min_length=1)


class Interviews(_Model):
    price_probes: bool
    van_westendorp: bool
    custom_questions_max: int = Field(ge=0, le=5)


class Simulation(_Model):
    engine: Literal["mirofish", "baseline", "mock"]
    engine_version: str = Field(min_length=1)
    platforms: list[Literal["twitter", "reddit"]] = Field(min_length=1)
    max_rounds: int = Field(ge=1, le=200)
    enable_graph_memory_update: bool
    ensemble: Ensemble
    baseline: Baseline
    interviews: Interviews


class LLMRoutes(_Model):
    simulation: list[str] = Field(min_length=1)
    analysis: list[str] = Field(min_length=1)


class LLM(_Model):
    policy: Literal["third_party_allowed", "self_hosted_only"]
    routes: LLMRoutes
    provider_allowlist: list[str] = Field(default_factory=list)
    store_prompt_text: bool


class Memory(_Model):
    backend: Literal["zep_cloud"]
    api_key_ref: VaultRef
    # Not configurable (§3.10): graphs are always deleted at end of run.
    delete_after_run: Literal[True]


class RateLimits(_Model):
    reads_per_min: PosInt
    writes_per_min: PosInt
    jobs_per_hour: PosInt


class Concurrency(_Model):
    max_running_jobs: PosInt
    max_concurrent_runs: PosInt


class Cost(_Model):
    per_job_usd_cap: float = Field(gt=0)
    monthly_usd_cap: float = Field(gt=0)
    require_admin_approval_above_usd: float = Field(ge=0)
    zep_monthly_ops_cap: NonNegInt

    @model_validator(mode="after")
    def _ordered(self) -> Cost:
        if self.per_job_usd_cap > self.monthly_usd_cap:
            raise ValueError("cost.per_job_usd_cap must be <= monthly_usd_cap")
        if self.require_admin_approval_above_usd > self.per_job_usd_cap:
            raise ValueError("cost.require_admin_approval_above_usd must be <= per_job_usd_cap")
        return self


class Limits(_Model):
    rate: RateLimits
    concurrency: Concurrency
    cost: Cost


class Review(_Model):
    require_analyst: bool
    # §4.6: never more than ceil(0.5 x planned_runs) extra runs.
    auto_rerun_budget_fraction: float = Field(ge=0, le=0.5)


class Calibration(_Model):
    enabled: bool
    min_outcomes: int = Field(ge=5, le=1000)
    share_track_record_in_reports: bool
    category_pooling: bool


class RetentionDays(_Model):
    raw_scrape: NonNegInt | None
    run_artifacts: NonNegInt | None
    reports: NonNegInt | None
    intake: NonNegInt | None
    outcomes: NonNegInt | None


class NotificationChannel(_Model):
    type: Literal["webhook", "email", "slack"]
    url_ref: VaultRef


class Notifications(_Model):
    enabled: bool = False
    channels: list[NotificationChannel] = Field(default_factory=list)


class Features(_Model):
    portal_persona_chat: bool = False
    export_csv: bool = True


class TenantConfig(_Model):
    """Root of ``config/tenants/<slug>.yaml``."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        json_schema_extra={"$id": SCHEMA_ID, "title": "Coreset MRE tenant configuration"},
    )

    schema_version: Literal[1]
    tenant: TenantInfo
    auth: Auth
    branding: Branding
    intake: Intake
    personas: Personas
    simulation: Simulation
    llm: LLM
    memory: Memory
    limits: Limits
    review: Review
    calibration: Calibration
    retention_days: RetentionDays
    notifications: Notifications = Field(default_factory=Notifications)
    features: Features = Field(default_factory=Features)


class LoadedTenantConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    config: TenantConfig
    version_hash: str
    source: str


# --- schema ---------------------------------------------------------------


def generate_json_schema() -> dict[str, Any]:
    schema = TenantConfig.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def render_json_schema() -> str:
    return json.dumps(generate_json_schema(), indent=2, sort_keys=True) + "\n"


def write_json_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_json_schema(), encoding="utf-8")


# --- inline-secret guard --------------------------------------------------

_SECRET_KEY_RE = re.compile(
    r"(secret|password|passwd|token|api_?key|private_?key|credential)", re.IGNORECASE
)
_NON_SECRET_REFS = frozenset({"agreement_ref"})
_SECRET_VALUE_RES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bmre_live_[A-Za-z0-9]+_[A-Za-z0-9]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),  # JWT
]


def find_inline_secrets(data: Any, path: str = "") -> list[str]:
    """Return paths of values that look like inline secrets instead of ``vault://`` refs."""
    problems: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{path}.{key}" if path else str(key)
            k = str(key)
            secretish = (k.endswith("_ref") and k not in _NON_SECRET_REFS) or bool(
                _SECRET_KEY_RE.search(k)
            )
            if secretish and isinstance(value, str) and not value.startswith("vault://"):
                problems.append(here)
                continue
            problems.extend(find_inline_secrets(value, here))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            problems.extend(find_inline_secrets(item, f"{path}[{i}]"))
    elif isinstance(data, str) and any(r.search(data) for r in _SECRET_VALUE_RES):
        problems.append(path)
    return problems


# --- loading --------------------------------------------------------------


def config_hash(config: TenantConfig) -> str:
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_tenant_config(
    data: Any, *, source: str = "<memory>", schema: dict[str, Any] | None = None
) -> LoadedTenantConfig:
    if not isinstance(data, dict):
        raise TenantConfigError(f"{source}: top level must be a mapping")
    secrets = find_inline_secrets(data)
    if secrets:
        raise TenantConfigError(
            f"{source}: inline secrets are not allowed; use vault:// refs at: "
            + ", ".join(sorted(secrets))
        )
    validator = jsonschema.Draft202012Validator(schema or generate_json_schema())
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        msgs = [f"{'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]
        raise TenantConfigError(f"{source}: schema validation failed: " + "; ".join(msgs[:20]))
    try:
        # JSON mode: strict typing that still accepts enum/UUID strings as written in YAML.
        config = TenantConfig.model_validate_json(json.dumps(data))
    except ValidationError as exc:
        msgs = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        raise TenantConfigError(f"{source}: " + "; ".join(msgs[:20])) from exc
    return LoadedTenantConfig(config=config, version_hash=config_hash(config), source=source)


def load_tenant_config(path: Path, *, schema_path: Path | None = None) -> LoadedTenantConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TenantConfigError(f"{path}: invalid YAML: {exc}") from exc
    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path else None
    loaded = parse_tenant_config(data, source=str(path), schema=schema)
    if loaded.config.tenant.slug != path.stem:
        raise TenantConfigError(f"{path}: tenant.slug must match the file name")
    return loaded


def load_all_tenant_configs(
    directory: Path, *, schema_path: Path | None = None
) -> dict[str, LoadedTenantConfig]:
    out: dict[str, LoadedTenantConfig] = {}
    ids: set[UUID] = set()
    for path in sorted(directory.glob("*.yaml")):
        loaded = load_tenant_config(path, schema_path=schema_path)
        if loaded.config.tenant.id in ids:
            raise TenantConfigError(f"{path}: duplicate tenant id")
        ids.add(loaded.config.tenant.id)
        out[loaded.config.tenant.slug] = loaded
    return out


def main() -> None:  # pragma: no cover - CLI shim
    """``python -m mre_core.tenant_config`` regenerates the committed schema."""
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "config/schema/tenant.schema.json")
    write_json_schema(target)
    print(f"wrote {target}")


if __name__ == "__main__":  # pragma: no cover
    main()
