"""Lectura tolerante de valores de negocio.

Principio rector: NUNCA convertir en silencio un dato ilegible en cero.
Todo valor que no se pueda interpretar queda como NaN y se contabiliza en las estadísticas,
para que la compuerta de calidad pueda advertir o bloquear el análisis.
"""
from __future__ import annotations

import re
import unicodedata
import warnings
from typing import Any

import numpy as np
import pandas as pd

BLANKS = {"", "nan", "none", "null", "n/a", "na", "nd", "n/d", "-", "--", "—", "#n/a", "#n/d", "s/d"}

_CURRENCY_TOKENS = re.compile(r"(?i)\b(cop|usd|eur|mxn)\b")
_LETTERS = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]")
_SCIENTIFIC = re.compile(r"^[+-]?\d+([.,]\d+)?[eE][+-]?\d+$")
_INNER_SEPARATOR = re.compile(r"\d\s*[-/]\s*\d")  # fechas o rangos: "2024-01-05", "3/4"


def normalize_text(value: Any) -> str:
    """Minúsculas, sin tildes, solo letras/números separados por un espacio."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def is_missing(x: Any) -> bool:
    if x is None or x is pd.NA or x is pd.NaT:
        return True
    if isinstance(x, (float, np.floating)) and np.isnan(x):
        return True
    return False


# ---------------------------------------------------------------------------
# Números
# ---------------------------------------------------------------------------
def detect_decimal_separator(strings: list) -> tuple:
    """Decide por columna si el decimal es ',' (convención colombiana) o '.'.

    Devuelve (separador, ambiguo). Es ambiguo cuando no hay evidencia y solo hay valores
    del estilo "1.500" que podrían ser 1500 o 1,5.
    """
    dot_votes = comma_votes = ambiguous = 0
    for raw in strings:
        core = re.sub(r"[^\d.,]", "", raw)
        if not core:
            continue
        has_dot, has_comma = "." in core, "," in core
        if has_dot and has_comma:
            if core.rfind(".") > core.rfind(","):
                dot_votes += 1
            else:
                comma_votes += 1
        elif has_comma:
            if core.count(",") > 1:
                dot_votes += 1  # 1,234,567 -> la coma es de miles
            elif len(core) - core.rfind(",") - 1 == 3:
                ambiguous += 1
            else:
                comma_votes += 1
        elif has_dot:
            if core.count(".") > 1:
                comma_votes += 1  # 1.234.567 -> el punto es de miles
            elif len(core) - core.rfind(".") - 1 == 3:
                ambiguous += 1
            else:
                dot_votes += 1
    if dot_votes > comma_votes:
        return ".", False
    if comma_votes > dot_votes:
        return ",", False
    return ",", ambiguous > 0


def _parse_one(text: str, decimal: str) -> float:
    t = _CURRENCY_TOKENS.sub("", text).strip()
    if _SCIENTIFIC.match(t):
        try:
            return float(t.replace(",", "."))
        except ValueError:
            return float("nan")
    if _LETTERS.search(t) or _INNER_SEPARATOR.search(t):
        return float("nan")
    negative = (t.startswith("(") and t.endswith(")")) or t.startswith(("-", "−")) or t.endswith(("-", "−"))
    core = re.sub(r"[^\d.,]", "", t)
    if not re.search(r"\d", core):
        return float("nan")
    if decimal == ",":
        core = core.replace(".", "").replace(",", ".")
    else:
        core = core.replace(",", "")
    try:
        value = float(core)
    except ValueError:
        return float("nan")
    if not np.isfinite(value):
        return float("nan")
    return -value if negative else value


def parse_numeric(series: pd.Series) -> tuple:
    """Convierte a float respetando el formato local. Devuelve (serie, estadísticas)."""
    n = len(series)
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        out = pd.to_numeric(series, errors="coerce").astype("float64")
        out = out.where(np.isfinite(out))
        blank = int(series.isna().sum())
        return out, {"total": n, "blank": blank, "unparsed": int(out.isna().sum()) - blank,
                     "decimal_sep": None, "ambiguous": False}

    values = series.to_numpy(dtype=object)
    sample = []
    for x in values:
        if isinstance(x, str):
            t = x.strip()
            if t.lower() not in BLANKS:
                sample.append(t)
                if len(sample) >= 2000:
                    break
    decimal, ambiguous = detect_decimal_separator(sample)

    arr = np.full(n, np.nan)
    blank = unparsed = 0
    for i, x in enumerate(values):
        if is_missing(x):
            blank += 1
        elif isinstance(x, (bool, np.bool_)):
            unparsed += 1
        elif isinstance(x, (int, float, np.integer, np.floating)):
            fx = float(x)
            if np.isfinite(fx):
                arr[i] = fx
            else:
                unparsed += 1
        else:
            t = str(x).strip()
            if t.lower() in BLANKS:
                blank += 1
                continue
            v = _parse_one(t, decimal)
            if v != v:
                unparsed += 1
            else:
                arr[i] = v
    return pd.Series(arr, index=series.index), {
        "total": n, "blank": blank, "unparsed": unparsed, "decimal_sep": decimal, "ambiguous": ambiguous,
    }


def numeric_success_rate(sample: pd.Series) -> float:
    """Proporción de valores no vacíos que se leen como número."""
    parsed, st = parse_numeric(sample)
    considered = st["total"] - st["blank"]
    return (parsed.notna().sum() / considered) if considered > 0 else 0.0


# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------
def parse_dates(series: pd.Series) -> tuple:
    n = len(series)
    try:
        if pd.api.types.is_datetime64_any_dtype(series):
            out = pd.to_datetime(series, errors="coerce")
        elif pd.api.types.is_numeric_dtype(series):
            num = pd.to_numeric(series, errors="coerce")
            num = num.where(num.between(20000, 80000))  # seriales de Excel (1954-2119)
            out = pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out = pd.to_datetime(series, errors="coerce", dayfirst=True)
    except Exception:  # noqa: BLE001 - un fallo de fechas nunca debe tumbar el análisis
        out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    blank = int(series.isna().sum())
    return out, {"total": n, "blank": blank, "unparsed": int((out.isna() & series.notna()).sum())}


def date_success_rate(sample: pd.Series) -> float:
    if sample.empty:
        return 0.0
    _, st = parse_dates(sample)
    considered = st["total"] - st["blank"]
    return (1 - st["unparsed"] / considered) if considered > 0 else 0.0


# ---------------------------------------------------------------------------
# Llaves
# ---------------------------------------------------------------------------
def normalize_key(series: pd.Series, plate: bool = False) -> pd.Series:
    """Homogeniza llaves para que crucen: mayúsculas, sin espacios extra, sin '.0' de Excel.

    Las placas también pierden guiones y espacios ("abc-123" == "ABC123").
    Los vacíos quedan como None (nunca se cruzan entre sí).
    """
    def f(x):
        if is_missing(x):
            return None
        if isinstance(x, (float, np.floating)) and float(x).is_integer():
            x = int(x)
        t = str(x).strip().upper()
        if t.lower() in BLANKS:
            return None
        if plate:
            t = re.sub(r"[^A-Z0-9]", "", t)
        else:
            t = re.sub(r"\s+", " ", t)
            if re.fullmatch(r"\d+\.0+", t):
                t = t.split(".")[0]
        return t or None

    return pd.Series([f(x) for x in series.to_numpy(dtype=object)], index=series.index, dtype=object)
