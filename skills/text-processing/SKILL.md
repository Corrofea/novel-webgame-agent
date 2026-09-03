---
name: text-processing
description: 小说文本清洗、章节识别、分块与按模式特异性解构提取。输入原始小说文件，输出干净的章节列表、分块与文本解构素材库。文本管线的第一道工序。
---

# 文本处理：清洗 / 章节识别 / 分块 / 解构提取

## 输入
- 原始小说文件（TXT / MD / EPUB，EPUB 需安装 ebooklib）
- 已选定的游戏模式 id（决定解构提取策略，见 `../../templates/game_modes/<id>.json` 的 extraction 字段）

## 输出
- `runtime/<书>/chapters.json`：`{"chapters": [{"id": 1, "title": "第1章", "text": "..."}]}`
- `runtime/<书>/chunks.json`：`{"chunks": [{"id": "c001", "chapters": [1,2], "text": "...", "mode": "classic"}]}`
- `runtime/<书>/bible/extract_<mode_id>.json`：按模式特异性解构的素材库 `{"mode_id", "items", "failed_blocks"}`

## 流程

### 1. 读取与清洗（脚本 `scripts/chunker.py --ingest`）
- 编码探测：优先 UTF-8，失败尝试 GBK/GB18030（网文 TXT 常见 GBK 乱码）
- 去除：BOM、连续空白行、行首广告残留行（"本章未完"、"手机用户请访问"、"请记住本书首发域名"等模式）
- 去除行尾多余空格与全角空白
- 简体化不做（保留原文）

### 2. 章节识别（脚本，规则为主）
- 匹配模式：`第[零一二三四五六七八九十百千万0-9]+[章节回卷部集]` 或 `Chapter \d+` 或 `楔子/序章/尾声/番外`
- 无章节标记的文本：按自然段落群切分为伪章节（每 ~3000 字一章）
- 每章标题保留原文

### 3. 分块（脚本，定长切块）
- 顺序累加章节，`chunk_chars`（默认 8000 字）满即切块；超长块再切、过小块合并
- **模式 id 只记录在块元数据中，不改变切块方式**——模式特异性处理在下一步（解构提取）体现
- 章节标题对后续摘要与场景生成很重要，不要丢弃

### 4. 解构提取（extract 阶段，LLM）
按模式特异性把文本块解构为游戏设计素材（引擎的"可玩化"输入）。触发方式：管线在 chunk 后执行 extract 阶段，逐块调用 LLM（提示词见 `prompts/extract.md`），合并为素材库 `bible/extract_<mode_id>.json`。

**模式分派表**（每模式的提取任务与输出 schema 定义在 `templates/game_modes/<id>.json` 的 `extraction` 字段，此处为总览）：

| 模式 | 提取目标 | 下游用途 |
|---|---|---|
| classic | 场景单元（summary 含起因经过结果闭环 / motivation / characters / location / time / key_event / emotion / atmosphere） | design 场景蓝图节点，emotion/atmosphere 辅助叙述基调 |
| narrative | 章轮廓（抉择点 / 章末悬置 / 伏笔） | 章内分支与章节锚点（chapter_end）设计 |
| strategy | 时期表 / 势力表 / 事件表 / 资源维度 | 回合事件卡片、战略博弈对象、attributes 数值维度 |
| puzzle | 案件弧线 / 线索链 / 证词 / 时间线矛盾 | inventory 线索物品、询问对象、推理台结论候选 |
| epic | 主角线 × 时间轴分段 / 跨线时间对齐 / 旁批点候选 | 多视角线（perspectives）与旁批（commentary） |
| galgame | 心动场景 / 经典对白 / 路线分歧点 | 好感度抉择场景与 galgame 对话框台词 |
| survival | 行程段 / 资源维度 / 风险抉择 / 绝境素材 | 行程 N 段、资源类属性、死亡分支 |
| riddle | 谜题素材（可猜对象 / 谜面原文 / 分层提示 / 线索场景 / 可猜回目） | riddle 节点（question/answer/hints）与线索场景 |
| fate | 转生身份池条目 / 独立命运事件 / 量化禀赋 | fate.draw_pool、SCENES.events 事件池、attributes 禀赋 |

**质量自检**：
- 无空 items；关键字段完整（validate_extract 检查结构性硬伤）
- 引用原文一致性：素材必须出自文本块实际内容，不创作
- 失败块降级：单块失败重试 1 次仍失败则跳过并记录 failed_blocks（素材缺失可容忍，design 会合理扩展）

## 注意事项
- 不要修改正文内容本身（只删噪音），分块边界宁可保守
- 解构提取只做"提取"不做"创作"：素材是原文的浓缩与标注，玩法设计（分支/选项/数值）由 design 阶段完成
- 卷/卷层级信息保留在 chapter 的 `volume` 字段
