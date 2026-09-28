#!/usr/bin/env python3
"""Pasa el día de avisos por el monitor y cuenta cuántos mensajes recibe el equipo.

    python scripts/generar_avisos.py
    python scripts/simular_monitor.py

La cifra que importa: cuántos errores llegaron y cuántas veces se interrumpió
a una persona. Sin monitor, cada error es un correo de n8n.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.monitor import decidir, fila_registro, leer_evento, resumen_dia  # noqa: E402

AVISOS = ROOT / "data" / "avisos_dia.json"
LIMA = timezone(timedelta(hours=-5))


def simular() -> tuple[list[dict], list[dict]]:
    if not AVISOS.exists():
        raise SystemExit("Primero corre: python scripts/generar_avisos.py")
    estado: dict = {}
    resultados, filas = [], []
    for a in json.loads(AVISOS.read_text(encoding="utf-8")):
        r = decidir(leer_evento(a["aviso"]), estado, a["llega"])
        fecha = datetime.fromtimestamp(a["llega"], LIMA).isoformat()
        resultados.append(dict(r, llega=fecha))
        filas.append(fila_registro(r, fecha))
    return resultados, filas


def main():
    resultados, filas = simular()
    avisos = Counter(r["aviso"] for r in resultados if r["aviso"])
    print(f"Errores recibidos:        {len(resultados)}")
    print(f"Mensajes al equipo:       {sum(avisos.values())}  ({avisos['alerta']} alertas, "
          f"{avisos['escalado']} escalados, {avisos['recordatorio']} recordatorios)")
    print(f"Reintentados solos:       {sum(1 for r in resultados if r['reintentar'])}")
    print(f"Contados sin interrumpir: {sum(1 for r in resultados if r['accion'] == 'silenciar')}")

    print("\nMensajes que recibió el equipo:")
    for r in resultados:
        if r["mensaje"]:
            print(f"\n--- {r['llega'][11:16]} ---\n{r['mensaje']}")

    print("\nResumen de las 18:00:")
    print(resumen_dia(filas, filas[0]["fecha"][:10]))


if __name__ == "__main__":
    main()
