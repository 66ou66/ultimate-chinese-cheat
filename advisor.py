#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
编排层：把「视觉识别」和「引擎分析」串成一条完整链路。

数据流：
    识别(capture_board) -> 拼完整 FEN -> Pikafish 分析 -> 中文格式化建议
"""

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_XIANGQI_BOT_DIR = os.path.join(_SCRIPT_DIR, 'xiangqi-bot')
if _XIANGQI_BOT_DIR not in sys.path:
    sys.path.insert(0, _XIANGQI_BOT_DIR)

from xiangqi_bot import Bot                       # noqa: E402
from engine_wrapper import PikafishEngine         # noqa: E402
from turn_detector import TurnDetector            # noqa: E402
from move_notation import uci_to_cn, format_score, apply_move, pv_to_cn, uci_cn # noqa: E402


RED = '\033[31m'
BLK = '\033[90m'
RST = '\033[0m'

_PIECE_SYMBOLS = {
    'R': '车', 'N': '马', 'B': '相', 'A': '仕', 'K': '帅', 'C': '炮', 'P': '兵',
    'r': '车', 'n': '马', 'b': '象', 'a': '士', 'k': '将', 'c': '炮', 'p': '卒',
}


def print_board_cn(board, title="棋盘"):
    """打印中文棋盘（board[y][x]，y=0 为屏幕顶部，按从上到下打印）。"""
    print("\n" + "=" * 52)
    print(f"  {title}")
    print("=" * 52)
    for y in range(10):
        line = ""
        for x in range(9):
            p = board[y][x]
            if p:
                color = RED if p.isupper() else BLK
                line += f" {color}{_PIECE_SYMBOLS.get(p, '?')}{RST}"
            else:
                line += " 十"
        print(f"  {line}")
    print("   九 八 七 六 五 四 三 二 一")


class Advisor:
    """一站式 AI 助手：识别棋盘 -> 引擎分析 -> 输出建议。"""

    def __init__(self, depth=18, movetime=2000, multi_pv=3, engine_path=None):
        self.bot = Bot()
        self.engine = PikafishEngine(engine_path=engine_path)
        self.turn = TurnDetector()
        self.depth = depth
        self.movetime = movetime
        self.multi_pv = multi_pv
        self.cnn = None
        self.last = None  # 最近一次 run_once 的结果

    # --- CNN 懒加载（模型加载慢，只加载一次）---

    def ensure_cnn(self):
        if self.cnn is None:
            from xiangqi_cnn import PieceClassifierCNN
            self.cnn = PieceClassifierCNN()
        return self.cnn

    # --- 识别端 ---

    def capture_board(self):
        """找窗口、加载校准、截屏、检测方向、CNN 识别。

        Returns:
            (board, playing_red)
            board: 10x9 数组（屏幕方向，r0=屏幕顶部）
            playing_red: True=玩家执红，False=玩家执黑
        """
        self.bot.find_window()
        if not self.bot.load_calibration():
            raise RuntimeError("校准数据加载失败。请先运行校准（打开天天象棋到棋盘界面后校准）。")
        img = self.bot.screenshot_for_processing()
        self.bot.detect_orientation(img)
        cnn = self.ensure_cnn()
        board = cnn.parse_board(
            img,
            self.bot.cols_logical,
            self.bot.rows_logical,
            self.bot.dpi_scale,
            self.bot.win_x,
            self.bot.win_y,
            self.bot.cell_w,
            self.bot.cell_h,
        )
        return board, self.bot.playing_red

    # --- FEN 生成 ---

    def board_to_full_fen(self, board, playing_red, turn):
        """把棋盘数组转成引擎可用的完整 FEN（含 turn 字段）。"""
        saved = self.bot.playing_red
        self.bot.playing_red = playing_red
        try:
            placement = self.bot.board_to_fen(board)
        finally:
            self.bot.playing_red = saved
        return f"{placement} {turn} - - 0 1"

    # --- 引擎分析 ---

    def analyze(self, fen):
        if not self.engine._initialized:
            self.engine.initialize()
        return self.engine.analyze(
            fen, depth=self.depth, movetime=self.movetime, multi_pv=self.multi_pv,
        )

    # --- 一站式 ---

    def run_once(self, turn):
        """执行一次完整识别 + 分析。

        Args:
            turn: 'w'=红方走 / 'b'=黑方走

        Returns:
            dict: board / playing_red / fen / turn / result
        """
        board, playing_red = self.capture_board()
        fen = self.board_to_full_fen(board, playing_red, turn)
        result = self.analyze(fen)
        self.last = {
            'board': board,
            'playing_red': playing_red,
            'fen': fen,
            'turn': turn,
            'result': result,
        }
        return self.last

    def capture_and_edit(self):
        """识别 + 人工检验，返回修正后的 (board, playing_red)；取消则返回自动结果。"""
        board, playing_red = self.capture_board()
        auto_fen = self.board_to_full_fen(board, playing_red, self.turn.current())
        print_board_cn(board, "自动识别结果（进入人工修正前）")
        print(f"\n自动 FEN: {auto_fen}")
        print("\n操作提示: ↑↓←→ 移动光标 | [1-7]红方 | [Shift+1-7]黑方 | [0]清除 | [C]清盘 | [R]重置")
        input("按 Enter 进入人工编辑...")

        from manual_edit import ManualBoardEditor
        os.system('cls')
        editor = ManualBoardEditor(board, playing_red, self.bot)
        corrected = editor.run()

        if corrected is None:
            print("\n⚠ 人工检验已取消，保留自动识别结果。")
            return board, playing_red

        os.system('cls')
        corrected_fen = self.board_to_full_fen(corrected, playing_red, self.turn.current())
        print(f"\n✅ 人工修正完成，修正 FEN: {corrected_fen}")
        return corrected, playing_red

    def run_edited_once(self, turn):
        """识别 + 人工检验 + 分析，返回与 run_once 相同结构的结果。"""
        board, playing_red = self.capture_and_edit()
        fen = self.board_to_full_fen(board, playing_red, turn)
        result = self.analyze(fen)
        self.last = {
            'board': board,
            'playing_red': playing_red,
            'fen': fen,
            'turn': turn,
            'result': result,
        }
        return self.last

    def close(self):
        self.engine.close()


def format_suggestion(data):
    """把 run_once 的结果格式化为中文可读文本。"""
    board = data['board']
    result = data['result']
    playing_red = data['playing_red']

    side_name = '红方' if data['turn'] == 'w' else '黑方'
    lines = []
    lines.append(f"轮次: {side_name}走")
    lines.append(f"FEN : {data['fen']}")

    if not result.bestmove:
        lines.append("⚠ 引擎未返回着法（可能已结束或局面异常）")
        return '\n'.join(lines)

    best_cn = uci_cn(result.bestmove, board)
    best_score = format_score(result.candidates[0].score_cp, result.candidates[0].score_mate) \
        if result.candidates else '?'

    lines.append(f"最佳着法: {best_cn}  评分 {best_score}")
    if result.ponder:
        ponder_board = apply_move(board, result.bestmove)
        lines.append(f"对方可能应着(Ponder): {uci_cn(result.ponder, ponder_board)}")

    if result.candidates:
        lines.append("")
        lines.append("候选变例 (MultiPV):")
        for c in result.candidates:
            score = format_score(c.score_cp, c.score_mate)
            pv = ' '.join(pv_to_cn(c.pv, board)[:8])
            lines.append(f"  #{c.multipv} depth={c.depth:<3} {score:<10} {pv}")

    return '\n'.join(lines)
