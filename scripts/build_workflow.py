#!/usr/bin/env python3
"""Construye el monitor de errores y los workflows de ejemplo que fallan.

    python scripts/build_workflow.py

Genera:
  workflows/monitor_demo.json        Error Trigger + panel web, SIN credenciales
  workflows/monitor_produccion.json  Error Trigger → clasificar → Telegram, Gmail,
                                     registro en Sheets y resumen diario
  workflows/monitor_reintentos.json  sub-workflow que espera 1 minuto y reintenta
                                     la ejecución por la API de n8n
  workflows/ejemplos/*.json          tres workflows que fallan a propósito y
                                     tienen a la demo como Error Workflow
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from diseno_canvas import acomodar  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "workflows" / "src" / "monitor.js"
OUT = ROOT / "workflows"
EJEMPLOS = OUT / "ejemplos"
ID_DEMO = "monitordemo"
ID_REINTENTOS = "monitorreintentos"
ID_HOJA = {"__rl": True, "value": "REEMPLAZAR_ID_HOJA", "mode": "id"}


def codigo(modo: str) -> str:
    js = JS.read_text(encoding="utf-8")
    if js.count("'__MODO__'") != 1:
        raise SystemExit(f"se esperaba una sola vez '__MODO__' en {JS}")
    return js.replace("'__MODO__'", f"'{modo}'")


def _node(nid, name, ntype, tv, pos, params, extra=None):
    n = {"parameters": params, "id": nid, "name": name, "type": ntype,
         "typeVersion": tv, "position": pos}
    if extra:
        n.update(extra)
    return n


def _link(*destinos):
    return [{"node": d, "type": "main", "index": 0} for d in destinos]


def _webhook(nid, nombre, metodo, ruta, pos):
    return _node(nid, nombre, "n8n-nodes-base.webhook", 2, pos,
                 {"httpMethod": metodo, "path": ruta, "responseMode": "responseNode",
                  "options": {"allowedOrigins": "*"}},
                 {"webhookId": ruta.replace("/", "-")})


def _responder(nid, nombre, pos, html=False):
    params = {"respondWith": "firstIncomingItem", "options": {}}
    if html:
        params = {"respondWith": "text", "responseBody": "={{ $json.html }}",
                  "options": {"responseHeaders": {"entries": [
                      {"name": "Content-Type", "value": "text/html; charset=utf-8"}]}}}
    return _node(nid, nombre, "n8n-nodes-base.respondToWebhook", 1.1, pos, params)


def _flujo(wid, nombre, nodes, connections, extra_settings=None):
    settings = {"executionOrder": "v1"}
    settings.update(extra_settings or {})
    return {"id": wid, "name": nombre, "nodes": nodes, "connections": connections,
            "settings": settings, "pinData": {}, "meta": {"instanceId": wid}, "tags": []}


@acomodar
def build_demo() -> dict:
    reiniciar = ("const m = $getWorkflowStaticData('global');\n"
                 "for (const k of Object.keys(m)) delete m[k];\n"
                 "return [{ json: { reiniciado: true } }];\n")
    nodes = [
        _node("err-1", "Error en otro workflow", "n8n-nodes-base.errorTrigger", 1, [0, 0], {},
              {"notes": "Se dispara cuando falla cualquier workflow que tenga a este como "
                        "Error Workflow (Settings → Error Workflow)."}),
        _node("code-1", "Clasificar error", "n8n-nodes-base.code", 2, [240, 0],
              {"jsCode": codigo("error")}),
        _webhook("wh-1", "Webhook · Simular error", "POST", "monitor-demo/error", [0, 200]),
        _node("code-2", "Clasificar error simulado", "n8n-nodes-base.code", 2, [240, 200],
              {"jsCode": codigo("error")}),
        _responder("resp-1", "Responder decisión", [480, 200]),
        _webhook("wh-2", "Webhook · Panel", "GET", "monitor-demo/panel", [0, 400]),
        _node("code-3", "Armar panel", "n8n-nodes-base.code", 2, [240, 400],
              {"jsCode": codigo("panel")}),
        _responder("resp-2", "Mostrar panel", [480, 400], html=True),
        _webhook("wh-3", "Webhook · Reiniciar", "POST", "monitor-demo/reiniciar", [0, 600]),
        _node("code-4", "Borrar memoria", "n8n-nodes-base.code", 2, [240, 600],
              {"jsCode": reiniciar}),
        _responder("resp-3", "Responder reinicio", [480, 600]),
    ]
    connections = {
        "Error en otro workflow": {"main": [_link("Clasificar error")]},
        "Webhook · Simular error": {"main": [_link("Clasificar error simulado")]},
        "Clasificar error simulado": {"main": [_link("Responder decisión")]},
        "Webhook · Panel": {"main": [_link("Armar panel")]},
        "Armar panel": {"main": [_link("Mostrar panel")]},
        "Webhook · Reiniciar": {"main": [_link("Borrar memoria")]},
        "Borrar memoria": {"main": [_link("Responder reinicio")]},
    }
    return _flujo(ID_DEMO, "Monitor de errores · DEMO sin credenciales", nodes, connections)


def _regla(valor, cid):
    return {"conditions": {
                "options": {"caseSensitive": True, "leftValue": "",
                            "typeValidation": "strict", "version": 2},
                "conditions": [{"id": cid, "leftValue": "={{ $json.accion }}", "rightValue": valor,
                                "operator": {"type": "string", "operation": "equals"}}],
                "combinator": "and"},
            "renameOutput": True, "outputKey": valor}


def _telegram(nid, nombre, texto, pos):
    return _node(nid, nombre, "n8n-nodes-base.telegram", 1.2, pos,
                 {"chatId": "REEMPLAZAR_CHAT_EQUIPO", "text": texto,
                  "additionalFields": {"appendAttribution": False}})


@acomodar
def build_prod() -> dict:
    nodes = [
        _node("err-1", "Error en otro workflow", "n8n-nodes-base.errorTrigger", 1, [0, 300], {}),
        _node("code-1", "Clasificar error", "n8n-nodes-base.code", 2, [220, 300],
              {"jsCode": codigo("error")}),
        _node("if-r", "¿Reintentar?", "n8n-nodes-base.if", 2.2, [440, 140],
              {"conditions": {
                  "options": {"caseSensitive": True, "leftValue": "",
                              "typeValidation": "strict", "version": 2},
                  "conditions": [{"id": "r1", "leftValue": "={{ $json.reintentar }}",
                                  "rightValue": "",
                                  "operator": {"type": "boolean", "operation": "true",
                                               "singleValue": True}}],
                  "combinator": "and"},
               "options": {}}),
        _node("if-a", "¿Avisar?", "n8n-nodes-base.if", 2.2, [440, 360],
              {"conditions": {
                  "options": {"caseSensitive": True, "leftValue": "",
                              "typeValidation": "strict", "version": 2},
                  "conditions": [{"id": "v1", "leftValue": "={{ $json.aviso }}",
                                  "rightValue": "",
                                  "operator": {"type": "string", "operation": "notEmpty",
                                               "singleValue": True}}],
                  "combinator": "and"},
               "options": {}},
              {"notes": "Reintentar y avisar son decisiones independientes: un error pasajero "
                        "que se repite mucho se reintenta y además se escala."}),
        _node("exec-1", "Pedir reintento (sin esperar)", "n8n-nodes-base.executeWorkflow", 1.1,
              [680, 80],
              {"source": "database",
               "workflowId": {"__rl": True, "value": ID_REINTENTOS, "mode": "id"},
               "mode": "each", "options": {"waitForSubWorkflow": False}},
              {"notes": "Sin esperar a propósito: si el monitor esperara, el reintento "
                        "fallaría antes de que el monitor guarde su memoria y el contador "
                        "de reintentos nunca subiría (ver GUIA.md)."}),
        _telegram("tg-al", "Telegram · Alerta", "={{ $json.mensaje }}", [680, 260]),
        _node("if-grave", "¿Alta o crítica?", "n8n-nodes-base.if", 2.2, [680, 420],
              {"conditions": {
                  "options": {"caseSensitive": True, "leftValue": "",
                              "typeValidation": "strict", "version": 2},
                  "conditions": [
                      {"id": "g1", "leftValue": "={{ $json.severidad }}", "rightValue": "alta",
                       "operator": {"type": "string", "operation": "equals"}},
                      {"id": "g2", "leftValue": "={{ $json.severidad }}", "rightValue": "critica",
                       "operator": {"type": "string", "operation": "equals"}}],
                  "combinator": "or"},
               "options": {}}),
        _node("gm-1", "Gmail · Aviso al responsable", "n8n-nodes-base.gmail", 2.1, [900, 400],
              {"sendTo": "REEMPLAZAR_CORREO_RESPONSABLE",
               "subject": "=[{{ $json.severidad.toUpperCase() }}] Falló {{ $json.evento.workflow }}",
               "emailType": "text", "message": "={{ $json.mensaje }}",
               "options": {"appendAttribution": False}}),
        _node("set-1", "Fila de registro", "n8n-nodes-base.set", 3.4, [440, 700],
              {"mode": "raw", "jsonOutput": "={{ JSON.stringify($json.fila) }}", "options": {}}),
        _node("gs-1", "Sheets · Registro de errores", "n8n-nodes-base.googleSheets", 4.5,
              [680, 700],
              {"operation": "append", "documentId": ID_HOJA,
               "sheetName": {"__rl": True, "value": "Errores", "mode": "name"},
               "columns": {"mappingMode": "autoMapInputData", "value": {}},
               "options": {}}),
        # ── resumen diario ──
        _node("cron-1", "Todos los días 18:00", "n8n-nodes-base.scheduleTrigger", 1.2,
              [0, 940],
              {"rule": {"interval": [{"field": "cronExpression", "expression": "0 18 * * *"}]}}),
        _node("gs-2", "Sheets · Leer registro", "n8n-nodes-base.googleSheets", 4.5, [220, 940],
              {"documentId": ID_HOJA,
               "sheetName": {"__rl": True, "value": "Errores", "mode": "name"},
               "options": {}},
              {"executeOnce": True, "alwaysOutputData": True}),
        _node("code-2", "Resumen del día", "n8n-nodes-base.code", 2, [440, 940],
              {"jsCode": codigo("resumen")}),
        _telegram("tg-res", "Telegram · Resumen del día", "={{ $json.texto }}", [680, 940]),
    ]
    connections = {
        "Error en otro workflow": {"main": [_link("Clasificar error")]},
        "Clasificar error": {"main": [_link("¿Reintentar?", "¿Avisar?", "Fila de registro")]},
        "¿Reintentar?": {"main": [_link("Pedir reintento (sin esperar)"), []]},
        "¿Avisar?": {"main": [_link("Telegram · Alerta", "¿Alta o crítica?"), []]},
        "¿Alta o crítica?": {"main": [_link("Gmail · Aviso al responsable"), []]},
        "Fila de registro": {"main": [_link("Sheets · Registro de errores")]},
        "Todos los días 18:00": {"main": [_link("Sheets · Leer registro")]},
        "Sheets · Leer registro": {"main": [_link("Resumen del día")]},
        "Resumen del día": {"main": [_link("Telegram · Resumen del día")]},
    }
    return _flujo("monitorprod", "Monitor de errores · producción", nodes, connections)


@acomodar
def build_reintentos() -> dict:
    """Sub-workflow: espera, reintenta por la API y avisa si la API falla."""
    nodes = [
        _node("in-1", "Pedido de reintento", "n8n-nodes-base.executeWorkflowTrigger", 1.1,
              [0, 0], {"inputSource": "passthrough"}),
        _node("wait-1", "Esperar 1 minuto", "n8n-nodes-base.wait", 1.1, [220, 0],
              {"amount": 1, "unit": "minutes"},
              {"notes": "Reintentar al instante un timeout suele volver a fallar."}),
        _node("http-1", "n8n · Reintentar ejecución", "n8n-nodes-base.httpRequest", 4.2,
              [440, 0],
              {"method": "POST",
               "url": "=REEMPLAZAR_URL_N8N/api/v1/executions/{{ $json.evento.ejecucion_id }}/retry",
               "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
               "sendBody": True, "specifyBody": "json",
               "jsonBody": "={ \"loadWorkflow\": false }", "options": {}},
              {"onError": "continueErrorOutput",
               "notes": "API pública de n8n. Credencial Header Auth: X-N8N-API-KEY."}),
        _telegram("tg-rf", "Telegram · No se pudo reintentar",
                  "=No se pudo reintentar la ejecución {{ $('Pedido de reintento').item.json.evento.ejecucion_id }} "
                  "de {{ $('Pedido de reintento').item.json.evento.workflow }}.\n"
                  "Error original: {{ $('Pedido de reintento').item.json.evento.mensaje }}\n"
                  "{{ $('Pedido de reintento').item.json.evento.url }}", [660, 80]),
    ]
    connections = {
        "Pedido de reintento": {"main": [_link("Esperar 1 minuto")]},
        "Esperar 1 minuto": {"main": [_link("n8n · Reintentar ejecución")]},
        "n8n · Reintentar ejecución": {"main": [[], _link("Telegram · No se pudo reintentar")]},
    }
    return _flujo(ID_REINTENTOS, "Monitor de errores · reintentos", nodes, connections)


def build_ejemplos() -> dict:
    """Tres workflows que fallan de verdad, cada uno con un tipo de error."""
    def ejemplo(wid, nombre, ruta, nodo_falla):
        nodes = [_node(f"{wid}-wh", "Webhook", "n8n-nodes-base.webhook", 2, [0, 0],
                       {"httpMethod": "POST", "path": ruta, "responseMode": "onReceived",
                        "options": {}}, {"webhookId": ruta}),
                 nodo_falla]
        return _flujo(wid, nombre, nodes, {"Webhook": {"main": [_link(nodo_falla["name"])]}},
                      {"errorWorkflow": ID_DEMO})

    return {
        "falla_servicio_caido.json": ejemplo(
            "ejemplopagos", "Cobros · sincronizar pagos", "falla-pagos",
            _node("f1", "API de pagos", "n8n-nodes-base.httpRequest", 4.2, [240, 0],
                  {"url": "http://10.255.255.1:81/api/pagos", "options": {"timeout": 2000}},
                  {"notes": "Dirección que no responde: provoca un timeout real."})),
        "falla_dato_inesperado.json": ejemplo(
            "ejemplofacturas", "Facturas · leer PDF", "falla-datos",
            _node("f2", "Leer total", "n8n-nodes-base.code", 2, [240, 0],
                  {"jsCode": "// Espera {factura: {total}} y no valida la entrada\n"
                              "return [{ json: { total: $input.first().json.body.factura.total } }];\n"})),
        "falla_credencial.json": ejemplo(
            "ejemplocrm", "CRM · exportar contactos", "falla-credencial",
            _node("f3", "API del CRM", "n8n-nodes-base.httpRequest", 4.2, [240, 0],
                  {"url": "http://localhost:5678/api/v1/workflows", "options": {}},
                  {"notes": "Llama a la API de n8n sin API key: responde 401 de verdad."})),
    }


def main():
    EJEMPLOS.mkdir(parents=True, exist_ok=True)
    salidas = {OUT / "monitor_demo.json": build_demo(),
               OUT / "monitor_produccion.json": build_prod(),
               OUT / "monitor_reintentos.json": build_reintentos()}
    salidas.update({EJEMPLOS / k: v for k, v in build_ejemplos().items()})
    for ruta, wf in salidas.items():
        ruta.write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
        print(f"ok  {ruta.relative_to(ROOT)} — {len(wf['nodes'])} nodos")


if __name__ == "__main__":
    main()
