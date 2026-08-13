#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国象棋 AI 助手 —— 主入口（纯终端 CLI）

流程：截屏识别棋盘 → 拼 FEN → Pikafish 引擎分析 → 输出中文建议（可选 DeepSeek 解释）。

使用前：
1. 打开天天象棋并进入棋盘界面；
2. 确认根目录下已有 engine/ 与 xiangqi-bot/ 及 calib.json（首次需校准）；
3. 设置环境变量 DEEPSEEK_API_KEY（可选，用于 AI 解释）。
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


def main():
    os.system('')  # 启用 Windows 终端 ANSI 颜色

    print("=" * 52)
    print("  中国象棋 AI 助手（截屏识别 + Pikafish 引擎）")
    print("=" * 52)

    advisor = Advisor()
    explainer = DeepSeekExplainer()

    # 提前初始化引擎（可选），失败不阻塞
    try:
        advisor.engine.initialize()
        print(f"[引擎] {advisor.engine.engine_path}")
    except Exception as exc:
        print(f"[警告] 引擎初始化失败: {exc}")

    if explainer.available():
        print("[AI]   DeepSeek 解释已启用")
    else:
        print("[AI]   未设置 DEEPSEEK_API_KEY，AI 解释不可用（不影响出招）")

    print("\n操作说明：")
    print("  [1]     识别当前棋盘并给出建议")
    print("  [3]     识别 + 人工修正 + 分析")
    print("  [R]/[B] 设定当前轮到 红方/黑方 走")
    print("  [空格]  翻转轮次（下完一步后按一下）")
    print("  [2]     用 AI 解释上一次建议")
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
            except Exception as exc:
                print(f"❌ 人工检验出错: {exc}")
                print("   请确认：天天象棋已打开且进入棋盘界面；校准数据有效。")
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
            if not advisor.last:
                print("   还没有建议，请先按 [1] 识别分析。")
                continue
            data = advisor.last
            result = data['result']
            if not result.bestmove:
                print("   无有效着法，无法解释。")
                continue
            if not explainer.available():
                print("   未设置 DEEPSEEK_API_KEY，无法调用 AI 解释。")
                continue

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
            print("  AI 解读")
            print("=" * 52)
            print(text)
            print("=" * 52)
        else:
            if key is not None:
                print(f"'{key}' 无效，请按 1 / 3 / R / B / 空格 / 2 / Q")

    advisor.close()


if __name__ == '__main__':
    main()
