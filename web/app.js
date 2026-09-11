"use strict";

const state = {
  csrf: "",
  user: "",
  overview: null,
  view: "overview",
  search: "",
  timer: null,
  confirmAction: null,
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
    button.dataset.original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `${icon("refresh")}<span>${label || "处理中…"}</span>`;
  } else {
    button.disabled = false;
    if (button.dataset.original) button.innerHTML = button.dataset.original;
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

function showLogin() {
  window.clearInterval(state.timer);
  state.timer = null;
  state.csrf = "";
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
    system: ["运行维护", "系统"],
  };
  document.querySelectorAll(".view-panel").forEach((panel) => panel.classList.add("hidden"));
  byId(`${view}Panel`).classList.remove("hidden");
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  byId("pageEyebrow").textContent = titles[view][0];
  byId("pageTitle").textContent = titles[view][1];
  closeSidebar();
  if (view === "system") loadLogs();
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
  renderRecent(clients);
  renderClientTable(clients);
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
    cell.colSpan = 6;
    cell.innerHTML = `<div class="empty-state">${needle ? "没有匹配的客户端。" : "尚未创建客户端配置。"}</div>`;
    row.append(cell);
    root.append(row);
    return;
  }
  filtered.forEach((client) => {
    const row = document.createElement("tr");
    const connection = client.connection || {};
    row.innerHTML = `
      <td><div class="client-cell"><div class="client-symbol">${icon("key")}</div><div><strong></strong><span></span></div></div></td>
      <td><div class="state-cell"><span class="state-label"><i></i><span></span></span><span class="cell-subtle"></span></div></td>
      <td><strong class="vpn-address"></strong><div class="cell-subtle real-address"></div></td>
      <td><div class="traffic-cell"><span class="received"></span><span class="sent"></span></div></td>
      <td><strong class="expiry"></strong><div class="cell-subtle serial"></div></td>
      <td><div class="row-actions"><button class="row-button download" title="下载配置" aria-label="下载配置">${icon("download")}</button><button class="row-button danger revoke" title="吊销配置" aria-label="吊销配置">${icon("trash")}</button></div></td>`;
    row.querySelector(".client-cell strong").textContent = client.name;
    row.querySelector(".client-cell span").textContent = `创建于 ${formatDate(client.created_at)}`;
    const label = row.querySelector(".state-label");
    label.classList.add(statusClass(client));
    label.querySelector("span").textContent = statusLabel(client);
    row.querySelector(".state-cell .cell-subtle").textContent = connection.connected_since || client.status;
    row.querySelector(".vpn-address").textContent = connection.virtual_address || "—";
    row.querySelector(".real-address").textContent = connection.real_address || "没有活动隧道";
    row.querySelector(".received").textContent = `↓ ${formatBytes(connection.bytes_received)}`;
    row.querySelector(".sent").textContent = `↑ ${formatBytes(connection.bytes_sent)}`;
    row.querySelector(".expiry").textContent = formatDate(client.expires_at);
    row.querySelector(".serial").textContent = `序列号 ${client.serial || "—"}`;
    const download = row.querySelector(".download");
    const revoke = row.querySelector(".revoke");
    const active = client.status === "active";
    download.disabled = !active || !client.has_profile;
    revoke.disabled = !active;
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

function openClientDialog() {
  byId("clientForm").reset();
  byId("clientFormError").textContent = "";
  openDialog("clientDialog");
  window.setTimeout(() => byId("clientName").focus(), 50);
}

async function createClient(event) {
  event.preventDefault();
  const button = byId("createClientButton");
  const name = byId("clientName").value.trim();
  byId("clientFormError").textContent = "";
  setBusy(button, true, "正在创建…");
  try {
    await api("/api/clients", { method: "POST", body: { name } });
    closeDialog("clientDialog");
    toast("配置创建成功", `${name}.ovpn 已可下载。`);
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

function confirmRevoke(name) {
  showConfirm({
    title: `确认吊销 ${name}？`,
    message: "该配置会立即失效。OpenVPN 将短暂重启以断开活动会话，并且此证书名称以后不能再次使用。",
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
  byId("passwordFormError").textContent = "";
  openDialog("passwordDialog");
  window.setTimeout(() => byId("currentPassword").focus(), 50);
}

function copyEndpoint() {
  const value = byId("heroEndpoint").textContent;
  navigator.clipboard.writeText(value).then(() => toast("复制成功", "VPN 地址已复制到剪贴板。" )).catch(() => toast("复制失败", "请手动选择并复制 VPN 地址。", "error"));
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
  byId("passwordButton").addEventListener("click", openPasswordDialog);
  byId("passwordForm").addEventListener("submit", changePassword);
  byId("restartButton").addEventListener("click", confirmRestart);
  byId("loadLogsButton").addEventListener("click", loadLogs);
  byId("copyEndpoint").addEventListener("click", copyEndpoint);
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
