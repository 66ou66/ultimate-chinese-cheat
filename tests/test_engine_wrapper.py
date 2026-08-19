#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""engine_wrapper 引擎命令构造单元测试（不启动真实引擎，mock _send/_read）。"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 兼容中文/emoji 输出
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

from engine_wrapper import PikafishEngine  # noqa: E402

FEN = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1'


def _engine(bestmove='b2e2'):
    """构造已初始化、可捕获 _send 的假引擎。"""
    engine = PikafishEngine(engine_path='__fake__')
    engine._initialized = True
    engine.sent = []
    engine._send = lambda cmd: engine.sent.append(cmd)
    engine._read = mock.Mock(side_effect=[f'bestmove {bestmove}', None])
    return engine


def _position_cmds(engine):
    return [c for c in engine.sent if c.startswith('position ')]


def test_position_fen_only_without_moves():
    """moves=None → 与旧行为完全一致：只发 position fen。"""
    engine = _engine()
    result = engine.analyze(FEN, depth=10, movetime=100, multi_pv=1)
    cmds = _position_cmds(engine)
    assert cmds == [f'position fen {FEN}']
    assert result.bestmove == 'b2e2'


def test_position_fen_with_moves():
    """moves 提供时 → position fen <fen> moves <uci 序列>。"""
    engine = _engine('h2h9')
    engine.analyze(FEN, depth=10, movetime=100, multi_pv=1,
                   moves=['b2e2', 'b7e7'])
    cmds = _position_cmds(engine)
    assert cmds == [f'position fen {FEN} moves b2e2 b7e7']


def test_position_fen_empty_moves_same_as_none():
    """moves=[]（空历史）→ 与 None 一致，不产生 moves 段。"""
    engine = _engine()
    engine.analyze(FEN, depth=10, movetime=100, multi_pv=1, moves=[])
    cmds = _position_cmds(engine)
    assert cmds == [f'position fen {FEN}']
    assert not any(' moves ' in c for c in cmds)


def test_position_fen_moves_long_history():
    """长历史（多次循环着法）逐个空格拼接，一次发给引擎。"""
    engine = _engine()
    history = ['b2e2', 'b7e7', 'e2b2', 'e7b7', 'b2e2', 'b7e7']
    engine.analyze(FEN, depth=10, movetime=100, multi_pv=1, moves=history)
    cmds = _position_cmds(engine)
    assert cmds == [f'position fen {FEN} moves ' + ' '.join(history)]


if __name__ == '__main__':
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  \u2713 {name}")
        except Exception:
            failed += 1
            print(f"  \u2717 {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
