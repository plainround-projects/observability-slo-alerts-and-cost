"""Configuration read once from the environment and validated (twelve-factor)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class SettingsError(ValueError):
    """An environment variable is missing or has an unusable value."""


def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = env.get(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError as error:
        raise SettingsError(f"{name} must be a number, got {raw!r}") from error
    if not low <= value <= high:
        raise SettingsError(f"{name} must be between {low} and {high}, got {value}")
    return value


def _flag(env: Mapping[str, str], name: str) -> bool:
    raw = env.get(name, "false").strip().lower()
    if raw not in {"true", "false"}:
        raise SettingsError(f"{name} must be true or false, got {raw!r}")
    return raw == "true"


def _level(env: Mapping[str, str]) -> str:
    level = env.get("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise SettingsError(f"LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR, got {level!r}")
    return level


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    """What the checkout service reads from its environment."""

    service_name: str
    bind_host: str
    port: int
    allow_simulation: bool
    slow_seconds: float
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ServiceSettings:
        """Build the settings, raising ``SettingsError`` for an invalid variable."""
        return cls(
            service_name=env.get("OTEL_SERVICE_NAME", "checkout"),
            bind_host=env.get("BIND_HOST", "127.0.0.1"),
            port=int(_number(env, "SERVICE_PORT", 8080, 1, 65535)),
            allow_simulation=_flag(env, "ALLOW_SIMULATION"),
            slow_seconds=_number(env, "SLOW_SECONDS", 1.4, 0.0, 30.0),
            log_level=_level(env),
        )


@dataclass(frozen=True, slots=True)
class PagerSettings:
    """What the fake pager reads from its environment."""

    bind_host: str
    port: int
    pages_file: str
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> PagerSettings:
        """Build the settings, raising ``SettingsError`` for an invalid variable."""
        return cls(
            bind_host=env.get("BIND_HOST", "127.0.0.1"),
            port=int(_number(env, "PAGER_PORT", 8090, 1, 65535)),
            pages_file=env.get("PAGES_FILE", "/data/pages.jsonl"),
            log_level=_level(env),
        )
