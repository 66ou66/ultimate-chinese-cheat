#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""window_settings（主菜单 [8] 窗口设置）单元测试。

分三部分：
1. 持久化：mock.patch 把 window_settings.SETTINGS_PATH 指向临时目录，验证
   默认值 / 保存重载 / 损坏回退 / 非法字段回退 / 行列下限钳制 / 旧版像素字段
   回退 / 恢复默认。
2. 窗口位置：把 ctypes.windll 换成假 kernel32/user32，验证 get_console_rect
   读回矩形、set_console_pos 的传参与旗标（SWP_NOSIZE）、无控制台（hwnd=0）
   时安全返回。
3. 窗口大小：验证 get_console_grid 解析 CONSOLE_SCREEN_BUFFER_INFO、
   set_console_size 的调用顺序（扩大先扩缓冲区、缩小先缩窗口）/ 行列钳制 /
   失败返回 False，以及 apply_window_settings 的启用/停用行为。
4. 窗口大小回退：Windows Terminal / 新版 conhost 忽略 SetConsoleWindowInfo
   （窗口边框像素尺寸不变）时，验证退回用 SetWindowPos 按实测字符单元格像素
   改窗口（_cell_px / _window_resize_target / _set_window_size_px），并用
   窗口像素尺寸而不是行列来判断成败。

不依赖真实窗口：全程不调用真实 Win32，也不启动任何外部进程。
"""

import ctypes
import json
import os
import shutil
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 兼容中文/emoji 输出（直接跑测试时 stdout 可能是 GBK）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import window_settings  # noqa: E402
from window_settings import (GRID_STEP, MIN_COLS, MIN_ROWS, STEP,  # noqa: E402
                             SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOSIZE)


# --- 假组件 ---

class _FakeUser32:
    """实现窗口位置/大小用到的 user32 API，并分别记录调用。

    `client` / `cell` / `linked_kernel` 用于回退路径（_set_window_size_px）：
    默认 client=None 表示「没有 GetClientRect」→ 无法测量单元格，回退直接失败；
    传入 client 与 cell 后，SetWindowPos 会模拟真实终端把窗口像素尺寸换算成字符
    行列写回 linked_kernel，供读回校验。
    """

    def __init__(self, rect=(10, 20, 810, 620), rect_result=1, set_result=1,
                 client=None, client_result=1, linked_kernel=None, cell=(10, 24)):
        self.rect = rect              # (left, top, right, bottom)
        self.rect_result = rect_result
        self.set_result = set_result
        self.client = client          # (width, height) 客户区像素；None=不支持该 API
        self.client_result = client_result
        self.linked_kernel = linked_kernel  # 联动假 kernel32：SetWindowPos 真的改行列
        self.cell = cell              # (cell_w, cell_h) 字符单元格像素
        self.rect_calls = []
        self.set_calls = []
        self.client_calls = []

    def resize_to(self, cols, rows, cell=None):
        """把窗口像素矩形与客户区改成 cols 列 × rows 行（模拟真实窗口缩放）。"""
        cell_w, cell_h = cell if cell is not None else self.cell
        self.rect = (self.rect[0], self.rect[1],
                     self.rect[0] + cols * cell_w, self.rect[1] + rows * cell_h)
        self.client = (cols * cell_w, rows * cell_h)

    def GetWindowRect(self, hwnd, rect_ptr):
        self.rect_calls.append(hwnd)
        target = getattr(rect_ptr, '_obj', None)   # byref 包装的真实 RECT
        if target is not None:
            target.left, target.top, target.right, target.bottom = self.rect
        return self.rect_result

    def GetClientRect(self, hwnd, rect_ptr):
        self.client_calls.append(hwnd)
        if self.client is None:                    # 模拟 API 不可用
            return 0
        target = getattr(rect_ptr, '_obj', None)
        if target is not None:
            target.left, target.top = 0, 0
            target.right, target.bottom = self.client
        return self.client_result

    def SetWindowPos(self, hwnd, insert_after, x, y, cx, cy, flags):
        self.set_calls.append(('SetWindowPos', hwnd, insert_after, x, y, cx, cy, flags))
        if self.set_result and self.linked_kernel is not None:
            # 模拟真实终端：窗口像素尺寸变了 → 字符窗口按单元格尺寸吸附后跟着变
            self.rect = (self.rect[0], self.rect[1],
                         self.rect[0] + cx, self.rect[1] + cy)
            cell_w, cell_h = self.cell
            cols = int((self.rect[2] - self.rect[0]) // cell_w)
            rows = int((self.rect[3] - self.rect[1]) // cell_h)
            self.client = (cols * cell_w, rows * cell_h)
            self.linked_kernel.window = (0, 0, max(0, cols - 1), max(0, rows - 1))
        return self.set_result


class _FakeKernel32:
    """假 kernel32：窗口句柄 / 缓冲区句柄 / 行列信息 / 缓冲区与窗口调整。

    默认值模拟典型 cmd 窗口：屏幕缓冲区 120 列 × 9001 行（有滚动历史）、
    可见窗口 80 列 × 25 行、当前字体与屏幕下最大 240 列 × 80 行。
    """

    def __init__(self, hwnd=0x1234, handle=0x9999, conout=0x7777,
                 buffer=(120, 9001), window=(0, 0, 79, 24), max_window=(240, 80),
                 info_result=1, buffer_result=1, window_result=1,
                 update_window=True, cell=(10, 24), user32=None, move_pixels=True):
        self.hwnd = hwnd
        self.handle = handle
        self.conout = conout          # stdout 被重定向时 CONOUT$ 打开的句柄（0=打不开）
        self.conout_calls = []
        self.buffer = buffer          # (cols, rows) 屏幕缓冲区
        self.window = window          # (left, top, right, bottom) 可见窗口（字符坐标）
        self.max_window = max_window  # (cols, rows) 允许的最大窗口
        self.info_result = info_result
        self.buffer_result = buffer_result
        self.window_result = window_result
        # update_window=False 模拟「API 报成功但可见窗口纹丝不动」（Windows Terminal）
        self.update_window = update_window
        self.cell = cell              # (cell_w, cell_h) 字符单元格像素
        self.user32 = user32          # 联动假 user32：改窗口时同步像素矩形
        # move_pixels=False 模拟「API 改了行列/缓冲区，但窗口像素尺寸纹丝不动」
        # （Windows Terminal：看起来只剩滚动条/白条，边框没动）
        self.move_pixels = move_pixels
        self.hwnd_calls = 0
        self.order = []               # 调用顺序，用于验证「先扩缓冲区 / 先缩窗口」

    def GetConsoleWindow(self):
        self.hwnd_calls += 1
        return self.hwnd

    def GetStdHandle(self, which):
        return self.handle

    def CreateFileW(self, name, access, share, sec, disposition, flags, template):
        self.conout_calls.append(name)
        return self.conout

    def GetConsoleScreenBufferInfo(self, handle, info_ptr):
        target = getattr(info_ptr, '_obj', None)
        if target is not None:
            target.dwSize.X, target.dwSize.Y = self.buffer
            left, top, right, bottom = self.window
            target.srWindow.Left, target.srWindow.Top = left, top
            target.srWindow.Right, target.srWindow.Bottom = right, bottom
            target.wAttributes = 0x07
            target.dwMaximumWindowSize.X = self.max_window[0]
            target.dwMaximumWindowSize.Y = self.max_window[1]
        return self.info_result

    def SetConsoleScreenBufferSize(self, handle, coord):
        self.order.append(('buffer', coord.X, coord.Y))
        return self.buffer_result

    def SetConsoleWindowInfo(self, handle, absolute, rect_ptr):
        target = getattr(rect_ptr, '_obj', None)
        if target is None:
            self.order.append(('window', absolute, None, None, None, None))
        else:
            self.order.append(('window', absolute, target.Left, target.Top,
                               target.Right, target.Bottom))
        if self.window_result and self.update_window and target is not None:
            # 真实 conhost：API 成功后可见窗口行列随之变化（供读回校验），
            # 窗口的像素矩形与客户区也跟着变（供 set_console_size 的像素校验）
            self.window = (target.Left, target.Top, target.Right, target.Bottom)
            if self.user32 is not None and self.move_pixels:
                self.user32.resize_to(target.Right - target.Left + 1,
                                      target.Bottom - target.Top + 1, self.cell)
        return self.window_result


def _patch_windll(user32=None, kernel32=None):
    """把 ctypes.windll 换成假的 kernel32/user32（退出 with 时自动还原）。"""
    return mock.patch.object(
        ctypes, 'windll',
        mock.Mock(user32=user32 if user32 is not None else _FakeUser32(),
                  kernel32=kernel32 if kernel32 is not None else _FakeKernel32()))


def _console_pair(window=(0, 0, 79, 24), buffer=(120, 9001), max_window=(240, 80),
                  cell=(10, 24), hwnd=0x1234, handle=0x9999, conout=0x7777,
                  window_result=1, buffer_result=1, update_window=True,
                  move_pixels=True, set_result=1, include_client=True):
    """构造一对互相联动的假 kernel32 / user32，模拟一个真实控制台窗口。

    两边共享字符单元格像素尺寸 cell，并让「窗口像素矩形 / 客户区」与「可见窗口
    字符行列」始终自洽：

      * SetConsoleWindowInfo 成功 → 字符窗口变，窗口像素矩形与客户区跟着变（conhost）；
      * SetWindowPos 成功 → 窗口像素矩形变，字符窗口按 cell 吸附后跟着变（终端）。

    这样 set_console_size 的两条路径、窗口像素校验和像素吸附重试都能被真实验证。

    Args:
        cell: 字符单元格像素 (cell_w, cell_h)。
        update_window: False=API 报成功但可见窗口行列也不变。
        move_pixels: False=API 改了行列/缓冲区但窗口像素尺寸不动（Windows Terminal）。
        include_client: False 表示 GetClientRect 不可用（无法换算单元格像素）。

    Returns:
        tuple: (user32, kernel32)（已互相挂好引用）。
    """
    cols = window[2] - window[0] + 1
    rows = window[3] - window[1] + 1
    kernel = _FakeKernel32(hwnd=hwnd, handle=handle, conout=conout, buffer=buffer,
                           window=window, max_window=max_window,
                           window_result=window_result, buffer_result=buffer_result,
                           update_window=update_window, cell=cell,
                           move_pixels=move_pixels)
    user = _FakeUser32(rect=(10, 20, 10 + cols * cell[0], 20 + rows * cell[1]),
                       set_result=set_result, cell=cell, linked_kernel=kernel,
                       client=(cols * cell[0], rows * cell[1]) if include_client else None)
    kernel.user32 = user
    return user, kernel


def _make_tmp():
    """临时目录 + 指向它的 window_settings.json 路径。"""
    tmp = tempfile.mkdtemp(prefix='window_settings_test_')
    return os.path.join(tmp, 'window_settings.json'), tmp


def _write_json(path, payload):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f)


# --- 常量 ---

def test_step_constants_positive():
    """位置步长与大小步长都为正（方向键交互依赖）。"""
    assert STEP > 0
    assert GRID_STEP > 0


def test_min_grid_constants_positive():
    """行列下限都为正，避免窗口缩到几乎看不见后找不回来。"""
    assert MIN_COLS > 0
    assert MIN_ROWS > 0


# --- 持久化 ---

def test_default_when_no_file():
    path, tmp = _make_tmp()
    try:
        with mock.patch('window_settings.SETTINGS_PATH', path):
            assert window_settings.get_window_settings() == {
                'enabled': False, 'x': 0, 'y': 0, 'cols': 0, 'rows': 0}
    finally:
        shutil.rmtree(tmp)


def test_save_then_load_returns_saved():
    path, tmp = _make_tmp()
    try:
        with mock.patch('window_settings.SETTINGS_PATH', path):
            window_settings.save_window_settings(100, 50, 90, 30)
            assert window_settings.get_window_settings() == {
                'enabled': True, 'x': 100, 'y': 50, 'cols': 90, 'rows': 30}
    finally:
        shutil.rmtree(tmp)


def test_saved_json_uses_grid_fields():
    """落盘字段是 cols/rows（字符行列），不再有旧版的像素 width/height。"""
    path, tmp = _make_tmp()
    try:
        with mock.patch('window_settings.SETTINGS_PATH', path):
            window_settings.save_window_settings(100, 50, 90, 30)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data == {'enabled': True, 'x': 100, 'y': 50, 'cols': 90, 'rows': 30}
    finally:
        shutil.rmtree(tmp)


def test_save_then_load_allows_negative_position():
    """多屏时窗口可以落在主屏左侧/上方（负坐标）。"""
    path, tmp = _make_tmp()
    try:
        with mock.patch('window_settings.SETTINGS_PATH', path):
            window_settings.save_window_settings(-300, -120, 90, 30)
            settings = window_settings.get_window_settings()
        assert (settings['x'], settings['y']) == (-300, -120)
    finally:
        shutil.rmtree(tmp)


def test_corrupt_json_falls_back_to_default():
    path, tmp = _make_tmp()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{ 这不是合法 JSON')
        with mock.patch('window_settings.SETTINGS_PATH', path):
            assert window_settings.get_window_settings()['enabled'] is False
    finally:
        shutil.rmtree(tmp)


def test_non_dict_json_falls_back_to_default():
    path, tmp = _make_tmp()
    try:
        _write_json(path, [1, 2, 3])
        with mock.patch('window_settings.SETTINGS_PATH', path):
            assert window_settings.get_window_settings()['enabled'] is False
    finally:
        shutil.rmtree(tmp)


def test_disabled_flag_returns_default():
    path, tmp = _make_tmp()
    try:
        _write_json(path, {'enabled': False, 'x': 10, 'y': 20,
                           'cols': 90, 'rows': 30})
        with mock.patch('window_settings.SETTINGS_PATH', path):
            assert window_settings.get_window_settings() == {
                'enabled': False, 'x': 0, 'y': 0, 'cols': 0, 'rows': 0}
    finally:
        shutil.rmtree(tmp)


def test_missing_grid_field_falls_back_to_default():
    path, tmp = _make_tmp()
    try:
        _write_json(path, {'enabled': True, 'x': 10, 'y': 20, 'cols': 90})
        with mock.patch('window_settings.SETTINGS_PATH', path):
            assert window_settings.get_window_settings()['enabled'] is False
    finally:
        shutil.rmtree(tmp)


def test_legacy_pixel_fields_fall_back_to_default():
    """旧版本存的是像素 width/height（对控制台大小无效）→ 视为未启用。"""
    path, tmp = _make_tmp()
    try:
        _write_json(path, {'enabled': True, 'x': 10, 'y': 20,
                           'width': 900, 'height': 700})
        with mock.patch('window_settings.SETTINGS_PATH', path):
            assert window_settings.get_window_settings()['enabled'] is False
    finally:
        shutil.rmtree(tmp)


def test_invalid_field_falls_back_to_default():
    path, tmp = _make_tmp()
    try:
        _write_json(path, {'enabled': True, 'x': 'abc', 'y': 20,
                           'cols': 90, 'rows': 30})
        with mock.patch('window_settings.SETTINGS_PATH', path):
            assert window_settings.get_window_settings()['enabled'] is False
    finally:
        shutil.rmtree(tmp)


def test_bool_field_falls_back_to_default():
    """JSON 的 true/false 不是合法坐标（bool 是 int 子类，需显式排除）。"""
    path, tmp = _make_tmp()
    try:
        _write_json(path, {'enabled': True, 'x': True, 'y': 20,
                           'cols': 90, 'rows': 30})
        with mock.patch('window_settings.SETTINGS_PATH', path):
            assert window_settings.get_window_settings()['enabled'] is False
    finally:
        shutil.rmtree(tmp)


def test_float_fields_are_truncated_to_int():
    path, tmp = _make_tmp()
    try:
        _write_json(path, {'enabled': True, 'x': 10.7, 'y': -20.9,
                           'cols': 90.2, 'rows': 30.9})
        with mock.patch('window_settings.SETTINGS_PATH', path):
            settings = window_settings.get_window_settings()
        assert (settings['x'], settings['y']) == (10, -20)
        assert (settings['cols'], settings['rows']) == (90, 30)
    finally:
        shutil.rmtree(tmp)


def test_get_clamps_grid_below_minimum():
    """读文件时也兜住下限（防止手动改 JSON 把窗口设成看不见的大小）。"""
    path, tmp = _make_tmp()
    try:
        _write_json(path, {'enabled': True, 'x': 10, 'y': 20, 'cols': 3, 'rows': 1})
        with mock.patch('window_settings.SETTINGS_PATH', path):
            settings = window_settings.get_window_settings()
        assert (settings['cols'], settings['rows']) == (MIN_COLS, MIN_ROWS)
    finally:
        shutil.rmtree(tmp)


def test_save_clamps_grid_below_minimum():
    path, tmp = _make_tmp()
    try:
        with mock.patch('window_settings.SETTINGS_PATH', path):
            window_settings.save_window_settings(10, 20, 1, 1)
            settings = window_settings.get_window_settings()
        assert (settings['cols'], settings['rows']) == (MIN_COLS, MIN_ROWS)
    finally:
        shutil.rmtree(tmp)


def test_reset_removes_file():
    path, tmp = _make_tmp()
    try:
        with mock.patch('window_settings.SETTINGS_PATH', path):
            assert window_settings.reset_window_settings() is False  # 本来就没有
            window_settings.save_window_settings(100, 50, 90, 30)
            assert window_settings.reset_window_settings() is True
            assert not os.path.exists(path)
            assert window_settings.get_window_settings()['enabled'] is False
    finally:
        shutil.rmtree(tmp)


# --- 控制台窗口位置（像素）---

def test_get_console_rect_reads_size():
    """GetWindowRect 给的是 (left, top, right, bottom)，需转成 (x, y, w, h)。"""
    fake = _FakeUser32(rect=(10, 20, 810, 620))
    with _patch_windll(user32=fake, kernel32=_FakeKernel32(hwnd=0x1234)):
        assert window_settings.get_console_rect() == (10, 20, 800, 600)
    assert fake.rect_calls == [0x1234]


def test_get_console_rect_without_console_is_none():
    """非 Windows / 无控制台（hwnd=0）→ 安全返回 None，不调用 user32。"""
    fake = _FakeUser32()
    with _patch_windll(user32=fake, kernel32=_FakeKernel32(hwnd=0)):
        assert window_settings.get_console_rect() is None
    assert fake.rect_calls == []


def test_get_console_rect_api_failure_is_none():
    fake = _FakeUser32(rect_result=0)
    with _patch_windll(user32=fake, kernel32=_FakeKernel32()):
        assert window_settings.get_console_rect() is None


def test_set_console_pos_only_moves():
    """只移动位置：cx/cy 传 0 且带 SWP_NOSIZE，不改变 Z 序、不激活窗口。"""
    fake = _FakeUser32()
    with _patch_windll(user32=fake, kernel32=_FakeKernel32(hwnd=0x1234)):
        assert window_settings.set_console_pos(100, 50) is True
    assert fake.set_calls == [
        ('SetWindowPos', 0x1234, 0, 100, 50, 0, 0,
         SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)]


def test_set_console_pos_allows_negative_position():
    fake = _FakeUser32()
    with _patch_windll(user32=fake, kernel32=_FakeKernel32()):
        window_settings.set_console_pos(-50, -20)
    assert fake.set_calls[0][3:5] == (-50, -20)


def test_set_console_pos_without_console_is_false():
    fake = _FakeUser32()
    with _patch_windll(user32=fake, kernel32=_FakeKernel32(hwnd=0)):
        assert window_settings.set_console_pos(1, 2) is False
    assert fake.set_calls == []


def test_set_console_pos_api_failure_is_false():
    fake = _FakeUser32(set_result=0)
    with _patch_windll(user32=fake, kernel32=_FakeKernel32()):
        assert window_settings.set_console_pos(1, 2) is False


# --- 控制台窗口大小（字符行列）---

def test_get_console_grid_parses_window_and_max():
    """srWindow 是闭区间 (left, top, right, bottom)，行列数要 +1。"""
    kernel = _FakeKernel32(handle=0x9999, window=(0, 0, 79, 24), max_window=(240, 80))
    with _patch_windll(kernel32=kernel):
        assert window_settings.get_console_grid() == (80, 25, 240, 80)


def test_get_console_grid_handles_offset_window():
    """窗口上沿滚动过（top>0）时，行列数按 Right-Left / Bottom-Top 计算。"""
    kernel = _FakeKernel32(window=(0, 5, 79, 29), max_window=(240, 80))
    with _patch_windll(kernel32=kernel):
        assert window_settings.get_console_grid()[:2] == (80, 25)


def test_get_console_grid_without_console_is_none():
    """GetStdHandle 返回 0 且 CONOUT$ 也打不开（真无控制台）→ 安全返回 None。"""
    kernel = _FakeKernel32(handle=0, conout=0)
    with _patch_windll(kernel32=kernel):
        assert window_settings.get_console_grid() is None


def test_get_console_grid_invalid_handle_is_none():
    """GetStdHandle 失败返回 INVALID_HANDLE_VALUE(-1) → 安全返回 None。"""
    kernel = _FakeKernel32(handle=-1, conout=0)
    with _patch_windll(kernel32=kernel):
        assert window_settings.get_console_grid() is None


def test_get_console_grid_api_failure_is_none():
    kernel = _FakeKernel32(info_result=0)
    with _patch_windll(kernel32=kernel):
        assert window_settings.get_console_grid() is None


def test_get_console_grid_rejects_empty_geometry():
    """窗口行列非法（0）→ None，避免把 0 当成有效设置保存下来。"""
    kernel = _FakeKernel32(window=(0, 0, -1, -1))
    with _patch_windll(kernel32=kernel):
        assert window_settings.get_console_grid() is None


def test_set_console_size_sets_window_rect():
    """设成 cols 列 × rows 行：SMALL_RECT 右下角是 (cols-1, rows-1)。"""
    user, kernel = _console_pair(buffer=(120, 9001), window=(0, 0, 79, 24),
                                 max_window=(240, 80))
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(100, 40) is True
    assert ('window', True, 0, 0, 99, 39) in kernel.order
    assert user.set_calls == []      # 边框像素真的变了 → 不走 SetWindowPos 回退


def test_set_console_size_enlarges_buffer_before_window():
    """缓冲区装不下新窗口时，必须先扩缓冲区再设窗口（否则 API 会失败）。"""
    user, kernel = _console_pair(buffer=(80, 25), window=(0, 0, 79, 24),
                                 max_window=(240, 80))
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(120, 50) is True
    assert kernel.order == [
        ('buffer', 120, 50),
        ('window', True, 0, 0, 119, 49),
        ('buffer', 120, 50),
    ]


def test_set_console_size_shrinks_window_before_buffer():
    """缩小窗口：先设小窗口，再把缓冲区宽度对齐到新列数（消掉水平滚动条）。"""
    user, kernel = _console_pair(buffer=(120, 9001), window=(0, 0, 119, 49),
                                 max_window=(240, 80))
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(60, 20) is True
    assert kernel.order == [
        ('window', True, 0, 0, 59, 19),
        ('buffer', 60, 9001),
    ]


def test_set_console_size_clamps_below_minimum():
    """过小 → 抬到 MIN_COLS × MIN_ROWS。"""
    user, kernel = _console_pair(window=(0, 0, 79, 24), max_window=(240, 80))
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(1, 1) is True
    assert ('window', True, 0, 0, MIN_COLS - 1, MIN_ROWS - 1) in kernel.order


def test_set_console_size_ignores_reported_max_when_growing():
    """回归：dwMaximumWindowSize 会随窗口/缓冲区缩小而退化（= min(屏幕, 缓冲区)），
    拿它当上限会导致「缩过就再也放不回去」——目标行列必须原样下发。"""
    user, kernel = _console_pair(buffer=(117, 9001), window=(0, 0, 116, 27),
                                 max_window=(117, 47))
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(118, 29) is True
    assert kernel.order == [
        ('buffer', 118, 9001),             # 先把缓冲区扩到装得下 118 列
        ('window', True, 0, 0, 117, 28),   # 再把窗口设成 118 列 × 29 行
        ('buffer', 118, 9001),             # 缓冲区宽度对齐到新列数
    ]


def test_set_console_size_false_when_buffer_cannot_grow():
    """缓冲区扩不到目标宽度（超出屏幕可容纳列数）→ False，且不动窗口。"""
    kernel = _FakeKernel32(buffer=(120, 9001), window=(0, 0, 119, 29),
                           max_window=(120, 47), buffer_result=0)
    with _patch_windll(kernel32=kernel):
        assert window_settings.set_console_size(200, 30) is False
    assert kernel.order == [('buffer', 200, 9001)]


def test_set_console_size_without_console_is_false():
    kernel = _FakeKernel32(handle=0, conout=0)
    with _patch_windll(kernel32=kernel):
        assert window_settings.set_console_size(100, 40) is False
    assert kernel.order == []


def test_set_console_size_window_api_failure_is_false():
    """SetConsoleWindowInfo 失败（例如被系统拒绝）→ 返回 False。"""
    kernel = _FakeKernel32(window_result=0)
    with _patch_windll(kernel32=kernel):
        assert window_settings.set_console_size(100, 40) is False


def test_get_console_grid_falls_back_to_conout():
    """stdout 被重定向（GetStdHandle 拿到管道句柄）→ 改用 CONOUT$ 读控制台。"""
    kernel = _FakeKernel32(handle=0, conout=0x7777,
                           window=(0, 0, 79, 24), max_window=(240, 80))
    with _patch_windll(kernel32=kernel):
        assert window_settings.get_console_grid() == (80, 25, 240, 80)
    assert kernel.conout_calls == ['CONOUT$']


def test_set_console_size_falls_back_to_conout():
    """同上：重定向时用 CONOUT$ 句柄设置窗口行列。"""
    user, kernel = _console_pair(handle=0, conout=0x7777,
                                 window=(0, 0, 79, 24), max_window=(240, 80))
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(100, 40) is True
    assert kernel.conout_calls == ['CONOUT$']   # 只开一次：基准行列与调 API 共用
    assert ('window', True, 0, 0, 99, 39) in kernel.order


# --- 窗口大小回退（Windows Terminal / 新版 conhost 忽略 SetConsoleWindowInfo）---

def test_cell_px_uses_client_and_grid():
    """单元格像素 = 客户区像素 ÷ 字符行列。"""
    user = _FakeUser32(client=(800, 600))
    with _patch_windll(user32=user, kernel32=_FakeKernel32()):
        assert window_settings._cell_px(0x1234, (80, 25)) == (10.0, 24.0)
    assert user.client_calls == [0x1234]


def test_cell_px_without_client_is_none():
    """GetClientRect 不可用 → 无法测量 → None（回退路径据此安全放弃）。"""
    with _patch_windll(user32=_FakeUser32(), kernel32=_FakeKernel32()):
        assert window_settings._cell_px(0x1234, (80, 25)) is None


def test_cell_px_without_grid_is_none():
    """读不到行列（或无窗口句柄）→ None，不猜尺寸。"""
    with _patch_windll(user32=_FakeUser32(client=(800, 600)),
                       kernel32=_FakeKernel32()):
        assert window_settings._cell_px(0x1234, None) is None
        assert window_settings._cell_px(0, (80, 25)) is None


def test_set_console_size_prefers_api_without_setwindowpos():
    """老式 conhost：API 生效且窗口像素尺寸真的变了 → 不走 SetWindowPos 回退。"""
    user, kernel = _console_pair(buffer=(120, 9001), window=(0, 0, 79, 24))
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(100, 40) is True
    assert user.set_calls == []


def test_set_console_size_falls_back_when_api_reports_failure():
    """SetConsoleWindowInfo 报失败 → 退回 SetWindowPos 按像素改窗口。"""
    user, kernel = _console_pair(window_result=0)
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(81, 25) is True
    assert user.set_calls == [
        ('SetWindowPos', 0x1234, 0, 0, 0, 810, 600,
         SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)]
    assert kernel.window == (0, 0, 80, 24)   # 81 列 × 25 行


def test_set_console_size_falls_back_when_api_silently_ignored():
    """API 报成功但行列也不动 → 回退 SetWindowPos（像素判据：边框确实变了）。"""
    user, kernel = _console_pair(window_result=1, update_window=False)
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(80, 30) is True
    assert user.set_calls[0][5:7] == (800, 720)   # 行数 +5 → 高度 +5×24
    assert kernel.window == (0, 0, 79, 29)        # 80 列 × 30 行


def test_set_console_size_falls_back_when_grid_changes_but_frame_does_not():
    """Windows Terminal 场景（用户报的现象）：API 改了行列和缓冲区、只剩滚动条/
    白条，但窗口边框像素尺寸纹丝不动 → 像素校验发现没变，回退 SetWindowPos。"""
    user, kernel = _console_pair(window_result=1, move_pixels=False)
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(80, 30) is True
    assert user.set_calls[0][5:7] == (800, 720)   # 按 API 调用前的基准尺寸换算
    assert kernel.window == (0, 0, 79, 29)        # 80 列 × 30 行


def test_set_console_size_false_when_window_px_does_not_change_grid():
    """回退调了 SetWindowPos 但字符行列纹丝不动（已达屏幕极限）→ False。"""
    kernel = _FakeKernel32(buffer=(120, 9001), window=(0, 0, 79, 24),
                           window_result=0)
    user = _FakeUser32(client=(800, 600), set_result=1)  # 不联动 → 行列不变
    with _patch_windll(user32=user, kernel32=kernel):
        assert window_settings.set_console_size(81, 25) is False
    assert user.set_calls[0][5:7] == (810, 600)


def test_set_console_size_false_when_both_paths_fail():
    """API 失败且无法测量单元格（没有 GetClientRect）→ False。"""
    kernel = _FakeKernel32(window_result=0)
    with _patch_windll(user32=_FakeUser32(), kernel32=kernel):
        assert window_settings.set_console_size(100, 40) is False


# --- 启动时自动套用 ---

def test_apply_window_settings_disabled_is_noop():
    """从没按过 [8] 保存（或已按 R 恢复默认）→ 完全不碰窗口。"""
    path, tmp = _make_tmp()
    fake = _FakeUser32()
    kernel = _FakeKernel32()
    try:
        with mock.patch('window_settings.SETTINGS_PATH', path):
            with _patch_windll(user32=fake, kernel32=kernel):
                assert window_settings.apply_window_settings() is False
        assert fake.set_calls == []
        assert kernel.order == []
    finally:
        shutil.rmtree(tmp)


def test_apply_window_settings_applies_position_and_size():
    path, tmp = _make_tmp()
    fake, kernel = _console_pair(hwnd=0xABCD, window=(0, 0, 79, 24),
                                 max_window=(240, 80))
    try:
        with mock.patch('window_settings.SETTINGS_PATH', path):
            window_settings.save_window_settings(100, 50, 90, 30)
            with _patch_windll(user32=fake, kernel32=kernel):
                assert window_settings.apply_window_settings() is True
        assert fake.set_calls == [
            ('SetWindowPos', 0xABCD, 0, 100, 50, 0, 0,
             SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)]
        assert ('window', True, 0, 0, 89, 29) in kernel.order
    finally:
        shutil.rmtree(tmp)


def test_apply_window_settings_no_console_is_false():
    """已保存但当前没有控制台（hwnd=0 / 句柄无效）→ False，不影响启动流程。"""
    path, tmp = _make_tmp()
    fake = _FakeUser32()
    kernel = _FakeKernel32(hwnd=0, handle=0, conout=0)
    try:
        with mock.patch('window_settings.SETTINGS_PATH', path):
            window_settings.save_window_settings(100, 50, 90, 30)
            with _patch_windll(user32=fake, kernel32=kernel):
                assert window_settings.apply_window_settings() is False
        assert fake.set_calls == []
        assert kernel.order == []
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
