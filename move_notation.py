#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国象棋 UCI 走法 → 中文着法（记谱）转换模块。

坐标约定（与 fen_utils.py 保持一致）：
- board[y][x]，y=0 黑方底线（rank 9），y=9 红方底线（rank 0）
- x=0..8 对应 UCI 列 a..i
- UCI 走法形如 "b2e2"：列字母 + rank 数字
- 红方棋子用大写字母（R N B A K C P），黑方用小写（r n b a k c p）

中文记谱规则：
- 列号（"路"）：红方从右往左数，x=8 是"一"路、x=0 是"九"路；
  黑方从左往右数，x=0 是"1"路、x=8 是"9"路。
- 动作词：x 不变 → "平"（目标列号）；
  x 改变 → "进/退"。
  - 红方"进" = 向黑方（y 减小）；黑方"进" = 向红方（y 增大）。
- 进/退后的数字描述：
  - 车、炮、将帅、兵卒（直线棋子）：步数（纵向格数）。
  - 马、相象、仕士（斜线/非直线）：目标列号。
- 数字：红方用汉字（一~九），黑方用阿拉伯数字（1~9）。
- 并列歧义（同列同名棋子）：前/后（2个）、前/中/后（3个）、数字序号（≥4个）。
"""

from fen_utils import uci_to_coord

# 红方列号：x=8→一 ... x=0→九（从右往左）
RED_COL = {8: '一', 7: '二', 6: '三', 5: '四', 4: '五', 3: '六', 2: '七', 1: '八', 0: '九'}
# 黑方列号：x=0→1 ... x=8→9（从左往右）
BLACK_COL = {0: '1', 1: '2', 2: '3', 3: '4', 4: '5', 5: '6', 6: '7', 7: '8', 8: '9'}

# 红方棋子中文名
RED_NAME = {'R': '车', 'N': '马', 'B': '相', 'A': '仕', 'K': '帅', 'C': '炮', 'P': '兵'}
# 黑方棋子中文名
BLACK_NAME = {'r': '车', 'n': '马', 'b': '象', 'a': '士', 'k': '将', 'c': '炮', 'p': '卒'}

# 红方步数用汉字
RED_NUM = {1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '七', 8: '八', 9: '九'}

# 直线棋子（进/退用步数描述）
_LINEAR_PIECES = {'R', 'C', 'K', 'P', 'r', 'c', 'k', 'p'}


def _piece_name(piece):
    """棋子字符 → 中文名。"""
    if piece in RED_NAME:
        return RED_NAME[piece]
    if piece in BLACK_NAME:
        return BLACK_NAME[piece]
    raise ValueError(f"未知棋子: {piece!r}")


def _is_red(piece):
    return piece.isupper()


def _col_label(x, piece):
    """列号标签：红方汉字、黑方数字。"""
    if _is_red(piece):
        return RED_COL[x]
    return BLACK_COL[x]


def _num_label(n, piece):
    """步数标签：红方汉字、黑方数字。"""
    if _is_red(piece):
        return RED_NUM[n]
    return str(n)


def _is_advance(piece, fy, ty):
    """判断纵向移动是否为"进"。红方进=y减小，黑方进=y增大。"""
    if _is_red(piece):
        return ty < fy
    return ty > fy


def _prefix_for_column(board, piece, fx, fy):
    """处理同列同名棋子的并列歧义，返回前缀（前/后/中/序号/空串）。"""
    same = []
    for y in range(10):
        if board[y][fx] == piece and y != fy:
            same.append(y)

    if not same:
        return ''

    all_ys = [fy] + same
    # 按"前→后"排序：红方 y 小在前，黑方 y 大在前
    if _is_red(piece):
        all_ys.sort()
    else:
        all_ys.sort(reverse=True)

    idx = all_ys.index(fy)
    n = len(all_ys)
    if n == 2:
        return '前' if idx == 0 else '后'
    if n == 3:
        return ['前', '中', '后'][idx]
    # 极端情况（≥4 个兵卒同列）用数字序号
    return ['一', '二', '三', '四', '五'][idx]


def uci_to_cn(uci, board):
    """
    将 UCI 走法转换为中文着法。

    Args:
        uci: UCI 走法字符串，如 "b2e2"。
        board: 10x9 数组，board[y][x] 为大写红方/小写黑方/None。

    Returns:
        中文着法字符串，如 "炮八平五"、"前车进一"。

    Raises:
        ValueError: uci 非法或起始格无棋子。
    """
    (fx, fy), (tx, ty) = uci_to_coord(uci)
    piece = board[fy][fx]
    if piece is None:
        raise ValueError(f"起始格无棋子: {uci} (from=({fx},{fy}))")

    name = _piece_name(piece)
    col = _col_label(fx, piece)

    if fy == ty:
        # 纵向不变 = 横移 = 平
        action = '平'
        desc = _col_label(tx, piece)
    else:
        action = '进' if _is_advance(piece, fy, ty) else '退'
        if piece in _LINEAR_PIECES:
            desc = _num_label(abs(ty - fy), piece)
        else:
            desc = _col_label(tx, piece)

    prefix = _prefix_for_column(board, piece, fx, fy)
    if prefix:
        # 有前/后/中/序号前缀时省略列号：如"前车进一"
        return f"{prefix}{name}{action}{desc}"
    return f"{name}{col}{action}{desc}"


def uci_to_coord_label(uci, board):
    """
    将 UCI 走法转换为直观的坐标描述（调试/兜底用）。

    Returns:
        "红炮 第8路第3行 → 第5路第3行" 这类字符串。
    """
    (fx, fy), (tx, ty) = uci_to_coord(uci)
    piece = board[fy][fx]
    side = '红' if (piece and _is_red(piece)) else '黑'
    name = _piece_name(piece) if piece else '?'
    return f"{side}{name} 第{_col_label(fx, piece) if piece else '?'}路第{fy + 1}行 → " \
           f"第{_col_label(tx, piece) if piece else '?'}路第{ty + 1}行"


def format_score(score_cp, score_mate):
    """
    把引擎分值格式化为中文描述。

    Args:
        score_cp: 厘卒分值（None 表示无）。
        score_mate: 将杀步数（None 表示无）。

    Returns:
        中文分值字符串，如 "+25 分"、"杀 3 步"。
    """
    if score_mate is not None:
        if score_mate > 0:
            return f"杀 {score_mate} 步"
        return f"被将 {-score_mate} 步"
    if score_cp is not None:
        return f"{score_cp:+d} 分"
    return "?"


def apply_move(board, uci):
    """在棋盘上应用一步 UCI 走法，返回新棋盘（不改原棋盘）。

    中国象棋走法均可由 from→to 覆盖完成（无升变/易位/吃过路兵），
    引擎输出的走法保证合法，故此处只做棋子搬移。
    """
    (fx, fy), (tx, ty) = uci_to_coord(uci)
    new_board = [row[:] for row in board]
    piece = new_board[fy][fx]
    new_board[fy][fx] = None
    new_board[ty][tx] = piece
    return new_board


def uci_cn(uci, board):
    """单步 UCI → '中文着法 (uci)' 格式，如 '炮五进四 (e2e6)'。"""
    return f"{uci_to_cn(uci, board)} ({uci})"


def pv_to_cn(pv, board, with_uci=True):
    """把整条 PV 变例转成中文着法列表，逐步推演局面。

    每个候选变例都从同一初始局面独立推演（第 2 步起棋子位置已变化）。
    单步转换/推演失败时降级保留 UCI 原文，保证不中断。
    """
    cur = [row[:] for row in board]
    out = []
    for uci in pv:
        try:
            cn = uci_to_cn(uci, cur)
            out.append(f"{cn} ({uci})" if with_uci else cn)
        except Exception:
            out.append(uci)
        try:
            cur = apply_move(cur, uci)
        except Exception:
            break
    return out


# --- 自测 ---
if __name__ == '__main__':
    from fen_utils import fen_to_board

    def show(fen, uci, label):
        board, _ = fen_to_board(fen)
        print(f"{uci:<8} -> {uci_to_cn(uci, board):<12} (期望: {label})")

    FEN_INIT = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1'
    print("=== 初始局面（红方走）===")
    show(FEN_INIT, 'b2e2', '炮八平五')   # 左炮平中路
    show(FEN_INIT, 'h2e2', '炮二平五')   # 右炮平中路
    show(FEN_INIT, 'b0c2', '马八进七')   # 左马进
    show(FEN_INIT, 'h0g2', '马二进三')   # 右马进
    show(FEN_INIT, 'c0e2', '相七进五')   # 相进
    show(FEN_INIT, 'g0e2', '相三进五')
    show(FEN_INIT, 'a3a4', '兵九进一')   # 兵进一
    show(FEN_INIT, 'g3g4', '兵三进一')
    show(FEN_INIT, 'a0a1', '车九进一')   # 车进一
    show(FEN_INIT, 'i0i1', '车一进一')
