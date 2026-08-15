#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
棋盘差分工具：定位两帧识别结果之间的变化格，并校验是否构成一步合法棋。

纯函数模块，不依赖窗口/鼠标/引擎，可独立单测。

核心概念：
- 一次识别与上一次已确认局面做逐格对比，得到"变化格集合"。
- 一步棋在棋盘上恰好表现为 2 个变化格：
    from 格：旧局面有子 → 新局面为空
    to   格：新局面有子（吃子时旧局面也有子，但被替换）
  from 与 to 的棋子必须同色。
- 其余 88 格的识别噪声完全不参与判定 —— 这是相对"全盘指纹精确匹配"的核心改进。
"""

from typing import List, NamedTuple, Optional, Tuple


class BoardDiff(NamedTuple):
    """两帧棋盘的差分结果。"""
    changed: List[Tuple[int, int, Optional[str], Optional[str]]]  # (y, x, old_piece, new_piece)
    old_count: Tuple[int, int]   # (red, black) 旧局面棋子数
    new_count: Tuple[int, int]   # (red, black) 新局面棋子数


def _count(board) -> Tuple[int, int]:
    """统计红/黑棋子数。红方大写、黑方小写。"""
    red = black = 0
    for row in board:
        for p in row:
            if p is None:
                continue
            if p.isupper():
                red += 1
            else:
                black += 1
    return (red, black)


def diff_boards(old, new) -> BoardDiff:
    """逐格对比 old / new 两个 10x9 棋盘。

    Args:
        old / new: board[y][x]，棋子大写=红 / 小写=黑 / None=空。

    Returns:
        BoardDiff：
          changed:  变化格列表，每项 (y, x, old_piece, new_piece)，按 (y, x) 排序
          old_count / new_count: 两局面红/黑棋子数
    """
    changed = []
    for y in range(10):
        for x in range(9):
            a, b = old[y][x], new[y][x]
            if a != b:
                changed.append((y, x, a, b))
    return BoardDiff(changed, _count(old), _count(new))


def single_move_from_diff(diff: BoardDiff) -> Optional[Tuple[int, int, int, int]]:
    """从 diff 中提取一步棋的 from/to 格。

    一步棋（含吃子）恰好对应 2 个变化格：
      - from：旧有子 → 新空
      - to：  新有子（吃子时旧有子且被替换为不同子）
    from 与 to 的棋子必须同色。

    Args:
        diff: diff_boards() 的返回值。

    Returns:
        (from_y, from_x, to_y, to_x)；变化不构成单步（含识别噪声/非法局面）返回 None。
    """
    changed = diff.changed
    if len(changed) != 2:
        return None
    from_cells = [(y, x) for (y, x, old, new) in changed if old and not new]
    to_cells = [(y, x) for (y, x, old, new) in changed
                if new and (not old or old != new)]
    if len(from_cells) != 1 or len(to_cells) != 1:
        return None
    (fy, fx), (ty, tx) = from_cells[0], to_cells[0]
    old_from = next(o for (y, x, o, n) in changed if (y, x) == (fy, fx))
    new_to = next(n for (y, x, o, n) in changed if (y, x) == (ty, tx))
    if old_from is None or new_to is None:
        return None
    if old_from.isupper() != new_to.isupper():   # 同色校验
        return None
    return (fy, fx, ty, tx)


def is_same_move(actual: BoardDiff, expected: BoardDiff) -> bool:
    """实际变化与预期变化是否构成同一步棋（from/to 完全一致）。

    Returns:
        True：两者都构成单步且 from/to 相同。
        False：任一侧不构成单步（含无变化），或 from/to 不同。
    """
    a = single_move_from_diff(actual)
    e = single_move_from_diff(expected)
    return a is not None and a == e


def moves_in_diff(diff: BoardDiff) -> List[Tuple[int, int, int, int]]:
    """从差分中枚举所有候选单步（from→to 同色），容忍额外噪声格。

    与 single_move_from_diff 的区别：
      single_move_from_diff 要求变化格恰好 2 个，多一个噪声格就返回 None
      （对识别噪声零容忍——这是\"等待对方落子\"检测不稳定的根因）；
      本函数只要求能定位到\"旧有子→新空\"与\"新有子\"且同色的一对格，
      其余噪声格不参与判定，返回全部候选，由上层多帧投票决定取舍。

    吃子同样适用：from 格旧有子→新空，to 格旧子被替换为新子。

    Args:
        diff: diff_boards() 的返回值。

    Returns:
        [(fy, fx, ty, tx), ...]；无候选时返回空列表。
    """
    from_cells = [(y, x, old) for (y, x, old, new) in diff.changed if old and not new]
    to_cells = [(y, x, new) for (y, x, old, new) in diff.changed
                if new and (not old or old != new)]
    moves = []
    for fy, fx, f_old in from_cells:
        for ty, tx, t_new in to_cells:
            if f_old.isupper() == t_new.isupper():   # 同色校验
                moves.append((fy, fx, ty, tx))
    return moves


def contains_move(actual: BoardDiff, expected: BoardDiff) -> bool:
    """实际变化是否"包含"预期的一步棋（回读确认用，容忍额外变化格）。

    与 is_same_move 的区别：
      is_same_move 要求两侧都恰好构成单步（恰 2 个变化格）且 from/to 一致，
      对识别噪声/对方同步落子零容忍 —— 这是"落子回读确认失败"的根因之一；
      本函数只要求实际变化格里存在与预期完全相同 from/to 的两格变化即可，
      其余多余变化格（识别噪声 / 对方同步落子 / 动画中间态）不参与判定。

    Returns:
        True：实际变化格里能定位到与预期一致的一步棋（这步已落在棋盘上）。
        False：预期本身不构成单步，或实际变化格里找不到这步。
    """
    e = single_move_from_diff(expected)
    if e is None:
        return False
    fy, fx, ty, tx = e
    e_map = {(y, x): (o, n) for (y, x, o, n) in expected.changed}
    from_old = e_map[(fy, fx)][0]   # 预期 from 的旧子（己方颜色）
    to_new = e_map[(ty, tx)][1]     # 预期 to 的新子（己方颜色）

    a_map = {(y, x): (o, n) for (y, x, o, n) in actual.changed}
    if (fy, fx) not in a_map or (ty, tx) not in a_map:
        return False
    a_from_old, a_from_new = a_map[(fy, fx)]
    _, a_to_new = a_map[(ty, tx)]
    # from：旧子与预期一致、新为空（子已离开）；to：新子与预期一致
    if a_from_old != from_old or a_from_new is not None:
        return False
    if a_to_new != to_new:
        return False
    return True


# --- 自测 ---
if __name__ == '__main__':
    from fen_utils import fen_to_board
    from move_notation import apply_move

    FEN1 = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'
    b1, _ = fen_to_board(FEN1)
    b2 = apply_move(b1, 'b2e2')

    print("=== 单步变化 ===")
    d = diff_boards(b1, b2)
    print(f"  changed={d.changed}")
    print(f"  move={single_move_from_diff(d)}  (期望 (7,1,7,4))")

    print("\n=== 吃子变化 ===")
    board = [[None] * 9 for _ in range(10)]
    board[0][0], board[9][0] = 'r', 'R'
    after = apply_move(board, 'a0a9')
    d2 = diff_boards(board, after)
    print(f"  changed={d2.changed}")
    print(f"  move={single_move_from_diff(d2)}  (期望 (9,0,0,0))")

    print("\n=== 噪声（3 格变化）===")
    noisy = [row[:] for row in b1]
    noisy[3][3] = 'P'
    print(f"  single_move={single_move_from_diff(diff_boards(b1, noisy))}  (期望 None)")
