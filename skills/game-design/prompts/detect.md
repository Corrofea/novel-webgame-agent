# 类型分析 → 游戏模式选择（阶段一任务②）

你将看到一部小说的开头部分（约 3000~8000 字）。任务是判断它的类型并选择游戏模式。

## 判断依据（看文本特性，不看题材标签）
1. 多主角平行线 + 史诗跨度 → `s4_epic`
2. 推理/解谜/线索回收是核心 → `s3_puzzle`
3. 多势力/编年史/数据密集，情节连续性弱 → `g2_strategy`
4. 其余（单主角连续情节）→ `g1_narrative`（默认）

模棱两可时选更靠近叙事的模式。判断顺序：S4 → S3 → G2 → G1。

## 主题选择（templates/themes/ 中的 id）
ancient 古风 / modern 现代简洁 / scifi 科幻霓虹 / western 西幻羊皮纸 / noir 暗黑悬疑 / light 轻小说清新
按小说氛围选择并说明理由。

## 分块策略（决定阶段一任务③怎么切）
- g1_narrative: 按章节/卷分块，保持时间线
- g2_strategy: 按时期/事件阶段分块
- s3_puzzle: 按案件弧线分块
- s4_epic: 按主角线×时期分块

## 输出（严格 JSON）
```json
{
  "mode_id": "g1_narrative",
  "theme_id": "modern",
  "chunk_strategy": "按章节分块，保持时间线",
  "genre": "都市言情",
  "rationale": "简述选择理由（2~3 句）"
}
```
