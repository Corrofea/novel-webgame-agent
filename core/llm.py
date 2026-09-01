# -*- coding: utf-8 -*-
"""LLM 客户端：DeepSeek API（OpenAI 兼容）+ Mock 客户端（测试/无 key 时）。

使用：llm.chat(messages, model='chat'|'reasoner', json_mode=bool) -> str
所有 worker 共享同一客户端，区别只在 model 档位与系统提示词。
"""
import json
import os
import time

import requests

from .utils import ROOT


class LLMError(Exception):
    pass


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
        self._model = self.chat_model
        if not self.api_key:
            raise LLMError('未设置 DEEPSEEK_API_KEY 环境变量（可复制 .env.example 为 .env 并填写）')

    def model_for(self, tier: str) -> str:
        return self.reasoner_model if tier == 'reasoner' else self.chat_model

    def chat(self, messages, model='chat', json_mode=False, temperature=0.7,
             max_tokens=None, extra=None) -> str:
        """调用 chat/completions，带重试与退避。返回内容字符串。"""
        url = f'{self.base_url}/chat/completions'
        payload = {
            'model': self.model_for(model),
            'messages': messages,
            'temperature': temperature,
            'stream': False,
        }
        if max_tokens:
            payload['max_tokens'] = max_tokens
        # JSON 模式：deepseek-chat 支持 response_format；reasoner 用指令式
        if json_mode and model != 'reasoner':
            payload['response_format'] = {'type': 'json_object'}
        if extra:
            payload.update(extra)

        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(url, json=payload, timeout=self.timeout, headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                })
                if r.status_code == 200:
                    data = r.json()
                    return data['choices'][0]['message']['content']
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = f'HTTP {r.status_code}: {r.text[:200]}'
                else:
                    raise LLMError(f'API 错误 HTTP {r.status_code}: {r.text[:300]}')
            except requests.RequestException as e:
                last_err = str(e)
            if attempt < self.max_retries:
                time.sleep(self.retry_delay * (2 ** attempt))
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
        for key in ('[STAGE:detect]', '[STAGE:summarize]', '[STAGE:style]', '[STAGE:characters]',
                    '[STAGE:design]', '[STAGE:generate]', '[STAGE:repair]', '[STAGE:qa_review]'):
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
        'mode_id': 'g1_narrative', 'theme_id': 'modern', 'chunk_strategy': '按章节分块',
        'genre': '现代都市', 'rationale': 'mock 测试固定返回叙事冒险模式',
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
