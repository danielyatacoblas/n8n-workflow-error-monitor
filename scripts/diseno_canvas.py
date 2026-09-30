"""Acomoda los nodos del workflow en el canvas de n8n a partir de sus conexiones.

Escribir las posiciones a mano en el build terminaba con nodos encimados cada
vez que se agregaba una rama. Aquí se calculan:

  - columna: distancia desde el disparador (primero se llega, primero se ubica;
    así un regreso, como el de la IA al reparto, no empuja todo a la derecha);
  - fila: cada hijo empieza a la altura de su padre y baja si la fila está
    ocupada, lo que dibuja cada rama como un árbol legible;
  - sub-nodos de IA (el modelo de un LLM Chain) quedan justo debajo de su nodo;
  - cada disparador arranca su propio carril, uno debajo del otro.
"""
from __future__ import annotations

from functools import wraps

ANCHO = 280      # separación entre columnas
ALTO = 200       # separación entre filas
EXTRA_ANCHO = 140  # los nodos de IA (cadenas, extractores) se dibujan más anchos
ANCHOS = ("@n8n/n8n-nodes-langchain.chain", "@n8n/n8n-nodes-langchain.informationExtractor",
          "@n8n/n8n-nodes-langchain.agent")


def ordenar(wf: dict) -> dict:
    nodos = {n["name"]: n for n in wf["nodes"]}
    hijos: dict[str, list[tuple[int, str]]] = {k: [] for k in nodos}
    padres: dict[str, set[str]] = {k: set() for k in nodos}
    sub_de: dict[str, list[str]] = {k: [] for k in nodos}
    for origen, salidas in wf["connections"].items():
        for tipo, ramas in salidas.items():
            for i, rama in enumerate(ramas):
                for d in rama:
                    if tipo == "main":
                        hijos[origen].append((i, d["node"]))
                        padres[d["node"]].add(origen)
                    else:                              # ai_languageModel, etc.
                        sub_de[d["node"]].append(origen)
    subnodos = {s for lista in sub_de.values() for s in lista}
    disparadores = [k for k in nodos if not padres[k] and k not in subnodos]

    pos: dict[str, tuple[int, int]] = {}
    libre: dict[int, int] = {}                         # próxima fila libre por columna
    base = 0
    for disparador in disparadores:
        cola = [(disparador, 0, base)]
        while cola:
            nombre, col, fila = cola.pop(0)
            if nombre in pos:
                continue
            fila = max(fila, libre.get(col, base))
            pos[nombre] = (col, fila)
            libre[col] = fila + 1
            for s in sub_de[nombre]:                   # el modelo, debajo del nodo
                pos[s] = (col, libre[col])
                libre[col] += 1
            for _, h in sorted(hijos[nombre], key=lambda x: x[0]):
                if h not in pos:
                    cola.append((h, col + 1, fila))
        base = max(libre.values()) + 1                 # el siguiente carril, más abajo
        libre = {c: base for c in libre}

    # Una columna con un nodo ancho empuja a las siguientes
    x, cols = {}, sorted({c for c, _ in pos.values()})
    acumulado = 0
    for c in cols:
        x[c] = acumulado
        ancha = any(nodos[n]["type"].startswith(ANCHOS) for n, (cc, _) in pos.items() if cc == c)
        acumulado += ANCHO + (EXTRA_ANCHO if ancha else 0)
    for nombre, (col, fila) in pos.items():
        nodos[nombre]["position"] = [x[col], fila * ALTO]
    return wf


def acomodar(build):
    """Decorador para las funciones build_*: devuelven el workflow ya acomodado."""
    @wraps(build)
    def envoltura(*args, **kwargs):
        return ordenar(build(*args, **kwargs))
    return envoltura
