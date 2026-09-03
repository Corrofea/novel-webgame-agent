# -*- coding: utf-8 -*-
"""契约层与 ReAct 循环单元测试。

运行: python tests/run_tests.py   （或 python -m unittest tests.test_contracts）
"""
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.contracts import (validate_characters, validate_detect, validate_extract,
                            validate_style)
from core.image import (ImageError, MockImageClient, SiliconFlowImage,
                        load_prompt_file)
from core.llm import MockLLM, parse_json_block
from core.react import json_validator, react_loop
from core.utils import load_dotenv

# 1x1 透明 PNG（与 MockImageClient 同款）
TINY_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


class FakeResp:
    def __init__(self, status_code=200, json_data=None, text='', content=b''):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = content

    def json(self):
        return self._json


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
        for mode in ('classic', 'strategy', 'puzzle', 'epic',
                     'narrative', 'riddle', 'survival', 'fate', 'galgame'):
            ok, errors = validate_detect({'mode_id': mode, 'theme_id': 'modern',
                                          'chunk_strategy': 'x'})
            self.assertTrue(ok, errors)

    def test_invalid_mode(self):
        ok, errors = validate_detect({'mode_id': 'g9_xxx', 'theme_id': 'modern',
                                      'chunk_strategy': 'x'})
        self.assertFalse(ok)

    def test_missing_fields(self):
        ok, errors = validate_detect({'mode_id': 'classic'})
        self.assertFalse(ok)


class TestValidateExtract(unittest.TestCase):
    def test_classic_ok(self):
        data = {'mode_id': 'classic', 'items': [
            {'summary': '因下雨两人走散', 'motivation': '赶路', 'characters': ['陈默'],
             'location': '山路', 'time': '秋日', 'key_event': '走散', 'emotion': '焦虑',
             'atmosphere': '雨雾'}]}
        ok, errors = validate_extract(data, 'classic')
        self.assertTrue(ok, errors)

    def test_classic_missing_summary(self):
        data = {'mode_id': 'classic', 'items': [{'key_event': '走散'}]}
        ok, errors = validate_extract(data, 'classic')
        self.assertFalse(ok)
        self.assertTrue(any('summary' in e for e in errors))

    def test_riddle_ok(self):
        data = {'mode_id': 'riddle', 'items': [
            {'kind': 'target', 'name': '林黛玉', 'aliases': ['颦儿']},
            {'kind': 'material', 'target': '林黛玉', 'quote': '玉带林中挂',
             'hints': ['红楼女子']}]}
        ok, errors = validate_extract(data, 'riddle')
        self.assertTrue(ok, errors)

    def test_riddle_missing_quote(self):
        data = {'mode_id': 'riddle', 'items': [
            {'kind': 'target', 'name': '林黛玉'},
            {'kind': 'material', 'target': '林黛玉'}]}
        ok, errors = validate_extract(data, 'riddle')
        self.assertFalse(ok)
        self.assertTrue(any('quote' in e for e in errors))

    def test_fate_ok(self):
        data = {'mode_id': 'fate', 'items': [
            {'kind': 'pool', 'name': '江南首富独子', 'description': '锦衣玉食'},
            {'kind': 'event', 'title': '诗会夺魁', 'outcome': '才名远播'},
            {'kind': 'fortune', 'name': '家世'}]}
        ok, errors = validate_extract(data, 'fate')
        self.assertTrue(ok, errors)

    def test_fate_missing_pool_name(self):
        data = {'mode_id': 'fate', 'items': [
            {'kind': 'pool', 'description': '锦衣玉食'},
            {'kind': 'event', 'title': '诗会夺魁'}]}
        ok, errors = validate_extract(data, 'fate')
        self.assertFalse(ok)
        self.assertTrue(any('name' in e for e in errors))

    def test_empty_items(self):
        ok, errors = validate_extract({'mode_id': 'classic', 'items': []}, 'classic')
        self.assertFalse(ok)

    def test_items_not_list(self):
        ok, errors = validate_extract({'mode_id': 'classic', 'items': 'x'}, 'classic')
        self.assertFalse(ok)

    def test_non_dict_item(self):
        ok, errors = validate_extract({'mode_id': 'classic', 'items': ['x']}, 'classic')
        self.assertFalse(ok)


class TestModeTemplateConsistency(unittest.TestCase):
    """模式模板自检：JSON 可解析、id 与 runtime.mode_id 一致、extraction 完整。"""

    ROOT = Path(__file__).resolve().parent.parent

    def test_all_templates_valid(self):
        from core.contracts import validate_detect
        for tpl in sorted((self.ROOT / 'templates' / 'game_modes').glob('*.json')):
            data = json.loads(tpl.read_text(encoding='utf-8'))
            mode_id = data['id']
            self.assertEqual(data['runtime']['mode_id'], mode_id,
                             f'{tpl.name}: id 与 runtime.mode_id 不一致')
            # 白名单同步（改名时必须两边一起动）
            ok, errors = validate_detect({'mode_id': mode_id, 'theme_id': 'modern',
                                          'chunk_strategy': 'x'})
            self.assertTrue(ok, f'{tpl.name}: mode_id 不在契约白名单: {errors}')
            # extraction 契约
            ext = data.get('extraction')
            self.assertIsNotNone(ext, f'{tpl.name}: 缺少 extraction 字段')
            for field in ('task', 'schema', 'uses'):
                self.assertTrue(ext.get(field), f'{tpl.name}: extraction 缺少 {field}')


class TestEngineContract(unittest.TestCase):
    """引擎契约防回归：结局收集能力必须完整（历史 bug：clearSave 清 meta、
    封面计数不刷新、file:// 下 storage 静默失效、endings 存字符串渲染对象）。"""

    ROOT = Path(__file__).resolve().parent.parent

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'validate_game', cls.ROOT / 'skills' / 'qa-check' / 'scripts' / 'validate_game.py')
        cls.vg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.vg)
        cls.js = (cls.ROOT / 'engine' / 'engine.js').read_text(encoding='utf-8')

    def test_all_contracts_pass(self):
        for sev, msg, cond in self.vg.ENGINE_CONTRACT:
            self.assertTrue(cond(self.js), f'引擎契约失败 [{sev}]: {msg}')

    def test_old_buggy_engine_caught(self):
        """旧引擎（clearSave 前缀清空 + 无 storage 封装 + 存字符串）必须被契约抓住。"""
        old = '''
function clearSave() {
  var keys = [];
  for (var i = 0; i < localStorage.length; i++) {
    var k = localStorage.key(i);
    if (k && k.indexOf(GAME.save_key || 'nwa_game') === 0) keys.push(k);
  }
  keys.forEach(function (k) { localStorage.removeItem(k); });
}
function endGame(node) { var meta = getMeta(); meta.endings.push(en.title); setMeta(meta); }
function getMeta() {} function setMeta() {}
'''
        failed = [msg for sev, msg, cond in self.vg.ENGINE_CONTRACT if not cond(old)]
        self.assertTrue(any('clearSave' in m for m in failed), failed)
        self.assertTrue(any('storage' in m or 'file://' in m for m in failed), failed)

    def test_clear_save_only_clears_archive(self):
        self.assertIn('function clearSave() { storage.removeItem(SAVE_KEY); }', self.js)
        self.assertIn('function clearAllData()', self.js)


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


class TestImageClient(unittest.TestCase):
    """硅基流动生图客户端：响应解析（url/b64_json/data URL）、失败降级、落盘。"""

    CFG = {'base_url': 'https://api.siliconflow.cn/v1', 'api_key_env': 'SILICONFLOW_API_KEY',
           'model': 'black-forest-labs/FLUX.1-schnell', 'size': '1024x1024',
           'max_retries': 1, 'retry_delay': 0, 'webp': False}
    # webp: False —— 本组测响应解析字节透传；转码行为由 test_image.TestWebpSave 覆盖

    def _client(self):
        with mock.patch.dict(os.environ, {'SILICONFLOW_API_KEY': 'sk-test'}):
            return SiliconFlowImage(self.CFG)

    def _out(self):
        d = tempfile.mkdtemp()
        return Path(d) / 'out.png'

    def test_mock_writes_real_png(self):
        out = self._out()
        MockImageClient().generate('x', str(out))
        self.assertTrue(out.exists())
        self.assertTrue(out.read_bytes().startswith(b'\x89PNG'))

    def test_response_data_url(self):
        out = self._out()
        b64 = base64.b64encode(TINY_PNG).decode()
        with mock.patch('core.image.requests.post',
                        return_value=FakeResp(json_data={'data': [
                            {'url': f'data:image/png;base64,{b64}'}]})):
            self._client().generate('p', str(out), '1:1')
        self.assertEqual(out.read_bytes(), TINY_PNG)

    def test_response_b64_json(self):
        out = self._out()
        b64 = base64.b64encode(TINY_PNG).decode()
        with mock.patch('core.image.requests.post',
                        return_value=FakeResp(json_data={'data': [{'b64_json': b64}]})):
            self._client().generate('p', str(out))
        self.assertEqual(out.read_bytes(), TINY_PNG)

    def test_response_url_download(self):
        out = self._out()
        with mock.patch('core.image.requests.post',
                        return_value=FakeResp(json_data={'images': [
                            {'url': 'https://cdn.example.com/a.png'}]})), \
             mock.patch('core.image.requests.get',
                        return_value=FakeResp(content=TINY_PNG)):
            self._client().generate('p', str(out))
        self.assertEqual(out.read_bytes(), TINY_PNG)

    def test_default_webp_transcodes_on_save(self):
        """默认配置（webp: true）落盘即 .webp（RIFF 容器）——管线默认图格式契约。"""
        import importlib.util
        if importlib.util.find_spec('PIL') is None:
            self.skipTest('未安装 Pillow')
        out = self._out()
        b64 = base64.b64encode(TINY_PNG).decode()
        cfg = dict(self.CFG, webp=True)  # 本组 CFG 关转码；此用例单独开
        with mock.patch.dict(os.environ, {'SILICONFLOW_API_KEY': 'sk-test'}), \
             mock.patch('core.image.requests.post',
                        return_value=FakeResp(json_data={'data': [{'b64_json': b64}]})):
            saved = SiliconFlowImage(cfg).generate('p', str(out))
        p = Path(saved)
        self.assertEqual(p.suffix, '.webp')
        head = p.read_bytes()
        self.assertTrue(head.startswith(b'RIFF') and b'WEBP' in head[:12])

    def test_html_error_page_rejected(self):
        """下载到 HTML（网关错误页）应报错而不是落盘垃圾。"""
        out = self._out()
        with mock.patch('core.image.requests.post',
                        return_value=FakeResp(json_data={'data': [
                            {'url': 'https://cdn.example.com/a.png'}]})), \
             mock.patch('core.image.requests.get',
                        return_value=FakeResp(content=b'<html>502 Bad Gateway</html>')):
            with self.assertRaises(ImageError):
                self._client().generate('p', str(out))
        self.assertFalse(out.exists())

    def test_empty_response_raises(self):
        with mock.patch('core.image.requests.post',
                        return_value=FakeResp(json_data={'data': []})):
            with self.assertRaises(ImageError):
                self._client().generate('p', str(self._out()))

    def test_http_error_retries_then_raise(self):
        with mock.patch('core.image.requests.post',
                        return_value=FakeResp(status_code=500, text='boom')):
            with self.assertRaises(ImageError):
                self._client().generate('p', str(self._out()))

    def test_missing_key_raises(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SILICONFLOW_API_KEY', None)
            with self.assertRaises(ImageError):
                SiliconFlowImage(self.CFG)

    def test_load_prompt_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x.prompt.txt'
            p.write_text('# portrait / 陈默 / 画风: 水墨 (ink_wash)\n'
                         '# 尺寸: 9:16\n\nPrompt: ink wash, portrait of chen\n\n'
                         'Negative: text\n', encoding='utf-8')
            prompt, size = load_prompt_file(p)
            self.assertEqual(prompt, 'ink wash, portrait of chen')
            self.assertEqual(size, '9:16')

    def test_size_map_9_16(self):
        out = self._out()
        sent = {}

        def fake_post(url, json=None, **kw):
            sent['payload'] = json
            return FakeResp(json_data={'data': [{'url': 'https://cdn.example.com/a.png'}]})

        with mock.patch('core.image.requests.post', side_effect=fake_post), \
             mock.patch('core.image.requests.get',
                        return_value=FakeResp(content=TINY_PNG)):
            self._client().generate('p', str(out), '9:16')
        self.assertEqual(sent['payload']['image_size'], '720x1280')


class TestLoadDotenv(unittest.TestCase):
    def test_loads_and_does_not_override(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / '.env'
            env.write_text('# comment\nDEEPSEEK_API_KEY=sk-a\nSILICONFLOW_API_KEY=sk-b\n'
                           'QUOTED="sk-c"\n', encoding='utf-8')
            with mock.patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'sk-exists'}, clear=False):
                load_dotenv(env)
                self.assertEqual(os.environ['DEEPSEEK_API_KEY'], 'sk-exists', '已有变量不覆盖')
                self.assertEqual(os.environ['SILICONFLOW_API_KEY'], 'sk-b')
                self.assertEqual(os.environ['QUOTED'], 'sk-c')

    def test_missing_file_noop(self):
        load_dotenv(Path('/nonexistent/.env'))
        self.assertTrue(True)


if __name__ == '__main__':
    unittest.main()
