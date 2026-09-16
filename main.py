from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import io

app = FastAPI(title="GENESIS OMNI CORE", version="0.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 1. DICCIONARIO SEMÁNTICO
# ==========================================
SEMANTIC_DICT = {
    "VIAJE_ID": ["viaje", "id", "orden", "ticket"],
    "VEHICULO": ["vehiculo", "unidad", "tracto", "placa", "camion"],
    "INGRESO": ["ingreso", "venta", "flete", "facturado"],
    "COSTO": ["costo_total", "costo", "gastos"],
    "MARGEN": ["margen"],
    "MARGEN_PCT": ["margen_%", "margen %", "rentabilidad"],
    "KM": ["km", "kilometros", "recorrido"],
    "LITROS": ["litros", "combustible", "diesel", "galones"],
    "PRECIO_DIESEL": ["precio_diesel", "precio"],
    "OTROS_COSTOS": ["otros_costos", "extra", "maniobras"]
}

def find_col(df, semantic_key):
    aliases = SEMANTIC_DICT.get(semantic_key, [])
    for col in df.columns:
        col_lower = str(col).lower().strip()
        for alias in aliases:
            if alias in col_lower:
                return col
    return None

# ==========================================
# 2. DATA QUALITY GATE (NUEVO MOTOR)
# ==========================================
def evaluar_calidad_datos(df, columnas_mapeadas):
    total_filas = len(df)
    if total_filas == 0:
        return 0, "NULA", {}

    # A. UNICIDAD (Detectar viajes duplicados)
    col_viaje = columnas_mapeadas.get("VIAJE_ID")
    if col_viaje:
        viajes_unicos = df[col_viaje].nunique()
        unicidad = (viajes_unicos / total_filas) * 100
    else:
        unicidad = 0.0

    # B. COMPLETITUD (Detectar vacíos en métricas vitales)
    cols_criticas = [columnas_mapeadas.get(k) for k in ["KM", "LITROS", "INGRESO", "COSTO"] if columnas_mapeadas.get(k)]
    if cols_criticas:
        celdas_totales = total_filas * len(cols_criticas)
        # Cuenta cuántas celdas NO son nulas o cero cuando no deberían
        celdas_llenas = df[cols_criticas].replace([0, '0', '', ' '], np.nan).notna().sum().sum()
        completitud = (celdas_llenas / celdas_totales) * 100
    else:
        completitud = 0.0

    # C. VALIDEZ NUMÉRICA (Consistencia de tipos de datos)
    # Si las columnas de dinero tienen letras, penalizamos
    validez = 100.0
    col_ingreso = columnas_mapeadas.get("INGRESO")
    if col_ingreso:
        no_numericos = pd.to_numeric(df[col_ingreso], errors='coerce').isna().sum()
        validez = ((total_filas - no_numericos) / total_filas) * 100

    # SCORE GLOBAL (Ponderado: Completitud pesa más)
    score_global = (unicidad * 0.3) + (completitud * 0.5) + (validez * 0.2)
    
    # NIVEL DE CONFIANZA
    if score_global >= 90:
        confianza = "ALTA"
    elif score_global >= 70:
        confianza = "MEDIA"
    else:
        confianza = "BAJA - CUIDADO"

    return round(score_global, 1), confianza, {
        "unicidad": round(unicidad, 1),
        "completitud": round(completitud, 1),
        "validez": round(validez, 1)
    }

@app.post("/api/procesar-matriz")
async def procesar_archivo(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.xlsm')):
        raise HTTPException(status_code=400, detail="GENESIS v0.3 requiere un archivo Excel.")
    
    try:
        contents = await file.read()
        xls = pd.ExcelFile(io.BytesIO(contents))
        sheets = {sheet.lower().strip(): pd.read_excel(xls, sheet_name=sheet) for sheet in xls.sheet_names}
        
        sheet_viajes = next((sheets[k] for k in sheets.keys() if "viaje" in k or "operacion" in k), None)
        sheet_facturacion = next((sheets[k] for k in sheets.keys() if "factura" in k or "ingreso" in k), None)
        
        if sheet_viajes is None:
            raise HTTPException(status_code=400, detail="GENESIS requiere una pestaña llamada 'Viajes' u 'Operaciones'.")

        df_viajes = sheet_viajes.copy()
        
        # Mapeo Semántico
        columnas_mapeadas = {
            "VIAJE_ID": find_col(df_viajes, "VIAJE_ID"),
            "VEHICULO": find_col(df_viajes, "VEHICULO"),
            "INGRESO": find_col(df_viajes, "INGRESO"),
            "COSTO": find_col(df_viajes, "COSTO"),
            "MARGEN": find_col(df_viajes, "MARGEN"),
            "MARGEN_PCT": find_col(df_viajes, "MARGEN_PCT"),
            "KM": find_col(df_viajes, "KM"),
            "LITROS": find_col(df_viajes, "LITROS"),
            "OTROS_COSTOS": find_col(df_viajes, "OTROS_COSTOS")
        }

        # ⚡ EJECUCIÓN DEL DATA QUALITY GATE ANTES DEL ANÁLISIS
        score_dq, nivel_confianza, metricas_dq = evaluar_calidad_datos(df_viajes, columnas_mapeadas)
        
        facturas_dict = {}
        if sheet_facturacion is not None:
            col_fact_viaje = find_col(sheet_facturacion, "VIAJE_ID")
            col_fact_ingreso = find_col(sheet_facturacion, "INGRESO")
            if col_fact_viaje and col_fact_ingreso:
                for _, r in sheet_facturacion.iterrows():
                    v_id = str(r[col_fact_viaje]).strip()
                    facturas_dict[v_id] = r[col_fact_ingreso]

        total_ingresos = 0
        total_costos = 0
        dinero_en_riesgo = 0
        hallazgos = []

        # Motor Económico
        for index, row in df_viajes.iterrows():
            viaje_id = str(row[columnas_mapeadas["VIAJE_ID"]]) if columnas_mapeadas["VIAJE_ID"] and pd.notna(row[columnas_mapeadas["VIAJE_ID"]]) else f"Fila {index+1}"
            vehiculo = str(row[columnas_mapeadas["VEHICULO"]]) if columnas_mapeadas["VEHICULO"] and pd.notna(row[columnas_mapeadas["VEHICULO"]]) else "N/A"
            
            # Limpieza rápida de datos al vuelo (por si la validez falló)
            try: ingreso = float(row[columnas_mapeadas["INGRESO"]]) if columnas_mapeadas["INGRESO"] and pd.notna(row[columnas_mapeadas["INGRESO"]]) else 0
            except: ingreso = 0
            try: costo = float(row[columnas_mapeadas["COSTO"]]) if columnas_mapeadas["COSTO"] and pd.notna(row[columnas_mapeadas["COSTO"]]) else 0
            except: costo = 0
            
            margen = float(row[columnas_mapeadas["MARGEN"]]) if columnas_mapeadas["MARGEN"] and pd.notna(row[columnas_mapeadas["MARGEN"]]) else (ingreso - costo)
            margen_pct = float(row[columnas_mapeadas["MARGEN_PCT"]]) if columnas_mapeadas["MARGEN_PCT"] and pd.notna(row[columnas_mapeadas["MARGEN_PCT"]]) else ((margen / ingreso * 100) if ingreso > 0 else 0)
            
            try: km = float(row[columnas_mapeadas["KM"]]) if columnas_mapeadas["KM"] and pd.notna(row[columnas_mapeadas["KM"]]) else 0
            except: km = 0
            try: litros = float(row[columnas_mapeadas["LITROS"]]) if columnas_mapeadas["LITROS"] and pd.notna(row[columnas_mapeadas["LITROS"]]) else 0
            except: litros = 0
            try: otros_costos = float(row[columnas_mapeadas["OTROS_COSTOS"]]) if columnas_mapeadas["OTROS_COSTOS"] and pd.notna(row[columnas_mapeadas["OTROS_COSTOS"]]) else 0
            except: otros_costos = 0

            total_ingresos += ingreso
            total_costos += costo

            if margen_pct <= 0:
                impacto = abs(margen)
                dinero_en_riesgo += impacto
                hallazgos.append({
                    "id": f"F1-{viaje_id}",
                    "prioridad": 1,
                    "tipo": "MARGEN_CRITICO",
                    "titulo": f"Pérdida Operativa - Viaje {viaje_id}",
                    "causa": f"Costos extraordinarios anómalos (${otros_costos:,.2f})" if otros_costos > 1000 else "El costo total superó los ingresos.",
                    "impacto": impacto,
                    "vehiculo": vehiculo,
                    "accion": "Auditar costos extraordinarios y retener liquidación."
                })

            if km > 0 and litros > 0:
                rendimiento_real = km / litros
                rendimiento_esperado = 2.6
                if rendimiento_real < 2.25:
                    litros_desperdiciados = litros - (km / rendimiento_esperado)
                    impacto_comb = litros_desperdiciados * 25.0 
                    dinero_en_riesgo += impacto_comb
                    hallazgos.append({
                        "id": f"F2-{viaje_id}",
                        "prioridad": 2,
                        "tipo": "FUGA_COMBUSTIBLE",
                        "titulo": f"Consumo Anormal - Viaje {viaje_id}",
                        "causa": f"Rendimiento de {rendimiento_real:.2f} km/L vs {rendimiento_esperado} esperado.",
                        "impacto": impacto_comb,
                        "vehiculo": vehiculo,
                        "accion": "Cruzar carga de diésel con telemetría GPS del motor."
                    })

            if sheet_facturacion is not None and viaje_id in facturas_dict:
                try: ingreso_facturado = float(facturas_dict[viaje_id]) if pd.notna(facturas_dict[viaje_id]) else 0
                except: ingreso_facturado = 0
                
                if ingreso_facturado < ingreso and (ingreso - ingreso_facturado) > 10:
                    impacto_fact = ingreso - ingreso_facturado
                    dinero_en_riesgo += impacto_fact
                    hallazgos.append({
                        "id": f"F3-{viaje_id}",
                        "prioridad": 1,
                        "tipo": "FRAUDE_FACTURACION",
                        "titulo": f"Servicio No Cobrado - Viaje {viaje_id}",
                        "causa": f"Tráfico reporta un cobro de ${ingreso:,.2f} pero Finanzas solo facturó ${ingreso_facturado:,.2f}.",
                        "impacto": impacto_fact,
                        "vehiculo": vehiculo,
                        "accion": "Detener pago a proveedores de este viaje hasta cuadrar factura con el cliente."
                    })

        hallazgos = sorted(hallazgos, key=lambda x: x['impacto'], reverse=True)
        margen_global = ((total_ingresos - total_costos) / total_ingresos * 100) if total_ingresos > 0 else 0

        # EL OBJETO DEFINITIVO
        return {
            "status": "success",
            "calidad_datos": {
                "scoreGlobal": score_dq,
                "nivelConfianza": nivel_confianza,
                "metricas": metricas_dq
            },
            "analisis": {
                "totalIngresos": total_ingresos,
                "totalCostos": total_costos,
                "margenGlobal": margen_global,
                "dineroEnRiesgo": dinero_en_riesgo,
                "totalHallazgos": len(hallazgos),
                "filasAnalizadas": len(df_viajes)
            },
            "hallazgos": hallazgos[:10]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "Motor Inteligente GENESIS CORE v0.3 en línea y operando."}
