from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    model_fast: str = "claude-haiku-4-5"
    model_strong: str = "claude-sonnet-4-5"
    llm_max_output_tokens: int = 1200
    min_field_confidence: float = 0.85

    database_url: str = "sqlite:///./data/invoice_guard.db"
    checkpoint_url: str = "./data/checkpoints.db"

    date_max_age_days: int = 90
    price_tolerance: float = 0.02
    total_tolerance_minor: int = 1
    maker_checker_threshold_lkr: int = 1_000_000


settings = Settings()
