const state = { authenticated: false, status: null, roots: [] };

const $ = (selector) => document.querySelector(selector);

function setMessage(selector, text, type = "") {
  const element = $(selector);
  element.textContent = text || "";
  element.hidden = !text;
  element.className = `form-message ${type ? `is-${type}` : ""}`;
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = null; }
  if (!response.ok) {
    const detail = payload?.detail || `请求失败（${response.status}）`;
    throw new Error(detail);
  }
  return payload;
}

function setSignal(title, detail, tone = "") {
  $("#signal-title").textContent = title;
  $("#signal-detail").textContent = detail;
  const pill = $("#connection-pill");
  pill.textContent = title;
  pill.className = `pill ${tone ? `pill-${tone}` : "pill-muted"}`;
}

function formatDate(value) {
  if (!value) return "尚未运行";
  try { return new Date(value).toLocaleString("zh-CN", { hour12: false }); } catch (_) { return value; }
}

function renderStatus() {
  const status = state.status;
  if (!status) return;
  $("#api-version").textContent = `API ${status.api_version}`;
  $("#reconcile-time").textContent = status.reconcile_time;
  $("#schedule-timezone").textContent = status.sync_timezone;
  const configured = status.notion_configured;
  const workspaceInitialized = Boolean(status.workspace_initialized);
  const ready = configured && workspaceInitialized;
  $("#credential-state").textContent = configured ? `已配置 · ${status.notion_token_source}` : "未配置";
  $("#credential-state").className = `status-dot ${configured ? "status-on" : "status-off"}`;
  $("#root-count").textContent = workspaceInitialized ? "全工作区已启用" : "尚未初始化";
  if (ready) setSignal("已就绪", "定时同步与本地归档已具备运行条件。", "on");
  else if (configured) setSignal("准备全量初始化", "Token 已保存，点击“初始化全工作区”开始发现全部可见内容。", "warn");
  else setSignal("等待 Notion 授权", "先保存一个只读 Internal Connection Token。", "warn");
}

function renderRoots() {
  const list = $("#root-list");
  if (!state.roots.length) {
    list.innerHTML = '<div class="empty-state">还没有初始化全工作区。初始化后会自动发现当前连接可见的页面、数据库和数据源。</div>';
    return;
  }
  list.innerHTML = state.roots.map((root) => {
    const disabled = root.status === "disabled";
    const workspace = root.root_object_id === "__workspace__";
    return `<article class="root-item">
      <div>
        <strong>${workspace ? "全工作区（自动发现）" : escapeHtml(root.name)}</strong>
        <small>${workspace ? "Notion Search · 所有当前可见对象" : escapeHtml(root.root_object_id)}</small>
        <div class="root-item-meta"><span>${disabled ? "已停用" : escapeHtml(root.status)}</span><span>·</span><span>上次同步：${escapeHtml(formatDate(root.last_sync_at))}</span></div>
      </div>
      <div class="root-item-actions">
        ${disabled ? "" : `<button class="mini-button" data-action="sync" data-root="${escapeHtml(root.id)}">立即同步</button><button class="mini-button danger" data-action="disable" data-root="${escapeHtml(root.id)}">停用</button>`}
      </div>
    </article>`;
  }).join("");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

async function loadConsole() {
  state.status = await request("/api/v1/setup/status");
  state.roots = await request("/api/v1/sync/roots");
  renderStatus();
  renderRoots();
}

function showConsole() {
  state.authenticated = true;
  $("#login-panel").hidden = true;
  $("#console-panel").hidden = false;
}

async function tryExistingSession() {
  try {
    const result = await request("/api/v1/admin/session");
    if (result.authenticated) {
      showConsole();
      await loadConsole();
    }
  } catch (_) { /* The login panel is the intended unauthenticated state. */ }
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("#login-error", "");
  const token = $("#setup-token").value;
  try {
    await request("/api/v1/admin/session", { method: "POST", body: JSON.stringify({ setup_token: token }) });
    $("#setup-token").value = "";
    showConsole();
    await loadConsole();
  } catch (error) {
    setMessage("#login-error", error.message || "无法建立管理会话", "error");
  }
});

$("#logout-button").addEventListener("click", async () => {
  await request("/api/v1/admin/session", { method: "DELETE" });
  window.location.reload();
});

$("#token-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("#token-message", "");
  const tokenInput = $("#notion-token");
  if (!tokenInput.value.trim()) {
    setMessage("#token-message", "请输入 Notion Token。", "error");
    return;
  }
  try {
    await request("/api/v1/admin/notion-token", { method: "PUT", body: JSON.stringify({ token: tokenInput.value }) });
    tokenInput.value = "";
    await loadConsole();
    setMessage("#token-message", "Token 已保存到本地运行时 secret 文件。", "success");
  } catch (error) {
    setMessage("#token-message", error.message, "error");
  }
});

$("#test-token-button").addEventListener("click", async () => {
  setMessage("#token-message", "正在连接 Notion…");
  try {
    await request("/api/v1/admin/notion/test", { method: "POST" });
    setMessage("#token-message", "连接成功。下一步可以初始化全工作区。", "success");
    await loadConsole();
  } catch (error) {
    setMessage("#token-message", error.message, "error");
  }
});

$("#clear-token-button").addEventListener("click", async () => {
  if (!window.confirm("移除本地运行时 Token？已有归档不会被删除。")) return;
  try {
    await request("/api/v1/admin/notion-token", { method: "DELETE" });
    await loadConsole();
    setMessage("#token-message", "本地运行时 Token 已移除。", "success");
  } catch (error) {
    setMessage("#token-message", error.message, "error");
  }
});

$("#initialize-workspace-button").addEventListener("click", async () => {
  setMessage("#root-message", "");
  if (!window.confirm("将初始化当前连接可见的全部 Notion 内容，首次同步可能需要较长时间。继续吗？")) return;
  try {
    await request("/api/v1/sync/workspace", { method: "POST" });
    await loadConsole();
    setMessage("#root-message", "全工作区初始化任务已加入队列。", "success");
  } catch (error) {
    setMessage("#root-message", error.message, "error");
  }
});

$("#root-list").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const rootId = button.dataset.root;
  try {
    if (button.dataset.action === "disable") {
      if (!window.confirm("停用这个同步范围？本地数据和快照会保留。")) return;
      await request(`/api/v1/sync/roots/${rootId}`, { method: "DELETE" });
    } else {
      await request(`/api/v1/sync/roots/${rootId}/runs`, { method: "POST" });
    }
    await loadConsole();
    setMessage("#console-message", button.dataset.action === "disable" ? "同步范围已停用。" : "同步任务已加入队列。", "success");
  } catch (error) {
    setMessage("#console-message", error.message, "error");
  }
});

document.querySelectorAll("[data-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.toggle);
    const visible = input.type === "text";
    input.type = visible ? "password" : "text";
    button.textContent = visible ? "显示" : "隐藏";
  });
});

tryExistingSession();
