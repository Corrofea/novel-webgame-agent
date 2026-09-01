# 游戏数据生成（阶段二·第二步）

依据设计 brief 与全部参考材料，输出**完整的游戏数据包**（严格 JSON）：

```json
{
  "data": {
    "game": { "title": "", "subtitle": "", "author": "", "book_id": "", "mode": "", "entry": "s001", "version": "0.1.0", "save_key": "" },
    "mode": { "mode_id": "", "mode_name": "", "mechanics": [], "attributes": [], "inventory": {"enabled": false, "limit": 20, "items": []}, "panels": [], "perspectives": [], "achievements": {"enabled": false, "list": []}, "commentary": {"enabled": false}, "endings": {"min": 2, "max": 5} },
    "characters": { "characters": [ {"id": "", "name": "", "aliases": [], "role": "", "desc": "", "portrait": ""} ] },
    "scenes": { "scenes": { "s001": {...} } },
    "theme": { "name": "", "colors": {}, "fonts": {}, "cover": {} }
  }
}
```

## 唯一 schema 权威
`templates/game_folder/data/*.js` 的文件头注释定义了每个字段的格式与硬性规则——生成前必须读取。本提示只是速览。

## 硬性规则（违反必被 QA 拦下）
1. 节点三选一：choices(≥1) / auto / ending；goto 必须指向存在的 id
2. requires/effects 的 attrs id 必须存在于 mode.attributes；物品 id 必须存在于 inventory.items
3. 所有节点从 entry 可达；无死路
4. 结局节点数 ∈ [mode.endings.min, max]；ending 必须有 title
5. narration 用原文改写压缩（60~200 字/场景），保留原文气质，**禁止剧本标记**（如"旁白："、"[场景切换]"）
6. 选项文案具体可感知（"收下手帕"），禁止"接受/拒绝"式空泛选项
7. effects 数值克制：好感度单次 ±1~3，重要抉择 ±5~8
8. theme 必须用提供的主题模板字段，不要自创颜色
9. 输出必须是**合法 JSON**，不得包含注释、尾逗号、截断

## 长篇分批
若要求"只生成第 N 批"，则只输出该批的 scenes 节点（合并后仍须整体可达），
其余字段照常完整输出。
