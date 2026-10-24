/*
 * 对话引擎管理浮层（core / tavern）
 *
 * 当前 WebConsole 的 React bundle 是预编译产物，人格配置页没有引擎字段入口；
 * 这里用 Shadow DOM 加一个独立面板，不改 React bundle。服务端返回 index.html
 * 时注入本脚本（见 webconsole/setup_frontend.py）。
 *
 * 管理三层：
 *   1. 全局：AI 总开关 / 酒馆引擎总开关 / 默认引擎；
 *   2. 人格：默认引擎 + 目标级覆盖（群聊 group:<id> / 私聊 user:<id>）；
 *   3. 酒馆编排：persona/<name>/tavern.json。
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
        align-items: center; justify-content: center; padding: 20px;
        background: rgba(15,23,42,.55); backdrop-filter: blur(2px);
      }
      #overlay[hidden] { display: none; }
      .panel {
        width: min(820px, 100%); max-height: 90vh; overflow: auto;
        background: #ffffff; color: #0f172a; border-radius: 14px;
        box-shadow: 0 24px 80px rgba(0,0,0,.35); padding: 20px 22px;
      }
      .head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 4px; }
      .head h2 { margin: 0; font-size: 18px; }
      .badge { font-size: 11px; color: #475569; border: 1px solid #cbd5e1; border-radius: 999px; padding: 2px 8px; }
      .hint { color: #64748b; font-size: 12px; line-height: 1.6; margin: 6px 0 14px; }
      .section { border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 14px; margin: 12px 0; }
      .section h3 { margin: 0 0 10px; font-size: 13px; color: #334155; }
      .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
      .field { flex: 1 1 200px; margin-bottom: 8px; }
      label { display: block; font-size: 12px; font-weight: 600; color: #334155; margin-bottom: 5px; }
      select, input[type=text], textarea {
        width: 100%; border: 1px solid #cbd5e1; border-radius: 8px;
        background: #f8fafc; color: #0f172a; padding: 7px 9px; font-size: 13px;
      }
      select:focus, input:focus, textarea:focus { outline: 2px solid #93c5fd; border-color: #60a5fa; }
      textarea { min-height: 220px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; line-height: 1.5; }
      .check { display: flex; align-items: center; gap: 7px; font-size: 12px; color: #334155; margin-bottom: 8px; }
      .check input { width: auto; }
      .override-row { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
      .override-row select.o-type { flex: 0 0 92px; }
      .override-row input.o-id { flex: 1 1 180px; }
      .override-row select.o-engine { flex: 0 0 110px; }
      button.act {
        border: 1px solid #cbd5e1; border-radius: 8px; background: #ffffff;
        color: #0f172a; padding: 7px 12px; font-size: 12px; cursor: pointer;
      }
      button.act:hover { background: #f1f5f9; }
      button.primary { background: #2563eb; border-color: #2563eb; color: #ffffff; }
      button.primary:hover { background: #1d4ed8; }
      button.danger { color: #b91c1c; border-color: #fecaca; }
      button.mini { padding: 5px 9px; font-size: 12px; }
      .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
      #status { min-height: 20px; margin-top: 10px; font-size: 12px; color: #475569; white-space: pre-wrap; }
      #status.ok { color: #15803d; }
      #status.err { color: #b91c1c; }
      details { margin-top: 8px; }
      summary { cursor: pointer; font-size: 12px; font-weight: 600; color: #334155; }
      .muted { color: #94a3b8; font-size: 11px; }
    </style>
    <button id="fab" type="button">对话引擎</button>
    <div id="overlay" hidden>
      <div class="panel" role="dialog" aria-modal="true" aria-label="对话引擎管理">
        <div class="head"><h2>对话引擎管理</h2><span class="badge">core / tavern</span></div>
        <p class="hint">
          引擎决定一轮对话怎么编排：<b>core</b> 是现有群聊 Agent，<b>tavern</b> 是酒馆角色（原生示例 + PHI + 角色化输出）。
          优先级：目标级覆盖 &gt; 人格默认 &gt; 全局默认；AI 总开关关掉时两个引擎都不回复，酒馆总开关关掉时所有 tavern 分配回退 core。
        </p>

        <div class="section">
          <h3>全局开关</h3>
          <label class="check"><input type="checkbox" id="ai-enable"> AI 服务总开关</label>
          <label class="check"><input type="checkbox" id="tavern-enable"> 酒馆引擎总开关</label>
          <div class="field" style="max-width:260px">
            <label for="global-default">默认引擎（人格没有指定时）</label>
            <select id="global-default">
              <option value="gscore">core（群聊 Agent）</option>
              <option value="tavern">tavern（酒馆角色）</option>
            </select>
          </div>
        </div>

        <div class="section">
          <h3>人格引擎分配</h3>
          <div class="row">
            <div class="field">
              <label for="persona">人格</label>
              <select id="persona"></select>
            </div>
            <div class="field">
              <label for="persona-default">该人格默认引擎</label>
              <select id="persona-default">
                <option value="gscore">core（群聊 Agent）</option>
                <option value="tavern">tavern（酒馆角色）</option>
              </select>
            </div>
          </div>
          <label>目标级覆盖 <span class="muted">群聊只认群号；私聊只认用户 ID；同名目标后加覆盖先覆盖</span></label>
          <div id="overrides"></div>
          <button class="act mini" id="add-override" type="button">+ 添加群/用户覆盖</button>
        </div>

        <div class="section">
          <h3>酒馆编排（persona/&lt;name&gt;/tavern.json）</h3>
          <div class="actions" style="margin-top:0">
            <button class="act mini" id="template" type="button">填入模板</button>
            <button class="act mini danger" id="delete-tavern" type="button">删除 tavern.json</button>
          </div>
          <details open>
            <summary>编辑 JSON</summary>
            <textarea id="json" spellcheck="false" placeholder="{ }"></textarea>
          </details>
        </div>

        <div class="actions">
          <button class="act primary" id="save" type="button">保存全部</button>
          <button class="act" id="close" type="button">关闭</button>
        </div>
        <div id="status"></div>
      </div>
    </div>
  `;

  const fab = root.getElementById("fab");
  const overlay = root.getElementById("overlay");
  const personaSelect = root.getElementById("persona");
  const personaDefault = root.getElementById("persona-default");
  const overridesBox = root.getElementById("overrides");
  const jsonArea = root.getElementById("json");
  const statusEl = root.getElementById("status");
  const aiEnable = root.getElementById("ai-enable");
  const tavernEnable = root.getElementById("tavern-enable");
  const globalDefault = root.getElementById("global-default");
  const saveBtn = root.getElementById("save");
  const closeBtn = root.getElementById("close");

  let loaded = false;

  const setStatus = (text, kind = "") => {
    statusEl.textContent = text;
    statusEl.className = kind;
  };

  const currentPersona = () => personaSelect.value;

  const makeOption = (value, label) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    return opt;
  };

  const addOverrideRow = (type = "group", id = "", engine = "tavern") => {
    const row = document.createElement("div");
    row.className = "override-row";

    const typeSelect = document.createElement("select");
    typeSelect.className = "o-type";
    typeSelect.appendChild(makeOption("group", "群聊"));
    typeSelect.appendChild(makeOption("user", "私聊"));
    typeSelect.value = type;

    const idInput = document.createElement("input");
    idInput.type = "text";
    idInput.className = "o-id";
    idInput.placeholder = "群号 / 用户 ID";
    idInput.value = id;

    const engineSelect = document.createElement("select");
    engineSelect.className = "o-engine";
    engineSelect.appendChild(makeOption("gscore", "core"));
    engineSelect.appendChild(makeOption("tavern", "tavern"));
    engineSelect.value = engine;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "act mini danger";
    removeBtn.textContent = "删除";
    removeBtn.addEventListener("click", () => row.remove());

    row.append(typeSelect, idInput, engineSelect, removeBtn);
    overridesBox.appendChild(row);
  };

  const collectOverrides = () => {
    const map = {};
    for (const row of overridesBox.querySelectorAll(".override-row")) {
      const type = row.querySelector(".o-type").value;
      const id = row.querySelector(".o-id").value.trim();
      const engine = row.querySelector(".o-engine").value;
      if (id) map[`${type}:${id}`] = engine;
    }
    return map;
  };

  const loadGlobal = async () => {
    const data = await api("/api/persona/engines/global");
    aiEnable.checked = Boolean(data.ai_enable);
    tavernEnable.checked = Boolean(data.tavern_enable);
    globalDefault.value = data.default_engine === "tavern" ? "tavern" : "gscore";
  };

  const loadPersona = async () => {
    const name = currentPersona();
    if (!name) {
      setStatus("没有可用人格。请先在人格配置里创建角色。", "err");
      return;
    }
    setStatus("加载中…");
    try {
      const [engines, tavern] = await Promise.all([
        api(`/api/persona/${encodeURIComponent(name)}/engines`),
        api(`/api/persona/${encodeURIComponent(name)}/tavern`),
      ]);
      personaDefault.value =
        engines.default || engines.persona_default || globalDefault.value || "gscore";
      overridesBox.innerHTML = "";
      for (const item of engines.overrides || []) {
        addOverrideRow(item.target_type, item.target_id, item.engine);
      }
      jsonArea.value = JSON.stringify((tavern && tavern.config) || {}, null, 2);
      localStorage.setItem(PERSONA_KEY, name);
      setStatus(
        tavern && tavern.exists
          ? "已加载引擎分配与 tavern.json。保存后下一次消息重建会话生效。"
          : "该人格还没有 tavern.json；切到 tavern 前建议先“填入模板”。",
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
      personaSelect.appendChild(makeOption(name, name));
    }
    const remembered = localStorage.getItem(PERSONA_KEY);
    if (remembered && [...personaSelect.options].some((o) => o.value === remembered)) {
      personaSelect.value = remembered;
    }
    loaded = true;
    if (!personaSelect.value && personaSelect.options.length) {
      personaSelect.selectedIndex = 0;
    }
  };

  const open = async () => {
    overlay.hidden = false;
    setStatus("");
    try {
      await loadGlobal();
      await loadPersonas();
      await loadPersona();
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
    let tavernConfig;
    try {
      tavernConfig = JSON.parse(jsonArea.value || "{}");
    } catch (err) {
      setStatus(`tavern.json 解析失败：${err.message}`, "err");
      return;
    }
    if (!tavernConfig || Array.isArray(tavernConfig) || typeof tavernConfig !== "object") {
      setStatus("tavern.json 必须是 JSON 对象", "err");
      return;
    }

    saveBtn.disabled = true;
    setStatus("保存中…");
    try {
      await api("/api/persona/engines/global", {
        method: "PUT",
        body: JSON.stringify({
          ai_enable: aiEnabled(),
          tavern_enable: tavernEnabled(),
          default_engine: globalDefault.value,
        }),
      });
      await api(`/api/persona/${encodeURIComponent(name)}/engines`, {
        method: "PUT",
        body: JSON.stringify({
          default: personaDefault.value,
          overrides: collectOverrides(),
        }),
      });
      await api(`/api/persona/${encodeURIComponent(name)}/tavern`, {
        method: "PUT",
        body: JSON.stringify({ config: tavernConfig }),
      });
      setStatus("已保存。全局开关会清理存活 session；人格分配/tavern.json 在下一次消息重建会话生效。", "ok");
    } catch (err) {
      setStatus(String(err.message || err), "err");
    } finally {
      saveBtn.disabled = false;
    }
  };

  const aiEnabled = () => aiEnable.checked;
  const tavernEnabled = () => tavernEnable.checked;

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
        output_style: "roleplay",
        single_message: true,
      },
      null,
      2,
    );
    setStatus("已填入模板；按需修改后点保存。", "ok");
  };

  const removeTavern = async () => {
    const name = currentPersona();
    if (!name) return;
    if (!confirm(`删除「${name}」的 tavern.json？引擎分配不会被自动改回 core。`)) return;
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
  personaSelect.addEventListener("change", loadPersona);
  saveBtn.addEventListener("click", save);
  root.getElementById("template").addEventListener("click", fillTemplate);
  root.getElementById("delete-tavern").addEventListener("click", removeTavern);
  root.getElementById("add-override").addEventListener("click", () => addOverrideRow());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !overlay.hidden) close();
  });
})();
