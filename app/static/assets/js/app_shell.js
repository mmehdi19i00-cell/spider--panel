/* ===================================================================
   Spider Panel — consolidated admin console (SPA shell)
   Single authenticated shell. A fixed left sidebar switches
   sections WITHOUT page reloads. Every section pulls real data
   from the existing JSON APIs. No emojis — SVG icons only.
   =================================================================== */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  /* ---------- Section registry (single source of truth) ---------- */
  // icon: inline SVG path data (24x24, stroke=currentColor). No emojis.
  const I = {
    home: '<path d="M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10"/>',
    news: '<path d="M4 5h13v14H4zM8 9h5M8 13h5M8 17h3"/><path d="M18 8h2v11h-2"/>',
    users: '<path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 00-3-3.87M16 3.13A4 4 0 01 16 11"/>',
    inbound: '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M3 9h18M8 14h.01M12 14h.01M16 14h.01"/>',
    domain: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 010 18 0M12 3a14 14 0 010-18 0"/>',
    chrome: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M21 12h-6M12 21v-6M3 12h6"/>',
    logs: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h10M7 13h10M7 17h6"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 001.09 1.83l.06.05a2 2 0 110 2.83 2.83l-.06-.05a1.65 1.65 0 001.83-1.09V14a2 2 0 11.27-2l.05.06a1.65 1.65 0 001.09-1.83l.06-.05a2 2 0 11-2.83-2.83l-.06.05A1.65 1.65 0 00-1.83-1.09H9a1.65 1.65 0 00-1.83 1.09l-.05.06A2 2 0 11.27 12H2a2 2 0 110-2 2l.05.06A1.65 1.65 0 001.09 15" transform="translate(0 .5)"/>',
  };

  const SECTIONS = [
    { id: "home",     label: "Home",      icon: I.home,     title: "Home",      kbd: "1" },
    { id: "news",     label: "News",      icon: I.news,     title: "News",      kbd: "2" },
    { id: "users",    label: "Users",     icon: I.users,    title: "Users",     kbd: "3" },
    { id: "inbounds", label: "Inbound",   icon: I.inbound,  title: "Inbound Management", kbd: "4" },
    { id: "domains",  label: "Domain",    icon: I.domain,   title: "Domain Management", kbd: "5" },
    { id: "chrome",   label: "Chrome",    icon: I.chrome,   title: "Chrome",    kbd: "6" },
    { id: "logs",     label: "Xray Logs", icon: I.logs,     title: "Xray Logs", kbd: "7" },
    { id: "settings", label: "Settings",   icon: I.settings,  title: "Settings",   kbd: "8" },
  ];

  const RENDERERS = {
    home: renderHome,
    news: renderNews,
    users: renderUsers,
    inbounds: renderInbounds,
    domains: renderDomains,
    chrome: renderChrome,
    logs: renderLogs,
    settings: renderSettings,
  };

  /* ---------- State ---------- */
  let current = "home";
  const viewHistory = [];         // stack for back navigation when re-clicking active nav
  let music = { enabled: false, volume: 70, random: false, track: "", files: [], prefix: "/musics/" };

  /* ---------- Boot ---------- */
  function buildNav() {
    const nav = $("#side-nav");
    nav.innerHTML = SECTIONS.map((s) => `
      <button class="nav-item" data-view="${s.id}" title="${s.label} (${s.kbd})" aria-label="${s.label}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${s.icon}</svg>
        <span class="nav-label">${s.label}</span>
        ${s.kbd ? `<span class="nav-kbd">${s.kbd}</span>` : ""}
        <span class="tip">${s.label} <kbd>${s.kbd}</kbd></span>
      </button>`).join("");
    $$(".nav-item", nav).forEach((b) => (b.onclick = () => {
      const id = b.dataset.view;
      // Click the already-active section again = go back to the previous one.
      if (id === current && viewHistory.length) {
        const back = viewHistory.pop();
        showView(back, /*fromBack*/ true);
      } else {
        showView(id);
      }
    }));
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

    // Keyboard shortcuts: 1-8 switch sections; "[" collapses sidebar.
    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea")) return;
      if (e.key === "[") { $("#console").classList.toggle("collapsed"); return; }
      const map = { "1":"home","2":"news","3":"users","4":"inbounds","5":"domains","6":"chrome","7":"logs","8":"settings" };
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
  async function showView(name, fromBack = false) {
    if (!RENDERERS[name]) name = "home";
    // Maintain back history: push the outgoing view unless we're returning from a back.
    if (name !== current && !fromBack) viewHistory.push(current);
    current = name;
    $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
    const sec = SECTIONS.find((s) => s.id === name);
    $("#view-title").textContent = sec ? sec.title : name;
    $("#console").classList.remove("nav-open");
    const root = $("#content");
    root.innerHTML = `<div class="skeleton-wrap"><div class="skeleton-bar"></div><div class="skeleton-bar short"></div><div class="skeleton-card"></div></div>`;
    try {
      const host = document.createElement("div");
      host.className = "view-enter";
      root.innerHTML = "";
      root.appendChild(host);
      await RENDERERS[name](host);
    } catch (e) {
      root.innerHTML = `<div class="panel glass"><p class="error-text" role="alert">${esc(e.message)}</p></div>`;
    }
  }

  /* ===================================================================
     SHARED HELPERS
     =================================================================== */
  async function cardGrid(pairs) {
    return `<div class="grid cards">${pairs.map(([k,v,sub]) => `
      <div class="card glass"><div class="k">${k}</div><div class="v">${v}</div><div class="sub">${sub||""}</div></div>`).join("")}</div>`;
  }

  function subUrl(uuid) { return `${location.origin}/sub/${uuid}`; }

  function svgIcon(kind) {
    // small inline svg helper for buttons (24x24, stroke currentColor)
    const paths = {
      copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>',
      qr: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
      edit: '<path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 00 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>',
      key: '<rect x="2" y="2" width="20" height="20" rx="2"/><path d="M6 12h12"/>',
      trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>',
      plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
      play: '<path d="M8 5v14l11-7z"/>',
      pause: '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
      next: '<path d="M5 4l10 8-10 8zM19 5v14"/>',
      prev: '<path d="M19 4l-10 8 10 8zM5 5v14"/>',
      repeat: '<path d="M17 2l4 4-4 4"/><path d="M3 11V9a4 4 0 014-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a4 4 0 01-4 4H3"/>',
      shuffle: '<path d="M16 3h5v5"/><path d="M4 20L21 3"/><path d="M21 16v5h-5"/><path d="M15 15l6 6"/><path d="M4 4l5 5"/>',
      search: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
      share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"/>',
      zoomin: '<circle cx="11" cy="11" r="7"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/><line x1="16" y1="16" x2="21" y2="21"/>',
      zoomout: '<circle cx="11" cy="11" r="7"/><line x1="8" y1="11" x2="14" y2="11"/><line x1="16" y1="16" x2="21" y2="21"/>',
    };
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths[kind]||""}</svg>`;
  }

  /* ===================================================================
     HOME
     =================================================================== */
  async function renderHome(root) {
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
          <div><div class="k">Auto-restart</div><div class="v" style="font-size:20px">${st.auto_restart ? "On" : "—"}</div>
        </div>
        <div class="row-actions" style="margin-top:14px">
          <button class="btn btn-sm" id="d-restart">${svgIcon("")} Restart Xray</button>
          <button class="btn btn-sm" id="d-config">View Config</button>
        </div>
        <pre class="codebox" id="d-cfg" hidden></pre>
      </div>`;
    root.querySelector("#d-restart").onclick = async () => { try { await api("/system/restart", { method: "POST" }); toast("Xray restart sent"); } catch (e) { toast(e.message, "err"); } };
    root.querySelector("#d-config").onclick = async () => {
      const pre = root.querySelector("#d-cfg");
      try { const c = await api("/xray/config"); pre.textContent = JSON.stringify(c, null, 2); pre.hidden = false; } catch (e) { toast(e.message, "err"); }
    };
  }

  /* ===================================================================
     NEWS
     =================================================================== */
  async function renderNews(root) {
    root.innerHTML = `<div class="panel glass"><h3>NEWS</h3>
      <div class="row-actions" style="margin-bottom:12px">
        <label class="search-pill" style="flex:1;min-width:160px">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${svgIcon("search")}</svg>
          <input id="news-q" placeholder="Search news (default: Iran)" autocomplete="off">
        </label>
        <button class="btn btn-sm" id="news-go">Search</button>
      </div>
      <div id="news-list" class="muted" style="font-size:13px">Loading…</div></div>`;
    const list = root.querySelector("#news-list");
    const load = async (q) => {
      list.innerHTML = `<div class="muted">Loading…</div>`;
      try {
        const r = await api(`/news?query=${encodeURIComponent(q || "Iran")}&limit=12`);
        if (!r.items || !r.items.length) { list.innerHTML = `<div class="muted">No news available right now.</div>`; return; }
        list.innerHTML = r.items.map((it) => `
          <a class="news-item glass" href="${esc(it.link || "#")}" target="_blank" rel="noopener" style="display:block;padding:12px 14px;margin-bottom:10px;border-radius:12px;text-decoration:none;color:inherit">
            <div style="font-weight:600;font-size:14px">${esc(it.title)}</div>
            <div class="muted" style="font-size:12px;margin-top:4px">${esc((it.text || "").slice(0, 180))}${(it.text||"").length>180?"…":""}</div>
            <div class="muted" style="font-size:11px;margin-top:6px;color:var(--neon-soft)">${esc(it.source||"")} · ${esc(it.published||"")}</div>
          </a>`).join("");
      } catch (e) { list.innerHTML = `<div class="error-text">${esc(e.message)}</div>`; }
    };
    root.querySelector("#news-go").onclick = () => load(root.querySelector("#news-q").value.trim());
    root.querySelector("#news-q").addEventListener("keydown", (e) => { if (e.key === "Enter") load(e.target.value.trim()); });
    load("Iran");
  }

  /* ===================================================================
     USERS
     =================================================================== */
  async function renderUsers(root) {
    const users = await api("/users");
    const cards = users.map((u) => userCard(u)).join("");
    root.innerHTML = `
      <div class="panel glass">
        <div class="panel-head"><h3>USERS</h3><span class="spacer"></span>
          <label class="search-pill" style="max-width:220px">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${svgIcon("search")}</svg>
            <input id="user-search" placeholder="search users…" autocomplete="off">
          </label>
          <button class="btn btn-primary neon btn-sm" id="add-user">${svgIcon("plus")} Add</button>
        </div>
        ${users.length ? `<div class="cards" style="margin-top:12px">${cards}</div>` : `<p class="muted" style="margin-top:12px">No users yet.</p>`}
      </div>`;
    root.querySelector("#add-user").onclick = () => userForm(null);
    root.querySelector("#user-search").oninput = async (e) => {
      const q = e.target.value.trim();
      const list = await api("/users" + (q ? `?search=${encodeURIComponent(q)}` : ""));
      const wrap = root.querySelector(".cards");
      wrap.innerHTML = list.map(userCard).join("") || `<p class="muted">No users</p>`;
      bindUserCards(root);
    };
    bindUserCards(root);
  }

  function userCard(u) {
    const expired = u.status === "expired" || !u.enabled;
    return `<div class="card glass user-card" data-id="${u.id}">
      <div class="panel-head" style="margin-bottom:8px">
        <h3 style="margin:0;font-size:15px">${esc(u.username)}</h3>
        <span class="badge ${u.status}">${u.status}</span>
      </div>
      <div class="kv"><span>UUID</span><code>${esc(u.uuid.slice(0,8))}…</code></div>
      <div class="kv"><span>Expire</span><span>${u.expire_at ? fmtDate(u.expire_at) : "Never"}</span></div>
      <div class="kv"><span>Traffic</span><span>${fmtBytes(u.used_traffic_bytes)} / ${u.traffic_limit_bytes ? fmtBytes(u.traffic_limit_bytes) : "∞"}</span></div>
      <div class="row-actions" style="margin-top:10px;flex-wrap:wrap">
        <button class="btn btn-sm" data-act="config" title="Show config / QR">${svgIcon("qr")} Config</button>
        <button class="btn btn-sm" data-act="copysub" title="Copy Subscription">${svgIcon("copy")} Sub</button>
        <button class="btn btn-sm" data-act="copycfg" title="Copy Config">${svgIcon("copy")} Copy</button>
        <button class="btn btn-sm" data-act="share" title="Copy share link">${svgIcon("share")} Share</button>
        <button class="btn btn-sm" data-act="edit" title="Edit">${svgIcon("edit")}</button>
        <button class="btn btn-sm" data-act="reset" title="Reset UUID">${svgIcon("key")}</button>
        <button class="btn btn-sm ${u.enabled ? "" : "btn-ok"}" data-act="toggle" data-en="${u.enabled}" title="${u.enabled ? "Disable" : "Enable"}">${u.enabled ? "Disable" : "Enable"}</button>
        <button class="btn btn-sm btn-ghost danger" data-act="del" title="Delete">${svgIcon("trash")}</button>
      </div>
    </div>`;
  }

  function bindUserCards(root) {
    root.querySelectorAll("[data-act]").forEach((b) => {
      b.onclick = async () => {
        const id = b.dataset.id;
        try {
          if (b.dataset.act === "config" || b.dataset.act === "qr") showUserQR(Number(id));
          else if (b.dataset.act === "copycfg") { const u = await api(`/users/${id}`); const d = await api(`/sub/${u.uuid}?format=json`); copyText(d.uris.join("\n"), "Config copied"); }
          else if (b.dataset.act === "copysub") { const u = await api(`/users/${id}`); copyText(subUrl(u.uuid), "Subscription copied"); }
          else if (b.dataset.act === "share") { const u = await api(`/users/${id}`); copyText(subUrl(u.uuid), "Share link copied"); }
          else if (b.dataset.act === "edit") userForm(Number(id));
          else if (b.dataset.act === "reset") { await api(`/users/${id}/reset-uuid`, { method: "POST" }); toast("UUID reset"); renderUsers(); }
          else if (b.dataset.act === "toggle") { await api(`/users/${id}/${b.dataset.en === "true" ? "disable" : "enable"}`, { method: "POST" }); toast("Updated"); renderUsers(); }
          else if (b.dataset.act === "del") { if (!confirm("Delete this user?")) return; await api(`/users/${id}`, { method: "DELETE" }); toast("User deleted"); renderUsers(); }
        } catch (e) { toast(e.message, "err"); }
      };
    });
  }

  async function showUserQR(id) {
    const u = await api(`/users/${id}`);
    const d = await api(`/sub/${u.uuid}?format=json`).catch(() => ({ uris: [] }));
    const cfg = d.uris[0] || "";
    openModal(`<h3>QR — ${esc(u.username)}</h3>
      <div style="display:flex;justify-content:center;padding:12px">
        <img src="/api/qr/${esc(u.uuid)}" alt="qr" style="width:240px;height:240px;background:#fff;border-radius:12px">
      </div>
      <div class="field"><label>VLESS URI</label><textarea class="codebox" readonly style="min-height:90px">${esc(cfg)}</textarea></div>
      <div class="field"><label>Subscription URL</label><input readonly value="${esc(location.origin + "/sub/" + u.uuid)}" class="codebox"></div>
      <div class="modal-actions">
        <button class="btn" data-c="1">Copy Config</button>
        <button class="btn" data-s="1">Copy Sub</button>
        <button class="btn modal-close">Close</button>
      </div>`);
    document.querySelector("#modal-card [data-c]").onclick = () => copyText(cfg, "Config copied");
    document.querySelector("#modal-card [data-s]").onclick = () => copyText(location.origin + "/sub/" + u.uuid, "Sub copied");
  }

  function userForm(id) {
    const isEdit = !!id;
    if (isEdit) {
      api(`/users/${id}`).then((u) => { openModal(buildUserForm(u)); bindUserForm(id); }).catch((e) => toast(e.message, "err"));
    } else { openModal(buildUserForm({})); bindUserForm(null); }
  }

  function buildUserForm(u) {
    return `<h3>${u.id ? "EDIT USER" : "CREATE USER"}</h3>
      <form id="user-f" style="display:flex;flex-direction:column;gap:12px">
        ${!u.id ? `<div class="field"><label>Username<input type="text" name="username" value="${esc(u.username || "")}" required></label></div>` : ""}
        <div class="field"><label>Password (leave blank to keep / auto-generate)<input type="password" name="password" placeholder="${u.id ? "••••••••" : "auto-generate if empty"}"></label></div>
        <div class="field-row">
          <div class="field"><label>Expire (days, 0 = never)<input name="expire_days" type="number" min="0" placeholder="0 = never"></label></div>
          <div class="field"><label>Traffic limit (GB)<input name="traffic_limit_gb" type="number" min="0" step="0.1" value="${u.traffic_limit_gb || 0}"></label></div>
        </div>
        <div class="field-row">
          <div class="field"><label>IP limit (0=∞)<input name="ip_limit" type="number" min="0" value="${u.ip_limit || 0}"></label></div>
          <div class="field"><label>Inbound tags (comma separated, empty = all)<input name="inbound_tags" value="${esc(Array.isArray(u.inbound_tags) ? u.inbound_tags.join(",") : (u.inbound_tags || ""))}"></label></div>
        </div>
        <label class="switch"><input type="checkbox" name="enabled" ${u.enabled !== false ? "checked" : ""}> <span class="slider"></span></label> <span class="k">Enabled</span>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost modal-close">Cancel</button>
          <button type="submit" class="btn btn-primary neon">${u.id ? "Save" : "Create"}</button>
        </div>
      </form>`;
  }

  function bindUserForm(id) {
    const modal = document.getElementById("modal");
    const form = document.getElementById("user-f");
    const isEdit = !!id;
    modal.querySelector(".modal-close").onclick = () => { modal.hidden = true; };
    form.onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const body = {
        expire_days: fd.get("expire_days") ? Number(fd.get("expire_days")) : null,
        traffic_limit_gb: Number(fd.get("traffic_limit_gb") || 0),
        ip_limit: Number(fd.get("ip_limit") || 0),
        enabled: fd.get("enabled") === "on",
        inbound_tags: fd.get("inbound_tags") ? fd.get("inbound_tags").split(",").map((s) => s.trim()).filter(Boolean) : [],
      };
      const pwd = fd.get("password");
      if (pwd) body.password = pwd;
      const exp = fd.get("expire_days");
      if (exp) body.expire_days = Number(exp);
      try {
        if (isEdit) { await api(`/users/${id}`, { method: "PUT", body }); toast("User updated"); }
        else { body.username = fd.get("username"); await api("/users", { method: "POST", body }); toast("User created"); }
        modal.hidden = true; renderUsers();
      } catch (e) { toast(e.message, "err"); }
    };
  }

  /* ===================================================================
     INBOUNDS
     =================================================================== */
  async function renderInbounds(root) {
    const list = await api("/inbounds");
    const cards = list.map(inboundCard).join("");
    root.innerHTML = `
      <div class="panel glass">
        <div class="panel-head"><h3>INBOUNDS</h3><span class="spacer"></span>
          <button class="btn btn-primary neon btn-sm" id="add-ib">${svgIcon("plus")} New Inbound</button>
        </div>
        ${list.length ? `<div class="cards" style="margin-top:12px">${cards}</div>` : `<p class="muted" style="margin-top:12px">No inbounds yet.</p>`}
      </div>`;
    root.querySelector("#add-ib").onclick = () => inboundForm(null);
    bindInboundCards(root);
  }

  function inboundCard(ib) {
    return `<div class="card glass" data-id="${ib.id}">
      <div class="panel-head" style="margin-bottom:8px">
        <h3 style="margin:0;font-size:15px">${esc(ib.name || ib.tag)}</h3>
        <span class="badge ${ib.enabled ? "active" : "disabled"}">${ib.enabled ? "on" : "off"}</span>
      </div>
      <div class="kv"><span>Type</span><span>${esc(ib.security)} / ${esc(ib.network)}</span></div>
      <div class="kv"><span>Domain</span><span>${esc(ib.domain || "—")}</span></div>
      <div class="kv"><span>Port</span><span>${ib.port}${ib.external_port && ib.external_port !== ib.port ? ` (ext:${ib.external_port})` : ""}</span></div>
      ${ib.security === "reality" ? `<div class="kv"><span>Reality</span><span>pbk:${esc((ib.public_key||"").slice(0,10))}…</span></div>` : ""}
      <div class="row-actions" style="margin-top:10px;flex-wrap:wrap">
        <button class="btn btn-sm" data-act="edit" title="Edit">${svgIcon("edit")}</button>
        <button class="btn btn-sm" data-act="keys" title="Regenerate Reality keys">${svgIcon("key")}</button>
        <button class="btn btn-sm btn-ghost danger" data-act="del" title="Delete">${svgIcon("trash")}</button>
      </div>
    </div>`;
  }

  function bindInboundCards(root) {
    root.querySelectorAll('[data-act="edit"]').forEach((b) => b.onclick = () => inboundForm(Number(b.dataset.id)));
    root.querySelectorAll('[data-act="keys"]').forEach((b) => b.onclick = async () => {
      try { await api(`/inbounds/${b.dataset.id}/regen-keys`, { method: "POST" }); toast("Reality keys regenerated"); renderInbounds(); }
      catch (e) { toast(e.message, "err"); }
    });
    root.querySelectorAll('[data-act="del"]').forEach((b) => b.onclick = async () => {
      if (!confirm("Delete inbound?")) return;
      try { await api(`/inbounds/${b.dataset.id}`, { method: "DELETE" }); toast("Deleted"); renderInbounds(); }
      catch (e) { toast(e.message, "err"); }
    });
  }

  function inboundForm(id) {
    const isEdit = id != null;
    openModal(`<h3>${isEdit ? "EDIT INBOUND" : "NEW INBOUND"}</h3>
      <form id="ib-f">
        <div class="field-row">
          <div class="field"><label>Tag<input name="tag" required ${isEdit ? "disabled" : ""}></label></div>
          <div class="field"><label>Name<input name="name"></label></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Domain (per-inbound; blank=active)<input name="domain" placeholder="vpn.example.com"></label></div>
          <div class="field"><label>External port (client)<input name="external_port" type="number" min="1" max="65535" placeholder="Railway TCP port"></label></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Internal port (bind)<input name="port" type="number" min="1" max="65535" value="8443"></label></div>
          <div class="field"><label>Security<select name="security"><option value="reality">reality</option><option value="tls">tls</option><option value="none">none</option></select></label></div>
          <div class="field"><label>Network<select name="network"><option value="xhttp">xhttp</option><option value="ws">ws</option><option value="tcp">tcp</option></select></label></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Server name (Reality dest)<input name="server_name" placeholder="target.com:443"></label></div>
          <div class="field"><label>SpiderX<input name="spider_x" value="/"></label></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Path<input name="transport_path" value="/"></label></div>
          <div class="field"><label>WS Host (ws only)<input name="ws_host" placeholder="optional"></label></div>
        </div>
        <div class="field"><label>Enabled<input name="enabled" value="1"></label></div>
        <div class="modal-actions"><button type="button" class="btn btn-ghost modal-close">Cancel</button>
          <button class="btn btn-primary neon">Save</button></div>
      </form>`);
    const form = document.getElementById("ib-f");
    if (isEdit) {
      api(`/inbounds/${id}`).then((ib) => {
        Object.keys(ib).forEach((k) => {
          const el = form.querySelector(`[name="${k}"]`);
          if (el) { if (el.type === "checkbox") el.checked = ib[k]; else if (el.type === "select-one") el.value = ib[k]; else el.value = ib[k] ?? ""; }
        });
      }).catch((e) => toast(e.message, "err"));
    }
    document.querySelector(".modal-close").onclick = () => closeModal();
    form.onsubmit = async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(form).entries());
      const payload = {
        name: f.name, port: Number(f.port), security: f.security, network: f.network,
        server_name: f.server_name, spider_x: f.spider_x, transport_path: f.transport_path,
        domain: f.domain || "", ws_host: f.ws_host, xhttp_mode: f.xhttp_mode,
        external_port: f.external_port ? Number(f.external_port) : null,
        enabled: f.enabled === "1",
      };
      if (!isEdit) payload.tag = f.tag;
      try { if (isEdit) await api(`/inbounds/${id}`, { method: "PUT", body: payload }); else await api("/inbounds", { method: "POST", body: payload }); closeModal(); toast("Saved"); renderInbounds(); }
      catch (e) { toast(e.message, "err"); }
    };
  }

  /* ===================================================================
     DOMAINS
     =================================================================== */
  async function renderDomains(root) {
    const list = await api("/domains");
    const cards = list.map((d) => `
      <div class="card glass">
        <div class="panel-head" style="margin-bottom:8px">
          <h3 style="margin:0;font-size:15px">${esc(d.domain)}</h3>
          ${d.is_active ? '<span class="badge active">ACTIVE</span>' : ""}
        </div>
        <div class="kv"><span>Note</span><span>${esc(d.note || "—")}</span></div>
        <div class="row-actions" style="margin-top:10px;flex-wrap:wrap">
          ${d.is_active ? "" : `<button class="btn btn-sm btn-ok" data-act="act">Activate</button>`}
          <button class="btn btn-sm btn-ghost danger" data-act="del" data-d="${esc(d.domain)}">${svgIcon("trash")}</button>
        </div>
      </div>`).join("");
    root.innerHTML = `
      <div class="panel glass">
        <div class="panel-head"><h3>DOMAINS</h3><span class="spacer"></span></div>
        <p class="muted" style="font-size:12px">The active domain is used for Reality SNI, TLS, and subscription links.</p>
        <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
          <label class="search-pill" style="flex:1;min-width:200px">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${svgIcon("search")}</svg>
            <input id="dom-in" placeholder="example.com" autocomplete="off">
          </label>
          <button class="btn btn-primary neon btn-sm" id="add-dom">${svgIcon("plus")} Add</button>
        </div>
        ${list.length ? `<div class="cards">${cards}</div>` : `<p class="muted">No domains yet.</p>`}
      </div>`;
    root.querySelector("#add-dom").onclick = async () => {
      const dom = root.querySelector("#dom-in").value.trim();
      if (!dom) return;
      try { await api("/domains", { method: "POST", body: { domain: dom } }); toast("Added"); renderDomains(); } catch (e) { toast(e.message, "err"); }
    };
    root.querySelectorAll("[data-act]").forEach((b) => {
      b.onclick = async () => {
        const d = b.dataset.d;
        try {
          if (b.dataset.act === "act") { await api(`/domains/${encodeURIComponent(d)}/activate`, { method: "POST" }); toast("Activated + config reloaded"); renderDomains(); }
          else if (b.dataset.act === "del") { if (confirm(`Delete ${d}?`)) { await api(`/domains/${encodeURIComponent(d)}`, { method: "DELETE" }); toast("Deleted"); renderDomains(); } }
        } catch (e) { toast(e.message, "err"); }
      };
    });
  }

  /* ===================================================================
     CHROME (embedded browser)
     =================================================================== */
  let chromeTabs = [];
  let chromeActive = null;
  async function renderChrome(root) {
    if (chromeTabs.length === 0) addChromeTab("https://www.google.com");
    root.innerHTML = `
      <div class="panel glass">
        <h3>CHROME</h3>
        <div class="tabs" id="ctabs"></div>
        <div class="browser-chrome">
          <button class="icon-btn" id="b-back" title="Back" aria-label="Back">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
          <button class="icon-btn" id="b-fwd" title="Forward" aria-label="Forward">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
          </button>
          <button class="icon-btn" id="b-refresh" title="Refresh" aria-label="Refresh">${svgIcon("")}</button>
          <button class="icon-btn" id="b-home" title="Home" aria-label="Home">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10"/></svg>
          </button>
          <div class="addr"><input id="b-addr" placeholder="Search Google or type a URL" aria-label="Address or search" autocomplete="off">
            <button class="btn btn-sm" id="b-go">Go</button></div>
          <button class="icon-btn" id="b-newtab" title="New tab" aria-label="New tab">${svgIcon("plus")}</button>
          <span class="zoom-group" role="group" aria-label="Zoom">
            <button class="icon-btn" id="b-zoom-out" title="Zoom out" aria-label="Zoom out">${svgIcon("zoomout")}</button>
            <button class="icon-btn zoom-level" id="b-zoom-reset" title="Reset zoom" aria-label="Reset zoom">100%</button>
            <button class="icon-btn" id="b-zoom-in" title="Zoom in" aria-label="Zoom in">${svgIcon("zoomin")}</button>
          </span>
        </div>
        <div class="browser-frame-wrap">
          <div class="browser-loading" id="b-load"></div>
          <iframe class="browser-frame" id="b-frame" referrerpolicy="no-referrer" sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"></iframe>
        </div>
        <p class="muted" style="color:var(--txt-dim);font-size:11px;margin-top:8px">
          Embedded browser: pages load through a same-origin proxy that strips
          embed-blocking headers, so most sites render. Sites that hard-block
          framing or require logged-in sessions may still be limited.</p>
      </div>`;
    const frame = $("#b-frame");
    const addr = $("#b-addr");
    let zoom = 1;
    const applyZoom = () => {
      frame.style.transform = `scale(${zoom})`;
      frame.style.transformOrigin = "0 0";
      // Keep the scaled frame from overflowing its container weirdly.
      frame.style.width = `${100 / zoom}%`;
      frame.style.height = `${100 / zoom}%`;
      const zl = root.querySelector("#b-zoom-reset");
      if (zl) zl.textContent = Math.round(zoom * 100) + "%";
    };
    const load = (url) => {
      if (!url) return;
      $("#b-load").classList.add("on");
      // Route through the same-origin proxy so sites that block embedding
      // (X-Frame-Options / CSP frame-ancestors) actually render.
      const proxied = "/api/browser/proxy?url=" + encodeURIComponent(url);
      frame.src = proxied; addr.value = url; chromeActive.url = url; renderChromeTabs();
    };
    frame.onload = () => { $("#b-load").classList.remove("on"); try { addr.value = frame.contentWindow.location.href; chromeActive.url = addr.value; } catch {} };
    frame.onerror = () => $("#b-load").classList.remove("on");
    $("#b-go").onclick = () => load(normalizeUrl(addr.value));
    addr.addEventListener("keydown", (e) => { if (e.key === "Enter") load(normalizeUrl(addr.value)); });
    $("#b-refresh").onclick = () => { $("#b-load").classList.add("on"); try { frame.contentWindow.location.reload(); } catch { frame.src = frame.src; } };
    $("#b-home").onclick = () => load(chromeActive.home || "https://www.google.com");
    $("#b-back").onclick = () => { try { frame.contentWindow.history.back(); } catch { toast("Back not available in iframe mode", "err"); } };
    $("#b-fwd").onclick = () => { try { frame.contentWindow.history.forward(); } catch { toast("Forward not available in iframe mode", "err"); } };
    $("#b-newtab").onclick = () => { addChromeTab("https://www.google.com"); load(chromeActive.url); };
    const setZoom = (z) => { zoom = Math.min(3, Math.max(0.25, Math.round(z * 100) / 100)); applyZoom(); };
    $("#b-zoom-in").onclick = () => setZoom(zoom + 0.1);
    $("#b-zoom-out").onclick = () => setZoom(zoom - 0.1);
    $("#b-zoom-reset").onclick = () => setZoom(1);
    load(chromeActive.url);
  }
  function normalizeUrl(input) {
    const v = input.trim();
    if (!v) return null;
    if (/^https?:\/\//i.test(v) || /^file:\/\//i.test(v)) return v;
    if (/^[\w-]+(\.[\w-]+)+(\/.*)?$/.test(v) && !/\s/.test(v)) return "https://" + v;
    return "https://www.google.com/search?q=" + encodeURIComponent(v);
  }
  function addChromeTab(url) {
    const t = { id: "ct" + Date.now() + Math.random().toString(36).slice(2, 6), url, home: url, title: "New tab" };
    chromeTabs.push(t); chromeActive = t; renderChromeTabs();
  }
  function renderChromeTabs() {
    const el = $("#ctabs"); if (!el) return;
    el.innerHTML = chromeTabs.map((t) => `
      <div class="tab ${t === chromeActive ? "active" : ""}" data-id="${t.id}">
        <span class="t-title">${esc(t.title || t.url || "New tab")}</span>
        <span class="t-close" data-close="${t.id}" title="Close">${svgIcon("trash")}</span>
      </div>`).join("");
    $$(".tab", el).forEach((tab) => {
      tab.onclick = (e) => {
        if (e.target.dataset.close) { closeChromeTab(e.target.dataset.close); return; }
        chromeActive = chromeTabs.find((x) => x.id === tab.dataset.id); renderChromeTabs();
        const f = $("#b-frame"); if (f) { f.src = "/api/browser/proxy?url=" + encodeURIComponent(chromeActive.url); $("#b-addr").value = chromeActive.url; }
      };
    });
  }
  function closeChromeTab(id) {
    const i = chromeTabs.findIndex((t) => t.id === id); if (i < 0) return;
    chromeTabs.splice(i, 1);
    if (chromeTabs.length === 0) addChromeTab("https://www.google.com");
    if (chromeActive && chromeActive.id === id) chromeActive = chromeTabs[Math.max(0, i - 1)];
    renderChromeTabs(); const f = $("#b-frame"); if (f) f.src = "/api/browser/proxy?url=" + encodeURIComponent(chromeActive.url);
  }

  /* ===================================================================
     XRAY LOGS
     =================================================================== */
  async function renderLogs(root) {
    const last = await api("/xray/last-result").catch(() => ({}));
    root.innerHTML = `
      <div class="panel glass">
        <h3>XRAY LOGS</h3>
        <div class="row-actions" style="margin-bottom:10px">
          <button class="btn btn-sm" id="logs-pause">${svgIcon("pause")} Pause</button>
          <button class="btn btn-sm" id="logs-clear">${svgIcon("trash")} Clear</button>
          <button class="btn btn-sm" id="logs-copy">${svgIcon("copy")} Copy</button>
          <input type="text" id="logs-search" class="search-pill" style="margin:0;max-width:240px;padding:6px 14px" placeholder="Search logs…">
        </div>
        <p class="muted" style="font-size:12px">Live tail of xray stdout/stderr. If Xray failed to start, the exact parser error appears here.</p>
        ${last && last.error ? `<div class="error-text" style="white-space:pre-wrap;font-size:12px;margin-bottom:10px">${esc(last.error)}</div>` : ""}
        <pre id="logs-box" class="codebox" style="min-height:50vh;max-height:70vh;overflow:auto;font-size:11px;background:var(--code-bg);border:1px solid var(--glass-brd);border-radius:10px;padding:12px;color:var(--neon-soft);font-family:'JetBrains Mono',monospace"></pre>
      </div>`;
    const box = root.querySelector("#logs-box");
    let paused = false;
    let ws = null;
    // Auth is carried by the HttpOnly spider_token cookie (auto-sent by the
    // browser on the same-origin WS), so we deliberately do NOT put the JWT in
    // the URL — that would leak it into browser/network logs and proxies.
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const connect = () => {
      ws = new WebSocket(`${proto}//${location.host}/api/xray/logs/stream`);
      ws.onmessage = (e) => { if (!paused) { box.textContent += e.data + "\n"; box.scrollTop = box.scrollHeight; } };
      ws.onclose = () => { if (!paused) setTimeout(connect, 2000); };
      ws.onerror = () => { try { ws.close(); } catch {} };
    };
    connect();
    root.querySelector("#logs-pause").onclick = () => {
      paused = !paused;
      const b = root.querySelector("#logs-pause");
      b.innerHTML = paused ? `${svgIcon("play")} Resume` : `${svgIcon("pause")} Pause`;
    };
    root.querySelector("#logs-clear").onclick = () => { box.textContent = ""; };
    root.querySelector("#logs-copy").onclick = () => { navigator.clipboard.writeText(box.textContent).then(() => toast("Logs copied")).catch(() => toast("Failed to copy", "err")); };
    root.querySelector("#logs-search").oninput = (e) => {
      const q = e.target.value.toLowerCase();
      const lines = box.textContent.split("\n");
      box.textContent = (q ? lines.filter((l) => l.toLowerCase().includes(q)) : lines).join("\n");
    };
    api("/xray/logs?limit=400").then((d) => { box.textContent = (d.lines || []).join("\n"); box.scrollTop = box.scrollHeight; }).catch(() => {});
  }

  /* ===================================================================
     SETTINGS (account, music, theme, telegram)
     =================================================================== */
  async function renderSettings(root) {
    let s = {};
    try { s = await api("/settings") || {}; } catch (e) { toast("Failed to load settings", "err"); }
    const m = s.music || { enabled: false, volume: 70, random: false, track: "", files: [], prefix: "/musics/" };
    music = { enabled: m.enabled, volume: m.volume, random: m.random, track: m.track, files: m.files || [], prefix: m.prefix || "/musics/" };
    const acc = s.settings || {};
    const tgHandle = "amirsplder";
    root.innerHTML = `
      <div class="panel glass" style="margin-top:16px">
        <h3>ACCOUNT</h3>
        <div class="setting-row"><div><div class="k">Username</div><div class="sub">${esc(acc.admin_username || "admin")}</div></div></div>
        <div class="setting-row"><div><div class="k">Email</div><div class="sub">${esc(acc.admin_email || "(none)")}</div></div></div>
        <div class="setting-row"><div><div class="k">Data directory</div><div class="sub">${esc(acc.data_dir || "/app/data")}</div></div></div>
        <div class="setting-row"><div><div class="k">Log level</div><div class="sub">${esc(acc.log_level || "INFO")}</div></div></div>
        <div class="setting-row" style="border:none">
          <button class="btn btn-sm" id="cred-open">Change credentials</button>
        </div>
      </div>

      <div class="panel glass" style="margin-top:16px">
        <h3>BACKGROUND MUSIC</h3>
        <div class="setting-row">
          <div><div class="k">Music</div><div class="sub">Play a random ambient track when the panel opens</div></div>
          <label class="switch"><input type="checkbox" id="m-on" ${m.enabled ? "checked" : ""}> <span class="slider"></span></label>
        </div>
        <div class="setting-row">
          <div><div class="k">Volume</div><div class="sub" id="m-vol-label">${m.volume}%</div></div>
          <input type="range" id="m-vol" min="0" max="100" value="${m.volume}" style="accent-color:var(--neon);max-width:220px">
        </div>
        <div class="setting-row">
          <div><div class="k">Random track</div><div class="sub">Pick a different track each session</div></div>
          <label class="switch"><input type="checkbox" id="m-rand" ${m.random ? "checked" : ""}> <span class="slider"></span></label>
        </div>
        <div class="setting-row">
          <div><div class="k">Selected track</div><div class="sub">Choose a specific file (ignored when Random is on)</div></div>
          <select id="m-track" style="max-width:260px">
            <option value="">${m.random ? "(random)" : "(first / random)"}</option>
            ${(m.files || []).map((f) => `<option value="${esc(f)}" ${m.track === f ? "selected" : ""}>${esc(f)}</option>`).join("")}
          </select>
        </div>
        <div class="row-actions" id="m-test" style="margin-top:8px">
          <button class="btn btn-sm" id="m-next">${svgIcon("next")} Next Track</button>
          <button class="btn btn-sm" id="m-play">${m.enabled ? svgIcon("pause") + " Pause" : svgIcon("play") + " Play"}</button>
        </div>
        ${m.files && m.files.length === 0 ? `<p class="muted" style="font-size:12px;margin-top:10px">No music files found in <code>app/static/musics/</code>. Drop .mp3/.ogg/.wav there.</p>` : ""}
      </div>

      <div class="panel glass" style="margin-top:16px">
        <h3>APPEARANCE</h3>
        <div class="setting-row">
          <div><div class="k">Panel theme</div><div class="sub">Switch between dark and light</div></div>
          <button class="btn btn-sm" id="m-theme">Toggle theme</button>
        </div>
      </div>

      <div class="panel glass" style="margin-top:16px">
        <h3>TELEGRAM CONTACT</h3>
        <div class="setting-row">
          <div><div class="k">Telegram handle</div><div class="sub">Shown on the floating contact button</div></div>
          <input id="tg-handle" class="field" style="max-width:220px;margin:0" value="@${tgHandle}">
        </div>
        <p class="muted" style="font-size:12px">The floating glass button links to <code>https://t.me/${tgHandle}</code> and stays pinned top-left, above the sidebar.</p>
      </div>

      <div class="panel glass" style="margin-top:16px">
        <h3>XRAY CONFIG</h3>
        <div class="row-actions">
          <button class="btn btn-sm" id="validate-btn">Validate Config</button>
          <button class="btn btn-sm" id="reload-btn">Reload Config</button>
          <button class="btn btn-sm" id="restart-btn">Restart Xray</button>
        </div>
        <div id="validate-out" class="muted" style="font-size:12px;white-space:pre-wrap"></div>
      </div>`;

    // ----- credentials -----
    root.querySelector("#cred-open").onclick = () => {
      openModal(`
      <h3>CHANGE CREDENTIALS</h3>
      <form id="cred-form" style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Current password<input type="password" name="current_password" required></label></div>
        <div class="field"><label>New username (optional)<input type="text" name="new_username" placeholder="leave blank to keep"></label></div>
        <div class="field"><label>New password (optional)<input type="password" name="new_password" placeholder="min 6 chars"></label></div>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost modal-close">Cancel</button>
          <button type="submit" class="btn btn-primary neon">Update</button>
        </div>
      </form>`);
      document.querySelector("#modal-card .modal-close").onclick = () => closeModal();
    document.querySelector("#cred-form").onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const body = {
        current_password: fd.get("current_password"),
        new_username: fd.get("new_username") || undefined,
        new_password: fd.get("new_password") || undefined,
      };
      try { await api("/auth/change-credentials", { method: "POST", body }); toast("Credentials updated"); closeModal(); }
      catch (e) { toast(e.message, "err"); }
    };
    };

    // ----- music -----
    const setMusic = async (key, value) => {
      try { await api("/settings", { method: "POST", body: { key, value: String(value) } }); }
      catch (e) { toast(e.message, "err"); }
    };
    root.querySelector("#m-on").onchange = (e) => { setMusic("music_enabled", e.target.checked ? "1" : "0"); music.enabled = e.target.checked; };
    root.querySelector("#m-rand").onchange = (e) => { setMusic("music_random", e.target.checked ? "1" : "0"); music.random = e.target.checked; };
    root.querySelector("#m-vol").oninput = (e) => {
      const v = Number(e.target.value);
      root.querySelector("#m-vol-label").textContent = v + "%";
      music.volume = v;
      const a = document.getElementById("bg-audio"); if (a) a.volume = v / 100;
    };
    root.querySelector("#m-vol").onchange = (e) => setMusic("music_volume", e.target.value);
    root.querySelector("#m-track").onchange = (e) => { setMusic("music_track", e.target.value); music.track = e.target.value; };
    root.querySelector("#m-next").onclick = () => { startMusic(true); };
    root.querySelector("#m-play").onclick = () => {
      const a = document.getElementById("bg-audio");
      if (a && !a.paused) { a.pause(); root.querySelector("#m-play").innerHTML = svgIcon("play") + " Play"; }
      else { if (!a) startMusic(false); else { a.play(); root.querySelector("#m-play").innerHTML = svgIcon("pause") + " Pause"; } }
    };

    // ----- theme -----
    root.querySelector("#m-theme").onclick = () => { $("#theme-btn").click(); };

    // ----- telegram -----
    root.querySelector("#tg-handle").onchange = (e) => {
      const h = (e.target.value || "").replace(/^@/, "");
      toast("Telegram handle updates require a redeploy (set at build). Preview: @" + h);
    };

    // ----- xray -----
    root.querySelector("#validate-btn").onclick = async () => {
      const out = root.querySelector("#validate-out");
      out.textContent = "Validating…";
      try { const r = await api("/xray/validate", { method: "POST" }); out.className = r.ok ? "ok" : "error-text"; out.style.whiteSpace = "pre-wrap"; out.textContent = r.ok ? "Config valid: " + (r.message || "") : r.message + "\n" + (r.stderr || ""); }
      catch (e) { out.className = "error-text"; out.textContent = e.message; }
    };
    root.querySelector("#reload-btn").onclick = async () => { try { await api("/system/reload", { method: "POST" }); toast("Config reloaded"); } catch (e) { toast(e.message, "err"); } };
    root.querySelector("#restart-btn").onclick = async () => { try { await api("/system/restart", { method: "POST" }); toast("Restarting..."); setTimeout(renderSettings, 3000); } catch (e) { toast(e.message, "err"); } };
  }

  /* ===================================================================
     BACKGROUND MUSIC MANAGER (HTML5 audio; requires a user gesture)
     =================================================================== */
  function ensureAudioEl() {
    let a = document.getElementById("bg-audio");
    if (!a) {
      a = document.createElement("audio");
      a.id = "bg-audio";
      a.loop = music.repeat || false;
      a.preload = "auto";
      document.body.appendChild(a);
      a.addEventListener("timeupdate", () => {
        const fill = document.getElementById("music-progress-fill");
        if (a.duration && fill) fill.style.width = (a.currentTime / a.duration * 100) + "%";
      });
      a.addEventListener("ended", () => {
        if (music.repeat) { a.currentTime = 0; a.play().catch(() => {}); }
        else if (music.random) startMusic(true);
        else { const nx = nextTrackName(true); startMusicName(nx); }
      });
    }
    return a;
  }
  function nextTrackName(advance) {
    if (!music.files || music.files.length === 0) return null;
    const a = document.getElementById("bg-audio");
    let i = a && a.dataset.file ? music.files.indexOf(a.dataset.file) : -1;
    if (music.random) return music.files[Math.floor(Math.random() * music.files.length)];
    if (advance) return music.files[(i + 1) % music.files.length];
    return music.files[(i - 1 + music.files.length) % music.files.length];
  }
  function startMusicName(name) {
    if (!name) return;
    const a = ensureAudioEl();
    a.dataset.file = name; a.src = music.prefix + encodeURIComponent(name);
    a.volume = (music.volume || 70) / 100;
    a.play().then(() => { setMusicBar(true, name); }).catch(() => {});
  }
  function pickTrack() {
    if (!music.files || music.files.length === 0) return null;
    if (music.random) return music.files[Math.floor(Math.random() * music.files.length)];
    if (music.track && music.files.includes(music.track)) return music.track;
    return music.files[0];
  }
  function startMusic(forceNext) {
    if (!music.files || music.files.length === 0) return;
    if (forceNext) { startMusicName(nextTrackName(true)); return; }
    const t = pickTrack();
    if (!t) return;
    startMusicName(t);
  }
  function setMusicBar(playing, file) {
    const bar = document.getElementById("music-bar");
    if (!bar) return;
    bar.hidden = false;
    document.getElementById("music-title").textContent = file || (music.files[0] || "—");
    const btn = document.getElementById("music-toggle");
    btn.innerHTML = playing
      ? '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
    document.body.classList.toggle("music-off", !playing);
  }
  function bindMusicBar() {
    const toggle = document.getElementById("music-toggle");
    const prev = document.getElementById("music-prev");
    const next = document.getElementById("music-next");
    const vol = document.getElementById("music-vol");
    const shuffle = document.getElementById("music-shuffle");
    const repeat = document.getElementById("music-repeat");
    const progress = document.getElementById("music-progress");
    if (!toggle) return;
    toggle.onclick = () => {
      const a = document.getElementById("bg-audio");
      if (a && !a.paused) { a.pause(); setMusicBar(false, a.dataset.file); }
      else if (a) { a.play().then(() => setMusicBar(true, a.dataset.file)).catch(() => {}); }
      else { startMusic(false); }
    };
    prev.onclick = () => {
      const a = document.getElementById("bg-audio");
      if (a && a.dataset.file) startMusicName(nextTrackName(false));
      else startMusic(false);
    };
    next.onclick = () => startMusic(true);
    shuffle.onclick = () => {
      music.random = !music.random;
      shuffle.classList.toggle("active", music.random);
      shuffle.setAttribute("aria-pressed", String(music.random));
      setMusic("music_random", music.random ? "1" : "0");
    };
    repeat.onclick = () => {
      music.repeat = !music.repeat;
      repeat.classList.toggle("active", music.repeat);
      repeat.setAttribute("aria-pressed", String(music.repeat));
      const a = document.getElementById("bg-audio");
      if (a) a.loop = music.repeat;
      setMusic("music_repeat", music.repeat ? "1" : "0");
    };
    if (progress) progress.onclick = (e) => {
      const a = document.getElementById("bg-audio");
      if (!a || !a.duration) return;
      const r = progress.getBoundingClientRect();
      a.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * a.duration;
    };
    vol.oninput = (e) => { music.volume = Number(e.target.value); const a = document.getElementById("bg-audio"); if (a) a.volume = music.volume / 100; };
  }
  async function initMusic() {
    try { const s = await api("/settings"); const m = s.music || {}; music = { enabled: m.enabled, volume: m.volume, random: m.random, repeat: m.repeat || false, track: m.track, files: m.files || [], prefix: m.prefix || "/musics/" }; } catch { return; }
    const bar = document.getElementById("music-bar");
    if (bar) { bar.hidden = false; document.getElementById("music-vol").value = music.volume || 70; }
    const shuffle = document.getElementById("music-shuffle");
    const repeat = document.getElementById("music-repeat");
    if (shuffle) { shuffle.classList.toggle("active", !!music.random); shuffle.setAttribute("aria-pressed", String(!!music.random)); }
    if (repeat) { repeat.classList.toggle("active", !!music.repeat); repeat.setAttribute("aria-pressed", String(!!music.repeat)); }
    if (music.enabled && music.files.length) {
      // Autoplay needs a user gesture in browsers. Play on first interaction.
      const kick = () => { startMusic(false); window.removeEventListener("pointerdown", kick); window.removeEventListener("keydown", kick); };
      window.addEventListener("pointerdown", kick, { once: true });
      window.addEventListener("keydown", kick, { once: true });
    }
  }

  function bindTelegramPopup() {
    const fab = document.getElementById("tg-fab");
    const popup = document.getElementById("tg-popup");
    const x = document.getElementById("tg-popup-x");
    if (!fab || !popup) return;
    const open = () => { popup.hidden = false; fab.setAttribute("aria-expanded", "true"); };
    const close = () => { popup.hidden = true; fab.setAttribute("aria-expanded", "false"); };
    fab.onclick = () => { popup.hidden ? open() : close(); };
    if (x) x.onclick = (e) => { e.stopPropagation(); close(); };
    document.addEventListener("click", (e) => {
      if (!popup.hidden && !popup.contains(e.target) && e.target !== fab && !fab.contains(e.target)) close();
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }

  /* ---------- Init ---------- */
  buildNav();
  bindShell();
  bindMusicBar();
  bindTelegramPopup();
  initMusic();
  // Determine initial section from ?tab= (legacy redirect support) or default home.
  const params = new URLSearchParams(location.search);
  const tab = params.get("tab");
  showView(SECTIONS.find((s) => s.id === tab) ? tab : "home");
  window.addEventListener("hashchange", () => { const h = location.hash.slice(1); if (SECTIONS.find((s) => s.id === h)) showView(h); });
})();
