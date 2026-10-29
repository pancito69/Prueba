'use strict';

const researchState={selected:null,batches:[],lastImport:null,listRequest:0,detailRequest:0};
const researchCaseLabels={open:'Por investigar',proposals_created:'Propuestas creadas',minimum_proposed:'Mínimo por revisar',minimum_reviewed:'Mínimo revisado',insufficient:'Evidencia insuficiente',conflict:'Fuentes en conflicto',stale:'Datos desactualizados',rejected:'Mínimo rechazado'};
const researchFieldLabels={weight_g:'Peso real',height_cm:'Alto',length_cm:'Largo',width_cm:'Ancho'};

function researchErrorText(value){return typeof value==='string'?value:JSON.stringify(value);}
function researchTimestamp(value){return value?date(value):'Fecha no disponible';}

async function loadResearch(){
  const target=$('#research-content');
  if(!target)return;
  const request=++researchState.listRequest;
  target.innerHTML='<div class="panel loading-cell"><span class="spinner"></span><p>Preparando investigación sin API…</p></div>';
  try{
    const [data,categories]=await Promise.all([api('/api/research/batches'),api('/api/categories').catch(()=>({items:[]}))]);
    if(request!==researchState.listRequest)return;
    researchState.batches=Array.isArray(data.items)?data.items:[];
    if(!researchState.batches.some(batch=>String(batch.id)===String(researchState.selected)))researchState.selected=researchState.batches[0]?.id ?? null;
    renderResearchWorkspace(categories.items || []);
    if(researchState.selected!==null)await loadResearchBatch(researchState.selected);
  }catch(error){
    if(request!==researchState.listRequest)return;
    target.innerHTML=`<div class="panel empty-state"><h3>No pudimos cargar los lotes</h3><p>${esc(error.message)}</p><button type="button" class="button secondary" id="research-retry">Volver a intentar</button></div>`;
    $('#research-retry').addEventListener('click',loadResearch);
    toast(error.message,true);
  }
}

function renderResearchWorkspace(categories){
  const categoryNames=[...new Set(categories.map(item=>item.category).filter(Boolean))];
  $('#research-content').innerHTML=`
    <div class="research-intro"><span>${icon('shield')}</span><div><strong>Investiga con tus herramientas y conserva el respaldo.</strong><p>Este flujo no conecta un proveedor ni realiza solicitudes a una API pagada. El uso manual de ChatGPT o Claude depende de tu plan y de las funciones disponibles. Importar una respuesta no verifica que la fuente o la cita sean verdaderas.</p></div></div>
    <div class="research-steps" aria-label="Pasos de investigación">
      <article><span>01</span><div><h2>Prepara un lote</h2><p>Agrupa productos con atención y sin propuesta activa. Empieza con una muestra pequeña.</p></div></article>
      <article><span>02</span><div><h2>Investiga las fuentes</h2><p>Descarga los casos y las instrucciones para usarlos manualmente con tu asistente.</p></div></article>
      <article><span>03</span><div><h2>Importa y revisa</h2><p>Devuelve el CSV, comprueba las citas y decide qué evidencia respalda una corrección.</p></div></article>
    </div>
    <section class="panel research-create" aria-labelledby="research-create-title"><div class="research-section-heading"><div><h2 id="research-create-title">Crear lote de investigación</h2><p>Se priorizan las subidas potenciales y se evitan productos que ya tienen un lote abierto.</p></div><span class="subtle-tag">SIN API CONECTADA</span></div>
      <form id="research-create-form"><div class="research-create-fields"><label class="field">Máximo de productos<input name="limit" type="number" min="1" max="100" step="1" required value="20"></label><label class="field">Dirección<select name="direction"><option value="all">Todas · priorizar subidas</option><option value="up">Subidas potenciales</option><option value="down">Bajadas potenciales</option><option value="same">Misma talla</option><option value="unknown">Sin base de comparación</option></select></label><label class="field">Categoría opcional<input name="category" list="research-categories" placeholder="Todas las categorías"><datalist id="research-categories">${categoryNames.map(category=>`<option value="${esc(category)}"></option>`).join('')}</datalist></label><button type="submit" class="button primary">${icon('plus')} Crear lote</button></div><p class="inline-error" id="research-create-error" role="alert" hidden></p></form>
    </section>
    <div class="research-workspace"><aside class="panel research-batches" aria-labelledby="research-batches-title"><div class="research-batches-heading"><h2 id="research-batches-title">Tus lotes</h2><span class="counter">${integer(researchState.batches.length)}</span></div><div id="research-batch-list"></div></aside><section class="panel research-batch-detail" id="research-batch-detail" aria-label="Detalle del lote">${researchState.selected===null?researchEmptyState():'<div class="loading-cell"><span class="spinner"></span><p>Cargando lote…</p></div>'}</section></div>
  `;
  renderResearchBatchList();
  $('#research-create-form').addEventListener('submit',createResearchBatch);
}

function researchEmptyState(){return `<div class="empty-state"><div class="empty-illustration">${icon('search')}</div><h3>La primera investigación empieza aquí</h3><p>Crea un lote para descargar productos agrupados y las instrucciones de búsqueda. Si no hay fuentes suficientes, el resultado debe quedar como insuficiente.</p></div>`;}

function renderResearchBatchList(){
  const target=$('#research-batch-list');if(!target)return;
  target.innerHTML=researchState.batches.length?researchState.batches.map(batch=>`<button type="button" class="research-batch-button ${String(batch.id)===String(researchState.selected)?'active':''}" data-research-batch="${esc(batch.id)}" ${String(batch.id)===String(researchState.selected)?'aria-current="true"':''}><span class="research-batch-number">${icon('file-check')}Lote ${esc(String(batch.id).slice(0,8))}</span><strong>${integer(batch.case_count ?? batch.cases_count)} productos <span>· ${integer(batch.member_count)} SKUs</span></strong><small>${esc(researchTimestamp(batch.created_at))}</small></button>`).join(''):'<p class="muted-copy research-no-batches">Todavía no hay lotes creados.</p>';
  $$('[data-research-batch]',target).forEach(button=>button.addEventListener('click',()=>loadResearchBatch(button.dataset.researchBatch).catch(error=>toast(error.message,true))));
}

async function createResearchBatch(event){
  event.preventDefault();const form=event.currentTarget,button=$('button[type="submit"]',form),errorEl=$('#research-create-error');
  errorEl.hidden=true;button.disabled=true;
  const values=Object.fromEntries(new FormData(form));
  try{
    const batch=await api('/api/research/batches',{method:'POST',body:JSON.stringify({limit:Number(values.limit),direction:values.direction,category:values.category.trim()})});
    researchState.selected=batch.id;researchState.lastImport=null;
    await loadResearch();
    toast(`Lote preparado con ${integer(batch.case_count ?? batch.cases_count)} productos. Descarga los casos y las instrucciones.`);
  }catch(error){errorEl.textContent=error.message;errorEl.hidden=false;button.disabled=false;}
}

async function loadResearchBatch(id){
  researchState.selected=id;renderResearchBatchList();
  const target=$('#research-batch-detail'),request=++researchState.detailRequest;
  if(!target)return;
  target.setAttribute('aria-busy','true');
  target.innerHTML='<div class="loading-cell"><span class="spinner"></span><p>Cargando lote…</p></div>';
  try{
    const data=await api(`/api/research/batches/${encodeURIComponent(id)}`);
    if(request!==researchState.detailRequest)return;
    renderResearchBatch(data);
  }catch(error){
    if(request!==researchState.detailRequest)return;
    target.innerHTML=`<div class="empty-state"><h3>No pudimos abrir el lote</h3><p>${esc(error.message)}</p><button type="button" class="button secondary" id="research-batch-retry">Volver a intentar</button></div>`;
    $('#research-batch-retry').addEventListener('click',()=>loadResearchBatch(id));
    toast(error.message,true);
  }finally{if(request===researchState.detailRequest)target.removeAttribute('aria-busy');}
}

function renderResearchBatch(batch){
  const cases=Array.isArray(batch.cases)?batch.cases:Array.isArray(batch.items)?batch.items:[];
  const batchId=batch.id ?? researchState.selected;
  const base=`/api/research/batches/${encodeURIComponent(batchId)}`;
  const last=researchState.lastImport && String(researchState.lastImport.batchId)===String(batchId)?researchState.lastImport.result:null;
  $('#research-batch-detail').innerHTML=`
    <div class="research-detail-heading"><div><div class="eyebrow">LOTE DE INVESTIGACIÓN</div><h2>Lote ${esc(String(batchId).slice(0,8))}</h2><p>${integer(batch.case_count ?? cases.length)} productos · ${integer(batch.member_count)} SKUs · ${esc(researchTimestamp(batch.created_at))}</p></div><span class="subtle-tag">EVIDENCIA POR COMPROBAR</span></div>
    <section class="research-download-step"><div><span class="research-step-number">02</span><h3>Descarga y entrega ambos archivos a tu asistente</h3><p>Usa una sesión con acceso a las fuentes. Si no hay navegación o no se encuentra el mismo empaque de envío, debe abstenerse y devolver evidencia insuficiente. Las medidas no se estiman ni se rellenan.</p></div><div class="research-download-actions"><a class="button primary compact" href="${base}/cases.csv">${icon('download')}Casos CSV</a><a class="button secondary compact" href="${base}/instructions.txt">${icon('download')}Instrucciones</a></div></section>
    <section class="research-import-step"><div class="research-import-heading"><span class="research-step-number">03</span><div><h3>Importa el CSV de resultados</h3><p>Un registro por campo, con URL y cita. Máximo 10 MiB. Los indicadores del asistente no sustituyen tu comprobación.</p></div></div><form id="research-import-form"><div class="research-import-mode" role="group" aria-label="Cómo ingresar resultados"><label><input type="radio" name="research_input_mode" value="file" checked>Subir archivo CSV</label><label><input type="radio" name="research_input_mode" value="paste">Pegar CSV del asistente</label></div><div class="research-import-body"><label class="field" id="research-file-field"><span>Archivo de resultados</span><input type="file" name="file" accept=".csv,text/csv" required></label><label class="field" id="research-paste-field" hidden><span>Pega el CSV de resultados</span><textarea name="pasted_csv" spellcheck="false" placeholder="case_id,status,field,value,unit,measurement_type,source_url,quote,source_title,identity_confirmed,packaging_confirmed"></textarea></label><button type="submit" class="button primary compact">${icon('upload')}Importar resultados</button></div></form><a class="source-link research-results-template" href="${base}/results-template.csv">${icon('download')}Descargar plantilla de respuesta CSV</a><p class="inline-error" id="research-import-error" role="alert" hidden></p><details class="research-format-help"><summary>Formato y significado de los resultados</summary><p>Columnas: <code>case_id, status, field, value, unit, measurement_type, source_url, quote, source_title, identity_confirmed, packaging_confirmed</code>.</p><p><strong>found</strong>: dato encontrado con evidencia. <strong>insufficient</strong>: evidencia insuficiente. <strong>conflict</strong>: fuentes contradictorias. Solo se admite <code>shipping_package</code> como medida del paquete de envío.</p><p>Cuatro campos respaldados pueden generar propuestas pendientes. Peso real o tres dimensiones respaldadas pueden sustentar solo un mínimo. Un mínimo no determina la talla exacta y no se exporta como corrección.</p></details></section>
    ${last?renderResearchImportSummary(last):''}
    <div class="research-cases-heading"><h3>Casos del lote <span class="counter">${integer(cases.length)}</span></h3><p>Las fuentes y citas importadas pueden contener errores. Compruébalas antes de aceptar evidencia o aprobar una propuesta.</p></div>
    <div class="research-case-list">${cases.length?cases.map(renderResearchCase).join(''):'<p class="muted-copy research-empty-cases">Este lote no contiene casos.</p>'}</div>
  `;
  $('#research-import-form').addEventListener('submit',event=>importResearchResults(event,batchId));
  $$('#research-import-form [name="research_input_mode"]').forEach(input=>input.addEventListener('change',()=>{
    const paste=input.value==='paste'&&input.checked;
    $('#research-file-field').hidden=paste;$('#research-paste-field').hidden=!paste;
    $('#research-file-field input').required=!paste;$('#research-paste-field textarea').required=paste;
    $('#research-import-error').hidden=true;
  }));
  $$('[data-research-case-form]').forEach(form=>configureResearchReview(form,batchId));
  $$('[data-research-retry-form]').forEach(form=>form.addEventListener('submit',retryResearchCase));
  $$('[data-research-sku]').forEach(button=>button.addEventListener('click',()=>openDetail(button.dataset.researchSku)));
}

function renderResearchImportSummary(result){
  const errors=Array.isArray(result.errors)?result.errors:[];
  return `<section class="research-import-summary" role="status"><h3>${result.duplicate?'Este archivo ya había sido importado':'Resultado de la importación'}</h3><p>${result.duplicate?'No se crean propuestas duplicadas. Consulta los casos vigentes del lote.':'Los datos quedan registrados para revisión. El formato válido no certifica la fuente ni las medidas.'}</p><dl>${[
    ['Casos importados',result.imported_cases],['Propuestas pendientes',result.proposals_created],['Mínimos propuestos',result.minimum_cases],['Insuficientes',result.insufficient_cases],['En conflicto',result.conflict_cases],['Desactualizados',result.stale_cases]
  ].map(([label,count])=>`<div><dt>${label}</dt><dd>${integer(count)}</dd></div>`).join('')}</dl>${errors.length?`<details class="research-import-errors" open><summary>${integer(errors.length)} incidencias de importación</summary><ul>${errors.map(error=>`<li>${esc(researchErrorText(error))}</li>`).join('')}</ul></details>`:''}</section>`;
}

function renderResearchCase(item){
  const evidence=Array.isArray(item.evidence)?item.evidence:[];
  const minimum=['minimum_proposed','minimum_reviewed'].includes(item.status);
  const canRetry=item.can_retry === true;
  const canReview=item.status==='minimum_proposed' && Boolean(item.minimum_size);
  return `<article class="research-case"><div class="research-case-heading"><div><h4>${esc(item.name || `Caso ${item.id}`)}</h4><p>Caso ${esc(item.id)} · ${integer(item.member_count)} SKUs${item.category?` · ${esc(item.category)}`:''}</p></div><span class="research-case-status ${esc(item.status)}">${esc(researchCaseLabels[item.status] || item.status || 'Por investigar')}</span></div>
    ${item.reason?`<p class="research-case-reason">${esc(item.reason)}</p>`:''}
    ${minimum?`<div class="research-minimum"><strong>Peso aplicable mínimo: al menos ${esc(item.minimum_weight_kg ?? '—')} kg</strong><p>${item.minimum_size?`Umbral inferior de talla: <b>al menos ${esc(item.minimum_size)}</b>. `:''}No determina una talla exacta ni se exporta como corrección. ${item.status==='minimum_reviewed'?'La revisión humana del mínimo quedó registrada.':'Requiere comprobar las fuentes y la compatibilidad del empaque.'}</p>${item.minimum_sku_id?`<p>Alcance: solo el SKU representativo #${esc(item.minimum_sku_id)}${item.minimum_baseline_size?`, con talla base ${esc(item.minimum_baseline_size)}`:''}. No se extrapola automáticamente a todos los SKUs del grupo.</p>`:''}${item.minimum_note?`<p>${esc(item.minimum_note)}</p>`:''}</div>`:''}
    ${item.proposal_count?`<div class="small-notice">${integer(item.proposal_count)} propuestas creadas para revisión. Comprueba la evidencia por campo y continúa en <button type="button" class="text-button" data-view="revisiones">Revisiones</button> para aprobar o rechazar.</div>`:''}
    ${evidence.length?`<details class="research-evidence-details" ${minimum?'open':''}><summary>Evidencia registrada por campo <span>${integer(evidence.length)}</span></summary><div class="research-evidence-list">${evidence.map(renderResearchEvidence).join('')}</div></details>`:'<p class="research-no-evidence">Todavía no hay evidencia por campo. La falta de evidencia no autoriza estimar una medida.</p>'}
    ${canReview?`<form data-research-case-form="${esc(item.id)}" class="research-review-form"><h5>Revisar únicamente el mínimo</h5><label class="field">Nota de revisión<textarea name="note" required maxlength="2000" placeholder="Documenta qué fuentes comprobaste y el alcance de tu decisión."></textarea></label><label class="check-field"><input type="checkbox" name="attestation">Comprobé las fuentes y citas, la identidad del producto y que las medidas corresponden al mismo empaque de envío.</label><div class="research-review-actions"><span>Esta decisión no aprueba una talla exacta ni un cobro.</span><div><button type="button" class="button danger compact" data-research-decision="reject">Rechazar mínimo</button><button type="button" class="button primary compact" data-research-decision="accept">Registrar revisión del mínimo</button></div></div><p class="inline-error research-review-error" role="alert" hidden></p></form>`:''}
    ${Array.isArray(item.prior_evidence)&&item.prior_evidence.length?`<details class="research-evidence-details research-prior-evidence"><summary>Evidencia de la investigación anterior <span>${integer(item.prior_evidence.length)}</span></summary><p class="muted-copy" style="padding:10px 12px">Se conserva como contexto. Debe contrastarse de nuevo; no se hereda una verificación ni se modifica la investigación anterior.</p><div class="research-evidence-list">${item.prior_evidence.map(renderResearchEvidence).join('')}</div></details>`:''}
    ${canRetry?`<details class="research-retry-details"><summary>Reinvestigar este caso</summary><p>Crea otro lote con el estado actual del SKU y conserva la evidencia anterior como contexto. No modifica los resultados previos.</p><form data-research-retry-form="${esc(item.id)}"><label class="field">Motivo de la nueva investigación<textarea name="note" required maxlength="2000" placeholder="Ej. Buscar las dimensiones faltantes del empaque en otra fuente."></textarea></label><div class="form-actions"><button type="submit" class="button secondary compact">Crear nueva investigación</button></div><p class="inline-error research-retry-error" role="alert" hidden></p></form></details>`:''}
    ${item.representative_sku_id?`<button type="button" class="text-button research-open-sku" data-research-sku="${esc(item.representative_sku_id)}">Abrir SKU representativo →</button>`:''}
  </article>`;
}

function renderResearchEvidence(item){
  const source=safeLink(item.source_url);
  return `<section class="research-evidence-item"><div class="research-field-heading"><strong>${esc(researchFieldLabels[item.field] || item.field || 'Campo')}</strong><span>${esc(item.value ?? 'Sin valor')} ${esc(item.unit || '')}</span></div><div class="research-source-meta"><span>${esc(item.measurement_type==='shipping_package'?'Paquete de envío':item.measurement_type || 'Tipo de medida no informado')}</span><span>Identidad declarada: ${item.identity_confirmed?'sí':'no'}</span><span>Empaque declarado: ${item.packaging_confirmed?'sí':'no'}</span></div>${item.normalized_value!=null?`<p class="research-normalized">Valor normalizado: ${esc(item.normalized_value)} ${item.field==='weight_g'?'g':'cm'}</p>`:''}<p class="research-source-title">${esc(item.source_title || 'Fuente sin título')}</p>${source?`<a class="source-link" href="${esc(source)}" target="_blank" rel="noopener noreferrer">Abrir fuente ${icon('external')}</a>`:'<p class="muted-copy">Sin enlace de fuente utilizable.</p>'}${item.quote?`<blockquote>${esc(item.quote)}</blockquote>`:'<p class="muted-copy">No se registró una cita.</p>'}${item.eligible===false?`<p class="research-ineligible">${esc(item.ineligibility_reason || 'La evidencia no cumple los requisitos para sustentar una propuesta.')}</p>`:''}<p class="research-evidence-caution">Declaraciones del archivo importado. La aplicación no ha visitado la URL ni comprobado la cita.</p></section>`;
}

async function importResearchResults(event,batchId){
  event.preventDefault();const form=event.currentTarget,input=$('input[type="file"]',form),button=$('button[type="submit"]',form),errorEl=$('#research-import-error');
  let file=input.files[0];
  const fail=message=>{errorEl.textContent=message;errorEl.hidden=false;};errorEl.hidden=true;
  if($('[name="research_input_mode"]:checked',form).value==='paste'){
    let csv=$('[name="pasted_csv"]',form).value.trim();
    if(!csv)return fail('Pega el CSV de resultados antes de importar.');
    const fenced=csv.match(/^```(?:csv)?[ \t]*\r?\n([\s\S]*)\r?\n```[ \t]*$/i);
    if(fenced)csv=fenced[1];
    file=new File([csv],'resultados-pegados.csv',{type:'text/csv;charset=utf-8'});
  }
  if(!file)return fail('Selecciona el CSV de resultados.');
  if(!/\.csv$/i.test(file.name))return fail('Los resultados deben ser un archivo CSV.');
  if(file.size>10*1024*1024)return fail('El archivo supera el máximo de 10 MiB. Divide los resultados en archivos más pequeños.');
  button.disabled=true;button.textContent='Importando…';
  const body=new FormData();body.append('file',file);
  let saved=false;
  try{
    const result=await api(`/api/research/batches/${encodeURIComponent(batchId)}/results`,{method:'POST',body});
    saved=true;researchState.lastImport={batchId,result};
    await Promise.all([loadResearchBatch(batchId),loadSummary()]);
    toast(result.duplicate?'Este archivo ya estaba importado. No se duplicaron propuestas.':'Resultados registrados. Revisa las fuentes antes de decidir.');
  }catch(error){
    if(saved){toast('Los resultados se registraron, pero no pudimos actualizar la vista. Vuelve a abrir el lote.',true);}
    else{fail(error.message);button.disabled=false;button.textContent='Importar resultados';}
  }
}

function configureResearchReview(form,batchId){
  form.addEventListener('submit',event=>event.preventDefault());
  $$('[data-research-decision]',form).forEach(button=>button.addEventListener('click',()=>reviewResearchMinimum(form,batchId,button.dataset.researchDecision)));
}

async function reviewResearchMinimum(form,batchId,decision){
  const note=$('[name="note"]',form).value.trim(),attested=$('[name="attestation"]',form).checked,errorEl=$('.research-review-error',form);
  const fail=message=>{errorEl.textContent=message;errorEl.hidden=false;};errorEl.hidden=true;
  if(!note)return fail('Registra una nota humana de revisión para continuar.');
  if(decision==='accept'&&!attested)return fail('Comprueba las fuentes, las citas, la identidad y el empaque antes de registrar el mínimo.');
  const buttons=$$('button',form);buttons.forEach(button=>button.disabled=true);form.setAttribute('aria-busy','true');
  let saved=false;
  try{
    await api(`/api/research/cases/${encodeURIComponent(form.dataset.researchCaseForm)}/review`,{method:'POST',body:JSON.stringify({decision,note,attestation:attested})});
    saved=true;await Promise.all([loadResearchBatch(batchId),loadSummary()]);
    toast(decision==='accept'?'Revisión del mínimo registrada. No se aprobó una talla exacta ni un cobro.':'Mínimo rechazado. La nota de revisión quedó registrada.');
  }catch(error){
    if(saved)toast('La revisión quedó registrada, pero no pudimos actualizar la vista. Vuelve a abrir el lote.',true);
    else{fail(error.message);buttons.forEach(button=>button.disabled=false);form.removeAttribute('aria-busy');}
  }
}

async function retryResearchCase(event){
  event.preventDefault();const form=event.currentTarget,note=$('[name="note"]',form).value.trim(),errorEl=$('.research-retry-error',form),button=$('button[type="submit"]',form);
  errorEl.hidden=true;if(!note){errorEl.textContent='Documenta el motivo para abrir una nueva investigación.';errorEl.hidden=false;return;}
  button.disabled=true;let saved=false;
  try{
    const batch=await api(`/api/research/cases/${encodeURIComponent(form.dataset.researchRetryForm)}/retry`,{method:'POST',body:JSON.stringify({note})});
    saved=true;researchState.selected=batch.id;researchState.lastImport=null;await loadResearch();
    toast('Nueva investigación creada. La evidencia anterior se conserva como contexto.');
  }catch(error){if(saved)toast('El nuevo lote quedó creado, pero no pudimos actualizar la vista. Vuelve a abrir Investigación.',true);else{errorEl.textContent=error.message;errorEl.hidden=false;button.disabled=false;}}
}
