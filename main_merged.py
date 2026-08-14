#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国象棋 AI 助手 —— 主入口（纯终端 CLI）

流程：截屏识别棋盘 → 拼 FEN → Pikafish 引擎分析 → 输出中文建议（可选 DeepSeek 解释）。

使用前：
1. 打开天天象棋并进入棋盘界面；
2. 确认根目录下已有 engine/ 与 xiangqi-bot/ 及 calib.json（首次需校准）；
3. AI 解说密钥：默认内置在 explainer.py（DEFAULT_API_KEY），也可用环境变量 DEEPSEEK_API_KEY 覆盖。
   按 [2] 手动看解说；按 [E] 切换为"出建议后自动附带解说"。
"""

import os
import sys
import msvcrt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from advisor import Advisor, format_suggestion, print_board_cn  # noqa: E402
from explainer import DeepSeekExplainer              # noqa: E402
from move_notation import uci_to_cn, format_score, pv_to_cn  # noqa: E402


def _read_key():
    """读取单个按键并返回小写字符。"""
    try:
        ch = msvcrt.getch()
    except Exception:
        return None
    try:
        return ch.decode('utf-8').lower()
    except UnicodeDecodeError:
        return None


def _poll_key():
    """非阻塞读取按键：有按键时返回字符，无按键返回 None。"""
    try:
        if not msvcrt.kbhit():
            return None
    except Exception:
        return None
    return _read_key()


def _input_interval(current=3.0):
    """交互式输入自动识别间隔秒数，校验最小 0.5 秒。非法输入返回当前值。"""
    while True:
        try:
            raw = input(f"  设置间隔秒数（当前 {current:g}，最小 0.5）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return current
        if not raw:
            return current
        try:
            val = float(raw)
        except ValueError:
            print("  ⚠ 请输入数字，例如 2 或 1.5。")
            continue
        if val < 0.5:
            print("  ⚠ 间隔不能小于 0.5 秒。")
            continue
        return val


def _ai_explain_last(advisor, explainer):
    """对 advisor.last 中的引擎建议调用 AI 解说并打印。

    返回解说文本；无可解说内容或调用失败时返回 None。
    """
    data = advisor.last
    if data is None:
        print("   还没有建议，请先按 [1] 识别分析。")
        return None
    result = data['result']
    if not result.bestmove:
        print("   无有效着法，无法解释。")
        return None
    if not explainer.available():
        print("   AI 解说未启用（未配置密钥）。")
        return None

    board = data['board']
    best_cn = uci_to_cn(result.bestmove, board)
    best_score = format_score(
        result.candidates[0].score_cp,
        result.candidates[0].score_mate,
    ) if result.candidates else '?'
    pv_lines = [
        f"#{c.multipv} {' '.join(pv_to_cn(c.pv, board)[:6])} ({format_score(c.score_cp, c.score_mate)})"
        for c in result.candidates
    ]
    side_name = '红方' if data['turn'] == 'w' else '黑方'

    print("\n⏳ 正在调用 DeepSeek 解释...")
    text = explainer.explain(
        data['fen'], best_cn, result.bestmove, best_score, pv_lines, side_name,
    )
    print("\n" + "=" * 52)
    print("  AI 解说")
    print("=" * 52)
    print(text)
    print("=" * 52)
    return text


def auto_mode(advisor, explainer, state):
    """自动识别+建议模式：每隔 x 秒自动识别棋盘并给出引擎建议。

    按键：
      [5]      暂停 / 恢复自动识别
      [X]      设置间隔秒数（最小 0.5 秒）
      [R]/[B]  设定当前轮到 红方/黑方 走
      [空格]   翻转轮次
      [E]      切换自动解说（出建议后自动调 AI 解说）
      [4]      退出自动识别模式
      [Q]      退出程序
    """
    import time as _time

    interval = 3.0
    paused = False
    last_run = 0.0  # 初始为0 → 进入模式后立即识别一次，随后每 interval 秒识别

    print("\n" + "=" * 52)
    print("  自动识别模式（每 x 秒自动识别 + 建议）")
    print("=" * 52)
    print("  [5] 暂停/恢复  [X] 设置间隔(>=0.5秒)")
    print("  [R]/[B] 设轮次  [空格] 翻转轮次")
    print("  [E] 自动解说开关  [4] 退出自动模式  [Q] 退出程序")

    while True:
        key = _poll_key()

        if key in ('q', '\x1b'):
            print("Q\n👋 退出。")
            advisor.close()
            raise SystemExit
        elif key == '5':
            paused = not paused
            status = '已暂停' if paused else '已恢复'
            print(f"\n  ⏸ 自动识别{status}（当前间隔 {interval:g} 秒，按 [5] 切换）")
        elif key == 'x':
            interval = _input_interval(interval)
            print(f"  ✅ 间隔已设为 {interval:g} 秒")
        elif key == 'r':
            advisor.turn.set('w')
            print("  R - 已设为红方走")
        elif key == 'b':
            advisor.turn.set('b')
            print("  B - 已设为黑方走")
        elif key == ' ':
            advisor.turn.flip()
            print(f"  已翻转轮次 -> {advisor.turn.label()}走")
        elif key == 'e':
            state['auto_explain'] = not state['auto_explain']
            mode = '自动解说' if state['auto_explain'] else '手动解说'
            print(f"  E - 已切换为{mode}（出建议后{'自动' if state['auto_explain'] else '不自动'}调 AI 解说）")
        elif key == '4':
            print("  已退出自动识别模式。\n")
            return

        now = _time.time()
        if not paused and now - last_run >= interval:
            print(f"\n⏳ 自动识别（间隔 {interval:g} 秒 | 当前轮次={advisor.turn.label()} | [5]暂停 [X]改间隔）...")
            try:
                advisor.run_once(advisor.turn.current())
                print_board_cn(advisor.last['board'], "识别结果")
                print()
                print(format_suggestion(advisor.last))
                if state['auto_explain']:
                    _ai_explain_last(advisor, explainer)
            except Exception as exc:
                print(f"❌ 识别/分析出错: {exc}")
                print("   请确认：天天象棋已打开且进入棋盘界面；校准数据有效。")
            last_run = _time.time()

        _time.sleep(0.05)


def main():
    os.system('')  # 启用 Windows 终端 ANSI 颜色

    print("=" * 52)
    print("  中国象棋 AI 助手（截屏识别 + Pikafish 引擎）")
    print("=" * 52)

    advisor = Advisor()
    explainer = DeepSeekExplainer()
    state = {'auto_explain': False}  # 默认手动解说，按 [E] 一键切换为自动解说

    # 提前初始化引擎（可选），失败不阻塞
    try:
        advisor.engine.initialize()
        print(f"[引擎] {advisor.engine.engine_path}")
    except Exception as exc:
        print(f"[警告] 引擎初始化失败: {exc}")

    if explainer.available():
        print("[AI]   DeepSeek 解释已启用（[2]手动看解说，[E]切换自动解说）")
    else:
        print("[AI]   AI 解释未启用（未配置密钥，不影响出招）")

    print("\n操作说明：")
    print("  [1]     识别当前棋盘并给出建议")
    print("  [3]     识别 + 人工修正 + 分析")
    print("  [4]     自动识别模式（每 x 秒识别+建议，[5]暂停，[X]设间隔）")
    print("  [R]/[B] 设定当前轮到 红方/黑方 走")
    print("  [空格]  翻转轮次（下完一步后按一下）")
    print("  [2]     用 AI 解释上一次建议")
    print("  [E]     切换自动解说（开启后 [1]/[3]/[4] 出建议即自动附带 AI 解说）")
    print("  [Q]     退出")

    while True:
        print(f"\n⏳ 当前轮次={advisor.turn.label()}，等待按键... ", end="", flush=True)
        key = _read_key()

        if key in ('q', '\x1b'):
            print("Q\n👋 退出。")
            break
        elif key == '1':
            print("1 - 识别并分析...")
            try:
                advisor.run_once(advisor.turn.current())
                print_board_cn(advisor.last['board'], "识别结果")
                print()
                print(format_suggestion(advisor.last))
                if state['auto_explain']:
                    _ai_explain_last(advisor, explainer)
            except Exception as exc:
                print(f"❌ 识别/分析出错: {exc}")
                print("   请确认：天天象棋已打开且进入棋盘界面；校准数据有效。")
        elif key == '3':
            print("3 - 识别 + 人工修正 + 分析...")
            try:
                advisor.run_edited_once(advisor.turn.current())
                print_board_cn(advisor.last['board'], "修正后结果")
                print()
                print(format_suggestion(advisor.last))
                if state['auto_explain']:
                    _ai_explain_last(advisor, explainer)
            except Exception as exc:
                print(f"❌ 人工检验出错: {exc}")
                print("   请确认：天天象棋已打开且进入棋盘界面；校准数据有效。")
        elif key == '4':
            auto_mode(advisor, explainer, state)
        elif key == 'r':
            advisor.turn.set('w')
            print("R - 已设为红方走")
        elif key == 'b':
            advisor.turn.set('b')
            print("B - 已设为黑方走")
        elif key == ' ':
            advisor.turn.flip()
            print(f"  已翻转轮次 -> {advisor.turn.label()}走")
        elif key == '2':
            _ai_explain_last(advisor, explainer)
        elif key == 'e':
            state['auto_explain'] = not state['auto_explain']
            mode = '自动解说' if state['auto_explain'] else '手动解说'
            print(f"  E - 已切换为{mode}（出建议后{'自动' if state['auto_explain'] else '不自动'}调 AI 解说）")
        else:
            if key is not None:
                print(f"'{key}' 无效，请按 1 / 3 / R / B / 空格 / 2 / E / Q")

    advisor.close()


if __name__ == '__main__':
    main()
