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
STATE_STAGES = ['ingest', 'detect', 'chunk', 'characters', 'style', 'game_init',
                'design', 'generate', 'qa', 'illustrate', 'package']


class TestMockPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 清理历史产物，保证测试确定性
        for p in (ROOT / 'runtime' / BOOK_ID, ROOT / 'games' / BOOK_ID,
                  ROOT / 'archive' / f'{BOOK_ID}.zip'):
            if p.exists():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        cls.result = subprocess.run(
            [sys.executable, str(ROOT / 'agent.py'),
             str(ROOT / 'tests' / 'fixtures' / 'tiny_novel.txt'), '--mock'],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        cls.log = cls.result.stdout + cls.result.stderr

    def test_process_exit_ok(self):
        self.assertEqual(self.result.returncode, 0,
                         f'agent 退出码非 0:\n{self.log[-3000:]}')

    def test_state_completed(self):
        state = json.loads((ROOT / 'runtime' / BOOK_ID / 'state.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(state['done'], STATE_STAGES)
        self.assertEqual(state['plan_id'], 'short_novel')
        self.assertEqual(state['mode']['mode_id'], 'g1_narrative')

    def test_middle_products(self):
        wd = ROOT / 'runtime' / BOOK_ID
        self.assertTrue((wd / 'chapters.json').exists())
        self.assertTrue((wd / 'chunks.json').exists())
        self.assertTrue((wd / 'characters.json').exists())
        self.assertTrue((wd / 'bible' / 'world.md').exists())
        self.assertTrue((wd / 'design' / 'brief.json').exists())
        self.assertTrue((wd / 'qa' / 'qa_report.json').exists())
        self.assertTrue((wd / 'mode.json').exists())
        chapters = json.loads((wd / 'chapters.json').read_text(encoding='utf-8'))
        self.assertEqual(len(chapters['chapters']), 3, '应识别出 3 个章节')

    def test_game_folder_valid(self):
        game_dir = ROOT / 'games' / BOOK_ID
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

    def test_smoke_passes(self):
        game_dir = ROOT / 'games' / BOOK_ID
        r = subprocess.run(
            [sys.executable, str(ROOT / 'skills' / 'qa-check' / 'scripts'
             / 'smoke_test.py'), str(game_dir), '--runs', '30', '--json'],
            capture_output=True, text=True, cwd=str(ROOT))
        out = json.loads(r.stdout)
        self.assertTrue(out['ok'], f'冒烟未通过:\n{out["issues"]}')
        self.assertEqual(out['summary']['ending_rate'], '100%')
        self.assertEqual(out['summary']['unreachable_nodes'], [])

    def test_archive_zip(self):
        zip_path = ROOT / 'archive' / f'{BOOK_ID}.zip'
        self.assertTrue(zip_path.exists())
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            for f in ('index.html', 'engine/engine.js', 'data/scenes.js', 'data/game.js'):
                self.assertIn(f, names)
            self.assertNotIn('assets/bg/.gitkeep', names, 'zip 不应包含 .gitkeep')

    def test_upload_registered_expiry(self):
        expiry = ROOT / 'runtime' / 'expiry.json'
        recs = json.loads(expiry.read_text(encoding='utf-8'))
        self.assertIn(BOOK_ID, recs)
        self.assertIn('expires_at', recs[BOOK_ID])

    @unittest.skipUnless(HAS_NODE, '需要 node 运行时')
    def test_engine_selftest(self):
        """真实引擎代码 + 真实数据文件：随机游玩 25 次应无运行时错误。"""
        r = subprocess.run(['node', str(ROOT / 'tests' / 'engine_selftest.js'),
                            str(ROOT / 'games' / BOOK_ID)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, f'引擎自检失败:\n{r.stdout}\n{r.stderr}')
        self.assertIn('ENGINE_SELFTEST_OK', r.stdout)


class TestRepairLoopOnBadData(unittest.TestCase):
    """坏数据 → repair 修复循环：多轮失败后应标记 degraded 且不崩溃。"""

    @classmethod
    def setUpClass(cls):
        for p in (ROOT / 'runtime' / BOOK_ID, ROOT / 'games' / BOOK_ID,
                  ROOT / 'archive' / f'{BOOK_ID}.zip'):
            if p.exists():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        cls.result = subprocess.run(
            [sys.executable, str(ROOT / 'agent.py'),
             str(ROOT / 'tests' / 'fixtures' / 'tiny_novel.txt'), '--mock',
             '--mock-dir', str(ROOT / 'tests' / 'fixtures' / 'mock_data_bad')],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        cls.log = cls.result.stdout + cls.result.stderr

    def test_pipeline_does_not_crash(self):
        self.assertEqual(self.result.returncode, 0,
                         f'agent 崩溃:\n{self.log[-3000:]}')
        state = json.loads((ROOT / 'runtime' / BOOK_ID / 'state.json')
                           .read_text(encoding='utf-8'))
        self.assertIn('qa', state['done'])
        self.assertIn('package', state['done'])

    def test_qa_marked_degraded(self):
        report = json.loads((ROOT / 'runtime' / BOOK_ID / 'qa' / 'qa_report.json')
                            .read_text(encoding='utf-8'))
        self.assertTrue(report['degraded'], '坏数据下 QA 应标记 degraded')
        self.assertEqual(report['round'], 4)  # 1 轮 + 3 次修复递归

    def test_qa_report_records_issues(self):
        report = json.loads((ROOT / 'runtime' / BOOK_ID / 'qa' / 'qa_report.json')
                            .read_text(encoding='utf-8'))
        self.assertFalse(report['ok'])
        self.assertGreater(len(report['issues']), 0)


class TestLongNovelMockPipeline(unittest.TestCase):
    """长篇小说（≥8 万字）路径：summarize 逐卷摘要 + generate 分批生成。"""

    LONG_BOOK = 'long_novel'

    @classmethod
    def setUpClass(cls):
        for p in (ROOT / 'runtime' / cls.LONG_BOOK, ROOT / 'games' / cls.LONG_BOOK,
                  ROOT / 'archive' / f'{cls.LONG_BOOK}.zip'):
            if p.exists():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        cls.result = subprocess.run(
            [sys.executable, str(ROOT / 'agent.py'),
             str(ROOT / 'tests' / 'fixtures' / 'long_novel.txt'), '--mock',
             '--mock-dir', str(ROOT / 'tests' / 'fixtures' / 'mock_data_long')],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        cls.log = cls.result.stdout + cls.result.stderr

    def test_long_plan_and_summarize(self):
        self.assertEqual(self.result.returncode, 0,
                         f'agent 退出码非 0:\n{self.log[-3000:]}')
        state = json.loads((ROOT / 'runtime' / self.LONG_BOOK / 'state.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(state['plan_id'], 'long_novel')
        self.assertIn('summarize', state['done'])
        summaries = (ROOT / 'runtime' / self.LONG_BOOK / 'bible' / 'summaries.md')
        self.assertTrue(summaries.exists())
        self.assertIn('块', summaries.read_text(encoding='utf-8'))

    def test_long_design_used_custom_fixture(self):
        """mock-dir 应覆盖默认 fixture：15 场景蓝图而非默认 5 场景。"""
        brief = json.loads((ROOT / 'runtime' / self.LONG_BOOK / 'design' / 'brief.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(brief['game_title'], '山茶书')
        self.assertEqual(len(brief['scene_blueprint']), 15)

    def test_long_game_valid(self):
        game_dir = ROOT / 'games' / self.LONG_BOOK
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
        self.assertTrue((ROOT / 'archive' / f'{self.LONG_BOOK}.zip').exists())


if __name__ == '__main__':
    unittest.main()
