"""Process settings read from the environment (Vault Agent injects secrets as env/files)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. No secret has a default; missing secrets stay ``None``."""

    model_config = SettingsConfigDict(env_prefix="MRE_", env_file=None, extra="ignore")

    env: str = "dev"
    service_name: str = "mre"
    log_level: str = "INFO"

    # Application connection (role mre_app, subject to RLS).
    database_url: SecretStr | None = Field(default=None, validation_alias="DATABASE_URL")
    # Migration connection (role mre_migrator / owner). Only used by Alembic.
    migrator_database_url: SecretStr | None = Field(
        default=None, validation_alias="MIGRATOR_DATABASE_URL"
    )
    redis_url: SecretStr | None = Field(default=None, validation_alias="REDIS_URL")

    tenant_config_dir: Path = Path("config/tenants")
    tenant_schema_path: Path = Path("config/schema/tenant.schema.json")

    db_pool_size: int = 5
    db_statement_timeout_ms: int = 30_000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
