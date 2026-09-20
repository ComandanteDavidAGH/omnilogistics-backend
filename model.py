"""Modelo de datos canónico de GENESIS para operaciones de transporte de carga.

Cada campo define:
  - type: tipo de dato esperado ("text" acepta también IDs numéricos)
  - strong: sinónimos específicos (coincidencia confiable)
  - weak: sinónimos genéricos que SIEMPRE requieren confirmación humana
Los sinónimos van sin tildes; el motor normaliza las columnas antes de comparar.
"""
from __future__ import annotations

FIELDS: dict = {
    "TRIP_ID": {
        "entity": "TRIP", "type": "text", "label": "ID de viaje",
        "strong": ["viaje", "folio", "ticket", "operacion", "guia", "manifiesto", "remesa",
                   "planilla", "orden de servicio", "numero de viaje", "id viaje", "codigo de viaje"],
        "weak": ["id", "codigo", "numero", "consecutivo", "documento"],
    },
    "TRIP_DATE": {
        "entity": "TRIP", "type": "date", "label": "Fecha del viaje",
        "strong": ["fecha", "fecha viaje", "fecha salida", "salida", "emision", "fecha de emision",
                   "fecha servicio"],
        "weak": ["dia", "periodo"],
    },
    "DISTANCE_KM": {
        "entity": "TRIP", "type": "numeric", "label": "Distancia (km)",
        "strong": ["km", "kms", "kilometros", "kilometraje", "distancia", "recorrido", "km recorridos"],
        "weak": [],
    },
    "VEHICLE_ID": {
        "entity": "VEHICLE", "type": "text", "label": "Vehículo / placa",
        "strong": ["placa", "vehiculo", "unidad", "tracto", "tractocamion", "camion", "truck", "movil"],
        "weak": ["equipo", "carro"],
    },
    "DRIVER_ID": {
        "entity": "DRIVER", "type": "text", "label": "Conductor",
        "strong": ["conductor", "chofer", "operador", "driver"],
        "weak": ["empleado", "cedula"],
    },
    "ROUTE_NAME": {
        "entity": "ROUTE", "type": "text", "label": "Ruta",
        "strong": ["ruta", "trayecto", "tramo", "origen destino"],
        "weak": ["origen", "destino"],
    },
    "CUSTOMER_NAME": {
        "entity": "CUSTOMER", "type": "text", "label": "Cliente",
        "strong": ["cliente", "razon social", "cuenta"],
        "weak": ["empresa", "tercero"],
    },
    "REVENUE": {
        "entity": "FINANCE", "type": "numeric", "label": "Ingreso / flete",
        "strong": ["ingreso", "flete", "facturado", "facturacion", "valor flete", "tarifa", "venta",
                   "valor facturado", "total facturado", "valor factura", "ingreso bruto", "ingreso neto"],
        "weak": ["total", "valor", "monto", "importe", "precio"],
    },
    "COST_FUEL": {
        "entity": "COST", "type": "numeric", "label": "Costo de combustible",
        "strong": ["combustible", "diesel", "acpm", "gasolina", "tanqueo", "costo combustible",
                   "valor combustible"],
        "weak": [],
    },
    "COST_TOLL": {
        "entity": "COST", "type": "numeric", "label": "Costo de peajes",
        "strong": ["peaje", "costo peaje", "valor peaje"],
        "weak": [],
    },
    "COST_MAINT": {
        "entity": "COST", "type": "numeric", "label": "Costo de mantenimiento",
        "strong": ["mantenimiento", "repuesto", "taller", "llanta", "reparacion"],
        "weak": [],
    },
    "COST_DRIVER": {
        "entity": "COST", "type": "numeric", "label": "Costo de conductor / viáticos",
        "strong": ["viatico", "pago conductor", "comision conductor", "nomina conductor", "anticipo",
                   "alimentacion"],
        "weak": [],
    },
    "COST_OTHER": {
        "entity": "COST", "type": "numeric", "label": "Otros costos",
        "strong": ["otros costos", "otros gastos", "gastos varios", "imprevisto", "costos indirectos"],
        "weak": [],
    },
    "COST_TOTAL": {
        "entity": "COST", "type": "numeric", "label": "Costo total del viaje",
        "strong": ["costo total", "total costos", "gasto total", "total gastos", "costo operacional"],
        "weak": ["costo", "gasto", "egreso"],
    },
    "VOLUME_LTS": {
        "entity": "FUEL", "type": "numeric", "label": "Combustible (litros)",
        "strong": ["litros", "lts", "litro", "volumen litros", "volumen lts"],
        "weak": ["volumen"],
    },
    "VOLUME_GAL": {
        "entity": "FUEL", "type": "numeric", "label": "Combustible (galones)",
        "strong": ["galones", "galon", "gal", "gls", "volumen galones"],
        "weak": ["volumen"],
    },
}

CANONICAL_IDS = list(FIELDS.keys())
LABELS = {k: v["label"] for k, v in FIELDS.items()}

KEYS = ["TRIP_ID", "VEHICLE_ID", "DRIVER_ID"]
COST_COMPONENTS = ["COST_FUEL", "COST_TOLL", "COST_MAINT", "COST_DRIVER", "COST_OTHER"]
COST_COLUMNS = COST_COMPONENTS + ["COST_TOTAL"]
VOLUME_COLUMNS = ["VOLUME_LTS", "VOLUME_GAL"]
MEASURES = ["REVENUE"] + COST_COLUMNS + VOLUME_COLUMNS + ["DISTANCE_KM"]

LITROS_POR_GALON = 3.78541
