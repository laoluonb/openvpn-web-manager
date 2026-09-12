"use strict";

const state = {
  csrf: "",
  user: "",
  overview: null,
  view: "overview",
  search: "",
  timer: null,
  updateTimer: null,
  confirmAction: null,
  profile: null,
  serverDirty: false,
};

const byId = (id) => document.getElementById(id);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`;

async function api(path, options = {}) {
  const request = { credentials: "same-origin", ...options, headers: { ...(options.headers || {}) } };
  if (request.body && typeof request.body !== "string") {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  if (request.method && request.method !== "GET" && state.csrf) {
    request.headers["X-CSRF-Token"] = state.csrf;
  }
  const response = await fetch(path, request);
  const type = response.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await response.json() : await response.blob();
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/login") showLogin();
    throw new Error(data?.error || `请求失败（${response.status}）`);
  }
  return data;
}

function setBusy(button, busy, label) {
  if (!button) return;
  if (busy) {
    if (!button.dataset.original) button.dataset.original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `${icon("refresh")}<span>${label || "处理中…"}</span>`;
  } else {
    button.disabled = false;
    if (button.dataset.original) {
      button.innerHTML = button.dataset.original;
      delete button.dataset.original;
    }
  }
}

function toast(title, message, type = "success") {
  const item = document.createElement("div");
  item.className = `toast ${type === "error" ? "error" : ""}`;
  item.innerHTML = `${icon(type === "error" ? "x" : "shield")}<div><strong></strong><span></span></div>`;
  item.querySelector("strong").textContent = title;
  item.querySelector("span").textContent = message;
  byId("toastRegion").append(item);
  window.setTimeout(() => item.remove(), 4300);
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("ovpn-theme", theme);
  document.querySelectorAll(".theme-icon use").forEach((node) => node.setAttribute("href", theme === "dark" ? "#i-sun" : "#i-moon"));
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

function clearTimers() {
  window.clearInterval(state.timer);
  window.clearInterval(state.updateTimer);
  state.timer = null;
  state.updateTimer = null;
}

function showLogin() {
  clearTimers();
  state.csrf = "";
  state.profile = null;
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  byId("dashboardView").classList.add("hidden");
  byId("loginView").classList.remove("hidden");
  byId("loginPassword").value = "";
  window.setTimeout(() => byId("loginUsername").focus(), 40);
}

function showDashboard(session) {
  state.user = session.user;
  state.csrf = session.csrf;
  byId("loginView").classList.add("hidden");
  byId("dashboardView").classList.remove("hidden");
  byId("sidebarUsername").textContent = state.user;
  byId("userAvatar").textContent = (state.user[0] || "A").toUpperCase();
  switchView("overview");
  loadOverview();
  window.clearInterval(state.timer);
  state.timer = window.setInterval(() => loadOverview(true), 15000);
}

async function restoreSession() {
  try {
    const session = await api("/api/session");
    showDashboard(session);
  } catch (_) {
    showLogin();
  }
}

async function login(event) {
  event.preventDefault();
  const button = byId("loginButton");
  const error = byId("loginError");
  error.textContent = "";
  setBusy(button, true, "正在登录…");
  try {
    const session = await api("/api/login", {
      method: "POST",
      body: { username: byId("loginUsername").value.trim(), password: byId("loginPassword").value },
    });
    showDashboard(session);
  } catch (err) {
    error.textContent = err.message;
  } finally {
    setBusy(button, false);
  }
}

async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch (_) { /* cookie is cleared by expiry or session loss */ }
  showLogin();
}

function switchView(view) {
  state.view = view;
  const titles = {
    overview: ["控制中心", "概览"],
    clients: ["访问管理", "客户端"],
    server: ["网络配置", "服务端"],
    system: ["运行维护", "系统"],
  };
  document.querySelectorAll(".view-panel").forEach((panel) => panel.classList.add("hidden"));
  byId(`${view}Panel`).classList.remove("hidden");
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  byId("pageEyebrow").textContent = titles[view][0];
  byId("pageTitle").textContent = titles[view][1];
  closeSidebar();
  if (view === "system") {
    loadLogs();
    loadUpdateStatus();
  }
  if (view === "server" && state.overview && !state.serverDirty) {
    renderServerForm(state.overview.status);
  }
}

function formatDate(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(parsed);
}

function formatBytes(value) {
  const number = Number(value || 0);
  if (!number) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(number) / Math.log(1024)), units.length - 1);
  return `${(number / (1024 ** index)).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function statusClass(client) {
  if (client.status === "revoked" || client.status === "expired") return "revoked";
  return client.online ? "online" : "offline";
}

function statusLabel(client) {
  if (client.status === "revoked") return "已吊销";
  if (client.status === "expired") return "已过期";
  return client.online ? "在线" : "离线";
}

function serviceLabel(value) {
  const labels = {
    active: "正常运行",
    inactive: "已停止",
    failed: "运行故障",
    activating: "正在启动",
    deactivating: "正在停止",
    unknown: "状态未知",
  };
  return labels[value] || value || "状态未知";
}

function renderOverview(data) {
  const status = data.status;
  const clients = data.clients || [];
  const active = clients.filter((item) => item.status === "active");
  const hero = byId("heroStatus");
  hero.className = `service-pill ${status.healthy ? "online" : "offline"}`;
  hero.querySelector("span:last-child").textContent = status.healthy ? "OpenVPN 运行正常" : `服务${serviceLabel(status.service)}`;
  const endpoint = `${status.endpoint}:${status.vpn_port}/${String(status.vpn_protocol).toUpperCase()}`;
  byId("heroEndpoint").textContent = endpoint;
  byId("onlineCount").textContent = status.online_count;
  byId("activeCount").textContent = active.length;
  byId("totalCount").textContent = clients.length;
  byId("transportValue").textContent = `${String(status.vpn_protocol).toUpperCase()} · ${status.vpn_port}`;
  byId("subnetValue").textContent = status.vpn_subnet;
  byId("clientNavCount").textContent = active.length;
  byId("serviceValue").textContent = serviceLabel(status.service);
  byId("versionValue").textContent = String(status.version || "OpenVPN").replace(/^OpenVPN\s+/i, "");
  byId("gatewayValue").textContent = `${status.endpoint}:${status.vpn_port}`;
  byId("managementValue").textContent = `HTTPS :${status.web_port}`;
  byId("serverStateBadge").textContent = serviceLabel(status.service);
  byId("serverStateBadge").className = `badge ${status.healthy ? "success" : "danger"}`;
  byId("lastUpdated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
  byId("configEndpoint").textContent = status.endpoint;
  byId("configTransport").textContent = `${String(status.vpn_protocol).toUpperCase()} 端口 ${status.vpn_port}`;
  byId("configSubnet").textContent = status.vpn_subnet;
  byId("configWebPort").textContent = status.web_port;
  byId("configState").textContent = serviceLabel(status.service);
  byId("managerVersion").textContent = status.manager_version || "未知";
  byId("updateOpenvpnVersion").textContent = String(status.version || "OpenVPN").replace(/^OpenVPN\s+/i, "");
  if (!state.serverDirty) renderServerForm(status);
  renderRecent(clients);
  renderClientTable(clients);
}

function renderServerForm(status) {
  byId("serverEndpoint").value = status.endpoint || "";
  byId("serverVpnPort").value = status.vpn_port || 1194;
  byId("serverProtocol").value = status.vpn_protocol || "udp";
  byId("serverSubnet").value = status.vpn_subnet || "10.8.0.0/24";
  const dns = status.dns_servers || [];
  byId("serverDns1").value = dns[0] || "";
  byId("serverDns2").value = dns[1] || "";
  byId("serverMaxClients").value = status.max_clients || 100;
  byId("serverWebAllow").value = status.web_allow || "0.0.0.0/0";
  byId("serverTunMtu").value = status.tun_mtu || 1500;
  byId("serverMssfix").value = status.mssfix ?? 1450;
  byId("serverKeepalivePing").value = status.keepalive_ping || 10;
  byId("serverKeepaliveTimeout").value = status.keepalive_timeout || 120;
  byId("serverDataCipher").value = status.data_cipher || "AES-256-GCM";
  byId("serverAuthDigest").value = status.auth_digest || "SHA256";
  byId("serverLogVerb").value = String(status.log_verb ?? 3);
  byId("serverPushRoutes").value = (status.push_routes || []).join("\n");
  byId("serverRedirectGateway").checked = status.redirect_gateway !== false;
  byId("serverWebPort").textContent = `HTTPS :${status.web_port}`;
}

function renderRecent(clients) {
  const root = byId("recentClients");
  root.replaceChildren();
  const recent = [...clients].sort((a, b) => String(b.created_at || b.revoked_at || "").localeCompare(String(a.created_at || a.revoked_at || ""))).slice(0, 4);
  if (!recent.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact";
    empty.textContent = "尚未签发任何客户端配置。";
    root.append(empty);
    return;
  }
  recent.forEach((client) => {
    const row = document.createElement("div");
    row.className = "recent-item";
    row.innerHTML = `<div class="client-symbol">${icon("key")}<i></i></div><div class="recent-name"><strong></strong><span></span></div><div class="recent-address"><strong></strong><span>VPN 地址</span></div><span class="badge"></span>`;
    row.querySelector(".client-symbol i").className = client.online ? "online" : "";
    row.querySelector(".recent-name strong").textContent = client.name;
    row.querySelector(".recent-name span").textContent = client.status === "active" ? `创建于 ${formatDate(client.created_at)}` : `吊销于 ${formatDate(client.revoked_at)}`;
    row.querySelector(".recent-address strong").textContent = client.connection?.virtual_address || "当前未连接";
    const badge = row.querySelector(".badge");
    badge.textContent = statusLabel(client);
    badge.className = `badge ${client.online ? "success" : client.status === "active" ? "neutral" : "danger"}`;
    root.append(row);
  });
}

function renderClientTable(clients) {
  const root = byId("clientTableBody");
  root.replaceChildren();
  const needle = state.search.toLowerCase();
  const filtered = clients.filter((item) => item.name.toLowerCase().includes(needle));
  if (!filtered.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.innerHTML = `<div class="empty-state">${needle ? "没有匹配的客户端。" : "尚未创建客户端配置。"}</div>`;
    row.append(cell);
    root.append(row);
    return;
  }
  filtered.forEach((client) => {
    const row = document.createElement("tr");
    const connection = client.connection || {};
    row.innerHTML = `
      <td><div class="client-cell"><div class="client-symbol">${icon("key")}</div><div><button class="client-name-button" type="button"></button><span></span></div></div></td>
      <td><div class="state-cell"><span class="state-label"><i></i><span></span></span><span class="cell-subtle"></span></div></td>
      <td><div class="route-badge"><strong></strong><span></span></div></td>
      <td><strong class="vpn-address"></strong><div class="cell-subtle real-address"></div></td>
      <td><div class="traffic-cell"><span class="received"></span><span class="sent"></span></div></td>
      <td><strong class="expiry"></strong><div class="cell-subtle serial"></div></td>
      <td><div class="row-actions"><button class="row-button view" title="查看配置内容" aria-label="查看配置内容">${icon("eye")}</button><button class="row-button network" title="配置下级内网" aria-label="配置下级内网">${icon("route")}</button><button class="row-button warning disconnect" title="踢出当前连接" aria-label="踢出当前连接">${icon("plug")}</button><button class="row-button download" title="下载配置" aria-label="下载配置">${icon("download")}</button><button class="row-button danger revoke" title="吊销配置" aria-label="吊销配置">${icon("trash")}</button></div></td>`;
    const nameButton = row.querySelector(".client-name-button");
    nameButton.textContent = client.name;
    row.querySelector(".client-cell span").textContent = `创建于 ${formatDate(client.created_at)}`;
    const label = row.querySelector(".state-label");
    label.classList.add(statusClass(client));
    label.querySelector("span").textContent = statusLabel(client);
    const stateDetail = connection.connected_since
      || (client.status === "revoked" ? `吊销于 ${formatDate(client.revoked_at)}` : "")
      || (client.status === "expired" ? "证书已过期" : "等待连接");
    row.querySelector(".state-cell .cell-subtle").textContent = stateDetail;
    const route = row.querySelector(".route-badge");
    route.querySelector("strong").textContent = client.lan_subnet || "未配置";
    const routeState = route.querySelector("span");
    routeState.textContent = !client.lan_subnet
      ? "普通终端"
      : !client.online
        ? "客户端离线，内网不可达"
        : client.share_lan
          ? "其他客户端可访问"
          : "仅服务端可访问";
    routeState.classList.toggle("shared", Boolean(client.lan_subnet && client.online && client.share_lan));
    routeState.classList.toggle("unavailable", Boolean(client.lan_subnet && !client.online));
    row.querySelector(".vpn-address").textContent = connection.virtual_address || "—";
    row.querySelector(".real-address").textContent = connection.real_address || "没有活动隧道";
    row.querySelector(".received").textContent = `↓ ${formatBytes(connection.bytes_received)}`;
    row.querySelector(".sent").textContent = `↑ ${formatBytes(connection.bytes_sent)}`;
    row.querySelector(".expiry").textContent = formatDate(client.expires_at);
    row.querySelector(".serial").textContent = `序列号 ${client.serial || "—"}`;
    const active = client.status === "active";
    const view = row.querySelector(".view");
    const network = row.querySelector(".network");
    const disconnect = row.querySelector(".disconnect");
    const download = row.querySelector(".download");
    const revoke = row.querySelector(".revoke");
    view.disabled = !active || !client.has_profile;
    network.disabled = !active;
    disconnect.disabled = !active || !client.online;
    download.disabled = !active || !client.has_profile;
    revoke.disabled = !active;
    nameButton.disabled = view.disabled;
    nameButton.addEventListener("click", () => viewProfile(client.name));
    view.addEventListener("click", () => viewProfile(client.name));
    network.addEventListener("click", () => openNetworkDialog(client));
    disconnect.addEventListener("click", () => confirmDisconnect(client.name));
    download.addEventListener("click", () => downloadProfile(client.name));
    revoke.addEventListener("click", () => confirmRevoke(client.name));
    root.append(row);
  });
}

async function loadOverview(quiet = false) {
  const refresh = byId("refreshButton");
  if (!quiet) refresh.classList.add("spinning");
  try {
    state.overview = await api("/api/overview");
    renderOverview(state.overview);
  } catch (err) {
    if (!quiet) toast("刷新失败", err.message, "error");
  } finally {
    refresh.classList.remove("spinning");
  }
}

function openDialog(id) {
  const dialog = byId(id);
  if (!dialog.open) dialog.showModal();
}

function closeDialog(id) {
  const dialog = byId(id);
  if (dialog.open) dialog.close();
}

function updateShareControl(inputId, checkboxId) {
  const hasSubnet = Boolean(byId(inputId).value.trim());
  byId(checkboxId).disabled = !hasSubnet;
  if (!hasSubnet) byId(checkboxId).checked = false;
}

function openClientDialog() {
  byId("clientForm").reset();
  byId("clientFormError").textContent = "";
  updateShareControl("clientLanSubnet", "clientShareLan");
  openDialog("clientDialog");
  window.setTimeout(() => byId("clientName").focus(), 50);
}

async function createClient(event) {
  event.preventDefault();
  const button = byId("createClientButton");
  const name = byId("clientName").value.trim();
  const lanSubnet = byId("clientLanSubnet").value.trim();
  byId("clientFormError").textContent = "";
  setBusy(button, true, "正在创建…");
  try {
    await api("/api/clients", {
      method: "POST",
      body: { name, lan_subnet: lanSubnet, share_lan: Boolean(lanSubnet && byId("clientShareLan").checked) },
    });
    closeDialog("clientDialog");
    toast("配置创建成功", lanSubnet ? `${name}.ovpn 与下级内网路由已创建。` : `${name}.ovpn 已可下载。`);
    await loadOverview(true);
    switchView("clients");
  } catch (err) {
    byId("clientFormError").textContent = err.message;
  } finally {
    setBusy(button, false);
  }
}

async function downloadProfile(name) {
  try {
    const blob = await api(`/api/clients/${encodeURIComponent(name)}/profile`);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${name}.ovpn`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("开始下载", `${name}.ovpn 已安全生成。`);
  } catch (err) {
    toast("下载失败", err.message, "error");
  }
}

function renderProfile(profile) {
  state.profile = profile;
  const ikuai = profile.ikuai || {};
  byId("profileTitle").textContent = `${profile.name} 的客户端配置`;
  byId("profileFilename").textContent = profile.filename;
  byId("profileContent").textContent = profile.content;
  byId("ikuaiDialName").textContent = ikuai.dial_name || profile.name;
  byId("ikuaiServer").textContent = ikuai.server || "—";
  byId("ikuaiPort").textContent = ikuai.port || "—";
  byId("ikuaiProtocol").textContent = ikuai.protocol || "—";
  byId("ikuaiLine").textContent = ikuai.line || "自动";
  byId("ikuaiTunnelType").textContent = ikuai.tunnel_type || "TUN";
  byId("ikuaiCipher").textContent = ikuai.cipher || "AES-256-GCM";
  byId("ikuaiCompression").textContent = ikuai.compression || "关闭";
  byId("ikuaiMtu").textContent = ikuai.mtu || "1500";
  byId("ikuaiAuthentication").textContent = ikuai.authentication || "静态密钥（tls-crypt）";
  byId("ikuaiAdditionalConfig").textContent = ikuai.additional_config || "—";
  byId("ikuaiRoutes").textContent = ikuai.routes || "通常留空（由服务器路由推送）";
  byId("ikuaiRoutePush").textContent = ikuai.server_route_push === false ? "关闭" : "启用";
  byId("ikuaiRedial").textContent = `${ikuai.redial ? "启用" : "关闭"} / ${ikuai.line_check || "使用爱快默认值"}`;
  byId("ikuaiCaCertificate").textContent = ikuai.ca_certificate || "—";
  byId("ikuaiClientCertificate").textContent = ikuai.client_certificate || "—";
  byId("ikuaiPrivateKey").textContent = ikuai.private_key || "—";
  byId("ikuaiTlsCryptKey").textContent = ikuai.tls_crypt_key || "—";
}

async function viewProfile(name) {
  state.profile = null;
  byId("profileTitle").textContent = `${name} 的客户端配置`;
  byId("profileFilename").textContent = `${name}.ovpn`;
  byId("profileContent").textContent = "正在读取配置…";
  [
    "ikuaiDialName",
    "ikuaiServer",
    "ikuaiPort",
    "ikuaiProtocol",
    "ikuaiLine",
    "ikuaiTunnelType",
    "ikuaiCipher",
    "ikuaiCompression",
    "ikuaiMtu",
    "ikuaiAuthentication",
    "ikuaiAdditionalConfig",
    "ikuaiRoutes",
    "ikuaiRoutePush",
    "ikuaiRedial",
    "ikuaiCaCertificate",
    "ikuaiClientCertificate",
    "ikuaiPrivateKey",
    "ikuaiTlsCryptKey",
  ].forEach((id) => { byId(id).textContent = "—"; });
  openDialog("profileDialog");
  try {
    const profile = await api(`/api/clients/${encodeURIComponent(name)}/profile?format=json`);
    renderProfile(profile);
  } catch (err) {
    byId("profileContent").textContent = `无法读取配置：${err.message}`;
    toast("读取配置失败", err.message, "error");
  }
}

async function copyText(value, successMessage = "内容已复制到剪贴板。") {
  if (!value) throw new Error("没有可复制的内容");
  try {
    await navigator.clipboard.writeText(String(value));
  } catch (_) {
    const area = document.createElement("textarea");
    area.value = String(value);
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    if (!copied) throw new Error("浏览器拒绝复制");
  }
  toast("复制成功", successMessage);
}

function copyProfileField(field) {
  if (!state.profile) return;
  const value = state.profile.ikuai?.[field];
  if (field === "routes" && !value) {
    toast("当前无需添加路由", "服务器路由推送已覆盖可用路由，请在爱快中保持此项留空。", "success");
    return;
  }
  copyText(value, "爱快参数已复制。" ).catch((err) => toast("复制失败", err.message, "error"));
}

function buildIkuaiChecklist() {
  const ikuai = state.profile?.ikuai || {};
  const lines = [
    `拨号名称：${ikuai.dial_name || ""}`,
    `服务器地址/域名：${ikuai.server || ""}`,
    `服务器端口：${ikuai.port || ""}`,
    "认证方式：静态密钥（tls-crypt）",
    `线路：${ikuai.line || "自动"}`,
    `隧道协议：${ikuai.protocol || ""}`,
    `隧道类型：${ikuai.tunnel_type || "TUN"}`,
    `加密算法：${ikuai.cipher || "AES-256-GCM"}`,
    `LZO 压缩：${ikuai.compression || "关闭"}`,
    `MTU：${ikuai.mtu || "1500"}`,
    "",
    "附加配置：",
    ikuai.additional_config || "",
    "",
    `服务器路由推送：${ikuai.server_route_push === false ? "关闭" : "启用"}`,
    `添加路由：${ikuai.routes || "留空"}`,
    `定时重拨：${ikuai.redial ? "启用" : "关闭"}`,
    `线路检测：${ikuai.line_check || "使用爱快默认值"}`,
    "",
    "证书与密钥请分别复制页面下方的 CA 证书、客户端证书、客户端私钥和 tls-crypt 静态密钥。",
  ];
  return lines.join("\n");
}

function openNetworkDialog(client) {
  byId("networkForm").reset();
  byId("networkClientName").value = client.name;
  byId("networkDialogTitle").textContent = `配置 ${client.name} 的下级内网`;
  byId("networkLanSubnet").value = client.lan_subnet || "";
  byId("networkShareLan").checked = Boolean(client.lan_subnet && client.share_lan);
  byId("networkFormError").textContent = "";
  updateShareControl("networkLanSubnet", "networkShareLan");
  openDialog("networkDialog");
  window.setTimeout(() => byId("networkLanSubnet").focus(), 50);
}

async function saveClientNetwork(event) {
  event.preventDefault();
  const button = byId("saveNetworkButton");
  const name = byId("networkClientName").value;
  const lanSubnet = byId("networkLanSubnet").value.trim();
  byId("networkFormError").textContent = "";
  setBusy(button, true, "正在应用…");
  try {
    await api(`/api/clients/${encodeURIComponent(name)}/network`, {
      method: "POST",
      body: { lan_subnet: lanSubnet, share_lan: Boolean(lanSubnet && byId("networkShareLan").checked) },
    });
    closeDialog("networkDialog");
    toast("客户端路由已更新", lanSubnet ? `${name} 的下级内网已应用。` : `${name} 的下级内网配置已移除。`);
    await loadOverview(true);
  } catch (err) {
    byId("networkFormError").textContent = err.message;
  } finally {
    setBusy(button, false);
  }
}

function showConfirm({ title, message, label, danger = true, action }) {
  byId("confirmTitle").textContent = title;
  byId("confirmMessage").textContent = message;
  const button = byId("confirmAction");
  button.textContent = label;
  button.className = `button ${danger ? "button-danger" : "button-primary"}`;
  byId("confirmIcon").className = `modal-icon ${danger ? "danger" : ""}`;
  state.confirmAction = action;
  openDialog("confirmDialog");
}

function confirmDisconnect(name) {
  showConfirm({
    title: `踢出 ${name}？`,
    message: "只会立即断开当前在线会话，不会吊销证书。客户端仍可使用原配置重新连接。",
    label: "踢出连接",
    danger: false,
    action: async () => {
      await api(`/api/clients/${encodeURIComponent(name)}/disconnect`, { method: "POST" });
      toast("客户端已踢出", `${name} 的当前连接已断开。`);
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      await loadOverview(true);
    },
  });
}

function confirmRevoke(name) {
  showConfirm({
    title: `确认吊销 ${name}？`,
    message: "该配置会立即失效。OpenVPN 将短暂重启，并且此证书名称以后不能再次使用。",
    label: "吊销配置",
    action: async () => {
      await api(`/api/clients/${encodeURIComponent(name)}`, { method: "DELETE" });
      toast("配置已吊销", `${name} 已无法继续连接。`);
      await loadOverview(true);
    },
  });
}

function confirmRestart() {
  showConfirm({
    title: "确认重启 OpenVPN？",
    message: "服务重启期间，已连接客户端可能会中断数秒。",
    label: "重启服务",
    danger: false,
    action: async () => {
      await api("/api/server/restart", { method: "POST" });
      toast("服务已重启", "OpenVPN 已恢复正常运行。" );
      await loadOverview(true);
    },
  });
}

function submitServerSettings(event) {
  event.preventDefault();
  const dnsServers = [byId("serverDns1").value.trim(), byId("serverDns2").value.trim()].filter(Boolean);
  const payload = {
    endpoint: byId("serverEndpoint").value.trim(),
    vpn_port: Number(byId("serverVpnPort").value),
    vpn_protocol: byId("serverProtocol").value,
    vpn_subnet: byId("serverSubnet").value.trim(),
    dns_servers: dnsServers,
    redirect_gateway: byId("serverRedirectGateway").checked,
    max_clients: Number(byId("serverMaxClients").value),
    web_allow: byId("serverWebAllow").value.trim(),
    tun_mtu: Number(byId("serverTunMtu").value),
    mssfix: Number(byId("serverMssfix").value),
    keepalive_ping: Number(byId("serverKeepalivePing").value),
    keepalive_timeout: Number(byId("serverKeepaliveTimeout").value),
    data_cipher: byId("serverDataCipher").value,
    auth_digest: byId("serverAuthDigest").value,
    log_verb: Number(byId("serverLogVerb").value),
    push_routes: byId("serverPushRoutes").value.split(/[,\n\s]+/).map((item) => item.trim()).filter(Boolean),
  };
  byId("serverFormError").textContent = "";
  showConfirm({
    title: "应用新的服务端设置？",
    message: `OpenVPN 将切换到 ${payload.endpoint}:${payload.vpn_port}/${payload.vpn_protocol.toUpperCase()}，全部有效客户端配置会重新生成，当前连接会短暂中断。`,
    label: "保存并应用",
    danger: false,
    action: async () => {
      await api("/api/server/settings", { method: "POST", body: payload });
      state.serverDirty = false;
      toast("服务端设置已应用", "OpenVPN、客户端配置、路由和防火墙已同步更新。" );
      await loadOverview(true);
    },
  });
}

async function runConfirmedAction() {
  const button = byId("confirmAction");
  setBusy(button, true, "处理中…");
  try {
    if (state.confirmAction) await state.confirmAction();
    closeDialog("confirmDialog");
  } catch (err) {
    closeDialog("confirmDialog");
    toast("操作失败", err.message, "error");
  } finally {
    setBusy(button, false);
    state.confirmAction = null;
  }
}

async function loadLogs() {
  const output = byId("logOutput");
  const button = byId("loadLogsButton");
  setBusy(button, true, "正在加载…");
  output.textContent = "正在加载最近的服务日志…";
  try {
    const logs = await api("/api/logs?lines=160");
    output.textContent = (logs.lines || []).join("\n") || "没有最近的 OpenVPN 日志。";
    output.scrollTop = output.scrollHeight;
  } catch (err) {
    output.textContent = `无法加载日志：${err.message}`;
  } finally {
    setBusy(button, false);
  }
}

function renderUpdateStatus(status) {
  const box = byId("updateStatusBox");
  const rawState = status.state || "idle";
  const active = rawState === "queued" || rawState === "running";
  const displayState = active ? "running" : rawState;
  const labels = {
    idle: "尚未更新",
    queued: "等待执行",
    running: "正在更新",
    completed: "更新完成",
    failed: "更新失败",
  };
  box.className = `update-status ${displayState}`;
  byId("updateStatusTitle").textContent = labels[rawState] || "更新状态";
  byId("updateStatusMessage").textContent = status.message || "暂无更新记录。";
  if (status.manager_version) byId("managerVersion").textContent = status.manager_version;
  if (status.openvpn_version) byId("updateOpenvpnVersion").textContent = String(status.openvpn_version).replace(/^OpenVPN\s+/i, "");
  const button = byId("updateButton");
  button.disabled = active;
  button.innerHTML = active ? `${icon("refresh")}<span>正在更新…</span>` : `${icon("cloud")}<span>更新面板与 OpenVPN</span>`;
  if (active && !state.updateTimer) {
    state.updateTimer = window.setInterval(() => loadUpdateStatus(true), 3500);
  }
  if (!active && state.updateTimer) {
    window.clearInterval(state.updateTimer);
    state.updateTimer = null;
  }
}

async function loadUpdateStatus(quiet = false) {
  try {
    renderUpdateStatus(await api("/api/update"));
  } catch (err) {
    if (!quiet) toast("读取更新状态失败", err.message, "error");
  }
}

function confirmUpdate() {
  showConfirm({
    title: "更新管理面板与 OpenVPN？",
    message: "系统将从 GitHub 最新稳定版本下载安装包，备份现有配置，并通过软件源升级 OpenVPN。现有 VPN 端口和服务端参数不会改变；页面可能短暂断开，请勿重复点击。",
    label: "开始更新",
    danger: false,
    action: async () => {
      const result = await api("/api/update", { method: "POST" });
      renderUpdateStatus(result);
      toast("更新任务已启动", "本次更新只升级软件并保留现有 VPN 端口和服务端参数。" );
      switchView("system");
    },
  });
}

async function changePassword(event) {
  event.preventDefault();
  const current = byId("currentPassword").value;
  const next = byId("newPassword").value;
  const confirmation = byId("confirmPassword").value;
  const error = byId("passwordFormError");
  error.textContent = "";
  if (next !== confirmation) {
    error.textContent = "两次输入的新密码不一致。";
    return;
  }
  const button = byId("changePasswordButton");
  setBusy(button, true, "正在更新…");
  try {
    await api("/api/password", { method: "POST", body: { current_password: current, new_password: next } });
    closeDialog("passwordDialog");
    byId("passwordForm").reset();
    toast("密码已更新", "控制台登录密码已成功修改。" );
  } catch (err) {
    error.textContent = err.message;
  } finally {
    setBusy(button, false);
  }
}

function openPasswordDialog() {
  byId("passwordForm").reset();
  byId("passwordUsername").value = state.user;
  byId("passwordFormError").textContent = "";
  openDialog("passwordDialog");
  window.setTimeout(() => byId("currentPassword").focus(), 50);
}

function copyEndpoint() {
  copyText(byId("heroEndpoint").textContent, "VPN 地址已复制到剪贴板。" ).catch((err) => toast("复制失败", err.message, "error"));
}

function openSidebar() {
  byId("sidebar").classList.add("open");
  byId("sidebarBackdrop").classList.remove("hidden");
}

function closeSidebar() {
  byId("sidebar").classList.remove("open");
  byId("sidebarBackdrop").classList.add("hidden");
}

function bindEvents() {
  byId("loginForm").addEventListener("submit", login);
  byId("logoutButton").addEventListener("click", logout);
  byId("themeButton").addEventListener("click", toggleTheme);
  byId("loginThemeButton").addEventListener("click", toggleTheme);
  byId("refreshButton").addEventListener("click", () => loadOverview());
  byId("createClientTop").addEventListener("click", openClientDialog);
  byId("createClientPanel").addEventListener("click", openClientDialog);
  byId("clientForm").addEventListener("submit", createClient);
  byId("clientLanSubnet").addEventListener("input", () => updateShareControl("clientLanSubnet", "clientShareLan"));
  byId("networkForm").addEventListener("submit", saveClientNetwork);
  byId("networkLanSubnet").addEventListener("input", () => updateShareControl("networkLanSubnet", "networkShareLan"));
  byId("serverSettingsForm").addEventListener("submit", submitServerSettings);
  byId("serverSettingsForm").addEventListener("input", () => { state.serverDirty = true; });
  byId("passwordButton").addEventListener("click", openPasswordDialog);
  byId("passwordForm").addEventListener("submit", changePassword);
  byId("restartButton").addEventListener("click", confirmRestart);
  byId("loadLogsButton").addEventListener("click", loadLogs);
  byId("updateButton").addEventListener("click", confirmUpdate);
  byId("copyEndpoint").addEventListener("click", copyEndpoint);
  byId("copyProfileButton").addEventListener("click", () => {
    copyText(state.profile?.content, "完整 .ovpn 配置已复制。" ).catch((err) => toast("复制失败", err.message, "error"));
  });
  byId("downloadProfileButton").addEventListener("click", () => {
    if (state.profile?.name) downloadProfile(state.profile.name);
  });
  byId("copyIkuaiChecklistButton").addEventListener("click", () => {
    copyText(buildIkuaiChecklist(), "完整爱快填写清单已复制。" ).catch((err) => toast("复制失败", err.message, "error"));
  });
  document.querySelectorAll("[data-profile-field]").forEach((button) => button.addEventListener("click", () => copyProfileField(button.dataset.profileField)));
  byId("confirmCancel").addEventListener("click", () => closeDialog("confirmDialog"));
  byId("confirmAction").addEventListener("click", runConfirmedAction);
  byId("menuButton").addEventListener("click", openSidebar);
  byId("sidebarBackdrop").addEventListener("click", closeSidebar);
  byId("clientSearch").addEventListener("input", (event) => {
    state.search = event.target.value.trim();
    if (state.overview) renderClientTable(state.overview.clients || []);
  });
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  document.querySelectorAll("[data-jump]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.jump)));
  document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => closeDialog(button.dataset.closeDialog)));
  document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
  }));
}

document.addEventListener("DOMContentLoaded", () => {
  const saved = localStorage.getItem("ovpn-theme");
  const preferred = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  applyTheme(saved || preferred);
  bindEvents();
  restoreSession();
});
