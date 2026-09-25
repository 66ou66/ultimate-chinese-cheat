#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无限全自动对战模块：一局对弈结束后自动点击一组预设坐标（如「再来一局」
按钮），从而自动开下一局、无限循环。

结构：
- ClickPointStore：坐标序列的增删改查 + 间隔 + JSON 持久化
- click_sequence：按编号顺序点击坐标（纯逻辑，可单测）
- is_opening_board：判断棋盘是否已是标准开局（纯逻辑，可单测）
- wait_for_opening：点击「再来一局」后轮询识别，确认画面已变为开局棋盘再放行
- play_one_game：单局对弈主循环（返回结束状态，供 F 单局 / 6 无限复用）
- run_infinite_battle：无限循环包装（'finished' → 点击坐标 → 等开局就绪 → 重新开局）

依赖：
- Win32 API（ctypes）：鼠标读取/移动/点击；非 Windows 回退 pyautogui
- hotkey.poll_stop：全局 `` ` `` 键边沿检测（无限对战期间任何焦点都能紧急退出）
- auto_battle.AutoBattle：差分状态机（lazy import，避免顶层加载引擎依赖）

纯逻辑部分（ClickPointStore / click_sequence / play_one_game 的状态判断）
不依赖鼠标 / 引擎 / 窗口，可独立单测；鼠标与引擎依赖在函数内 lazy import。
"""

import os
import sys
import json
import time
import ctypes
import ctypes.wintypes

try:
    import pyautogui
except ImportError:  # 非 Windows / 纯逻辑环境
    pyautogui = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hotkey import poll_stop  # noqa: E402

# --- 默认参数 ---
DEFAULT_INTERVAL = 1.0    # 坐标序列相邻两次点击间隔（秒）
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'auto_click.json')
POLL_INTERVAL = 0.8       # 对弈轮询间隔（秒，沿用 auto_battle）
WAIT_TIMEOUT = 180.0       # 单局无进展超时（秒）
MAX_MOVES = 200           # 单局最大走子数（保护上限）
RESTART_WAIT = 3.0        # 续局轮询：每轮等待新局加载的秒数上限（期间持续识别棋盘）
OPENING_DIFF_TOLERANCE = 6  # 与标准开局局面差异格数上限：≤ 视为「新局已就绪」。
                            # 覆盖 CNN 识别噪声（2~3 格）+ 执黑时红方先手第一步（2 格）；
                            # 终局/中盘局面与开局差异几十格，可可靠区分。

# Win32 鼠标事件常量
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

_IS_WINDOWS = (os.name == 'nt')
_ARROW_MAP = {b'H': 'up', b'P': 'down', b'K': 'left', b'M': 'right'}

# --- 中文棋盘打印（与 advisor.print_board_cn 视觉一致）---
# 内置于本模块：play_one_game 用，避免顶层 import advisor 拉入 cv2/torch 依赖
RED = '\033[31m'
BLK = '\033[90m'
RST = '\033[0m'

_PIECE_SYMBOLS = {
    'R': '车', 'N': '马', 'B': '相', 'A': '仕', 'K': '帅', 'C': '炮', 'P': '兵',
    'r': '车', 'n': '马', 'b': '象', 'a': '士', 'k': '将', 'c': '炮', 'p': '卒',
}


def print_board_cn(board, title="棋盘"):
    """打印中文棋盘（board[y][x]，y=0 为屏幕顶部，按从上到下打印）。"""
    print("\n" + "=" * 52)
    print(f"  {title}")
    print("=" * 52)
    for y in range(10):
        line = ""
        for x in range(9):
            p = board[y][x]
            if p:
                color = RED if p.isupper() else BLK
                line += f" {color}{_PIECE_SYMBOLS.get(p, '?')}{RST}"
            else:
                line += " 十"
        print(f"  {line}")
    print("   九 八 七 六 五 四 三 二 一")


def _poll_key():
    """非阻塞读取按键：有按键时返回小写字符，无按键返回 None。"""
    import msvcrt
    try:
        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getch()
    except Exception:
        return None
    try:
        return ch.decode('utf-8').lower()
    except UnicodeDecodeError:
        return None


class ClickPointStore:
    """坐标序列存储：添加 / 删除 / 移动 / 间隔 + JSON 持久化。

    坐标编号 = 列表下标 + 1（1-based），按编号升序即点击顺序；
    删除后编号自动重排，保证「数字决定点击顺序」恒成立。
    """

    def __init__(self, path=None):
        self.path = path or DEFAULT_PATH
        self._data = {'interval': DEFAULT_INTERVAL, 'points': []}
        self.load()

    # --- 查询 ---

    def interval(self):
        """坐标序列相邻两次点击间隔（秒）。"""
        return float(self._data.get('interval', DEFAULT_INTERVAL))

    def count(self):
        """坐标数量。"""
        return len(self._data['points'])

    def list_points(self):
        """返回 [(编号, x, y), ...]，编号 1-based 升序（即点击顺序）。"""
        return [(i + 1, int(p.get('x', 0)), int(p.get('y', 0)))
                for i, p in enumerate(self._data['points'])]

    def point_coords(self, idx):
        """返回编号 idx 的坐标 (x, y)；编号不存在返回 None。"""
        if not (1 <= idx <= self.count()):
            return None
        p = self._data['points'][idx - 1]
        return int(p.get('x', 0)), int(p.get('y', 0))

    # --- 写操作 ---

    def add_point(self, x, y):
        """追加坐标（自动接在最后，编号 = 当前数量 + 1）。返回新编号。"""
        self._data['points'].append({'x': int(round(x)), 'y': int(round(y))})
        self.save()
        return self.count()

    def delete_point(self, idx):
        """删除编号 idx 的坐标，之后的编号自动前移。返回是否删除成功。"""
        if not (1 <= idx <= self.count()):
            return False
        del self._data['points'][idx - 1]
        self.save()
        return True

    def move_point(self, idx, x, y):
        """更新编号 idx 的坐标为 (x, y)。返回是否成功。"""
        if not (1 <= idx <= self.count()):
            return False
        self._data['points'][idx - 1] = {'x': int(round(x)), 'y': int(round(y))}
        self.save()
        return True

    def set_interval(self, val):
        """设置坐标序列相邻点击间隔（秒）。"""
        self._data['interval'] = float(val)
        self.save()

    # --- 持久化 ---

    def load(self):
        """从 JSON 读取；文件缺失 / 损坏时回退默认（空坐标 + 默认间隔）。"""
        if not os.path.exists(self.path):
            self._data = {'interval': DEFAULT_INTERVAL, 'points': []}
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            self._data = {'interval': DEFAULT_INTERVAL, 'points': []}
            return
        points = raw.get('points', [])
        if not isinstance(points, list):
            points = []
        points = [p for p in points
                  if isinstance(p, dict) and 'x' in p and 'y' in p]
        try:
            interval = float(raw.get('interval', DEFAULT_INTERVAL))
        except (TypeError, ValueError):
            interval = DEFAULT_INTERVAL
        self._data = {'interval': interval, 'points': points}

    def save(self):
        """写回 JSON。"""
        try:
            os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass


def click_sequence(points, interval, clicker):
    """按顺序点击坐标序列，相邻两次之间等待 interval 秒。

    Args:
        points: [(x, y), ...]（已按编号排序，即点击顺序）
        interval: 相邻点击间隔（秒）
        clicker: 点击函数 clicker(x, y)
    """
    n = len(points)
    for i, (x, y) in enumerate(points):
        clicker(x, y)
        if i < n - 1 and interval > 0:
            time.sleep(interval)


def get_cursor_pos():
    """读取当前鼠标屏幕绝对坐标 (x, y)。"""
    if _IS_WINDOWS:
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    if pyautogui is not None:
        p = pyautogui.position()
        return int(p[0]), int(p[1])
    raise RuntimeError("无可用鼠标后端（Windows 需要 ctypes，非 Windows 需要 pyautogui）")


def _screen_size():
    """当前屏幕分辨率 (宽, 高)；获取失败返回 None。"""
    try:
        if _IS_WINDOWS:
            return (ctypes.windll.user32.GetSystemMetrics(0),
                    ctypes.windll.user32.GetSystemMetrics(1))
    except Exception:
        pass
    if pyautogui is not None:
        try:
            sz = pyautogui.size()
            return int(sz[0]), int(sz[1])
        except Exception:
            pass
    return None


def move_mouse(x, y):
    """移动鼠标到屏幕绝对坐标 (x, y)（自动 clamp 到屏幕范围内）。"""
    size = _screen_size()
    if size:
        x = max(0, min(int(round(x)), size[0] - 1))
        y = max(0, min(int(round(y)), size[1] - 1))
    if _IS_WINDOWS:
        ctypes.windll.user32.SetCursorPos(int(round(x)), int(round(y)))
        return
    if pyautogui is not None:
        pyautogui.moveTo(int(round(x)), int(round(y)))
        return
    raise RuntimeError("无可用鼠标后端（Windows 需要 ctypes，非 Windows 需要 pyautogui）")


def click_at(x, y):
    """移动到屏幕坐标 (x, y) 并左键点击（用于对局结束后点「再来一局」等按钮）。"""
    move_mouse(x, y)
    time.sleep(0.05)
    if _IS_WINDOWS:
        user32 = ctypes.windll.user32
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return
    if pyautogui is not None:
        pyautogui.click()
        return
    raise RuntimeError("无可用鼠标后端（Windows 需要 ctypes，非 Windows 需要 pyautogui）")


def is_opening_board(board):
    """判断 board 是否为「新开局」棋盘（标准开局或其 180° 旋转，且差异格数 ≤ 容忍值）。

    用于续局时区分「新局已加载（开局局面）」与「仍停留在结算/终局界面」：
    - 开局局面（含 CNN 噪声、执黑时红方先手第一步）与标准开局差异 ≤
      OPENING_DIFF_TOLERANCE 格 → True；
    - 中盘/终局局面与开局差异几十格 → False。
    屏幕方向无关：同时对比标准开局（红在下）与其 180° 旋转（红在上）。
    纯函数，不依赖窗口/鼠标/引擎，可独立单测。
    """
    from fen_utils import fen_to_board, INITIAL_FEN     # noqa: E402（lazy：保持模块顶层轻依赖）
    from board_diff import diff_boards                  # noqa: E402
    init, _ = fen_to_board(INITIAL_FEN)
    rotated = [row[::-1] for row in reversed(init)]
    for base in (init, rotated):
        if len(diff_boards(base, board).changed) <= OPENING_DIFF_TOLERANCE:
            return True
    return False


def wait_for_opening(advisor, restart_wait=RESTART_WAIT, retries=3):
    """点击「再来一局」后轮询识别，直到画面确认已是标准开局棋盘。

    替代固定 time.sleep：新局加载慢就多等，加载好了立即放行；期间每轮
    restart_wait 秒、最多 retries 轮，并持续响应 `` ` `` 紧急退出键。
    每轮至少识别一次（restart_wait=0 时也能立即完成一次判断，测试用）。

    Args:
        advisor: Advisor 实例（提供 capture_board()）
        restart_wait: 每轮轮询等待秒数上限（秒）
        retries: 最大轮数

    Returns:
        'ok': 已识别到开局棋盘（新局就绪）
        'stopped': 用户按 `` ` `` 键取消
        'timeout': 超过 retries × restart_wait 仍未见开局棋盘
    """
    for attempt in range(1, retries + 1):
        deadline = time.time() + restart_wait
        while True:
            if poll_stop():
                return 'stopped'
            try:
                board, _ = advisor.capture_board()
            except Exception:
                board = None  # 截屏/识别异常视为「新局还没就绪」，继续等
            if board is not None and is_opening_board(board):
                return 'ok'
            if time.time() >= deadline:
                break
            time.sleep(0.3)
        print(f"  ⏳ 尚未识别到开局棋盘，第 {attempt}/{retries} 轮等待..."
              f"（每轮最多 {restart_wait:g} 秒；按 ` 取消）")
    return 'timeout'


def play_one_game(advisor, battle, poll_key=None):
    """单局对弈主循环（差分等待对方 + 引擎落子 + 回读确认）。

    逻辑抽成可复用函数，供 [6] 无限全自动对战调用。

    Args:
        advisor: Advisor 实例
        battle: 已 start() 的 AutoBattle 实例
        poll_key: 非阻塞键盘读取函数（测试可注入），默认 _poll_key

    Returns:
        'finished': 对局正常结束（帅/将缺失 / 引擎无着法 / 我方将死对方 / 识别崩溃判定结束）
        'stopped': 用户按 `` ` `` / Q / Esc 手动停止
        'abort': 异常停止（落子确认失败 / 引擎异常 / 超时 / 步数上限）
    """
    from auto_battle import has_move
    from fen_utils import is_game_over as _is_game_over

    _poll = poll_key or _poll_key
    last_action = time.time()

    while True:
        key = _poll()
        if poll_stop():
            print("\n⏹ 已按 ` 键，立即退出全自动对战。")
            return 'stopped'
        if key in ('q', '\x1b'):
            print("\n⏹ 已手动停止全自动对战。")
            return 'stopped'

        try:
            board, _ = advisor.capture_board()
        except Exception as exc:
            print(f"⚠ 识别失败: {exc}（1 秒后重试）")
            time.sleep(1)
            continue

        if _is_game_over(board):
            print("\n🏁 检测到帅/将缺失，对局结束。")
            print_board_cn(board, "终局棋盘")
            return 'finished'

        # 识别质量崩溃 = 棋盘疑似已不在画面（对手投降/时间耗尽 → 提前跳到结算
        # 界面）。is_game_over 只查帅/将，垃圾棋盘里恰好仍含 K/k 时不判结束，
        # 这里用"合法性校验修正数激增"兜底判定对局结束，进入后续自动续局流程。
        if getattr(advisor, 'recognition_collapsed', None) and advisor.recognition_collapsed():
            print("\n🏁 识别质量骤降（棋盘疑似已切换为结算界面：对手投降/超时），对局结束。")
            return 'finished'

        if battle.move_count >= MAX_MOVES:
            print(f"\n🛑 我方已走满 {MAX_MOVES} 步，达到保护上限，自动停止。")
            return 'abort'

        if battle.my_turn:
            # 轮到我方：引擎分析 → 自动落子 → 回读确认
            print_board_cn(board, "识别结果")
            try:
                status = battle.play_my_move()
            except Exception as exc:
                print(f"\n❌ 我方回合处理异常: {exc}")
                print("   已暂停。请人工检查局面后按 [6] 重新启动；"
                      "若引擎异常请先运行引擎自检。")
                return 'abort'
            if status == 'no_move':
                print("\n🏁 引擎无合法着法，对局结束（我方被将死/困毙）。")
                return 'finished'
            if status == 'board_gone':
                print("\n🏁 识别质量骤降（棋盘已切换为结算界面：对手投降/超时），对局结束。")
                return 'finished'
            if status == 'confirm_failed':
                print("\n❌ 落子回读确认失败，已暂停。请人工检查棋盘与鼠标位置后，"
                      "再按 [6] 重新启动。")
                return 'abort'
            # 落子成功：立即校验"对方是否已无合法着法"（我方将死/困毙对方 → 判胜）
            try:
                opp_turn = 'b' if battle.playing_red else 'w'
                fen = advisor.board_to_full_fen(
                    battle.last_board, battle.playing_red, opp_turn)
                r2 = advisor.analyze(fen)
                if not has_move(r2.bestmove):
                    print("\n🏆 我方已将死/困毙对方，对局结束！")
                    print_board_cn(battle.last_board, "终局棋盘")
                    return 'finished'
            except Exception:
                pass  # 校验失败不阻断，继续正常监控
            print("✅ 我方已落子，继续监控对方...")
            last_action = time.time()
        else:
            # 等待对方：差分检测棋盘变化
            if battle.update_board(board):
                print("\n👀 检测到对方已落子，轮到我方！")
                print_board_cn(board, "对方走完")
                last_action = time.time()

        # 无进展超时保护（对局静默结束/识别卡住时防死循环）
        if time.time() - last_action > WAIT_TIMEOUT:
            print(f"\n⚠ 超过 {WAIT_TIMEOUT:g} 秒没有任何进展"
                  f"（对局可能已结束或识别卡住）。")
            print("   请人工检查局面；若对局已结束请直接退出，"
                  "若只是卡住可再按 [6] 重新启动。")
            return 'abort'

        # 小步进轮询：等待对方走子期间按 `` ` `` 也能 ~0.1 秒内响应紧急退出
        steps = max(1, int(battle.poll_interval / 0.05))
        for _ in range(steps):
            if poll_stop():
                print("\n⏹ 已按 ` 键，立即退出全自动对战。")
                return 'stopped'
            time.sleep(0.05)


def run_infinite_battle(advisor, store, poll_interval=POLL_INTERVAL,
                        restart_wait=RESTART_WAIT, restart_retries=3,
                        clicker=None):
    """无限全自动对战：单局结束后自动点击坐标序列续局，循环直到手动停止或异常。

    单局复用 play_one_game；仅当对局【正常结束】('finished') 才自动点击坐标
    重新开局；手动停止 / 异常（落子确认失败、超时、步数上限）一律停下交人工。
    点击「再来一局」后轮询识别开局棋盘；若始终未识别到（wait_for_opening
    超时），自动重新点击坐标序列再等（应对「再来一局」未生效 / 界面卡在结算页 /
    新局加载慢），直到识别成功或按 `` ` `` 取消。

    Args:
        advisor: Advisor 实例
        store: ClickPointStore（坐标序列 + 相邻点击间隔）
        poll_interval: 对弈轮询间隔（秒）
        restart_wait: 续局时每轮轮询等待新局加载的秒数上限（等待期间持续
                      识别棋盘，直到画面变为开局棋盘才放行）
        restart_retries: 每次点击坐标后 wait_for_opening 的识别轮数上限
        clicker: 点击函数 clicker(x, y)（测试可注入），默认 click_at

    Returns:
        'stopped': 用户手动停止
        'abort': 异常停止（确认失败/超时/步数上限）
        'no_points': 对局结束但未配置坐标，无法续局
        'restart_failed': 新局 battle.start() 开局识别失败（含重试后仍失败）
    """
    from auto_battle import AutoBattle

    do_click = clicker or click_at
    round_no = 0

    while True:
        battle = AutoBattle(advisor, poll_interval=poll_interval)
        try:
            board, playing_red = battle.start()
        except Exception as exc:
            if round_no == 0:
                print(f"\n❌ 第 1 局开局识别失败: {exc}")
                print("   请确认：天天象棋已打开且进入棋盘界面；校准数据有效。")
                return 'restart_failed'
            # 续局失败：可能是新局还没加载好，等待后重试
            for attempt in range(1, restart_retries + 1):
                print(f"  ⏳ 新局未就绪，{restart_wait:g} 秒后第"
                      f"{attempt}/{restart_retries} 次重试...")
                deadline = time.time() + restart_wait
                while time.time() < deadline:
                    if poll_stop():
                        print("\n⏹ 已按 ` 键，退出无限对战。")
                        return 'stopped'
                    time.sleep(0.05)
                battle = AutoBattle(advisor, poll_interval=poll_interval)
                try:
                    board, playing_red = battle.start()
                    break
                except Exception as exc2:
                    exc = exc2
            else:
                print(f"\n❌ 续局开局识别失败（已重试 {restart_retries} 次）: {exc}")
                print("   请确认：天天象棋已打开且进入棋盘界面；校准数据有效。")
                return 'restart_failed'

        round_no += 1
        side = '执红（先手）' if playing_red else '执黑（后手）'
        print(f"\n⚔ 第 {round_no} 局全自动对战开始：我方{side}。")
        print("   按 [Q]/[Esc] 或 [`] 键随时停止（` 为全局热键，无需点回本窗口）；")
        print("   鼠标甩到屏幕左上角紧急中止。")
        print_board_cn(board, "开局识别")

        status = play_one_game(advisor, battle)
        if status != 'finished':
            if status == 'stopped':
                print("\n⏹ 已停止无限全自动对战。")
            else:
                print("\n🛑 异常停止（落子确认失败 / 超时 / 步数上限），请人工介入。")
            return status

        # 对局正常结束 → 点击坐标续局
        points = [(x, y) for _, x, y in store.list_points()]
        if not points:
            print("\n⚠ 本局已结束，但未配置任何坐标，无法自动开始下一局。")
            print("   请返回菜单按 [3] 添加坐标（如「再来一局」按钮位置）。")
            return 'no_points'

        print(f"\n⏳ 本局已结束。{restart_wait:g} 秒后自动点击坐标开始下一局"
              f"（{len(points)} 个，间隔 {store.interval():g} 秒；按 ` 取消）...")
        deadline = time.time() + restart_wait
        while time.time() < deadline:
            if poll_stop():
                print("\n⏹ 已按 ` 键，取消自动续局，退出无限对战。")
                return 'stopped'
            time.sleep(0.05)

        # 点击坐标 → 等待开局就绪。若 wait_for_opening 超时（未识别到开局棋盘），
        # 自动重新点击坐标序列再等（应对「再来一局」未生效 / 界面卡在结算页 /
        # 新局加载慢），直到识别成功或按 ` 取消。
        while True:
            print("   🖱 自动点击坐标序列...")
            click_sequence(points, store.interval(), do_click)
            # 关键：点击「再来一局」后不能盲等固定时长——若识别太早，capture_board()
            # 会把仍在屏幕上的「结算/终局界面」当成新开局棋盘并静默返回（不抛异常），
            # 下一局状态机就会在错误局面上跑。这里轮询识别，直到画面确认已变为
            # 标准开局棋盘（is_opening_board）才进入下一局；超时则重新点击坐标再等。
            print("   ✅ 已点击坐标，等待新对局就绪（确认画面已变为开局棋盘）...")
            state = wait_for_opening(advisor, restart_wait=restart_wait,
                                     retries=restart_retries)
            if state == 'stopped':
                print("\n⏹ 已按 ` 键，取消自动续局，退出无限对战。")
                return 'stopped'
            if state == 'timeout':
                print("   ⚠ 未识别到开局棋盘，重新点击坐标序列续局（按 ` 取消）...")
                continue
            break  # state == 'ok'：画面已是开局棋盘，回到循环顶部由 battle.start() 正式开始

