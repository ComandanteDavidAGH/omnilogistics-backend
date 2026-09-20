"""Exportación a Excel de una auditoría, con todos los casos de evidencia.

Los textos que vienen de los archivos del cliente se neutralizan contra "inyección de fórmulas":
una celda que empiece por = + - @ se guardaría como fórmula y podría ejecutarse al abrir el Excel.
"""
from __future__ import annotations

import io

import pandas as pd

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")
_CASE_FIRST = ["hoja", "fila", "trip_id", "vehiculo", "ruta", "cliente", "fecha", "ingreso", "costo"]


def xl_safe(value):
    if isinstance(value, str) and value[:1] in _DANGEROUS:
        return "'" + value
    return value


def _safe_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object or str(out[col].dtype).startswith("str"):
            out[col] = out[col].map(xl_safe)
    out.columns = [xl_safe(str(c)) for c in out.columns]
    return out


def _autofit(ws) -> None:
    for column in ws.columns:
        width = max((len(str(c.value)) for c in column if c.value is not None), default=8)
        ws.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 60)
    ws.freeze_panes = "A2"


def build_audit_workbook(audit: dict, findings: list, tasks: list) -> bytes:
    fin = audit.get("financials") or {}
    cal = audit.get("calidad") or {}

    resumen = [
        ("Archivo", audit.get("filename")), ("Fecha de análisis", audit.get("timestamp")),
        ("Estado", audit.get("estado")), ("Nivel de confianza", cal.get("nivelConfianza")),
        ("Calidad de datos (0-100)", cal.get("data_quality_score")),
        ("Confianza analítica (0-100)", cal.get("analytical_confidence")),
        ("Versión del motor", audit.get("engine_version")),
        ("", ""),
        ("Ingresos (COP, sin duplicados exactos)", fin.get("totalIngresos")),
        ("Costos (COP)", fin.get("totalCostos")),
        ("Margen global % (base comparable)", fin.get("margenGlobal")),
        ("Costos incluidos en el margen", ", ".join(fin.get("costosIncluidos") or [])),
        ("Dinero en riesgo (COP)", fin.get("dineroEnRiesgo")),
        ("   Posible sobrefacturación por duplicados", (fin.get("dineroEnRiesgoDetalle") or {}).get("sobrefacturacion_potencial")),
        ("   Pérdida directa (costo > ingreso)", (fin.get("dineroEnRiesgoDetalle") or {}).get("perdida_directa")),
        ("Filas analizadas", fin.get("filasAnalizadas")),
        ("", ""),
    ]
    resumen += [("Advertencia", w) for w in audit.get("advertencias") or []]
    resumen += [(f"Calidad: {m['codigo']}", m["mensaje"]) for m in cal.get("motivos") or []]

    hallazgos = pd.DataFrame([{
        "Prioridad": f["prioridad"], "Tipo": f["tipo"], "Severidad": f["severidad"], "Título": f["titulo"],
        "Causa": f["causa"], "Impacto (COP)": f["impacto"]["impacto_directo"], "Tipo de impacto": f["impacto"]["descripcion"],
        "Casos": f["casos_total"], "Método": (f.get("evidencia") or {}).get("metodo"),
        "Nivel de evidencia": (f.get("evidencia") or {}).get("nivel_evidencia"),
        "Departamento": f["accion"]["departamento"], "Acción": f["accion"]["accion"], "Urgencia": f["accion"]["urgencia"],
        "Vehículos": ", ".join(f.get("vehiculos_afectados") or []),
    } for f in findings])

    rows = []
    for f in findings:
        for c in f.get("casos") or []:
            rows.append({"hallazgo": f"#{f['prioridad']} {f['titulo']}", **c})
    casos = pd.DataFrame(rows)
    if not casos.empty:
        first = ["hallazgo"] + [c for c in _CASE_FIRST if c in casos.columns]
        casos = casos[first + [c for c in casos.columns if c not in first]]

    mensual = pd.DataFrame(audit.get("monthly") or [])
    tareas = pd.DataFrame([{
        "Tarea": t["title"], "Departamento": t["department"], "Impacto (COP)": t["financial_impact"],
        "Urgencia": t.get("urgency"), "Estado": t["status"], "Comentario": t.get("comment") or "",
        "Acción sugerida": t["description"],
    } for t in tasks])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        _safe_frame(pd.DataFrame(resumen, columns=["Concepto", "Valor"])).to_excel(xw, sheet_name="Resumen", index=False)
        _safe_frame(hallazgos).to_excel(xw, sheet_name="Hallazgos", index=False)
        _safe_frame(casos).to_excel(xw, sheet_name="Casos", index=False)
        _safe_frame(mensual).to_excel(xw, sheet_name="Serie mensual", index=False)
        _safe_frame(tareas).to_excel(xw, sheet_name="Tareas", index=False)
        for ws in xw.book.worksheets:
            _autofit(ws)
    return buf.getvalue()
