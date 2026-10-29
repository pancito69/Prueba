'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const numeric = value => value === null || value === undefined || String(value).trim() === '' ? NaN : Number(String(value).trim().replace(',', '.'));
const number = value => value === null || value === undefined || value === '' || !Number.isFinite(numeric(value)) ? '—' : new Intl.NumberFormat('es-PE', {maximumFractionDigits: 6}).format(numeric(value));
const integer = value => new Intl.NumberFormat('es-PE').format(value || 0);
const date = value => value ? new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`).toLocaleString('es-PE', {dateStyle:'medium',timeStyle:'short',timeZone:'America/Lima'}) : 'Fecha no disponible';
const statusNames = {valid:'Calculados sin alertas',review:'Por revisar',pending:'Pendiente',approved:'Aprobado'};
const verificationNames={unverified:'Sin evidencia verificada',declared_reference:'Referencia declarada',evidence_registered:'Evidencia registrada',approved:'Aprobación humana'};
const basisNames={billed_vs_proposed:'Cobro declarado → propuesta',billed_vs_calculated:'Cobro declarado → cálculo',calculated_vs_proposed:'Cálculo del catálogo → propuesta',unknown:'Sin base de comparación'};
const sourceNames = {medicion_logistica:'Medición logística',fabricante:'Fabricante',tienda_oficial:'Tienda oficial',otro_seller:'Otro seller'};
const historyNames = {catalog_updated:'Catálogo actualizado',proposal_created:'Propuesta registrada',evidence_stale:'Evidencia desactualizada',rules_changed:'Criterio de talla actualizado',approved:'Corrección aprobada',rejected:'Propuesta rechazada'};
const proposalNames = {pending:'Por aprobar',proposed:'Por aprobar',approved:'Aprobada',rejected:'Rechazada',stale:'Obsoleta',superseded:'Reemplazada'};
const icons = {
  chart:'<path d="M3 3v18h18M7 16v-4m5 4V7m5 9V4"/>',
  box:'<path d="m21 7-9-5-9 5v10l9 5 9-5Z"/><path d="m3 7 9 5 9-5M12 12v10M7.5 4.5l9 5"/>',
  grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  'check-square':'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m8 12 3 3 6-6"/>',
  upload:'<path d="M12 16V3m-4 4 4-4 4 4M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  sliders:'<path d="M4 7h7m6 0h3M4 17h3m6 0h7"/><circle cx="14" cy="7" r="3"/><circle cx="10" cy="17" r="3"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  'chevron-right':'<path d="m9 5 7 7-7 7"/>', 'chevron-left':'<path d="m15 5-7 7 7 7"/>',
  sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4m-2-2h4"/>',
  plus:'<path d="M12 5v14M5 12h14"/>', 'check-circle':'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  flag:'<path d="M5 22V3c5-4 9 4 14 0v11c-5 4-9-4-14 0"/>',
  'file-check':'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6m-12 7 3 3 5-6"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  download:'<path d="M12 3v12m-4-4 4 4 4-4M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',x:'<path d="m6 6 12 12M18 6 6 18"/>',
  external:'<path d="M15 3h6v6m0-6-10 10M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.box}</svg>`;
function hydrateIcons(root = document) { $$('[data-icon]', root).forEach(el => { el.innerHTML = icon(el.dataset.icon); }); }
function badge(status) { return `<span class="status ${Object.hasOwn(statusNames,status)?status:'pending'}">${esc(statusNames[status] || status || 'Pendiente')}</span>`; }
function sizeBadge(size) { return `<span class="size-badge ${size?'':'empty'}">${esc(size || '—')}</span>`; }
function directionBadge(row) {
  const direction=['up','down','same'].includes(row.direction)?row.direction:'unknown';
  if(direction==='unknown')return '<div class="direction-cell unknown"><strong>— Sin base</strong><small>No hay dos tallas comparables</small></div>';
  const symbol={up:'↑',down:'↓',same:'='}[direction];
  const label={up:'Sube',down:'Baja',same:'Misma talla'}[direction];
  return `<div class="direction-cell ${direction}"><strong>${symbol} ${esc(row.comparison_from_size || '—')} → ${esc(row.comparison_to_size || '—')}</strong><small>${esc(basisNames[row.direction_basis] || label+' · tallas disponibles')}</small></div>`;
}
function verificationBadge(value){return `<small class="verification-label">${esc(verificationNames[value] || verificationNames.unverified)}</small>`;}
function safeLink(url) { try { const parsed = new URL(url); return ['https:','http:'].includes(parsed.protocol) ? parsed.href : null; } catch { return null; } }

const state = {view:'catalogo',reviewMode:'sku',groupPage:1,direction:'all',reason:'all',status:'all',page:1,pageSize:25,search:'',total:0,pages:1,record:null,proposal:null,upload:null,jobId:null,jobTimer:null,listRequest:0,summary:null};
let toastTimer, searchTimer, modalOpener;

async function api(path, options = {}) {
  const response = await fetch(path,{...options,headers:{...(options.body && !(options.body instanceof FormData)?{'Content-Type':'application/json'}:{}),...options.headers}});
  if (!response.ok) {
    let detail;
    try { detail=(await response.json()).detail; } catch { detail=null; }
    if (Array.isArray(detail)) detail=detail.map(item=>`${item.loc?.slice(1).join('.') || 'Dato'}: ${item.msg}`).join('; ');
    throw new Error(typeof detail === 'string' ? detail : `No se pudo completar la operación (${response.status}).`);
  }
  return response.status === 204 ? null : response.json();
}
function toast(message, error = false) {
  clearTimeout(toastTimer); const el=$('#toast'); el.textContent=message; el.classList.toggle('error',error); el.hidden=false;
  toastTimer=setTimeout(()=>el.hidden=true,error?9000:5000);
}
function openModal(kind) { modalOpener=document.activeElement; const overlay=$(`#${kind}-overlay`); overlay.hidden=false; document.body.style.overflow='hidden'; $('section',overlay).focus(); }
function closeModal(kind) { $(`#${kind}-overlay`).hidden=true; if ($$('.overlay').every(el=>el.hidden)) document.body.style.overflow=''; modalOpener?.focus(); }

async function loadSummary() {
  const summary=await api('/api/summary'); state.summary=summary;
  $('#metric-total').textContent=integer(summary.total); $('#metric-valid').textContent=integer(summary.valid);
  $('#metric-review').textContent=integer((summary.review || 0)+(summary.pending || 0)); $('#metric-approved').textContent=integer(summary.approved);
  $('#nav-count').textContent=integer(summary.total); $('#review-count').textContent=integer(summary.review);
  $('#pending-description').textContent=`${integer(summary.review)} por revisar · ${integer(summary.pending)} pendientes`;
  $('#up-count').textContent=integer(summary.directions?.up);
  $('#rule-tag').textContent=summary.rule_mode==='upper_bounds'?'Límites continuos':'Tabla literal';
}
async function loadSkus() {
  const request=++state.listRequest;
  const params=new URLSearchParams({page:state.page,page_size:state.pageSize,search:state.search,status:state.status,direction:state.direction,reason:state.reason});
  const data=await api(`/api/skus?${params}`); if(request!==state.listRequest)return;
  state.total=data.total; state.pages=Math.max(1,data.pages || Math.ceil(data.total/state.pageSize)); state.page=data.page || state.page;
  $('#table-count').textContent=integer(data.total);
  const filtered=Boolean(state.search || state.status!=='all' || state.direction!=='all' || state.reason!=='all');
  $('#sku-body').innerHTML=data.items.length ? data.items.map(row=>`<tr data-id="${esc(row.id)}"><td><div class="product-cell"><span class="product-tile">${icon('box')}</span><div><span class="product-name" title="${esc(row.name || row.sku)}">${esc(row.name || 'Producto sin nombre')}</span><span class="product-sku">SKU ${esc(row.sku)}</span></div></div></td><td><span class="seller-name" title="${esc(row.seller)}">${esc(row.seller)}</span></td><td class="dim-text">${number(row.height_cm)} × ${number(row.length_cm)} × ${number(row.width_cm)}</td><td class="weight-cell"><strong>${number(row.charge_kg)}${row.charge_kg!=null?' kg':''}</strong><small>Real: ${number(numeric(row.weight_g)/1000)} kg</small></td><td>${sizeBadge(row.size)}</td><td>${directionBadge(row)}</td><td>${badge(row.status)}${verificationBadge(row.verification)}</td><td><button class="row-arrow" data-detail="${esc(row.id)}" aria-label="Revisar SKU ${esc(row.sku)}">${icon('chevron-right')}</button></td></tr>`).join('') : `<tr><td colspan="8" class="empty-state"><div class="empty-illustration">${icon(filtered?'search':'box')}</div><h3>${filtered?'No hay registros con este filtro':'Cada talla correcta empieza con tus datos'}</h3><p>${filtered?'Prueba otra búsqueda o cambia los filtros para encontrar más SKUs.':'Carga tu catálogo en CSV o Excel para calcular tallas y encontrar oportunidades de corrección. También puedes explorar una demo con datos ficticios.'}</p><div class="empty-actions">${filtered?'<button class="button secondary" data-action="clear-filters">Limpiar filtros</button>':'<button class="button primary" data-action="upload">Cargar catálogo</button><button class="button secondary" data-action="demo">Explorar demo</button>'}</div></td></tr>`;
  $('#pagination-label').textContent=data.total?`${integer((state.page-1)*state.pageSize+1)}–${integer(Math.min(state.page*state.pageSize,data.total))} de ${integer(data.total)} SKUs`:'0 registros';
  $('#page-label').textContent=`${state.page} / ${state.pages}`; $('#prev-page').disabled=state.page<=1; $('#next-page').disabled=state.page>=state.pages;
}
async function loadCurrentList(){if(state.view==='revisiones'&&state.reviewMode==='groups')await loadGroups();else await loadSkus();}
async function refresh() {
  await Promise.all([loadSummary(),loadCurrentList()]);
  if(state.view==='diagnostico')await loadDiagnostics();
}
function syncFilters(){
  $('#direction-filter').value=state.direction;$('#reason-filter').value=state.reason;
  $('#prioritize-up').classList.toggle('active',state.direction==='up');
}
async function setView(view) {
  state.view=view; const catalog=['catalogo','revisiones'].includes(view);
  $('#catalog-view').hidden=!catalog; $('#jobs-view').hidden=view!=='cargas'; $('#rules-view').hidden=view!=='reglas';$('#diagnostics-view').hidden=view!=='diagnostico';
  $$('.nav-item').forEach(el=>{el.classList.toggle('active',el.dataset.view===view); if(el.dataset.view===view)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  $('#breadcrumb').textContent=({catalogo:'Catálogo',revisiones:'Revisiones',cargas:'Cargas',reglas:'Reglas de talla',diagnostico:'Diagnóstico'})[view];
  if(catalog) {
    $('#catalog-title').innerHTML=view==='revisiones'?'Cada corrección, con evidencia<span>.</span>':'Tu catálogo, bajo control<span>.</span>';
    $('#catalog-description').textContent=view==='revisiones'?'Prioriza las posibles subidas y revisa las propuestas por SKU o por grupo.':'Calcula tallas, identifica inconsistencias y corrige con evidencia.';
    $('#table-title').innerHTML=`${view==='revisiones'?'Bandeja de revisión':'Catálogo de SKUs'} <span id="table-count" class="counter">0</span>`;
    $('#review-mode').hidden=view!=='revisiones';
    const groups=view==='revisiones'&&state.reviewMode==='groups';
    $('#sku-review').hidden=groups;$('#group-review').hidden=!groups;$('#reason-filter-label').hidden=groups;$('#table-subtitle').textContent=groups?'Selecciona propuestas que compartan evidencia y revisa su compatibilidad antes de decidir.':'Revisa el resultado y abre un SKU para consultar su evidencia.';
    setStatus(view==='revisiones'?'review':'all',false);syncFilters(); await refresh();
  }
  if(view==='cargas')await loadJobs();if(view==='reglas')await loadRules();if(view==='diagnostico')await loadDiagnostics();
}
async function setReviewMode(mode){
  state.reviewMode=mode;state.groupPage=1;
  $$('[data-review-mode]').forEach(el=>el.classList.toggle('active',el.dataset.reviewMode===mode));
  const groups=mode==='groups';$('#sku-review').hidden=groups;$('#group-review').hidden=!groups;$('#reason-filter-label').hidden=groups;$('#table-subtitle').textContent=groups?'Selecciona propuestas que compartan evidencia y revisa su compatibilidad antes de decidir.':'Revisa el resultado y abre un SKU para consultar su evidencia.';
  await loadCurrentList();
}
async function applyDiagnosticFilter(reason='all',direction='all'){
  state.reason=reason;state.direction=direction;state.page=1;state.search='';$('#search-input').value='';
  await setView('catalogo');
}
function setStatus(status,load=true) { state.status=status;state.page=1;$$('[data-status]').forEach(el=>el.classList.toggle('active',el.dataset.status===status));if(load)loadSkus().catch(error=>toast(error.message,true)); }

async function openDetail(id) {
  $('#drawer-content').innerHTML='<div class="loading-cell"><span class="spinner"></span><p>Cargando registro…</p></div>'; openModal('drawer');
  try { const data=await api(`/api/skus/${encodeURIComponent(id)}`); renderDetail(data); } catch(error) { $('#drawer-content').innerHTML=`<p class="inline-error">${esc(error.message)}</p>`; }
}
function renderDetail(data) {
  const row=data.record, p=data.proposal, original=data.original || row, originalCalc=data.original_calculated || row; state.record=row;state.proposal=p;
  const canDecide=p && !['approved','rejected','stale','superseded'].includes(p.state);
  const source=p? safeLink(p.source_url):null;
  $('#drawer-content').innerHTML=`<h2 id="drawer-title">${esc(row.name || row.sku)}</h2><p class="detail-meta">${esc(row.seller)} · SKU <code>${esc(row.sku)}</code><br>${esc([row.brand,row.model,row.variant].filter(Boolean).join(' · '))}${row.gtin?`<br>GTIN/EAN: ${esc(row.gtin)}`:''}${row.presentation?`<br>Presentación: ${esc(row.presentation)}`:''}${row.packaging?`<br>Empaque: ${esc(row.packaging)}`:''}</p>${badge(row.status)}${verificationBadge(row.verification)}<section class="detail-section"><h3>Comparación de tallas</h3>${directionBadge(row)}<p class="muted-copy" style="margin-top:12px">Talla de cobro declarada: <strong>${esc(row.billed_size || 'No informada')}</strong>. La dirección indica una diferencia entre tallas disponibles; no certifica medidas ni confirma un cobro incorrecto.</p></section><section class="detail-section"><h3>${row.status==='approved'?'Datos aprobados vigentes':'Datos del catálogo'}</h3><dl class="detail-grid"><div><dt>Peso real</dt><dd>${number(row.weight_g)} g</dd></div><div><dt>Alto × largo × ancho</dt><dd>${number(row.height_cm)} × ${number(row.length_cm)} × ${number(row.width_cm)} cm</dd></div><div><dt>Peso volumétrico</dt><dd>${number(row.volume_kg)} kg</dd></div><div><dt>Peso aplicable</dt><dd>${number(row.charge_kg)} kg</dd></div><div class="calculated"><dt>Talla de cobro calculada</dt><dd>${sizeBadge(row.size)}</dd></div></dl></section>${row.flags?.length?`<section class="detail-section"><h3>Qué necesita atención</h3><ul class="flag-list">${row.flags.map(flag=>`<li>${esc(flag)}</li>`).join('')}</ul></section>`:''}${row.observations?.length?`<section class="detail-section"><h3>Observaciones del empaque</h3><p class="muted-copy">Estas observaciones no cambian por sí solas el estado del SKU.</p><ul class="observation-list">${row.observations.map(item=>`<li>${esc(item)}</li>`).join('')}</ul><p class="muted-copy">Comprueba si las medidas describen el producto desplegado, su caja o el paquete de envío. Una dimensión grande puede ser correcta.</p></section>`:''}${p?`<section class="detail-section"><div class="section-heading"><h3>Propuesta de corrección</h3><span class="subtle-tag">${esc(proposalNames[p.state] || p.state)}</span></div><p class="muted-copy">${esc(p.reason || 'Corrección respaldada por evidencia registrada.')}</p><table class="comparison"><thead><tr><th>Dato</th><th>Catálogo</th><th>Propuesta</th></tr></thead><tbody>${[['Peso real (g)','weight_g'],['Alto (cm)','height_cm'],['Largo (cm)','length_cm'],['Ancho (cm)','width_cm']].map(([label,key])=>`<tr><td>${label}</td><td>${number(original[key])}</td><td>${number(p[key])}</td></tr>`).join('')}<tr><td>Talla</td><td>${esc(originalCalc.size || 'Pendiente')}</td><td>${esc(p.size || 'Pendiente')}</td></tr></tbody></table><p class="mini-label">${esc(sourceNames[p.source_kind] || p.source_kind || 'Evidencia')}</p>${source?`<a class="source-link" href="${esc(source)}" target="_blank" rel="noopener noreferrer">Consultar fuente ${icon('external')}</a>`:'<p class="muted-copy">Fuente registrada sin enlace web.</p>'}<p class="evidence-note">${esc(p.evidence_note || '')}</p>${canDecide?`<form id="decision-form"><label class="field">Nota de revisión<input name="note" placeholder="Observación o motivo de rechazo"></label><label class="check-field"><input type="checkbox" id="approve-attestation">He comprobado la evidencia y que corresponde al mismo producto, presentación y empaque de envío.</label><div class="form-actions"><button type="button" class="button danger" data-decision="reject">Rechazar</button><button type="button" class="button primary" data-decision="approve">Aprobar corrección</button></div></form>`:''}</section>`:''}<section class="detail-section"><h3>${p?'Registrar otra propuesta':'Proponer una corrección'}</h3><p class="muted-copy">Registra medidas respaldadas por una fuente comprobable. Este piloto no realiza búsquedas con IA ni consulta sitios externos automáticamente.</p><form id="proposal-form"><div class="form-grid" style="margin-top:16px">${[['Peso real (g)','weight_g'],['Alto (cm)','height_cm'],['Largo (cm)','length_cm'],['Ancho (cm)','width_cm']].map(([label,key])=>`<label class="field">${label}<input type="number" name="${key}" min="0.000001" step="any" required value="${esc(row[key] ?? '')}"></label>`).join('')}<label class="field full">Tipo de fuente<select name="source_kind">${Object.entries(sourceNames).map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}</select></label><label class="field full">Enlace a la fuente<input name="source_url" type="url" required minlength="8" placeholder="https://…"></label><label class="field full">Evidencia y compatibilidad del empaque<textarea name="evidence_note" required minlength="15" placeholder="Describe la medición o cita el dato y explica por qué corresponde al mismo empaque."></textarea></label><label class="field full">Motivo de corrección<input name="reason" required minlength="5" placeholder="Ej. Alto incorrecto frente a medición logística"></label></div><label class="check-field"><input type="checkbox" required>La evidencia corresponde al mismo producto, presentación y empaque de envío.</label><div class="form-actions"><button class="button primary" type="submit">Guardar propuesta</button></div><p class="inline-error" id="proposal-error" role="alert" hidden></p></form></section><section class="detail-section"><h3>Historial del registro</h3>${data.history?.length?`<ol class="timeline">${data.history.map(item=>`<li><strong>${esc(historyNames[item.action] || item.action)}</strong><time>${esc(date(item.created_at))}</time><p>${esc(typeof item.detail==='object'?JSON.stringify(item.detail):item.detail || '')}</p></li>`).join('')}</ol>`:'<p class="muted-copy">Aún no hay cambios registrados.</p>'}</section>`;
  $('.drawer').scrollTop=0;
  $('#proposal-form').addEventListener('submit',submitProposal);
  $$('[data-decision]').forEach(button=>button.addEventListener('click',()=>decide(button.dataset.decision)));
}
async function submitProposal(event) {
  event.preventDefault();const form=event.currentTarget,button=$('button[type="submit"]',form);button.disabled=true;
  const data=Object.fromEntries(new FormData(form)); // Keep decimal strings exact for the rule engine.
  try { await api(`/api/skus/${encodeURIComponent(state.record.id)}/proposal`,{method:'POST',body:JSON.stringify(data)});renderDetail(await api(`/api/skus/${encodeURIComponent(state.record.id)}`));await refresh();toast('Propuesta guardada. Revisa la evidencia antes de aprobar.'); }
  catch(error) {$('#proposal-error').textContent=error.message;$('#proposal-error').hidden=false;button.disabled=false;}
}
async function decide(decision) {
  if(decision==='approve' && !$('#approve-attestation').checked){toast('Confirma que revisaste la evidencia y el empaque antes de aprobar.',true);return;}
  const note=$('[name="note"]',$('#decision-form')).value.trim();
  if(decision==='reject'&&!note){toast('Registra el motivo del rechazo.',true);return;}
  const form=$('#decision-form');form.classList.add('busy');
  try {await api(`/api/proposals/${encodeURIComponent(state.proposal.id)}/decision`,{method:'POST',body:JSON.stringify({decision,note})});renderDetail(await api(`/api/skus/${encodeURIComponent(state.record.id)}`));await refresh();toast(decision==='approve'?'Corrección aprobada. Ya está disponible para exportar.':'Propuesta rechazada. El dato original se conserva.');}
  catch(error){form.classList.remove('busy');toast(error.message,true);}
}

function showUpload() {
  state.upload=null;state.jobId=null;
  $('#upload-content').innerHTML=`<p class="step-description">Carga un catálogo inicial o una actualización. Los registros se identifican por seller y SKU; volver a cargar el mismo archivo no duplica los SKUs.</p><label class="dropzone" id="dropzone" tabindex="0"><span class="empty-illustration">${icon('upload')}</span><strong>Arrastra tu catálogo aquí</strong><p>CSV o Excel (.xlsx) · Peso en gramos · Dimensiones en centímetros</p><span class="file-label">Seleccionar archivo</span><input class="sr-only" type="file" id="file-input" accept=".csv,.xlsx"></label><div class="upload-help"><span>En el siguiente paso podrás asociar las columnas.</span><a href="/api/template.csv">${icon('download')} Descargar plantilla</a></div><div class="small-notice">El archivo original se conserva. Las referencias solo se utilizan si confirmas que están verificadas y describen el empaque de envío.</div><p id="upload-error" class="inline-error" role="alert" hidden></p>`;
  openModal('upload');const input=$('#file-input'),zone=$('#dropzone');
  input.addEventListener('change',()=>{if(input.files[0])previewFile(input.files[0]);});
  zone.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();input.click();}});
  ['dragenter','dragover'].forEach(type=>zone.addEventListener(type,event=>{event.preventDefault();zone.classList.add('dragging');}));
  ['dragleave','drop'].forEach(type=>zone.addEventListener(type,event=>{event.preventDefault();zone.classList.remove('dragging');}));
  zone.addEventListener('drop',event=>{if(event.dataTransfer.files[0])previewFile(event.dataTransfer.files[0]);});
}
async function previewFile(file) {
  if(!/\.(csv|xlsx)$/i.test(file.name)){toast('Selecciona un archivo CSV o XLSX.',true);return;}
  $('#dropzone').classList.add('busy');$('#dropzone').innerHTML=`<span class="spinner"></span><p>Preparando ${esc(file.name)}…</p><small>La carga continúa mientras esta ventana esté abierta.</small>`;
  const form=new FormData();form.append('file',file);
  try {state.upload=await api('/api/uploads/preview',{method:'POST',body:form});renderMapping();}
  catch(error){showUpload();$('#upload-error').textContent=error.message;$('#upload-error').hidden=false;}
}
const fields=[['seller','Seller',true],['sku','SKU',true],['name','Nombre del producto'],['gtin','GTIN / EAN'],['brand','Marca'],['model','Modelo'],['variant','Variante'],['presentation','Presentación / pack'],['packaging','Tipo de empaque'],['billed_size','Talla de cobro actual (cooperados)'],['weight_g','Peso real (g)',true],['height_cm','Alto (cm)',true],['length_cm','Largo (cm)',true],['width_cm','Ancho (cm)',true],['reference_verified','Referencia verificada'],['source_url','URL de evidencia'],['evidence_kind','Tipo de medida / evidencia']];
function renderMapping() {
  const data=state.upload;
  $('#upload-content').innerHTML=`<div class="file-summary">${icon('file-check')}<span>${esc(data.filename)}</span></div><p class="step-description">Asocia tus columnas. Los campos con <span class="required">*</span> son obligatorios. Las medidas deben venir en cm y el peso real en g.</p><form id="mapping-form"><div class="mapping-grid">${fields.map(([key,label,required])=>`<label class="field"><span>${label}${required?' <span class="required">*</span>':''}</span><select name="${key}" ${required?'required':''}><option value="">${required?'Seleccionar columna':'No incluir'}</option>${data.headers.map(header=>`<option value="${esc(header)}" ${data.suggested_mapping?.[key]===header?'selected':''}>${esc(header)}</option>`).join('')}</select></label>`).join('')}</div><label class="check-field"><input type="checkbox" name="trust_references">Confirmo que las referencias marcadas están verificadas y describen el mismo empaque de envío.</label><div class="small-notice">Usa números sin separadores de miles. Se admite coma o punto decimal: 1,200 significa 1,2; para 1200 gramos escribe 1200.<br>La talla de cobro actual es opcional y debe usar XXXS–XXXXL de cooperados; no asocies aquí la clase logística de otra nomenclatura.<br>Las columnas de referencia son opcionales. Si no tienes evidencia verificada, podrás registrar propuestas después del análisis. Las coincidencias entre sellers no se aprueban automáticamente.</div><div class="form-actions"><button type="button" class="button secondary" data-action="upload">Cambiar archivo</button><button class="button primary" type="submit">Analizar catálogo ${icon('chevron-right')}</button></div><p class="inline-error" id="mapping-error" role="alert" hidden></p></form>`;
  $('#mapping-form').addEventListener('submit',startImport);
}
async function startImport(event) {
  event.preventDefault();const form=event.currentTarget,values=Object.fromEntries(new FormData(form));
  const trust=values.trust_references==='on';delete values.trust_references;
  const mapping=Object.fromEntries(Object.entries(values).filter(([,value])=>value));
  if(new Set(Object.values(mapping)).size!==Object.keys(mapping).length){$('#mapping-error').textContent='Cada columna del archivo debe asociarse a un solo campo.';$('#mapping-error').hidden=false;return;}
  $('button[type="submit"]',form).disabled=true;
  try {const data=await api(`/api/uploads/${encodeURIComponent(state.upload.upload_id)}/process`,{method:'POST',body:JSON.stringify({mapping,trust_references:trust})});watchJob(data.job_id);}
  catch(error){$('#mapping-error').textContent=error.message;$('#mapping-error').hidden=false;$('button[type="submit"]',form).disabled=false;}
}
async function loadDemo() {
  $('#demo-button').disabled=true;
  try {const data=await api('/api/demo',{method:'POST'});openModal('upload');watchJob(data.job_id);}
  catch(error){toast(error.message,true);}finally{$('#demo-button').disabled=false;}
}
function watchJob(id) {
  if(state.jobTimer)clearTimeout(state.jobTimer);state.jobId=id;
  $('#upload-title').textContent='Analizando catálogo';
  $('#upload-content').innerHTML='<div class="loading-cell"><span class="spinner"></span><p>Iniciando procesamiento…</p></div>';
  const poll=async()=>{
    try {const job=await api(`/api/jobs/${encodeURIComponent(id)}`);if(state.jobId===id)renderJobProgress(job);
      if(['completed','failed'].includes(job.status)){state.jobTimer=null;await refresh();if(state.view==='cargas')await loadJobs();if($('#upload-overlay').hidden)toast(job.status==='completed'?'Carga completada. El catálogo ya está actualizado.':`La carga terminó con un error: ${job.error || 'consulta el historial'}`,job.status==='failed');}
      else state.jobTimer=setTimeout(poll,1200);
    }catch(error){state.jobTimer=null;toast(error.message,true);if(state.jobId===id)$('#upload-content').innerHTML=`<p class="inline-error">${esc(error.message)}</p><p class="muted-copy">Puedes consultar el estado en Cargas.</p>`;}
  };poll();
}
function renderJobProgress(job) {
  const done=job.status==='completed',failed=job.status==='failed';$('#upload-title').textContent=done?'Análisis completado':failed?'Carga con incidencias':'Analizando catálogo';
  const percent=done?100:job.total?Math.min(99,Math.round(job.processed/job.total*100)):null;
  $('#upload-content').innerHTML=`<div class="file-summary">${icon(done?'check-circle':'upload')}<span>${esc(job.filename || 'Catálogo')}</span></div><div class="progress-card"><div class="progress-title"><strong>${done?'Tu catálogo está actualizado':failed?'El proceso se detuvo':esc(job.stage || 'Procesando registros')}</strong><span>${percent!==null?`${percent}%`:'En proceso'}</span></div><div class="progress-track"><div class="progress-bar ${percent===null?'indeterminate':''}" style="${percent!==null?`width:${percent}%`:''}"></div></div><dl class="job-metrics">${[['Procesados',job.processed],['Nuevos',job.inserted],['Actualizados',job.updated],['Sin cambios',job.unchanged]].map(([label,count])=>`<div><dt>${label}</dt><dd>${integer(count)}</dd></div>`).join('')}</dl></div>${job.errors?`<div class="small-notice warning">${integer(job.errors)} registros con incidencias. Revisa sus datos antes de volver a cargarlos.</div>`:''}${job.error?`<p class="job-error">${esc(job.error)}</p>`:''}${job.error_examples?.length?`<p class="job-error">${job.error_examples.map(item=>esc(typeof item==='string'?item:JSON.stringify(item))).join('<br>')}</p>`:''}<p class="step-description" style="margin-top:16px">${done?'Las propuestas requieren revisión y aprobación. La carga no aplica cambios en tu sistema de origen.':'Puedes cerrar esta ventana y seguir el avance desde Cargas.'}</p><div class="form-actions"><button class="button ${done?'primary':'secondary'}" data-close="upload">${done?'Ver catálogo':'Cerrar ventana'}</button></div>`;
}
async function loadJobs() {
  const data=await api('/api/jobs');
  $('#jobs-content').innerHTML=data.items.length?data.items.map(job=>`<article class="job-card"><div class="job-card-header"><div><h3>${esc(job.filename || 'Catálogo')}</h3><time>${esc(date(job.created_at))}</time></div><span class="job-status ${esc(job.status)}">${({queued:'En cola',running:'En proceso',completed:'Completada',failed:'Fallida'})[job.status] || esc(job.status)}</span></div><div class="job-stats-line"><span><strong>${integer(job.processed)}</strong> procesados</span><span><strong>${integer(job.inserted)}</strong> nuevos</span><span><strong>${integer(job.updated)}</strong> actualizados</span><span><strong>${integer(job.unchanged)}</strong> sin cambios</span><span><strong>${integer(job.errors)}</strong> incidencias</span></div>${job.error?`<p class="job-error">${esc(job.error)}</p>`:''}${job.error_examples?.length?`<details style="margin-top:14px"><summary class="muted-copy">Ver incidencias</summary><p class="job-error">${job.error_examples.map(item=>esc(typeof item==='string'?item:JSON.stringify(item))).join('<br>')}</p></details>`:''}${['queued','running'].includes(job.status)?`<div class="form-actions"><button class="button secondary compact" data-watch="${esc(job.id)}">Ver progreso</button></div>`:''}</article>`).join(''):`<div class="empty-state"><div class="empty-illustration">${icon('upload')}</div><h3>Tu primera carga empieza aquí</h3><p>Importa un catálogo para ver el historial de procesamiento y sus resultados.</p><button class="button primary" data-action="upload">Cargar catálogo</button></div>`;
}
async function loadRules() {
  const settings=await api('/api/settings');
  $('#rules-content').innerHTML=`<div class="rules-layout"><section class="panel rules-card"><h2>Cálculo del peso aplicable</h2><div class="formula">Peso real (kg) = Peso (g) / 1000</div><div class="formula">Peso volumétrico (kg) =<br>Alto × Largo × Ancho (cm) / 5000</div><div class="formula">Peso aplicable = máximo(real, volumétrico)</div><p class="muted-copy">Los valores faltantes o no positivos requieren revisión. Los pesos superiores a 500 kg quedan como excepción. La clase logística original del Excel no define la talla.</p><h2 style="margin-top:25px">Tabla de cobro de cooperados</h2><table class="rules-table"><thead><tr><th>Desde (kg)</th><th>Hasta (kg)</th><th>Talla</th></tr></thead><tbody>${settings.bands.map(band=>`<tr><td>${number(band.min)}</td><td>${number(band.max)}</td><td>${sizeBadge(band.size)}</td></tr>`).join('')}</tbody></table></section><section class="panel rules-card"><h2>Tratamiento de los intervalos</h2><p class="muted-copy">La tabla original tiene huecos: por ejemplo, 1,05 kg no pertenece literalmente a ningún rango. Elige el criterio que tu negocio haya validado.</p><form id="rules-form"><label class="radio-card"><input type="radio" name="rule_mode" value="strict" ${settings.rule_mode==='strict'?'checked':''}><span><strong>Tabla literal</strong><small>Respeta los límites desde / hasta. Los pesos entre rangos quedan pendientes de revisión.</small></span></label><label class="radio-card"><input type="radio" name="rule_mode" value="upper_bounds" ${settings.rule_mode==='upper_bounds'?'checked':''}><span><strong>Límites continuos</strong><small>Utiliza los límites superiores. Por ejemplo, un peso mayor que 1 y hasta 3 kg corresponde a XS.</small></span></label><h3 class="form-section-title" style="margin-top:23px">Alerta por relación volumen / peso</h3><label class="field">Umbral de peso volumétrico ÷ peso real<input name="volume_ratio_threshold" type="number" min="1" max="1000000" step="any" required value="${esc(settings.volume_ratio_threshold || '100')}"></label><p class="muted-copy" style="margin-top:9px">Es una heurística configurable para priorizar revisión; un producto liviano y voluminoso puede ser correcto. La alerta no prueba que las medidas estén mal.</p><div class="small-notice">Una dimensión mayor que 300 cm se muestra como observación. No basta para marcar un error: puede describir un producto desplegado o un paquete grande válido.</div><div class="small-notice warning">Cambiar el criterio o el umbral recalcula el catálogo y deja obsoletas las propuestas y aprobaciones anteriores. Deben revisarse nuevamente antes de exportar.</div><label class="check-field"><input type="checkbox" required>Confirmo que el criterio seleccionado está validado para esta operación.</label><div class="form-actions"><button type="submit" class="button primary">Guardar criterio</button></div><p class="inline-error" id="rules-error" hidden role="alert"></p></form><div class="small-notice" style="margin-top:24px"><strong>Estados y evidencia</strong><br>Pendiente: no hay una talla utilizable por datos o reglas. Por revisar: existe una alerta o una propuesta que requiere revisión. Calculados sin alertas: las reglas básicas no detectaron una incidencia; las medidas no quedan certificadas.<br><br>Este piloto permite registrar evidencia y reutilizar referencias verificadas importadas. La investigación externa con IA no está conectada. Las propuestas requieren aprobación y la exportación no aplica cobros.</div></section></div>`;
  $('#rules-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget,button=$('button[type="submit"]',form);button.disabled=true;try{await api('/api/settings',{method:'PUT',body:JSON.stringify({rule_mode:new FormData(form).get('rule_mode'),volume_ratio_threshold:new FormData(form).get('volume_ratio_threshold')})});await loadSummary();await loadRules();toast('Criterio guardado. Catálogo recalculado.');}catch(error){$('#rules-error').textContent=error.message;$('#rules-error').hidden=false;button.disabled=false;}});
}

document.addEventListener('click',event=>{
  const nav=event.target.closest('[data-view]');if(nav){setView(nav.dataset.view).catch(error=>toast(error.message,true));return;}
  const close=event.target.closest('[data-close]');if(close){closeModal(close.dataset.close);return;}
  const tab=event.target.closest('[data-status]');if(tab){setStatus(tab.dataset.status);return;}
  const mode=event.target.closest('[data-review-mode]');if(mode){setReviewMode(mode.dataset.reviewMode).catch(error=>toast(error.message,true));return;}
  const action=event.target.closest('[data-action]');if(action){if(action.dataset.action==='upload'){$('#upload-title').textContent='Cargar catálogo';showUpload();}if(action.dataset.action==='demo')loadDemo();if(action.dataset.action==='clear-filters'){state.search='';state.direction='all';state.reason='all';$('#search-input').value='';syncFilters();setStatus('all');}return;}
  const watch=event.target.closest('[data-watch]');if(watch){openModal('upload');watchJob(watch.dataset.watch);return;}
  const row=event.target.closest('tr[data-id]');if(row)openDetail(row.dataset.id);
});
document.addEventListener('keydown',event=>{
  const modal=$$('.overlay').find(el=>!el.hidden);if(!modal)return;
  if(event.key==='Escape'){event.preventDefault();closeModal(modal.id.startsWith('drawer')?'drawer':'upload');}
  if(event.key==='Tab'){
    const elements=$$('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]',modal).filter(el=>el.offsetParent!==null);
    if(!elements.length){event.preventDefault();return;}const first=elements[0],last=elements[elements.length-1];
    if(event.shiftKey&&(document.activeElement===first||document.activeElement===$('section',modal))){last.focus();event.preventDefault();}else if(!event.shiftKey&&document.activeElement===last){first.focus();event.preventDefault();}
  }
});
$$('.overlay').forEach(el=>el.addEventListener('click',event=>{if(event.target===el)closeModal(el.id.startsWith('drawer')?'drawer':'upload');}));
$('#search-input').addEventListener('input',event=>{clearTimeout(searchTimer);const value=event.target.value;searchTimer=setTimeout(()=>{state.search=value;state.page=1;loadSkus().catch(error=>toast(error.message,true));},250);});
$('#prev-page').addEventListener('click',()=>{state.page--;loadSkus().catch(error=>toast(error.message,true));});
$('#next-page').addEventListener('click',()=>{state.page++;loadSkus().catch(error=>toast(error.message,true));});
$('#direction-filter').addEventListener('change',event=>{state.direction=event.target.value;state.page=1;state.groupPage=1;syncFilters();loadCurrentList().catch(error=>toast(error.message,true));});
$('#reason-filter').addEventListener('change',event=>{state.reason=event.target.value;state.page=1;loadSkus().catch(error=>toast(error.message,true));});
$('#prioritize-up').addEventListener('click',()=>{state.direction=state.direction==='up'?'all':'up';state.page=1;state.groupPage=1;syncFilters();loadCurrentList().catch(error=>toast(error.message,true));});
$('#demo-button').addEventListener('click',loadDemo);
hydrateIcons();
refresh().catch(error=>{toast(error.message,true);$('#sku-body').innerHTML=`<tr><td colspan="8" class="empty-state"><h3>No pudimos cargar el catálogo</h3><p>${esc(error.message)}</p><button class="button secondary" onclick="location.reload()">Volver a intentar</button></td></tr>`;});
