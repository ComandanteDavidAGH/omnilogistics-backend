"""Compuerta de calidad: decide si los datos son suficientemente confiables para mostrar cifras.

Dos ideas separadas (y ambas se reportan):
  - data_quality_score: qué tan limpios están los datos que llegaron (0-100)
  - analytical_confidence: qué tan sólido es el análisis que permiten (0-100)
Si hay un problema bloqueante NO se calculan cifras: es preferible no mostrar nada a mostrar algo falso.
"""
from __future__ import annotations

import pandas as pd

from .model import COST_COLUMNS, LABELS

UNPARSED_WARN = 0.05
UNPARSED_BLOCK = 0.20
MISSING_REVENUE_BLOCK = 0.50


def _ratio(num: float, den: float) -> float:
    return (num / den) if den else 0.0


class DataQualityGate:
    def evaluate(self, master: pd.DataFrame, parse_stats: dict, merge_report: dict) -> dict:
        motivos: list = []

        def add(codigo: str, severidad: str, mensaje: str) -> None:
            motivos.append({"codigo": codigo, "severidad": severidad, "mensaje": mensaje})

        if master is None or master.empty:
            add("SIN_DATOS", "BLOQUEANTE", "Ninguna columna quedó asignada a un campo del modelo: no hay datos que analizar.")
            return self._result(0.0, 0.0, motivos, {"filas": 0})

        n = len(master)
        cols = set(master.columns)
        cost_cols = [c for c in COST_COLUMNS if c in cols]
        has_rev = "REVENUE" in cols

        # --- Métricas ---------------------------------------------------------------------------
        key_fields = [c for c in ["TRIP_ID", "REVENUE", "VEHICLE_ID", "TRIP_DATE"] + cost_cols if c in cols]
        completitud = 100 * sum(master[c].notna().mean() for c in key_fields) / len(key_fields) if key_fields else 0.0
        canon = [c for c in master.columns if not c.startswith("_src_") and not c.endswith("__alt")]
        unicidad = 100 * (1 - master.duplicated(subset=canon).mean()) if canon and len(canon) >= 2 else 100.0
        cobertura_id = 100 * master["TRIP_ID"].notna().mean() if "TRIP_ID" in cols else None

        def unparsed_rate(field: str) -> float:
            st = parse_stats.get(field)
            if not st:
                return 0.0
            return _ratio(st["unparsed"], st["total"] - st["blank"])

        rev_unparsed = unparsed_rate("REVENUE") if has_rev else 0.0
        cost_unparsed = max((unparsed_rate(c) for c in cost_cols), default=0.0)
        tasa_lectura = 100 * (1 - max(rev_unparsed, cost_unparsed))
        joins = [j for j in merge_report.get("joins", []) if j.get("modo") == "por_viaje"]
        cobertura_cruce = min((j["pct_base_con_match"] for j in joins), default=None)

        metricas = {
            "filas": n, "completitud_campos_clave": round(completitud, 1), "unicidad": round(unicidad, 1),
            "cobertura_id_viaje": None if cobertura_id is None else round(cobertura_id, 1),
            "tasa_lectura_numerica": round(tasa_lectura, 1),
            "cobertura_cruce": cobertura_cruce,
        }
        quality = (completitud + unicidad + tasa_lectura) / 3

        # --- Confianza analítica ---------------------------------------------------------------------
        confidence = 100.0
        if not has_rev and not cost_cols:
            add("SIN_MEDIDAS", "BLOQUEANTE", "No hay columna de ingresos ni de costos asignada; no se puede calcular nada económico.")
        if not has_rev:
            confidence -= 35
            add("SIN_INGRESO", "ALTA", "No se asignó la columna de ingresos: no se calculan ingresos ni márgenes.")
        if not cost_cols:
            confidence -= 25
            add("SIN_COSTOS", "ALTA", "No se asignó ninguna columna de costos: no se calculan márgenes.")
        if "TRIP_ID" not in cols:
            confidence -= 20
            add("SIN_ID_VIAJE", "MEDIA", "Sin ID de viaje no se pueden distinguir duplicados reales ni cruzar hojas por viaje.")
        elif cobertura_id is not None and cobertura_id < 90:
            confidence -= 10
            add("ID_INCOMPLETO", "MEDIA", f"Solo el {cobertura_id:.0f}% de las filas tiene ID de viaje.")
        if "VEHICLE_ID" not in cols:
            confidence -= 5

        for fld, rate in [("REVENUE", rev_unparsed)] + [(c, unparsed_rate(c)) for c in cost_cols]:
            if fld not in cols:
                continue
            if rate > UNPARSED_BLOCK:
                add("LECTURA_NUMERICA", "BLOQUEANTE",
                    f"El {rate:.0%} de los valores de '{LABELS[fld]}' no se pudo leer como número. Revisa el formato de la columna.")
            elif rate > UNPARSED_WARN:
                confidence -= 15
                add("LECTURA_NUMERICA", "ALTA", f"El {rate:.0%} de los valores de '{LABELS[fld]}' no se pudo leer y se excluyó de los cálculos.")

        if has_rev:
            missing_rev = 1 - master["REVENUE"].notna().mean()
            if missing_rev > MISSING_REVENUE_BLOCK:
                add("INGRESO_VACIO", "BLOQUEANTE", f"El {missing_rev:.0%} de las filas no tiene ingreso; las cifras no serían representativas.")
            elif missing_rev > 0.10:
                confidence -= 10
                add("INGRESO_INCOMPLETO", "MEDIA", f"El {missing_rev:.0%} de las filas no tiene ingreso.")

        for fld, st in parse_stats.items():
            if st.get("ambiguous"):
                confidence -= 5
                add("FORMATO_AMBIGUO", "MEDIA",
                    f"Los números de '{LABELS.get(fld, fld)}' podrían leerse con punto o coma decimal; "
                    "se asumió el formato colombiano (punto de miles, coma decimal). Verifica un valor.")
        date_st = parse_stats.get("TRIP_DATE")
        if date_st and _ratio(date_st["unparsed"], date_st["total"] - date_st["blank"]) > 0.10:
            confidence -= 5
            add("FECHAS_ILEGIBLES", "MEDIA", "Más del 10% de las fechas no se pudo interpretar; la serie mensual puede estar incompleta.")

        if cobertura_cruce is not None and cobertura_cruce < 80:
            confidence -= 25
            add("COBERTURA_CRUCE", "ALTA", f"Solo el {cobertura_cruce:.0f}% de los viajes cruzó con la otra hoja; el margen es parcial.")
        elif cobertura_cruce is not None and cobertura_cruce < 95:
            confidence -= 8
            add("COBERTURA_CRUCE", "MEDIA", f"El {cobertura_cruce:.0f}% de los viajes cruzó con la otra hoja; el resto queda fuera del margen.")

        return self._result(quality, max(0.0, confidence), motivos, metricas)

    @staticmethod
    def _result(quality: float, confidence: float, motivos: list, metricas: dict) -> dict:
        blocked = any(m["severidad"] == "BLOQUEANTE" for m in motivos)
        if blocked:
            nivel = "BLOQUEADA"
        elif confidence >= 80:
            nivel = "ALTA"
        elif confidence >= 60:
            nivel = "MEDIA"
        elif confidence >= 40:
            nivel = "BAJA"
        else:
            nivel = "BLOQUEADA"
            blocked = True
        return {
            "scoreGlobal": round(quality, 1), "data_quality_score": round(quality, 1),
            "analytical_confidence": round(confidence, 1), "nivelConfianza": nivel,
            "bloqueante": blocked, "motivos": motivos, "metricas": metricas,
        }
