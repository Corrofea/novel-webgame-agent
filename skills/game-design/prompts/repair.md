# 游戏数据修复（QA 反馈循环）

你上次生成的数据包未通过校验。以下是问题清单（每条含文件与定位）：
{qa_issues}

请修正后**输出补丁式数据包**：
{"patch": {"game": {...}, "mode": {...}, "characters": {...}, "theme": {...},
 "scenes": {"s001": {...新节点...}, "s009": null, ...}}}
- 顶层字段（game/mode/characters/theme）：需要整改才给出，否则省略
- scenes：只需给出**有改动的节点**；`null` 表示删除该节点（删除前确认没有其他节点
  指向它，或同步修正所有指向它的 goto）
- 只改动问题节点，未出错的节点保持原样；禁止整包重写引入回归
参考 `templates/game_folder/data/*.js` 的 schema 权威注释与常见 bug 清单 `docs/common-bugs.md`。

修复策略：
- error 级问题必须解决：缺失 goto、非法 attr id、孤立节点、死路、结局数量不符
- warning 级：素材缺失不用补（可后填），但引用格式（assets/ 前缀）要修
- 不要为了通过校验而删除有意义的结局或选择——优先补充缺失节点/修正 id
