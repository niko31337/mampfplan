from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_TOKEN_LENGTH = 22  # entspricht ~128 Bit bei base64url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/mampfplan.db"
    space_token: str | None = None
    space_name: str = "Familie"
    members: str = "Person 1,Person 2"
    timezone: str = "Europe/Berlin"
    secure_cookies: bool = False
    public_url: str = "http://localhost:8000"
    spoonacular_api_key: str | None = None

    @field_validator("space_token")
    @classmethod
    def _token_long_enough(cls, value: str | None) -> str | None:
        if value and len(value) < MIN_TOKEN_LENGTH:
            raise ValueError(f"SPACE_TOKEN muss mindestens {MIN_TOKEN_LENGTH} Zeichen lang sein")
        return value or None

    @property
    def member_names(self) -> list[str]:
        names = [n.strip()[:50] for n in self.members.split(",") if n.strip()]
        return names or ["Person 1"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def now() -> datetime:
    """Aktuelle Zeit (naiv) in der konfigurierten Zeitzone."""
    return datetime.now(ZoneInfo(get_settings().timezone)).replace(tzinfo=None)


def today() -> date:
    return now().date()
