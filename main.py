from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker
import io
import json
import pandas as pd
import numpy as np
import difflib
import datetime

# =================================================================
# 🗄️ CAPA DE PERSISTENCIA (Fase Gamma - Nivel 3)
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
    z_score_threshold = Column(Float, default=3.0)
    allow_negative_margin = Column(Integer, default=0)

# NUEVA TABLA: Workflow de Acciones (Action Engine)
class ActionTask(Base):
    __tablename__ = "action_tasks"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, index=True)
    audit_id = Column(Integer)
    department = Column(String) # Mantenimiento, Contabilidad, Operaciones
    title = Column(String)
    description = Column(String)
    financial_impact = Column(Float)
    status = Column(String, default="PENDIENTE") # PENDIENTE, EN_REVISION, CERRADO
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)


# =================================================================
# 🧠 FASE 1: MOTOR SEMÁNTICO (DATA UNDERSTANDING)
# =================================================================
class GenesisDataUnderstanding:
    def __init__(self):
        self.data_model = {
            "TRIP": {
                "TRIP_ID": {"type": "text", "synonyms": ["viaje", "folio", "id", "ticket", "operacion", "guia"]},
                "TRIP_DATE": {"type": "date", "synonyms": ["fecha", "salida", "emision", "dia"]},
                "DISTANCE_KM": {"type": "numeric", "synonyms": ["km", "kilometros", "distancia", "recorrido"]}
            },
            "VEHICLE": {
                "VEHICLE_ID": {"type": "text", "synonyms": ["placa", "unidad", "vehiculo", "tracto", "truck", "camion"]}
            },
            "CUSTOMER": {
                "CUSTOMER_NAME": {"type": "text", "synonyms": ["cliente", "empresa", "cuenta", "razon social"]}
            },
            "EXPENSE": {
                "REVENUE": {"type": "numeric", "synonyms": ["ingreso", "flete", "facturado", "total", "tarifa"]},
                "COST_FUEL": {"type": "numeric", "synonyms": ["diesel", "combustible", "gasolina", "costo", "gasto"]},
                "VOLUME_LTS": {"type": "numeric", "synonyms": ["litros", "lts", "volumen", "galones"]}
            }
        }

    def _infer_data_type(self, series: pd.Series) -> str:
        cleaned = series.astype(str).str.replace(r'[$,\s]', '', regex=True)
        is_num = pd.to_numeric(cleaned, errors='coerce').notna().mean() > 0.6
        if is_num or pd.api.types.is_numeric_dtype(series): return "numeric"
        elif pd.api.types.is_datetime64_any_dtype(series) or "fecha" in str(series.name).lower(): return "date"
        else: return "text"

    def analyze_workbook(self, dfs_dict: dict):
        workbook_result = {"status": "success", "sheets_detected": len(dfs_dict), "sheets_analysis": {}, "global_entities": [], "total_records": 0}
        for sheet_name, df in dfs_dict.items():
            if df.empty: continue
            workbook_result["total_records"] += len(df)
            raw_columns = list(df.columns)
            sheet_analysis = {"records": len(df), "columns_count": len(raw_columns), "fields_mapping": {}, "ambiguities": [], "entities_found": set()}
            canonical_competitors = {}

            for col in raw_columns:
                col_str = str(col).lower().strip()
                actual_type = self._infer_data_type(df[col])
                best_match, best_entity, highest_confidence = None, None, 0.0

                for entity, attributes in self.data_model.items():
                    for attr_key, rules in attributes.items():
                        for syn in rules["synonyms"]:
                            similitud = 1.0 if syn == col_str else (0.90 if syn in col_str or col_str in syn else difflib.SequenceMatcher(None, col_str, syn).ratio())
                            if rules["type"] != actual_type and similitud < 1.0: similitud *= 0.4 
                            if similitud > highest_confidence: highest_confidence, best_match, best_entity = similitud, attr_key, entity

                if highest_confidence >= 0.85:
                    if best_match not in canonical_competitors: canonical_competitors[best_match] = []
                    canonical_competitors[best_match].append({"col": col, "confidence": highest_confidence, "type": actual_type, "entity": best_entity})
                elif highest_confidence >= 0.35:
                    sheet_analysis["ambiguities"].append({"original_column": col, "detected_type": actual_type, "possible_match": best_match, "confidence": round(highest_confidence, 2)})
                else:
                    sheet_analysis["ambiguities"].append({"original_column": col, "detected_type": actual_type, "possible_match": "UNKNOWN", "confidence": round(highest_confidence, 2)})

            for canonical, competitors in canonical_competitors.items():
                if len(competitors) == 1:
                    comp = competitors[0]
                    sheet_analysis["fields_mapping"][comp["col"]] = {"canonical": canonical, "confidence": round(comp["confidence"], 2), "detected_type": comp["type"]}
                    sheet_analysis["entities_found"].add(comp["entity"])
                    if comp["entity"] not in workbook_result["global_entities"]: workbook_result["global_entities"].append(comp["entity"])
                else:
                    for comp in competitors:
                        sheet_analysis["ambiguities"].append({"original_column": comp["col"], "detected_type": comp["type"], "possible_match": canonical, "confidence": round(comp["confidence"], 2), "collision_warning": True})

            sheet_analysis["entities_found"] = list(sheet_analysis["entities_found"])
            workbook_result["sheets_analysis"][sheet_name] = sheet_analysis
        return workbook_result


# =================================================================
# 🛡️ DATA QUALITY vs ANALYTICAL CONFIDENCE
# =================================================================
class DataQualityGate:
    def evaluate(self, df: pd.DataFrame, dedup_subset=None):
        if df.empty: return {"nivelConfianza": "BAJA", "scoreGlobal": 0, "metricas": {"unicidad": 0, "completitud": 0}}
        total_filas, total_celdas = len(df), df.size
        completitud = float(round((df.notna().sum().sum() / total_celdas) * 100, 1)) if total_celdas > 0 else 0.0
        subset = dedup_subset if (dedup_subset and all(c in df.columns for c in dedup_subset)) else None
        unicidad = float(round(((total_filas - df.duplicated(subset=subset).sum()) / total_filas) * 100, 1)) if total_filas > 0 else 0.0
        quality_score = float(round((completitud + unicidad) / 2.0, 1))

        columns_present = set(df.columns)
        confidence_score = 100.0
        if "TRIP_ID" not in columns_present: confidence_score -= 40.0
        if "REVENUE" not in columns_present: confidence_score -= 30.0
        if "COST_FUEL" not in columns_present: confidence_score -= 20.0
        if "VEHICLE_ID" not in columns_present: confidence_score -= 10.0
        
        return {
            "scoreGlobal": quality_score,
            "data_quality_score": quality_score,
            "analytical_confidence": max(0.0, float(round(confidence_score, 1))),
            "nivelConfianza": "ALTA" if confidence_score >= 80 else "MEDIA" if confidence_score >= 50 else "BLOQUEADA",
            "metricas": {"unicidad": unicidad, "completitud": completitud}
        }


# =================================================================
# ⚖️ NIVEL 3: MOTORES EMPRESARIALES (EVIDENCE, IMPACT, ACTION)
# =================================================================
class EvidenceEngine:
    @staticmethod
    def generate(anom_type: str, base_val: float, obs_val: float, n_samples: int):
        confidence = min(99.0, 50.0 + (n_samples * 2.5)) if n_samples > 0 else 50.0
        desviacion = round(((obs_val - base_val) / base_val) * 100, 1) if base_val > 0 else 0.0
        return {
            "linea_base": round(base_val, 2),
            "valor_observado": round(obs_val, 2),
            "desviacion_pct": desviacion,
            "muestra_comparable": n_samples,
            "confianza_estadistica_pct": round(confidence, 1)
        }

class ImpactEngine:
    @staticmethod
    def project(impact_por_evento: float, eventos_mes: int = 4):
        # Proyección asumiendo frecuencia promedio B2B. Luego se conectará a BD histórica.
        impacto_mensual = impact_por_evento * eventos_mes
        impacto_anual = impacto_mensual * 12
        return {
            "impacto_directo": round(impact_por_evento, 2),
            "proyeccion_mensual": round(impacto_mensual, 2),
            "proyeccion_anual": round(impacto_anual, 2)
        }

class ActionEngine:
    @staticmethod
    def route(anom_type: str, vehiculo: str):
        if anom_type == "DUPLICADO":
            return {"departamento": "Contabilidad", "accion": "Revisar facturación doble en ERP para no emitir pagos/cobros duplicados.", "urgencia": "ALTA"}
        elif anom_type == "MARGEN_NEGATIVO":
            return {"departamento": "Pricing/Ventas", "accion": "Auditar estructura tarifaria o negociar ajuste de tarifa con el cliente.", "urgencia": "MEDIA"}
        elif anom_type == "OUTLIER":
            return {"departamento": "Operaciones", "accion": f"Auditar viaje atípico en vehículo {vehiculo} para descartar fraude o error de digitación.", "urgencia": "ALTA"}
        elif anom_type == "MARGEN_GLOBAL_BAJO":
            return {"departamento": "Dirección", "accion": "Revisión general de costos directos (Combustible/Peajes) vs Tarifario.", "urgencia": "CRÍTICA"}
        return {"departamento": "Auditoría", "accion": "Revisión general del hallazgo.", "urgencia": "BAJA"}


# =================================================================
# 💸 FASE 2: MOTOR ECONÓMICO RELACIONAL DINÁMICO (Orquestador)
# =================================================================
class EconomicRuleEngine:
    def _to_numeric(self, series: pd.Series) -> pd.Series:
        cleaned = series.astype(str).str.replace(r'[$,\s]', '', regex=True)
        return pd.to_numeric(cleaned, errors='coerce')

    def _rename_to_canonical(self, df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
        rename_dict, seen_canonical = {}, set()
        for original_col, canonical in mapping.items():
            if not canonical or canonical == "UNKNOWN": continue
            if canonical in seen_canonical: continue
            if original_col in df.columns:
                rename_dict[original_col] = canonical
                seen_canonical.add(canonical)
        return df.rename(columns=rename_dict)

    def _merge_sheets(self, dfs_dict: dict, mapping: dict):
        warnings, processed = [], []
        for sheet_name, df in dfs_dict.items():
            if df.empty: continue
            scoped_mapping = mapping.get(sheet_name, {})
            df_renamed = self._rename_to_canonical(df.copy(), scoped_mapping)
            canonical_cols = set(df_renamed.columns).intersection(set(scoped_mapping.values()))
            if canonical_cols: processed.append((sheet_name, df_renamed))
            else: warnings.append(f"La hoja '{sheet_name}' fue omitida (sin campos canónicos).")

        if not processed: return pd.DataFrame(), warnings

        base_name, base_df = None, None
        for i, (name, df) in enumerate(processed):
            if "TRIP_ID" in df.columns:
                base_name, base_df = processed.pop(i)
                break
        if base_df is None: base_name, base_df = processed.pop(0)

        for name, df in processed:
            common_keys = list(set(base_df.columns) & set(df.columns) & {"TRIP_ID", "VEHICLE_ID"})
            join_key = "TRIP_ID" if "TRIP_ID" in common_keys else ("VEHICLE_ID" if "VEHICLE_ID" in common_keys else None)
            if join_key is None:
                warnings.append(f"La hoja '{name}' no comparte llave. Omitida.")
                continue
            base_df = pd.merge(base_df, df, on=join_key, how="left", suffixes=("", f"__dup_{name}"))
            
        cols_to_keep = [c for c in base_df.columns if "__dup_" not in c]
        return base_df[cols_to_keep], warnings

    def analyze(self, master_df: pd.DataFrame, config: dict):
        anomalies = []
        has_revenue = "REVENUE" in master_df.columns
        has_cost = "COST_FUEL" in master_df.columns
        has_vehicle = "VEHICLE_ID" in master_df.columns
        has_trip_id = "TRIP_ID" in master_df.columns

        min_margin_target = config.get("min_margin_percent", 10.0)
        allow_neg = bool(config.get("allow_negative_margin", 0))

        if has_revenue: master_df["REVENUE"] = self._to_numeric(master_df["REVENUE"]).fillna(0)
        if has_cost: master_df["COST_FUEL"] = self._to_numeric(master_df["COST_FUEL"]).fillna(0)

        total_ingresos = float(master_df["REVENUE"].sum()) if has_revenue else 0.0
        total_costos = float(master_df["COST_FUEL"].sum()) if has_cost else 0.0
        margen_global = round(((total_ingresos - total_costos) / total_ingresos) * 100, 2) if (has_revenue and has_cost and total_ingresos > 0) else None

        dinero_en_riesgo = 0.0
        prioridad = 1

        # 1. Detección de Duplicados
        subset = ["TRIP_ID"] if has_trip_id else None
        dup_mask = master_df.duplicated(subset=subset, keep="first")
        if dup_mask.sum() > 0:
            impacto_dup = float(master_df.loc[dup_mask, "REVENUE"].sum()) if has_revenue else 0.0
            dinero_en_riesgo += impacto_dup
            vehiculos_afectados = ", ".join(sorted(set(master_df.loc[dup_mask, "VEHICLE_ID"].astype(str)))[:3]) if has_vehicle else "N/D"
            
            anomalies.append({
                "prioridad": prioridad, 
                "vehiculo": vehiculos_afectados, 
                "titulo": "Duplicidad Operativa", 
                "causa": f"{dup_mask.sum()} registros clonados.", 
                "evidencia": EvidenceEngine.generate("DUPLICADO", 0, impacto_dup, len(master_df)),
                "impacto": ImpactEngine.project(impacto_dup),
                "accion": ActionEngine.route("DUPLICADO", vehiculos_afectados)
            })
            prioridad += 1

        # 2. Detección de Margen Negativo
        if has_revenue and has_cost and not allow_neg:
            neg_mask = (master_df["COST_FUEL"] > master_df["REVENUE"]) & (master_df["REVENUE"] > 0)
            if neg_mask.sum() > 0:
                impacto_neg = float((master_df.loc[neg_mask, "COST_FUEL"] - master_df.loc[neg_mask, "REVENUE"]).sum())
                dinero_en_riesgo += impacto_neg
                vehiculos_neg = ", ".join(sorted(set(master_df.loc[neg_mask, "VEHICLE_ID"].astype(str)))[:3]) if has_vehicle else "N/D"
                
                anomalies.append({
                    "prioridad": prioridad, 
                    "vehiculo": vehiculos_neg, 
                    "titulo": "Margen Negativo (Pérdida Directa)", 
                    "causa": f"{neg_mask.sum()} viajes en pérdida.", 
                    "evidencia": EvidenceEngine.generate("MARGEN_NEGATIVO", total_ingresos / len(master_df), impacto_neg, len(master_df)),
                    "impacto": ImpactEngine.project(impacto_neg),
                    "accion": ActionEngine.route("MARGEN_NEGATIVO", vehiculos_neg)
                })
                prioridad += 1

        # 3. Detección Outliers Estadísticos
        if has_revenue and master_df["REVENUE"].std(ddof=0) > 0:
            mean_rev = master_df["REVENUE"].mean()
            z_scores = (master_df["REVENUE"] - mean_rev) / master_df["REVENUE"].std(ddof=0)
            outlier_mask = z_scores.abs() > 3
            if outlier_mask.sum() > 0:
                impacto_out = float(master_df.loc[outlier_mask, "REVENUE"].sum())
                vehiculos_out = ", ".join(sorted(set(master_df.loc[outlier_mask, "VEHICLE_ID"].astype(str)))[:3]) if has_vehicle else "N/D"
                
                anomalies.append({
                    "prioridad": prioridad, 
                    "vehiculo": vehiculos_out, 
                    "titulo": "Ingresos Atípicos (Outliers)", 
                    "causa": f"Desviación superior a 3 Sigmas.", 
                    "evidencia": EvidenceEngine.generate("OUTLIER", mean_rev, impacto_out, len(master_df)),
                    "impacto": ImpactEngine.project(impacto_out, 1), # Outliers proyectan menos mensual
                    "accion": ActionEngine.route("OUTLIER", vehiculos_out)
                })
                prioridad += 1

        financial_results = {"totalIngresos": round(total_ingresos, 2), "totalCostos": round(total_costos, 2), "margenGlobal": margen_global, "dineroEnRiesgo": round(dinero_en_riesgo, 2)}
        warnings = []
        if not has_revenue: warnings.append("Falta REVENUE.")
        if not has_cost: warnings.append("Falta COST_FUEL.")
        return financial_results, anomalies, warnings


# =================================================================
# 🚀 ORQUESTADOR PRINCIPAL (API FASTAPI)
# =================================================================
app = FastAPI(title="GENESIS CORE B2B - Nivel 3 Engine")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def _read_excel_or_csv_multisheet(filename: str, file_bytes: bytes) -> dict:
    buf = io.BytesIO(file_bytes)
    if filename.lower().endswith(".csv"): return {"Hoja1": pd.read_csv(buf)}
    return pd.read_excel(buf, sheet_name=None)

@app.post("/api/v1/data-understanding")
async def data_understanding(file: UploadFile = File(...)):
    try:
        file_bytes = await file.read()
        dfs_dict = _read_excel_or_csv_multisheet(file.filename, file_bytes)
        return GenesisDataUnderstanding().analyze_workbook(dfs_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/api/v1/tenant-config")
async def get_tenant_config(x_tenant_id: str = Header(default="DEFAULT_TENANT")):
    db = SessionLocal()
    try:
        cfg = db.query(TenantConfig).filter(TenantConfig.tenant_id == x_tenant_id).first()
        if not cfg:
            cfg = TenantConfig(tenant_id=x_tenant_id)
            db.add(cfg)
            db.commit()
            db.refresh(cfg)
        return {"tenant_id": cfg.tenant_id, "min_margin_percent": cfg.min_margin_percent, "allow_negative_margin": cfg.allow_negative_margin}
    finally:
        db.close()

@app.post("/api/procesar-matriz")
async def procesar_matriz(file: UploadFile = File(...), mapping: str = Form(None), x_tenant_id: str = Header(default="DEFAULT_TENANT")):
    try:
        file_bytes = await file.read()
        dfs_dict = _read_excel_or_csv_multisheet(file.filename, file_bytes)
        mapping_dict = json.loads(mapping) if mapping else {}

        db = SessionLocal()
        try:
            cfg = db.query(TenantConfig).filter(TenantConfig.tenant_id == x_tenant_id).first()
            tenant_rules = {"min_margin_percent": cfg.min_margin_percent if cfg else 10.0, "allow_negative_margin": cfg.allow_negative_margin if cfg else 0}
        finally:
            db.close()

        eco_engine = EconomicRuleEngine()
        master_df, merge_warnings = eco_engine._merge_sheets(dfs_dict, mapping_dict)
        preview_data = master_df.head(5).fillna("").to_dict(orient="records") if not master_df.empty else []

        quality_gate = DataQualityGate()
        dedup_key = ["TRIP_ID"] if "TRIP_ID" in master_df.columns else None
        q_metrics = quality_gate.evaluate(master_df, dedup_subset=dedup_key)

        fin_results, anomalies, analysis_warnings = eco_engine.analyze(master_df, config=tenant_rules)

        db = SessionLocal()
        try:
            # 1. Guardar Auditoría
            audit_log = AuditRecord(
                tenant_id=x_tenant_id, filename=file.filename, quality_score=q_metrics["data_quality_score"],
                analytical_confidence=q_metrics["analytical_confidence"], financial_results=fin_results, anomalies=anomalies
            )
            db.add(audit_log)
            db.flush() # Obtener ID antes del commit
            
            # 2. Guardar Tareas (Workflow / Action Engine)
            for anom in anomalies:
                task = ActionTask(
                    tenant_id=x_tenant_id, audit_id=audit_log.id, department=anom["accion"]["departamento"],
                    title=anom["titulo"], description=anom["accion"]["accion"], financial_impact=anom["impacto"]["proyeccion_anual"]
                )
                db.add(task)
            
            db.commit()
        finally:
            db.close()

        return {
            "status": "success", "tenant_id": x_tenant_id, "calidad_datos": q_metrics,
            "advertencias": merge_warnings + analysis_warnings, "preview_join": preview_data,
            "analisis": {
                "totalIngresos": fin_results["totalIngresos"], "margenGlobal": fin_results["margenGlobal"],
                "dineroEnRiesgo": fin_results["dineroEnRiesgo"], "totalHallazgos": len(anomalies)
            },
            "hallazgos": anomalies # Ahora el frontend recibirá Evidencia, Impacto y Acción detallada.
        }
    except Exception as e: 
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")
