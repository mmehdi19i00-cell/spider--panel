/* ===================================================================
   Spider Panel — frontend SPA controller (vanilla JS, mobile-first)
   =================================================================== */
(() => {
  "use strict";

  const API = "/api";
  let TOKEN = localStorage.getItem("spider_token") || "";
  let ME = localStorage.getItem("spider_user") || "";

  // CSRF: a per-session random token sent on state-changing requests.
  function csrfToken() {
    let t = localStorage.getItem("spider_csrf");
    if (!t) { t = Array.from(crypto.getRandomValues(new Uint8Array(16))).map(b => b.toString(16).padStart(2, "0")).join(""); localStorage.setItem("spider_csrf", t); }
    return t;
  }

  /* ---------------- helpers ---------------- */
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "SpiderSPA" };
    if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
    if (opts.method && opts.method !== "GET") headers["X-CSRF-Token"] = csrfToken();
    let body;
    if (opts.form) {
      // urlencoded for token endpoint
      body = new URLSearchParams(opts.form).toString();
      headers["Content-Type"] = "application/x-www-form-urlencoded";
    } else if (opts.body !== undefined) {
      body = JSON.stringify(opts.body);
    }
    const res = await fetch(API + path, { method: opts.method || "GET", headers, body });
    if (res.status === 401 && path !== "/auth/token" && path !== "/auth/login") {
      logout(true);
      throw new Error("Session expired");
    }
    let data = null;
    try { data = await res.json(); } catch { /* text */ }
    if (!res.ok) {
      const msg = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }

  function toast(msg, kind = "ok") {
    const wrap = $("#toast-wrap");
    const el = document.createElement("div");
    el.className = `toast glass ${kind}`;
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 3200);
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    const u = ["B", "KB", "MB", "GB", "TB", "PB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(2)} ${u[i]}`;
  }
  function fmtDate(d) {
    if (!d) return "—";
    return new Date(d).toLocaleString();
  }

  /* ---------------- auth ---------------- */
  async function doLogin(username, password) {
    try {
      const data = await api("/auth/token", { method: "POST", form: { username, password } });
      TOKEN = data.access_token;
      ME = data.username;
      localStorage.setItem("spider_token", TOKEN);
      localStorage.setItem("spider_user", ME);
      enterApp();
    } catch (e) {
      const el = $("#login-error");
      el.textContent = e.message;
      el.hidden = false;
    }
  }
  function logout(expired = false) {
    TOKEN = ""; ME = "";
    localStorage.removeItem("spider_token");
    localStorage.removeItem("spider_user");
    $("#app-view").hidden = true;
    $("#login-view").hidden = false;
    if (expired) toast("Session expired, please log in", "err");
  }

  /* ---------------- modal ---------------- */
  function openModal(html) {
    $("#modal-card").innerHTML = html;
    $("#modal").hidden = false;
  }
  function closeModal() { $("#modal").hidden = true; $("#modal-card").innerHTML = ""; }
  document.addEventListener("click", (e) => {
    if (e.target.id === "modal") closeModal();
    if (e.target.classList && e.target.classList.contains("modal-close")) closeModal();
  });

  /* ---------------- views ---------------- */
  const views = {
    dashboard: renderDashboard,
    users: renderUsers,
    inbounds: renderInbounds,
    domains: renderDomains,
    system: renderSystem,
    logs: renderLogs,
    settings: renderSettings,
    news: renderNews,
  };
  const titles = {
    dashboard: "Dashboard", users: "Users", inbounds: "Inbounds",
    domains: "Domains", system: "System", logs: "Xray Logs", settings: "Settings",
    news: "News",
  };

  async function showView(name) {
    $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
    $("#view-title").textContent = titles[name] || name;
    $("#sidebar").classList.remove("open");
    const content = $("#content");
    content.innerHTML = `<div class="muted" style="padding:30px">Loading…</div>`;
    try {
      await views[name](content);
    } catch (e) {
      content.innerHTML = `<div class="panel glass"><p class="error-text">${e.message}</p></div>`;
    }
  }

  /* ---- Dashboard ---- */
  async function renderDashboard(root) {
    const s = await api("/dashboard/stats");
    const st = s.extra || {};
    const storage = st.storage || {};
    const cards = [
      ["Total Users", s.total_users, `${s.active_users} active`],
      ["Active", s.active_users, "online allowed"],
      ["Expired", s.expired_users, "need renewal"],
      ["Disabled", s.disabled_users, "manually off"],
      ["Online Conns", s.online_connections, "live sessions"],
      ["Traffic", fmtBytes(s.total_traffic_bytes), "sum used"],
      ["CPU", s.cpu_percent == null ? "—" : s.cpu_percent + "%", "load"],
      ["RAM", s.memory_percent == null ? "—" : s.memory_percent + "%", "used"],
      ["Storage", storage.total_bytes ? fmtBytes(storage.used_bytes) + " / " + fmtBytes(storage.total_bytes) : "—", storage.free_bytes ? fmtBytes(storage.free_bytes) + " free" : ""],
    ];
    const xray = s.xray_running
      ? `<span class="pill on">Xray: RUNNING</span>`
      : `<span class="pill off">Xray: STOPPED</span>`;
    $("#xray-status").outerHTML = xray.startsWith("<span")
      ? xray : `<span id="xray-status" class="pill">Xray: …</span>`;
    const html = `
      <div class="grid cards">
        ${cards.map(([k, v, sub]) => `
          <div class="card glass">
            <div class="k">${k}</div>
            <div class="v">${v}</div>
            <div class="sub">${sub}</div>
          </div>`).join("")}
      </div>
      <div class="panel glass" style="margin-top:16px">
        <h3>SERVER STATUS</h3>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
          <div><div class="k">Xray</div><div class="v" style="font-size:20px">${s.xray_running ? "🟢 Up" : "🔴 Down"}</div></div>
          <div><div class="k">PID</div><div class="v" style="font-size:20px">${s.xray_pid ?? "—"}</div></div>
          <div><div class="k">Auto-restart</div><div class="v" style="font-size:20px">${st.auto_restart ? "🟢 On" : "—"}</div></div>
          <div><div class="k">Last error</div><div class="v" style="font-size:13px;color:${st.last_error ? "var(--neon)" : "inherit"}">${st.last_error ? "see Logs" : "none"}</div></div>
        </div>
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-sm" data-act="restart">⟳ Restart Xray</button>
          <button class="btn btn-sm" data-act="reload">⚡ Reload Config</button>
          <button class="btn btn-sm" id="dash-logs">📜 View Logs</button>
          <button class="btn btn-sm" id="dash-validate">✓ Validate</button>
        </div>
        <div id="dash-validate-out" class="muted" style="font-size:12px;margin-top:10px"></div>
      </div>`;
    root.innerHTML = html;
    root.querySelector('[data-act="restart"]').onclick = sysRestart;
    root.querySelector('[data-act="reload"]').onclick = sysReload;
    root.querySelector("#dash-logs").onclick = () => showView("logs");
    root.querySelector("#dash-validate").onclick = async () => {
      const out = root.querySelector("#dash-validate-out");
      out.textContent = "Validating…";
      try {
        const r = await api("/xray/validate", { method: "POST" });
        out.className = r.ok ? "ok" : "error-text";
        out.style.whiteSpace = "pre-wrap";
        out.textContent = r.ok ? "✓ Config valid: " + (r.message || "") : "✗ " + (r.message || "") + "\n" + (r.stderr || "");
      } catch (e) { out.className = "error-text"; out.textContent = e.message; }
    };
  }

  /* ---- Users ---- */
  async function renderUsers(root) {
    const users = await api("/users");
    const rows = users.map((u) => `
      <tr>
        <td data-label="User">${esc(u.username)}</td>
        <td data-label="UUID"><code style="font-size:11px">${esc(u.uuid.slice(0, 8))}…</code></td>
        <td data-label="Status"><span class="badge ${u.status}">${u.status}</span></td>
        <td data-label="Expires">${fmtDate(u.expire_at)}</td>
        <td data-label="Traffic">${fmtBytes(u.used_traffic_bytes)} / ${u.traffic_limit_bytes ? fmtBytes(u.traffic_limit_bytes) : "∞"}</td>
        <td data-label="IP Limit">${u.ip_limit || "∞"}</td>
        <td data-label="Actions">
          <div class="row-actions">
            <button class="btn btn-sm" data-act="qr" data-id="${u.id}" title="QR Code">▣</button>
            <button class="btn btn-sm" data-act="copycfg" data-id="${u.id}" title="Copy Config">⧉</button>
            <button class="btn btn-sm" data-act="copysub" data-id="${u.id}" title="Copy Subscription">🔗</button>
            <button class="btn btn-sm" data-act="edit" data-id="${u.id}">✎</button>
            <button class="btn btn-sm" data-act="reset" data-id="${u.id}">🔑</button>
            <button class="btn btn-sm ${u.enabled ? "" : "btn-ok"}" data-act="toggle" data-id="${u.id}" data-en="${u.enabled}">${u.enabled ? "⏸" : "▶"}</button>
            <button class="btn btn-sm" data-act="sessions" data-id="${u.id}">📡</button>
            <button class="btn btn-sm btn-ghost danger" data-act="del" data-id="${u.id}">🗑</button>
          </div>
        </td>
      </tr>`).join("");
    root.innerHTML = `
      <div class="panel glass">
        <div class="panel-head">
          <h3>USERS</h3>
          <span class="spacer"></span>
          <input id="user-search" class="field" style="margin:0;max-width:200px" placeholder="search…" />
          <button class="btn btn-primary neon btn-sm" id="add-user">+ Add</button>
        </div>
        <table class="table"><thead><tr>
          <th>User</th><th>UUID</th><th>Status</th><th>Expires</th><th>Traffic</th><th>IP Limit</th><th>Actions</th>
        </tr></thead><tbody>${rows || `<tr><td colspan="7" class="muted">No users yet</td></tr>`}</tbody></table>
      </div>`;
    root.querySelector("#add-user").onclick = () => userForm(null);
    root.querySelector("#user-search").oninput = async (e) => {
      const q = e.target.value.trim();
      const list = await api("/users" + (q ? `?search=${encodeURIComponent(q)}` : ""));
      const tb = root.querySelector("tbody");
      tb.innerHTML = list.map(userRowInner).join("") || `<tr><td colspan="7" class="muted">No users</td></tr>`;
      bindUserRows(root);
    };
    bindUserRows(root);
  }
  function userRowInner(u) {
    return `<tr>
        <td data-label="User">${esc(u.username)}</td>
        <td data-label="UUID"><code style="font-size:11px">${esc(u.uuid.slice(0, 8))}…</code></td>
        <td data-label="Status"><span class="badge ${u.status}">${u.status}</span></td>
        <td data-label="Expires">${fmtDate(u.expire_at)}</td>
        <td data-label="Traffic">${fmtBytes(u.used_traffic_bytes)} / ${u.traffic_limit_bytes ? fmtBytes(u.traffic_limit_bytes) : "∞"}</td>
        <td data-label="IP Limit">${u.ip_limit || "∞"}</td>
        <td data-label="Actions"><div class="row-actions">
          <button class="btn btn-sm" data-act="qr" data-id="${u.id}" title="QR Code">▣</button>
          <button class="btn btn-sm" data-act="copycfg" data-id="${u.id}" title="Copy Config">⧉</button>
          <button class="btn btn-sm" data-act="copysub" data-id="${u.id}" title="Copy Subscription">🔗</button>
          <button class="btn btn-sm" data-act="edit" data-id="${u.id}">✎</button>
          <button class="btn btn-sm" data-act="reset" data-id="${u.id}">🔑</button>
          <button class="btn btn-sm ${u.enabled ? "" : "btn-ok"}" data-act="toggle" data-id="${u.id}" data-en="${u.enabled}">${u.enabled ? "⏸" : "▶"}</button>
          <button class="btn btn-sm" data-act="sessions" data-id="${u.id}">📡</button>
          <button class="btn btn-sm btn-ghost danger" data-act="del" data-id="${u.id}">🗑</button>
        </div></td></tr>`;
  }
  function bindUserRows(root) {
    root.querySelectorAll("[data-act]").forEach((b) => {
      b.onclick = async () => {
        const id = b.dataset.id;
        try {
          if (b.dataset.act === "qr") showUserQR(Number(id));
          else if (b.dataset.act === "copycfg") { const u = await api(`/users/${id}`); const d = await api(`/sub/${u.uuid}?format=json`); copyText(d.uris.join("\n"), "Config copied"); }
          else if (b.dataset.act === "copysub") { const u = await api(`/users/${id}`); copyText(location.origin + "/sub/" + u.uuid, "Subscription copied"); }
          else if (b.dataset.act === "edit") userForm(Number(id));
          else if (b.dataset.act === "reset") { await api(`/users/${id}/reset-uuid`, { method: "POST" }); toast("UUID reset"); showView("users"); }
          else if (b.dataset.act === "toggle") {
            await api(`/users/${id}/${b.dataset.en === "true" ? "disable" : "enable"}`, { method: "POST" });
            toast("Updated"); showView("users");
          } else if (b.dataset.act === "del") {
            if (!confirm("Delete this user?")) return;
            await api(`/users/${id}`, { method: "DELETE" }); toast("Deleted"); showView("users");
          } else if (b.dataset.act === "sessions") showSessions(Number(id));
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
        <img src="/api/qr/${esc(u.uuid)}" alt="qr" style="width:240px;height:240px;background:#fff;border-radius:12px" />
      </div>
      <div class="field"><label>VLESS URI</label><textarea class="codebox" readonly style="min-height:90px">${esc(cfg)}</textarea></div>
      <div class="field"><label>Subscription URL</label><input readonly value="${esc(location.origin + "/sub/" + u.uuid)}" class="codebox"></div>
      <div class="modal-actions">
        <button class="btn" data-c="1">Copy Config</button>
        <button class="btn" data-s="1">Copy Sub</button>
        <button class="btn modal-close">Close</button>
      </div>`);
    $("#modal-card [data-c]").onclick = () => copyText(cfg, "Config copied");
    $("#modal-card [data-s]").onclick = () => copyText(location.origin + "/sub/" + u.uuid, "Sub copied");
  }
  function userForm(id) {
    const isEdit = id != null;
    openModal(`
      <h3>${isEdit ? "EDIT USER" : "NEW USER"}</h3>
      <form id="user-f">
        ${isEdit ? "" : `<div class="field"><label>Username</label><input name="username" required></div>`}
        <div class="field-row">
          <div class="field"><label>Expire (days)</label><input name="expire_days" type="number" min="0" placeholder="0 = never"></div>
          <div class="field"><label>Traffic limit (GB)</label><input name="traffic_limit_gb" type="number" min="0" step="0.1" value="0"></div>
        </div>
        <div class="field-row">
          <div class="field"><label>IP limit (0=∞)</label><input name="ip_limit" type="number" min="0" value="0"></div>
          <div class="field"><label>Enabled inbounds</label><input name="enabled_inbounds" placeholder="comma,separated,tags (blank=all)"></div>
        </div>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost modal-close">Cancel</button>
          <button class="btn btn-primary neon">Save</button>
        </div>
      </form>`);
    $("#user-f").onsubmit = async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target).entries());
      const payload = {
        expire_days: f.expire_days ? Number(f.expire_days) : null,
        traffic_limit_gb: Number(f.traffic_limit_gb || 0),
        ip_limit: Number(f.ip_limit || 0),
        enabled_inbounds: f.enabled_inbounds || "",
      };
      try {
        if (isEdit) { await api(`/users/${id}`, { method: "PUT", body: payload }); }
        else { payload.username = f.username; await api("/users", { method: "POST", body: payload }); }
        closeModal(); toast("Saved"); showView("users");
      } catch (e) { toast(e.message, "err"); }
    };
  }
  async function showSessions(id) {
    const [u] = await api(`/users/${id}`).catch(() => [null]);
    const sessions = await api(`/users/${id}/sessions`);
    openModal(`<h3>SESSIONS — ${esc(u ? u.username : id)}</h3>
      <table class="table"><thead><tr><th>IP</th><th>Connected</th><th>Last seen</th></tr></thead>
      <tbody>${sessions.map((s) => `<tr><td>${esc(s.ip)}</td><td>${fmtDate(s.connected_at)}</td><td>${fmtDate(s.last_seen)}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">No active sessions</td></tr>`}</tbody></table>
      <div class="modal-actions"><button class="btn modal-close">Close</button></div>`);
  }

  /* ---- Inbounds ---- */
  async function renderInbounds(root) {
    const list = await api("/inbounds");
    const rows = list.map((ib) => `
      <tr>
        <td data-label="Tag">${esc(ib.tag)}</td>
        <td data-label="Type">${ib.security} / ${ib.network}</td>
        <td data-label="Domain">${esc(ib.domain || "—")}</td>
        <td data-label="Port">${ib.port}${ib.external_port && ib.external_port !== ib.port ? ` <span class="badge">ext:${ib.external_port}</span>` : ""}</td>
        <td data-label="Reality">${ib.security === "reality" ? `pbk:${esc(ib.public_key.slice(0, 10))}…` : "—"}</td>
        <td data-label="Status"><span class="badge ${ib.enabled ? "active" : "disabled"}">${ib.enabled ? "on" : "off"}</span></td>
        <td data-label="Actions"><div class="row-actions">
          <button class="btn btn-sm" data-act="edit" data-id="${ib.id}">✎</button>
          <button class="btn btn-sm" data-act="keys" data-id="${ib.id}">🔑</button>
          <button class="btn btn-sm btn-ghost danger" data-act="del" data-id="${ib.id}">🗑</button>
        </div></td>
      </tr>`).join("");
    root.innerHTML = `
      <div class="panel glass">
        <div class="panel-head"><h3>INBOUNDS</h3><span class="spacer"></span>
          <button class="btn btn-primary neon btn-sm" id="add-ib">+ New Inbound</button></div>
        <table class="table"><thead><tr><th>Tag</th><th>Type</th><th>Domain</th><th>Port</th><th>Reality</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="7" class="muted">No inbounds</td></tr>`}</tbody></table>
      </div>`;
    root.querySelector("#add-ib").onclick = () => inboundForm(null);
    root.querySelectorAll("[data-act]").forEach((b) => {
      b.onclick = async () => {
        const id = b.dataset.id;
        try {
          if (b.dataset.act === "edit") inboundForm(Number(id));
          else if (b.dataset.act === "keys") { await api(`/inbounds/${id}/regen-keys`, { method: "POST" }); toast("Reality keys regenerated"); showView("inbounds"); }
          else if (b.dataset.act === "del") { if (confirm("Delete inbound?")) { await api(`/inbounds/${id}`, { method: "DELETE" }); toast("Deleted"); showView("inbounds"); } }
        } catch (e) { toast(e.message, "err"); }
      };
    });
  }
  function inboundForm(id) {
    const isEdit = id != null;
    openModal(`<h3>${isEdit ? "EDIT INBOUND" : "NEW INBOUND"}</h3>
      <form id="ib-f">
        <div class="field-row">
          <div class="field"><label>Tag</label><input name="tag" required ${isEdit ? "disabled" : ""}></div>
          <div class="field"><label>Name</label><input name="name"></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Domain (per-inbound; blank=active)</label><input name="domain" placeholder="vpn.example.com"></div>
          <div class="field"><label>External port (client)</label><input name="external_port" type="number" min="1" max="65535" placeholder="Railway TCP port"></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Internal port (bind)</label><input name="port" type="number" min="1" max="65535" value="8443"></div>
          <div class="field"><label>Security</label><select name="security"><option value="reality">reality</option><option value="tls">tls</option><option value="none">none</option></select></div>
          <div class="field"><label>Network</label><select name="network"><option value="xhttp">xhttp</option><option value="ws">ws</option><option value="tcp">tcp</option></select></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Server name (Reality dest)</label><input name="server_name" placeholder="target.com:443"></div>
          <div class="field"><label>SpiderX</label><input name="spider_x" value="/"></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Path</label><input name="transport_path" value="/"></div>
          <div class="field"><label>WS Host (ws only)</label><input name="ws_host" placeholder="optional"></div>
        </div>
        <div class="field"><label>Enabled inbounds? (1/0)</label><input name="enabled" value="1"></div>
        <div class="modal-actions"><button type="button" class="btn btn-ghost modal-close">Cancel</button>
          <button class="btn btn-primary neon">Save</button></div>
      </form>`);
    $("#ib-f").onsubmit = async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target).entries());
      const payload = {
        name: f.name, port: Number(f.port), security: f.security, network: f.network,
        server_name: f.server_name, spider_x: f.spider_x, transport_path: f.transport_path,
        domain: f.domain || "", ws_host: f.ws_host, xhttp_mode: f.xhttp_mode,
        external_port: f.external_port ? Number(f.external_port) : null,
        enabled: f.enabled === "1",
      };
      if (!isEdit) payload.tag = f.tag;
      try {
        if (isEdit) await api(`/inbounds/${id}`, { method: "PUT", body: payload });
        else await api("/inbounds", { method: "POST", body: payload });
        closeModal(); toast("Saved"); showView("inbounds");
      } catch (e) { toast(e.message, "err"); }
    };
  }

  /* ---- Domains ---- */
  async function renderDomains(root) {
    const list = await api("/domains");
    const rows = list.map((d) => `
      <tr><td data-label="Domain">${esc(d.domain)}</td>
      <td data-label="Status">${d.is_active ? '<span class="badge active">ACTIVE</span>' : "—"}</td>
      <td data-label="Note">${esc(d.note || "")}</td>
      <td data-label="Actions"><div class="row-actions">
        ${d.is_active ? "" : `<button class="btn btn-sm btn-ok" data-act="act" data-d="${esc(d.domain)}">Activate</button>`}
        <button class="btn btn-sm btn-ghost danger" data-act="del" data-d="${esc(d.domain)}">🗑</button>
      </div></td></tr>`).join("");
    root.innerHTML = `
      <div class="panel glass">
        <div class="panel-head"><h3>DOMAINS</h3><span class="spacer"></span>
          <input id="dom-in" placeholder="example.com"><button class="btn btn-primary neon btn-sm" id="add-dom">+ Add</button></div>
        <p class="muted" style="font-size:12px">The active domain is used for Reality SNI, TLS, and subscription links.</p>
        <table class="table"><thead><tr><th>Domain</th><th>Status</th><th>Note</th><th>Actions</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="4" class="muted">No domains</td></tr>`}</tbody></table>
      </div>`;
    root.querySelector("#add-dom").onclick = async () => {
      const dom = root.querySelector("#dom-in").value.trim();
      if (!dom) return;
      try { await api("/domains", { method: "POST", body: { domain: dom } }); toast("Added"); showView("domains"); }
      catch (e) { toast(e.message, "err"); }
    };
    root.querySelectorAll("[data-act]").forEach((b) => {
      b.onclick = async () => {
        const d = b.dataset.d;
        try {
          if (b.dataset.act === "act") { await api(`/domains/${encodeURIComponent(d)}/activate`, { method: "POST" }); toast("Activated + config reloaded"); showView("domains"); }
          else if (b.dataset.act === "del") { if (confirm(`Delete ${d}?`)) { await api(`/domains/${encodeURIComponent(d)}`, { method: "DELETE" }); toast("Deleted"); showView("domains"); } }
        } catch (e) { toast(e.message, "err"); }
      };
    });
  }

  /* ---- Xray Logs (live) ---- */
  async function renderLogs(root) {
    const last = await api("/xray/last-result").catch(() => ({}));
    root.innerHTML = `
      <div class="panel glass">
        <div class="panel-head"><h3>XRAY LOGS</h3><span class="spacer"></span>
          <button class="btn btn-sm" id="logs-pause">⏸ Pause</button>
          <button class="btn btn-sm" id="logs-clear">🗑 Clear</button>
        </div>
        <p class="muted" style="font-size:12px">Live tail of xray stdout/stderr. If Xray failed to start, the exact parser error appears here.</p>
        ${last && last.error ? `<div class="error-text" style="white-space:pre-wrap;font-size:12px">${esc(last.error)}</div>` : ""}
        <pre id="logs-box" class="codebox" style="min-height:50vh;max-height:70vh;overflow:auto;font-size:11px"></pre>
      </div>`;
    const box = root.querySelector("#logs-box");
    let paused = false;
    let ws = null;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    function connect() {
      ws = new WebSocket(`${proto}//${location.host}/api/xray/logs/stream?token=${encodeURIComponent(TOKEN)}`);
      ws.onmessage = (e) => { if (!paused) { box.textContent += e.data + "\n"; box.scrollTop = box.scrollHeight; } };
      ws.onclose = () => { if (!paused) setTimeout(connect, 2000); };
      ws.onerror = () => { try { ws.close(); } catch {} };
    }
    connect();
    root.querySelector("#logs-pause").onclick = () => { paused = !paused; root.querySelector("#logs-pause").textContent = paused ? "▶ Resume" : "⏸ Pause"; };
    root.querySelector("#logs-clear").onclick = () => { box.textContent = ""; };
    // fetch backlog
    api("/xray/logs?limit=400").then((d) => { box.textContent = (d.lines || []).join("\n"); box.scrollTop = box.scrollHeight; }).catch(() => {});
  }

  /* ---- Subscription (inline in Users view) ---- */
  async function renderSubscription(root) {
    const users = await api("/users");
    root.innerHTML = `
      <div class="panel glass">
        <h3>SUBSCRIPTION LINKS</h3>
        <p class="muted" style="font-size:12px">Each link returns validated VLESS URIs. Copy the link into your client, or copy the raw config.</p>
        <table class="table"><thead><tr><th>User</th><th>Subscription</th><th>Raw</th></tr></thead>
        <tbody>${users.map((u) => `<tr>
          <td data-label="User">${esc(u.username)}</td>
          <td data-label="Sub"><code id="sub-${u.id}">/sub/${esc(u.uuid)}</code> <button class="btn btn-sm" data-copy="/sub/${esc(u.uuid)}">Copy</button></td>
          <td data-label="Raw"><button class="btn btn-sm" data-raw="${u.id}">View</button></td>
        </tr>`).join("")}</tbody></table>
      </div>`;
    root.querySelectorAll("[data-copy]").forEach((b) => b.onclick = () => copyText(location.origin + b.dataset.copy, "Link copied"));
    root.querySelectorAll("[data-raw]").forEach((b) => b.onclick = () => showRaw(b.dataset.raw));
  }
  async function showRaw(id) {
    const users = await api("/users");
    const u = users.find((x) => String(x.id) === String(id));
    if (!u) return;
    const data = await api(`/sub/${u.uuid}?format=json`);
    openModal(`<h3>CONFIG — ${esc(u.username)}</h3>
      <textarea class="codebox" readonly style="min-height:240px">${esc(data.uris.join("\n"))}</textarea>
      <div class="modal-actions"><button class="btn" data-c="1">Copy all</button><button class="btn modal-close">Close</button></div>`);
    $("#modal-card [data-c]").onclick = () => copyText(data.uris.join("\n"), "Copied");
  }

  /* ---- System ---- */
  async function renderSystem(root) {
    const h = await api("/system/xray/health");
    root.innerHTML = `
      <div class="panel glass">
        <h3>XRAY CORE</h3>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
          <div><div class="k">Status</div><div class="v" style="font-size:18px">${h.running ? "🟢 Running" : "🔴 Stopped"}</div></div>
          <div><div class="k">PID</div><div class="v" style="font-size:18px">${h.pid ?? "—"}</div></div>
          <div><div class="k">Binary</div><div class="v" style="font-size:13px">${esc(h.binary)}</div></div>
          <div><div class="k">Version</div><div class="v" style="font-size:13px">${esc(h.version || "unknown")}</div></div>
          <div><div class="k">Config</div><div class="v" style="font-size:13px">${esc(h.config)}</div></div>
        </div>
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-sm" data-act="start">▶ Start</button>
          <button class="btn btn-sm" data-act="stop">⏸ Stop</button>
          <button class="btn btn-sm" data-act="restart">⟳ Restart</button>
          <button class="btn btn-sm" data-act="reload">⚡ Reload</button>
        </div>
        <div class="row-actions" style="margin-top:10px">
          <button class="btn btn-sm" id="view-logs">📜 View Logs</button>
          <button class="btn btn-sm" id="validate-cfg">✓ Validate Config</button>
          <a class="btn btn-sm" id="dl-cfg" href="/api/xray/config" target="_blank">⤓ Download config.json</a>
        </div>
        <div id="validate-out" class="muted" style="font-size:12px;margin-top:10px"></div>
        <div class="panel glass" style="margin-top:16px">
          <h3>ACCOUNT</h3>
          <button class="btn btn-sm" id="chg-cred">Change username / password</button>
        </div>
      </div>`;
    const map = { start: sysStart, stop: sysStop, restart: sysRestart, reload: sysReload };
    root.querySelectorAll("[data-act]").forEach((b) => b.onclick = map[b.dataset.act]);
    root.querySelector("#chg-cred").onclick = changeCredentials;
    root.querySelector("#view-logs").onclick = () => showView("logs");
    root.querySelector("#validate-cfg").onclick = async () => {
      const out = root.querySelector("#validate-out");
      out.textContent = "Validating…";
      try {
        const r = await api("/xray/validate", { method: "POST" });
        if (r.ok) { out.className = "ok"; out.textContent = "✓ Config valid: " + (r.message || ""); }
        else { out.className = "error-text"; out.style.whiteSpace = "pre-wrap"; out.textContent = "✗ " + (r.message || "invalid") + "\n" + (r.stderr || ""); }
      } catch (e) { out.className = "error-text"; out.textContent = e.message; }
    };
  }
  async function sysStart() { try { await api("/system/xray/start", { method: "POST" }); toast("Xray started"); showView("system"); } catch (e) { toast(e.message, "err"); } }
  async function sysStop() { try { await api("/system/xray/stop", { method: "POST" }); toast("Xray stopped"); showView("system"); } catch (e) { toast(e.message, "err"); } }
  async function sysRestart() { toast("Restarting…"); try { await api("/system/xray/restart", { method: "POST" }); toast("Restarted"); showView("system"); } catch (e) { toast(e.message, "err"); } }
  async function sysReload() { toast("Reloading config…"); try { await api("/system/xray/reload", { method: "POST" }); toast("Reloaded"); showView("system"); } catch (e) { toast(e.message, "err"); } }
  function changeCredentials() {
    openModal(`<h3>CHANGE CREDENTIALS</h3>
      <form id="cc-f">
        <div class="field"><label>Current password</label><input name="current_password" type="password" required></div>
        <div class="field"><label>New username (blank=keep)</label><input name="new_username"></div>
        <div class="field"><label>New password (blank=keep)</label><input name="new_password" type="password"></div>
        <div class="modal-actions"><button type="button" class="btn modal-close">Cancel</button>
          <button class="btn btn-primary neon">Update</button></div>
      </form>`);
    $("#cc-f").onsubmit = async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target).entries());
      const payload = { current_password: f.current_password };
      if (f.new_username) payload.new_username = f.new_username;
      if (f.new_password) payload.new_password = f.new_password;
      try { await api("/auth/change-credentials", { method: "POST", body: payload }); closeModal(); toast("Credentials updated"); $("#me-name").textContent = ME; }
      catch (e) { toast(e.message, "err"); }
    };
  }

  /* ---- Settings ---- */
  async function renderSettings(root) {
    const s = await api("/settings").catch(() => ({}));
    const music = await api("/settings/music/list").catch(() => ({ files: [] }));
    const onOpen = s.music_on_open === "1" || s.music_on_open === "" || s.music_on_open === undefined;
    const files = music.files || [];
    root.innerHTML = `
      <div class="panel glass">
        <h3>PANEL SETTINGS</h3>
        <div class="setting-row">
          <div>
            <div class="k">Music on open</div>
            <div class="sub">Play a random track from <code>/musics</code> every time the panel opens.</div>
          </div>
          <label class="switch">
            <input type="checkbox" id="set-music" ${onOpen ? "checked" : ""}>
            <span class="slider"></span>
          </label>
        </div>
        ${files.length ? `
        <div class="field" style="margin-top:10px">
          <label>Preview (${files.length} track${files.length > 1 ? "s" : ""} available)</label>
          <div class="row-actions">
            <button class="btn btn-sm" id="preview-music">▶ Preview random</button>
            <span id="now-playing" class="sub"></span>
          </div>
        </div>` : `
        <p class="muted" style="font-size:12px;margin-top:10px">
          No audio files found in <code>/musics</code>. Drop <code>.mp3</code>/<code>.wav</code> files there (or run the bundled generator) to enable this feature.
        </p>`}
      </div>

      <div class="panel glass" style="margin-top:16px">
        <h3>ADMIN CREDENTIALS</h3>
        <p class="muted" style="font-size:12px">Change the panel login username / password. Required on first run (default <code>admin</code> / <code>admin</code>).</p>
        <form id="cred-f" class="field-row">
          <div class="field"><label>Current password</label><input name="current_password" type="password" required></div>
          <div class="field"><label>New username (blank=keep)</label><input name="new_username" placeholder="admin"></div>
          <div class="field"><label>New password (blank=keep)</label><input name="new_password" type="password"></div>
        </form>
        <div class="row-actions" style="margin-top:12px">
          <button class="btn btn-primary neon btn-sm" id="save-cred">Update credentials</button>
        </div>
      </div>`;

    const toggle = root.querySelector("#set-music");
    toggle.onchange = async () => {
      try {
        await api("/settings", { method: "POST", body: { key: "music_on_open", value: toggle.checked ? "1" : "" } });
        toast(toggle.checked ? "Music on open enabled" : "Music on open disabled");
        localStorage.setItem("spider_music_on_open", toggle.checked ? "1" : "");
      } catch (e) { toast(e.message, "err"); }
    };

    const preview = root.querySelector("#preview-music");
    if (preview) preview.onclick = () => playRandomMusic(files, root.querySelector("#now-playing"));

    root.querySelector("#save-cred").onclick = async () => {
      const f = Object.fromEntries(new FormData(root.querySelector("#cred-f")).entries());
      if (!f.current_password) { toast("Enter current password", "err"); return; }
      const payload = { current_password: f.current_password };
      if (f.new_username) payload.new_username = f.new_username;
      if (f.new_password) payload.new_password = f.new_password;
      if (!f.new_username && !f.new_password) { toast("Nothing to change", "err"); return; }
      try {
        await api("/auth/change-credentials", { method: "POST", body: payload });
        toast("Credentials updated");
        root.querySelector("#cred-f").reset();
      } catch (e) { toast(e.message, "err"); }
    };
  }

  let _musicAudio = null;
  function playRandomMusic(files, labelEl) {
    if (!files || !files.length) return;
    const pick = files[Math.floor(Math.random() * files.length)];
    if (_musicAudio) { _musicAudio.pause(); _musicAudio = null; }
    const a = new Audio("/musics/" + encodeURIComponent(pick));
    a.loop = true;
    a.volume = 0.5;
    _musicAudio = a;
    a.play().then(() => { if (labelEl) labelEl.textContent = "♪ " + pick; }).catch(() => {
      if (labelEl) labelEl.textContent = "autoplay blocked — click again";
    });
  }

  /* ---- News (latest Iran news, text only, scrollable) ---- */
  let _newsItems = [];
  let _newsIdx = 0;
  async function renderNews(root) {
    root.innerHTML = `
      <div class="panel glass">
        <div class="panel-head">
          <h3>📰 LATEST NEWS · IRAN</h3>
          <span class="spacer"></span>
          <input id="news-q" class="field" style="margin:0;max-width:180px" placeholder="search…" value="Iran" />
          <button class="btn btn-primary neon btn-sm" id="news-search">Search</button>
          <button class="btn btn-sm" id="news-refresh">⟳ Refresh</button>
        </div>
        <p class="muted" style="font-size:12px">Latest Iran news, fetched live. Text only — scroll inside the box if it's long.</p>
        <div id="news-box" class="news-box">
          <div class="muted" style="padding:20px">Loading…</div>
        </div>
        <div class="row-actions" style="margin-top:12px">
          <button class="btn btn-sm" id="news-prev">‹ Prev</button>
          <span id="news-pos" class="sub"></span>
          <button class="btn btn-sm" id="news-next">Next ›</button>
          <span class="spacer"></span>
          <a class="btn btn-sm" id="news-link" target="_blank" rel="noopener noreferrer" style="display:none">↗ Open source</a>
        </div>
      </div>`;

    const box = root.querySelector("#news-box");
    const pos = root.querySelector("#news-pos");
    const link = root.querySelector("#news-link");

    function renderItem() {
      const it = _newsItems[_newsIdx];
      if (!it) { box.innerHTML = `<div class="muted" style="padding:20px">No news available right now.</div>`; return; }
      box.innerHTML = `
        <div class="news-title">${esc(it.title)}</div>
        ${it.source ? `<div class="news-meta">${esc(it.source)}${it.published ? " · " + esc(it.published) : ""}</div>` : ""}
        <div class="news-text">${esc(it.text)}</div>`;
      box.scrollTop = 0;
      pos.textContent = `${_newsIdx + 1} / ${_newsItems.length}`;
      if (it.link) { link.href = it.link; link.style.display = ""; } else { link.style.display = "none"; }
    }

    async function load(q) {
      box.innerHTML = `<div class="muted" style="padding:20px">Searching…</div>`;
      try {
        const d = await api("/news?query=" + encodeURIComponent(q || "Iran") + "&limit=8");
        _newsItems = d.items || [];
        _newsIdx = 0;
        if (!_newsItems.length) {
          box.innerHTML = `<div class="muted" style="padding:20px">${esc(d.error ? "Couldn't load news: " + d.error : "No news found.")}</div>`;
          pos.textContent = "0 / 0"; link.style.display = "none";
          return;
        }
        renderItem();
      } catch (e) {
        box.innerHTML = `<div class="error-text" style="padding:20px">${esc(e.message)}</div>`;
      }
    }

    root.querySelector("#news-search").onclick = () => load(root.querySelector("#news-q").value.trim() || "Iran");
    root.querySelector("#news-q").onkeydown = (e) => { if (e.key === "Enter") load(root.querySelector("#news-q").value.trim() || "Iran"); };
    root.querySelector("#news-refresh").onclick = () => load(root.querySelector("#news-q").value.trim() || "Iran");
    root.querySelector("#news-prev").onclick = () => { if (_newsItems.length) { _newsIdx = (_newsIdx - 1 + _newsItems.length) % _newsItems.length; renderItem(); } };
    root.querySelector("#news-next").onclick = () => { if (_newsItems.length) { _newsIdx = (_newsIdx + 1) % _newsItems.length; renderItem(); } };
    await load("Iran");
  }

  /* ---- autoplay on open ---- */
  function maybePlayMusicOnOpen() {
    // Default ON: if the user has never toggled it, treat as enabled.
    const pref = localStorage.getItem("spider_music_on_open");
    const enabled = pref === null || pref === "1";
    if (pref === null) localStorage.setItem("spider_music_on_open", "1");
    if (!enabled) return;
    if (document.getElementById("app-view") && !document.getElementById("app-view").hidden) {
      api("/settings/music/list").then((m) => playRandomMusic(m.files)).catch(() => {});
    }
  }

  /* ---------------- misc ---------------- */
  function copyText(text, msg) {
    navigator.clipboard.writeText(text).then(() => toast(msg || "Copied")).catch(() => toast("Copy failed", "err"));
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function spawnParticles() {
    const box = $("#particles");
    if (!box) return;
    for (let i = 0; i < 26; i++) {
      const d = document.createElement("span");
      d.className = "dot";
      d.style.left = Math.random() * 100 + "vw";
      d.style.bottom = "-10px";
      d.style.animationDuration = (10 + Math.random() * 16) + "s";
      d.style.animationDelay = (-Math.random() * 18) + "s";
      d.style.transform = `scale(${0.5 + Math.random()})`;
      box.appendChild(d);
    }
  }

  /* ---------------- app entry ---------------- */
  async function enterApp(initial) {
    $("#login-view").hidden = true;
    $("#app-view").hidden = false;
    $("#me-name").textContent = ME;
    // xray status pill
    try {
      const h = await api("/system/xray/health");
      $("#xray-status").outerHTML = h.running
        ? `<span id="xray-status" class="pill on">Xray: RUNNING</span>`
        : `<span id="xray-status" class="pill off">Xray: STOPPED</span>`;
    } catch { /* ignore */ }
    showView(initial && views[initial] ? initial : "dashboard");
    maybePlayMusicOnOpen();
  }

  function bindShell() {
    $("#login-form").onsubmit = (e) => {
      e.preventDefault();
      const u = $("#login-username").value.trim();
      const p = $("#login-password").value;
      doLogin(u, p);
    };
    $$(".nav-item").forEach((n) => n.onclick = () => showView(n.dataset.view));
    $("#logout-btn").onclick = () => logout();
    $("#menu-btn").onclick = () => $("#sidebar").classList.toggle("open");
    $("#theme-toggle").onclick = () => {
      const cur = document.documentElement.getAttribute("data-theme");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("spider_theme", next);
    };
    const savedTheme = localStorage.getItem("spider_theme");
    if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);
  }

  // boot
  bindShell();
  spawnParticles();

  function initialView() {
    const seg = (location.pathname || "/").replace(/^\/+|\/+$/g, "").split("/")[0];
    const map = {
      login: "login", dashboard: "dashboard", users: "users", inbounds: "inbounds",
      domains: "domains", system: "system", logs: "logs", settings: "settings", news: "news",
    };
    return map[seg] || "dashboard";
  }

  if (TOKEN) {
    // verify token still valid, then open the view implied by the URL
    api("/auth/me").then(() => enterApp(initialView())).catch(() => logout());
  } else {
    // no token -> always show login (login view handles its own path)
    if (initialView() === "login") { /* login already shown */ }
  }
})();
