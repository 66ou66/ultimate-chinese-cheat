#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动识别模式（主菜单 [4]）的运行期设置存储。

用户在自动识别模式中按 [X] 修改的识别间隔会持久化到本目录下的
auto_mode_settings.json，下次启动程序自动加载并覆盖默认值，
无需手改 main_merged.py 源码。
"""

import json
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(_SCRIPT_DIR, 'auto_mode_settings.json')

# 默认识别间隔（秒）
DEFAULT_INTERVAL = 3.0
# 允许的最小识别间隔（秒），与 main_merged._input_interval 的校验保持一致
MIN_INTERVAL = 0.5


def _read_interval():
    """读取保存文件中的间隔值；无文件/损坏/非法时返回 None。"""
    if not os.path.exists(SETTINGS_PATH):
        return None
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get('interval')
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return None
    return val


def get_interval():
    """返回当前生效的识别间隔秒数（默认 3.0；小于 0.5 的值钳到 0.5）。"""
    val = _read_interval()
    if val is None or val != val:  # val != val 即 NaN，视为非法
        return DEFAULT_INTERVAL
    return max(MIN_INTERVAL, val)


def save_interval(val):
    """保存识别间隔秒数到 auto_mode_settings.json（自动钳到最小 0.5 秒）。"""
    try:
        val = float(val)
    except (TypeError, ValueError):
        val = DEFAULT_INTERVAL
    val = max(MIN_INTERVAL, val)
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump({'interval': val}, f, indent=2, ensure_ascii=False)


def reset_interval():
    """删除保存文件，恢复默认间隔。

    Returns:
        bool: True=已删除覆盖文件；False=本来就没有（已是默认）。
    """
    try:
        os.remove(SETTINGS_PATH)
        return True
    except OSError:
        return False
