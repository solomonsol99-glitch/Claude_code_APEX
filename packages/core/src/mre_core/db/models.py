"""SQLAlchemy 2.0 models (Appendix B). Every tenant-owned table has ``tenant_id`` + forced RLS."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from mre_core.states import JobStatus, RunStatus

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
    }


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))


def _tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


job_status_enum = Enum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e])
run_status_enum = Enum(RunStatus, name="run_status", values_callable=lambda e: [m.value for m in e])


class Tenant(Base):
    """Platform table; RLS keys on ``id`` (a tenant sees only itself)."""

    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    created_at: Mapped[datetime] = _created()


class TenantConfigVersion(Base):
    __tablename__ = "tenant_config_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "version_hash"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    version_hash: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "oidc_subject"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    oidc_subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320))
    disabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = _created()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created()


class ApiKey(Base):
    """Only the prefix and an Argon2id hash are stored (§3.2)."""

    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    prefix: Mapped[str] = mapped_column(String(32), unique=True)
    hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    ip_allowlist: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Intake(Base):
    __tablename__ = "intakes"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    current_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()


class IntakeVersion(Base):
    """Immutable; every edit creates a new version."""

    __tablename__ = "intake_versions"
    __table_args__ = (UniqueConstraint("intake_id", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    intake_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("intakes.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]]
    attested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = _created()


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    intake_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("intake_versions.id"))
    config_version_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[JobStatus] = mapped_column(
        job_status_enum, server_default=JobStatus.SUBMITTED.value
    )
    is_partial: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    estimate_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Arm(Base):
    __tablename__ = "arms"
    __table_args__ = (UniqueConstraint("job_id", "label"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(64))
    copy_variant: Mapped[dict[str, Any]]


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    arm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("arms.id", ondelete="CASCADE"))
    engine: Mapped[str] = mapped_column(String(32))
    engine_version: Mapped[str] = mapped_column(String(64))
    model_route: Mapped[str] = mapped_column(String(64))
    seed: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[RunStatus] = mapped_column(
        run_status_enum, server_default=RunStatus.PENDING.value
    )
    attempt: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    is_baseline: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    truncated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    engine_instance: Mapped[str | None] = mapped_column(String(128))
    engine_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class RunCheckpoint(Base):
    __tablename__ = "run_checkpoints"
    __table_args__ = (UniqueConstraint("run_id", "stage", "attempt"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    stage: Mapped[RunStatus] = mapped_column(run_status_enum)
    attempt: Mapped[int] = mapped_column(Integer)
    outputs_uri: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created()


class RunValidation(Base):
    __tablename__ = "run_validations"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    check_name: Mapped[str] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean)
    action: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created()


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]]
    score: Mapped[float] = mapped_column(Float)
    grade: Mapped[str] = mapped_column(String(16))
    caps_applied: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = _created()


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    template_version: Mapped[str] = mapped_column(String(32))
    files: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    is_draft: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = _created()


class Outcome(Base):
    __tablename__ = "outcomes"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    outcome_type: Mapped[str] = mapped_column(String(16))  # ab_test | launch | survey
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = _created()


class UsageLedger(Base):
    __tablename__ = "usage_ledger"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    model_route: Mapped[str | None] = mapped_column(String(64))
    tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    zep_ops: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    created_at: Mapped[datetime] = _created()


class AuditEvent(Base):
    """Append-only, hash-chained per tenant (§3.9). ``tenant_id`` NULL = platform chain."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index(
            "uq_audit_events_chain_seq",
            text("coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            "seq",
            unique=True,
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    seq: Mapped[int] = mapped_column(BigInteger)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(255))
    actor_type: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str] = mapped_column(String(255))
    result: Mapped[str] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class DeletionCertificate(Base):
    __tablename__ = "deletion_certificates"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    subject: Mapped[str] = mapped_column(String(255))
    certificate: Mapped[dict[str, Any]]
    signature: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


TENANT_TABLES: tuple[str, ...] = tuple(
    t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c
)
