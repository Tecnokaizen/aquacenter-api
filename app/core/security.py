from __future__ import annotations

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="API_KEY no configurada en entorno.")
    if credentials is None or (credentials.scheme or "").lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authorization Bearer requerido.")
    if credentials.credentials.strip() != settings.api_key:
        raise HTTPException(status_code=401, detail="API key inválida.")

