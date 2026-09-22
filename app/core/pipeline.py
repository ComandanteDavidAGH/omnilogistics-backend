"""Orquestación central de Genesis Core v1.3.

Flujo semántico refinado:
  0. ModelBuilder: Clasifica FACT, DIMENSION y REFERENCE y define el grano.
  1. Consolidación de tablas (build_master) + Relationship Engine.
  2. Evaluación de Calidad e Integridad del Modelo (QualityEngine).
  3. Cálculo de Cobertura y Confianza Analítica Acotada (ConfidenceEngine).
  4. Ejecución del Motor de Reglas Económicas (EconomicRuleEngine).
"""
from __future__ import annotations

from .confidence import ConfidenceEngine
from .engine import EconomicRuleEngine
from .merge import build_master
from .model_builder import ModelBuilder
from .quality import QualityEngine, compute_coverage


def run_pipeline(dfs: dict, mapping: dict, config: dict) -> dict:
    # 0. Inferencia Estructural del Modelo de Datos
    data_model = ModelBuilder().build_model(dfs, mapping)

    # 1. Consolidación impulsada por Relationship Engine
    # Pasamos implicit_base si ModelBuilder identificó claramente la tabla de viajes
    merge = build_master(dfs, mapping, implicit_base=data_model.base_table)

    # 2. Construcción de metadatos de cobertura por hoja y grano
    hojas_metadatos = []
    base_name = merge.report.get("base")
    joins_by_table = {j["tabla"]: j for j in merge.report.get("joins", [])}

    for name, df in dfs.items():
        if df is None or df.empty:
            continue
            
        f_len = len(df)
        j_info = joins_by_table.get(name)
        meta_tb = data_model.tablas.get(name)

        if name == base_name or (j_info and j_info.get("modo") in ("por_viaje", "atributos")):
            estado = "INCORPORADA"
        else:
            estado = "NO_INCORPORADA"

        scoped = mapping.get(name, {}) or {}
        medidas = [c for c in scoped.values() if c and c != "UNKNOWN" and not c.startswith("TRIP_") and not c.startswith("VEHICLE_")]
        aporta = len(medidas) > 0

        hojas_metadatos.append({
            "nombre": name,
            "tabla": name,
            "estado": estado,
            "filas": f_len,
            "aporta_medidas": aporta,
            "medidas": medidas,
            "secundaria": j_info.get("modo") == "aparte" if j_info else False,
            "tipo_tabla": meta_tb.table_type.value if meta_tb else "UNKNOWN",
            "grano": meta_tb.grain.value if meta_tb else "UNKNOWN"
        })

    sec_used = {s["name"] for s in merge.secondary}
    coverage = compute_coverage(hojas_metadatos, secondary_used=sec_used)

    # 3. Motor de Calidad de Datos e Integridad del Modelo
    quality_report = QualityEngine().evaluate(merge.master, merge.parse_stats, merge.report)

    # 4. Motor de Confianza Analítica Acotada
    confidence_report = ConfidenceEngine.calculate(quality_report, coverage=coverage, merge_report=merge.report)

    # Payload estandarizado para compatibilidad total con Frontend (Vercel)
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
