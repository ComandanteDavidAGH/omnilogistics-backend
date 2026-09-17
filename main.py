from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import io

# Importación de los 3 motores base
from semantic_engine import SemanticMapper
from quality_gate import DataQualityGate
from economic_engine import EconomicRuleEngine

app = FastAPI(title="GENESIS CORE B2B - Economic Intelligence Engine")

# Configuración de CORS para permitir la conexión desde Vercel / Codespaces
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/procesar-matriz")
async def procesar_matriz(file: UploadFile = File(...)):
    """
    Endpoint principal de auditoría multi-pestaña orquestado.
    """
    try:
        # Leer el archivo Excel cargado en memoria
        file_bytes = await file.read()
        excel_file = io.BytesIO(file_bytes)
        
        # --- ESCALÓN 1: DATA UNDERSTANDING (Mapeo Semántico) ---
        mapper = SemanticMapper()
        mapped_data, mapping_report = mapper.map_entities(excel_file)
        
        # --- ESCALÓN 2: DATA QUALITY GATE (Control de Calidad) ---
        quality = DataQualityGate()
        quality_metrics = quality.evaluate(mapped_data)
        
        # --- ESCALÓN 3: MOTOR ECONÓMICO (Reglas B2B y Fugas) ---
        economic_engine = EconomicRuleEngine()
        financial_results, anomalies = economic_engine.analyze(mapped_data, quality_metrics)
        
        # --- ENSAMBLAJE Y MAPEO EXACTO PARA CONTRATO FRONTEND (page.js) ---
        return {
            "status": "success",
            "calidad_datos": {
                "nivelConfianza": quality_metrics.get("nivelConfianza", "ALTA"),
                "scoreGlobal": quality_metrics.get("scoreGlobal", 100),
                "metricas": {
                    "unicidad": quality_metrics.get("metricas", {}).get("unicidad", 100),
                    "completitud": quality_metrics.get("metricas", {}).get("completitud", 100),
                    "validez": quality_metrics.get("metricas", {}).get("validez", 100)
                }
            },
            "analisis": {
                "filasAnalizadas": len(mapped_data),
                "totalIngresos": financial_results.get("totalIngresos", 0.0),
                "margenGlobal": financial_results.get("margenGlobal", 0.0),
                "dineroEnRiesgo": financial_results.get("dineroEnRiesgo", 0.0),
                "totalHallazgos": len(anomalies)
            },
            "hallazgos": anomalies
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error procesando la matriz en GENESIS: {str(e)}")
