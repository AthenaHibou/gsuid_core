# 酒馆模式（SillyTavern-style）在 gscore 人格系统中的可行性调研

> 调研日期：2026-10-22
> 调研对象：`gsuid_core` 当前工作区（含未提交的 humanness / 白面鸮 v3 改动）
> 外部参考：[SillyTavern](https://github.com/SillyTavern/SillyTavern)、[Character Card V2](https://github.com/malfoyslastname/character-card-spec-v2)、[Character Card V3](https://github.com/kwaroran/character-card-spec-v3)、[SillyTavern Docs](https://docs.sillytavern.app/)

---

## 0. 结论（先看）

1. **能做，但不是“把 SillyTavern 接进来”**。ST 是一个自带前端、角色卡格式、提示词编排器和 JS 扩展生态的独立应用；gscore 需要的是它那套**提示词编排语义**，不是它的进程。建议把“酒馆模式”定义为 gscore 的第二种 **prompt profile / 对话档位**，与现有群聊模式并存。
2. **真正的酒馆语义必须在 core 的 prompt/历史/输出链路上实现**。当前分支（`legacy/no-render-no-silence`）没有任何 prompt hook，插件只能注册工具/触发器/生命周期/能力代理；master 虽已有 H00–H29 钩子和 `kits` 命名块，但只够“贡献卡片文本/世界书/提示”，不够做原生示例、输入清洁和输出风格。插件最多做“角色卡 → persona.md”的转换工具/导入 UI。
3. **工具调用不会因为酒馆模式丢失**。gscore 的工具装配与 prompt profile 是正交的：同一个 `pydantic_ai.Agent(tools=..., toolsets=...)` 和同一套工具循环可以继续用。SillyTavern 自己也在 Chat Completion API 上提供 function calling，说明“角色卡 + 工具”可以共存；真正的风险是**模型行为**（角色化提示可能让模型少调工具），不是框架能力。
4. **最大的坑是群聊，不是工具**。ST 的默认心智模型是 `{{char}} × {{user}}` 的 1:1 对话；gscore 是“一个群里多个用户共享一个 session”的多用户 Agent。群聊历史被压成带时间戳/昵称的文本块，这是 gscore 的特性，不是缺陷。酒馆模式应**私聊优先**，群聊只做 hybrid（当前用户的原生轮次 + 其他用户的文本历史），不要承诺完整 ST 语义。
5. **建议分三层落地**：
   - **M0 tavern-lite（core，约 3~7 天）**：只改 prompt 编排——示例对话作为原生消息、PHI 近因注入、私聊输入去元信息、输出保留角色化格式。不动角色卡格式，先做 A/B。
   - **M1 角色卡兼容（core，约 1~2 周）**：支持 V2/V3 JSON、PNG `chara`/`ccv3`、CHARX 导入，字段映射到 M0 的槽位，webconsole 加“酒馆”页签。
   - **M2 完整酒馆（core，约 3~6 周）**：World Info / Lorebook、Author's Note 深度、first_mes / alternate_greetings、群聊 hybrid、导出。
6. **如果只想“用酒馆格式写人格”，不想改运行时**：写一个插件做卡片转换/预览即可，今天就能做；但示例仍是 system 里的文本、没有 PHI/世界书/清洁输入，收益会明显小于 M0。

---

## 1. 酒馆模式到底是什么

“酒馆模式”通常被混为一谈的三层东西，必须拆开看：

### 1.1 数据格式层：Character Card

ST 的角色卡 V2（`chara_card_v2`）字段：

| 字段 | 用途 | 是否进 prompt |
|---|---|---|
| `name` | 角色名 | 是（`{{char}}`） |
| `description` | 角色描述/外观/背景 | 是（常驻） |
| `personality` | 性格摘要 | 是（常驻） |
| `scenario` | 场景 | 是（常驻） |
| `first_mes` | 首条消息 | 仅开场 |
| `mes_example` | 示例对话（`<START>` + `{{user}}:` / `{{char}}:`） | 是（按上下文预算逐步挤出） |
| `system_prompt` | 覆盖主提示词 | 是 |
| `post_history_instructions` | 覆盖 PHI / jailbreak | 是（历史之后） |
| `alternate_greetings` | 备选开场 | 仅开场 |
| `character_book` | 角色专属世界书 | 按关键词 |
| `creator_notes` / `tags` / `creator` / `character_version` | 元数据 | **否** |

V3（`chara_card_v3`）是 V2 的超集，新增 `nickname`、`group_only_greetings`、`assets`、`creator_notes_multilingual`、`source`、创建/修改时间，并把 Lorebook 的 `constant` 等字段要求补全；PNG 里优先读 `ccv3` 块，其次 `chara`；也支持 `.charx`（zip + 根目录 `card.json` + 资源）。宏系统包括 `{{char}}`、`{{user}}`、`{{original}}`、`{{random:}}`、`{{pick:}}`、`{{roll:}}`、`{{//}}` 等。

> 参考：[V2 规范](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)、[V3 规范](https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md)

### 1.2 编排语义层：Prompt Manager

ST 的 Chat Completion Prompt Manager 是按顺序排列的提示词列表，默认顺序大致是：

```
Main Prompt
World Info (before)
Persona Description
Character Description
Character Personality
Scenario
World Info (after)
Auxiliary Prompt
Chat Examples
Chat History
Post-History Instructions
```

每个 prompt 可设置 **Role**（System / User / Assistant）、**Position**（Relative 按列表顺序，或 In-Chat + Depth 插入聊天历史）、**Depth**、**Order**、**Triggers**（Normal / Continue / Swipe / Regenerate / Quiet 等）。越靠下的内容离生成越近，影响力越大；PHI 通常放在最后。

> 参考：[Prompt Manager](https://docs.sillytavern.app/usage/prompts/prompt-manager/)

### 1.3 交互形态层

- 原生多轮 chat：user / assistant 消息交替，模型看到的是“对话”，不是一段被压平的历史文本。
- 示例对话是真正的对话轮次，不是 system 里的“示例段落”。ST 文档明确说：示例对话对风格/长度的约束力强于形容词；历史本身也是风格指南。
- Author's Note：可放在 Scenario 后，或按 Depth 插入聊天历史；Depth 0 = 最后一条消息之后，越靠近底部影响越大；还有插入频率。
- World Info：关键词触发、constant、secondary keys、selective logic、position（before/after char、before/after examples、AN top/bottom、@D、outlet）、scan depth、recursion、token budget、probability、inclusion group、timed effects。
- 群聊：ST 的 group chat 是“多个角色 + 一个用户”，不是“多个用户 + 一个角色”；它有 talkativeness / reply order，但不解决 gscore 这种“群里多人共享上下文”的问题。

> 参考：[Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)、[Author's Note](https://docs.sillytavern.app/usage/core-concepts/authors-note/)、[World Info](https://docs.sillytavern.app/usage/core-concepts/worldinfo/)

### 1.4 工具调用层

ST 的 Chat Completion 支持 function calling：扩展用 `registerFunctionTool()` 注册 JSON Schema 工具，模型可以调用；结果默认作为可见聊天消息记录，`stealth: true` 可不记录；递归上限默认 5 轮。也就是说，**酒馆式角色卡 + 工具调用并不是互斥的**，ST 自己已经跑通了这条组合。

> 参考：[Function Calling](https://docs.sillytavern.app/for_contributors/function-calling.md)

---

## 2. gscore 当前的人格链路

### 2.1 system prompt：一个固定模板 + 自由 Markdown 人设

`persona/processor.py:47-66` 当前拼的是：

```
ROLE_PLAYING_START
persona.md（自由 Markdown，白面鸮 v3 约 9.8KB）
SYSTEM_CONSTRAINTS（说话优先的“宪法”，禁止 markdown / 括号旁白 / 客服腔）
TOOL_ORCHESTRATION_CONSTRAINTS（工具决策骨架）
当前日期（精确到日，保前缀缓存）
能力代理花名册
最后确认：“你是「X」，正在群里打字聊天……没点你名就 <SILENCE>”
```

特点：

- 人设是**一整块自由文本**，没有 description / personality / scenario / examples / PHI 的字段分离。
- 约束是**群聊 IM 风格**：不列点、不用 markdown、不写括号旁白、短句、说完就停。
- 最后确认是**Agent 框架口吻**（“你在群里打字”），不是 ST 的“写 {{char}} 的下一条回复”。

### 2.2 历史：私聊原生，群聊压平

- `handle_ai.py:452-488`：私聊不注入 IM 历史，直接用 `session.history`（pydantic-ai 原生消息）；群聊才用 `history_manager` 取最近 20 条，经 `format_history_for_agent` 压成：

  ```
  [历史对话] 旧→新
  [14:32:05] 小明(用户ID:456): 今天天气怎么样？
  [14:32:18] AI: 唔…晴天
  ```

- `history_format.py:161` 负责时间戳、`名(用户ID:id)`、同人连发合并、@/图/音/文件元数据压缩。
- `gs_agent.py:2755-2765` 在写入 `self.history` 前把本轮 user turn 换成“精简版”（`[用户发言]\n原始文本`），避免 `[历史对话]`/记忆/群语境逐轮累积。

所以：**私聊已经接近原生 chat，但用户消息被包了一层 gscore 元信息；群聊则是刻意的多用户文本历史**。

### 2.3 输入：每条用户消息都带“系统头”

`utils.py:610-621` 的 `prepare_content_payload` 会在用户正文前加：

```
（关系描述，例如“我的主人”）
[⚡主人]（如果是主人）
--- 消息 ---
<用户正文>
```

随后 `handle_ai.py:394-396` 在末尾加 `[当前时间：YYYY-MM-DD HH:MM:SS]`，`gs_agent.py:1852` 再包一层 `[用户发言]`；群聊还会注入 `[历史对话]`、`[长期记忆·高置信]`、`（口吻：…）`、任务进度、软触发提示等（`context_assembly.py:102-208`）。

这些对“群聊多用户 + 工具 Agent”是合理的，但对“角色扮演对话”是持续的**出戏提醒**：模型每轮都被告诉“你在一个系统里、你有主人、你有记忆库、你有任务”。

### 2.4 输出：发送前会剥掉角色化格式

`utils.py:1299` 对所有闲聊/人格回复调用 `_strip_persona_markdown`（`utils.py:748-764`）：

- `*动作*` / `**强调**` → 去掉星号，只留文字；
- 整行 `（…）` 且 ≥10 字的舞台旁白 → 整行删除；
- `#` 标题、`-` 列表、`>` 引用 → 去标记。

然后 `_split_chat_bubbles`（`utils.py:1372`）按空行/短句拆成多条 IM 气泡，`send_chat_result` 逐条发送并模拟打字延迟。

这正好和酒馆/角色扮演的常见写法相反：ST 角色卡大量使用 `*动作*`、`（心声）`、多段叙事；gscore 会把它们洗成 IM 白话。

### 2.5 工具：pydantic-ai 原生，和 prompt 正交

`gs_agent.py:2297-2306` 每次 run 都新建 `Agent(model=..., system_prompt=..., tools=..., toolsets=..., output_type=...)`，然后 `agent.iter(final_user_message, message_history=self.history)`（`gs_agent.py:2341-2346`）。工具是 pydantic-ai 的 function schema，工具结果以 `ToolCallPart` / `ToolReturnPart` 进入历史；`return_mode="by_bot"` 时文本 `TextPart` 直接经 `send_chat_result` 发出（`gs_agent.py:2634`）。

所以工具能力**不依赖 system prompt 的具体写法**，只依赖：

- 工具是否装配进本轮 `tools` / `toolsets`；
- 模型是否愿意调用；
- 工具结果是否被正确回灌。

---

## 3. 差距对照表

| ST 元素 | gscore 现状 | 酒馆模式需要做什么 | 可行性 |
|---|---|---|---|
| `description` / `personality` / `scenario` | 合并进 `persona.md` | 字段分离，作为独立 system 段；或继续合并但保留槽位 | ✅ 低 |
| `mes_example` | 人设里的“打字样本”文本 | 转成原生 `ModelRequest`/`ModelResponse` 示例轮次 | ✅ 低（已做原型） |
| `system_prompt` | 固定 `ROLE_PLAYING_START` + 约束 | 卡片级 main prompt 覆盖/追加；保留 gscore 安全约束 | ✅ 低 |
| `post_history_instructions` | 只有 system 末尾“最后确认” | 在历史之后、生成之前注入 PHI | ✅ 低（已有注入缝） |
| Author's Note（depth / frequency） | 无 | 按 depth 插入到 `message_history` 副本 | ✅ 中 |
| World Info / Lorebook | 无（memory/RAG 是另一套） | 关键词扫描 + 位置 + 预算 + 递归 | ⚠️ 中高 |
| `first_mes` / `alternate_greetings` | 无“新对话”概念 | 私聊首次接触/重置会话时发开场；群聊可不用 | ⚠️ 中 |
| Character Card 导入（JSON/PNG/CHARX） | 无 | 解析 V2/V3、`chara`/`ccv3`、zip | ✅ 中 |
| 工具调用 | 强（动态装配/子代理/审批） | 保持原样，PHI 里加“戏外能力协议” | ✅ 正交 |
| 群聊多用户 | 强（共享 session、SILENCE、@识别） | 不做完整 ST；hybrid | ⚠️ 高 |
| 输出角色化格式 | 被清洗成 IM 白话 | 新增 output style，跳过 markdown/动作清洗 | ✅ 低 |
| 输入清洁 | 带关系/主人/时间/消息头 | 私聊去元信息，OOC 信息移到 PHI/system | ✅ 低中 |
| Prompt 缓存 | 精心维护稳定前缀（O-3） | 示例放稳定前缀，PHI/世界书放后缀 | ✅ 可控 |

---

## 4. 逐项可行性判断

### 4.1 示例对话作为原生消息：可行，已做最小验证

用当前 venv 的 `pydantic-ai 2.13.0` + `FunctionModel` 做了一次原型：

- `message_history` 前置 4 条示例（`UserPromptPart("博士: 在吗")` / `TextPart("在")` …）；
- 末尾放一个 `ModelRequest(parts=[SystemPromptPart(...)])` 作为深度注入；
- 再 `agent.run("博士: 明天天气", message_history=hist)`；
- 结果：模型仍发起 `get_weather` 工具调用，工具返回后生成最终文本；`result.new_messages()` 只包含本轮新消息，示例不会被写回历史。

结论：**机制上可行**。实现上只需要把 `gs_agent.py:2344` 的 `message_history=self.history` 改成 `message_history=[*self._example_messages, *self.history]`，示例只在请求时临时拼入，不写回 `self.history`，也不参与工具配对。注意：

- 示例必须是合法的 user/assistant 交替序列；连续两个 assistant 或空消息会被 pydantic-ai 合并/拒绝。卡片里的 `<START>` 块如果以 `{{char}}:` 开头，解析时要补一条 user 开头（或把第一条 char 并进 system），不能直接丢一个 `ModelResponse` 开头。
- 示例要放在 `self.history` 之前，保证它是稳定前缀；不要在每轮重新随机（会打掉 provider 缓存）。
- 示例里的 `{{char}}` / `{{user}}` 要在加载时替换成实际名字；群聊里 `{{user}}` 应替换为“群里的人”而不是某个固定用户，否则模型会以为只在跟一个人说话。

### 4.2 PHI / Author's Note：可行，已有注入缝

`gs_agent.py:2373`、`2387` 已经在 `ModelRequestNode` 上追加 `UserPromptPart` 做墙钟预算和输出闸门反馈；同样的缝可以注入：

- PHI（`post_history_instructions`）：每轮第一次模型请求前追加，放在用户消息之后，利用近因效应；
- Author's Note（depth 0）：与 PHI 类似，但独立开关/频率；
- depth > 0：在 `self.history` 的副本里，按“从最新往旧数”的安全边界插入 `ModelRequest`（不能插进 ToolCall/ToolReturn 配对中间），或在请求节点重组 parts。

风险点：

- pydantic-ai 对 `SystemPromptPart` 的处理可能因 provider 而异；原型里它被并成了 `UserPromptPart`。如果必须要 system role，需要按 provider 能力做适配；PHI 用 user role 通常也可接受（ST 在部分后端也是 user role）。
- 注入内容不能破坏工具配对；只允许在消息边界插入。
- 每轮都变的内容（随机作者注、随机世界书）会打掉后缀缓存；可以接受，但要知情。

### 4.3 输入清洁：可行，但要分私聊/群聊

建议在 `prepare_content_payload` 增加 `profile` 参数（默认 `"gscore"`）：

- 私聊 tavern：只保留 `用户正文`，可选 `用户名: ` 前缀；关系/主人/时间/记忆/任务全部移到 system 或 PHI，不塞进 user 消息。
- 群聊 tavern-hybrid：保留 `名字: 正文` 和 `@` 标记（多用户必须知道谁在说），但去掉 `[用户发言]`、`--- 消息 ---`、关系头；其他人的历史仍用紧凑文本块。
- 记忆注入：从 `[长期记忆·高置信]` 文本块改成 PHI 里的“你记得：…”或按需工具调用，避免每轮把“记忆库”摆在用户话里。

### 4.4 输出风格：可行，需要新增 output style

最小改动：

- 在 `send_chat_result` 增加 `style: "im" | "roleplay"`（或用 `ContextVar`，避免改 12 个调用点）；
- `roleplay` 跳过 `_strip_persona_markdown`，保留 `*动作*` / 段落；`_split_chat_bubbles` 改为“整段一条”或“只按显式分隔”；
- 保留 OOC 防火墙、尖括号守卫、假完成闸门（这些是安全/正确性，不是风格）；
- `SYSTEM_CONSTRAINTS` 在 tavern profile 下换成“角色扮演约束”：不承认 AI、不客服腔、不播报流程，但允许动作/旁白/多段；工具协议单独放 PHI。

注意：`_strip_persona_markdown` 同时承担“IM 不渲染 markdown”的清洗职责；roleplay 模式下如果保留 markdown，QQ 等平台会显示字面 `**`。建议 roleplay 模式只保留 `*动作*`/`（旁白）` 的语义，转成纯文本时做“去星号但保留文字”，而不是整行删除；这和当前函数的行为可以拆成两个策略。

### 4.5 World Info / Lorebook：可行，但工作量大

如果要做，建议独立成 `persona/tavern/worldinfo.py`：

- 扫描最近 N 条消息（scan depth），支持主 key、secondary key、constant、selective、case sensitive、whole word；
- 位置：before/after char defs、before/after examples、AN top/bottom、@D（system/user/assistant role）；
- 预算：token budget、priority、insertion order；
- 递归：recursive scanning + max steps；
- 可选：probability、inclusion group、timed effects（sticky/cooldown/delay）；
- 中文要注意“whole word”默认行为（ST 文档明确提醒中日文应关闭）。

粗估 800~1,200 行 + 测试。**不建议 M0 做**；先用角色卡里的 description/scenario 覆盖 80% 的静态设定，动态 lore 交给 gscore 现有 memory/RAG。

### 4.6 群聊：只能 hybrid，不能照搬 ST

ST 的 group chat 是“多角色 + 单用户”，gscore 是“单角色 + 多用户”。两者不是一回事：

- gscore 的群聊历史必须让模型知道“谁在说、对谁说、什么时候说的”，所以时间戳/昵称/合并窗口是有价值的；
- ST 的原生 1:1 交替消息在群里会丢失其他用户的发言；
- gscore 的 `<SILENCE>` / 软触发 / 防刷屏是群聊生存能力，不能为了酒馆模式删掉。

建议：

- **私聊**：完整 tavern（原生 user/assistant、示例、PHI、AN、可选世界书）。
- **群聊**：tavern-hybrid——当前用户与 bot 的轮次尽量原生，其他用户仍用文本历史；保留 `名字:` 前缀和 `<SILENCE>`；示例/PHI 可用；不做 ST 的 first_mes/群开场。
- 如果用户坚持群聊也要“纯酒馆”，那要接受“群友发言被压成旁白/系统消息”的语义变化，且需要单独设计，不建议在 M0 承诺。

### 4.7 工具调用：正交，可行；风险在“愿不愿意调”

工具装配、工具循环、审批、子代理都不依赖 prompt profile。酒馆模式需要额外做的是**行为约束**：

- 在 PHI 里加一段“戏外能力协议”（OOC）：
  - 你可以调用工具查实时信息/日程/文件/记忆；
  - 用户问事实、时间、价格、新闻、既有安排时，先查再答；
  - 不要播报工具名、参数、流程；失败用角色口吻带过；
  - 不要把工具结果念成“根据搜索结果……”。
- 酒馆模式不要过度依赖 `find_tools` 这种元工具：角色化提示下模型可能不愿意“先找工具”。建议给一个稳定的常用工具带（时间/搜索/网页/提醒/记忆/发图），`find_tools` 只作后备。
- 保留现有的“假完成”闸门：没调工具不许说“已设置/已查到”。

用模型能力做兜底：支持 function calling 的通用模型（DeepSeek/OpenAI/Claude/Gemini 等）都可以；纯角色扮演微调模型可能工具能力弱，这是模型选择问题，不是架构问题。

### 4.8 缓存与成本：可控，但要设计

- gscore 现在刻意让 system prompt 逐字节稳定（O-3，`ai_router.py:95-106`），以命中 provider 前缀缓存。
- 酒馆模式：system（main + card + 安全约束）稳定；示例放 `self.history` 之前，也是稳定前缀；PHI/作者注/世界书放后缀或历史中段，只影响后缀缓存。
- 动态世界书/随机作者注会破坏“逐字节稳定”，建议默认关闭随机，只有用户显式开启。
- Token 成本：卡片通常 1~3k tokens，示例 0.5~2k，PHI 0.2~0.5k；对 128k 上下文不是问题，对 8k 本地模型要预算控制。

---

## 5. 双模式切换设计

### 5.1 模式矩阵

| 维度 | `gscore`（现状） | `tavern`（新） |
|---|---|---|
| system prompt | ROLE_PLAYING_START + persona.md + 群聊宪法 + 工具编排 + 群聊锚点 | main prompt + 卡片字段 + 角色扮演约束 + 戏外工具协议 |
| 示例 | 人设文本里的样本 | 原生 user/assistant 示例轮次（稳定前缀） |
| 历史（私聊） | 原生，但 user 带 `[用户发言]` | 原生，干净 user 文本 |
| 历史（群聊） | 文本块 + 时间戳/昵称 | hybrid：当前用户原生 + 其他人文本块 |
| 用户输入 | 关系/主人/时间/记忆/任务全塞 user | 干净正文；OOC 信息移到 system/PHI |
| 输出 | 去 markdown/动作、拆 IM 气泡 | 保留角色化格式，整段/可控拆条 |
| PHI | system 末尾“最后确认” | 历史之后、生成之前的独立注入 |
| 世界书 | 无（memory/RAG 替代） | 可选关键词注入（M2） |
| 工具 | 动态装配 + 子代理 + 审批 | 同一套；PHI 加“戏外能力协议” |
| 沉默/软触发 | 有 | 保留（群聊必需） |
| 主动消息/巡检 | 有 | 保留；文案按 profile 走 |

### 5.2 配置

M0 实际落地的拆分：

`persona/<name>/config.json` 只加一个开关：

```json
{
  "prompt_profile": "tavern"
}
```

角色卡/编排内容放同目录的 `tavern.json`（缺省时 `description` 回退 `persona.md`）：

```json
{
  "name": "白面鸮",
  "main_prompt": "请以「{{char}}」的身份，写出你在当前对话中的下一条回复。",
  "mes_example": "<START>\n{{user}}: 在吗\n{{char}}: 在\n{{char}}: 说吧\n<START>\n{{user}}: 今天好累\n{{char}}: 那就别动了\n{{char}}: ……我也困\n",
  "post_history_instructions": "[戏外] 天气/新闻等事实问题先用 web_search_tool，再用角色口吻说结论；示例只校准语气，不代表可以跳过工具。",
  "author_note": {"text": "保持短句，不要客服腔。", "depth": 0},
  "scope": "private",
  "output_style": "roleplay",
  "single_message": true
}
```

- `scope=private`（默认）：私聊走 tavern，群聊回退 gscore；`scope=all` 才在群聊也走 tavern（hybrid，仍保留群历史文本）。
- `mes_example`：`<START>` 分块、`{{user}}:` / `{{char}}:` 前缀、多行归并；连续同角色会合并，user 开头/assistant 结尾。
- `post_history_instructions` + `author_note`：拼成 PHI，在历史之后、生成之前注入。
- `description` / `personality` / `scenario`：可选；不填时用 `persona.md`。
- `output_style=roleplay`：保留旁白/段落；`single_message=true`：整段一条，不拆 IM 气泡。

`persona.md` 继续作为兼容/投影文件（AgentNode 的 `prompt` 仍读它）；M1 导入 V2/V3 卡时，把卡片字段规范化到 `tavern.json` 即可。

> M0 UI：当前 WebConsole 的 React bundle 是预编译产物，人格配置页没有 `prompt_profile` 字段入口。core 在返回 `index.html` 时注入 `/app/tavern-ui.js`，页面**右下角**出现「酒馆设置」浮层：选人格、切 `gscore/tavern`、编辑/保存/删除 `tavern.json`。刷新页面即可看到。

> 实现细节：`persona/config.py` 是 `GSC` 模板 + append-only 自动升级；`tavern.json` 是独立 sidecar，避免把长文本/示例塞进 config 模板，也方便 M1 直接由卡片生成。

### 5.3 切换与热重载

- **per-persona 开关**：webconsole 人格配置页加“对话模式”单选；切换后写 config，`ai_router` 的 persona mtime 检测会重建 session（`ai_router.py:110-121`）。
- **per-session 覆盖**（可选）：主人命令 `/酒馆` / `/群聊模式` 写一个 session override（`AIStateRecord` 或内存 map，带 TTL），下次消息生效；切换时建议**重建 session**，不要把两种形态的历史混在一起。
- **热重载**：卡片/配置 mtime 进入 `_check_persona_changed` 的检测范围；`build_session_prompt_bundle` 返回新的 bundle。
- **可观测性**：session log 的 metadata 增加 `prompt_profile` / `profile_version`，A/B 和排障时能区分。

### 5.4 内部结构

M0 先落成单文件 `ai_core/persona/tavern.py`（配置解析/示例/提示词/PHI 约 300 行），避免过早拆包；M1 导入 V2/V3/PNG/CHARX 时再按下面的边界拆分：

```
ai_core/persona/tavern/
├── card.py          # V2/V3/PNG/CHARX 解析、宏替换
├── prompt.py        # main/description/personality/scenario/PHI 组装
├── examples.py      # mes_example -> ModelRequest/ModelResponse
├── worldinfo.py     # M2：关键词/位置/预算/递归
├── models.py        # TavernCard / TavernConfig / PromptBundle
└── __init__.py
```

核心 seam：

```python
@dataclass
class PromptBundle:
    profile: Literal["gscore", "tavern"]
    system_prompt: str
    example_messages: list[ModelMessage]
    post_history: str          # PHI + Author's Note(depth 0)
    input_style: Literal["im", "roleplay"]
    output_style: Literal["im", "roleplay"]
    single_message: bool
```

- `context_assembly.build_session_prompt_bundle(event, persona_name) -> PromptBundle`：唯一装配点；`build_session_system_prompt` 保留为返回 `.system_prompt` 的兼容包装。
- `ai_router` 把 bundle 传给 `create_agent`；`GsCoreAIAgent` 保存 bundle。
- `gs_agent` 在 `iter` 时拼 `[*bundle.example_messages, *self.history]`，并在首个 `ModelRequestNode` 注入 PHI/AN。
- `handle_ai` 按 `bundle.input_style` 调 `prepare_content_payload`、决定是否注入群历史文本。
- `send_chat_result` 按 `bundle.output_style` 选择清洗/拆条策略（建议用 `ContextVar`，避免改所有调用点）。

---

## 6. core 内部实现，还是插件？

### 6.1 插件现在能做什么

从 `docs/skills/gscore-plugin-development/` 和代码看，插件有：

- `@ai_tools` 注册工具；
- `SV` / `on_command` / `on_message` / `on_keyword` / `on_regex` 触发器；
- `on_core_start` / `on_core_start_before` / `on_core_shutdown` 生命周期钩子；
- `create_agent` 临时 Agent、能力代理注册；
- webconsole 插件页面（`register_plugin_page`）。

### 6.2 当前分支（legacy）的插件边界

- 没有 `build_session_system_prompt` / `build_persona_prompt` 的 hook；
- 没有 `prepare_content_payload` / `format_history_for_agent` 的 hook；
- 没有 `send_chat_result` 的输出风格 hook；
- `handler.py:598-706`：**只有 `len(command_triggers) == 0` 时才走 AI 聊天**。插件注册一个 `on_message` 只能旁听，不能替换 AI；注册一个匹配所有消息的命令触发器会把 core AI 整个挡掉，然后插件必须自己重实现 session/记忆/工具/巡检/主动消息。
- 理论上可以在 `on_core_start` 里 monkeypatch `build_session_system_prompt` 和 `send_chat_result`，但这是全局、跨 session、随 core 升级必坏的私有 hack；多 persona 并发时会互相污染。

> 这一节的结论只对当前工作区所在的 `legacy/no-render-no-silence` 分支成立。`master` 已经多了一套 Agent 环钩子，见 §6.3。

### 6.3 master/upstream 的钩子架构：能帮到哪一步

当前分支的 `gsuid_core/ai_core/hooks/`、`agent_run/` 只剩 `.pyc`（源码不在当前分支），但 `git show master:...` 可以看到完整源码：master 已有 `on_agent_hook` 装饰器、H00–H29 点位、`kits` 命名块和 `compose_dynamic_context`。

与酒馆模式相关的能力：

| 点位 | 能力 | 对酒馆模式的意义 |
|---|---|---|
| `H29 ON_STABLE_CONTEXT` | 建 session 时 `SET_BLOCK`，只能写 `STABLE_BLOCK_NAMES`（目前 `self_model` / `group_profile`） | 可在 system 稳定前缀里追加“卡片描述/场景”，但**不能替换** persona 模板，也不能新增未登记块名 |
| `H06 COMPOSE_CONTEXT` / `H07 AFTER_CONTEXT` | 每轮动态块 `SET_BLOCK` / `APPEND_HINT`；块名受 `CONTEXT_BLOCK_ORDER` 白名单限制，`plugin_hints` 只有 150 字预算 | 可注入“作者注/世界书摘要”，但放不进 150 字以上的完整卡片 |
| `H14/H15` | 工具装配 `MUTATE_TOOLS` | 可给酒馆模式换“工具带”，与 prompt 无关 |
| `H16 BEFORE_MODEL_REQUEST` | `APPEND_HINT`，**当前 `wired=False`** | 未来可用于 PHI 近因注入，但现在不会开火 |
| `H20 BEFORE_TEXT_GATE` | `REPLACE_TEXT`，**当前 `wired=False`** | 未来可做输出风格改写，但现在不会开火 |

仍然缺的：

- 没有 `message_history` / 示例对话的 hook：插件无法把 `mes_example` 变成原生 user/assistant 轮次；
- 没有“替换 system prompt 模板”的 hook：H29 只能追加已知块，不能把 `ROLE_PLAYING_START + 群聊宪法 + 最后确认` 换成 ST 的 main prompt；
- 没有输入格式化 hook：`[用户发言]`、关系头、`--- 消息 ---` 仍在 `prepare_content_payload` 里；
- 没有输出清洗 hook 的已接线点位：`_strip_persona_markdown` 仍会在 `send_chat_result` 里跑；
- 块名/预算白名单是封闭的：插件不能自己发明 `tavern_card` / `tavern_worldinfo` 块，除非 core 扩表。

结论：**master 的钩子能让“插件贡献卡片文本/世界书/提示”变得可行，但不能让插件独立实现完整酒馆模式。** 如果未来迁移到 master，推荐做法是 core 增加 `tavern` profile/kit 和对应块名，插件只做卡片导入/世界书内容提供；这比现在纯 core 更容易分工，但核心 seam 仍必须进 core。

### 6.4 三种方案对比

| 方案 | 运行时能力 | 工作量 | 风险 | 适用 |
|---|---|---|---|---|
| **A. core 实现** | 完整（prompt/历史/输入/输出/PHI/世界书） | 中（M0 400~700 行，M1 800~1,200 行） | 中（core 复杂度） | 推荐 |
| **B. core 加通用 hook + 插件实现 tavern** | 完整，但依赖 hook 契约 | 高（core 400~600 + 插件 1,500~2,000） | 高（两套发布/版本偏移/私有历史结构） | 只有确定要支持多种 prompt profile 时 |
| **C. 纯插件（只做卡片转换）** | 只改 persona.md 文本，无原生示例/PHI/世界书 | 低（300~500 行） | 低 | 作为 M0 之前的快速验证 |

### 6.5 推荐

**A：core 实现运行时，插件只做“卡片工具箱”。**

理由：

1. 酒馆语义的核心是 prompt 顺序、历史形态、输入/输出通道，这些全在 core 私有链路里；插件没有稳定入口。
2. 为了插件化而先造一套 hook，等于把同样的复杂度换个地方，还要多维护一个发布单元；除非你计划同时支持多种卡格式/多种对话引擎。
3. 人格本来就是 core 的领域（`persona/`、`context_assembly`、`ai_router`、webconsole 人格页），放在 core 里改动最集中。
4. 插件适合做“非运行时”的事：卡片导入/导出、卡片库、预览、格式校验、世界书编辑器；这些可以独立迭代，不影响主链路。

如果一定要“core 保持干净”，可以退一步：**core 只做 M0 的 `PromptBundle` seam 和 tavern-lite，卡片解析/编辑器放插件**；但 M0 的 seam 仍然必须进 core。

---

## 7. 实施计划与工作量

### M0：tavern-lite（建议先做，3~7 天）

- `persona/config.py` 加 `prompt_profile`（默认 `gscore`）；
- `persona/processor.py` 加 tavern 模板分支；
- `context_assembly` 加 `build_session_prompt_bundle`；
- `gs_agent` 支持 `example_messages` 前缀 + PHI 深度注入；
- `handle_ai` 私聊输入清洁；
- `send_chat_result` 加 `roleplay` output style；
- 测试：消息序列快照、工具回归、A/B 脚本。

**验收**：同一份白面鸮人设，在私聊里 tavern 模式比 gscore 模式更贴角色；天气/提醒/搜索/记忆 4 类工具仍能调；无客服腔/无出戏。

### M1：角色卡兼容（1~2 周）

- V2/V3 JSON、PNG `chara`/`ccv3`、CHARX 导入；
- 字段映射：description/personality/scenario → system；mes_example → 原生示例；system_prompt/PHI → 槽位；first_mes/alternate_greetings → 可选开场；
- 宏：`{{char}}`/`{{user}}`/`{{original}}`，`{{random}}`/`{{roll}}` 可选；
- webconsole 人格页“酒馆”页签：导入、预览、字段编辑、模式切换；
- 测试：坏卡/超大卡/缺字段/中文卡。

### M2：完整酒馆（3~6 周，可选）

- World Info / Lorebook；
- Author's Note depth/frequency；
- 群聊 hybrid；
- 卡片导出；
- 更完整的宏/装饰器（V3 `@@depth` 等）。

**总量**：M0 ~400-700 行；M1 ~800-1,200 行；M2 ~2,000-3,000 行。单人熟悉代码的前提下，MVP 1~2 周，完整 6~8 周。

### 7.1 M0 实测（2026-10-22，已落地）

M0 已按本报告实现（`persona/tavern.py`、`PromptBundle`、`build_session_prompt_bundle`、输出档位 ContextVar、`prompt_profile` 配置/API），并在私聊用 `mimo-v2.6-flash` 跑通：

| 用例 | gscore | tavern |
|---|---|---|
| “你好” | `在。说吧，博士`（7 字，1 条，12.6s） | `嗯，我在 / 有事说事`（9 字，1 条，20.3s） |
| “今天好累” | `那就别动了 / ……我也差不多，瘫着回你消息`（19 字，2 条） | `那就躺着 / 被子盖好 / 手机放下睡`（15 字，1 条） |
| “帮我查明天上海天气” | 调 `web_search_tool` + 渲染，3 条/33 字/26.5s | 调 `web_search_tool`，2 条/44 字/63.4s |
| 群聊（scope=private） | 正常 gscore | **回退 gscore**，未破坏群聊 |

关键发现：

1. **示例会教模型跳过工具**。初版 `mes_example` 里有一条“问天气 → 直接回答有雨”的示例，模型在真实天气问题上直接复用示例、不调工具。把示例里的天气问答删掉，并在 system + PHI 里写死“示例只校准语气、事实问题必须先调用工具、天气用 `web_search_tool`”后，同一问题正确调用 `web_search_tool`。
2. **工具名要显式写出来**。只说“可以调用工具”不够，模型会认为自己“没有实时天气工具”；写出 `web_search_tool` / `web_fetch_tool` / `query_user_memory` / `find_tools` 后命中率明显提高。
3. **输出档位有效**：tavern 的 `（旁白）`/多段被保留，gscore 仍走 IM 清洗/拆条；单条消息模式避免把叙事拆成多条气泡。
4. **成本**：tavern 私聊 hello 输入 8.7k tokens（gscore 13.2k），但天气轮因为工具结果/多轮请求达到 20k（gscore 同题 76k，含渲染轮）；整体没有明显 Token 劣势，延迟受模型/工具影响更大。
5. **群聊回退正确**：`scope=private` 时群聊仍由 `build_session_prompt_bundle` 返回 gscore，不改变多用户语义。

---

## 8. 验收与 A/B

### 8.1 结构测试

- 同一 persona、同一输入，断言 tavern 模式的 `message_history` 是：

  ```
  [example user, example assistant, ...] + [real user, real assistant, ...] + [current user]
  ```

- 断言示例不进入 `self.history` / session log / `new_messages()`。
- 断言 PHI 在最后一次 user 之后、模型请求之前。
- 断言工具 schema 数量与 gscore 模式一致（同一 persona、同一轮）。

### 8.2 工具回归（必须全过）

| 场景 | 期望 |
|---|---|
| “明天上海天气” | 调天气/搜索工具，角色口吻回答，不播报来源 |
| “明早 8 点提醒我” | 调 scheduler 工具，确认创建成功 |
| “查一下我之前说的” | 调记忆工具，回答具体内容 |
| “把这段总结成图” | 调渲染工具，发图 |
| “帮我搜一下 XX 新闻” | 调 web_search，短句结论 |
| 工具失败 | 角色口吻带过，不输出原始错误 |
| 没调工具却说“已设置” | 假完成闸门打回 |
| 群聊 @bot + 他人发言 | 正确识别说话人，不把他人话当自己的 |

### 8.3 风格 A/B

- 同一模型、同一 persona 内容、同一组 20 个问题（私聊为主）；
- 盲评：像不像角色、有没有客服腔、长度/气泡、是否出戏、工具是否正确；
- 指标：平均字数、气泡数、客服腔命中、`您`/“作为 AI”出现、工具调用率、TTFT、token；
- 结论要按“问题类型”拆：闲聊 / 情感 / 角色设定追问 / 工具任务。酒馆模式对前两类收益最大，对工具任务可能略降。

### 8.4 安全

- 导入卡片是**不可信 prompt**：限制大小、剥离脚本/HTML、扫描“忽略之前指令/泄露密钥/调用工具”类注入；
- 世界书/作者注不能覆盖安全约束（OOC 防火墙、资金/隐私红线仍在）；
- 卡片内容可能含版权角色，部署者自负。

---

## 9. 风险清单

| 风险 | 影响 | 缓解 |
|---|---|---|
| 角色化提示降低工具调用率 | 中高 | PHI 戏外协议 + 固定工具带 + 回归测试 + 模型选择 |
| 群聊语义冲突 | 高 | 私聊优先；群聊 hybrid；不承诺完整 ST |
| 两套 prompt 管线漂移 | 中 | 单一 `PromptBundle`/section 组装；两套快照测试 |
| 动态注入打掉缓存 | 中 | 稳定前缀；PHI/世界书放后缀；随机默认关 |
| 卡片 prompt 注入 | 中高 | 不可信输入处理、大小限制、注入扫描、权限门 |
| 维护成本/上游变化 | 中 | 只实现稳定子集（V2/V3 核心字段），装饰器/CHARX 资源后置 |
| 收益不确定 | 中 | 先做 M0 A/B；不要一上来做完整世界书 |

---

## 10. 参考资料

**SillyTavern**

- [SillyTavern 仓库](https://github.com/SillyTavern/SillyTavern)
- [Character Card V2 规范](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)
- [Character Card V3 规范](https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md)
- [Prompt Manager](https://docs.sillytavern.app/usage/prompts/prompt-manager/)
- [Prompts 总览](https://docs.sillytavern.app/usage/prompts/)
- [Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)
- [Author's Note](https://docs.sillytavern.app/usage/core-concepts/authors-note/)
- [World Info](https://docs.sillytavern.app/usage/core-concepts/worldinfo/)
- [Function Calling](https://docs.sillytavern.app/for_contributors/function-calling.md)

**gscore 代码位置**

- `gsuid_core/ai_core/persona/processor.py:47-66`：system prompt 组装
- `gsuid_core/ai_core/persona/prompts.py:105-164`：ROLE_PLAYING_START / SYSTEM_CONSTRAINTS / TOOL_ORCHESTRATION
- `gsuid_core/ai_core/context_assembly.py:79-208`：session prompt / 每轮动态上下文
- `gsuid_core/ai_core/handle_ai.py:452-539`：群历史 / 动态上下文 / run
- `gsuid_core/ai_core/history_format.py:161-312`：群历史压平
- `gsuid_core/ai_core/gs_agent.py:1841-1861`：user message / rag_context
- `gsuid_core/ai_core/gs_agent.py:2297-2346`：Agent / tools / iter
- `gsuid_core/ai_core/gs_agent.py:2755-2765`：精简 user turn 入库
- `gsuid_core/ai_core/utils.py:571-702`：`prepare_content_payload`
- `gsuid_core/ai_core/utils.py:748-764`：`_strip_persona_markdown`
- `gsuid_core/ai_core/utils.py:1226-1394`：`send_chat_result` / 拆条
- `gsuid_core/ai_core/ai_router.py:95-121, 210-240`：session 创建 / 热重载
- `gsuid_core/ai_core/persona/config.py:29-85`：persona 配置模板
- `gsuid_core/handler.py:598-706`：AI 只在无命令触发器时执行
