import io
import json
import pandas as pd
import numpy as np
import difflib
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware

# =================================================================
# 🧠 FASE 1: MOTOR SEMÁNTICO MULTI-HOJA (GENESIS DATA MODEL v0.1)
# =================================================================
class GenesisDataUnderstanding:
    def __init__(self):
        # El Contrato Arquitectónico Fundacional
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
        """Analiza un diccionario de DataFrames (múltiples hojas de un Excel)"""
        workbook_result = {
            "status": "success",
            "sheets_detected": len(dfs_dict),
            "sheets_analysis": {},
            "global_entities": [],
            "total_records": 0
        }

        for sheet_name, df in dfs_dict.items():
            if df.empty:
                continue
                
            workbook_result["total_records"] += len(df)
            raw_columns = list(df.columns)
            
            sheet_analysis = {
                "records": len(df),
                "columns_count": len(raw_columns),
                "fields_mapping": {},
                "ambiguities": [],
                "entities_found": set()
            }

            # Rastrear colisiones: qué columnas compiten por el mismo canónico
            canonical_competitors = {}

            for col in raw_columns:
                col_str = str(col).lower().strip()
                actual_type = self._infer_data_type(df[col])
                
                best_match = None
                best_entity = None
                highest_confidence = 0.0

                for entity, attributes in self.data_model.items():
                    for attr_key, rules in attributes.items():
                        for syn in rules["synonyms"]:
                            if syn == col_str:
                                similitud = 1.0
                            elif syn in col_str or col_str in syn:
                                similitud = 0.90
                            else:
                                similitud = difflib.SequenceMatcher(None, col_str, syn).ratio()

                            if rules["type"] != actual_type and similitud < 1.0:
                                similitud = similitud * 0.4 # Penalización estricta por tipo

                            if similitud > highest_confidence:
                                highest_confidence = similitud
                                best_match = attr_key
                                best_entity = entity

                if highest_confidence >= 0.85:
                    if best_match not in canonical_competitors:
                        canonical_competitors[best_match] = []
                    canonical_competitors[best_match].append({
                        "col": col, 
                        "confidence": highest_confidence, 
                        "type": actual_type,
                        "entity": best_entity
                    })
                elif highest_confidence >= 0.35:
                    sheet_analysis["ambiguities"].append({
                        "original_column": col,
                        "detected_type": actual_type,
                        "possible_match": best_match,
                        "confidence": round(highest_confidence, 2)
                    })
                else:
                    sheet_analysis["ambiguities"].append({
                        "original_column": col,
                        "detected_type": actual_type,
                        "possible_match": "UNKNOWN",
                        "confidence": round(highest_confidence, 2)
                    })

            # Resolver colisiones y asentar mapeos definitivos
            for canonical, competitors in canonical_competitors.items():
                if len(competitors) == 1:
                    # Match limpio sin colisión
                    comp = competitors[0]
                    sheet_analysis["fields_mapping"][comp["col"]] = {
                        "canonical": canonical,
                        "confidence": round(comp["confidence"], 2),
                        "detected_type": comp["type"]
                    }
                    sheet_analysis["entities_found"].add(comp["entity"])
                    if comp["entity"] not in workbook_result["global_entities"]:
                        workbook_result["global_entities"].append(comp["entity"])
                else:
                    # GUARDARRAÍL: Colisión detectada (Ej: COSTO y OTROS_COSTOS compitiendo por COST_FUEL)
                    for comp in competitors:
                        sheet_analysis["ambiguities"].append({
                            "original_column": comp["col"],
                            "detected_type": comp["type"],
                            "possible_match": canonical,
                            "confidence": round(comp["confidence"], 2),
                            "collision_warning": True
                        })

            sheet_analysis["entities_found"] = list(sheet_analysis["entities_found"])
            workbook_result["sheets_analysis"][sheet_name] = sheet_analysis

        return workbook_result

def _read_excel_or_csv_multisheet(filename: str, file_bytes: bytes) -> dict:
    """Devuelve un diccionario {nombre_hoja: DataFrame}. CSV se trata como hoja única."""
    buf = io.BytesIO(file_bytes)
    if filename.lower().endswith(".csv"):
        return {"Hoja1": pd.read_csv(buf)}
    return pd.read_excel(buf, sheet_name=None)  # sheet_name=None lee TODAS las hojas
