"""Exportador de Auditorías Económicas de Genesis Core v1.2.

Genera libros de Excel con diseño ejecutivo gerencial C-Level:
  - Banner institucional superior unificado en TODAS las pestañas.
  - Título explicativo por pestaña y subtítulo con nombre del archivo/fecha.
  - Estética limpia sin líneas de cuadrícula (showGridLines = False).
  - Formato de celdas estricto (COP $, %, enteros).
  - Auto-ajuste de ancho de columnas y protección contra inyección de fórmulas.
"""
from __future__ import annotations

import io
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# --- PALETA CORPORATIVA GENESIS ---
COLOR_HEADER_BG = "0F172A"       # Azul Marino Oscuro / Navy (Fila 1 Banner)
COLOR_HEADER_TEXT = "FFFFFF"     # Blanco
COLOR_TABLE_HEADER = "1E3A8A"   # Azul Corporativo (Encabezado de Tabla)
COLOR_ZEBRA = "F8FAFC"           # Gris ultra claro para filas pares
COLOR_BORDER = "CBD5E1"          # Gris bordes suaves
COLOR_CARD_BG = "F1F5F9"         # Fondo para tarjetas KPI

SEVERITY_STYLES = {
    "ALTA": {"fill": "FEE2E2", "font": "991B1B"},      # Rojo suave
    "MEDIA": {"fill": "FEF3C7", "font": "92400E"},     # Ámbar suave
    "BAJA": {"fill": "DCFCE7", "font": "166534"},      # Verde suave
    "PENDIENTE": {"fill": "E2E8F0", "font": "334155"}  # Gris neutral
}

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _xl_safe(value):
    if isinstance(value, str) and value[:1] in _DANGEROUS:
        return "'" + value
    return value


def _add_sheet_banner(ws, title_text: str, subtitle_text: str, max_col: int = 12):
    """Crea el banner institucional estandarizado en las filas 1 y 2 de cada pestaña."""
    col_letter = get_column_letter(max(max_col, 8))
    ws.merge_cells(f"A1:{col_letter}1")
    
    title_cell = ws["A1"]
    title_cell.value = _xl_safe(title_text)
    title_cell.font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill(start_color=COLOR_HEADER_BG, end_color=COLOR_HEADER_BG, fill_type="solid")
    title_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 38

    ws.merge_cells(f"A2:{col_letter}2")
    sub_cell = ws["A2"]
    sub_cell.value = _xl_safe(subtitle_text)
    sub_cell.font = Font(name="Calibri", size=10, italic=True, color="475569")
    sub_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[2].height = 20


def _apply_corporate_table(ws, start_row: int, df: pd.DataFrame, currency_cols=None, pct_cols=None):
    currency_cols = currency_cols or []
    pct_cols = pct_cols or []
    thin_border = Border(
        left=Side(style="thin", color=COLOR_BORDER),
        right=Side(style="thin", color=COLOR_BORDER),
        top=Side(style="thin", color=COLOR_BORDER),
        bottom=Side(style="thin", color=COLOR_BORDER)
    )

    # 1. Encabezados de Tabla (Fila start_row)
    for col_idx, col_name in enumerate(df.columns, 1):
        cell = ws.cell(row=start_row, column=col_idx)
        cell.value = _xl_safe(col_name)
        cell.font = Font(name="Calibri", size=11, bold=True, color=COLOR_HEADER_TEXT)
        cell.fill = PatternFill(start_color=COLOR_TABLE_HEADER, end_color=COLOR_TABLE_HEADER, fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[start_row].height = 26

    # 2. Filas de Datos
    for row_idx, row_data in enumerate(df.values, start_row + 1):
        is_even = (row_idx % 2 == 0)
        row_fill = PatternFill(start_color=COLOR_ZEBRA, end_color=COLOR_ZEBRA, fill_type="solid") if is_even else None

        for col_idx, val in enumerate(row_data, 1):
            col_name = df.columns[col_idx - 1]
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = _xl_safe(val)
            cell.font = Font(name="Calibri", size=10)
            cell.border = thin_border
            if row_fill:
                cell.fill = row_fill

            # Formato y alineación por tipo de columna
            if col_name in currency_cols:
                cell.number_format = '"$"#,##0'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_name in pct_cols:
                cell.number_format = '0.0%'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif isinstance(val, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

            # Formato de Badges para Severidad / Urgencia
            str_val = str(val).upper()
            if str_val in SEVERITY_STYLES:
                style = SEVERITY_STYLES[str_val]
                cell.fill = PatternFill(start_color=style["fill"], end_color=style["fill"], fill_type="solid")
                cell.font = Font(name="Calibri", size=10, bold=True, color=style["font"])
                cell.alignment = Alignment(horizontal="center", vertical="center")

        ws.row_dimensions[row_idx].height = 20

    # Desactivar líneas de cuadrícula para acabado ejecutivo limpio
    ws.views.sheetView[0].showGridLines = False
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)

    # Auto-ajuste de ancho de columnas
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 55)


def build_audit_workbook(audit: dict, findings: list, tasks: list) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # Eliminar pestaña por defecto

    fin = audit.get("financials") or {}
    cal = audit.get("calidad") or {}
    filename = audit.get("filename") or "Auditoria"
    timestamp = audit.get("timestamp") or ""
    sub_info = f"Cliente / Archivo: {filename} | Fecha de Auditoría: {timestamp}"

    # ---------------------------------------------------------------------------
    # PESTAÑA 1: RESUMEN EJECUTIVO
    # ---------------------------------------------------------------------------
    ws_resumen = wb.create_sheet(title="Resumen Ejecutivo")
    _add_sheet_banner(ws_resumen, "GENESIS CORE v1.2 — INFORME DE AUDITORÍA ECONÓMICA", sub_info, max_col=8)

    # Tarjetas KPI
    kpis = [
        ("TOTAL INGRESOS", fin.get("totalIngresos"), '"$"#,##0'),
        ("TOTAL COSTOS", fin.get("totalCostos"), '"$"#,##0'),
        ("MARGEN GLOBAL", (fin.get("margenGlobal") or 0) / 100, '0.0%'),
        ("DINERO EN RIESGO", fin.get("dineroEnRiesgo"), '"$"#,##0'),
    ]

    for i, (label, val, fmt) in enumerate(kpis):
        col_start = 1 + (i * 2)
        c1 = ws_resumen.cell(row=4, column=col_start)
        c2 = ws_resumen.cell(row=5, column=col_start)
        c1.value = label
        c1.font = Font(name="Calibri", size=9, bold=True, color="64748B")
        c1.fill = PatternFill(start_color=COLOR_CARD_BG, end_color=COLOR_CARD_BG, fill_type="solid")
        c2.value = val
        c2.font = Font(name="Calibri", size=14, bold=True, color=COLOR_HEADER_BG)
        c2.number_format = fmt
        c2.fill = PatternFill(start_color=COLOR_CARD_BG, end_color=COLOR_CARD_BG, fill_type="solid")

    ws_resumen.row_dimensions[4].height = 18
    ws_resumen.row_dimensions[5].height = 28

    resumen_data = [
        ["Estado de la Auditoría", audit.get("estado")],
        ["Nivel de Confianza Analítica", cal.get("nivelConfianza")],
        ["Puntaje Calidad de Datos", f"{cal.get('data_quality_score')}/100"],
        ["Confianza Analítica Calculada", f"{cal.get('analytical_confidence')}/100"],
        ["Filas Analizadas", fin.get("filasAnalizadas")],
        ["Duplicados Exactos Encontrados", fin.get("duplicadosExactos")],
        ["Pérdida Directa Detectada", (fin.get("dineroEnRiesgoDetalle") or {}).get("perdida_directa")],
        ["Versión del Motor", audit.get("engine_version")],
    ]
    df_resumen = pd.DataFrame(resumen_data, columns=["Métrica / Indicador", "Valor Evaluado"])
    _apply_corporate_table(ws_resumen, start_row=8, df=df_resumen, currency_cols=["Valor Evaluado"])

    # ---------------------------------------------------------------------------
    # PESTAÑA 2: HALLAZGOS Y TRAZABILIDAD
    # ---------------------------------------------------------------------------
    ws_hallazgos = wb.create_sheet(title="Hallazgos")
    hallazgos_rows = []
    for f in findings:
        evi = f.get("evidencia") or {}
        acc = f.get("accion") or {}
        imp = f.get("impacto") or {}
        hallazgos_rows.append({
            "Prioridad": f.get("prioridad"),
            "Severidad": f.get("severidad"),
            "Título del Hallazgo": f.get("titulo"),
            "Impacto (COP)": imp.get("impacto_directo"),
            "Tipo de Impacto": imp.get("descripcion"),
            "Regla de Negocio": evi.get("regla_negocio", "N/A"),
            "Variable Evaluada": evi.get("variable_evaluada", "N/A"),
            "Benchmark Esperado": evi.get("benchmark_esperado", "N/A"),
            "Valor Detectado": evi.get("valor_detectado", "N/A"),
            "Departamento": acc.get("departamento"),
            "Acción Sugerida": acc.get("accion"),
            "Urgencia": acc.get("urgencia"),
        })
    df_hallazgos = pd.DataFrame(hallazgos_rows)
    _add_sheet_banner(ws_hallazgos, "MATRIZ DE HALLAZGOS Y TRAZABILIDAD ECONÓMICA", sub_info, max_col=len(df_hallazgos.columns))
    _apply_corporate_table(ws_hallazgos, start_row=4, df=df_hallazgos, currency_cols=["Impacto (COP)"])

    # ---------------------------------------------------------------------------
    # PESTAÑA 3: PLAN DE ACCIÓN Y TAREAS
    # ---------------------------------------------------------------------------
    ws_tareas = wb.create_sheet(title="Plan de Acción")
    tareas_rows = [{
        "Tarea": t.get("title"),
        "Departamento": t.get("department"),
        "Impacto (COP)": t.get("financial_impact"),
        "Urgencia": t.get("urgency"),
        "Estado": t.get("status"),
        "Acción Recomendada": t.get("description"),
    } for t in tasks]
    df_tareas = pd.DataFrame(tareas_rows)
    _add_sheet_banner(ws_tareas, "PLAN DE ACCIÓN Y RUTEO DE TAREAS POR DEPARTAMENTO", sub_info, max_col=len(df_tareas.columns))
    _apply_corporate_table(ws_tareas, start_row=4, df=df_tareas, currency_cols=["Impacto (COP)"])

    # ---------------------------------------------------------------------------
    # PESTAÑA 4: SERIE MENSUAL
    # ---------------------------------------------------------------------------
    if audit.get("monthly"):
        ws_mensual = wb.create_sheet(title="Serie Mensual")
        df_mensual = pd.DataFrame(audit.get("monthly"))
        if "margen_pct" in df_mensual.columns:
            df_mensual["margen_pct"] = df_mensual["margen_pct"] / 100.0
        df_mensual.rename(columns={
            "periodo": "Periodo", "viajes": "Viajes", "ingresos": "Ingresos (COP)",
            "costos": "Costos (COP)", "margen_pct": "Margen %"
        }, inplace=True)
        _add_sheet_banner(ws_mensual, "SERIE MENSUAL Y TENDENCIA FINANCIERA DE LA FLOTA", sub_info, max_col=len(df_mensual.columns))
        _apply_corporate_table(ws_mensual, start_row=4, df=df_mensual, 
                              currency_cols=["Ingresos (COP)", "Costos (COP)"], pct_cols=["Margen %"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
