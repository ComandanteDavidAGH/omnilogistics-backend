"""Consolidación segura de hojas (v1.1: con registro del rol de CADA hoja para medir cobertura).

Reglas que evitan los errores silenciosos más comunes:
  1. Hojas con la misma estructura (p. ej. una por mes) se APILAN, no se cruzan.
  2. Un cruce por viaje agrega primero el lado "muchos" (varias cargas de combustible por viaje se
     suman) y valida la cardinalidad muchos-a-uno: el cruce jamás multiplica filas.
  3. Una hoja con medidas y sin TRIP_ID NO se cruza por vehículo fila a fila (inflaría los totales).
  4. Todo cruce reporta su cobertura (qué porcentaje de llaves coincidió) y los registros huérfanos.
  5. Si dos hojas aportan la misma medida, la segunda se guarda como `<CAMPO>__alt` para conciliar.
  6. Cada hoja recibida queda registrada en report["hojas"] con su estado
     (INCORPORADA / NO_INCORPORADA / AUXILIAR) y el motivo. La compuerta de calidad usa esto
     para que la confianza nunca sea 100 % si quedaron datos económicos sin usar.
  7. La detección de "medidas" no depende solo de MEASURES: también reconoce cualquier columna
     canónica con prefijo económico (REVENUE, COST_, VOLUME_, DISTANCE_...) y naturaleza numérica.
     Esto evita que una hoja con dinero quede clasificada como "sin medidas" cuando el campo
     canónico no está formalmente en MEASURES.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

try:  # estructura de paquete (app/core/...)
    from .model import KEYS, MEASURES
    from .parsing import normalize_key, parse_dates, parse_numeric
except ImportError:  # estructura plana (archivos sueltos en una carpeta)
    from model import KEYS, MEASURES
    from parsing import normalize_key, parse_dates, parse_numeric


# Prefijos y fragmentos que identifican una columna canónica como económica,
# aunque no esté listada explícitamente en MEASURES.
_PREFIJOS_ECON = ("REVENUE", "COST_", "VOLUME_", "DISTANCE_", "LITERS", "GAL_", "KM_", "AMOUNT", "PRICE")


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


def _measures_in(df: pd.DataFrame) -> list:
    """Detecta columnas canónicas con naturaleza económica.

    No se limita a MEASURES: si una columna canónica se llama VOLUME_LTS, COST_*,
    REVENUE, DISTANCE_* o similar y es numérica, cuenta como medida. Esto evita
    que una hoja con dinero quede como "sin medidas" sólo porque el canónico
    no esté en la lista formal.
    """
    found: list = []
    for c in df.columns:
        if c.startswith("_src_") or c.endswith("__alt"):
            continue
        if c in MEASURES:
            found.append(c)
            continue
        if any(c.startswith(p) or p in c for p in _PREFIJOS_ECON):
            if pd.api.types.is_numeric_dtype(df[c]):
                found.append(c)
    return found


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
                g["rows"].update(t["rows"])
                g["cols"] |= cols
                break
        else:
            groups.append({"names": [t["name"]], "dfs": [t["df"]], "rows": dict(t["rows"]), "cols": set(cols)})

    result = []
    for g in groups:
        if len(g["dfs"]) > 1:
            stacked = pd.concat(g["dfs"], ignore_index=True, sort=False)
            warnings.append(f"Las hojas {', '.join(repr(n) for n in g['names'])} tienen la misma estructura y se "
                            f"apilaron como un solo periodo continuo ({len(stacked):,} filas).")
        else:
            stacked = g["dfs"][0].reset_index(drop=True)
        result.append({"name": " + ".join(g["names"]), "df": stacked, "sheets": list(g["names"]), "rows": g["rows"]})
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


def _mark(hojas: list, table: dict, estado: str, rol: str, motivo: str, measures: list, secundaria: bool = False) -> None:
    """Registra el estado de cada hoja de origen que compone esta tabla."""
    for sheet in table["sheets"]:
        hojas.append({
            "nombre": sheet, "filas": int(table["rows"].get(sheet, 0)), "tabla": table["name"],
            "estado": estado, "rol": rol, "motivo": motivo, "medidas": list(measures),
            "aporta_medidas": bool(measures), "secundaria": secundaria,
        })


def build_master(dfs: dict, mapping: dict) -> MergeResult:
    warnings: list = []
    stats: dict = {}
    hojas: list = []
    tables = []
    for sheet, df in dfs.items():
        if df.empty:
            continue
        table = _canonical_table(sheet, df, mapping.get(sheet, {}) or {}, warnings, stats)
        if table is None:
            hojas.append({"nombre": sheet, "filas": int(len(df)), "tabla": sheet, "estado": "AUXILIAR", "rol": "AUXILIAR",
                          "motivo": "No participa en el modelo: ninguna de sus columnas quedó asignada a un campo "
                                    "(hoja de control, notas o parámetros).",
                          "medidas": [], "aporta_medidas": False, "secundaria": False})
            warnings.append(f"La hoja '{sheet}' se trató como auxiliar: no participa en el modelo económico.")
            continue
        tables.append({"name": sheet, "df": table, "sheets": [sheet], "rows": {sheet: len(df)}})

    if not tables:
        return MergeResult(master=pd.DataFrame(), warnings=warnings, parse_stats=stats,
                           report={"tablas": [], "joins": [], "base": None, "hojas": hojas})

    tables = _stack_compatible(tables, warnings)
    base_idx = max(range(len(tables)), key=lambda i: _score_table(tables[i]["df"]))
    base_t = tables[base_idx]
    base_name, base = base_t["name"], base_t["df"]
    others = [t for i, t in enumerate(tables) if i != base_idx]

    _mark(hojas, base_t, "INCORPORADA", "BASE" if len(base_t["sheets"]) == 1 else "BASE_APILADA",
          "Hoja principal del análisis." if len(base_t["sheets"]) == 1
          else "Hojas con la misma estructura apiladas como hoja principal.", _measures_in(base))

    result = MergeResult(master=base, warnings=warnings, parse_stats=stats,
                         report={"base": base_name, "tablas": [{"nombre": t["name"], "filas": len(t["df"])} for t in tables],
                                 "joins": [], "alternativas": {}, "hojas": hojas})

    for t in others:
        name, right = t["name"], t["df"]
        shared = [k for k in KEYS if k in base.columns and k in right.columns]
        right_measures = _measures_in(right)

        if "TRIP_ID" in shared:
            mode, key = "por_viaje", "TRIP_ID"
        elif shared and not right_measures:
            mode, key = "atributos", shared[0]
        elif "VEHICLE_ID" in shared:
            result.secondary.append({"name": name, "df": right})
            result.report["joins"].append({"tabla": name, "modo": "aparte", "llave": "VEHICLE_ID",
                                           "filas_origen": len(right)})
            _mark(hojas, t, "NO_INCORPORADA", "SIN_ID_DE_VIAJE",
                  f"Aporta {', '.join(right_measures)} pero no tiene ID de viaje: no se puede cruzar fila a fila "
                  "sin multiplicar los valores.", right_measures, secundaria=True)
            warnings.append(f"La hoja '{name}' aporta {', '.join(right_measures)} pero no tiene TRIP_ID: no se cruzó "
                            "fila a fila (multiplicaría los valores). Se usa para el análisis por vehículo.")
            continue
        else:
            result.report["joins"].append({"tabla": name, "modo": "sin_cruce", "llave": None, "filas_origen": len(right)})
            _mark(hojas, t, "NO_INCORPORADA", "SIN_CRUCE",
                  "No comparte ID de viaje ni placa con la hoja base. Asigna una de esas columnas para poder cruzarla.",
                  right_measures)
            warnings.append(f"La hoja '{name}' no comparte una llave utilizable (TRIP_ID o placa) con '{base_name}'; "
                            "sus datos NO se incluyeron en el análisis.")
            continue

        with_key = right[right[key].notna()]
        if len(with_key) < len(right):
            warnings.append(f"La hoja '{name}' tiene {len(right) - len(with_key):,} filas sin {key}; no se pueden cruzar.")
        agg = _aggregate(with_key, key)
        collapsed = len(with_key) - len(agg)
        if collapsed > 0 and mode == "por_viaje":
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
        if len(base) != rows_before:  # no debería ocurrir; defensa en profundidad
            raise RuntimeError(f"El cruce con '{name}' alteró el número de filas ({rows_before} -> {len(base)}).")

        pct_orphan = round(100 * len(agg_keys - base_keys) / max(1, len(agg_keys)), 1)
        pct_trips = round(100 * len(matched) / max(1, len(base_keys)), 1)
        result.report["joins"].append({
            "tabla": name, "modo": mode, "llave": key, "filas_origen": len(right),
            "llaves_origen": len(agg_keys), "llaves_con_match": len(matched),
            "pct_origen_con_match": round(100 - pct_orphan, 1), "pct_base_con_match": pct_trips,
            "medidas_aportadas": [c for c in contributed if c in MEASURES],
            "columnas_aportadas": contributed,
        })
        if mode == "por_viaje":
            _mark(hojas, t, "INCORPORADA", "CRUZADA_POR_VIAJE",
                  f"Cruzada por ID de viaje: el {pct_trips}% de los viajes tiene registro aquí.", right_measures)
            if len(orphan):
                result.unmatched[name] = orphan.reset_index(drop=True)
                warnings.append(f"{len(orphan):,} de {len(agg_keys):,} llaves de '{name}' ({pct_orphan}%) no existen en "
                                f"'{base_name}'.")
            if pct_trips < 100:
                warnings.append(f"Solo el {pct_trips}% de los viajes de '{base_name}' tiene registro en '{name}'.")
        else:
            if contributed:
                _mark(hojas, t, "INCORPORADA", "ATRIBUTOS", f"Enlazada por {key} para agregar atributos.", right_measures)
            else:
                _mark(hojas, t, "NO_INCORPORADA", "SIN_CAMPOS_NUEVOS",
                      "Sus campos ya existían en la hoja base: no cambia el cálculo.", right_measures)

    result.master = base.reset_index(drop=True)
    return result
