#!/usr/bin/env python3
"""Genera un día de avisos de error con la forma exacta que entrega n8n.

    python scripts/generar_avisos.py

Parte de los avisos reales capturados del Error Trigger de n8n 2.40
(data/avisos_reales.json) y arma data/avisos_dia.json: 60 avisos de 6
workflows a lo largo de un día, cada uno con el segundo en que llega.

Incluye lo que hace ruido en un canal de alertas real: una API que empieza a
limitar peticiones (429) durante 20 minutos, un token vencido que hace fallar
el mismo workflow cada 10 minutos, un error de datos que se repite, un
disparador que falla y un mensaje de error que trae un token a la vista.
Semilla fija: siempre genera exactamente lo mismo.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REALES = ROOT / "data" / "avisos_reales.json"
SALIDA = ROOT / "data" / "avisos_dia.json"
INICIO = 1_790_600_400            # 2026-09-28 08:00 (hora de Lima) en segundos


def aviso(base: dict, wid: str, nombre: str, nodo: str, mensaje: str,
          codigo: str = "", ejecucion: int = 0, reintento_de: int = 0, modo: str = "webhook",
          descripcion: str = "") -> dict:
    a = copy.deepcopy(base)
    a["workflow"] = {"id": wid, "name": nombre}
    ex = a["execution"]
    ex["id"] = str(ejecucion)
    ex["url"] = f"https://n8n.empresa.pe/workflow/{wid}/executions/{ejecucion}"
    ex["lastNodeExecuted"] = nodo
    ex["mode"] = modo
    if reintento_de:
        ex["retryOf"] = str(reintento_de)
    err = ex["error"]
    err["message"] = mensaje
    err["description"] = descripcion or None
    if codigo:
        err["httpCode"] = codigo
    else:
        err.pop("httpCode", None)
    if isinstance(err.get("node"), dict):
        err["node"]["name"] = nodo
    return a


def main():
    reales = json.loads(REALES.read_text(encoding="utf-8"))
    base_code = next(a for a in reales if a["execution"]["error"]["name"] == "WrappedExecutionError")
    base_http = next(a for a in reales if a["execution"]["error"]["name"] == "NodeApiError")
    rng = random.Random(3)
    eventos: list[tuple[int, dict]] = []
    ejecucion = 5000

    def agregar(t, a):
        eventos.append((t, a))

    # 1. Una API de envíos limita peticiones (429) entre las 10:00 y las 10:20
    for i in range(14):
        ejecucion += 1
        agregar(INICIO + 7200 + i * 85 + rng.randint(0, 30),
                aviso(base_http, "wf-envios", "Envíos · cotizar courier", "API del courier",
                      "Too many requests - please slow down", "429", ejecucion))

    # 2. Token vencido: el CRM falla cada 10 minutos desde las 12:00
    for i in range(12):
        ejecucion += 1
        agregar(INICIO + 14400 + i * 600,
                aviso(base_http, "ejemplocrm", "CRM · exportar contactos", "API del CRM",
                      "Authorization failed - please check your credentials", "401", ejecucion,
                      descripcion="Request failed with status code 401. Header Authorization: "
                                  "Bearer eyJhbGciOiJIUzI1NiJ9.demo.firma"))

    # 3. Un proveedor manda facturas con otro formato: el mismo error de datos
    for i in range(18):
        ejecucion += 1
        agregar(INICIO + 3600 + i * 1500 + rng.randint(0, 300),
                aviso(base_code, "ejemplofacturas", "Facturas · leer PDF", "Leer total",
                      f"Cannot read properties of undefined (reading 'total') [line {rng.choice([1, 2])}]",
                      ejecucion=ejecucion))

    # 4. El servicio de pagos se cae dos veces; los reintentos resuelven la primera
    for hora, fallas in ((9 * 3600, 1), (16 * 3600, 3)):
        original = ejecucion + 1
        previo = 0
        for k in range(fallas):
            ejecucion += 1
            agregar(INICIO + hora - 8 * 3600 + k * 65,
                    aviso(base_http, "ejemplopagos", "Cobros · sincronizar pagos", "API de pagos",
                          "The connection was aborted, perhaps the server is offline",
                          "ECONNABORTED", ejecucion, reintento_de=previo,
                          modo="webhook" if k == 0 else "retry"))
            previo = ejecucion
        del original

    # 5. Errores sueltos: configuración, uno manual y un disparador
    ejecucion += 1
    agregar(INICIO + 5400, aviso(base_code, "wf-stock", "Tienda · sincronizar stock",
                                 "Sheets · Actualizar inventario",
                                 "Sheet with name 'Inventario ' not found", ejecucion=ejecucion))
    ejecucion += 1
    agregar(INICIO + 6000, aviso(base_http, "wf-envios", "Envíos · cotizar courier",
                                 "API del courier", "Service unavailable", "503", ejecucion,
                                 modo="manual"))
    disparador = {"trigger": {"error": {"message": "Gmail: invalid_grant (token has expired)",
                                        "name": "NodeOperationError"}, "mode": "trigger"},
                  "workflow": {"id": "wf-facturas-mail", "name": "Facturas · recibir correo"}}
    agregar(INICIO + 30000, disparador)

    eventos.sort(key=lambda x: x[0])
    SALIDA.write_text(json.dumps([{"llega": t, "aviso": a} for t, a in eventos],
                                 ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8", newline="\n")
    print(f"{len(eventos)} avisos en {SALIDA.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
