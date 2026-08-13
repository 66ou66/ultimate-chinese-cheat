#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""turn_detector 单元测试。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turn_detector import TurnDetector  # noqa: E402


def test_default():
    t = TurnDetector()
    assert t.current() == 'w'
    assert t.label() == '红方'


def test_set():
    t = TurnDetector()
    t.set('b')
    assert t.current() == 'b'
    assert t.label() == '黑方'


def test_flip():
    t = TurnDetector('w')
    t.flip()
    assert t.current() == 'b'
    t.flip()
    assert t.current() == 'w'


def test_invalid():
    t = TurnDetector()
    try:
        t.set('x')
        assert False, "应当抛出 ValueError"
    except ValueError:
        pass


if __name__ == '__main__':
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except Exception:
            failed += 1
            print(f"  ✗ {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
