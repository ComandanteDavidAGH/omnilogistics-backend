"""Persistencia. Funciona con PostgreSQL (producción) y SQLite (desarrollo/pruebas)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, create_engine, text)
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import get_settings

settings = get_settings()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autoflush=False, bind=engine, expire_on_commit=False)
Base = declarative_base()


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(String(60), primary_key=True)
    name = Column(String(200), nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ApiKey(Base):
    """Solo se guarda el hash SHA-256 de la clave; la clave en claro se muestra una única vez al crearla."""
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(60), ForeignKey("tenants.id"), index=True, nullable=False)
    key_hash = Column(String(64), unique=True, index=True, nullable=False)
    prefix = Column(String(12), nullable=False)
    label = Column(String(100))
    revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    last_used_at = Column(DateTime(timezone=True))


class TenantConfig(Base):
    __tablename__ = "tenant_configs"
    tenant_id = Column(String(60), ForeignKey("tenants.id"), primary_key=True)
    min_margin_percent = Column(Float, default=10.0, nullable=False)
    z_score_threshold = Column(Float, default=3.5, nullable=False)
    allow_negative_margin = Column(Integer, default=0, nullable=False)
    revenue_includes_vat = Column(Integer, default=0, nullable=False)
    vat_rate = Column(Float, default=0.19, nullable=False)
    outlier_min_group = Column(Integer, default=8, nullable=False)
    reconciliation_tolerance_pct = Column(Float, default=1.0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MappingMemory(Base):
    """Decisiones de mapeo confirmadas por el cliente, por firma de encabezados."""
    __tablename__ = "mapping_memory"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(60), ForeignKey("tenants.id"), nullable=False)
    signature = Column(String(64), nullable=False)
    sheet_name = Column(String(200))
    mapping = Column(JSON, nullable=False)
    times_used = Column(Integer, default=1, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "signature", name="uq_mapping_tenant_signature"),)


class AuditRecord(Base):
    __tablename__ = "audit_records"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(60), ForeignKey("tenants.id"), index=True, nullable=False)
    filename = Column(String(255))
    file_sha256 = Column(String(64), nullable=False)
    mapping_sha256 = Column(String(64), nullable=False)
    config_sha256 = Column(String(64), nullable=False)
    engine_version = Column(String(20), nullable=False)
    timestamp = Column(DateTime(timezone=True), default=utcnow, index=True)
    estado = Column(String(20), nullable=False)           # OK | CON_RESERVAS | BLOQUEADA
    gate_level = Column(String(12))
    quality_score = Column(Float)
    analytical_confidence = Column(Float)
    mapping = Column(JSON)
    config_snapshot = Column(JSON)
    quality_report = Column(JSON)
    financial_results = Column(JSON)
    warnings = Column(JSON)
    monthly = Column(JSON)
    merge_report = Column(JSON)
    __table_args__ = (Index("ix_audit_dedupe", "tenant_id", "file_sha256", "mapping_sha256", "config_sha256"),)


class Finding(Base):
    __tablename__ = "findings"
    id = Column(Integer, primary_key=True)
    audit_id = Column(Integer, ForeignKey("audit_records.id"), index=True, nullable=False)
    tenant_id = Column(String(60), index=True, nullable=False)
    tipo = Column(String(40), nullable=False)
    prioridad = Column(Integer, nullable=False)
    severidad = Column(String(10), nullable=False)
    titulo = Column(String(255), nullable=False)
    causa = Column(Text)
    impacto = Column(JSON)
    evidencia = Column(JSON)
    accion = Column(JSON)
    vehiculos = Column(JSON)
    casos = Column(JSON)
    casos_total = Column(Integer, default=0)


class ActionTask(Base):
    __tablename__ = "action_tasks"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(60), index=True, nullable=False)
    audit_id = Column(Integer, ForeignKey("audit_records.id"), index=True, nullable=False)
    finding_id = Column(Integer, ForeignKey("findings.id"))
    department = Column(String(80))
    title = Column(String(255))
    description = Column(Text)
    financial_impact = Column(Float, default=0.0)
    urgency = Column(String(10))
    status = Column(String(20), default="PENDIENTE", nullable=False)
    comment = Column(Text)
    resolved_at = Column(DateTime(timezone=True), nullable=True)     # <--- Fecha de resolución (NUEVO)
    recovered_amount = Column(Float, nullable=True)                 # <--- Dinero recuperado (NUEVO)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def _ensure_pilot_columns() -> None:
    """Añade columnas nuevas a PostgreSQL en Render automáticamente sin borrar ni afectar datos anteriores."""
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE action_tasks 
              ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP WITH TIME ZONE,
              ADD COLUMN IF NOT EXISTS recovered_amount DOUBLE PRECISION;
        """))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_pilot_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
