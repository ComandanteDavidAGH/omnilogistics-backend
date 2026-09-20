"""Ingesta de archivos Excel/CSV con las manías reales de un ERP colombiano:
  - CSV en UTF-8 o Windows-1252, separado por coma, punto y coma, tabulador o barra vertical
  - Títulos y filas vacías antes de los encabezados
  - Encabezados repetidos o vacíos
Cada DataFrame resultante lleva `_src_row` (fila original en el archivo) para trazabilidad.
"""
from __future__ import annotations

import csv
import io
import os
import re

import pandas as pd

from ..errors import ApiError
from .parsing import BLANKS

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".csv"}
_XLS_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def _validate_signature(ext: str, data: bytes) -> None:
    if ext in (".xlsx", ".xlsm") and not data.startswith(b"PK"):
        raise ApiError(422, "ARCHIVO_INVALIDO", "El archivo no es un Excel .xlsx válido (puede estar dañado).")
    if ext == ".xls" and not data.startswith(_XLS_MAGIC):
        raise ApiError(422, "ARCHIVO_INVALIDO", "El archivo no es un Excel .xls válido (puede estar dañado).")
    if ext == ".csv" and b"\x00" in data[:4096]:
        raise ApiError(422, "ARCHIVO_INVALIDO", "El archivo CSV contiene datos binarios; verifica que sea texto.")


def _looks_numeric(value: str) -> bool:
    return bool(re.fullmatch(r"[\s$()\-+.,\d]+", value)) and any(ch.isdigit() for ch in value)


def _detect_header_row(raw: pd.DataFrame) -> int:
    head = raw.head(30)
    counts = head.notna().sum(axis=1)
    top = int(counts.max()) if len(counts) else 0
    if top == 0:
        return 0
    needed = min(max(2, int(0.6 * top)), top)
    for i in range(len(head)):
        cells = head.iloc[i].dropna()
        if len(cells) < needed:
            continue
        text_share = sum(isinstance(v, str) and not _looks_numeric(v) for v in cells) / len(cells)
        if text_share >= 0.7:
            return i
    return 0


def _unique_headers(values: list) -> list:
    names, seen = [], {}
    for j, v in enumerate(values):
        name = str(v).strip() if v is not None and not (isinstance(v, float) and pd.isna(v)) else ""
        if not name or name.lower() in BLANKS:
            name = f"columna_{j + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        names.append(name)
    return names


def _frame_from_grid(raw: pd.DataFrame, header_row: int, first_line_number: int = 1) -> pd.DataFrame:
    headers = _unique_headers(list(raw.iloc[header_row].tolist()))
    body = raw.iloc[header_row + 1:].copy()
    body.columns = headers
    body["_src_row"] = [first_line_number + header_row + 1 + k for k in range(len(body))]
    body = body.dropna(how="all", subset=headers).dropna(axis=1, how="all", subset=None) if len(body) else body
    body = body.reset_index(drop=True)
    body.attrs["header_row"] = header_row + first_line_number - 1
    return body


def _decode_csv(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _sniff_delimiter(text: str) -> str:
    """Elige el separador cuyo conteo por línea es más consistente (los decimales con coma no engañan)."""
    lines = [ln for ln in text.splitlines()[:12] if ln.strip()]
    if not lines:
        return ","
    best, best_score = ",", (0.0, 0)
    for cand in (",", ";", "\t", "|"):
        counts = [ln.count(cand) for ln in lines]
        nonzero = [c for c in counts if c > 0]
        if len(nonzero) < max(1, int(0.6 * len(lines))):
            continue
        mode = max(set(nonzero), key=nonzero.count)
        score = (nonzero.count(mode) / len(lines), mode)
        if score > best_score:
            best, best_score = cand, score
    return best


def _csv_grid(text: str, sep: str) -> pd.DataFrame:
    """Lee el CSV como una cuadrícula de texto (sin inferir tipos) tolerando filas de distinto ancho.

    Las líneas en blanco se conservan para que `_src_row` coincida con la línea real del archivo.
    """
    rows = [[(c if c.strip() != "" else None) for c in r] for r in csv.reader(io.StringIO(text), delimiter=sep)]
    width = max((len(r) for r in rows), default=0)
    rows = [r + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(rows, dtype=object)


def read_workbook(filename: str, data: bytes, settings) -> dict:
    """Devuelve {nombre_hoja: DataFrame}. Lanza ApiError con mensajes accionables."""
    name = os.path.basename(filename or "archivo")
    ext = os.path.splitext(name.lower())[1]
    if ext not in ALLOWED_EXTENSIONS:
        raise ApiError(415, "TIPO_NO_SOPORTADO", "Sube un archivo .xlsx, .xls o .csv.")
    _validate_signature(ext, data)

    try:
        if ext == ".csv":
            text = _decode_csv(data)
            sep = _sniff_delimiter(text)
            grids = {"Hoja1": _csv_grid(text, sep)}
        else:
            grids = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object)
    except ImportError as exc:
        raise ApiError(422, "LECTOR_NO_DISPONIBLE",
                       "No se pudo leer este formato en el servidor. Guarda el archivo como .xlsx.") from exc
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ApiError(422, "ARCHIVO_ILEGIBLE",
                       "No se pudo leer el archivo. Verifica que no esté protegido con contraseña ni dañado.") from exc

    frames: dict = {}
    for sheet, raw in grids.items():
        if raw is None or raw.empty:
            continue
        raw = raw.reset_index(drop=True)
        frame = _frame_from_grid(raw, _detect_header_row(raw))
        data_cols = [c for c in frame.columns if c != "_src_row"]
        if frame.empty or not data_cols:
            continue
        if len(data_cols) > settings.max_columns:
            raise ApiError(413, "DEMASIADAS_COLUMNAS",
                           f"La hoja '{sheet}' tiene {len(data_cols)} columnas; el máximo es {settings.max_columns}.")
        if len(frame) > settings.max_rows:
            raise ApiError(413, "DEMASIADAS_FILAS",
                           f"La hoja '{sheet}' tiene {len(frame):,} filas; el máximo es {settings.max_rows:,}. "
                           "Divide el archivo por periodos.")
        frames[str(sheet)] = frame

    if not frames:
        raise ApiError(422, "ARCHIVO_SIN_DATOS", "El archivo no contiene filas de datos.")
    return frames
