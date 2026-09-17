from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import io

# Importación de los 3 motores base (ajusta los nombres según tus archivos reales)
from semantic_engine import SemanticMapper
from quality_gate import DataQualityGate
from economic_engine import EconomicRuleEngine

app = FastAPI(title="GENESIS CORE B2B - Economic Intelligence Engine")

# Configuración estricta de CORS para permitir que tu Frontend (Next.js) se conecte
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # En producción, restringir a la URL de Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/v1/audit")
async def run_full_audit(file: UploadFile = File(...)):
    """
    Endpoint principal de auditoría. Recibe el Excel masivo y orquesta las 3 capas.
    """
    # Leer el archivo Excel en memoria
    file_bytes = await file.read()
    excel_file = io.BytesIO(file_bytes)
    
    # --- ESCALÓN 1: DATA UNDERSTANDING (Mapeo Semántico) ---
    mapper = SemanticMapper()
    mapped_data, mapping_report = mapper.map_entities(excel_file)
    
    # --- ESCALÓN 2: DATA QUALITY GATE (Control de Calidad) ---
    quality = DataQualityGate()
    quality_metrics = quality.evaluate(mapped_data)
    
    # --- ESCALÓN 3: MOTOR ECONÓMICO (Reglas B2B y Fugas) ---
    # Solo ejecuta la matemática si la calidad de datos es aceptable
    economic_engine = EconomicRuleEngine()
    financial_results, anomalies = economic_engine.analyze(mapped_data, quality_metrics)
    
    # --- ENSAMBLAJE DE RESPUESTA PARA EL FRONTEND ---
    return {
        "status": "success",
        "operations_analyzed": len(mapped_data), # Aquí es donde salen las 3000 operaciones
        "mapping_report": mapping_report,
        "quality_metrics": quality_metrics,
        "financial_summary": financial_results,
        "priority_findings": anomalies
    }
