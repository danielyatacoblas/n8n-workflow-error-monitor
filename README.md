<h1 align="center">Monitor central de errores para n8n</h1>

<p align="center"><i>51 errores en un día, 10 mensajes al equipo, y ninguno sin explicar qué hacer</i></p>

<p align="center">
  <img alt="tests" src="https://img.shields.io/badge/tests-62%20passed-brightgreen">
  <img alt="ruido" src="https://img.shields.io/badge/ruido-51%20errores%20%E2%86%92%2010%20mensajes-brightgreen">
  <img alt="n8n" src="https://img.shields.io/badge/n8n-self--hosted-EA4B71">
  <img alt="python" src="https://img.shields.io/badge/python-3.12-3776AB">
  <img alt="licencia" src="https://img.shields.io/badge/licencia-MIT-blue">
</p>

---

## Para qué existe este repositorio

Una empresa con veinte workflows en n8n se entera de que uno falló cuando un cliente reclama. Si activa los avisos, pasa lo contrario: un token vencido hace fallar el mismo workflow cada 10 minutos y llegan 50 correos idénticos, hasta que nadie los lee. Y buena parte de los errores son pasajeros (una API que tardó o limitó las peticiones) y se habrían resuelto reintentando.

**Este workflow recibe el error de cualquier otro workflow, lo clasifica, lo reintenta si es pasajero, agrupa las repeticiones y avisa una sola vez, con el nodo que falló, el motivo y qué hacer. Además quita tokens y contraseñas antes de mostrar nada.**

```mermaid
flowchart TD
    W1["Workflow A"] -->|falla| ET
    W2["Workflow B"] -->|falla| ET
    W3["Workflow C"] -->|falla| ET
    subgraph N ["Monitor (Error Workflow)"]
        ET["Error Trigger"] --> L["Leer y limpiar<br/>sin tokens ni stack"]
        L --> C["Clasificar<br/>transitorio · credenciales ·<br/>datos · configuración"]
        C --> I{"¿Incidente<br/>abierto?"}
    end
    C -->|"pasajero, hasta 2 veces"| R["Sub-workflow:<br/>esperar 1 min y reintentar<br/>por la API de n8n"]
    I -->|"nuevo"| A["Alerta"]
    I -->|"5.ª repetición"| E["Escalado"]
    I -->|"abierto hace 4 h"| RC["Recordatorio"]
    I -->|"repetición"| S["Solo se cuenta"]
    A --> T["Telegram + correo si es grave"]
    E --> T
    RC --> T
    C --> REG["Registro en Sheets<br/>y resumen de las 18:00"]
```

---

## Arquitectura

<p align="center"><img src="docs/arquitectura.png" alt="Arquitectura: entradas, pasos dentro de n8n y salidas" width="900"></p>

---

## El workflow en n8n

<p align="center"><img src="docs/workflow_n8n.png" alt="Workflow de producción abierto en el editor de n8n" width="900"></p>

<p align="center"><i>Captura del editor de n8n 2.40 con <code>workflows/monitor_produccion.json</code> importado.
Los triángulos rojos solo indican credenciales por conectar (Google, Telegram, IA).</i></p>

### Paso a paso: la cadena de reintentos tal como corrió en n8n real

```mermaid
sequenceDiagram
    autonumber
    participant W as Workflow de cobros
    participant ET as Monitor · Error Trigger
    participant M as workflowStaticData
    participant SUB as Sub-workflow · reintentos
    participant API as API de n8n
    participant T as Telegram
    W->>ET: falla n.º 131 (timeout)
    ET->>M: cadena n.º 131 = 0 reintentos
    ET-)SUB: pedir reintento, sin esperar
    Note over ET,M: el monitor termina y guarda su memoria
    SUB->>SUB: esperar 1 minuto
    SUB->>API: POST /executions/131/retry
    API->>W: reintento n.º 136
    W->>ET: falla n.º 136 (retryOf 131)
    ET->>M: 131 → 1 reintento
    ET-)SUB: pedir reintento, sin esperar
    SUB->>API: POST /executions/136/retry
    API->>W: reintento n.º 139
    W->>ET: falla n.º 139 (retryOf 136)
    ET->>M: 136 → 2 reintentos = máximo
    ET->>T: una sola alerta: "falló también tras 2 reintentos automáticos"
```

### Técnicas de n8n que usa

**Monitor de errores · producción** · 14 nodos

| Técnica de n8n | Para qué se usa aquí |
| --- | --- |
| Error Trigger | un solo workflow recibe los fallos de todos los demás |
| Memoria persistente (workflowStaticData) | recuerda estado entre ejecuciones sin base de datos |
| Lectura de otros nodos por nombre ($('Nodo')) | usa datos de pasos anteriores aunque $input traiga otra cosa |
| Sub-workflow asíncrono (Execute Workflow sin esperar) | el flujo principal termina sin bloquearse |
| Schedule Trigger con cron | el flujo corre solo, sin que nadie lo dispare |
| Execute Once | lee una hoja completa una sola vez aunque lleguen varios items |
| Always Output Data | una hoja vacía no corta el flujo |

<details><summary>Nodo por nodo</summary>

| Nodo | Tipo | Configuración |
| --- | --- | --- |
| Error en otro workflow | Error Trigger | — |
| Clasificar error | Code (JavaScript) | 317 líneas generadas desde `workflows/src/` |
| ¿Reintentar? | If | — |
| ¿Avisar? | If | Reintentar y avisar son decisiones independientes: un error pasajero que se repite mucho se reintenta y además se escala. |
| Pedir reintento (sin esperar) | Execute Workflow | no espera al sub-workflow. Sin esperar a propósito: si el monitor esperara, el reintento fallaría antes de que el monitor guarde su memoria y el contador de reintentos nunca subiría (ver GUIA.md). |
| Telegram · Alerta | Telegram | — |
| ¿Alta o crítica? | If | — |
| Gmail · Aviso al responsable | Gmail | — |
| Fila de registro | Edit Fields (Set) | — |
| Sheets · Registro de errores | Google Sheets | operación `append`, pestaña `Errores` |
| Todos los días 18:00 | Schedule Trigger | cron `0 18 * * *` |
| Sheets · Leer registro | Google Sheets | pestaña `Errores`, Execute Once, Always Output Data |
| Resumen del día | Code (JavaScript) | 317 líneas generadas desde `workflows/src/` |
| Telegram · Resumen del día | Telegram | — |

</details>

**Monitor de errores · reintentos** · 4 nodos

| Técnica de n8n | Para qué se usa aquí |
| --- | --- |
| Wait | espera antes de reintentar, en vez de insistir al instante |
| Salida de error del nodo (On Error → error output) | si un servicio falla, el flujo sigue por otra rama |
| HTTP Request con credencial genérica | llama a cualquier API aunque n8n no tenga un nodo para ella |
| Lectura de otros nodos por nombre ($('Nodo')) | usa datos de pasos anteriores aunque $input traiga otra cosa |

<details><summary>Nodo por nodo</summary>

| Nodo | Tipo | Configuración |
| --- | --- | --- |
| Pedido de reintento | Execute Workflow Trigger | — |
| Esperar 1 minuto | Wait | Reintentar al instante un timeout suele volver a fallar. |
| n8n · Reintentar ejecución | HTTP Request | salida de error. API pública de n8n. Credencial Header Auth: X-N8N-API-KEY. |
| Telegram · No se pudo reintentar | Telegram | — |

</details>

<sub>Tablas generadas del JSON del workflow con <code>python scripts/documentar_workflow.py workflows/monitor_produccion.json workflows/monitor_reintentos.json</code>.</sub>

---

## Demo

<!-- VIDEO: arrastra aquí el .mp4 al editar el README en GitHub y deja solo la URL que genera. -->

<p align="center"><img src="docs/panel_monitor.png" alt="Panel del monitor de errores" width="820"></p>

<p align="center"><i>El panel de la demo después de hacer fallar de verdad tres workflows en
n8n (servicio caído, dato inesperado y credencial inválida) y enviar parte del
día de avisos simulado.</i></p>

---

## Qué hace con cada error

Términos que conviene conocer:

- **Error Workflow:** en n8n, cada workflow puede indicar en *Settings* otro workflow que se ejecuta cuando falla. Este repositorio es ese workflow, compartido por todos.
- **Error pasajero:** falla por una causa externa que suele resolverse sola (tiempo de espera agotado, código 429 "demasiadas peticiones", 503 "servicio no disponible").
- **Incidente:** un mismo error que se repite. Sigue abierto mientras vuelva a ocurrir con menos de 30 minutos de pausa.

| Tipo | Ejemplos | Severidad | ¿Se reintenta? |
| --- | --- | --- | --- |
| Transitorio | timeout, 429, 502, 503, conexión abortada | baja | Sí, hasta 2 veces, esperando 1 minuto |
| Credenciales | 401, 403, token vencido, `invalid_grant` | alta | No: fallaría igual |
| Datos | `Cannot read properties of undefined`, JSON inválido | media | No |
| Configuración | hoja o recurso no encontrado, parámetro faltante | alta | No |

Los workflows marcados como críticos (cobros, stock) suben un nivel de severidad. Los avisos de severidad alta o crítica llegan también por correo.

**Cuándo avisa:** al abrirse el incidente, al llegar a 5 repeticiones (escalado, un nivel más de severidad) y cada 4 horas si sigue abierto. Si un error pasajero se reintenta pero se repite mucho, se sigue reintentando y también se escala.

**Qué nunca muestra:** el aviso de n8n incluye los parámetros del nodo, las cabeceras de la petición y el stack. El monitor no copia nada de eso, y además tapa en el mensaje cualquier `Bearer …`, `api_key=…`, `password: …` o usuario y contraseña dentro de una URL.

---

## Cómo se probó

**En n8n real (2.40), de punta a punta:**

- Tres workflows de ejemplo fallan de verdad: uno llama a una dirección que no responde (timeout), otro lee un campo que no existe y otro llama a la API de n8n sin clave (401). Los tres llegaron al monitor por el Error Trigger y se clasificaron bien.
- Un workflow con timeout se reintentó por la API pública de n8n: la ejecución original falló, el monitor pidió 2 reintentos, ambos fallaron y recién entonces envió **una** alerta: "falló también tras 2 reintentos automáticos".

**Con un día simulado** de 51 avisos, armado a partir de avisos reales capturados de n8n:

| | |
| --- | --- |
| Errores recibidos | 51 |
| Mensajes al equipo | 10 (6 alertas, 3 escalados, 1 recordatorio) |
| Reintentados solos | 17 |
| Contados sin interrumpir | 25 |

Los tests verifican además que **todo workflow con un error sin resolver genera al menos un aviso**: reducir el ruido no puede esconder problemas.

---

## Lo que salió mal en la primera versión (y cómo se corrigió)

La primera versión entró en un **bucle de reintentos infinito** al probarla en n8n real. La causa: el monitor llamaba a la API para reintentar y n8n ejecutaba ese reintento **mientras el monitor seguía esperando la respuesta**. El reintento fallaba y disparaba otra ejecución del monitor, que leía la memoria antes de que la primera la guardara. El contador de reintentos nunca pasaba de 1.

La corrección tiene dos partes, y un test para cada una:

1. **El reintento se pide a un sub-workflow sin esperar la respuesta.** El monitor termina y guarda su memoria antes de que ocurra el reintento.
2. **Un reintento cuyo origen no está en la memoria no se reintenta más.** Aunque la memoria fallara por otro motivo, no puede haber bucle.

---

## Pruebas

<p align="center"><img src="docs/pruebas.png" alt="Resultados de las pruebas automáticas y de la verificación en n8n real" width="900"></p>

La integración continua corre todos los tests en cada push. Lo de la columna
derecha se verificó importando los workflows en n8n 2.40 con Docker.

---

## Probarlo en 2 minutos

```bash
pip install pytest
python scripts/simular_monitor.py    # el día de 51 errores y los 10 mensajes
python -m pytest -v                  # 62 tests
```

**Con n8n de verdad** (Docker):

```bash
docker compose up -d
for f in workflows/monitor_demo.json workflows/ejemplos/*.json; do
  docker exec monitor_n8n n8n import:workflow --input=/$f
done
for id in monitordemo ejemplopagos ejemplofacturas ejemplocrm; do
  docker exec monitor_n8n n8n update:workflow --id=$id --active=true
done
docker restart monitor_n8n
```

Haz fallar los ejemplos (`curl -X POST localhost:5678/webhook/falla-datos`,
`falla-credencial`, `falla-pagos`) y abre el panel en
<http://localhost:5678/webhook/monitor-demo/panel>. La configuración de
producción (API de n8n, Telegram, Gmail y Sheets) está en [`GUIA.md`](GUIA.md).

---

## Estructura

```
├── data/
│   ├── avisos_reales.json            # avisos capturados del Error Trigger de n8n 2.40
│   └── avisos_dia.json               # 51 avisos de un día, armados a partir de los reales
├── src/monitor.py                    # limpiar, clasificar, agrupar y decidir
├── workflows/
│   ├── src/monitor.js                # el código de los nodos Code (+ panel HTML)
│   ├── monitor_demo.json             # Error Trigger + panel web, SIN credenciales
│   ├── monitor_produccion.json       # alertas, correo, registro y resumen diario
│   ├── monitor_reintentos.json       # sub-workflow: esperar y reintentar por la API
│   └── ejemplos/                     # tres workflows que fallan a propósito
├── scripts/                          # generador de avisos, simulación y build
├── tests/                            # 62 tests (incluye paridad JS ↔ Python)
└── docker-compose.yml                # n8n self-hosted
```

---

## Flujo de trabajo con Git

El repositorio sigue **Git Flow**: `main` siempre desplegable, `develop` como
integración, y una rama por cambio. Los merges son `--no-ff` para que cada
funcionalidad quede como un bloque legible en el historial, y cada versión
lleva su tag.

```mermaid
gitGraph
   commit id: "chore: set up the repository"
   branch develop
   checkout develop
   branch feature/avisos-de-n8n
   checkout feature/avisos-de-n8n
   commit id: "test: add error payloads captured from a real..."
   commit id: "feat: generate a day of 51 error payloads fro..."
   checkout develop
   merge feature/avisos-de-n8n
   branch feature/clasificacion-e-incidentes
   checkout feature/clasificacion-e-incidentes
   commit id: "feat: classify, group into incidents and deci..."
   commit id: "test: cover reading real payloads, masking, c..."
   commit id: "feat: simulate the day and count how many mes..."
   commit id: "test: fewer messages than a fifth of the erro..."
   checkout develop
   merge feature/clasificacion-e-incidentes
   branch feature/nodo-n8n
   checkout feature/nodo-n8n
   commit id: "feat: port the monitor to the n8n Code node w..."
   commit id: "test: run the n8n node outside n8n and compar..."
   checkout develop
   merge feature/nodo-n8n
   branch feature/workflows
   checkout feature/workflows
   commit id: "feat: build the demo, production and retry wo..."
   commit id: "test: check async retries, independent decisi..."
   commit id: "docs: add the demo panel after real failures ..."
   checkout develop
   merge feature/workflows
   branch chore/ci
   checkout chore/ci
   commit id: "chore: run tests and the day simulation on ev..."
   checkout develop
   merge chore/ci
   branch docs/documentacion
   checkout docs/documentacion
   commit id: "docs: explain the noise problem, the incident..."
   commit id: "docs: add the production guide, settings and ..."
   checkout develop
   merge docs/documentacion
   branch release/v1.0.0
   checkout release/v1.0.0
   commit id: "chore(release): prepare v1.0.0"
   checkout main
   merge release/v1.0.0 tag: "v1.0.0"
   checkout develop
   merge release/v1.0.0
   branch docs/imagenes-readme
   checkout docs/imagenes-readme
   commit id: "docs: add architecture and test result images..."
   checkout develop
   merge docs/imagenes-readme
   branch release/v1.1.0
   checkout release/v1.1.0
   commit id: "chore(release): prepare v1.1.0"
   checkout main
   merge release/v1.1.0 tag: "v1.1.0"
   checkout develop
   merge release/v1.1.0
   branch feature/diagrama-git
   checkout feature/diagrama-git
   commit id: "feat: draw the Git Flow history as a Mermaid ..."
   checkout develop
   merge feature/diagrama-git
   branch docs/diagrama-git-flow
   checkout docs/diagrama-git-flow
   commit id: "docs: show the branch history as a gitGraph i..."
   checkout develop
   merge docs/diagrama-git-flow
   branch release/v1.1.1
   checkout release/v1.1.1
   commit id: "chore(release): prepare v1.1.1"
   checkout main
   merge release/v1.1.1 tag: "v1.1.1"
   checkout develop
   merge release/v1.1.1
   branch feature/canvas-ordenado
   checkout feature/canvas-ordenado
   commit id: "feat: lay out the canvas from the workflow co..."
   checkout develop
   merge feature/canvas-ordenado
   branch feature/documentar-workflow
   checkout feature/documentar-workflow
   commit id: "feat: document the n8n techniques each workfl..."
   checkout develop
   merge feature/documentar-workflow
```

<p align="center"><i>Historial real del repositorio, generado con
<code>python scripts/diagrama_git.py</code>.</i></p>

| Rama | Para qué |
| --- | --- |
| `main` | Solo versiones liberadas. Cada merge lleva su tag. |
| `develop` | Integración de todo lo terminado. |
| `feature/*` | Una funcionalidad nueva. |
| `fix/*` | Una corrección concreta. |
| `release/*` | Preparación de la versión; luego se fusiona a `main` y `develop`. |

Los mensajes siguen [Conventional Commits](https://www.conventionalcommits.org/):
`feat:`, `fix:`, `test:`, `docs:`, `chore:`, con el porqué del cambio en el cuerpo.

Los tags de versión no se crean a mano: la GitHub Action
[`tags.yml`](.github/workflows/tags.yml) etiqueta cada merge de `release/*` en
`main` con su número de versión.

---

## Documentación

| Documento | Contenido |
| --- | --- |
| [`GUIA.md`](GUIA.md) | Puesta en producción, API de n8n, reglas ajustables, límites y comportamientos de n8n encontrados al probar |
| [`CHANGELOG.md`](CHANGELOG.md) | Cambios por versión |

---

## Licencia

[MIT](LICENSE) · Daniel Yataco Blas

> Proyecto de demostración. Los workflows de ejemplo y el día de avisos son
> ficticios; los avisos base se capturaron de una instancia local de n8n.
