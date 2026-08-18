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
from infinite_battle import (ClickPointStore, click_sequence,  # noqa: E402
                             play_one_game, run_infinite_battle)

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

