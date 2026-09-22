"""GENESIS CORE B2B - API.

Endpoints (todos requieren X-API-Key salvo /health, /ready y /admin, que usa X-Admin-Key):
  GET    /health, /ready
  POST   /api/v1/admin/tenants                        crea un cliente y su primera clave
  POST   /api/v1/admin/tenants/{id}/keys              emite otra clave (rotación)
  POST   /api/v1/admin/keys/{key_id}/revoke          revoca una clave
  GET    /api/v1/me                                  cliente actual y configuración
  PUT    /api/v1/config                              umbrales del cliente (margen mínimo, IVA...)
  POST   /api/v1/data-understanding                  analiza un archivo y propone el mapeo
  POST   /api/v1/audits                              ejecuta la auditoría económica
  GET    /api/v1/audits, /audits/{id}, /audits/{id}/export, DELETE /audits/{id}
  GET    /api/v1/tasks, PATCH /api/v1/tasks/{id}
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, File, Form, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import ENGINE_VERSION, get_settings
from .core.ingestion import read_workbook
from .core.model import CANONICAL_IDS
from .core.pipeline import run_pipeline
from .core.semantic import GenesisDataUnderstanding, sheet_signature
from .core.serialize import clean
from .db import (ActionTask, ApiKey, AuditRecord, Finding, MappingMemory, Tenant, TenantConfig, get_db, init_db,
                 utcnow)
from .errors import ApiError
from .export import build_audit_workbook
from .security import generate_api_key, hash_key, heavy_tenant, get_tenant_id, require_admin

settings = get_settings()
log = logging.getLogger("genesis")

CONFIG_FIELDS = ["min_margin_percent", "z_score_threshold", "allow_negative_margin", "revenue_includes_vat",
                 "vat_rate", "outlier_min_group", "reconciliation_tolerance_pct"]
TASK_STATUSES = ("PENDIENTE", "EN_PROCESO", "RESUELTA", "DESCARTADA")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    if not settings.admin_api_key:
        log.warning("ADMIN_API_KEY no está definida: no podrás crear clientes hasta configurarla.")
    yield


app = FastAPI(title="GENESIS CORE B2B", version=ENGINE_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "X-Admin-Key", "Content-Type"],
    expose_headers=["Content-Disposition", "X-Request-ID"],
)


# ---------------------------------------------------------------------------
# Middleware y manejo de errores
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]
    request.state.request_id = rid
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    log.info("%s %s -> %s (%.0f ms) rid=%s", request.method, request.url.path, response.status_code,
             (time.perf_counter() - start) * 1000, rid)
    return response


def _error(request: Request, status: int, code: str, message: str, **extra) -> JSONResponse:
    body = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None), **extra}
    return JSONResponse(status_code=status, content={"error": body})


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError):
    return _error(request, exc.status, exc.code, exc.message)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    return _error(request, exc.status_code, "HTTP_ERROR", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    details = [{"campo": ".".join(str(p) for p in e.get("loc", [])), "mensaje": e.get("msg")} for e in exc.errors()]
    return _error(request, 422, "DATOS_INVALIDOS", "Revisa los datos enviados.", details=details)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    error_id = uuid.uuid4().hex[:10]
    log.exception("Error no controlado error_id=%s path=%s", error_id, request.url.path)
    return _error(request, 500, "ERROR_INTERNO",
                  f"Ocurrió un error inesperado. Si persiste, comparte este código con soporte: {error_id}.")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_sha(obj) -> str:
    return _sha(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))


def read_upload(file: UploadFile) -> tuple:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ApiError(413, "ARCHIVO_MUY_GRANDE", f"El archivo supera {settings.max_upload_mb} MB. Divídelo por periodos.")
    if not data:
        raise ApiError(400, "ARCHIVO_VACIO", "El archivo está vacío.")
    return (file.filename or "archivo"), data


def parse_mapping(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise ApiError(400, "MAPEO_INVALIDO", "El mapeo enviado no es un JSON válido.")
    if not isinstance(data, dict) or len(data) > 50:
        raise ApiError(400, "MAPEO_INVALIDO", "El mapeo debe ser un objeto {hoja: {columna: campo}}.")
    allowed = set(CANONICAL_IDS) | {"UNKNOWN"}
    out: dict = {}
    for sheet, cols in data.items():
        if not isinstance(cols, dict) or len(cols) > 500:
            raise ApiError(400, "MAPEO_INVALIDO", f"El mapeo de la hoja '{sheet}' no es válido.")
        out[str(sheet)] = {}
        for col, canonical in cols.items():
            if not isinstance(canonical, str) or canonical not in allowed:
                raise ApiError(422, "CAMPO_INVALIDO", f"'{canonical}' no es un campo válido del modelo.")
            out[str(sheet)][str(col)] = canonical
    return out


def get_or_create_config(db: Session, tenant_id: str) -> TenantConfig:
    row = db.get(TenantConfig, tenant_id)
    if row is None:
        row = TenantConfig(tenant_id=tenant_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def config_to_dict(row: TenantConfig) -> dict:
    return {k: getattr(row, k) for k in CONFIG_FIELDS}


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def finding_dict(f: Finding, cases_limit: Optional[int]) -> dict:
    casos = f.casos or []
    shown = casos if cases_limit is None else casos[:cases_limit]
    return {
        "id": f.id, "tipo": f.tipo, "prioridad": f.prioridad, "severidad": f.severidad, "titulo": f.titulo,
        "causa": f.causa, "impacto": f.impacto, "evidencia": f.evidencia, "accion": f.accion,
        "vehiculos_afectados": f.vehiculos or [], "casos": shown, "casos_total": f.casos_total or 0,
        "casos_guardados": len(casos),
    }


def task_dict(t: ActionTask) -> dict:
    return {"id": t.id, "audit_id": t.audit_id, "finding_id": t.finding_id, "department": t.department,
            "title": t.title, "description": t.description, "financial_impact": t.financial_impact,
            "urgency": t.urgency, "status": t.status, "comment": t.comment,
            "created_at": _iso(t.created_at), "updated_at": _iso(t.updated_at)}


def audit_payload(db: Session, audit: AuditRecord, cases_limit: Optional[int] = 20) -> dict:
    findings = (db.query(Finding).filter(Finding.audit_id == audit.id, Finding.tenant_id == audit.tenant_id)
                .order_by(Finding.prioridad).all())
    tasks = (db.query(ActionTask).filter(ActionTask.audit_id == audit.id, ActionTask.tenant_id == audit.tenant_id)
             .order_by(ActionTask.id).all())
    return {
        "id": audit.id, "filename": audit.filename, "timestamp": _iso(audit.timestamp), "estado": audit.estado,
        "engine_version": audit.engine_version, "file_sha256": audit.file_sha256, "calidad": audit.quality_report,
        "financials": audit.financial_results, "advertencias": audit.warnings or [], "monthly": audit.monthly or [],
        "merge_report": audit.merge_report, "config": audit.config_snapshot,
        "findings": [finding_dict(f, cases_limit) for f in findings], "tasks": [task_dict(t) for t in tasks],
    }


def remember_mapping(db: Session, tenant_id: str, mapping: dict, dfs: dict) -> None:
    for sheet, scoped in mapping.items():
        df = dfs.get(sheet)
        if df is None:
            continue
        cols = [c for c in df.columns if not str(c).startswith("_src_")]
        signature = sheet_signature(cols)
        full = {c: scoped.get(c, "UNKNOWN") for c in cols}
        row = (db.query(MappingMemory).filter(MappingMemory.tenant_id == tenant_id,
                                              MappingMemory.signature == signature).first())
        if row is None:
            db.add(MappingMemory(tenant_id=tenant_id, signature=signature, sheet_name=sheet, mapping=full))
        else:
            row.mapping = full
            row.sheet_name = sheet
            row.times_used = (row.times_used or 0) + 1


# ---------------------------------------------------------------------------
# Salud
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "CEBO_ATRAPADO", "mensaje": "EL BACKEND SI ME ESTA ESCUCHANDO", "version": ENGINE_VERSION}


@app.get("/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


# ---------------------------------------------------------------------------
# Administración (clave de servidor)
# ---------------------------------------------------------------------------
class TenantCreate(BaseModel):
    tenant_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]{2,40}$")
    name: str = Field(..., min_length=2, max_length=200)


@app.post("/api/v1/admin/tenants", dependencies=[Depends(require_admin)])
def create_tenant(body: TenantCreate, db: Session = Depends(get_db)):
    if db.get(Tenant, body.tenant_id):
        raise ApiError(409, "CLIENTE_EXISTE", "Ya existe un cliente con ese identificador.")
    raw = generate_api_key()
    db.add(Tenant(id=body.tenant_id, name=body.name))
    db.flush()
    db.add(TenantConfig(tenant_id=body.tenant_id))
    db.add(ApiKey(tenant_id=body.tenant_id, key_hash=hash_key(raw), prefix=raw[:8], label="principal"))
    db.commit()
    return {"tenant_id": body.tenant_id, "api_key": raw, "aviso": "Guarda esta clave ahora: no se volverá a mostrar."}


@app.post("/api/v1/admin/tenants/{tenant_id}/keys", dependencies=[Depends(require_admin)])
def issue_key(tenant_id: str, label: str = Query("rotacion", max_length=100), db: Session = Depends(get_db)):
    if not db.get(Tenant, tenant_id):
        raise ApiError(404, "CLIENTE_NO_ENCONTRADO", "No existe ese cliente.")
    raw = generate_api_key()
    db.add(ApiKey(tenant_id=tenant_id, key_hash=hash_key(raw), prefix=raw[:8], label=label))
    db.commit()
    return {"tenant_id": tenant_id, "api_key": raw, "aviso": "Guarda esta clave ahora: no se volverá a mostrar."}


@app.get("/api/v1/admin/tenants/{tenant_id}/keys", dependencies=[Depends(require_admin)])
def list_keys(tenant_id: str, db: Session = Depends(get_db)):
    rows = db.query(ApiKey).filter(ApiKey.tenant_id == tenant_id).order_by(ApiKey.id).all()
    return [{"id": k.id, "prefix": k.prefix, "label": k.label, "revoked": k.revoked,
             "created_at": _iso(k.created_at), "last_used_at": _iso(k.last_used_at)} for k in rows]


@app.post("/api/v1/admin/keys/{key_id}/revoke", dependencies=[Depends(require_admin)])
def revoke_key(key_id: int, db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if key is None:
        raise ApiError(404, "CLAVE_NO_ENCONTRADA", "No existe esa clave.")
    key.revoked = True
    db.commit()
    return {"id": key.id, "revoked": True}


# ---------------------------------------------------------------------------
# Cliente actual y configuración
# ---------------------------------------------------------------------------
@app.get("/api/v1/me")
def me(tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    return {"tenant_id": tenant_id, "name": tenant.name, "config": config_to_dict(get_or_create_config(db, tenant_id)),
            "engine_version": ENGINE_VERSION, "limits": {"max_upload_mb": settings.max_upload_mb}}


class ConfigUpdate(BaseModel):
    min_margin_percent: Optional[float] = Field(None, ge=0, le=100)
    z_score_threshold: Optional[float] = Field(None, ge=2, le=10)
    allow_negative_margin: Optional[bool] = None
    revenue_includes_vat: Optional[bool] = None
    vat_rate: Optional[float] = Field(None, ge=0, le=0.5)
    outlier_min_group: Optional[int] = Field(None, ge=5, le=100)
    reconciliation_tolerance_pct: Optional[float] = Field(None, ge=0, le=50)


@app.put("/api/v1/config")
def update_config(body: ConfigUpdate, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    row = get_or_create_config(db, tenant_id)
    for field in CONFIG_FIELDS:
        value = getattr(body, field)
        if value is None:
            continue
        setattr(row, field, int(value) if field in ("allow_negative_margin", "revenue_includes_vat") else value)
    db.commit()
    return config_to_dict(row)


# ---------------------------------------------------------------------------
# Entendimiento de datos y auditoría
# ---------------------------------------------------------------------------
@app.post("/api/v1/data-understanding")
def data_understanding(file: UploadFile = File(...), tenant_id: str = Depends(heavy_tenant),
                       db: Session = Depends(get_db)):
    name, data = read_upload(file)
    dfs = read_workbook(name, data, settings)
    memory = {m.signature: m.mapping for m in db.query(MappingMemory).filter(MappingMemory.tenant_id == tenant_id).all()}
    analysis = GenesisDataUnderstanding().analyze_workbook(dfs, memory_lookup=memory.get)
    analysis["file_sha256"] = _sha(data)
    return clean(analysis)


@app.post("/api/v1/audits")
def create_audit(file: UploadFile = File(...), mapping: str = Form(...), tenant_id: str = Depends(heavy_tenant),
                 db: Session = Depends(get_db)):
    name, data = read_upload(file)
    mapping_dict = parse_mapping(mapping)
    config = config_to_dict(get_or_create_config(db, tenant_id))
    file_hash, mapping_hash, config_hash = _sha(data), _json_sha(mapping_dict), _json_sha(config)

    dfs = read_workbook(name, data, settings)
    outcome = clean(run_pipeline(dfs, mapping_dict, config))

    calidad = outcome["calidad"]
    audit = AuditRecord(
        tenant_id=tenant_id, filename=name[:255], file_sha256=file_hash, mapping_sha256=mapping_hash,
        config_sha256=config_hash, engine_version=ENGINE_VERSION, estado=outcome["estado"],
        gate_level=calidad["nivelConfianza"], quality_score=calidad["data_quality_score"],
        analytical_confidence=calidad["analytical_confidence"], mapping=mapping_dict, config_snapshot=config,
        quality_report=calidad, financial_results=outcome["financials"], warnings=outcome["advertencias"],
        monthly=outcome["monthly"], merge_report=outcome["merge_report"])
    db.add(audit)
    db.flush()

    for f in outcome["findings"]:
        row = Finding(
            audit_id=audit.id, tenant_id=tenant_id, tipo=f["tipo"], prioridad=f["prioridad"], severidad=f["severidad"],
            titulo=f["titulo"], causa=f["causa"], impacto=f["impacto"], evidencia=f["evidencia"], accion=f["accion"],
            vehiculos=f["vehiculos_afectados"], casos=f["casos"], casos_total=f["casos_total"])
        db.add(row)
        db.flush()
        db.add(ActionTask(
            tenant_id=tenant_id, audit_id=audit.id, finding_id=row.id, department=f["accion"]["departamento"],
            title=f["titulo"], description=f["accion"]["accion"], financial_impact=f["impacto"]["impacto_directo"],
            urgency=f["accion"]["urgencia"]))

    if outcome["estado"] != "BLOQUEADA":
        remember_mapping(db, tenant_id, mapping_dict, dfs)
    db.commit()
    payload = audit_payload(db, audit)
    payload["reutilizado"] = False
    return clean(payload)


def _get_audit(db: Session, tenant_id: str, audit_id: int) -> AuditRecord:
    audit = db.query(AuditRecord).filter(AuditRecord.id == audit_id, AuditRecord.tenant_id == tenant_id).first()
    if audit is None:
        raise ApiError(404, "AUDITORIA_NO_ENCONTRADA", "No existe esa auditoría.")
    return audit


@app.get("/api/v1/audits")
def list_audits(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    q = db.query(AuditRecord).filter(AuditRecord.tenant_id == tenant_id)
    total = q.count()
    rows = q.order_by(AuditRecord.id.desc()).limit(limit).offset(offset).all()
    items = []
    for a in rows:
        fin = a.financial_results or {}
        items.append({"id": a.id, "filename": a.filename, "timestamp": _iso(a.timestamp), "estado": a.estado,
                      "nivel": a.gate_level, "totalIngresos": fin.get("totalIngresos"),
                      "margenGlobal": fin.get("margenGlobal"), "dineroEnRiesgo": fin.get("dineroEnRiesgo"),
                      "totalHallazgos": fin.get("totalHallazgos", 0)})
    return {"items": items, "total": total}


@app.get("/api/v1/audits/{audit_id}")
def get_audit(audit_id: int, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    return clean(audit_payload(db, _get_audit(db, tenant_id, audit_id)))


@app.get("/api/v1/audits/{audit_id}/export")
def export_audit(audit_id: int, tenant_id: str = Depends(heavy_tenant), db: Session = Depends(get_db)):
    payload = audit_payload(db, _get_audit(db, tenant_id, audit_id), cases_limit=None)
    content = build_audit_workbook(payload, payload["findings"], payload["tasks"])
    return Response(content=content, media_type=XLSX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="genesis_auditoria_{audit_id}.xlsx"'})


@app.delete("/api/v1/audits/{audit_id}")
def delete_audit(audit_id: int, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    audit = _get_audit(db, tenant_id, audit_id)
    db.query(ActionTask).filter(ActionTask.audit_id == audit.id, ActionTask.tenant_id == tenant_id).delete()
    db.query(Finding).filter(Finding.audit_id == audit.id, Finding.tenant_id == tenant_id).delete()
    db.delete(audit)
    db.commit()
    return {"deleted": audit_id}


# ---------------------------------------------------------------------------
# Tareas
# ---------------------------------------------------------------------------
@app.get("/api/v1/tasks")
def list_tasks(status: Optional[str] = Query(None), audit_id: Optional[int] = Query(None),
               limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
               tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    q = db.query(ActionTask).filter(ActionTask.tenant_id == tenant_id)
    if status:
        if status not in TASK_STATUSES:
            raise ApiError(422, "ESTADO_INVALIDO", f"Estado inválido. Usa: {', '.join(TASK_STATUSES)}.")
        q = q.filter(ActionTask.status == status)
    if audit_id is not None:
        q = q.filter(ActionTask.audit_id == audit_id)
    total = q.count()
    rows = q.order_by(ActionTask.financial_impact.desc(), ActionTask.id).limit(limit).offset(offset).all()
    return {"items": [task_dict(t) for t in rows], "total": total}


class TaskUpdate(BaseModel):
    status: Optional[Literal["PENDIENTE", "EN_PROCESO", "RESUELTA", "DESCARTADA"]] = None
    comment: Optional[str] = Field(None, max_length=2000)


@app.patch("/api/v1/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    task = db.query(ActionTask).filter(ActionTask.id == task_id, ActionTask.tenant_id == tenant_id).first()
    if task is None:
        raise ApiError(404, "TAREA_NO_ENCONTRADA", "No existe esa tarea.")
    if body.status is not None:
        task.status = body.status
    if body.comment is not None:
        task.comment = body.comment
    db.commit()
    return task_dict(task)
