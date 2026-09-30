# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y versionado [SemVer](https://semver.org/lang/es/).

## [1.1.1]

### Añadido
- Diagrama gitGraph del historial en el README, generado por `scripts/diagrama_git.py` a partir de las ramas y tags reales.

## [1.1.0]

### Añadido
- Imagen de arquitectura en el README: problema, entradas, pasos dentro de n8n y salidas.
- Imagen de pruebas en el README: tests por archivo y verificaciones hechas en n8n real.

## [1.0.0]

### Añadido
- Error Workflow central: lee el aviso de n8n, descarta parámetros, cabeceras y stack, y tapa tokens y contraseñas.
- Clasificación en transitorio, credenciales, datos, configuración y desconocido, con severidad y sugerencia de qué hacer.
- Incidentes: un mismo error se agrupa mientras se repita sin pausas de 30 minutos; aviso al abrir, escalado a las 5 repeticiones y recordatorio cada 4 horas.
- Reintento automático de errores pasajeros (hasta 2) por la API pública de n8n, en un sub-workflow que no bloquea al monitor.
- Guarda contra reintentos infinitos cuando el origen de un reintento no está en la memoria.
- Registro en Google Sheets y resumen diario a las 18:00 por Telegram.
- Demo con panel web y tres workflows de ejemplo que fallan de verdad.
- Día de 51 avisos simulados a partir de avisos reales de n8n 2.40.
- Test de paridad entre el nodo JavaScript y la lógica Python.
