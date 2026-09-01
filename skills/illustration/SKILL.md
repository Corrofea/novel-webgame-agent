---
name: illustration
description: 游戏插画生成。把角色卡/场景描述转化为绘图提示词，并调用配置好的图片生成 API 出图。输出统一画风的角色立绘（9:16）、场景背景（16:9）与事件 CG。
---

# 插画生成：文字 → 游戏图片

## 输入
- 角色卡 JSON（来自 novel-character-cards 或 characters.js）
- 或场景描述（scenes.js 节点的 narration + 模式/主题上下文）
- 画风配置：`templates/style_refs.json`（团队预先统一，保证一次生成的所有图片同一画风）

## 输出
- 图片文件写入 `games/<书>/assets/characters/<char_id>.png` / `assets/bg/<scene_id>.png`
- 图片 URL 或路径回填到 characters.js / scenes.js 对应字段

## 流程
1. **定画风**：一次生成任务只允许一种画风（从 style_refs.json 选），画风关键词 + 负向提示词统一前缀
2. **转提示词**（脚本 `scripts/prompt_builder.py`）：
   - 角色立绘：中文角色卡 → 英文提示词（外貌、服饰、气质、身份），格式：
     `[风格前缀], character portrait of {name}, {appearance}, {outfit}, {mood}, full body, 9:16, [画风关键词]`
   - 场景背景：`[风格前缀], scene background of {place}, {atmosphere}, {era}, wide shot, 16:9, no characters, [画风关键词]`
   - 事件 CG：`[风格前缀], cinematic scene of {event}, {characters}, {emotion}, 16:9, [画风关键词]`
3. **调 API**（脚本 `scripts/generate_image.py`）：读 config 的 IMAGE_API_URL/KEY；
   失败重试 2 次；全部失败则**降级**：在数据中保留引用路径但不生成文件（引擎自动用占位色块），
   并记录 warning
4. **一致性**：角色立绘生成后，后续出现该角色的所有场景引用同一文件（id 命名），
   禁止同一角色多张脸；如需表情差分，在 `characters.js` 增加 `expressions` 字段（M2 再实现）

## 画风选择指引（style_refs.json）
| 小说氛围 | 建议画风 |
|---|---|
| 古风/仙侠/武侠 | 水墨淡彩 |
| 现代/都市/日常 | 扁平插画（几何+纯色） |
| 悬疑/推理 | 黑白线稿+红色强调 |
| 科幻/未来 | 赛博霓虹（硬边+发光） |
| 西幻/史诗 | 古典油画厚涂 |
| 治愈/轻小说 | 赛璐璐日系 |

## 注意事项
- 未配置图片 API 时，脚本只生成提示词文件（`assets/prompts/*.txt`）并提示，不报错
- 图片尺寸：立绘 9:16、背景 16:9、CG 16:9；输出前检查比例
- 敏感内容：涉及暴力/情色的场景描述直接降级为占位，不调用 API（内容安全分级，见 docs/common-bugs.md）
- 成本控制：先出低分辨率草稿确认构图，再出最终图（M2 再做两段式）
