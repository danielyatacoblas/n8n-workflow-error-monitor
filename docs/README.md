# docs

Material de apoyo del repositorio.

- `panel_monitor.png`: el panel de la demo después de hacer fallar de verdad los tres workflows de ejemplo en n8n y enviar parte del día de avisos simulado.

## Grabar el video de la demo

1. `docker compose up -d` e importa `workflows/monitor_demo.json` y los tres
   workflows de `workflows/ejemplos/`. Activa los cuatro (ver README).
2. Abre <http://localhost:5678/webhook/monitor-demo/panel> y, a un lado, n8n
   en la pestaña *Executions*.
3. Haz fallar los ejemplos y recarga el panel después de cada paso:
   - `curl -X POST localhost:5678/webhook/falla-datos` cinco veces → una alerta y luego un escalado.
   - `curl -X POST localhost:5678/webhook/falla-credencial` → alerta alta por credenciales.
   - `curl -X POST localhost:5678/webhook/falla-pagos` → timeout, se marca para reintentar.
4. Guarda el archivo como `docs/video.mp4`. Luego, al editar el README en
   GitHub, arrastra el video donde dice `VIDEO` para que se reproduzca en la página.
