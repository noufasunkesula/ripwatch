"""Settings for every RipWatch service, loaded from environment variables (sprint-1.md N-03).

This is the only place resource name defaults live (sprint-1.md section 5). Values with no
safe default (queue URLs, topic ARN, account ID) are optional at load time; code that needs one
calls `settings.require("<field>")`, which raises `MissingSettingError` naming the variable.
Empty variables count as unset.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class MissingSettingError(RuntimeError):
    """A setting the caller needs is not set in the environment."""


class SettingsError(ValueError):
    """An environment variable is set to a value that cannot be used."""


class Settings(BaseModel):
    """Frozen runtime settings. Field aliases are the environment variable names."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    # AWS access
    aws_region: str = Field("us-east-1", alias="AWS_REGION")
    aws_account_id: str | None = Field(None, alias="AWS_ACCOUNT_ID", pattern=r"^\d{12}$")
    aws_endpoint_url: str | None = Field(None, alias="RW_AWS_ENDPOINT_URL")
    aws_read_timeout_s: float = Field(10.0, alias="RW_AWS_READ_TIMEOUT_S", gt=0)
    bedrock_read_timeout_s: float = Field(20.0, alias="RW_BEDROCK_READ_TIMEOUT_S", gt=0)

    # Names (sprint-1.md section 5)
    prefix: str = Field("rw", alias="RW_PREFIX")
    ssm_prefix: str = Field("/rw", alias="RW_SSM_PREFIX")
    data_bucket: str | None = Field(None, alias="RW_DATA_BUCKET")
    artifacts_bucket: str | None = Field(None, alias="RW_ARTIFACTS_BUCKET")
    table_cameras: str = Field("rw-cameras", alias="RW_TABLE_CAMERAS")
    table_jobs: str = Field("rw-jobs", alias="RW_TABLE_JOBS")
    table_detections: str = Field("rw-detections", alias="RW_TABLE_DETECTIONS")
    table_incidents: str = Field("rw-incidents", alias="RW_TABLE_INCIDENTS")
    table_trace: str = Field("rw-agent-trace", alias="RW_TABLE_TRACE")
    table_approvals: str = Field("rw-approvals", alias="RW_TABLE_APPROVALS")
    queue_jobs_url: str | None = Field(None, alias="RW_QUEUE_JOBS_URL")
    queue_candidates_url: str | None = Field(None, alias="RW_QUEUE_CANDIDATES_URL")
    topic_lifeguard_arn: str | None = Field(None, alias="RW_TOPIC_LIFEGUARD_ARN")
    worker_asg: str = Field("rw-worker-asg", alias="RW_WORKER_ASG")
    metric_namespace: str = Field("RipWatch", alias="RW_METRIC_NAMESPACE")

    # Runtime
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO", alias="RW_LOG_LEVEL"
    )
    runtime: Literal["cool", "std-arm", "std-x86"] | None = Field(None, alias="RW_RUNTIME")
    heartbeat_dir: str = Field("/var/run/rw", alias="RW_HEARTBEAT_DIR")
    # Ingest work dirs, camera-sim pointer (persistent, unlike the heartbeat dir).
    state_dir: str = Field("/var/lib/rw", alias="RW_STATE_DIR")
    # Fake by default so nothing calls (and pays for) Bedrock unless asked to.
    llm: Literal["fake", "bedrock"] = Field("fake", alias="RW_LLM")
    bedrock_model_id: str = Field("amazon.nova-lite-v1:0", alias="RW_BEDROCK_MODEL_ID")

    # Services
    camera_sim_interval_s: float = Field(10.0, alias="RW_CAMERA_SIM_INTERVAL_S", gt=0)
    frame_folder_fps: float = Field(15.0, alias="RW_FRAME_FOLDER_FPS", gt=0)
    allowed_origin: str | None = Field(None, alias="RW_ALLOWED_ORIGIN")

    # Ocean data (rw-ocean-poller)
    noaa_station_id: str | None = Field(None, alias="RW_NOAA_STATION_ID")
    nws_zone_id: str | None = Field(None, alias="RW_NWS_ZONE_ID")
    nws_user_agent: str | None = Field(None, alias="RW_NWS_USER_AGENT")

    @model_validator(mode="after")
    def _derive_bucket_names(self) -> Settings:
        """Bucket names follow `<prefix>-<kind>-<account_id>` when not set explicitly."""
        if self.aws_account_id:
            for field, kind in (("data_bucket", "data"), ("artifacts_bucket", "artifacts")):
                if getattr(self, field) is None:
                    name = f"{self.prefix}-{kind}-{self.aws_account_id}"
                    object.__setattr__(self, field, name)
        return self

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Load from `environ` (default `os.environ`), with a clear error on bad values."""
        source = os.environ if environ is None else environ
        aliases = {f.alias for f in cls.model_fields.values()}
        values = {k: v for k, v in source.items() if k in aliases and v.strip() != ""}
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            problems = "; ".join(
                f"{err['loc'][0] if err['loc'] else 'settings'}: {err['msg']}"
                for err in exc.errors()
            )
            raise SettingsError(f"Invalid environment: {problems}") from None

    def require(self, field: str) -> Any:
        """Return a setting that must be present, or raise naming its environment variable."""
        info = type(self).model_fields.get(field)
        if info is None:
            raise AttributeError(f"Settings has no field {field!r}")
        value = getattr(self, field)
        if value is None:
            hint = " (or set AWS_ACCOUNT_ID)" if field.endswith("_bucket") else ""
            raise MissingSettingError(f"{info.alias} is not set{hint}; add it to .env")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings from the process environment, loaded once. Tests call `cache_clear()`."""
    return Settings.from_env()
