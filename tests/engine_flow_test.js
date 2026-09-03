#!/usr/bin/env node
/* 模式引擎级流程验证：真实 engine.js + 内嵌数据，驱动各模式核心交互路径：
 *   survival: 初始分配 → 决策 → hp 归零 → 自动跳死亡结局
 *   riddle:   谜面渲染 → 逐字输入揭字 → 全揭示 → 继续 → 结局
 * 退出码: 0=全部通过; 1=任一流程断言失败或引擎抛错
 */
'use strict';
const fs = require('fs');
const path = require('path');

class El {
  constructor(tag) {
    this.tagName = (tag || 'div').toUpperCase();
    this.children = []; this.className = ''; this.style = {};
    this.title = ''; this.src = ''; this.parentNode = null; this.firstChild = null;
    this.handlers = {}; this._text = '';
    this.value = '';  // input 支持
    this.maxLength = null; this.placeholder = '';
    this.classList = { add(c) { (this._cls = this._cls || []).push(c); }, remove() {} };
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

function bootEngine(data) {
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
  const locationShim = { search: '' };
  const windowObj = {};
  for (const k of ['GAME', 'MODE', 'CHARACTERS', 'SCENES', 'THEME']) windowObj[k] = data[k];
  const AudioShim = function () { return { play() { return Promise.resolve(); }, pause() {}, loop: false, volume: 1, src: '' }; };
  const engineSrc = fs.readFileSync(path.join(__dirname, '..', 'engine', 'engine.js'), 'utf-8');
  const sandbox = {
    window: windowObj, document: documentShim, location: locationShim,
    localStorage: localStorageShim, Audio: AudioShim,
    alert() {}, confirm() { return true; }, setTimeout,
    console: { log: () => {}, error: () => {} },
  };
  require('vm').createContext(sandbox);
  require('vm').runInContext(engineSrc, sandbox);
  return { appEl, windowObj, bodyClasses, setProps };
}

/* theme 2.0 boot 断言：新产物（colors 空）应挂 mode 类 + style 类，不走变量注入 */
function assertThemeBoot(modeId, name, bodyClasses, setProps) {
  assert(bodyClasses.indexOf('mode-' + modeId) >= 0,
         `body 应挂模式类 mode-${modeId}（实际: ${bodyClasses.join(',')}）`);
  assert(bodyClasses.indexOf('style-' + name) >= 0,
         `body 应挂风格类 style-${name}（实际: ${bodyClasses.join(',')}）`);
  assert(setProps.length === 0, `colors 为空的 theme.js 不应走变量注入（${setProps.length} 次 setProperty）`);
}

const failures = [];
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
function findElByClass(root, cls) {
  // 精确类名匹配（避免 riddle-input 误中 riddle-input-row）
  const q = [root];
  while (q.length) {
    const n = q.shift();
    if (n.className.split(/\s+/).indexOf(cls) >= 0) return n;
    for (const c of n.children) q.push(c);
  }
  return null;
}
function findText(root, text) {
  const q = [root];
  while (q.length) {
    const n = q.shift();
    if (n.textContent.indexOf(text) >= 0) return true;
    for (const c of n.children) q.push(c);
  }
  return false;
}
function click(b) { if (!b) return; for (const fn of (b.handlers.click || [])) fn(); }
function fireKey(b, key) { for (const fn of (b.handlers.keydown || [])) fn({ key }); }

/* ---------- survival：分配 → hp 归零 → 死亡结局 ---------- */
(function testSurvival() {
  const data = {
    GAME: { title: '生存测试', book_id: 'surv_test', mode: 'survival', entry: 's001',
            save_key: 'nwa_surv_test', version: '0.1.0' },
    MODE: {
      mode_id: 'survival', mode_name: '生存试炼', mechanics: ['choices', 'survival'],
      attributes: [{ id: 'hp', label: '生命值', min: 0, max: 100, start: 100, visible: true }],
      inventory: { enabled: false, limit: 20, items: [] },
      panels: ['status', 'choices'], perspectives: [], achievements: { enabled: false, list: [] },
      commentary: { enabled: false },
      survival: { enabled: true, alloc_points: 30, alloc_attrs: [], hp_attr: 'hp',
                  death_threshold: 0, death_scene: 's900' },
      endings: { min: 2, max: 5 },
    },
    CHARACTERS: { characters: [] },
    SCENES: { scenes: {
      s001: { id: 's001', narration: '营地出发。', choices: [
        { text: '稳妥路线', goto: 's002', effects: { attrs: { hp: -20 } } },
        { text: '死亡捷径', goto: 's003', effects: { attrs: { hp: -100 } } }] },
      s002: { id: 's002', narration: '穿越成功。', ending: { type: 'good', title: '穿越成功' } },
      s003: { id: 's003', narration: '筋疲力尽。', ending: { type: 'neutral', title: '筋疲力尽' } },
      s900: { id: 's900', narration: '你倒下了……', ending: { type: 'bad', title: '长眠垭口' } },
    } },
    THEME: { name: 'modern', colors: {}, fonts: {} },
  };
  const { appEl, bodyClasses, setProps } = bootEngine(data);
  assertThemeBoot('survival', 'modern', bodyClasses, setProps);
  click(findBtn(appEl, '开始游戏'));
  assert(findBtn(appEl, '确认出发'), 'survival: 开始游戏后应进入点数分配界面');
  click(findBtn(appEl, '确认出发'));
  assert(findBtn(appEl, '死亡捷径'), 'survival: 分配确认后应进入主线场景');
  click(findBtn(appEl, '死亡捷径'));
  assert(findText(appEl, '长眠垭口'), 'survival: hp 归零应自动跳转死亡结局');
})();

/* ---------- riddle：逐字输入揭字 → 全揭示 → 继续 ---------- */
(function testRiddle() {
  const data = {
    GAME: { title: '字谜测试', book_id: 'riddle_test', mode: 'riddle', entry: 's001',
            save_key: 'nwa_riddle_test', version: '0.1.0' },
    MODE: {
      mode_id: 'riddle', mode_name: '字谜问答', mechanics: ['riddle'],
      attributes: [],
      inventory: { enabled: false, limit: 20, items: [] },
      panels: ['choices'], perspectives: [], achievements: { enabled: false, list: [] },
      commentary: { enabled: false }, riddle: { enabled: true },
      endings: { min: 1, max: 3 },
    },
    CHARACTERS: { characters: [] },
    SCENES: { scenes: {
      s001: { id: 's001', narration: '猜猜他是谁。', riddle: {
        question: '衔玉而生的神瑛侍者？', answer: '贾宝玉',
        hints: ['红楼第一主角'], goto: 's002' } },
      s002: { id: 's002', narration: '全对！', ending: { type: 'good', title: '解谜通关' } },
    } },
    THEME: { name: 'modern', colors: {}, fonts: {} },
  };
  const { appEl, bodyClasses, setProps } = bootEngine(data);
  assertThemeBoot('riddle', 'modern', bodyClasses, setProps);
  click(findBtn(appEl, '开始游戏'));
  const tiles = findElByClass(appEl, 'riddle-tiles');
  assert(tiles && tiles.children.length === 3, 'riddle: 答案 3 字应渲染 3 个遮罩方块');
  const input = findElByClass(appEl, 'riddle-input');
  assert(!!input, 'riddle: 应有输入框');
  const submit = findBtn(appEl, '揭字');
  input.value = '贾';
  click(submit);
  assert(findElByClass(appEl, 'riddle-tiles').children[0].textContent === '贾',
         'riddle: 输入"贾"应揭示首字');
  input.value = '宝'; click(submit);
  input.value = '玉'; click(submit);
  assert(findText(appEl, '✔ 破解成功'), 'riddle: 全部揭示后应显示破解成功');
  click(findBtn(appEl, '继续'));
  assert(findText(appEl, '解谜通关'), 'riddle: 解出后应进入 goto 场景（结局）');
})();

if (failures.length) {
  console.error('ENGINE_FLOW_TEST_FAIL:\n' + failures.map(f => '  - ' + f).join('\n'));
  process.exit(1);
}
console.log('ENGINE_FLOW_TEST_OK: survival 死亡判定 + riddle 逐字揭示 流程通过');
process.exit(0);
