'use strict';

let groupRequest = 0;
let groupPageCount = 1;
let groupPaginationReady = false;

function initializeGroupPagination() {
  if (groupPaginationReady) return;
  const previous = document.querySelector('#prev-group-page');
  const next = document.querySelector('#next-group-page');
  if (!previous || !next) return;
  previous.addEventListener('click', () => {
    state.groupPage = Math.max(1, (state.groupPage || 1) - 1);
    loadGroups().catch(error => toast(error.message, true));
  });
  next.addEventListener('click', () => {
    state.groupPage = Math.min(groupPageCount, (state.groupPage || 1) + 1);
    loadGroups().catch(error => toast(error.message, true));
  });
  groupPaginationReady = true;
}

function groupMemberLabel(group, proposalId) {
  const member = Array.isArray(group.members)
    ? group.members.find(item => Number(item.proposal_id ?? item.id) === proposalId)
    : null;
  if (!member) return `<strong>Propuesta #${integer(proposalId)}</strong><small>Identificador de la propuesta pendiente</small>`;
  return `<strong>${esc(member.sku || `Propuesta #${proposalId}`)}${member.seller ? ` <span>· ${esc(member.seller)}</span>` : ''}</strong><small>${esc(member.name || `Propuesta #${proposalId}`)}</small>`;
}

function renderGroupCard(group, index) {
  const proposalIds = [...new Set((group.proposal_ids || []).map(Number))]
    .filter(value => Number.isSafeInteger(value) && value > 0).slice(0, 500);
  const source = safeLink(group.source_url);
  const count = Number(group.count) || proposalIds.length;
  const omitted = Math.max(0, count - proposalIds.length);
  const basisCopy = {
    billed_vs_proposed: 'Talla de cobro declarada → talla propuesta.',
    billed_vs_calculated: 'Talla de cobro declarada → talla calculada.',
    calculated_vs_proposed: 'Talla calculada del catálogo → talla propuesta.',
    unknown: 'No hay una base suficiente para determinar el cambio.'
  };
  return `<article class="group-card" data-group-card="${index}">
    <div class="group-card-heading">
      <div class="group-product-icon">${icon('box')}</div>
      <div class="group-card-title"><h3>${esc(group.name || 'Producto sin nombre')}</h3><p>${integer(count)} propuestas · ${integer(group.seller_count)} sellers · Evidencia compartida</p></div>
      <div class="group-change">${directionBadge(group)}</div>
    </div>
    <p class="group-basis">${esc(basisCopy[group.direction_basis] || 'Consulta la base de comparación de cada SKU antes de decidir. El cambio no certifica las medidas físicas.')}</p>
    <div class="group-evidence">
      <div class="group-evidence-heading"><strong>Evidencia que respalda el grupo</strong>${source ? `<a class="source-link" href="${esc(source)}" target="_blank" rel="noopener noreferrer">Consultar fuente ${icon('external')}</a>` : '<span class="muted-copy">Sin enlace disponible</span>'}</div>
      ${group.reason ? `<p class="group-reason">${esc(group.reason)}</p>` : ''}
      <p class="group-evidence-note">${esc(group.evidence_note || 'Revisa la evidencia individual antes de aprobar esta selección.')}</p>
    </div>
    <form class="group-decision-form" data-group-form="${index}">
      <details class="group-members" open>
        <summary>Seleccionar propuestas <span>${integer(proposalIds.length)} visibles</span></summary>
        <div class="group-selection-toolbar"><span class="group-selected-count" aria-live="polite">0 seleccionadas</span><div><button type="button" class="text-button" data-group-select="all">Seleccionar visibles</button><span aria-hidden="true"> · </span><button type="button" class="text-button" data-group-select="none">Limpiar</button></div></div>
        <div class="group-member-list">${proposalIds.map(id => `<label class="group-member"><input type="checkbox" name="proposal_ids" value="${id}"><span>${groupMemberLabel(group, id)}</span></label>`).join('') || '<p class="muted-copy">No hay propuestas seleccionables en este grupo.</p>'}</div>
      </details>
      ${omitted ? `<div class="small-notice warning">Este grupo contiene ${integer(count)} propuestas. Se muestran hasta ${integer(proposalIds.length)} por decisión. Después de procesarlas, recarga el grupo para revisar las ${integer(omitted)} restantes.</div>` : ''}
      <label class="field group-note">Nota de revisión <span class="group-field-hint">Obligatoria para rechazar.</span><textarea name="note" maxlength="2000" placeholder="Documenta el criterio aplicado a la selección."></textarea></label>
      <label class="check-field group-attestation"><input type="checkbox" name="attestation">He revisado la evidencia y confirmo que las propuestas seleccionadas corresponden al mismo producto, presentación y empaque de envío.</label>
      <div class="group-decision-footer"><span>Se aplicará únicamente a las propuestas marcadas.</span><div class="group-decision-actions"><button type="button" class="button danger compact" data-group-decision="reject" ${proposalIds.length ? '' : 'disabled'}>Rechazar selección</button><button type="button" class="button primary compact" data-group-decision="approve" ${proposalIds.length ? '' : 'disabled'}>Aprobar selección</button></div></div>
      <p class="inline-error group-error" role="alert" hidden></p>
    </form>
  </article>`;
}

async function loadGroups() {
  initializeGroupPagination();
  const target = document.querySelector('#groups-content');
  if (!target) return;
  const request = ++groupRequest;
  const page = state.groupPage || 1;
  const params = new URLSearchParams({page, page_size: 20, direction: state.direction || 'all'});
  target.setAttribute('aria-busy', 'true');
  try {
    const data = await api(`/api/groups?${params}`);
    if (request !== groupRequest) return;
    groupPageCount = Math.max(1, data.pages || Math.ceil(data.total / 20));
    state.groupPage = data.page || page;
    if (state.groupPage > groupPageCount) {
      state.groupPage = groupPageCount;
      return await loadGroups();
    }
    target.innerHTML = `<div class="group-review-intro"><strong>Revisa una evidencia, selecciona sus propuestas.</strong><p>Los grupos reúnen propuestas pendientes que comparten una referencia y el mismo cambio de talla. La selección comienza vacía. Cada aprobación requiere tu revisión; compartir fuente no demuestra por sí solo que las medidas sean correctas.</p></div>${data.items.length
      ? data.items.map(renderGroupCard).join('')
      : `<div class="empty-state"><div class="empty-illustration">${icon('check-square')}</div><h3>No hay grupos pendientes con este filtro</h3><p>Las propuestas respaldadas por referencias aparecerán aquí. Puedes revisar las alertas sin propuesta en la vista por SKU.</p></div>`}`;
    data.items.forEach((group, index) => configureGroupForm(document.querySelector(`[data-group-form="${index}"]`)));
    const total = Number(data.total) || 0;
    document.querySelector('#table-count').textContent=integer(total);
    const start = total ? (state.groupPage - 1) * 20 + 1 : 0;
    document.querySelector('#group-pagination-label').textContent = `${integer(start)}–${integer(Math.min(state.groupPage * 20, total))} de ${integer(total)} grupos`;
    document.querySelector('#group-page-label').textContent = `${state.groupPage} / ${groupPageCount}`;
    document.querySelector('#prev-group-page').disabled = state.groupPage <= 1;
    document.querySelector('#next-group-page').disabled = state.groupPage >= groupPageCount;
  } finally {
    if (request === groupRequest) target.removeAttribute('aria-busy');
  }
}

function configureGroupForm(form) {
  if (!form) return;
  const inputs = [...form.querySelectorAll('input[name="proposal_ids"]')];
  const update = () => {
    const selected = inputs.filter(input => input.checked).length;
    form.querySelector('.group-selected-count').textContent = `${integer(selected)} ${selected === 1 ? 'seleccionada' : 'seleccionadas'}`;
  };
  inputs.forEach(input => input.addEventListener('change', update));
  form.querySelectorAll('[data-group-select]').forEach(button => button.addEventListener('click', () => {
    inputs.forEach(input => { input.checked = button.dataset.groupSelect === 'all'; });
    update();
  }));
  form.querySelectorAll('[data-group-decision]').forEach(button => button.addEventListener('click', () => submitGroupDecision(form, button.dataset.groupDecision)));
  form.addEventListener('submit', event => event.preventDefault());
}

async function submitGroupDecision(form, decision) {
  const errorElement = form.querySelector('.group-error');
  errorElement.hidden = true;
  const proposalIds = [...form.querySelectorAll('input[name="proposal_ids"]:checked')].map(input => Number(input.value));
  const note = form.querySelector('[name="note"]').value.trim();
  const attested = form.querySelector('[name="attestation"]').checked;
  const fail = message => { errorElement.textContent = message; errorElement.hidden = false; };
  if (!proposalIds.length) return fail('Marca al menos una propuesta para continuar.');
  if (proposalIds.length > 500) return fail('Selecciona como máximo 500 propuestas por decisión.');
  if (decision === 'approve' && !attested) return fail('Confirma que revisaste la evidencia y su compatibilidad con el empaque de envío.');
  if (decision === 'reject' && !note) return fail('Registra el motivo del rechazo antes de continuar.');
  const buttons = [...form.querySelectorAll('button')];
  buttons.forEach(button => { button.disabled = true; });
  form.setAttribute('aria-busy', 'true');
  let result;
  try {
    result = await api('/api/groups/decision', {method:'POST', body: JSON.stringify({proposal_ids:proposalIds, decision, note})});
  } catch (error) {
    fail(`${error.message} No se confirmó toda la selección. Actualiza la bandeja para comprobar los casos vigentes.`);
    toast('No se pudo confirmar toda la selección. Revisa el detalle del grupo.', true);
    buttons.forEach(button => { button.disabled = false; });
    form.removeAttribute('aria-busy');
    return;
  }
  const failures = Number(result?.failed || result?.failed_count || 0);
  const errorItems = Array.isArray(result?.errors) ? result.errors : [];
  try {
    await Promise.all([loadGroups(), loadSummary()]);
  } catch (error) {
    fail(`El servidor respondió a la decisión, pero no se pudo actualizar la bandeja: ${error.message} Recarga para consultar el resultado.`);
    toast('La decisión fue recibida. Recarga para comprobar el estado actualizado.', true);
    form.removeAttribute('aria-busy');
    return;
  }
  if (failures || errorItems.length) {
    toast('La decisión tuvo incidencias. Revisa las propuestas que siguen pendientes; no se confirmó toda la selección.', true);
  } else {
    toast('Decisión registrada. La bandeja muestra las propuestas que siguen pendientes.');
  }
}
