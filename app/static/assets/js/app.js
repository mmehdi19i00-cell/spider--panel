/* =========================================================================
   Spider Panel — app.js  (mobile-first, multi-page, cookie session + CSRF)
   No JWT in the browser; the session cookie authenticates. Every page loads
   this script and calls `bootPage(name)` with its page name.
   ========================================================================= */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- CSRF token (set by /api/auth/login, cached in sessionStorage) ----
  let CSRF = sessionStorage.getItem("csrf") || "";
  function setCsrf(t) { CSRF = t || ""; if (CSRF) sessionStorage.setItem("csrf", CSRF); }
  sessionStorage.removeItem("csrf"); // never persist across reloads longer than needed

  // ---- API helper ----
  async function api(path, opts = {}) {
    const headers = Object.assign(
      { "Content-Type": "application/json", "X-Requested-With": "SpiderSPA" },
      opts.headers || {}
    );
    if (["POST", "PUT", "DELETE", "PATCH"].includes((opts.method || "GET").toUpperCase())) {
      if (CSRF) headers["X-CSRF-Token"] = CSRF;
    }
    const res = await fetch(path, Object.assign({ credentials: "same-origin" }, opts, { headers }));
    if (res.status === 401) { location.href = "/login"; throw new Error("Session expired"); }
    let data = null;
    try { data = await res.json(); } catch { /* non-json */ }
    if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
    return data;
  }

  // ---- toast ----
  function toast(msg, kind) {
    const w = $("#toast-wrap") || (() => { const d = document.createElement("div"); d.id = "toast-wrap"; document.body.appendChild(d); return d; })();
    const t = document.createElement("div");
    t.className = "toast " + (kind || "");
    t.textContent = msg;
    w.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 250); }, 2600);
  }

  // ---- modal ----
  function openModal(html) {
    const b = $("#modal");
    $("#modal-card").innerHTML = html;
    b.hidden = false;
    $$("#modal-card [data-close]").forEach((e) => (e.onclick = closeModal));
  }
  function closeModal() { $("#modal").hidden = true; }

  // ---- sidebar (mobile slide-over) ----
  function initSidebar() {
    const sb = $("#sidebar"), scrim = $("#scrim");
    if (!sb) return;
    const toggle = () => { sb.classList.toggle("open"); scrim.classList.toggle("show"); };
    const menuBtn = $("#menu-btn");
    if (menuBtn) menuBtn.onclick = toggle;
    if (scrim) scrim.onclick = toggle;
    $$(".nav-item", sb).forEach((n) => (n.onclick = () => { sb.classList.remove("open"); scrim.classList.remove("show"); if (n.dataset.go) location.href = n.dataset.go; }));
    const logout = $("#logout-btn");
    if (logout) logout.onclick = logoutNow;
  }
  async function logoutNow() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch {}
    location.href = "/login";
  }

  // ---- clipboard ----
  async function copy(text, label) {
    try { await navigator.clipboard.writeText(text); toast(label || "Copied", "ok"); }
    catch { toast("Copy failed", "err"); }
  }

  // =====================================================================
  //  RENDERERS
  // =====================================================================
  function svg(name) { return window.icon ? window.icon(name) : ""; }

  // ---------- Dashboard ----------
  async function renderDashboard(root) {
    const s = await api("/api/dashboard/stats");
    const cards = [
      ["users", "Users", s.total_users, "user"],
      ["activity", "Online", s.online_connections, "activity"],
      ["bell", "Expired", s.expired_users, "bell"],
      ["shield", "Disabled", s.disabled_users, "shield"],
      ["traffic", "Traffic", fmtBytes(s.total_traffic_bytes), "traffic"],
      ["cpu", "CPU", (s.cpu_percent ?? 0) + "%", "cpu"],
      ["server", "RAM", (s.memory_percent ?? 0) + "%", "server"],
      ["disk", "Storage", "—", "disk"],
    ];
    root.innerHTML =
      `<div class="grid">` +
      cards.map(([ic, k, v]) =>
        `<div class="card stat"><div class="k">${svg(ic)} ${esc(k)}</div><div class="v">${esc(v)}</div></div>`
      ).join("") +
      `</div>
      <div class="panel glass" style="margin-top:14px">
        <h3>System</h3>
        <div class="row">
          <span class="pill ${s.xray_running ? "on" : "off"}">Xray: ${s.xray_running ? "RUNNING" : "STOPPED"}</span>
          ${s.xray_pid ? `<span class="pill">PID ${s.xray_pid}</span>` : ""}
        </div>
      </div>`;
  }

  // ---------- Users ----------
  async function renderUsers(root) {
    const users = await api("/api/users");
    const rows = users.map((u) => `
      <div class="tr">
        <div><strong>${esc(u.username)}</strong><div class="grow">${esc(u.uuid)}</div></div>
        <div class="actions">
          <span class="badge ${u.enabled ? "on" : "off"}">${u.enabled ? "active" : "disabled"}</span>
          <button class="btn btn-sm" data-reset="${u.id}">${svg("refresh")} Reset</button>
          <button class="btn btn-sm btn-danger" data-del="${u.id}">${svg("trash")}</button>
        </div>
      </div>`).join("");
    root.innerHTML = `
      <div class="row-actions" style="margin-bottom:12px">
        <button class="btn btn-primary neon" id="add-user">${svg("userPlus")} New User</button>
      </div>
      <div class="tbl">${rows || `<div class="empty">${svg("users")}<p>No users yet.</p></div>`}</div>`;
    $("#add-user").onclick = showAddUser;
    $$("[data-del]", root).forEach((b) => (b.onclick = () => delUser(b.dataset.del)));
    $$("[data-reset]", root).forEach((b) => (b.onclick = () => resetTraffic(b.dataset.reset)));
  }
  function showAddUser() {
    openModal(`<h3>New User</h3>
      <form id="f"><div class="field"><label>Username</label><input name="username" required></div>
      <div class="field"><label>Expire (days)</label><input name="expire_days" type="number" value="30"></div>
      <div class="field"><label>Traffic limit (GB, 0=unlimited)</label><input name="traffic_limit_gb" type="number" value="0"></div>
      <div class="field"><label>IP limit (0=unlimited)</label><input name="ip_limit" type="number" value="0"></div>
      <div class="modal-actions"><button type="button" class="btn btn-ghost" data-close>Cancel</button>
      <button class="btn btn-primary neon" id="save">Create</button></div></form>`);
    $("#save").onclick = async () => {
      const f = Object.fromEntries(new FormData($("#f")).entries());
      try { await api("/api/users", { method: "POST", body: JSON.stringify({ username: f.username, expire_days: +f.expire_days || null, traffic_limit_gb: +f.traffic_limit_gb || 0, ip_limit: +f.ip_limit || 0 }) }); closeModal(); toast("User created", "ok"); loadNow("users"); }
      catch (e) { toast(e.message, "err"); }
    };
  }
  async function delUser(id) {
    if (!confirm("Delete this user?")) return;
    try { await api("/api/users/" + id, { method: "DELETE" }); toast("Deleted", "ok"); loadNow("users"); }
    catch (e) { toast(e.message, "err"); }
  }
  async function resetTraffic(id) {
    try { await api("/api/users/" + id + "/reset-traffic", { method: "POST" }); toast("Traffic reset", "ok"); loadNow("users"); }
    catch (e) { toast(e.message, "err"); }
  }

  // ---------- Inbounds ----------
  async function renderInbounds(root) {
    const list = await api("/api/inbounds");
    const rows = list.map((i) => `
      <div class="tr">
        <div><strong>${esc(i.name || i.tag)}</strong>
          <div class="grow">${esc(i.protocol)} · ${esc(i.security)} · ${esc(i.network)} · :${i.port}</div></div>
        <div class="actions"><span class="badge ${i.enabled ? "on" : "off"}">${i.enabled ? "on" : "off"}</span>
          <button class="btn btn-sm" data-regen="${i.id}">${svg("key")}</button>
          <button class="btn btn-sm btn-danger" data-del="${i.id}">${svg("trash")}</button></div>
      </div>`).join("");
    root.innerHTML = `
      <div class="row-actions" style="margin-bottom:12px">
        <button class="btn btn-primary neon" id="add-ib">${svg("plus")} New Inbound</button>
      </div>
      <div class="tbl">${rows || `<div class="empty">${svg("inbounds")}<p>No inbounds.</p></div>`}</div>`;
    $("#add-ib").onclick = showAddInbound;
    $$("[data-del]", root).forEach((b) => (b.onclick = async () => { if (!confirm("Delete?")) return; try { await api("/api/inbounds/" + b.dataset.del, { method: "DELETE" }); toast("Deleted", "ok"); loadNow("inbounds"); } catch (e) { toast(e.message, "err"); } }));
    $$("[data-regen]", root).forEach((b) => (b.onclick = async () => { try { await api("/api/inbounds/" + b.dataset.regen + "/regen-keys", { method: "POST" }); toast("Keys regenerated", "ok"); loadNow("inbounds"); } catch (e) { toast(e.message, "err"); } }));
  }
  function showAddInbound() {
    openModal(`<h3>New Inbound</h3><form id="f">
      <div class="field"><label>Tag</label><input name="tag" required></div>
      <div class="field"><label>Name</label><input name="name"></div>
      <div class="field"><label>Port</label><input name="port" type="number" value="24567" required></div>
      <div class="field"><label>Security</label><select name="security"><option value="reality">reality</option><option value="tls">tls</option><option value="none">none</option></select></div>
      <div class="field"><label>Network</label><select name="network"><option value="xhttp">xhttp</option><option value="ws">ws</option><option value="tcp">tcp</option></select></div>
      <div class="field"><label>Server name (SNI)</label><input name="server_name" placeholder="is1-ssl.mzstatic.com"></div>
      <div class="modal-actions"><button type="button" class="btn btn-ghost" data-close>Cancel</button>
      <button class="btn btn-primary neon" id="save">Create</button></div></form>`);
    $("#save").onclick = async () => {
      const f = Object.fromEntries(new FormData($("#f")).entries());
      try { await api("/api/inbounds", { method: "POST", body: JSON.stringify(Object.assign(f, { port: +f.port })) }); closeModal(); toast("Inbound created", "ok"); loadNow("inbounds"); }
      catch (e) { toast(e.message, "err"); }
    };
  }

  // ---------- Domains ----------
  async function renderDomains(root) {
    const list = await api("/api/domains");
    const rows = list.map((d) => `
      <div class="tr">
        <div><strong>${esc(d.domain)}</strong>
          <div class="grow">${d.is_active ? "Active" : "Inactive"}</div></div>
        <div class="actions">${d.is_active ? "" : `<button class="btn btn-sm" data-act="${d.domain}">${svg("check")} Activate</button>`}
          <button class="btn btn-sm btn-danger" data-del="${esc(d.domain)}">${svg("trash")}</button></div>
      </div>`).join("");
    root.innerHTML = `
      <div class="row-actions" style="margin-bottom:12px">
        <button class="btn btn-primary neon" id="add-d">${svg("globe")} Add Domain</button>
      </div>
      <div class="tbl">${rows || `<div class="empty">${svg("domains")}<p>No domains.</p></div>`}</div>`;
    $("#add-d").onclick = () => {
      openModal(`<h3>Add Domain</h3><form id="f"><div class="field"><label>Domain</label><input name="domain" placeholder="vpn.example.com" required></div>
        <div class="modal-actions"><button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary neon" id="save">Add</button></div></form>`);
      $("#save").onclick = async () => { const f = Object.fromEntries(new FormData($("#f")).entries()); try { await api("/api/domains", { method: "POST", body: JSON.stringify(f) }); closeModal(); toast("Domain added", "ok"); loadNow("domains"); } catch (e) { toast(e.message, "err"); } };
    };
    $$("[data-del]", root).forEach((b) => (b.onclick = async () => { if (!confirm("Delete?")) return; try { await api("/api/domains/" + encodeURIComponent(b.dataset.del), { method: "DELETE" }); toast("Deleted", "ok"); loadNow("domains"); } catch (e) { toast(e.message, "err"); } }));
    $$("[data-act]", root).forEach((b) => (b.onclick = async () => { try { await api("/api/domains/" + encodeURIComponent(b.dataset.act) + "/activate", { method: "POST" }); toast("Activated", "ok"); loadNow("domains"); } catch (e) { toast(e.message, "err"); } }));
  }

  // ---------- System ----------
  async function renderSystem(root) {
    const h = await api("/api/system/xray/health");
    root.innerHTML = `
      <div class="panel glass">
        <h3>Xray Core</h3>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr))">
          <div class="stat"><div class="k">${svg("activity")} Status</div><div class="v" style="font-size:18px">${h.running ? "Running" : "Stopped"}</div></div>
          <div class="stat"><div class="k">PID</div><div class="v">${h.pid ?? "—"}</div></div>
          <div class="stat"><div class="k">Binary</div><div class="v" style="font-size:13px">${esc(h.binary)}</div></div>
          <div class="stat"><div class="k">Version</div><div class="v" style="font-size:13px">${esc(h.version || "—")}</div></div>
        </div>
        <div class="row-actions" style="margin-top:14px">
          <button class="btn btn-sm" data-act="start">${svg("play")} Start</button>
          <button class="btn btn-sm" data-act="stop">${svg("stop")} Stop</button>
          <button class="btn btn-sm" data-act="restart">${svg("refresh")} Restart</button>
          <button class="btn btn-sm" id="view-logs">${svg("logs")} View Logs</button>
        </div>
      </div>
      <div class="panel glass" style="margin-top:14px">
        <h3>Account</h3>
        <button class="btn btn-sm" id="chg">${svg("key")} Change username / password</button>
      </div>`;
    const map = { start: "system/xray/start", stop: "system/xray/stop", restart: "system/xray/restart" };
    $$("[data-act]", root).forEach((b) => (b.onclick = async () => { try { await api("/api/" + map[b.dataset.act], { method: "POST" }); toast(b.dataset.act + " ok", "ok"); loadNow("system"); } catch (e) { toast(e.message, "err"); } }));
    $("#view-logs").onclick = () => (location.href = "/xray");
    $("#chg").onclick = () => {
      openModal(`<h3>Change Credentials</h3><form id="cc">
        <div class="field"><label>Current password</label><input name="current_password" type="password" required></div>
        <div class="field"><label>New username (blank=keep)</label><input name="new_username"></div>
        <div class="field"><label>New password (blank=keep)</label><input name="new_password" type="password"></div>
        <div class="modal-actions"><button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary neon" id="save">Update</button></div></form>`);
      $("#save").onclick = async () => { const f = Object.fromEntries(new FormData($("#cc")).entries()); const body = { current_password: f.current_password }; if (f.new_username) body.new_username = f.new_username; if (f.new_password) body.new_password = f.new_password; try { await api("/api/auth/change-credentials", { method: "POST", body }); closeModal(); toast("Updated", "ok"); } catch (e) { toast(e.message, "err"); } };
    };
  }

  // ---------- Settings ----------
  async function renderSettings(root) {
    const s = await api("/api/settings").catch(() => ({}));
    const music = await api("/api/settings/music/list").catch(() => ({ files: [] }));
    const onOpen = s.music_on_open === "1" || s.music_on_open === "" || s.music_on_open === undefined;
    const files = music.files || [];
    root.innerHTML = `
      <div class="panel glass">
        <h3>Panel</h3>
        <div class="card" style="display:flex;align-items:center;justify-content:space-between;gap:10px">
          <div><div style="font-weight:700">${svg("music")} Music on open</div><div class="grow muted" style="font-size:12px">Play a random track from /musics on load.</div></div>
          <label class="switch"><input type="checkbox" id="set-music" ${onOpen ? "checked" : ""}><span class="slider"></span></label>
        </div>
        ${files.length ? `<div class="row-actions" style="margin-top:10px"><button class="btn btn-sm" id="prev">${svg("play")} Preview</button><span id="np" class="sub"></span></div>`
          : `<p class="muted" style="font-size:12px;margin-top:10px">No audio in /musics. Drop .mp3/.ogg/.wav there.</p>`}
      </div>
      <div class="panel glass" style="margin-top:14px">
        <h3>Security</h3>
        <button class="btn btn-sm" id="chg">${svg("key")} Change credentials</button>
      </div>`;
    const toggle = $("#set-music");
    if (toggle) toggle.onchange = async () => { try { await api("/api/settings", { method: "POST", body: { key: "music_on_open", value: toggle.checked ? "1" : "" } }); toast(toggle.checked ? "Music on" : "Music off", "ok"); } catch (e) { toast(e.message, "err"); } };
    const prev = $("#prev");
    if (prev) prev.onclick = () => playRandom(files, $("#np"));
    const chg = $("#chg");
    if (chg) chg.onclick = () => {
      openModal(`<h3>Change Credentials</h3><form id="cc"><div class="field"><label>Current password</label><input name="current_password" type="password" required></div><div class="field"><label>New username</label><input name="new_username"></div><div class="field"><label>New password</label><input name="new_password" type="password"></div><div class="modal-actions"><button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary neon" id="s">Update</button></div></form>`);
      $("#s").onclick = async () => { const f = Object.fromEntries(new FormData($("#cc")).entries()); const body = { current_password: f.current_password }; if (f.new_username) body.new_username = f.new_username; if (f.new_password) body.new_password = f.new_password; try { await api("/api/auth/change-credentials", { method: "POST", body }); closeModal(); toast("Updated", "ok"); } catch (e) { toast(e.message, "err"); } };
    };
  }
  let _audio = null;
  function playRandom(files, label) {
    if (!files.length) return;
    const pick = files[Math.floor(Math.random() * files.length)];
    if (_audio) { _audio.pause(); _audio = null; }
    const a = new Audio("/musics/" + encodeURIComponent(pick));
    a.loop = true; a.volume = 0.5; _audio = a;
    a.play().then(() => { if (label) label.textContent = "♪ " + pick; }).catch(() => { if (label) label.textContent = "autoplay blocked"; });
  }

  // ---------- News ----------
  let _news = [], _ni = 0;
  async function renderNews(root) {
    root.innerHTML = `
      <div class="panel glass">
        <div class="row-actions" style="margin-bottom:8px">
          <input id="nq" class="field" style="margin:0;flex:1;min-width:0" placeholder="search…" value="Iran">
          <button class="btn btn-primary neon btn-sm" id="ns">${svg("search") || "Go"}</button>
          <button class="btn btn-sm" id="nr">${svg("refresh")}</button>
        </div>
        <div id="nbox" class="news-box"><div class="muted" style="padding:14px">Loading…</div></div>
        <div class="row-actions" style="margin-top:10px">
          <button class="btn btn-sm" id="np">${svg("arrowRight")} Prev</button>
          <span id="npos" class="sub"></span>
          <button class="btn btn-sm" id="nn">Next ${svg("arrowRight")}</button>
          <span class="spacer"></span>
          <a class="btn btn-sm" id="nl" target="_blank" rel="noopener" style="display:none">${svg("link")} Source</a>
        </div>
      </div>`;
    const box = $("#nbox");
    function show() {
      const it = _news[_ni]; if (!it) { box.innerHTML = `<div class="muted" style="padding:14px">No news.</div>`; return; }
      box.innerHTML = `<div class="news-title">${esc(it.title)}</div>${it.source ? `<div class="news-meta">${esc(it.source)}</div>` : ""}<div class="news-text">${esc(it.text)}</div>`;
      box.scrollTop = 0; $("#npos").textContent = `${_ni + 1}/${_news.length}`;
      const lk = $("#nl"); if (it.link) { lk.href = it.link; lk.style.display = ""; } else lk.style.display = "none";
    }
    async function load(q) {
      box.innerHTML = `<div class="muted" style="padding:14px">Searching…</div>`;
      try { const d = await api("/api/news?query=" + encodeURIComponent(q || "Iran") + "&limit=8"); _news = d.items || []; _ni = 0; if (!_news.length) { box.innerHTML = `<div class="muted" style="padding:14px">${esc(d.error ? "Couldn't load: " + d.error : "No news.")}</div>`; $("#npos").textContent = "0/0"; $("#nl").style.display = "none"; return; } show(); }
      catch (e) { box.innerHTML = `<div class="error-text" style="padding:14px">${esc(e.message)}</div>`; }
    }
    $("#ns").onclick = () => load($("#nq").value.trim() || "Iran");
    $("#nq").onkeydown = (e) => { if (e.key === "Enter") load($("#nq").value.trim() || "Iran"); };
    $("#nr").onclick = () => load($("#nq").value.trim() || "Iran");
    $("#np").onclick = () => { if (_news.length) { _ni = (_ni - 1 + _news.length) % _news.length; show(); } };
    $("#nn").onclick = () => { if (_news.length) { _ni = (_ni + 1) % _news.length; show(); } };
    await load("Iran");
  }

  // ---------- Xray logs ----------
  let _logTimer = null, _ws = null;
  async function renderXray(root) {
    root.innerHTML = `
      <div class="panel glass">
        <div class="row-actions" style="margin-bottom:8px">
          <button class="btn btn-sm" id="live">${svg("activity")} Live</button>
          <button class="btn btn-sm" id="refresh">${svg("refresh")}</button>
          <button class="btn btn-sm" id="validate">${svg("check")} Validate</button>
        </div>
        <div id="term" class="term"></div>
      </div>`;
    const term = $("#term");
    let buf = [];
    async function pull() {
      try { const d = await api("/api/xray/logs?limit=200"); buf = (d.lines || []); paint(); }
      catch (e) { paint([{ t: "err", m: e.message }]); }
    }
    function paint(lines) {
      const ls = lines || buf;
      term.innerHTML = ls.slice(-300).map((l) => {
        const cls = (l.t === "err" || /error|fail/i.test(l.m)) ? "l-err" : (l.t === "warn" || /warn/i.test(l.m)) ? "l-warn" : (l.t === "ok" || /started|running|success/i.test(l.m)) ? "l-ok" : (l.t === "dim") ? "l-dim" : "l-info";
        return `<div class="${cls}">${esc(l.m)}</div>`;
      }).join("");
      term.scrollTop = term.scrollHeight;
    }
    async function validate() {
      const out = $("#term");
      try { const r = await api("/api/xray/validate", { method: "POST" }); if (r.ok) toast("Config valid", "ok"); else toast("Invalid: " + (r.message || ""), "err"); }
      catch (e) { toast(e.message, "err"); }
    }
    $("#live").onclick = () => {
      if (_ws) { _ws.close(); _ws = null; $("#live").textContent = "Live"; return; }
      const proto = location.protocol === "https:" ? "wss" : "ws";
      _ws = new WebSocket(`${proto}//${location.host}/api/xray/logs/stream`);
      _ws.onmessage = (e) => { try { const d = JSON.parse(e.data); if (d.line) { buf.push(d.line); if (buf.length > 600) buf.shift(); paint(); } } catch {} };
      _ws.onclose = () => { $("#live").textContent = "Live"; };
      $("#live").textContent = "Stop";
    };
    $("#refresh").onclick = pull;
    $("#validate").onclick = validate;
    pull();
    _logTimer = setInterval(pull, 5000);
  }

  // ---------- Subscription ----------
  async function renderSub(root) {
    // Public landing: enter a UUID, or open /sub/<uuid>
    const params = new URLSearchParams(location.search);
    const uuid = params.get("uuid");
    if (uuid) return loadSub(root, uuid);
    root.innerHTML = `
      <div class="auth-card glass" style="margin:8vh auto">
        <div class="brand"><div class="spider-logo">${spiderLogo()}</div><h1 class="title-neon">SUBSCRIPTION</h1></div>
        <div class="auth-form">
          <label class="lbl">Subscription UUID</label>
          <input id="suuid" placeholder="paste your uuid" />
          <button class="btn btn-primary neon btn-block" id="go">${svg("arrowRight")} Open</button>
        </div>
        <p class="muted" style="margin-top:10px">Your subscription link looks like <code>/sub/&lt;uuid&gt;</code>.</p>
      </div>`;
    $("#go").onclick = () => { const v = $("#suuid").value.trim(); if (v) location.href = "/sub?uuid=" + encodeURIComponent(v); };
  }
  async function loadSub(root, uuid) {
    root.innerHTML = `<div class="empty">Loading…</div>`;
    try {
      const data = await api(`/sub/${encodeURIComponent(uuid)}?format=json`);
      const uri = data.uris && data.uris[0];
      const user = data.user || {};
      const expire = user.expire_at ? new Date(user.expire_at).toLocaleString() : "—";
      const traffic = fmtBytes(user.used_traffic_bytes || 0) + " / " + (user.traffic_limit_bytes ? fmtBytes(user.traffic_limit_bytes) : "∞");
      root.innerHTML = `
        <div class="panel glass">
          <div class="logo-row" style="margin-bottom:12px"><div class="spider-mini">${spiderLogo(26)}</div><strong>${esc(data.username || "Subscription")}</strong></div>
          <div class="qr-box"><img id="qr" alt="QR"></div>
          <div class="row-actions" style="justify-content:center">
            <button class="btn btn-primary neon btn-sm" id="copyu">${svg("copy")} Copy</button>
            <button class="btn btn-sm" id="open">${svg("link")} Open</button>
          </div>
          <div class="code" id="uri" style="margin-top:10px">${esc(uri)}</div>
        </div>
        <div class="grid" style="margin-top:12px">
          <div class="card stat"><div class="k">${svg("bell")} Expire</div><div class="v" style="font-size:15px">${esc(expire)}</div></div>
          <div class="card stat"><div class="k">${svg("traffic")} Traffic</div><div class="v" style="font-size:15px">${esc(traffic)}</div></div>
        </div>
        <div class="row-actions" style="margin-top:12px">
          <button class="btn btn-sm" id="reset">${svg("refresh")} Reset Traffic</button>
        </div>`;
      $("#qr").src = "/api/qr/" + encodeURIComponent(uuid);
      $("#copyu").onclick = () => copy(uri, "Link copied");
      $("#open").onclick = () => window.open(uri, "_blank");
      $("#reset").onclick = () => toast("Ask your admin to reset traffic", "ok");
    } catch (e) {
      root.innerHTML = `<div class="empty">${svg("x")}<p>${esc(e.message)}</p><a class="btn btn-sm" href="/sub">Back</a></div>`;
    }
  }

  // ---------- Login ----------
  function bindLogin() {
    const form = $("#login-form");
    if (!form) return;
    const pw = $("#login-password"), toggle = $("#toggle-pw");
    if (toggle) toggle.onclick = () => { pw.type = pw.type === "password" ? "text" : "password"; };
    form.onsubmit = async (e) => {
      e.preventDefault();
      const u = $("#login-username").value.trim(), p = $("#login-password").value;
      const err = $("#login-error"); err.hidden = true;
      try {
        const r = await fetch("/api/auth/login", {
          method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "SpiderSPA" },
          credentials: "same-origin", body: JSON.stringify({ username: u, password: p }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { err.textContent = data.detail || "Login failed"; err.hidden = false; return; }
        setCsrf(data.csrf_token);
        location.href = "/dashboard";
      } catch (ex) { err.textContent = "Network error"; err.hidden = false; }
    };
  }

  // ---------- shared spider logo ----------
  function spiderLogo(size) {
    size = size || 64;
    return `<svg viewBox="0 0 100 100" width="${size}" height="${size}"><g class="legs" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round"><path d="M50 50 L18 22 M50 50 L14 50 M50 50 L18 78 M50 50 L82 22 M50 50 L86 50 M50 50 L82 78"/></g><circle class="body" cx="50" cy="50" r="14" fill="currentColor"/><circle cx="45" cy="46" r="2.4" fill="#08060a"/><circle cx="55" cy="46" r="2.4" fill="#08060a"/></svg>`;
  }

  // ---------- per-page renderer map ----------
  const renderers = {
    dashboard: renderDashboard, users: renderUsers, inbounds: renderInbounds,
    domains: renderDomains, system: renderSystem, settings: renderSettings,
    news: renderNews, xray: renderXray, sub: renderSub,
  };
  function loadNow(page) { const root = $("#content"); if (root && renderers[page]) renderers[page](root); }

  function fmtBytes(n) {
    n = Number(n) || 0; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(2) : n) + " " + u[i];
  }

  // ---- boot a page ----
  window.bootPage = function (page) {
    if (page === "login") { bindLogin(); return; }
    initSidebar();
    // mark active nav
    const active = document.querySelector(`.nav-item[data-go="/${page}"]`);
    if (active) $$(".nav-item").forEach((n) => n.classList.remove("active")), active.classList.add("active");
    const titleEl = $("#view-title"); if (titleEl && active) titleEl.textContent = active.textContent.trim();
    const root = $("#content");
    const me = $("#me-name");
    api("/api/auth/me").then((m) => { if (me) me.textContent = m.username; }).catch(() => {});
    if (renderers[page]) renderers[page](root);
  };

  // stop timers/logs when leaving
  window.addEventListener("pagehide", () => { if (_logTimer) clearInterval(_logTimer); if (_ws) _ws.close(); });
})();
