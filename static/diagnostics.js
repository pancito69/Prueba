'use strict';

const diagReasonLabels = {
  category_real_below_min:'Peso real menor al rango de categoría',
  category_real_above_max:'Peso real mayor al rango de categoría',
  category_volume_below_min:'Peso volumétrico menor al rango de categoría',
  category_volume_above_max:'Peso volumétrico mayor al rango de categoría',
  billed_size_mismatch:'Talla declarada distinta del cálculo',
  invalid_physical_data:'Datos físicos inválidos o incompletos',
  table_gap:'Peso entre intervalos de la tabla',
  above_table:'Peso superior a 500 kg',
  volume_weight_ratio:'Relación volumen / peso para revisar',
  proposal_waiting:'Propuesta de corrección pendiente',
  billed_size_invalid:'Talla de cobro declarada no válida'
};
const diagVerificationLabels = {
  unverified:['Sin evidencia registrada','El cálculo por sí solo no verifica las medidas.'],
  declared_reference:['Referencia declarada','Marcada como verificada al importar; requiere respaldo.'],
  evidence_registered:['Evidencia registrada','Existe una propuesta con fuente; aún requiere revisión.'],
  approved:['Corrección aprobada','Una persona revisó la propuesta y dejó su aprobación.']
};

function diagMetric(label,value,description,kind='') {
  return `<article class="metric diag-metric ${kind}"><div class="metric-label">${esc(label)}</div><strong>${integer(value)}</strong><small>${esc(description)}</small></article>`;
}

async function loadDiagnostics() {
  const target=$('#diagnostics-content');
  target.innerHTML='<div class="panel loading-cell"><span class="spinner"></span><p>Preparando diagnóstico del catálogo…</p></div>';
  try {
    const data=await api('/api/diagnostics');
    const research=data.research || {}, directions=data.directions || {}, verification=data.verification || {};
    const reasons=Array.isArray(data.reasons)?data.reasons:[];
    const maxReason=Math.max(1,...reasons.map(reason=>Number(reason.count)||0));
    const ai=data.ai || {enabled:false,external_requests:0};
    target.innerHTML=`
      <div class="diag-intro"><span class="diag-intro-icon">${icon('info')}</span><div><strong>Calculado no significa físicamente verificado.</strong><p>Este diagnóstico identifica reglas y evidencia pendientes. Las alertas son señales para investigar; no demuestran que una medida esté equivocada.</p></div></div>
      <div class="metrics diag-top-metrics">
        ${diagMetric('SKUs analizados',data.total,'Catálogo completo del espacio de trabajo')}
        ${diagMetric('Casos con atención',data.attention,'Por revisar y pendientes, sin duplicar registros','attention')}
        ${diagMetric('Productos identificables',research.unique_products,'Grupos únicos entre los casos con atención')}
        ${diagMetric('Solicitudes de IA externa',ai.external_requests,ai.enabled?'Estado informado por el servicio':'IA externa desactivada')}
      </div>
      <div class="diag-grid">
        <section class="panel diag-card diag-reasons" aria-labelledby="diag-reasons-title">
          <div class="diag-card-heading"><div><h2 id="diag-reasons-title">¿Qué necesita atención?</h2><p>Abre un motivo para revisar los SKUs relacionados.</p></div><span class="subtle-tag">POR MOTIVO</span></div>
          <div class="diag-reason-list">${reasons.length?reasons.map(reason=>`<button type="button" class="diag-reason" data-diag-reason="${esc(reason.code)}"><span class="diag-reason-title">${esc(reason.label || diagReasonLabels[reason.code] || reason.code)}<strong>${integer(reason.count)}</strong></span><span class="diag-track"><span style="width:${Math.max(0,Math.min(100,Number(reason.count)/maxReason*100))}%"></span></span></button>`).join(''):'<p class="muted-copy">No se informaron motivos de atención.</p>'}</div>
          <p class="diag-caption">Un SKU puede tener más de un motivo; los conteos por causa pueden superponerse.</p>
          <div class="diag-state-help"><div><strong>Por revisar</strong><p>Hay una alerta heurística o una propuesta que necesita evaluación de evidencia.</p></div><div><strong>Pendiente</strong><p>Faltan datos válidos o una regla no permite asignar talla: huecos de intervalos o peso fuera de tabla.</p></div></div>
          <div class="diag-export-actions"><a class="button primary compact" href="/api/diagnostics.csv">${icon('download')}Exportar casos con atención</a><a class="button secondary compact" href="/api/diagnostics.csv?scope=all">Catálogo completo</a></div>
        </section>
        <section class="panel diag-card" aria-labelledby="diag-directions-title">
          <div class="diag-card-heading"><div><h2 id="diag-directions-title">Dirección de la comparación</h2><p>Prioriza las diferencias que llevan a una talla mayor.</p></div></div>
          <div class="diag-directions">${[
            ['up','↑','Talla mayor','Comparación hacia una talla superior'],
            ['down','↓','Talla menor','Comparación hacia una talla inferior'],
            ['same','→','Misma talla','Sin cambio de talla en la comparación'],
            ['unknown','—','Sin base','Información insuficiente para comparar']
          ].map(([code,symbol,label,description])=>`<button class="diag-direction ${code}" data-diag-direction="${code}" title="${esc(description)}"><span class="diag-direction-symbol">${symbol}</span><span>${label}<strong>${integer(directions[code])}</strong></span>${icon('chevron-right')}</button>`).join('')}</div>
          <p class="diag-caption">Cuando existe una talla de cobro declarada, se compara con el cálculo o propuesta. Si falta, una propuesta puede compararse con el cálculo del catálogo. Sin una base comparable, la dirección queda desconocida.</p>
          <div class="small-notice">Una talla mayor es una prioridad de revisión. No confirma subcobro, exactitud de medidas ni impacto monetario.</div>
        </section>
        <section class="panel diag-card" aria-labelledby="diag-verification-title">
          <div class="diag-card-heading"><div><h2 id="diag-verification-title">Cálculo y evidencia, por separado</h2><p>Consulta qué respaldo está registrado para los datos.</p></div></div>
          <dl class="diag-verification">${Object.entries(diagVerificationLabels).map(([code,[label,description]])=>`<div><dt>${label}<small>${description}</small></dt><dd>${integer(verification[code])}</dd></div>`).join('')}</dl>
          <p class="diag-caption">La referencia declarada y la evidencia registrada describen el estado del piloto. La herramienta no comprueba automáticamente las páginas de origen.</p>
        </section>
        <section class="panel diag-card" aria-labelledby="diag-research-title">
          <div class="diag-card-heading"><div><h2 id="diag-research-title">Preparar la investigación</h2><p>Resuelve referencias por producto y reutilízalas donde sean compatibles.</p></div></div>
          <dl class="diag-research">${[
            ['Filas con atención',research.attention_rows],
            ['Filas con identidad suficiente',research.identifiable_rows],
            ['Productos únicos identificables',research.unique_products],
            ['Filas sin identidad suficiente',research.missing_identity_rows],
            ['Filas con propuesta',research.with_proposal_rows],
            ['Filas sin propuesta',research.without_proposal_rows]
          ].map(([label,value])=>`<div><dt>${label}</dt><dd>${integer(value)}</dd></div>`).join('')}</dl>
          <div class="small-notice">La identidad debe coincidir en producto, variante, presentación y empaque de envío. Un nombre parecido o varios sellers con el mismo dato no prueban que las medidas sean correctas.</div>
        </section>
      </div>
      <section class="panel diag-card diag-ai" aria-labelledby="diag-ai-title">
        <div class="diag-card-heading"><div><div class="eyebrow">ANTES DE CONECTAR UN PROVEEDOR</div><h2 id="diag-ai-title">IA y costos de investigación</h2><p>Este piloto calcula y reutiliza referencias sin realizar búsquedas externas con IA.</p></div><span class="diag-ai-status">${icon('shield')}${ai.enabled?'Estado externo activo':'IA externa desactivada'}</span></div>
        <div class="diag-cost-grid">
          <article><span class="diag-cost-index">01</span><h3>Lo que ya hace el piloto</h3><p>Cálculo de tallas, reglas, diagnóstico, agrupación y referencias importadas. No generan consultas a una API de IA. Las fuentes se registran manualmente; no hay búsqueda web integrada.</p></article>
          <article><span class="diag-cost-index">02</span><h3>Una API se factura por separado</h3><p>Conectar un proveedor requeriría su propia configuración y facturación. Una suscripción a ChatGPT no aporta saldo para la API. Modelo, búsqueda y volumen de uso determinarían el costo.</p></article>
          <article><span class="diag-cost-index">03</span><h3>Un modelo local requiere recursos</h3><p>Puede evitar una tarifa por consulta del proveedor, pero necesita hardware, memoria, ejecución y mantenimiento. La calidad de la evidencia debe validarse igual.</p></article>
        </div>
        <div class="diag-budget"><div><strong>Dimensionar por productos únicos, no por filas del Excel.</strong><p>Primero identificar productos, reutilizar evidencia y cachear resultados. Antes de activar IA: fijar topes de gasto y solicitudes, controlar reintentos y probar calidad con una muestra. Los grupos identificables son una base de planificación; no equivalen a consultas necesarias ni a un presupuesto aprobado.</p></div><span class="diag-api-count">${integer(ai.external_requests)}<small>solicitudes externas<br>informadas por el piloto</small></span></div>
      </section>
      ${Array.isArray(data.notes)&&data.notes.length?`<div class="diag-notes"><h3>Cómo interpretar este diagnóstico</h3><ul>${data.notes.map(note=>`<li>${esc(note)}</li>`).join('')}</ul></div>`:''}
    `;
    $$('[data-diag-reason]',target).forEach(button=>button.addEventListener('click',()=>applyDiagnosticFilter(button.dataset.diagReason)));
    $$('[data-diag-direction]',target).forEach(button=>button.addEventListener('click',()=>applyDiagnosticFilter('all',button.dataset.diagDirection)));
  } catch(error) {
    target.innerHTML=`<div class="panel empty-state"><h3>No pudimos preparar el diagnóstico</h3><p>${esc(error.message)}</p><button id="diag-retry" class="button secondary">Volver a intentar</button></div>`;
    $('#diag-retry').addEventListener('click',loadDiagnostics);
    toast(error.message,true);
  }
}
