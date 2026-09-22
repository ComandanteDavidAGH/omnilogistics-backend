"""ModelBuilder de Genesis Core v1.3.

Construye la representación formal del Modelo Semántico antes de consolidar:
  Hojas -> Tablas -> Tipo de Tabla (FACT/DIMENSION/REFERENCE) -> Grano -> Relaciones.

Garantía de producción:
  - Diseño 100% aditivo: No altera ni rompe las interfaces existentes del pipeline.
  - Clasificación determinista por presencia de medidas, claves y cardinalidad de grano.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import pandas as pd

from .model import TableType, Grain, MEASURES, KEYS


@dataclass
class TableMetadata:
    nombre: str
    table_type: TableType
    grain: Grain
    filas: int
    columnas: list[str]
    primary_keys: list[str]
    foreign_keys: list[str]
    measures: list[str]
    attributes: list[str]


@dataclass
class DataModel:
    tablas: dict[str, TableMetadata] = field(default_factory=dict)
    fact_tables: list[str] = field(default_factory=list)
    dimension_tables: list[str] = field(default_factory=list)
    reference_tables: list[str] = field(default_factory=list)
    base_table: str | None = None


class ModelBuilder:
    """Constructor del modelo relacional semántico."""

    def build_model(self, dfs: dict[str, pd.DataFrame], mapping: dict[str, dict]) -> DataModel:
        model = DataModel()

        for name, df in dfs.items():
            if df is None or df.empty:
                continue

            scoped = mapping.get(name, {}) or {}
            mapped_fields = {k: v for k, v in scoped.items() if v and v != "UNKNOWN"}
            
            pks = [canon for canon in mapped_fields.values() if canon in KEYS]
            measures = [canon for canon in mapped_fields.values() if canon in MEASURES]
            
            n_rows = len(df)
            n_cols = len(df.columns)

            # --- Determinar Grano y Tipo de Tabla ---
            if "TRIP_ID" in pks or ("REVENUE" in measures and n_rows > 1):
                grain = Grain.TRIP
                table_type = TableType.FACT
            elif "VEHICLE_ID" in pks and "TRIP_ID" not in pks:
                grain = Grain.VEHICLE
                table_type = TableType.DIMENSION if len(measures) == 0 else TableType.FACT
            elif "CUSTOMER_NAME" in mapped_fields.values() and "TRIP_ID" not in pks:
                grain = Grain.CUSTOMER
                table_type = TableType.DIMENSION
            elif n_rows <= 31 and "TRIP_DATE" in mapped_fields.values():
                grain = Grain.PERIOD
                table_type = TableType.REFERENCE
            else:
                grain = Grain.UNKNOWN
                table_type = TableType.FACT if measures else TableType.DIMENSION

            meta = TableMetadata(
                nombre=name,
                table_type=table_type,
                grain=grain,
                filas=n_rows,
                columnas=list(df.columns),
                primary_keys=pks,
                foreign_keys=[k for k in pks if k != "TRIP_ID"],
                measures=measures,
                attributes=[k for k, v in mapped_fields.items() if v not in pks and v not in measures]
            )

            model.tablas[name] = meta

            if table_type == TableType.FACT:
                model.fact_tables.append(name)
            elif table_type == TableType.DIMENSION:
                model.dimension_tables.append(name)
            else:
                model.reference_tables.append(name)

        # Selección de la tabla base semántica (prioriza Fact de Viajes)
        for t_name, t_meta in model.tablas.items():
            if t_meta.grain == Grain.TRIP and t_meta.table_type == TableType.FACT:
                model.base_table = t_name
                break

        if not model.base_table and model.fact_tables:
            model.base_table = model.fact_tables[0]
        elif not model.base_table and model.tablas:
            model.base_table = list(model.tablas.keys())[0]

        return model
