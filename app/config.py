import os

from pydantic import AnyHttpUrl, BaseModel, SecretStr, ValidationError, field_validator
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

DEFAULT_KEITARO_URL = "https://keitaro-for-tests.etolegko.com"
DEFAULT_DATABASE_URL = "sqlite:///./data/keitaro.db"


class ConfigurationError(RuntimeError):
    pass


def database_url_from_env() -> str:
    value = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        url = make_url(value)
    except ArgumentError:
        raise ConfigurationError("DATABASE_URL должен указывать на файл SQLite.") from None
    if (
        url.drivername not in {"sqlite", "sqlite+pysqlite"}
        or not url.database
        or url.database == ":memory:"
        or url.username
        or url.password
        or url.host
        or url.port
        or url.query
    ):
        raise ConfigurationError("DATABASE_URL должен указывать на файл SQLite.")
    return value


class Settings(BaseModel):
    model_config = {"hide_input_in_errors": True}

    keitaro_base_url: str
    keitaro_api_key: SecretStr
    keitaro_test_prefix: str
    keitaro_domain_id: int
    keitaro_group_id: int
    keitaro_traffic_source_id: int
    database_url: str

    @field_validator("keitaro_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            url = AnyHttpUrl(value)
        except ValueError:
            raise ValueError("укажите адрес Keitaro.") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username is not None
            or url.password is not None
            or url.query is not None
            or url.fragment is not None
        ):
            raise ValueError("укажите HTTP(S)-адрес Keitaro.")
        return str(url).rstrip("/")

    @field_validator("keitaro_api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("укажите ключ API Keitaro.")
        return value

    @field_validator("keitaro_test_prefix")
    @classmethod
    def validate_test_prefix(cls, value: str) -> str:
        if (
            not value.startswith("test_")
            or not value.endswith("_")
            or not value[5:-1]
            or any(char.isspace() for char in value)
        ):
            raise ValueError("укажите тестовый префикс в формате test_фамилия_.")
        return value

    @field_validator("keitaro_domain_id", "keitaro_group_id", "keitaro_traffic_source_id")
    @classmethod
    def validate_configured_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("укажите положительный ID.")
        return value


def load_settings() -> Settings:
    try:
        return Settings(
            keitaro_base_url=os.environ.get("KEITARO_BASE_URL", DEFAULT_KEITARO_URL),
            keitaro_api_key=os.environ.get("KEITARO_API_KEY", ""),
            keitaro_test_prefix=os.environ.get("KEITARO_TEST_PREFIX", ""),
            keitaro_domain_id=os.environ.get("KEITARO_DOMAIN_ID", ""),
            keitaro_group_id=os.environ.get("KEITARO_GROUP_ID", ""),
            keitaro_traffic_source_id=os.environ.get("KEITARO_TRAFFIC_SOURCE_ID", ""),
            database_url=database_url_from_env(),
        )
    except ValidationError as exc:
        variables = ", ".join(error["loc"][0].upper() for error in exc.errors())
        raise ConfigurationError(f"Некорректная конфигурация: {variables}.") from None
