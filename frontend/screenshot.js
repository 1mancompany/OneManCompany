/**
 * SVG Screenshot Export
 *
 * Exports the entire OneManCompany UI as a single SVG file.
 * - Canvas office scene → pure vector via canvas2svg (C2S)
 * - HTML panels → foreignObject with inline styles
 *
 * Known limitation: drawImage sprites are embedded as base64 data URIs
 * in the SVG. This makes the file self-contained but larger.
 */

function exportSVG() {
  const renderer = window.officeRenderer;
  if (!renderer) { alert('Office renderer not ready'); return; }

  const btn = document.getElementById('screenshot-toolbar-btn');
  if (btn) btn.disabled = true;

  try {
    _doExport(renderer);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function _doExport(renderer) {
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

  // Extract inner SVG content (strip outer <svg> wrapper to avoid nested viewport)
  const canvasInner = _extractSvgInner(svgCtx.getSerializedSvg(true));

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
    ${canvasInner}
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
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

// ── Helpers ────────────────────────────────────────────────────────────────

/** Strip outer <svg> wrapper, return only inner content (defs + groups). */
function _extractSvgInner(svgStr) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(svgStr, 'image/svg+xml');
  const root = doc.documentElement;
  let inner = '';
  for (const child of root.children) {
    inner += new XMLSerializer().serializeToString(child);
  }
  return inner;
}

/** Collect all stylesheet rules as a string for embedding in SVG. */
function _collectStyles() {
  const lines = [];
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        lines.push(rule.cssText);
      }
    } catch (e) {
      // Cross-origin stylesheets (e.g. Google Fonts) can't be read.
      // Font references in the SVG will use fallbacks.
      console.warn('[SVG export] Cannot read cross-origin stylesheet:', sheet.href, e.message);
    }
  }
  return lines.join('\n');
}

/** Sanitize a cloned DOM tree for safe embedding in SVG foreignObject. */
function _sanitizeClone(clone) {
  // Remove dangerous elements
  for (const el of clone.querySelectorAll('script,iframe,embed,object,base,meta,link[rel="import"]')) {
    el.remove();
  }
  // Remove event handlers and javascript: URIs
  for (const el of clone.querySelectorAll('*')) {
    for (const attr of [...el.attributes]) {
      if (attr.name.startsWith('on') ||
          (attr.name === 'href' && attr.value.trim().toLowerCase().startsWith('javascript:')) ||
          (attr.name === 'src' && attr.value.trim().toLowerCase().startsWith('javascript:'))) {
        el.removeAttribute(attr.name);
      }
    }
  }
  return clone;
}

/** Serialize a DOM panel into a <foreignObject> positioned at its layout offset. */
function _panelToForeignObject(el, appRect) {
  const r = el.getBoundingClientRect();
  const x = Math.round(r.left - appRect.left);
  const y = Math.round(r.top - appRect.top);
  const w = Math.round(r.width);
  const h = Math.round(r.height);

  const clone = _sanitizeClone(el.cloneNode(true));
  const html = new XMLSerializer().serializeToString(clone);

  return `<foreignObject x="${x}" y="${y}" width="${w}" height="${h}">
    <div xmlns="http://www.w3.org/1999/xhtml" style="width:${w}px;height:${h}px;overflow:hidden;">
      ${html}
    </div>
  </foreignObject>\n`;
}

window.exportSVG = exportSVG;

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('screenshot-toolbar-btn');
  if (btn) btn.addEventListener('click', exportSVG);
});
