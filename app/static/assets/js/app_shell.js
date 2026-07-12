/* ===================================================================
   Spider Panel — Console SPA shell
   Fixed left sidebar + no-reload section router + Browser/Clipboard/Remote.
   Reuses globals from base.html: api(), toast(), esc(), fmtBytes(), logout().
   =================================================================== */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  /* ---------- Section registry (single source of truth) ---------- */
  // icon: inline SVG path data (24x24). badge: optional promise -> number.
  const I = {
    grid: '<path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"/>',
    xray: '<path d="M12 2l9 5v10l-9 5-9-5V7zM12 2v20M2 7l20 10M22 7L2 17"/>',
    sliders: '<path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h0M16 18a2 2 0 104 0 2 2 0 10-4 0"/>',
    plug: '<path d="M9 2v6M15 2v6M6 8h12v3a6 6 0 01-12 0zM12 17v5"/>',
    logs: '<path d="M4 4h16v16H4zM7 9h10M7 13h10M7 17h6"/>',
    globe: '<path d="M12 2a10 10 0 100 20 10 10 0 000-20zM2 12h20M12 2c3 3 3 17 0 20M12 2c-3 3-3 17 0 20"/>',
    cursor: '<path d="M4 4l7 16 2-7 7-2z"/>',
    gear: '<path d="M12 8a4 4 0 100 8 4 4 0 000-8zM3 12h2M19 12h2M12 3v2M12 19v2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
    info: '<path d="M12 2a10 10 0 100 20 10 10 0 000-20zM12 10v6M12 7h0"/>',
  };

  const SECTIONS = [
    { id: "dashboard",  label: "Dashboard",       icon: I.grid,    title: "Dashboard",      kbd: "1", badge: null },
    { id: "xray",       label: "Xray Mgmt",       icon: I.xray,    title: "Xray Management", kbd: "2", badge: null },
    { id: "configs",    label: "Configurations",  icon: I.sliders, title: "Configurations",  kbd: "3", badge: null },
    { id: "connections",label: "Connections",     icon: I.plug,    title: "Connections",     kbd: "4", badge: "online" },
    { id: "logs",       label: "Logs",            icon: I.logs,    title: "Logs",            kbd: "5", badge: null },
    { id: "browser",    label: "Browser",         icon: I.globe,   title: "Browser",         kbd: "6", badge: null },
    { id: "remote",     label: "Remote Control",  icon: I.cursor,  title: "Remote Control",  kbd: "7", badge: null },
    { id: "settings",   label: "Settings",        icon: I.gear,    title: "Settings",        kbd: "8", badge: null },
    { id: "installs",   label: "Installations",   icon: I.download,title: "Installations",   kbd: "9", badge: null },
    { id: "about",      label: "About",           icon: I.info,    title: "About",           kbd: "0", badge: null },
  ];

  const RENDERERS = {
    dashboard: renderDashboard,
    xray: renderXray,
    configs: renderConfigs,
    connections: renderConnections,
    logs: renderLogs,
    browser: renderBrowser,
    remote: renderRemote,
    settings: renderSettings,
    installs: renderInstalls,
    about: renderAbout,
  };

  /* ---------- State ---------- */
  let current = "dashboard";
  let electronMode = false; // becomes true if a host agent reports in

  /* ---------- Boot ---------- */
  function buildNav() {
    const nav = $("#side-nav");
    nav.innerHTML = SECTIONS.map((s) => `
      <button class="nav-item" data-view="${s.id}" title="${s.label} (${s.kbd})" aria-label="${s.label}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${s.icon}</svg>
        <span class="nav-label">${s.label}</span>
        ${s.kbd ? `<span class="nav-kbd">${s.kbd}</span>` : ""}
        ${s.badge ? `<span class="nav-badge" id="badge-${s.id}" hidden>0</span>` : ""}
        <span class="tip">${s.label} <kbd>${s.kbd}</kbd></span>
      </button>`).join("");
    $$(".nav-item", nav).forEach((b) => (b.onclick = () => showView(b.dataset.view)));
    refreshBadges();
  }

  async function refreshBadges() {
    try {
      const s = await api("/dashboard/stats");
      const online = s.online_connections || 0;
      const el = $("#badge-connections");
      if (el) { el.textContent = online; el.hidden = online === 0; }
    } catch { /* non-fatal */ }
  }

  function bindShell() {
    $("#side-collapse").onclick = () => $("#console").classList.toggle("collapsed");
    $("#menu-btn").onclick = () => $("#console").classList.add("nav-open");
    $("#scrim").onclick = () => $("#console").classList.remove("nav-open");
    $("#side-logout").onclick = () => { logout(); };
    $("#theme-btn").onclick = () => {
      const cur = document.documentElement.getAttribute("data-theme");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("spider_theme", next);
    };
    const saved = localStorage.getItem("spider_theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);

    // Keyboard shortcuts: 1-9/0 switch sections; "[" collapses sidebar.
    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea")) return;
      if (e.key === "[") { $("#console").classList.toggle("collapsed"); return; }
      const map = { "1":"dashboard","2":"xray","3":"configs","4":"connections","5":"logs","6":"browser","7":"remote","8":"settings","9":"installs","0":"about" };
      if (map[e.key]) showView(map[e.key]);
    });
    pollConnection();
    setInterval(pollConnection, 15000);
  }

  async function pollConnection() {
    const dot = $("#conn-dot"), txt = $("#conn-text");
    try {
      const h = await api("/system/xray/health");
      const up = !!h.running;
      dot.classList.toggle("on", up);
      txt.textContent = up ? "Xray online" : "Xray offline";
      const pill = $("#xray-pill");
      if (pill) { pill.textContent = "Xray: " + (up ? "RUNNING" : "STOPPED"); pill.className = "pill " + (up ? "on" : "off"); }
    } catch { txt.textContent = "No connection"; }
  }

  /* ---------- Router (no reload) ---------- */
  async function showView(name) {
    if (!RENDERERS[name]) name = "dashboard";
    current = name;
    $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
    const sec = SECTIONS.find((s) => s.id === name);
    $("#view-title").textContent = sec ? sec.title : name;
    $("#console").classList.remove("nav-open");
    const root = $("#content");
    // skeleton
    root.innerHTML = `<div class="skeleton-wrap"><div class="skeleton-bar"></div><div class="skeleton-bar short"></div><div class="skeleton-card"></div></div>`;
    try {
      const html = document.createElement("div");
      html.className = "view-enter";
      root.innerHTML = "";
      root.appendChild(html);
      await RENDERERS[name](html);
    } catch (e) {
      root.innerHTML = `<div class="panel glass"><p class="error-text" role="alert">${esc(e.message)}</p></div>`;
    }
  }

  /* ===================================================================
     SECTION RENDERERS
     Each renders into `root` (a .view-enter div). They pull real data
     from the existing JSON APIs so the console is genuinely functional.
     =================================================================== */

  async function cardGrid(pairs) {
    return `<div class="grid cards">${pairs.map(([k,v,sub]) => `
      <div class="card glass"><div class="k">${k}</div><div class="v">${v}</div><div class="sub">${sub||""}</div></div>`).join("")}</div>`;
  }

  async function renderDashboard(root) {
    const s = await api("/dashboard/stats");
    const st = s.extra || {};
    const storage = st.storage || {};
    root.innerHTML = await cardGrid([
      ["Total Users", s.total_users, `${s.active_users} active`],
      ["Active", s.active_users, "online allowed"],
      ["Expired", s.expired_users, "need renewal"],
      ["Disabled", s.disabled_users, "off"],
      ["Online", s.online_connections, "live sessions"],
      ["Traffic", fmtBytes(s.total_traffic_bytes), "sum used"],
      ["CPU", s.cpu_percent == null ? "—" : s.cpu_percent + "%", "load"],
      ["RAM", s.memory_percent == null ? "—" : s.memory_percent + "%", "used"],
      ["Storage", storage.total_bytes ? fmtBytes(storage.used_bytes) + " / " + fmtBytes(storage.total_bytes) : "—", storage.free_bytes ? fmtBytes(storage.free_bytes) + " free" : ""],
    ]) + `
      <div class="panel glass" style="margin-top:16px"><h3>SERVER STATUS</h3>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
          <div><div class="k">Xray</div><div class="v" style="font-size:20px">${s.xray_running ? "Up" : "Down"}</div></div>
          <div><div class="k">PID</div><div class="v" style="font-size:20px">${s.xray_pid ?? "—"}</div></div>
          <div><div class="k">Auto-restart</div><div class="v" style="font-size:20px">${st.auto_restart ? "On" : "—"}</div></div>
        </div>
        <div class="row-actions" style="margin-top:14px">
          <button class="btn btn-sm" id="d-restart">⟳ Restart Xray</button>
          <button class="btn btn-sm" id="d-config">⚡ View Config</button>
        </div>
        <pre class="codebox" id="d-cfg" hidden></pre>
      </div>`;
    root.querySelector("#d-restart").onclick = async () => { try { await api("/system/xray/restart", { method: "POST" }); toast("Xray restart sent"); } catch (e) { toast(e.message, "err"); } };
    root.querySelector("#d-config").onclick = async () => {
      const pre = root.querySelector("#d-cfg");
      try { const c = await api("/xray/config"); pre.textContent = JSON.stringify(c, null, 2); pre.hidden = false; } catch (e) { toast(e.message, "err"); }
    };
  }

  async function renderXray(root) {
    const ins = await api("/inbounds");
    root.innerHTML = `<div class="panel glass"><h3>XRAY INBOUNDS</h3>
      <div class="grid cards">${ins.map((i) => `
        <div class="card glass"><div class="k">${esc(i.name || i.tag)}</div>
          <div class="v" style="font-size:16px">${esc(i.protocol)} / ${esc(i.security)}</div>
          <div class="sub">${esc(i.network)} · :${i.port}</div></div>`).join("")}</div>
      <p class="muted" style="color:var(--txt-dim);font-size:12px;margin-top:10px">Full inbound editor lives in the Connections / Configurations pages.</p>
    </div>`;
  }

  async function renderConfigs(root) {
    const cfg = await api("/settings");
    root.innerHTML = `<div class="panel glass"><h3>CONFIGURATIONS</h3>
      <pre class="codebox">${esc(JSON.stringify(cfg, null, 2))}</pre></div>`;
  }

  async function renderConnections(root) {
    const users = await api("/users");
    root.innerHTML = `<div class="panel glass"><h3>CONNECTIONS (${users.length} users)</h3>
      <table class="table"><thead><tr><th>User</th><th>Status</th><th>Traffic</th><th>IP Limit</th></tr></thead><tbody>
      ${users.map((u) => `<tr><td data-label="User">${esc(u.username)}</td>
        <td data-label="Status"><span class="badge ${u.status}">${u.status}</span></td>
        <td data-label="Traffic">${fmtBytes(u.used_traffic_bytes)} / ${u.traffic_limit_bytes ? fmtBytes(u.traffic_limit_bytes) : "∞"}</td>
        <td data-label="IP Limit">${u.ip_limit || "∞"}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  async function renderLogs(root) {
    root.innerHTML = `<div class="panel glass"><h3>XRAY LOGS</h3>
      <pre class="codebox" id="logbox" style="min-height:320px">Loading…</pre>
      <div class="row-actions" style="margin-top:10px"><button class="btn btn-sm" id="log-refresh">↻ Refresh</button></div></div>`;
    const box = root.querySelector("#logbox");
    const load = async () => { try { const r = await api("/xray/logs"); box.textContent = (r.logs || "").slice(-8000) || "(empty)"; } catch (e) { box.textContent = e.message; } };
    root.querySelector("#log-refresh").onclick = load;
    load();
  }

  async function renderSettings(root) {
    const me = await api("/auth/me");
    root.innerHTML = `<div class="panel glass"><h3>SETTINGS</h3>
      <div class="setting-row"><div><div class="k">Account</div><div class="sub">${esc(me.username)}</div></div></div>
      <div class="setting-row"><div><div class="k">Change credentials</div><div class="sub">Update admin username / password</div></div>
        <button class="btn btn-sm" id="s-chg">Change</button></div>
      <p class="muted" style="color:var(--txt-dim);font-size:12px">Full settings surface (music, theme, xray) is available via /settings.</p></div>`;
    root.querySelector("#s-chg").onclick = async () => {
      const pw = prompt("New password (min 6):");
      if (!pw) return;
      try { await api("/auth/change-credentials", { method: "POST", body: { new_password: pw } }); toast("Password updated"); }
      catch (e) { toast(e.message, "err"); }
    };
  }

  async function renderInstalls(root) {
    root.innerHTML = `<div class="panel glass"><h3>INSTALLATIONS</h3>
      <p class="muted" style="color:var(--txt-dim)">Installation helpers (certbot, xray binary, systemd) are invoked from the host. This panel shows status only.</p>
      <div class="grid cards">
        <div class="card glass"><div class="k">Xray Binary</div><div class="v" style="font-size:16px">${esc((await safeHealth()).binary || "—")}</div></div>
        <div class="card glass"><div class="k">Version</div><div class="v" style="font-size:16px">${esc((await safeHealth()).version || "—")}</div></div>
      </div></div>`;
  }
  async function safeHealth() { try { return await api("/system/xray/health"); } catch { return {}; } }

  async function renderAbout(root) {
    root.innerHTML = `<div class="panel glass"><h3>ABOUT</h3>
      <p>Spider Panel — Xray Core Management Console.</p>
      <p class="muted" style="color:var(--txt-dim)">Cyberpunk dark console. Sections switch without page reloads.
      The Browser page embeds a webview in iframe mode; full Chromium + OS-level Remote Control require the Electron host agent.</p>
      <p style="font-size:12px;color:var(--txt-dim)">Remote driver mode: <span id="about-drv">…</span></p></div>`;
    try { const r = await api("/remote/status"); $("#about-drv").textContent = r.mode + " — " + r.note; } catch {}
  }

  /* ===================================================================
     BROWSER PAGE (iframe mode)
     =================================================================== */
  let tabs = [];
  let activeTab = null;

  function normalizeUrl(input) {
    const v = input.trim();
    if (!v) return null;
    if (/^https?:\/\//i.test(v) || /^file:\/\//i.test(v)) return v;
    if (/^[\w-]+(\.[\w-]+)+(\/.*)?$/.test(v) && !/\s/.test(v)) return "https://" + v; // example.com -> open
    return "https://www.google.com/search?q=" + encodeURIComponent(v); // ChatGPT -> search Google
  }

  async function renderBrowser(root) {
    if (tabs.length === 0) addTab("https://www.google.com");
    root.innerHTML = `
      <div class="tabs" id="tabs"></div>
      <div class="browser-chrome">
        <button class="icon-btn" id="b-back" title="Back" aria-label="Back">‹</button>
        <button class="icon-btn" id="b-fwd" title="Forward" aria-label="Forward">›</button>
        <button class="icon-btn" id="b-refresh" title="Refresh" aria-label="Refresh">⟳</button>
        <button class="icon-btn" id="b-home" title="Home" aria-label="Home">⌂</button>
        <div class="addr"><input id="b-addr" placeholder="Search Google or type a URL" aria-label="Address or search" autocomplete="off">
          <button class="btn btn-sm" id="b-go">Go</button></div>
        <div class="zoom">
          <button class="icon-btn" id="b-zout" title="Zoom out" aria-label="Zoom out">−</button>
          <span id="b-zoom" style="min-width:42px;text-align:center">100%</span>
          <button class="icon-btn" id="b-zin" title="Zoom in" aria-label="Zoom in">+</button>
        </div>
        <button class="icon-btn" id="b-full" title="Fullscreen" aria-label="Fullscreen">⛶</button>
        <button class="icon-btn" id="b-newtab" title="New tab" aria-label="New tab">＋</button>
      </div>
      <div class="browser-frame-wrap">
        <div class="browser-loading" id="b-load"></div>
        <iframe class="browser-frame" id="b-frame" referrerpolicy="no-referrer" sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"></iframe>
      </div>
      <p class="muted" style="color:var(--txt-dim);font-size:11px;margin-top:8px">
        Embedded browser runs in <b>iframe mode</b>: navigation, back/forward, refresh, tabs, zoom, fullscreen and address/search work.
        Cross-origin sites may block embedding (X-Frame-Options/CSP). Downloads/uploads/cookies per-site control require the Electron <code>webview</code> build.</p>`;

    const frame = $("#b-frame");
    const addr = $("#b-addr");
    let zoom = 1;

    const load = (url) => {
      if (!url) return;
      $("#b-load").classList.add("on");
      frame.src = url;
      addr.value = url;
      activeTab.url = url;
      renderTabs();
    };
    frame.onload = () => { $("#b-load").classList.remove("on"); try { addr.value = frame.contentWindow.location.href; activeTab.url = addr.value; } catch {} };
    frame.onerror = () => $("#b-load").classList.remove("on");

    $("#b-go").onclick = () => load(normalizeUrl(addr.value));
    addr.addEventListener("keydown", (e) => { if (e.key === "Enter") load(normalizeUrl(addr.value)); });
    $("#b-refresh").onclick = () => { $("#b-load").classList.add("on"); try { frame.contentWindow.location.reload(); } catch { frame.src = frame.src; } };
    $("#b-home").onclick = () => load(activeTab.home || "https://www.google.com");
    $("#b-back").onclick = () => { try { frame.contentWindow.history.back(); } catch { toast("Back not available in iframe mode", "err"); } };
    $("#b-fwd").onclick = () => { try { frame.contentWindow.history.forward(); } catch { toast("Forward not available in iframe mode", "err"); } };
    $("#b-zin").onclick = () => { zoom = Math.min(2, zoom + 0.1); applyZoom(); };
    $("#b-zout").onclick = () => { zoom = Math.max(0.5, zoom - 0.1); applyZoom(); };
    $("#b-full").onclick = () => { const w = $(".browser-frame-wrap"); if (!document.fullscreenElement) w.requestFullscreen?.(); else document.exitFullscreen?.(); };
    $("#b-newtab").onclick = () => { addTab("https://www.google.com"); load(activeTab.url); };

    function applyZoom() { frame.style.transform = `scale(${zoom})`; frame.style.transformOrigin = "0 0"; $("#b-zoom").textContent = Math.round(zoom * 100) + "%"; }

    load(activeTab.url);
  }

  function addTab(url) {
    const t = { id: "t" + Date.now() + Math.random().toString(36).slice(2, 6), url, home: url, title: "New tab" };
    tabs.push(t); activeTab = t; renderTabs();
  }
  function renderTabs() {
    const el = $("#tabs"); if (!el) return;
    el.innerHTML = tabs.map((t) => `
      <div class="tab ${t === activeTab ? "active" : ""}" data-id="${t.id}">
        <span class="t-title">${esc(t.title || t.url || "New tab")}</span>
        <span class="t-close" data-close="${t.id}" title="Close">✕</span>
      </div>`).join("");
    $$(".tab", el).forEach((tab) => {
      tab.onclick = (e) => {
        if (e.target.dataset.close) { closeTab(e.target.dataset.close); return; }
        activeTab = tabs.find((x) => x.id === tab.dataset.id); renderTabs();
        const f = $("#b-frame"); if (f) { f.src = activeTab.url; $("#b-addr").value = activeTab.url; }
      };
    });
  }
  function closeTab(id) {
    const i = tabs.findIndex((t) => t.id === id); if (i < 0) return;
    tabs.splice(i, 1);
    if (tabs.length === 0) addTab("https://www.google.com");
    if (activeTab.id === id) activeTab = tabs[Math.max(0, i - 1)];
    renderTabs(); const f = $("#b-frame"); if (f) f.src = activeTab.url;
  }

  /* ===================================================================
     REMOTE CONTROL PAGE (loopback driver; agent-ready)
     =================================================================== */
  async function renderRemote(root) {
    let driverNote = "";
    try { const r = await api("/remote/status"); electronMode = r.mode === "agent"; driverNote = r.note; } catch { driverNote = "remote status unavailable"; }
    root.innerHTML = `
      <div class="panel glass" style="max-width:560px">
        <h3>REMOTE MOUSE</h3>
        <p class="muted" style="color:var(--txt-dim);font-size:12px">Drag on the touchpad to move the cursor; buttons send click intents to the active driver.
        ${electronMode ? "" : "<br><b>Loopback demo:</b> " + esc(driverNote)}</p>
        <div class="touchpad" id="tp" role="application" aria-label="Touchpad — drag to move cursor">
          <div class="cur" id="tp-cur" style="left:50%;top:50%"></div>
        </div>
        <div class="fp-grid" style="margin-top:12px">
          <button class="btn btn-sm" data-m="left">Left</button>
          <button class="btn btn-sm" data-m="right">Right</button>
          <button class="btn btn-sm" data-m="middle">Middle</button>
          <button class="btn btn-sm" data-m="dbl">Double</button>
          <button class="btn btn-sm" id="m-up">↑ Scroll</button>
          <button class="btn btn-sm" id="m-dn">↓ Scroll</button>
          <button class="btn btn-sm" id="m-drag">Drag Start</button>
          <button class="btn btn-sm" id="m-dragend">Drag End</button>
          <button class="btn btn-sm" id="m-reset">Reset</button>
        </div>
        <div class="row-actions" style="margin-top:12px"><button class="btn btn-sm" id="m-status">Driver status</button></div>
      </div>

      <div class="panel glass" style="max-width:560px;margin-top:16px">
        <h3>CLIPBOARD</h3>
        <textarea class="field clip-box" id="clip" placeholder="Copied text appears here…" aria-label="Clipboard contents"></textarea>
        <div class="row-actions" style="margin-top:10px">
          <button class="btn btn-sm" id="c-paste">Paste</button>
          <button class="btn btn-sm" id="c-copy">Copy</button>
          <button class="btn btn-sm btn-ghost danger" id="c-clear">Clear</button>
          <button class="btn btn-sm" id="c-refresh">Refresh</button>
        </div>
        <p class="muted" style="color:var(--txt-dim);font-size:11px;margin-top:8px">Paste injects into the page's currently focused text field (best-effort). Copy reads the local clipboard.</p>
      </div>`;

    const tp = $("#tp"), cur = $("#tp-cur");
    let dragging = false, draggingActive = false;
    const sendMove = (clientX, clientY) => {
      const r = tp.getBoundingClientRect();
      const x = Math.round(((clientX - r.left) / r.width) * 100);
      const y = Math.round(((clientY - r.top) / r.height) * 100);
      cur.style.left = ((clientX - r.left) / r.width) * 100 + "%";
      cur.style.top = ((clientY - r.top) / r.height) * 100 + "%";
      api("/remote/mouse/move", { method: "POST", body: { x, y, smooth: true } }).catch(() => {});
    };
    tp.addEventListener("pointerdown", (e) => { dragging = true; tp.setPointerCapture(e.pointerId); sendMove(e.clientX, e.clientY); });
    tp.addEventListener("pointermove", (e) => { if (dragging) sendMove(e.clientX, e.clientY); });
    tp.addEventListener("pointerup", () => { dragging = false; });

    $$("[data-m]", root).forEach((b) => b.onclick = () => {
      const m = b.dataset.m;
      if (m === "dbl") api("/remote/mouse/click", { method: "POST", body: { button: "left", double: true } }).catch(() => {});
      else api("/remote/mouse/click", { method: "POST", body: { button: m } }).catch(() => {});
    });
    $("#m-up").onclick = () => api("/remote/mouse/scroll", { method: "POST", body: { dx: 0, dy: -120 } }).catch(() => {});
    $("#m-dn").onclick = () => api("/remote/mouse/scroll", { method: "POST", body: { dx: 0, dy: 120 } }).catch(() => {});
    $("#m-drag").onclick = () => { draggingActive = true; api("/remote/mouse/drag", { method: "POST", body: { x: 50, y: 50, start: true } }).catch(() => {}); toast("Drag started"); };
    $("#m-dragend").onclick = () => { draggingActive = false; api("/remote/mouse/drag", { method: "POST", body: { x: 50, y: 50, start: false } }).catch(() => {}); toast("Drag ended"); };
    $("#m-reset").onclick = () => { cur.style.left = "50%"; cur.style.top = "50%"; api("/remote/mouse/move", { method: "POST", body: { x: 50, y: 50, smooth: true } }).catch(() => {}); };
    $("#m-status").onclick = async () => { try { const r = await api("/remote/status"); toast("Driver: " + r.mode); } catch (e) { toast(e.message, "err"); } };

    const clip = $("#clip");
    $("#c-refresh").onclick = async () => { try { const r = await api("/remote/clipboard"); clip.value = r.text; } catch { try { clip.value = await navigator.clipboard.readText(); } catch {} } };
    $("#c-copy").onclick = async () => { try { await navigator.clipboard.writeText(clip.value); api("/remote/clipboard", { method: "POST", body: { text: clip.value } }).catch(() => {}); toast("Copied to clipboard"); } catch (e) { toast(e.message, "err"); } };
    $("#c-clear").onclick = () => { clip.value = ""; api("/remote/clipboard", { method: "POST", body: { text: "" } }).catch(() => {}); };
    $("#c-paste").onclick = async () => {
      // Paste into the currently focused field on THIS page (best-effort).
      const active = document.activeElement;
      if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) {
        active.value = clip.value; active.dispatchEvent(new Event("input", { bubbles: true })); toast("Pasted into focused field");
      } else {
        try { await navigator.clipboard.writeText(clip.value); toast("Clipboard set — paste where needed (Cmd/Ctrl+V)"); }
        catch (e) { toast(e.message, "err"); }
      }
    };
    // Seed from local clipboard
    try { clip.value = await navigator.clipboard.readText(); } catch {}
  }

  /* ---------- Init ---------- */
  buildNav();
  bindShell();
  // Determine initial section from hash, default dashboard
  const hash = (location.hash || "#dashboard").slice(1);
  showView(SECTIONS.find((s) => s.id === hash) ? hash : "dashboard");
  window.addEventListener("hashchange", () => { const h = location.hash.slice(1); if (SECTIONS.find((s) => s.id === h)) showView(h); });
})();
