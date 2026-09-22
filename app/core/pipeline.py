"""Orquestación central de Genesis Core v1.2.

Flujo semántico refinado:
  1. Consolidación de tablas (build_master) + Relationship Engine
  2. Evaluación de Calidad e Integridad del Modelo (QualityEngine)
  3. Cálculo de Cobertura y Confianza Analítica Acotada (ConfidenceEngine)
  4. Ejecución del Motor de Reglas Económicas (EconomicRuleEngine)
"""
from __future__ import annotations

from .confidence import ConfidenceEngine
from .engine import EconomicRuleEngine
from .merge import build_master
from .quality import QualityEngine, compute_coverage


def run_pipeline(dfs: dict, mapping: dict, config: dict) -> dict:
    # 1. Consolidación previa impulsada por el Relationship Engine
    merge = build_master(dfs, mapping)

    # 2. Construcción de metadatos de cobertura por hoja
    hojas_metadatos = []
    base_name = merge.report.get("base")
    joins_by_table = {j["tabla"]: j for j in merge.report.get("joins", [])}

    for t in merge.report.get("tablas", []):
        nombre = t["nombre"]
        f_len = t.get("filas", 0)
        j_info = joins_by_table.get(nombre)

        if nombre == base_name or (j_info and j_info.get("modo") in ("por_viaje", "atributos")):
            estado = "INCORPORADA"
        else:
            estado = "NO_INCORPORADA"

        scoped = mapping.get(nombre, {}) or {}
        medidas = [c for c in scoped.values() if c and c != "UNKNOWN" and not c.startswith("TRIP_") and not c.startswith("VEHICLE_")]
        aporta = len(medidas) > 0

        hojas_metadatos.append({
            "nombre": nombre,
            "tabla": nombre,
            "estado": estado,
            "filas": f_len,
            "aporta_medidas": aporta,
            "medidas": medidas,
            "secundaria": j_info.get("modo") == "aparte" if j_info else False
        })

    sec_used = {s["name"] for s in merge.secondary}
    coverage = compute_coverage(hojas_metadatos, secondary_used=sec_used)

    # 3. Motor de Calidad de Datos e Integridad del Modelo
    quality_report = QualityEngine().evaluate(merge.master, merge.parse_stats, merge.report)

    # 4. Motor de Confianza Analítica Acotada (Aplica caps metodológicos de cobertura)
    confidence_report = ConfidenceEngine.calculate(quality_report, coverage=coverage, merge_report=merge.report)

    # Payload estandarizado de respuesta para mantener compatibilidad con API y Frontend
    calidad_payload = {
        "scoreGlobal": quality_report.data_quality_score,
        "data_quality_score": quality_report.data_quality_score,
        "analytical_confidence": confidence_report.analytical_confidence,
        "nivelConfianza": confidence_report.confidence_level,
        "bloqueante": quality_report.is_blocked,
        "motivos": quality_report.motivos,
        "metricas": quality_report.metricas,
        "cobertura": coverage,
        "breakdown_confianza": confidence_report.breakdown
    }

    warnings = list(merge.warnings)

    # Si la calidad bloquea o la confianza cae a niveles críticos, interrumpe el análisis económico
    if quality_report.is_blocked or confidence_report.confidence_level == "BLOQUEADA":
        return {
            "estado": "BLOQUEADA",
            "calidad": calidad_payload,
            "advertencias": warnings,
            "financials": None,
            "findings": [],
            "monthly": [],
            "merge_report": merge.report
        }

    # 5. Ejecución del Motor Económico
    result = EconomicRuleEngine(config).analyze(merge.master, merge)
    warnings += result["warnings"]

    estado = "OK" if confidence_report.confidence_level == "ALTA" else "CON_RESERVAS"

    return {
        "estado": estado,
        "calidad": calidad_payload,
        "advertencias": warnings,
        "financials": result["financials"],
        "findings": result["findings"],
        "monthly": result["monthly"],
        "merge_report": merge.report
    }
