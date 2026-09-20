"""Seguridad.

  - Cada cliente (tenant) tiene una o más claves API. En base de datos solo se guarda su hash.
  - El tenant SIEMPRE sale de la clave, nunca de una cabecera que el usuario pueda falsificar.
  - Las rutas de administración usan una clave aparte (ADMIN_API_KEY) definida en el servidor.
  - Límite de peticiones por cliente para los endpoints pesados.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .config import get_settings
from .db import ApiKey, Tenant, get_db, utcnow
from .errors import ApiError


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return "gk_" + secrets.token_urlsafe(32)


def _aware(value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def get_tenant_id(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
                  db: Session = Depends(get_db)) -> str:
    if not x_api_key:
        raise ApiError(401, "NO_AUTENTICADO", "Falta la clave de acceso (cabecera X-API-Key).")
    key = db.query(ApiKey).filter(ApiKey.key_hash == hash_key(x_api_key.strip())).first()
    if key is None or key.revoked:
        raise ApiError(401, "CLAVE_INVALIDA", "La clave de acceso no es válida o fue revocada.")
    tenant = db.get(Tenant, key.tenant_id)
    if tenant is None or not tenant.active:
        raise ApiError(403, "CLIENTE_INACTIVO", "Esta cuenta está desactivada. Contacta a soporte.")
    last = _aware(key.last_used_at)
    if last is None or (utcnow() - last) > dt.timedelta(minutes=5):
        key.last_used_at = utcnow()
        db.commit()
    return tenant.id


def require_admin(x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key")) -> None:
    expected = get_settings().admin_api_key
    if not expected:
        raise ApiError(503, "ADMIN_DESHABILITADO", "La administración está deshabilitada: define ADMIN_API_KEY en el servidor.")
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise ApiError(403, "ADMIN_NO_AUTORIZADO", "Clave de administración incorrecta.")


class SlidingWindowLimiter:
    """Límite en memoria por proceso. Con varias instancias, reemplazar por Redis."""

    def __init__(self, window_seconds: int = 60):
        self.window = window_seconds
        self.hits: dict = defaultdict(deque)

    def check(self, key: str, limit: int) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= limit:
            raise ApiError(429, "DEMASIADAS_PETICIONES", "Demasiadas solicitudes seguidas. Espera un minuto e intenta de nuevo.")
        q.append(now)


_limiter = SlidingWindowLimiter()


def heavy_tenant(tenant_id: str = Depends(get_tenant_id)) -> str:
    _limiter.check(tenant_id, get_settings().rate_limit_per_minute)
    return tenant_id
