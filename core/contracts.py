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
    valid_modes = {'g1_narrative', 'g2_strategy', 's3_puzzle', 's4_epic'}
    if data.get('mode_id') not in valid_modes:
        errors.append(f"mode_id 非法: {data.get('mode_id')}（可选: {sorted(valid_modes)}）")
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
