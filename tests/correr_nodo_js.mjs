// Carga las funciones del nodo Code del monitor fuera de n8n y las ejecuta.
// Uso:  node tests/correr_nodo_js.mjs <pedido.json>
// pedido = { avisos: [{llega, aviso}], dia, textos: [...] }
// Salida: { resultados, filas, resumen, limpios, panel }

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const fuente = readFileSync(join(ROOT, 'workflows', 'src', 'monitor.js'), 'utf8')
  .replace("'__MODO__'", "'error'");
const biblioteca = fuente.split('// ── Ejecución en n8n ──')[0] +
  'return { leerEvento, decidir, filaRegistro, resumenDia, limpiar, armarPanel };';
const f = new Function(biblioteca)();

const { avisos, dia, textos } = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const estado = {};
const resultados = [];
const filas = [];
for (const a of avisos) {
  const r = f.decidir(f.leerEvento(a.aviso), estado, a.llega);
  resultados.push(r);
  filas.push(f.filaRegistro(r, a.fecha));
}
estado.eventos = filas;
process.stdout.write(JSON.stringify({
  resultados, filas, resumen: f.resumenDia(filas, dia),
  limpios: (textos || []).map(f.limpiar), panel: f.armarPanel(estado, dia),
}));
