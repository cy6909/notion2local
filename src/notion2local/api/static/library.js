const state = {
  rootId: null,
  pages: [],
  offset: 0,
  pageSize: 100,
  selectedId: null,
  loadingPages: false,
};

const $ = (selector) => document.querySelector(selector);

async function request(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = null; }
  if (response.status === 401 || response.status === 403) {
    const error = new Error("登录后才能读取本地笔记。");
    error.auth = true;
    throw error;
  }
  if (!response.ok) throw new Error(payload?.detail || `读取失败（${response.status}）`);
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
}

function safeHref(value) {
  try {
    const url = new URL(String(value), window.location.origin);
    if (["http:", "https:", "mailto:"].includes(url.protocol)) return url.href;
  } catch (_) { /* malformed or unsupported links stay plain text */ }
  return "";
}

function formatDate(value) {
  if (!value) return "尚未记录";
  try { return new Date(value).toLocaleString("zh-CN", { hour12: false }); } catch (_) { return value; }
}

function richTextToHtml(value) {
  if (!Array.isArray(value)) return escapeHtml(value || "");
  return value.map((item) => {
    if (typeof item === "string") return escapeHtml(item);
    if (!item || typeof item !== "object") return "";
    const text = item.plain_text ?? item.text?.content ?? item.equation?.expression ?? "";
    if (!text) return "";
    let html = escapeHtml(text);
    const annotations = item.annotations || {};
    if (annotations.code) html = `<code>${html}</code>`;
    if (annotations.bold) html = `<strong>${html}</strong>`;
    if (annotations.italic) html = `<em>${html}</em>`;
    if (annotations.underline) html = `<u>${html}</u>`;
    if (annotations.strikethrough) html = `<s>${html}</s>`;
    const href = safeHref(item.href || item.text?.link?.url);
    if (href) html = `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${html}</a>`;
    return html;
  }).join("");
}

function blockValue(payload) {
  const type = payload?.type;
  const value = payload?.[type];
  return value && typeof value === "object" ? value : {};
}

function blockRichText(payload) {
  const value = blockValue(payload);
  return richTextToHtml(value.rich_text || value.title || value.caption || []);
}

function blockHtml(block) {
  const payload = block.current_payload || {};
  const type = payload.type || block.object_type || "unknown";
  const value = blockValue(payload);
  const text = blockRichText(payload);
  const depth = Math.max(0, Number(block.depth || 0) - 1);
  const style = ` style="--depth:${depth}"`;
  if (type === "heading_1") return `<div class="article-block"${style}><h3>${text}</h3></div>`;
  if (type === "heading_2" || type === "heading_3") return `<div class="article-block"${style}><h4>${text}</h4></div>`;
  if (type === "bulleted_list_item") return `<div class="article-block"${style}><ul><li>${text}</li></ul></div>`;
  if (type === "numbered_list_item") return `<div class="article-block"${style}><ol><li>${text}</li></ol></div>`;
  if (type === "to_do") return `<div class="article-block"${style}><div class="todo-line"><span class="todo-box ${value.checked ? "is-done" : ""}"></span><span>${text}</span></div></div>`;
  if (type === "quote") return `<div class="article-block"${style}><blockquote>${text}</blockquote></div>`;
  if (type === "callout") return `<div class="article-block"${style}><div class="callout"><span class="callout-icon">${escapeHtml(value.icon?.emoji || "⌁")}</span><span>${text}</span></div></div>`;
  if (type === "code") return `<div class="article-block"${style}><pre><span class="code-label">${escapeHtml(value.language || "code")}</span>${escapeHtml(value.rich_text?.map((item) => item.plain_text || item.text?.content || "").join("") || "")}</pre></div>`;
  if (type === "divider") return `<div class="article-block"${style}><hr></div>`;
  if (["image", "video", "file", "pdf", "audio", "bookmark", "embed", "link_preview"].includes(type)) {
    return `<div class="article-block"${style}><div class="media-card"><strong>${escapeHtml(type)}</strong>${text || "本地已保存该媒体的 Notion 元数据；二进制阅读投影尚未接入。"}</div></div>`;
  }
  if (type === "child_page" || type === "child_database") {
    return `<div class="article-block"${style}><div class="child-card"><strong>${escapeHtml(type === "child_page" ? "子页面" : "子数据库")}</strong>${text || escapeHtml(block.title || "未命名")}</div></div>`;
  }
  if (type === "table_row") {
    const cells = Array.isArray(value.cells) ? value.cells.map((cell) => `<td>${richTextToHtml(cell)}</td>`).join("") : "";
    return `<div class="article-block"${style}><table><tbody><tr>${cells}</tr></tbody></table></div>`;
  }
  if (type === "table_of_contents" || type === "breadcrumb" || type === "column_list" || type === "column") {
    return `<div class="article-block"${style}><div class="media-card"><strong>${escapeHtml(type)}</strong>结构块已归档。</div></div>`;
  }
  if (text) return `<div class="article-block"${style}><p>${text}</p></div>`;
  return `<div class="article-block"${style}><div class="media-card"><strong>${escapeHtml(type)}</strong>该块已归档，当前阅读器暂不展开其专用渲染。</div></div>`;
}

function propertyText(property) {
  if (!property || !property.type) return "";
  const value = property[property.type];
  if (property.type === "title" || property.type === "rich_text") return (value || []).map((item) => item.plain_text || item.text?.content || "").join("");
  if (property.type === "select" || property.type === "status") return value?.name || "";
  if (property.type === "multi_select") return (value || []).map((item) => item.name).join(" · ");
  if (property.type === "checkbox") return value ? "已完成" : "未完成";
  if (property.type === "url") return value || "";
  if (property.type === "number") return value == null ? "" : String(value);
  if (property.type === "date") return value?.start || "";
  if (property.type === "people") return (value || []).map((item) => item.name || item.id).join(" · ");
  if (property.type === "relation") return `${(value || []).length} 个关联页面`;
  return value == null ? "" : String(value);
}

function renderPages() {
  const query = $("#page-search").value.trim().toLowerCase();
  const visible = state.pages.filter((page) => !query || (page.title || "未命名页面").toLowerCase().includes(query));
  $("#list-count").textContent = `${visible.length}${query ? ` / ${state.pages.length}` : ""}`;
  $("#page-list").innerHTML = visible.map((page) => {
    const trashed = page.sync_state === "trashed" || page.sync_state === "missing";
    return `<button class="page-card ${page.object_id === state.selectedId ? "is-selected" : ""}" data-page-id="${escapeHtml(page.object_id)}" role="listitem">
      <span class="page-card-title">${escapeHtml(page.title || "未命名页面")}</span>
      <span class="page-card-meta"><span>${escapeHtml(formatDate(page.updated_at))}</span><span class="page-card-state ${trashed ? "is-trashed" : ""}">${trashed ? "已归档" : "本地"}</span></span>
    </button>`;
  }).join("");
  $("#load-more").hidden = Boolean(query) || state.pages.length < state.offset;
}

async function loadPages(reset = false) {
  if (state.loadingPages || !state.rootId) return;
  state.loadingPages = true;
  if (reset) { state.pages = []; state.offset = 0; }
  try {
    const pages = await request(`/api/v1/library?root_id=${encodeURIComponent(state.rootId)}&object_type=page&limit=${state.pageSize}&offset=${state.offset}`);
    state.pages.push(...pages);
    state.offset += pages.length;
    renderPages();
    if (!state.selectedId && state.pages[0]) selectPage(state.pages[0].object_id);
  } catch (error) {
    if (error.auth) showGate();
    else $("#reader-message").textContent = error.message;
  } finally {
    state.loadingPages = false;
  }
}

function showGate() {
  $("#reader-gate").hidden = false;
  $("#empty-note").hidden = true;
  $("#note-article").hidden = true;
}

async function selectPage(pageId) {
  state.selectedId = pageId;
  renderPages();
  $("#empty-note").hidden = true;
  $("#note-article").hidden = true;
  $("#reader-message").textContent = "正在读取本地正文…";
  try {
    const result = await request(`/api/v1/library/pages/${encodeURIComponent(pageId)}/content?depth=4&limit=600`);
    renderNote(result);
    history.replaceState(null, "", `#page=${encodeURIComponent(pageId)}`);
    $("#reader-message").textContent = "";
  } catch (error) {
    if (error.auth) showGate();
    else $("#reader-message").textContent = error.message;
  }
}

function renderNote(result) {
  const page = result.page;
  const payload = page.current_payload || {};
  const title = page.title || "未命名页面";
  $("#article-title").textContent = title;
  $("#article-kind").textContent = page.sync_state === "trashed" ? "ARCHIVED PAGE" : "PAGE";
  $("#article-state").textContent = `${page.sync_state.toUpperCase()} · ${result.blocks.length} BLOCKS`;
  const sourceUrl = safeHref(page.source_url);
  $("#article-meta").innerHTML = `<span>最后编辑：${escapeHtml(formatDate(page.last_seen_at))}</span><span>本地更新：${escapeHtml(formatDate(page.updated_at))}</span>${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">打开 Notion 原页 ↗</a>` : ""}`;
  const properties = Object.entries(payload.properties || {}).filter(([name]) => name !== "title").map(([name, property]) => {
    const value = propertyText(property);
    return value ? `<span class="property-chip"><strong>${escapeHtml(name)}</strong> · ${escapeHtml(value)}</span>` : "";
  }).filter(Boolean);
  $("#article-properties").innerHTML = properties.join("");
  $("#article-body").innerHTML = `${result.truncated ? `<div class="truncated-note">正文树较大，当前先展示前 ${result.blocks.length} 个块；原始 JSON 仍完整保存在本地归档。</div>` : ""}${result.blocks.length ? result.blocks.map(blockHtml).join("") : `<p class="muted-copy">这个页面目前没有已归档的子块，或同步尚未提交到该页。</p>`}`;
  $("#article-foot").innerHTML = `<strong>本地归档</strong> · ${escapeHtml(page.record_key)} · hash ${escapeHtml((page.current_hash || "").slice(0, 12))}…`;
  $("#note-article").hidden = false;
}

async function refreshStats() {
  try {
    const stats = await request("/api/v1/library/stats");
    state.rootId = stats.root_id;
    const run = stats.run;
    const progress = run?.status === "running" && run.stats_json?.discovered
      ? `同步中 · ${run.stats_json.seen || 0}/${run.stats_json.discovered}`
      : run?.status === "succeeded" ? `已完成 · ${run.stats_json?.changed || 0} 处变更` : run?.status === "partial" ? "部分完成" : run?.status === "failed" ? "最近一次失败" : "等待初始化";
    $("#sync-state").textContent = progress;
    $("#sync-state").className = `status-pill ${run?.status === "running" ? "status-warn" : run?.status === "succeeded" ? "status-on" : "status-muted"}`;
    $("#archive-count").textContent = `${stats.total || 0} 个对象`;
    if (!state.pages.length && state.rootId) await loadPages(true);
  } catch (error) {
    if (error.auth) showGate();
    else $("#reader-message").textContent = error.message;
  }
}

$("#page-search").addEventListener("input", renderPages);
$("#page-list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-page-id]");
  if (button) selectPage(button.dataset.pageId);
});
$("#load-more").addEventListener("click", () => loadPages(false));

refreshStats();
window.setInterval(refreshStats, 5000);
