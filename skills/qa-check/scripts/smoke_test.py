#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冒烟测试：模拟玩家游玩，检测运行时级问题。

与 validate_game.py 的区别：validate 查结构（静态），smoke 查运行（模拟状态演进）。
检测项：
  - 模拟随机游玩 N 次，能否到达结局（到达率）
  - 状态模拟下出现"无可用选项"的死路
  - 条件结局在模拟中是否可达（可达性受属性门槛影响）
  - 游玩路径平均长度（体验节奏信号：过短=内容不足，过长=拖沓）
"""
import argparse
import json
import random
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_game import load_game


def run_once(scenes, mode, entry, rng):
    state = {"attrs": {}, "flags": {}, "inventory": [], "scene": entry, "steps": 0,
             "visited": [], "result": None, "msg": ""}
    for a in mode.get("attributes", []):
        state["attrs"][a["id"]] = a.get("start", a.get("min", 0))
    guard = 0
    while guard < 500:
        guard += 1
        state["steps"] += 1
        node = scenes.get(state["scene"])
        if node is None:
            state["result"] = "missing"; state["msg"] = f"场景不存在 {state['scene']}"; return state
        if node.get("ending"):
            state["result"] = "ending"; state["msg"] = node["ending"].get("title", ""); return state
        if node.get("auto") and node["auto"].get("goto") \
                and _passes(node["auto"].get("requires"), state, mode):
            state["scene"] = node["auto"]["goto"]
            if state["visited"].count(state["scene"]) > 3:
                state["result"] = "loop"; state["msg"] = f"auto 链循环 @{state['scene']}"; return state
            continue
        if node.get("riddle") and node["riddle"].get("goto"):
            # riddle 节点：模拟视为必解（答案唯一），直接推进到解出后的去向
            state["scene"] = node["riddle"]["goto"]
            if state["visited"].count(state["scene"]) > 3:
                state["result"] = "loop"; state["msg"] = f"riddle 链循环 @{state['scene']}"; return state
            continue
        choices = [c for c in node.get("choices", []) if _passes(c.get("requires"), state, mode)]
        if not choices:
            state["result"] = "deadlock"
            state["msg"] = f"无可用选项 @{state['scene']}"
            return state
        pick = rng.choice(choices)
        _apply(pick.get("effects"), state, mode)
        state["scene"] = pick["goto"]
        state["visited"].append(state["scene"])
    state["result"] = "loop"; state["msg"] = "步数超限（疑似循环）"
    return state


def _passes(req, state, mode):
    if not req:
        return True
    if req.get("attrs"):
        for k, v in req["attrs"].items():
            cur = state["attrs"].get(k, 0)
            if v.get("gte") is not None and not (cur >= v["gte"]): return False
            if v.get("lte") is not None and not (cur <= v["lte"]): return False
            if v.get("eq") is not None and not (cur == v["eq"]): return False
    if req.get("flags"):
        for f, v in req["flags"].items():
            if state["flags"].get(f) != v: return False
    if req.get("inventory"):
        for it, need in req["inventory"].items():
            if state["inventory"].count(it) < need: return False
    if req.get("perspective") and state.get("perspective") not in (None, req["perspective"]):
        return False
    return True


def _apply(eff, state, mode):
    if not eff:
        return
    for k, d in (eff.get("attrs") or {}).items():
        def_ = None
        for a in mode.get("attributes", []):
            if a["id"] == k: def_ = a; break
        cur = state["attrs"].get(k, 0) + d
        if def_:
            cur = min(def_.get("max", 10 ** 9), max(def_.get("min", -10 ** 9), cur))
        state["attrs"][k] = cur
    for f, v in (eff.get("flags") or {}).items():
        state["flags"][f] = v
    inv = eff.get("inventory") or {}
    for it in inv.get("add", []): state["inventory"].append(it)
    for it in inv.get("remove", []):
        if it in state["inventory"]: state["inventory"].remove(it)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('game_dir')
    ap.add_argument('--runs', type=int, default=40)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--json', action='store_true', help='输出 JSON（输出恒为 JSON，此参数仅为兼容）')
    args = ap.parse_args()

    game_dir = Path(args.game_dir)
    data, fatal = load_game(game_dir)
    if fatal:
        print(json.dumps({"ok": False, "error_count": 1, "issues": [{"severity": "error", "file": "scenes.js", "message": fatal}]}, ensure_ascii=False))
        sys.exit(1)

    scenes = data['scenes.js']['scenes']
    mode = data['mode.js']
    entry = data['game.js']['entry']
    rng = random.Random(args.seed)
    # 剧情起点：入口；fate 模式入口是抽取锚点（空壳），起点为各转生身份起点
    fate_cfg = mode.get('fate') or {}
    starts = [entry]
    if fate_cfg.get('enabled'):
        starts = [d['start_scene'] for d in fate_cfg.get('draw_pool', [])
                  if d.get('start_scene')] or starts

    # 1. 确定性全覆盖游走：BFS 模拟可达性（宽松：requires 全部视为可过）
    all_scenes = set(scenes.keys())
    q = deque(starts); reached = set()
    while q:
        cur = q.popleft()
        if cur in reached: continue
        reached.add(cur)
        node = scenes[cur]
        for ch in node.get("choices", []):
            if ch.get("goto") and ch["goto"] not in reached: q.append(ch["goto"])
        if node.get("auto") and node["auto"].get("goto") and node["auto"]["goto"] not in reached:
            q.append(node["auto"]["goto"])
        if node.get("riddle") and node["riddle"].get("goto") and node["riddle"]["goto"] not in reached:
            q.append(node["riddle"]["goto"])
    unreachable = all_scenes - reached
    if fate_cfg.get('enabled') and entry in unreachable:
        unreachable.discard(entry)  # fate 入口是抽取锚点（空壳），玩家不经过

    # 2. 随机游玩（起点随机：模拟转生抽取）
    results = [run_once(scenes, mode, rng.choice(starts), rng) for _ in range(args.runs)]
    endings = [r for r in results if r["result"] == "ending"]
    deadlocks = [r for r in results if r["result"] == "deadlock"]
    loops = [r for r in results if r["result"] == "loop"]
    avg_steps = sum(r["steps"] for r in results) / len(results)
    ending_titles = sorted({r["msg"] for r in endings})

    issues = []
    if unreachable:
        issues.append({"severity": "error", "file": "scenes.js",
                       "message": f"冒烟：{len(unreachable)} 个节点在模拟中不可达: {sorted(unreachable)[:8]}"})
    for d in deadlocks[:5]:
        issues.append({"severity": "error", "file": "scenes.js", "message": f"冒烟死路: {d['msg']}"})
    for l in loops[:3]:
        issues.append({"severity": "warning", "file": "scenes.js", "message": f"冒烟循环: {l['msg']}"})
    if not endings:
        issues.append({"severity": "error", "file": "scenes.js", "message": "冒烟：40 次游玩未到达任何结局"})
    if avg_steps < 5:
        issues.append({"severity": "warning", "file": "scenes.js", "message": f"冒烟：平均 {avg_steps:.1f} 步到结局，内容可能过短"})
    if avg_steps > 120:
        issues.append({"severity": "warning", "file": "scenes.js", "message": f"冒烟：平均 {avg_steps:.1f} 步到结局，可能拖沓"})

    errors = [i for i in issues if i["severity"] == "error"]
    print(json.dumps({
        "ok": not errors,  # 警告不视为失败（与 validate_game.py 语义一致）
        "error_count": len(errors),
        "warning_count": len([i for i in issues if i["severity"] == "warning"]),
        "summary": {
            "runs": args.runs, "reached_ending": len(endings),
            "ending_rate": f"{len(endings) / args.runs:.0%}", "avg_steps": round(avg_steps, 1),
            "ending_titles": ending_titles, "unreachable_nodes": sorted(unreachable)[:8],
        },
        "issues": issues,
    }, ensure_ascii=False, indent=2))
    sys.exit(1 if any(i["severity"] == "error" for i in issues) else 0)


if __name__ == '__main__':
    main()
