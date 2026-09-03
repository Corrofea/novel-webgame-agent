# -*- coding: utf-8 -*-
"""core/image.py 单元测试：提示词文件解析（尺寸标注回归）+ 客户端行为。"""
import base64
import importlib.util
import tempfile
import unittest
from pathlib import Path

from core.image import SIZE_MAP, SiliconFlowImage, _save_image, load_prompt_file

_HAS_PIL = importlib.util.find_spec('PIL') is not None
# 与 MockImageClient 同款的真实 1x1 PNG
_PNG_1PX = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')

# 2026-09 实录 Bug：_fmt_prompt 写单行头 `# kind / 画风: X (id) / 尺寸: 9:16`，
# load_prompt_file 只匹配 `# 尺寸: ` 开头的行 → 尺寸标注永远丢失，
# 全库所有图按默认 1024x1024 方形生成（bg 16:9 / 立绘 9:16 从未生效）。
HEADER_ONE_LINE = (
    '# portrait / 画风: 黑白线稿+红强调 (noir_line) / 尺寸: 9:16\n'
    'Prompt: black and white line art, portrait of X, 9:16, high quality\n\n'
    'Negative: photorealistic\n'
)
HEADER_LEGACY = (
    '# portrait\n'
    '# 尺寸: 16:9\n'
    'Prompt: a wide scene\n'
)
HEADER_NO_SIZE = 'Prompt: plain prompt without header\n'
HEADER_MISSING_PROMPT = '# bg / 画风: flat (flat_modern) / 尺寸: 1:1\n'


def _write(tmp: str, text: str) -> Path:
    p = Path(tmp) / 'probe.prompt.txt'
    p.write_text(text, encoding='utf-8')
    return p


class TestLoadPromptFile(unittest.TestCase):
    def test_one_line_header_size_parsed(self):
        """单行头 `... / 尺寸: 9:16` 是 _fmt_prompt 的真实输出格式，必须解析出尺寸。"""
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, HEADER_ONE_LINE)
            prompt, size = load_prompt_file(p)
        self.assertEqual(size, '9:16')
        self.assertTrue(prompt.startswith('black and white line art'))

    def test_legacy_own_line_size_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            prompt, size = load_prompt_file(_write(d, HEADER_LEGACY))
        self.assertEqual(size, '16:9')
        self.assertTrue(prompt.startswith('a wide scene'))

    def test_no_size_annotation_returns_none(self):
        """无尺寸标注 → None（上层回落默认正方形），不得误报或抛错。"""
        with tempfile.TemporaryDirectory() as d:
            _, size = load_prompt_file(_write(d, HEADER_NO_SIZE))
        self.assertIsNone(size)

    def test_no_prompt_line_falls_back_to_full_text(self):
        """没有 Prompt: 行时整文件兜底（load_prompt_file 的契约）。"""
        with tempfile.TemporaryDirectory() as d:
            text, size = load_prompt_file(_write(d, HEADER_MISSING_PROMPT))
        self.assertIn('尺寸: 1:1', text)
        self.assertEqual(size, '1:1')


class TestSizeMap(unittest.TestCase):
    def test_ratio_mapped_to_siliconflow_fixed_sizes(self):
        self.assertEqual(SIZE_MAP['9:16'], '720x1280')
        self.assertEqual(SIZE_MAP['16:9'], '1280x720')
        self.assertEqual(SIZE_MAP['1:1'], '1024x1024')

    def test_unknown_size_passes_through(self):
        """未知标注原样透传（generate 里 SIZE_MAP.get(size, size) 的语义，见 image.py）。"""
        self.assertEqual(SIZE_MAP.get('768x1024', '768x1024') or '1024x1024', '768x1024')
        self.assertEqual(SIZE_MAP.get('nonsense', 'nonsense') or '1024x1024', 'nonsense')


class TestWebpSave(unittest.TestCase):
    """_save_image 的 WebP 本地转码：有 Pillow 转 .webp（RIFF 容器），
    无 Pillow / 内容非图片时静默降级原格式，不抛错不中断。"""

    def test_webp_true_transcodes_to_webp(self):
        with tempfile.TemporaryDirectory() as d:
            out = _save_image(_PNG_1PX, f'{d}/portrait.png', webp=True)
            p = Path(out)
            self.assertEqual(p.suffix, '.webp')
            head = p.read_bytes()
            self.assertTrue(head.startswith(b'RIFF') and b'WEBP' in head[:12])

    def test_webp_false_keeps_source_format(self):
        with tempfile.TemporaryDirectory() as d:
            out = _save_image(_PNG_1PX, f'{d}/portrait.png', webp=False)
            self.assertEqual(Path(out).suffix, '.png')
            self.assertTrue(Path(out).read_bytes().startswith(b'\x89PNG'))

    def test_webp_decodes_bad_content_falls_back(self):
        """内容不是图片（如网关错误页）时 Pillow 解码失败 → 降级原样落盘。"""
        junk = b'<html>error page</html>'
        with tempfile.TemporaryDirectory() as d:
            out = _save_image(junk, f'{d}/x.png', webp=True)
            self.assertEqual(Path(out).read_bytes(), junk)

    def test_default_webp_off(self):
        """默认不开转码（mock 客户端不引 Pillow 依赖）。"""
        with tempfile.TemporaryDirectory() as d:
            out = _save_image(_PNG_1PX, f'{d}/x.png')
            self.assertEqual(Path(out).suffix, '.png')


class TestRetryPolicy(unittest.TestCase):
    def test_retryable_status_contains_429_and_5xx(self):
        self.assertEqual(SiliconFlowImage.RETRYABLE_STATUS, {429, 500, 502, 503, 504})

    def test_client_requires_key(self):
        """无 key 构造即抛 ImageError（agent._image_client 捕获后降级提示词模式）。"""
        import os
        from core.image import ImageError
        old = os.environ.pop('SILICONFLOW_API_KEY', None)
        try:
            with self.assertRaises(ImageError):
                SiliconFlowImage({'api_key_env': 'SILICONFLOW_API_KEY'})
        finally:
            if old is not None:
                os.environ['SILICONFLOW_API_KEY'] = old


if __name__ == '__main__':
    unittest.main()
