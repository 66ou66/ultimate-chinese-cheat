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
from calib_manager import CalibStore, run_calibration, sanitize_name  # noqa: E402

# 兼容中文/emoji 输出：强制 stdout/stderr 使用 UTF-8，避免 GBK 控制台打印 ⚔/✅/⏳ 等字符崩溃
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8')
        except Exception:
            pass


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

    我方执红/执黑自动判断（CNN 红像素比例 + 开局局面底线校验双重确认）。
    按键： [Q]/[Esc] 手动停止；鼠标甩到屏幕左上角紧急中止（pyautogui.FAILSAFE）。

    v2 改动：
    - 落子回读改为"变化格定位"（diff_boards/is_same_move），不再要求全盘 90 格
      指纹精确相等 —— 修复之前每次提示"落子回读确认失败"的问题。
    - 等待对方用"变化格 + 连续 N 帧投票"，CNN 识别噪声不再导致永远等不到。
    - 引擎 bestmove '(none)/0000/(pass)' 统一视为无着法 —— 修复将死时崩溃。
    - 落子后立即用引擎校验"对方是否已无合法着法"，我方将死对方时自动判胜。
    - 增加"无进展超时 + 最大步数"保护，防止对局静默结束时的死循环。

    v3 修复（"偶发落子回读确认失败"）：
    - 回读判定放宽为"包含预期 from/to 两格变化"（contains_move）：我方落子 +
      对方同步走子 = 4 格变化时不再误判失败。
    - 回读对比基准优先用已确认局面 self.last_board，消除确认帧到落子前帧的
      时间差窗口。
    - 等待对方时校验变化格必须是"对方颜色"的子走的，避免己方残留/方向误判
      带偏轮次。
    """
    import time as _time
    from auto_battle import AutoBattle, has_move
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

    WAIT_TIMEOUT = 60.0   # 无任何进展的最大静默秒数，超时暂停防死循环
    MAX_MOVES = 200
    last_action = _time.time()

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

        if battle.move_count >= MAX_MOVES:
            print(f"\n🛑 我方已走满 {MAX_MOVES} 步，达到保护上限，自动停止。")
            return

        if battle.my_turn:
            # 轮到我方：引擎分析 → 自动落子 → 回读确认
            print_board_cn(board, "识别结果")
            try:
                status = battle.play_my_move()
            except Exception as exc:
                print(f"\n❌ 我方回合处理异常: {exc}")
                print("   已暂停。请人工检查局面后按 [F] 重新启动；若引擎异常请先运行引擎自检。")
                return
            if status == 'no_move':
                print("\n🏁 引擎无合法着法，对局结束（我方被将死/困毙）。")
                return
            if status == 'confirm_failed':
                print("\n❌ 落子回读确认失败，已暂停。请人工检查棋盘与鼠标位置后，再按 [F] 重新启动。")
                return
            # 落子成功：立即校验"对方是否已无合法着法"（我方将死/困毙对方 → 判胜）
            try:
                opp_turn = 'b' if battle.playing_red else 'w'
                fen = advisor.board_to_full_fen(
                    battle.last_board, battle.playing_red, opp_turn)
                r2 = advisor.analyze(fen)
                if not has_move(r2.bestmove):
                    print("\n🏆 我方已将死/困毙对方，对局结束！")
                    print_board_cn(battle.last_board, "终局棋盘")
                    return
            except Exception:
                pass  # 校验失败不阻断，继续正常监控
            print("✅ 我方已落子，继续监控对方...")
            last_action = _time.time()
        else:
            # 等待对方：差分检测棋盘变化
            if battle.update_board(board):
                print("\n👀 检测到对方已落子，轮到我方！")
                print_board_cn(board, "对方走完")
                last_action = _time.time()

        # 无进展超时保护（对局静默结束/识别卡住时防死循环）
        if _time.time() - last_action > WAIT_TIMEOUT:
            print(f"\n⚠ 超过 {WAIT_TIMEOUT:g} 秒没有任何进展（对局可能已结束或识别卡住）。")
            print("   请人工检查局面；若对局已结束请直接退出，若只是卡住可再按 [F] 重新启动。")
            return

        _time.sleep(battle.poll_interval)


def _calib_choose(store, prompt):
    """列出模板并让用户按编号选择，返回名称；取消返回 None。"""
    names = store.list_profiles()
    if not names:
        print("  ⚠ 暂无已保存的棋盘模板。")
        return None
    print("  已保存的棋盘:")
    for i, n in enumerate(names, 1):
        print(f"    {i}. {n}")
    try:
        raw = input(f"  {prompt}（输入编号，回车取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not raw:
        return None
    try:
        idx = int(raw)
    except ValueError:
        print("  ⚠ 请输入数字编号。")
        return None
    if not (1 <= idx <= len(names)):
        print("  ⚠ 编号超出范围。")
        return None
    return names[idx - 1]


def _calib_select(store):
    name = _calib_choose(store, "选择要使用的棋盘")
    if name is None:
        return
    if store.activate(name):
        print(f"  ✅ 已切换为使用棋盘「{name}」。回到主菜单按 [1] 识别即可。")
    else:
        print("  ❌ 激活失败，模板可能已损坏。")


def _calib_add(store):
    print("\n  开始添加棋盘（拖框校准）...")
    ratios = run_calibration()
    if ratios is None:
        return
    while True:
        try:
            raw = input("  请输入新棋盘名称（回车取消）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            print("  已取消。")
            return
        if not sanitize_name(raw):
            print("  ⚠ 名称无效，请使用字母/数字/中文等。")
            continue
        if sanitize_name(raw) in store.list_profiles():
            print(f"  ⚠ 名称「{sanitize_name(raw)}」已存在，请换一个。")
            continue
        break
    store.save_profile(raw, ratios)
    store.activate(raw)
    print(f"  ✅ 已保存并设为当前棋盘「{sanitize_name(raw)}」。")


def _calib_delete(store):
    name = _calib_choose(store, "选择要删除的棋盘")
    if name is None:
        return
    try:
        confirm = input(f"  确认删除「{name}」？(y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if confirm != 'y':
        print("  已取消删除。")
        return
    if store.delete_profile(name):
        print(f"  ✅ 已删除棋盘「{name}」。")
    else:
        print("  ❌ 删除失败。")


def _calib_rename(store):
    name = _calib_choose(store, "选择要重命名的棋盘")
    if name is None:
        return
    while True:
        try:
            raw = input(f"  请输入新名称（当前「{name}」，回车取消）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            print("  已取消。")
            return
        if not sanitize_name(raw):
            print("  ⚠ 名称无效，请使用字母/数字/中文等。")
            continue
        if sanitize_name(raw) == sanitize_name(name):
            return
        if sanitize_name(raw) in store.list_profiles():
            print(f"  ⚠ 名称「{sanitize_name(raw)}」已存在，请换一个。")
            continue
        break
    try:
        store.rename_profile(name, raw)
        print(f"  ✅ 已重命名「{name}」→「{sanitize_name(raw)}」。")
    except (ValueError, OSError) as exc:
        print(f"  ❌ 重命名失败: {exc}")


def _calib_revalidate(store):
    name = _calib_choose(store, "选择要重新校验的棋盘")
    if name is None:
        return
    print(f"\n  重新校验棋盘「{name}」（拖框校准）...")
    ratios = run_calibration()
    if ratios is None:
        return
    store.save_profile(name, ratios)
    if store.active_name() == sanitize_name(name):
        store.activate(name)
        print(f"  ✅ 已重新校验并更新当前棋盘「{sanitize_name(name)}」。")
    else:
        print(f"  ✅ 已重新校验棋盘「{sanitize_name(name)}」。")


def calib_manage(store=None):
    """校验功能：多棋盘模板管理（主菜单按 [5] 进入）。

    按键：
      [1] 选择棋盘（激活已校验模板）
      [2] 添加棋盘（新增校验模板）
      [3] 删除棋盘
      [4] 重命名
      [5] 重新校验（选一个棋盘重新校准）
      [Q] 返回主菜单
    """
    store = store or CalibStore()

    # 首次进入：已有 calib.json 且还没有模板时，自动导入为「默认」并设为当前
    if not store.list_profiles() and store.current_exists():
        try:
            store.import_current('默认')
            store.activate('默认')
            print("  已把现有 calib.json 自动导入为模板「默认」并设为当前。")
        except Exception:
            pass

    while True:
        names = store.list_profiles()
        active = store.active_name()

        print("\n" + "=" * 52)
        print("  校验功能 - 棋盘模板管理")
        print("=" * 52)
        if names:
            print("  已保存的棋盘:")
            for i, n in enumerate(names, 1):
                mark = "  √ 当前" if n == active else ""
                print(f"    {i}. {n}{mark}")
        else:
            print("  暂无已保存的棋盘模板（请先按 [2] 添加）。")
        if not active and store.current_exists():
            print("    （当前 calib.json 未被任何模板管理，识别仍使用它）")

        print("=" * 52)
        print("  [1] 选择棋盘    [2] 添加棋盘")
        print("  [3] 删除棋盘    [4] 重命名")
        print("  [5] 重新校验    [Q] 返回主菜单")
        print("=" * 52)

        key = _read_key()
        if key in ('q', '\x1b'):
            print("Q\n  已返回主菜单。")
            return
        elif key == '1':
            _calib_select(store)
        elif key == '2':
            _calib_add(store)
        elif key == '3':
            _calib_delete(store)
        elif key == '4':
            _calib_rename(store)
        elif key == '5':
            _calib_revalidate(store)
        elif key is not None:
            print(f"  '{key}' 无效，请按 1 / 2 / 3 / 4 / 5 / Q")


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
    print("  [5]     校验管理（多棋盘模板：添加/选择/删除/重命名/重新校验）")
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
        elif key == '5':
            print("5 - 校验功能（棋盘模板管理）...")
            calib_manage()
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
                print(f"'{key}' 无效，请按 A / F / 1 / 3 / 4 / 5 / R / B / 空格 / 2 / E / Q")

    advisor.close()


if __name__ == '__main__':
    main()
