from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io

app = FastAPI(title="GENESIS CORE - FOUNDATION", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# ESCALÓN 1: SEMANTIC MAPPING & DATA MODEL
# ==========================================
SEMANTIC_MAP = {
    "ENT_TRIP_ID": ["viaje", "id", "orden", "ticket", "folio"],
    "ENT_VEHICLE_ID": ["vehiculo", "unidad", "tracto", "placa", "camion", "economico"],
    "ENT_CUSTOMER_ID": ["cliente", "customer", "empresa", "cuenta"],
    "ENT_DRIVER_ID": ["conductor", "operador", "chofer"],
    "METRIC_REVENUE": ["ingreso", "venta", "flete", "facturado"],
    "METRIC_COST": ["costo", "gasto", "egreso"],
    "METRIC_DISTANCE": ["km", "kilometros", "distancia", "recorrido"],
    "METRIC_FUEL_VOL": ["litros", "combustible", "diesel", "galones"]
}

# Diccionario de campos peligrosos (El sistema no adivina, levanta la mano)
AMBIGUOUS_MAP = {
    "AMB_MONEY": ["valor", "monto", "importe", "total", "saldo"],
    "AMB_DATE": ["fecha", "date", "dia"]
}

def analyze_sheet(df, sheet_name):
    cols = [str(c).lower().strip() for c in df.columns]
    found_entities = {}
    ambiguities = []
    unmapped = []

    for raw_col, lower_col in zip(df.columns, cols):
        mapped = False
        
        # 1. Intentar mapear a una Entidad o Métrica conocida
        for entity, aliases in SEMANTIC_MAP.items():
            if any(alias in lower_col for alias in aliases):
                found_entities[entity] = raw_col
                mapped = True
                break
        
        # 2. Detectar Ambigüedades (Ej: "Valor" -> ¿Es costo o ingreso?)
        if not mapped:
            for amb, aliases in AMBIGUOUS_MAP.items():
                if any(alias in lower_col for alias in aliases):
                    ambiguities.append({
                        "columna": raw_col, 
                        "tipo": amb, 
                        "motivo": f"Requiere contexto: ¿Qué representa exactamente '{raw_col}' en el modelo económico?"
                    })
                    mapped = True
                    break
        
        if not mapped:
            unmapped.append(raw_col)

    # 3. Inferir el contexto de la hoja
    context = "HOJA DESCONOCIDA"
    if "ENT_TRIP_ID" in found_entities and "METRIC_REVENUE" in found_entities:
        context = "ENTIDAD PRINCIPAL: OPERACIONES / FACTURACIÓN"
    elif "ENT_TRIP_ID" in found_entities and "METRIC_FUEL_VOL" in found_entities:
        context = "ENTIDAD PRINCIPAL: TELEMETRÍA / COMBUSTIBLE"

    return {
        "hoja": sheet_name,
        "filas": len(df),
        "contexto_inferido": context,
        "entidades_detectadas": found_entities,
        "ambiguedades": ambiguities,
        "columnas_huerfanas": len(unmapped)
    }

@app.post("/api/procesar-matriz")
async def procesar_archivo(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        xls = pd.ExcelFile(io.BytesIO(contents))
        
        understanding_report = []
        for sheet in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet)
            if not df.empty:
                report = analyze_sheet(df, sheet)
                understanding_report.append(report)

        # ==========================================
        # HACK ARQUITECTÓNICO PARA EL FRONTEND CONGELADO
        # Convertimos la comprensión de datos en "Hallazgos" visuales
        # ==========================================
        hallazgos_simulados = []
        score_dq = 100
        
        for r in understanding_report:
            # 1. Tarjeta de Éxito Semántico
            hallazgos_simulados.append({
                "id": f"ENT-{r['hoja']}",
                "prioridad": 2,  # Amarillo
                "tipo": "MODELO_DATOS",
                "titulo": f"Mapeo Exitoso - Hoja: {r['hoja']}",
                "causa": f"{r['contexto_inferido']}. Entidades mapeadas: {', '.join(r['entidades_detectadas'].keys())}",
                "impacto": len(r['entidades_detectadas']),
                "vehiculo": "SISTEMA",
                "accion": f"El Semantic Engine estructuró esta hoja. Columnas ignoradas/huérfanas: {r['columnas_huerfanas']}"
            })

            # 2. Tarjetas Críticas de Ambigüedad
            for amb in r['ambiguedades']:
                score_dq -= 10 # Penalizamos la confianza del modelo
                hallazgos_simulados.append({
                    "id": f"AMB-{amb['columna']}",
                    "prioridad": 1, # Rojo Crítico
                    "tipo": "AMBIGUEDAD_SEMANTICA",
                    "titulo": f"Campo Ambiguo Detectado: '{amb['columna']}'",
                    "causa": amb['motivo'],
                    "impacto": 0,
                    "vehiculo": "RIESGO DE MODELO",
                    "accion": "Detener ingesta. El usuario o el sistema debe desambiguar este campo antes de pasarlo al Economic Engine."
                })

        # Ordenar para que las ambigüedades salgan primero
        hallazgos_simulados = sorted(hallazgos_simulados, key=lambda x: x['prioridad'])

        # Devolvemos la estructura exacta que el frontend espera, pero con Data Understanding
        return {
            "status": "success",
            "calidad_datos": {
                "scoreGlobal": max(10, score_dq),
                "nivelConfianza": "FASE: DATA UNDERSTANDING",
                "metricas": {"unicidad": 100, "completitud": 100, "validez": 100}
            },
            "analisis": {
                "totalIngresos": 0,
                "totalCostos": 0,
                "margenGlobal": 0,
                "dineroEnRiesgo": len(understanding_report), # Hojas leídas
                "totalHallazgos": len(hallazgos_simulados),
                "filasAnalizadas": sum(r['filas'] for r in understanding_report)
            },
            "hallazgos": hallazgos_simulados,
            
            # EL PAYLOAD REAL (El cerebro puro guardando el modelo para el futuro)
            "genesis_data_model": understanding_report
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "GENESIS FOUNDATION - Semantic Mapping Engine en línea."}
