"""Pruebas de integración de la API (requieren: pip install -r requirements.txt httpx pytest)."""
from __future__ import annotations

import io

import pandas as pd
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ADMIN = {"X-Admin-Key": "admin-test-key"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _new_tenant(client, tenant_id: str) -> dict:
    r = client.post("/api/v1/admin/tenants", json={"tenant_id": tenant_id, "name": f"Empresa {tenant_id}"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _workbook() -> bytes:
    n = 20
    trips = pd.DataFrame({
        "Viaje": [f"T{i:03d}" for i in range(1, n + 1)], "Placa": ["ABC123" if i % 2 else "XYZ987" for i in range(n)],
        "Ruta": ["BAQ-BOG"] * n, "Flete": [1_000_000 + 10_000 * (i % 5) for i in range(n)], "Km": [1000] * n})
    trips.loc[3, "Flete"] = 300_000
    fuel = pd.DataFrame({"Viaje": [f"T{i:03d}" for i in range(1, n + 1)], "Combustible": [500_000] * n})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        trips.to_excel(xw, sheet_name="Viajes", index=False)
        fuel.to_excel(xw, sheet_name="Combustible", index=False)
    return buf.getvalue()


def _mapping_from(analysis: dict) -> dict:
    out = {}
    for sheet, a in analysis["sheets_analysis"].items():
        m = {c: v["canonical"] for c, v in a["fields_mapping"].items()}
        for amb in a["ambiguities"]:
            m[amb["original_column"]] = amb["possible_match"] if amb["requires_decision"] else "UNKNOWN"
        out[sheet] = m
    return out


def test_health_y_autenticacion(client):
    assert client.get("/health").json()["status"] == "healthy"
    r = client.get("/api/v1/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "NO_AUTENTICADO"
    assert client.get("/api/v1/me", headers={"X-API-Key": "gk_falsa"}).status_code == 401


def test_admin_requiere_clave(client):
    r = client.post("/api/v1/admin/tenants", json={"tenant_id": "acme", "name": "Acme"})
    assert r.status_code == 403
    r = client.post("/api/v1/admin/tenants", json={"tenant_id": "MAL ID", "name": "x"}, headers=ADMIN)
    assert r.status_code == 422


def test_flujo_completo_y_aislamiento_entre_clientes(client):
    h1, h2 = _new_tenant(client, "cliente-uno"), _new_tenant(client, "cliente-dos")
    files = {"file": ("op.xlsx", _workbook(), "application/octet-stream")}

    r = client.post("/api/v1/data-understanding", files=files, headers=h1)
    assert r.status_code == 200, r.text
    analysis = r.json()
    mapping = _mapping_from(analysis)

    import json as _json
    r = client.post("/api/v1/audits", files={"file": ("op.xlsx", _workbook(), "application/octet-stream")},
                    data={"mapping": _json.dumps(mapping)}, headers=h1)
    assert r.status_code == 200, r.text
    audit = r.json()
    assert audit["estado"] in ("OK", "CON_RESERVAS") and audit["reutilizado"] is False
    tipos = {f["tipo"] for f in audit["findings"]}
    assert "MARGEN_NEGATIVO" in tipos and len(audit["tasks"]) == len(audit["findings"])

    # Mismo archivo + mismo mapeo + misma configuración -> se reutiliza, no se duplican tareas
    r2 = client.post("/api/v1/audits", files={"file": ("op.xlsx", _workbook(), "application/octet-stream")},
                     data={"mapping": _json.dumps(mapping)}, headers=h1)
    assert r2.json()["reutilizado"] is True and r2.json()["id"] == audit["id"]

    # Memoria de mapeos: el siguiente análisis ya no pide decisiones
    again = client.post("/api/v1/data-understanding", files={"file": ("op.xlsx", _workbook(), "application/octet-stream")}, headers=h1).json()
    assert all(a["from_memory"] for a in again["sheets_analysis"].values())

    # Exportación a Excel
    x = client.get(f"/api/v1/audits/{audit['id']}/export", headers=h1)
    assert x.status_code == 200 and x.content[:2] == b"PK"

    # Tareas
    tasks = client.get("/api/v1/tasks", headers=h1).json()
    assert tasks["total"] == len(audit["tasks"])
    t = client.patch(f"/api/v1/tasks/{tasks['items'][0]['id']}", json={"status": "EN_PROCESO", "comment": "Asignado"}, headers=h1)
    assert t.status_code == 200 and t.json()["status"] == "EN_PROCESO"
    assert client.patch(f"/api/v1/tasks/{tasks['items'][0]['id']}", json={"status": "XX"}, headers=h1).status_code == 422

    # Aislamiento: el cliente dos NO puede ver, exportar, borrar ni editar lo del cliente uno
    assert client.get(f"/api/v1/audits/{audit['id']}", headers=h2).status_code == 404
    assert client.get(f"/api/v1/audits/{audit['id']}/export", headers=h2).status_code == 404
    assert client.delete(f"/api/v1/audits/{audit['id']}", headers=h2).status_code == 404
    assert client.patch(f"/api/v1/tasks/{tasks['items'][0]['id']}", json={"status": "RESUELTA"}, headers=h2).status_code == 404
    assert client.get("/api/v1/tasks", headers=h2).json()["total"] == 0

    # Supresión
    assert client.delete(f"/api/v1/audits/{audit['id']}", headers=h1).json() == {"deleted": audit["id"]}
    assert client.get(f"/api/v1/audits/{audit['id']}", headers=h1).status_code == 404


def test_archivo_no_soportado_y_mapeo_invalido(client):
    h = _new_tenant(client, "cliente-tres")
    r = client.post("/api/v1/data-understanding", files={"file": ("virus.exe", b"MZ", "application/octet-stream")}, headers=h)
    assert r.status_code == 415 and r.json()["error"]["code"] == "TIPO_NO_SOPORTADO"
    r = client.post("/api/v1/audits", files={"file": ("op.xlsx", _workbook(), "application/octet-stream")},
                    data={"mapping": "no-es-json"}, headers=h)
    assert r.status_code == 400
    r = client.post("/api/v1/audits", files={"file": ("op.xlsx", _workbook(), "application/octet-stream")},
                    data={"mapping": '{"Viajes": {"Flete": "CAMPO_INVENTADO"}}'}, headers=h)
    assert r.status_code == 422


def test_todo_ignorado_devuelve_bloqueada_y_no_error_500(client):
    h = _new_tenant(client, "cliente-cuatro")
    import json as _json
    mapping = {"Viajes": {"Viaje": "UNKNOWN", "Flete": "UNKNOWN"}}
    r = client.post("/api/v1/audits", files={"file": ("op.xlsx", _workbook(), "application/octet-stream")},
                    data={"mapping": _json.dumps(mapping)}, headers=h)
    assert r.status_code == 200 and r.json()["estado"] == "BLOQUEADA" and r.json()["findings"] == []
