#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""infinite_battle 无限全自动对战模块单元测试（不依赖窗口/鼠标/引擎）。"""

import os
import sys
import time
import tempfile
import shutil
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 兼容中文/emoji 输出（测试进程直接跑时 stdout 可能是 GBK，避免 🏁/⏹/⚔ 打印崩溃）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

from fen_utils import fen_to_board                        # noqa: E402
from move_notation import apply_move                      # noqa: E402
from infinite_battle import (ClickPointStore, click_sequence,  # noqa: E402
                             is_fresh_position, is_opening_board,
                             play_one_game, run_coin_battle,
                             run_infinite_battle, wait_for_fresh_position,
                             wait_for_opening)

FEN1 = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'


def _no_red_board():
    b, _ = fen_to_board(FEN1)
    b[9][4] = None   # 去掉帅 K → is_game_over True（对局结束）
    return b


def _make_store():
    tmp = tempfile.mkdtemp(prefix='auto_click_test_')
    return ClickPointStore(os.path.join(tmp, 'auto_click.json')), tmp


# --- ClickPointStore ---

def test_store_add_list_move_delete():
    store, tmp = _make_store()
    try:
        assert store.list_points() == []
        assert store.add_point(100, 200) == 1
        assert store.add_point(300, 400) == 2
        assert store.list_points() == [(1, 100, 200), (2, 300, 400)]
        assert store.point_coords(1) == (100, 200)
        assert store.point_coords(9) is None
        assert store.move_point(1, 150, 250) is True
        assert store.list_points() == [(1, 150, 250), (2, 300, 400)]
        assert store.delete_point(2) is True
        assert store.list_points() == [(1, 150, 250)]
        assert store.delete_point(5) is False
        assert store.move_point(9, 0, 0) is False
    finally:
        shutil.rmtree(tmp)


def test_store_delete_renumbers():
    """删除后编号自动重排：编号 = 列表位置，恒等于点击顺序。"""
    store, tmp = _make_store()
    try:
        store.add_point(1, 1)
        store.add_point(2, 2)
        store.add_point(3, 3)
        store.delete_point(2)
        assert store.list_points() == [(1, 1, 1), (2, 3, 3)]
    finally:
        shutil.rmtree(tmp)


def test_store_interval():
    store, tmp = _make_store()
    try:
        assert store.interval() == 1.0
        store.set_interval(2.5)
        assert store.interval() == 2.5
    finally:
        shutil.rmtree(tmp)


def test_store_persist():
    tmp = tempfile.mkdtemp(prefix='auto_click_test_')
    try:
        path = os.path.join(tmp, 'auto_click.json')
        s1 = ClickPointStore(path)
        s1.add_point(10, 20)
        s1.set_interval(3.0)
        s2 = ClickPointStore(path)   # 重新加载
        assert s2.list_points() == [(1, 10, 20)]
        assert s2.interval() == 3.0
    finally:
        shutil.rmtree(tmp)


def test_store_load_corrupt_falls_back():
    tmp = tempfile.mkdtemp(prefix='auto_click_test_')
    try:
        path = os.path.join(tmp, 'auto_click.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{bad json')
        store = ClickPointStore(path)
        assert store.list_points() == []
        assert store.interval() == 1.0
    finally:
        shutil.rmtree(tmp)


# --- click_sequence ---

def test_click_sequence_order_and_interval():
    points = [(1, 2), (3, 4), (5, 6)]
    clicked = []
    sleeps = []
    orig = time.sleep
    time.sleep = lambda s: sleeps.append(s)
    try:
        click_sequence(points, 1.5, lambda x, y: clicked.append((x, y)))
    finally:
        time.sleep = orig
    assert clicked == [(1, 2), (3, 4), (5, 6)]   # 按编号顺序点击
    assert sleeps == [1.5, 1.5]                  # 相邻点击之间等待


def test_click_sequence_single_point_no_wait():
    clicked = []
    click_sequence([(9, 9)], 5.0, lambda x, y: clicked.append((x, y)))
    assert clicked == [(9, 9)]


# --- play_one_game（单局状态机）---

class _FakeAdvisor:
    def __init__(self, board, playing_red=True, collapsed=False):
        self._board = board
        self._playing_red = playing_red
        self._collapsed = collapsed

    def capture_board(self):
        return [row[:] for row in self._board], self._playing_red

    def recognition_collapsed(self, threshold=15):
        return self._collapsed


class _FakeBattle:
    def __init__(self, poll_interval=0.8):
        self.poll_interval = poll_interval
        self.move_count = 0
        self.my_turn = False
        self.playing_red = True
        self.last_board = None

    def update_board(self, board):
        return False


def test_play_one_game_finished_on_game_over():
    advisor = _FakeAdvisor(_no_red_board())
    status = play_one_game(advisor, _FakeBattle(), poll_key=lambda: None)
    assert status == 'finished'


def test_play_one_game_stopped_by_q():
    advisor = _FakeAdvisor(_no_red_board())
    status = play_one_game(advisor, _FakeBattle(), poll_key=lambda: 'q')
    assert status == 'stopped'


def test_play_one_game_max_moves_aborts():
    board, _ = fen_to_board(FEN1)   # 完整开局（帅将都在 → 非终局）
    advisor = _FakeAdvisor(board)
    battle = _FakeBattle()
    battle.move_count = 200   # 触发保护上限
    status = play_one_game(advisor, battle, poll_key=lambda: None)
    assert status == 'abort'


def test_play_one_game_finished_on_recognition_collapse():
    """对手投降/超时跳结算界面 → 识别崩溃 → 判定对局结束（返回 finished）。

    完整开局棋盘（帅将都在 → is_game_over 为 False），仅凭识别崩溃信号
    （last_corrections >= 阈值）就应判定结束，与 is_game_over 互补。
    """
    board, _ = fen_to_board(FEN1)
    advisor = _FakeAdvisor(board, collapsed=True)
    status = play_one_game(advisor, _FakeBattle(), poll_key=lambda: None)
    assert status == 'finished'


# --- run_infinite_battle（无限循环）---

class _FakeBattleLoop:
    """开局即终局（无帅），供无限循环测试：第 3 次开局抛异常。"""
    starts = 0

    def __init__(self, advisor, **kwargs):
        self.poll_interval = 0.8
        self.move_count = 0
        self.my_turn = False
        self.playing_red = True
        self.last_board = None

    def start(self):
        _FakeBattleLoop.starts += 1
        if _FakeBattleLoop.starts >= 3:
            raise RuntimeError('开局失败（测试用）')
        return [row[:] for row in _no_red_board()], True

    def update_board(self, board):
        return False


def test_run_infinite_battle_restarts_and_clicks():
    _FakeBattleLoop.starts = 0
    store, tmp = _make_store()
    clicked = []
    try:
        store.add_point(100, 200)
        store.set_interval(0)
        advisor = _FakeAdvisor(_no_red_board())
        with mock.patch('auto_battle.AutoBattle', _FakeBattleLoop):
            status = run_infinite_battle(advisor, store, restart_wait=0,
                                         clicker=lambda x, y: clicked.append((x, y)))
        assert status == 'restart_failed'
        # 两局结束后各点击一次坐标序列（按编号顺序）
        assert clicked == [(100, 200), (100, 200)]
    finally:
        shutil.rmtree(tmp)


def test_run_infinite_battle_no_points():
    _FakeBattleLoop.starts = 0
    store, tmp = _make_store()   # 不添加坐标
    try:
        advisor = _FakeAdvisor(_no_red_board())
        with mock.patch('auto_battle.AutoBattle', _FakeBattleLoop):
            status = run_infinite_battle(advisor, store, restart_wait=0,
                                         clicker=lambda x, y: None)
        assert status == 'no_points'
    finally:
        shutil.rmtree(tmp)


# --- is_opening_board（续局时判断「新局是否已就绪」）---

def _opening_board():
    """标准开局棋盘（红在下、屏幕方向与标准一致）。"""
    b, _ = fen_to_board(FEN1)
    return b


def _midgame_board():
    """比开局差 8 格（4 步：双炮 + 两兵）的中盘局面：应被 is_opening_board 拒绝。

    注意 UCI rank 从红方底线算起（红兵在 rank 3 → y=6），
    写错 rank 会搬到空格上导致步数不生效。
    """
    b = _opening_board()
    for uci in ('b2e2', 'b7e7', 'a3a4', 'c3c4'):
        b = apply_move(b, uci)
    return b


def test_is_opening_board_initial():
    assert is_opening_board(_opening_board()) is True


def test_is_opening_board_rotated():
    """黑方视角：开局棋盘 180° 旋转（红在上）同样视为已就绪。"""
    b = _opening_board()
    assert is_opening_board([row[::-1] for row in reversed(b)]) is True


def test_is_opening_board_red_first_move():
    """执黑时红方已先走一步（差 2 格）→ 仍在容忍范围内。"""
    b = apply_move(_opening_board(), 'b2e2')
    assert is_opening_board(b) is True


def test_is_opening_board_noise():
    """CNN 识别噪声（开局棋盘上随机多几个杂格）→ 容忍。"""
    b = _opening_board()
    b[5][5] = 'P'
    b[4][7] = 'p'
    assert is_opening_board(b) is True


def test_is_opening_board_rejects_midgame():
    """中盘/终局局面与开局差几十格 → 判定未就绪（避免把结算界面当新局）。"""
    assert is_opening_board(_midgame_board()) is False


# --- wait_for_opening（点击「再来一局」后轮询直到开局就绪）---

class _WaitAdvisor:
    """按顺序返回预设棋盘；超出后一直返回最后一个。"""

    def __init__(self, boards):
        self._boards = boards
        self.calls = 0
        self.last = None

    def capture_board(self):
        if self.calls < len(self._boards):
            self.last = [row[:] for row in self._boards[self.calls]]
            self.calls += 1
        return [row[:] for row in self.last], True


class _FailThenOkAdvisor:
    """前 fail_count 次 capture_board 抛异常（窗口/识别异常），之后返回开局棋盘。"""

    def __init__(self, fail_count=2, board=None):
        self.fail_count = fail_count
        self.board = board if board is not None else _opening_board()

    def capture_board(self):
        if self.fail_count > 0:
            self.fail_count -= 1
            raise RuntimeError('窗口不可见（测试用）')
        return [row[:] for row in self.board], True


def test_wait_for_opening_ok():
    """先识别到终局界面，再识别到开局棋盘 → 'ok'。"""
    advisor = _WaitAdvisor([_midgame_board(), _midgame_board(), _opening_board()])
    with mock.patch('infinite_battle.poll_stop', return_value=False), \
            mock.patch('time.sleep'):
        state = wait_for_opening(advisor, restart_wait=10, retries=3)
    assert state == 'ok'


def test_wait_for_opening_timeout():
    """始终识别不到开局棋盘 → 超时 'timeout'。"""
    advisor = _WaitAdvisor([_midgame_board()])
    with mock.patch('infinite_battle.poll_stop', return_value=False), \
            mock.patch('time.sleep'):
        state = wait_for_opening(advisor, restart_wait=0, retries=2)
    assert state == 'timeout'


def test_wait_for_opening_stopped():
    """等待期间按下 ` 键 → 立即取消 'stopped'。"""
    advisor = _WaitAdvisor([_midgame_board()])
    with mock.patch('infinite_battle.poll_stop', return_value=True), \
            mock.patch('time.sleep'):
        state = wait_for_opening(advisor, restart_wait=10, retries=3)
    assert state == 'stopped'


def test_wait_for_opening_tolerates_recognition_error():
    """截屏/识别异常不中断等待（视为还没就绪），恢复后立即放行。"""
    advisor = _FailThenOkAdvisor()
    with mock.patch('infinite_battle.poll_stop', return_value=False), \
            mock.patch('time.sleep'):
        state = wait_for_opening(advisor, restart_wait=10, retries=3)
    assert state == 'ok'


# --- run_infinite_battle：续局必须先等开局就绪（回归测试）---

def test_run_infinite_battle_reclicks_on_opening_timeout():
    """点击「再来一局」后 wait_for_opening 超时 → 重新点击坐标序列再等（而非退出）。"""
    _FakeBattleLoop.starts = 0
    store, tmp = _make_store()
    clicked = []
    try:
        store.add_point(100, 200)
        store.set_interval(0)
        advisor = _FakeAdvisor(_no_red_board())
        calls = {'n': 0}

        def _wfo(*args, **kwargs):
            calls['n'] += 1
            return 'timeout' if calls['n'] == 1 else 'ok'

        with mock.patch('auto_battle.AutoBattle', _FakeBattleLoop), \
                mock.patch('infinite_battle.wait_for_opening', side_effect=_wfo):
            status = run_infinite_battle(advisor, store, restart_wait=0,
                                         clicker=lambda x, y: clicked.append((x, y)))
        assert status == 'restart_failed'        # 第 3 次 battle.start() 失败（测试桩）
        # 第 1 局结束点击 1 次 → 超时重新点击 1 次 → 第 2 局结束点击 1 次
        assert clicked == [(100, 200), (100, 200), (100, 200)]
    finally:
        shutil.rmtree(tmp)


def test_run_infinite_battle_restart_cancelled_by_backtick():
    """等开局期间按 ` 键 → 取消续局退出（返回 'stopped'）。"""
    _FakeBattleLoop.starts = 0
    store, tmp = _make_store()
    try:
        store.add_point(100, 200)
        store.set_interval(0)
        advisor = _FakeAdvisor(_no_red_board())
        with mock.patch('auto_battle.AutoBattle', _FakeBattleLoop), \
                mock.patch('infinite_battle.wait_for_opening', return_value='stopped'):
            status = run_infinite_battle(advisor, store, restart_wait=0,
                                         clicker=lambda x, y: None)
        assert status == 'stopped'
    finally:
        shutil.rmtree(tmp)


# --- is_fresh_position（铜钱场续局：新随机局面是否就绪）---

def test_is_fresh_position_accepts_legal_boards():
    """帅将齐全的局面（标准开局 / 随机中局）→ 视为合法新局面。"""
    assert is_fresh_position(_opening_board()) is True
    assert is_fresh_position(_midgame_board()) is True


def test_is_fresh_position_rejects_game_over():
    """缺帅/将（终局或结算界面）→ 未就绪。"""
    assert is_fresh_position(_no_red_board()) is False


def test_is_fresh_position_rejects_unchanged_frame():
    """与上一帧逐格一致（画面还没刷新）→ 未就绪。"""
    b = _midgame_board()
    assert is_fresh_position([row[:] for row in b], prev_board=b) is False


def test_is_fresh_position_accepts_changed_frame():
    """与上一帧不同且帅将都在 → 就绪（上一帧是终局画面同样能放过新局）。"""
    assert is_fresh_position(_midgame_board(),
                             prev_board=_opening_board()) is True
    assert is_fresh_position(_opening_board(),
                             prev_board=_no_red_board()) is True


# --- wait_for_fresh_position（点击坐标后轮询到新随机局面）---

def test_wait_for_fresh_position_ok():
    """先识别到旧画面（终局/与上一帧相同），再识别到新合法局面 → 'ok'。"""
    advisor = _WaitAdvisor([_no_red_board(), _no_red_board(),
                            _midgame_board()])
    with mock.patch('infinite_battle.poll_stop', return_value=False), \
            mock.patch('time.sleep'):
        state = wait_for_fresh_position(advisor, prev_board=_no_red_board(),
                                        restart_wait=10, retries=3)
    assert state == 'ok'


def test_wait_for_fresh_position_timeout():
    """画面始终停在旧局面（与 prev_board 相同）→ 超时 'timeout'。"""
    b = _midgame_board()
    advisor = _WaitAdvisor([b])
    with mock.patch('infinite_battle.poll_stop', return_value=False), \
            mock.patch('time.sleep'):
        state = wait_for_fresh_position(advisor, prev_board=b,
                                        restart_wait=0, retries=2)
    assert state == 'timeout'


def test_wait_for_fresh_position_stopped():
    """等待期间按下 ` 键 → 立即取消 'stopped'。"""
    advisor = _WaitAdvisor([_midgame_board()])
    with mock.patch('infinite_battle.poll_stop', return_value=True), \
            mock.patch('time.sleep'):
        state = wait_for_fresh_position(advisor, restart_wait=10, retries=3)
    assert state == 'stopped'


def test_wait_for_fresh_position_tolerates_recognition_error():
    """截屏/识别异常不中断等待（视为还没就绪），恢复后立即放行。"""
    advisor = _FailThenOkAdvisor()
    with mock.patch('infinite_battle.poll_stop', return_value=False), \
            mock.patch('time.sleep'):
        state = wait_for_fresh_position(advisor, restart_wait=10, retries=3)
    assert state == 'ok'


# --- run_coin_battle（铜钱场无限循环：随机开局 + 新局面续局）---

class _FakeCoinBattleLoop:
    """铜钱场假 battle：断言必须以 random_opening=True 启动；第 3 次抛异常。"""

    starts = 0

    def __init__(self, advisor, **kwargs):
        self.poll_interval = 0.8
        self.move_count = 0
        self.my_turn = False
        self.playing_red = False
        self.last_board = None

    def start(self, random_opening=False):
        assert random_opening is True    # 铜钱场必须以随机中局开局启动
        _FakeCoinBattleLoop.starts += 1
        if _FakeCoinBattleLoop.starts >= 3:
            raise RuntimeError('开局失败（测试用）')
        return [row[:] for row in _no_red_board()], False

    def update_board(self, board):
        return False


def test_run_coin_battle_restarts_and_clicks():
    """单局结束后点击坐标续局；第 3 次开局失败 → 'restart_failed'，共点击 2 次。"""
    _FakeCoinBattleLoop.starts = 0
    store, tmp = _make_store()
    clicked = []
    try:
        store.add_point(100, 200)
        store.set_interval(0)
        advisor = _FakeAdvisor(_no_red_board())
        with mock.patch('auto_battle.AutoBattle', _FakeCoinBattleLoop), \
                mock.patch('infinite_battle.wait_for_fresh_position',
                           return_value='ok'):
            status = run_coin_battle(advisor, store, restart_wait=0,
                                     clicker=lambda x, y: clicked.append((x, y)))
        assert status == 'restart_failed'
        assert clicked == [(100, 200), (100, 200)]
    finally:
        shutil.rmtree(tmp)


def test_run_coin_battle_no_points():
    """对局结束但未配置坐标 → 'no_points'（不点击）。"""
    _FakeCoinBattleLoop.starts = 0
    store, tmp = _make_store()
    try:
        advisor = _FakeAdvisor(_no_red_board())
        with mock.patch('auto_battle.AutoBattle', _FakeCoinBattleLoop):
            status = run_coin_battle(advisor, store, restart_wait=0,
                                     clicker=lambda x, y: None)
        assert status == 'no_points'
    finally:
        shutil.rmtree(tmp)


def test_run_coin_battle_passes_prev_board_to_fresh_wait():
    """续局等待新局面时必须带上「上一帧棋盘」（区分新局与结算画面）。"""
    _FakeCoinBattleLoop.starts = 0
    store, tmp = _make_store()
    seen = {}
    try:
        store.add_point(100, 200)
        store.set_interval(0)
        advisor = _FakeAdvisor(_no_red_board())

        def _wff(*args, **kwargs):
            seen.update(kwargs)
            return 'ok'

        with mock.patch('auto_battle.AutoBattle', _FakeCoinBattleLoop), \
                mock.patch('infinite_battle.wait_for_fresh_position',
                           side_effect=_wff):
            run_coin_battle(advisor, store, restart_wait=0,
                            clicker=lambda x, y: None)
        assert seen.get('prev_board') is not None
        assert seen.get('restart_wait') == 0
    finally:
        shutil.rmtree(tmp)


def test_run_coin_battle_reclicks_on_fresh_timeout():
    """等待新局面超时 → 重新点击坐标序列再等（而非退出）。"""
    _FakeCoinBattleLoop.starts = 0
    store, tmp = _make_store()
    clicked = []
    try:
        store.add_point(100, 200)
        store.set_interval(0)
        advisor = _FakeAdvisor(_no_red_board())
        calls = {'n': 0}

        def _wff(*args, **kwargs):
            calls['n'] += 1
            return 'timeout' if calls['n'] == 1 else 'ok'

        with mock.patch('auto_battle.AutoBattle', _FakeCoinBattleLoop), \
                mock.patch('infinite_battle.wait_for_fresh_position',
                           side_effect=_wff):
            status = run_coin_battle(advisor, store, restart_wait=0,
                                     clicker=lambda x, y: clicked.append((x, y)))
        assert status == 'restart_failed'   # 第 3 次 battle.start() 失败（测试桩）
        # 第 1 局结束点击 1 次 → 超时重新点击 1 次 → 第 2 局结束点击 1 次
        assert clicked == [(100, 200), (100, 200), (100, 200)]
    finally:
        shutil.rmtree(tmp)


def test_run_coin_battle_cancelled_by_backtick():
    """等新局面期间按 ` 键 → 取消续局退出（返回 'stopped'）。"""
    _FakeCoinBattleLoop.starts = 0
    store, tmp = _make_store()
    try:
        store.add_point(100, 200)
        store.set_interval(0)
        advisor = _FakeAdvisor(_no_red_board())
        with mock.patch('auto_battle.AutoBattle', _FakeCoinBattleLoop), \
                mock.patch('infinite_battle.wait_for_fresh_position',
                           return_value='stopped'):
            status = run_coin_battle(advisor, store, restart_wait=0,
                                     clicker=lambda x, y: None)
        assert status == 'stopped'
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
        except Exception:
            failed += 1
            print(f"  [FAIL] {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

