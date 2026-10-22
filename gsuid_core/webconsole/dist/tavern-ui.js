/*
 * 酒馆模式设置浮层（M0）
 *
 * 为什么是独立脚本：当前 WebConsole 的 React bundle 是预编译产物，人格配置页把
 * ai_mode/scope/keywords/tool_packs 等字段写死在 bundle 里，没有 prompt_profile
 * 的入口。这里用 Shadow DOM 加一个独立面板，不改 React bundle；服务端返回
 * index.html 时注入本脚本（见 webconsole/setup_frontend.py）。
 */
(() => {
  const TOKEN_KEY = "auth_token";
  const PERSONA_KEY = "tavern_ui_persona";
  const API_BASE = (localStorage.getItem("custom_api_host") || "").replace(/\/$/, "");

  const api = async (path, options = {}) => {
    const token = localStorage.getItem(TOKEN_KEY);
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: "include" });
    if (res.status === 401) throw new Error("登录已失效，请刷新页面重新登录");
    let body;
    try {
      body = await res.json();
    } catch {
      throw new Error(`HTTP ${res.status}`);
    }
    if (body.status !== 0) throw new Error(body.msg || `HTTP ${res.status}`);
    return body.data;
  };

  const host = document.createElement("div");
  host.id = "tavern-ui-host";
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>
      :host { all: initial; }
      * { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
      #fab {
        position: fixed; right: 24px; bottom: 24px; z-index: 2147483000;
        border: 1px solid rgba(148,163,184,.45); border-radius: 999px;
        background: #0f172a; color: #e2e8f0; padding: 10px 16px;
        font-size: 13px; cursor: pointer; box-shadow: 0 10px 30px rgba(0,0,0,.25);
      }
      #fab:hover { background: #1e293b; }
      #overlay {
        position: fixed; inset: 0; z-index: 2147483001; display: flex;
        align-items: center; justify-content: center; padding: 24px;
        background: rgba(15,23,42,.55); backdrop-filter: blur(2px);
      }
      #overlay[hidden] { display: none; }
      .panel {
        width: min(760px, 100%); max-height: 88vh; overflow: auto;
        background: #ffffff; color: #0f172a; border-radius: 14px;
        box-shadow: 0 24px 80px rgba(0,0,0,.35); padding: 20px 22px;
      }
      .head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 4px; }
      .head h2 { margin: 0; font-size: 18px; }
      .badge { font-size: 11px; color: #475569; border: 1px solid #cbd5e1; border-radius: 999px; padding: 2px 8px; }
      .hint { color: #64748b; font-size: 12px; line-height: 1.6; margin: 6px 0 14px; }
      .row { display: flex; gap: 12px; flex-wrap: wrap; }
      .field { flex: 1 1 240px; margin-bottom: 12px; }
      label { display: block; font-size: 12px; font-weight: 600; color: #334155; margin-bottom: 6px; }
      select, textarea {
        width: 100%; border: 1px solid #cbd5e1; border-radius: 8px;
        background: #f8fafc; color: #0f172a; padding: 8px 10px; font-size: 13px;
      }
      select:focus, textarea:focus { outline: 2px solid #93c5fd; border-color: #60a5fa; }
      textarea { min-height: 300px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; line-height: 1.5; }
      .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
      button.act {
        border: 1px solid #cbd5e1; border-radius: 8px; background: #ffffff;
        color: #0f172a; padding: 8px 14px; font-size: 13px; cursor: pointer;
      }
      button.act:hover { background: #f1f5f9; }
      button.primary { background: #2563eb; border-color: #2563eb; color: #ffffff; }
      button.primary:hover { background: #1d4ed8; }
      button.danger { color: #b91c1c; border-color: #fecaca; }
      #status { min-height: 20px; margin-top: 10px; font-size: 12px; color: #475569; white-space: pre-wrap; }
      #status.ok { color: #15803d; }
      #status.err { color: #b91c1c; }
    </style>
    <button id="fab" type="button">酒馆设置</button>
    <div id="overlay" hidden>
      <div class="panel" role="dialog" aria-modal="true" aria-label="酒馆模式设置">
        <div class="head"><h2>酒馆模式设置</h2><span class="badge">M0</span></div>
        <p class="hint">
          酒馆模式按人格切换。私聊走 tavern（原生示例 + PHI + 角色化输出），群聊默认回退 gscore；
          这里保存后会写入 <code>persona/&lt;name&gt;/tavern.json</code>，下一次消息自动重建 session。
        </p>
        <div class="row">
          <div class="field">
            <label for="persona">人格</label>
            <select id="persona"></select>
          </div>
          <div class="field">
            <label for="mode">对话模式</label>
            <select id="mode">
              <option value="gscore">gscore（群聊 Agent，现状）</option>
              <option value="tavern">tavern（酒馆角色，私聊优先）</option>
            </select>
          </div>
        </div>
        <div class="field">
          <label for="json">tavern.json</label>
          <textarea id="json" spellcheck="false" placeholder="{ }"></textarea>
        </div>
        <div class="actions">
          <button class="act primary" id="save" type="button">保存</button>
          <button class="act" id="template" type="button">填入模板</button>
          <button class="act danger" id="delete" type="button">删除 tavern.json</button>
          <button class="act" id="close" type="button">关闭</button>
        </div>
        <div id="status"></div>
      </div>
    </div>
  `;

  const fab = root.getElementById("fab");
  const overlay = root.getElementById("overlay");
  const personaSelect = root.getElementById("persona");
  const modeSelect = root.getElementById("mode");
  const jsonArea = root.getElementById("json");
  const statusEl = root.getElementById("status");
  const saveBtn = root.getElementById("save");
  const templateBtn = root.getElementById("template");
  const deleteBtn = root.getElementById("delete");
  const closeBtn = root.getElementById("close");

  let loaded = false;

  const setStatus = (text, kind = "") => {
    statusEl.textContent = text;
    statusEl.className = kind;
  };

  const currentPersona = () => personaSelect.value;

  const loadCurrent = async () => {
    const name = currentPersona();
    if (!name) {
      setStatus("没有可用人格。请先在人格配置里创建角色。", "err");
      return;
    }
    setStatus("加载中…");
    try {
      const [config, tavern] = await Promise.all([
        api(`/api/persona/${encodeURIComponent(name)}/config`),
        api(`/api/persona/${encodeURIComponent(name)}/tavern`),
      ]);
      modeSelect.value = (config && config.prompt_profile) || "gscore";
      jsonArea.value = JSON.stringify((tavern && tavern.config) || {}, null, 2);
      localStorage.setItem(PERSONA_KEY, name);
      setStatus(
        tavern && tavern.exists
          ? "已加载 tavern.json。修改后点保存，下一次消息生效。"
          : "该人格还没有 tavern.json；可点“填入模板”后保存。",
        "ok",
      );
    } catch (err) {
      setStatus(String(err.message || err), "err");
    }
  };

  const loadPersonas = async () => {
    if (loaded) return;
    const list = await api("/api/persona/list");
    personaSelect.innerHTML = "";
    for (const item of list || []) {
      const name = typeof item === "string" ? item : item.name;
      if (!name) continue;
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      personaSelect.appendChild(option);
    }
    const remembered = localStorage.getItem(PERSONA_KEY);
    if (remembered && [...personaSelect.options].some((o) => o.value === remembered)) {
      personaSelect.value = remembered;
    }
    loaded = true;
    if (!personaSelect.value && personaSelect.options.length) {
      personaSelect.selectedIndex = 0;
    }
    await loadCurrent();
  };

  const open = async () => {
    overlay.hidden = false;
    setStatus("");
    try {
      await loadPersonas();
    } catch (err) {
      setStatus(String(err.message || err), "err");
    }
  };

  const close = () => {
    overlay.hidden = true;
  };

  const save = async () => {
    const name = currentPersona();
    if (!name) return;
    let config;
    try {
      config = JSON.parse(jsonArea.value || "{}");
    } catch (err) {
      setStatus(`JSON 解析失败：${err.message}`, "err");
      return;
    }
    if (!config || Array.isArray(config) || typeof config !== "object") {
      setStatus("tavern.json 必须是 JSON 对象", "err");
      return;
    }
    saveBtn.disabled = true;
    setStatus("保存中…");
    try {
      await api(`/api/persona/${encodeURIComponent(name)}/tavern`, {
        method: "PUT",
        body: JSON.stringify({ config }),
      });
      await api(`/api/persona/${encodeURIComponent(name)}/config`, {
        method: "PUT",
        body: JSON.stringify({ prompt_profile: modeSelect.value }),
      });
      setStatus("已保存。当前人格下一次消息会重建 session 并生效。", "ok");
    } catch (err) {
      setStatus(String(err.message || err), "err");
    } finally {
      saveBtn.disabled = false;
    }
  };

  const fillTemplate = () => {
    const name = currentPersona() || "角色名";
    jsonArea.value = JSON.stringify(
      {
        name,
        main_prompt: `请以「${name}」的身份，写出你在当前对话中的下一条回复。只写角色会说的话和做的事，不要解释、不要跳出角色。`,
        mes_example:
          "<START>\n{{user}}: 在吗\n{{char}}: 在。\n{{char}}: 说吧\n" +
          "<START>\n{{user}}: 今天好累\n{{char}}: 那就别动了\n{{char}}: ……我也困\n",
        post_history_instructions:
          "[戏外] 需要天气/新闻/价格/日程/文件或记忆时，先调用工具（天气/新闻用 web_search_tool，不确定就 find_tools），" +
          "再用角色口吻说结论；没有本轮工具结果时不要凭记忆或示例回答。示例对话只校准语气，不代表可以跳过工具。",
        author_note: { text: "保持短句，不要客服腔。", depth: 0 },
        scope: "private",
        output_style: "roleplay",
        single_message: true,
      },
      null,
      2,
    );
    setStatus("已填入模板；按需修改后点保存。", "ok");
  };

  const remove = async () => {
    const name = currentPersona();
    if (!name) return;
    if (!confirm(`删除「${name}」的 tavern.json？对话模式不会被自动改回 gscore。`)) return;
    try {
      await api(`/api/persona/${encodeURIComponent(name)}/tavern`, { method: "DELETE" });
      jsonArea.value = "{}";
      setStatus("已删除 tavern.json。", "ok");
    } catch (err) {
      setStatus(String(err.message || err), "err");
    }
  };

  fab.addEventListener("click", open);
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  personaSelect.addEventListener("change", loadCurrent);
  saveBtn.addEventListener("click", save);
  templateBtn.addEventListener("click", fillTemplate);
  deleteBtn.addEventListener("click", remove);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !overlay.hidden) close();
  });
})();
