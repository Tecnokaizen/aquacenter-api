from __future__ import annotations

from typing import Any

from app.core.config import settings


def ensure_relative_path(path: str) -> str:
    normalized = (path or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def build_public_url(request: Any, path: str) -> str:
    rel = ensure_relative_path(path)
    if not rel:
        return ""
    if rel.startswith("http://") or rel.startswith("https://"):
        return rel

    if settings.public_base_url:
        return settings.public_base_url.rstrip("/") + rel

    forwarded_proto = _first_header_value(request.headers.get("x-forwarded-proto"))
    forwarded_host = _first_header_value(request.headers.get("x-forwarded-host"))
    if forwarded_host:
        scheme = forwarded_proto or request.url.scheme or "http"
        return f"{scheme}://{forwarded_host}{rel}"

    if forwarded_proto:
        host = request.headers.get("host")
        if host:
            return f"{forwarded_proto}://{host}{rel}"

    return str(request.base_url).rstrip("/") + rel


def _first_header_value(value: str | None) -> str:
    if not value:
        return ""
    return value.split(",")[0].strip().lower()
