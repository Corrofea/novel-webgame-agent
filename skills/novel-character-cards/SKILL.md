---
name: novel-character-cards
description: 小说人物结构化数据。从小说文本提取人物信息（别名消歧/性格/经历/关系/结局），只输出 JSON 人物卡（characters.json），供后续 AI 查询与游戏数据使用。不生成 Word/JPG 等展示物。
---

# 小说人物卡片（JSON 数据）

## 何时使用
- 需要人物结构化数据作为游戏化设计、插画一致性、后续 AI 查询的事实基础

## 输入
- 小说文本分块（`runtime/<run_id>/chunks.json`）或摘要文件
- 可选说明：关注角色范围、补充字段

## 输出（唯一产出：JSON，无 Word / JPG / 任何展示物）
- `characters.json`：人物结构化数据，**仅供 AI 查询**，不面向用户展示
- 管线落盘位置：
  1. 本 skill 阶段 → `runtime/<run_id>/characters.json`（中间产物）
  2. generate 阶段 → `games/<run_id>/data/characters.js`（游戏数据文件，AI 查询与渲染共用同一份，保持单一数据源）

## 字段与 JSON Schema（与 core/contracts.py validate_characters 一致）
```json
{
  "characters": [
    {
      "name": "林黛玉",
      "aliases": ["黛玉", "林姑娘", "潇湘妃子"],  // 实体消歧关键：所有称呼
      "gender": "女",
      "identity": "女主角，贾府外孙女",
      "traits": ["敏感多愁", "才华横溢"],        // 3-5 个关键词
      "experiences": ["进贾府", "葬花", "焚稿断痴情"],
      "relationships": ["贾宝玉：恋人", "贾母：外孙女"],
      "ending": "泪尽而逝",
      "notes": ["金陵十二钗正册之首", "住所：潇湘馆"]
    }
  ]
}
```
字段缺失时标注"未提及"，**禁止编造**。

## 流程
1. 读取分块文本；超过 8000 字按块处理（上下文预算内逐块提取）
2. **实体消歧是本技能重点**：同一人物的所有称呼（真名/字号/昵称/身份称谓/代称）
   必须归并到同一张卡；建立"别名 → 常用姓名"映射（插画一致性与 AI 查询依赖此映射）
3. 逐角色填写字段；只出现一次且无特征的路人可简要标注"出场较少"
4. 合并所有块的提取结果，输出严格 JSON：`{"characters": [...]}`

## 约束
- 只输出 JSON 数据，**不生成 docx/jpg/任何展示卡片**（旧版 Word/Pillow 脚本已移除）
- 输出文件必须落在**游戏项目目录体系**（runtime/ → games/），与 agent 本体文件隔离
- 不修改小说原文，不做人物创作（原文未提及的结局/性格禁止编造）

## 下游消费者
- design 阶段（场景蓝图/好感度对象选取）
- generate 阶段（写入 games/<run_id>/data/characters.js）
- illustrate 阶段（角色立绘提示词的一致性基准）
- QA 语义评审（人物 OOC 检测）
