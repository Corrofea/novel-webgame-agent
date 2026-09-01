#!/usr/bin/env node
/* 引擎级冒烟：用最小 DOM shim 加载真实游戏数据文件，跑 engine.js 的
 * ?selftest=1 随机自检（25 次随机游玩，报告死路/循环/缺场景）。
 *
 * 用法: node tests/engine_selftest.js <game_dir>
 * 退出码: 0=自检通过; 1=发现运行时错误
 */
'use strict';
const fs = require('fs');
const path = require('path');

const gameDir = process.argv[2];
if (!gameDir) { console.error('用法: node tests/engine_selftest.js <game_dir>'); process.exit(2); }

/* ---------- 最小 DOM shim（只覆盖 engine.js 用到的 API） ---------- */
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
const documentShim = {
  readyState: 'complete',
  documentElement: { style: { setProperty() {} } },
  getElementById(id) { return id === 'app' ? appEl : null; },
  createElement(tag) { return new El(tag); },
  addEventListener() {},
};
const locationShim = { search: '?selftest=1' };
const windowObj = {}; // engine 通过 window.GAME 等访问数据
const AudioShim = function () { return { play() { return Promise.resolve(); }, pause() {}, loop: false, volume: 1, src: '' }; };

/* ---------- 加载 5 个数据文件（同 validate_game.py 的提取逻辑） ---------- */
for (const name of ['game', 'mode', 'characters', 'scenes', 'theme']) {
  const file = path.join(gameDir, 'data', name + '.js');
  const text = fs.readFileSync(file, 'utf-8');
  const m = text.match(new RegExp('window\\.' + name.toUpperCase() + '\\s*=\\s*'));
  if (!m) { console.error(`data/${name}.js 缺少 window.${name.toUpperCase()} =`); process.exit(2); }
  const rest = text.slice(m.index + m[0].length);
  // 找配对的花括号
  let depth = 0, inStr = null, end = -1;
  for (let i = 0; i < rest.length; i++) {
    const c = rest[i];
    if (inStr) { if (c === '\\') i++; else if (c === inStr) inStr = null; continue; }
    if (c === '"' || c === "'") inStr = c;
    else if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) { end = i; break; } }
  }
  if (end < 0) { console.error(`data/${name}.js 花括号不配对`); process.exit(2); }
  windowObj[name.toUpperCase()] = JSON.parse(rest.slice(0, end + 1));
}

/* ---------- 运行引擎 ---------- */
const engineSrc = fs.readFileSync(path.join(__dirname, '..', 'engine', 'engine.js'), 'utf-8');
const sandbox = {
  window: windowObj, document: documentShim, location: locationShim,
  localStorage: localStorageShim, Audio: AudioShim,
  alert() {}, confirm() { return true; }, setTimeout,
  console: { log: () => {}, error: () => {} },
};
require('vm').createContext(sandbox);
require('vm').runInContext(engineSrc, sandbox);

/* ---------- 读自检结果 ---------- */
function findLog(el) {
  if (el.className === 'selftest-log') return el;
  for (const c of el.children) { const r = findLog(c); if (r) return r; }
  return null;
}
const logEl = findLog(appEl);
if (!logEl) { console.error('未找到自检输出（引擎可能抛错或未启动）'); process.exit(1); }
console.log('引擎自检: ' + logEl.textContent);
if (logEl.textContent.indexOf('无运行时错误') >= 0) {
  console.log('ENGINE_SELFTEST_OK');
  process.exit(0);
} else {
  console.error('引擎自检发现错误');
  process.exit(1);
}
