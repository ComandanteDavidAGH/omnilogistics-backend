from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import io

# =================================================================
# 🛡️ LOS 3 MOTORES DE GENESIS (FUSIONADOS EN EL MISMO ARCHIVO)
# =================================================================

class SemanticMapper:
    def map_entities(self, excel_file):
        # Simulador de procesamiento del Excel masivo
        filas_procesadas = [1] * 3000  # Simula las 3,000 operaciones
        return filas_procesadas, "Mapeo Exitoso - Hoja: Viajes"

class DataQualityGate:
    def evaluate(self, mapped_data):
        # Simulador del análisis de calidad de los datos
        return {
            "nivelConfianza": "ALTA",
            "scoreGlobal": 98,
            "metricas": {
                "unicidad": 100,
                "completitud": 95,
                "validez": 99
            }
        }

class EconomicRuleEngine:
    def analyze(self, mapped_data, quality_metrics):
        # Simulador de matemáticas financieras y detección de fugas reales
        anomalias = [
            {
                "prioridad": 1,
                "vehiculo": "TRK-902",
                "titulo": "Clonación de Viaje Detectada",
                "causa": "Misma fecha, ruta y camión duplicado en la pestaña de billing.",
                "accion": "Verificar duplicidad en ERP antes de autorizar pago.",
                "impacto": 4500.00
            },
            {
                "prioridad": 2,
                "vehiculo": "TRK-105",
                "titulo": "Sobreprecio de Tarifa",
                "causa": "El cobro excede un 15% el tabulador pactado para esta ruta.",
                "accion": "Ajustar factura a tarifa base negociada.",
                "impacto": 1200.00
            }
        ]
        resultados_financieros = {
            "totalIngresos": 150000.00,
            "margenGlobal": 24.50,
            "dineroEnRiesgo": 5700.00
        }
        return resultados_financieros, anomalias

# =================================================================
# 🚀 ORQUESTADOR PRINCIPAL (API)
# =================================================================

app = FastAPI(title="GENESIS CORE B2B - Economic Intelligence Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/procesar-matriz")
async def procesar_matriz(file: UploadFile = File(...)):
    try:
        file_bytes = await file.read()
        excel_file = io.BytesIO(file_bytes)
        
        # Ejecutamos los motores que ahora viven aquí mismo
        mapper = SemanticMapper()
        mapped_data, mapping_report = mapper.map_entities(excel_file)
        
        quality = DataQualityGate()
        quality_metrics = quality.evaluate(mapped_data)
        
        economic_engine = EconomicRuleEngine()
        financial_results, anomalies = economic_engine.analyze(mapped_data, quality_metrics)
        
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
        raise HTTPException(status_code=500, detail=f"Error procesando la matriz: {str(e)}")
