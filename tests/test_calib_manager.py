#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calib_manager 的 CalibStore 纯逻辑单元测试（不依赖窗口/GUI）。"""

import os
import sys
import json
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calib_manager import CalibStore, sanitize_name  # noqa: E402

RATIOS = {'rx1': 0.3, 'ry1': 0.14, 'rx2': 0.7, 'ry2': 0.9}
RATIOS2 = {'rx1': 0.28, 'ry1': 0.12, 'rx2': 0.72, 'ry2': 0.93}


def _make_store():
    tmp = tempfile.mkdtemp(prefix='calib_test_')
    calibs = os.path.join(tmp, 'calibs')
    calib = os.path.join(tmp, 'calib.json')
    return CalibStore(calibs_dir=calibs, calib_path=calib), tmp


def test_save_list_load():
    store, tmp = _make_store()
    try:
        assert store.list_profiles() == []
        store.save_profile('人机模式', RATIOS)
        assert store.list_profiles() == ['人机模式']
        assert store.load_profile('人机模式') == RATIOS
        assert store.load_profile('不存在') is None
    finally:
        shutil.rmtree(tmp)


def test_delete_profile():
    store, tmp = _make_store()
    try:
        store.save_profile('A', RATIOS)
        store.save_profile('B', RATIOS)
        assert store.delete_profile('A') is True
        assert store.list_profiles() == ['B']
        assert store.delete_profile('A') is False
    finally:
        shutil.rmtree(tmp)


def test_rename_profile():
    store, tmp = _make_store()
    try:
        store.save_profile('旧名', RATIOS)
        assert store.rename_profile('旧名', '新名') is True
        assert store.list_profiles() == ['新名']
        assert store.load_profile('新名') == RATIOS
    finally:
        shutil.rmtree(tmp)


def test_rename_keep_active():
    store, tmp = _make_store()
    try:
        store.save_profile('旧名', RATIOS)
        store.activate('旧名')
        store.rename_profile('旧名', '新名')
        assert store.active_name() == '新名'
    finally:
        shutil.rmtree(tmp)


def test_rename_conflict_raises():
    store, tmp = _make_store()
    try:
        store.save_profile('A', RATIOS)
        store.save_profile('B', RATIOS)
        try:
            store.rename_profile('A', 'B')
            raise AssertionError("应当抛出名称冲突异常")
        except ValueError:
            pass
    finally:
        shutil.rmtree(tmp)


def test_activate_writes_calib():
    store, tmp = _make_store()
    try:
        store.save_profile('M', RATIOS)
        assert store.active_name() is None
        assert store.activate('M') is True
        with open(store.calib_path, encoding='utf-8') as f:
            data = json.load(f)
        assert data == RATIOS
        assert store.active_name() == 'M'
    finally:
        shutil.rmtree(tmp)


def test_activate_missing_returns_false():
    store, tmp = _make_store()
    try:
        assert store.activate('不存在') is False
    finally:
        shutil.rmtree(tmp)


def test_delete_active_clears_marker_keeps_calib():
    store, tmp = _make_store()
    try:
        store.save_profile('M', RATIOS)
        store.activate('M')
        store.delete_profile('M')
        assert store.active_name() is None
        assert os.path.exists(store.calib_path)
    finally:
        shutil.rmtree(tmp)


def test_import_current():
    store, tmp = _make_store()
    try:
        with open(store.calib_path, 'w', encoding='utf-8') as f:
            json.dump(RATIOS, f)
        assert store.import_current('默认') is True
        assert store.load_profile('默认') == RATIOS
        # 无 calib.json 时返回 False
        store2, tmp2 = _make_store()
        try:
            assert store2.import_current('默认') is False
        finally:
            shutil.rmtree(tmp2)
    finally:
        shutil.rmtree(tmp)


def test_save_overwrite():
    store, tmp = _make_store()
    try:
        store.save_profile('M', RATIOS)
        store.save_profile('M', RATIOS2)
        assert store.load_profile('M') == RATIOS2
    finally:
        shutil.rmtree(tmp)


def test_sanitize_name():
    assert sanitize_name('a/b\\c') == 'a_b_c'
    assert sanitize_name('  名字  ') == '名字'
    assert sanitize_name('a?b*c') == 'a_b_c'
    assert sanitize_name('a:b<c>d|e') == 'a_b_c_d_e'
    assert sanitize_name('   ') == ''
    assert sanitize_name('') == ''
    assert sanitize_name(None) == ''


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
