#!/usr/bin/env node
/* fate 模式引擎级验证：真实 engine.js + 内嵌 fate 数据，走完整开局流程
 * 分配点数 → 转生抽取 → 剧情线（命运事件点）→ 事件抽取 → 结局。
 * 退出码: 0=通过; 1=流程断言失败或引擎抛错
 */
'use strict';
const fs = require('fs');
const path = require('path');

/* ---------- 最小 DOM shim（同 engine_selftest.js） ---------- */
class El {
  constructor(tag) {
    this.tagName = (tag || 'div').toUpperCase();
    this.children = []; this.className = ''; this.style = {};
    this.title = ''; this.src = ''; this.parentNode = null; this.firstChild = null;
    this.handlers = {}; this._text = '';
    this.classList = { add() {}, remove() {} };
    this.onerror = null; this.onload = null;
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); }
  appendChild(c) { c.parentNode = this; this.children.push(c); if (!this.firstChild) this.firstChild = c; return c; }
  insertBefore(c, ref) { c.parentNode = this; this.children.push(c); if (!this.firstChild) this.firstChild = c; return c; }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i >= 0) this.children.splice(i, 1);
    if (this.firstChild === c) this.firstChild = this.children[0] || null;
    return c;
  }
  addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); }
}

const storage = new Map();
const localStorageShim = {
  getItem(k) { return storage.has(k) ? storage.get(k) : null; },
  setItem(k, v) { storage.set(k, String(v)); },
  removeItem(k) { storage.delete(k); },
  get length() { return storage.size; },
  key(i) { return [...storage.keys()][i]; },
};

const appEl = new El('div');
const bodyClasses = [];
const setProps = [];
const documentShim = {
  readyState: 'complete',
  documentElement: { style: { setProperty(k, v) { setProps.push([k, v]); } } },
  body: { classList: { add(c) { bodyClasses.push(c); }, remove() {} } },
  getElementById(id) { return id === 'app' ? appEl : null; },
  createElement(tag) { return new El(tag); },
  addEventListener() {},
};
const locationShim = { search: '' };  // 正常 boot（非 selftest）
const windowObj = {};
const AudioShim = function () { return { play() { return Promise.resolve(); }, pause() {}, loop: false, volume: 1, src: '' }; };

/* ---------- fate 黄金数据 ---------- */
windowObj.GAME = { title: '命运测试', book_id: 'fate_test', mode: 'fate',
                   entry: 's001', save_key: 'nwa_fate_test', version: '0.1.0' };
windowObj.MODE = {
  mode_id: 'fate', mode_name: '命运轮回', mechanics: ['fate'],
  attributes: [
    { id: 'jiach', label: '家世', min: 0, max: 20, start: 0, visible: true },
    { id: 'caiqi', label: '才气', min: 0, max: 20, start: 0, visible: true },
  ],
  inventory: { enabled: false, limit: 20, items: [] },
  panels: ['status', 'choices'],
  perspectives: [], achievements: { enabled: false, list: [] },
  commentary: { enabled: false },
  fate: { enabled: true, alloc_points: 20, alloc_attrs: [], draw_pool: [
    { id: 'fate_rich', name: '钟鸣鼎食之子', desc: '生于豪族', start_scene: 's101' },
    { id: 'fate_scholar', name: '寒门书生', desc: '家徒四壁', start_scene: 's101',
      requires: { attrs: { jiach: { gte: 99 } } } },  // 永不命中 → 验证兜底
  ] },
  endings: { min: 2, max: 6 },
};
windowObj.CHARACTERS = { characters: [] };
windowObj.SCENES = {
  events: [
    { id: 'evt_001', title: '天降横财', narration: '路上拾得一袋银子。',
      effects: { attrs: { jiach: 1 } }, goto: 's103' },
  ],
  scenes: {
    s001: { id: 's001', narration: '（抽取锚点：玩家不经过）' },
    s101: { id: 's101', narration: '你生在钟鸣鼎食之家。', choices: [
      { text: '赴京赶考', goto: 's102' }] },
    s102: { id: 's102', fate_event: true, narration: '命运的岔路。', choices: [
      { text: '继续前行', goto: 's103' }] },
    s103: { id: 's103', narration: '功成名就。', ending: { type: 'good', title: '光耀门楣' } },
  },
};
windowObj.THEME = { name: 'modern', colors: {}, fonts: {} };

/* ---------- 加载引擎 ---------- */
const engineSrc = fs.readFileSync(path.join(__dirname, '..', 'engine', 'engine.js'), 'utf-8');
const sandbox = {
  window: windowObj, document: documentShim, location: locationShim,
  localStorage: localStorageShim, Audio: AudioShim,
  alert() {}, confirm() { return true; }, setTimeout,
  console: { log: () => {}, error: () => {} },
};
require('vm').createContext(sandbox);
require('vm').runInContext(engineSrc, sandbox);

/* ---------- 驱动辅助 ---------- */
function assert(cond, msg) { if (!cond) failures.push(msg); }
function findBtn(root, text) {
  const q = [root];
  while (q.length) {
    const n = q.shift();
    if (n.className.indexOf('btn') >= 0 && n.textContent === text) return n;
    for (const c of n.children) q.push(c);
  }
  return null;
}
function click(b) { if (!b) return; for (const fn of (b.handlers.click || [])) fn(); }
function findText(root, text) {
  const q = [root];
  while (q.length) {
    const n = q.shift();
    if (n.textContent.indexOf(text) >= 0) return true;
    for (const c of n.children) q.push(c);
  }
  return false;
}

/* ---------- theme 2.0 boot 断言（先于流程，failures 需在此前声明） ---------- */
const failures = [];
assert(bodyClasses.indexOf('mode-fate') >= 0,
       `body 应挂模式类 mode-fate（实际: ${bodyClasses.join(',')}）`);
assert(bodyClasses.indexOf('style-modern') >= 0,
       `body 应挂风格类 style-modern（实际: ${bodyClasses.join(',')}）`);
assert(setProps.length === 0,
       `colors 为空的 theme.js 不应走变量注入（${setProps.length} 次 setProperty）`);

/* ---------- 完整开局流程 ---------- */
click(findBtn(appEl, '开始游戏'));
assert(findBtn(appEl, '确认出发'), '开始游戏后应进入点数分配界面');
click(findBtn(appEl, '确认出发'));
assert(findBtn(appEl, '抽取命运'), '分配确认后应进入转生抽取界面');
click(findBtn(appEl, '抽取命运'));
assert(findText(appEl, '你转生为'), '抽取后应显示转生结果');
click(findBtn(appEl, '开始这一世'));
assert(findBtn(appEl, '赴京赶考'), '转生确认后应进入身份剧情线起点');
click(findBtn(appEl, '赴京赶考'));
assert(findBtn(appEl, '⚡ 抽取命运事件'), 'fate_event 节点应显示命运抽取按钮');
click(findBtn(appEl, '⚡ 抽取命运事件'));
assert(findText(appEl, '天降横财'), '事件抽取后应显示事件内容');
click(findBtn(appEl, '继续'));
assert(findText(appEl, '光耀门楣'), '事件 goto 应推进到结局');

if (failures.length) {
  console.error('FATE_ENGINE_TEST_FAIL:\n' + failures.map(f => '  - ' + f).join('\n'));
  process.exit(1);
}
console.log('FATE_ENGINE_TEST_OK: 分配→抽取→事件→结局 全流程通过');
process.exit(0);
