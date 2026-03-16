/**
 * plugin-loader.js — Dynamic plugin discovery, script/style injection, and rendering.
 */
class PluginLoader {
  constructor() {
    this._plugins = [];   // [{id, name, icon, render_function, ...}]
    this._loaded = false;
  }

  /** Fetch plugin list from backend and inject all JS/CSS assets. */
  async init() {
    try {
      const resp = await fetch('/api/plugins?view_type=project_tab');
      this._plugins = await resp.json();
    } catch (e) {
      console.warn('[PluginLoader] Failed to fetch plugins:', e);
      this._plugins = [];
      return;
    }

    // Inject <script> and <link> for each plugin
    const promises = [];
    for (const p of this._plugins) {
      promises.push(this._injectScript(p.id));
      promises.push(this._injectStyle(p.id));
    }
    await Promise.allSettled(promises);
    this._loaded = true;
  }

  /** Return the list of registered plugins. */
  getPlugins() {
    return this._plugins;
  }

  /**
   * Fetch plugin data and invoke the plugin's render function.
   * @param {string} pluginId
   * @param {string} projectId
   * @param {HTMLElement} container
   * @param {Object} ctx - {escHtml: fn, projectId: str}
   */
  async render(pluginId, projectId, container, ctx) {
    const plugin = this._plugins.find(p => p.id === pluginId);
    if (!plugin) {
      container.innerHTML = `<div style="color:var(--pixel-red);font-size:6px;">Plugin '${pluginId}' not found</div>`;
      return;
    }

    container.innerHTML = '<div style="color:var(--text-dim);font-size:6px;">Loading...</div>';

    try {
      const resp = await fetch(`/api/projects/${encodeURIComponent(projectId)}/plugin/${encodeURIComponent(pluginId)}`);
      const data = await resp.json();

      const renderFn = window[plugin.render_function];
      if (typeof renderFn !== 'function') {
        container.innerHTML = `<div style="color:var(--pixel-red);font-size:6px;">Render function '${plugin.render_function}' not found</div>`;
        return;
      }

      renderFn(container, data, ctx);
    } catch (err) {
      container.innerHTML = `<div style="color:var(--pixel-red);font-size:6px;">Plugin error: ${err.message}</div>`;
    }
  }

  /** Inject a <script> tag for a plugin's JS file. */
  _injectScript(pluginId) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = `/api/plugins/${encodeURIComponent(pluginId)}/script`;
      s.onload = resolve;
      s.onerror = () => {
        console.warn(`[PluginLoader] Failed to load script for plugin '${pluginId}'`);
        resolve(); // Don't block on failure
      };
      document.head.appendChild(s);
    });
  }

  /** Inject a <link> tag for a plugin's CSS file. */
  _injectStyle(pluginId) {
    return new Promise((resolve) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = `/api/plugins/${encodeURIComponent(pluginId)}/style`;
      link.onload = resolve;
      link.onerror = () => {
        // CSS is optional, don't warn
        resolve();
      };
      document.head.appendChild(link);
    });
  }
}

// Create global instance
window.pluginLoader = new PluginLoader();
