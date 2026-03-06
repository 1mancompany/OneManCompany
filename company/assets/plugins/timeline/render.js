/**
 * Timeline view plugin — render function
 * Enhanced: task type coloring, status indication, dependency arrows, estimated duration
 */
function renderTimeline(container, data, ctx) {
  const escHtml = ctx.escHtml;
  const timeline = data.timeline || [];

  if (timeline.length === 0) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:6px;padding:8px;">No dispatches with timing data yet</div>';
    return;
  }

  // Task type color map
  const typeColors = {
    execution: '#00ddff',
    acceptance: '#ffdd00',
    ea_review: '#ff9900',
    rectification: '#ff4455',
    hiring: '#51cf66',
  };

  const typeLabels = {
    execution: 'Exec',
    acceptance: 'Accept',
    ea_review: 'EA',
    rectification: 'Fix',
    hiring: 'Hire',
  };

  // Calculate time range
  const starts = timeline.map(t => new Date(t.start).getTime());
  const ends = timeline.map(t => t.end ? new Date(t.end).getTime() : Date.now());
  const minTime = Math.min(...starts);
  const maxTime = Math.max(...ends);
  const range = maxTime - minTime || 1;

  // Build a dispatch_id -> index map for dependency arrows
  const idxMap = {};
  timeline.forEach((t, i) => { if (t.dispatch_id) idxMap[t.dispatch_id] = i; });

  let html = '<div class="timeline-view" style="position:relative;">';

  // SVG overlay for dependency arrows
  const rowHeight = 22; // must match CSS .timeline-row height + margin
  const svgHeight = timeline.length * rowHeight;
  html += `<svg class="timeline-dep-arrows" style="position:absolute;top:0;left:60px;right:35px;height:${svgHeight}px;pointer-events:none;overflow:visible;">`;

  // Collect arrow data
  const arrows = [];
  timeline.forEach((t, i) => {
    const deps = t.depends_on || [];
    for (const depId of deps) {
      if (depId in idxMap) {
        arrows.push({ from: idxMap[depId], to: i });
      }
    }
  });

  for (const arrow of arrows) {
    const fromT = timeline[arrow.from];
    const toT = timeline[arrow.to];
    const fromEndMs = fromT.end ? new Date(fromT.end).getTime() : Date.now();
    const toStartMs = new Date(toT.start).getTime();
    const x1Pct = ((fromEndMs - minTime) / range) * 100;
    const x2Pct = ((toStartMs - minTime) / range) * 100;
    const y1 = arrow.from * rowHeight + rowHeight / 2;
    const y2 = arrow.to * rowHeight + rowHeight / 2;
    html += `<line x1="${x1Pct}%" y1="${y1}" x2="${x2Pct}%" y2="${y2}" class="dep-arrow-line"/>`;
    // Arrowhead
    html += `<circle cx="${x2Pct}%" cy="${y2}" r="2" class="dep-arrow-head"/>`;
  }
  html += '</svg>';

  for (const t of timeline) {
    const startMs = new Date(t.start).getTime();
    const endMs = t.end ? new Date(t.end).getTime() : Date.now();
    const left = ((startMs - minTime) / range) * 100;
    const width = Math.max(((endMs - startMs) / range) * 100, 2);
    const durationMin = Math.round((endMs - startMs) / 60000);
    const durationStr = durationMin < 60 ? `${durationMin}m` : `${Math.round(durationMin / 60)}h`;

    const taskType = t.task_type || 'execution';
    const color = typeColors[taskType] || '#00ddff';
    const status = t.status || (t.end ? 'completed' : 'in_progress');

    // Status CSS class
    let statusClass = '';
    if (status === 'in_progress') statusClass = ' timeline-bar--running';
    else if (status === 'pending') statusClass = ' timeline-bar--pending';

    // Build bar tooltip
    const typeLabel = typeLabels[taskType] || taskType;
    const tooltip = `[${typeLabel}] ${t.description}`;

    html += `<div class="timeline-row">`;
    html += `<div class="timeline-label" title="${escHtml(t.employee_name)}">${escHtml(t.employee_name)}</div>`;
    html += `<div class="timeline-bar-container">`;

    // Estimated duration bar (ghost/comparison)
    if (t.estimated_duration_min && t.estimated_duration_min > 0) {
      const estMs = t.estimated_duration_min * 60000;
      const estWidth = Math.max(((estMs) / range) * 100, 2);
      html += `<div class="timeline-bar timeline-bar--estimated" style="left:${left.toFixed(1)}%;width:${estWidth.toFixed(1)}%;background:${color};" title="Estimated: ${t.estimated_duration_min}min"></div>`;
    }

    // Actual bar
    html += `<div class="timeline-bar${statusClass}" data-type="${taskType}" style="left:${left.toFixed(1)}%;width:${width.toFixed(1)}%;background:${color};" title="${escHtml(tooltip)}"></div>`;
    html += `</div>`;

    const statusText = status === 'in_progress' ? 'running' : (t.end ? durationStr : 'pending');
    html += `<div class="timeline-duration">${statusText}</div>`;
    html += `</div>`;
  }
  html += '</div>';

  // Task type legend
  const usedTypes = [...new Set(timeline.map(t => t.task_type || 'execution'))];
  if (usedTypes.length > 0) {
    html += '<div style="font-size:4px;color:var(--text-dim);margin-top:4px;">';
    for (const tt of usedTypes) {
      const c = typeColors[tt] || '#888';
      const label = typeLabels[tt] || tt;
      html += `<span style="margin-right:6px;"><span style="display:inline-block;width:6px;height:4px;background:${c};margin-right:2px;vertical-align:middle;"></span>${label}</span>`;
    }
    html += '</div>';
  }

  container.innerHTML = html;
}
