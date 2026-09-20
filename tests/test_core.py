"""Pruebas del núcleo con archivos sintéticos y respuestas conocidas.

Ejecutar:  pytest -q      (o:  python -m tests.run_core  si no tienes pytest)
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.core.engine import EconomicRuleEngine, robust_z
from app.core.ingestion import read_workbook
from app.core.merge import build_master
from app.core.parsing import normalize_key, parse_numeric
from app.core.pipeline import run_pipeline
from app.core.semantic import GenesisDataUnderstanding, sheet_signature
from app.errors import ApiError

SETTINGS = SimpleNamespace(max_rows=100_000, max_columns=100)


def _with_src(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["_src_row"] = range(2, len(df) + 2)
    return df


def _trips(n=20) -> pd.DataFrame:
    return pd.DataFrame({
        "Viaje": [f"T{i:03d}" for i in range(1, n + 1)],
        "Placa": ["ABC123" if i % 2 else "XYZ987" for i in range(n)],
        "Ruta": ["BAQ-BOG"] * n,
        "Flete": [1_000_000 + 10_000 * (i % 5) for i in range(n)],
        "Km": [1000] * n,
    })


# ---------------------------------------------------------------- números ---
def test_parse_numeric_formato_colombiano():
    s = pd.Series(["$1.500.000", "2.300.000,50", "1234,56", "COP 800.000", "(1.000)", "abc", None, ""])
    out, st = parse_numeric(s)
    assert out.iloc[0] == 1_500_000
    assert out.iloc[1] == 2_300_000.50
    assert out.iloc[2] == pytest.approx(1234.56)  # la coma es decimal: NO 123456
    assert out.iloc[3] == 800_000
    assert out.iloc[4] == -1000
    assert np.isnan(out.iloc[5])
    assert st["unparsed"] == 1 and st["blank"] == 2 and st["decimal_sep"] == ","


def test_parse_numeric_formato_us_y_ambiguo():
    out, st = parse_numeric(pd.Series(["1,234.56", "2,000.00", "15.5"]))
    assert list(out) == [1234.56, 2000.0, 15.5] and st["decimal_sep"] == "."
    out, st = parse_numeric(pd.Series(["1.500", "2.000"]))
    assert list(out) == [1500.0, 2000.0] and st["ambiguous"] is True


def test_parse_numeric_no_acepta_fechas_ni_texto_con_letras():
    out, st = parse_numeric(pd.Series(["2024-01-05", "Pendiente 2", "3/4"]))
    assert out.isna().all() and st["unparsed"] == 3


def test_normalize_key_placas_y_ids():
    out = normalize_key(pd.Series(["abc-123 ", "ABC123", 1001.0, "T-1", None]), plate=True)
    assert out.iloc[0] == out.iloc[1] == "ABC123"
    assert out.iloc[2] == "1001" and out.iloc[4] is None


# ------------------------------------------------------------- semántica ---
def test_semantica_no_confunde_subcadenas():
    df = pd.DataFrame({
        "Validado": ["si", "no", "si"], "ID Viaje": [1001, 1002, 1003], "Costo Peajes": [50000, 60000, 55000],
        "Total litros": [100, 120, 90], "Total": [1_000_000, 1_100_000, 900_000], "Notas": ["a", "b", "c"],
        "Fecha salida": ["05/01/2026", "06/01/2026", "07/01/2026"],
    })
    res = GenesisDataUnderstanding().analyze_workbook({"H": _with_src(df)})["sheets_analysis"]["H"]
    mapped = {c: v["canonical"] for c, v in res["fields_mapping"].items()}
    assert mapped["ID Viaje"] == "TRIP_ID"
    assert mapped["Costo Peajes"] == "COST_TOLL"
    assert mapped["Total litros"] == "VOLUME_LTS"
    assert mapped["Fecha salida"] == "TRIP_DATE"
    assert "Validado" not in mapped
    amb = {a["original_column"]: a for a in res["ambiguities"]}
    assert amb["Total"]["possible_match"] == "REVENUE" and amb["Total"]["requires_decision"] is True  # genérico: confirmar
    assert amb["Notas"]["requires_decision"] is False  # desconocida: se ignora sola


def test_semantica_memoria_de_mapeos():
    df = _with_src(_trips())
    sig = sheet_signature([c for c in df.columns if not c.startswith("_src_")])
    memory = {sig: {"Viaje": "TRIP_ID", "Placa": "VEHICLE_ID", "Ruta": "ROUTE_NAME", "Flete": "REVENUE", "Km": "UNKNOWN"}}
    res = GenesisDataUnderstanding().analyze_workbook({"H": df}, memory_lookup=memory.get)["sheets_analysis"]["H"]
    assert res["from_memory"] and res["fields_mapping"]["Flete"]["source"] == "memoria"
    assert not any(a["requires_decision"] for a in res["ambiguities"])


# ------------------------------------------------------------ ingestión ---
def test_ingesta_csv_latin1_punto_y_coma_con_titulo():
    text = "Reporte de fletes enero\n\nViaje;Placa;Flete\nT1;ABC123;\"1.500.000,50\"\nT2;XYZ987;900.000\n"
    frames = read_workbook("fletes.csv", text.encode("cp1252"), SETTINGS)
    df = frames["Hoja1"]
    assert list(df.columns)[:3] == ["Viaje", "Placa", "Flete"] and len(df) == 2
    assert df["_src_row"].tolist() == [4, 5]  # trazabilidad: línea real del archivo
    assert parse_numeric(df["Flete"])[0].tolist() == [1_500_000.50, 900_000.0]


def test_ingesta_excel_con_titulos_y_hojas():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([["Informe operativo"], [None], ["Viaje", "Flete"], ["T1", 100], ["T2", 200]]).to_excel(
            xw, sheet_name="Enero", header=False, index=False)
    frames = read_workbook("op.xlsx", buf.getvalue(), SETTINGS)
    assert list(frames["Enero"].columns)[:2] == ["Viaje", "Flete"]
    assert frames["Enero"]["_src_row"].tolist() == [4, 5]


def test_ingesta_rechaza_archivos_invalidos():
    with pytest.raises(ApiError) as e:
        read_workbook("virus.exe", b"MZ", SETTINGS)
    assert e.value.code == "TIPO_NO_SOPORTADO"
    with pytest.raises(ApiError) as e:
        read_workbook("falso.xlsx", b"esto no es excel", SETTINGS)
    assert e.value.code == "ARCHIVO_INVALIDO"


# --------------------------------------------------------------- cruces ---
MAP_TRIPS = {"Viaje": "TRIP_ID", "Placa": "VEHICLE_ID", "Ruta": "ROUTE_NAME", "Flete": "REVENUE", "Km": "DISTANCE_KM"}


def test_cruce_agrega_combustible_y_no_multiplica_filas():
    trips = _trips(20)
    fuel = pd.DataFrame({  # 2 tanqueos por viaje
        "Viaje": [f"T{i:03d}" for i in range(1, 21)] * 2, "Combustible": [200_000] * 40})
    res = build_master({"Viajes": _with_src(trips), "Combustible": _with_src(fuel)},
                       {"Viajes": MAP_TRIPS, "Combustible": {"Viaje": "TRIP_ID", "Combustible": "COST_FUEL"}})
    assert len(res.master) == 20  # NO 40
    assert (res.master["COST_FUEL"] == 400_000).all()  # 2 x 200.000 sumados por viaje
    assert res.report["joins"][0]["pct_base_con_match"] == 100.0


def test_hojas_mensuales_se_apilan_no_se_cruzan():
    ene, feb = _trips(10), _trips(10)
    feb["Viaje"] = [f"F{i:03d}" for i in range(10)]
    res = build_master({"Enero": _with_src(ene), "Febrero": _with_src(feb)},
                       {"Enero": MAP_TRIPS, "Febrero": MAP_TRIPS})
    assert len(res.master) == 20 and set(res.master["_src_sheet"]) == {"Enero", "Febrero"}


def test_llave_por_placa_con_medidas_no_multiplica():
    trips = _trips(20)
    fuel = pd.DataFrame({"Placa": ["abc-123"] * 30 + ["xyz 987"] * 30, "Combustible": [100_000] * 60})
    res = build_master({"Viajes": _with_src(trips), "Combustible": _with_src(fuel)},
                       {"Viajes": MAP_TRIPS, "Combustible": {"Placa": "VEHICLE_ID", "Combustible": "COST_FUEL"}})
    assert len(res.master) == 20 and "COST_FUEL" not in res.master.columns
    assert len(res.secondary) == 1 and any("análisis por vehículo" in w for w in res.warnings)


def test_ids_de_distinto_tipo_cruzan():
    trips = _trips(5)
    trips["Viaje"] = [1, 2, 3, 4, 5]
    fuel = pd.DataFrame({"Viaje": ["1", "2", "3.0", "4", "5"], "Combustible": [1, 1, 1, 1, 1]})
    res = build_master({"V": _with_src(trips), "C": _with_src(fuel)},
                       {"V": MAP_TRIPS, "C": {"Viaje": "TRIP_ID", "Combustible": "COST_FUEL"}})
    assert res.master["COST_FUEL"].notna().all()


# ---------------------------------------------------------------- motor ---
def _pipeline(trips, fuel=None, cfg=None):
    dfs = {"Viajes": _with_src(trips)}
    mapping = {"Viajes": MAP_TRIPS}
    if fuel is not None:
        dfs["Combustible"] = _with_src(fuel)
        mapping["Combustible"] = {"Viaje": "TRIP_ID", "Combustible": "COST_FUEL"}
    return run_pipeline(dfs, mapping, cfg or {})


def test_motor_cifras_conocidas():
    trips = _trips(20)  # ingreso: 4 x (1.000.000, 1.010.000, 1.020.000, 1.030.000, 1.040.000) = 20.400.000
    trips.loc[3, "Flete"] = 300_000                          # viaje con pérdida (costo 500.000); antes valía 1.030.000
    dup = trips.iloc[[0]].copy()                             # copia exacta del primer viaje
    trips = pd.concat([trips, dup], ignore_index=True)
    fuel = pd.DataFrame({"Viaje": [f"T{i:03d}" for i in range(1, 21)], "Combustible": [500_000] * 20})
    out = _pipeline(trips, fuel)
    fin = out["financials"]
    assert out["estado"] in ("OK", "CON_RESERVAS")
    assert fin["duplicadosExactos"] == 1 and fin["filasNetas"] == 20
    assert fin["totalIngresos"] == 19_670_000  # 20.400.000 - 1.030.000 + 300.000; la copia exacta NO suma
    tipos = {f["tipo"]: f for f in out["findings"]}
    assert tipos["DUPLICADO_EXACTO"]["impacto"]["impacto_directo"] == 1_000_000  # ingreso del viaje copiado
    assert tipos["MARGEN_NEGATIVO"]["impacto"]["impacto_directo"] == 200_000      # 500.000 - 300.000
    assert tipos["MARGEN_NEGATIVO"]["casos"][0]["trip_id"] == "T004"              # trazabilidad
    assert tipos["MARGEN_NEGATIVO"]["casos"][0]["fila"] == 5                      # fila 5 del archivo original
    det = fin["dineroEnRiesgoDetalle"]
    assert fin["dineroEnRiesgo"] == det["sobrefacturacion_potencial"] + det["perdida_directa"] == 1_200_000
    assert "Costo de combustible" in fin["costosIncluidos"]
    assert any("No incluye" in w for w in out["advertencias"])                     # margen solo con combustible: se avisa


def test_motor_ingreso_ilegible_no_se_vuelve_cero():
    trips = _trips(20)
    trips["Flete"] = trips["Flete"].astype(object)
    trips.loc[0:3, "Flete"] = "N/A pendiente"                 # 4 de 20 = 20% ilegible -> aviso (se excluye de los cálculos)
    out = _pipeline(trips)
    codigos = {m["codigo"] for m in out["calidad"]["motivos"]}
    assert "LECTURA_NUMERICA" in codigos
    trips.loc[0:9, "Flete"] = "sin dato"                       # 50% ilegible -> bloqueada
    out = _pipeline(trips)
    assert out["estado"] == "BLOQUEADA" and out["financials"] is None


def test_motor_bloquea_si_no_hay_columnas_asignadas_sin_error_interno():
    out = run_pipeline({"Viajes": _with_src(_trips())}, {"Viajes": {c: "UNKNOWN" for c in ["Viaje", "Placa"]}}, {})
    assert out["estado"] == "BLOQUEADA" and out["calidad"]["motivos"][0]["codigo"] == "SIN_DATOS"


def test_motor_ids_vacios_no_se_cuentan_como_duplicados():
    trips = _trips(20)
    trips["Viaje"] = trips["Viaje"].astype(object)
    trips.loc[[2, 7, 11], "Viaje"] = None
    trips.loc[[2, 7, 11], "Flete"] = [1_001_000, 1_002_000, 1_003_000]  # filas distintas entre sí
    out = _pipeline(trips)
    assert out["financials"]["duplicadosExactos"] == 0


def test_atipicos_robustos_en_grupo_pequeno():
    x = pd.Series([100.0] * 9 + [1000.0])
    assert robust_z(x).abs().max() > 3.5  # con z clásico (std) este caso no se detectaría nunca
    trips = _trips(10)
    trips.loc[9, "Flete"] = 9_000_000
    out = _pipeline(trips)
    tipos = [f["tipo"] for f in out["findings"]]
    assert "OUTLIER_INGRESO" in tipos


def test_iva_configurable():
    trips = _trips(10)
    fuel = pd.DataFrame({"Viaje": [f"T{i:03d}" for i in range(1, 11)], "Combustible": [500_000] * 10})
    con = _pipeline(trips, fuel, {"revenue_includes_vat": 1, "vat_rate": 0.19})["financials"]
    sin = _pipeline(trips, fuel)["financials"]
    assert con["totalIngresos"] == pytest.approx(sin["totalIngresos"] / 1.19)
    assert con["ivaExcluido"] and not sin["ivaExcluido"]


def test_conciliacion_entre_fuentes_y_huerfanos():
    ops = _trips(10)
    fact = pd.DataFrame({"Viaje": [f"T{i:03d}" for i in range(1, 11)] + ["T999"],
                         "Flete": [1_000_000 + 10_000 * (i % 5) for i in range(10)] + [500_000]})
    fact.loc[2, "Flete"] = 1_500_000                     # difiere de la operación
    dfs = {"Operacion": _with_src(ops), "Facturacion": _with_src(fact)}
    mapping = {"Operacion": MAP_TRIPS, "Facturacion": {"Viaje": "TRIP_ID", "Flete": "REVENUE"}}
    out = run_pipeline(dfs, mapping, {})
    tipos = {f["tipo"]: f for f in out["findings"]}
    assert "CONCILIACION" in tipos and "REGISTRO_SIN_VIAJE" in tipos
    assert tipos["REGISTRO_SIN_VIAJE"]["casos"][0]["trip_id"] == "T999"


def test_nivel_vehiculo_cuando_costos_no_traen_viaje():
    trips = _trips(20)
    fuel = pd.DataFrame({"Placa": ["ABC123", "XYZ987"], "Combustible": [30_000_000, 2_000_000]})  # ABC123 pierde
    dfs = {"Viajes": _with_src(trips), "Costos": _with_src(fuel)}
    mapping = {"Viajes": MAP_TRIPS, "Costos": {"Placa": "VEHICLE_ID", "Combustible": "COST_FUEL"}}
    out = run_pipeline(dfs, mapping, {})
    tipos = {f["tipo"]: f for f in out["findings"]}
    assert "MARGEN_VEHICULO_NEGATIVO" in tipos and out["financials"]["nivelVehiculo"] is True
    assert tipos["MARGEN_VEHICULO_NEGATIVO"]["casos"][0]["vehiculo"] == "ABC123"


def test_rendimiento_de_combustible():
    n = 30
    trips = _trips(n)
    fuel = pd.DataFrame({"Viaje": [f"T{i:03d}" for i in range(1, n + 1)], "Galones": [100.0] * n, "Costo": [1_000_000] * n})
    fuel.loc[5, "Galones"] = 400.0                         # 2,5 km/gal contra 10 km/gal habituales
    dfs = {"V": _with_src(trips), "C": _with_src(fuel)}
    mapping = {"V": MAP_TRIPS, "C": {"Viaje": "TRIP_ID", "Galones": "VOLUME_GAL", "Costo": "COST_FUEL"}}
    out = run_pipeline(dfs, mapping, {})
    f = {x["tipo"]: x for x in out["findings"]}["RENDIMIENTO_COMBUSTIBLE"]
    assert f["casos"][0]["trip_id"] == "T006" and f["impacto"]["impacto_directo"] > 0
    assert out["financials"]["rendimientoFlotaKmGal"] == pytest.approx(10.0)
