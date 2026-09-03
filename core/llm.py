# -*- coding: utf-8 -*-
"""LLM 客户端：DeepSeek API（OpenAI 兼容）+ Mock 客户端（测试/无 key 时）。

使用：llm.chat(messages, model='chat'|'reasoner', json_mode=bool) -> str
所有 worker 共享同一客户端，区别只在 model 档位与系统提示词。
"""
import json
import os
import queue as _queue
import signal
import threading
import time

import requests

from .utils import ROOT

# NWA_TRACE=1 时打印调用轨迹（诊断服务端僵死用，测试/CI 不设即零开销）
_T = os.environ.get('NWA_TRACE')


def _t(msg):
    if _T:
        print(f'[llm {time.monotonic():8.1f}] {msg}', flush=True)


class LLMError(Exception):
    pass


class _CallDeadline(Exception):
    """单次 LLM 调用的整体墙钟上限已到（服务端僵死保护，见 _install_deadline）。"""


def _alarm_handler(signum, frame):
    _t('SIGALRM 触发（硬期限到，抛 _CallDeadline）')
    raise _CallDeadline('单次 LLM 调用超时（服务端僵死）')


def _install_deadline(seconds):
    """主线程装 SIGALRM 硬期限：可打断主线程任何阻塞（socket/queue/锁等待），
    返回恢复函数；非主线程调用返回 None（此时 queue 层 deadline 仍兜底）。"""
    try:
        old = signal.signal(signal.SIGALRM, _alarm_handler)
    except ValueError:
        return None
    signal.setitimer(signal.ITIMER_REAL, seconds)

    def restore():
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    return restore


class DeepSeekClient:
    def __init__(self, config: dict):
        api = config.get('api', {})
        self.base_url = api.get('base_url', 'https://api.deepseek.com').rstrip('/')
        self.api_key = os.environ.get(api.get('api_key_env', 'DEEPSEEK_API_KEY'), '')
        self.chat_model = api.get('chat_model', 'deepseek-chat')
        self.reasoner_model = api.get('reasoner_model', 'deepseek-reasoner')
        self.timeout = api.get('timeout', 180)
        self.max_retries = api.get('max_retries', 3)
        self.retry_delay = api.get('retry_delay', 5)
        self.call_deadline = api.get('call_deadline', 480)  # 单次 chat 整体墙钟上限（秒）
        self._model = self.chat_model
        if not self.api_key:
            raise LLMError('未设置 DEEPSEEK_API_KEY 环境变量（可复制 .env.example 为 .env 并填写）')

    def model_for(self, tier: str) -> str:
        return self.reasoner_model if tier == 'reasoner' else self.chat_model

    def _post(self, url: str, payload: dict, deadline=None):
        """带整体期限的 POST：requests 的 per-op timeout 在服务端半死连接上可能
        长期不触发（2026-09 实录：qa_review 挂在 reasoner 上 35 分钟零字节），
        daemon 线程 + 切片式有界等待保证单次请求绝不超过 limit 秒；超时抛 LLMError。

        切片（2026-09 实录）：本环境（macOS 3.13.14 VM）长定时等待不可靠——
        180s 的 q.get(timeout=180) 与 480s SIGALRM 曾在真实僵死中 50 分钟不触发，
        而 ≤5s 的短等待实测可靠。故把总期限切成 5s 一段的 q.get + 单调钟累计，
        任一片段返回都检查累计；即使切片本身失效，chat() 外层的 SIGALRM 仍兜底。
        """
        limit = deadline or self.timeout
        q = _queue.Queue(maxsize=1)

        def worker():
            try:
                r = requests.post(url, json=payload, timeout=self.timeout, headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                })
            except BaseException as e:  # noqa: BLE001 —— daemon 线程里全部转交主线程
                q.put(('err', e))
            else:
                q.put(('ok', r))

        threading.Thread(target=worker, daemon=True).start()
        t0 = time.monotonic()
        n = 0
        while True:
            remaining = limit - (time.monotonic() - t0)
            if remaining <= 0:
                _t(f'_post 总期限 {limit}s 到（切片 {n} 次）')
                raise LLMError(f'请求超过 {limit}s 无响应（服务端僵死），放弃该次尝试')
            n += 1
            try:
                kind, val = q.get(timeout=min(remaining, 5))
                break
            except _queue.Empty:
                if n % 6 == 0:
                    _t(f'_post 切片等待中 {time.monotonic() - t0:.0f}s/{limit}s')
        if kind == 'err':
            raise val
        return val

    def chat(self, messages, model='chat', json_mode=False, temperature=0.7,
             max_tokens=None, extra=None) -> str:
        """调用 chat/completions：SIGALRM 硬期限 + 重试退避 + 空内容降级链。

        硬期限（2026-09 实录：服务端半死连接时 requests 超时可能长期不触发，
        qa 语义评审曾挂 35 分钟零字节）——主线程装 call_deadline 秒的 SIGALRM，
        无论阻塞在 socket/队列/锁上都保证单次调用整体有界，超时抛 LLMError。
        空内容降级链（v4-flash 批次长输出偶发空响应）：重试时先去掉 max_tokens
        （交服务端默认上限），仍空再去掉 response_format json 约束。
        """
        restore = _install_deadline(self.call_deadline)
        _t(f'chat enter model={model} deadline={self.call_deadline}s')
        try:
            out = self._chat_impl(messages, model, json_mode, temperature,
                                  max_tokens, extra)
            _t(f'chat ok {len(out)} chars')
            return out
        except _CallDeadline as e:
            _t('chat 捕获 _CallDeadline → LLMError')
            raise LLMError(f'单次调用超过 {self.call_deadline}s（服务端僵死），放弃') from e
        finally:
            if restore is not None:
                restore()

    def _chat_impl(self, messages, model, json_mode, temperature,
                   max_tokens, extra) -> str:
        url = f'{self.base_url}/chat/completions'
        base = {
            'model': self.model_for(model),
            'messages': messages,
            'temperature': temperature,
            'stream': False,
        }
        if max_tokens:
            base['max_tokens'] = max_tokens
        if extra:
            base.update(extra)

        last_err = None
        empty_streak = 0
        stall = 0  # 连续僵死超时计数：服务端整体故障时重试无意义，2 次即放弃
        _t(f'chat_impl enter max_retries={self.max_retries}')
        for attempt in range(self.max_retries + 1):
            _t(f'  attempt {attempt} start')
            payload = dict(base)
            # JSON 模式：chat 档支持 response_format；reasoner 用指令式
            if json_mode and model != 'reasoner':
                payload['response_format'] = {'type': 'json_object'}
            if empty_streak >= 1:
                payload.pop('max_tokens', None)
            if empty_streak >= 2:
                payload.pop('response_format', None)
            try:
                r = self._post(url, payload)
            except LLMError as e:  # 整体期限超时（僵死）
                stall += 1
                last_err = str(e)
                if stall >= 2:
                    break
            except requests.Timeout as e:  # per-op 超时：同样是无响应的僵死特征
                stall += 1
                last_err = str(e)
                if stall >= 2:
                    break
            except requests.RequestException as e:
                stall = 0
                last_err = str(e)
            else:
                stall = 0
                if r.status_code == 200:
                    data = r.json()
                    content = data['choices'][0]['message'].get('content', '') or ''
                    if content.strip():
                        return content
                    empty_streak += 1
                    last_err = ('模型返回空内容（尝试降级: '
                                + ('去 max_tokens → ' if empty_streak == 1 else '')
                                + ('去 response_format' if empty_streak >= 2 else '') + '）')
                elif r.status_code in (429, 500, 502, 503, 504):
                    last_err = f'HTTP {r.status_code}: {r.text[:200]}'
                else:
                    raise LLMError(f'API 错误 HTTP {r.status_code}: {r.text[:300]}')
            if attempt < self.max_retries and stall < 2:
                _t(f'  attempt {attempt} 失败退避 sleep '
                   f'{self.retry_delay * (2 ** attempt)}s (stall={stall})')
                time.sleep(self.retry_delay * (2 ** attempt))
        _t(f'chat_impl 退出，stall={stall} last_err={str(last_err)[:60]}')
        raise LLMError(f'请求失败（重试 {self.max_retries} 次后放弃）: {last_err}')

    def chat_json(self, messages, model='chat', temperature=0.7, max_tokens=None) -> dict:
        """调用并解析 JSON 输出；解析失败抛 LLMError。"""
        content = self.chat(messages, model=model, json_mode=True,
                            temperature=temperature, max_tokens=max_tokens)
        return parse_json_block(content)


def parse_json_block(content: str) -> dict:
    """稳健解析：先整体 json.loads，失败则提取第一个 {...} 块。"""
    s = content.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start = s.find('{')
    end = s.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError(f'无法解析 JSON 输出: {content[:200]}')


class MockLLM:
    """确定性 mock：按 (role, stage) 返回 fixtures/mock_data/<stage>.txt 或内置样例。
    用于无 API key 时的管线测试与 CI。"""

    def __init__(self, fixture_dir=None):
        self.fixture_dir = fixture_dir
        self.calls = []

    def _fixture(self, name):
        # 支持多目录回退：fixture_dir 可以是 str 或 list（前面的优先）
        dirs = self.fixture_dir if isinstance(self.fixture_dir, (list, tuple)) else [self.fixture_dir]
        for d in dirs:
            if d:
                p = os.path.join(d, f'{name}.txt')
                if os.path.exists(p):
                    return open(p, encoding='utf-8').read()
        return None

    def chat(self, messages, model='chat', json_mode=False, temperature=0.7,
             max_tokens=None, extra=None) -> str:
        self.calls.append({'model': model, 'messages': [m.get('content', '')[:60] for m in messages]})
        # 从最后一条用户消息里找 stage 标记
        joined = ' '.join(m.get('content', '') for m in messages if m.get('role') == 'user')
        stage = None
        for key in ('[STAGE:detect]', '[STAGE:extract]', '[STAGE:summarize]', '[STAGE:style]',
                    '[STAGE:characters]', '[STAGE:design]', '[STAGE:generate]', '[STAGE:repair]',
                    '[STAGE:qa_review]'):
            if key in joined:
                stage = key.strip('[]').split(':')[1]
                break
        if not stage:
            stage = 'fallback'
        fixture = self._fixture(stage)
        if fixture is not None:
            return fixture
        return MOCK_RESPONSES.get(stage, MOCK_RESPONSES['fallback'])

    def chat_json(self, messages, model='chat', temperature=0.7, max_tokens=None) -> dict:
        return parse_json_block(self.chat(messages, model, True, temperature, max_tokens))


MOCK_RESPONSES = {
    'detect': json.dumps({
        'mode_id': 'classic', 'theme_id': 'modern', 'chunk_strategy': '按章节分块',
        'genre': '现代都市', 'rationale': 'mock 测试固定返回经典叙事模式',
    }, ensure_ascii=False),
    'summarize': '这是 mock 摘要。\n新人物：无。\n关键事件：示例事件。',
    'style': '## 主旨\n测试小说的主旨是验证管线。\n\n## 世界观\n一个示例世界。\n\n'
             '## 情感基调\n中性，用于测试。\n\n## 风格定调\nmodern（理由：mock）',
    'characters': json.dumps({
        'characters': [
            {'name': '陈默', 'aliases': ['小陈'], 'gender': '男', 'identity': '主角，软件工程师',
             'traits': ['内向'], 'experiences': ['在沙龙认识苏晴'], 'relationships': ['苏晴：恋人'],
             'ending': '求婚成功', 'notes': []}
        ]}, ensure_ascii=False),
    'extract': json.dumps({
        'items': [{'summary': 'mock 提取：因主角赴约咖啡馆，雨夜重逢苏晴，两人关系破冰',
                   'motivation': '约定日久未兑现',
                   'characters': ['陈默', '苏晴'], 'location': '城南咖啡馆', 'time': '星期四晚',
                   'key_event': '雨夜重逢', 'emotion': '温暖', 'atmosphere': '雨打玻璃的暖黄灯光'}],
    }, ensure_ascii=False),
    'design': json.dumps({
        'game_title': '测试之书', 'subtitle': 'mock', 'player': '陈默',
        'attributes': [{'id': 'affection_suqing', 'label': '苏晴好感', 'min': 0, 'max': 100, 'start': 0, 'visible': True}],
        'decision_points': 2, 'scene_count': 2, 'endings': [{'title': '结局一', 'type': 'neutral'}],
    }, ensure_ascii=False),
    'generate': 'MOCK_GENERATE',
    'repair': 'MOCK_REPAIR',
    'qa_review': json.dumps({
        'score': 8, 'problems': [], 'praise': ['结构完整'],
    }, ensure_ascii=False),
    'fallback': '{}',
}
