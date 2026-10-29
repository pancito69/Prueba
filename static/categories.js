'use strict';

const categoryRuleState = {items:[], categories:[], editing:null, loading:0};
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
  return `<section class="panel category-list-card" aria-labelledby="category-list-title">
    <div class="category-card-heading"><div><h2 id="category-list-title">Reglas de tu operación <span class="counter">${integer(items.length)}</span></h2><p>${integer(active)} activas · Los límites generan alertas; no sustituyen evidencia física.</p></div></div>
    ${items.length?`<div class="category-rule-list">${items.map(rule=>`<article class="category-rule ${rule.enabled?'':'disabled'}">
      <div class="category-rule-top"><div><h3>${esc(rule.category)}</h3><p>${esc(rule.subcategory || 'Todas las subcategorías')} <span aria-hidden="true">/</span> ${esc(rule.presentation || 'Todas las presentaciones')}</p></div><span class="category-enabled ${rule.enabled?'':'off'}">${rule.enabled?'Activa':'Deshabilitada'}</span></div>
      <dl class="category-limits"><div><dt>Peso real</dt><dd>${categoryRange(rule.real_min_kg,rule.real_max_kg)}</dd></div><div><dt>Peso volumétrico</dt><dd>${categoryRange(rule.volume_min_kg,rule.volume_max_kg)}</dd></div></dl>
      <p class="category-rule-note">${esc(rule.note)}</p>
      <div class="category-rule-footer"><span>Versión ${integer(rule.version)}</span><div><button class="button secondary compact" type="button" data-category-edit="${esc(rule.id)}">Editar</button>${rule.enabled?`<button class="button danger compact" type="button" data-category-disable="${esc(rule.id)}">Deshabilitar</button>`:''}</div></div>
    </article>`).join('')}</div>`:`<div class="empty-state category-empty"><div class="empty-illustration">${icon('sliders')}</div><h3>Define tus criterios de revisión</h3><p>Aún no hay reglas por categoría. Empieza con límites respaldados por tu operación; la herramienta no inventa rangos para tus productos.</p></div>`}
  </section>`;
}

function renderCategoryRules() {
  $('#categories-content').innerHTML=`<div class="category-intro"><span>${icon('info')}</span><div><strong>Una regla de categoría es una señal para revisar.</strong><p>Los límites ayudan a encontrar datos que requieren contraste. No demuestran un error ni asignan medidas nuevas. Guardar o deshabilitar reglas actualiza el diagnóstico y conserva las aprobaciones existentes.</p></div></div><div class="category-layout">${categoryRuleForm(categoryRuleState.editing)}${categoryRulesList()}</div><div class="category-catalog-note"><strong>Valores presentes en el catálogo</strong><p>Las sugerencias de categoría y subcategoría provienen de tus cargas. Puedes crear una regla antes de que lleguen productos de esa categoría. Los campos opcionales no crean categorías inferidas.</p><span>${integer(categoryRuleState.categories.length)} combinaciones de categoría y subcategoría informadas.</span></div>`;
  const form=$('#category-rule-form');
  form.addEventListener('submit',saveCategoryRule);
  form.elements.category.addEventListener('input',updateSubcategorySuggestions);
  updateSubcategorySuggestions();
  $('#category-cancel-edit')?.addEventListener('click',()=>{categoryRuleState.editing=null;renderCategoryRules();});
  $$('[data-category-edit]',$('#categories-content')).forEach(button=>button.addEventListener('click',()=>{
    categoryRuleState.editing=categoryRuleState.items.find(item=>String(item.id)===button.dataset.categoryEdit) || null;
    renderCategoryRules();$('#category-form-title').scrollIntoView({block:'center',behavior:'smooth'});$('#category-rule-form [name="category"]').focus({preventScroll:true});
  }));
  $$('[data-category-disable]',$('#categories-content')).forEach(button=>button.addEventListener('click',()=>disableCategoryRule(button)));
}

function updateSubcategorySuggestions() {
  const category=$('#category-rule-form [name="category"]').value.trim().toLocaleLowerCase('es');
  const values=[...new Set(categoryRuleState.categories.filter(item=>!category || String(item.category).trim().toLocaleLowerCase('es')===category).map(item=>item.subcategory).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'es'));
  $('#subcategory-values').innerHTML=values.map(value=>`<option value="${esc(value)}"></option>`).join('');
}

async function loadCategoryRules() {
  const request=++categoryRuleState.loading;
  $('#categories-content').innerHTML='<div class="panel loading-cell"><span class="spinner"></span><p>Cargando reglas por categoría…</p></div>';
  try {
    const [rules,categories]=await Promise.all([api('/api/category-rules'),api('/api/categories')]);
    if(request!==categoryRuleState.loading)return;
    categoryRuleState.items=rules.items || [];categoryRuleState.categories=categories.items || [];
    if(categoryRuleState.editing)categoryRuleState.editing=categoryRuleState.items.find(item=>String(item.id)===String(categoryRuleState.editing.id)) || null;
    renderCategoryRules();
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
