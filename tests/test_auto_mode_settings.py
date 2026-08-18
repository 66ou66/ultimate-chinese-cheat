#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_mode_settings 自动识别间隔持久化模块单元测试。

不依赖真实文件系统污染：通过 mock.patch 把 auto_mode_settings.SETTINGS_PATH
指向临时目录，验证默认值 / 保存重载 / 损坏回退 / 小于 0.5 钳制。
"""

import json
import os
import sys
import tempfile
import shutil
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_mode_settings  # noqa: E402


def _make_tmp():
    tmp = tempfile.mkdtemp(prefix='auto_mode_settings_test_')
    return os.path.join(tmp, 'auto_mode_settings.json'), tmp


def test_default_when_no_file():
    path, tmp = _make_tmp()
    try:
        with mock.patch('auto_mode_settings.SETTINGS_PATH', path):
            assert auto_mode_settings.get_interval() == 3.0
    finally:
        shutil.rmtree(tmp)


def test_save_then_load_returns_saved():
    path, tmp = _make_tmp()
    try:
        with mock.patch('auto_mode_settings.SETTINGS_PATH', path):
            auto_mode_settings.save_interval(2.0)
            assert os.path.exists(path)
            with open(path, 'r', encoding='utf-8') as f:
                assert json.load(f) == {'interval': 2.0}
            assert auto_mode_settings.get_interval() == 2.0
    finally:
        shutil.rmtree(tmp)


def test_load_corrupt_file_falls_back_to_default():
    path, tmp = _make_tmp()
    try:
        with mock.patch('auto_mode_settings.SETTINGS_PATH', path):
            with open(path, 'w', encoding='utf-8') as f:
                f.write('{bad json')
            assert auto_mode_settings.get_interval() == 3.0
    finally:
        shutil.rmtree(tmp)


def test_load_non_dict_falls_back_to_default():
    path, tmp = _make_tmp()
    try:
        with mock.patch('auto_mode_settings.SETTINGS_PATH', path):
            with open(path, 'w', encoding='utf-8') as f:
                json.dump([1, 2], f)
            assert auto_mode_settings.get_interval() == 3.0
    finally:
        shutil.rmtree(tmp)


def test_load_value_below_min_clamped_to_0_5():
    path, tmp = _make_tmp()
    try:
        with mock.patch('auto_mode_settings.SETTINGS_PATH', path):
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'interval': 0.2}, f)
            assert auto_mode_settings.get_interval() == 0.5
    finally:
        shutil.rmtree(tmp)


def test_save_value_below_min_clamped_to_0_5():
    path, tmp = _make_tmp()
    try:
        with mock.patch('auto_mode_settings.SETTINGS_PATH', path):
            auto_mode_settings.save_interval(0.1)
            assert auto_mode_settings.get_interval() == 0.5
    finally:
        shutil.rmtree(tmp)


def test_save_string_number_coerced():
    path, tmp = _make_tmp()
    try:
        with mock.patch('auto_mode_settings.SETTINGS_PATH', path):
            auto_mode_settings.save_interval('2.5')
            assert auto_mode_settings.get_interval() == 2.5
    finally:
        shutil.rmtree(tmp)


def test_load_nan_falls_back_to_default():
    path, tmp = _make_tmp()
    try:
        with mock.patch('auto_mode_settings.SETTINGS_PATH', path):
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'interval': float('nan')}, f)
            assert auto_mode_settings.get_interval() == 3.0
    finally:
        shutil.rmtree(tmp)


def test_reset_interval():
    path, tmp = _make_tmp()
    try:
        with mock.patch('auto_mode_settings.SETTINGS_PATH', path):
            assert auto_mode_settings.reset_interval() is False  # 本来就没有
            auto_mode_settings.save_interval(2.0)
            assert auto_mode_settings.reset_interval() is True
            assert auto_mode_settings.get_interval() == 3.0
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
