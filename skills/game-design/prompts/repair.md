# 游戏数据修复（QA 反馈循环）

你上次生成的数据包未通过校验。以下是问题清单（每条含文件与定位）：
{qa_issues}

请修正后**输出补丁式数据包**：
{"patch": {"game": {...}, "mode": {...}, "characters": {...},
 "scenes": {"s001": {...新节点...}, "s009": null, ...}}}
- 顶层字段（game/mode/characters）：需要整改才给出，否则省略
- 顶层禁止 theme 字段（theme.js 是引擎视觉开关，由 game_init 写入；补丁不含此通道，
  QA 若报 theme 问题 = 旧版 colors 漂移残留，无法用补丁修复，跳过即可）
- scenes：只需给出**有改动的节点**；`null` 表示删除该节点（删除前确认没有其他节点
  指向它，或同步修正所有指向它的 goto）
- 只改动问题节点，未出错的节点保持原样；禁止整包重写引入回归
参考 `templates/game_folder/data/*.js` 的 schema 权威注释与常见 bug 清单 `docs/common-bugs.md`。

修复策略：
- error 级问题必须解决：缺失 goto、非法 attr id、孤立节点、死路、结局数量不符
- warning 级：素材缺失不用补（可后填），但引用格式（assets/ 前缀）要修
- 不要为了通过校验而删除有意义的结局或选择——优先补充缺失节点/修正 id
- **玩法深度问题（语义评审报"属性空转/纯分支/物品摆设"）按补丁通道修**：
  mode 补 attributes/inventory.items（顶层 mode 键整体替换，需带全现有字段）、
  scenes 给 2~3 个节点挂 effects/requires、有物品的加 inventory.add/remove 与
  requires.inventory；这些改动不破坏图结构，只需保证新引用 id 存在于 mode。
  若某结局想改成条件式：把结局前路径插入一个带 requires 门槛的选择节点
  （原 goto 改指向新节点），不要删掉原有内容
