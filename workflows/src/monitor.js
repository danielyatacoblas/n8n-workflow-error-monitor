// Nodos Code de n8n del monitor de errores.
// Espejo en JavaScript de src/monitor.py (misma lógica, misma salida).
// La paridad entre ambos la verifica tests/test_paridad_js.py.
//
// scripts/build_workflow.py fija el marcador MODO:
//   'error'    clasifica un aviso del Error Trigger (o uno simulado por webhook)
//   'resumen'  arma el resumen del día desde el registro
//   'panel'    arma el panel HTML de la demo

const MODO = '__MODO__';

const PAUSA_CIERRE = 30 * 60;
const UMBRAL_ESCALAR = 5;
const RECORDATORIO = 4 * 60 * 60;
const MAX_REINTENTOS = 2;
const NIVELES = ['baja', 'media', 'alta', 'critica'];
const CRITICOS = ['Cobros · sincronizar pagos', 'Tienda · sincronizar stock'];
const SIN_REINTENTO = ['Newsletter · envío masivo'];
const MAX_EVENTOS = 300;       // eventos que guarda la demo para el panel

const CATEGORIAS = [
  ['transitorio', ['timeout', 'timed out', 'econnreset', 'econnaborted', 'econnrefused',
    'etimedout', 'socket hang up', 'too many requests', 'rate limit',
    'bad gateway', 'service unavailable', 'gateway timeout',
    'server is offline', 'temporarily', '#429', '#502', '#503', '#504']],
  ['credenciales', ['unauthorized', 'forbidden', 'invalid credentials', 'authentication',
    'token expired', 'token has expired', 'invalid_grant', 'api key',
    'permission', '#401', '#403']],
  ['datos', ['cannot read properties', 'is not a function', 'is not defined',
    'unexpected token', 'invalid json', 'invalid input', 'validation',
    'required field', 'typeerror']],
  ['configuracion', ['not found', 'does not exist', 'no node', 'missing', 'parameter',
    'could not find', '#404']],
];

const SEVERIDAD_BASE = { transitorio: 'baja', credenciales: 'alta', datos: 'media',
                         configuracion: 'alta', desconocido: 'media' };

const SUGERENCIA = {
  transitorio: 'El servicio externo no respondió a tiempo. Si sigue fallando, revisar ' +
    'si está caído o si se superó su límite de peticiones.',
  credenciales: 'Una credencial venció o no tiene permisos. Renovarla en n8n → ' +
    'Credentials; hasta entonces este workflow fallará en cada ejecución.',
  datos: 'Llegó un dato con un formato inesperado. Abrir la ejecución para ver qué ' +
    'entrada lo causó.',
  configuracion: 'Falta algo en la configuración del workflow: un nodo, un recurso o ' +
    'un parámetro.',
  desconocido: 'Abrir la ejecución para revisar el detalle.',
};

const SECRETOS = [
  /(bearer[ \t]+)[A-Za-z0-9._~+/=-]+/gi,
  /((?:api[_-]?key|token|secret|password|clave|access_token)["']?[ \t]*[:=][ \t]*["']?)[^ \t\r\n"'&,}]+/gi,
  /(https?:\/\/[^ \t\r\n:/@]+:)[^ \t\r\n@/]+(?=@)/gi,
];

// Cortar por caracteres y no por unidades UTF-16, como hace Python con [:n]
const cortar = (s, n) => Array.from(s).slice(0, n).join('');

// ── lectura y limpieza ──

function limpiar(texto) {
  let t = String(texto === null || texto === undefined ? '' : texto);
  for (const patron of SECRETOS) t = t.replace(patron, '$1***');
  return t;
}

function leerEvento(aviso) {
  if (aviso.body && typeof aviso.body === 'object' && 'workflow' in aviso.body) aviso = aviso.body;
  const ejecucion = aviso.execution || {};
  const disparador = aviso.trigger || {};
  const error = ejecucion.error || disparador.error || {};
  const nodo = (error.node && typeof error.node === 'object' && !Array.isArray(error.node)) ? error.node : {};
  const mensajes = Array.isArray(error.messages) ? error.messages : [];
  const workflow = aviso.workflow || {};
  const tieneDisparador = Object.keys(disparador).length > 0;
  return {
    workflow_id: String(workflow.id || ''),
    workflow: String(workflow.name || '(sin nombre)'),
    ejecucion_id: String(ejecucion.id || ''),
    url: String(ejecucion.url || ''),
    nodo: String(nodo.name || ejecucion.lastNodeExecuted || (tieneDisparador ? '(disparador)' : '')),
    tipo_error: String(error.name || ''),
    mensaje: cortar(limpiar(error.message || '(sin mensaje)'), 300),
    detalle: cortar(limpiar(error.description || (mensajes.length ? mensajes[0] : '')), 300),
    codigo: String(error.httpCode || ''),
    reintento_de: String(ejecucion.retryOf || ''),
    modo: String(ejecucion.mode || disparador.mode || ''),
  };
}

// ── clasificación ──

function clasificar(e) {
  const texto = [e.mensaje, e.detalle, e.codigo, e.tipo_error].join(' ').toLowerCase();
  const codigos = new Set([...texto.matchAll(/(?<![0-9])([0-9]{3})(?![0-9])/g)].map((m) => '#' + m[1]));
  for (const [categoria, patrones] of CATEGORIAS) {
    for (const p of patrones) {
      if (p.startsWith('#') ? codigos.has(p) : texto.includes(p)) return categoria;
    }
  }
  return 'desconocido';
}

function severidad(categoria, workflow, subir = 0) {
  let nivel = NIVELES.indexOf(SEVERIDAD_BASE[categoria]) + subir;
  if (CRITICOS.includes(workflow)) nivel += 1;
  return NIVELES[Math.min(nivel, NIVELES.length - 1)];
}

function huella(e) {
  let m = e.mensaje.toLowerCase();
  m = m.replace(/https?:\/\/[^ \t\r\n]+/g, '<url>');
  m = m.replace(/[a-z0-9_.+-]+@[a-z0-9_-]+\.[a-z0-9_.]+/g, '<email>');
  m = m.replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, '<id>');
  m = m.replace(/'[^']*'|"[^"]*"/g, '<txt>');
  m = m.replace(/[0-9]+/g, '<n>');
  m = cortar(m.replace(/[ \t\r\n]+/g, ' ').replace(/^ +| +$/g, ''), 160);
  return `${e.workflow_id}|${e.nodo}|${m}`;
}

// ── decisión ──

function decidir(e, estado, ahora) {
  estado.grupos = estado.grupos || {};
  estado.cadenas = estado.cadenas || {};
  const { grupos, cadenas } = estado;
  const categoria = clasificar(e);
  const h = huella(e);

  // Un reintento cuyo origen no está en la memoria se da por agotado: sin
  // esta regla, un error que nunca se resuelve se reintentaría para siempre.
  const conocido = !e.reintento_de || Object.prototype.hasOwnProperty.call(cadenas, e.reintento_de);
  const n = !e.reintento_de ? 0 : conocido ? cadenas[e.reintento_de] + 1 : MAX_REINTENTOS;
  if (e.ejecucion_id) cadenas[e.ejecucion_id] = n;

  // Un incidente sigue abierto mientras el error se repita sin pausas largas
  let g = grupos[h];
  let anterior = 0;
  if (!g || ahora - g.ultima >= PAUSA_CIERRE) {
    if (g) anterior = g.conteo;
    g = { inicio: ahora, ultima: ahora, conteo: 0, total: g ? g.total : 0,
          ultimo_aviso: 0, alertado: false, escalado: false,
          workflow: e.workflow, nodo: e.nodo, categoria, mensaje: e.mensaje };
    grupos[h] = g;
  }
  g.conteo += 1;
  g.total += 1;
  g.ultima = ahora;

  const reintentar = categoria === 'transitorio' && n < MAX_REINTENTOS &&
    Boolean(e.ejecucion_id) && e.modo !== 'manual' && !SIN_REINTENTO.includes(e.workflow);
  let aviso = '';
  if (g.conteo >= UMBRAL_ESCALAR && !g.escalado) aviso = 'escalado';
  else if (!reintentar && !g.alertado) aviso = 'alerta';
  else if (g.alertado && ahora - g.ultimo_aviso >= RECORDATORIO) aviso = 'recordatorio';
  if (aviso) {
    g.alertado = true;
    g.escalado = g.escalado || aviso === 'escalado';
    g.ultimo_aviso = ahora;
  }

  const accion = aviso
    ? { alerta: 'alertar', escalado: 'escalar', recordatorio: 'recordar' }[aviso]
    : reintentar ? 'reintentar' : 'silenciar';
  const sev = severidad(categoria, e.workflow, aviso === 'escalado' ? 1 : 0);
  const r = { accion, reintentar, aviso, categoria, severidad: sev, huella: h,
              conteo: g.conteo, reintento: n, evento: e, mensaje: '' };
  if (aviso) {
    r.mensaje = mensajeAlerta(e, categoria, sev, g, conocido ? n : -1, anterior, aviso, reintentar);
  }
  return r;
}

function duracion(segundos) {
  const minutos = Math.max(1, Math.floor((segundos + 59) / 60));
  if (minutos < 60) return `${minutos} min`;
  return `${Math.floor(minutos / 60)} h ${String(minutos % 60).padStart(2, '0')} min`;
}

function mensajeAlerta(e, categoria, sev, g, n, anterior, aviso, reintentar) {
  const titulo = { alerta: '', escalado: 'Se repite: ', recordatorio: 'Sigue fallando: ' }[aviso];
  const lineas = [`[${sev.toUpperCase()}] ${titulo}${e.workflow}`];
  if (aviso !== 'alerta') {
    lineas.push(`${g.conteo} veces en ${duracion(g.ultima - g.inicio)}` +
      (reintentar ? ' · cada ejecución se sigue reintentando sola.' : '.'));
  }
  lineas.push(`Nodo: ${e.nodo || '(desconocido)'}`);
  lineas.push(`Error: ${e.mensaje}${e.codigo ? ` (${e.codigo})` : ''}`);
  let tipo = `Tipo: ${categoria}`;
  if (n > 0) {
    const s = n > 1 ? 's' : '';
    tipo += ` · falló también tras ${n} reintento${s} automático${s}`;
  } else if (n < 0) {
    tipo += ' · es un reintento y volvió a fallar';
  }
  lineas.push(tipo);
  if (anterior > 1) lineas.push(`Ya había pasado antes: el incidente anterior sumó ${anterior} veces.`);
  lineas.push(`Qué hacer: ${SUGERENCIA[categoria]}`);
  if (e.url) lineas.push(`Ejecución: ${e.url}`);
  return lineas.join('\n');
}

function filaRegistro(r, fecha) {
  const e = r.evento;
  return { fecha, workflow: e.workflow, nodo: e.nodo, categoria: r.categoria,
           severidad: r.severidad, accion: r.accion, mensaje: e.mensaje, codigo: e.codigo,
           ejecucion: e.ejecucion_id, reintento: r.reintento,
           reintentado: r.reintentar ? 'si' : 'no', huella: r.huella };
}

// ── resumen del día ──

function resumenDia(filas, dia) {
  const hoy = filas.filter((f) => String(f.fecha || '').slice(0, 10) === dia);
  if (!hoy.length) return `Errores del ${dia}: ninguno. Todos los workflows funcionaron.`;
  const porWorkflow = {};
  for (const f of hoy) {
    const k = String(f.workflow || '?');
    porWorkflow[k] = porWorkflow[k] || { total: 0, reintentados: 0 };
    porWorkflow[k].total += 1;
    if (f.reintentado === 'si') porWorkflow[k].reintentados += 1;
  }
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const nombres = Object.keys(porWorkflow);
  const lineas = [`Errores del ${dia}: ${hoy.length} en ${nombres.length} workflow` +
    `${nombres.length > 1 ? 's' : ''}.`];
  for (const [nombre, w] of Object.entries(porWorkflow)
    .sort((a, b) => (b[1].total - a[1].total) || cmp(a[0], b[0]))) {
    lineas.push(`- ${nombre}: ${w.total}${w.reintentados ? ` (${w.reintentados} reintentados)` : ''}`);
  }
  const categorias = {};
  for (const f of hoy) {
    const c = String(f.categoria || '?');
    categorias[c] = (categorias[c] || 0) + 1;
  }
  lineas.push('Por tipo: ' + Object.entries(categorias)
    .sort((a, b) => (b[1] - a[1]) || cmp(a[0], b[0]))
    .map(([c, n]) => `${c} ${n}`).join(', '));
  return lineas.join('\n');
}

// ── panel HTML (solo demo) ──

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function armarPanel(estado, dia) {
  const eventos = estado.eventos || [];
  const grupos = Object.values(estado.grupos || {}).sort((a, b) => b.ultima - a.ultima);
  const colores = { baja: '#6b7480', media: '#b4561b', alta: '#b3261e', critica: '#7a0f0a' };
  const acciones = { reintentar: 'reintentado', alertar: 'alerta enviada', escalar: 'escalado',
                     recordar: 'recordatorio', silenciar: 'contado sin avisar' };
  const conteo = (a) => eventos.filter((e) => e.accion === a).length;
  const filas = grupos.map((g) => `<tr>
      <td>${esc(g.workflow)}<div class="sub">${esc(g.nodo)}</div></td>
      <td><span class="cat">${esc(g.categoria)}</span></td>
      <td class="msg">${esc(g.mensaje)}</td>
      <td class="num">${g.total}</td></tr>`).join('');
  const ultimos = eventos.slice(-12).reverse().map((e) => `<li>
      <b style="color:${colores[e.severidad]}">${esc(e.severidad)}</b>
      ${esc(e.workflow)} · ${esc(e.nodo)} — <i>${esc(acciones[e.accion] || e.accion)}</i></li>`).join('');
  const tarjeta = (t, v) => `<div class="kpi"><div class="sub">${esc(t)}</div><b>${v}</b></div>`;
  return `<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Monitor de errores</title>
<style>
:root{--bg:#f2f3f5;--panel:#fff;--t:#1c2530;--s:#66727f;--b:#dde1e6}
@media (prefers-color-scheme:dark){:root{--bg:#11161c;--panel:#1a2129;--t:#e6ebf0;--s:#9aa6b2;--b:#2b3540}}
body{margin:0;background:var(--bg);color:var(--t);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:22px 16px;display:grid;gap:16px}
section{background:var(--panel);border:1px solid var(--b);border-radius:12px;padding:16px;overflow-x:auto}
h1{font-size:20px;margin:0}h2{font-size:12px;color:var(--s);text-transform:uppercase;letter-spacing:.05em;margin:0 0 10px}
.sub{color:var(--s);font-size:12px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{border:1px solid var(--b);border-radius:10px;padding:10px}.kpi b{font-size:24px}
table{width:100%;border-collapse:collapse}td{padding:8px 6px;border-top:1px solid var(--b);vertical-align:top}
.msg{font-family:ui-monospace,Consolas,monospace;font-size:12px}.num{text-align:right;font-weight:700}
.cat{font-size:12px;border:1px solid var(--b);border-radius:99px;padding:2px 8px}
ul{margin:0;padding-left:18px;display:grid;gap:4px}pre{white-space:pre-wrap;margin:0;font-size:13px}
</style></head><body><main>
<section><h1>Monitor de errores de n8n</h1><div class="sub">Actualizado ${esc(dia)} · un incidente sigue abierto mientras el mismo error se repita con menos de 30 minutos de pausa</div></section>
<section class="kpis">
${tarjeta('Errores recibidos', eventos.length)}${tarjeta('Mensajes al equipo', conteo('alertar') + conteo('escalar') + conteo('recordar'))}
${tarjeta('Reintentados solos', eventos.filter((e) => e.reintentado === 'si').length)}${tarjeta('Contados sin avisar', conteo('silenciar'))}
</section>
<section><h2>Errores agrupados</h2><table>${filas || '<tr><td>Sin errores todavía.</td></tr>'}</table></section>
<section><h2>Últimos eventos</h2><ul>${ultimos || '<li>Sin eventos.</li>'}</ul></section>
<section><h2>Resumen del día (lo que se enviaría a las 18:00)</h2><pre>${esc(resumenDia(eventos, dia))}</pre></section>
</main></body></html>`;
}

// ── Ejecución en n8n ──

const memoria = $getWorkflowStaticData('global');
const ahora = Math.floor(Date.now() / 1000);
const hoy = $now.toISODate();

if (MODO === 'panel') {
  return [{ json: { html: armarPanel(memoria, hoy) } }];
}

if (MODO === 'resumen') {
  // En producción el registro está en la hoja; en la demo, en la memoria
  let filas;
  try { filas = $('Sheets · Leer registro').all().map((i) => i.json); }
  catch (e) { filas = memoria.eventos || []; }
  return [{ json: { texto: resumenDia(filas, hoy) } }];
}

return $input.all().map((item) => {
  const r = decidir(leerEvento(item.json), memoria, ahora);
  const fila = filaRegistro(r, $now.toISO());
  memoria.eventos = (memoria.eventos || []).concat([fila]).slice(-MAX_EVENTOS);
  // Las cadenas de reintentos viejas no sirven: se guardan solo las últimas 500
  const ids = Object.keys(memoria.cadenas);
  if (ids.length > 500) for (const id of ids.slice(0, ids.length - 500)) delete memoria.cadenas[id];
  return { json: Object.assign(r, { fila }) };
});
