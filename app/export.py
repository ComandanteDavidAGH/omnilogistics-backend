"""Exportación a Excel de una auditoría económica (v1.2 Enterprise).

Genera un libro multi-pestaña con trazabilidad ejecutiva:
  - Resumen: Indicadores financieros, desglose de confianza y calidad.
  - Hallazgos: Tabla de hallazgos con Regla, Variable, Benchmark y Valor detectado.
  - Casos: Evidencia detallada registro a registro.
  - Serie mensual: Tendencia de ingresos, costos y margen.
  - Tareas: Plan de acción priorizado por departamento.

Protección contra inyección de fórmulas:
Cualquier celda de texto que comience con =, +, -, @ se escapa con una comilla (').
"""
from __future__ import annotations

import io
import pandas as pd

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")
_CASE_FIRST = ["hallazgo", "hoja", "fila", "trip_id", "vehiculo", "ruta", "cliente", "fecha", "ingreso", "costo"]


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
        ("Archivo", audit.get("filename")),
        ("Fecha de análisis", audit.get("timestamp")),
        ("Estado de la auditoría", audit.get("estado")),
        ("Nivel de confianza", cal.get("nivelConfianza")),
        ("Calidad de los datos (0-100)", cal.get("data_quality_score")),
        ("Confianza analítica (0-100)", cal.get("analytical_confidence")),
        ("Versión del motor", audit.get("engine_version")),
        ("", ""),
        ("Ingresos (COP, sin duplicados exactos)", fin.get("totalIngresos")),
        ("Costos (COP)", fin.get("totalCostos")),
        ("Margen global % (base comparable)", fin.get("margenGlobal")),
        ("Costos incluidos en el margen", ", ".join(fin.get("costosIncluidos") or [])),
        ("Dinero en riesgo (COP)", fin.get("dineroEnRiesgo")),
        ("    Posible sobrefacturación por duplicados", (fin.get("dineroEnRiesgoDetalle") or {}).get("sobrefacturacion_potencial")),
        ("    Pérdida directa (costo > ingreso)", (fin.get("dineroEnRiesgoDetalle") or {}).get("perdida_directa")),
        ("Filas analizadas", fin.get("filasAnalizadas")),
        ("", ""),
    ]
    resumen += [("Advertencia", w) for w in audit.get("advertencias") or []]
    resumen += [(f"Motivo ({m.get('severidad', 'INFO')})", m.get("mensaje")) for m in cal.get("motivos") or []]

    # --- Pestaña Hallazgos con Trazabilidad B2B ---
    hallazgos_rows = []
    for f in findings:
        evi = f.get("evidencia") or {}
        acc = f.get("accion") or {}
        imp = f.get("impacto") or {}
        hallazgos_rows.append({
            "Prioridad": f.get("prioridad"),
            "Tipo": f.get("tipo"),
            "Severidad": f.get("severidad"),
            "Título": f.get("titulo"),
            "Causa": f.get("causa"),
            "Impacto (COP)": imp.get("impacto_directo"),
            "Tipo de impacto": imp.get("descripcion"),
            "Regla de negocio": evi.get("regla_negocio", "N/A"),
            "Variable evaluada": evi.get("variable_evaluada", "N/A"),
            "Benchmark esperado": evi.get("benchmark_esperado", "N/A"),
            "Valor detectado": evi.get("valor_detectado", "N/A"),
            "Método de análisis": evi.get("metodo"),
            "Nivel de evidencia": evi.get("nivel_evidencia"),
            "Casos totales": f.get("casos_total"),
            "Departamento": acc.get("departamento"),
            "Acción sugerida": acc.get("accion"),
            "Urgencia": acc.get("urgencia"),
            "Vehículos afectados": ", ".join(f.get("vehiculos_afectados") or []),
        })
    hallazgos = pd.DataFrame(hallazgos_rows)

    # --- Pestaña Casos ---
    rows = []
    for f in findings:
        for c in f.get("casos") or []:
            rows.append({"hallazgo": f"#{f.get('prioridad')} {f.get('titulo')}", **c})
    casos = pd.DataFrame(rows)
    if not casos.empty:
        first = [c for c in _CASE_FIRST if c in casos.columns]
        casos = casos[first + [c for c in casos.columns if c not in first]]

    mensual = pd.DataFrame(audit.get("monthly") or [])

    # --- Pestaña Tareas ---
    tareas = pd.DataFrame([{
        "Tarea": t.get("title"),
        "Departamento": t.get("department"),
        "Impacto (COP)": t.get("financial_impact"),
        "Urgencia": t.get("urgency"),
        "Estado": t.get("status"),
        "Comentario": t.get("comment") or "",
        "Descripción": t.get("description"),
    } for t in tasks])

    # --- Escritura del Libro ---
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
