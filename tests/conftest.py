"""Entorno aislado para las pruebas: base SQLite temporal y claves de prueba.

Debe ejecutarse ANTES de importar app.main (por eso vive en conftest.py).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_tmp = tempfile.mkdtemp(prefix="genesis_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ADMIN_API_KEY"] = "admin-test-key"
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
