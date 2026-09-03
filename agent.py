#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel-webgame-agent 主入口与编排器（plan-execute + 阶段内 ReAct）。

用法:
    python agent.py 小说.txt                  # 正常执行（需 DEEPSEEK_API_KEY）
    python agent.py 小说.txt --mock           # 用 mock LLM 跑通管线（测试/无 key）
    python agent.py 小说.txt --resume         # 从断点继续（自动定位该书最近一次运行）
    python agent.py 小说.txt --run-id xyz     # 指定运行 id（幂等续跑/测试确定性）

输入：小说文件（TXT/MD/EPUB，均为标准库处理）
输出：games/<run_id>/ 游戏文件夹 + archive/<run_id>.zip 打包存档

隔离：每次调用生成独立的 run_id（<book_id>_<时间戳>），games/ 是游戏库——
每个 run_id 一个文件夹，同书多次运行互不覆盖；runtime/ 中间产物同样按
run_id 隔离，--resume 自动定位该书最近一次运行（或 --run-id 显式指定）。

流程（plan 实例化阶段序列，execute 逐阶段执行，每阶段写检查点）：
  ingest → detect → chunk → [summarize(长篇)] → characters → style
  → game_init → design → generate(分批) → qa(含修复循环) → illustrate → package
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from core.contracts import (run_qa_scripts, validate_characters, validate_detect,
                            validate_extract, validate_style)
from core.llm import DeepSeekClient, MockLLM, LLMError
from core.react import ReactResult, json_validator, react_loop
from core.utils import (ROOT, clamp_context, load_dotenv, read_json, read_text,
                        slugify, write_json, write_text)
from core.workers import get_worker

VERSION = '0.1.0'
LONG_NOVEL_CHARS = 80000
GENERATE_BATCH_SCENES = 10


# ---------------------------------------------------------------- 数据包写盘

def apply_patch(game_dir: Path, patch: dict) -> list:
    """把生成/修复包写进 games/<book_id>/data/。返回写盘错误列表。

    patch 契约（顶层字段可选，缺省不动）：
      game/mode/characters         整体替换对应数据对象
      scenes                       {"s001": {...}, "s009": null} 逐节点合并，null=删除
      events                       命运事件池（list），整体替换 SCENES.events
      theme                       忽略（theme.js 由 game_init 按 detect 主题写死；
                                   patch 里出现该键静默不写——LLM 无视觉调色通道）
    """
    errors = []
    data_dir = game_dir / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    for key in ('game', 'mode', 'characters'):
        if key in patch and patch[key] is not None:
            _write_js(data_dir / f'{key}.js', key.upper(), patch[key])
    scenes = patch.get('scenes')
    events = patch.get('events')
    if scenes is not None or events is not None:
        path = data_dir / 'scenes.js'
        existing = _load_js(path, 'SCENES', {})
        merged = dict(existing.get('scenes', {}))
        if scenes is not None:
            for sid, node in scenes.items():
                if node is None:
                    merged.pop(sid, None)
                else:
                    merged[sid] = node
        out = {'scenes': merged}
        if events is not None:
            out['events'] = events
        else:
            out['events'] = existing.get('events', [])
        _write_js(path, 'SCENES', out)
    return errors


def _write_js(path: Path, var: str, obj):
    path.write_text(f'/* 由 agent 生成，勿手改 */\nwindow.{var} = '
                    + json.dumps(obj, ensure_ascii=False, indent=2) + ';\n', encoding='utf-8')


def _load_js(path: Path, var: str, default):
    if not path.exists():
        return default
    text = path.read_text(encoding='utf-8')
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end <= start:
        return default
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return default


def theme_card(theme_id: str) -> str:
    """主题目录卡（theme 2.0 提示词注入用）：只给 id/中文名/气质/适用。

    模板 JSON 已无任何色值；配色与质感由引擎 CSS 的 style-<id> 块自动套用，
    LLM 不需要也不允许输出 theme 相关字段（见 generate.md 硬性规则）。
    """
    tpl = read_json(ROOT / 'templates' / 'themes' / f'{theme_id}.json')
    return (f'id: {theme_id}\n'
            f'风格: {tpl.get("name", "")}\n'
            f'气质: {tpl.get("气质", "")}\n'
            f'适用: {tpl.get("适用", "")}\n'
            '视觉由引擎自动套用，输出中禁止出现 theme/colors/fonts 字段')


def _extract_epub_text(epub_path: Path, out_path: Path):
    """EPUB → 纯文本（标准库实现，无需 ebooklib）。

    按 content.opf 的 spine 顺序提取各 html 章节，去标签后拼接。
    无法解析 spine 时退化为按文件名排序。
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile

    OPF_NS = '{http://www.idpf.org/2007/opf}'
    try:
        zf = zipfile.ZipFile(epub_path)
        names = [n for n in zf.namelist() if not n.endswith('/')]
        # 找 content.opf
        opf_name = next((n for n in names if n.endswith('.opf')), None)
        order = []
        if opf_name:
            root = ET.fromstring(zf.read(opf_name))
            manifest = {}
            for it in root.iter(OPF_NS + 'item'):
                manifest[it.get('id')] = it.get('href')
            spine = root.find(OPF_NS + 'spine')
            if spine is not None:
                base = opf_name.rsplit('/', 1)[0] if '/' in opf_name else ''
                for ref in spine.iter(OPF_NS + 'itemref'):
                    href = manifest.get(ref.get('idref'), '')
                    if href:
                        order.append(f'{base}/{href}' if base else href)
        if not order:
            order = sorted(n for n in names if n.endswith(('.html', '.xhtml', '.htm')))
        text = []
        for name in order:
            try:
                raw = zf.read(name).decode('utf-8', errors='ignore')
            except KeyError:
                continue
            body = re.search(r'<body[^>]*>(.*)</body>', raw, re.S)
            chunk = body.group(1) if body else raw
            chunk = re.sub(r'<[^>]+>', '\n', chunk)
            chunk = re.sub(r'&nbsp;?', ' ', chunk)
            chunk = re.sub(r'&amp;', '&', chunk)
            chunk = re.sub(r'\n{3,}', '\n\n', chunk).strip()
            if chunk:
                text.append(chunk)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text('\n\n'.join(text), encoding='utf-8')
    except (zipfile.BadZipFile, OSError, ET.ParseError) as e:
        raise RuntimeError(f'EPUB 解析失败: {e}')


def _fmt_prompt(r: dict) -> str:
    return (f"# {r['kind']} / 画风: {r['style_name']} ({r['style_id']}) / 尺寸: {r['size']}\n"
            f"Prompt: {r['prompt']}\n\nNegative: {r['negative_prompt']}\n")


def _envelope_validator(game_dir: Path, full_check: bool):
    """生成/修复输出的校验器：解析 → 写盘 → （末批）跑完整 QA。"""
    def validator(content):
        try:
            env = json.loads(content)
        except json.JSONDecodeError as e:
            return False, [f'JSON 语法错误: {e}']
        patch = env.get('patch') if 'patch' in env else env.get('data')
        if not isinstance(patch, dict):
            return False, ['输出必须是 {"patch": {...}} 或 {"data": {...}} 结构']
        errors = apply_patch(game_dir, patch)
        if errors:
            return False, errors
        if not full_check:
            return True, []
        report = run_qa_scripts(game_dir)
        if not report['ok']:
            return False, [f"{i['severity']} {i['file']}: {i['message']}"
                           for i in report['issues'] if i['severity'] == 'error']
        return True, []
    return validator


# ---------------------------------------------------------------- 编排器

class NovelAgent:
    def __init__(self, novel_path: str, config: dict, mock: bool = False, resume: bool = False,
                 mock_dirs: list = None, run_id: str = None):
        self.novel_path = Path(novel_path)
        if not self.novel_path.exists():
            raise SystemExit(f'小说文件不存在: {self.novel_path}')
        self.config = config
        self.mock = mock
        self.resume = resume
        self._mock_dirs = [Path(d) for d in (mock_dirs or [])]
        self.title = self.novel_path.stem
        self.book_id = slugify(self.title)
        # run_id：每次调用唯一（游戏库隔离）。显式指定 > resume 找最近 > 新时间戳
        if run_id:
            self.run_id = run_id
        elif self.resume:
            self.run_id = self._latest_run_id()
        else:
            self.run_id = self._new_run_id()
        self.work_dir = ROOT / 'runtime' / self.run_id
        self.game_dir = ROOT / 'games' / self.run_id
        self.state_path = self.work_dir / 'state.json'
        self.state = self._load_state()
        default_fixtures = ROOT / 'tests' / 'fixtures' / 'mock_data'
        # 自定义 fixture 目录优先（可覆盖同名的默认桩）
        self.llm = MockLLM(self._mock_dirs + [default_fixtures]) if mock else \
            DeepSeekClient(config)
        self._reasoner_down = False  # reasoner 连续失败熔断：语义评审只证明一次

    # ---- run_id 生成与定位 ----
    def _new_run_id(self) -> str:
        """新运行：<book_id>_<时间戳>；同秒撞名时加 -2/-3 后缀。"""
        ts = datetime.now().strftime('%Y%m%d-%H%M%S')
        candidate = f'{self.book_id}_{ts}'
        n = 2
        while (ROOT / 'runtime' / candidate).exists():
            candidate = f'{self.book_id}_{ts}-{n}'
            n += 1
        return candidate

    def _latest_run_id(self) -> str:
        """resume 定位：该书（book_id 或 book_id_<时间戳>）最近一次运行。

        严格匹配 run_id 形态（_new_run_id 的产出），避免把其他书的目录
        （如 tiny_novel_bad 之于 tiny_novel）误判为本书运行。
        时间戳后缀可字典序排序（YYYYMMDD-HHMMSS），直接取 max。
        """
        import re
        base = ROOT / 'runtime'
        pat = re.compile(r'^' + re.escape(self.book_id) + r'(_\d{8}-\d{6}(-\d+)?)?$')
        candidates = [d.name for d in base.iterdir() if d.is_dir() and pat.match(d.name)]
        if not candidates:
            raise SystemExit(
                f'--resume 但 runtime/ 下找不到 {self.book_id} 的历史运行'
                f'（首次运行请去掉 --resume）')
        return max(candidates)

    # ---- 状态与检查点 ----
    def _load_state(self) -> dict:
        if self.resume and self.state_path.exists():
            return read_json(self.state_path)
        return {'book_id': self.book_id, 'run_id': self.run_id, 'title': self.title,
                'done': [], 'mode': None, 'qa_rounds': 0, 'plan_id': None}

    def _checkpoint(self, stage: str):
        if stage not in self.state['done']:
            self.state['done'].append(stage)
        write_json(self.state_path, self.state)

    def _done(self, stage: str) -> bool:
        return self.resume and stage in self.state['done']

    # ---- 工具 ----
    def _run(self, *cmd):
        r = subprocess.run([sys.executable, *[str(c) for c in cmd]],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f'命令失败 {" ".join(cmd)}: {r.stderr[:500]}')
        return r.stdout

    def _llm_stage(self, stage: str, worker_name: str, task: str,
                   validator, model_hint=None) -> ReactResult:
        worker = get_worker(worker_name)
        model = model_hint or self.config['workers'].get(worker_name, {}).get('model', 'chat')
        result = react_loop(self.llm, worker['system_prompt'], task, validator,
                            max_rounds=3, model=model)
        if not result.ok:
            print(f'⚠ {stage} 阶段 {result.rounds} 轮后仍未通过校验')
            if model != 'chat':
                print('  （深模型校验失败，可能输出格式问题；考虑降级重试或人工介入）')
        return result

    def _design_validator(self):
        """design 校验器：基础 JSON + 必需字段 + scene_blueprint 规模上限。

        场景数必须按原文素材量缩放（2026-09 实录：1149 字原文被设计了 20 个场景，
        每场景仅 57 字原文支撑——提示词里的"宁少勿滥"管不住生成，这里硬性拦截）。
        上限与 design.md 一致，给 2 个容差：<3000 字 → ≤12；<1.5 万字 → ≤16；否则 ≤40。
        """
        try:
            chapters = read_json(self.work_dir / 'chapters.json')
            n_chars = int(chapters.get('cleaned_chars', 0))
        except Exception:
            n_chars = 0
        hi = 12 if n_chars < 3000 else (16 if n_chars < 15000 else 40)

        def validate(content):
            ok, errors = json_validator(['game_title', 'scene_blueprint',
                                         'endings', 'attributes'])(content)
            if not ok:
                return False, errors
            data = json.loads(content)
            n = len(data.get('scene_blueprint') or [])
            if n > hi:
                return False, [f'scene_blueprint 有 {n} 个场景，超过原文 {n_chars} 字的'
                               f'合理上限 {hi}（每场景约需 80~150 字原文支撑，'
                               '宁少勿滥，禁止凭空编造主线）。请删减/合并到该范围内重出']
            return True, []
        return validate

    def _read_source_text(self, budget=40000) -> str:
        """读取用于 LLM 阶段的原文（短篇：分块；长篇：摘要）。"""
        chunks = read_json(self.work_dir / 'chunks.json')['chunks']
        return clamp_context('\n\n'.join(c['text'] for c in chunks), budget)

    # ---- 阶段执行 ----
    def run(self):
        print(f'== novel-webgame-agent v{VERSION} ==')
        print(f'小说: {self.title}  运行: {self.run_id}  工作目录: runtime/{self.run_id}/')
        if self.mock:
            print('（mock 模式：LLM 输出为测试桩）')

        # ===== EXECUTE：先 ingest（计划基于清洗后的真实字数） =====
        if not self._done('ingest'):
            print('\n[执行] ingest')
            self.stage_ingest()
            self._checkpoint('ingest')
            print('[完成] ingest')
        else:
            print('[跳过] ingest（已完成）')

        # ===== PLAN：基于 ingest 事实实例化阶段序列 =====
        chapters = read_json(self.work_dir / 'chapters.json')
        total_chars = chapters['cleaned_chars']
        plan_id = 'long_novel' if total_chars >= LONG_NOVEL_CHARS else 'short_novel'
        self.state['plan_id'] = plan_id
        plan = read_json(ROOT / 'templates' / 'plans' / f'{plan_id}.json')
        stages = [s['name'] for s in plan['stages']]
        print(f'计划: {plan_id}（{total_chars} 字） 阶段序列: {" → ".join(stages)}')

        # ===== EXECUTE：其余阶段 =====
        for stage in stages:
            if stage == 'ingest' or self._done(stage):
                if stage != 'ingest':
                    print(f'[跳过] {stage}（已完成）')
                continue
            print(f'\n[执行] {stage}')
            fn = getattr(self, f'stage_{stage}')
            try:
                fn()
            except LLMError as e:
                # 阶段级 LLM 故障不留下裸 traceback：checkpoint 未写 → resume 会从本阶段续
                raise RuntimeError(f'{stage} 阶段 LLM 请求失败（可 --resume 续跑，'
                                   f'已完成阶段不会重做）: {e}') from None
            self._checkpoint(stage)
            print(f'[完成] {stage}')

        print('\n== 全部阶段完成 ==')
        print(f'游戏文件夹: {self.game_dir}/')
        print(f'打包存档:   {ROOT / "archive" / (self.run_id + ".zip")}')

    # ---- 各阶段 ----
    def stage_ingest(self):
        source = self.novel_path
        if source.suffix.lower() == '.epub':
            txt = self.work_dir / 'source.txt'
            _extract_epub_text(source, txt)
            print(f'EPUB 已提取: {len(txt.read_text(encoding="utf-8"))} 字 → source.txt')
            source = txt
        self._run(ROOT / 'skills' / 'text-processing' / 'scripts' / 'chunker.py',
                  '--ingest', source, '--out', self.work_dir)

    def stage_detect(self):
        chapters = read_json(self.work_dir / 'chapters.json')
        head = clamp_context('\n\n'.join(
            c['text'] for c in chapters['chapters'][:6]), 8000)
        task = ('[STAGE:detect]\n以下是小说开头（供类型分析）：\n\n' + head +
                '\n\n请按 detect.md 的判断流程输出 JSON。')
        result = self._llm_stage('detect', 'detect', task,
                                 json_validator(['mode_id', 'theme_id', 'chunk_strategy']))
        if not result.ok:
            raise RuntimeError(f'detect 输出未通过校验: {result.errors[:3]}')
        data = json.loads(result.content)
        ok, errors = validate_detect(data)
        if not ok:
            raise RuntimeError(f'detect 校验失败: {errors}')
        self.state['mode'] = data
        write_json(self.work_dir / 'mode.json', data)
        print(f'模式: {data["mode_id"]}  主题: {data["theme_id"]}  类型: {data.get("genre", "")}')

    def stage_chunk(self):
        mode_id = self.state['mode']['mode_id']
        self._run(ROOT / 'skills' / 'text-processing' / 'scripts' / 'chunker.py',
                  '--chunk', '--chapters-json', self.work_dir / 'chapters.json',
                  '--mode', mode_id, '--out', self.work_dir)

    def stage_extract(self):
        """按模式特异性解构文本：逐块 LLM 提取 → bible/extract_<mode_id>.json。

        模式差异（提取任务与输出结构）来自 templates/game_modes/<id>.json 的
        extraction 字段；单块失败重试 1 次后跳过（降级不阻断），产物供 design 使用。
        """
        mode_id = self.state['mode']['mode_id']
        mode_tpl = read_json(ROOT / 'templates' / 'game_modes' / f'{mode_id}.json')
        extraction = mode_tpl.get('extraction')
        if not extraction:
            print(f'⚠ 模式 {mode_id} 无 extraction 配置，跳过文本解构')
            return
        chunks = read_json(self.work_dir / 'chunks.json')['chunks']
        worker = get_worker('extract')
        schema = json.dumps(extraction.get('schema', {}), ensure_ascii=False, indent=1)
        items, failures = [], []
        for c in chunks:
            task = ('[STAGE:extract]\n'
                    f'游戏模式：{mode_id}（{mode_tpl.get("name", "")}）\n'
                    f'提取任务：{extraction["task"]}\n'
                    f'输出结构参照（items 数组中每个元素的字段以此为准）：\n{schema}\n\n'
                    f'### 文本块 {c["id"]}\n{clamp_context(c["text"], 9000)}'
                    '\n\n输出严格 JSON：{"items": [...]}。只提取本块实际出现的内容，'
                    '不确定的字段用空字符串/空数组，不要编造。')
            content, last_err = None, None
            for _ in range(2):  # 单次调用 + 1 次重试；仍失败则跳过该块
                try:
                    # v4-flash 推理模型会先消耗 max_tokens 做 reasoning，
                    # 4000 不够（推理耗尽 → 空 content 失败），与 react.py 同款问题
                    content = self.llm.chat_json([{'role': 'user', 'content': task}],
                                                 model='chat', max_tokens=16000)
                    break
                except LLMError as e:
                    last_err = e
            if content is None or not isinstance(content.get('items'), list) or not content['items']:
                failures.append(c['id'])
                if content is not None and not content.get('items'):
                    last_err = 'items 为空'
                print(f'  ⚠ 块 {c["id"]} 解构失败（跳过）: {last_err}')
                continue
            items.extend(content['items'])
        out = {'mode_id': mode_id, 'items': items, 'failed_blocks': failures}
        write_json(self.work_dir / 'bible' / f'extract_{mode_id}.json', out)
        ok, errors = validate_extract(out, mode_id)
        if failures:
            print(f'⚠ 文本解构: {len(chunks) - len(failures)}/{len(chunks)} 块成功，'
                  f'失败块: {failures}（素材可能不完整，design 可容忍）')
        if not ok:
            print(f'⚠ 解构产物结构问题: {errors[:3]}（不阻断管线）')
        print(f'文本解构: {len(items)} 条素材 → bible/extract_{mode_id}.json')

    def stage_summarize(self):
        chunks = read_json(self.work_dir / 'chunks.json')['chunks']
        worker = get_worker('summarize')
        lines = []
        for c in chunks:
            task = ('[STAGE:summarize]\n' + worker['system_prompt']
                    + '\n\n### 分块 c' + str(c['id']) + '\n' + clamp_context(c['text'], 9000))
            content = self.llm.chat([{'role': 'user', 'content': task}],
                                    model='chat', json_mode=False, max_tokens=600)
            lines.append(f'--- 块 {c["id"]} ---\n{content.strip()}')
        write_text(self.work_dir / 'bible' / 'summaries.md', '\n\n'.join(lines))
        print(f'分卷摘要: {len(chunks)} 块 → bible/summaries.md')

    def stage_characters(self):
        source = self._read_source_text(30000)
        task = ('[STAGE:characters]\n根据 novel-character-cards skill 整理人物信息。'
                '以下是文本：\n\n' + source +
                '\n\n输出严格 JSON：{"characters": [{"name","aliases","gender","identity",'
                '"traits","experiences","relationships","ending","notes"}]}')
        result = self._llm_stage('characters', 'characters', task,
                                 json_validator(['characters']))
        if not result.ok:
            raise RuntimeError(f'characters 输出未通过校验: {result.errors[:3]}')
        data = json.loads(result.content)
        ok, errors = validate_characters(data)
        if not ok:
            raise RuntimeError(f'人物卡校验失败: {errors[:3]}')
        write_json(self.work_dir / 'characters.json', data)
        print(f'人物: {len(data["characters"])} 人')

    def stage_style(self):
        source = self._read_source_text(40000)
        task = ('[STAGE:style]\n' + source +
                '\n\n按 style.md 的格式提炼主旨/世界观/情感基调，输出 Markdown。')
        worker = get_worker('style')
        content = self.llm.chat([{'role': 'user', 'content': task}],
                                model='chat', json_mode=False, max_tokens=2000)
        ok, errors = validate_style(content)
        if not ok:
            print(f'⚠ style 输出缺小节: {errors}（继续执行，下游可容忍）')
        write_text(self.work_dir / 'bible' / 'world.md', content)
        print('world.md 已生成')

    def stage_game_init(self):
        self._run(ROOT / 'tools' / 'game_init.py',
                  '--book-id', self.book_id, '--run-id', self.run_id,
                  '--title', self.title, '--game-dir', self.game_dir)

    def stage_design(self):
        world = read_text(self.work_dir / 'bible' / 'world.md')
        chars = read_json(self.work_dir / 'characters.json')
        mode_id = self.state['mode']['mode_id']
        mode_tpl = read_json(ROOT / 'templates' / 'game_modes' / f'{mode_id}.json')
        theme_id = self.state['mode']['theme_id']
        task = ('[STAGE:design]\n'
                f'游戏模式模板：\n{json.dumps(mode_tpl, ensure_ascii=False, indent=1)}\n\n'
                f'主题（视觉基调，仅作氛围把握）：\n{theme_card(theme_id)}\n\n'
                f'世界观圣经：\n{clamp_context(world, 20000)}\n\n'
                f'人物卡：\n{json.dumps(chars, ensure_ascii=False, indent=1)}\n\n')
        # 文本解构素材（extract 阶段产物）：作为场景蓝图的素材库，存在才注入
        extract_path = self.work_dir / 'bible' / f'extract_{mode_id}.json'
        if extract_path.exists():
            extract_data = read_json(extract_path)
            task += (f'文本解构素材（按此设计场景蓝图——数量按素材量缩放见 design.md，'
                     f'宁少勿滥，严禁为凑场景凭空编造主线）：\n'
                     f'{clamp_context(json.dumps(extract_data, ensure_ascii=False, indent=1), 20000)}\n\n')
        task += '按 design.md 输出设计 brief（严格 JSON）。'
        result = self._llm_stage('design', 'design', task,
                                 self._design_validator())
        if not result.ok:
            raise RuntimeError(f'design 输出未通过校验: {result.errors[:3]}')
        data = json.loads(result.content)
        write_json(self.work_dir / 'design' / 'brief.json', data)
        blueprint = data.get('scene_blueprint', [])
        print(f'设计 brief: {data["game_title"]}，{len(blueprint)} 个场景蓝图，'
              f'{len(data["endings"])} 个结局')

    def stage_generate(self):
        brief = read_json(self.work_dir / 'design' / 'brief.json')
        mode_id = self.state['mode']['mode_id']
        theme_id = self.state['mode']['theme_id']
        mode_tpl = read_json(ROOT / 'templates' / 'game_modes' / f'{mode_id}.json')
        chars = read_json(self.work_dir / 'characters.json')
        blueprint = brief.get('scene_blueprint', [])
        batches = [blueprint[i:i + GENERATE_BATCH_SCENES]
                   for i in range(0, len(blueprint), GENERATE_BATCH_SCENES)]
        if not batches:
            raise RuntimeError('设计 brief 的 scene_blueprint 为空')
        worker = get_worker('generate')
        for idx, batch in enumerate(batches):
            last = (idx == len(batches) - 1)
            batch_ids = [s['id'] for s in batch]
            task = ('[STAGE:generate]\n'
                    + f'book_id: {self.book_id}\n'
                    + f'book 名: {self.title}\n'
                    + f'模式模板 runtime：\n{json.dumps(mode_tpl["runtime"], ensure_ascii=False, indent=1)}\n'
                    + f'主题（视觉基调，仅作氛围把握）：\n{theme_card(theme_id)}\n'
                    + f'人物卡：\n{json.dumps(chars, ensure_ascii=False, indent=1)}\n'
                    + f'设计 brief：\n{json.dumps(brief, ensure_ascii=False, indent=1)}\n'
                    + f'本次生成第 {idx + 1}/{len(batches)} 批，场景 id：{batch_ids}\n'
                    + '输出格式：{"patch": {"game": {...}, "mode": {...}, "characters": {...}, '
                      '"scenes": {批量场景节点}}}——顶层禁止 theme 字段。'
                    + ('这是最后一批，场景必须整体完整可达。' if last
                       else '非最后一批：只输出本批 scenes 与完整顶层字段，其余照常。'))
            result = react_loop(self.llm, worker['system_prompt'], task,
                                _envelope_validator(self.game_dir, full_check=last),
                                max_rounds=3, model='chat')
            if not result.ok:
                # 生成重试耗尽不阻断流程：问题清单留给 QA 阶段的 repair 循环
                # （repair 用同样的校验器 + 回传问题，最多 qa_max_rounds 轮）
                print(f'⚠ 批次 {idx + 1}/{len(batches)} 生成未通过校验（{result.rounds} 轮），'
                      f'前 {min(3, len(result.errors))} 个问题: {result.errors[:3]}')
                print('  问题将交由 QA 修复循环处理')
            else:
                print(f'生成批次 {idx + 1}/{len(batches)} 完成')

    def stage_qa(self):
        self.state['qa_rounds'] = self.state.get('qa_rounds', 0) + 1
        budget = self.config['pipeline'].get('qa_max_rounds', 3)
        report = run_qa_scripts(self.game_dir)
        errors = [i for i in report['issues'] if i['severity'] == 'error']
        warnings = [i for i in report['issues'] if i['severity'] == 'warning']

        # 语义评审（深模型）
        semantic = self._semantic_review()
        problems = semantic.get('problems', []) if isinstance(semantic, dict) else []
        major = [p for p in problems if isinstance(p, dict) and p.get('severity') == 'major']

        print(f'QA 第 {self.state["qa_rounds"]} 轮：结构错误 {len(errors)}，警告 {len(warnings)}，'
              f'语义主要问题 {len(major)}，评分 {semantic.get("score", "?") if isinstance(semantic, dict) else "?"}')

        if not errors and not major:
            write_json(self.work_dir / 'qa' / 'qa_report.json', {
                'ok': True, 'round': self.state['qa_rounds'], 'issues': report['issues'],
                'semantic': semantic})
            return
        if self.state['qa_rounds'] > budget:
            print(f'⚠ QA 修复轮数达到上限 {budget}，剩余问题：')
            for e in errors[:5]:
                print(f'  {e["message"]}')
            write_json(self.work_dir / 'qa' / 'qa_report.json', {
                'ok': False, 'round': self.state['qa_rounds'], 'issues': report['issues'],
                'semantic': semantic, 'degraded': True})
            return

        # 修复循环：把问题清单回传给 repair worker（补丁式输出）
        issues = [f"{i['severity']} {i['file']}: {i['message']}" for i in report['issues'][:40]]
        for p in major:
            issues.append(f"major {p.get('scene', '')}: {p.get('problem', '')}")
        worker = get_worker('repair')
        prompt = worker['system_prompt'].replace('{qa_issues}', '\n'.join(issues))
        task = '[STAGE:repair]\n输出 {"patch": {...}} 补丁格式，逐条修复上述问题清单。'
        result = react_loop(self.llm, prompt, task,
                            _envelope_validator(self.game_dir, full_check=True),
                            max_rounds=3, model='chat')
        if not result.ok:
            print(f'⚠ 修复未完全通过（{result.rounds} 轮），保留当前状态')
        # 回到 QA 循环
        self.stage_qa()

    def _semantic_review(self):
        if self._reasoner_down:
            # 熔断：reasoner 本 run 已确认不可用，不再每次花几分钟证明它死了
            print('⏭ 语义评审跳过（reasoner 本 run 已失败，评分按 0，结构校验不受影响）')
            return {'score': 0, 'problems': []}
        try:
            world = read_text(self.work_dir / 'bible' / 'world.md')
        except FileNotFoundError:
            world = ''
        chars = read_json(self.work_dir / 'characters.json')
        scenes = _load_js(self.game_dir / 'data' / 'scenes.js', 'SCENES', {})
        worker = get_worker('qa_review')
        task = ('[STAGE:qa_review]\n'
                f'世界观圣经：{clamp_context(world, 15000)}\n'
                f'人物卡：{json.dumps(chars, ensure_ascii=False)[:4000]}\n'
                f'场景数据：{json.dumps(scenes, ensure_ascii=False)[:12000]}\n'
                '按 review_prompt.md 输出严格 JSON：{"score": 0-10, "problems": [...], "praise": [...]}')
        try:
            content = self.llm.chat([{'role': 'user', 'content': task}],
                                    model='reasoner', json_mode=True, max_tokens=8000)
            return json.loads(content)
        except (LLMError, json.JSONDecodeError) as e:
            print(f'⚠ 语义评审失败: {e}')
            self._reasoner_down = True  # 一次失败即熔断，后续轮次直接跳过
            return {'score': 0, 'problems': []}

    def _image_client(self):
        """生图客户端：--mock 用 MockImageClient；真实模式无 key 时返回 None（降级为仅提示词）。"""
        if self.mock:
            from core.image import MockImageClient
            return MockImageClient()
        try:
            from core.image import ImageError, SiliconFlowImage
            return SiliconFlowImage(self.config.get('image', {}))
        except ImageError as e:
            print(f'  ℹ 未配置生图 key（{e}），仅生成提示词，图片可后续手动补放 assets/')
            return None

    def stage_illustrate(self):
        """插画：提示词生成（本地）+ 可选生图（SiliconFlow，配置 key 后启用）。

        每个角色与有背景的场景先生成统一画风的 .prompt.txt；若配置了
        SILICONFLOW_API_KEY 则调用生图 API 输出 PNG 到同名路径
        （角色图写入 portrait 字段，引擎直接展示；失败时降级为无图，引擎不报错）。
        """
        sys.path.insert(0, str(ROOT / 'skills' / 'illustration' / 'scripts'))
        from prompt_builder import build_prompt  # noqa: E402
        from core.image import ImageError, load_prompt_file

        theme_id = self.state['mode']['theme_id']
        style_id = self.config['pipeline'].get('illustration_styles', {}).get(theme_id, 'flat_modern')

        chars = _load_js(self.game_dir / 'data' / 'characters.js', 'CHARACTERS', {})
        scenes = _load_js(self.game_dir / 'data' / 'scenes.js', 'SCENES', {})
        img = self._image_client()
        written, imaged, failed = [], 0, []

        for c in chars.get('characters', []):
            desc = c.get('desc') or c.get('role') or ''
            try:
                r = build_prompt('portrait', c.get('name', c['id']), desc, style_id)
                prompt_path = self.game_dir / 'assets' / 'characters' / f"{c['id']}.prompt.txt"
                prompt_path.write_text(_fmt_prompt(r), encoding='utf-8')
                written.append(prompt_path.name)
            except Exception as e:
                print(f'  ⚠ 角色 {c.get("id")} 提示词生成失败: {e}')
                continue
            if img is None:
                continue
            target = c.get('portrait')
            if not target or not target.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                target = f'assets/characters/{c["id"]}.png'
            try:
                prompt, size = load_prompt_file(prompt_path)
                saved = img.generate(prompt, self.game_dir / target, size)
                imaged += 1
                rel = str(Path(saved).relative_to(self.game_dir))
                if c.get('portrait') != rel:
                    # 回写 portrait 字段（格式可能把 .png 变 .jpg），引擎按此渲染
                    data = _load_js(self.game_dir / 'data' / 'characters.js', 'CHARACTERS', {})
                    for cc in data.get('characters', []):
                        if cc.get('id') == c['id']:
                            cc['portrait'] = rel
                    _write_js(self.game_dir / 'data' / 'characters.js', 'CHARACTERS', data)
            except ImageError as e:
                print(f'  ⚠ 角色 {c.get("id")} 生图失败（已降级为无图）: {e}')
                failed.append({'kind': 'portrait', 'id': c.get('id'), 'error': str(e)})

        # 背景素材语义化：从引用该 bg 的第一个场景取 场景名 + 叙述片段 作文生图描述
        # （旧实现只传文件名占位，图永远泛泛；2026-09 实录缺陷）
        bg_meta = {}
        for node in scenes.get('scenes', {}).values():
            if not node.get('bg'):
                continue
            fn = Path(node['bg']).name
            if fn in bg_meta:
                continue
            t = (node.get('title') or '').strip()
            n = (node.get('narration') or '').strip()
            bg_meta[fn] = (t, n[:90])
        bg_files = set(bg_meta)
        for fname in sorted(bg_files):
            node_bg = next(n['bg'] for n in scenes.get('scenes', {}).values()
                           if n.get('bg') and Path(n['bg']).name == fname)
            t, n = bg_meta[fname]
            try:
                r = build_prompt('bg', t or Path(fname).stem,
                                 n or f'{Path(fname).stem} 的场景氛围', style_id)
                prompt_path = self.game_dir / 'assets' / 'bg' / f'{fname}.prompt.txt'
                prompt_path.write_text(_fmt_prompt(r), encoding='utf-8')
                written.append(prompt_path.name)
            except Exception as e:
                print(f'  ⚠ 背景 {fname} 提示词生成失败: {e}')
                continue
            if img is None:
                continue
            try:
                prompt, size = load_prompt_file(prompt_path)
                saved = img.generate(prompt, self.game_dir / node_bg, size)
                imaged += 1
                rel = str(Path(saved).relative_to(self.game_dir))
                if rel != node_bg:
                    # 格式扩展名变化时同步 scenes.js 的 bg 引用
                    sdata = _load_js(self.game_dir / 'data' / 'scenes.js', 'SCENES', {})
                    if any(n.get('bg') == node_bg for n in sdata.get('scenes', {}).values()):
                        for n in sdata['scenes'].values():
                            if n.get('bg') == node_bg:
                                n['bg'] = rel
                        _write_js(self.game_dir / 'data' / 'scenes.js', 'SCENES', sdata)
            except ImageError as e:
                print(f'  ⚠ 背景 {fname} 生图失败（已降级为无图）: {e}')
                failed.append({'kind': 'bg', 'id': fname, 'error': str(e)})

        # 封面主视觉（可选 assets/cover.webp，illustrate 阶段生成；引擎缺图自动隐藏
        # → 主题纹理兜底；.png 兼容历史产物，引擎双扩展名容错）
        cover_art = self.game_dir / 'assets' / 'cover.webp'
        brief_p = self.work_dir / 'design' / 'brief.json'
        try:
            if brief_p.exists():
                b = read_json(brief_p)
                cg = build_prompt('cg', b.get('game_title') or self.title,
                                  b.get('subtitle') or '', style_id)
                if img is not None:
                    try:
                        saved = img.generate(cg['prompt'], cover_art, cg['size'])
                        imaged += 1
                        print(f'  ✓ 封面主视觉已生成 {Path(saved).name}（引擎双扩展名容错）')
                    except ImageError as e:
                        failed.append({'kind': 'cover', 'id': Path(cover_art).name, 'error': str(e)})
                        print(f'  ⚠ 封面主视觉失败（降级为主题纹理封面）: {e}')
        except Exception as e:
            print(f'  ⚠ 封面主视觉跳过: {e}')

        # 落盘 manifest：QA/用户可查"哪些图没生成"，失败不再只活在 console
        manifest = {
            'style_id': style_id,
            'img_client': 'mock' if self.mock else ('siliconflow' if img else 'none（未配置 key）'),
            'prompt_files': len(written),
            'images_ok': imaged,
            'failed': failed,
        }
        try:
            run_dir = ROOT / 'runtime' / self.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / 'illustrate.json').write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError:
            pass
        if written:
            print(f'插画提示词（画风 {style_id}）: {len(written)} 个 → assets/'
                  + (f'，已生成 {imaged} 张图片' if imaged else '（未配置生图 key，图片待补）'))
            if failed:
                print(f'  ⚠ {len(failed)} 张失败（已降级为无图）: '
                      + '、'.join(f["kind"] + ' ' + f["id"] for f in failed)
                      + '（明细见 runtime/<run_id>/illustrate.json）')
        else:
            print(f'插画提示词: 无（角色 0 人 / 无背景引用）')

    def stage_package(self):
        self._run(ROOT / 'tools' / 'package.py', self.game_dir, '--archive', ROOT / 'archive')
        backend = self.config.get('upload', {}).get('backend', 'local')
        ttl = self.config.get('upload', {}).get('link_ttl_minutes', 30)
        zip_path = ROOT / 'archive' / f'{self.run_id}.zip'
        if backend == 's3':
            self._run(ROOT / 'tools' / 'upload.py', zip_path, '--backend', 's3', '--ttl', str(ttl))
        else:
            self._run(ROOT / 'tools' / 'upload.py', zip_path, '--backend', 'local', '--ttl', str(ttl))
        print('（链接到期后可用 tools/cleanup.py 清理本地产物）')


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description='novel-webgame-agent：小说 → 网页游戏')
    ap.add_argument('novel', help='小说文件路径（TXT/MD/EPUB）')
    ap.add_argument('--config', default=str(ROOT / 'config.json'))
    ap.add_argument('--mock', action='store_true', help='使用 mock LLM（测试）')
    ap.add_argument('--mock-dir', action='append', default=[],
                    help='额外 mock fixture 目录（优先级高于默认 fixtures）')
    ap.add_argument('--resume', action='store_true',
                    help='从断点继续（自动定位该书最近一次运行）')
    ap.add_argument('--run-id', help='运行 id（默认 <book_id>_<时间戳>；resume 时可用它指定具体某次运行）')
    args = ap.parse_args()

    load_dotenv()  # 读取项目根目录 .env（DEEPSEEK_API_KEY / SILICONFLOW_API_KEY 等）
    config = read_json(args.config)
    try:
        agent = NovelAgent(args.novel, config, mock=args.mock, resume=args.resume,
                           mock_dirs=args.mock_dir, run_id=args.run_id)
    except LLMError as e:
        print(f'配置错误: {e}\n（离线测试请加 --mock）')
        sys.exit(1)
    try:
        agent.run()
    except (LLMError, RuntimeError) as e:
        # 阶段故障（LLM 空响应/超时/校验失败）已带续跑指引，不打印裸 traceback；
        # checkpoint 未写入 → 直接 --resume 从失败阶段继续
        print(f'\n✗ 管线中断: {e}')
        sys.exit(1)
    except KeyboardInterrupt:
        print('\n（用户中断，可 --resume 续跑）')
        sys.exit(130)


if __name__ == '__main__':
    main()
