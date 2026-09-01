---
name: novel-character-cards
description: 小说人物信息卡片。从小说文本提取人物结构化信息（别名消歧/性格/经历/关系/结局），输出 characters.json，可选渲染为 Word 卡片册或 JPG 图片卡片。是世界观圣经与游戏角色数据的事实基础。
---

# 小说人物信息卡片

## 何时使用
- 用户（或编排层）要求"整理小说中的人物信息"
- 需要人物结构化数据作为后续游戏化设计/插画一致性的事实源

## 输入
- 小说文本分块（`runtime/<书>/chunks.json`）或摘要文件
- 可选说明：关注角色范围、补充字段、是否要图片卡片

## 输出
- **默认**：`characters.json` 汇总文件 + Word 文档（`.docx` 人物卡片册，每人一页）
- **可选**：仅当明确要求时，额外生成每人一张 JPG 卡片（`outputs/<姓名>.jpg`）
- 路径约定：相对本 skill 的 `outputs/`（由编排层传入实际工作目录）

## 卡片字段与 JSON Schema
```json
{
  "era": "ancient",   // 可选：用于 JPG 主题（ancient/modern/scifi/western）
  "book": "红楼梦",   // 可选：用于 Word 封面
  "author": "曹雪芹", // 可选
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
   必须归并到同一张卡；建立"别名 → 常用姓名"映射（后续插画一致性依赖此映射）
3. 逐角色填写字段；只出现一次且无特征的路人可简要标注"出场较少"
4. 合并所有块的提取结果，输出 characters.json
5. 调用 `scripts/character_cards.py` 生成 Word 文档（每人一页）
6. 仅当明确要求图片时调用 `scripts/generate_card.py` 生成 JPG

## 使用脚本
```bash
# Word 卡片册（python-docx，书名/作者从 JSON 读取，不再硬编码）
python scripts/character_cards.py --input outputs/characters.json --output outputs/

# JPG 卡片（Pillow；可选，默认不生成）
python scripts/generate_card.py --input outputs/characters.json --output outputs/
```

## 注意事项
- 依赖：`pip install python-docx Pillow`（见项目 requirements.txt）
- Word 模板：若提供模板则按其配色/字段布局美化；默认标题 48pt、标签 24pt、正文 22pt
- 人物关系必须双向记录视角（A→B 与 B→A 在各自卡中对应）
- 与 game-design 的衔接：输出的 name/aliases 直接映射为 characters.js 的 id/name/aliases
