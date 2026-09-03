/* theme.js —— 视觉风格开关（schema 权威）
 *
 * name 必须是 templates/themes/ 下的一个主题 id（12 选 1），由 detect 阶段
 * 决定、game_init 实例化时写入。配色与质感全部由 engine/theme.css 的
 * body.style-<name> 块决定，engine.js boot() 挂 class 即生效。
 * 禁止包含 colors/fonts/cover 字段（旧版 LLM 自创色漂移的源头，QA 会告警）。
 */
window.THEME = {
  "name": "ancient"
};
