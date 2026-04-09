/**
 * SVG Screenshot Export
 *
 * Exports the entire OneManCompany UI as a single SVG file.
 * - Canvas office scene → pure vector via canvas2svg (C2S)
 * - HTML panels → foreignObject with inline styles
 */

function exportSVG() {
  const renderer = window.officeRenderer;
  if (!renderer) { alert('Office renderer not ready'); return; }

  const app = document.getElementById('app');
  const appRect = app.getBoundingClientRect();
  const W = Math.round(appRect.width);
  const H = Math.round(appRect.height);

  // ── 1. Render Canvas scene to SVG via C2S ──────────────────────────────
  const canvasEl = renderer.canvas;
  const canvasRect = canvasEl.getBoundingClientRect();
  const cw = Math.round(canvasRect.width);
  const ch = Math.round(canvasRect.height);

  const svgCtx = new C2S(cw, ch);

  // Swap context: all sub-methods read this.ctx
  const origCtx = renderer.ctx;
  const origDpr = renderer.dpr;
  renderer.ctx = svgCtx;
  renderer.dpr = 1;  // SVG is resolution-independent

  try {
    renderer.render();
  } finally {
    renderer.ctx = origCtx;
    renderer.dpr = origDpr;
  }

  const canvasSvgStr = svgCtx.getSerializedSvg(true);

  // ── 2. Collect styles for foreignObject ────────────────────────────────
  const styles = _collectStyles();

  // ── 3. Build composite SVG ─────────────────────────────────────────────
  const canvasOffX = Math.round(canvasRect.left - appRect.left);
  const canvasOffY = Math.round(canvasRect.top - appRect.top);

  const panels = [
    'left-top-panel', 'left-bottom-panel',
    'roster-panel', 'console-panel',
  ];

  // Also capture the office-panel header (toolbar)
  const officePanel = document.getElementById('office-panel');
  const panelHeader = officePanel ? officePanel.querySelector('.panel-header') : null;

  let foreignObjects = '';
  for (const id of panels) {
    const el = document.getElementById(id);
    if (!el) continue;
    foreignObjects += _panelToForeignObject(el, appRect);
  }
  if (panelHeader) {
    foreignObjects += _panelToForeignObject(panelHeader, appRect);
  }

  const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
  <defs>
    <style type="text/css"><![CDATA[
${styles}
    ]]></style>
  </defs>
  <!-- Background -->
  <rect width="${W}" height="${H}" fill="#0d1117"/>
  <!-- Canvas (vector) -->
  <g transform="translate(${canvasOffX},${canvasOffY})">
    ${canvasSvgStr}
  </g>
  <!-- HTML panels -->
  ${foreignObjects}
</svg>`;

  // ── 4. Download ────────────────────────────────────────────────────────
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `onemancompany-${ts}.svg`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── Helpers ────────────────────────────────────────────────────────────────

/** Collect all stylesheet rules as a string for embedding in SVG. */
function _collectStyles() {
  const lines = [];
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        lines.push(rule.cssText);
      }
    } catch (_) {
      // cross-origin stylesheets (e.g. Google Fonts) can't be read;
      // we'll inline the font-family references instead
    }
  }
  return lines.join('\n');
}

/** Serialize a DOM panel into a <foreignObject> positioned at its layout offset. */
function _panelToForeignObject(el, appRect) {
  const r = el.getBoundingClientRect();
  const x = Math.round(r.left - appRect.left);
  const y = Math.round(r.top - appRect.top);
  const w = Math.round(r.width);
  const h = Math.round(r.height);

  // Clone and inline computed styles on top-level element
  const clone = el.cloneNode(true);

  // Remove script tags from clone
  for (const s of clone.querySelectorAll('script')) s.remove();

  const html = new XMLSerializer().serializeToString(clone);

  return `<foreignObject x="${x}" y="${y}" width="${w}" height="${h}">
    <div xmlns="http://www.w3.org/1999/xhtml" style="width:${w}px;height:${h}px;overflow:hidden;">
      ${html}
    </div>
  </foreignObject>\n`;
}

window.exportSVG = exportSVG;
