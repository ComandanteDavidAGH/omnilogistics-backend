"""Entendimiento de datos: propone a qué campo canónico corresponde cada columna.

Mejoras clave frente a la versión de coincidencia por subcadenas:
  - Compara PALABRAS completas y sin tildes ("Validado" ya no coincide con "id").
  - Sinónimos genéricos ("total", "costo", "id") nunca se aplican solos: piden confirmación.
  - Los IDs numéricos no se penalizan.
  - Cuando dos columnas compiten por el mismo campo, gana la claramente mejor; si empatan, decide el humano.
  - Una columna "no reconocida" ya no bloquea: se ignora por defecto.
  - Los mapeos confirmados se recuerdan por cliente (firma de encabezados).
"""
from __future__ import annotations

import difflib
import hashlib
from typing import Callable, Optional

import pandas as pd

from .model import FIELDS, CANONICAL_IDS, normalize_canonical
from .parsing import date_success_rate, normalize_text, numeric_success_rate

AUTO_THRESHOLD = 0.85
SUGGEST_THRESHOLD = 0.35
WINNER_MARGIN = 0.08


def _stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _prepare_synonyms() -> list:
    prepared = []
    for canonical, spec in FIELDS.items():
        for weak, synonyms in ((False, spec["strong"]), (True, spec["weak"])):
            for syn in synonyms:
                tokens = tuple(_stem(t) for t in normalize_text(syn).split())
                if tokens:
                    prepared.append((canonical, tokens, " ".join(tokens), weak))
    return prepared


_SYNONYMS = _prepare_synonyms()


def sheet_signature(columns) -> str:
    """Huella estable de los encabezados de una hoja; identifica "el mismo formato de archivo"."""
    norm = sorted(normalize_text(c) for c in columns)
    return hashlib.sha256("|".join(norm).encode("utf-8")).hexdigest()[:32]


def infer_data_type(series: pd.Series) -> str:
    sample = series.dropna()
    if sample.empty:
        return "empty"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if len(sample) > 500:
        sample = sample.sample(500, random_state=0)
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return "numeric"
    if numeric_success_rate(sample) >= 0.8:
        return "numeric"
    if date_success_rate(sample) >= 0.8:
        return "date"
    return "text"


def _type_factor(expected: str, actual: str) -> float:
    if actual == "empty":
        return 0.5
    if expected == actual:
        return 1.0
    if expected == "text" and actual == "numeric":
        return 1.0  # IDs y placas pueden ser numéricos
    if expected == "text" and actual == "date":
        return 0.5
    return 0.4


def _best_candidate(column: str, actual_type: str) -> tuple:
    """(canónico, confianza, longitud_del_sinónimo) del mejor campo para una columna."""
    tokens = [_stem(t) for t in normalize_text(column).split()]
    if not tokens:
        return None, 0.0, 0
    col_str = " ".join(tokens)
    token_set = set(tokens)
    best = (None, 0.0, 0)
    for canonical, syn_tokens, syn_str, weak in _SYNONYMS:
        if col_str == syn_str:
            score = 1.0
        elif token_set.issuperset(syn_tokens):
            score = 0.92
        elif len(syn_str) >= 5:
            ratio = difflib.SequenceMatcher(None, col_str, syn_str).ratio()
            score = ratio * 0.8 if ratio >= 0.8 else 0.0
        else:
            score = 0.0
        if score == 0.0:
            continue
        if weak:
            score *= 0.65
        score *= _type_factor(FIELDS[canonical]["type"], actual_type)
        if (score, len(syn_str)) > (best[1], best[2]):
            best = (canonical, score, len(syn_str))
    return best


def _samples(series: pd.Series, n: int = 3) -> list:
    return [str(v)[:40] for v in series.dropna().head(n).tolist()]


class GenesisDataUnderstanding:
    def analyze_workbook(self, dfs: dict, memory_lookup: Optional[Callable[[str], Optional[dict]]] = None) -> dict:
        result = {
            "status": "success",
            "sheets_detected": 0,
            "sheets_analysis": {},
            "global_entities": [],
            "total_records": 0,
            "canonical_fields": [{"id": k, "label": v["label"], "type": v["type"]} for k, v in FIELDS.items()],
        }
        for sheet, df in dfs.items():
            if df.empty:
                continue
            analysis = self._analyze_sheet(df, memory_lookup)
            result["sheets_detected"] += 1
            result["total_records"] += len(df)
            result["sheets_analysis"][sheet] = analysis
            for entity in analysis["entities_found"]:
                if entity not in result["global_entities"]:
                    result["global_entities"].append(entity)
        return result

    def _analyze_sheet(self, df: pd.DataFrame, memory_lookup) -> dict:
        columns = [c for c in df.columns if not str(c).startswith("_src_")]
        signature = sheet_signature(columns)
        remembered = memory_lookup(signature) if memory_lookup else None

        analysis = {
            "records": len(df), "columns_count": len(columns), "header_row": df.attrs.get("header_row", 0),
            "signature": signature, "fields_mapping": {}, "ambiguities": [], "entities_found": [],
            "from_memory": bool(remembered),
        }
        competitors: dict = {}
        entities: set = set()

        for col in columns:
            actual = infer_data_type(df[col])
            samples = _samples(df[col])

            if remembered and col in remembered:
                canonical = normalize_canonical(remembered[col])   # ← NUEVO: traduce alias
                if canonical in CANONICAL_IDS:
                    analysis["fields_mapping"][col] = {"canonical": canonical, "confidence": 1.0,
                                                       "detected_type": actual, "source": "memoria",
                                                       "sample_values": samples}
                    entities.add(FIELDS[canonical]["entity"])
                else:  # decisión previa: ignorar
                    analysis["ambiguities"].append({
                        "original_column": col, "detected_type": actual, "possible_match": "UNKNOWN",
                        "confidence": 1.0, "requires_decision": False, "sample_values": samples,
                        "note": "Ignorada según la decisión anterior de tu equipo."})
                continue

            canonical, confidence, _ = _best_candidate(col, actual)
            if canonical and confidence >= AUTO_THRESHOLD:
                competitors.setdefault(canonical, []).append(
                    {"col": col, "confidence": confidence, "type": actual, "samples": samples})
            elif canonical and confidence >= SUGGEST_THRESHOLD:
                analysis["ambiguities"].append({
                    "original_column": col, "detected_type": actual, "possible_match": canonical,
                    "confidence": round(confidence, 2), "requires_decision": True, "sample_values": samples})
            else:
                analysis["ambiguities"].append({
                    "original_column": col, "detected_type": actual, "possible_match": "UNKNOWN",
                    "confidence": round(confidence, 2), "requires_decision": False, "sample_values": samples})

        for canonical, items in competitors.items():
            items.sort(key=lambda c: -c["confidence"])
            clear_winner = len(items) == 1 or (items[0]["confidence"] - items[1]["confidence"] >= WINNER_MARGIN)
            if clear_winner:
                win = items[0]
                analysis["fields_mapping"][win["col"]] = {
                    "canonical": canonical, "confidence": round(win["confidence"], 2),
                    "detected_type": win["type"], "source": "auto", "sample_values": win["samples"]}
                entities.add(FIELDS[canonical]["entity"])
                for lost in items[1:]:
                    analysis["ambiguities"].append({
                        "original_column": lost["col"], "detected_type": lost["type"],
                        "possible_match": "UNKNOWN", "confidence": round(lost["confidence"], 2),
                        "requires_decision": False, "sample_values": lost["samples"],
                        "note": f"Compite con '{win['col']}' por el mismo campo; se ignora salvo que la asignes."})
            else:
                for item in items:
                    analysis["ambiguities"].append({
                        "original_column": item["col"], "detected_type": item["type"],
                        "possible_match": canonical, "confidence": round(item["confidence"], 2),
                        "requires_decision": True, "collision_warning": True, "sample_values": item["samples"]})

        analysis["entities_found"] = sorted(entities)
        return analysis
