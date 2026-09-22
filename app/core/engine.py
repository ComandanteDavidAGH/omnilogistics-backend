"""Motor Económico de Genesis Core v1.2.

Evalúa las reglas de negocio sobre la base de datos consolidada.
Características B2B:
  * Trazabilidad absoluta: Cada hallazgo reporta su Regla, Variable, Benchmark y Detectado.
  * Dinero en riesgo riguroso: Separa sobrefacturación de pérdida directa sin solapamientos.
  * Sin heurísticas de limpieza: Asume que el Quality Engine ya midió la salud de los datos.
  * Atípicos robustos: Usa estadística resistente (mediana/MAD) frente a los propios errores.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .merge import MergeResult
from .model import COST_COLUMNS, COST_COMPONENTS, LABELS, VOLUME_COLUMNS

MAX_CASES = 500
DEFAULT_NOTE = "Impacto observado en los datos cargados; no es una proyección anual."
SEVERITY_WEIGHT = {"ALTA": 1.0, "MEDIA": 0.6, "BAJA": 0.3}
IMPACT_WEIGHT = {"perdida_directa": 1.0, "sobrefacturacion_potencial": 1.0, "diferencia_entre_fuentes": 0.6,
                 "estimado": 0.5, "brecha": 0.3, "exposicion": 0.25, "desviacion": 0.2}

DEFAULT_CONFIG = {
    "min_margin_percent": 10.0,
    "z_score_threshold": 3.5,
    "allow_negative_margin": 0,
    "revenue_includes_vat": 0,
    "vat_rate": 0.19,
    "outlier_min_group": 8,
    "reconciliation_tolerance_pct": 1.0,
}

LITROS_POR_GALON = 3.78541


# ---------------------------------------------------------------------------
# Utilidades estadísticas
# ---------------------------------------------------------------------------
def robust_z(x: pd.Series) -> pd.Series:
    """Puntaje z modificado (mediana y MAD). Resistente a los propios atípicos."""
    x = x.astype(float)
    med = x.median()
    mad = (x - med).abs().median()
    if mad > 0:
        return 0.6745 * (x - med) / mad
    mean_ad = (x - med).abs().mean()
    if mean_ad > 0:
        return (x - med) / (1.253314 * mean_ad)
    return pd.Series(0.0, index=x.index)


def grouped_robust_z(values: pd.Series, groups, min_group: int) -> tuple:
    """z robusto por segmento; los segmentos pequeños se evalúan juntos."""
    z = pd.Series(np.nan, index=values.index)
    med = pd.Series(np.nan, index=values.index)
    valid = values.notna()
    if groups is not None:
        sub = values[valid]
        for _, idx in sub.groupby(groups[valid]).groups.items():
            if len(idx) >= min_group:
                z.loc[idx] = robust_z(values.loc[idx])
                med.loc[idx] = values.loc[idx].median()
    rest = valid & z.isna()
    if int(rest.sum()) >= min_group:
        z.loc[rest] = robust_z(values[rest])
        med.loc[rest] = values[rest].median()
    return z, med


def compute_cost(frame: pd.DataFrame) -> tuple:
    """Calcula costo total. Si existe COST_TOTAL explícito, lo usa para evitar doble conteo."""
    comps = [c for c in COST_COMPONENTS if c in frame.columns]
    comp_sum = frame[comps].sum(axis=1, min_count=1).astype(float) if comps else None
    if "COST_TOTAL" in frame.columns:
        total = frame["COST_TOTAL"].astype(float)
        return (total.where(total.notna(), comp_sum) if comp_sum is not None else total), ["COST_TOTAL"]
    if comp_sum is not None:
        return comp_sum, comps
    return None, []


# ---------------------------------------------------------------------------
# Estructuras de Trazabilidad Empresarial
# ---------------------------------------------------------------------------
def _evidence(metodo: str, afectados: int, poblacion: int, nivel: str,
              regla: str = None, variable: str = None, benchmark: str = None, detectado: str = None) -> dict:
    return {
        "metodo": metodo, "registros_afectados": int(afectados), "registros_poblacion": int(poblacion),
        "pct_poblacion": round(100 * afectados / poblacion, 2) if poblacion else None,
        "nivel_evidencia": nivel, "regla_negocio": regla, "variable_evaluada": variable,
        "benchmark_esperado": benchmark, "valor_detectado": detectado
    }


def _finding(tipo, severidad, titulo, causa, valor, impacto_tipo, descripcion, evidencia, casos, total,
             accion, vehiculos=None, nota=None) -> dict:
    return {
        "tipo": tipo, "severidad": severidad, "titulo": titulo, "causa": causa,
        "impacto": {"impacto_directo": round(float(valor or 0.0), 2), "tipo": impacto_tipo,
                    "descripcion": descripcion, "moneda": "COP", "nota": nota or DEFAULT_NOTE},
        "evidencia": evidencia, "casos": casos, "casos_total": int(total), "accion": accion,
        "vehiculos_afectados": vehiculos or [],
    }


def _accion(departamento: str, texto: str, urgencia: str) -> dict:
    return {"departamento": departamento, "accion": texto, "urgencia": urgencia}


class EconomicRuleEngine:
    def __init__(self, config: dict | None = None):
        self.cfg = {**DEFAULT_CONFIG, **{k: v for k, v in (config or {}).items() if v is not None}}

    # -- casos ------------------------------------------------------------------------------------
    @staticmethod
    def _case_base(df: pd.DataFrame, i, rev: pd.Series, cost: pd.Series) -> dict:
        case: dict = {}
        if "_src_sheet" in df.columns:
            case["hoja"] = df.at[i, "_src_sheet"]
        if "_src_row" in df.columns and pd.notna(df.at[i, "_src_row"]):
            case["fila"] = int(df.at[i, "_src_row"])
        for canon, label in (("TRIP_ID", "trip_id"), ("VEHICLE_ID", "vehiculo"),
                             ("ROUTE_NAME", "ruta"), ("CUSTOMER_NAME", "cliente")):
            if canon in df.columns and pd.notna(df.at[i, canon]):
                case[label] = str(df.at[i, canon])
        if "TRIP_DATE" in df.columns and pd.notna(df.at[i, "TRIP_DATE"]):
            case["fecha"] = pd.Timestamp(df.at[i, "TRIP_DATE"]).date().isoformat()
        if pd.notna(rev.at[i]):
            case["ingreso"] = round(float(rev.at[i]), 2)
        if pd.notna(cost.at[i]):
            case["costo"] = round(float(cost.at[i]), 2)
        return case

    def _cases(self, df, mask, rev, cost, order=None, extra=None) -> list:
        idx = df.index[mask]
        if order is not None:
            idx = order[mask].sort_values(ascending=False).index
        out = []
        for i in list(idx[:MAX_CASES]):
            case = self._case_base(df, i, rev, cost)
            if extra:
                case.update(extra(i))
            out.append(case)
        return out

    @staticmethod
    def _top_vehicles(df, mask, weights, n=3) -> list:
        if "VEHICLE_ID" not in df.columns:
            return []
        sub = df.loc[mask & df["VEHICLE_ID"].notna()]
        if sub.empty:
            return []
        w = weights.loc[sub.index].fillna(0)
        return [str(k) for k in w.groupby(sub["VEHICLE_ID"]).sum().sort_values(ascending=False).head(n).index]

    # -- Análisis Principal -----------------------------------------------------------------------
    def analyze(self, master: pd.DataFrame, merge: MergeResult) -> dict:
        cfg = self.cfg
        df = master.reset_index(drop=True).copy()
        cols = set(df.columns)
        warnings: list = []
        findings: list = []
        n_rows = len(df)

        has_rev = "REVENUE" in cols
        vat_div = 1.0 + float(cfg["vat_rate"]) if cfg["revenue_includes_vat"] else 1.0
        rev = df["REVENUE"].astype(float) / vat_div if has_rev else pd.Series(np.nan, index=df.index)
        
        if has_rev and vat_div != 1.0:
            warnings.append(f"Los ingresos se dividieron por {vat_div:.2f} para excluir el IVA "
                            f"({float(cfg['vat_rate']):.0%}), según la configuración de tu empresa.")

        cost, cost_included = compute_cost(df)
        has_cost = cost is not None
        if not has_cost:
            cost = pd.Series(np.nan, index=df.index)
        elif "COST_TOTAL" in cost_included and any(c in cols for c in COST_COMPONENTS):
            warnings.append("Se usó el costo total del viaje explícito; los componentes individuales "
                            "no se sumaron para evitar contarlos dos veces.")
        elif "COST_TOTAL" not in cost_included:
            missing = [LABELS[c] for c in COST_COMPONENTS if c not in cost_included]
            if missing:
                warnings.append("El costo incluye solo: " + ", ".join(LABELS[c] for c in cost_included) +
                                ". Faltan: " + ", ".join(missing) + ". (Margen real podría ser menor).")

        # -- Duplicados y Llaves --
        canon = [c for c in df.columns if not c.startswith("_src_") and not c.endswith("__alt")]
        has_id = "TRIP_ID" in cols
        dup_exact = df.duplicated(subset=canon, keep="first") if (has_id or len(canon) >= 4) else pd.Series(False, index=df.index)
        conflict = df["TRIP_ID"].notna() & df.duplicated(subset=["TRIP_ID"], keep="first") & ~dup_exact if has_id else pd.Series(False, index=df.index)
        
        net = ~dup_exact

        # -- Totales (Base comparable) --
        total_ing = float(rev.where(net).sum()) if has_rev else None
        total_cost = float(cost.where(net).sum()) if has_cost else None
        both = net & rev.notna() & cost.notna()
        ing_c = float(rev[both].sum())
        cost_c = float(cost[both].sum())
        margen = round((ing_c - cost_c) / ing_c * 100, 2) if (both.any() and ing_c > 0) else None

        risk_dup = float(rev[dup_exact].sum()) if has_rev else 0.0
        risk_loss = 0.0

        # 1. DUPLICADOS EXACTOS
        if dup_exact.any():
            n = int(dup_exact.sum())
            findings.append(_finding(
                "DUPLICADO_EXACTO", "ALTA", "Registros duplicados exactos",
                f"{n:,} registros son copias exactas (todas las columnas coinciden).",
                risk_dup, "sobrefacturacion_potencial", "Ingreso potencialmente duplicado",
                _evidence("Comparación por columnas", n, n_rows, "DETERMINISTICA", 
                          regla="Fila Única", variable="Todas las columnas", benchmark="0 Duplicados", detectado=f"{n} Duplicados"),
                self._cases(df, dup_exact, rev, cost, order=rev), n,
                _accion("Contabilidad", f"Verifica si estos viajes se facturaron/pagaron dos veces y anula el duplicado.", "ALTA"),
                self._top_vehicles(df, dup_exact, rev.fillna(0))))

        # 2. CONFLICTO DE LLAVES
        if conflict.any():
            n = int(conflict.sum())
            findings.append(_finding(
                "LLAVE_CONFLICTO", "MEDIA", "Mismo ID de viaje con datos distintos",
                f"{n:,} registros repiten un ID de viaje pero con valores diferentes.",
                float(rev[conflict].sum()) if has_rev else 0.0, "exposicion", "Ingreso a verificar",
                _evidence("Coincidencia de TRIP_ID", n, n_rows, "DETERMINISTICA",
                          regla="Unicidad de Viaje", variable="TRIP_ID", benchmark="1 fila por ID", detectado="Múltiples filas"),
                self._cases(df, conflict, rev, cost, order=rev), n,
                _accion("Operaciones", "Confirma cuál registro es correcto o si es un error de digitación.", "MEDIA"),
                self._top_vehicles(df, conflict, rev.fillna(0))))

        # 3. PÉRDIDA DIRECTA (Margen Negativo)
        if has_rev and has_cost and not int(cfg["allow_negative_margin"]):
            neg = both & (cost > rev) & (rev > 0)
            if neg.any():
                loss = (cost - rev).where(neg)
                risk_loss += float(loss.sum())
                n = int(neg.sum())
                findings.append(_finding(
                    "MARGEN_NEGATIVO", "ALTA", "Viajes con pérdida directa",
                    f"{n:,} viaje(s) tienen un costo mayor que el ingreso facturado.",
                    float(loss.sum()), "perdida_directa", "Pérdida directa (costo − ingreso)",
                    _evidence("Cálculo fila a fila", n, int(both.sum()), "DETERMINISTICA",
                              regla="Rentabilidad Unitaria", variable="Ingreso - Costo", benchmark="> $0", detectado="< $0"),
                    self._cases(df, neg, rev, cost, order=loss, extra=lambda i: {"perdida": round(float(loss.at[i]), 2)}), n,
                    _accion("Pricing/Ventas", "Audita tarifa frente a costo y renegocia rutas con pérdida recurrente.", "MEDIA"),
                    self._top_vehicles(df, neg, loss)))

        # 4. COSTO SIN INGRESO (Viaje no facturado)
        if has_rev and has_cost:
            noinc = net & cost.gt(0) & (rev.isna() | rev.eq(0))
            if noinc.any():
                n = int(noinc.sum())
                findings.append(_finding(
                    "VIAJE_SIN_INGRESO", "ALTA", "Viajes con costo pero sin ingreso",
                    f"{n:,} viaje(s) registran costo y no tienen ingreso: posible viaje no facturado.",
                    float(cost[noinc].sum()), "exposicion", "Costo incurrido sin ingreso",
                    _evidence("Filtro fila a fila", n, n_rows, "DETERMINISTICA",
                              regla="Completitud de Ciclo", variable="Ingreso", benchmark="> $0", detectado="$0 / Vacío"),
                    self._cases(df, noinc, rev, cost, order=cost), n,
                    _accion("Facturación", "Confirma si el viaje se ejecutó; de ser así, emite la factura.", "ALTA"),
                    self._top_vehicles(df, noinc, cost.fillna(0))))

        # 5. MARGEN BAJO EL MÍNIMO
        if has_rev and has_cost:
            pct = (rev - cost) / rev * 100
            low = both & rev.gt(0) & (pct >= 0) & (pct < float(cfg["min_margin_percent"]))
            if low.any():
                gap = (float(cfg["min_margin_percent"]) / 100 * rev - (rev - cost)).clip(lower=0).where(low)
                n = int(low.sum())
                findings.append(_finding(
                    "MARGEN_BAJO", "MEDIA", f"Viajes bajo el margen mínimo ({float(cfg['min_margin_percent']):g}%)",
                    f"{n:,} viaje(s) dejan un margen inferior al mínimo definido.",
                    float(gap.sum()), "brecha", "Brecha frente al margen mínimo",
                    _evidence("Cálculo porcentual", n, int(both.sum()), "DETERMINISTICA",
                              regla="Margen Objetivo", variable="Margen %", benchmark=f">={cfg['min_margin_percent']}%", detectado="Inferior"),
                    self._cases(df, low, rev, cost, order=gap, extra=lambda i: {"margen_pct": round(float(pct.at[i]), 1)}), n,
                    _accion("Pricing", "Revisa tarifas de estos viajes para alcanzar el margen objetivo.", "MEDIA"),
                    self._top_vehicles(df, low, gap)))

        # 6. INGRESOS ATÍPICOS (Outliers)
        out = self._revenue_outliers(df, net, rev, cost)
        if out: findings.append(out)

        # 7. RENDIMIENTO COMBUSTIBLE
        fuel_finding, fleet_kmgal = self._fuel_efficiency(df, net, cost)
        if fuel_finding: findings.append(fuel_finding)

        # 8. HALLAZGOS DESDE EL MERGE (Orphans, etc)
        findings += self._from_merge(df, net, rev, cost, merge)

        # 9. CONCILIACIÓN
        findings += self._reconciliation(df, net, merge)

        # 10. ANÁLISIS A NIVEL VEHÍCULO
        veh = None
        if not has_cost:
            veh = self._vehicle_level(df, net, rev, merge, warnings)
        if veh:
            total_cost = float(veh["veh_cost"].sum())
            ing_c = float(veh["veh_rev"][veh["common"]].sum())
            cost_c = float(veh["veh_cost"][veh["common"]].sum())
            margen = round((ing_c - cost_c) / ing_c * 100, 2) if ing_c > 0 else None
            risk_loss += veh["loss"]
            if veh["finding"]: findings.append(veh["finding"])
            both = pd.Series(False, index=df.index)
            cost_included = veh["included"]

        # -- Ponderación Final --
        for f in findings:
            f["_score"] = (max(f["impacto"]["impacto_directo"], 0.0) * SEVERITY_WEIGHT[f["severidad"]]
                           * IMPACT_WEIGHT.get(f["impacto"]["tipo"], 0.5))
        findings.sort(key=lambda f: (-f["_score"], list(SEVERITY_WEIGHT).index(f["severidad"]), f["tipo"]))
        for rank, f in enumerate(findings, 1):
            f["prioridad"] = rank
            del f["_score"]

        financials = {
            "totalIngresos": total_ing, "totalCostos": total_cost, "margenGlobal": margen,
            "ingresosComparables": ing_c if margen is not None else None,
            "costosComparables": cost_c if margen is not None else None,
            "filasConMargen": int(both.sum()) if not veh else len(veh["common"]),
            "dineroEnRiesgo": round(risk_dup + risk_loss, 2),
            "dineroEnRiesgoDetalle": {"sobrefacturacion_potencial": round(risk_dup, 2), "perdida_directa": round(risk_loss, 2)},
            "costosIncluidos": [LABELS[c] for c in cost_included],
            "ivaExcluido": vat_div != 1.0,
            "filasAnalizadas": n_rows, "filasNetas": int(net.sum()), "duplicadosExactos": int(dup_exact.sum()),
            "rendimientoFlotaKmGal": round(fleet_kmgal, 2) if fleet_kmgal else None,
            "totalHallazgos": len(findings),
            "nivelVehiculo": bool(veh),
        }
        return {"financials": financials, "findings": findings, "warnings": warnings, "monthly": self._monthly(df, net, rev, cost)}

    # -- Sub-Motores Detectores -----------------------------------------------------------------------
    def _revenue_outliers(self, df, net, rev, cost):
        cfg = self.cfg
        valid = net & rev.gt(0)
        if int(valid.sum()) < int(cfg["outlier_min_group"]): return None
        groups, seg = (df["ROUTE_NAME"], "por ruta") if "ROUTE_NAME" in df.columns else ((df["CUSTOMER_NAME"], "por cliente") if "CUSTOMER_NAME" in df.columns else (None, "global"))
        
        z, med = grouped_robust_z(rev.where(valid), groups, int(cfg["outlier_min_group"]))
        mask = z.abs() > float(cfg["z_score_threshold"])
        if not mask.any(): return None
        
        dev = (rev - med).abs()
        n, pop = int(mask.sum()), int(valid.sum())
        return _finding(
            "OUTLIER_INGRESO", "MEDIA", "Ingresos atípicos frente a su segmento",
            f"{n:,} viaje(s) tienen un ingreso muy alejado de la mediana de su segmento.",
            float(dev[mask].sum()), "desviacion", "Desviación total frente a mediana",
            _evidence("Puntaje z robusto", n, pop, "ESTADISTICA", regla="Consistencia de Pricing", variable="Z-Score", benchmark=f"< {cfg['z_score_threshold']}", detectado="Atípico"),
            self._cases(df, mask, rev, cost, order=dev, extra=lambda i: {"mediana_segmento": round(float(med.at[i]), 2), "desviacion": round(float(dev.at[i]), 2)}), n,
            _accion("Ventas", "Verifica la tarifa aplicada en estos viajes.", "MEDIA"),
            self._top_vehicles(df, mask, dev.fillna(0)))

    def _fuel_efficiency(self, df, net, cost):
        cfg = self.cfg
        if "DISTANCE_KM" not in df.columns or not any(c in df.columns for c in VOLUME_COLUMNS): return None, None
        
        gal = df["VOLUME_GAL"].astype(float) if "VOLUME_GAL" in df.columns else pd.Series(np.nan, index=df.index)
        if "VOLUME_LTS" in df.columns: gal = gal.where(gal.notna(), df["VOLUME_LTS"].astype(float) / LITROS_POR_GALON)
        km = df["DISTANCE_KM"].astype(float)
        
        valid = net & km.gt(0) & gal.gt(0)
        if int(valid.sum()) < int(cfg["outlier_min_group"]): return None, None
        
        kmgal = (km / gal).where(valid)
        fleet = float(kmgal.median())
        groups = df["VEHICLE_ID"] if "VEHICLE_ID" in df.columns else None
        
        z, med = grouped_robust_z(kmgal, groups, int(cfg["outlier_min_group"]))
        bad = z < -float(cfg["z_score_threshold"])
        if not bad.any(): return None, fleet
        
        excess = (gal - km / med).clip(lower=0).where(bad)
        value = 0.0
        if "COST_FUEL" in df.columns:
            price = (df["COST_FUEL"].astype(float) / gal).where(valid)
            price_med = price.median()
            if pd.notna(price_med): value = float((excess * price.fillna(price_med)).sum())
            
        n, pop = int(bad.sum()), int(valid.sum())
        return _finding(
            "RENDIMIENTO_COMBUSTIBLE", "MEDIA", "Consumo de combustible superior al esperado",
            f"{n:,} viaje(s) rinden muy por debajo de los km/gal habituales de su vehículo (mediana: {fleet:.1f} km/gal).",
            value, "estimado", "Sobrecosto estimado",
            _evidence("Z-Score km/gal", n, pop, "ESTADISTICA", regla="Eficiencia de Flota", variable="km/gal", benchmark="Cerca a Mediana", detectado="Bajo rendimiento"),
            self._cases(df, bad, pd.Series(np.nan, index=df.index), cost, order=excess, extra=lambda i: {"km_por_galon": round(float(kmgal.at[i]), 2), "galones_exceso": round(float(excess.at[i]), 2)}), n,
            _accion("Flota", "Revisa tanqueos: posible fuga o mala calibración.", "ALTA"),
            self._top_vehicles(df, bad, excess.fillna(0))), fleet

    def _from_merge(self, df, net, rev, cost, merge: MergeResult) -> list:
        out = []
        joins = {j["tabla"]: j for j in merge.report.get("joins", [])}
        for name, orphan in merge.unmatched.items():
            oc, _ = compute_cost(orphan)
            amount = oc.fillna(0.0) if oc is not None else pd.Series(0.0, index=orphan.index)
            pct = round(100 - joins.get(name, {}).get("pct_origen_con_match", 100), 1)
            n = len(orphan)
            out.append(_finding(
                "REGISTRO_SIN_VIAJE", "ALTA" if pct >= 20 else "MEDIA", f"Registros de '{name}' sin viaje asociado",
                f"{n:,} registros ({pct}%) tienen ID que no cruza.", float(amount.sum()), "exposicion", "Costo huérfano",
                _evidence("Integridad Referencial", n, joins.get(name, {}).get("llaves_origen", n), "DETERMINISTICA", regla="Cruce de Llaves", variable="TRIP_ID", benchmark="100% Coincidencia", detectado=f"{pct}% Huérfanos"),
                [{"hoja": name, "trip_id": str(orphan.at[i, "TRIP_ID"]), "costo": round(float(amount.at[i]), 2)} for i in amount.sort_values(ascending=False).index[:MAX_CASES]], n,
                _accion("Operaciones", f"Concilia costos no registrados en '{name}'.", "ALTA" if pct >= 20 else "MEDIA")))
        return out

    def _reconciliation(self, df, net, merge: MergeResult) -> list:
        out = []
        tol = float(self.cfg["reconciliation_tolerance_pct"])
        for alt in [c for c in df.columns if c.endswith("__alt")]:
            field = alt[:-5]
            if field not in df.columns: continue
            a, b = df[field].astype(float), df[alt].astype(float)
            ok = net & a.notna() & b.notna()
            diff = (a - b).abs()
            rel = diff / b.abs().clip(lower=1.0)
            flagged = ok & (rel * 100 > tol) & (diff > 1.0)
            if not flagged.any(): continue
            n = int(flagged.sum())
            other, base = merge.report.get("alternativas", {}).get(field, "otra hoja"), merge.report.get("base", "base")
            out.append(_finding(
                "CONCILIACION", "ALTA" if field == "REVENUE" else "MEDIA", f"Diferencias entre fuentes: {LABELS[field]}",
                f"{n:,} viaje(s) tienen valores distintos entre '{base}' y '{other}'.", float(diff[flagged].sum()), "diferencia_entre_fuentes", "Diferencia acumulada",
                _evidence("Cruce 1:1", n, int(ok.sum()), "DETERMINISTICA", regla="Consistencia Multi-fuente", variable=field, benchmark=f"Diff <= {tol}%", detectado="Diferencia detectada"),
                self._cases(df, flagged, pd.Series(np.nan, index=df.index), pd.Series(np.nan, index=df.index), order=diff, extra=lambda i: {"diferencia": round(float(diff.at[i]), 2)}), n,
                _accion("Contabilidad", f"Concilia {LABELS[field]} entre fuentes.", "ALTA"), self._top_vehicles(df, flagged, diff.fillna(0))))
        return out

    def _vehicle_level(self, df, net, rev, merge: MergeResult, warnings: list):
        if "VEHICLE_ID" not in df.columns or "REVENUE" not in df.columns or not merge.secondary: return None
        veh_cost, included = None, []
        for sec in merge.secondary:
            c, incl = compute_cost(sec["df"])
            if c is None or "VEHICLE_ID" not in sec["df"].columns: continue
            part = c.groupby(sec["df"]["VEHICLE_ID"]).sum(min_count=1)
            veh_cost = part if veh_cost is None else veh_cost.add(part, fill_value=0)
            included += [c2 for c2 in incl if c2 not in included]
        if veh_cost is None or veh_cost.empty: return None
        
        sub = df[net & df["VEHICLE_ID"].notna()]
        veh_rev = rev.loc[sub.index].groupby(sub["VEHICLE_ID"]).sum(min_count=1)
        common = veh_cost.index.intersection(veh_rev.index)
        
        if len(common) == 0:
            warnings.append("Ninguna placa común entre ingresos y costos. Revisa el formato.")
            return None
            
        warnings.append("El margen se calculó por vehículo, consolidando la información de toda la flota en el periodo.")
        
        loss_total, finding = 0.0, None
        if not int(self.cfg["allow_negative_margin"]):
            r, c = veh_rev[common], veh_cost[common]
            neg = (c > r) & (r > 0)
            if neg.any():
                loss = (c - r)[neg]
                loss_total = float(loss.sum())
                finding = _finding(
                    "MARGEN_VEHICULO_NEGATIVO", "ALTA", "Vehículos con pérdida", f"{int(neg.sum())} vehículo(s) operan a pérdida en este periodo.",
                    loss_total, "perdida_directa", "Pérdida por vehículo",
                    _evidence("Consolidación por Vehículo", int(neg.sum()), len(common), "DETERMINISTICA", regla="Rentabilidad Vehicular", variable="Ingreso - Costo Flota", benchmark="> $0", detectado="< $0"),
                    [{"vehiculo": str(v), "perdida": round(float(loss[v]), 2)} for v in loss.sort_values(ascending=False).index[:MAX_CASES]], int(neg.sum()),
                    _accion("Flota", "Audita rentabilidad de estos vehículos.", "ALTA"), [str(v) for v in loss.sort_values(ascending=False).head(3).index])
        return {"veh_cost": veh_cost, "veh_rev": veh_rev, "common": common, "loss": loss_total, "finding": finding, "included": included}

    @staticmethod
    def _monthly(df, net, rev, cost) -> list:
        if "TRIP_DATE" not in df.columns: return []
        d = pd.to_datetime(df["TRIP_DATE"], errors="coerce")
        ok = net & d.notna()
        if not ok.any(): return []
        both = ok & rev.notna() & cost.notna()
        
        frame = pd.DataFrame({"periodo": d.dt.strftime("%Y-%m"), "viajes": 1.0, "ing": rev.where(ok), "cost": cost.where(ok), "ing_c": rev.where(both), "cost_c": cost.where(both)})[ok]
        g = frame.groupby("periodo")
        table = pd.DataFrame({"viajes": g["viajes"].sum(), "ingresos": g["ing"].sum(min_count=1), "costos": g["cost"].sum(min_count=1), "ing_c": g["ing_c"].sum(min_count=1), "cost_c": g["cost_c"].sum(min_count=1)}).sort_index().tail(36)
        
        rows = []
        for periodo, r in table.iterrows():
            margin = round((r["ing_c"] - r["cost_c"]) / r["ing_c"] * 100, 1) if pd.notna(r["ing_c"]) and r["ing_c"] > 0 and pd.notna(r["cost_c"]) else None
            rows.append({"periodo": periodo, "viajes": int(r["viajes"]), "ingresos": None if pd.isna(r["ingresos"]) else float(r["ingresos"]), "costos": None if pd.isna(r["costos"]) else float(r["costos"]), "margen_pct": margin})
        return rows
