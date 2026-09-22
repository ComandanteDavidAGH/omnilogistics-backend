"""Consolidación segura de hojas impulsada por el Motor de Relaciones (Relationship Engine).

Reglas orquestadas:
  1. Hojas con la misma estructura (p. ej. una por mes) se APILAN, no se cruzan.
  2. Un cruce por viaje agrega primero el lado "muchos" y valida la cardinalidad muchos-a-uno.
  3. Relaciones denegadas por el RelationshipEngine (ej. costo por vehículo) se conservan intactas en la capa de análisis superior.
  4. Todo cruce reporta su cobertura (porcentaje de coincidencia) y huérfanos.
  5. Alternativas (ej. dos fuentes de ingresos) se renombran a <CAMPO>__alt.
  6. La tabla base se puede seleccionar mediante heurística o ser dictada por el ModelBuilder.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
import pandas as pd

from .model import KEYS, MEASURES
from .parsing import normalize_key, parse_dates, parse_numeric
from .relationships import RelationshipEngine, JoinMode


@dataclass
class MergeResult:
    master: pd.DataFrame
    secondary: list = field(default_factory=list)   # [{"name", "df"}]
    unmatched: dict = field(default_factory=dict)   # tabla -> DataFrame con llaves sin viaje
    report: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    parse_stats: dict = field(default_factory=dict)  # campo canónico -> estadísticas de lectura


def _add_stats(store: dict, canonical: str, stats: dict) -> None:
    cur = store.setdefault(canonical, {"total": 0, "blank": 0, "unparsed": 0,
                                       "decimal_sep": stats.get("decimal_sep"), "ambiguous": False})
    for k in ("total", "blank", "unparsed"):
        cur[k] += stats.get(k, 0)
    cur["ambiguous"] = cur["ambiguous"] or bool(stats.get("ambiguous"))
    if cur["decimal_sep"] is None:
        cur["decimal_sep"] = stats.get("decimal_sep")


def _canonical_table(sheet: str, df: pd.DataFrame, scoped: dict, warnings: list, stats: dict):
    rename: dict = {}
    seen: dict = {}
    for original, canonical in scoped.items():
        if not canonical or canonical == "UNKNOWN" or original not in df.columns:
            continue
        if canonical in seen:
            warnings.append(f"Hoja '{sheet}': las columnas '{seen[canonical]}' y '{original}' se asignaron a "
                            f"{canonical}; se usó '{seen[canonical]}' y se ignoró la otra.")
            continue
        seen[canonical] = original
        rename[original] = canonical
    if not rename:
        return None

    keep = list(rename.keys()) + (["_src_row"] if "_src_row" in df.columns else [])
    out = df[keep].rename(columns=rename).copy()
    out["_src_sheet"] = sheet

    for col in [c for c in MEASURES if c in out.columns]:
        out[col], st = parse_numeric(out[col])
        _add_stats(stats, col, st)
    if "TRIP_DATE" in out.columns:
        out["TRIP_DATE"], st = parse_dates(out["TRIP_DATE"])
        _add_stats(stats, "TRIP_DATE", st)
    for key in KEYS:
        if key in out.columns:
            out[key] = normalize_key(out[key], plate=(key == "VEHICLE_ID"))
    return out


def _canon_cols(df: pd.DataFrame) -> set:
    return {c for c in df.columns if not c.startswith("_src_") and not c.endswith("__alt")}


def _parallel_sources(existing: list, other: pd.DataFrame) -> bool:
    """Dos hojas con los mismos viajes son fuentes paralelas (operación vs. facturación), no periodos."""
    if "TRIP_ID" not in other.columns or not any("TRIP_ID" in d.columns for d in existing):
        return False
    a = set(pd.concat([d["TRIP_ID"] for d in existing if "TRIP_ID" in d.columns]).dropna())
    b = set(other["TRIP_ID"].dropna())
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.5


def _stack_compatible(tables: list, warnings: list) -> list:
    groups: list = []
    for t in tables:
        cols = _canon_cols(t["df"])
        for g in groups:
            union = cols | g["cols"]
            if union and len(cols & g["cols"]) / len(union) >= 0.8 and not _parallel_sources(g["dfs"], t["df"]):
                g["names"].append(t["name"])
                g["dfs"].append(t["df"])
                g["cols"] |= cols
                break
        else:
            groups.append({"names": [t["name"]], "dfs": [t["df"]], "cols": set(cols)})

    result = []
    for g in groups:
        if len(g["dfs"]) > 1:
            stacked = pd.concat(g["dfs"], ignore_index=True, sort=False)
            warnings.append(f"Las hojas {', '.join(repr(n) for n in g['names'])} tienen la misma estructura y se "
                            f"apilaron como un solo periodo continuo ({len(stacked):,} filas).")
        else:
            stacked = g["dfs"][0].reset_index(drop=True)
        result.append({"name": " + ".join(g["names"]), "df": stacked})
    return result


def _aggregate(df: pd.DataFrame, key: str) -> pd.DataFrame:
    measure_cols = [c for c in MEASURES if c in df.columns]
    other = [c for c in df.columns if c not in measure_cols and c != key and not c.startswith("_src_")]
    grouped = df.groupby(key, sort=False)
    parts = []
    if measure_cols:
        parts.append(grouped[measure_cols].sum(min_count=1))
    if other:
        parts.append(grouped[other].first())
    if not parts:
        return df[[key]].drop_duplicates().reset_index(drop=True)
    return pd.concat(parts, axis=1).reset_index()


def _score_table(df: pd.DataFrame) -> tuple:
    return (
        (4 if "TRIP_ID" in df.columns else 0) + (3 if "REVENUE" in df.columns else 0)
        + (1 if any(c.startswith("COST_") for c in df.columns) else 0) + (1 if "DISTANCE_KM" in df.columns else 0),
        len(df),
    )


def build_master(dfs: dict[str, pd.DataFrame], mapping: dict[str, dict], implicit_base: str | None = None) -> MergeResult:
    """Consolida las hojas validando integridad relacional y cardinalidad."""
    start_time = time.time()
    warnings: list = []
    stats: dict = {}
    tables = []
    
    # 1. Parsing y Limpieza Individual
    for sheet, df in dfs.items():
        if df.empty:
            continue
        table = _canonical_table(sheet, df, mapping.get(sheet, {}) or {}, warnings, stats)
        if table is None:
            warnings.append(f"La hoja '{sheet}' se omitió: ninguna de sus columnas quedó asignada a un campo.")
            continue
        tables.append({"name": sheet, "df": table})

    if not tables:
        return MergeResult(master=pd.DataFrame(), warnings=warnings, parse_stats=stats,
                           report={"tablas": [], "joins": [], "base": None})

    tables = _stack_compatible(tables, warnings)
    
    # 2. Selección de la Tabla Base (Dictada por ModelBuilder o Heurística)
    if implicit_base and any(t["name"] == implicit_base for t in tables):
        base_idx = next(i for i, t in enumerate(tables) if t["name"] == implicit_base)
    else:
        base_idx = max(range(len(tables)), key=lambda i: _score_table(tables[i]["df"]))
        
    base_name, base = tables[base_idx]["name"], tables[base_idx]["df"]
    others = [t for i, t in enumerate(tables) if i != base_idx]

    result = MergeResult(master=base, warnings=warnings, parse_stats=stats,
                         report={"base": base_name, "tablas": [{"nombre": t["name"], "filas": len(t["df"])} for t in tables],
                                 "joins": [], "alternativas": {}})

    # 3. Integración iterativa impulsada por RelationshipEngine
    for t in others:
        name, right = t["name"], t["df"]
        
        rel_report = RelationshipEngine.analyze(base, right, base_name, name)
        mode = rel_report.join_mode.value
        key = rel_report.primary_key_candidate

        if mode == JoinMode.NO_JOIN.value:
            result.report["joins"].append({"tabla": name, "modo": "sin_cruce", "llave": None, "filas_origen": len(right)})
            warnings.append(rel_report.reason)
            continue

        elif mode == JoinMode.BY_VEHICLE.value:
            result.secondary.append({"name": name, "df": right})
            result.report["joins"].append({"tabla": name, "modo": "aparte", "llave": key, "filas_origen": len(right)})
            warnings.append(rel_report.reason)
            continue

        # Si el Join Engine autorizó el cruce (PER_TRIP o ATTRIBUTES), procedemos.
        with_key = right[right[key].notna()]
        if len(with_key) < len(right):
            warnings.append(f"La hoja '{name}' tiene {len(right) - len(with_key):,} filas sin {key}; no se pueden cruzar.")
        
        agg = _aggregate(with_key, key)
        collapsed = len(with_key) - len(agg)
        if collapsed > 0 and mode == JoinMode.PER_TRIP.value:
            warnings.append(f"'{name}': {collapsed:,} registros se agregaron por {key} (suma de valores) para no "
                            "multiplicar filas al cruzar.")

        drop, rename_alt = [], {}
        for c in agg.columns:
            if c == key or c not in base.columns:
                continue
            if c in MEASURES and f"{c}__alt" not in base.columns:
                rename_alt[c] = f"{c}__alt"
                result.report["alternativas"][c] = name
            else:
                drop.append(c)
        
        agg = agg.drop(columns=drop).rename(columns=rename_alt)
        contributed = [c for c in agg.columns if c != key]

        base_keys = set(base[key].dropna())
        agg_keys = set(agg[key])
        matched = base_keys & agg_keys
        orphan = agg[~agg[key].isin(base_keys)]
        
        rows_before = len(base)
        base = base.merge(agg, on=key, how="left", validate="m:1")
        if len(base) != rows_before:
            raise RuntimeError(f"El cruce con '{name}' alteró el número de filas ({rows_before} -> {len(base)}). Violación de la regla del 1 a muchos autorizada por el Relationship Engine.")

        pct_orphan = round(100 * len(agg_keys - base_keys) / max(1, len(agg_keys)), 1)
        pct_trips = round(100 * len(matched) / max(1, len(base_keys)), 1)
        
        result.report["joins"].append({
            "tabla": name, "modo": mode, "llave": key, "filas_origen": len(right),
            "llaves_origen": len(agg_keys), "llaves_con_match": len(matched),
            "pct_origen_con_match": round(100 - pct_orphan, 1), "pct_base_con_match": pct_trips,
            "medidas_aportadas": [c for c in contributed if c in MEASURES],
            "columnas_aportadas": contributed,
        })

        if mode == JoinMode.PER_TRIP.value:
            if len(orphan):
                result.unmatched[name] = orphan.reset_index(drop=True)
                warnings.append(f"{len(orphan):,} de {len(agg_keys):,} llaves de '{name}' ({pct_orphan}%) no existen en "
                                f"'{base_name}'.")
            if pct_trips < 100:
                warnings.append(f"Solo el {pct_trips}% de las llaves base tiene registro en '{name}'.")

    result.master = base.reset_index(drop=True)
    return result
