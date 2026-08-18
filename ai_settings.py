#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 解说运行期设置存储。

默认值仍来自 ai_config.py；用户在主菜单 [9]「AI 解说设置」中修改的
参数会持久化到本目录下的 ai_settings.json，下次启动自动加载并覆盖默认值。

这样无需手改 ai_config.py 源码，程序运行时即可配置
API 密钥 / API 地址 / 模型名 / 超时 / 随机性 / max_tokens。
"""

import json
import os

from ai_config import (API_KEY, API_URL, MAX_TOKENS, MODEL,  # noqa: E402
                       TEMPERATURE, TIMEOUT)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(_SCRIPT_DIR, 'ai_settings.json')

# 默认值（来自 ai_config.py）
DEFAULTS = {
    'api_key': API_KEY or '',
    'api_url': API_URL,
    'model': MODEL,
    'timeout': TIMEOUT,
    'temperature': TEMPERATURE,
    'max_tokens': MAX_TOKENS,
}

# 供 [9] 菜单展示/编辑的字段：key -> (中文名, 值类型)
# 值类型：'str'=普通字符串 / 'float'=数字 / 'int'=整数
FIELDS = [
    ('api_key', 'API 密钥', 'str'),
    ('api_url', 'API 地址', 'str'),
    ('model', '模型名', 'str'),
    ('timeout', '超时秒数', 'float'),
    ('temperature', '随机性(0~2)', 'float'),
    ('max_tokens', '最大 token', 'int'),
]


def _read_overrides():
    """读取 ai_settings.json 中的用户覆盖值（无文件/损坏时返回 {}）。"""
    if not os.path.exists(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get_settings():
    """返回当前生效的完整设置 dict（默认值 + ai_settings.json 覆盖）。"""
    merged = dict(DEFAULTS)
    merged.update(_read_overrides())
    return merged


def save_settings(settings):
    """把完整设置 dict 保存到 ai_settings.json。"""
    data = {k: settings[k] for k in DEFAULTS if k in settings}
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def reset_settings():
    """删除用户覆盖文件，恢复为 ai_config.py 中的默认值。

    Returns:
        bool: True=已删除覆盖文件；False=本来就没有覆盖（已是默认）。
    """
    try:
        os.remove(SETTINGS_PATH)
        return True
    except OSError:
        return False
