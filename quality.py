"""Compuerta de calidad v1.1: calidad, COBERTURA y confianza analítica.

Tres ideas separadas (y las tres se reportan):
  - data_quality_score: qué tan limpios están los datos que SÍ entraron al análisis (0-100)
  - cobertura: qué parte de lo recibido entró realmente al cálculo (hojas y registros económicos)
  - analytical_confidence: qué tan sólido es el resultado; NUNCA supera lo que permite la cobertura
Si hay un problema bloqueante NO se calculan cifras: es preferible no mostrar nada a mostrar algo falso.
"""
from __future__ import annotations

import pandas as pd

try:  # estructura de paquete
    from .model import COST_COLUMNS, LABELS
except ImportError:  # estructura plana
    from model import COST_COLUMNS, LABELS

UNPARSED_WARN = 0.05
UNPARSED_BLOCK = 0.20
MISSING_REVENUE_BLOCK = 0.50

CAP_MEDIDAS_SIN_USAR = 70.0     # hay hojas con valores económicos que no entraron al cálculo -> como máximo MEDIA
CAP_COBERTURA_BAJA = 55.0       # menos del 60 % de los registros económicos participó -> como máximo BAJA


def _ratio(num: float, den: float) -> float:
    return (num / den) if den else 0.0


def _pct(num: float, den: float):
    return round(100 * num / den, 1) if den else None


def compute_coverage(hojas: list, secondary_used=None) -> dict:
    """Cobertura analítica a partir del estado de cada hoja recibida.

    - AUXILIAR: no participa (notas, parámetros); se informa pero no penaliza.
    - Solo importan para la confianza las hojas que traen MEDIDAS económicas (ingresos, costos, km, litros).
    - Una hoja "secundaria" (medidas sin ID de viaje) cuenta como incorporada solo si el motor la usó de verdad.
    """
    secondary_used = secondary_used or set()
    items = []
    for h in hojas:
        h = dict(h)
        if h.get("secundaria") and h.get("tabla") in secondary_used:
            h["estado"], h["rol"] = "INCORPORADA", "POR_VEHICULO"
            h["motivo"] = "Se usó para calcular costos y margen por vehículo."
        items.append(h)

    modelables = [h for h in items if h["estado"] != "AUXILIAR"]
    incorporadas = [h for h in modelables if h["estado"] == "INCORPORADA"]
    no_inc = [h for h in modelables if h["estado"] != "INCORPORADA"]
    econ = [h for h in modelables if h.get("aporta_medidas")]
    econ_inc = [h for h in econ if h["estado"] == "INCORPORADA"]
    econ_no_inc = [h for h in econ if h["estado"] != "INCORPORADA"]

    return {
        "hojas": items,
        "hojas_recibidas": len(items),
        "hojas_auxiliares": len(items) - len(modelables),
        "hojas_modelables": len(modelables),
        "hojas_incorporadas": len(incorporadas),
        "hojas_no_incorporadas": [h["nombre"] for h in no_inc],
        "hojas_con_medidas_no_incorporadas": [h["nombre"] for h in econ_no_inc],
        "medidas_no_incorporadas": sorted({m for h in econ_no_inc for m in h.get("medidas", [])}),
        "registros_recibidos": sum(h["filas"] for h in modelables),
        "registros_incorporados": sum(h["filas"] for h in incorporadas),
        "cobertura_hojas": _pct(len(incorporadas), len(modelables)),
        "cobertura_economica": _pct(sum(h["filas"] for h in econ_inc), sum(h["filas"] for h in econ)),
    }


class DataQualityGate:
    def evaluate(self, master: pd.DataFrame, parse_stats: dict, merge_report: dict, coverage=None) -> dict:
        motivos: list = []

        def add(codigo: str, severidad: str, mensaje: str) -> None:
            motivos.append({"codigo": codigo, "severidad": severidad, "mensaje": mensaje})

        if master is None or master.empty:
            add("SIN_DATOS", "BLOQUEANTE", "Ninguna columna quedó asignada a un campo del modelo: no hay datos que analizar.")
            return self._result(0.0, 0.0, motivos, {"filas": 0}, coverage)

        n = len(master)
        cols = set(master.columns)
        cost_cols = [c for c in COST_COLUMNS if c in cols]
        has_rev = "REVENUE" in cols

        # --- Métricas de limpieza (sobre lo que entró al análisis) -----------------------------------
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
            "tasa_lectura_numerica": round(tasa_lectura, 1), "cobertura_cruce": cobertura_cruce,
        }
        quality = (completitud + unicidad + tasa_lectura) / 3

        # --- Confianza analítica ---------------------------------------------------------------------
        confidence = 100.0
        cap = 100.0
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

        # --- Cobertura: lo que NO entró al cálculo limita la confianza --------------------------------
    if not coverage:
        add("SIN_COBERTURA", "ALTA", "El pipeline no reportó cobertura; la confianza puede estar sobreestimada.")
    else:
        pend = coverage.get("hojas_con_medidas_no_incorporadas", [])
        
        # --- ¡EL GOLPE DE GRACIA! ---
        if not pend and coverage.get("hojas_no_incorporadas"):
            pend = coverage.get("hojas_no_incorporadas")
        # ----------------------------

        if pend:
            cap = min(cap, CAP_MEDIDAS_SIN_USAR)
            medidas = ", ".join(LABELS.get(m, m) for m in coverage.get("medidas_no_incorporadas", []))
            hojas_txt = ", ".join(f"«{h}»" for h in pend)
            add("MEDIDAS_NO_INCORPORADAS", "ALTA",
                f"{'La hoja' if len(pend) == 1 else 'Las hojas'} {hojas_txt} "
                f"{'trae' if len(pend) == 1 else 'traen'} valores económicos ({medidas}) que NO entraron al cálculo. "
                "El resultado puede estar incompleto hasta que se puedan cruzar.")
        econ = coverage.get("cobertura_economica")
        if econ is not None and econ < 60:
            cap = min(cap, CAP_COBERTURA_BAJA)
            add("COBERTURA_BAJA", "ALTA", f"Solo el {econ:.0f}% de los registros con valores económicos participó en el cálculo.")
        otras = [h for h in coverage.get("hojas_no_incorporadas", []) if h not in pend]
        if otras:
            add("HOJA_SIN_INCORPORAR", "BAJA",
                "Hojas de referencia sin valores económicos que no cambian el cálculo: " + ", ".join(f"«{h}»" for h in otras) + ".")
        metricas["cobertura_hojas"] = coverage.get("cobertura_hojas")
        metricas["cobertura_economica"] = econ

    confidence = min(confidence, cap)
    return self._result(quality, max(0.0, confidence), motivos, metricas, coverage)

    @staticmethod
    def _result(quality: float, confidence: float, motivos: list, metricas: dict, coverage=None) -> dict:
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
            "bloqueante": blocked, "motivos": motivos, "metricas": metricas, "cobertura": coverage,
        }
