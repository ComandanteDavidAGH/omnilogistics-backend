import json
import datetime
from fastapi import FastAPI, UploadFile, File, Form, Header, Request, HTTPException, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker

# Importación de módulos del núcleo
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.ingestion import read_workbook
from app.core.understanding import GenesisDataUnderstanding
from app.core.merge import build_master
from app.core.quality import DataQualityGate
from app.core.engine import EconomicRuleEngine
from app.core.cleaner import clean

settings = get_settings()

# =================================================================
# 🗄️ CAPA DE PERSISTENCIA
# =================================================================
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class AuditRecord(Base):
    __tablename__ = "audit_records"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, index=True)
    filename = Column(String)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    quality_score = Column(Float)
    analytical_confidence = Column(Float)
    financial_results = Column(JSON)
    anomalies = Column(JSON)

class TenantConfig(Base):
    __tablename__ = "tenant_configs"
    tenant_id = Column(String, primary_key=True, index=True)
    min_margin_percent = Column(Float, default=10.0)
    z_score_threshold = Column(Float, default=3.5)
    allow_negative_margin = Column(Integer, default=0)

class TenantMapping(Base):
    __tablename__ = "tenant_mappings"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, index=True)
    signature = Column(String, index=True)
    mapping_data = Column(JSON)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class ActionTask(Base):
    __tablename__ = "action_tasks"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, index=True)
    audit_id = Column(Integer)
    department = Column(String)
    title = Column(String)
    description = Column(String)
    financial_impact = Column(Float)
    status = Column(String, default="PENDIENTE")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)

# =================================================================
# 🚀 APLICACIÓN FASTAPI
# =================================================================
app = FastAPI(title="GENESIS CORE B2B - Enterprise Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status,
        content={"code": exc.code, "detail": exc.message}
    )

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "engine_version": settings.ENGINE_VERSION,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }

@app.post("/api/v1/data-understanding")
async def data_understanding(
    file: UploadFile = File(...),
    x_tenant_id: str = Header(default="DEFAULT_TENANT")
):
    contents = await file.read()
    dfs = read_workbook(file.filename, contents, settings)
    
    db = SessionLocal()
    try:
        def memory_lookup(sig: str):
            record = db.query(TenantMapping).filter(
                TenantMapping.tenant_id == x_tenant_id,
                TenantMapping.signature == sig
            ).first()
            return record.mapping_data if record else None

        understanding = GenesisDataUnderstanding()
        result = understanding.analyze_workbook(dfs, memory_lookup=memory_lookup)
        return clean(result)
    finally:
        db.close()

@app.post("/api/procesar-matriz")
async def procesar_matriz(
    file: UploadFile = File(...),
    mapping: str = Form(None),
    x_tenant_id: str = Header(default="DEFAULT_TENANT")
):
    contents = await file.read()
    dfs = read_workbook(file.filename, contents, settings)
    
    mapping_dict = {}
    if mapping:
        try:
            mapping_dict = json.loads(mapping)
        except Exception:
            raise ApiError(400, "MAPPING_INVALIDO", "El parámetro 'mapping' debe ser un JSON válido.")

    db = SessionLocal()
    try:
        # 1. Recuperar o guardar configuración del tenant
        cfg = db.query(TenantConfig).filter(TenantConfig.tenant_id == x_tenant_id).first()
        tenant_rules = {
            "min_margin_percent": cfg.min_margin_percent if cfg else 10.0,
            "z_score_threshold": cfg.z_score_threshold if cfg else 3.5,
            "allow_negative_margin": cfg.allow_negative_margin if cfg else 0
        }

        # 2. Guardar decisiones de mapeo en la memoria institucional (TenantMapping)
        for sheet_name, sheet_analysis in GenesisDataUnderstanding().analyze_workbook(dfs).get("sheets_analysis", {}).items():
            sig = sheet_analysis.get("signature")
            scoped_map = mapping_dict.get(sheet_name)
            if sig and scoped_map:
                existing = db.query(TenantMapping).filter(
                    TenantMapping.tenant_id == x_tenant_id,
                    TenantMapping.signature == sig
                ).first()
                if existing:
                    existing.mapping_data = scoped_map
                    existing.updated_at = datetime.datetime.utcnow()
                else:
                    db.add(TenantMapping(tenant_id=x_tenant_id, signature=sig, mapping_data=scoped_map))
        db.commit()

        # 3. Ensamblar la matriz unificada (Merge seguro sin duplicación de filas)
        merge_result = build_master(dfs, mapping_dict)

        # 4. Evaluar la compuerta de calidad de datos
        quality_gate = DataQualityGate()
        q_metrics = quality_gate.evaluate(merge_result.master, merge_result.parse_stats, merge_result.report)

        # Si hay un bloqueo por calidad crítica, detener la ejecución y retornar causas
        if q_metrics.get("bloqueante"):
            return clean({
                "status": "blocked",
                "tenant_id": x_tenant_id,
                "calidad_datos": q_metrics,
                "advertencias": merge_result.warnings,
                "mensajes": q_metrics["motivos"]
            })

        # 5. Ejecutar el motor económico
        rule_engine = EconomicRuleEngine(config=tenant_rules)
        analysis_output = rule_engine.analyze(merge_result.master, merge_result)

        all_warnings = merge_result.warnings + analysis_output["warnings"]

        # 6. Registrar la auditoría y generar tareas operativas
        audit_log = AuditRecord(
            tenant_id=x_tenant_id,
            filename=file.filename,
            quality_score=q_metrics["data_quality_score"],
            analytical_confidence=q_metrics["analytical_confidence"],
            financial_results=analysis_output["financials"],
            anomalies=analysis_output["findings"]
        )
        db.add(audit_log)
        db.flush()

        for f in analysis_output["findings"]:
            task = ActionTask(
                tenant_id=x_tenant_id,
                audit_id=audit_log.id,
                department=f["accion"]["departamento"],
                title=f["titulo"],
                description=f["accion"]["accion"],
                financial_impact=f["impacto"]["impacto_directo"]
            )
            db.add(task)

        db.commit()

        # Generar la vista previa del cruce (primeras 5 filas)
        preview_data = merge_result.master.head(5).fillna("").to_dict(orient="records") if not merge_result.master.empty else []

        return clean({
            "status": "success",
            "tenant_id": x_tenant_id,
            "calidad_datos": q_metrics,
            "advertencias": all_warnings,
            "preview_join": preview_data,
            "financials": analysis_output["financials"],
            "findings": analysis_output["findings"],
            "monthly": analysis_output.get("monthly", [])
        })

    finally:
        db.close()

@app.get("/api/v1/action-tasks")
async def get_action_tasks(status: str = None, x_tenant_id: str = Header(default="DEFAULT_TENANT")):
    db = SessionLocal()
    try:
        query = db.query(ActionTask).filter(ActionTask.tenant_id == x_tenant_id)
        if status:
            query = query.filter(ActionTask.status == status)
        tasks = query.order_by(ActionTask.created_at.desc()).all()
        return clean([
            {
                "id": t.id,
                "audit_id": t.audit_id,
                "department": t.department,
                "title": t.title,
                "description": t.description,
                "financial_impact": t.financial_impact,
                "status": t.status,
                "created_at": t.created_at
            }
            for t in tasks
        ])
    finally:
        db.close()

@app.patch("/api/v1/action-tasks/{task_id}")
async def update_task_status(
    task_id: int,
    new_status: str = Body(..., embed=True),
    x_tenant_id: str = Header(default="DEFAULT_TENANT")
):
    db = SessionLocal()
    try:
        task = db.query(ActionTask).filter(ActionTask.id == task_id, ActionTask.tenant_id == x_tenant_id).first()
        if not task:
            raise ApiError(404, "TAREA_NO_ENCONTRADA", f"La tarea con ID {task_id} no existe.")
        task.status = new_status
        db.commit()
        return {"status": "success", "task_id": task_id, "updated_status": new_status}
    finally:
        db.close()
