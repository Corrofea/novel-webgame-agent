# 游戏数据生成（阶段二·第二步）

依据设计 brief 与全部参考材料，输出**完整的游戏数据包**（严格 JSON，补丁式契约）：

```json
{
  "patch": {
    "game": { "title": "", "subtitle": "", "author": "", "book_id": "", "mode": "", "entry": "s001", "version": "0.1.0", "save_key": "" },
    "mode": { "mode_id": "", "mode_name": "", "mechanics": [], "attributes": [], "inventory": {"enabled": false, "limit": 20, "items": []}, "panels": [], "perspectives": [], "achievements": {"enabled": false, "list": []}, "commentary": {"enabled": false}, "endings": {"min": 2, "max": 5} },
    "characters": { "characters": [ {"id": "", "name": "", "aliases": [], "role": "", "desc": "", "portrait": ""} ] },
    "scenes": { "s001": {...} },
    "events": []    // fate 模式必填：命运事件池（list，整体替换；其余模式省略）
  }
}
```

注意：**顶层没有 theme 字段**。视觉主题由 detect 阶段决定、game_init 写进 theme.js，
配色与质感由引擎 CSS 自动套用——你输出任何 theme/colors/fonts 都会被丢弃。

## 唯一 schema 权威
`templates/game_folder/data/*.js` 的文件头注释定义了每个字段的格式与硬性规则——生成前必须读取。本提示只是速览。

## 硬性规则（违反必被 QA 拦下）
1. 节点三选一：choices(≥1) / auto / ending；goto 必须指向存在的 id
2. requires/effects 的 attrs id 必须存在于 mode.attributes；物品 id 必须存在于 inventory.items
3. 所有节点从 entry 可达；无死路
4. 结局节点数 ∈ [mode.endings.min, max]；ending 必须是对象
   {"type": "good|bad|neutral", "title": "...", "desc": "..."}——
   type 与 title 分开写，禁止用 "good" 这类字符串代替整个对象
5. narration 用原文改写压缩（60~200 字/场景），保留原文气质，**禁止剧本标记**（如"旁白："、"[场景切换]"）
6. 选项文案具体可感知（"收下手帕"），禁止"接受/拒绝"式空泛选项
7. effects 数值克制：好感度单次 ±1~3，重要抉择 ±5~8
8. 禁止输出 theme/colors/fonts/cover 字段——视觉由引擎按 detect 主题自动套用，
   配色唯一权威是引擎 CSS，输出任何配色都会被丢弃（QA 会拦）
9. **深度落实（从设计 brief 落地，QA 语义评审会查）**：
   - brief 的每个 attributes 必须被 ≥2 个场景节点的 effects 或 requires 引用
     （属性空转 = 语义 major 问题）；好感度外的实质属性要真的开门/锁门/裁决结局
   - brief 声明 inventory_items → mode.inventory.enabled: true 且 panels 含
     "inventory"，物品在场景里经 inventory.add/remove 进出，并用
     requires.inventory 做门槛——物品不是摆设，至少要参与一次门槛或效果
   - 结局按 brief 的条件裁决：通往结局的路径上必有 requires 门槛或裁决分支，
     禁止 A→B→C 直通式单线结局；同一好结局应从 ≥2 条不同状态路径可达
   - requires 门槛选项必须可解释（引擎会自动用 describeRequires 提示缺什么），
     不要用"消失"藏掉选项
10. 输出必须是**合法 JSON**，不得包含注释、尾逗号、截断
11. 章节叙事（narrative）：每个节点带 chapter 字段（章节名）；每章末设一个
    chapter_end: true 锚点节点，章内所有选择路径最终都 goto 到它（叙事可控）；
    mode.chapter_progress.chapters 列出全部章节名（来自设计 brief 的 chapters 字段）
12. 命运轮回（fate）：mode.fate.draw_pool ≥ 2 个转生身份（id/name/desc/start_scene/
    requires 禀赋门槛/effects 身份加成），start_scene 指向该身份剧情线起点；
    scenes 顶层 events 预生成 5~10 个命运事件（id/title/narration/requires/effects/
    goto 指回主线后续节点）；主线放 2~4 个 fate_event: true 节点（自身有 choices 出路）；
    GAME.entry 是抽取锚点（空壳即可，玩家不经过，抽取后直接跳转 start_scene）

## 长篇分批
若要求"只生成第 N 批"，则只输出该批的 scenes 节点（合并后仍须整体可达），
其余字段照常完整输出。
