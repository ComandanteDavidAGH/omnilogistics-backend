"""Metamodelo Semántico de Genesis Core v1.2.

Define la arquitectura formal de datos:
  - Tipos de tabla (HECHO, DIMENSIÓN, REFERENCIA)
  - Granos de análisis (VIAJE, VEHÍCULO, CLIENTE, RUTA, DÍA)
  - Taxonomía de campos (PK, FK, MEDIDA, ATRIBUTO, FECHA)
  - Diccionarios y listas de compatibilidad para el motor actual
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# 1. ENUMERADORES Y ARQUITECTURA DE MODELADO (EL CEREBRO)
# ---------------------------------------------------------------------------
class TableType(str, Enum):
    FACT = "FACT"             # Hecho transaccional (ej. Viajes, Manifiestos)
    DIMENSION = "DIMENSION"   # Entidad descriptiva (ej. Ficha técnica de Vehículos, Clientes)
    REFERENCE = "REFERENCE"   # Parámetros y tarifas (ej. Precios de Combustible por fecha, TRM)


class Grain(str, Enum):
    TRIP = "1_TRIP"           # Un registro = Un viaje
    VEHICLE = "1_VEHICLE"     # Un registro = Un vehículo
    CUSTOMER = "1_CUSTOMER"   # Un registro = Un cliente
    ROUTE = "1_ROUTE"         # Un registro = Una ruta
    PERIOD = "1_PERIOD"       # Un registro = Un periodo/mes
    DAY = "1_DAY"             # Un registro = Un día
    UNKNOWN = "UNKNOWN"


class FieldRole(str, Enum):
    PRIMARY_KEY = "PK"
    FOREIGN_KEY = "FK"
    MEASURE = "MEASURE"
    ATTRIBUTE = "ATTRIBUTE"
    DATE = "DATE"


@dataclass(frozen=True)
class EntityDef:
    id: str
    name: str
    table_type: TableType
    expected_grain: Grain
    description: str


@dataclass(frozen=True)
class FieldDef:
    canonical: str
    role: FieldRole
    entity: str
    label: str
    type: str  # "numeric", "text", "date"
    strong_synonyms: tuple[str, ...]
    weak_synonyms: tuple[str, ...]


# ---------------------------------------------------------------------------
# 2. DEFINICIÓN DE ENTIDADES DEL NEGOCIO
# ---------------------------------------------------------------------------
ENTITIES: dict[str, EntityDef] = {
    "TRIP": EntityDef(
        id="TRIP",
        name="Viaje / Operación",
        table_type=TableType.FACT,
        expected_grain=Grain.TRIP,
        description="Transacción operativa individual de transporte de carga."
    ),
    "VEHICLE": EntityDef(
        id="VEHICLE",
        name="Vehículo / Flota",
        table_type=TableType.DIMENSION,
        expected_grain=Grain.VEHICLE,
        description="Activo productivo (tractomula, camión, remolque)."
    ),
    "CUSTOMER": EntityDef(
        id="CUSTOMER",
        name="Cliente",
        table_type=TableType.DIMENSION,
        expected_grain=Grain.CUSTOMER,
        description="Generador de carga o pagador del flete."
    ),
    "ROUTE": EntityDef(
        id="ROUTE",
        name="Ruta / Corredor",
        table_type=TableType.DIMENSION,
        expected_grain=Grain.ROUTE,
        description="Trayecto origen-destino estandarizado."
    ),
    "DRIVER": EntityDef(
        id="DRIVER",
        name="Conductor",
        table_type=TableType.DIMENSION,
        expected_grain=Grain.UNKNOWN,
        description="Operador asignado al vehículo o viaje."
    ),
    "FUEL_PRICE": EntityDef(
        id="FUEL_PRICE",
        name="Referencia de Combustible",
        table_type=TableType.REFERENCE,
        expected_grain=Grain.DAY,
        description="Tabla de precios de referencia o histórico de galón."
    )
}


# ---------------------------------------------------------------------------
# 3. TAXONOMÍA COMPLETA DE CAMPOS CANÓNICOS
# ---------------------------------------------------------------------------
TAXONOMY: dict[str, FieldDef] = {
    # --- Llaves y Fechas ---
    "TRIP_ID": FieldDef(
        canonical="TRIP_ID", role=FieldRole.PRIMARY_KEY, entity="TRIP", label="ID de viaje", type="text",
        strong_synonyms=("id viaje", "manifiesto", "remesa", "planilla", "numero viaje", "num viaje", "orden carga"),
        weak_synonyms=("id", "viaje", "codigo", "documento", "consecutivo")
    ),
    "VEHICLE_ID": FieldDef(
        canonical="VEHICLE_ID", role=FieldRole.FOREIGN_KEY, entity="VEHICLE", label="Placa", type="text",
        strong_synonyms=("placa", "patente", "vehiculo id", "id vehiculo", "placa vehiculo", "placa camion"),
        weak_synonyms=("vehiculo", "camion", "unidad", "equipo", "cabazote", "mula")
    ),
    "DRIVER_ID": FieldDef(
        canonical="DRIVER_ID", role=FieldRole.FOREIGN_KEY, entity="DRIVER", label="ID conductor", type="text",
        strong_synonyms=("cedula conductor", "id conductor", "cc conductor", "nit conductor"),
        weak_synonyms=("conductor", "chofer", "operador")
    ),
    "CUSTOMER_NAME": FieldDef(
        canonical="CUSTOMER_NAME", role=FieldRole.ATTRIBUTE, entity="CUSTOMER", label="Cliente", type="text",
        strong_synonyms=("nombre cliente", "razon social", "generador carga", "cliente final"),
        weak_synonyms=("cliente", "empresa", "pagador")
    ),
    "ROUTE_NAME": FieldDef(
        canonical="ROUTE_NAME", role=FieldRole.ATTRIBUTE, entity="ROUTE", label="Ruta", type="text",
        strong_synonyms=("nombre ruta", "trayecto", "origen destino", "corredor"),
        weak_synonyms=("ruta", "origen", "destino", "tramo")
    ),
    "TRIP_DATE": FieldDef(
        canonical="TRIP_DATE", role=FieldRole.DATE, entity="TRIP", label="Fecha de viaje", type="date",
        strong_synonyms=("fecha viaje", "fecha despacho", "fecha salida", "fecha manifiesto", "fecha cargue"),
        weak_synonyms=("fecha", "dia", "periodo")
    ),

    # --- Medidas Económicas (Ingresos y Costos) ---
    "REVENUE": FieldDef(
        canonical="REVENUE", role=FieldRole.MEASURE, entity="TRIP", label="Ingreso", type="numeric",
        strong_synonyms=("ingreso", "flete", "flete total", "valor flete", "facturado", "valor viaje", "ingreso viaje"),
        weak_synonyms=("valor", "total", "neto", "bruto", "monto", "venta")
    ),
    "COST_TOTAL": FieldDef(
        canonical="COST_TOTAL", role=FieldRole.MEASURE, entity="TRIP", label="Costo total", type="numeric",
        strong_synonyms=("costo total", "costo viaje", "gastos totales", "costo operativo total"),
        weak_synonyms=("costo", "gastos")
    ),
    "COST_FUEL": FieldDef(
        canonical="COST_FUEL", role=FieldRole.MEASURE, entity="TRIP", label="Costo de combustible", type="numeric",
        strong_synonyms=("costo combustible", "valor combustible", "gasto ACPM", "gasto diesel", "costo ACPM", "tanqueo valor"),
        weak_synonyms=("combustible", "acpm", "diesel", "gasolina", "tanqueo")
    ),
    "COST_TOLLS": FieldDef(
        canonical="COST_TOLLS", role=FieldRole.MEASURE, entity="TRIP", label="Costo de peajes", type="numeric",
        strong_synonyms=("costo peajes", "valor peajes", "gastos peaje", "peajes total"),
        weak_synonyms=("peaje", "peajes", "flypass")
    ),
    "COST_MAINT": FieldDef(
        canonical="COST_MAINT", role=FieldRole.MEASURE, entity="TRIP", label="Costo de mantenimiento", type="numeric",
        strong_synonyms=("costo mantenimiento", "gastos repuestos", "taller", "llantas", "mantenimiento total"),
        weak_synonyms=("mantenimiento", "repuestos", "reparaciones")
    ),
    "COST_DRIVER": FieldDef(
        canonical="COST_DRIVER", role=FieldRole.MEASURE, entity="TRIP", label="Costo de conductor / viáticos", type="numeric",
        strong_synonyms=("viaticos", "pago conductor", "bonificacion conductor", "gastos viaje conductor"),
        weak_synonyms=("conductor", "nomina")
    ),
    "COST_OTHER": FieldDef(
        canonical="COST_OTHER", role=FieldRole.MEASURE, entity="TRIP", label="Otros costos", type="numeric",
        strong_synonyms=("otros costos", "gastos varios", "imprevistos", "cargue y descargue", "descarque"),
        weak_synonyms=("otros", "varios")
    ),

    # --- Medidas Operativas (Distancias y Volúmenes) ---
    "DISTANCE_KM": FieldDef(
        canonical="DISTANCE_KM", role=FieldRole.MEASURE, entity="TRIP", label="Distancia (km)", type="numeric",
        strong_synonyms=("kilometros", "km recorridos", "distancia km", "kilometraje"),
        weak_synonyms=("km", "distancia")
    ),
    "VOLUME_GAL": FieldDef(
        canonical="VOLUME_GAL", role=FieldRole.MEASURE, entity="TRIP", label="Galones", type="numeric",
        strong_synonyms=("galones", "volumen galones", "cant galones", "galones acpm"),
        weak_synonyms=("gal", "galones")
    ),
    "VOLUME_LTS": FieldDef(
        canonical="VOLUME_LTS", role=FieldRole.MEASURE, entity="TRIP", label="Litros", type="numeric",
        strong_synonyms=("litros", "volumen litros", "cant litros"),
        weak_synonyms=("lts", "litros")
    )
}


# ---------------------------------------------------------------------------
# 4. CAPA DE COMPATIBILIDAD CON EL CÓDIGO EXISTENTE
# ---------------------------------------------------------------------------
# Todo el código previo (merge.py, quality.py, engine.py, semantic.py) seguirá
# funcionando de forma transparente sin modificar sus importaciones.

CANONICAL_IDS: list[str] = list(TAXONOMY.keys())

KEYS: list[str] = [k for k, v in TAXONOMY.items() if v.role in (FieldRole.PRIMARY_KEY, FieldRole.FOREIGN_KEY)]

MEASURES: list[str] = [k for k, v in TAXONOMY.items() if v.role == FieldRole.MEASURE]

COST_COLUMNS: list[str] = [
    "COST_TOTAL", "COST_FUEL", "COST_TOLLS", "COST_MAINT", "COST_DRIVER", "COST_OTHER"
]

COST_COMPONENTS: list[str] = [
    "COST_FUEL", "COST_TOLLS", "COST_MAINT", "COST_DRIVER", "COST_OTHER"
]

VOLUME_COLUMNS: list[str] = ["VOLUME_GAL", "VOLUME_LTS"]

LABELS: dict[str, str] = {k: v.label for k, v in TAXONOMY.items()}

# Diccionario consumido por GenesisDataUnderstanding (semantic.py)
FIELDS: dict[str, dict] = {
    k: {
        "label": v.label,
        "type": v.type,
        "strong": list(v.strong_synonyms),
        "weak": list(v.weak_synonyms),
        "entity": v.entity,
    }
    for k, v in TAXONOMY.items()
}

LITROS_POR_GALON: float = 3.78541
