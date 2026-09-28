"""Paridad: el nodo Code de n8n (JS) y la lógica Python deben decidir igual.

Requiere Node.js; si no está instalado, se salta.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.monitor import (decidir, fila_registro, leer_evento, limpiar,  # noqa: E402
                         resumen_dia)

RUNNER = ROOT / "tests" / "correr_nodo_js.mjs"
LIMA = timezone(timedelta(hours=-5))
DIA = json.loads((ROOT / "data" / "avisos_dia.json").read_text(encoding="utf-8"))
REALES = json.loads((ROOT / "data" / "avisos_reales.json").read_text(encoding="utf-8"))

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="Node.js no está instalado")


def _con_fecha(avisos):
    return [dict(a, fecha=datetime.fromtimestamp(a["llega"], LIMA).isoformat()) for a in avisos]


def _python(avisos):
    estado: dict = {}
    resultados, filas = [], []
    for a in avisos:
        r = decidir(leer_evento(a["aviso"]), estado, a["llega"])
        resultados.append(r)
        filas.append(fila_registro(r, a["fecha"]))
    return resultados, filas


def _js(avisos, tmp_path, textos=()):
    ruta = tmp_path / "pedido.json"
    ruta.write_text(json.dumps({"avisos": avisos, "dia": "2026-09-28", "textos": list(textos)},
                               ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(["node", str(RUNNER), str(ruta)],
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        pytest.fail(f"el nodo JS falló:\n{proc.stderr}")
    return json.loads(proc.stdout)


def test_el_dia_completo_da_lo_mismo(tmp_path):
    avisos = _con_fecha(DIA)
    resultados, filas = _python(avisos)
    js = _js(avisos, tmp_path)
    for i, (p, j) in enumerate(zip(resultados, js["resultados"])):
        assert p == j, f"divergen en el aviso {i}\n python={p}\n js    ={j}"
    assert js["filas"] == filas
    assert js["resumen"] == resumen_dia(filas, "2026-09-28")


def test_avisos_reales_de_n8n_y_raros(tmp_path):
    raros = [
        {"workflow": {"id": "x", "name": "Sin ejecución"}},
        {"execution": {"id": 0, "error": {"message": None}}, "workflow": {}},
        {"body": {"workflow": {"id": "w", "name": "Simulado"},
                  "execution": {"id": "9", "error": {"message": "Ñandú 😀 falló 'x' 12345",
                                                     "messages": ["uno", 2]}}}},
        {"execution": {"id": "10", "error": {"message": "x" * 400 + "😀" * 10,
                                             "node": ["no es objeto"]}},
         "workflow": {"id": "w2", "name": "Largo"}},
    ]
    avisos = _con_fecha([{"llega": 1_790_600_400 + i * 60, "aviso": a}
                         for i, a in enumerate(REALES + raros)])
    resultados, _ = _python(avisos)
    assert _js(avisos, tmp_path)["resultados"] == resultados


def test_limpieza_de_secretos_igual(tmp_path):
    textos = ["Authorization: Bearer abc.def-123", "https://user:pa55@host.com/x",
              "api_key=XYZ&x=1", 'password: "hola mundo"', "TOKEN=abc123 y Clave: 999",
              "sin secretos aquí", "access_token\":\"eyJ.abc\""]
    js = _js([], tmp_path, textos)
    assert js["limpios"] == [limpiar(t) for t in textos]
