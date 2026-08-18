#!/usr/bin/env python3
"""
Xiangqi FEN 工具函数

- fen_to_board():   FEN字符串 → 棋盘二维数组 + 元数据
- board_to_fen():   棋盘二维数组 → FEN字符串
- uci_to_coord():   UCI走法 (b2e2) → 棋盘坐标 ((1,7), (4,7))
- coord_to_uci():   棋盘坐标 → UCI走法

约定：
- 棋盘: 10行×9列, board[y][x], y=0是黑方底线 (rank 9), y=9是红方底线 (rank 0)
- FEN rank: 从黑方底线开始 (y=0 → rank 9, y=9 → rank 0)
- UCI: 列a-i对应x=0-8, rank 0-9对应y=9-0 (红方底线rank 0 = y=9)
- turn: 'w'=红方先行, 'b'=黑方先行
"""

import re
from typing import Tuple, List, Optional


# --- 常量 ---

# 标准开局局面（红方在下、先手）。用于全自动对弈启动时判断
# "执黑时红方是否已经先走一步"（对比当前棋盘与开局局面的差分）。
INITIAL_FEN = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'


# --- 棋子映射 ---

FEN_TO_PIECE = {
    'K': 'K', 'R': 'R', 'N': 'N', 'B': 'B', 'A': 'A', 'C': 'C', 'P': 'P',
    'k': 'k', 'r': 'r', 'n': 'n', 'b': 'b', 'a': 'a', 'c': 'c', 'p': 'p',
}

PIECE_TO_FEN = {v: k for k, v in FEN_TO_PIECE.items()}


def fen_to_board(fen: str) -> Tuple[List[List[Optional[str]]], dict]:
    """
    将Xiangqi FEN字符串解析为棋盘数组和元数据。

    Args:
        fen: 标准Xiangqi FEN, 如 "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"

    Returns:
        (board, meta): board[y][x] 为 'K'/'r' 等或None, meta含 turn/fullmove/halfmove 等
    """
    fen = fen.strip()
    # 分离主体和 moves 部分
    moves_part = ''
    if ' moves ' in fen:
        fen, moves_part = fen.split(' moves ', 1)

    parts = fen.split()
    placement = parts[0]
    turn = parts[1] if len(parts) > 1 else 'w'
    # parts[2], parts[3] 通常为 '-' '-'
    halfmove = int(parts[4]) if len(parts) > 4 else 0
    fullmove = int(parts[5]) if len(parts) > 5 else 1

    board: List[List[Optional[str]]] = [[None] * 9 for _ in range(10)]
    ranks = placement.split('/')

    if len(ranks) != 10:
        raise ValueError(f"FEN需10个rank, 实际{len(ranks)}个: {placement}")

    # FEN rank 0 = 黑方底线 (y=0), rank 9 = 红方底线 (y=9)
    for rank_idx, rank_str in enumerate(ranks):
        y = rank_idx  # rank 0 → y=0
        x = 0
        for ch in rank_str:
            if ch.isdigit():
                x += int(ch)
            elif ch in FEN_TO_PIECE:
                board[y][x] = FEN_TO_PIECE[ch]
                x += 1
            else:
                raise ValueError(f"FEN非法字符: '{ch}' in '{rank_str}'")

    meta = {
        'turn': turn,
        'halfmove': halfmove,
        'fullmove': fullmove,
        'moves': moves_part,
    }
    return board, meta


def board_to_fen(board: List[List[Optional[str]]], turn: str = 'w',
                 halfmove: int = 0, fullmove: int = 1) -> str:
    """
    将棋盘数组生成Xiangqi FEN字符串。

    Args:
        board: 10x9数组, board[y][x]
        turn: 'w'=红方先行, 'b'=黑方先行
        halfmove: 半步数
        fullmove: 完整回合数

    Returns:
        FEN字符串
    """
    rows = []
    # y=0→黑方底线(rank 9), y=9→红方底线(rank 0); FEN从黑方底线开始
    for y in range(10):
        empty = 0
        row_parts = []
        for x in range(9):
            piece = board[y][x]
            if piece is None:
                empty += 1
            else:
                if empty > 0:
                    row_parts.append(str(empty))
                    empty = 0
                row_parts.append(PIECE_TO_FEN.get(piece, '?'))
        if empty > 0:
            row_parts.append(str(empty))
        rows.append(''.join(row_parts))

    return f"{'/'.join(rows)} {turn} - - {halfmove} {fullmove}"


def to_engine_board(board, playing_red):
    # Black player: screen board is standard/engine board rotated 180 deg.
    if playing_red:
        return board
    return [row[::-1] for row in reversed(board)]


def infer_orientation(board):
    """开局局面（顶/底行整列满子）时，从棋子颜色可靠推断玩家执红/执黑。

    顶行全为黑（小写）、底行全为红（大写） → 红在屏幕下方 → 玩家执红(True)；
    顶行全为红、底行全为黑 → 红在屏幕上方 → 玩家执黑(False)；
    非标准开局（中盘/顶底行有缺子）→ 返回 None，沿用已锁定方向，避免中盘误判。

    说明：与 auto_battle.py 旧版 _infer_orientation 逻辑一致。作为
    detect_orientation()（红像素比例启发式，玩家执黑时可能误判）的双重校验：
    CNN 识别结果可靠时以棋盘内容为准。
    """
    top = board[0]
    bot = board[9]
    if not (all(p is not None for p in top) and all(p is not None for p in bot)):
        return None
    top_red = sum(1 for p in top if p.isupper())
    bot_red = sum(1 for p in bot if p.isupper())
    if top_red == 0 and bot_red == 9:
        return True
    if top_red == 9 and bot_red == 0:
        return False
    return None


def board_placement(board: List[List[Optional[str]]]) -> str:
    """棋盘指纹：把 10x9 棋盘压缩成唯一字符串（供全自动对弈差分检测用）。

    每格：棋子字符原样；空格用 '1'（数字字符，不与棋子字母冲突）。
    不同局面 → 不同指纹；相同局面 → 相同指纹（幂等）。
    """
    return '/'.join(
        ''.join(board[y][x] if board[y][x] else '1' for x in range(9))
        for y in range(10)
    )


def is_game_over(board: List[List[Optional[str]]]) -> bool:
    """对局是否已结束：帅('K') 或将('k') 任一不在棋盘上（有方被将死/认输）。"""
    has_red = any(board[y][x] == 'K' for y in range(10) for x in range(9))
    has_black = any(board[y][x] == 'k' for y in range(10) for x in range(9))
    return not (has_red and has_black)


def uci_to_coord(uci: str) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """
    UCI走法 → 棋盘坐标。

    UCI: a-i 对应 x=0-8, rank数字0-9
    rank 0 = 红方底线 → y=9, rank 9 = 黑方底线 → y=0
    即 y = 9 - int(rank)

    Example:
        uci_to_coord('b2e2') → ((1, 7), (4, 7))
        uci_to_coord('b9c7') → ((1, 0), (2, 2))
    """
    m = re.match(r'([a-i])(\d{1,2})([a-i])(\d{1,2})', uci)
    if not m:
        raise ValueError(f"无效UCI: {uci}")
    fx = ord(m.group(1)) - ord('a')
    fy = 9 - int(m.group(2))
    tx = ord(m.group(3)) - ord('a')
    ty = 9 - int(m.group(4))
    return (fx, fy), (tx, ty)


def coord_to_uci(from_xy: Tuple[int, int], to_xy: Tuple[int, int]) -> str:
    """棋盘坐标 → UCI走法。"""
    fx, fy = from_xy
    tx, ty = to_xy
    return f"{chr(ord('a') + fx)}{9 - fy}{chr(ord('a') + tx)}{9 - ty}"


def print_board(board: List[List[Optional[str]]]):
    """打印棋盘（中文视角：红方在下）。"""
    # 从 y=9 (红方底线) 打印到 y=0 (黑方底线)
    for y in range(9, -1, -1):
        row = ' '.join(board[y][x] if board[y][x] else '.' for x in range(9))
        print(f"  {y}: {row}")
    print("     a b c d e f g h i")


# --- 测试 ---
if __name__ == '__main__':
    FEN_INITIAL = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1'

    print("=== 初始局面 FEN解析 ===")
    board, meta = fen_to_board(FEN_INITIAL)
    print(f"Turn: {meta['turn']}")
    print_board(board)

    print(f"\n重新生成FEN: {board_to_fen(board, meta['turn'])}")

    print("\n=== UCI坐标测试 ===")
    for uci in ['b2e2', 'b9c7', 'h2e2', 'a0a1']:
        try:
            f, t = uci_to_coord(uci)
            print(f"  {uci} → from{f} to{t} → {coord_to_uci(f, t)}")
        except ValueError as e:
            print(f"  {uci} → {e}")
