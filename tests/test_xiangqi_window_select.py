#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""find_window 选窗逻辑单元测试（[1] 键整盘识别错乱的根因回归）。

背景：助手自身的控制台窗口标题是「启动象棋外挂助手」，含「象棋」二字。
旧 find_window 的兜底条件 ``'象棋' in w.title`` 会把它当成游戏窗口；
[1] 键手动识别时控制台正在前台（Z 序靠前），于是截到控制台画面 →
校准/识别全部错乱。而 [6] 键自动落子前会先激活游戏窗口，Z 序里游戏窗口靠前，
同一套代码又「正常」，问题因此被长期掩盖。

修复：新增纯函数 rank_xiangqi_windows —— 类名 QQChess > 标题含「天天象棋」
> 标题含「象棋」，且标题含「外挂」/「助手」的助手自身窗口一律排除。

不启动真实窗口 / 鼠标 / 引擎：只测纯函数 is_assistant_window / rank_xiangqi_windows。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, 'xiangqi-bot')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 兼容中文/emoji 输出（直接跑测试时 stdout 可能是 GBK）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

from xiangqi_bot import (GAME_WINDOW_CLASS, is_assistant_window,   # noqa: E402
                         rank_xiangqi_windows)

# 真实环境里的两个标题（见 [1] 键故障日志）
CONSOLE_TITLE = '启动象棋外挂助手'      # 助手自身控制台
GAME_TITLE = '天天象棋'                 # 游戏窗口
CONSOLE_CLASS = 'ConsoleWindowClass'
GAME_CLASS = 'Chrome_WidgetWin_1'       # 非 QQChess，故只能靠标题命中


# --- is_assistant_window ---

def test_console_title_is_assistant():
    """「启动象棋外挂助手」必须被识别为助手自身窗口。"""
    assert is_assistant_window(CONSOLE_TITLE) is True


def test_game_title_is_not_assistant():
    assert is_assistant_window(GAME_TITLE) is False


def test_assistant_marker_variants():
    """含「外挂」或「助手」的标题一律排除；普通象棋窗口不受影响。"""
    for title in ('象棋外挂', '象棋助手', '启动天天象棋助手', 'XX外挂助手'):
        assert is_assistant_window(title) is True, title
    for title in ('天天象棋', '象棋小游戏', '中国象棋', 'QQ游戏-中国象棋'):
        assert is_assistant_window(title) is False, title


def test_empty_title_is_not_assistant():
    assert is_assistant_window('') is False
    assert is_assistant_window(None) is False


# --- rank_xiangqi_windows：本次故障的回归用例 ---

def test_console_first_still_picks_game():
    """回归：控制台 Z 序在前（[1] 键场景），仍必须选中游戏窗口。"""
    entries = [
        (CONSOLE_TITLE, CONSOLE_CLASS),
        (GAME_TITLE, GAME_CLASS),
    ]
    index, label = rank_xiangqi_windows(entries)
    assert index == 1, '应跳过助手控制台，选中「天天象棋」'
    assert label == GAME_TITLE


def test_realistic_z_order():
    """真实 Z 序：控制台在前台，游戏窗口排在若干无关窗口之后。"""
    entries = [
        (CONSOLE_TITLE, CONSOLE_CLASS),
        ('Windows PowerShell', CONSOLE_CLASS),
        ('设置', 'ApplicationFrameWindow'),
        (GAME_TITLE, GAME_CLASS),
        ('Program Manager', 'Progman'),
    ]
    index, label = rank_xiangqi_windows(entries)
    assert index == 3
    assert label == GAME_TITLE


def test_game_title_beats_generic_xiangqi():
    """「天天象棋」优先于其它含「象棋」的窗口，即使后者 Z 序更靠前。"""
    entries = [
        ('象棋小游戏', 'Chrome_WidgetWin_1'),
        (GAME_TITLE, GAME_CLASS),
    ]
    index, label = rank_xiangqi_windows(entries)
    assert index == 1
    assert label == GAME_TITLE


def test_qqchess_class_wins():
    """类名 QQChess 是最高优先级判据（标题为空也算）。"""
    entries = [
        (GAME_TITLE, GAME_CLASS),
        ('', GAME_WINDOW_CLASS),
    ]
    index, label = rank_xiangqi_windows(entries)
    assert index == 1
    assert label == '天天象棋(%s)' % GAME_WINDOW_CLASS


def test_generic_xiangqi_fallback():
    """没有「天天象棋」窗口时，退回到任意含「象棋」的窗口。"""
    entries = [
        ('记事本', 'Notepad'),
        ('象棋小游戏', 'Chrome_WidgetWin_1'),
    ]
    index, label = rank_xiangqi_windows(entries)
    assert index == 1
    assert label == '象棋小游戏'


def test_assistant_excluded_even_in_fallback():
    """只有助手控制台匹配「象棋」时 → 判定未找到，绝不返回控制台。"""
    entries = [
        ('Windows PowerShell', CONSOLE_CLASS),
        (CONSOLE_TITLE, CONSOLE_CLASS),
    ]
    assert rank_xiangqi_windows(entries) == (None, None)


def test_no_match_returns_none():
    entries = [
        ('记事本', 'Notepad'),
        ('', 'Shell_TrayWnd'),
        ('', ''),
    ]
    assert rank_xiangqi_windows(entries) == (None, None)


def test_empty_entries_returns_none():
    assert rank_xiangqi_windows([]) == (None, None)


def test_none_fields_are_tolerated():
    """pygetwindow 偶尔给不出标题：None 不能把匹配逻辑炸掉。"""
    entries = [
        (None, None),
        (GAME_TITLE, None),
    ]
    index, label = rank_xiangqi_windows(entries)
    assert index == 1
    assert label == GAME_TITLE


# --- 直接运行（无需 pytest）---

def _main():
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith('test_') and callable(obj)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print('FAIL %s: %r' % (name, exc))
        else:
            print('ok   %s' % name)
    print('\n%d passed, %d failed (共 %d)' % (len(tests) - failed, failed, len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(_main())
