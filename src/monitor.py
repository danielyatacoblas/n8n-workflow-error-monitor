"""Monitor central de errores de n8n: clasifica, agrupa y decide qué hacer.

Es la misma lógica que corre en el nodo Code del workflow de errores
(workflows/src/monitor.js); tests/test_paridad_js.py verifica que ambas copias
den el mismo resultado.

Cada workflow de la empresa apunta a este como su "Error Workflow". Por cada
fallo, n8n le entrega un aviso con la ejecución, el nodo y el error. El
monitor toma dos decisiones independientes:

  ¿reintentar?  si el error es pasajero (timeout, 429, 503), hasta 2 veces
  ¿avisar?      los errores iguales se agrupan en un incidente, que sigue
                abierto mientras se repitan con menos de 30 minutos de pausa.
                Se avisa al abrirlo, se escala a las 5 repeticiones y se
                recuerda cada 4 horas si sigue abierto. El resto solo se cuenta.
"""
from __future__ import annotations

import re

PAUSA_CIERRE = 30 * 60        # sin repetirse este tiempo, el incidente se cierra
UMBRAL_ESCALAR = 5            # repeticiones del incidente para escalarlo
RECORDATORIO = 4 * 60 * 60    # cada cuánto se recuerda un incidente que sigue abierto
MAX_REINTENTOS = 2            # reintentos automáticos por ejecución original
NIVELES = ["baja", "media", "alta", "critica"]

# Workflows donde un error sube un nivel de severidad (cobros, pedidos...)
CRITICOS = ["Cobros · sincronizar pagos", "Tienda · sincronizar stock"]

# Workflows que no se deben reintentar: repetirlos repetiría efectos (correos,
# cobros) que ya ocurrieron antes del nodo que falló
SIN_REINTENTO = ["Newsletter · envío masivo"]

CATEGORIAS = [
    ("transitorio", ["timeout", "timed out", "econnreset", "econnaborted", "econnrefused",
                     "etimedout", "socket hang up", "too many requests", "rate limit",
                     "bad gateway", "service unavailable", "gateway timeout",
                     "server is offline", "temporarily", "#429", "#502", "#503", "#504"]),
    ("credenciales", ["unauthorized", "forbidden", "invalid credentials", "authentication",
                      "token expired", "token has expired", "invalid_grant", "api key",
                      "permission", "#401", "#403"]),
    ("datos", ["cannot read properties", "is not a function", "is not defined",
               "unexpected token", "invalid json", "invalid input", "validation",
               "required field", "typeerror"]),
    ("configuracion", ["not found", "does not exist", "no node", "missing", "parameter",
                       "could not find", "#404"]),
]

SEVERIDAD_BASE = {"transitorio": "baja", "credenciales": "alta", "datos": "media",
                  "configuracion": "alta", "desconocido": "media"}

SUGERENCIA = {
    "transitorio": "El servicio externo no respondió a tiempo. Si sigue fallando, revisar "
                   "si está caído o si se superó su límite de peticiones.",
    "credenciales": "Una credencial venció o no tiene permisos. Renovarla en n8n → "
                    "Credentials; hasta entonces este workflow fallará en cada ejecución.",
    "datos": "Llegó un dato con un formato inesperado. Abrir la ejecución para ver qué "
             "entrada lo causó.",
    "configuracion": "Falta algo en la configuración del workflow: un nodo, un recurso o "
                     "un parámetro.",
    "desconocido": "Abrir la ejecución para revisar el detalle.",
}

# Lo que nunca debe salir en una alerta ni en el registro. Clases explícitas
# ([0-9], [A-Za-z]) en vez de \d o \w: en Python aceptan letras y dígitos de
# otros alfabetos y en JavaScript no, y el nodo de n8n debe dar lo mismo.
SECRETOS = [
    r"(bearer[ \t]+)[A-Za-z0-9._~+/=-]+",
    r"((?:api[_-]?key|token|secret|password|clave|access_token)[\"']?[ \t]*[:=][ \t]*[\"']?)[^ \t\r\n\"'&,}]+",
    r"(https?://[^ \t\r\n:/@]+:)[^ \t\r\n@/]+(?=@)",
]


# ── lectura y limpieza ─────────────────────────────────────────────────────

def limpiar(texto) -> str:
    """Quita tokens, claves y contraseñas de un texto antes de mostrarlo."""
    t = str(texto if texto is not None else "")
    for patron in SECRETOS:
        t = re.sub(patron, r"\1***", t, flags=re.IGNORECASE)
    return t


def leer_evento(aviso: dict) -> dict:
    """Extrae lo útil del aviso del Error Trigger y descarta lo sensible.

    El aviso trae los parámetros del nodo y las cabeceras de la petición, que
    pueden incluir tokens: no se copian. Tampoco el stack, que es largo y no
    ayuda a quien recibe la alerta.
    """
    if isinstance(aviso.get("body"), dict) and "workflow" in aviso["body"]:
        aviso = aviso["body"]                       # simulación por webhook
    ejecucion = aviso.get("execution") or {}
    disparador = aviso.get("trigger") or {}
    error = ejecucion.get("error") or disparador.get("error") or {}
    nodo = error.get("node") if isinstance(error.get("node"), dict) else {}
    mensajes = error.get("messages") if isinstance(error.get("messages"), list) else []
    workflow = aviso.get("workflow") or {}
    return {
        "workflow_id": str(workflow.get("id") or ""),
        "workflow": str(workflow.get("name") or "(sin nombre)"),
        "ejecucion_id": str(ejecucion.get("id") or ""),
        "url": str(ejecucion.get("url") or ""),
        "nodo": str(nodo.get("name") or ejecucion.get("lastNodeExecuted")
                    or ("(disparador)" if disparador else "")),
        "tipo_error": str(error.get("name") or ""),
        "mensaje": limpiar(error.get("message") or "(sin mensaje)")[:300],
        "detalle": limpiar(error.get("description") or (mensajes[0] if mensajes else ""))[:300],
        "codigo": str(error.get("httpCode") or ""),
        "reintento_de": str(ejecucion.get("retryOf") or ""),
        "modo": str(ejecucion.get("mode") or disparador.get("mode") or ""),
    }


# ── clasificación ──────────────────────────────────────────────────────────

def clasificar(e: dict) -> str:
    texto = " ".join([e["mensaje"], e["detalle"], e["codigo"], e["tipo_error"]]).lower()
    # Los códigos HTTP se buscan como número suelto ("#429"): "14290 filas" no es un 429
    codigos = {"#" + c for c in re.findall(r"(?<![0-9])([0-9]{3})(?![0-9])", texto)}
    for categoria, patrones in CATEGORIAS:
        for p in patrones:
            if (p in codigos) if p.startswith("#") else (p in texto):
                return categoria
    return "desconocido"


def severidad(categoria: str, workflow: str, subir: int = 0) -> str:
    nivel = NIVELES.index(SEVERIDAD_BASE[categoria]) + subir
    if workflow in CRITICOS:
        nivel += 1
    return NIVELES[min(nivel, len(NIVELES) - 1)]


def huella(e: dict) -> str:
    """Identifica "el mismo error" aunque cambien ids, montos o URLs."""
    m = e["mensaje"].lower()
    m = re.sub(r"https?://[^ \t\r\n]+", "<url>", m)
    m = re.sub(r"[a-z0-9_.+-]+@[a-z0-9_-]+\.[a-z0-9_.]+", "<email>", m)
    m = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<id>", m)
    m = re.sub(r"'[^']*'|\"[^\"]*\"", "<txt>", m)
    m = re.sub(r"[0-9]+", "<n>", m)
    m = re.sub(r"[ \t\r\n]+", " ", m).strip(" ")[:160]
    return f"{e['workflow_id']}|{e['nodo']}|{m}"


# ── decisión ───────────────────────────────────────────────────────────────

def decidir(e: dict, estado: dict, ahora: int) -> dict:
    """Decide qué hacer con un error y actualiza el estado del monitor.

    estado = {"grupos": {huella: {...}}, "cadenas": {ejecucion_id: n_reintento}}
    """
    grupos = estado.setdefault("grupos", {})
    cadenas = estado.setdefault("cadenas", {})
    categoria = clasificar(e)
    h = huella(e)

    # En qué reintento va esta cadena (0 = ejecución original). Si llega un
    # reintento cuyo origen no está en la memoria (lo reintentó una persona, o
    # la memoria se perdió), se da por agotado: sin esta regla, un error que
    # nunca se resuelve se reintentaría para siempre.
    conocido = True
    if e["reintento_de"]:
        conocido = e["reintento_de"] in cadenas
        n = cadenas[e["reintento_de"]] + 1 if conocido else MAX_REINTENTOS
    else:
        n = 0
    if e["ejecucion_id"]:
        cadenas[e["ejecucion_id"]] = n

    # Un incidente sigue abierto mientras el error se repita sin pausas largas
    g = grupos.get(h)
    anterior = 0
    if g is None or ahora - g["ultima"] >= PAUSA_CIERRE:
        if g is not None:
            anterior = g["conteo"]
        g = {"inicio": ahora, "ultima": ahora, "conteo": 0, "total": g["total"] if g else 0,
             "ultimo_aviso": 0, "alertado": False, "escalado": False,
             "workflow": e["workflow"], "nodo": e["nodo"], "categoria": categoria,
             "mensaje": e["mensaje"]}
        grupos[h] = g
    g["conteo"] += 1
    g["total"] += 1
    g["ultima"] = ahora

    reintentar = (categoria == "transitorio" and n < MAX_REINTENTOS
                  and bool(e["ejecucion_id"]) and e["modo"] != "manual"
                  and e["workflow"] not in SIN_REINTENTO)
    aviso = ""
    if g["conteo"] >= UMBRAL_ESCALAR and not g["escalado"]:
        aviso = "escalado"
    elif not reintentar and not g["alertado"]:
        aviso = "alerta"
    elif g["alertado"] and ahora - g["ultimo_aviso"] >= RECORDATORIO:
        aviso = "recordatorio"
    if aviso:
        g["alertado"] = True
        g["escalado"] = g["escalado"] or aviso == "escalado"
        g["ultimo_aviso"] = ahora

    accion = ({"alerta": "alertar", "escalado": "escalar", "recordatorio": "recordar"}[aviso]
              if aviso else "reintentar" if reintentar else "silenciar")
    sev = severidad(categoria, e["workflow"], 1 if aviso == "escalado" else 0)
    r = {"accion": accion, "reintentar": reintentar, "aviso": aviso, "categoria": categoria,
         "severidad": sev, "huella": h, "conteo": g["conteo"], "reintento": n,
         "evento": e, "mensaje": ""}
    if aviso:
        r["mensaje"] = mensaje_alerta(e, categoria, sev, g, n if conocido else -1,
                                      anterior, aviso, reintentar)
    return r


def duracion(segundos: int) -> str:
    minutos = max(1, (segundos + 59) // 60)
    if minutos < 60:
        return f"{minutos} min"
    return f"{minutos // 60} h {minutos % 60:02d} min"


def mensaje_alerta(e, categoria, sev, g, n, anterior, aviso, reintentar) -> str:
    """n: reintentos que lleva la cadena; -1 si es un reintento de origen desconocido."""
    titulo = {"alerta": "", "escalado": "Se repite: ", "recordatorio": "Sigue fallando: "}[aviso]
    lineas = [f"[{sev.upper()}] {titulo}{e['workflow']}"]
    if aviso != "alerta":
        lineas.append(f"{g['conteo']} veces en {duracion(g['ultima'] - g['inicio'])}"
                      + (" · cada ejecución se sigue reintentando sola." if reintentar else "."))
    lineas.append(f"Nodo: {e['nodo'] or '(desconocido)'}")
    error = e["mensaje"] + (f" ({e['codigo']})" if e["codigo"] else "")
    lineas.append(f"Error: {error}")
    tipo = f"Tipo: {categoria}"
    if n > 0:
        tipo += f" · falló también tras {n} reintento{'s' if n > 1 else ''} automático{'s' if n > 1 else ''}"
    elif n < 0:
        tipo += " · es un reintento y volvió a fallar"
    lineas.append(tipo)
    if anterior > 1:
        lineas.append(f"Ya había pasado antes: el incidente anterior sumó {anterior} veces.")
    lineas.append(f"Qué hacer: {SUGERENCIA[categoria]}")
    if e["url"]:
        lineas.append(f"Ejecución: {e['url']}")
    return "\n".join(lineas)


def fila_registro(r: dict, fecha: str) -> dict:
    e = r["evento"]
    return {"fecha": fecha, "workflow": e["workflow"], "nodo": e["nodo"],
            "categoria": r["categoria"], "severidad": r["severidad"], "accion": r["accion"],
            "mensaje": e["mensaje"], "codigo": e["codigo"], "ejecucion": e["ejecucion_id"],
            "reintento": r["reintento"], "reintentado": "si" if r["reintentar"] else "no",
            "huella": r["huella"]}


# ── resumen del día ────────────────────────────────────────────────────────

def resumen_dia(filas: list[dict], dia: str) -> str:
    """Texto para el equipo al final del día, a partir del registro."""
    hoy = [f for f in filas if str(f.get("fecha") or "")[:10] == dia]
    if not hoy:
        return f"Errores del {dia}: ninguno. Todos los workflows funcionaron."
    por_workflow: dict = {}
    for f in hoy:
        w = por_workflow.setdefault(str(f.get("workflow") or "?"), {"total": 0, "reintentados": 0})
        w["total"] += 1
        if f.get("reintentado") == "si":
            w["reintentados"] += 1
    lineas = [f"Errores del {dia}: {len(hoy)} en {len(por_workflow)} workflow"
              f"{'s' if len(por_workflow) > 1 else ''}."]
    for nombre, w in sorted(por_workflow.items(), key=lambda kv: (-kv[1]["total"], kv[0])):
        extra = f" ({w['reintentados']} reintentados)" if w["reintentados"] else ""
        lineas.append(f"- {nombre}: {w['total']}{extra}")
    categorias: dict = {}
    for f in hoy:
        c = str(f.get("categoria") or "?")
        categorias[c] = categorias.get(c, 0) + 1
    lineas.append("Por tipo: " + ", ".join(
        f"{c} {n}" for c, n in sorted(categorias.items(), key=lambda kv: (-kv[1], kv[0]))))
    return "\n".join(lineas)
