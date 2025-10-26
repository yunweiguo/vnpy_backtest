from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


CONFIG_ENV_KEY = "BACKTEST_CONFIG_PATH"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "settings.toml"


@dataclass
class Settings:
    mysql_stock_dsn: str
    mysql_option_qs_us_dsn: str
    mysql_option_qs_hk_dsn: str
    mysql_option_history_dsn: str
    redis_url: str
    artifacts_root: str


def _load_config_dict() -> dict:
    path = Path(os.getenv(CONFIG_ENV_KEY, DEFAULT_CONFIG_PATH))
    if not path.exists():
        raise FileNotFoundError(
            f"配置文件未找到：{path}. 可通过环境变量 {CONFIG_ENV_KEY} 指定路径。"
        )
    with open(path, "rb") as f:
        return tomllib.load(f)


def _env_override(key: str, default: str | None) -> str:
    env_key = f"BACKTEST_{key.upper()}"
    value = os.getenv(env_key)
    if value:
        return value
    if default is None:
        raise ValueError(f"缺少配置项：{key}")
    return default


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    cfg = _load_config_dict()
    mysql = cfg.get("mysql", {})
    redis = cfg.get("redis", {})
    artifacts = cfg.get("artifacts", {})

    return Settings(
        mysql_stock_dsn=_env_override("mysql_stock_dsn", mysql.get("stock_dsn")),
        mysql_option_qs_us_dsn=_env_override("mysql_option_qs_us_dsn", mysql.get("option_qs_us_dsn")),
        mysql_option_qs_hk_dsn=_env_override("mysql_option_qs_hk_dsn", mysql.get("option_qs_hk_dsn")),
        mysql_option_history_dsn=_env_override("mysql_option_history_dsn", mysql.get("option_history_dsn")),
        redis_url=_env_override("redis_url", redis.get("url")),
        artifacts_root=_env_override("artifacts_root", artifacts.get("root")),
    )
