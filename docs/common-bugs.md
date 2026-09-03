# 常见 Bug 收录与 QA 自查清单

> 本清单是 `repair` worker 修复问题时的自查参照，也是 `qa-check` 阶段人工复核的检查项。
> 素材来源：并列仓库 ClickMacondo 的开发提交记录 + 本管线的静态引擎契约。
> 按类别编号，方便在 QA 报告与修复循环中引用（如 `Bug#2`）。

---

## A. 存档与进度（最高优先级：直接丢玩家进度）

### Bug#1 周目重置没有清空全部存档 key（但"开始新游戏"不得清结局收集）
**症状**：玩家"再来一次"后，旧周目的结局收集、成就、旗标仍残留，或多周目数值串档。
**根因**：重置只删了主存档 key，没删带同一前缀的 `_meta`/成就等派生 key。
**自检**：本管线引擎区分两个入口——`clearSave()` 只删主存档 key（"开始游戏"/"再来一次"，
**保留结局收集**，结局要跨周目累积）；`clearAllData()` 遍历删除所有以 `save_key` 开头的
key（"重置游戏"按钮用，含 meta）。**注意**：曾把"开始游戏"也接在按前缀清空的逻辑上，
导致每开一局结局收集就被清空（见 Bug#18）；修复时不得再让普通重开清掉 meta。

### Bug#2 进度有多个数据源，改动一处另一处不同步
**症状**：改存档逻辑后属性显示正确但结局收集丢失，或反之。
**根因**：同一份进度同时存在场景变量、DOM 隐藏状态、localStorage 三处，只维护了其中一处。
**自检**：进度只能有单一数据源 = `state` 对象（含 attrs/flags/inventory/scene/visited），
localStorage 只是它的序列化；渲染层只读 state，绝不反向写。

### Bug#3 结局重复计数 / 重复入库
**症状**：同一结局达成两次，收集列表出现两遍；周目数不对。
**根因**：入库前没有查重（按结局 title 判等），或计数与入库解耦后重复执行。
**自检**：`endGame` 先 `meta.endings.indexOf(title) < 0` 再 push；playthrough 每次结局
+1 且只加一次。QA 冒烟报告里的 `ending_titles` 应无重复。

### Bug#18 结局收集不记录 / 封面计数永远是 0（2026-09 实录）
**症状**：玩完一局回到封面，"已解锁结局"仍是 0/5；翻遍所有生成的游戏都一样。
**根因**（四个叠加，全部在共享引擎 engine/engine.js，故所有游戏同病）：
1. `clearSave()` 按 `save_key` 前缀清空所有 key，"开始游戏"调它 → **每开一局就把
   `_meta_v1`（结局收集）删了**；
2. 封面"结局收集 (0/5)"按钮文本在 `buildCover()` 启动时渲染一次，`show('cover')`
   只切 display 不重建 → 回封面永远显示旧计数；
3. Safari 直接打开本地文件（file://）时 `localStorage` 抛 SecurityError，读写全部被
   try/catch 静默吞掉 → 存档与结局收集**完全写不进去**且无任何提示；
4. `endGame` 往 `meta.endings` 存的是 title **字符串**，`showEndings` 按对象
   访问 `en.type/en.title` → 全部 undefined，收集页渲染空卡片。
**自检**（validate_game.py `ENGINE_CONTRACT` 已覆盖前 3 项）：
- `clearSave()` 必须只 `removeItem(SAVE_KEY)`；`clearAllData()` 才清全部前缀 key
- 封面结局计数每次 `show('cover')` 刷新（`refreshCoverMeta`）
- 所有读写走 `storage` 封装（localStorage 不可用时降级内存 Map + 封面提示条）
- `meta.endings` 存对象 `{type, title, desc}`，渲染时兼容旧字符串数据

---

## B. 数据引用一致性（结构性错误，validate_game.py 已覆盖）

### Bug#4 goto 指向不存在的场景 / entry 不在场景表里
**症状**：点选项后黑屏（引擎 fatal："场景不存在"）。
**自检**：validate_game.py 的图分析；修复时用场景 id 集合做全量交叉检查，
删除节点时必须同步删除指向它的所有 goto。

### Bug#5 requires/effects 引用了未定义的属性或物品 id
**症状**：选项永远锁死（requires 判假），或数值凭空出现/不显示。
**根因**：mode.js 的 attributes 与 scenes.js 的引用各自手写，拼写/改名不同步。
**自检**：凡在 `requires.attrs / effects.attrs / inventory.add/remove / requires.inventory`
里出现的 id，必须存在于 mode.js；生成时优先复用模式模板 runtime 节给出的 id 集合。

### Bug#6 节点缺 choices/auto/ending 三者之一（死路）
**症状**：走到某场景后没有任何选项，游戏卡死。
**自检**：每个节点恰好满足"choices(≥1) 或 auto 或 ending"；无选项且无 auto 的节点
只能以 ending 收尾。

### Bug#7 auto 节点携带 effects
**症状**：自动跳转时效果不生效或重复生效，行为与设计不符。
**根因**：引擎只在选项分支执行 effects，auto 节点的 effects 被静默忽略。
**自检**：validate_game.py 对 `auto.effects` 直接报错；设计阶段把数值变动放在选项上，
不要放 auto 上。

---

## C. 图结构（QA 冒烟覆盖）

### Bug#8 从入口不可达的孤立节点 / 分支被条件锁死
**症状**：内容写了一大堆，玩家永远玩不到（内容浪费）；冒烟报 unreachable。
**根因**：蓝图拼接漏边，或 requires 条件在可达路径上永远不满足。
**自检**：validate_game 做入口 BFS 全图可达性；冒烟随机游玩 N 次后
`unreachable_nodes` 应为空；requires 门槛过高的选项用 `flashHint` 文案提示玩家
缺什么，不要静默锁死。

### Bug#9 场景循环 / 步数超限
**症状**：auto 链死循环，或两个节点互跳，玩家卡住；冒烟报 loop。
**自检**：冒烟模拟有步数上限（500 步）；auto 链总长建议 ≤ 5 段；
修复时给循环段插入分支或 ending。

### Bug#10 结局数不达标 / 过多
**症状**：mode.js endings.min 配置了 3 个结局，实际只有 2 个节点带 ending 标记；
或结局泛滥导致收集面板刷屏。
**自检**：ending 节点数 ∈ [endings.min, endings.max]；每个结局必须有 title（收集面板
按 title 展示），type ∈ good/bad/neutral。

---

## D. 渲染与交互（浏览器运行期）

### Bug#11 用 innerHTML 拼接文本 → XSS / 排版错乱
**症状**：小说原文里的 `<...>` 或引号把页面打断，甚至脚本注入。
**根因**：用 innerHTML 渲染用户文本（小说正文属于不可信内容）。
**自检**：所有小说/选项/人物文本一律 `textContent`；引擎已强制此约定，
修复循环中不得引入 innerHTML。

### Bug#12 素材路径写错（相对路径/大小写/缺失）
**症状**：背景图、立绘、BGM 加载失败，白屏或破图。
**根因**：路径不带 `assets/` 前缀、文件名大小写不符、引用了未生成的素材。
**自检**：bg/portrait/bgm 一律相对路径且首段为 `assets/`；validate_game 对缺失文件
报 warning，QA 阶段应在警告清单里核对（占位素材可接受，路径错误不可接受）。

### Bug#13 界面元素互相遮挡（移动端布局）
**症状**：选项按钮被侧栏盖住点不到，或窄屏下文字溢出。
**根因**：固定像素宽度 + 绝对定位，未做响应式。
**自检**：theme.css 用 CSS 变量 + 弹性布局；QA 抽查 ≥320px 宽度；选项文案 ≤60 字
（validate_game 有 warning）。

### Bug#14 选项条件无提示，玩家不知道为何锁死
**症状**：选项灰着，玩家误以为 bug。
**自检**：锁定选项要能说明原因（引擎 `describeRequires` 生成 tooltip + 点击提示）；
不得用"消失"的方式处理不满足条件的选项。

---

## E. 管线产物（agent 侧）

### Bug#15 路径变更后旧产物残留
**症状**：改了 book_id/目录名后，旧 games/、archive/、runtime/ 产物仍存在，
上传的 zip 内容与当前代码不符。
**自检**：book_id 由 `slugify(文件名)` 决定，改名后手工清理旧产物；
cleanup.py --dry-run 检查过期清单；打包前确认 zip 内无 .gitkeep、无旧 data 残留。

### Bug#16 LLM 输出带 Markdown 代码围栏 / 前后缀文字
**症状**：JSON 解析失败，ReAct 循环空转。
**根因**：LLM 在 JSON 外包了 ```json ... ```。
**自检**：parse_json_block 先整体解析，失败再提取第一个 `{...}` 块；
repair 指令明确"输出必须是纯 JSON，不得用代码围栏"。

### Bug#17 修复循环越修越坏（回归）
**症状**：修 A 问题引入了 B 问题，QA 轮数耗尽。
**根因**：repair 重写整个数据包时丢了未报错部分的信息。
**自检**：repair 采用**补丁式输出**（只改有问题的场景/字段），不要整包重写；
validate_game 的问题清单按文件归类回传，一次修复后立即复跑全套校验。

### Bug#19 主题漂移：theme.js 被 LLM 塞入自创色（theme 2.0 实录，2026-09）
**症状**：同一套模板生成的所有游戏视觉都是"暖咖自配色"，盖过主题气质；
生成的 theme.js 出现 `colors/fonts/cover` 字段，色值与 detect 主题毫无关系。
**根因**（三层叠加）：
1. 主题模板 JSON 携带机器色值（colors/fonts/cover）→ LLM 拿到"我可以调色"的错觉，
   generate 阶段把模板 JSON 拷进 theme.js 再"发挥"改色；
2. theme.js 的写入通道开放给 LLM patch（apply_patch 顶层含 theme 键）；
3. QA 不校验 theme.js 内容 → 漂移零成本落地。
**根治（theme 2.0）**：视觉唯一权威 = `engine/theme.css` 的 12 个 `style-<id>` 块；
theme.js 只许 `{"name": "<白名单 id>"}`（detect 决定 → game_init 写入，LLM 无通道）；
主题模板 JSON 不再存任何色值；apply_patch 丢弃 theme 键。
**自检**（validate_game.py check_theme 已覆盖）：
- theme.js 有 `colors` = warning「旧版视觉漂移残留」→ 跑 `tools/theme_backfill.py` 回填
- `name` 不在 `templates/themes/` 白名单 / CSS 无对应 `body.style-<id>` 块 = error
- 修数据源时不得再往 theme.js 写色值；repair 补丁无 theme 通道

### Bug#20 LLM 空响应裸崩：阶段故障无兜底、失败 exit 0（2026-09 实录）
**症状**：真实跑 generate 批 2/2 时 DeepSeek 连续 3 次返回空内容 → LLMError 未捕获，
裸 traceback 中断整条管线；`python agent.py ... | tee log` 下管道掩盖退出码（exit 0），
用户误以为跑成功。
**根因**（两层叠加）：
1. `react_loop`/`stage_generate` 只处理"校验不过"，不处理 LLM 调用层异常 → 异常炸穿 run()；
2. main() 无顶层捕获；经由管道跑时退出码被 tee 吞掉。
**自检**（agent.py 已修）：
- run() 阶段级 `except LLMError → RuntimeError（带 --resume 指引）`，main 顶层捕获
  LLMError/RuntimeError → 友好信息 + `sys.exit(1)`；KeyboardInterrupt → exit 130
- checkpoint 未写 = 阶段未完成 → `--resume` 从失败阶段续（apply_patch 合并语义，幂等）
- 空内容重试降级链（core/llm.py）：先去掉 max_tokens（交服务端默认），仍空再去掉
  response_format json 约束（自由输出后由 parse_json_block 容错解析）
- 别再用管道掩盖退出码：检查 `$?` 或用 `set -o pipefail`

### Bug#21 生图提示词尺寸标注永远丢失（2026-09 实录）
**症状**：所有生成图片都是 1024×1024 方形——立绘该是 9:16、背景该是 16:9，
全库无一例外；生图成功率看似正常，实际比例信息从未送达 API。
**根因**：`_fmt_prompt` 写单行头 `# portrait / 画风: X / 尺寸: 9:16`，
`load_prompt_file` 却只匹配以 `# 尺寸: ` 开头的独立行 → 尺寸永远解析为 None → 回落默认。
**自检**（core/image.py `load_prompt_file` 已改为行内搜索 `尺寸:`；tests/test_image.py 回归）：
- prompt 头含 `尺寸:` 标注时出图必须按 720x1280/1280x720 请求（Kolors 实测支持）
- 顺带：重试分类——429/5xx 退避重试，4xx（参数/鉴权/余额）立即失败不空转
- 每阶段结束写 `runtime/<run_id>/illustrate.json` manifest，失败图不再只活在 console

---

## 附：QA 通过标准（四阶段全绿）

1. **结构**（validate_game.py）：0 error；warning 逐条确认有理由。
2. **运行**（smoke_test.py，40 次随机游玩）：到达结局率 ≥ 90%，无死路、无循环、
   无不可达节点；平均步数 5–120。
3. **语义**（qa_review，深模型评审）：score ≥ 7，无 major 问题
   （人物 OOC、结局与主旨冲突、选项与原文矛盾、内容明显缺失）。
4. **演示**（人工抽查）：封面 → 开始 → 至少打完一条线 → 结局收集 → 重置 → 再来一次，
   全程无 console 报错。
