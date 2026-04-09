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

/** Key visual CSS properties to inline (CSS variables won't resolve in SVG foreignObject). */
const _INLINE_PROPS = [
  'background-color', 'background', 'background-image', 'color',
  'border', 'border-top', 'border-bottom', 'border-left', 'border-right',
  'border-radius', 'border-color',
  'font-family', 'font-size', 'font-weight', 'line-height',
  'text-align', 'text-shadow', 'box-shadow', 'text-decoration',
  'padding', 'padding-top', 'padding-bottom', 'padding-left', 'padding-right',
  'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
  'display', 'position', 'top', 'left', 'right', 'bottom',
  'flex-direction', 'align-items', 'justify-content', 'flex-wrap',
  'flex-grow', 'flex-shrink', 'flex-basis', 'flex', 'gap', 'order',
  'overflow', 'overflow-x', 'overflow-y',
  'width', 'height', 'min-width', 'min-height', 'max-width', 'max-height',
  'white-space', 'opacity', 'visibility', 'z-index',
  'grid-area', 'grid-template', 'grid-template-columns', 'grid-template-rows',
  'word-break', 'overflow-wrap', 'text-overflow',
  'cursor', 'user-select', 'pointer-events',
  'object-fit', 'vertical-align',
  'box-sizing',
];

/** Inline computed styles on every element so CSS variables are resolved. */
function _inlineComputedStyles(original, clone) {
  const cs = getComputedStyle(original);
  let style = '';
  for (const prop of _INLINE_PROPS) {
    const val = cs.getPropertyValue(prop);
    if (val) {
      style += `${prop}:${val};`;
    }
  }
  // Force explicit dimensions for elements with computed size
  const rect = original.getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0) {
    style += `width:${rect.width}px;height:${rect.height}px;`;
  }
  style += 'box-sizing:border-box;';
  if (style) clone.setAttribute('style', style);

  // Recurse into children (skip hidden/collapsed elements)
  const origChildren = original.children;
  const cloneChildren = clone.children;
  for (let i = 0; i < origChildren.length && i < cloneChildren.length; i++) {
    _inlineComputedStyles(origChildren[i], cloneChildren[i]);
  }
}

/** Serialize a DOM panel into a <foreignObject> positioned at its layout offset. */
function _panelToForeignObject(el, appRect) {
  const r = el.getBoundingClientRect();
  const x = Math.round(r.left - appRect.left);
  const y = Math.round(r.top - appRect.top);
  const w = Math.round(r.width);
  const h = Math.round(r.height);

  // Clone first, inline styles + images while indices still align with original,
  // THEN sanitize (removing scripts/iframes may shift child indices)
  const clone = el.cloneNode(true);
  _inlineComputedStyles(el, clone);
  _inlineImages(el, clone);
  _sanitizeClone(clone);
  // Ensure all transparent backgrounds get the panel dark bg
  _fillTransparentBg(clone, '#0d0d1a');
  const html = new XMLSerializer().serializeToString(clone);

  return `<foreignObject x="${x}" y="${y}" width="${w}" height="${h}">
    <div xmlns="http://www.w3.org/1999/xhtml" style="width:${w}px;height:${h}px;overflow:hidden;font-family:'Press Start 2P',monospace;background:#0d0d1a;">
      ${html}
    </div>
  </foreignObject>\n`;
}

/** Convert <img> src to inline base64 data URIs so they work outside the server. */
function _inlineImages(original, clone) {
  const origImgs = original.querySelectorAll('img');
  const cloneImgs = clone.querySelectorAll('img');
  for (let i = 0; i < origImgs.length && i < cloneImgs.length; i++) {
    const img = origImgs[i];
    if (!img.complete || !img.naturalWidth) continue;
    try {
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0);
      cloneImgs[i].setAttribute('src', canvas.toDataURL('image/png'));
    } catch (e) {
      console.warn('[SVG export] Cannot inline image:', img.src, e.message);
    }
  }
}

/** Fill transparent/rgba(0,0,0,0) backgrounds with a fallback color. */
function _fillTransparentBg(clone, fallback) {
  const style = clone.getAttribute('style') || '';
  // Match transparent backgrounds regardless of browser formatting
  const isTransparent = /background-color:\s*rgba\(\s*0[\s,]+0[\s,]+0[\s,]+0\s*\)/.test(style) ||
      style.includes('background-color:transparent') ||
      style.includes('background-color: transparent') ||
      (!style.includes('background-color') && !style.includes('background:'));
  if (isTransparent) {
    clone.setAttribute('style', style + `background-color:${fallback};`);
  }
  for (const child of clone.children) {
    _fillTransparentBg(child, fallback);
  }
}

window.exportSVG = exportSVG;

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('screenshot-toolbar-btn');
  if (btn) btn.addEventListener('click', exportSVG);
});
