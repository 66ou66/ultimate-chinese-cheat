#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国象棋 AI 助手 —— 主入口（纯终端 CLI）

流程：截屏识别棋盘 → 拼 FEN → Pikafish 引擎分析 → 输出中文建议（可选 DeepSeek 解释）。

使用前：
1. 打开天天象棋并进入棋盘界面；
2. 确认根目录下已有 engine/ 与 xiangqi-bot/ 及 calib.json（首次需校准）；
3. AI 解说密钥：主菜单按 [9] 进入「AI 解说设置」配置 API 密钥（未配置时 AI 解说不可用，不影响出招）。
   按 [2] 手动看解说；按 [E] 切换为"出建议后自动附带解说"。
"""

import os
import sys
import msvcrt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from advisor import Advisor, format_suggestion, print_board_cn  # noqa: E402
from explainer import DeepSeekExplainer              # noqa: E402
from move_notation import uci_to_cn, format_score, pv_to_cn  # noqa: E402
from fen_utils import to_engine_board  # noqa: E402
from calib_manager import CalibStore, run_calibration, sanitize_name  # noqa: E402
from hotkey import poll_stop                                   # noqa: E402
from infinite_battle import (ClickPointStore, get_cursor_pos,  # noqa: E402
                             move_mouse, run_coin_battle, run_infinite_battle)
from ai_settings import FIELDS, get_settings, reset_settings, save_settings  # noqa: E402
from auto_mode_settings import get_interval, save_interval            # noqa: E402

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


def _read_key_or_hotkey():
    """阻塞等待按键，期间轮询全局 `` ` `` 键（1 键左边）。

    `` ` `` 键被按下时立即返回 '`'，供"原本阻塞读键"的交互循环
    （如校验管理）在焦点被游戏窗口抢走时也能用 `` ` `` 退出返回主菜单。
    """
    import time as _time
    while True:
        if poll_stop():
            return '`'
        key = _poll_key()
        if key is not None:
            return key
        _time.sleep(0.02)


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
        print("   ⚠ AI 解说未启用：未检测到 API 密钥。")
        print("     请按 [9] 进入 AI 设置配置 API 密钥（DeepSeek 密钥）后即可使用，无需重启。")
        return None

    board = data['board']
    eb = to_engine_board(board, data['playing_red'])
    best_cn = uci_to_cn(result.bestmove, eb)
    best_score = format_score(
        result.candidates[0].score_cp,
        result.candidates[0].score_mate,
    ) if result.candidates else '?'
    pv_lines = [
        f"#{c.multipv} {' '.join(pv_to_cn(c.pv, eb)[:6])} ({format_score(c.score_cp, c.score_mate)})"
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
      [X]      设置间隔秒数（最小 0.5 秒，修改后自动保存，重启仍生效）
      [R]/[B]  设定当前轮到 红方/黑方 走
      [E]      切换自动解说（出建议后自动调 AI 解说）
      [4] / [`]  退出自动识别模式（`` ` `` 为全局紧急退出键）
      [Q]      退出程序
    """
    import time as _time

    interval = get_interval()
    paused = False
    last_run = 0.0  # 初始为0 → 进入模式后立即识别一次，随后每 interval 秒识别

    print("\n" + "=" * 52)
    print("  自动识别模式（每 x 秒自动识别 + 建议）")
    print("=" * 52)
    print("  [5] 暂停/恢复  [X] 设置间隔(>=0.5秒)")
    print("  [R]/[B] 设轮次")
    print("  [E] 自动解说开关  [4]/[`] 退出自动模式  [Q] 退出程序")

    while True:
        key = _poll_key()

        if poll_stop():
            print("` - 已退出自动识别模式。\n")
            return

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
            save_interval(interval)
            print(f"  ✅ 间隔已设为 {interval:g} 秒（已保存，重启后自动生效）")
        elif key == 'r':
            advisor.turn.set('w')
            print("  R - 已设为红方走")
        elif key == 'b':
            advisor.turn.set('b')
            print("  B - 已设为黑方走")
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


_ARROW_KEYS = {b'H': 'up', b'P': 'down', b'K': 'left', b'M': 'right'}


def _read_arrow_key():
    """阻塞读取键盘：方向键返回 'up'/'down'/'left'/'right'；普通键返回小写字符。

    优先检测全局 `` ` `` 键（返回 '`'），保证方向键微调坐标时也能随时紧急退出。
    """
    import time as _time
    while True:
        if poll_stop():
            return '`'
        try:
            if not msvcrt.kbhit():
                _time.sleep(0.02)
                continue
            ch = msvcrt.getch()
        except Exception:
            return None
        if ch in (b'\xe0', b'\x00'):   # 方向键：功能键前缀 + 第二字节
            try:
                ch2 = msvcrt.getch()
            except Exception:
                return None
            return _ARROW_KEYS.get(ch2, None)
        try:
            return ch.decode('utf-8').lower()
        except UnicodeDecodeError:
            return None


def _shift_pressed():
    """Shift 键是否处于按下状态（用于方向键加速步长）。"""
    import ctypes as _ctypes
    try:
        return bool(_ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
    except Exception:
        return False


def infinite_battle_menu(advisor):
    """无限全自动对战菜单：对局结束后自动点击坐标序列重新开局。

    菜单项：[1]移动坐标 [2]删除坐标 [3]添加坐标 [4]设置点击间隔
           [6]/[0]开始无限对战 [5]/[Q]/[`] 返回主菜单
    """
    from infinite_battle import ClickPointStore, run_infinite_battle

    store = ClickPointStore()
    while True:
        points = store.list_points()
        print("\n" + "=" * 52)
        print("  无限全自动对战（对局结束后自动点击坐标开局）")
        print("=" * 52)
        if points:
            print("  点击坐标序列（按编号顺序点击）：")
            for idx, x, y in points:
                print(f"    {idx}. ({x}, {y})")
        else:
            print("  暂无坐标（请先按 [3] 添加，如「再来一局」按钮位置）。")
        print(f"  点击间隔：{store.interval():g} 秒")
        print("=" * 52)
        print("  [1] 移动坐标  [2] 删除坐标  [3] 添加坐标")
        print("  [4] 设置点击间隔  [6]/[0] 开始无限对战")
        print("  [5]/[Q]/[`] 返回主菜单")
        print("=" * 52)
        key = _read_key_or_hotkey()
        if key in ('5', 'q', '\x1b', '`'):
            print(f"  {key} - 已返回主菜单。")
            return
        elif key in ('0', '6'):
            print("6 - 开始无限全自动对战...")
            run_infinite_battle(advisor, store)
        elif key == '1':
            print("1 - 移动坐标（方向键微调真实鼠标指针）...")
            if _point_move(store):
                return
        elif key == '2':
            print("2 - 删除坐标...")
            if _point_delete(store):
                return
        elif key == '3':
            print("3 - 添加坐标（鼠标移到目标位置后按回车记录）...")
            if _point_add(store):
                return
        elif key == '4':
            print("4 - 设置点击间隔...")
            _point_set_interval(store)
        elif key is not None:
            print(f"  '{key}' 无效，请按 1/2/3/4/6/5")


def coin_battle_menu(advisor):
    """铜钱场自动对战菜单：随机中局开局，对局结束后自动点击坐标序列开下一局。

    与 [6] 无限全自动对战的区别：
      1. 开局是**随机中局**（红方恒先手，我方可能执红或执黑，且每局可能互换），
         交由 AutoBattle.start(random_opening=True) 处理；
      2. 坐标存到独立文件 coin_click.json（铜钱场的「再来一局」等按钮位置
         通常与标准对战不同，两套坐标互不干扰）。

    菜单项：[1]移动坐标 [2]删除坐标 [3]添加坐标 [4]设置点击间隔
           [6]/[0]开始铜钱场对战 [5]/[Q]/[`] 返回主菜单
    """
    from infinite_battle import ClickPointStore, COIN_PATH, run_coin_battle

    store = ClickPointStore(COIN_PATH)
    while True:
        points = store.list_points()
        print("\n" + "=" * 52)
        print("  铜钱场自动对战（随机中局开局；结束后自动点击坐标开下一局）")
        print("=" * 52)
        if points:
            print("  点击坐标序列（按编号顺序点击）：")
            for idx, x, y in points:
                print(f"    {idx}. ({x}, {y})")
        else:
            print("  暂无坐标（请先按 [3] 添加，如铜钱场「再来一局」按钮位置）。")
        print(f"  点击间隔：{store.interval():g} 秒")
        print("=" * 52)
        print("  [1] 移动坐标  [2] 删除坐标  [3] 添加坐标")
        print("  [4] 设置点击间隔  [6]/[0] 开始铜钱场对战")
        print("  [5]/[Q]/[`] 返回主菜单")
        print("=" * 52)
        key = _read_key_or_hotkey()
        if key in ('5', 'q', '\x1b', '`'):
            print(f"  {key} - 已返回主菜单。")
            return
        elif key in ('0', '6'):
            print("6 - 开始铜钱场自动对战...")
            run_coin_battle(advisor, store)
        elif key == '1':
            print("1 - 移动坐标（方向键微调真实鼠标指针）...")
            if _point_move(store):
                return
        elif key == '2':
            print("2 - 删除坐标...")
            if _point_delete(store):
                return
        elif key == '3':
            print("3 - 添加坐标（鼠标移到目标位置后按回车记录）...")
            if _point_add(store):
                return
        elif key == '4':
            print("4 - 设置点击间隔...")
            _point_set_interval(store)
        elif key is not None:
            print(f"  '{key}' 无效，请按 1/2/3/4/6/5")


def _point_add(store):
    """添加坐标：鼠标移到目标位置 → 按回车记录当前绝对坐标。返回 True 表示按 ` 退出。"""
    print("\n  🖱 请把鼠标移到目标位置（如「再来一局」按钮）。")
    print("  【回车】记录当前鼠标位置；【Esc】/【`】取消。")
    while True:
        key = _read_key_or_hotkey()
        if key == '\r':
            x, y = get_cursor_pos()
            new_no = store.add_point(x, y)
            print(f"  ✅ 已添加坐标 {new_no}：({x}, {y})")
            return False
        if key == '`':
            print("`\n  ` 已返回主菜单。")
            return True
        if key in ('\x1b', 'q'):
            print("  已取消添加。")
            return False


def _point_delete(store):
    """删除坐标：按数字键选择编号。返回 True 表示按 ` 退出。"""
    points = store.list_points()
    if not points:
        print("  ⚠ 暂无坐标。")
        return False
    print("  当前坐标序列：")
    for idx, x, y in points:
        print(f"    {idx}. ({x}, {y})")
    print("  按数字键选择要删除的编号（1-9）；【Esc】/【`】取消。")
    while True:
        key = _read_key_or_hotkey()
        if key == '`':
            print("`\n  ` 已返回主菜单。")
            return True
        if key in ('\x1b', 'q'):
            print("  已取消删除。")
            return False
        if key and key.isdigit() and 1 <= int(key) <= len(points):
            idx = int(key)
            x, y = points[idx - 1][1], points[idx - 1][2]
            store.delete_point(idx)
            print(f"  ✅ 已删除坐标 {idx}：({x}, {y})。")
            return False
        if key is not None:
            print(f"  ⚠ 编号 '{key}' 不存在，请输入 1-{len(points)}。")


def _point_move(store):
    """移动坐标：选编号后方向键移动真实鼠标指针，回车保存。返回 True 表示按 ` 退出。"""
    points = store.list_points()
    if not points:
        print("  ⚠ 暂无坐标，请先按 [3] 添加。")
        return False
    print("  当前坐标序列：")
    for idx, x, y in points:
        print(f"    {idx}. ({x}, {y})")
    print("  按数字键选择要移动的编号（1-9）；【Esc】/【`】取消。")
    while True:
        key = _read_key_or_hotkey()
        if key == '`':
            print("`\n  ` 已返回主菜单。")
            return True
        if key in ('\x1b', 'q'):
            print("  已取消移动。")
            return False
        if key and key.isdigit() and 1 <= int(key) <= len(points):
            return _point_move_selected(store, int(key))
        if key is not None:
            print(f"  ⚠ 编号 '{key}' 不存在，请输入 1-{len(points)}。")


def _point_move_selected(store, idx):
    """用方向键移动真实鼠标指针微调坐标 idx：回车保存 / Esc 取消 / ` 退出菜单。"""
    x, y = store.point_coords(idx)
    move_mouse(x, y)
    print(f"\n  正在微调坐标 {idx}，当前 ({x}, {y})：")
    print("  【方向键】每步移动 5px（按住 Shift 一次 20px）；")
    print("  【回车】保存；【Esc】/【`】取消。")
    step = 5
    fast_step = 20
    while True:
        key = _read_arrow_key()
        if key in ('up', 'down', 'left', 'right'):
            s = fast_step if _shift_pressed() else step
            if key == 'up':
                y -= s
            elif key == 'down':
                y += s
            elif key == 'left':
                x -= s
            else:
                x += s
            move_mouse(x, y)
            x, y = get_cursor_pos()   # 读回真实位置（屏幕边界自动 clamp）
            print(f"    坐标 {idx} 当前: ({x}, {y})", end='\r')
        elif key == '\r':
            store.move_point(idx, x, y)
            print(f"\n  ✅ 坐标 {idx} 已保存为 ({x}, {y})。")
            return False
        elif key == '`':
            print("\n  ` 已返回主菜单。")
            return True
        elif key in ('\x1b', 'q'):
            print("\n  已取消移动，未保存。")
            return False


def _point_set_interval(store):
    """交互式设置坐标序列相邻点击间隔秒数（最小 0.1 秒）。"""
    print(f"\n  当前点击间隔：{store.interval():g} 秒")
    while True:
        try:
            raw = input("  输入新间隔秒数（最小 0.1，直接回车取消）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            print("  已取消。")
            return
        try:
            val = float(raw)
        except ValueError:
            print("  ⚠ 请输入数字，例如 1 或 1.5。")
            continue
        if val < 0.1:
            print("  ⚠ 间隔不能小于 0.1 秒。")
            continue
        store.set_interval(val)
        print(f"  ✅ 点击间隔已设为 {val:g} 秒。")
        return


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
      [Q] / [`]  返回主菜单（`` ` `` 为全局紧急退出键）
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
        print("  [5] 重新校验    [Q]/[`] 返回主菜单")
        print("=" * 52)

        key = _read_key_or_hotkey()
        if key == '`':
            print("`\n  已返回主菜单。")
            return
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


# ===== AI 解说设置菜单（主菜单按 [9] 进入）=====

def _mask_secret(value):
    """API 密钥打码显示：前 4 位 + **** + 后 4 位；过短则全 *；空显示 (空)。"""
    if not value:
        return "(空)"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "****" + value[-4:]


def _input_secret(prompt):
    """读取一行敏感输入并回显 *（用于 API 密钥等）。

    Returns:
        str: 输入内容（可为空字符串）；读键异常时返回 None。
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars = []
    while True:
        try:
            ch = msvcrt.getch()
        except Exception:
            sys.stdout.write("\n")
            sys.stdout.flush()
            return None
        if ch in (b"\r", b"\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(chars)
        if ch in (b"\x03",):  # Ctrl+C
            raise KeyboardInterrupt
        if ch in (b"\x08", b"\x7f"):  # 退格
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        try:
            text = ch.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if text:
            chars.append(text)
            sys.stdout.write("*")
            sys.stdout.flush()


def _edit_setting_field(field, current):
    """交互编辑单个 AI 解说参数，改完即保存到 ai_settings.json。"""
    info = {f[0]: f for f in FIELDS}[field]
    label, value_type = info[1], info[2]
    cur = current.get(field)

    # API 密钥：走 * 回显的安全输入（明文不落屏）
    if field == 'api_key':
        print(f"\n  当前 API 密钥: {_mask_secret(cur)}")
        print("  （输入回显为 *；直接回车保持原值；输入为空再回车 = 清除密钥停用解说）")
        try:
            raw = _input_secret("  新密钥: ")
        except KeyboardInterrupt:
            print("\n  已取消。")
            return
        if raw is None or raw.strip() == cur:
            print("  ✅ 密钥未变化。")
            return
        current[field] = raw.strip()
        save_settings(current)
        print("  ✅ 已保存 API 密钥，返回后立即生效。")
        return

    while True:
        try:
            raw = input(f"  当前 {label}: {cur}\n  新值（直接回车保持原值）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            print(f"  ✅ 保持原值: {cur}")
            return
        if value_type == 'str':
            if field == 'api_url' and not raw.startswith(("http://", "https://")):
                print("  ⚠ API 地址必须以 http:// 或 https:// 开头，请重新输入。")
                continue
            new_val = raw
        elif value_type == 'float':
            try:
                new_val = float(raw)
            except ValueError:
                print("  ⚠ 请输入数字，例如 30 或 0.4。")
                continue
            if field == 'timeout' and new_val <= 0:
                print("  ⚠ 超时秒数必须大于 0。")
                continue
            if field == 'temperature' and not (0 <= new_val <= 2):
                print("  ⚠ 随机性（temperature）必须在 0~2 之间。")
                continue
        else:  # int
            try:
                new_val = int(raw)
            except ValueError:
                print("  ⚠ 请输入整数。")
                continue
            if new_val < 1:
                print("  ⚠ max_tokens 必须 ≥ 1。")
                continue
        current[field] = new_val
        save_settings(current)
        print(f"  ✅ 已保存 {label}: {new_val}，返回后立即生效。")
        return


def ai_settings_menu(explainer=None):
    """AI 解说设置（主菜单按 [9] 进入）。

    数字键选择要修改的参数，修改后保存到 ai_settings.json 并立即生效（无需重启）。
    按键：
      [1] API 密钥    [2] API 地址    [3] 模型名
      [4] 超时秒数    [5] 随机性 0~2 [6] 最大 token
      [R] 恢复默认    [Q] / [`] 返回主菜单（` 为全局紧急退出键）
    """
    field_keys = {'1': 'api_key', '2': 'api_url', '3': 'model',
                  '4': 'timeout', '5': 'temperature', '6': 'max_tokens'}
    while True:
        s = get_settings()
        print("\n" + "=" * 52)
        print("  AI 解说设置（保存到 ai_settings.json，修改立即生效）")
        print("=" * 52)
        print(f"  [1] API 密钥   : {_mask_secret(s.get('api_key'))}")
        print(f"  [2] API 地址   : {s.get('api_url')}")
        print(f"  [3] 模型名     : {s.get('model')}")
        print(f"  [4] 超时秒数   : {s.get('timeout')}")
        print(f"  [5] 随机性 0~2 : {s.get('temperature')}")
        print(f"  [6] 最大 token : {s.get('max_tokens')}")
        print("-" * 52)
        print("  [R] 恢复默认（回到 ai_config.py 中的值）")
        print("  [Q] / [`] 返回主菜单")
        print("=" * 52)

        key = _read_key_or_hotkey()
        if key == '`':
            print("`\n  已返回主菜单。")
            return
        if key in ('q', '\x1b'):
            print("Q\n  已返回主菜单。")
            return
        if key == 'r':
            if reset_settings():
                print("  ✅ 已恢复默认设置（ai_config.py 中的值）。")
            else:
                print("  ✅ 当前已是默认设置。")
            if explainer is not None:
                explainer._refresh()
            continue
        field = field_keys.get(key)
        if field is None:
            if key is not None:
                print(f"  '{key}' 无效，请按 1 / 2 / 3 / 4 / 5 / 6 / R / Q")
            continue
        _edit_setting_field(field, s)
        if explainer is not None:
            explainer._refresh()


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
        print("[AI]   AI 解释未启用：请按 [9] 进入 AI 设置配置 API 密钥后再使用 AI 解说（不影响出招）")

    print("\n操作说明：")
    print("  [1]     识别当前棋盘并给出建议（多帧投票去噪，识别更稳）")
    print("  [3]     识别 + 人工修正 + 分析")
    print("  [4]     自动识别模式（每 x 秒识别+建议，[5]暂停，[X]设间隔）")
    print("  [5]     校验管理（多棋盘模板：添加/选择/删除/重命名/重新校验）")
    print("  [9]     AI 解说设置（API 密钥/地址/模型/超时/随机性/max_tokens）")
    print("  [6]     无限全自动对战（对局结束自动点坐标续局，[1~4] 配置坐标）")
    print("  [7]     铜钱场自动对战（随机中局开局，红先黑后，独立坐标配置）")
    print("  [`]     全局紧急退出键：自动/识别/校验/编辑中随时按它返回主菜单")
    print("  [R]/[B] 设定当前轮到 红方/黑方 走")
    print("  [2]     用 AI 解释上一次建议")
    print("  [E]     切换自动解说（开启后 [1]/[3]/[4] 出建议即自动附带 AI 解说）")
    print("  [Q]     退出")

    while True:
        print(f"\n⏳ 当前轮次={advisor.turn.label()}，等待按键... ", end="", flush=True)
        key = _read_key()

        if key in ('q', '\x1b'):
            print("Q\n👋 退出。")
            break
        elif key == '6':
            print("6 - 无限全自动对战...")
            infinite_battle_menu(advisor)
        elif key == '7':
            print("7 - 铜钱场自动对战...")
            coin_battle_menu(advisor)
        elif key == '1':
            print("1 - 识别并分析（多帧投票去噪）...")
            try:
                advisor.run_once_voted(advisor.turn.current())
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
        elif key == '9':
            print("9 - AI 解说设置...")
            ai_settings_menu(explainer)
        elif key == 'r':
            advisor.turn.set('w')
            print("R - 已设为红方走")
        elif key == 'b':
            advisor.turn.set('b')
            print("B - 已设为黑方走")
        elif key == '2':
            _ai_explain_last(advisor, explainer)
        elif key == 'e':
            state['auto_explain'] = not state['auto_explain']
            mode = '自动解说' if state['auto_explain'] else '手动解说'
            print(f"  E - 已切换为{mode}（出建议后{'自动' if state['auto_explain'] else '不自动'}调 AI 解说）")
        else:
            if key is not None:
                print(f"'{key}' 无效，请按 9 / 6 / 7 / 1 / 3 / 4 / 5 / R / B / 2 / E / Q")

    advisor.close()


if __name__ == '__main__':
    main()
