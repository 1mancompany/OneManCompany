/**
 * Kanban board plugin — render function
 */
function renderKanban(container, data, ctx) {
  const escHtml = ctx.escHtml;
  const columns = data.columns || {};
  const phases = data.phases || [];

  // Phase summary
  let phaseHtml = '';
  if (phases.length > 0) {
    phaseHtml = '<div style="margin-bottom:4px;font-size:5px;color:var(--text-dim);">';
    for (const p of phases) {
      const pctDone = p.total > 0 ? Math.round((p.completed / p.total) * 100) : 0;
      phaseHtml += `<span style="margin-right:6px;">P${p.phase}: ${p.completed}/${p.total} (${pctDone}%)</span>`;
    }
    phaseHtml += '</div>';
  }

  const statusLabels = { pending: 'Pending', in_progress: 'In Progress', completed: 'Completed' };
  let boardHtml = '<div class="kanban-board">';
  for (const status of ['pending', 'in_progress', 'completed']) {
    const cards = columns[status] || [];
    boardHtml += `<div class="kanban-column">`;
    boardHtml += `<div class="kanban-column-title">${statusLabels[status]} (${cards.length})</div>`;
    if (cards.length === 0) {
      boardHtml += `<div style="font-size:4px;color:var(--text-dim);text-align:center;padding:4px;">-</div>`;
    }
    for (const card of cards) {
      boardHtml += `<div class="kanban-card" data-phase="${card.phase}">`;
      boardHtml += `<div class="card-employee">${escHtml(card.employee_name)}</div>`;
      boardHtml += `<div class="card-desc">${escHtml(card.description)}</div>`;
      boardHtml += `<span class="card-phase">P${card.phase}</span>`;
      boardHtml += `</div>`;
    }
    boardHtml += `</div>`;
  }
  boardHtml += '</div>';

  container.innerHTML = phaseHtml + boardHtml;
}
