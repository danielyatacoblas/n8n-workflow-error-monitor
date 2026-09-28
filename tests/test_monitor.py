"""Tests del monitor: lectura, limpieza, clasificación, incidentes y reintentos."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.monitor import (MAX_REINTENTOS, PAUSA_CIERRE, RECORDATORIO,  # noqa: E402
                         UMBRAL_ESCALAR, clasificar, decidir, huella, leer_evento,
                         limpiar, resumen_dia)

REALES = json.loads((ROOT / "data" / "avisos_reales.json").read_text(encoding="utf-8"))
T0 = 1_790_600_400


def evento(mensaje="Cannot read properties of undefined (reading 'total')", codigo="",
           ejecucion="100", reintento_de="", modo="webhook", workflow="Facturas · leer PDF",
           nodo="Leer total"):
    return {"workflow_id": "wf", "workflow": workflow, "ejecucion_id": ejecucion,
            "url": f"https://n8n/x/{ejecucion}", "nodo": nodo, "tipo_error": "",
            "mensaje": mensaje, "detalle": "", "codigo": codigo,
            "reintento_de": reintento_de, "modo": modo}


def timeout(**kw):
    return evento(mensaje="The connection was aborted, perhaps the server is offline",
                  codigo="ECONNABORTED", **kw)


# ── lectura de avisos reales de n8n ────────────────────────────────────────

def test_lee_el_aviso_real_de_un_error_de_codigo():
    e = leer_evento(next(a for a in REALES if a["execution"]["error"]["name"] == "WrappedExecutionError"))
    assert e["nodo"] == "Leer total"                 # sale de lastNodeExecuted
    assert e["mensaje"].startswith("Cannot read properties of undefined")
    assert e["workflow"] == "Facturas · leer PDF"


def test_lee_el_aviso_real_de_un_timeout_http():
    e = leer_evento(next(a for a in REALES if a["execution"]["error"]["name"] == "NodeApiError"))
    assert (e["nodo"], e["codigo"]) == ("API de pagos", "ECONNABORTED")
    assert e["detalle"] == "timeout of 2000ms exceeded"


def test_no_copia_parametros_del_nodo_ni_el_stack():
    """El aviso real trae la URL, cabeceras y parámetros del nodo: pueden tener tokens."""
    e = leer_evento(REALES[1])
    texto = json.dumps(e)
    assert "rejectUnauthorized" not in texto and "stack" not in texto
    assert "10.255.255.1" not in texto


def test_error_en_el_disparador():
    e = leer_evento({"trigger": {"error": {"message": "invalid_grant"}, "mode": "trigger"},
                     "workflow": {"id": "w", "name": "Correo"}})
    assert (e["nodo"], e["ejecucion_id"], e["modo"]) == ("(disparador)", "", "trigger")


# ── limpieza ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texto,no_debe_quedar", [
    ("Header Authorization: Bearer eyJhbGciOi.xyz", "eyJhbGciOi"),
    ("https://admin:SuperClave1@api.empresa.pe/v1", "SuperClave1"),
    ("GET /x?api_key=ABC123&page=2", "ABC123"),
    ('{"password": "hola"}', "hola"),
    ("access_token=tok_live_99", "tok_live_99"),
])
def test_limpiar_tapa_secretos(texto, no_debe_quedar):
    limpio = limpiar(texto)
    assert no_debe_quedar not in limpio and "***" in limpio


def test_limpiar_no_toca_texto_normal():
    assert limpiar("Cannot read properties of undefined") == "Cannot read properties of undefined"


# ── clasificación ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("mensaje,codigo,categoria", [
    ("The connection was aborted", "ECONNABORTED", "transitorio"),
    ("Too many requests", "429", "transitorio"),
    ("Request failed", "503", "transitorio"),
    ("Authorization failed - please check your credentials", "401", "credenciales"),
    ("invalid_grant", "", "credenciales"),
    ("Cannot read properties of undefined (reading 'x')", "", "datos"),
    ("Sheet with name 'X' not found", "", "configuracion"),
    ("Algo raro pasó", "", "desconocido"),
])
def test_clasificar(mensaje, codigo, categoria):
    assert clasificar(evento(mensaje=mensaje, codigo=codigo)) == categoria


def test_un_numero_que_contiene_429_no_es_un_429():
    assert clasificar(evento(mensaje="Se procesaron 14290 filas y falló", codigo="")) == "desconocido"


def test_la_huella_ignora_ids_montos_y_urls():
    a = evento(mensaje="Pedido 123 falló en https://api.x.com/a?id=9 para 'Ana'")
    b = evento(mensaje="Pedido 987 falló en https://api.x.com/b para 'Luis'")
    assert huella(a) == huella(b)


# ── incidentes ─────────────────────────────────────────────────────────────

def test_primer_error_alerta_y_los_siguientes_se_cuentan():
    estado = {}
    acciones = [decidir(evento(ejecucion=str(i)), estado, T0 + i * 60)["accion"]
                for i in range(4)]
    assert acciones == ["alertar", "silenciar", "silenciar", "silenciar"]


def test_se_escala_al_llegar_al_umbral_y_una_sola_vez():
    estado = {}
    acciones = [decidir(evento(ejecucion=str(i)), estado, T0 + i * 60)["accion"]
                for i in range(UMBRAL_ESCALAR + 3)]
    assert acciones.count("escalar") == 1
    assert acciones[UMBRAL_ESCALAR - 1] == "escalar"


def test_escalar_sube_la_severidad():
    estado = {}
    r = [decidir(evento(ejecucion=str(i)), estado, T0 + i) for i in range(UMBRAL_ESCALAR)]
    assert (r[0]["severidad"], r[-1]["severidad"]) == ("media", "alta")


def test_workflow_critico_sube_un_nivel():
    r = decidir(evento(workflow="Cobros · sincronizar pagos"), {}, T0)
    assert r["severidad"] == "alta"


def test_tras_media_hora_de_pausa_es_un_incidente_nuevo():
    estado = {}
    decidir(evento(ejecucion="1"), estado, T0)
    decidir(evento(ejecucion="2"), estado, T0 + 60)
    r = decidir(evento(ejecucion="3"), estado, T0 + 60 + PAUSA_CIERRE)
    assert r["accion"] == "alertar"
    assert "el incidente anterior sumó 2 veces" in r["mensaje"]


def test_un_error_cronico_no_vuelve_a_alertar_cada_media_hora():
    """Falla cada 10 minutos todo el día: alerta, escalado y recordatorios, nada más."""
    estado = {}
    r = [decidir(evento(ejecucion=str(i)), estado, T0 + i * 600) for i in range(6 * 9)]
    avisos = [x["aviso"] for x in r if x["aviso"]]
    assert avisos == ["alerta", "escalado", "recordatorio", "recordatorio"]


def test_recordatorio_4_horas_despues_del_ultimo_aviso():
    estado = {}
    avisos = []
    for k in range(20):                          # falla cada 25 minutos, más de 8 horas
        t = T0 + k * 1500
        r = decidir(evento(ejecucion=str(k)), estado, t)
        if r["aviso"]:
            avisos.append((r["aviso"], t))
    assert [a for a, _ in avisos][:3] == ["alerta", "escalado", "recordatorio"]
    # cada recordatorio llega al menos 4 horas después del aviso anterior
    for (_, antes), (tipo, despues) in zip(avisos, avisos[1:]):
        if tipo == "recordatorio":
            assert despues - antes >= RECORDATORIO


def test_errores_distintos_son_incidentes_distintos():
    estado = {}
    a = decidir(evento(), estado, T0)
    b = decidir(evento(mensaje="Sheet not found"), estado, T0 + 1)
    assert a["accion"] == b["accion"] == "alertar"


# ── reintentos ─────────────────────────────────────────────────────────────

def test_error_pasajero_se_reintenta_sin_avisar():
    r = decidir(timeout(), {}, T0)
    assert (r["accion"], r["reintentar"], r["aviso"]) == ("reintentar", True, "")


def test_cadena_de_reintentos_se_detiene_y_avisa():
    """Así se comportó en n8n real: original, 2 reintentos y una alerta."""
    estado = {}
    r0 = decidir(timeout(ejecucion="131"), estado, T0)
    r1 = decidir(timeout(ejecucion="136", reintento_de="131", modo="retry"), estado, T0 + 65)
    r2 = decidir(timeout(ejecucion="139", reintento_de="136", modo="retry"), estado, T0 + 130)
    assert [r0["accion"], r1["accion"], r2["accion"]] == ["reintentar", "reintentar", "alertar"]
    assert r2["reintento"] == MAX_REINTENTOS
    assert "falló también tras 2 reintentos automáticos" in r2["mensaje"]


def test_reintento_de_origen_desconocido_no_se_reintenta():
    """La guarda contra el bucle infinito encontrado en las pruebas con n8n real."""
    r = decidir(timeout(ejecucion="53", reintento_de="51", modo="retry"), {}, T0)
    assert not r["reintentar"]
    assert r["accion"] == "alertar"
    assert "es un reintento y volvió a fallar" in r["mensaje"]


def test_errores_de_prueba_manual_no_se_reintentan():
    assert not decidir(timeout(modo="manual"), {}, T0)["reintentar"]


def test_un_pasajero_que_se_repite_mucho_se_reintenta_y_tambien_se_avisa():
    estado = {}
    r = [decidir(timeout(ejecucion=str(i)), estado, T0 + i * 60) for i in range(UMBRAL_ESCALAR)]
    assert all(x["reintentar"] for x in r)
    assert r[-1]["aviso"] == "escalado"
    assert "se sigue reintentando sola" in r[-1]["mensaje"]


def test_workflows_excluidos_no_se_reintentan():
    r = decidir(timeout(workflow="Newsletter · envío masivo"), {}, T0)
    assert not r["reintentar"] and r["accion"] == "alertar"


def test_errores_de_credenciales_nunca_se_reintentan():
    r = decidir(evento(mensaje="Unauthorized", codigo="401"), {}, T0)
    assert not r["reintentar"] and r["severidad"] == "alta"


# ── resumen del día ────────────────────────────────────────────────────────

def test_resumen_sin_errores():
    assert "ninguno" in resumen_dia([], "2026-09-28")


def test_resumen_cuenta_por_workflow_y_tipo():
    filas = [{"fecha": "2026-09-28T10:00", "workflow": "A", "categoria": "datos", "reintentado": "no"},
             {"fecha": "2026-09-28T11:00", "workflow": "A", "categoria": "datos", "reintentado": "no"},
             {"fecha": "2026-09-28T12:00", "workflow": "B", "categoria": "transitorio", "reintentado": "si"},
             {"fecha": "2026-09-27T12:00", "workflow": "C", "categoria": "datos", "reintentado": "no"}]
    texto = resumen_dia(filas, "2026-09-28")
    assert texto.splitlines() == ["Errores del 2026-09-28: 3 en 2 workflows.",
                                  "- A: 2", "- B: 1 (1 reintentados)",
                                  "Por tipo: datos 2, transitorio 1"]
