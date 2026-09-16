from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import io

app = FastAPI(title="GENESIS OMNI CORE", version="0.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 1. DICCIONARIO SEMÁNTICO (Semantic Mapper)
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
    """Busca la columna en el Excel sin importar cómo la escriba el cliente."""
    aliases = SEMANTIC_DICT.get(semantic_key, [])
    for col in df.columns:
        col_lower = str(col).lower().strip()
        for alias in aliases:
            if alias in col_lower:
                return col
    return None

@app.post("/api/procesar-matriz")
async def procesar_archivo(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.xlsm')):
        raise HTTPException(status_code=400, detail="GENESIS v0.2 requiere un archivo Excel multipestaña.")
    
    try:
        contents = await file.read()
        xls = pd.ExcelFile(io.BytesIO(contents))
        
        # 2. INGESTA OMNI: Cargar todas las pestañas al mismo tiempo
        sheets = {sheet.lower().strip(): pd.read_excel(xls, sheet_name=sheet) for sheet in xls.sheet_names}
        
        # Detectar hojas clave
        sheet_viajes = next((sheets[k] for k in sheets.keys() if "viaje" in k or "operacion" in k), None)
        sheet_facturacion = next((sheets[k] for k in sheets.keys() if "factura" in k or "ingreso" in k), None)
        
        if sheet_viajes is None:
            raise HTTPException(status_code=400, detail="GENESIS requiere una pestaña llamada 'Viajes' u 'Operaciones'.")

        df_viajes = sheet_viajes.copy()
        
        # 3. MAPEADO SEMÁNTICO EN ACCIÓN
        col_viaje = find_col(df_viajes, "VIAJE_ID")
        col_vehiculo = find_col(df_viajes, "VEHICULO")
        col_ingreso = find_col(df_viajes, "INGRESO")
        col_costo = find_col(df_viajes, "COSTO")
        col_margen = find_col(df_viajes, "MARGEN")
        col_margen_pct = find_col(df_viajes, "MARGEN_PCT")
        col_km = find_col(df_viajes, "KM")
        col_litros = find_col(df_viajes, "LITROS")
        col_otros_costos = find_col(df_viajes, "OTROS_COSTOS")
        
        # 4. MEMORIA CRUZADA: Cargar facturación para auditar fraudes
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

        # 5. EL MOTOR ECONÓMICO Y DE REGLAS (Ejecutándose en el Backend)
        for index, row in df_viajes.iterrows():
            viaje_id = str(row[col_viaje]) if col_viaje and pd.notna(row[col_viaje]) else f"Fila {index+1}"
            vehiculo = str(row[col_vehiculo]) if col_vehiculo and pd.notna(row[col_vehiculo]) else "N/A"
            
            ingreso = float(row[col_ingreso]) if col_ingreso and pd.notna(row[col_ingreso]) else 0
            costo = float(row[col_costo]) if col_costo and pd.notna(row[col_costo]) else 0
            margen = float(row[col_margen]) if col_margen and pd.notna(row[col_margen]) else (ingreso - costo)
            margen_pct = float(row[col_margen_pct]) if col_margen_pct and pd.notna(row[col_margen_pct]) else ((margen / ingreso * 100) if ingreso > 0 else 0)
            
            km = float(row[col_km]) if col_km and pd.notna(row[col_km]) else 0
            litros = float(row[col_litros]) if col_litros and pd.notna(row[col_litros]) else 0
            otros_costos = float(row[col_otros_costos]) if col_otros_costos and pd.notna(row[col_otros_costos]) else 0

            total_ingresos += ingreso
            total_costos += costo

            # REGLA 1: MARGEN DESTRUIDO
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

            # REGLA 2: RENDIMIENTO / HUACHICOL
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

            # REGLA 3: FRAUDE CRUZADO (Operaciones vs Facturación)
            if sheet_facturacion is not None and viaje_id in facturas_dict:
                ingreso_facturado = float(facturas_dict[viaje_id]) if pd.notna(facturas_dict[viaje_id]) else 0
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

        # Ordenar hallazgos de mayor a menor impacto (El Radar)
        hallazgos = sorted(hallazgos, key=lambda x: x['impacto'], reverse=True)
        margen_global = ((total_ingresos - total_costos) / total_ingresos * 100) if total_ingresos > 0 else 0

        # Respuesta final empaquetada con Inteligencia
        return {
            "status": "success",
            "analisis": {
                "totalIngresos": total_ingresos,
                "totalCostos": total_costos,
                "margenGlobal": margen_global,
                "dineroEnRiesgo": dinero_en_riesgo,
                "totalHallazgos": len(hallazgos),
                "filasAnalizadas": len(df_viajes)
            },
            "hallazgos": hallazgos[:10] # Top 10
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "Motor Inteligente GENESIS CORE v0.2 en línea y operando."}
