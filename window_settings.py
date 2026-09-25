#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主菜单 [8]「窗口设置」的运行期设置存储 + 控制台窗口位置/大小控制。

用户在主菜单按 [8] 进入设置，用方向键移动黑色控制台窗口、Shift+方向键调整
窗口大小，回车后保存到本目录下的 window_settings.json；下次启动程序时自动
读取并应用，让窗口每次都按设定好的位置和大小弹出。

坐标系与单位：
  * 位置：物理像素 + 屏幕坐标——GetWindowRect 读、SetWindowPos 写，同一套
    坐标系自洽；刻意不声明 DPI aware，避免改变棋盘识别依赖的 mss 物理像素
    + win_x/win_y 逻辑坐标体系。
  * 大小：字符行列（列数 × 行数）——控制台窗口的可见大小本质上由字符网格
    决定（像素 = 行列 × 字体单元格）。默认走传统控制台 API
    （SetConsoleScreenBufferSize + SetConsoleWindowInfo）按行列调整，老式
    conhost 生效且精度最好；但 Windows Terminal / 新版 conhost 会忽略
    SetConsoleWindowInfo（窗口大小由用户完全掌控），此时退回用 SetWindowPos
    对窗口句柄按像素改大小——用实测的字符单元格像素尺寸把行列增量换算成像素
    增量，两种终端都生效（见 set_console_size / _set_window_size_px）。
"""

import ctypes
from ctypes import wintypes
import json
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(_SCRIPT_DIR, 'window_settings.json')

# --- Win32 常量（命名与 xiangqi_bot.py / auto_player.py 保持一致）---
SWP_NOSIZE = 0x0001       # SetWindowPos：不改大小（只移动位置）
SWP_NOMOVE = 0x0002       # SetWindowPos：不改位置（只改大小）
SWP_NOZORDER = 0x0004     # SetWindowPos：不改变 Z 序
SWP_NOACTIVATE = 0x0010   # SetWindowPos：不激活窗口（不抢键盘焦点）

STD_OUTPUT_HANDLE = -11   # GetStdHandle：标准输出（控制台屏幕缓冲区）

# CreateFileW 参数（stdout 被重定向时用 CONOUT$ 直接打开当前控制台缓冲区）
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
CONOUT = 'CONOUT$'        # 当前控制台的活动屏幕缓冲区

# 窗口最小尺寸（字符行列）：控制台窗口大小本质是「列数 × 行数」，系统本身
# 也会 clamp 到当前字体/屏幕允许的最大行列，这里再加一层下限保护，
# 避免调到几乎不可见后找不回来。
MIN_COLS = 40
MIN_ROWS = 12

# 调整步长：STEP 是位置移动的像素步长（方向键）；
# GRID_STEP 是大小调整的字符行列步长（Shift+方向键，每次 1 列/行）
STEP = 5
GRID_STEP = 1


# ============================================================
#  设置持久化
# ============================================================

def _as_int(value):
    """把 JSON 数值安全转成 int；bool / NaN / 非数值返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    return int(value)


def _default():
    """未启用（不强制窗口位置/大小）时的返回结构。"""
    return {'enabled': False, 'x': 0, 'y': 0, 'cols': 0, 'rows': 0}


def _read_raw():
    """读取 window_settings.json；无文件/损坏/非 dict 时返回 {}。"""
    if not os.path.exists(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get_window_settings():
    """返回当前生效的窗口设置。

    文件缺失 / 损坏 / 未启用 / 字段非法时，返回 enabled=False（不强制窗口）。
    cols/rows 钳到 MIN_COLS/MIN_ROWS 以上；x/y 允许为负（多屏左侧）。

    旧版本存的是像素 width/height（对控制台大小无效），读到这类文件时按
    enabled=False 回退，让用户重新按 [8] 调一次。
    """
    data = _read_raw()
    if not data.get('enabled'):
        return _default()
    x = _as_int(data.get('x'))
    y = _as_int(data.get('y'))
    cols = _as_int(data.get('cols'))
    rows = _as_int(data.get('rows'))
    if x is None or y is None or cols is None or rows is None:
        return _default()
    return {
        'enabled': True,
        'x': x,
        'y': y,
        'cols': max(MIN_COLS, cols),
        'rows': max(MIN_ROWS, rows),
    }


def save_window_settings(x, y, cols, rows):
    """保存窗口位置（像素）和大小（字符行列）到 window_settings.json。"""
    data = {
        'enabled': True,
        'x': int(x),
        'y': int(y),
        'cols': max(MIN_COLS, int(cols)),
        'rows': max(MIN_ROWS, int(rows)),
    }
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def reset_window_settings():
    """删除保存文件，下次启动不再强制窗口位置/大小。

    Returns:
        bool: True=已删除；False=本来就没有（已是默认）。
    """
    try:
        os.remove(SETTINGS_PATH)
        return True
    except OSError:
        return False


# ============================================================
#  控制台窗口控制（Win32）
# ============================================================

# --- Win32 结构体（控制台行列相关；字段布局见 MSDN CONSOLE_SCREEN_BUFFER_INFO）---

class _COORD(ctypes.Structure):
    """Win32 COORD：字符网格坐标（X=列, Y=行）。"""

    _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]


class _SMALL_RECT(ctypes.Structure):
    """Win32 SMALL_RECT：字符网格矩形（Right/Bottom 为闭区间端点）。"""

    _fields_ = [('Left', ctypes.c_short), ('Top', ctypes.c_short),
                ('Right', ctypes.c_short), ('Bottom', ctypes.c_short)]


class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    """Win32 CONSOLE_SCREEN_BUFFER_INFO：缓冲区行列 / 窗口矩形 / 最大窗口行列。"""

    _fields_ = [('dwSize', _COORD),
                ('dwCursorPosition', _COORD),
                ('wAttributes', wintypes.WORD),
                ('srWindow', _SMALL_RECT),
                ('dwMaximumWindowSize', _COORD)]


def _console_hwnd():
    """返回当前控制台窗口句柄（HANDLE 整数）；非 Windows / 无控制台时返回 0。"""
    try:
        return ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:
        return 0


def get_console_rect():
    """读取当前控制台窗口的屏幕矩形。

    Returns:
        tuple: (x, y, width, height)（物理像素屏幕坐标）；无法获取时返回 None。
    """
    hwnd = _console_hwnd()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    try:
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    except Exception:
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _get_client_size(hwnd):
    """读取控制台窗口客户区的像素宽高（不含标题栏/边框）；失败返回 None。

    与字符行列一起用来实测「一个字符单元格占多少像素」——Windows Terminal /
    新版 conhost 忽略 SetConsoleWindowInfo，只能对窗口句柄按像素改大小，需要
    这个换算率（见 _measure_cell_px / _set_window_size_px）。
    """
    if not hwnd:
        return None
    rect = wintypes.RECT()
    try:
        if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
    except Exception:
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return (width, height)


def _open_conout(kernel32):
    """用 CONOUT$ 打开当前控制台屏幕缓冲区；打不开返回 0。"""
    try:
        create_file = kernel32.CreateFileW
    except Exception:
        return 0
    try:
        # 真实 windll 上声明签名：64 位下句柄必须按指针宽度返回，否则会被截断
        create_file.restype = wintypes.HANDLE
        create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p]
    except Exception:
        pass
    try:
        handle = create_file(CONOUT, GENERIC_READ | GENERIC_WRITE,
                             FILE_SHARE_READ | FILE_SHARE_WRITE,
                             None, OPEN_EXISTING, 0, None)
    except Exception:
        return 0
    if not handle or handle == ctypes.c_void_p(-1).value:  # 0 / INVALID_HANDLE_VALUE
        return 0
    return handle


def _console_out_handle():
    """返回控制台屏幕缓冲区的句柄；非 Windows / 无控制台时返回 0。

    优先用 GetStdHandle(STD_OUTPUT_HANDLE)；但 stdout 被重定向（管道 / 写日志）时
    它拿到的是管道句柄、控制台 API 会失败，此时退回用 CONOUT$ 打开当前控制台，
    保证重定向日志时窗口大小照样能调。
    """
    try:
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return 0
    handle = 0
    try:
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    except Exception:
        handle = 0
    if handle in (0, -1, None):  # 0 / INVALID_HANDLE_VALUE(-1) 都不是控制台句柄
        return _open_conout(kernel32)
    info = _CONSOLE_SCREEN_BUFFER_INFO()
    try:
        if kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return handle
    except Exception:
        pass
    return _open_conout(kernel32)


def set_console_pos(x, y):
    """把控制台窗口移动到屏幕坐标 (x, y)（只改位置，大小交给 set_console_size）。

    不改变 Z 序、不激活窗口（不抢键盘焦点，用户仍可在窗口里按键）。

    Returns:
        bool: True=调用成功；False=无控制台窗口 / 调用失败。
    """
    hwnd = _console_hwnd()
    if not hwnd:
        return False
    try:
        ok = ctypes.windll.user32.SetWindowPos(
            hwnd, 0, int(x), int(y), 0, 0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    except Exception:
        return False
    return bool(ok)


def get_console_grid():
    """读取控制台窗口的行列信息（窗口大小只有字符行列这一个可控单位）。

    Returns:
        tuple: (cols, rows, max_cols, max_rows)——
               cols/rows 为当前可见窗口的字符列数/行数；
               max_cols/max_rows 为系统当前报告的「最大窗口行列」，仅供展示：
               它等于 min(屏幕可容纳行列, 屏幕缓冲区行列)，会随窗口/缓冲区
               缩小而退化，所以不能当放大时的上限（否则缩过就再也放不回去）；
               无法获取时返回 None。
    """
    handle = _console_out_handle()
    if not handle:
        return None
    info = _CONSOLE_SCREEN_BUFFER_INFO()
    try:
        ok = ctypes.windll.kernel32.GetConsoleScreenBufferInfo(
            handle, ctypes.byref(info))
    except Exception:
        return None
    if not ok:
        return None
    window = info.srWindow
    cols = window.Right - window.Left + 1
    rows = window.Bottom - window.Top + 1
    max_cols = info.dwMaximumWindowSize.X
    max_rows = info.dwMaximumWindowSize.Y
    if cols <= 0 or rows <= 0 or max_cols <= 0 or max_rows <= 0:
        return None
    return (cols, rows, max_cols, max_rows)


def _cell_px(hwnd, grid):
    """按已知的可见窗口行列 grid 计算一个字符单元格的像素宽高。

    窗口宽 = 客户区宽 + 固定边框 chrome，chrome 不随内容大小变化，所以客户端
    像素增量 ÷ 行列增量 = 单个字符的像素尺寸。用它把「行列增量」换算成「窗口
    像素增量」（见 _set_window_size_px）。

    Args:
        hwnd: 窗口句柄（读客户区像素用）。
        grid: 已知 (cols, rows[, ...])；None 表示读不到行列。

    Returns:
        tuple: (cell_w, cell_h)（float 像素）；无法测量时返回 None。
    """
    client = _get_client_size(hwnd)
    if client is None or not grid:
        return None
    cols, rows = grid[0], grid[1]
    if cols <= 0 or rows <= 0:
        return None
    return (client[0] / float(cols), client[1] / float(rows))


def _window_grid(handle):
    """从已打开的屏幕缓冲区句柄读取可见窗口行列 (cols, rows)；失败返回 None。

    供 _set_console_size_api 做「读回校验」用：复用同一个句柄，避免每次调整窗口
    都重新 CreateFileW(CONOUT$)（stdout 被重定向时会多开句柄）。
    """
    info = _CONSOLE_SCREEN_BUFFER_INFO()
    try:
        if not ctypes.windll.kernel32.GetConsoleScreenBufferInfo(
                handle, ctypes.byref(info)):
            return None
    except Exception:
        return None
    cols = info.srWindow.Right - info.srWindow.Left + 1
    rows = info.srWindow.Bottom - info.srWindow.Top + 1
    if cols <= 0 or rows <= 0:
        return None
    return (cols, rows)


def _set_console_size_api(handle, cols, rows):
    """传统控制台 API 路径：老式 conhost 生效，现代终端可能被忽略。

    控制台窗口大小只能是字符网格的整数倍，所以按行列调整而不是像素：
    先把屏幕缓冲区扩到装得下新窗口（缓冲区不允许小于窗口），再设置窗口矩形，
    最后把缓冲区宽度对齐到新列数（消掉水平滚动条）；缓冲区高度保持以留滚动历史。

    Windows Terminal / 新版 conhost 会忽略 SetConsoleWindowInfo（窗口大小由用户
    完全掌控）：此时缓冲区照变、API 也可能报成功，但可见窗口行列纹丝不动。所以
    这里调用完再读回行列，只有确实变化才算成功，否则交给 _set_window_size_px 兜底。

    Args:
        handle: 控制台输出句柄（0/None 表示没有控制台）。
        cols, rows: 目标字符列数/行数（调用方已兜过下限）。

    Returns:
        bool: True=可见窗口行列确实发生了变化；False=无控制台 / API 失败 / 被忽略。
    """
    if not handle:
        return False
    info = _CONSOLE_SCREEN_BUFFER_INFO()
    try:
        if not ctypes.windll.kernel32.GetConsoleScreenBufferInfo(
                handle, ctypes.byref(info)):
            return False
    except Exception:
        return False

    before_cols = info.srWindow.Right - info.srWindow.Left + 1
    before_rows = info.srWindow.Bottom - info.srWindow.Top + 1
    target = _SMALL_RECT(0, 0, cols - 1, rows - 1)
    try:
        kernel32 = ctypes.windll.kernel32
        # 1) 缓冲区必须先装得下新窗口（缓冲区不允许小于窗口）
        if info.dwSize.X < cols or info.dwSize.Y < rows:
            if not kernel32.SetConsoleScreenBufferSize(
                    handle, _COORD(max(cols, info.dwSize.X),
                                   max(rows, info.dwSize.Y))):
                return False
        # 2) 设置可见窗口行列（左上角固定在字符 (0, 0)）
        if not kernel32.SetConsoleWindowInfo(handle, True, ctypes.byref(target)):
            return False
        # 3) 缓冲区宽度对齐到新列数，消掉水平滚动条；高度保持（保留滚动历史）
        kernel32.SetConsoleScreenBufferSize(
            handle, _COORD(cols, max(rows, info.dwSize.Y)))
    except Exception:
        return False
    # 读回校验：被忽略（行列无变化）时由上层回退到 SetWindowPos
    grid = _window_grid(handle)
    if grid is None:
        return True   # 读不回就按 API 返回值成功处理，保持旧行为
    return grid[0] != before_cols or grid[1] != before_rows


def _window_resize_target(cols, rows, frame, cell, base_grid):
    """把「目标字符行列」换算成「目标窗口像素尺寸 (w, h)」。

    窗口宽 = 客户区宽 + 固定边框 chrome，chrome 不随内容大小变化，所以

        目标窗口像素 = 基准窗口像素 + (目标行列 - 基准行列) × 单元格像素

    基准 frame / cell / base_grid 必须是同一时刻测的（那时三者互相自洽）；这里
    刻意不读当前行列——现代终端会让控制台 API 改掉内部行列却不动窗口边框，所以
    只有像素尺寸才是可信的。

    Returns:
        tuple: (target_w, target_h)；无法换算时返回 None。
    """
    if not frame or not cell or not base_grid:
        return None
    base_cols, base_rows = base_grid[0], base_grid[1]
    width = int(round(frame[2] + (int(cols) - base_cols) * cell[0]))
    height = int(round(frame[3] + (int(rows) - base_rows) * cell[1]))
    if width <= 0 or height <= 0:
        return None
    return (width, height)


def _set_window_size_px(hwnd, width, height, attempts=3):
    """回退路径：直接按像素把窗口设成 width × height（Windows Terminal 也生效）。

    Windows Terminal / 新版 conhost 忽略 SetConsoleWindowInfo，但窗口本身仍是普通
    Win32 窗口，SetWindowPos 能改大小（ConPTY 下由 conhost 把窗口操作转给真正的
    终端窗口）。

    判据用**窗口像素尺寸**而不是行列：这类终端下 API 会「报成功、行列也读得回来、
    只是边框不动」，行列读回不可信，像素尺寸才是用户真正看得见的东西（见
    set_console_size 里的像素校验）。

    终端会把窗口像素吸附到字符网格、有的还会把标签栏算进客户区，单次换算可能差
    1 行/列，所以最多重试 attempts 次，始终朝同一个目标尺寸逼近；一旦发现调用后
    像素尺寸纹丝不动（已达屏幕可容纳极限）就立即收手。

    Args:
        hwnd: 控制台窗口句柄。
        width, height: 目标窗口像素尺寸。
        attempts: 最多尝试几次（默认 3）。

    Returns:
        bool: True=窗口像素尺寸确实变了；False=无窗口 / 调用失败 / 本来就是这个尺寸。
    """
    if not hwnd:
        return False
    changed = False
    for _ in range(max(1, int(attempts))):
        cur = get_console_rect()
        if cur is None:
            return changed
        if (cur[2], cur[3]) == (width, height):
            return changed              # 已经是目标尺寸（无需调整，或上一轮刚改到）
        try:
            ok = ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, width, height,
                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
        except Exception:
            return changed
        if not ok:
            return changed
        after = get_console_rect()
        if after is None:
            return changed
        if (after[2], after[3]) == (cur[2], cur[3]):
            return changed              # 终端没接受（已达屏幕可容纳极限）
        changed = True                  # 边框确实动了
        if (after[2], after[3]) == (width, height):
            return True                 # 精确落到目标像素尺寸
    return changed


def set_console_size(cols, rows):
    """把控制台窗口（可见区域）调整为 cols 列 × rows 行。

    两条路径依次尝试，谁先真正改到窗口就用谁：

      1) 传统控制台 API（SetConsoleScreenBufferSize + SetConsoleWindowInfo）——
         老式 conhost 生效且精度最好（见 _set_console_size_api）。调完必须核对
         **窗口像素尺寸也确实变了**：Windows Terminal / 新版 conhost 下这个 API
         会「报成功但窗口边框不动」（只改内部视口与缓冲区，于是只剩滚动条/白条），
         这种情况继续走第 2 步。
      2) SetWindowPos 按像素改窗口句柄大小（见 _set_window_size_px）。

    目标行列只兜下限（MIN_COLS/MIN_ROWS）；上限不在这里压——Windows 报告的
    dwMaximumWindowSize 会随窗口/缓冲区缩小而退化，拿它当上限会导致缩小之后
    再也放不回去。超限时两条路径都会失败并返回 False（菜单会提示已到极限、
    保持当前尺寸）。

    Returns:
        bool: True=可见窗口已按目标调整（终端可能按字符网格吸附到附近值）；
              False=无控制台 / 超出屏幕可容纳行列 / 调用失败。
    """
    cols = max(MIN_COLS, int(cols))
    rows = max(MIN_ROWS, int(rows))
    handle = _console_out_handle()
    if not handle:
        return False
    hwnd = _console_hwnd()
    # 基准：此刻「可见行列」「窗口像素矩形」「客户区像素」三者互相自洽
    frame = get_console_rect()
    base_grid = _window_grid(handle)
    cell = _cell_px(hwnd, base_grid)
    if _set_console_size_api(handle, cols, rows):
        after = get_console_rect()
        if frame is None or after is None:
            return True                  # 拿不到窗口句柄：只能以行列读回为准
        if (after[2], after[3]) != (frame[2], frame[3]):
            return True                  # 窗口像素尺寸确实变了（边框真的动了）
        # API 报成功但窗口边框没动（被现代终端忽略）→ 用 SetWindowPos 再试
    target = _window_resize_target(cols, rows, frame, cell, base_grid)
    if target is None:
        return False
    return _set_window_size_px(hwnd, target[0], target[1])


def apply_window_settings():
    """启动时应用已保存的窗口设置（主菜单 [8] 保存的位置/大小）。

    Returns:
        bool: True=已应用（位置或大小至少一项生效）；False=未设置 / 无法应用。
    """
    settings = get_window_settings()
    if not settings.get('enabled'):
        return False
    moved = set_console_pos(settings['x'], settings['y'])
    resized = set_console_size(settings['cols'], settings['rows'])
    return moved or resized
