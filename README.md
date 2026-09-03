# novel-webgame-agent

**把一本小说变成一款可玩的网页互动游戏。**

输入小说文件（TXT / MD / EPUB），自动完成：类型分析 → 模式选择 → 文本解构
→ 人物/世界观提炼 → 游戏设计 → 场景数据生成 → QA 修复 → 打包上传。
输出一个完整游戏文件夹（纯静态，双击 `index.html` 即可玩）+ zip 存档 + 限时下载链接。

> 架构：**plan-execute 外层编排 + 各阶段内 ReAct 修复循环**。
> LLM 只产出结构化数据，引擎是固定代码；提示词全部外置为技能包文件。
> 全程用 DeepSeek API（chat/reasoner 双档位）。

---

## 一、快速开始

```bash
pip install -r requirements.txt        # 依赖：requests + Pillow（生图本地转码 WebP；其余全标准库）
cp .env.example .env                   # 填入 DEEPSEEK_API_KEY（生图可选 SILICONFLOW_API_KEY）
python tools/check_keys.py             # 一键自检两个 key + 列出在售生图模型

# 正式运行（真实 DeepSeek）
python agent.py 你的小说.txt

# 带用户要求（最高优先级，配合自动模式选择；不填则完全自动）
python agent.py 你的小说.txt --prompt "要 galgame 恋爱养成元素，出场角色少而精"

# 无 API key 时用 mock 桩跑通管线（同样的产物结构，用于测试/演示）
python agent.py tests/fixtures/tiny_novel.txt --mock

# 从断点继续（跳过已完成阶段；自动定位该书最近一次运行）
python agent.py 你的小说.txt --resume

# 显式指定运行 id（games/ 游戏库中的唯一文件夹名）
python agent.py 你的小说.txt --run-id novel_abc_20260901-1030
```

**产物一览**（均在仓库根目录，已被 .gitignore 排除）：

| 路径 | 内容 |
|---|---|
| `games/<book_id>_<时间戳>/` | 完整游戏文件夹（**游戏库**：一次调用 = 一个唯一文件夹，互不覆盖），`index.html` 直接打开即玩 |
| `archive/<book_id>_<时间戳>.zip` | 打包存档（zip 内不含 .gitkeep） |
| `web/<book_id>_<时间戳>.zip` | **发布副本**（配 `upload.base_url` 后即输出真实 HTTP 下载链接；服务器用 nginx/caddy 托管此目录即可） |
| `runtime/<book_id>_<时间戳>/` | 中间产物：章节/分块/人物卡/world.md/设计brief/QA报告/state.json |
| `runtime/expiry.json` | 上传到期登记（`python tools/cleanup.py` 自动清理过期产物） |

---

## 二、工作流（plan-execute 十一个阶段）

```
ingest → detect → chunk → [summarize: 长篇] → extract(模式特异性解构)
→ characters → style → game_init → design → generate(分批)
→ qa(修复循环) → illustrate → package
```

计划模板分两档：**short_novel**（<8 万字）与 **long_novel**（≥8 万字，多一步逐卷摘要、
生成分批）。计划在 ingest 之后按**清洗后的真实字数**实例化（不是按文件名猜）。

| 阶段 | 做什么 | 产物 | worker |
|---|---|---|---|
| ingest | 编码探测、广告行清洗、章节识别；EPUB 自动提取（标准库） | `chapters.json` | 工具 |
| detect | 类型分析 → 选游戏模式（9 个语义化模式：classic/narrative/strategy/puzzle/epic/galgame/survival/riddle/fate）+ 视觉主题（12 选 1） | `mode.json` | chat |
| chunk | 分块（8000 字/块，章节边界切分） | `chunks.json` | 工具 |
| summarize | 长篇逐卷摘要（每块一次调用，控制上下文） | `bible/summaries.md` | chat |
| extract | **模式特异性文本解构**：场景单元/谜题素材/事件池等逐块提取（任务与 schema 定义在模式模板 extraction 字段） | `bible/extract_<mode_id>.json` | chat |
| characters | 人物信息摘要（novel-character-cards skill） | `characters.json` | chat |
| style | 提炼主旨/世界观/情感基调/风格定调 | `bible/world.md` | chat |
| game_init | 游戏文件夹模板实例化（空骨架，防示例数据污染；run_id 唯一目录） | `games/<run_id>/` | 工具 |
| design | 设计 brief：场景蓝图/属性/结局清单（深模型） | `design/brief.json` | reasoner |
| generate | 分批生成场景数据（每批 ≤10 场景，patch 合并），末批跑完整校验 | `data/*.js` | chat |
| qa | 结构校验 + 随机游玩冒烟 + 语义评审；问题回传 repair 修复循环 | `qa/qa_report.json` | chat+reasoner |
| illustrate | 角色立绘/场景背景绘图提示词（本地，按主题选画风）；配置生图 key 后自动出图并回写 portrait。API 只吐 PNG/JPEG，落盘统一本地转码 **WebP**（体积约为 PNG 的 1/5~1/10，视觉无损档 q82；无 Pillow 自动降级原格式） | `assets/**/*.prompt.txt` + `*.webp` | 工具 |
| package | zip 打包 + 上传（local/s3）+ 到期登记 | `archive/*.zip` | 工具 |

---

## 三、架构解释

### 3.1 两层编排：plan-execute × ReAct

```
┌─ agent.py（编排器）───────────────────────────────────────────┐
│  读小说 → ingest → 选计划模板 → 逐阶段执行 → 每阶段写检查点     │
│  plan  = 模板实例化的阶段序列（固定知识，运行时加载）            │
│  execute = 顺序跑 stage_xxx()，state.json 支持 --resume 续跑   │
└───────────────────────────────────────────────────────────────┘
        │ 每个"生成型"阶段内部
        ▼
┌─ ReAct 修复循环（core/react.py）─────────────────────────────┐
│   LLM 输出 ──→ 校验器(validator)观察 ──→ 不通过则回传问题清单  │
│   └──────────── 修订重出，最多 N 轮 ─────────────┘            │
└───────────────────────────────────────────────────────────────┘
```

- **LLM 从不接触文件系统**。它的"行动"只是结构化输出；写盘由编排器做，
  校验器（脚本）是"观察"对象，结果回传为下一次修正的输入。
- 校验器三件套：`validate_game.py`（静态图分析：id 唯一、goto 存在性、
  attr 引用、可达性、死路、结局数 + 各模式专属校验：章节锚点/survival 死亡场景/
  riddle 答案契约/fate 转生池与事件池）+ `smoke_test.py`（40 次随机游玩模拟：
  死锁/循环/结局率/平均步数）+ 深模型**语义评审**（人物 OOC、结局与主旨冲突）。

### 3.2 Worker：提示词即技能包

8 个 worker 共享**同一个** DeepSeek 客户端（[core/llm.py](core/llm.py)，指数退避重试、
JSON 模式），区别只在**模型档位**（chat 便宜快 / reasoner 深思考）与**系统提示词**：

```
worker = 角色框架(ROLE_HEADER) + skills/<名称>/SKILL.md 正文 + prompts/<任务>.md
```

提示词作为文件存放（[core/workers.py](core/workers.py) 只做装配，不硬编码），
新增/调整 worker 不需要改代码。

| worker | 技能包 | 档位 |
|---|---|---|
| detect / summarize / style / characters | game-design / world-bible / novel-character-cards | chat |
| design / qa_review | game-design / qa-check | **reasoner** |
| generate / repair | game-design | chat |

### 3.3 内容与引擎分离：数据契约

游戏 = **5 个数据对象**（`window.GAME/MODE/CHARACTERS/SCENES/THEME = {...}`，
JS 数据文件而非 JSON，保证 file:// 直接打开可用）+ **固定引擎**（[engine/engine.js](engine/engine.js)）。

- 引擎负责：场景渲染、选项分支、属性/好感度、物品栏、多视角（epic）、旁批（epic）、
  成就（strategy）、存档（localStorage，前缀式 key）、结局收集、`?selftest=1` 随机自检。
- 视觉开关 = `theme.js` 的 `{"name": "<主题 id>"}`（detect 决定、game_init 写入，
  LLM 无通道）；配色与质感全部由 [engine/theme.css](engine/theme.css) 的
  `body.style-<id>` 块决定——产物里任何 `colors/fonts/cover` 都是旧版漂移残留
  （Bug#19），校验按 warning/error 拦。
- 引擎用 `textContent` 渲染（防 XSS，Bug#11），素材缺失自动降级为占位色块。
- 模式差异集中在 `mode.js` 的功能开关（mechanics/panels/inventory/
  achievements/perspectives/commentary 及模式专属配置节），**一个引擎覆盖所有模式**。
- schema 的唯一权威注释在 `templates/game_folder/data/*.js` 文件头。

**9 种游戏模式**（4 基础 + 5 玩法扩展，模板见 `templates/game_modes/`）：

| 模式 | 玩法核心 | 专属机制 |
|---|---|---|
| classic | 经典叙事：选项分支 + 好感度（默认） | — |
| strategy | 策略模拟：属性博弈 + 成就 | achievements |
| puzzle | 解谜探案：线索收集 + 推理提交 | inventory + 条件选项 |
| epic | 多线史诗：多视角 + 旁批 | perspectives + commentary |
| narrative | 章节化叙事：每章分支汇聚锚点 + 进度条 | chapter/chapter_end + chapter_progress |
| galgame | 恋爱养成：立绘对话框排版 + 好感度门槛 | galgame 排版（mode-galgame body 类） |
| survival | 生存试炼：开局点数分配 + 生命值死亡判定 | allocation UI + hp 死亡跳转 |
| riddle | 字谜问答：谜面 + 答案逐字遮罩揭示 | riddle 节点（question/answer/hints/goto） |
| fate | 命运轮回：分配命运点数 → 转生抽取 → 事件池随机遭遇 | fate.draw_pool + SCENES.events + fate_event 节点 |

**生成/修复使用补丁式契约**（防回归，Bug#17）：

```json
{"patch": {"game": {...}, "mode": {...}, "scenes": {"s001": {...}, "s009": null}}}
```

顶层字段需要整改才给出；scenes 只给改动节点，`null` 表示删除。
编排器按此合并进 `data/scenes.js`（先读旧文件 → 合并 → 写回）。

### 3.4 文件即契约 + 检查点

- worker 之间**不对话**，只通过 `runtime/<book>/` 下的 JSON/MD 传递；
  阶段顺序即依赖顺序。
- 每阶段完成后写 `state.json`（done 列表），`--resume` 跳过已完成阶段；
  QA 修复轮数（qa_rounds）也持久化，重启不丢预算。

### 3.5 模板 = 固定知识，运行时加载

| 模板目录 | 内容 |
|---|---|
| `templates/game_modes/*.json` | 9 种模式的判定标准/分块策略/extraction 解构契约/设计指引/open_slots/QA 标准/运行时开关 |
| `templates/themes/*.json` | 12 套视觉主题目录卡（id/名称/气质/适用/画风）；色值零存储——配色与质感唯一权威是 `engine/theme.css` 的 `style-<id>` 块 |
| `templates/plans/*.json` | 长短篇的阶段图（哪个阶段、用哪个 worker、为什么） |
| `templates/game_folder/` | 游戏文件夹模板（index.html + 数据骨架 + assets 结构） |

### 3.6 常见 Bug 收录（docs/common-bugs.md）

从并列仓库 ClickMacondo 的开发提交记录中提取的 17 类高频问题，按类别编号
（存档清理/单一数据源/结局去重/图结构/渲染交互/管线产物），
既是 `repair` worker 修复时的自查清单，也是人工复核的检查项（Bug#1–#17）。

---

## 四、全文件解读

```
novel-webgame-agent/
├── agent.py                      # ★ 主编排器：CLI 入口 + plan 实例化 + 11 阶段执行
│                                 #   + 检查点/续跑 + patch 写盘 + EPUB 提取
├── config.json                   # 全部配置：API 端点/模型档位/分块预算/QA轮数/上传
├── requirements.txt              # 运行时依赖：requests + Pillow（生图转码 WebP；其余全标准库）
├── .env.example                  # API key 模板（DeepSeek + 硅基流动生图 + S3）
├── .gitignore                    # 排除 runtime/ games/ archive/ 等产物
│
├── core/                         # ★ 基础设施（与具体小说无关的通用件）
│   ├── llm.py                    #   DeepSeek 客户端（重试/退避/JSON模式）+ MockLLM
│   ├── image.py                  #   硅基流动生图客户端（OpenAI 兼容）+ MockImageClient
│   │                             #   （按 [STAGE:x] 标记读 fixtures，无 key 可测全管线）
│   ├── react.py                  #   ReAct 修复循环：输出→校验→回传问题→重试（≤N 轮）
│   ├── workers.py                #   worker 装配：角色框架 + 技能包提示词 → 系统提示词
│   ├── contracts.py              #   契约校验：人物卡/detect/style 结构 + QA 脚本调度
│   └── utils.py                  #   slugify(中文→novel_<hash>)/上下文截断/JSON 读写/极简 .env 加载
│
├── engine/                       # ★ 静态游戏引擎（固定代码，LLM 永不修改）
│   ├── engine.js                 #   渲染/分支/属性/物品/视角/存档/结局收集/自检
│   ├── theme.css                 #   30 个 CSS 变量 + 12 个 style-<id> 风格块（视觉唯一权威）
│   └── theme-preview.html        #   目检工具：12 宫格 iframe 换肤预览（?theme=<id>；不打包进产物）
│
├── skills/                       # ★ 6 个技能包：SKILL.md(操作手册) + prompts/ + scripts/
│   ├── text-processing/          #   清洗(chunker.py)：编码探测/广告过滤/章节识别/分块
│   │                             #   + extract.md 解构提取（按模式特异性提取素材）
│   ├── world-bible/              #   世界观圣经：分卷摘要 + 主旨提炼提示词
│   ├── game-design/              #   核心 skill：模式检测/设计/生成/修复提示词
│   ├── illustration/             #   绘图提示词：12 画风 style_refs + prompt_builder.py
│   ├── qa-check/                 #   ★ QA 三件套：validate_game.py(静态图)
│   │                             #     + smoke_test.py(随机游玩) + review_prompt.md(语义)
│   └── novel-character-cards/    #   人物卡：纯 JSON 结构化数据（复用
│                                 #     games/<run_id>/data/characters.js）+ evals/
│
├── templates/                    # ★ 固定知识（运行时加载，LLM 的"标准答案"）
│   ├── game_modes/               #   9 种模式模板（判定/分块/设计/QA/运行时开关）
│   ├── themes/                   #   12 套视觉主题目录卡（theme 2.0：只存 id，色值在 CSS）
│   ├── plans/                    #   2 份阶段图（short/long）
│   └── game_folder/              #   游戏文件夹模板（schema 权威注释所在）
│
├── tools/                        # ★ 独立工具（agent 以子进程调用）
│   ├── game_init.py              #   游戏文件夹实例化（空骨架 + 引擎复制）
│   ├── package.py                #   zip 打包（排除 .gitkeep）
│   ├── upload.py                 #   上传：local 登记 / s3 预签名 URL（30 分钟）
│   └── cleanup.py                #   到期清理（按 expiry.json 删 games/ 与 zip）
│
├── tests/                        # ★ 128 项 Python 测试 + 3 个引擎 node 测试（unittest）
│   ├── fixtures/                 #   tiny_novel.txt(短篇) + long_novel.txt(10万字)
│   │   ├── mock_data/            #   默认 mock 桩（黄金游戏数据）
│   │   ├── mock_data_long/       #   长篇桩（15 场景 → 分批生成）
│   │   ├── mock_data_bad/        #   坏数据桩（触发修复循环路径）
│   │   └── mock_data_fate/       #   fate 桩（事件池 + 转生身份 → 端到端验证）
│   ├── test_contracts.py         #   契约校验 + ReAct 循环单元测试
│   ├── test_validate.py          #   图校验器：黄金数据 + 9 模式破坏用例
│   ├── test_pipeline.py          #   端到端 mock 管线（短篇/长篇/修复循环/游戏库隔离/fate）
│   ├── engine_selftest.js        #   引擎级冒烟：DOM shim + 真实数据 + 随机游玩
│   ├── fate_engine_test.js       #   fate 模式全流程：分配→转生抽取→事件抽取→结局
│   ├── engine_flow_test.js       #   survival 死亡判定 + riddle 逐字揭示 交互流程
│   └── run_tests.py              #   测试入口
│
├── docs/                         # 设计文档
│   ├── game-modes.md             # 9 种游戏模式分类依据与设计参考
│   └── common-bugs.md            # 17 类常见 bug 收录与 QA 自查清单
│
├── insights/                     # 团队早期调研笔记（保留待删，与本仓库运行无关）
│
└── games/ archive/ runtime/      # 产物目录（git 忽略；cleanup.py 管理生命周期）
```

**数据流**（文件级）：

```
小说文件 ─→ chapters.json ─→ mode.json + chunks.json ─→ (summaries.md)
  ─→ bible/extract_<mode>.json（模式特异性解构素材）
  ─→ characters.json + world.md ─→ brief.json ─→ games/<run_id>/data/*.js
  ─→ qa_report.json(修复循环) ─→ assets/**/*.prompt.txt ─→ archive/<run_id>.zip
```

---

## 五、配置说明（config.json）

| 键 | 说明 |
|---|---|
| `api.base_url` / `api_key_env` | DeepSeek 端点与环境变量名（默认 `DEEPSEEK_API_KEY`） |
| `api.chat_model` / `api.reasoner_model` | deepseek-v4-flash / deepseek-v4-pro（按账号可用模型调整） |
| `image.base_url` / `api_key_env` | 硅基流动端点与环境变量名（默认 `SILICONFLOW_API_KEY`；不配则不出图） |
| `image.model` / `image.size` | 生图模型（默认 Kwai-Kolors/Kolors）与默认尺寸 |
| `workers.*.model` | 8 个 worker 的档位（design/qa_review 用 reasoner） |
| `pipeline.chunk_chars` | 分块字符预算（默认 8000） |
| `pipeline.qa_max_rounds` | QA 修复循环上限（默认 3；每轮含 3 次 ReAct 重试） |
| `pipeline.illustration_styles` | 主题 → 画风映射（插画提示词） |
| `pipeline.llm_stage_retries` / `llm_stage_retry_delay` | 阶段级 LLM 故障自动重试（默认 2 次 / 间隔 45s；reasoner 档耗尽后自动降级 chat 档续跑） |
| `upload.backend` | `local`（默认，拷贝到 web_dir 并登记到期）/ `s3`（预签名 URL，需 boto3） |
| `upload.link_ttl_minutes` | 下载链接有效期（默认 30 分钟） |
| `upload.web_dir` | 发布目录（默认 `web/`，local 后端把 zip 拷入） |
| `upload.base_url` | 静态站点域名（如 `https://dl.example.com`；配了才输出真实 HTTP 链接） |

**服务器部署**：`config.json` 里 `upload.backend: "s3"` + `.env` 的 `S3_*`（R2/S3 预签名 URL），
或 `backend: "local"` + 用 nginx/caddy 把 `web/` 托管成静态站点并配 `upload.base_url`。
管线跑完即在日志输出下载链接；`python tools/cleanup.py` 按 `runtime/expiry.json` 定期清理过期产物。

---

## 六、测试

```bash
python tests/run_tests.py            # 全部：142 项
python tests/run_tests.py contracts  # 契约/ReAct 单元测试（含 extract 校验与模式模板一致性）
python tests/run_tests.py validate   # 图校验器（黄金数据 + 9 模式破坏用例）
python tests/run_tests.py pipeline   # 端到端 mock 管线（短篇+长篇+修复循环+游戏库隔离+fate）
node tests/engine_selftest.js games/<run_id>   # 引擎级随机游玩自检（node ≥ 18）
node tests/fate_engine_test.js       # fate 全流程驱动测试
node tests/engine_flow_test.js       # survival/riddle 交互流程驱动测试
```

---

## 七、已知边界

- **插画**：管线为每个角色/场景产出统一画风的绘图提示词（`assets/**/*.prompt.txt`）。
  配置 `SILICONFLOW_API_KEY` 后自动调硅基流动生图（[core/image.py](core/image.py)），
  PNG 落到同名路径并回写角色 `portrait` 字段，引擎直接展示；未配置或无 key 时
  降级为仅提示词（可用外部工具出图后放到同名路径），引擎对缺失素材自动降级。
- **真实 API 未验证**：mock 管线覆盖全部路径与校验闭环，但真实 DeepSeek 的
  输出格式漂移（如 reasoner 多讲话）只在真实运行中暴露；遇到时看
  `runtime/<book>/qa/qa_report.json` 的 `degraded` 标记与问题清单。
- **EPUB**：支持标准 EPUB；加密/畸形文件会明确报错退出。
- **上传**：默认 local 后端只登记本地路径与到期时间；S3/R2 需 `.env` 的 `S3_*` 变量。
