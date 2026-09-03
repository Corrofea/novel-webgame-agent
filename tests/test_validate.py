# -*- coding: utf-8 -*-
"""validate_game.py / smoke_test.py 图校验测试。

用 mock 黄金数据搭建游戏目录，验证：
  1) 黄金数据通过全部校验（回归护栏）
  2) 各类破坏性改动（坏 goto / 孤立节点 / 死路 / 重复结局）能被检出
"""
import json
import random
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


def build_game_dir(scenes, mode=None, game=None, characters=None, events=None) -> Path:
    """用黄金数据 + 覆盖场景，搭一个临时游戏目录。

    events: fate 命运事件池（scenes.js 顶层 events 字段）。
    """
    tmp = Path(tempfile.mkdtemp(prefix='nwa_test_'))
    data_dir = tmp / 'data'
    data_dir.mkdir(parents=True)
    # 引擎契约检查需要 engine/engine.js 存在：从仓库模板复制（单一来源）
    (tmp / 'engine').mkdir(parents=True)
    (tmp / 'engine' / 'engine.js').write_bytes(
        (ROOT / 'engine' / 'engine.js').read_bytes())
    (tmp / 'engine' / 'theme.css').write_bytes(
        (ROOT / 'engine' / 'theme.css').read_bytes())
    mode = mode or {
        'mode_id': 'classic', 'mode_name': '经典叙事', 'mechanics': ['choices', 'affection'],
        'attributes': [{'id': 'affection_suqing', 'label': '苏晴好感', 'min': 0, 'max': 100,
                        'start': 0, 'visible': True}],
        'inventory': {'enabled': False, 'items': []}, 'panels': ['status', 'choices'],
        'perspectives': [], 'achievements': {'enabled': False, 'list': []},
        'commentary': {'enabled': False}, 'endings': {'min': 2, 'max': 5},
    }
    game = game or {'title': '测试', 'book_id': 'test_book', 'mode': 'classic',
                    'entry': 's001', 'save_key': 'nwa_test', 'version': '0.1.0'}
    characters = characters or {'characters': [
        {'id': 'c_mo', 'name': '陈默', 'aliases': [], 'role': '主角'}]}
    scenes_obj = {'scenes': scenes}
    if events is not None:
        scenes_obj['events'] = events
    objs = {'game': game, 'mode': mode, 'characters': characters,
            'scenes': scenes_obj, 'theme': {'name': 'modern', 'colors': {}}}
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


class TestShapeDriftTolerance(unittest.TestCase):
    """LLM 输出 shape 漂移（字符串代替对象）不崩溃，且报可修错误（回归：真实 API 暴露）。"""

    def test_string_endings_reported_not_crashed(self):
        scenes = {
            's001': {'id': 's001', 'narration': '开始。', 'choices': [
                {'text': '去', 'goto': 's002'}, {'text': '走', 'goto': 's003'}]},
            's002': {'id': 's002', 'narration': '好结局。', 'ending': 'good'},
            's003': {'id': 's003', 'narration': '坏结局。', 'ending': 'bad'},
        }
        tmp = build_game_dir(scenes)
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            self.assertEqual(data['scenes.js']['scenes']['s002']['ending']['title'], 'good',
                             '字符串 ending 应规范化为对象')
            issues = validate(data, tmp)
            self.assertTrue(any('应为对象' in i.msg for i in issues), issues)
            # CLI 不崩溃、报错误（修复循环据此修复数据源）
            r = subprocess.run([sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
                                / 'validate_game.py'), str(tmp), '--json'],
                               capture_output=True, text=True)
            self.assertNotIn('Traceback', r.stdout + r.stderr)
            out = json.loads(r.stdout)
            self.assertFalse(out['ok'])
            self.assertGreater(out['error_count'], 0)
            # smoke 同样不崩溃
            r2 = subprocess.run([sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
                                 / 'smoke_test.py'), str(tmp), '--json'],
                                capture_output=True, text=True)
            self.assertNotIn('Traceback', r2.stdout + r2.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_string_choice_normalized(self):
        scenes = {
            's001': {'id': 's001', 'narration': '开始。', 'choices': ['直接走', '回头']},
            's002': {'id': 's002', 'narration': '终。', 'ending': {'type': 'good', 'title': '好'}},
        }
        tmp = build_game_dir(scenes)
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            self.assertEqual(data['scenes.js']['scenes']['s001']['choices'][0],
                             {'text': '直接走', 'goto': ''})
            issues = validate(data, tmp)
            self.assertTrue(any('选项 0 应为对象' in i.msg for i in issues), issues)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_string_auto_and_riddle_normalized(self):
        scenes = {
            's001': {'id': 's001', 'narration': '开始。', 'auto': 's002'},
            's002': {'id': 's002', 'narration': '谜面。', 'riddle': '谁'},
        }
        tmp = build_game_dir(scenes)
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            node = data['scenes.js']['scenes']
            self.assertEqual(node['s001']['auto'], {})
            self.assertEqual(node['s002']['riddle'], {})
            issues = validate(data, tmp)
            msgs = [i.msg for i in issues]
            self.assertTrue(any('auto 应为对象' in m for m in msgs), msgs)
            self.assertTrue(any('riddle 应为对象' in m for m in msgs), msgs)
            # smoke 不崩溃（auto/riddle 空对象被跳过，走死路检测）
            r = subprocess.run([sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
                                 / 'smoke_test.py'), str(tmp), '--json'],
                               capture_output=True, text=True)
            self.assertNotIn('Traceback', r.stdout + r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_auto_list_reported_not_crashed(self):
        """auto 写成多路分支数组（2026-09 狂人日记 s013 实录）→ 报可修复错误而非崩溃。

        引擎只支持单个 auto 路由；校验器曾对数组直接 .get 崩溃，导致 QA 修复循环
        永远拿不到可读错误（AttributeError 进错误流），游戏卡死在 qa 阶段。
        """
        scenes = {
            's001': {'id': 's001', 'narration': '开始。', 'auto': [
                {'requires': {'courage': {'min': 16}}, 'goto': 's002'},
                {'goto': 's003'}]},
            's002': {'id': 's002', 'narration': '高勇气。', 'ending': {'type': 'good', 'title': '好'}},
            's003': {'id': 's003', 'narration': '默认。', 'ending': {'type': 'neutral', 'title': '平'}},
        }
        tmp = build_game_dir(scenes)
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            issues = validate(data, tmp)
            msgs = [i.msg for i in issues]
            self.assertTrue(any('只支持单个 auto' in m for m in msgs), msgs)
            # CLI 与 smoke 不崩溃
            for script in ('validate_game.py', 'smoke_test.py'):
                r = subprocess.run([sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
                                     / script), str(tmp), '--json'],
                                   capture_output=True, text=True)
                self.assertNotIn('Traceback', r.stdout + r.stderr, script)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def narrative_scenes():
    """narrative 黄金数据：两章，各章分支汇聚到章节锚点。"""
    return {
        's001': {'id': 's001', 'chapter': '第一章', 'narration': '初入。', 'choices': [
            {'text': '左路', 'goto': 's002'}, {'text': '右路', 'goto': 's003'}]},
        's002': {'id': 's002', 'chapter': '第一章', 'narration': '左路发展。', 'choices': [
            {'text': '汇合', 'goto': 's004'}]},
        's003': {'id': 's003', 'chapter': '第一章', 'narration': '右路发展。', 'choices': [
            {'text': '汇合', 'goto': 's004'}]},
        's004': {'id': 's004', 'chapter': '第一章', 'chapter_end': True,
                 'narration': '第一章终。', 'choices': [{'text': '进入第二章', 'goto': 's005'}]},
        's005': {'id': 's005', 'chapter': '第二章', 'narration': '第二章。', 'choices': [
            {'text': '好结局', 'goto': 's006'}, {'text': '坏结局', 'goto': 's007'}]},
        's006': {'id': 's006', 'chapter': '第二章', 'narration': '好结局。',
                 'ending': {'type': 'good', 'title': '好结局'}},
        's007': {'id': 's007', 'chapter': '第二章', 'narration': '坏结局。',
                 'ending': {'type': 'bad', 'title': '坏结局'}},
    }


def narrative_mode():
    return {
        'mode_id': 'narrative', 'mode_name': '章节叙事', 'mechanics': ['choices', 'chapter_progress'],
        'attributes': [], 'inventory': {'enabled': False, 'items': []},
        'panels': ['status', 'choices'], 'perspectives': [],
        'achievements': {'enabled': False, 'list': []}, 'commentary': {'enabled': False},
        'chapter_progress': {'enabled': True, 'chapters': ['第一章', '第二章']},
        'endings': {'min': 2, 'max': 3},
    }


class TestNarrativeChapterStructure(unittest.TestCase):
    """章节叙事模式：锚点/章节字段/跨章跳转检查。"""

    def _issues(self, scenes, mode=None):
        tmp = build_game_dir(scenes, mode=mode or narrative_mode())
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            return validate(data, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_good_narrative_passes(self):
        issues = self._issues(narrative_scenes())
        errors = [i for i in issues if i.severity == 'error']
        self.assertEqual(errors, [], [str(i) for i in errors])

    def test_missing_chapter_field_warns(self):
        scenes = narrative_scenes()
        del scenes['s001']['chapter']
        issues = self._issues(scenes)
        self.assertTrue(any('缺 chapter' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_no_anchor_warns(self):
        scenes = narrative_scenes()
        for n in scenes.values():
            n.pop('chapter_end', None)
        issues = self._issues(scenes)
        self.assertTrue(any('没有任何 chapter_end 锚点' in i.msg for i in issues))

    def test_cross_chapter_jump_warns(self):
        scenes = narrative_scenes()
        # s002 直接跳到第二章 s005（应经 s004 锚点）
        scenes['s002']['choices'][0]['goto'] = 's005'
        issues = self._issues(scenes)
        self.assertTrue(any('跳到其他章节' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_chapter_progress_disabled_skips_checks(self):
        mode = narrative_mode()
        mode['chapter_progress']['enabled'] = False
        scenes = narrative_scenes()
        for n in scenes.values():
            n.pop('chapter', None)
        issues = self._issues(scenes, mode)
        self.assertFalse(any('章节模式' in i.msg for i in issues))


def galgame_scenes():
    """galgame 黄金数据：共通线 → 好感度门槛 → 路线结局。"""
    return {
        's001': {'id': 's001', 'speaker': '陈默', 'narration': '转角遇见了她。', 'choices': [
            {'text': '帮她捡起书', 'goto': 's002', 'effects': {'attrs': {'affection_lin': 5}}},
            {'text': '只是路过', 'goto': 's003', 'effects': {'attrs': {'affection_lin': -3}}}]},
        's002': {'id': 's002', 'speaker': '林小满', 'narration': '谢谢你！', 'choices': [
            {'text': '告白（好感≥10）', 'goto': 's004',
             'requires': {'attrs': {'affection_lin': {'gte': 10}}}},
            {'text': '先当朋友', 'goto': 's005', 'effects': {'attrs': {'affection_lin': 5}}}]},
        's003': {'id': 's003', 'speaker': '林小满', 'narration': '……', 'choices': [
            {'text': '再追上去', 'goto': 's002', 'effects': {'attrs': {'affection_lin': 2}}}]},
        's004': {'id': 's004', 'speaker': '林小满', 'narration': '我也喜欢你！',
                 'ending': {'type': 'good', 'title': '恋爱达成'}},
        's005': {'id': 's005', 'speaker': '林小满', 'narration': '那就一直做朋友吧。',
                 'ending': {'type': 'neutral', 'title': '友达以上'}},
    }


def galgame_mode():
    return {
        'mode_id': 'galgame', 'mode_name': '恋爱养成', 'mechanics': ['choices', 'affection', 'galgame'],
        'attributes': [{'id': 'affection_lin', 'label': '林小满好感', 'min': 0, 'max': 100,
                        'start': 0, 'visible': True}],
        'inventory': {'enabled': False, 'items': []}, 'panels': ['status', 'choices'],
        'perspectives': [], 'achievements': {'enabled': False, 'list': []},
        'commentary': {'enabled': False}, 'galgame': {'enabled': True},
        'endings': {'min': 2, 'max': 5},
    }


class TestGalgameMode(unittest.TestCase):
    """恋爱养成：好感度门槛 + 对话驱动数据应通过校验。"""

    def _issues(self, scenes, mode=None):
        tmp = build_game_dir(scenes, mode=mode or galgame_mode())
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            return validate(data, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_galgame_golden_passes(self):
        issues = self._issues(galgame_scenes())
        errors = [i for i in issues if i.severity == 'error']
        self.assertEqual(errors, [], [str(i) for i in errors])

    def test_galgame_unreachable_gate_detected(self):
        """好感度门槛无法达成（门槛 50 > 最大累积 12）→ warning 检出。"""
        scenes = galgame_scenes()
        scenes['s002']['choices'][0]['requires'] = {'attrs': {'affection_lin': {'gte': 50}}}
        issues = self._issues(scenes)
        self.assertTrue(any('门槛' in i.msg and '无法达成' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_galgame_reachable_gate_no_warning(self):
        """门槛可达（10 ≤ 全图正增量 10）→ 不报门槛 warning。"""
        scenes = galgame_scenes()
        issues = self._issues(scenes)
        self.assertFalse(any('无法达成' in i.msg for i in issues),
                        [str(i) for i in issues])


def survival_scenes():
    """survival 黄金数据：决策消耗/恢复生命值，死亡场景 + 通关终点。"""
    return {
        's001': {'id': 's001', 'narration': '营地出发，前方是垭口。', 'choices': [
            {'text': '稳妥路线（耗时）', 'goto': 's002', 'effects': {'attrs': {'hp': -5}}},
            {'text': '捷径（危险）', 'goto': 's003', 'effects': {'attrs': {'hp': -15}}}]},
        's002': {'id': 's002', 'narration': '绕路遇到补给站，恢复体力。', 'choices': [
            {'text': '休息后前进', 'goto': 's004', 'effects': {'attrs': {'hp': 10}}}]},
        's003': {'id': 's003', 'narration': '捷径遇险，勉强脱身。', 'choices': [
            {'text': '继续前进', 'goto': 's004', 'effects': {'attrs': {'hp': -5}}}]},
        's004': {'id': 's004', 'narration': '抵达终点！',
                 'ending': {'type': 'good', 'title': '穿越成功'}},
        's005': {'id': 's005', 'narration': '你倒在了风雪中……',
                 'ending': {'type': 'bad', 'title': '长眠垭口'}},
    }


def survival_mode():
    return {
        'mode_id': 'survival', 'mode_name': '生存试炼', 'mechanics': ['choices', 'survival'],
        'attributes': [{'id': 'hp', 'label': '生命值', 'min': 0, 'max': 100, 'start': 100,
                        'visible': True},
                       {'id': 'food', 'label': '食物', 'min': 0, 'max': 30, 'start': 0,
                        'visible': True}],
        'inventory': {'enabled': True, 'items': []}, 'panels': ['status', 'choices'],
        'perspectives': [], 'achievements': {'enabled': False, 'list': []},
        'commentary': {'enabled': False},
        'survival': {'enabled': True, 'alloc_points': 30, 'alloc_attrs': ['food'],
                     'hp_attr': 'hp', 'death_threshold': 0, 'death_scene': 's005'},
        'endings': {'min': 2, 'max': 5},
    }


class TestSurvivalMode(unittest.TestCase):
    """生存试炼：生命值死亡判定配置 + 通关可达性。"""

    def _issues(self, scenes, mode=None):
        tmp = build_game_dir(scenes, mode=mode or survival_mode())
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            return validate(data, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_survival_golden_passes(self):
        issues = self._issues(survival_scenes())
        errors = [i for i in issues if i.severity == 'error']
        self.assertEqual(errors, [], [str(i) for i in errors])

    def test_missing_death_scene_is_error(self):
        mode = survival_mode()
        mode['survival']['death_scene'] = ''
        issues = self._issues(survival_scenes(), mode)
        self.assertTrue(any('death_scene' in i.msg for i in issues))

    def test_death_scene_must_be_ending(self):
        mode = survival_mode()
        scenes = survival_scenes()
        del scenes['s005']['ending']          # 死亡节点不再是结局
        scenes['s005']['choices'] = [{'text': '爬不起来', 'goto': 's005'}]
        issues = self._issues(scenes, mode)
        self.assertTrue(any('必须是结局节点' in i.msg for i in issues))

    def test_bad_hp_attr_is_error(self):
        mode = survival_mode()
        mode['survival']['hp_attr'] = 'hp_ghost'
        issues = self._issues(survival_scenes(), mode)
        self.assertTrue(any('hp_attr' in i.msg for i in issues))

    def test_alloc_attr_must_be_defined(self):
        mode = survival_mode()
        mode['survival']['alloc_attrs'] = ['ghost_food']
        issues = self._issues(survival_scenes(), mode)
        self.assertTrue(any('alloc_attrs' in i.msg for i in issues))


def riddle_scenes():
    """riddle 黄金数据：序 → 谜题（猜人物名）→ 最终谜（猜书名）→ 通关。"""
    return {
        's001': {'id': 's001', 'narration': '你翻开泛黄的卷册。', 'riddle': {
            'question': '衔玉而生的神瑛侍者，住在哪里？',
            'answer': '贾宝玉', 'hints': ['此乃红楼第一主角', '衔玉而生'],
            'goto': 's002'}},
        's002': {'id': 's002', 'narration': '答对了。最后一问。', 'riddle': {
            'question': '这部写满痴男怨女的书，书名是什么？',
            'answer': '红楼梦', 'hints': ['前八十回为曹雪芹所作'],
            'goto': 's003'}},
        's003': {'id': 's003', 'narration': '大观园的门为你敞开。',
                 'ending': {'type': 'good', 'title': '解谜通关'}},
    }


def riddle_mode():
    return {
        'mode_id': 'riddle', 'mode_name': '字谜问答', 'mechanics': ['riddle'],
        'attributes': [],
        'inventory': {'enabled': False, 'items': []}, 'panels': ['choices'],
        'perspectives': [], 'achievements': {'enabled': False, 'list': []},
        'commentary': {'enabled': False}, 'riddle': {'enabled': True},
        'endings': {'min': 1, 'max': 3},
    }


class TestRiddleMode(unittest.TestCase):
    """字谜问答：riddle 节点契约（question/answer/goto）校验 + 冒烟必解推进。"""

    def _issues(self, scenes, mode=None):
        tmp = build_game_dir(scenes, mode=mode or riddle_mode())
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            return validate(data, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_riddle_golden_passes(self):
        """纯 riddle 链（无任何 choices 节点）应通过：riddle 是合法节点类型。"""
        issues = self._issues(riddle_scenes())
        errors = [i for i in issues if i.severity == 'error']
        self.assertEqual(errors, [], [str(i) for i in errors])

    def test_missing_answer_is_error(self):
        scenes = riddle_scenes()
        del scenes['s001']['riddle']['answer']
        issues = self._issues(scenes)
        self.assertTrue(any('缺 answer' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_missing_goto_is_error(self):
        scenes = riddle_scenes()
        del scenes['s001']['riddle']['goto']
        issues = self._issues(scenes)
        self.assertTrue(any('缺 goto' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_bad_goto_is_error(self):
        scenes = riddle_scenes()
        scenes['s001']['riddle']['goto'] = 's_ghost'
        issues = self._issues(scenes)
        self.assertTrue(any('指向不存在的节点' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_answer_length_warns(self):
        scenes = riddle_scenes()
        scenes['s001']['riddle']['answer'] = '玉'
        issues = self._issues(scenes)
        self.assertTrue(any('2~8 字' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_smoke_treats_riddle_as_solvable(self):
        """冒烟模拟把 riddle 视为必解，沿 goto 推进到结局而非死路。"""
        tmp = build_game_dir(riddle_scenes(), mode=riddle_mode())
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            scenes = data['scenes.js']['scenes']
            mode = data['mode.js']
            res = run_once(scenes, mode, 's001', random.Random(1))
            self.assertEqual(res['result'], 'ending', res['msg'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def fate_scenes():
    """fate 黄金数据：空壳入口 + 两条转生线（第二条线含命运事件点）。"""
    return {
        's001': {'id': 's001', 'narration': '（抽取锚点：玩家不经过此节点）'},
        's101': {'id': 's101', 'narration': '你生在钟鸣鼎食之家。', 'choices': [
            {'text': '赴京赶考', 'goto': 's102'}]},
        's102': {'id': 's102', 'fate_event': True, 'narration': '命运的岔路。', 'choices': [
            {'text': '继续前行', 'goto': 's103'}]},
        's103': {'id': 's103', 'narration': '功成名就。',
                 'ending': {'type': 'good', 'title': '光耀门楣'}},
        's201': {'id': 's201', 'narration': '你出身寒门。', 'choices': [
            {'text': '参加乡试', 'goto': 's202'}]},
        's202': {'id': 's202', 'narration': '终老林泉。',
                 'ending': {'type': 'neutral', 'title': '归隐山林'}},
    }


def fate_mode():
    return {
        'mode_id': 'fate', 'mode_name': '命运轮回', 'mechanics': ['fate'],
        'attributes': [{'id': 'jiach', 'label': '家世', 'min': 0, 'max': 20, 'start': 0,
                        'visible': True},
                       {'id': 'caiqi', 'label': '才气', 'min': 0, 'max': 20, 'start': 0,
                        'visible': True}],
        'inventory': {'enabled': False, 'items': []}, 'panels': ['status', 'choices'],
        'perspectives': [], 'achievements': {'enabled': False, 'list': []},
        'commentary': {'enabled': False},
        'fate': {'enabled': True, 'alloc_points': 20, 'alloc_attrs': [],
                 'draw_pool': [
                     {'id': 'fate_rich', 'name': '钟鸣鼎食之子', 'desc': '生于豪族，锦衣玉食',
                      'start_scene': 's101', 'requires': {'attrs': {'jiach': {'gte': 5}}},
                      'effects': {'attrs': {'jiach': 2}}},
                     {'id': 'fate_scholar', 'name': '寒门书生', 'desc': '家徒四壁，唯书为伴',
                      'start_scene': 's201', 'requires': {'attrs': {'caiqi': {'gte': 5}}},
                      'effects': {'attrs': {'caiqi': 2}}}]},
        'endings': {'min': 2, 'max': 6},
    }


def fate_events():
    """命运事件池：按禀赋过滤，goto 指回主线节点。"""
    return [
        {'id': 'evt_001', 'title': '长辈赠金', 'narration': '族中长辈送了一笔盘缠。',
         'requires': {'attrs': {'jiach': {'gte': 3}}}, 'effects': {'attrs': {'jiach': 1}},
         'goto': 's102'},
        {'id': 'evt_002', 'title': '诗会夺魁', 'narration': '乡试诗会上一鸣惊人。',
         'requires': {'attrs': {'caiqi': {'gte': 4}}}, 'effects': {'attrs': {'caiqi': 1}},
         'goto': 's103'},
    ]


class TestFateMode(unittest.TestCase):
    """命运轮回：转生池契约 + 事件池校验 + 空壳入口豁免 + 多源可达。"""

    def _issues(self, scenes=None, mode=None, events=None):
        tmp = build_game_dir(scenes or fate_scenes(), mode=mode or fate_mode(),
                             events=fate_events() if events is None else events)
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            return validate(data, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fate_golden_passes(self):
        """空壳入口 + 双转生线 + 事件池：无 error；入口豁免死路检查。"""
        issues = self._issues()
        errors = [i for i in issues if i.severity == 'error']
        self.assertEqual(errors, [], [str(i) for i in errors])
        self.assertFalse(any('没有 choices' in i.msg for i in issues),
                        'fate 空壳入口不应报缺节点类型')

    def test_start_scene_missing_is_error(self):
        mode = fate_mode()
        mode['fate']['draw_pool'][0]['start_scene'] = 's_ghost'
        issues = self._issues(mode=mode)
        self.assertTrue(any('start_scene' in i.msg and '不存在' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_event_bad_goto_is_error(self):
        events = fate_events()
        events[0]['goto'] = 's_ghost'
        issues = self._issues(events=events)
        self.assertTrue(any('事件' in i.msg and '指向不存在' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_event_undeclared_attr_is_error(self):
        events = fate_events()
        events[0]['requires'] = {'attrs': {'ghost_attr': {'gte': 3}}}
        issues = self._issues(events=events)
        self.assertTrue(any('未定义的属性' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_pool_too_small_warns(self):
        mode = fate_mode()
        mode['fate']['draw_pool'] = mode['fate']['draw_pool'][:1]
        issues = self._issues(mode=mode)
        self.assertTrue(any('draw_pool' in i.msg and '至少' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_no_fate_event_node_warns(self):
        scenes = fate_scenes()
        del scenes['s102']['fate_event']
        issues = self._issues(scenes=scenes)
        self.assertTrue(any('fate_event' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_events_without_fate_warns(self):
        mode = fate_mode()
        mode['fate']['enabled'] = False
        issues = self._issues(mode=mode)
        self.assertTrue(any('未启用' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_entry_shell_error_when_not_fate(self):
        """非 fate 模式下空壳入口必须报错（豁免仅限 fate 模式）。"""
        mode = fate_mode()
        mode['fate']['enabled'] = False
        issues = self._issues(mode=mode)
        self.assertTrue(any('没有 choices' in i.msg for i in issues),
                        [str(i) for i in issues])

    def test_smoke_multi_source_start(self):
        """冒烟：起点取转生身份线（绕过空壳入口），应到达结局而非死路。"""
        tmp = build_game_dir(fate_scenes(), mode=fate_mode(), events=fate_events())
        try:
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            scenes = data['scenes.js']['scenes']
            mode = data['mode.js']
            for start in ('s101', 's201'):
                res = run_once(scenes, mode, start, random.Random(1))
                self.assertEqual(res['result'], 'ending', f'{start}: {res["msg"]}')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
