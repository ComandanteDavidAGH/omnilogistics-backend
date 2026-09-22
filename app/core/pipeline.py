"""Orquestación: consolidar -> compuerta de calidad -> motor económico.

No depende de FastAPI ni de la base de datos, por eso se puede probar con archivos sintéticos.
"""
from __future__ import annotations

from .engine import EconomicRuleEngine
from .merge import build_master
from .quality import DataQualityGate, compute_coverage


def run_pipeline(dfs: dict, mapping: dict, config: dict) -> dict:
    merge = build_master(dfs, mapping)

    # Construcción explícita del estado de cobertura por hoja
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

    gate = DataQualityGate().evaluate(merge.master, merge.parse_stats, merge.report, coverage=coverage)
    warnings = list(merge.warnings)

    if gate["bloqueante"]:
        return {"estado": "BLOQUEADA", "calidad": gate, "advertencias": warnings, "financials": None,
                "findings": [], "monthly": [], "merge_report": merge.report}

    result = EconomicRuleEngine(config).analyze(merge.master, merge)
    warnings += result["warnings"]
    estado = "OK" if gate["nivelConfianza"] == "ALTA" else "CON_RESERVAS"
    return {"estado": estado, "calidad": gate, "advertencias": warnings, "financials": result["financials"],
            "findings": result["findings"], "monthly": result["monthly"], "merge_report": merge.report}
