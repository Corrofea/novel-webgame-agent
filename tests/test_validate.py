# -*- coding: utf-8 -*-
"""validate_game.py / smoke_test.py 图校验测试。

用 mock 黄金数据搭建游戏目录，验证：
  1) 黄金数据通过全部校验（回归护栏）
  2) 各类破坏性改动（坏 goto / 孤立节点 / 死路 / 重复结局）能被检出
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'skills' / 'qa-check' / 'scripts'))

from validate_game import extract_js_object, load_game, validate  # noqa: E402
from smoke_test import run_once  # noqa: E402


def build_game_dir(scenes, mode=None, game=None, characters=None) -> Path:
    """用黄金数据 + 覆盖场景，搭一个临时游戏目录。"""
    tmp = Path(tempfile.mkdtemp(prefix='nwa_test_'))
    data_dir = tmp / 'data'
    data_dir.mkdir(parents=True)
    mode = mode or {
        'mode_id': 'g1_narrative', 'mode_name': '叙事冒险', 'mechanics': ['choices', 'affection'],
        'attributes': [{'id': 'affection_suqing', 'label': '苏晴好感', 'min': 0, 'max': 100,
                        'start': 0, 'visible': True}],
        'inventory': {'enabled': False, 'items': []}, 'panels': ['status', 'choices'],
        'perspectives': [], 'achievements': {'enabled': False, 'list': []},
        'commentary': {'enabled': False}, 'endings': {'min': 2, 'max': 5},
    }
    game = game or {'title': '测试', 'book_id': 'test_book', 'mode': 'g1_narrative',
                    'entry': 's001', 'save_key': 'nwa_test', 'version': '0.1.0'}
    characters = characters or {'characters': [
        {'id': 'c_mo', 'name': '陈默', 'aliases': [], 'role': '主角'}]}
    objs = {'game': game, 'mode': mode, 'characters': characters,
            'scenes': {'scenes': scenes}, 'theme': {'name': 'modern', 'colors': {}}}
    for key, obj in objs.items():
        (data_dir / f'{key}.js').write_text(
            f'window.{key.upper()} = {json.dumps(obj, ensure_ascii=False)};\n', encoding='utf-8')
    return tmp


def golden_scenes():
    return {
        's001': {'id': 's001', 'narration': '开场。', 'choices': [
            {'text': '继续', 'goto': 's002', 'effects': {'attrs': {'affection_suqing': 10}}},
            {'text': '离开', 'goto': 's003'}]},
        's002': {'id': 's002', 'narration': '发展。', 'choices': [
            {'text': '好结局', 'goto': 's004'}, {'text': '普通结局', 'goto': 's005'}]},
        's003': {'id': 's003', 'narration': '坏结局。',
                 'ending': {'type': 'bad', 'title': '坏结局'}},
        's004': {'id': 's004', 'narration': '好结局。',
                 'ending': {'type': 'good', 'title': '好结局'}},
        's005': {'id': 's005', 'narration': '普通结局。',
                 'ending': {'type': 'neutral', 'title': '普通结局'}},
    }


class TestGoldenGame(unittest.TestCase):
    """黄金数据护栏：改动引擎/校验器后应仍然全绿。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = build_game_dir(golden_scenes())
        data, fatal = load_game(cls.tmp)
        cls.data, cls.fatal = data, fatal

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_load_ok(self):
        self.assertIsNone(self.fatal)

    def test_no_errors(self):
        issues = validate(self.data, self.tmp)
        errors = [i for i in issues if i.severity == 'error']
        self.assertEqual(errors, [])

    def test_extract_js_object_handles_comments(self):
        text = (self.tmp / 'data' / 'game.js').read_text(encoding='utf-8')
        obj = extract_js_object('/* 注释 */\nwindow.GAME = { "title": "x" };\n', 'GAME')
        self.assertEqual(obj['title'], 'x')

    def test_smoke_no_deadlock(self):
        from smoke_test import _apply, _passes
        mode = self.data['mode.js']
        rng = __import__('random').Random(42)
        bad = []
        for _ in range(40):
            r = run_once(self.data['scenes.js']['scenes'], mode, 's001', rng)
            if r['result'] in ('deadlock', 'loop', 'missing'):
                bad.append(r)
        self.assertEqual(bad, [])


class TestDetectsBrokenGraphs(unittest.TestCase):
    def test_bad_goto(self):
        scenes = golden_scenes()
        scenes['s001']['choices'][0]['goto'] = 's999'
        tmp = build_game_dir(scenes)
        try:
            data, fatal = load_game(tmp)
            issues = validate(data, tmp)
            self.assertTrue(any('s999' in i.msg for i in issues))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unreachable_node(self):
        scenes = golden_scenes()
        scenes['s006'] = {'id': 's006', 'narration': '孤岛。',
                          'ending': {'type': 'neutral', 'title': '孤岛'}}
        tmp = build_game_dir(scenes)
        try:
            data, fatal = load_game(tmp)
            issues = validate(data, tmp)
            self.assertTrue(any('不可达' in i.msg for i in issues))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_undeclared_attr_ref(self):
        scenes = golden_scenes()
        scenes['s001']['choices'][0]['effects'] = {'attrs': {'ghost_attr': 1}}
        tmp = build_game_dir(scenes)
        try:
            data, fatal = load_game(tmp)
            issues = validate(data, tmp)
            self.assertTrue(any('ghost_attr' in i.msg for i in issues))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_deadlock_detected_by_smoke(self):
        # 所有选项都被 requires 锁死 → 冒烟应报死路
        scenes = golden_scenes()
        scenes['s001']['choices'] = [{'text': '需要100好感', 'goto': 's002',
                                      'requires': {'attrs': {'affection_suqing': {'gte': 100}}}}]
        tmp = build_game_dir(scenes)
        try:
            data, fatal = load_game(tmp)
            rng = __import__('random').Random(42)
            r = run_once(data['scenes.js']['scenes'], data['mode.js'], 's001', rng)
            self.assertEqual(r['result'], 'deadlock')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_too_few_endings(self):
        scenes = golden_scenes()
        del scenes['s004']
        del scenes['s005']
        scenes['s002']['choices'] = [{'text': '走向坏结局', 'goto': 's003'}]
        tmp = build_game_dir(scenes)
        try:
            data, fatal = load_game(tmp)
            issues = validate(data, tmp)
            self.assertTrue(any('少于配置下限' in i.msg for i in issues))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_exit_code(self):
        tmp = build_game_dir(golden_scenes())
        try:
            r = subprocess.run([sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
                                / 'validate_game.py'), str(tmp), '--json'],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            out = json.loads(r.stdout)
            self.assertTrue(out['ok'])
            self.assertEqual(out['error_count'], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
