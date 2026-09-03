# -*- coding: utf-8 -*-
"""图片生成客户端：SiliconFlow 硅基流动（OpenAI 兼容 /images/generations）。

使用：SiliconFlowImage(config).generate(prompt, out_path) -> Path
未配置 key 时上层捕获 ImageError 降级为仅提示词模式；MockImageClient
（写入真实 1x1 PNG）用于 --mock 管线测试，覆盖同样的落盘路径。
"""
import base64
import os
import queue as _queue
import re
import threading
import time
from pathlib import Path

import requests

from .utils import ROOT

# 提示词文件的尺寸标注 → SiliconFlow 固定尺寸档位
SIZE_MAP = {'9:16': '720x1280', '16:9': '1280x720', '1:1': '1024x1024'}
# 常见图片魔数（用于校验下载/解码结果确实是图片，而非 HTML 错误页）
_MAGIC = (b'\x89PNG', b'\xff\xd8', b'GIF8', b'BM')
# 魔数 → 扩展名（模型返回 JPEG 时落盘为 .jpg，避免 .png 里装 JPEG）
_MAGIC_EXT = ((b'\x89PNG', '.png'), (b'\xff\xd8', '.jpg'), (b'GIF8', '.gif'),
              (b'BM', '.bmp'))
_B64_HEADER = re.compile(r'^data:image/[a-zA-Z0-9.+-]+;base64,')


class ImageError(Exception):
    pass


def _bounded_request(fn, url, timeout, **kwargs):
    """线程 + 5s 切片有界请求（与 core/llm.py 同款防护）。

    2026-09 实录：requests 的 socket 超时在半死连接上长期不触发（曾挂 35 分钟
    零字节）；本环境（macOS 3.13 VM）长定时等待不可靠、≤5s 短等待可靠，故把
    总期限切成 5s 一段的 q.get + 单调钟累计，超时抛 ImageError（重试无意义）。
    """
    q = _queue.Queue(maxsize=1)

    def worker():
        try:
            r = fn(url, timeout=timeout, **kwargs)
        except BaseException as e:  # noqa: BLE001 —— daemon 线程异常转交主线程
            q.put(('err', e))
        else:
            q.put(('ok', r))

    threading.Thread(target=worker, daemon=True).start()
    t0 = time.monotonic()
    while True:
        remaining = timeout - (time.monotonic() - t0)
        if remaining <= 0:
            raise ImageError(f'请求超过 {timeout}s 无响应（服务端僵死），放弃该次尝试')
        try:
            kind, val = q.get(timeout=min(remaining, 5))
            break
        except _queue.Empty:
            continue
    if kind == 'err':
        raise val
    return val


class SiliconFlowImage:
    # 可重试的 HTTP 状态：限流与网关抖动；4xx（参数/鉴权/余额）重试无意义
    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, config: dict):
        cfg = config or {}
        self.base_url = cfg.get('base_url', 'https://api.siliconflow.cn/v1').rstrip('/')
        self.api_key = os.environ.get(cfg.get('api_key_env', 'SILICONFLOW_API_KEY'), '')
        self.model = cfg.get('model', 'black-forest-labs/FLUX.1-schnell')
        self.default_size = cfg.get('size', '1024x1024')
        self.timeout = cfg.get('timeout', 180)
        self.max_retries = cfg.get('max_retries', 3)
        self.retry_delay = cfg.get('retry_delay', 3)
        # WebP 转码（本地，Pillow）：API 只吐 PNG/JPEG，落盘时统一转 .webp
        # （体积约为 PNG 的 1/5~1/10，插画视觉无损档 q82）；无 Pillow 自动降级
        self.webp = bool(cfg.get('webp', True))
        self.webp_quality = int(cfg.get('webp_quality', 82) or 82)
        if not self.api_key:
            raise ImageError('未设置 SILICONFLOW_API_KEY 环境变量（可复制 .env.example 为 .env 并填写）')

    def generate(self, prompt: str, out_path, size: str = None) -> str:
        """文生图并保存到 out_path，返回保存路径。失败抛 ImageError。

        重试策略：网络错误与 429/5xx 退避重试（max_retries 次）；
        4xx 参数/鉴权类、以及响应结构异常（无图数据）立即失败——重试不会变好。
        """
        url = f'{self.base_url}/images/generations'
        payload = {
            'model': self.model,
            'prompt': prompt,
            'image_size': SIZE_MAP.get(size, size) or self.default_size,
            'batch_size': 1,
        }
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                r = _bounded_request(requests.post, url, self.timeout, json=payload, headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                })
            except requests.RequestException as e:
                last_err = f'网络错误: {e}'  # 可重试
            else:
                if r.status_code != 200:
                    err = f'HTTP {r.status_code}: {r.text[:200]}'
                    if r.status_code not in self.RETRYABLE_STATUS:
                        raise ImageError(f'生图失败（{err}）——非可重试错误（4xx 参数/鉴权/余额），不重试')
                    last_err = err
                else:
                    try:
                        data = r.json()
                        items = data.get('images') or data.get('data') or []
                        if not items:
                            raise ImageError(f'响应无图片数据: {str(data)[:200]}')
                        raw = items[0].get('url') or items[0].get('b64_json') or ''
                        if not raw:
                            raise ImageError(f'响应条目缺少 url/b64_json: {str(items[0])[:200]}')
                        content = self._to_bytes(raw)
                        return _save_image(content, out_path,
                                           webp=self.webp, quality=self.webp_quality)
                    except ImageError as e:
                        # 结构异常（无图数据）不重试；下载/解码失败（网络侧）可重试
                        if not str(e).startswith(('下载图片失败', '图片数据', '返回内容不是图片')):
                            raise
                        last_err = str(e)
            if attempt < self.max_retries:
                time.sleep(min(self.retry_delay * (2 ** attempt), 30))
        raise ImageError(f'生图失败（重试 {self.max_retries} 次后放弃）: {last_err}')

    def _to_bytes(self, raw: str) -> bytes:
        if raw.startswith('data:'):
            raw = _B64_HEADER.sub('', raw)
            return _check(base64.b64decode(raw))
        if raw.startswith('http'):
            r = _bounded_request(requests.get, raw, self.timeout)
            if r.status_code != 200:
                raise ImageError(f'下载图片失败 HTTP {r.status_code}')
            return _check(r.content)
        # 无前缀的裸 base64
        try:
            return _check(base64.b64decode(raw))
        except Exception:
            raise ImageError('图片数据既不是 URL 也不是 base64')


class MockImageClient:
    """mock：不调网络，写一个真实的最小 PNG（1x1），覆盖落盘路径。"""

    def __init__(self, config: dict = None):
        pass

    def generate(self, prompt: str, out_path, size: str = None) -> str:
        content = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
            'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
        return _save_image(content, out_path)


def load_prompt_file(prompt_path) -> tuple:
    """从 .prompt.txt 提取 (Prompt 行, 尺寸标注)；没有则整文件兜底。

    头行是 _fmt_prompt 写的一行式 `# kind / 画风: X (id) / 尺寸: 9:16`，
    也兼容旧式独立 `# 尺寸: 9:16` 行；两种都没匹配到返回 (全文, None)。
    """
    text = prompt_path.read_text(encoding='utf-8')
    prompt, size = None, None
    for line in text.splitlines():
        if line.startswith('Prompt: '):
            prompt = line[len('Prompt: '):].strip()
        m = re.search(r'尺寸:\s*(\S+)', line)
        if m:
            size = m.group(1)
    return (prompt or text.strip()), size


def _check(content: bytes) -> bytes:
    if not content or not any(content.startswith(m) for m in _MAGIC):
        raise ImageError('返回内容不是图片（可能被网关替换为错误页）')
    return content


def _save_image(content: bytes, out_path, webp: bool = False, quality: int = 82) -> str:
    """图片内容落盘，返回实际保存路径。

    webp=True 时本地转码为 .webp（体积约为 PNG 的 1/5~1/10）；
    未装 Pillow 或内容无法解码时自动降级为原格式落盘，不中断流程。
    扩展名跟随实际格式（JPEG 内容 → .jpg，转码 → .webp），调用方需回写引用。
    """
    p = Path(out_path)
    if not p.is_absolute():
        p = ROOT / p
    if webp:
        try:
            from io import BytesIO
            from PIL import Image
            img = Image.open(BytesIO(content))
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGBA' if img.mode == 'P' else 'RGB')
            p = p.with_suffix('.webp')
            p.parent.mkdir(parents=True, exist_ok=True)
            img.save(p, 'WEBP', quality=quality)
            return str(p)
        except Exception:
            pass  # 无 Pillow / 解码失败 → 走下方原格式落盘
    # 扩展名跟随实际格式（模型返回 JPEG 时 .png 路径自动改为 .jpg）
    ext = next((e for m, e in _MAGIC_EXT if content.startswith(m)), None)
    if ext and p.suffix.lower() != ext:
        p = p.with_suffix(ext)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return str(p)
