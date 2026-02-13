from __future__ import annotations

import json
import os
from typing import Any, Dict, Generator, Iterable, Optional

import requests

DEFAULT_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


class OllamaError(RuntimeError):
    """Raised when the Ollama HTTP API returns an error response."""


def _normalize_base_url(base_url: Optional[str]) -> str:
    base = base_url or DEFAULT_BASE_URL
    if base.endswith("/"):
        base = base[:-1]
    return base


def _build_url(path: str, base_url: Optional[str] = None) -> str:
    base = _normalize_base_url(base_url)
    return f"{base}{path}"


def generate(
    *,
    model: str,
    prompt: str,
    options: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
    stream: bool = False,
    timeout: Optional[int] = 120,
) -> Dict[str, Any] | Generator[Dict[str, Any], None, None]:
    """Call Ollama's /api/generate endpoint.

    When stream=False (default), a single response dict is returned.
    When stream=True, a generator yielding chunk dicts is returned.
    """

    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
    }
    if options:
        payload["options"] = options

    url = _build_url("/api/generate", base_url)

    if stream:
        response = requests.post(url, json=payload, stream=True, timeout=timeout)
        response.raise_for_status()

        def _stream() -> Generator[Dict[str, Any], None, None]:
            for line in response.iter_lines():
                if not line:
                    continue
                yield json.loads(line)

        return _stream()

    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def list_models(base_url: Optional[str] = None, timeout: int = 30) -> Iterable[Dict[str, Any]]:
    """Return the raw list of models from /api/tags."""

    url = _build_url("/api/tags", base_url)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    models = data.get("models")
    if isinstance(models, list):
        return models
    return []


def pull_model(
    model: str,
    *,
    base_url: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Iterable[Dict[str, Any]]:
    """Stream progress dictionaries while pulling a model."""

    url = _build_url("/api/pull", base_url)
    payload = {"name": model, "stream": True}
    response = requests.post(url, json=payload, stream=True, timeout=timeout)
    response.raise_for_status()

    for line in response.iter_lines():
        if not line:
            continue
        yield json.loads(line)


__all__ = [
    "DEFAULT_BASE_URL",
    "OllamaError",
    "generate",
    "list_models",
    "pull_model",
]
