"""Runtime configuration, validated once at startup via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.models import Category

LLMBackend = Literal["claude", "fake"]
OutboundMode = Literal["dry_run", "live"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- app ---------------------------------------------------------------
    app_env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    log_json: bool = True
    metrics_enabled: bool = False

    # --- auth --------------------------------------------------------------
    api_keys: list[str] = Field(
        default_factory=list,
        description="Comma-separated X-API-Key values accepted on every endpoint except "
        "/health, /ready, /metrics and the signed webhook routes.",
    )

    # --- persistence -------------------------------------------------------
    database_url: str = "sqlite:///./lead_agent.db"

    # --- llm ---------------------------------------------------------------
    anthropic_api_key: str | None = None
    lead_agent_llm: LLMBackend = "claude"
    lead_agent_model: str = "claude-sonnet-5-5"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    llm_price_input_per_mtok: float = 2.0
    llm_price_output_per_mtok: float = 10.0

    # --- inbound webhooks --------------------------------------------------
    twilio_webhook_enabled: bool = False
    twilio_auth_token: str | None = None
    twilio_webhook_url: str | None = Field(
        default=None,
        description="Public URL Twilio posts to (used for signature computation when the app "
        "sits behind a proxy that rewrites the Host header). Falls back to the request URL.",
    )
    sendgrid_webhook_enabled: bool = False
    sendgrid_inbound_secret: str | None = Field(
        default=None,
        description="Shared secret required as ?token= on /webhooks/sendgrid. "
        "SendGrid Inbound Parse does not sign requests.",
    )

    # --- outbound ----------------------------------------------------------
    outbound_mode: OutboundMode = "dry_run"
    twilio_account_sid: str | None = None
    twilio_from_number: str | None = None
    sendgrid_api_key: str | None = None
    sendgrid_from_email: str | None = None
    auto_send_categories: list[Category] = Field(
        default_factory=list,
        description="Categories whose send_reply action is executed without approval. "
        "Escalated threads are never auto-sent.",
    )

    @field_validator("api_keys", "auto_send_categories", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @model_validator(mode="after")
    def _check_production(self) -> Settings:
        if self.app_env != "production":
            return self
        problems: list[str] = []
        if not self.api_keys:
            problems.append("API_KEYS must be set")
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL must point at Postgres")
        if self.twilio_webhook_enabled and not self.twilio_auth_token:
            problems.append("TWILIO_AUTH_TOKEN required when TWILIO_WEBHOOK_ENABLED")
        if self.sendgrid_webhook_enabled and not self.sendgrid_inbound_secret:
            problems.append("SENDGRID_INBOUND_SECRET required when SENDGRID_WEBHOOK_ENABLED")
        if self.outbound_mode == "live":
            if not (self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_number):
                problems.append("Twilio credentials required for OUTBOUND_MODE=live")
            if not (self.sendgrid_api_key and self.sendgrid_from_email):
                problems.append("SendGrid credentials required for OUTBOUND_MODE=live")
        if problems:
            raise ValueError("production config invalid: " + "; ".join(problems))
        return self

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)


@lru_cache
def get_settings() -> Settings:
    return Settings()
