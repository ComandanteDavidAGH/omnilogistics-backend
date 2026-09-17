from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
import io
import json
import pandas as pd
import numpy as np
import difflib

# =================================================================
# 🧠 FASE 1: MOTOR SEMÁNTICO MULTI-HOJA (GENESIS DATA MODEL v0.1)
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
        cleaned_series = series.astype(str).str.replace(r'[$,\s]', '', regex=True)
        is_really_numeric = pd.to_numeric(cleaned_series, errors='coerce').notna().mean() > 0.6
        if is_really_numeric or pd.api.types.is_numeric_dtype(series):
            return "numeric"
        elif pd.api.types.is_datetime64_any_dtype(series) or "fecha" in str(series.name).lower():
            return "date"
        else:
            return "text"

    def analyze_workbook(self, dfs_dict: dict):
        workbook_result = {
            "status": "success",
            "sheets_detected": len(dfs_dict),
            "sheets_analysis": {},
            "global_entities": [],
            "total_records": 0
        }

        for sheet_name, df in dfs_dict.items():
            if df.empty: continue
                
            workbook_result["total_records"] += len(df)
            raw_columns = list(df.columns)
            
            sheet_analysis = {
                "records": len(df),
                "columns_count": len(raw_columns),
                "fields_mapping": {},
                "ambiguities": [],
                "entities_found": set()
            }

            canonical_competitors = {}

            for col in raw_columns:
                col_str = str(col).lower().strip()
                actual_type = self._infer_data_type(df[col])
                
                best_match, best_entity, highest_confidence = None, None, 0.0

                for entity, attributes in self.data_model.items():
                    for attr_key, rules in attributes.items():
                        for syn in rules["synonyms"]:
                            if syn == col_str: similitud = 1.0
                            elif syn in col_str or col_str in syn: similitud = 0.90
                            else: similitud = difflib.SequenceMatcher(None, col_str, syn).ratio()

                            if rules["type"] != actual_type and similitud < 1.0:
                                similitud = similitud * 0.4 

                            if similitud > highest_confidence:
                                highest_confidence, best_match, best_entity = similitud, attr_key, entity

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
# 🛡️ CALIDAD DE DATOS
# =================================================================
class DataQualityGate:
    def evaluate(self, df: pd.DataFrame):
        if df.empty: return {"nivelConfianza": "BAJA", "scoreGlobal": 0, "metricas": {"unicidad": 0, "completitud": 0, "validez": 0}}
        total_filas, total_celdas = len(df), df.size
        completitud = float(round((df.notna().sum().sum() / total_celdas) * 100, 1)) if total_celdas > 0 else 0.0
        unicidad = float(round(((total_filas - df.duplicated().sum()) / total_filas) * 100, 1)) if total_filas > 0 else 0.0
        score_global = int(round((completitud + unicidad + 100.0) / 3))
        return {"nivelConfianza": "ALTA" if score_global >= 85 else "MEDIA", "scoreGlobal": score_global, "metricas": {"unicidad": unicidad, "completitud": completitud, "validez": 100.0}}


# =================================================================
# 💸 FASE 2: MOTOR ECONÓMICO RELACIONAL (SOPORTA JOIN MULTI-HOJA)
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

    def _merge_sheets(self, dfs_dict: dict, mapping: dict) -> pd.DataFrame:
        processed_dfs = []
        for sheet_name, df in dfs_dict.items():
            if df.empty: continue
            df_renamed = self._rename_to_canonical(df.copy(), mapping)
            canonical_cols = set(df_renamed.columns).intersection(set(mapping.values()))
            if canonical_cols: processed_dfs.append(df_renamed)

        if not processed_dfs: return pd.DataFrame()

        base_df = None
        for i, df in enumerate(processed_dfs):
            if "TRIP_ID" in df.columns:
                base_df = processed_dfs.pop(i)
                break
        if base_df is None: base_df = processed_dfs.pop(0)

        for df in processed_dfs:
            common_keys = list(set(base_df.columns) & set(df.columns) & {"TRIP_ID", "VEHICLE_ID"})
            if "TRIP_ID" in common_keys:
                base_df = pd.merge(base_df, df, on="TRIP_ID", how="left", suffixes=("", "_duplicada"))
            elif "VEHICLE_ID" in common_keys:
                base_df = pd.merge(base_df, df, on="VEHICLE_ID", how="left", suffixes=("", "_duplicada"))

        cols_to_keep = [c for c in base_df.columns if not c.endswith('_duplicada')]
        return base_df[cols_to_keep]

    def analyze(self, master_df: pd.DataFrame, quality_metrics: dict):
        anomalies = []
        has_revenue = "REVENUE" in master_df.columns
        has_cost = "COST_FUEL" in master_df.columns
        has_vehicle = "VEHICLE_ID" in master_df.columns
        has_trip_id = "TRIP_ID" in master_df.columns

        if has_revenue: master_df["REVENUE"] = self._to_numeric(master_df["REVENUE"]).fillna(0)
        if has_cost: master_df["COST_FUEL"] = self._to_numeric(master_df["COST_FUEL"]).fillna(0)

        total_ingresos = float(master_df["REVENUE"].sum()) if has_revenue else 0.0
        total_costos = float(master_df["COST_FUEL"].sum()) if has_cost else 0.0
        margen_global = round(((total_ingresos - total_costos) / total_ingresos) * 100, 2) if (has_revenue and has_cost and total_ingresos > 0) else None

        dinero_en_riesgo = 0.0
        prioridad = 1

        subset = ["TRIP_ID"] if has_trip_id else None
        dup_mask = master_df.duplicated(subset=subset, keep="first")
        n_duplicados = int(dup_mask.sum())
        if n_duplicados > 0:
            impacto_dup = float(master_df.loc[dup_mask, "REVENUE"].sum()) if has_revenue else 0.0
            dinero_en_riesgo += impacto_dup
            vehiculos_afectados = ", ".join(sorted(set(master_df.loc[dup_mask, "VEHICLE_ID"].astype(str)))[:3]) if has_vehicle else "N/D"
            anomalies.append({"prioridad": prioridad, "vehiculo": vehiculos_afectados, "titulo": "Registros Duplicados Detectados", "causa": f"Se encontraron {n_duplicados} registro(s) duplicado(s).", "accion": "Verificar si corresponden a doble facturación.", "impacto": round(impacto_dup, 2)})
            prioridad += 1

        if has_revenue and has_cost:
            negative_margin_mask = (master_df["COST_FUEL"] > master_df["REVENUE"]) & (master_df["REVENUE"] > 0)
            n_negativos = int(negative_margin_mask.sum())
            if n_negativos > 0:
                impacto_neg = float((master_df.loc[negative_margin_mask, "COST_FUEL"] - master_df.loc[negative_margin_mask, "REVENUE"]).sum())
                dinero_en_riesgo += impacto_neg
                vehiculos_neg = ", ".join(sorted(set(master_df.loc[negative_margin_mask, "VEHICLE_ID"].astype(str)))[:3]) if has_vehicle else "N/D"
                anomalies.append({"prioridad": prioridad, "vehiculo": vehiculos_neg, "titulo": "Viajes con Margen Negativo", "causa": f"{n_negativos} viaje(s) tienen un costo de combustible mayor al ingreso.", "accion": "Revisar tarifa pactada.", "impacto": round(impacto_neg, 2)})
                prioridad += 1

        financial_results = {"totalIngresos": round(total_ingresos, 2), "totalCostos": round(total_costos, 2), "margenGlobal": margen_global, "dineroEnRiesgo": round(dinero_en_riesgo, 2)}
        warnings = []
        if not has_revenue: warnings.append("No se identificó REVENUE.")
        if not has_cost: warnings.append("No se identificó COST_FUEL.")
        return financial_results, anomalies, warnings


# =================================================================
# 🚀 ORQUESTADOR PRINCIPAL (API FASTAPI)
# =================================================================
app = FastAPI(title="GENESIS CORE B2B - Economic Intelligence Engine")

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
        motor_semantico = GenesisDataUnderstanding()
        return motor_semantico.analyze_workbook(dfs_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en Data Understanding: {str(e)}")

# AQUI ESTA EL CAMBIO CRUCIAL QUE NO SE APLICÓ LA VEZ PASADA
@app.post("/api/procesar-matriz")
async def procesar_matriz(file: UploadFile = File(...), mapping: str = Form(None)):
    try:
        file_bytes = await file.read()
        # LEEMOS TODAS LAS HOJAS
        dfs_dict = _read_excel_or_csv_multisheet(file.filename, file_bytes)

        if mapping:
            try: mapping_dict = json.loads(mapping)
            except json.JSONDecodeError: raise HTTPException(status_code=400, detail="El campo 'mapping' no es JSON.")
        else:
            mapping_dict = {}

        eco_engine = EconomicRuleEngine()
        
        # CRUZAMOS LAS HOJAS EN UNA MATRIZ MAESTRA
        master_df = eco_engine._merge_sheets(dfs_dict, mapping_dict)
        quality = DataQualityGate()
        q_metrics = quality.evaluate(master_df)
        fin_results, anomalies, warnings = eco_engine.analyze(master_df, q_metrics)

        return {
            "status": "success",
            "calidad_datos": q_metrics,
            "mapeo_utilizado": mapping_dict,
            "advertencias": warnings,
            "analisis": {
                "filasAnalizadas": len(master_df),
                "totalIngresos": fin_results["totalIngresos"],
                "totalCostos": fin_results["totalCostos"],
                "margenGlobal": fin_results["margenGlobal"],
                "dineroEnRiesgo": fin_results["dineroEnRiesgo"],
                "totalHallazgos": len(anomalies)
            },
            "hallazgos": anomalies
        }
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=500, detail=f"Error procesando la matriz: {str(e)}")
