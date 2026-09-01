# -*- coding: utf-8 -*-
"""契约层与 ReAct 循环单元测试。

运行: python tests/run_tests.py   （或 python -m unittest tests.test_contracts）
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.contracts import validate_characters, validate_detect, validate_style
from core.llm import MockLLM, parse_json_block
from core.react import json_validator, react_loop


class TestValidateCharacters(unittest.TestCase):
    def test_ok(self):
        data = {'characters': [
            {'name': '陈默', 'aliases': ['小陈'], 'gender': '男', 'identity': '主角',
             'traits': [], 'experiences': [], 'relationships': [], 'ending': '',
             'notes': []}]}
        ok, errors = validate_characters(data)
        self.assertTrue(ok, errors)

    def test_empty_rejected(self):
        ok, errors = validate_characters({'characters': []})
        self.assertFalse(ok)

    def test_missing_fields(self):
        ok, errors = validate_characters({'characters': [{'name': 'A'}]})
        self.assertFalse(ok)
        self.assertTrue(any('缺少字段' in e for e in errors))

    def test_duplicate_names(self):
        data = {'characters': [
            {'name': 'A', 'aliases': [], 'gender': '', 'identity': '', 'traits': [],
             'experiences': [], 'relationships': [], 'ending': '', 'notes': []},
            {'name': 'A', 'aliases': [], 'gender': '', 'identity': '', 'traits': [],
             'experiences': [], 'relationships': [], 'ending': '', 'notes': []}]}
        ok, errors = validate_characters(data)
        self.assertFalse(ok)
        self.assertTrue(any('重名' in e for e in errors))


class TestValidateDetect(unittest.TestCase):
    def test_valid_modes(self):
        for mode in ('g1_narrative', 'g2_strategy', 's3_puzzle', 's4_epic'):
            ok, errors = validate_detect({'mode_id': mode, 'theme_id': 'modern',
                                          'chunk_strategy': 'x'})
            self.assertTrue(ok, errors)

    def test_invalid_mode(self):
        ok, errors = validate_detect({'mode_id': 'g9_xxx', 'theme_id': 'modern',
                                      'chunk_strategy': 'x'})
        self.assertFalse(ok)

    def test_missing_fields(self):
        ok, errors = validate_detect({'mode_id': 'g1_narrative'})
        self.assertFalse(ok)


class TestValidateStyle(unittest.TestCase):
    def test_ok(self):
        text = '## 主旨\n...\n\n## 世界观\n...\n\n## 情感基调\n...\n\n## 风格定调\nmodern'
        ok, errors = validate_style(text)
        self.assertTrue(ok, errors)

    def test_missing_section(self):
        ok, errors = validate_style('## 主旨\n...\n\n## 世界观\n...')
        self.assertFalse(ok)


class TestParseJsonBlock(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_json_block('{"a": 1}'), {'a': 1})

    def test_fenced(self):
        self.assertEqual(parse_json_block('```json\n{"a": 1}\n```'), {'a': 1})

    def test_preamble(self):
        self.assertEqual(parse_json_block('好的，以下是结果：\n{"a": 1}\n完毕'), {'a': 1})

    def test_invalid(self):
        from core.llm import LLMError
        with self.assertRaises(LLMError):
            parse_json_block('这不是 JSON')


class TestReactLoop(unittest.TestCase):
    """用 MockLLM 验证 ReAct 修复循环：失败→观察→重试→成功。"""

    def test_first_try_ok(self):
        mock = MockLLM(None)
        mock.chat = lambda *a, **k: '{"ok": 1}'  # 恒通过
        r = react_loop(mock, 'sys', 'task', json_validator(['ok']), max_rounds=3)
        self.assertTrue(r.ok)
        self.assertEqual(r.rounds, 1)

    def test_retry_after_failure(self):
        mock = MockLLM(None)
        counter = {'n': 0}

        def fake_chat(*a, **k):
            counter['n'] += 1
            # 前两次缺字段，第三次补上（模拟 LLM 看到观察后修正）
            return '{}' if counter['n'] < 3 else '{"fixed": true}'

        mock.chat = fake_chat
        r = react_loop(mock, 'sys', 'task', json_validator(['fixed']), max_rounds=3)
        self.assertTrue(r.ok)
        self.assertEqual(r.rounds, 3)
        self.assertEqual(json.loads(r.content)['fixed'], True)

    def test_exhaust_rounds(self):
        mock = MockLLM(None)
        mock.chat = lambda *a, **k: '{"bad": 1}'  # 永远缺字段
        r = react_loop(mock, 'sys', 'task', json_validator(['fixed']), max_rounds=2)
        self.assertFalse(r.ok)
        self.assertEqual(r.rounds, 2)
        self.assertTrue(len(r.errors) > 0)

    def test_validator_exception_is_failure(self):
        mock = MockLLM(None)
        mock.chat = lambda *a, **k: '{}'

        def boom(content):
            raise RuntimeError('validator bug')

        r = react_loop(mock, 'sys', 'task', boom, max_rounds=1)
        self.assertFalse(r.ok)
        self.assertTrue(any('校验器异常' in e for e in r.errors))


if __name__ == '__main__':
    unittest.main()
