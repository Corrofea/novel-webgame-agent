# -*- coding: utf-8 -*-
"""ReAct 修复循环：LLM 输出 → 校验器观察 → 修订，最多 N 轮。

设计原则：LLM 不直接读写文件系统，所有"行动"以结构化输出呈现，
由编排层（agent.py）负责落盘与校验；校验结果作为观察回传给 LLM。
"""
import json


class ReactResult:
    def __init__(self, content, ok, rounds, errors):
        self.content = content
        self.ok = ok          # 最终是否通过校验
        self.rounds = rounds  # 实际轮数（1..max_rounds）
        self.errors = errors  # 最后一轮的错误清单


def react_loop(llm, system_prompt, task, validator, max_rounds=3,
               model='chat', json_mode=True, temperature=0.7) -> ReactResult:
    """通用 ReAct 循环。

    validator(content) -> (ok: bool, errors: list[str])
    失败时把错误清单回传，要求 LLM 修正后重新输出完整结果。
    """
    messages = [{'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': task}]
    content, errors = '', []
    for round_no in range(1, max_rounds + 1):
        if json_mode:
            content = llm.chat(messages, model=model, json_mode=True,
                               temperature=temperature, max_tokens=8000)
        else:
            content = llm.chat(messages, model=model, json_mode=False,
                               temperature=temperature, max_tokens=8000)
        try:
            ok, errors = validator(content)
        except Exception as e:  # 校验器自身异常视为失败
            ok, errors = False, [f'校验器异常: {e}']
        if ok:
            return ReactResult(content, True, round_no, [])
        messages.append({'role': 'assistant', 'content': content})
        messages.append({'role': 'user', 'content': (
            '校验未通过，以下是具体问题清单（逐条对照修正，输出必须仍是完整结果，不能只给修改说明）：\n'
            + json.dumps(errors, ensure_ascii=False, indent=1)
        )})
    return ReactResult(content, False, max_rounds, errors)


def json_validator(require_fields=None):
    """基础 JSON 校验器：解析 JSON + 必需字段检查。"""
    def validate(content):
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return False, [f'JSON 语法错误: {e}']
        if not isinstance(data, dict):
            return False, ['输出必须是 JSON 对象']
        if require_fields:
            missing = [f for f in require_fields if f not in data]
            if missing:
                return False, [f'缺少必需字段: {missing}']
        return True, []
    return validate
