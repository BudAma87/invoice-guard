"""Process-level settings (env / .env). Business thresholds live in RuleSetConfig
(libs.rules.config) and are versioned per legal entity."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    anthropic_api_key: str = ""
    model_primary: str = "claude-haiku-5-5"
    model_fallback: str = "claude-sonnet-5-5"
    llm_max_output_tokens: int = 1200
    llm_rpm: int = 500
    llm_tpm: int = 400_000

    # Storage
    database_url: str = "sqlite:///./data/invoice_guard.db"
    object_store_dir: str = "./data/blobs"

    # Messaging
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic_in: str = "invoices.received"
    kafka_topic_posted: str = "invoices.posted"
    kafka_topic_dlq: str = "invoices.dlq"

    # Reconciliation: where "what the channel received" is read from (file | kafka | none)
    message_ledger: str = "file"

    # Leases (seconds)
    extract_lease_s: int = 120
    review_lease_s: int = 15 * 60

    # Dev auth: when true, the API trusts X-User headers (no OIDC). Never in prod.
    # Off by default so a deploy that forgets the variable fails closed (opt in via .env).
    dev_auth: bool = False
    cors_origins: str = "http://localhost:3000"

    # Signed PDF links. Set from a vault in prod; empty = random per-process key (dev only).
    signing_secret: str = ""
    pdf_link_ttl_s: int = 300

    # Per-client request limit for the review API (requests per minute; 0 = off).
    api_rate_limit_per_min: int = 600


settings = Settings()
