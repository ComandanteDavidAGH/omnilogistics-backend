"""Quality Engine de Genesis Core v1.2.

Evalúa de forma independiente:
  1. Calidad de Datos (Cleanliness): Completitud, unicidad y tasa de lectura numérica.
  2. Integridad del Modelo (Model Integrity): Coincidencia de llaves, huérfanos y solidez de uniones.
  3. Cobertura de Hojas: Cálculo de registros e incorporación.
"""
from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from .model import COST_COLUMNS, LABELS


def _ratio(num: float, den: float) -> float:
    return (num / den) if den else 0.0


def _pct(num: float, den: float):
    return round(100 * num / den, 1) if den else None


def compute_coverage(hojas: list, secondary_used=None) -> dict:
    """Cobertura analítica a partir del estado de cada hoja recibida."""
    secondary_used = secondary_used or set()
    items = []
    for h in hojas:
        h = dict(h)
        if h.get("secundaria") and h.get("tabla") in secondary_used:
            h["estado"], h["rol"] = "INCORPORADA", "POR_VEHICULO"
            h["motivo"] = "Se usó para calcular costos y margen por vehículo."
        items.append(h)

    modelables = [h for h in items if h.get("estado") != "AUXILIAR"]
    incorporadas = [h for h in modelables if h.get("estado") == "INCORPORADA"]
    no_inc = [h for h in modelables if h.get("estado") != "INCORPORADA"]
    econ = [h for h in modelables if h.get("aporta_medidas")]
    econ_inc = [h for h in econ if h.get("estado") == "INCORPORADA"]
    econ_no_inc = [h for h in econ if h.get("estado") != "INCORPORADA"]

    return {
        "hojas": items,
        "hojas_recibidas": len(items),
        "hojas_auxiliares": len(items) - len(modelables),
        "hojas_modelables": len(modelables),
        "hojas_incorporadas": len(incorporadas),
        "hojas_no_incorporadas": [h["nombre"] for h in no_inc],
        "hojas_con_medidas_no_incorporadas": [h["nombre"] for h in econ_no_inc],
        "medidas_no_incorporadas": sorted({m for h in econ_no_inc for m in h.get("medidas", [])}),
        "registros_recibidos": sum(h.get("filas", 0) for h in modelables),
        "registros_incorporados": sum(h.get("filas", 0) for h in incorporadas),
        "cobertura_hojas": _pct(len(incorporadas), len(modelables)),
        "cobertura_economica": _pct(sum(h.get("filas", 0) for h in econ_inc), sum(h.get("filas", 0) for h in econ)),
    }


@dataclass
class QualityReport:
    data_quality_score: float         # 0 - 100 (Limpieza pura de datos)
    model_integrity_score: float      # 0 - 100 (Cruce y coherencia relacional)
    is_blocked: bool
    motivos: list[dict]
    metricas: dict


class QualityEngine:
    """Motor de evaluación de calidad de datos e integridad del modelo."""

    def evaluate(self, master: pd.DataFrame, parse_stats: dict, merge_report: dict) -> QualityReport:
        motivos: list = []

        def add(codigo: str, severidad: str, mensaje: str) -> None:
            motivos.append({"codigo": codigo, "severidad": severidad, "mensaje": mensaje})

        if master is None or master.empty:
            add("SIN_DATOS", "BLOQUEANTE", "Ninguna columna quedó asignada a un campo del modelo: no hay datos que analizar.")
            return QualityReport(
                data_quality_score=0.0,
                model_integrity_score=0.0,
                is_blocked=True,
                motivos=motivos,
                metricas={"filas": 0}
            )

        n = len(master)
        cols = set(master.columns)
        cost_cols = [c for c in COST_COLUMNS if c in cols]
        has_rev = "REVENUE" in cols

        # --- MÈTRICAS DE LIMPIEZA DE DATOS ---
        key_fields = [c for c in ["TRIP_ID", "REVENUE", "VEHICLE_ID", "TRIP_DATE"] + cost_cols if c in cols]
        completitud = 100 * sum(master[c].notna().mean() for c in key_fields) / len(key_fields) if key_fields else 0.0
        canon = [c for c in master.columns if not c.startswith("_src_") and not c.endswith("__alt")]
        unicidad = 100 * (1 - master.duplicated(subset=canon).mean()) if canon and len(canon) >= 2 else 100.0

        def unparsed_rate(field: str) -> float:
            st = parse_stats.get(field)
            if not st:
                return 0.0
            return _ratio(st["unparsed"], st["total"] - st["blank"])

        rev_unparsed = unparsed_rate("REVENUE") if has_rev else 0.0
        cost_unparsed = max((unparsed_rate(c) for c in cost_cols), default=0.0)
        tasa_lectura = 100 * (1 - max(rev_unparsed, cost_unparsed))

        data_quality_score = round((completitud + unicidad + tasa_lectura) / 3, 1)

        if not has_rev and not cost_cols:
            add("SIN_MEDIDAS", "BLOQUEANTE", "No hay columna de ingresos ni de costos asignada; no se puede calcular nada económico.")

        for fld, rate in [("REVENUE", rev_unparsed)] + [(c, unparsed_rate(c)) for c in cost_cols]:
            if fld in cols and rate > 0.20:
                add("LECTURA_NUMERICA", "BLOQUEANTE",
                    f"El {rate:.0%} de los valores de '{LABELS.get(fld, fld)}' no se pudo leer como número.")

        # --- INTEGRIDAD DEL MODELO Y RELACIONES ---
        joins = [j for j in merge_report.get("joins", []) if j.get("modo") == "por_viaje"]
        cobertura_cruce = min((j["pct_base_con_match"] for j in joins), default=100.0)
        cobertura_id = 100 * master["TRIP_ID"].notna().mean() if "TRIP_ID" in cols else 0.0

        model_integrity_score = round((cobertura_cruce * 0.6) + (cobertura_id * 0.4), 1)

        if "TRIP_ID" not in cols:
            add("SIN_ID_VIAJE", "MEDIA", "Sin ID de viaje no se pueden distinguir duplicados reales ni cruzar hojas por viaje.")

        metricas = {
            "filas": n,
            "completitud_campos_clave": round(completitud, 1),
            "unicidad": round(unicidad, 1),
            "tasa_lectura_numerica": round(tasa_lectura, 1),
            "cobertura_id_viaje": round(cobertura_id, 1),
            "cobertura_cruce": cobertura_cruce,
        }

        blocked = any(m["severidad"] == "BLOQUEANTE" for m in motivos)

        return QualityReport(
            data_quality_score=data_quality_score,
            model_integrity_score=model_integrity_score,
            is_blocked=blocked,
            motivos=motivos,
            metricas=metricas
        )
