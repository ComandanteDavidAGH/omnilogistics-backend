"""Suite de Pruebas de Regresión y Estrés para Genesis Core v1.3.

Valida que el motor responda correctamente frente a escenarios críticos de negocio:
  1. Protección contra multiplicación de filas en uniones 1:N.
  2. Acotamiento de Confianza Analítica al 70% por hojas secundarias no cruzadas.
  3. Taxonomía Financiera Estricta (Duplicados vs. Pérdida Directa).
  4. Resiliencia ante ingresos en $0 (evitar división por cero).
  5. Resiliencia ante ingresos negativos.
  6. Aislamiento de hojas basura o de control (Auxiliary sheets).
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
    """Prueba que un cruce 1:N no altere el número de filas de la tabla base."""
    viajes = pd.DataFrame({"TRIP_ID": ["T1", "T2"], "VEHICLE_ID": ["ABC123", "DEF456"], "REVENUE": [1000000, 1500000]})
    combustible = pd.DataFrame({"TRIP_ID": ["T1", "T1", "T2"], "COST_FUEL": [200000, 150000, 400000]})

    dfs = {"Viajes": viajes, "Combustible": combustible}
    mapping = {
        "Viajes": {"TRIP_ID": "TRIP_ID", "VEHICLE_ID": "VEHICLE_ID", "REVENUE": "REVENUE"},
        "Combustible": {"TRIP_ID": "TRIP_ID", "COST_FUEL": "COST_FUEL"}
    }
    result = run_pipeline(dfs, mapping, base_config)

    assert result["financials"]["filasAnalizadas"] == 2
    assert result["financials"]["totalCostos"] == 750000


def test_confidence_capped_at_70_for_secondary_vehicle_costs(base_config):
    """Prueba que la confianza analítica se tope a 70% si hay costos sin TRIP_ID directo."""
    viajes = pd.DataFrame({"TRIP_ID": ["T1", "T2"], "VEHICLE_ID": ["ABC123", "DEF456"], "REVENUE": [1000, 1500]})
    mantenimiento = pd.DataFrame({"VEHICLE_ID": ["ABC123", "DEF456"], "COST_MAINT": [300, 500]})

    dfs = {"Viajes": viajes, "Mantenimiento": mantenimiento}
    mapping = {
        "Viajes": {"TRIP_ID": "TRIP_ID", "VEHICLE_ID": "VEHICLE_ID", "REVENUE": "REVENUE"},
        "Mantenimiento": {"VEHICLE_ID": "VEHICLE_ID", "COST_MAINT": "COST_MAINT"}
    }
    result = run_pipeline(dfs, mapping, base_config)

    assert result["calidad"]["analytical_confidence"] <= 70.0
    assert result["calidad"]["nivelConfianza"] == "MEDIA"


def test_strict_financial_taxonomy_duplicates(base_config):
    """Prueba la nueva taxonomía financiera separando duplicados de pérdida directa."""
    viajes = pd.DataFrame({
        "TRIP_ID": ["T1", "T1", "T2"],
        "VEHICLE_ID": ["ABC123", "ABC123", "DEF456"],
        "REVENUE": [1000000, 1000000, 2000000],
        "COST_FUEL": [500000, 500000, 2500000]  # T2 tiene pérdida directa (2.5M > 2.0M)
    })

    dfs = {"Viajes": viajes}
    mapping = {"Viajes": {"TRIP_ID": "TRIP_ID", "VEHICLE_ID": "VEHICLE_ID", "REVENUE": "REVENUE", "COST_FUEL": "COST_FUEL"}}
    result = run_pipeline(dfs, mapping, base_config)

    detalle = result["financials"]["dineroEnRiesgoDetalle"]
    assert result["financials"]["duplicadosExactos"] == 1
    assert detalle["sobrecosto_potencial"] == 1000000     # Por el duplicado exacto de T1
    assert detalle["perdida_directa_observada"] == 500000 # Por la pérdida de T2 (2.5M - 2.0M)


def test_zero_and_negative_revenue_resilience(base_config):
    """Prueba que el motor no colapse con divisiones por cero o ingresos negativos."""
    viajes = pd.DataFrame({
        "TRIP_ID": ["T1", "T2", "T3"],
        "REVENUE": [0, -50000, 1000000],
        "COST_FUEL": [100000, 20000, 500000]
    })
    
    dfs = {"Viajes": viajes}
    mapping = {"Viajes": {"TRIP_ID": "TRIP_ID", "REVENUE": "REVENUE", "COST_FUEL": "COST_FUEL"}}
    result = run_pipeline(dfs, mapping, base_config)

    assert result["estado"] in ("OK", "CON_RESERVAS")
    # El margen global debe calcularse solo sobre el revenue neto positivo, o dar un número seguro
    assert isinstance(result["financials"]["margenGlobal"], float)


def test_auxiliary_garbage_sheet_isolation(base_config):
    """Prueba que una hoja de control basura no contamine ni rompa el modelo."""
    viajes = pd.DataFrame({"TRIP_ID": ["T1"], "REVENUE": [1000], "COST_FUEL": [500]})
    basura = pd.DataFrame({"NOTAS": ["Revisar esto", "Urgente"], "VALOR": [None, 45]})

    dfs = {"Viajes": viajes, "Basura": basura}
    mapping = {
        "Viajes": {"TRIP_ID": "TRIP_ID", "REVENUE": "REVENUE", "COST_FUEL": "COST_FUEL"},
        "Basura": {} # Sin mapeo
    }
    result = run_pipeline(dfs, mapping, base_config)

    assert result["estado"] in ("OK", "CON_RESERVAS")
    # La hoja 'Basura' no debe sumar filas analizadas ni afectar cálculos
    assert result["financials"]["filasAnalizadas"] == 1
