#!/usr/bin/env python3
"""Documenta un workflow de n8n a partir de su JSON.

    python scripts/documentar_workflow.py workflows/archivo.json [...]

Imprime en Markdown:
  - las técnicas de n8n que el workflow usa de verdad (se detectan en el JSON,
    no se escriben a mano, así la documentación no puede prometer algo que el
    workflow no hace);
  - una tabla nodo por nodo con su tipo y la configuración que importa.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIPOS = {
    "webhook": "Webhook", "telegramTrigger": "Telegram Trigger", "gmailTrigger": "Gmail Trigger",
    "scheduleTrigger": "Schedule Trigger", "errorTrigger": "Error Trigger",
    "executeWorkflowTrigger": "Execute Workflow Trigger", "code": "Code (JavaScript)",
    "switch": "Switch", "if": "If", "set": "Edit Fields (Set)", "noOp": "No Operation",
    "googleSheets": "Google Sheets", "telegram": "Telegram", "gmail": "Gmail",
    "httpRequest": "HTTP Request", "respondToWebhook": "Respond to Webhook",
    "extractFromFile": "Extract from File", "splitOut": "Split Out", "wait": "Wait",
    "executeWorkflow": "Execute Workflow", "chainLlm": "Basic LLM Chain",
    "informationExtractor": "Information Extractor", "lmChatOpenAi": "OpenAI Chat Model",
}

# (condición, técnica, por qué importa)
TECNICAS = [
    (lambda n, j: n["type"].endswith(".errorTrigger"),
     "Error Trigger", "un solo workflow recibe los fallos de todos los demás"),
    (lambda n, j: n["type"].endswith(".scheduleTrigger"),
     "Schedule Trigger con cron", "el flujo corre solo, sin que nadie lo dispare"),
    (lambda n, j: n["type"].endswith(("Trigger", ".webhook")) and not n["type"].endswith(
        (".errorTrigger", ".scheduleTrigger", ".executeWorkflowTrigger")),
     "Disparo por eventos (webhook o trigger de la app)", "reacciona al instante, sin revisar cada tanto"),
    (lambda n, j: n["parameters"].get("options", {}).get("rawBody"),
     "Webhook con Raw Body", "conserva los bytes exactos para verificar firmas HMAC"),
    (lambda n, j: n["type"].endswith(".respondToWebhook") and "{{" in str(n["parameters"]),
     "Respuesta HTTP con código dinámico", "responde 200 o 401 según lo que decidió el flujo"),
    # En producción algunos nodos traen la rama de memoria pero usan la hoja
    (lambda n, j: "$getWorkflowStaticData" in j and "REGISTRO = 'sheets'" not in j,
     "Memoria persistente (workflowStaticData)", "recuerda estado entre ejecuciones sin base de datos"),
    (lambda n, j: "$('" in j,
     "Lectura de otros nodos por nombre ($('Nodo'))", "usa datos de pasos anteriores aunque $input traiga otra cosa"),
    (lambda n, j: "$env." in j,
     "Secretos por variables de entorno ($env)", "ninguna clave queda escrita en el workflow"),
    (lambda n, j: "require('crypto')" in j,
     "Módulo crypto de Node en el nodo Code", "calcula firmas HMAC-SHA256 dentro de n8n"),
    (lambda n, j: n.get("onError") == "continueErrorOutput",
     "Salida de error del nodo (On Error → error output)", "si un servicio falla, el flujo sigue por otra rama"),
    (lambda n, j: n.get("retryOnFail"),
     "Reintentos del nodo (Retry On Fail)", "absorbe caídas breves de una API externa"),
    (lambda n, j: n.get("executeOnce"),
     "Execute Once", "lee una hoja completa una sola vez aunque lleguen varios items"),
    (lambda n, j: n.get("alwaysOutputData"),
     "Always Output Data", "una hoja vacía no corta el flujo"),
    (lambda n, j: n["type"].endswith(".switch"),
     "Switch con salidas con nombre", "cada decisión tiene su rama legible en el canvas"),
    (lambda n, j: n["type"].endswith(".splitOut"),
     "Split Out", "convierte una lista en items para procesarlos uno por uno"),
    (lambda n, j: n["type"].endswith(".executeWorkflow")
     and n["parameters"].get("options", {}).get("waitForSubWorkflow") is False,
     "Sub-workflow asíncrono (Execute Workflow sin esperar)", "el flujo principal termina sin bloquearse"),
    (lambda n, j: n["type"].endswith(".wait"),
     "Wait", "espera antes de reintentar, en vez de insistir al instante"),
    (lambda n, j: n["type"].endswith(".extractFromFile"),
     "Extract from File", "lee PDFs o archivos de texto dentro del flujo"),
    (lambda n, j: n["type"].startswith("@n8n/n8n-nodes-langchain.") and not n["type"].endswith("lmChatOpenAi"),
     "Nodos de IA de n8n (LangChain)", "la IA es un paso del flujo, con su modelo conectado aparte"),
    (lambda n, j: n["type"].endswith("lmChatOpenAi"),
     "Modelo de IA como sub-nodo intercambiable", "se cambia de proveedor sin tocar el resto del flujo"),
    (lambda n, j: n["parameters"].get("operation") == "appendOrUpdate",
     "Upsert en Google Sheets (appendOrUpdate)", "actualiza la fila si existe o la crea si no"),
    (lambda n, j: n["type"].endswith(".httpRequest") and n["parameters"].get("authentication") == "genericCredentialType",
     "HTTP Request con credencial genérica", "llama a cualquier API aunque n8n no tenga un nodo para ella"),
]


def tipo(n: dict) -> str:
    corto = n["type"].split(".")[-1]
    return TIPOS.get(corto, corto)


def configuracion(n: dict) -> str:
    p, partes = n["parameters"], []
    if p.get("operation"):
        partes.append(f"operación `{p['operation']}`")
    hoja = p.get("sheetName", {})
    if isinstance(hoja, dict) and hoja.get("value"):
        partes.append(f"pestaña `{hoja['value']}`")
    if p.get("path"):
        partes.append(f"`{p.get('httpMethod', 'GET')} /{p['path']}`")
    regla = p.get("rule", {}).get("interval", [{}])[0].get("expression")
    if regla:
        partes.append(f"cron `{regla}`")
    if n.get("onError") == "continueErrorOutput":
        partes.append("salida de error")
    if n.get("retryOnFail"):
        partes.append(f"{n.get('maxTries', 3)} intentos")
    if n.get("executeOnce"):
        partes.append("Execute Once")
    if n.get("alwaysOutputData"):
        partes.append("Always Output Data")
    if p.get("options", {}).get("rawBody"):
        partes.append("Raw Body")
    if p.get("options", {}).get("waitForSubWorkflow") is False:
        partes.append("no espera al sub-workflow")
    if n["type"].endswith(".code"):
        lineas = len(p.get("jsCode", "").splitlines())
        partes.append(f"{lineas} líneas generadas desde `workflows/src/`")
    texto = ", ".join(partes)
    if n.get("notes"):
        texto = (texto + ". " if texto else "") + n["notes"].replace("|", "/")
    return texto or "—"


def documentar(ruta: Path) -> str:
    wf = json.loads(ruta.read_text(encoding="utf-8"))
    tecnicas: dict[str, str] = {}
    for n in wf["nodes"]:
        j = json.dumps(n, ensure_ascii=False)
        for cond, nombre, porque in TECNICAS:
            if cond(n, j):
                tecnicas.setdefault(nombre, porque)
    salida = [f"**{wf['name']}** · {len(wf['nodes'])} nodos", "",
              "| Técnica de n8n | Para qué se usa aquí |", "| --- | --- |"]
    salida += [f"| {k} | {v} |" for k, v in tecnicas.items()]
    salida += ["", "<details><summary>Nodo por nodo</summary>", "",
               "| Nodo | Tipo | Configuración |", "| --- | --- | --- |"]
    salida += [f"| {n['name']} | {tipo(n)} | {configuracion(n)} |" for n in wf["nodes"]]
    salida += ["", "</details>"]
    return "\n".join(salida)


def main():
    print("\n\n".join(documentar(Path(a)) for a in sys.argv[1:]))


if __name__ == "__main__":
    main()
