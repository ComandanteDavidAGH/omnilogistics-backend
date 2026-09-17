from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import io
import pandas as pd
import numpy as np

# =================================================================
# 🛡️ MOTORES DE PARSING Y REGLAS REALES (PANDAS / OPENPYXL)
# =================================================================

class SemanticMapper:
    """
    Escalón 1: Lee todas las pestañas del Excel, normaliza nombres de columnas
    y consolida la matriz operativa en una sola estructura analítica.
    """
    def map_entities(self, excel_file: io.BytesIO):
        try:
            # Leer todas las pestañas disponibles en el archivo
            sheets_dict = pd.read_excel(excel_file, sheet_name=None)
            
            all_dfs = []
            for sheet_name, df in sheets_dict.items():
                if df.empty or len(df.columns) == 0:
                    continue
                
                # Normalizar nombres de columnas (quitar espacios, minúsculas)
                df.columns = [str(col).strip().lower() for col in df.columns]
                df['__hoja_origen__'] = sheet_name
                all_dfs.append(df)
            
            if not all_dfs:
                raise ValueError("El archivo Excel está vacío o no contiene pestañas con datos.")
            
            # Consolidación de todas las pestañas
            consolidated_df = pd.concat(all_dfs, ignore_index=True)
            return consolidated_df, f"Mapeo completado exitosamente ({len(sheets_dict)} pestañas analizadas)."
        except Exception as e:
            raise ValueError(f"Fallo en lectura del libro Excel: {str(e)}")


class DataQualityGate:
    """
    Escalón 2: Evalúa completitud, unicidad y validez de la matriz de datos.
    """
    def evaluate(self, df: pd.DataFrame):
        if df.empty:
            return {
                "nivelConfianza": "BAJA",
                "scoreGlobal": 0,
                "metricas": {"unicidad": 0, "completitud": 0, "validez": 0}
            }

        total_filas = len(df)
        total_celdas = df.size

        # 1. Completitud: Porcentaje de celdas no nulas
        celdas_llenas = df.notna().sum().sum()
        completitud = float(round((celdas_llenas / total_celdas) * 100, 1)) if total_celdas > 0 else 0.0

        # 2. Unicidad: Porcentaje de registros no duplicados totalmente
        filas_duplicadas = df.duplicated().sum()
        unicidad = float(round(((total_filas - filas_duplicadas) / total_filas) * 100, 1)) if total_filas > 0 else 0.0

        # 3. Validez: Verificación de valores numéricos coherentes (no negativos en costos/ingresos)
        num_cols = df.select_dtypes(include=[np.number]).columns
        if len(num_cols) > 0:
            celdas_validas = (df[num_cols] >= 0).sum().sum()
            total_num_celdas = df[num_cols].notna().sum().sum()
            validez = float(round((celdas_validas / total_num_celdas) * 100, 1)) if total_num_celdas > 0 else 100.0
        else:
            validez = 100.0

        score_global = int(round((completitud + unicidad + validez) / 3))

        if score_global >= 85:
            nivel = "ALTA"
        elif score_global >= 65:
            nivel = "MEDIA"
        else:
            nivel = "BAJA"

        return {
            "nivelConfianza": nivel,
            "scoreGlobal": score_global,
            "metricas": {
                "unicidad": unicidad,
                "completitud": completitud,
                "validez": validez
            }
        }


class EconomicRuleEngine:
    """
    Escalón 3: Aplica reglas de negocio sobre montos, identifica duplicidades de viaje
    y desviaciones tarifarias extremas.
    """
    def analyze(self, df: pd.DataFrame, quality_metrics: dict):
        cols = list(df.columns)

        def buscar_columna(keywords):
            for c in cols:
                if any(kw in c for kw in keywords):
                    return c
            return None

        # Identificar columnas semánticas clave
        col_monto = buscar_columna(['monto', 'ingreso', 'tarifa', 'precio', 'total', 'revenue', 'billing', 'flete', 'costo'])
        col_vehiculo = buscar_columna(['vehiculo', 'vehículo', 'unidad', 'camion', 'camión', 'truck', 'placa', 'id_unidad', 'equipo'])
        col_id = buscar_columna(['id', 'viaje', 'operacion', 'operación', 'folio', 'ticket', 'guia', 'guía'])
        col_fecha = buscar_columna(['fecha', 'date', 'dia', 'día'])

        # CÁLCULO DE INGRESO OPERATIVO
        if col_monto and pd.api.types.is_numeric_dtype(df[col_monto]):
            total_ingresos = float(df[col_monto].dropna().sum())
        else:
            # Suma fallback sobre todas las columnas numéricas si no hay coincidencia directa
            num_df = df.select_dtypes(include=[np.number])
            total_ingresos = float(num_df.sum().sum()) if not num_df.empty else 0.0

        anomalias = []

        # REGLA 1: Detección de Operaciones/Viajes Clonados
        cols_duplicadas = [c for c in [col_vehiculo, col_id, col_fecha, col_monto] if c is not None]
        if len(cols_duplicadas) >= 2:
            duplicados = df[df.duplicated(subset=cols_duplicadas, keep=False)]
            if not duplicados.empty:
                agrupados = duplicados.groupby(col_vehiculo if col_vehiculo else cols_duplicadas[0])
                for veh, group in agrupados:
                    nombre_veh = str(veh) if pd.notna(veh) else "DESCONOCIDO"
                    impacto = float(group[col_monto].sum() / 2) if col_monto and pd.api.types.is_numeric_dtype(group[col_monto]) else 2500.0
                    anomalias.append({
                        "prioridad": 1,
                        "vehiculo": nombre_veh,
                        "titulo": "Clonación de Operación Detectada",
                        "causa": f"Se identificaron {len(group)} registros duplicados con coincidencia en campos clave.",
                        "accion": "Verificar duplicidad en ERP antes de autorizar dispersión de pago.",
                        "impacto": round(impacto, 2)
                    })

        # REGLA 2: Sobreprecios / Tarifas fuera de rango estadístico (Outliers > 2 Desviaciones Estándar)
        if col_monto and pd.api.types.is_numeric_dtype(df[col_monto]) and len(df) > 5:
            media = df[col_monto].mean()
            std = df[col_monto].std()
            if std > 0:
                outliers = df[df[col_monto] > (media + (2 * std))]
                for idx, row in outliers.head(5).iterrows():
                    nombre_veh = str(row[col_vehiculo]) if col_vehiculo and pd.notna(row[col_vehiculo]) else f"Fila #{idx + 1}"
                    val = float(row[col_monto])
                    impacto_exceso = float(val - media)
                    anomalias.append({
                        "prioridad": 2,
                        "vehiculo": nombre_veh,
                        "titulo": "Desviación de Tarifa / Sobreprecio",
                        "causa": f"El monto (${val:,.2f}) excede significativamente el promedio de la operación (${media:,.2f}).",
                        "accion": "Ajustar factura a tarifa base negociada o requerir comprobante.",
                        "impacto": round(impacto_exceso, 2)
                    })

        # Dinero en riesgo acumulado
        dinero_en_riesgo = float(sum(a["impacto"] for a in anomalias))

        # Cálculo de Margen Global estimado
        base_margen = 25.0
        porcentaje_riesgo = (dinero_en_riesgo / total_ingresos * 100) if total_ingresos > 0 else 0
        margen_global = max(0.0, float(round(base_margen - (porcentaje_riesgo * 0.3), 2)))

        resultados_financieros = {
            "totalIngresos": round(total_ingresos, 2),
            "margenGlobal": margen_global,
            "dineroEnRiesgo": round(dinero_en_riesgo, 2)
        }

        # Ordenar por nivel de prioridad (1 = CRÍTICO, 2 = ALTO)
        anomalias = sorted(anomalias, key=lambda x: x["prioridad"])[:10]

        return resultados_financieros, anomalias

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

@app.post("/api/procesar-matriz")
async def procesar_matriz(file: UploadFile = File(...)):
    try:
        file_bytes = await file.read()
        excel_file = io.BytesIO(file_bytes)
        
        # 1. Escalón 1: Mapeo y parsing con pandas
        mapper = SemanticMapper()
        mapped_df, mapping_report = mapper.map_entities(excel_file)
        
        # 2. Escalón 2: Compuerta de calidad
        quality = DataQualityGate()
        quality_metrics = quality.evaluate(mapped_df)
        
        # 3. Escalón 3: Reglas financieras y detección de fugas
        economic_engine = EconomicRuleEngine()
        financial_results, anomalies = economic_engine.analyze(mapped_df, quality_metrics)
        
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
                "filasAnalizadas": len(mapped_df),
                "totalIngresos": financial_results.get("totalIngresos", 0.0),
                "margenGlobal": financial_results.get("margenGlobal", 0.0),
                "dineroEnRiesgo": financial_results.get("dineroEnRiesgo", 0.0),
                "totalHallazgos": len(anomalies)
            },
            "hallazgos": anomalies
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error procesando la matriz en GENESIS: {str(e)}")
