'use strict';

const categoryRuleState = {items:[], categories:[], editing:null, loading:0,rulePage:1,categoriesTruncated:false};
const categoryLimitFields = [
  ['real_min_kg','Peso real mínimo (kg)'],
  ['real_max_kg','Peso real máximo (kg)'],
  ['volume_min_kg','Peso volumétrico mínimo (kg)'],
  ['volume_max_kg','Peso volumétrico máximo (kg)']
];

function categoryRange(minimum, maximum) {
  const hasMinimum=minimum!==null && minimum!==undefined && minimum!=='';
  const hasMaximum=maximum!==null && maximum!==undefined && maximum!=='';
  if(!hasMinimum && !hasMaximum)return '<span class="category-no-limit">Sin límite definido</span>';
  if(hasMinimum && hasMaximum)return `${number(minimum)}–${number(maximum)} kg`;
  return hasMinimum?`Desde ${number(minimum)} kg`:`Hasta ${number(maximum)} kg`;
}

function categoryRuleForm(rule=null) {
  const editing=Boolean(rule);
  const categories=[...new Set(categoryRuleState.categories.map(item=>item.category).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'es'));
  return `<section class="panel category-form-card" aria-labelledby="category-form-title">
    <div class="category-card-heading"><div><div class="eyebrow">POLÍTICA DE ALERTAS</div><h2 id="category-form-title">${editing?'Editar regla':'Crear una regla'}</h2></div>${editing?`<span class="subtle-tag">VERSIÓN ${integer(rule.version)}</span>`:''}</div>
    <p class="muted-copy">Define límites de revisión sustentados por tu operación. No se proponen valores comerciales por defecto ni se rellenan medidas del catálogo.</p>
    <form id="category-rule-form">
      <div class="form-grid category-scope-fields">
        <label class="field full">Categoría <span class="sr-only">obligatoria</span><input name="category" list="category-values" required maxlength="250" placeholder="Selecciona o escribe una categoría" value="${esc(rule?.category || '')}"></label>
        <datalist id="category-values">${categories.map(value=>`<option value="${esc(value)}"></option>`).join('')}</datalist>
        <label class="field full">Subcategoría <span class="category-field-hint">Opcional; vacía incluye todas.</span><input name="subcategory" list="subcategory-values" maxlength="250" placeholder="Todas las subcategorías si se deja vacío" value="${esc(rule?.subcategory || '')}"></label>
        <datalist id="subcategory-values"></datalist>
        <label class="field full">Presentación <span class="category-field-hint">Opcional; vacía incluye todas.</span><input name="presentation" maxlength="250" placeholder="Todas las presentaciones si se deja vacío" value="${esc(rule?.presentation || '')}"></label>
      </div>
      <h3 class="category-form-subheading">Límites de revisión</h3>
      <p class="category-input-help">Define al menos un límite. Los valores son positivos, en kg y sin separadores de miles.</p>
      <div class="form-grid">${categoryLimitFields.map(([key,label])=>`<label class="field">${label}<input name="${key}" type="text" inputmode="decimal" autocomplete="off" placeholder="Sin límite" value="${esc(rule?.[key] ?? '')}"></label>`).join('')}</div>
      <label class="field">Justificación de la regla<textarea name="note" required maxlength="5000" placeholder="Documenta la fuente o el criterio de negocio que respalda estos límites.">${esc(rule?.note || '')}</textarea></label>
      <label class="check-field"><input type="checkbox" name="enabled" ${!rule || rule.enabled?'checked':''}>Regla activa para el diagnóstico.</label>
      <div class="small-notice">Una subcategoría o presentación vacía amplía el alcance a todas las de esa categoría. Si hay reglas compatibles, prevalece la más específica; comprueba el alcance antes de guardar.</div>
      <label class="check-field"><input type="checkbox" name="scope_confirmed" required>Confirmo que los límites y el alcance seleccionado están validados para esta operación.</label>
      <div class="form-actions">${editing?'<button type="button" class="button secondary" id="category-cancel-edit">Cancelar</button>':''}<button type="submit" class="button primary">${editing?'Guardar cambios':'Guardar regla'}</button></div>
      <p class="inline-error" id="category-rule-error" role="alert" hidden></p>
    </form>
  </section>`;
}

function categoryRulesList() {
  const items=categoryRuleState.items;
  const active=items.filter(item=>item.enabled).length;
  const pages=Math.max(1,Math.ceil(items.length/8));categoryRuleState.rulePage=Math.min(pages,Math.max(1,categoryRuleState.rulePage));
  const visible=items.slice((categoryRuleState.rulePage-1)*8,categoryRuleState.rulePage*8);
  return `<section class="panel category-list-card" aria-labelledby="category-list-title">
    <div class="category-card-heading"><div><h2 id="category-list-title">Reglas de tu operación <span class="counter">${integer(items.length)}</span></h2><p>${integer(active)} activas · Los límites generan alertas; no sustituyen evidencia física.</p></div></div>
    ${items.length?`<div class="category-rule-list">${visible.map(rule=>`<article class="category-rule ${rule.enabled?'':'disabled'}">
      <div class="category-rule-top"><div><h3>${esc(rule.category)}</h3><p>${esc(rule.subcategory || 'Todas las subcategorías')} <span aria-hidden="true">/</span> ${esc(rule.presentation || 'Todas las presentaciones')}</p></div><span class="category-enabled ${rule.enabled?'':'off'}">${rule.enabled?'Activa':'Deshabilitada'}</span></div>
      <dl class="category-limits"><div><dt>Peso real</dt><dd>${categoryRange(rule.real_min_kg,rule.real_max_kg)}</dd></div><div><dt>Peso volumétrico</dt><dd>${categoryRange(rule.volume_min_kg,rule.volume_max_kg)}</dd></div></dl>
      <p class="category-rule-note">${esc(rule.note)}</p>
      <div class="category-rule-footer"><span>Versión ${integer(rule.version)} · ${esc(categoryProfileSource(rule.origin || 'manual'))}${rule.support_count!==null&&rule.support_count!==undefined?` · soporte ${integer(rule.support_count)}`:''}</span><div><button class="button secondary compact" type="button" data-category-edit="${esc(rule.id)}">Editar</button>${rule.enabled?`<button class="button danger compact" type="button" data-category-disable="${esc(rule.id)}">Deshabilitar</button>`:''}</div></div>
    </article>`).join('')}</div>`:`<div class="empty-state category-empty"><div class="empty-illustration">${icon('sliders')}</div><h3>Define tus criterios de revisión</h3><p>Aún no hay reglas por categoría. Empieza con límites respaldados por tu operación; la herramienta no inventa rangos para tus productos.</p></div>`}
  ${items.length?`<div class="category-saved-pagination"><span>${integer((categoryRuleState.rulePage-1)*8+1)}–${integer(Math.min(categoryRuleState.rulePage*8,items.length))} de ${integer(items.length)} reglas</span><div><button type="button" class="icon-button" data-category-rule-page="${categoryRuleState.rulePage-1}" aria-label="Reglas anteriores" ${categoryRuleState.rulePage<=1?'disabled':''}>${icon('chevron-left')}</button><span>${categoryRuleState.rulePage} / ${pages}</span><button type="button" class="icon-button" data-category-rule-page="${categoryRuleState.rulePage+1}" aria-label="Reglas siguientes" ${categoryRuleState.rulePage>=pages?'disabled':''}>${icon('chevron-right')}</button></div></div>`:''}</section>`;
}

function renderCategoryRules() {
  $('#categories-content').innerHTML=`<div class="category-intro"><span>${icon('info')}</span><div><strong>Una regla de categoría es una señal para revisar.</strong><p>Los límites ayudan a encontrar datos que requieren contraste. No demuestran un error ni asignan medidas nuevas. Guardar o deshabilitar reglas actualiza el diagnóstico y conserva las aprobaciones existentes.</p></div></div><div id="category-profile-workspace"></div><details class="category-manual-editor" ${categoryRuleState.editing?'open':''}><summary>Reglas guardadas y ajustes manuales <span>Opcional · ${integer(categoryRuleState.items.length)} reglas guardadas</span></summary><p class="category-manual-help">El cálculo automático prepara los ámbitos en conjunto. Usa el editor manual para excepciones o criterios de negocio específicos; no necesitas completar cada rama.</p><div class="category-layout">${categoryRuleForm(categoryRuleState.editing)}<div id="category-saved-rules">${categoryRulesList()}</div></div></details><div class="category-catalog-note"><strong>Valores presentes en el catálogo</strong><p>Las sugerencias de categoría y subcategoría provienen de tus cargas. Puedes crear una regla antes de que lleguen productos de esa categoría. Los campos opcionales no crean categorías inferidas.</p><span>${integer(categoryRuleState.categories.length)} combinaciones disponibles como sugerencias de entrada.${categoryRuleState.categoriesTruncated?' La lista de entrada está limitada; el perfil recorre el catálogo completo.':''}</span></div>`;
  renderCategoryProfilePanel();
  const form=$('#category-rule-form');
  form.addEventListener('submit',saveCategoryRule);
  form.elements.category.addEventListener('input',updateSubcategorySuggestions);
  updateSubcategorySuggestions();
  $('#category-cancel-edit')?.addEventListener('click',()=>{categoryRuleState.editing=null;renderCategoryRules();});
  configureSavedCategoryRules();
}
function configureSavedCategoryRules(){
  $$('[data-category-edit]',$('#category-saved-rules')).forEach(button=>button.addEventListener('click',()=>{
    categoryRuleState.editing=categoryRuleState.items.find(item=>String(item.id)===button.dataset.categoryEdit) || null;
    renderCategoryRules();$('#category-form-title').scrollIntoView({block:'center',behavior:'smooth'});$('#category-rule-form [name="category"]').focus({preventScroll:true});
  }));
  $$('[data-category-disable]',$('#category-saved-rules')).forEach(button=>button.addEventListener('click',()=>disableCategoryRule(button)));
  $$('[data-category-rule-page]',$('#category-saved-rules')).forEach(button=>button.addEventListener('click',()=>{
    categoryRuleState.rulePage=Number(button.dataset.categoryRulePage);$('#category-saved-rules').innerHTML=categoryRulesList();configureSavedCategoryRules();
  }));
}

function updateSubcategorySuggestions() {
  const category=$('#category-rule-form [name="category"]').value.trim().toLocaleLowerCase('es');
  const values=[...new Set(categoryRuleState.categories.filter(item=>!category || String(item.category).trim().toLocaleLowerCase('es')===category).map(item=>item.subcategory).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'es'));
  $('#subcategory-values').innerHTML=values.map(value=>`<option value="${esc(value)}"></option>`).join('');
}

async function loadCategoryRules() {
  const request=++categoryRuleState.loading;stopCategoryProfilePolling();categoryProfileState.jobRequest++;categoryProfileState.suggestionRequest++;
  const heading=$('#categories-view .page-heading p');if(heading)heading.textContent='Calcula rangos de alerta para todo el catálogo y reserva las reglas manuales para excepciones.';
  $('#categories-content').innerHTML='<div class="panel loading-cell"><span class="spinner"></span><p>Cargando reglas por categoría…</p></div>';
  try {
    const [rules,categories,profiles]=await Promise.all([api('/api/category-rules'),api('/api/categories'),api('/api/category-profiles').catch(error=>({items:[],loadError:error.message}))]);
    if(request!==categoryRuleState.loading)return;
    categoryRuleState.items=rules.items || [];categoryRuleState.categories=categories.items || [];categoryRuleState.categoriesTruncated=Boolean(categories.truncated);
    const ps=categoryProfileState;ps.jobs=profiles.items || [];ps.loadError=profiles.loadError || null;
    if(!ps.jobs.some(job=>String(job.id)===String(ps.selected))){ps.selected=ps.jobs[0]?.id ?? null;ps.job=null;ps.suggestions=[];ps.page=1;}
    if(categoryRuleState.editing)categoryRuleState.editing=categoryRuleState.items.find(item=>String(item.id)===String(categoryRuleState.editing.id)) || null;
    renderCategoryRules();
    if(categoryProfileState.selected!==null)await loadCategoryProfileJob(categoryProfileState.selected);
    return true;
  }catch(error){
    if(request!==categoryRuleState.loading)return;
    $('#categories-content').innerHTML=`<div class="panel empty-state"><h3>No pudimos cargar las reglas</h3><p>${esc(error.message)}</p><button class="button secondary" id="category-rules-retry">Volver a intentar</button></div>`;
    $('#category-rules-retry').addEventListener('click',loadCategoryRules);toast(error.message,true);return false;
  }
}

async function saveCategoryRule(event) {
  event.preventDefault();const form=event.currentTarget;
  const errorElement=$('#category-rule-error'),button=$('button[type="submit"]',form);
  errorElement.hidden=true;
  const values=Object.fromEntries(new FormData(form));
  const fail=message=>{errorElement.textContent=message;errorElement.hidden=false;};
  const body={category:values.category.trim(),subcategory:values.subcategory.trim(),presentation:values.presentation.trim(),note:values.note.trim(),enabled:form.elements.enabled.checked};
  if(!body.category || !body.note)return fail('Completa la categoría y la justificación de la regla.');
  let supplied=0;
  for(const [key,label] of categoryLimitFields){
    const value=String(values[key] || '').trim();body[key]=value || null;
    if(!value)continue;
    if(!/^(?:\d+(?:[.,]\d*)?|[.,]\d+)$/.test(value) || !Number.isFinite(Number(value.replace(',','.'))) || Number(value.replace(',','.'))<=0)return fail(`${label}: usa un número positivo sin separadores de miles.`);
    supplied++;
  }
  if(!supplied)return fail('Define al menos un límite de peso real o volumétrico.');
  for(const prefix of ['real','volume']){
    const min=body[`${prefix}_min_kg`],max=body[`${prefix}_max_kg`];
    if(min!==null && max!==null && Number(min.replace(',','.'))>Number(max.replace(',','.')))return fail('Cada mínimo debe ser menor o igual a su máximo correspondiente.');
  }
  const id=categoryRuleState.editing?.id;
  button.disabled=true;button.textContent='Guardando y actualizando diagnóstico…';
  let saved=false;
  try {
    await api(id?`/api/category-rules/${encodeURIComponent(id)}`:'/api/category-rules',{method:id?'PUT':'POST',body:JSON.stringify(body)});
    saved=true;categoryRuleState.editing=null;
    const refreshed=await Promise.all([loadCategoryRules(),loadSummary()]);
    if(!refreshed[0]){toast('La regla se guardó, pero no pudimos actualizar toda la vista. Vuelve a cargar Categorías.',true);return;}
    toast('Regla guardada. Consulta el diagnóstico actualizado; las aprobaciones se conservan.');
  }catch(error){
    if(saved){toast('La regla se guardó, pero no pudimos actualizar toda la vista. Vuelve a cargar Categorías para consultar el resultado.',true);return;}
    fail(error.message);button.disabled=false;button.textContent=id?'Guardar cambios':'Guardar regla';
  }
}

async function disableCategoryRule(button) {
  button.disabled=true;
  let saved=false;
  try {
    await api(`/api/category-rules/${encodeURIComponent(button.dataset.categoryDisable)}`,{method:'DELETE'});saved=true;
    const refreshed=await Promise.all([loadCategoryRules(),loadSummary()]);
    if(!refreshed[0]){toast('La regla se deshabilitó, pero no pudimos actualizar toda la vista. Vuelve a cargar Categorías.',true);return;}
    toast('Regla deshabilitada. El diagnóstico se actualiza y las aprobaciones se conservan.');
  }catch(error){
    toast(saved?'La regla se deshabilitó, pero no pudimos actualizar toda la vista. Vuelve a cargar Categorías.':error.message,true);
    if(!saved)button.disabled=false;
  }
}

// Local profiling is separate from manually maintained category policies.
const categoryProfileState={jobs:[],selected:null,job:null,suggestions:[],total:0,pages:1,page:1,filter:'all',timer:null,jobRequest:0,suggestionRequest:0,lastApply:null,applying:false,creating:false,loadError:null};
const categoryProfileLabels={ready:'Apta como alerta',insufficient:'Muestra insuficiente',degenerate:'Sin dispersión suficiente',heterogeneous:'Grupo heterogéneo',existing_manual:'Regla manual protegida',applied:'Aplicado como alerta'};
const categoryProfileJobLabels={queued:'En cola',running:'Calculando',completed:'Perfil completado',failed:'No se pudo completar'};
function categoryProfileSource(source){return ({declared_references:'Con soporte declarado',catalog_statistics:'Estadístico provisional',manual:'Manual'})[source] || String(source || 'Fuente no informada');}
function categoryProfileObject(value){if(value && typeof value==='object')return value;if(typeof value==='string'){try{return JSON.parse(value);}catch{return {};}}return {};}
function categoryProfileNumber(value){return Number.isFinite(Number(value))?Number(value):0;}
function categoryProfileResultCount(value){return Array.isArray(value)?value.length:categoryProfileNumber(value);}
function categoryProfileId(id){return encodeURIComponent(String(id));}

function categoryProfileWorkspace(){
  const ps=categoryProfileState;
  return `<section class="panel category-profile-panel" aria-labelledby="category-profile-title"><div class="category-profile-header"><div><div class="eyebrow">RANGOS A PARTIR DE TUS DATOS · SIN API</div><h2 id="category-profile-title">Calcular rangos desde mi catálogo</h2><p>Genera sugerencias para todas las categorías, subcategorías y presentaciones con soporte suficiente. Puedes activar las aptas en una sola operación.</p></div><span class="category-profile-local">${icon('sliders')}Cálculo local</span></div><form id="category-profile-create-form"><div class="category-profile-start"><p>Se priorizan referencias declaradas y correcciones aprobadas. Cuando faltan, se utiliza un patrón estadístico provisional del catálogo. Las fuentes y sus muestras se conservan por métrica.</p><button class="button primary" type="submit" ${ps.creating?'disabled':''}>${icon('sparkles')}${ps.creating?'Iniciando…':'Calcular rangos desde mi catálogo'}</button></div><details class="category-profile-options"><summary>Ajustar mínimos de muestra <span>Muestra mínima: 30 casos · 3 sellers</span></summary><div class="category-profile-option-fields"><label class="field">Casos mínimos de soporte<input type="number" name="min_count" required min="30" step="1" value="30"></label><label class="field">Sellers mínimos<input type="number" name="min_sellers" required min="3" step="1" value="3"></label></div><p>Son mínimos operativos. Sellers distintos pueden compartir el mismo origen de datos; la cantidad no garantiza evidencia independiente ni exactitud física.</p></details><p id="category-profile-create-error" class="inline-error" hidden role="alert"></p></form><div class="category-profile-purpose"><strong>Son rangos de alerta, no medidas correctas.</strong><span>Aplicarlos no modifica pesos, dimensiones, tallas ni cobros. Las reglas manuales existentes se protegen.</span></div><div class="category-profile-history"><label class="field">Perfiles guardados<select id="category-profile-select" ${ps.jobs.length?'':'disabled'}>${ps.jobs.length?ps.jobs.map(job=>`<option value="${esc(job.id)}" ${String(job.id)===String(ps.selected)?'selected':''}>${esc(String(job.id).slice(0,8))} · ${esc(categoryProfileJobLabels[job.status] || job.status)} · ${esc(date(job.created_at))}</option>`).join(''):'<option>Aún no has calculado un perfil</option>'}</select></label><button type="button" class="button secondary compact" id="category-profile-refresh">${icon('sliders')}Actualizar</button></div><div id="category-profile-job-content">${categoryProfileJobContent()}</div></section>`;
}
function categoryProfileJobContent(){
  const ps=categoryProfileState,job=ps.job;
  if(ps.loadError)return `<div class="category-profile-empty"><p class="inline-error">${esc(ps.loadError)}</p><p class="muted-copy">Las reglas manuales siguen disponibles. Actualiza esta sección para volver a consultar los perfiles.</p></div>`;
  if(!job)return `<div class="category-profile-empty"><div class="empty-illustration">${icon('grid')}</div><h3>Un perfil para todo el catálogo</h3><p>Presiona calcular para preparar sugerencias por contexto. No necesitas definir los rangos rama por rama.</p></div>`;
  const active=['queued','running'].includes(job.status),summary=categoryProfileObject(job.summary),ready=categoryProfileNumber(summary.ready);
  const progress=categoryProfileNumber(job.progress),total=categoryProfileNumber(job.total),percent=job.status==='completed'?100:total>0?Math.max(0,Math.min(99,Math.floor(progress/total*100))):null;
  const result=ps.lastApply && String(ps.lastApply.id)===String(job.id)?ps.lastApply.result:null;
  return `<div class="category-profile-job" aria-live="polite"><div class="category-profile-job-heading"><div><strong>${esc(categoryProfileJobLabels[job.status] || job.status)}</strong><small>${esc(String(job.id).slice(0,8))} · ${esc(date(job.created_at))}</small></div><span>${percent===null?'En preparación':`${percent}%`}</span></div><div class="progress-track"><div class="progress-bar ${percent===null&&active?'indeterminate':''}" style="${percent===null?'':`width:${percent}%`}"></div></div><p class="category-profile-stage">${active?'Lectura y cálculo de rangos. Puedes salir de esta vista; el trabajo continúa en segundo plano.':job.status==='failed'?'El perfil no pudo completarse; no se aplicaron rangos desde este trabajo.':'Perfil calculado. Revisa el resumen y activa las sugerencias aptas como alertas.'}</p>${job.error?`<p class="job-error">${esc(job.error)}</p>`:''}${job.status==='completed'?`<div class="category-profile-summary">${[['Ámbitos analizados',summary.total_scopes],['Aptas para aplicar',ready],['Aplicadas como alerta',summary.applied],['Reglas manuales protegidas',summary.existing_manual]].map(([label,value])=>`<div><span>${label}</span><strong>${integer(value)}</strong></div>`).join('')}</div><form id="category-profile-apply-form"><label class="check-field"><input type="checkbox" id="category-profile-attestation" ${ready>0&&!ps.applying?'':'disabled'}><span>Entiendo que se aplicarán <strong>todas las sugerencias aptas de este perfil</strong>, incluidas las de otras páginas o filtros. Son alertas estadísticas, sin verificación física; no corrigen medidas, tallas ni cobros.</span></label><div class="category-profile-apply-row"><p>${integer(ready)} sugerencias aptas en el perfil completo. El servidor comprueba su vigencia y protege las reglas manuales antes de aplicar.</p><button class="button primary" type="submit" id="category-profile-apply" disabled>${ps.applying?'Aplicando y actualizando diagnóstico…':'Aplicar rangos recomendados como alertas'}</button></div><p id="category-profile-apply-error" class="inline-error" hidden role="alert"></p></form>${result?`<div class="category-profile-apply-result" role="status"><strong>Resultado de la aplicación</strong><dl>${[['Reglas aplicadas',result.applied],['Sugerencias omitidas',result.skipped],['Protegidas al aplicar',result.protected_manual],['Registros recalculados',result.recalculated]].map(([label,value])=>`<div><dt>${label}</dt><dd>${integer(categoryProfileResultCount(value))}</dd></div>`).join('')}</dl><p>Se actualizaron reglas de alerta y su diagnóstico. No se corrigieron medidas ni se aplicaron cambios de cobro.</p></div>`:''}<div class="category-profile-suggestion-heading"><label class="field">Mostrar sugerencias<select id="category-profile-filter">${[['all','Todos los estados'],...Object.entries(categoryProfileLabels)].map(([value,label])=>`<option value="${value}" ${ps.filter===value?'selected':''}>${label}</option>`).join('')}</select></label><a class="button secondary compact" href="/api/category-profiles/${categoryProfileId(job.id)}/suggestions.csv">${icon('download')}Exportar sugerencias CSV</a></div><div id="category-profile-suggestions">${categoryProfileSuggestionTable()}</div>`:''}</div>`;
}
function categoryProfileSupport(row){
  const counts=categoryProfileObject(row.counts || row.counts_json),method=categoryProfileObject(row.method || row.method_json);
  const metrics=counts.metrics || {},warningItems=Array.isArray(method.warnings)?method.warnings:[];
  return `<details class="category-profile-support"><summary>Ver soporte y método</summary><dl>${[['Filas consideradas',counts.rows],['Casos de la muestra',counts.entities],['Con identidad',counts.identified_entities],['Sin identidad suficiente',counts.unidentified_entities],['Sellers',counts.sellers]].filter(([,value])=>value!==undefined).map(([label,value])=>`<div><dt>${label}</dt><dd>${integer(value)}</dd></div>`).join('')}</dl>${Object.entries(metrics).map(([key,value])=>`<p><strong>${key==='real'?'Peso real':key==='volume'?'Peso volumétrico':esc(key)}:</strong> ${esc(categoryProfileSource(value.selected_source || value.source))} · ${integer(value.support_count)} casos de muestra · ${integer(value.seller_count)} sellers.</p>`).join('')}<p><strong>Método:</strong> ${esc(typeof row.method==='string' && !Object.keys(method).length?row.method:method.method || 'No informado')}${method.iqr_multiplier!==undefined?` · multiplicador ${esc(method.iqr_multiplier)}`:''}.</p>${warningItems.length?`<ul>${warningItems.map(warning=>`<li>${esc(warning)}</li>`).join('')}</ul>`:''}${row.reason?`<p>${esc(row.reason)}</p>`:''}<p>El soporte describe datos declarados o aprobaciones registradas. No demuestra que las fuentes sean independientes ni que las medidas físicas sean correctas.</p></details>`;
}
function categoryProfileSuggestionTable(){
  const ps=categoryProfileState;
  return `<div class="table-scroll category-profile-table-scroll" tabindex="0" role="region" aria-label="Sugerencias de rangos estadísticos"><table class="category-profile-table"><thead><tr><th>CONTEXTO</th><th>ESTADO</th><th>PROCEDENCIA / SOPORTE</th><th>PESO REAL</th><th>PESO VOLUMÉTRICO</th></tr></thead><tbody>${ps.suggestions.length?ps.suggestions.map(row=>{
    const bounds=categoryProfileObject(row.bounds || row.bounds_json),counts=categoryProfileObject(row.counts || row.counts_json);
    return `<tr><td><strong>${esc(row.category || 'Sin categoría')}</strong><small>${esc(row.subcategory || 'Todas las subcategorías')}<br>${esc(row.presentation || 'Todas las presentaciones')}</small></td><td><span class="category-profile-state ${esc(row.state)}">${esc(categoryProfileLabels[row.state] || row.state)}</span>${row.applied_rule_id?`<small>Regla #${esc(row.applied_rule_id)}</small>`:''}${row.reason?`<p class="category-profile-reason">${esc(row.reason)}</p>`:''}</td><td><strong class="category-profile-source">${esc(categoryProfileSource(row.source))}</strong><small>${counts.entities!==undefined?`${integer(counts.entities)} casos de muestra`:''}${counts.sellers!==undefined?` · ${integer(counts.sellers)} sellers`:''}</small>${categoryProfileSupport(row)}</td><td>${categoryRange(bounds.real_min_kg,bounds.real_max_kg)}</td><td>${categoryRange(bounds.volume_min_kg,bounds.volume_max_kg)}</td></tr>`;
  }).join(''):'<tr><td colspan="5" class="category-profile-no-suggestions">No hay sugerencias en este estado.</td></tr>'}</tbody></table></div><div class="table-footer"><span>${ps.total?`${integer((ps.page-1)*25+1)}–${integer(Math.min(ps.page*25,ps.total))} de ${integer(ps.total)} sugerencias`:'0 sugerencias'}</span><div class="pagination"><button class="icon-button" type="button" id="category-profile-prev" aria-label="Sugerencias anteriores" ${ps.page<=1?'disabled':''}>${icon('chevron-left')}</button><span>${ps.page} / ${ps.pages}</span><button class="icon-button" type="button" id="category-profile-next" aria-label="Sugerencias siguientes" ${ps.page>=ps.pages?'disabled':''}>${icon('chevron-right')}</button></div></div>`;
}
function configureCategoryProfileJob(){
  const ps=categoryProfileState;
  $('#category-profile-filter')?.addEventListener('change',event=>{ps.filter=event.target.value;ps.page=1;loadCategoryProfileSuggestions().catch(error=>toast(error.message,true));});
  $('#category-profile-attestation')?.addEventListener('change',event=>{$('#category-profile-apply').disabled=!event.target.checked || ps.applying || categoryProfileNumber(ps.job?.summary?.ready)<=0;});
  $('#category-profile-apply-form')?.addEventListener('submit',applyCategoryProfile);
  configureCategoryProfilePagination();
}
function configureCategoryProfilePagination(){
  $('#category-profile-prev')?.addEventListener('click',()=>{categoryProfileState.page--;loadCategoryProfileSuggestions().catch(error=>toast(error.message,true));});
  $('#category-profile-next')?.addEventListener('click',()=>{categoryProfileState.page++;loadCategoryProfileSuggestions().catch(error=>toast(error.message,true));});
}
function renderCategoryProfilePanel(){
  const target=$('#category-profile-workspace');if(!target)return;
  target.innerHTML=categoryProfileWorkspace();
  $('#category-profile-create-form').addEventListener('submit',createCategoryProfile);
  $('#category-profile-select').addEventListener('change',event=>selectCategoryProfile(event.target.value));
  $('#category-profile-refresh').addEventListener('click',()=>refreshCategoryProfiles().catch(error=>toast(error.message,true)));
  configureCategoryProfileJob();
}
function renderCategoryProfileJob(){const target=$('#category-profile-job-content');if(!target)return;target.innerHTML=categoryProfileJobContent();configureCategoryProfileJob();}
function stopCategoryProfilePolling(){if(categoryProfileState.timer!==null)clearTimeout(categoryProfileState.timer);categoryProfileState.timer=null;}
async function refreshCategoryProfiles(){
  const ps=categoryProfileState;const data=await api('/api/category-profiles');ps.jobs=data.items || [];ps.loadError=null;
  if(!ps.jobs.some(job=>String(job.id)===String(ps.selected))){ps.selected=ps.jobs[0]?.id ?? null;ps.job=null;ps.suggestions=[];ps.page=1;}
  renderCategoryProfilePanel();if(ps.selected!==null)await loadCategoryProfileJob(ps.selected);
}
async function selectCategoryProfile(id){
  const ps=categoryProfileState;stopCategoryProfilePolling();ps.jobRequest++;ps.suggestionRequest++;
  ps.selected=id;ps.job=null;ps.suggestions=[];ps.total=0;ps.pages=1;ps.page=1;ps.filter='all';ps.lastApply=null;
  renderCategoryProfileJob();await loadCategoryProfileJob(id);
}
async function loadCategoryProfileJob(id){
  const ps=categoryProfileState,request=++ps.jobRequest;
  stopCategoryProfilePolling();if(state.view!=='categorias')return;
  try{
    const job=await api(`/api/category-profiles/${categoryProfileId(id)}`);
    if(request!==ps.jobRequest || String(ps.selected)!==String(id))return;
    ps.job=job;ps.loadError=null;renderCategoryProfileJob();
    if(job.status==='completed')await loadCategoryProfileSuggestions();
    if(['queued','running'].includes(job.status) && state.view==='categorias')ps.timer=setTimeout(()=>loadCategoryProfileJob(id),1600);
  }catch(error){
    if(request!==ps.jobRequest)return;ps.loadError=error.message;renderCategoryProfilePanel();toast(error.message,true);
  }
}
async function loadCategoryProfileSuggestions(){
  const ps=categoryProfileState,id=ps.selected,request=++ps.suggestionRequest;
  const target=$('#category-profile-suggestions');if(target){target.setAttribute('aria-busy','true');target.innerHTML='<div class="category-profile-empty"><span class="spinner"></span><p>Cargando sugerencias…</p></div>';}
  const params=new URLSearchParams({page:ps.page,page_size:25,state:ps.filter});
  try{
    const data=await api(`/api/category-profiles/${categoryProfileId(id)}/suggestions?${params}`);
    if(request!==ps.suggestionRequest || String(ps.selected)!==String(id))return;
    ps.suggestions=data.items || [];ps.total=Number(data.total)||0;ps.pages=Math.max(1,Number(data.pages)||Math.ceil(ps.total/25));ps.page=Number(data.page)||ps.page;
    if(ps.page>ps.pages){ps.page=ps.pages;return loadCategoryProfileSuggestions();}
    if(target){target.innerHTML=categoryProfileSuggestionTable();configureCategoryProfilePagination();}
  }catch(error){
    if(request===ps.suggestionRequest && target){target.innerHTML=`<div class="category-profile-empty"><p class="inline-error">${esc(error.message)}</p><button type="button" class="button secondary compact" id="category-profile-retry-suggestions">Volver a cargar sugerencias</button></div>`;$('#category-profile-retry-suggestions').addEventListener('click',()=>loadCategoryProfileSuggestions().catch(problem=>toast(problem.message,true)));}
    throw error;
  }finally{if(request===ps.suggestionRequest && target)target.removeAttribute('aria-busy');}
}
async function createCategoryProfile(event){
  event.preventDefault();const ps=categoryProfileState,form=event.currentTarget,button=$('button[type="submit"]',form),errorEl=$('#category-profile-create-error');
  const data=new FormData(form),minCount=Number(data.get('min_count')),minSellers=Number(data.get('min_sellers'));
  if(!Number.isSafeInteger(minCount)||minCount<30||!Number.isSafeInteger(minSellers)||minSellers<3){errorEl.textContent='Usa al menos 30 casos de soporte y 3 sellers.';errorEl.hidden=false;return;}
  errorEl.hidden=true;button.disabled=true;ps.creating=true;
  let saved=false;
  try{
    const job=await api('/api/category-profiles',{method:'POST',body:JSON.stringify({min_count:minCount,min_sellers:minSellers})});
    saved=true;ps.selected=job.id;ps.job=job;ps.suggestions=[];ps.page=1;ps.filter='all';ps.lastApply=null;ps.creating=false;
    await refreshCategoryProfiles();toast('Perfil iniciado. El cálculo continúa en segundo plano.');
  }catch(error){
    ps.creating=false;
    if(saved){toast('El perfil se inició, pero no pudimos actualizar la vista. Usa Actualizar para consultar el avance.',true);button.disabled=false;}
    else{errorEl.textContent=error.message;errorEl.hidden=false;button.disabled=false;}
  }
}
async function applyCategoryProfile(event){
  event.preventDefault();const ps=categoryProfileState,id=ps.selected,form=event.currentTarget,errorEl=$('#category-profile-apply-error');
  if(!$('#category-profile-attestation').checked || ps.applying || categoryProfileNumber(ps.job?.summary?.ready)<=0)return;
  ps.applying=true;errorEl.hidden=true;$$('button,input',form).forEach(element=>element.disabled=true);$('#category-profile-apply').textContent='Aplicando y actualizando diagnóstico…';
  let saved=false;
  try{
    const result=await api(`/api/category-profiles/${categoryProfileId(id)}/apply`,{method:'POST',body:JSON.stringify({all_ready:true,attestation:true})});
    saved=true;ps.lastApply={id,result};ps.applying=false;
    const refreshed=await Promise.all([loadCategoryRules(),loadSummary()]);
    if(!refreshed[0]){toast('Los rangos se aplicaron, pero la vista necesita actualizarse. Vuelve a abrir Categorías.',true);return;}
    toast(`${integer(categoryProfileResultCount(result.applied))} reglas de alerta aplicadas. Las medidas y los cobros se conservan.`);
  }catch(error){
    ps.applying=false;
    if(saved){toast('Los rangos se aplicaron, pero no pudimos actualizar toda la vista. Vuelve a abrir Categorías.',true);return;}
    if(document.contains(errorEl)){errorEl.textContent=error.message;errorEl.hidden=false;$$('button,input',form).forEach(element=>element.disabled=false);$('#category-profile-attestation').checked=false;$('#category-profile-apply').disabled=true;$('#category-profile-apply').textContent='Aplicar rangos recomendados como alertas';}
    else toast(error.message,true);
  }
}
