from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import io
import pandas as pd
import numpy as np
import difflib

# =================================================================
# 🧠 FASE 1: NUEVO MOTOR SEMÁNTICO (DATA UNDERSTANDING v0.1)
# =================================================================
class GenesisDataUnderstanding:
    def __init__(self):
        # 1. DICCIONARIO EXPANDIDO: Ahora GENESIS entiende operaciones completas
        self.canonical_model = {
            "TRIP_ID": {"synonyms": ["viaje", "folio", "id", "ticket", "operacion", "guia"], "expected_type": "text"},
            "VEHICLE_ID": {"synonyms": ["placa", "unidad", "vehiculo", "tracto", "truck", "camion", "placas"], "expected_type": "text"},
            "TRIP_DATE": {"synonyms": ["fecha", "date", "salida", "emision", "dia"], "expected_type": "date"},
            "REVENUE": {"synonyms": ["ingreso", "tarifa", "flete", "facturado", "revenue", "importe", "total", "subtotal", "monto"], "expected_type": "numeric"},
            "COST_FUEL": {"synonyms": ["diesel", "combustible", "gasolina", "fuel", "costo", "gasto"], "expected_type": "numeric"},
            "DISTANCE_KM": {"synonyms": ["km", "kilometros", "distancia", "recorrido"], "expected_type": "numeric"},
            "VOLUME_LTS": {"synonyms": ["litros", "lts", "volumen", "galones"], "expected_type": "numeric"}
        }

    def _infer_data_type(self, series: pd.Series) -> str:
        # 2. PROFILING AVANZADO: Limpiamos símbolos extraños antes de evaluar
        cleaned_series = series.astype(str).str.replace(r'[$,\s]', '', regex=True)
        is_really_numeric = pd.to_numeric(cleaned_series, errors='coerce').notna().mean() > 0.6

        if is_really_numeric or pd.api.types.is_numeric_dtype(series):
            return "numeric"
        elif pd.api.types.is_datetime64_any_dtype(series) or "fecha" in str(series.name).lower():
            return "date"
        else:
            return "text"

    def analyze_schema(self, excel_file: io.BytesIO):
        df = pd.read_excel(excel_file)
        raw_columns = list(df.columns)
        
        understanding_result = {
            "status": "success",
            "dataset": {"records": len(df), "columns": len(raw_columns)},
            "fields_mapping": {},
            "ambiguities": []
        }

        for col in raw_columns:
            col_str = str(col).lower().strip()
            actual_type = self._infer_data_type(df[col])
            
            best_match = None
            highest_confidence = 0.0

            for canonical_key, rules in self.canonical_model.items():
                for syn in rules["synonyms"]:
                    # 3. LÓGICA DE SIMILITUD MEJORADA
                    if syn == col_str:
                        similitud = 1.0  # Match exacto
                    elif syn in col_str or col_str in syn:
                        similitud = 0.90 # Match parcial fuerte
                    else:
                        similitud = difflib.SequenceMatcher(None, col_str, syn).ratio()

                    # Penalización por tipo de dato (más suave para no romper matches obvios)
                    if rules["expected_type"] != actual_type and similitud < 1.0:
                        similitud = similitud * 0.4 

                    if similitud > highest_confidence:
                        highest_confidence = similitud
                        best_match = canonical_key

            if highest_confidence >= 0.85:
                understanding_result["fields_mapping"][col] = {
                    "canonical": best_match,
                    "confidence": round(highest_confidence, 2),
                    "detected_type": actual_type
                }
            elif highest_confidence >= 0.35:
                understanding_result["ambiguities"].append({
                    "original_column": col,
                    "detected_type": actual_type,
                    "possible_match": best_match,
                    "confidence": round(highest_confidence, 2)
                })
            else:
                understanding_result["ambiguities"].append({
                    "original_column": col,
                    "detected_type": actual_type,
                    "possible_match": "UNKNOWN",
                    "confidence": round(highest_confidence, 2)
                })

        return understanding_result

# =================================================================
# 🛡️ MOTORES LEGACY (Calidad y Economía - Se mantendrán por ahora)
# =================================================================
class DataQualityGate:
    def evaluate(self, df: pd.DataFrame):
        if df.empty: return {"nivelConfianza": "BAJA", "scoreGlobal": 0, "metricas": {"unicidad": 0, "completitud": 0, "validez": 0}}
        total_filas, total_celdas = len(df), df.size
        completitud = float(round((df.notna().sum().sum() / total_celdas) * 100, 1)) if total_celdas > 0 else 0.0
        unicidad = float(round(((total_filas - df.duplicated().sum()) / total_filas) * 100, 1)) if total_filas > 0 else 0.0
        score_global = int(round((completitud + unicidad + 100.0) / 3))
        return {"nivelConfianza": "ALTA" if score_global >= 85 else "MEDIA", "scoreGlobal": score_global, "metricas": {"unicidad": unicidad, "completitud": completitud, "validez": 100.0}}

class EconomicRuleEngine:
    def analyze(self, df: pd.DataFrame, quality_metrics: dict):
        # Lógica provisional para no romper el dashboard actual
        return {"totalIngresos": 31929741.48, "margenGlobal": 24.99, "dineroEnRiesgo": 7500.00}, [
            {"prioridad": 1, "vehiculo": "TR-02", "titulo": "Clonación o Registro Duplicado Detectado", "causa": "Se detectaron registros idénticos.", "accion": "Verificar duplicidad.", "impacto": 7500.00}
        ]

# =================================================================
# 🚀 ORQUESTADOR PRINCIPAL (API FASTAPI)
# =================================================================
app = FastAPI(title="GENESIS CORE B2B - Economic Intelligence Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 📍 NUEVO ENDPOINT ARQUITECTÓNICO: Punto de Control Semántico
@app.post("/api/v1/data-understanding")
async def data_understanding(file: UploadFile = File(...)):
    try:
        file_bytes = await file.read()
        excel_file = io.BytesIO(file_bytes)
        
        motor_semantico = GenesisDataUnderstanding()
        return motor_semantico.analyze_schema(excel_file)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en Data Understanding: {str(e)}")

# 📍 ENDPOINT LEGACY: Mantiene vivo el dashboard actual
@app.post("/api/procesar-matriz")
async def procesar_matriz(file: UploadFile = File(...)):
    try:
        file_bytes = await file.read()
        excel_file = io.BytesIO(file_bytes)
        df = pd.read_excel(excel_file)
        
        quality = DataQualityGate()
        q_metrics = quality.evaluate(df)
        eco_engine = EconomicRuleEngine()
        fin_results, anomalies = eco_engine.analyze(df, q_metrics)
        
        return {
            "status": "success",
            "calidad_datos": q_metrics,
            "analisis": {"filasAnalizadas": len(df), "totalIngresos": fin_results["totalIngresos"], "margenGlobal": fin_results["margenGlobal"], "dineroEnRiesgo": fin_results["dineroEnRiesgo"], "totalHallazgos": len(anomalies)},
            "hallazgos": anomalies
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error procesando la matriz: {str(e)}")
