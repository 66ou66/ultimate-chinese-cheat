#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_player 坐标转换与点击执行单元测试（不依赖真实窗口/鼠标）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_player  # noqa: E402
from auto_player import uci_to_screen_cell  # noqa: E402


class _FakeBot:
    """模拟 Bot：仅提供坐标与 DPI 信息（不依赖窗口/截图）。"""

    def __init__(self, cols, rows, win_x=100.0, win_y=200.0, dpi_scale=1.0):
        self.cols_logical = cols
        self.rows_logical = rows
        self.win_x = win_x
        self.win_y = win_y
        self.dpi_scale = dpi_scale
        self.win_hwnd = None


def _make_player(dpi_scale=1.0, win_x=100.0, win_y=200.0):
    """构造 AutoPlayer：cols/rows 是含 win 偏移的绝对屏幕坐标（与真实 Bot 一致）。"""
    cols = [win_x + 10.0 + i * 40.0 for i in range(9)]   # win_x+10 ... win_x+330
    rows = [win_y + 10.0 + j * 40.0 for j in range(10)]  # win_y+10 ... win_y+370
    bot = _FakeBot(cols, rows, win_x=win_x, win_y=win_y, dpi_scale=dpi_scale)
    return auto_player.AutoPlayer(bot, verbose=False)


def _patch_mouse(monkeypatch):
    """把窗口激活/鼠标点击替换成记录调用的假实现，返回点击调用记录。"""
    calls = []
    monkeypatch.setattr(auto_player, '_activate_window', lambda hwnd: True)
    monkeypatch.setattr(auto_player, '_click_at',
                        lambda x, y, dwell=0.05: calls.append((x, y)) or (x, y))
    return calls


def test_cell_to_screen_same_scale():
    """dpi_scale=1.0：屏幕坐标 == cols_logical/rows_logical 原值。"""
    player = _make_player(dpi_scale=1.0)
    assert player.cell_to_screen(0, 0) == (110.0, 210.0)
    assert player.cell_to_screen(4, 5) == (270.0, 410.0)
    assert player.cell_to_screen(8, 9) == (430.0, 570.0)


def test_cell_to_screen_high_dpi():
    """dpi_scale=2.0：屏幕坐标 = win + (cols_logical - win) * dpi_scale。"""
    player = _make_player(dpi_scale=2.0)
    # cols_logical[4]=270, rows_logical[5]=410
    assert player.cell_to_screen(4, 5) == (100.0 + (270.0 - 100.0) * 2.0,
                                           200.0 + (410.0 - 200.0) * 2.0)
    assert player.cell_to_screen(4, 5) == (440.0, 620.0)


def test_cell_to_screen_zero_win():
    """win_x/win_y 为 0 时公式退化，仍按 dpi_scale 换算（边界情况）。"""
    bot = _FakeBot([i * 40.0 for i in range(9)],
                   [j * 40.0 for j in range(10)],
                   win_x=0.0, win_y=0.0, dpi_scale=2.0)
    player = auto_player.AutoPlayer(bot, verbose=False)
    assert player.cell_to_screen(4, 5) == (320.0, 400.0)


def test_click_cell_activates_then_clicks(monkeypatch):
    """click_cell 会先激活窗口，再以换算后的坐标点击。"""
    player = _make_player(dpi_scale=1.0)
    activated = []
    monkeypatch.setattr(auto_player, '_activate_window',
                        lambda hwnd: activated.append(hwnd) or True)
    monkeypatch.setattr(auto_player, '_click_at',
                        lambda x, y, dwell=0.05: (x, y))
    player.click_cell(1, 1)
    assert activated, "应当先激活窗口"
    assert activated[0] is None  # _FakeBot.win_hwnd 为 None


def test_click_cell_high_dpi_coords(monkeypatch):
    """高 DPI 下点击坐标按统一公式换算。"""
    player = _make_player(dpi_scale=2.0)
    calls = _patch_mouse(monkeypatch)
    player.click_cell(4, 5)
    assert calls == [(440.0, 620.0)]


def test_play_uci_red_click_order(monkeypatch):
    """执红 b2e2：先点起点 (c1,r7) 再点终点 (c4,r7)。"""
    player = _make_player(dpi_scale=1.0)
    calls = _patch_mouse(monkeypatch)
    player.play_uci('b2e2', True)
    assert calls == [(150.0, 490.0), (270.0, 490.0)]


def test_play_uci_black_click_order(monkeypatch):
    """执黑 b7e7：屏幕方向旋转 180°，起点 (c7,r7) 终点 (c4,r7)。"""
    player = _make_player(dpi_scale=1.0)
    calls = _patch_mouse(monkeypatch)
    player.play_uci('b7e7', False)
    # uci_to_screen_cell('b7e7', False) == (7,7,4,7)
    assert calls == [(390.0, 490.0), (270.0, 490.0)]


def test_uci_to_screen_red():
    """我执红：屏幕方向与标准方向一致（红炮 b2e2 平中）。"""
    assert uci_to_screen_cell('b2e2', True) == (1, 7, 4, 7)


def test_uci_to_screen_black():
    """我执黑：屏幕方向 = 标准方向旋转 180°（黑炮 b7e7 平中）。"""
    assert uci_to_screen_cell('b7e7', False) == (7, 7, 4, 7)


def test_red_black_inverse():
    """同一 UCI：执黑结果 = 执红结果再旋转 180°，两者自洽。"""
    red = uci_to_screen_cell('b2e2', True)     # (1,7,4,7)
    black = uci_to_screen_cell('b2e2', False)  # (7,2,4,2)
    assert black == (8 - red[0], 9 - red[1], 8 - red[2], 9 - red[3])


def test_screen_cell_matches_board_flip():
    """执黑时转换出的屏幕格子，与 board_to_fen 的 180° 翻转逻辑一致。"""
    from fen_utils import fen_to_board, uci_to_coord

    FEN_INIT = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'
    standard, _ = fen_to_board(FEN_INIT)

    (fx, fy), (tx, ty) = uci_to_coord('b7e7')  # 标准方向黑炮平中
    assert standard[fy][fx] == 'c'             # b7 确实是黑炮
    assert standard[ty][tx] is None            # e7 初始为空

    screen = [row[::-1] for row in reversed(standard)]  # 与 Bot.board_to_fen 翻转一致

    from_c, from_r, to_c, to_r = uci_to_screen_cell('b7e7', playing_red=False)
    assert (from_c, from_r) == (8 - fx, 9 - fy)
    assert (to_c, to_r) == (8 - tx, 9 - ty)
    assert screen[from_r][from_c] == 'c'       # 起点在屏幕棋盘上仍是黑炮
    assert screen[to_r][to_c] is None          # 终点在屏幕棋盘上为空


if __name__ == '__main__':
    import inspect
    import traceback

    class _FakeMonkeypatch:
        """极简 monkeypatch 替代品：替换 auto_player 模块属性，跑完自动还原。"""

        def __init__(self):
            self._saved = {}

        def setattr(self, target, name, value):
            self._saved[name] = getattr(target, name)
            setattr(target, name, value)

        def restore(self):
            for name, old in self._saved.items():
                setattr(auto_player, name, old)

    tests = [(k, v) for k, v in list(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        mp = _FakeMonkeypatch()
        try:
            args = (mp,) if 'monkeypatch' in inspect.signature(fn).parameters else ()
            fn(*args)
            print(f"  ✓ {name}")
        except Exception:
            failed += 1
            print(f"  ✗ {name}")
            traceback.print_exc()
        finally:
            mp.restore()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
