#!/usr/bin/env python3
"""Dibuja el historial Git Flow del repositorio como diagrama Mermaid gitGraph.

    python scripts/diagrama_git.py

Recorre `develop` por su primer padre: cada merge de una rama se dibuja con
sus commits, y cada release se fusiona también a `main` con su tag. Imprime
el bloque listo para pegar en el README; así el diagrama sale del historial
real y no se dibuja a mano.
"""
from __future__ import annotations

import re
import subprocess
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout.strip()


class Grafo:
    def __init__(self):
        self.lineas = ["gitGraph"]
        self.usados: set[str] = set()

    def commit(self, sha: str) -> None:
        asunto = git("log", "-1", "--format=%s", sha).replace('"', "'")
        texto = asunto if len(asunto) <= 48 else asunto[:45] + "..."
        if texto in self.usados:            # Mermaid exige ids únicos
            texto = f"{texto} ({sha[:7]})"
        self.usados.add(texto)
        self.lineas.append(f'   commit id: "{texto}"')

    def add(self, linea: str) -> None:
        self.lineas.append("   " + linea)


def main():
    g = Grafo()
    primeros = git("rev-list", "--first-parent", "--reverse", "develop").split()
    tags = {git("rev-list", "-n1", t): t for t in git("tag").split()}
    raiz = primeros[0]
    g.commit(raiz)
    g.add("branch develop")
    g.add("checkout develop")

    for sha in primeros[1:]:
        padres = git("log", "-1", "--format=%P", sha).split()
        if len(padres) < 2:
            g.commit(sha)
            continue
        asunto = git("log", "-1", "--format=%s", sha)
        m = re.match(r"Merge branch '([^']+)'", asunto)
        rama = m.group(1) if m else "rama"
        commits = git("rev-list", "--reverse", "--no-merges", f"{padres[0]}..{padres[1]}").split()
        g.add(f"branch {rama}")
        g.add(f"checkout {rama}")
        for c in commits:
            g.commit(c)
        if rama.startswith("release/"):
            merge_main = git("log", "main", "--format=%H %s", "--grep", f"^Merge branch '{rama}'$")
            tag = tags.get(merge_main.split()[0]) if merge_main else None
            g.add("checkout main")
            g.add(f'merge {rama}' + (f' tag: "{tag}"' if tag else ""))
        g.add("checkout develop")
        g.add(f"merge {rama}")

    print("```mermaid")
    print("\n".join(g.lineas))
    print("```")


if __name__ == "__main__":
    main()
