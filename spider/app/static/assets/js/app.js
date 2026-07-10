/* ===================================================================
   Spider Panel — frontend SPA controller (vanilla JS, mobile-first)
   =================================================================== */
(() => {
  "use strict";

  const API = "/api";
  let TOKEN = localStorage.getItem("spider_token") || "";
  let ME = localStorage.getItem("spider_user") || "";

  /* ---------------- helpers ---------------- */
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
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
    subscription: renderSubscription,
    system: renderSystem,
  };
  const titles = {
    dashboard: "Dashboard", users: "Users", inbounds: "Inbounds",
    domains: "Domains", subscription: "Subscriptions", system: "System",
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
    const cards = [
      ["Total Users", s.total_users, `${s.active_users} active`],
      ["Active", s.active_users, "online allowed"],
      ["Expired", s.expired_users, "need renewal"],
      ["Disabled", s.disabled_users, "manually off"],
      ["Online Conns", s.online_connections, "live sessions"],
      ["Traffic", fmtBytes(s.total_traffic_bytes), "sum used"],
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
          <div><div class="k">CPU</div><div class="v" style="font-size:20px">${s.cpu_percent == null ? "—" : s.cpu_percent + "%"}</div></div>
          <div><div class="k">RAM</div><div class="v" style="font-size:20px">${s.memory_percent == null ? "—" : s.memory_percent + "%"}</div></div>
        </div>
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-sm" data-act="restart">⟳ Restart Xray</button>
          <button class="btn btn-sm" data-act="reload">⚡ Reload Config</button>
        </div>
      </div>`;
    root.innerHTML = html;
    root.querySelector('[data-act="restart"]').onclick = sysRestart;
    root.querySelector('[data-act="reload"]').onclick = sysReload;
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
          if (b.dataset.act === "edit") userForm(Number(id));
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
        <td data-label="Port">${ib.port}</td>
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
        <table class="table"><thead><tr><th>Tag</th><th>Type</th><th>Port</th><th>Reality</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="muted">No inbounds</td></tr>`}</tbody></table>
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
          <div class="field"><label>Port</label><input name="port" type="number" min="1" max="65535" value="443"></div>
          <div class="field"><label>Security</label><select name="security"><option value="reality">reality</option><option value="tls">tls</option><option value="none">none</option></select></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Network</label><select name="network"><option value="xhttp">xhttp</option><option value="ws">ws</option><option value="tcp">tcp</option></select></div>
          <div class="field"><label>XHTTP mode</label><select name="xhttp_mode"><option>auto</option><option>packet-up</option><option>packet-down</option><option>stream-up</option><option>stream-down</option></select></div>
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
        ws_host: f.ws_host, xhttp_mode: f.xhttp_mode,
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

  /* ---- Subscription ---- */
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
          <div><div class="k">Config</div><div class="v" style="font-size:13px">${esc(h.config)}</div></div>
        </div>
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-sm" data-act="start">▶ Start</button>
          <button class="btn btn-sm" data-act="stop">⏸ Stop</button>
          <button class="btn btn-sm" data-act="restart">⟳ Restart</button>
          <button class="btn btn-sm" data-act="reload">⚡ Reload</button>
        </div>
        <div class="panel glass" style="margin-top:16px">
          <h3>ACCOUNT</h3>
          <button class="btn btn-sm" id="chg-cred">Change username / password</button>
        </div>
      </div>`;
    const map = { start: sysStart, stop: sysStop, restart: sysRestart, reload: sysReload };
    root.querySelectorAll("[data-act]").forEach((b) => b.onclick = map[b.dataset.act]);
    root.querySelector("#chg-cred").onclick = changeCredentials;
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
  async function enterApp() {
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
    showView("dashboard");
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
  if (TOKEN) {
    // verify token still valid
    api("/auth/me").then(() => enterApp()).catch(() => logout());
  }
})();
