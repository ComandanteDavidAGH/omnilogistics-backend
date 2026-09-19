import io
import json
import re
import math
import unicodedata
import datetime
import pandas as pd
import numpy as np
from typing import Any
from fastapi import FastAPI, UploadFile, File, Form, Header, Request, HTTPException, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker

# =================================================================
# 🗄️ CAPA DE PERSISTENCIA (SQLAlchemy)
# =================================================================
DATABASE_URL = "sqlite:///./genesis_b2b.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
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
# 🧠 MODELO DE DATOS CANÓNICO Y LIMPIEZA
# =================================================================
FIELDS = {
    "TRIP_ID": {"entity": "TRIP", "type": "text", "label": "ID de Viaje / Folio", "synonyms": ["viaje", "folio", "ticket", "operacion", "guia", "manifiesto", "id viaje"]},
    "TRIP_DATE": {"entity": "TRIP", "type": "date", "label": "Fecha del Viaje", "synonyms": ["fecha", "salida", "emision", "fecha viaje"]},
    "DISTANCE_KM": {"entity": "TRIP", "type": "numeric", "label": "Distancia (Km)", "synonyms": ["km", "kilometros", "distancia", "recorrido"]},
    "VEHICLE_ID": {"entity": "VEHICLE", "type": "text", "label": "Vehículo / Placa", "synonyms": ["placa", "unidad", "vehiculo", "tracto", "truck", "camion"]},
    "DRIVER_ID": {"entity": "DRIVER", "type": "text", "label": "Conductor", "synonyms": ["conductor", "chofer", "operador", "driver"]},
    "ROUTE_NAME": {"entity": "ROUTE", "type": "text", "label": "Ruta / Trayecto", "synonyms": ["ruta", "trayecto", "tramo", "origen destino"]},
    "CUSTOMER_NAME": {"entity": "CUSTOMER", "type": "text", "label": "Cliente", "synonyms": ["cliente", "razon social", "cuenta"]},
    "REVENUE": {"entity": "FINANCE", "type": "numeric", "label": "Ingreso / Flete Facturado", "synonyms": ["ingreso", "flete", "facturado", "tarifa", "valor flete", "total facturado"]},
    "COST_FUEL": {"entity": "COST", "type": "numeric", "label": "Costo de Combustible", "synonyms": ["combustible", "diesel", "acpm", "gasolina", "tanqueo"]},
    "COST_TOLL": {"entity": "COST", "type": "numeric", "label": "Costo de Peajes", "synonyms": ["peaje", "peajes", "costo peaje"]},
    "COST_MAINT": {"entity": "COST", "type": "numeric", "label": "Costo de Mantenimiento", "synonyms": ["mantenimiento", "repuesto", "taller", "reparacion"]},
    "COST_DRIVER": {"entity": "COST", "type": "numeric", "label": "Costo de Conductor / Viáticos", "synonyms": ["viatico", "pago conductor", "viaticos"]},
    "COST_OTHER": {"entity": "COST", "type": "numeric", "label": "Otros Costos Directos", "synonyms": ["otros costos", "gastos varios"]},
    "COST_TOTAL": {"entity": "COST", "type": "numeric", "label": "Costo Total Operacional", "synonyms": ["costo total", "total costos", "gasto total"]},
    "VOLUME_LTS": {"entity": "FUEL", "type": "numeric", "label": "Volumen Combustible (Lts)", "synonyms": ["litros", "lts", "volumen litros"]},
    "VOLUME_GAL": {"entity": "FUEL", "type": "numeric", "label": "Volumen Combustible (Gal)", "synonyms": ["galones", "galon", "gls"]}
}

def clean_value(obj):
    if obj is None or obj is pd.NaT or obj is pd.NA: return None
    if isinstance(obj, (bool, np.bool_)): return bool(obj)
    if isinstance(obj, (int, np.integer)): return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, (pd.Timestamp, datetime.datetime, datetime.date)): return obj.isoformat()
    if isinstance(obj, dict): return {str(k): clean_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)): return [clean_value(v) for v in obj]
    return str(obj)

# =================================================================
# 🧠 MOTOR ANALÍTICO Y COMPONENTES CORE
# =================================================================
class GenesisDataUnderstanding:
    def analyze_workbook(self, dfs_dict: dict):
        result = {"status": "success", "sheets_detected": len(dfs_dict), "sheets_analysis": {}, "global_entities": [], "total_records": 0}
        for sheet_name, df in dfs_dict.items():
            if df.empty: continue
            result["total_records"] += len(df)
            raw_columns = list(df.columns)
            sheet_analysis = {"records": len(df), "columns_count": len(raw_columns), "fields_mapping": {}, "ambiguities": [], "entities_found": set()}
            
            for col in raw_columns:
                col_lower = str(col).lower().strip()
                matched_canonical = None
                highest_score = 0.0
                
                for canon_id, spec in FIELDS.items():
                    for syn in spec["synonyms"]:
                        if syn == col_lower:
                            highest_score, matched_canonical = 1.0, canon_id
                            break
                        elif syn in col_lower or col_lower in syn:
                            if 0.85 > highest_score:
                                highest_score, matched_canonical = 0.85, canon_id
                                
                if highest_score >= 0.85:
                    sheet_analysis["fields_mapping"][col] = {"canonical": matched_canonical, "confidence": highest_score, "detected_type": FIELDS[matched_canonical]["type"]}
                    sheet_analysis["entities_found"].add(FIELDS[matched_canonical]["entity"])
                    if FIELDS[matched_canonical]["entity"] not in result["global_entities"]:
                        result["global_entities"].append(FIELDS[matched_canonical]["entity"])
                else:
                    sheet_analysis["ambiguities"].append({
                        "original_column": col, "detected_type": "text", "possible_match": matched_canonical or "UNKNOWN",
                        "confidence": round(highest_score, 2), "requires_decision": True
                    })
            sheet_analysis["entities_found"] = list(sheet_analysis["entities_found"])
            result["sheets_analysis"][sheet_name] = sheet_analysis
        return result

class DataQualityGate:
    def evaluate(self, df: pd.DataFrame):
        if df.empty: return {"nivelConfianza": "BLOQUEADA", "bloqueante": True, "data_quality_score": 0.0, "analytical_confidence": 0.0}
        cols = set(df.columns)
        has_rev = "REVENUE" in cols
        has_cost = any(c.startswith("COST_") for c in cols)
        
        confidence = 100.0
        if not has_rev: confidence -= 35.0
        if not has_cost: confidence -= 25.0
        if "TRIP_ID" not in cols: confidence -= 20.0
        
        quality = float(round((df.notna().sum().sum() / max(1, df.size)) * 100, 1))
        return {
            "data_quality_score": quality,
            "analytical_confidence": max(0.0, float(round(confidence, 1))),
            "bloqueante": False,
            "nivelConfianza": "ALTA" if confidence >= 80 else "MEDIA" if confidence >= 50 else "BAJA"
        }

class EconomicRuleEngine:
    def _merge_sheets(self, dfs_dict: dict, mapping: dict):
        warnings, processed = [], []
        for sheet_name, df in dfs_dict.items():
            if df.empty: continue
            scoped_map = mapping.get(sheet_name, {})
            rename_dict = {orig: canon for orig, canon in scoped_map.items() if canon and canon != "UNKNOWN" and orig in df.columns}
            df_renamed = df.rename(columns=rename_dict)
            processed.append((sheet_name, df_renamed))
            
        if not processed: return pd.DataFrame(), warnings
        
        base_df = processed[0][1]
        for name, df in processed[1:]:
            common_keys = list(set(base_df.columns) & set(df.columns) & {"TRIP_ID", "VEHICLE_ID"})
            if not common_keys:
                warnings.append(f"La hoja '{name}' no comparte llaves con la hoja base. Se omitió del cruce.")
                continue
            join_key = "TRIP_ID" if "TRIP_ID" in common_keys else "VEHICLE_ID"
            base_df = pd.merge(base_df, df, on=join_key, how="left", suffixes=("", f"__dup_{name}"))
            
        cols_to_keep = [c for c in base_df.columns if "__dup_" not in c]
        merged = base_df[cols_to_keep]
        # Desduplicar nombres de columnas si hubo colisiones en el join
        merged = merged.loc[:, ~merged.columns.duplicated()].copy()
        return merged, warnings

    def analyze(self, master_df: pd.DataFrame, config: dict):
        anomalies, warnings = [], []
        
        # Garantizar que no existan columnas duplicadas con el mismo nombre
        master_df = master_df.loc[:, ~master_df.columns.duplicated()].copy()
        
        target_cols = ["REVENUE", "COST_FUEL", "COST_TOLL", "COST_MAINT", "COST_DRIVER", "COST_OTHER", "COST_TOTAL"]
        for col in master_df.columns:
            if col in target_cols:
                series_val = master_df[col]
                if isinstance(series_val, pd.DataFrame):
                    series_val = series_val.iloc[:, 0]
                cleaned = series_val.astype(str).str.replace(r'[$,\s]', '', regex=True)
                master_df[col] = pd.to_numeric(cleaned, errors='coerce')
                
        has_rev = "REVENUE" in master_df.columns
        tot_rev = float(master_df["REVENUE"].sum()) if has_rev else 0.0
        cost_cols = [c for c in master_df.columns if c.startswith("COST_")]
        tot_cost = float(master_df[cost_cols].sum().sum()) if cost_cols else 0.0
        
        margen = round(((tot_rev - tot_cost) / tot_rev) * 100, 2) if (has_rev and tot_rev > 0) else None
        dinero_riesgo = 0.0
        
        # Detección de Margen Negativo
        if has_rev and cost_cols:
            master_df["_TOTAL_COST"] = master_df[cost_cols].sum(axis=1)
            neg_mask = (master_df["_TOTAL_COST"] > master_df["REVENUE"]) & (master_df["REVENUE"] > 0)
            if neg_mask.sum() > 0:
                impacto_neg = float((master_df.loc[neg_mask, "_TOTAL_COST"] - master_df.loc[neg_mask, "REVENUE"]).sum())
                dinero_riesgo += impacto_neg
                anomalies.append({
                    "prioridad": 1, "titulo": "Margen Negativo (Pérdida Directa)",
                    "causa": f"Se identificaron {neg_mask.sum()} viajes con costo total mayor al ingreso.",
                    "impacto": {"impacto_directo": round(impacto_neg, 2)},
                    "evidencia": {"segmento_analizado": "Global", "nivel_evidencia": "RIGUROSA", "registros_afectados": int(neg_mask.sum()), "registros_poblacion": len(master_df)},
                    "accion": {"departamento": "Pricing/Ventas", "accion": "Auditar tarifas contractuales vs costos directos.", "urgencia": "ALTA"}
                })

        financials = {
            "totalIngresos": round(tot_rev, 2), "totalCostos": round(tot_cost, 2),
            "margenGlobal": margen, "dineroEnRiesgo": round(dinero_riesgo, 2),
            "filasAnalizadas": len(master_df)
        }
        return {"financials": financials, "findings": anomalies, "warnings": warnings}

# =================================================================
# 🚀 API FASTAPI ORQUESTADORA
# =================================================================
app = FastAPI(title="GENESIS CORE B2B - Unified Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _read_workbook_bytes(filename: str, file_bytes: bytes) -> dict:
    buf = io.BytesIO(file_bytes)
    if filename.lower().endswith(".csv"):
        return {"Hoja1": pd.read_csv(buf)}
    return pd.read_excel(buf, sheet_name=None)

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "1.0.1-FixDuplicatedCols", "timestamp": datetime.datetime.utcnow().isoformat()}

@app.post("/api/v1/data-understanding")
async def data_understanding(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        dfs = _read_workbook_bytes(file.filename, contents)
        understanding = GenesisDataUnderstanding()
        return clean_value(understanding.analyze_workbook(dfs))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en comprensión de datos: {str(e)}")

@app.post("/api/procesar-matriz")
async def procesar_matriz(
    file: UploadFile = File(...),
    mapping: str = Form(None),
    x_tenant_id: str = Header(default="DEFAULT_TENANT")
):
    try:
        contents = await file.read()
        dfs = _read_workbook_bytes(file.filename, contents)
        
        mapping_dict = {}
        if mapping:
            try:
                mapping_dict = json.loads(mapping)
            except Exception:
                raise HTTPException(status_code=400, detail="El parámetro 'mapping' debe ser un JSON válido.")

        rule_engine = EconomicRuleEngine()
        master_df, merge_warnings = rule_engine._merge_sheets(dfs, mapping_dict)
        
        quality_gate = DataQualityGate()
        q_metrics = quality_gate.evaluate(master_df)

        db = SessionLocal()
        try:
            cfg = db.query(TenantConfig).filter(TenantConfig.tenant_id == x_tenant_id).first()
            tenant_rules = {"min_margin_percent": cfg.min_margin_percent if cfg else 10.0}
        finally:
            db.close()

        analysis_output = rule_engine.analyze(master_df, config=tenant_rules)
        all_warnings = merge_warnings + analysis_output["warnings"]

        preview_data = master_df.head(5).fillna("").to_dict(orient="records") if not master_df.empty else []

        return clean_value({
            "status": "success",
            "tenant_id": x_tenant_id,
            "calidad_datos": q_metrics,
            "advertencias": all_warnings,
            "preview_join": preview_data,
            "financials": analysis_output["financials"],
            "findings": analysis_output["findings"]
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")
