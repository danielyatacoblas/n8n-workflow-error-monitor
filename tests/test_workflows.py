"""Revisa los workflows generados: conexiones, reintento asíncrono y secretos."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = {p.relative_to(ROOT / "workflows").as_posix(): json.loads(p.read_text(encoding="utf-8"))
             for p in (ROOT / "workflows").rglob("*.json")}


def _conexiones(wf):
    for origen, salidas in wf["connections"].items():
        for ramas in salidas.values():
            for i, rama in enumerate(ramas):
                for destino in rama:
                    yield origen, i, destino["node"]


def _nodo(wf, nombre):
    return next(n for n in wf["nodes"] if n["name"] == nombre)


def test_existen_todos_los_workflows():
    assert set(WORKFLOWS) == {
        "monitor_demo.json", "monitor_produccion.json", "monitor_reintentos.json",
        "ejemplos/falla_servicio_caido.json", "ejemplos/falla_dato_inesperado.json",
        "ejemplos/falla_credencial.json"}


@pytest.mark.parametrize("nombre", sorted(WORKFLOWS))
def test_conexiones_validas_y_sin_nodos_sueltos(nombre):
    wf = WORKFLOWS[nombre]
    nodos = {n["name"] for n in wf["nodes"]}
    tocados = set()
    for origen, _, destino in _conexiones(wf):
        assert origen in nodos and destino in nodos
        tocados.update((origen, destino))
    assert tocados == nodos


@pytest.mark.parametrize("nombre", sorted(WORKFLOWS))
def test_no_hay_credenciales(nombre):
    texto = json.dumps(WORKFLOWS[nombre])
    assert '"credentials"' not in texto
    assert not re.search(r"eyJ[A-Za-z0-9_-]{20,}", texto), "API key de n8n"


def test_los_ejemplos_usan_la_demo_como_error_workflow():
    for nombre, wf in WORKFLOWS.items():
        if nombre.startswith("ejemplos/"):
            assert wf["settings"]["errorWorkflow"] == "monitordemo"


def test_el_reintento_no_espera_al_sub_workflow():
    """Si esperara, el contador de reintentos no sube y el reintento entra en bucle."""
    prod = WORKFLOWS["monitor_produccion.json"]
    pedir = _nodo(prod, "Pedir reintento (sin esperar)")
    assert pedir["parameters"]["options"]["waitForSubWorkflow"] is False
    assert pedir["parameters"]["workflowId"]["value"] == WORKFLOWS["monitor_reintentos.json"]["id"]


def test_el_sub_workflow_espera_reintenta_y_avisa_si_la_api_falla():
    wf = WORKFLOWS["monitor_reintentos.json"]
    http = _nodo(wf, "n8n · Reintentar ejecución")
    assert http["parameters"]["url"].endswith("/api/v1/executions/{{ $json.evento.ejecucion_id }}/retry")
    assert http["onError"] == "continueErrorOutput"
    assert ("n8n · Reintentar ejecución", 1, "Telegram · No se pudo reintentar") in set(_conexiones(wf))
    assert _nodo(wf, "Esperar 1 minuto")["parameters"] == {"amount": 1, "unit": "minutes"}


def test_todo_error_queda_registrado():
    prod = WORKFLOWS["monitor_produccion.json"]
    destinos = {d for o, _, d in _conexiones(prod) if o == "Clasificar error"}
    assert "Fila de registro" in destinos


def test_resumen_diario_a_las_18():
    cron = _nodo(WORKFLOWS["monitor_produccion.json"], "Todos los días 18:00")
    assert cron["parameters"]["rule"]["interval"][0]["expression"] == "0 18 * * *"


def test_reintentar_y_avisar_son_decisiones_independientes():
    """Con un solo reparto por acción, los recordatorios no se enviaban y un error
    pasajero escalado dejaba de reintentarse."""
    prod = WORKFLOWS["monitor_produccion.json"]
    conexiones = set(_conexiones(prod))
    assert ("Clasificar error", 0, "¿Reintentar?") in conexiones
    assert ("Clasificar error", 0, "¿Avisar?") in conexiones
    assert ("¿Reintentar?", 0, "Pedir reintento (sin esperar)") in conexiones
    assert ("¿Avisar?", 0, "Telegram · Alerta") in conexiones
    assert "$json.aviso" in _nodo(prod, "¿Avisar?")["parameters"]["conditions"]["conditions"][0]["leftValue"]
