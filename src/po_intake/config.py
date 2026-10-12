"""Load config.yaml and the secrets from .env (spec section 3)."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """Startup problem the user must fix (missing setting, file or secret)."""


def load_config(path: str | Path = "config.yaml") -> dict:
    """Read config.yaml and load .env into the environment.

    Secrets stay in os.environ; they are never copied into the returned dict,
    so printing or logging the config can't leak them.
    """
    load_dotenv()  # .env -> os.environ; real environment variables win
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except OSError as e:
        raise ConfigError(f"Cannot read config file {path}: {e.strerror}") from None


def require_env(*names: str) -> None:
    """Exit-worthy error if any secret is missing. Reports names, never values."""
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise ConfigError(f"Missing environment variable(s): {', '.join(missing)}. See .env.example.")
