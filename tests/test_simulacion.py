"""El día de avisos debe reducir el ruido sin perder ningún problema."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from simular_monitor import simular  # noqa: E402


def test_menos_de_un_mensaje_cada_cinco_errores():
    resultados, _ = simular()
    mensajes = sum(1 for r in resultados if r["aviso"])
    assert mensajes * 5 <= len(resultados)


def test_cada_workflow_con_errores_sin_resolver_genera_al_menos_un_aviso():
    """Reducir ruido no puede significar esconder problemas."""
    resultados, _ = simular()
    con_error = {r["evento"]["workflow"] for r in resultados if not r["reintentar"]}
    avisados = {r["evento"]["workflow"] for r in resultados if r["aviso"]}
    assert con_error <= avisados


def test_ningun_mensaje_lleva_un_token():
    resultados, filas = simular()
    assert all("eyJhbGciOiJIUzI1NiJ9" not in r["mensaje"] for r in resultados)
    assert all("eyJhbGciOiJIUzI1NiJ9" not in str(f) for f in filas)
