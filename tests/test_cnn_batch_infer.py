#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整盘 CNN 批量推理（PieceClassifierCNN._classify_cells_full）单元测试。

背景：parse_board 要识别 10x9 = 90 个格子。改动前是逐格调用
_classify_cell_full，即 90 次独立模型前向 + 90 次 host↔device 往返；在无 CUDA
的 CPU 上这是「[1] 键识别慢」的主因。改动后 90 格叠成一个 batch 只做 1 次前向。

本文件锁定两点，防止以后被改回去：
  1. 快：整盘只做 1 次前向（且是 batch=90），空/过小的格子被跳过但不错位；
  2. 准：批量结果与逐格结果数值等价（eval 模式下 BatchNorm 逐样本独立），
     因此整盘批量识别与改动前的逐格识别**逐格完全一致**。

不加载权重文件 / 不启动窗口：直接用 PieceNet 架构（随机初始化 + 固定种子）。
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, 'xiangqi-bot'))   # xiangqi_cnn 所在目录

# 兼容中文/emoji 输出（直接跑测试时 stdout 可能是 GBK）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import numpy as np  # noqa: E402
import torch        # noqa: E402

from xiangqi_cnn import (PieceClassifierCNN, PieceNet, MODEL_PATH)  # noqa: E402

# 与 parse_board 约定一致的测试网格：格点间距 40px，格宽/高 36 → 采样半径 19
COLS = [100 + 40 * c for c in range(9)]
ROWS = [100 + 40 * r for r in range(10)]
CELL = 36
RADIUS = int(CELL * 0.55)      # = 19


# --- 假组件 ---

class _SpyNet(torch.nn.Module):
    """极轻的假模型：记录每次前向的 batch 大小，并恒定输出「空格」类别。

    为什么不用真 PieceNet：真模型对 90 格做一次 128x128 卷积在 CPU 上要约 1 秒，
    而「整盘是否只做 1 次前向 / 跳过格子后下标是否错位」并不需要真卷积来验证。
    真模型的数值一致性由 test_batched_inference_matches_per_cell* 与
    test_real_model_batch_matches_single_cell 覆盖。
    """

    def __init__(self):
        super().__init__()
        self.batch_sizes = []

    def forward(self, x):
        self.batch_sizes.append(int(x.shape[0]))
        logits = torch.zeros((x.shape[0], 15), dtype=torch.float32)
        logits[:, 0] = 1.0        # 恒为 '_'（空格），避免触发 _validate_board 修正
        return logits


# --- 工具 ---

def _cnn(model=None):
    """绕过 __init__（不加载 xiangqi_cnn.pt）构造可用的 PieceClassifierCNN。"""
    cnn = PieceClassifierCNN.__new__(PieceClassifierCNN)
    cnn.device = torch.device('cpu')
    cnn.model = model if model is not None else PieceNet()
    cnn.model.eval()               # 与真实 __init__ 一致：eval 让 Dropout/BN 确定
    cnn.last_corrections = 0
    return cnn


def _img(width=520, height=520, seed=1, rect=False):
    """构造确定性测试截图（rect=True 返回带色块的浅底图，更像真实棋盘）。"""
    rng = np.random.RandomState(seed)
    if rect:
        img = np.full((height, width, 3), 200, dtype=np.uint8)
        for r in range(10):
            for c in range(9):
                y, x = ROWS[r], COLS[c]
                img[y - 12:y + 12, x - 12:x + 12] = rng.randint(0, 255, (1, 1, 3))
        return img
    return rng.randint(0, 255, (height, width, 3), dtype=np.uint8)


def _parse(cnn, img, **kwargs):
    """按固定网格调用 parse_board（retina_scale=1、窗口在原点）。"""
    return cnn.parse_board(img, COLS, ROWS, 1.0, 0, 0, CELL, CELL, **kwargs)


def _patch(seed, size=38):
    return np.random.RandomState(seed).randint(0, 255, (size, size, 3), dtype=np.uint8)


# --- 快：整盘一次批量前向 ---

def test_parse_board_uses_single_batch_forward_for_whole_board():
    """整盘 90 格只做 1 次前向，且 batch 就是 90（改动前是 90 次前向）。"""
    spy = _SpyNet()
    cnn = _cnn(spy)
    board = _parse(cnn, _img())
    assert spy.batch_sizes == [90], f"应恰好 1 次 batch=90 的前向，实际 {spy.batch_sizes}"
    assert len(board) == 10 and all(len(row) == 9 for row in board)


def test_parse_board_skips_invalid_patches_without_shifting_cells():
    """画面外的格子被跳过，但结果下标不会错位（batch 大小 == 实际填了概率的格数）。"""
    spy = _SpyNet()
    cnn = _cnn(spy)
    # 300x300 画面：只有左上 5x5 个格点的采样框落在画面内
    board = _parse(cnn, _img(width=300, height=300))
    filled = sum(1 for r in range(10) for c in range(9)
                 if cnn._cell_probs[r][c] is not None)
    assert len(spy.batch_sizes) == 1
    assert spy.batch_sizes[0] == filled < 90, "跳过的格子不应进入 batch"
    assert cnn._cell_probs[0][0] is not None      # 左上角在画面内
    assert cnn._cell_probs[9][8] is None          # 右下角在画面外 → 保持空
    assert board[9][8] is None


def test_parse_board_fills_probs_and_patches_for_valid_cells():
    """有效格子会写入 _cell_probs / _cell_patches（_validate_board 依赖这两个字段）。"""
    cnn = _cnn(_SpyNet())
    _parse(cnn, _img())
    for r in (0, 4, 9):
        for c in (0, 8):
            assert cnn._cell_probs[r][c] is not None
            assert len(cnn._cell_probs[r][c]) == 15
            assert cnn._cell_patches[r][c].shape[0] == 2 * RADIUS


def test_classify_cells_full_handles_empty_and_invalid_input():
    """全部无效时直接返回占位结果，不触发任何前向。"""
    spy = _SpyNet()
    cnn = _cnn(spy)
    assert cnn._classify_cells_full([]) == []
    out = cnn._classify_cells_full([None, np.zeros((5, 5, 3), dtype=np.uint8)])
    assert out == [(None, 0.0, None), (None, 0.0, None)]
    assert spy.batch_sizes == []


# --- 准：批量与逐格数值等价 ---

def test_batched_inference_matches_per_cell():
    """同一批 patch：_classify_cells_full 与逐格 _classify_cell_full 结果一致。"""
    torch.manual_seed(0)
    cnn = _cnn()
    patches = [_patch(i) for i in range(12)]
    batched = cnn._classify_cells_full(patches)
    assert len(batched) == len(patches)
    for p, (b_piece, b_conf, b_probs) in zip(patches, batched):
        s_piece, s_conf, s_probs = cnn._classify_cell_full(p)
        assert b_piece == s_piece
        assert abs(b_conf - s_conf) < 1e-5
        assert np.allclose(b_probs, s_probs, atol=1e-5)


def test_batched_inference_matches_per_cell_after_skipping():
    """混入无效 patch 时，有效项的批量结果仍与逐格一致（下标不错位）。"""
    torch.manual_seed(0)
    cnn = _cnn()
    patches = [_patch(0), None, _patch(1), np.zeros((3, 3, 3), dtype=np.uint8),
               _patch(2)]
    batched = cnn._classify_cells_full(patches)
    assert batched[1] == (None, 0.0, None)
    assert batched[3] == (None, 0.0, None)
    for i in (0, 2, 4):
        s_piece, s_conf, s_probs = cnn._classify_cell_full(patches[i])
        assert batched[i][0] == s_piece
        assert abs(batched[i][1] - s_conf) < 1e-5
        assert np.allclose(batched[i][2], s_probs, atol=1e-5)


def test_parse_board_equals_manual_per_cell_reference():
    """整盘 parse_board（批量）与改动前的逐格实现逐格完全一致。"""
    torch.manual_seed(0)
    model = PieceNet()
    model.eval()
    img = _img(rect=True)
    # 随机初始化权重的网络会把噪声判成各种棋子，_validate_board 会刷很多
    # 「FEN fix ...」，这里静音以免刷屏（断言本身不受影响）。
    quiet = contextlib.redirect_stdout(io.StringIO())
    with quiet:
        got = _parse(_cnn(model), img)

    # 参考实现：改动前的逐格逻辑（对每格单独调 _classify_cell_full）
    ref = _cnn(model)
    ref._cell_probs = [[None] * 9 for _ in range(10)]
    ref._cell_patches = [[None] * 9 for _ in range(10)]
    ref_board = [[None] * 9 for _ in range(10)]
    h, w = img.shape[:2]
    for r in range(10):
        for c in range(9):
            x1, y1 = max(0, COLS[c] - RADIUS), max(0, ROWS[r] - RADIUS)
            x2, y2 = min(w, COLS[c] + RADIUS), min(h, ROWS[r] + RADIUS)
            patch = img[y1:y2, x1:x2]
            if patch.shape[0] < 20 or patch.shape[1] < 20:
                continue
            piece, _conf, probs = ref._classify_cell_full(patch)
            ref_board[r][c] = piece
            ref._cell_probs[r][c] = probs

    with quiet:
        assert got == ref._validate_board(ref_board)


def test_real_model_batch_matches_single_cell():
    """有真实权重（xiangqi_cnn.pt）时：批量与逐格结果一致（真实模型上的回归）。

    这一条专门防住一个真实踩过的坑：若批量张量按 NHWC 视图喂给网络，CPU/MKLDNN 会
    输出 channels_last 特征图，PieceNet 的 x.view(...) 直接抛 RuntimeError。
    """
    if not os.path.exists(MODEL_PATH):
        return                      # 没有训练产物时跳过，测试不依赖模型文件
    cnn = PieceClassifierCNN(MODEL_PATH)
    patches = [_patch(i) for i in range(8)]
    batched = cnn._classify_cells_full(patches)
    for p, (b_piece, b_conf, b_probs) in zip(patches, batched):
        s_piece, s_conf, s_probs = cnn._classify_cell_full(p)
        assert b_piece == s_piece
        assert abs(b_conf - s_conf) < 1e-4
        assert np.allclose(b_probs, s_probs, atol=1e-4)


# --- 与 debug 落盘、合法性校验的衔接 ---

def test_parse_board_still_saves_debug_patches():
    """debug_dir 开启时每格 patch 仍按「类别子目录 + 前缀」落盘（90 张）。"""
    cnn = _cnn(_SpyNet())
    d = tempfile.mkdtemp(prefix='xq_cnn_debug_')
    try:
        _parse(cnn, _img(), debug_dir=d, debug_prefix='s01_')
        files = []
        for root, _dirs, names in os.walk(d):
            files += [os.path.join(root, n) for n in names if n.endswith('.png')]
        assert len(files) == 90
        assert any(os.path.basename(f).startswith('s01_r0c0_') for f in files)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_parse_board_runs_validate_board_once():
    """批量推理不影响合法性校验：仍然只调用一次 _validate_board。"""
    cnn = _cnn(_SpyNet())
    calls = []
    original = cnn._validate_board
    cnn._validate_board = lambda b: (calls.append(1), original(b))[1]
    _parse(cnn, _img())
    assert calls == [1]


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
