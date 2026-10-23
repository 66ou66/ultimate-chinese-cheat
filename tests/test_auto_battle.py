#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_battle 全自动对弈逻辑单元测试（不依赖窗口/鼠标/引擎）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 兼容中文/emoji 输出（直接跑测试时 stdout 可能是 GBK，避免 ✓/✗ 打印崩溃）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

from fen_utils import fen_to_board, board_placement, is_game_over  # noqa: E402
from move_notation import apply_move                               # noqa: E402
from board_diff import diff_boards                                 # noqa: E402
from auto_battle import AutoBattle                                 # noqa: E402

FEN1 = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'


def _boards():
    b1, _ = fen_to_board(FEN1)
    b2 = apply_move(b1, 'b2e2')   # 红炮平中
    b3 = apply_move(b2, 'b7e7')   # 黑炮平中
    return b1, b2, b3


# --- 纯函数：棋盘指纹 / 结束检测 ---

def test_board_placement_uniqueness():
    b1, b2, b3 = _boards()
    p1, p2, p3 = board_placement(b1), board_placement(b2), board_placement(b3)
    assert p1 != p2 != p3
    assert board_placement(b1) == p1  # 幂等


def test_board_placement_length():
    b1, _, _ = _boards()
    p = board_placement(b1)
    assert len(p.split('/')) == 10
    assert all(len(rank) == 9 for rank in p.split('/'))


def test_is_game_over():
    b1, _, _ = _boards()
    assert is_game_over(b1) is False
    b_no_red = [row[:] for row in b1]
    b_no_red[9][4] = None          # 去掉帅 K
    assert is_game_over(b_no_red) is True
    b_no_black = [row[:] for row in b1]
    b_no_black[0][4] = None        # 去掉将 k
    assert is_game_over(b_no_black) is True


# --- 差分状态机 ---

class _FakeAdvisorStart:
    """仅用于 start() 的假 advisor。"""

    def __init__(self, board, playing_red):
        self.board = board
        self.playing_red = playing_red
        self.bot = _FakeBot()   # start() 方向修正时会写 bot.playing_red

    def capture_board(self):
        return self.board, self.playing_red

    def board_to_full_fen(self, board, playing_red, turn):
        from fen_utils import board_to_fen, to_engine_board
        return board_to_fen(to_engine_board(board, playing_red), turn=turn)


def test_start_red_first():
    b1, _, _ = _boards()
    battle = AutoBattle(_FakeAdvisorStart(b1, True))
    board, pr = battle.start()
    assert pr is True
    assert battle.my_turn is True   # 执红先手，立即走
    assert battle.last_board == b1
    # v5: start() 初始化开局 FEN 与空历史（供引擎循环规则）
    assert battle.start_fen == f"{FEN1} w - - 0 1"
    assert battle.move_history == []


def test_start_black_waits():
    """红在屏幕上方（我方执黑）：开局局面与方向推断一致 → 等待对方先走。"""
    b1, _, _ = _boards()
    screen1 = [row[::-1] for row in reversed(b1)]   # 旋转 180°：红在上
    battle = AutoBattle(_FakeAdvisorStart(screen1, False))
    battle.start()
    assert battle.my_turn is False  # 执黑后手，等待对方先走
    # 象棋红方恒先手：红方还没走 → 开局 turn 必须是 'w'
    # （修复前错设成 'b'，会让 _current_turn() 在红走一步后错算成 'w'，
    #   引擎返回红方着法 → 程序点红方棋子 → 落子回读确认失败）
    assert battle.start_fen == f"{FEN1} w - - 0 1"
    assert battle.move_history == []


def test_start_corrects_wrong_orientation():
    """方向误报时以开局局面为准修正：fake 报"执黑"，但棋盘红在下 → 修正为执红。"""
    b1, _, _ = _boards()
    fake = _FakeAdvisorStart(b1, False)
    battle = AutoBattle(fake)
    board, pr = battle.start()
    assert pr is True
    assert fake.bot.playing_red is True   # 已同步修正到底层 Bot
    assert battle.my_turn is True


def test_start_black_detects_red_already_moved():
    """执黑时若红方已先走一步（屏幕棋盘 = 开局 + 红炮平中）→ 直接轮到我们走。

    这是修复前"红方先动一步后再启动脚本"卡在等待、需手动走一步的根因场景。
    """
    b1, b2, _ = _boards()          # b2 = 红炮平中（红方先走一步）
    screen_after_red = [row[::-1] for row in reversed(b2)]
    battle = AutoBattle(_FakeAdvisorStart(screen_after_red, False))
    board, pr = battle.start()
    assert pr is False
    assert battle.my_turn is True                  # 不再卡在"等待红方"
    assert battle.last_board == screen_after_red   # 基准仍用当前确认局面
    # 红方已先走一步 → 开局 turn='b'（轮到黑方走）
    assert battle.start_fen.endswith(' b - - 0 1')


def test_current_turn_black_after_red_first_move():
    """执黑、红方走完第一步后 _current_turn() 必须返回 'b'（轮到我方黑走）。

    修复前 start() 把 start_fen 的 turn 错设成 'b'，红方走完第一步后
    _current_turn() 会错误返回 'w'，引擎按"该红方走"返回红方着法 → 程序
    去点击红方棋子（对方棋子）→ 落子回读确认失败。这正是真实日志中
    "执黑点红兵 (r3,c2)→(r4,c2) 后 confirm_failed" 的根因场景。
    """
    b1, b2, _ = _boards()          # b2 = 红炮平中（红方先走一步）
    screen1 = [row[::-1] for row in reversed(b1)]        # 屏幕方向：红在上
    screen_after_red = [row[::-1] for row in reversed(b2)]
    battle = AutoBattle(_FakeAdvisorStart(screen1, False), confirm_times=2)
    battle.start()                                       # 执黑，红方未走
    assert battle.my_turn is False
    assert battle.start_fen.endswith(' w - - 0 1')       # 红方未走，turn='w'
    assert battle.update_board(screen_after_red) is False   # 第 1 帧：待确认
    assert battle.update_board(screen_after_red) is True    # 第 2 帧：确认对方走完
    assert battle.my_turn is True
    assert battle._current_turn() == 'b'                 # 关键断言：轮到我方黑走


def test_start_black_red_not_moved_single_noise():
    """执黑时开局局面带一个噪声格（未构成完整红方着法）→ 不误判，继续等待红方。"""
    b1, _, _ = _boards()
    screen1 = [row[::-1] for row in reversed(b1)]
    noisy = [row[:] for row in screen1]
    noisy[5][5] = 'P'   # 单格噪声（只有 to 格、无 from 格，构不成完整着法）
    battle = AutoBattle(_FakeAdvisorStart(noisy, False))
    battle.start()
    assert battle.my_turn is False
    assert battle.last_board == noisy


def test_update_board_detect_opponent_move():
    b1, _, _ = _boards()
    b_opp = apply_move(b1, 'b7e7')   # 黑炮平中（执红时对方=黑）
    battle = AutoBattle(None, confirm_times=2)
    battle.playing_red = True
    battle.last_board = [row[:] for row in b1]
    battle.my_turn = False
    assert battle.update_board(b_opp) is False  # 第一次：待确认
    assert battle.update_board(b_opp) is True   # 连续第二次：确认对方走完
    assert battle.my_turn is True
    assert battle.last_board == b_opp


def test_update_board_rejects_own_color_move_red():
    """执红时变化格是红方（己方）的子走的 → 不算"对方已落子"。"""
    b1, b2, _ = _boards()   # b2 = 红炮平中（己方颜色）
    battle = AutoBattle(None, confirm_times=2)
    battle.playing_red = True
    battle.last_board = [row[:] for row in b1]
    battle.my_turn = False
    assert battle.update_board(b2) is False
    assert battle.update_board(b2) is False   # 连续多帧也永不触发
    assert battle.my_turn is False


def test_update_board_rejects_own_color_move_black():
    """执黑（红在上）时变化格是黑方（己方）的子走的 → 不算"对方已落子"。"""
    b1, _, _ = _boards()
    screen1 = [row[::-1] for row in reversed(b1)]        # 屏幕方向：红在上
    std_move = apply_move(b1, 'b7e7')                    # 黑炮平中（己方颜色）
    screen_move = [row[::-1] for row in reversed(std_move)]
    battle = AutoBattle(None, confirm_times=2)
    battle.playing_red = False
    battle.last_board = [row[:] for row in screen1]
    battle.my_turn = False
    assert battle.update_board(screen_move) is False
    assert battle.update_board(screen_move) is False
    assert battle.my_turn is False


def test_update_board_accepts_opponent_move_black():
    """执黑（红在上）时变化格是红方（对方）的子走的 → 正常触发。"""
    b1, _, _ = _boards()
    screen1 = [row[::-1] for row in reversed(b1)]
    std_move = apply_move(b1, 'b2e2')                    # 红炮平中（对方颜色）
    screen_move = [row[::-1] for row in reversed(std_move)]
    battle = AutoBattle(None, confirm_times=2)
    battle.playing_red = False
    battle.last_board = [row[:] for row in screen1]
    battle.my_turn = False
    assert battle.update_board(screen_move) is False
    assert battle.update_board(screen_move) is True
    assert battle.my_turn is True


def test_update_board_same_placement_no_change():
    b1, _, _ = _boards()
    battle = AutoBattle(None, confirm_times=2)
    battle.last_board = [row[:] for row in b1]
    battle.my_turn = False
    assert battle.update_board(b1) is False
    assert battle.update_board(b1) is False
    assert battle.my_turn is False


def test_update_board_revert_resets_count():
    b1, b2, _ = _boards()
    battle = AutoBattle(None, confirm_times=2)
    battle.last_board = [row[:] for row in b1]
    battle.my_turn = False
    battle.update_board(b2)   # 计数 1
    battle.update_board(b1)   # 变回旧局面 → 重置
    battle.update_board(b2)   # 重新计数 1（不触发）
    assert battle.my_turn is False
    battle.update_board(b2)   # 连续第 2 次 → 触发
    assert battle.my_turn is True


def test_update_board_detect_opponent_move_with_noise():
    """对方着法 + 每帧不同的随机噪声格（>2 格变化）→ 仍能确认对方已走。

    这是修复前"等待对方卡死、一直找窗口/60 秒无进展"的根因场景：
    旧逻辑要求 diff 恰好 2 格（single_move_from_diff），CNN 识别噪声让
    diff 恒大于 2 格且每帧噪声位置不同，对方着法永远无法确认；
    新逻辑用滑动窗口投票，真实着法每帧稳定出现凑够票数即可确认。
    """
    b1, _, _ = _boards()
    b_opp = apply_move(b1, 'b7e7')   # 对方（黑）炮平中
    battle = AutoBattle(None, confirm_times=2)
    battle.playing_red = True
    battle.last_board = [row[:] for row in b1]
    battle.my_turn = False
    # 每帧 2 个随机噪声格（共 4 格变化，正是日志里那种 diff），
    # 且帧间噪声位置不同；真实着法 b7e7 每帧都存在
    noisy1 = [row[:] for row in b_opp]
    noisy1[3][3] = 'P'               # 噪声格
    noisy1[4][4] = 'c'               # 噪声格
    noisy2 = [row[:] for row in b_opp]
    noisy2[8][8] = 'c'               # 另一个噪声格
    noisy2[5][5] = 'P'               # 另一个噪声格
    assert len(diff_boards(b1, noisy1).changed) == 4
    assert battle.update_board(noisy1) is False   # 第 1 票
    assert battle.update_board(noisy2) is True    # 真实着法凑够 2 票
    assert battle.my_turn is True
    assert battle.last_board == noisy2


def test_update_board_pure_noise_no_trigger():
    """纯噪声（每帧候选步不同、无跨帧稳定的着法）→ 永不误触发。

    防止"滑动窗口投票"把随机噪声当成对方着法：噪声格每帧位置不同，
    各自最多 1 票，永远凑不够 confirm_times。
    """
    b1, _, _ = _boards()
    battle = AutoBattle(None, confirm_times=2)
    battle.playing_red = True
    battle.last_board = [row[:] for row in b1]
    battle.my_turn = False
    # 每帧一个"黑卒移位"的假候选步，但位置每帧都不同
    noise_frames = [
        ((3, 4, None), (4, 4, 'p')),   # (3,4)黑卒消失 → (4,4)出现黑卒
        ((3, 6, None), (4, 6, 'p')),   # 换一列再"移位"一次
        ((3, 0, None), (4, 0, 'p')),   # 再换一列
    ]
    for (fy, fx, p1), (ty, tx, p2) in noise_frames:
        noisy = [row[:] for row in b1]
        noisy[fy][fx] = p1
        noisy[ty][tx] = p2
        assert battle.update_board(noisy) is False
    assert battle.my_turn is False


# --- 落子流程（假 advisor + 替换 auto_player.pyautogui）---

class _FakeResult:
    def __init__(self, bestmove):
        self.bestmove = bestmove


class _FakeAdvisor:
    """假的 Advisor：run_auto 返回固定着法；capture_board 返回指定局面。"""

    def __init__(self, board, playing_red, bestmove='b2e2', after_board=None,
                 collapsed=False):
        self.board = board
        self.playing_red = playing_red
        self.bestmove = bestmove
        self.after_board = after_board
        self.collapsed = collapsed
        self.bot = _FakeBot()

    def run_auto(self, moves=None, fen=None):
        return {
            'board': self.board,
            'playing_red': self.playing_red,
            'turn': 'w' if self.playing_red else 'b',
            'fen': fen or self.board_to_full_fen(
                self.board, self.playing_red,
                'w' if self.playing_red else 'b'),
            'moves': list(moves) if moves else [],
            'result': _FakeResult(self.bestmove),
        }

    def board_to_full_fen(self, board, playing_red, turn):
        from fen_utils import board_to_fen, to_engine_board
        return board_to_fen(to_engine_board(board, playing_red), turn=turn)

    def capture_board(self):
        if self.after_board is not None:
            return self.after_board, self.playing_red
        return self.board, self.playing_red

    def recognition_collapsed(self, threshold=15):
        return self.collapsed


class _FlakyAdvisor(_FakeAdvisor):
    """按 capture_board 调用次数切换返回局面（模拟"点击被吞/对不上"后自愈）。

    前 `fail_reads` 次 capture_board 返回 `fail_board`（默认 = 起始局面，
    即"点击未生效/棋盘无变化"；也可传"有变化但对不上预期"的局面），
    此后返回 `ok_board`（表示落子已生效）。用于验证 v6「在超时窗口内
    持续重试直到确认成功」的行为。
    """

    def __init__(self, board, playing_red, bestmove, ok_board, fail_reads,
                 fail_board=None):
        super().__init__(board, playing_red, bestmove=bestmove)
        self.ok_board = ok_board
        self.fail_reads = fail_reads
        self.fail_board = fail_board if fail_board is not None else board
        self.reads = 0

    def capture_board(self):
        self.reads += 1
        if self.reads > self.fail_reads:
            return self.ok_board, self.playing_red
        return self.fail_board, self.playing_red


class _FakeBot:
    """假的 Bot：提供格子逻辑坐标与窗口信息（v3 坐标层需要 win_x/win_y/dpi_scale）。"""

    def __init__(self, win_x=0.0, win_y=0.0, dpi_scale=1.0):
        self.win_x = win_x
        self.win_y = win_y
        self.dpi_scale = dpi_scale
        self.cols_logical = [win_x + 100 + 60 * c for c in range(9)]
        self.rows_logical = [win_y + 100 + 60 * r for r in range(10)]
        self.win_hwnd = None


def _patch_pyautogui():
    """替换 auto_player 的窗口激活/鼠标点击，防止真实鼠标移动、点击与窗口置顶。"""
    import auto_player
    from unittest import mock
    _activate = mock.patch.object(auto_player, '_activate_window', return_value=True)
    _click = mock.patch.object(auto_player, '_click_at', return_value=(0, 0))
    _activate.start()
    _click.start()

    class _MultiPatcher:
        def stop(self):
            _click.stop()
            _activate.stop()

    return _MultiPatcher()


def test_play_my_move_ok():
    b1, b2, _ = _boards()
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='b2e2', after_board=b2)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=1.0, max_reads=3,
                                   wait_anim=0.01, wait_read=0.01) == 'ok'
    finally:
        patcher.stop()
    assert battle.my_turn is False
    assert battle.last_board == b2


def test_play_my_move_no_move():
    b1, _, _ = _boards()
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='')
    battle = AutoBattle(fake)
    assert battle.play_my_move(confirm_timeout=1.0, max_reads=1,
                               wait_anim=0, wait_read=0) == 'no_move'


def test_play_my_move_ok_black():
    """执黑（后手）：屏幕方向 = 标准方向旋转180°，expected 须按屏幕方向计算。"""
    b1, b2, _ = _boards()
    screen1 = [row[::-1] for row in reversed(b1)]
    screen2 = [row[::-1] for row in reversed(b2)]
    fake = _FakeAdvisor(screen1, playing_red=False, bestmove='b2e2', after_board=screen2)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=1.0, max_reads=3,
                                   wait_anim=0.01, wait_read=0.01) == 'ok'
    finally:
        patcher.stop()
    assert battle.my_turn is False
    assert battle.last_board == screen2


def test_play_my_move_confirm_failed():
    b1, _, _ = _boards()
    # after_board=黑炮平中（另一步棋），不含预期红炮 b2e2 的 from/to → 读不到
    b_wrong = apply_move(b1, 'b7e7')
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='b2e2', after_board=b_wrong)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=0.01, max_reads=2,
                                   wait_anim=0.01, wait_read=0.01) == 'confirm_failed'
    finally:
        patcher.stop()


def test_play_my_move_board_gone_on_collapse():
    """识别质量崩溃（对手投降/超时跳结算界面）→ 不再落子，返回 board_gone。"""
    b1, _, _ = _boards()
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='b2e2', collapsed=True)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=0.01, max_reads=2,
                                   wait_anim=0.01, wait_read=0.01) == 'board_gone'
    finally:
        patcher.stop()


def test_play_my_move_ok_with_extra_change():
    """回读帧 = 我方落子 + 对方同步又走一步（4 格变化）→ 仍判成功。

    这是修复前"落子回读确认失败"的典型现场：只要实际变化格里包含
    预期 from/to，多余变化格（对方抢步/噪声）不再导致失败。
    """
    b1, b2, _ = _boards()
    b3 = apply_move(b2, 'b7e7')   # 我方红炮 b2e2 + 对方黑炮 b7e7
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='b2e2', after_board=b3)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=1.0, max_reads=3,
                                   wait_anim=0.01, wait_read=0.01) == 'ok'
    finally:
        patcher.stop()
    assert battle.my_turn is False
    # v4：基准 = 干净预期局面（before+uci），不再把带噪声/对方抢步的
    # 回读帧写进 last_board。对方抢步（b7e7）不会被吞进基准，下一轮
    # update_board 会把它作为"对方着法"正确检测到。
    assert battle.last_board == b2


def test_play_my_move_ok_with_noise():
    """回读帧 = 我方落子 + 1 个识别噪声格（3 格变化）→ 仍判成功。"""
    b1, b2, _ = _boards()
    noisy = [row[:] for row in b2]
    noisy[3][3] = 'P'   # 噪声格
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='b2e2', after_board=noisy)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=1.0, max_reads=3,
                                   wait_anim=0.01, wait_read=0.01) == 'ok'
    finally:
        patcher.stop()
    assert battle.my_turn is False


def test_play_my_move_uses_last_board_as_baseline():
    """回读基准优先用已确认局面 self.last_board（引擎分析仍用最新截图）。"""
    b1, b2, _ = _boards()
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='b2e2', after_board=b2)
    battle = AutoBattle(fake, click_interval=0.01)
    battle.last_board = [row[:] for row in b1]   # 已确认局面与数据一致
    battle.my_turn = True
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=1.0, max_reads=2,
                                   wait_anim=0.01, wait_read=0.01) == 'ok'
    finally:
        patcher.stop()
    assert battle.last_board == b2


# --- v6: 落子确认改为「超时兜底」后持续重试的行为 ---

def test_play_my_move_retries_until_success():
    """点击多次"没反应"（超过旧版 max_retries=3）仍持续重试，直到确认成功。

    v6：改为超时兜底后，前 4 次点击无效、第 5 次才生效也应返回 'ok'，
    不再像旧版那样几次失败就误报 confirm_failed 暂停全自动对战。
    """
    b1, b2, _ = _boards()
    fake = _FlakyAdvisor(b1, True, 'b2e2', ok_board=b2, fail_reads=4)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=5.0, max_reads=1,
                                   wait_anim=0.0, wait_read=0.0) == 'ok'
    finally:
        patcher.stop()
    assert fake.reads >= 5          # 确实重试了 5 次才成功（旧版只允许 3 次）
    assert battle.my_turn is False
    assert battle.last_board == b2


def test_play_my_move_retries_when_board_mismatched():
    """回读帧"有变化但对不上预期"时也持续重试，直到某帧确认成功。"""
    b1, b2, _ = _boards()
    b_wrong = apply_move(b1, 'b7e7')   # 黑炮平中：有变化但不是我方 b2e2
    fake = _FlakyAdvisor(b1, True, 'b2e2', ok_board=b2, fail_reads=2,
                         fail_board=b_wrong)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=5.0, max_reads=1,
                                   wait_anim=0.0, wait_read=0.0) == 'ok'
    finally:
        patcher.stop()
    assert battle.my_turn is False
    assert battle.last_board == b2


def test_play_my_move_pauses_after_timeout_with_retries():
    """点击始终无反应 → 超时窗口内持续重试多次，超过 confirm_timeout 才失败。"""
    b1, _, _ = _boards()
    fake = _FlakyAdvisor(b1, True, 'b2e2', ok_board=b1, fail_reads=10 ** 9)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=1.5, max_reads=1,
                                   wait_anim=0.01, wait_read=0.0) == 'confirm_failed'
    finally:
        patcher.stop()
    assert fake.reads > 1           # 不是点一次就放弃，而是重试到超时


# --- 引擎着法空判 / 方向推断 ---

def test_has_move():
    from auto_battle import has_move
    assert has_move('b2e2') is True
    assert has_move('h2h9') is True
    assert has_move('') is False
    assert has_move('(none)') is False
    assert has_move('0000') is False
    assert has_move('(pass)') is False
    assert has_move('nomove') is False


def test_infer_orientation():
    from auto_battle import _infer_orientation
    b1, _, _ = _boards()
    # 标准开局：顶行全黑、底行全红 → 我方执红
    assert _infer_orientation(b1) is True
    # 旋转 180°：顶行全红、底行全黑 → 我方执黑
    flipped = [row[::-1] for row in reversed(b1)]
    assert _infer_orientation(flipped) is False
    # 中盘（底行缺子）→ 将/帅九宫兜底判向，红帅在下方 → 我方执红
    mid = [row[:] for row in b1]
    mid[9][0] = None
    assert _infer_orientation(mid) is True


# --- v5: 着法历史 / 屏幕→标准 UCI 转换 / 历史自愈 ---

def test_screen_move_to_uci_red():
    """执红：屏幕方向 == 标准方向，着法直接转 UCI。"""
    from auto_battle import _screen_move_to_uci
    # 屏幕 (fy,fx,ty,tx)：红炮 b2e2 = from(x=1,y=7)→to(x=4,y=7)
    assert _screen_move_to_uci(7, 1, 7, 4, True) == 'b2e2'


def test_screen_move_to_uci_black():
    """执黑：屏幕方向旋转 180°，须映射回标准方向再转 UCI。

    标准黑炮 b7e7：from(x=1,y=2)→to(x=4,y=2)。
    屏幕方向 = 标准旋转 180°：(x,y)→(8-x,9-y)，故 from(7,7)→to(4,7)。
    """
    from auto_battle import _screen_move_to_uci
    assert _screen_move_to_uci(7, 7, 7, 4, False) == 'b7e7'


def test_move_history_tracks_opponent_and_self():
    """我方着法 + 对方着法依次追加进历史（标准 UCI），顺序正确。"""
    b1, b2, b3 = _boards()   # b1=开局, b2=红炮平中, b3=黑炮平中
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='b2e2', after_board=b2)
    battle = AutoBattle(fake, click_interval=0.01)
    # 手动初始化（等价 start() 执红）：开局局面 + 空历史。
    # 注意不能用 battle.start()：_FakeAdvisor.capture_board() 在 after_board
    # 非空时返回 after_board，start() 会把 start_fen 设成 b2 的局面。
    battle.playing_red = True
    battle.last_board = [row[:] for row in b1]
    battle.start_fen = f"{FEN1} w - - 0 1"
    battle.move_history = []
    battle.my_turn = True
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(confirm_timeout=1.0, max_reads=3,
                                   wait_anim=0.01, wait_read=0.01) == 'ok'
    finally:
        patcher.stop()
    assert battle.move_history == ['b2e2']   # 我方红炮平中
    assert battle.last_board == b2
    # 对方（黑）炮平中 b7e7 → update_board 确认后追加
    battle.my_turn = False
    assert battle.update_board(b3) is False
    assert battle.update_board(b3) is True
    assert battle.move_history == ['b2e2', 'b7e7']
    assert battle.my_turn is True


def test_reconcile_history_keeps_when_consistent():
    """历史与当前局面一致 → 不重置，历史保留。"""
    b1, b2, _ = _boards()
    fake = _FakeAdvisor(b2, playing_red=True)
    battle = AutoBattle(fake, confirm_times=2)
    battle.playing_red = True
    battle.start_fen = f"{FEN1} w - - 0 1"
    battle.move_history = ['b2e2']     # 重放 = b2（红炮平中）
    assert battle._reconcile_history(b2, True) is True
    assert battle.move_history == ['b2e2']


def test_reconcile_history_resets_on_mismatch():
    """历史与当前局面不一致（噪声污染/中途启动）→ 重置并以当前局面为起点。"""
    b1, b2, _ = _boards()
    fake = _FakeAdvisor(b2, playing_red=True)
    battle = AutoBattle(fake, confirm_times=2)
    battle.playing_red = True
    battle.start_fen = f"{FEN1} w - - 0 1"
    battle.move_history = ['b2e2', 'b7e7']  # 重放 = 红+黑炮平中 ≠ 当前 b2
    assert battle._reconcile_history(b2, True) is False
    assert battle.move_history == []
    assert battle.start_fen.startswith('rnbakabnr')   # 已重置为当前局面


# --- 铜钱场：随机中局开局（random_opening=True）---

def _random_midgame():
    """随机中局局面：双方各走若干步、双方底线均已被破坏（贴近铜钱场随机开局）。

    底线被破坏后 infer_orientation 无法用「顶/底行整列满子」判色，
    只能走将/帅九宫兜底分支 —— 正是铜钱场真实场景。
    """
    b, _ = fen_to_board(FEN1)
    for uci in ('b2e2', 'b7e7', 'a0a1', 'a9a8', 'c3c4'):
        b = apply_move(b, uci)
    return b


def test_start_random_opening_red_moves_first():
    """铜钱场：我方执红 → 立即先手（红方恒先手，随机中局同样成立）。"""
    mid = _random_midgame()
    battle = AutoBattle(_FakeAdvisorStart(mid, True))
    board, pr = battle.start(random_opening=True)
    assert pr is True
    assert battle.my_turn is True
    assert battle.last_board == mid
    assert battle.start_fen.endswith(' w - - 0 1')   # 随机中局 FEN + 轮到红方
    assert battle.move_history == []


def test_start_random_opening_black_waits():
    """铜钱场：我方执黑 → 直接等红方先走（不做标准开局差分）。"""
    mid = _random_midgame()
    screen = [row[::-1] for row in reversed(mid)]      # 红在上 = 我方执黑
    battle = AutoBattle(_FakeAdvisorStart(screen, False))
    board, pr = battle.start(random_opening=True)
    assert pr is False
    assert battle.my_turn is False       # 关键：不能误判成"红方已走"
    assert battle.last_board == screen   # 基准仍用当前确认局面
    assert battle.start_fen.endswith(' w - - 0 1')     # 红尚未走 → 轮红
    assert battle.move_history == []


def test_random_opening_vs_standard_opening_black():
    """对照：同一随机中局，标准开局逻辑判"红方已走"，随机开局逻辑则等待红方。"""
    mid = _random_midgame()
    screen = [row[::-1] for row in reversed(mid)]
    std = AutoBattle(_FakeAdvisorStart(screen, False))
    std.start()                             # 标准开局：与 INITIAL_FEN 差分 → 红方已走
    assert std.my_turn is True
    assert std.start_fen.endswith(' b - - 0 1')
    rand = AutoBattle(_FakeAdvisorStart(screen, False))
    rand.start(random_opening=True)         # 随机开局：红恒先手 → 等待红方
    assert rand.my_turn is False
    assert rand.start_fen.endswith(' w - - 0 1')


def test_start_random_opening_corrects_orientation():
    """铜钱场每局可能互换先后手：方向推断仍以将/帅九宫为准自动纠正。"""
    mid = _random_midgame()
    fake = _FakeAdvisorStart(mid, False)    # 底行已破 → 走将/帅九宫兜底 → 判为执红
    battle = AutoBattle(fake)
    board, pr = battle.start(random_opening=True)
    assert pr is True
    assert fake.bot.playing_red is True     # 已同步修正到底层 Bot
    assert battle.my_turn is True


if __name__ == '__main__':
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except Exception:
            failed += 1
            print(f"  ✗ {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

