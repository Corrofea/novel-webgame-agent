# -*- coding: utf-8 -*-
"""theme 2.0 视觉主题一致性测试。

权威分层（唯一真源 = engine/theme.css）：
  - :root 定义全部 30 个语义变量（默认中性基线）；
  - 每个视觉主题 = 一个 body.style-<id> 块，必须全量覆盖 REQUIRED_VARS（26 个），
    并允许 4 个可选变量（--page-texture/--page-vignette/--term-bg/--term-text）；
  - 布局段（:root 之后、首个风格块之前）只许引用变量，不许出现裸十六进制色值
    （防回退到硬编码配色时代）；
  - templates/themes/*.json 只存 {id, name, 气质…} 目录卡（无任何色值），
    其 id 集合必须与 CSS 风格块集合一致（双向防孤儿）。

跨文件同步：
  - core.contracts.validate_detect 的主题白名单同源自动读取；
  - validate_game.check_theme：name 白名单 + colors 残留告警 + CSS 风格块存在。
"""
import json
import re
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS_PATH = ROOT / 'engine' / 'theme.css'
THEMES_DIR = ROOT / 'templates' / 'themes'

# :root 全部 30 个变量（改名必须同步这里——两处不一致即测试失败）
EXPECTED_VARS = [
    '--bg', '--bg-deep', '--panel', '--panel-2',
    '--text', '--sub', '--border',
    '--accent', '--accent-light', '--on-accent',
    '--ok', '--warn', '--danger',
    '--font-title', '--font-body', '--font-ui',
    '--radius-s', '--radius-m', '--shadow-panel', '--shadow-float',
    '--page-texture', '--page-vignette',
    '--gal-stage-bg', '--gal-dialog-bg', '--gal-surface-bg',
    '--gal-border', '--gal-text', '--gal-accent',
    '--term-bg', '--term-text',
]
REQUIRED_VARS = EXPECTED_VARS[:20] + EXPECTED_VARS[22:28]  # 26 个风格块必定义


def theme_json_ids() -> list:
    ids = []
    for p in sorted(THEMES_DIR.glob('*.json')):
        t = json.loads(p.read_text(encoding='utf-8'))
        ids.append(t.get('id'))
    return ids


def css_style_ids(css: str) -> list:
    """文件顺序中每个主题块的 id（首个 `body.style-<id> {` 出现次序）。"""
    seen, ids = set(), []
    for m in re.finditer(r'^body\.style-([a-z0-9_-]+)\s*\{', css, re.M):
        sid = m.group(1)
        if sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return ids


def css_style_regions(css: str) -> dict:
    """id -> 文本切片 [start, end)。区间起点 = 该 id 首个 header；终点 = 下一个
    不同 id 的 header / @media 起始 / EOF。块内同 id 的独立规则（如
    `body.style-dream { font-weight: 300; }`）不构成边界。"""
    hdrs = [(m.start(), m.group(1))
            for m in re.finditer(r'^body\.style-([a-z0-9_-]+)\s*\{', css, re.M)]
    media = [m.start() for m in re.finditer(r'^@media', css, re.M)]
    regions, order = {}, []
    for i, (pos, sid) in enumerate(hdrs):
        if sid in regions:
            continue  # 块内同 id 规则，非新区间
        end = len(css)
        for p2, sid2 in hdrs[i + 1:]:
            if sid2 != sid:
                end = p2
                break
        for mm in media:
            if pos < mm < end:
                end = mm
        regions[sid] = (pos, end)
        order.append(sid)
    return regions


def defined_vars(region_text: str) -> set:
    """区间内 `--name:` 定义（块内变量赋值）。"""
    return set(re.findall(r'(--[a-z0-9-]+)\s*:', region_text))


def used_vars(region_text: str) -> set:
    return set(re.findall(r'var\((--[a-z0-9-]+)\)', region_text))


class TestCatalogConsistency(unittest.TestCase):
    """theme.css 内部一致性：变量 schema + 风格块覆盖 + 色值形态。"""

    @classmethod
    def setUpClass(cls):
        cls.css = CSS_PATH.read_text(encoding='utf-8')
        cls.regions = css_style_regions(cls.css)

    def test_root_defines_all_30_vars(self):
        m = re.search(r'^:root\s*\{', self.css, re.M)
        self.assertIsNotNone(m, ':root 块缺失')
        root_text = self.css[m.start():m.end() + 3000]
        # 取到 :root 闭合为止
        depth, i, in_str = 1, m.end(), None
        while i < len(root_text) and depth:
            c = root_text[i]
            if in_str:
                if c == '\\':
                    i += 2
                    continue
                if c == in_str:
                    in_str = None
            elif c in '"\'':
                in_str = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
            i += 1
        root_body = root_text[:i]
        defined = set(re.findall(r'(--[a-z0-9-]+)\s*:', root_body))
        self.assertEqual(set(EXPECTED_VARS), defined,
                         f':root 变量集合不一致，缺: {set(EXPECTED_VARS) - defined}')

    def test_style_block_ids_unique_and_anchored(self):
        """每个 id 恰好一个块区间，块内同 id 规则不产生孤儿区间。"""
        ids = css_style_ids(self.css)
        self.assertEqual(len(ids), len(set(ids)), f'风格块 id 重复: {ids}')
        self.assertEqual(set(ids), set(self.regions), 'block 扫描不一致')
        self.assertGreaterEqual(len(ids), 12, f'风格块应 ≥12（当前 {len(ids)}）')

    def test_each_style_block_covers_required_vars(self):
        for sid, (s, e) in self.regions.items():
            defined = defined_vars(self.css[s:e])
            missing = set(REQUIRED_VARS) - defined
            self.assertFalse(missing,
                             f'风格块 {sid} 缺 REQUIRED_VARS: {sorted(missing)}')
            # 块内只能定义已知变量（防打错变量名 → 静默失效）
            unknown = defined - set(EXPECTED_VARS)
            self.assertFalse(unknown, f'风格块 {sid} 定义了未知变量: {sorted(unknown)}')

    def test_all_var_usages_have_defaults(self):
        used = set(re.findall(r'var\((--[a-z0-9-]+)\)', self.css))
        unknown = used - set(EXPECTED_VARS)
        self.assertFalse(unknown, f'var() 引用未定义变量: {sorted(unknown)}')

    def test_layout_section_has_no_bare_hex_colors(self):
        """:root 之后、首个风格块之前的布局段只许引变量（含装饰性黑/白投影例外
        仍应走 rgba）。检查用整文件剥离 :root 与风格块后的残留区。"""
        regions = sorted(self.regions.values())
        start = regions[0][0]
        head = re.sub(r'^:root\s*\{.*?\n\}', '', self.css[:start], flags=re.M | re.S)
        head = re.sub(r'#app\b', '', head)  # #app 是元素选择器，非色值
        hexes = re.findall(r'#[0-9a-fA-F]{3,8}\b', head)
        self.assertEqual(hexes, [], f'布局段出现裸色值（应改引变量）: {hexes}')

    def test_hex_literals_have_valid_length(self):
        """全文 hex 只许 3/4/6/8 位（抓 #fff5 之类的截断色值）。"""
        body = re.sub(r'#app\b', '', self.css)
        bad = re.findall(r'#[0-9a-fA-F]{1,2}\b|#[0-9a-fA-F]{5}\b|#[0-9a-fA-F]{7}\b|#[0-9a-fA-F]{9,}\b', body)
        self.assertEqual(bad, [], f'非法长度色值: {bad}')


class TestCatalogThemesSync(unittest.TestCase):
    """templates/themes/*.json 与 theme.css 风格块双向一致（防孤儿）。"""

    @classmethod
    def setUpClass(cls):
        cls.css_ids = css_style_ids(CSS_PATH.read_text(encoding='utf-8'))
        cls.json_ids = theme_json_ids()

    def test_ids_equal_both_ways(self):
        self.assertEqual(len(self.json_ids), len(set(self.json_ids)),
                         'themes/*.json 存在重复 id')
        missing_json = set(self.css_ids) - set(self.json_ids)
        missing_css = set(self.json_ids) - set(self.css_ids)
        self.assertFalse(missing_json, f'CSS 有块但 themes 无 json: {missing_json}')
        self.assertFalse(missing_css, f'themes 有 json 但 CSS 无块: {missing_css}')

    def test_theme_json_has_no_color_fields(self):
        """主题 JSON 只存目录卡（id/name/气质/适用/illustration_style），
        任何 colors/fonts/cover 字段 = 给 LLM 递调色通道，直接失败。"""
        for p in sorted(THEMES_DIR.glob('*.json')):
            t = json.loads(p.read_text(encoding='utf-8'))
            for bad in ('colors', 'fonts', 'cover', 'css'):
                self.assertNotIn(bad, t, f'{p.name} 含机器字段 {bad}（色值唯一权威在 theme.css）')

    def test_theme_json_schema_fields(self):
        for p in sorted(THEMES_DIR.glob('*.json')):
            t = json.loads(p.read_text(encoding='utf-8'))
            for field in ('id', 'name', '适用', '气质', 'illustration_style'):
                self.assertTrue(t.get(field), f'{p.name} 缺目录卡字段 {field}')
            self.assertEqual(t['id'], p.stem, f'{p.name}: id 与文件名不一致')


class TestDetectWhitelistSync(unittest.TestCase):
    """contracts.validate_detect 的主题白名单与模板目录同源。"""

    def test_all_theme_ids_pass_validate_detect(self):
        from core.contracts import validate_detect, valid_theme_ids
        ids = valid_theme_ids()
        self.assertGreaterEqual(len(ids), 12, f'白名单过少: {ids}')
        for tid in ids:
            ok, errs = validate_detect({'mode_id': 'classic', 'theme_id': tid,
                                        'chunk_strategy': 'x'})
            self.assertTrue(ok, f'{tid}: {errs}')

    def test_unknown_theme_id_rejected(self):
        from core.contracts import validate_detect
        ok, errs = validate_detect({'mode_id': 'classic', 'theme_id': 'nope',
                                    'chunk_strategy': 'x'})
        self.assertFalse(ok)
        self.assertTrue(any('theme_id' in e for e in errs), errs)

    def test_whitelist_matches_theme_json_dir(self):
        from core.contracts import valid_theme_ids
        self.assertEqual(set(valid_theme_ids()), set(theme_json_ids()))


def _write_theme(tmp: Path, obj) -> Path:
    p = tmp / 'data' / 'theme.js'
    p.write_text(f'window.THEME = {json.dumps(obj, ensure_ascii=False)};\n',
                 encoding='utf-8')
    return p


class TestValidateGameThemeRules(unittest.TestCase):
    """validate_game.check_theme：theme.js 只许 {name: 白名单 id}。"""

    def _issues(self, theme_obj, css_override=None):
        # 延迟导入（test_validate 会往 sys.path 加 scripts 目录）
        from test_validate import build_game_dir, golden_scenes
        from validate_game import load_game, validate
        tmp = build_game_dir(golden_scenes())
        try:
            _write_theme(tmp, theme_obj)
            if css_override is not None:
                (tmp / 'engine' / 'theme.css').write_text(css_override, encoding='utf-8')
            data, fatal = load_game(tmp)
            self.assertIsNone(fatal)
            return validate(data, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_golden_theme_passes(self):
        issues = self._issues({'name': 'modern', 'colors': {}})
        errs = [i for i in issues if i.severity == 'error']
        warns = [i for i in issues if i.severity == 'warning']
        self.assertEqual(errs, [], [str(i) for i in errs])
        self.assertFalse(any('theme' in i.file for i in warns))

    def test_missing_name_is_error(self):
        issues = self._issues({'name': ''})
        self.assertTrue(any(i.severity == 'error' and '缺少 name' in i.msg
                            for i in issues), [str(i) for i in issues])

    def test_non_whitelisted_name_is_error(self):
        issues = self._issues({'name': 'nope'})
        self.assertTrue(any(i.severity == 'error' and '白名单' in i.msg
                            for i in issues), [str(i) for i in issues])

    def test_legacy_colors_warn_but_not_error(self):
        """旧产物（colors 非空）走引擎兼容注入：只告警不报错（回填前合法）。"""
        issues = self._issues({'name': 'modern', 'colors': {'bg': '#f5f0e8'}})
        self.assertFalse(any(i.severity == 'error' for i in issues),
                         [str(i) for i in issues])
        self.assertTrue(any(i.severity == 'warning' and 'colors' in i.msg
                            for i in issues), [str(i) for i in issues])

    def test_missing_css_style_block_is_error(self):
        css = ':root { --bg: #fff; --panel: #fff; --text: #000; }'
        issues = self._issues({'name': 'modern', 'colors': {}}, css_override=css)
        self.assertTrue(any(i.severity == 'error' and 'body.style-modern' in i.msg
                            for i in issues), [str(i) for i in issues])

    def test_missing_theme_css_is_error(self):
        issues = self._issues({'name': 'modern', 'colors': {}}, css_override='')
        self.assertTrue(any(i.severity == 'error' and 'theme.css' in i.msg
                            for i in issues), [str(i) for i in issues])


if __name__ == '__main__':
    unittest.main()
