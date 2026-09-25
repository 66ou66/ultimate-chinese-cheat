#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""advisor 多帧投票识别（[1] 键）单元测试。

不启动真实窗口 / 鼠标 / 引擎：用假 Bot + 假 CNN 驱动 capture_board_voted。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 兼容中文/emoji 输出（直接跑测试时 stdout 可能是 GBK）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import numpy as np  # noqa: E402

from advisor import Advisor, VOTE_FRAMES, VOTE_INTERVAL  # noqa: E402
from fen_utils import fen_to_board                       # noqa: E402
from xiangqi_cnn import CLASS_TO_IDX                     # noqa: E402

FEN1 = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'
N_CLASS = len(CLASS_TO_IDX)
EMPTY_IDX = CLASS_TO_IDX['_']
IDX_TO_CLASS = {v: k for k, v in CLASS_TO_IDX.items()}

# 用例坐标：(7,1)=红炮（开局就有），(5,4)=河界空格（开局为空）
CANNON = (7, 1)
EMPTY = (5, 4)


# --- 假组件 ---

class _FakeBot:
    """最小 Bot 替身：只提供识别链路用到的属性/方法。"""

    def __init__(self, playing_red=True):
        self.playing_red = playing_red
        self.cols_logical = list(range(9))
        self.rows_logical = list(range(10))
        self.dpi_scale = 1.0
        self.win_x = 0
        self.win_y = 0
        self.cell_w = 36
        self.cell_h = 36
        self.find_window_calls = 0
        self.load_calibration_calls = 0
        self.detect_orientation_calls = 0
        self.bring_to_front_calls = 0   # 识别前是否把游戏窗口提到前台

    def find_window(self):
        self.find_window_calls += 1
        return True

    def bring_window_to_front(self):
        """把游戏窗口提到前台（真实 Bot 走 Win32 置前；此处只记录调用）。"""
        self.bring_to_front_calls += 1
        return True

    def load_calibration(self):
        self.load_calibration_calls += 1
        return True

    def screenshot_for_processing(self):
        return np.zeros((32, 32, 3), dtype=np.uint8)

    def detect_orientation(self, img):
        self.detect_orientation_calls += 1
        return True


class _FakeCnn:
    """假 CNN：按预设帧序列返回棋盘，并像真 CNN 一样填 _cell_probs。"""

    def __init__(self, frames):
        self.frames = frames          # list[10x9]：每格是概率向量或 None
        self.parse_calls = 0
        self.validate_calls = 0
        self.last_corrections = 0
        self.seen_probs = None        # _validate_board 收到时的 _cell_probs
        self._cell_probs = [[None] * 9 for _ in range(10)]

    def parse_board(self, img, cols_logical, rows_logical, retina_scale,
                    win_x, win_y, cell_w, cell_h, **kwargs):
        frame = self.frames[min(self.parse_calls, len(self.frames) - 1)]
        self.parse_calls += 1
        board = [[None] * 9 for _ in range(10)]
        self._cell_probs = [[None] * 9 for _ in range(10)]
        for r in range(10):
            for c in range(9):
                pr = frame[r][c]
                if pr is None:
                    continue
                self._cell_probs[r][c] = np.asarray(pr, dtype=np.float32)
                cls = IDX_TO_CLASS[int(np.argmax(pr))]
                board[r][c] = None if cls == '_' else cls
        return board

    def _validate_board(self, board):
        self.validate_calls += 1
        self.seen_probs = self._cell_probs
        return board


# --- 工具 ---

def _probs(piece, conf=0.9):
    """构造 15 维概率分布：piece 概率 conf，其余均分。"""
    p = np.full(N_CLASS, (1.0 - conf) / (N_CLASS - 1), dtype=np.float32)
    p[EMPTY_IDX if piece is None else CLASS_TO_IDX[piece]] = conf
    return p


def _frame(base, overrides=None, conf=0.9):
    """单帧概率分布：base 棋盘 + overrides {(r, c): piece}。"""
    b = [row[:] for row in base]
    for (r, c), piece in (overrides or {}).items():
        b[r][c] = piece
    return [[_probs(b[r][c], conf) for c in range(9)] for r in range(10)]


def _opening():
    board, _ = fen_to_board(FEN1)
    return board


def _advisor(frames, playing_red=True, orientation_detected=True):
    """绕过 __init__（不创建 Bot / 引擎进程）构造可测 Advisor。"""
    advisor = Advisor.__new__(Advisor)
    advisor.bot = _FakeBot(playing_red)
    advisor.cnn = _FakeCnn(frames)
    advisor.last = None
    advisor._orientation_detected = orientation_detected
    advisor.depth = 10
    advisor.movetime = 100
    advisor.multi_pv = 2
    return advisor


# --- 投票去噪 ---

def test_voted_rejects_piece_appearing_in_one_frame():
    """5 帧只有 1 帧凭空多出一个红炮 → 投票后该格仍为空。"""
    base = _opening()
    frames = [_frame(base) for _ in range(5)]
    frames[2] = _frame(base, {EMPTY: 'C'})
    advisor = _advisor(frames)
    board, playing_red = advisor.capture_board_voted(frames=5, interval=0)
    assert board[EMPTY[0]][EMPTY[1]] is None
    assert board == base
    assert playing_red is True


def test_voted_keeps_piece_mislabelled_in_one_frame():
    """真棋子「红炮」有 1 帧被误认成红车 → 投票后仍是红炮。"""
    base = _opening()
    frames = [_frame(base) for _ in range(5)]
    frames[0] = _frame(base, {CANNON: 'R'})
    advisor = _advisor(frames)
    board, _ = advisor.capture_board_voted(frames=5, interval=0)
    assert board[CANNON[0]][CANNON[1]] == 'C'


def test_voted_keeps_piece_shown_as_empty_in_one_frame():
    """真棋子有 1 帧被误认成空格 → 投票后仍保留棋子。"""
    base = _opening()
    frames = [_frame(base) for _ in range(5)]
    frames[3] = _frame(base, {CANNON: None})
    advisor = _advisor(frames)
    board, _ = advisor.capture_board_voted(frames=5, interval=0)
    assert board[CANNON[0]][CANNON[1]] == 'C'


def test_voted_tie_broken_by_accumulated_confidence():
    """票数打平（2:2）时，用各帧累加置信度决胜。"""
    base = _opening()
    frames = [_frame(base, {CANNON: 'C'}, conf=0.9) for _ in range(2)]
    frames += [_frame(base, {CANNON: 'R'}, conf=0.6) for _ in range(2)]
    frames += [_frame(base, {CANNON: None}, conf=0.9)]
    advisor = _advisor(frames)
    board, _ = advisor.capture_board_voted(frames=5, interval=0)
    # C 票数 2 vs R 票数 2 → 比置信度：0.9×2 > 0.6×2
    assert board[CANNON[0]][CANNON[1]] == 'C'


def test_voted_all_frames_identical_equals_opening_board():
    """所有帧一致 → 投票结果与单帧完全一致（无副作用）。"""
    base = _opening()
    advisor = _advisor([_frame(base) for _ in range(VOTE_FRAMES)])
    board, _ = advisor.capture_board_voted(interval=0)
    assert board == base
    assert advisor.cnn.parse_calls == VOTE_FRAMES


# --- 帧数 / 参数 ---

def test_voted_parses_exactly_frames_times():
    """frames=N 时恰好截屏识别 N 帧。"""
    base = _opening()
    advisor = _advisor([_frame(base) for _ in range(3)])
    advisor.capture_board_voted(frames=3, interval=0)
    assert advisor.cnn.parse_calls == 3


def test_voted_frames_one_is_single_shot():
    """frames=1 退化为单帧识别（与 capture_board 等价）。"""
    base = _opening()
    advisor = _advisor([_frame(base)])
    board, _ = advisor.capture_board_voted(frames=1, interval=0)
    assert board == base
    assert advisor.cnn.parse_calls == 1


def test_voted_default_params_are_wired():
    """默认参数取自模块常量 VOTE_FRAMES / VOTE_INTERVAL。"""
    assert VOTE_FRAMES == 1, "默认应为单帧识别（用户只在静止局面按 [1]）"
    assert VOTE_INTERVAL > 0
    base = _opening()
    advisor = _advisor([_frame(base) for _ in range(VOTE_FRAMES)])
    advisor.capture_board_voted(interval=0)
    assert advisor.cnn.parse_calls == VOTE_FRAMES


def test_voted_default_wait_budget_is_bounded():
    """[1] 键默认的帧间等待预算 (N-1)×间隔 应 ≤ 0.2s，识别不该被 sleep 拖慢。"""
    assert (VOTE_FRAMES - 1) * VOTE_INTERVAL <= 0.2


# --- 窗口 / 校准 / 方向只做一次 ---

def test_voted_prepares_window_and_calibration_once():
    """多帧识别只在循环外找窗口 / 加载校准一次。"""
    base = _opening()
    advisor = _advisor([_frame(base) for _ in range(5)])
    advisor.capture_board_voted(frames=5, interval=0)
    assert advisor.bot.find_window_calls == 1
    assert advisor.bot.load_calibration_calls == 1


def test_voted_detects_orientation_once():
    """方向检测仍只做一次并锁定（多帧不会重复检测导致翻转）。"""
    base = _opening()
    advisor = _advisor([_frame(base) for _ in range(5)],
                       orientation_detected=False)
    advisor.capture_board_voted(frames=5, interval=0)
    assert advisor.bot.detect_orientation_calls == 1


# --- 与 _validate_board 的衔接 ---

def test_voted_injects_summed_probs_into_validate_board():
    """累加后的概率会回填给 CNN 供 _validate_board 用（且只调用一次）。"""
    base = _opening()
    advisor = _advisor([_frame(base) for _ in range(5)])
    cnn = advisor.cnn
    advisor.capture_board_voted(frames=5, interval=0)
    assert cnn.validate_calls == 1
    # 5 帧 × 0.9 = 4.5（累加，不是取平均）
    assert abs(float(cnn.seen_probs[CANNON[0]][CANNON[1]][CLASS_TO_IDX['C']]) - 4.5) < 1e-4
    assert abs(float(cnn.seen_probs[EMPTY[0]][EMPTY[1]][EMPTY_IDX]) - 4.5) < 1e-4


def test_voted_returns_validate_board_result():
    """投票结果会经过 _validate_board（合法性修正）后才返回。"""
    base = _opening()
    advisor = _advisor([_frame(base) for _ in range(3)])
    fixed = [row[:] for row in base]
    fixed[CANNON[0]][CANNON[1]] = 'R'   # 假装校验把红炮改成了红车
    advisor.cnn._validate_board = lambda board: fixed
    board, _ = advisor.capture_board_voted(frames=3, interval=0)
    assert board is fixed


# --- run_once_voted ---

def test_run_once_voted_returns_run_once_structure():
    """run_once_voted 的返回结构与 run_once 完全一致。"""
    base = _opening()
    advisor = _advisor([_frame(base)])
    calls = {}

    def _capture_voted(frames=None, interval=None):
        calls['frames'] = frames
        calls['interval'] = interval
        return base, True

    advisor.capture_board_voted = _capture_voted
    advisor.board_to_full_fen = lambda b, pr, turn: f'FEN({turn})'

    def _analyze(fen, moves=None):
        calls['analyze'] = (fen, moves)
        return 'RESULT'

    advisor.analyze = _analyze
    out = advisor.run_once_voted('w', moves=['b2e2'])
    assert calls['analyze'] == ('FEN(w)', ['b2e2'])
    assert out == {
        'board': base,
        'playing_red': True,
        'fen': 'FEN(w)',
        'turn': 'w',
        'result': 'RESULT',
    }
    assert calls['frames'] == VOTE_FRAMES      # 默认走模块常量
    assert calls['interval'] == VOTE_INTERVAL
    assert advisor.last is out


def test_run_once_voted_custom_frames_passthrough():
    """frames / interval 可透传给 capture_board_voted。"""
    base = _opening()
    advisor = _advisor([_frame(base)])
    seen = {}

    def _capture_voted(frames=None, interval=None):
        seen['frames'] = frames
        seen['interval'] = interval
        return base, False

    advisor.capture_board_voted = _capture_voted
    advisor.board_to_full_fen = lambda b, pr, turn: 'FEN'
    advisor.analyze = lambda fen, moves=None: 'RESULT'
    advisor.run_once_voted('b', frames=3, interval=0.0)
    assert seen == {'frames': 3, 'interval': 0.0}


def test_capture_board_still_single_shot():
    """capture_board 仍是单帧识别（6/A 等热循环路径不受影响）。"""
    base = _opening()
    advisor = _advisor([_frame(base) for _ in range(3)])
    board, playing_red = advisor.capture_board()
    assert board == base
    assert playing_red is True
    assert advisor.cnn.parse_calls == 1


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
