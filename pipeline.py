"""Orquestación: consolidar -> COBERTURA -> compuerta de calidad -> motor económico.

No depende de FastAPI ni de la base de datos, por eso se puede probar con archivos sintéticos.
"""
from __future__ import annotations

try:  # estructura de paquete (app/core/...)
    from .engine import EconomicRuleEngine
    from .merge import build_master
    from .quality import DataQualityGate, compute_coverage
except ImportError:  # estructura plana (archivos sueltos en una carpeta)
    from engine import EconomicRuleEngine
    from merge import build_master
    from quality import DataQualityGate, compute_coverage


def run_pipeline(dfs: dict, mapping: dict, config: dict) -> dict:
    merge = build_master(dfs, mapping)
    warnings = list(merge.warnings)

    if merge.master.empty:
        coverage = compute_coverage(merge.report.get("hojas", []))
        gate = DataQualityGate().evaluate(merge.master, merge.parse_stats, merge.report, coverage)
        return {"estado": "BLOQUEADA", "calidad": gate, "advertencias": warnings, "financials": None,
                "findings": [], "monthly": [], "merge_report": merge.report}

    # El motor económico corre primero (aunque el resultado se descarte si la compuerta bloquea)
    # porque necesitamos saber qué hojas secundarias usó de verdad para calcular la cobertura real.
    result = EconomicRuleEngine(config).analyze(merge.master, merge)
    coverage = compute_coverage(merge.report.get("hojas", []), result.get("secondary_used"))
    gate = DataQualityGate().evaluate(merge.master, merge.parse_stats, merge.report, coverage)

    if gate["bloqueante"]:
        return {"estado": "BLOQUEADA", "calidad": gate, "advertencias": warnings, "financials": None,
                "findings": [], "monthly": [], "merge_report": merge.report}

    warnings += result["warnings"]
    estado = "OK" if gate["nivelConfianza"] == "ALTA" else "CON_RESERVAS"
    return {"estado": estado, "calidad": gate, "advertencias": warnings, "financials": result["financials"],
            "findings": result["findings"], "monthly": result["monthly"], "merge_report": merge.report}
