# -*- coding: utf-8 -*-
"""端到端管线测试（mock LLM，无需 API key）。

跑完整 `python agent.py tests/fixtures/tiny_novel.txt --mock` 流程，
断言中间产物、游戏文件夹、打包产物与 QA 全绿。
"""
import json
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

try:
    subprocess.run(['node', '--version'], capture_output=True, check=True)
    HAS_NODE = True
except (FileNotFoundError, subprocess.CalledProcessError):
    HAS_NODE = False

ROOT = Path(__file__).resolve().parent.parent
BOOK_ID = 'tiny_novel'
# 显式 run-id：保证产物路径确定（生产运行时是 <book_id>_<时间戳> 自动生成）
RUN_ID = 'tiny_novel'
BAD_RUN_ID = 'tiny_novel_bad'
STATE_STAGES = ['ingest', 'detect', 'chunk', 'extract', 'characters', 'style',
                'game_init', 'design', 'generate', 'qa', 'illustrate', 'package']


class TestMockPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 清理历史产物，保证测试确定性
        for p in (ROOT / 'runtime' / RUN_ID, ROOT / 'games' / RUN_ID,
                  ROOT / 'archive' / f'{RUN_ID}.zip'):
            if p.exists():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        cls.result = subprocess.run(
            [sys.executable, str(ROOT / 'agent.py'),
             str(ROOT / 'tests' / 'fixtures' / 'tiny_novel.txt'), '--mock',
             '--run-id', RUN_ID],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        cls.log = cls.result.stdout + cls.result.stderr

    def test_process_exit_ok(self):
        self.assertEqual(self.result.returncode, 0,
                         f'agent 退出码非 0:\n{self.log[-3000:]}')

    def test_state_completed(self):
        state = json.loads((ROOT / 'runtime' / RUN_ID / 'state.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(state['done'], STATE_STAGES)
        self.assertEqual(state['plan_id'], 'short_novel')
        self.assertEqual(state['mode']['mode_id'], 'classic')
        self.assertEqual(state['run_id'], RUN_ID)

    def test_middle_products(self):
        wd = ROOT / 'runtime' / RUN_ID
        self.assertTrue((wd / 'chapters.json').exists())
        self.assertTrue((wd / 'chunks.json').exists())
        self.assertTrue((wd / 'characters.json').exists())
        self.assertTrue((wd / 'bible' / 'world.md').exists())
        self.assertTrue((wd / 'design' / 'brief.json').exists())
        self.assertTrue((wd / 'qa' / 'qa_report.json').exists())
        self.assertTrue((wd / 'mode.json').exists())
        # extract 阶段：模式特异性解构产物存在且素材非空
        extract = json.loads((wd / 'bible' / 'extract_classic.json')
                             .read_text(encoding='utf-8'))
        self.assertEqual(extract['mode_id'], 'classic')
        self.assertGreaterEqual(len(extract['items']), 1, '经典叙事应有场景单元素材')
        self.assertTrue(all(it.get('summary') for it in extract['items']),
                        '场景单元素材必须含 summary')
        chapters = json.loads((wd / 'chapters.json').read_text(encoding='utf-8'))
        self.assertEqual(len(chapters['chapters']), 3, '应识别出 3 个章节')

    def test_game_folder_valid(self):
        game_dir = ROOT / 'games' / RUN_ID
        for f in ('index.html', 'engine/engine.js', 'engine/theme.css',
                  'data/game.js', 'data/mode.js', 'data/characters.js',
                  'data/scenes.js', 'data/theme.js'):
            self.assertTrue((game_dir / f).exists(), f'缺少 {f}')
        r = subprocess.run(
            [sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
             / 'validate_game.py'), str(game_dir), '--json'],
            capture_output=True, text=True, cwd=str(ROOT))
        out = json.loads(r.stdout)
        self.assertTrue(out['ok'], f'游戏数据未通过结构校验:\n{out["issues"]}')
        self.assertEqual(out['error_count'], 0)
        # 场景数：5 个蓝图场景全部生成
        scenes = json.loads((game_dir / 'data' / 'scenes.js')
                            .read_text(encoding='utf-8').split('= ', 1)[1].rsplit(';', 1)[0])
        self.assertEqual(len(scenes['scenes']), 5)
        # theme 2.0：theme.js 只存 {"name": detect 主题 id}，无 colors/fonts 漂移
        theme = json.loads((game_dir / 'data' / 'theme.js')
                           .read_text(encoding='utf-8').split('= ', 1)[1].rsplit(';', 1)[0])
        self.assertEqual(theme, {'name': 'modern'},
                         f'theme.js 应为纯视觉开关（当前: {theme}）')

    def test_smoke_passes(self):
        game_dir = ROOT / 'games' / RUN_ID
        r = subprocess.run(
            [sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
             / 'smoke_test.py'), str(game_dir), '--runs', '30', '--json'],
            capture_output=True, text=True, cwd=str(ROOT))
        out = json.loads(r.stdout)
        self.assertTrue(out['ok'], f'冒烟未通过:\n{out["issues"]}')
        self.assertEqual(out['summary']['ending_rate'], '100%')
        self.assertEqual(out['summary']['unreachable_nodes'], [])

    def test_archive_zip(self):
        zip_path = ROOT / 'archive' / f'{RUN_ID}.zip'
        self.assertTrue(zip_path.exists())
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            for f in ('index.html', 'engine/engine.js', 'data/scenes.js', 'data/game.js'):
                self.assertIn(f, names)
            self.assertNotIn('assets/bg/.gitkeep', names, 'zip 不应包含 .gitkeep')

    def test_upload_registered_expiry(self):
        expiry = ROOT / 'runtime' / 'expiry.json'
        recs = json.loads(expiry.read_text(encoding='utf-8'))
        self.assertIn(RUN_ID, recs)
        self.assertIn('expires_at', recs[RUN_ID])

    def test_mock_image_assets(self):
        """mock 生图：MockImageClient 应产出真实 PNG 并回写 portrait 字段。"""
        game_dir = ROOT / 'games' / RUN_ID
        pngs = list((game_dir / 'assets' / 'characters').glob('*.png'))
        self.assertGreaterEqual(len(pngs), 1, 'mock 应生成至少 1 张角色立绘')
        for png in pngs:
            self.assertTrue(png.read_bytes().startswith(b'\x89PNG'), f'{png} 不是合法 PNG')
        chars = json.loads((game_dir / 'data' / 'characters.js')
                           .read_text(encoding='utf-8').split('= ', 1)[1].rsplit(';', 1)[0])
        portraits = [c for c in chars['characters'] if c.get('portrait')]
        self.assertEqual(len(portraits), len(chars['characters']),
                         '每个角色都应回写 portrait 指向已生成的立绘')

    @unittest.skipUnless(HAS_NODE, '需要 node 运行时')
    def test_engine_selftest(self):
        """真实引擎代码 + 真实数据文件：随机游玩 25 次应无运行时错误。"""
        r = subprocess.run(['node', str(ROOT / 'tests' / 'engine_selftest.js'),
                            str(ROOT / 'games' / RUN_ID)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, f'引擎自检失败:\n{r.stdout}\n{r.stderr}')
        self.assertIn('ENGINE_SELFTEST_OK', r.stdout)

    @unittest.skipUnless(HAS_NODE, '需要 node 运行时')
    def test_fate_engine_flow(self):
        """fate 模式完整开局流程：分配 → 转生抽取 → 命运事件抽取 → 结局。"""
        r = subprocess.run(['node', str(ROOT / 'tests' / 'fate_engine_test.js')],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, f'fate 引擎流程失败:\n{r.stdout}\n{r.stderr}')
        self.assertIn('FATE_ENGINE_TEST_OK', r.stdout)

    @unittest.skipUnless(HAS_NODE, '需要 node 运行时')
    def test_mode_engine_flows(self):
        """survival 死亡判定 + riddle 逐字揭示 引擎交互流程。"""
        r = subprocess.run(['node', str(ROOT / 'tests' / 'engine_flow_test.js')],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, f'模式引擎流程失败:\n{r.stdout}\n{r.stderr}')
        self.assertIn('ENGINE_FLOW_TEST_OK', r.stdout)


class TestRepairLoopOnBadData(unittest.TestCase):
    """坏数据 → repair 修复循环：多轮失败后应标记 degraded 且不崩溃。"""

    @classmethod
    def setUpClass(cls):
        for p in (ROOT / 'runtime' / BAD_RUN_ID, ROOT / 'games' / BAD_RUN_ID,
                  ROOT / 'archive' / f'{BAD_RUN_ID}.zip'):
            if p.exists():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        cls.result = subprocess.run(
            [sys.executable, str(ROOT / 'agent.py'),
             str(ROOT / 'tests' / 'fixtures' / 'tiny_novel.txt'), '--mock',
             '--run-id', BAD_RUN_ID,
             '--mock-dir', str(ROOT / 'tests' / 'fixtures' / 'mock_data_bad')],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        cls.log = cls.result.stdout + cls.result.stderr

    def test_pipeline_does_not_crash(self):
        self.assertEqual(self.result.returncode, 0,
                         f'agent 崩溃:\n{self.log[-3000:]}')
        state = json.loads((ROOT / 'runtime' / BAD_RUN_ID / 'state.json')
                           .read_text(encoding='utf-8'))
        self.assertIn('qa', state['done'])
        self.assertIn('package', state['done'])

    def test_qa_marked_degraded(self):
        report = json.loads((ROOT / 'runtime' / BAD_RUN_ID / 'qa' / 'qa_report.json')
                            .read_text(encoding='utf-8'))
        self.assertTrue(report['degraded'], '坏数据下 QA 应标记 degraded')
        self.assertEqual(report['round'], 4)  # 1 轮 + 3 次修复递归

    def test_qa_report_records_issues(self):
        report = json.loads((ROOT / 'runtime' / BAD_RUN_ID / 'qa' / 'qa_report.json')
                            .read_text(encoding='utf-8'))
        self.assertFalse(report['ok'])
        self.assertGreater(len(report['issues']), 0)


class TestLongNovelMockPipeline(unittest.TestCase):
    """长篇小说（≥8 万字）路径：summarize 逐卷摘要 + generate 分批生成。"""

    LONG_BOOK = 'long_novel'
    LONG_RUN_ID = 'long_novel'

    @classmethod
    def setUpClass(cls):
        for p in (ROOT / 'runtime' / cls.LONG_RUN_ID, ROOT / 'games' / cls.LONG_RUN_ID,
                  ROOT / 'archive' / f'{cls.LONG_RUN_ID}.zip'):
            if p.exists():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        cls.result = subprocess.run(
            [sys.executable, str(ROOT / 'agent.py'),
             str(ROOT / 'tests' / 'fixtures' / 'long_novel.txt'), '--mock',
             '--run-id', cls.LONG_RUN_ID,
             '--mock-dir', str(ROOT / 'tests' / 'fixtures' / 'mock_data_long')],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        cls.log = cls.result.stdout + cls.result.stderr

    def test_long_plan_and_summarize(self):
        self.assertEqual(self.result.returncode, 0,
                         f'agent 退出码非 0:\n{self.log[-3000:]}')
        state = json.loads((ROOT / 'runtime' / self.LONG_RUN_ID / 'state.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(state['plan_id'], 'long_novel')
        self.assertIn('summarize', state['done'])
        summaries = (ROOT / 'runtime' / self.LONG_RUN_ID / 'bible' / 'summaries.md')
        self.assertTrue(summaries.exists())
        self.assertIn('块', summaries.read_text(encoding='utf-8'))

    def test_long_design_used_custom_fixture(self):
        """mock-dir 应覆盖默认 fixture：15 场景蓝图而非默认 5 场景。"""
        brief = json.loads((ROOT / 'runtime' / self.LONG_RUN_ID / 'design' / 'brief.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(brief['game_title'], '山茶书')
        self.assertEqual(len(brief['scene_blueprint']), 15)

    def test_long_game_valid(self):
        game_dir = ROOT / 'games' / self.LONG_RUN_ID
        r = subprocess.run(
            [sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
             / 'validate_game.py'), str(game_dir), '--json'],
            capture_output=True, text=True, cwd=str(ROOT))
        out = json.loads(r.stdout)
        self.assertTrue(out['ok'], f'长篇小说游戏未通过校验:\n{out["issues"]}')
        scenes = json.loads((game_dir / 'data' / 'scenes.js')
                            .read_text(encoding='utf-8').split('= ', 1)[1].rsplit(';', 1)[0])
        self.assertEqual(len(scenes['scenes']), 15, '15 个蓝图场景应全部生成')

    def test_long_zip(self):
        self.assertTrue((ROOT / 'archive' / f'{self.LONG_RUN_ID}.zip').exists())


class TestFateMockPipeline(unittest.TestCase):
    """fate 模式端到端：定制 mock fixtures 驱动全流程，验证命运事件池落盘。"""

    FATE_RUN_ID = 'tiny_novel_fate'
    FATE_MOCK_DIR = 'mock_data_fate'

    @classmethod
    def setUpClass(cls):
        for p in (ROOT / 'runtime' / cls.FATE_RUN_ID,
                  ROOT / 'games' / cls.FATE_RUN_ID,
                  ROOT / 'archive' / f'{cls.FATE_RUN_ID}.zip'):
            if p.exists():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        cls.result = subprocess.run(
            [sys.executable, str(ROOT / 'agent.py'),
             str(ROOT / 'tests' / 'fixtures' / 'tiny_novel.txt'), '--mock',
             '--run-id', cls.FATE_RUN_ID,
             '--mock-dir', str(ROOT / 'tests' / 'fixtures' / cls.FATE_MOCK_DIR)],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        cls.log = cls.result.stdout + cls.result.stderr

    def test_fate_process_exit_ok(self):
        self.assertEqual(self.result.returncode, 0,
                         f'agent 退出码非 0:\n{self.log[-3000:]}')
        state = json.loads((ROOT / 'runtime' / self.FATE_RUN_ID / 'state.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(state['mode']['mode_id'], 'fate')
        self.assertEqual(state['mode']['theme_id'], 'ancient')
        self.assertEqual(state['done'], STATE_STAGES)

    def test_fate_extract_materials(self):
        """fate 模式解构产物应含转生池与命运事件素材。"""
        extract = json.loads((ROOT / 'runtime' / self.FATE_RUN_ID
                              / 'bible' / 'extract_fate.json')
                             .read_text(encoding='utf-8'))
        kinds = {it.get('kind') for it in extract['items']}
        self.assertIn('pool', kinds, '转生身份池素材缺失')
        self.assertIn('event', kinds, '命运事件素材缺失')
        self.assertGreaterEqual(len(extract['items']), 3)

    def test_fate_game_valid_and_events_persisted(self):
        """命运事件池应随 scenes 一起落盘（apply_patch 顶层 events 契约）。"""
        game_dir = ROOT / 'games' / self.FATE_RUN_ID
        r = subprocess.run(
            [sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
             / 'validate_game.py'), str(game_dir), '--json'],
            capture_output=True, text=True, cwd=str(ROOT))
        out = json.loads(r.stdout)
        self.assertTrue(out['ok'], f'fate 游戏未通过结构校验:\n{out["issues"]}')
        self.assertEqual(out['error_count'], 0)
        scenes = json.loads((game_dir / 'data' / 'scenes.js')
                            .read_text(encoding='utf-8').split('= ', 1)[1].rsplit(';', 1)[0])
        self.assertEqual(len(scenes['scenes']), 7, '7 个蓝图场景应全部生成')
        self.assertGreaterEqual(len(scenes['events']), 2, '命运事件池不应为空')
        self.assertEqual({e['id'] for e in scenes['events']},
                         {'evt_001', 'evt_002'})
        # draw_pool 转生身份也完整落盘
        mode = json.loads((game_dir / 'data' / 'mode.js')
                          .read_text(encoding='utf-8').split('= ', 1)[1].rsplit(';', 1)[0])
        self.assertTrue(mode['fate']['enabled'])
        self.assertEqual(len(mode['fate']['draw_pool']), 2)
        # theme 2.0：fate 主题 ancient 来自 detect，patch 里的 theme 键被丢弃不落盘
        theme = json.loads((game_dir / 'data' / 'theme.js')
                           .read_text(encoding='utf-8').split('= ', 1)[1].rsplit(';', 1)[0])
        self.assertEqual(theme, {'name': 'ancient'},
                         f'theme.js 应为纯视觉开关（当前: {theme}）')

    def test_fate_smoke_passes(self):
        """fate 多起点冒烟：两条转生线都应能走到结局。"""
        game_dir = ROOT / 'games' / self.FATE_RUN_ID
        r = subprocess.run(
            [sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
             / 'smoke_test.py'), str(game_dir), '--runs', '30', '--json'],
            capture_output=True, text=True, cwd=str(ROOT))
        out = json.loads(r.stdout)
        self.assertTrue(out['ok'], f'fate 冒烟未通过:\n{out["issues"]}')
        self.assertEqual(out['summary']['ending_rate'], '100%')
        self.assertEqual(out['summary']['unreachable_nodes'], [])

    def test_fate_qa_report_ok(self):
        report = json.loads((ROOT / 'runtime' / self.FATE_RUN_ID
                             / 'qa' / 'qa_report.json')
                            .read_text(encoding='utf-8'))
        self.assertTrue(report['ok'], f'QA 应一次通过:\n{report["issues"]}')

    def test_fate_archive_zip(self):
        self.assertTrue((ROOT / 'archive' / f'{self.FATE_RUN_ID}.zip').exists())

    @unittest.skipUnless(HAS_NODE, '需要 node 运行时')
    def test_fate_engine_selftest(self):
        """引擎自检以 draw_pool start_scenes 为起点（不含空壳 entry）。"""
        r = subprocess.run(['node', str(ROOT / 'tests' / 'engine_selftest.js'),
                            str(ROOT / 'games' / self.FATE_RUN_ID)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, f'fate 引擎自检失败:\n{r.stdout}\n{r.stderr}')
        self.assertIn('ENGINE_SELFTEST_OK', r.stdout)


class TestGameLibraryIsolation(unittest.TestCase):
    """games/ 是游戏库：每次调用一个独立文件夹，同书多次运行互不覆盖。"""

    RUN1 = 'iso_run1'
    RUN2 = 'iso_run2'

    @classmethod
    def setUpClass(cls):
        for rid in (cls.RUN1, cls.RUN2):
            for p in (ROOT / 'runtime' / rid, ROOT / 'games' / rid,
                      ROOT / 'archive' / f'{rid}.zip'):
                if p.exists():
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
        # 清理历史自动 run-id 目录（tiny_novel_<时间戳>）
        for d in list((ROOT / 'runtime').glob('tiny_novel_*')):
            if d.is_dir():
                shutil.rmtree(d)

    def test_two_runs_coexist(self):
        """显式 --run-id：两次调用生成两个互不覆盖的游戏文件夹。"""
        for rid in (self.RUN1, self.RUN2):
            r = subprocess.run(
                [sys.executable, str(ROOT / 'agent.py'),
                 str(ROOT / 'tests' / 'fixtures' / 'tiny_novel.txt'), '--mock',
                 '--run-id', rid],
                capture_output=True, text=True, timeout=600, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0,
                             f'run {rid} 失败:\n{r.stdout[-2000:]}{r.stderr[-2000:]}')
            game_dir = ROOT / 'games' / rid
            self.assertTrue((game_dir / 'index.html').exists(), f'{rid} 游戏缺失')
            self.assertTrue((game_dir / 'data' / 'scenes.js').exists())
        self.assertNotEqual(ROOT / 'games' / self.RUN1, ROOT / 'games' / self.RUN2)

    def test_default_run_id_auto_suffix_and_resume(self):
        """不传 --run-id：自动生成 <book_id>_<时间戳>；--resume 自动定位最近一次。"""
        r = subprocess.run(
            [sys.executable, str(ROOT / 'agent.py'),
             str(ROOT / 'tests' / 'fixtures' / 'tiny_novel.txt'), '--mock'],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, f'自动 run_id 运行失败:\n{r.stdout[-2000:]}{r.stderr[-2000:]}')

        auto_dirs = sorted(d.name for d in (ROOT / 'runtime').iterdir()
                           if d.name.startswith('tiny_novel_') and d.name != 'tiny_novel_bad')
        self.assertEqual(len(auto_dirs), 1, f'应恰好自动生成一个运行目录: {auto_dirs}')
        latest = auto_dirs[0]

        r = subprocess.run(
            [sys.executable, str(ROOT / 'agent.py'),
             str(ROOT / 'tests' / 'fixtures' / 'tiny_novel.txt'), '--mock',
             '--resume'],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, f'resume 失败:\n{r.stdout[-2000:]}{r.stderr[-2000:]}')
        # resume 应全部跳过（已完成），并定位到自动生成的那次运行
        self.assertIn('全部阶段完成', r.stdout)
        self.assertIn(latest, r.stdout)
        for stage in ('ingest', 'package'):
            self.assertIn(f'[跳过] {stage}', r.stdout)


if __name__ == '__main__':
    unittest.main()
