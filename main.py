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
    Escalón 3: Reglas financieras adaptables con vocabulario expandido.
    """
    def analyze(self, df: pd.DataFrame, quality_metrics: dict):
        cols = list(df.columns)

        def buscar_columna(keywords):
            for c in cols:
                if any(kw in str(c).lower() for kw in keywords):
                    return c
            return None

        # Vocabulario extendido para encontrar columnas en cualquier Excel B2B
        col_monto = buscar_columna(['monto', 'ingreso', 'tarifa', 'precio', 'total', 'revenue', 'billing', 'flete', 'costo', 'importe', 'val', 'subtotal'])
        col_vehiculo = buscar_columna(['vehiculo', 'vehículo', 'unidad', 'camion', 'camión', 'truck', 'placa', 'equipo', 'tracto', 'placas'])
        col_id = buscar_columna(['id', 'viaje', 'operacion', 'operación', 'folio', 'ticket', 'guia', 'guía', 'factura', 'remision', 'remisión', 'servicio'])
        col_fecha = buscar_columna(['fecha', 'date', 'dia', 'día', 'emision', 'salida'])

        # 1. Ingreso Operativo Real
        if col_monto and pd.api.types.is_numeric_dtype(df[col_monto]):
            total_ingresos = float(df[col_monto].dropna().sum())
        else:
            num_df = df.select_dtypes(include=[np.number])
            total_ingresos = float(num_df.sum().sum()) if not num_df.empty else 0.0

        anomalias = []

        # 2. Detección de Duplicados (Evaluación flexible)
        criterios_duplicados = [c for c in [col_id, col_vehiculo, col_fecha] if c is not None]
        
        if len(criterios_duplicados) > 0:
            duplicados = df[df.duplicated(subset=criterios_duplicados, keep=False)]
            if not duplicados.empty:
                col_agrupador = col_vehiculo if col_vehiculo else criterios_duplicados[0]
                agrupados = duplicados.groupby(col_agrupador)
                
                for key, group in agrupados:
                    monto_fuga = float(group[col_monto].sum() / 2) if col_monto and pd.api.types.is_numeric_dtype(group[col_monto]) else 1500.0
                    anomalias.append({
                        "prioridad": 1,
                        "vehiculo": str(key),
                        "titulo": "Clonación o Registro Duplicado Detectado",
                        "causa": f"Se detectaron {len(group)} registros idénticos en los campos: {', '.join(criterios_duplicados)}.",
                        "accion": "Verificar duplicidad en ERP antes de dispersar el pago.",
                        "impacto": round(monto_fuga, 2)
                    })

        # 3. Detección de Desviaciones Tarifarias (> 1.5 Desviaciones Estándar)
        if col_monto and pd.api.types.is_numeric_dtype(df[col_monto]) and len(df) > 5:
            media = df[col_monto].mean()
            std = df[col_monto].std()
            if std > 0:
                outliers = df[df[col_monto] > (media + (1.5 * std))]
                for idx, row in outliers.head(5).iterrows():
                    veh_label = str(row[col_vehiculo]) if col_vehiculo and pd.notna(row[col_vehiculo]) else f"Fila #{idx + 1}"
                    val = float(row[col_monto])
                    anomalias.append({
                        "prioridad": 2,
                        "vehiculo": veh_label,
                        "titulo": "Sobreprecio / Tarifa Fuera de Rango",
                        "causa": f"Monto registrado (${val:,.2f}) excede el promedio de la ruta (${media:,.2f}).",
                        "accion": "Ajustar cobro a la tarifa base negociada.",
                        "impacto": round(val - media, 2)
                    })

        dinero_en_riesgo = float(sum(a["impacto"] for a in anomalias))
        porcentaje_riesgo = (dinero_en_riesgo / total_ingresos * 100) if total_ingresos > 0 else 0
        margen_global = max(0.0, float(round(25.0 - (porcentaje_riesgo * 0.3), 2)))

        return {
            "totalIngresos": round(total_ingresos, 2),
            "margenGlobal": margen_global,
            "dineroEnRiesgo": round(dinero_en_riesgo, 2)
        }, sorted(anomalias, key=lambda x: x["prioridad"])[:10]

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
