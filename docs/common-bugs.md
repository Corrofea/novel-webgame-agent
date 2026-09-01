# 常见 Bug 收录与 QA 自查清单

> 本清单是 `repair` worker 修复问题时的自查参照，也是 `qa-check` 阶段人工复核的检查项。
> 素材来源：并列仓库 ClickMacondo 的开发提交记录 + 本管线的静态引擎契约。
> 按类别编号，方便在 QA 报告与修复循环中引用（如 `Bug#2`）。

---

## A. 存档与进度（最高优先级：直接丢玩家进度）

### Bug#1 周目重置没有清空全部存档 key
**症状**：玩家"再来一次"后，旧周目的结局收集、成就、旗标仍残留，或多周目数值串档。
**根因**：重置只删了主存档 key，没删带同一前缀的 `_meta`/成就等派生 key。
**自检**：引擎 `clearSave()` 必须遍历 `localStorage`，删除所有以 `save_key` 开头的 key
（本管线引擎已实现）；修复时不得绕过引擎自己往 localStorage 里写别的 key。

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

---

## 附：QA 通过标准（四阶段全绿）

1. **结构**（validate_game.py）：0 error；warning 逐条确认有理由。
2. **运行**（smoke_test.py，40 次随机游玩）：到达结局率 ≥ 90%，无死路、无循环、
   无不可达节点；平均步数 5–120。
3. **语义**（qa_review，深模型评审）：score ≥ 7，无 major 问题
   （人物 OOC、结局与主旨冲突、选项与原文矛盾、内容明显缺失）。
4. **演示**（人工抽查）：封面 → 开始 → 至少打完一条线 → 结局收集 → 重置 → 再来一次，
   全程无 console 报错。
