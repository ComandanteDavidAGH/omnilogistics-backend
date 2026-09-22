"""Relationship Engine de Genesis Core v1.2.

Analiza la estructura de relaciones entre tablas:
  - Detección de llaves candidatas y análisis de unicidad
  - Evaluación de cardinalidad real (1:1, 1:N, N:1, N:M, LOOKUP)
  - Verificación de integridad referencial y registros huérfanos
  - Autorización formal de uniones (joins) para impedir la multiplicación silenciosa de filas
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import pandas as pd

from .model import KEYS, TAXONOMY, FieldRole


class Cardinality(str, Enum):
    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"          # La base tiene 1, la secundaria tiene muchos
    MANY_TO_ONE = "N:1"          # La base tiene muchos, la secundaria tiene 1 (ej. Dimensión)
    MANY_TO_MANY = "N:M"        # Peligroso: requiere agregación previa
    LOOKUP_DATE = "LOOKUP_DATE"  # Búsqueda por rango de fechas/tarifas
    NONE = "SIN_RELACION"


class JoinMode(str, Enum):
    PER_TRIP = "por_viaje"               # Cruce fila a fila o agregando el lado N
    ATTRIBUTES = "atributos"             # Cruce dimensional (ej. Ficha del vehículo/Cliente)
    BY_VEHICLE = "aparte_por_vehiculo"   # Se conserva para análisis en la capa Vehículo
    NO_JOIN = "sin_cruce"                # No hay llave o la relación es ambigua


@dataclass
class RelationshipReport:
    left_table: str
    right_table: str
    primary_key_candidate: str | None
    shared_keys: list[str]
    cardinality: Cardinality
    join_mode: JoinMode
    referential_integrity_pct: float    # % de llaves de la derecha presentes en la base
    base_coverage_pct: float            # % de la base que tiene cruce en la derecha
    is_authorized: bool
    reason: str
    orphans_count: int = 0


class RelationshipEngine:
    """Motor de análisis de relaciones e integridad referencial."""

    @staticmethod
    def analyze(
        base_df: pd.DataFrame,
        right_df: pd.DataFrame,
        base_name: str = "Base",
        right_name: str = "Secundaria"
    ) -> RelationshipReport:
        if base_df.empty or right_df.empty:
            return RelationshipReport(
                left_table=base_name, right_table=right_name, primary_key_candidate=None,
                shared_keys=[], cardinality=Cardinality.NONE, join_mode=JoinMode.NO_JOIN,
                referential_integrity_pct=0.0, base_coverage_pct=0.0, is_authorized=False,
                reason="Una o ambas tablas están vacías."
            )

        base_cols = set(base_df.columns)
        right_cols = set(right_df.columns)
        shared = [k for k in KEYS if k in base_cols and k in right_cols]

        if not shared:
            return RelationshipReport(
                left_table=base_name, right_table=right_name, primary_key_candidate=None,
                shared_keys=[], cardinality=Cardinality.NONE, join_mode=JoinMode.NO_JOIN,
                referential_integrity_pct=0.0, base_coverage_pct=0.0, is_authorized=False,
                reason=f"La tabla '{right_name}' no comparte ninguna llave canónica (TRIP_ID, VEHICLE_ID, etc.) con '{base_name}'."
            )

        # Determinar la mejor llave primaria compartida
        pk = "TRIP_ID" if "TRIP_ID" in shared else shared[0]

        # Extraer llaves no nulas
        base_series = base_df[pk].dropna()
        right_series = right_df[pk].dropna()

        if base_series.empty or right_series.empty:
            return RelationshipReport(
                left_table=base_name, right_table=right_name, primary_key_candidate=pk,
                shared_keys=shared, cardinality=Cardinality.NONE, join_mode=JoinMode.NO_JOIN,
                referential_integrity_pct=0.0, base_coverage_pct=0.0, is_authorized=False,
                reason=f"La llave '{pk}' no tiene valores válidos para cruzar entre '{base_name}' y '{right_name}'."
            )

        # Evaluación de Unicidad
        base_is_unique = not base_series.duplicated().any()
        right_is_unique = not right_series.duplicated().any()

        if base_is_unique and right_is_unique:
            cardinality = Cardinality.ONE_TO_ONE
        elif base_is_unique and not right_is_unique:
            cardinality = Cardinality.ONE_TO_MANY
        elif not base_is_unique and right_is_unique:
            cardinality = Cardinality.MANY_TO_ONE
        else:
            cardinality = Cardinality.MANY_TO_MANY

        # Coberturas e Integridad Referencial
        set_base = set(base_series)
        set_right = set(right_series)

        matched_keys = set_base & set_right
        orphans = set_right - set_base

        ref_integrity = (len(matched_keys) / len(set_right) * 100) if set_right else 0.0
        base_cov = (len(matched_keys) / len(set_base) * 100) if set_base else 0.0

        # Lógica de Autorización de Join
        is_authorized = False
        join_mode = JoinMode.NO_JOIN
        reason = ""

        right_measures = [
            c for c in right_cols 
            if c in TAXONOMY and TAXONOMY[c].role == FieldRole.MEASURE
        ]

        if pk == "TRIP_ID":
            is_authorized = True
            join_mode = JoinMode.PER_TRIP
            if cardinality == Cardinality.ONE_TO_MANY:
                reason = f"Cruce autorizado por viaje ({pk}). Los registros de '{right_name}' se agregarán previa suma para preservar el número de filas de la base."
            else:
                reason = f"Cruce directo autorizado por ID de viaje ({pk})."

        elif pk == "VEHICLE_ID" and not right_measures:
            is_authorized = True
            join_mode = JoinMode.ATTRIBUTES
            reason = f"Cruce dimensional de atributos por placa ({pk}) autorizado."

        elif pk == "VEHICLE_ID" and right_measures:
            is_authorized = False
            join_mode = JoinMode.BY_VEHICLE
            reason = f"La tabla '{right_name}' aporta medidas económicas ({', '.join(right_measures)}) pero no tiene TRIP_ID. Se redirige al análisis por vehículo para no inflar los totales."

        else:
            if right_is_unique:
                is_authorized = True
                join_mode = JoinMode.ATTRIBUTES
                reason = f"Cruce dimensional de atributos por llave '{pk}' autorizado."
            else:
                is_authorized = False
                join_mode = JoinMode.NO_JOIN
                reason = f"Relación de cardinalidad {cardinality.value} sobre '{pk}' no autorizada directamente sin agregación previa."

        return RelationshipReport(
            left_table=base_name,
            right_table=right_name,
            primary_key_candidate=pk,
            shared_keys=shared,
            cardinality=cardinality,
            join_mode=join_mode,
            referential_integrity_pct=round(ref_integrity, 1),
            base_coverage_pct=round(base_cov, 1),
            is_authorized=is_authorized,
            reason=reason,
            orphans_count=len(orphans)
        )
