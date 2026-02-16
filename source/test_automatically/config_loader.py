from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

CONFIG_DIR = Path(__file__).resolve().parent / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "settings.yaml"
_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def _load_all() -> Dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    if not DEFAULT_CONFIG_PATH.exists():
        _CONFIG_CACHE = {}
        return _CONFIG_CACHE

    try:
        with DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as fp:
            loaded = yaml.safe_load(fp) or {}
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"設定ファイルの読み込みに失敗しました: {DEFAULT_CONFIG_PATH}") from exc

    if not isinstance(loaded, dict):
        loaded = {}

    _CONFIG_CACHE = loaded
    return loaded


def load_section(name: str) -> Dict[str, Any]:
    raw = _load_all().get(name, {})
    if isinstance(raw, dict):
        return raw
    return {}


def get_value(section: str, key: str, fallback: Any) -> Any:
    value = load_section(section).get(key, fallback)
    return fallback if value is None else value