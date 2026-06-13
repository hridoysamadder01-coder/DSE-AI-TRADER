from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./data/market.db"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_env: str = "dev"
    log_level: str = "INFO"

    dse_base_url: str = "https://www.dsebd.org"
    cse_base_url: str = "https://www.cse.com.bd"
    http_timeout_seconds: int = 20
    http_user_agent: str = "DSE-AI-Trader-OS/0.1 (data-foundation)"

    collect_intraday_cron_minute: str = "*/2"
    collect_eod_cron_hour: int = 16
    collect_eod_cron_minute: int = 30
    quality_check_cron_minute: str = "*/15"

    market_tz: str = "Asia/Dhaka"
    market_open_hour: int = 10
    market_open_minute: int = 0
    market_close_hour: int = 14
    market_close_minute: int = 30

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def data_dir(self) -> Path:
        d = self.project_root / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def logs_dir(self) -> Path:
        d = self.project_root / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d


@lru_cache
def get_settings() -> Settings:
    return Settings()
