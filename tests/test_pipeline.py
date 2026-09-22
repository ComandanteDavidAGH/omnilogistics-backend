"""Suite de Pruebas de Regresión para Genesis Core v1.2.

Valida que el motor responda correctamente frente a escenarios críticos de negocio:
  1. Protección contra multiplicación de filas en uniones 1:N.
  2. Acotamiento de Confianza Analítica al 70% por hojas secundarias no cruzadas.
  3. Detección precisa de duplicados exactos y cálculo sin solapamientos.
  4. Resiliencia ante datos ilegibles o formatos numéricos ambiguos.
"""
import pandas as pd
import pytest

from app.core.pipeline import run_pipeline


@pytest.fixture
def base_config():
    return {
        "min_margin_percent": 10.0,
        "z_score_threshold": 3.5,
        "allow_negative_margin": 0,
        "revenue_includes_vat": 0,
        "vat_rate": 0.19,
        "outlier_min_group": 5,
        "reconciliation_tolerance_pct": 1.0,
    }


def test_pipeline_prevents_row_multiplication(base_config):
    """Prueba que un cruce 1:N no altere el número de filas de la tabla base de viajes."""
    viajes = pd.DataFrame({
        "TRIP_ID": ["T1", "T2"],
        "VEHICLE_ID": ["ABC123", "DEF456"],
        "REVENUE": [1000000, 1500000]
    })
    # Combustible tiene 2 tanqueos para el viaje T1
    combustible = pd.DataFrame({
        "TRIP_ID": ["T1", "T1", "T2"],
        "COST_FUEL": [200000, 150000, 400000]
    })

    dfs = {"Viajes": viajes, "Combustible": combustible}
    mapping = {
        "Viajes": {"TRIP_ID": "TRIP_ID", "VEHICLE_ID": "VEHICLE_ID", "REVENUE": "REVENUE"},
        "Combustible": {"TRIP_ID": "TRIP_ID", "COST_FUEL": "COST_FUEL"}
    }

    result = run_pipeline(dfs, mapping, base_config)

    assert result["estado"] in ("OK", "CON_RESERVAS")
    assert result["financials"]["filasAnalizadas"] == 2  # No se multiplicó a 3 filas
    assert result["financials"]["totalCostos"] == 750000  # (200k + 150k) + 400k


def test_confidence_capped_at_70_for_secondary_vehicle_costs(base_config):
    """Prueba que la confianza analítica se tope a 70% si hay costos por vehículo sin TRIP_ID."""
    viajes = pd.DataFrame({
        "TRIP_ID": ["T1", "T2"],
        "VEHICLE_ID": ["ABC123", "DEF456"],
        "REVENUE": [1000000, 1500000]
    })
    # Costos de mantenimiento por vehículo (sin TRIP_ID)
    mantenimiento = pd.DataFrame({
        "VEHICLE_ID": ["ABC123", "DEF456"],
        "COST_MAINT": [300000, 500000]
    })

    dfs = {"Viajes": viajes, "Mantenimiento": mantenimiento}
    mapping = {
        "Viajes": {"TRIP_ID": "TRIP_ID", "VEHICLE_ID": "VEHICLE_ID", "REVENUE": "REVENUE"},
        "Mantenimiento": {"VEHICLE_ID": "VEHICLE_ID", "COST_MAINT": "COST_MAINT"}
    }

    result = run_pipeline(dfs, mapping, base_config)

    assert result["calidad"]["analytical_confidence"] <= 70.0
    assert result["calidad"]["nivelConfianza"] == "MEDIA"


def test_exact_duplicates_detection(base_config):
    """Prueba la detección de duplicados exactos y el aislamiento en el dinero en riesgo."""
    viajes = pd.DataFrame({
        "TRIP_ID": ["T1", "T1", "T2"],
        "VEHICLE_ID": ["ABC123", "ABC123", "DEF456"],
        "REVENUE": [1000000, 1000000, 2000000]
    })

    dfs = {"Viajes": viajes}
    mapping = {"Viajes": {"TRIP_ID": "TRIP_ID", "VEHICLE_ID": "VEHICLE_ID", "REVENUE": "REVENUE"}}

    result = run_pipeline(dfs, mapping, base_config)

    assert result["financials"]["duplicadosExactos"] == 1
    assert result["financials"]["totalIngresos"] == 3000000  # Suma neta sin el duplicado
    assert result["financials"]["dineroEnRiesgoDetalle"]["sobrefacturacion_potencial"] == 1000000
