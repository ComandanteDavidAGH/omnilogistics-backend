"""Orquestación: consolidar -> compuerta de calidad -> motor económico.

No depende de FastAPI ni de la base de datos, por eso se puede probar con archivos sintéticos.
"""
from __future__ import annotations

from .engine import EconomicRuleEngine
from .merge import build_master
from .quality import DataQualityGate


def run_pipeline(dfs: dict, mapping: dict, config: dict) -> dict:
    merge = build_master(dfs, mapping)
    gate = DataQualityGate().evaluate(merge.master, merge.parse_stats, merge.report)
    warnings = list(merge.warnings)

    if gate["bloqueante"]:
        return {"estado": "BLOQUEADA", "calidad": gate, "advertencias": warnings, "financials": None,
                "findings": [], "monthly": [], "merge_report": merge.report}

    result = EconomicRuleEngine(config).analyze(merge.master, merge)
    warnings += result["warnings"]
    estado = "OK" if gate["nivelConfianza"] == "ALTA" else "CON_RESERVAS"
    return {"estado": estado, "calidad": gate, "advertencias": warnings, "financials": result["financials"],
            "findings": result["findings"], "monthly": result["monthly"], "merge_report": merge.report}
