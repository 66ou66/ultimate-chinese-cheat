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


def auto_move_once(advisor, explainer, state, countdown=3.0):
    """半自动落子：识别 → 分析 → 打印建议 → 倒计时（Esc 取消） → 自动点击落子。

    我方执红/执黑由 playing_red 自动判断，turn 直接由此推导，不做手动翻转。
    按键：
      [Esc] / [C]  倒计时期间取消落子
    """
    import time as _time
    from auto_player import AutoPlayer

    # 1. 识别 + 分析（turn 由我方执哪方自动推导）
    try:
        data = advisor.run_auto()
    except Exception as exc:
        print(f"❌ 识别/分析出错: {exc}")
        print("   请确认：天天象棋已打开且进入棋盘界面；校准数据有效。")
        return

    print_board_cn(data['board'], "识别结果")
    print()
    print(format_suggestion(data))
    if state['auto_explain']:
        _ai_explain_last(advisor, explainer)

    result = data['result']
    if not result.bestmove:
        print("⚠ 引擎未返回着法，无法自动落子。")
        return

    side = '红方' if data['turn'] == 'w' else '黑方'
    best_cn = uci_to_cn(result.bestmove, data['board'])
    print(f"\n🖱 自动落子: {best_cn} ({result.bestmove})  "
          f"（我方{'执红' if data['playing_red'] else '执黑'}，当前 {side}走）")

    # 2. 倒计时（可取消）
    deadline = _time.time() + countdown
    last_sec = None
    while True:
        remain = deadline - _time.time()
        if remain <= 0:
            break
        sec = int(remain + 0.5)
        if sec != last_sec:
            print(f"   ⏳ {sec} 秒后自动落子（按 Esc 取消）...", end="\r", flush=True)
            last_sec = sec
        key = _poll_key()
        if key in ('\x1b', 'c'):
            print("\n   已取消，未落子。")
            return
        _time.sleep(0.2)
    print("   " + " " * 40, end="\r")

    # 3. 自动落子（点击-点击）
    try:
        player = AutoPlayer(advisor.bot)
        player.play_uci(result.bestmove, data['playing_red'])
        print("\n✅ 已自动落子。")
    except Exception as exc:
        print(f"\n❌ 落子失败: {exc}")
        print("   请确认：天天象棋窗口可见、校准数据有效。")


def auto_battle(advisor, state, poll_interval=0.8):
    """全自动对弈：识别 → 差分判断轮次 → 引擎分析 → 自动落子，循环到对局结束或手动停止。

    我方执红/执黑自动判断（红方在屏幕下方 = 执红；上方 = 执黑）。
    按键： [Q]/[Esc] 手动停止；鼠标甩到屏幕左上角紧急中止（pyautogui.FAILSAFE）。
    """
    import time as _time
    from auto_battle import AutoBattle
    from fen_utils import is_game_over as _is_game_over

    battle = AutoBattle(advisor, poll_interval=poll_interval)
    try:
        board, playing_red = battle.start()
    except Exception as exc:
        print(f"❌ 开局识别失败: {exc}")
        print("   请确认：天天象棋已打开且进入棋盘界面；校准数据有效。")
        return

    side = '执红（先手）' if playing_red else '执黑（后手）'
    print(f"\n⚔ 全自动对战开始：我方{side}。")
    print("   按 [Q]/[Esc] 随时停止；鼠标甩到屏幕左上角紧急中止。")
    print_board_cn(board, "开局识别")

    while True:
        key = _poll_key()
        if key in ('q', '\x1b'):
            print("\n⏹ 已手动停止全自动对战。")
            return

        try:
            board, _ = advisor.capture_board()
        except Exception as exc:
            print(f"⚠ 识别失败: {exc}（1 秒后重试）")
            _time.sleep(1)
            continue

        if _is_game_over(board):
            print("\n🏁 检测到帅/将缺失，对局结束。")
            print_board_cn(board, "终局棋盘")
            return

        if battle.my_turn:
            # 轮到我方：引擎分析 → 自动落子 → 回读确认
            print_board_cn(board, "识别结果")
            status = battle.play_my_move()
            if status == 'no_move':
                print("\n🏁 引擎无合法着法，对局结束（我方被将死/困毙）。")
                return
            if status == 'confirm_failed':
                print("\n❌ 落子回读确认失败，已暂停。请人工检查棋盘与鼠标位置后，再按 [F] 重新启动。")
                return
            print("✅ 我方已落子，继续监控对方...")
        else:
            # 等待对方：差分检测棋盘变化
            if battle.update_board(board):
                print("\n👀 检测到对方已落子，轮到我方！")
                print_board_cn(board, "对方走完")

        _time.sleep(battle.poll_interval)


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
    print("  [A]     半自动落子（识别+建议 → 倒计时 → 自动点击落子，Esc 取消）")
    print("  [F]     全自动对战（自动判断轮次并落子，[Q]/[Esc] 停止）")
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
        elif key == 'a':
            print("A - 半自动落子...")
            auto_move_once(advisor, explainer, state)
        elif key == 'f':
            print("F - 全自动对战...")
            auto_battle(advisor, state)
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
                print(f"'{key}' 无效，请按 A / F / 1 / 3 / 4 / R / B / 空格 / 2 / E / Q")

    advisor.close()


if __name__ == '__main__':
    main()
