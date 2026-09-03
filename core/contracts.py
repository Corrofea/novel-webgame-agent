# -*- coding: utf-8 -*-
"""契约层：中间产物与游戏数据的 schema 校验。

规则来源：templates/game_folder/data/*.js 文件头注释（唯一权威）。
游戏文件夹的完整校验委托给 skills/qa-check/scripts/validate_game.py（CLI）。
"""
import json
import subprocess
import sys
from pathlib import Path

from .utils import ROOT

THEMES_DIR = ROOT / 'templates' / 'themes'


def valid_theme_ids() -> list:
    """视觉主题白名单：templates/themes/*.json 的 id（与 CSS 风格块同源自动同步）。"""
    ids = []
    if THEMES_DIR.is_dir():
        for p in sorted(THEMES_DIR.glob('*.json')):
            try:
                t = json.loads(p.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(t, dict) and t.get('id'):
                ids.append(t['id'])
    return ids


def validate_characters(data) -> tuple:
    """characters.json 校验。返回 (ok, errors)。"""
    errors = []
    chars = data.get('characters') if isinstance(data, dict) else None
    if not isinstance(chars, list) or not chars:
        return False, ['characters 必须是非空列表']
    seen = set()
    for c in chars:
        if not isinstance(c, dict):
            errors.append(f'角色条目必须是对象: {c}')
            continue
        name = c.get('name')
        if not name:
            errors.append('存在缺少 name 的角色')
        if name in seen:
            errors.append(f'角色重名（需合并）: {name}')
        seen.add(name)
        for field in ('gender', 'identity', 'traits', 'experiences', 'relationships', 'ending'):
            if field not in c:
                errors.append(f'角色 {name} 缺少字段 {field}')
        if c.get('aliases') is None:
            errors.append(f'角色 {name} 缺少 aliases（实体消歧字段）')
    return (not errors), errors


def validate_detect(data) -> tuple:
    errors = []
    for field in ('mode_id', 'theme_id', 'chunk_strategy'):
        if not data.get(field):
            errors.append(f'detect 输出缺少 {field}')
    # 模式模板：templates/game_modes/*.json（9 个语义化命名模式）
    valid_modes = {'classic', 'strategy', 'puzzle', 'epic',
                   'narrative', 'riddle', 'survival', 'fate', 'galgame'}
    if data.get('mode_id') not in valid_modes:
        errors.append(f"mode_id 非法: {data.get('mode_id')}（可选: {sorted(valid_modes)}）")
    # 视觉主题：templates/themes/*.json 的 id（12 个，theme 2.0 起配色全归 CSS）
    theme_id = data.get('theme_id')
    if theme_id and theme_id not in valid_theme_ids():
        errors.append(f"theme_id 非法: {theme_id}（可选: {sorted(valid_theme_ids())}）")
    return (not errors), errors


def validate_extract(data, mode_id) -> tuple:
    """extract 阶段合并产物校验。返回 (ok, errors)。

    宽松策略：素材库是辅助输入（design 可容忍缺失），只查结构性硬伤。
    模式关键字段的细则定义在 templates/game_modes/<id>.json 的 extraction.schema。
    """
    errors = []
    items = data.get('items') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return False, ['extract 产物 items 必须是非空数组']
    if not items:
        return False, ['extract 产物 items 为空（全部文本块解构失败）']
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            errors.append(f'素材 {i} 必须是对象')
            continue
        kind = it.get('kind', '')
        if mode_id == 'classic' and not it.get('summary'):
            errors.append(f'经典叙事素材 {i} 缺少 summary（场景单元必须含起因→经过→结果）')
        elif mode_id == 'riddle':
            if kind == 'target' and not it.get('name'):
                errors.append(f'谜题素材 {i}: target 缺少 name（谜底）')
            if kind == 'material' and not it.get('quote'):
                errors.append(f'谜题素材 {i}: material 缺少 quote（谜面来源原文）')
        elif mode_id == 'fate':
            if kind == 'pool' and not it.get('name'):
                errors.append(f'命运素材 {i}: pool 条目缺少 name（转生身份名号）')
            if kind == 'event' and not it.get('title'):
                errors.append(f'命运素材 {i}: event 缺少 title')
    return (not errors), errors


def validate_style(data) -> tuple:
    required = ['主旨', '世界观', '情感基调', '风格定调']
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    missing = [k for k in required if k not in text]
    return (not missing), [f'style 输出缺少小节: {missing}'] if missing else []


def run_qa_scripts(game_dir: Path) -> dict:
    """运行结构校验 + 冒烟，返回汇总报告。"""
    scripts = ROOT / 'skills' / 'qa-check' / 'scripts'
    report = {'ok': True, 'error_count': 0, 'warning_count': 0, 'issues': []}
    for script, args in (('validate_game.py', []), ('smoke_test.py', ['--runs', '30'])):
        try:
            r = subprocess.run(
                [sys.executable, str(scripts / script), str(game_dir), *args, '--json'],
                capture_output=True, text=True, timeout=300)
            data = json.loads(r.stdout) if r.stdout.strip() else {'ok': False, 'issues': [
                {'severity': 'error', 'file': script, 'message': r.stderr[:300]}]}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            data = {'ok': False, 'issues': [{'severity': 'error', 'file': script, 'message': str(e)}]}
        report['ok'] = report['ok'] and data.get('ok', False)
        report['error_count'] += data.get('error_count', 0)
        report['warning_count'] += data.get('warning_count', 0)
        report['issues'].extend(data.get('issues', []))
    return report
