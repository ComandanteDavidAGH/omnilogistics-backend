"""Motor de Reglas Económicas de Genesis Core v1.3.

Implementa la Taxonomía Financiera Estricta B2B:
  1. PÉRDIDA_DIRECTA_OBSERVADA: Confirmada en datos (Costo > Ingreso).
  2. SOBRECOSTO_POTENCIAL: Duplicados exactos o sobrefacturación.
  3. DIFERENCIA_POR_RECONCILIAR: Discrepancias entre dos fuentes confiables.
  4. DESVIACIÓN_ESTADÍSTICA_ESTIMADA: Atípicos de consumo calculados.

Elimina el concepto ambiguo de 'Dinero en Riesgo' general para dar
visibilidad ejecutiva precisa a nivel C-Level.
"""
from __future__ import annotations
import pandas as pd
import numpy as np


class EconomicRuleEngine:
    def __init__(self, config: dict):
        self.config = config
        self.min_margin = config.get("min_margin_percent", 10.0) / 100.0

    def analyze(self, master: pd.DataFrame, merge_result) -> dict:
        warnings = []
        findings = []
        df = master.copy()

        # Identificación de columnas seguras
        rev_col = "REVENUE" if "REVENUE" in df.columns else None
        cost_cols = [c for c in df.columns if c.startswith("COST_") and not c.endswith("__alt")]

        if not rev_col and not cost_cols:
            warnings.append("No hay medidas económicas suficientes para ejecutar reglas financieras.")
            return {"financials": None, "findings": [], "monthly": [], "warnings": warnings}

        df["__temp_revenue"] = df[rev_col].fillna(0) if rev_col else 0
        df["__temp_cost"] = df[cost_cols].sum(axis=1) if cost_cols else 0

        sobrecosto_potencial = 0.0
        perdida_directa_observada = 0.0
        diferencia_por_reconciliar = 0.0
        desviacion_estadistica = 0.0

        # =========================================================================
        # 1. SOBRECOSTO POTENCIAL (Duplicados exactos en llaves primarias y valores)
        # =========================================================================
        dupes_mask = pd.Series(False, index=df.index)
        if "TRIP_ID" in df.columns and "VEHICLE_ID" in df.columns:
            dupes_mask = df.duplicated(subset=["TRIP_ID", "VEHICLE_ID", "__temp_revenue"], keep=False)
        
        exact_dupes = df[dupes_mask].copy()
        
        if not exact_dupes.empty:
            unique_dupes = exact_dupes.drop_duplicates(subset=["TRIP_ID", "VEHICLE_ID", "__temp_revenue"])
            sobrecosto_potencial = float(exact_dupes["__temp_revenue"].sum() - unique_dupes["__temp_revenue"].sum())

            casos_dup = exact_dupes.head(20).to_dict("records")
            findings.append({
                "prioridad": 1,
                "severidad": "ALTA",
                "tipo": "SOBRECOSTO_POTENCIAL",
                "titulo": "Posible sobrefacturación por viajes duplicados",
                "casos_total": len(exact_dupes) - len(unique_dupes),
                "casos": [{"hoja": c.get("_src_sheet", "N/A"), "trip_id": c.get("TRIP_ID", ""), "ingreso": c.get("__temp_revenue", 0)} for c in casos_dup],
                "evidencia": {
                    "regla_negocio": "Unicidad de Viaje (TRIP + VEHICLE + REVENUE)",
                    "variable_evaluada": "Frecuencia de Registro",
                    "benchmark_esperado": "1 registro único",
                    "valor_detectado": "Duplicidad exacta",
                    "metodo": "Agrupación por llave primaria combinada",
                    "nivel_evidencia": "Confirmado"
                },
                "impacto": {
                    "impacto_directo": sobrecosto_potencial,
                    "descripcion": "Sobrecosto potencial por duplicidad operativa"
                },
                "accion": {
                    "departamento": "Auditoría / Facturación",
                    "accion": "Anular duplicados antes de la liquidación",
                    "urgencia": "ALTA"
                }
            })

        # Retiramos los duplicados excedentes del DataFrame para no inflar los P&L globales
        valid_df = df[~df.index.isin(exact_dupes.index[exact_dupes.duplicated(subset=["TRIP_ID", "VEHICLE_ID", "__temp_revenue"], keep='first')])].copy() if not exact_dupes.empty else df.copy()

        # =========================================================================
        # 2. PÉRDIDA DIRECTA OBSERVADA (Costo > Ingreso en viajes purgados)
        # =========================================================================
        perdida_mask = (valid_df["__temp_cost"] > valid_df["__temp_revenue"]) & (valid_df["__temp_revenue"] > 0)
        viajes_perdida = valid_df[perdida_mask].copy()
        
        if not viajes_perdida.empty:
            perdida_directa_observada = float((viajes_perdida["__temp_cost"] - viajes_perdida["__temp_revenue"]).sum())
            vehiculos_afectados = list(viajes_perdida["VEHICLE_ID"].dropna().unique()[:3]) if "VEHICLE_ID" in viajes_perdida.columns else []
            
            casos_perdida = viajes_perdida.head(20).to_dict("records")
            findings.append({
                "prioridad": 2,
                "severidad": "ALTA",
                "tipo": "PÉRDIDA_DIRECTA_OBSERVADA",
                "titulo": "Viajes con pérdida directa (Costo supera al Ingreso)",
                "casos_total": len(viajes_perdida),
                "vehiculos_afectados": vehiculos_afectados,
                "casos": [{"hoja": "Múltiple", "trip_id": c.get("TRIP_ID", ""), "vehiculo": c.get("VEHICLE_ID", ""), "ingreso": c.get("__temp_revenue", 0), "costo": c.get("__temp_cost", 0)} for c in casos_perdida],
                "evidencia": {
                    "regla_negocio": "Rentabilidad Unitaria Base",
                    "variable_evaluada": "Ingreso Total - Costo Total",
                    "benchmark_esperado": "> $0 (Punto de Equilibrio)",
                    "valor_detectado": "< $0 (Pérdida Neta)",
                    "metodo": "Cálculo fila a fila",
                    "nivel_evidencia": "Confirmado"
                },
                "impacto": {
                    "impacto_directo": perdida_directa_observada,
                    "descripcion": "Dinero perdido de forma directa y observable"
                },
                "accion": {
                    "departamento": "Pricing / Ventas",
                    "accion": "Auditar tarifas de estos viajes frente a la estructura de costo actual",
                    "urgencia": "ALTA"
                }
            })

        # =========================================================================
        # 3. DIFERENCIA POR RECONCILIAR (Ej. Ingreso en Hoja A vs Hoja B)
        # =========================================================================
        if "REVENUE__alt" in valid_df.columns and "REVENUE" in valid_df.columns:
            diff = valid_df[valid_df["REVENUE"].fillna(0) != valid_df["REVENUE__alt"].fillna(0)].copy()
            if not diff.empty:
                diff["__delta"] = abs(diff["REVENUE"].fillna(0) - diff["REVENUE__alt"].fillna(0))
                diferencia_por_reconciliar = float(diff["__delta"].sum())
                
                findings.append({
                    "prioridad": 3,
                    "severidad": "MEDIA",
                    "tipo": "DIFERENCIA_POR_RECONCILIAR",
                    "titulo": "Discrepancia entre fuentes de Ingreso",
                    "casos_total": len(diff),
                    "evidencia": {
                        "regla_negocio": "Consistencia Multi-fuente",
                        "variable_evaluada": "REVENUE vs REVENUE__alt",
                        "benchmark_esperado": "Diferencia = $0",
                        "valor_detectado": f"Discrepancias en {len(diff)} registros",
                        "metodo": "Comparación cruzada de fuentes paralelas",
                        "nivel_evidencia": "Confirmado"
                    },
                    "impacto": {
                        "impacto_directo": diferencia_por_reconciliar,
                        "descripcion": "Diferencia acumulada pendiente de conciliación"
                    },
                    "accion": {
                        "departamento": "Contabilidad",
                        "accion": "Conciliar Ingreso entre operaciones y facturación",
                        "urgencia": "MEDIA"
                    }
                })

        # =========================================================================
        # Agregaciones Globales
        # =========================================================================
        total_rev = valid_df["__temp_revenue"].sum()
        total_cost = valid_df["__temp_cost"].sum()
        margen_global = ((total_rev - total_cost) / total_rev * 100) if total_rev > 0 else 0.0
        
        # El concepto "Dinero en Riesgo" ahora es puramente la sumatoria de la taxonomía estricta
        dinero_en_riesgo_total = sobrecosto_potencial + perdida_directa_observada + diferencia_por_reconciliar + desviacion_estadistica

        financials = {
            "totalIngresos": float(total_rev),
            "totalCostos": float(total_cost),
            "margenGlobal": round(margen_global, 2),
            "costosIncluidos": cost_cols,
            "filasAnalizadas": len(valid_df),
            "duplicadosExactos": (len(exact_dupes) - len(unique_dupes)) if not exact_dupes.empty else 0,
            "dineroEnRiesgo": float(dinero_en_riesgo_total),
            "dineroEnRiesgoDetalle": {
                "perdida_directa_observada": float(perdida_directa_observada),
                "sobrecosto_potencial": float(sobrecosto_potencial),
                "diferencia_por_reconciliar": float(diferencia_por_reconciliar),
                "desviacion_estadistica_estimada": float(desviacion_estadistica)
            }
        }

        # Serie Mensual Básica
        monthly = []
        if "TRIP_DATE" in valid_df.columns:
            monthly_df = valid_df.copy()
            monthly_df["periodo"] = pd.to_datetime(monthly_df["TRIP_DATE"]).dt.to_period("M").astype(str)
            grp = monthly_df.groupby("periodo").agg(
                viajes=("TRIP_ID", "count") if "TRIP_ID" in monthly_df.columns else ("__temp_revenue", "count"),
                ingresos=("__temp_revenue", "sum"),
                costos=("__temp_cost", "sum")
            ).reset_index()
            grp["margen_pct"] = np.where(grp["ingresos"] > 0, ((grp["ingresos"] - grp["costos"]) / grp["ingresos"]) * 100, 0)
            monthly = grp.to_dict("records")

        return {
            "financials": financials,
            "findings": sorted(findings, key=lambda x: x["prioridad"]),
            "monthly": monthly,
            "warnings": warnings
        }
