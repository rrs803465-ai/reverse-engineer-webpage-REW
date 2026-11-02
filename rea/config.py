"""REW configuration management.

Secrets (API keys) are stored separately from settings:
  1. OS keyring (preferred, via keyring library)
  2. Environment variables (REW_<PROVIDER>_API_KEY)
  3. Encrypted file fallback with restrictive permissions

Non-secret settings live in ~/.config/rew/config.json.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from rea.models import ProviderConfig, ProviderType
from rea.security.filesystem import restrict_permissions

log = logging.getLogger(__name__)

_CONFIG_DIR = Path.home() / ".config" / "rew"
_CONFIG_FILE = _CONFIG_DIR / "config.json"
_KEYRING_SERVICE = "rew"


def _config_dir() -> Path:
    d = _CONFIG_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Keyring / secret helpers
# ---------------------------------------------------------------------------

def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


def store_api_key(provider_name: str, key: str) -> None:
    """Store an API key for *provider_name*."""
    if _keyring_available():
        import keyring
        keyring.set_password(_KEYRING_SERVICE, provider_name, key)
        log.debug("Stored API key for '%s' in OS keyring.", provider_name)
    else:
        # Fallback: encrypted-ish file (plaintext but restricted perms).
        key_file = _config_dir() / "keys.json"
        keys: dict[str, str] = {}
        if key_file.exists():
            try:
                keys = json.loads(key_file.read_text())
            except Exception:
                keys = {}
        keys[provider_name] = key
        key_file.write_text(json.dumps(keys))
        restrict_permissions(key_file)
        log.warning(
            "keyring not available. API key stored in plaintext at %s (permissions: 600).",
            key_file,
        )


def get_api_key(provider_name: str) -> str | None:
    """Retrieve API key; returns None if not found."""
    # 1. Environment variable.
    env_name = f"REW_{provider_name.upper().replace('-', '_')}_API_KEY"
    val = os.environ.get(env_name)
    if val:
        log.debug("API key for '%s' loaded from environment.", provider_name)
        return val

    # 2. OS keyring.
    if _keyring_available():
        import keyring
        val = keyring.get_password(_KEYRING_SERVICE, provider_name)
        if val:
            return val

    # 3. File fallback.
    key_file = _config_dir() / "keys.json"
    if key_file.exists():
        try:
            keys = json.loads(key_file.read_text())
            return keys.get(provider_name)
        except Exception:
            return None

    return None


def delete_api_key(provider_name: str) -> None:
    if _keyring_available():
        import keyring
        try:
            keyring.delete_password(_KEYRING_SERVICE, provider_name)
        except Exception:
            pass

    key_file = _config_dir() / "keys.json"
    if key_file.exists():
        try:
            keys = json.loads(key_file.read_text())
            keys.pop(provider_name, None)
            key_file.write_text(json.dumps(keys))
            restrict_permissions(key_file)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Settings (non-secret)
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS: dict[str, Any] = {
    "default_provider": None,
    "providers": {},
    "browser": "chromium",
    "headless": True,
    "viewport_width": 1280,
    "viewport_height": 800,
    "crawl_depth": 3,
    "max_pages": 20,
    "request_timeout_seconds": 30,
    "tool_call_limit": 100,
    "max_iterations": 50,
    "input_token_budget": 500_000,
    "output_token_budget": 100_000,
    "cost_budget_usd": None,
    "retry_limit": 5,
    "concurrency": 2,
    "capture_screenshots": True,
    "full_page_screenshots": False,
    "cache_expiry_hours": 24,
    "output_workspace": str(Path.cwd() / "projects"),
    "log_verbosity": 0,
    "allowed_domains": [],
}


class Settings:
    """Thin wrapper around the JSON settings file."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(_DEFAULT_SETTINGS)
        self._load()

    def _load(self) -> None:
        if _CONFIG_FILE.exists():
            try:
                on_disk = json.loads(_CONFIG_FILE.read_text())
                self._data.update(on_disk)
            except Exception as exc:
                log.warning("Could not load config file: %s", exc)

    def save(self) -> None:
        _config_dir()
        _CONFIG_FILE.write_text(json.dumps(self._data, indent=2))

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def all(self) -> dict[str, Any]:
        return dict(self._data)

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def save_provider(self, name: str, cfg: ProviderConfig) -> None:
        providers = self._data.setdefault("providers", {})
        providers[name] = cfg.model_dump()
        self.save()

    def delete_provider(self, name: str) -> bool:
        providers = self._data.get("providers", {})
        if name in providers:
            del providers[name]
            self.save()
            return True
        return False

    def set_default_provider(self, name: str) -> None:
        self._data["default_provider"] = name
        self.save()

    def default_provider(self) -> str | None:
        return self._data.get("default_provider")

    def list_providers(self) -> list[ProviderConfig]:
        raw = self._data.get("providers", {})
        result = []
        for name, cfg in raw.items():
            try:
                result.append(ProviderConfig.from_dict(cfg))
            except Exception as exc:
                log.warning("Invalid provider config '%s': %s", name, exc)
        return result

    def get_provider(self, name: str) -> ProviderConfig | None:
        raw = self._data.get("providers", {}).get(name)
        if raw is None:
            return None
        try:
            return ProviderConfig.from_dict(raw)
        except Exception:
            return None


# Singleton instance
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
