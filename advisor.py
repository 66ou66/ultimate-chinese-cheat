#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
编排层：把「视觉识别」和「引擎分析」串成一条完整链路。

数据流：
    识别(capture_board) -> 拼完整 FEN -> Pikafish 分析 -> 中文格式化建议
"""

import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_XIANGQI_BOT_DIR = os.path.join(_SCRIPT_DIR, 'xiangqi-bot')
if _XIANGQI_BOT_DIR not in sys.path:
    sys.path.insert(0, _XIANGQI_BOT_DIR)

from xiangqi_bot import Bot                       # noqa: E402
from engine_wrapper import PikafishEngine         # noqa: E402
from turn_detector import TurnDetector            # noqa: E402
from move_notation import uci_to_cn, format_score, apply_move, pv_to_cn, uci_cn # noqa: E402
from fen_utils import to_engine_board, infer_orientation, board_placement  # noqa: E402


RED = '\033[31m'
BLK = '\033[90m'
RST = '\033[0m'

_PIECE_SYMBOLS = {
    'R': '车', 'N': '马', 'B': '相', 'A': '仕', 'K': '帅', 'C': '炮', 'P': '兵',
    'r': '车', 'n': '马', 'b': '象', 'a': '士', 'k': '将', 'c': '炮', 'p': '卒',
}

# 多帧投票识别默认参数（[1] 键识别用）。capture_board_voted 仍支持传 frames>1
# 做逐格投票去噪，但默认帧数定为 1：只在画面静止时按 [1]，无需多帧投票避开
# 走子动画，单帧即可（CNN 已改为「整盘 90 格一次批量前向」，见
# PieceClassifierCNN._classify_cells_full；帧数 3→1 后识别端耗时约再降到 1/3）。
# 若日后需要多帧去噪，把 VOTE_FRAMES 调回 3 即可（奇数，"多数"更稳）。
VOTE_FRAMES = 1        # 帧数（1=单帧识别，最快；改回 3 可恢复投票去噪）
VOTE_INTERVAL = 0.05   # 相邻两帧间隔（秒，仅 frames>1 时生效）

# 多帧投票识别诊断（排查「[1] 键整盘识别错乱」用）：默认关闭。
# 置环境变量 XIANGQI_DEBUG_VOTE=1 后，capture_board_voted 会：
#   1. 把每帧原始截图存到 debug_vote/frame_i.png
#   2. 打印每帧的棋盘指纹 board_placement + 方向 playing_red + 合法性修正格数
#   3. 打印最终投票指纹，以及「哪些帧指纹与最终结果一致」
# 用途：一眼区分三种故障 ——
#   (a) 每帧指纹相同但内容明显不是棋盘 → 截图被别的窗口遮挡（窗口前置问题）
#   (b) 各帧指纹不同 → 棋盘动画/抖动，投票可能被少数帧带偏
#   (c) 各帧指纹相同且确实是棋盘、最终仍错 → CNN 网格/方向问题
VOTE_DEBUG = os.environ.get('XIANGQI_DEBUG_VOTE') == '1'
VOTE_DEBUG_DIR = os.path.join(_SCRIPT_DIR, 'debug_vote')


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
        self._orientation_detected = False  # 方向是否已检测并锁定（防中盘翻转）
        self._last_img = None  # 最近一帧原始截图（仅诊断/排查时读取）

    # --- CNN 懒加载（模型加载慢，只加载一次）---

    def ensure_cnn(self):
        if self.cnn is None:
            from xiangqi_cnn import PieceClassifierCNN
            self.cnn = PieceClassifierCNN()
        return self.cnn

    # --- 识别端 ---

    def _prepare_capture(self):
        """找窗口 + 把游戏窗口提到前台 + 加载校准（多帧识别只需在循环外做一次）。

        把窗口提到前台这一步是必须的：screenshot_for_processing() 截的是屏幕
        坐标区域，若截图时游戏被控制台等窗口盖住，截到的就是遮挡窗口的内容，
        识别结果整盘错乱（[1] 键手动识别就是在控制台里按键，最容易踩到）。
        详见 Bot.bring_window_to_front()。
        """
        self.bot.find_window()
        self.bot.bring_window_to_front()
        if not self.bot.load_calibration():
            raise RuntimeError("校准数据加载失败。请先运行校准（打开天天象棋到棋盘界面后校准）。")

    def _capture_once(self):
        """截一帧并 CNN 识别（含方向检测与双重校验）。

        Returns:
            (board, playing_red)，含义同 capture_board。
        """
        img = self.bot.screenshot_for_processing()
        self._last_img = img   # 供 VOTE_DEBUG 诊断落盘（parse_board 不会原地改图）
        # 方向只在首次识别时检测一次并锁定：中盘顶/底行棋子变动后
        # "红像素比例"判定会失真，每次重检极易误判导致整盘 180° 翻转
        if not self._orientation_detected:
            self.bot.detect_orientation(img)
            self._orientation_detected = True
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
        # 方向双重校验：detect_orientation() 只靠"红像素比例"，中盘（尤其玩家
        # 执黑、红在屏幕上方）可能误判为执红，导致整盘 180° 翻转、引擎乱指导
        # （着法列号与进退方向全部镜像）。
        # 开局局面用顶/底行整列满子判色；中盘则用将/帅九宫位置（帅恒在 y=7..9、
        # 将恒在 y=0..2）兜底，均以棋盘内容为准（与 auto_battle.AutoBattle.start()
        # 的双重确认一致）；仅当两将帅同侧/缺失（返回 None）时沿用已锁定方向。
        inferred = infer_orientation(board)
        if inferred is not None and inferred != self.bot.playing_red:
            self.bot.playing_red = inferred
        return board, self.bot.playing_red

    def capture_board(self):
        """找窗口、加载校准、截屏、检测方向、CNN 识别（单帧）。

        Returns:
            (board, playing_red)
            board: 10x9 数组（屏幕方向，r0=屏幕顶部）
            playing_red: True=玩家执红，False=玩家执黑
        """
        self._prepare_capture()
        return self._capture_once()

    def capture_board_voted(self, frames=VOTE_FRAMES, interval=VOTE_INTERVAL):
        """多帧投票识别：连续截 N 帧，逐格投票 + 置信度累加去噪。

        [1] 键识别专用。单帧识别时 CNN 在某一格的偶发误判（棋子认错 /
        放错位置）会直接落到结果里；连续截 N 帧后逐格统计各棋子出现的
        帧数，取票数最多者 —— 真实棋子每帧稳定出现，偶发噪声只在个别
        帧出现，被投票纠正。票数相同时再比各帧累加的置信度（等价于把
        bot.parse_board_cnn 的"低置信度格双帧取平均"推广到 N 帧），仍
        相同则优先"有子"。整体思路与 auto_battle 的多帧投票一致。

        Args:
            frames:   截屏帧数（默认 VOTE_FRAMES=1 即单帧；>1 时逐格投票去噪）。
            interval: 相邻两帧间隔秒数（默认 VOTE_INTERVAL）。

        Returns:
            (board, playing_red)，与 capture_board 完全一致。
        """
        self._prepare_capture()
        frames = max(1, int(frames))
        if frames <= 1:
            board, playing_red = self._capture_once()
            if VOTE_DEBUG:
                fp = board_placement(board)
                self._dump_vote_frame(0, fp, playing_red)
                self._dump_vote_result(fp, playing_red, [fp])
            return board, playing_red
        cnn = self.ensure_cnn()
        from xiangqi_cnn import CLASS_TO_IDX   # ensure_cnn() 已加载该模块，开销可忽略
        votes = [[{} for _ in range(9)] for _ in range(10)]   # 每格：棋子 → 出现帧数
        conf_sum = [[None] * 9 for _ in range(10)]            # 每格：各帧概率累加（平票用）
        playing_red = self.bot.playing_red
        frame_fingerprints = []   # 诊断用：每帧的棋盘指纹
        for i in range(frames):
            board, playing_red = self._capture_once()
            if VOTE_DEBUG:
                fp = board_placement(board)
                frame_fingerprints.append(fp)
                self._dump_vote_frame(i, fp, playing_red)
            for r in range(10):
                for c in range(9):
                    piece = board[r][c]
                    votes[r][c][piece] = votes[r][c].get(piece, 0) + 1
                    probs = cnn._cell_probs[r][c]
                    if probs is None:
                        continue
                    if conf_sum[r][c] is None:
                        conf_sum[r][c] = probs.astype(float).copy()
                    else:
                        conf_sum[r][c] += probs
            if i < frames - 1:
                time.sleep(interval)
        board = [[None] * 9 for _ in range(10)]
        for r in range(10):
            for c in range(9):
                probs = conf_sum[r][c]
                best_piece, best_key = None, None
                for piece, n in votes[r][c].items():
                    conf = 0.0
                    if probs is not None:
                        conf = float(probs[CLASS_TO_IDX[piece or '_']])
                    key = (n, conf, piece is not None)   # 票数 → 置信度 → 有子优先
                    if best_key is None or key > best_key:
                        best_piece, best_key = piece, key
                board[r][c] = best_piece
        # 让合法性校验也用累加后的分布，保证校验结果与投票结果一致
        cnn._cell_probs = conf_sum
        board = cnn._validate_board(board)
        if VOTE_DEBUG:
            self._dump_vote_result(board_placement(board), playing_red,
                                   frame_fingerprints)
        return board, playing_red

    # --- 投票诊断（默认关闭，置环境变量 XIANGQI_DEBUG_VOTE=1 开启）---

    def _dump_vote_frame(self, index, fingerprint, playing_red):
        """（诊断）打印第 index 帧的识别指纹，并把该帧原始截图存盘。

        截图存到 VOTE_DEBUG_DIR/frame_<index>.png，可直接打开看「1 键到底
        截到了什么」：是棋盘画面，还是控制台文字/别的窗口。
        """
        print(f"  [投票诊断] 帧{index}: 指纹={fingerprint} "
              f"playing_red={playing_red} 修正格数={getattr(self.cnn, 'last_corrections', 0)}")
        img = getattr(self, '_last_img', None)
        if img is None:
            print(f"  [投票诊断] 帧{index}: 无可保存的截图")
            return
        try:
            import cv2
            os.makedirs(VOTE_DEBUG_DIR, exist_ok=True)
            path = os.path.join(VOTE_DEBUG_DIR, f"frame_{index}.png")
            cv2.imwrite(path, img)
            print(f"  [投票诊断] 帧{index}: 截图已存 {path}")
        except Exception as e:
            print(f"  [投票诊断] 帧{index}: 截图保存失败 {e}")

    def _dump_vote_result(self, fingerprint, playing_red, frame_fingerprints):
        """（诊断）打印投票结果，并汇总各帧指纹的一致性。"""
        print(f"  [投票诊断] 最终: 指纹={fingerprint} playing_red={playing_red} "
              f"修正格数={getattr(self.cnn, 'last_corrections', 0)}")
        if not frame_fingerprints:
            return
        counts = {}
        for fp in frame_fingerprints:
            counts[fp] = counts.get(fp, 0) + 1
        same = sum(1 for fp in frame_fingerprints if fp == fingerprint)
        print(f"  [投票诊断] 各帧指纹分布: {counts}")
        print(f"  [投票诊断] 与最终一致: {same}/{len(frame_fingerprints)} 帧"
              f"（全一致=画面稳定；众数指纹≠最终指纹=被少数帧带偏）")

    def recognition_collapsed(self, threshold=15):
        """识别质量是否崩溃（棋盘疑似已不在画面）。

        依据：CNN 合法性校验 _validate_board 的修正格子数。正常对局（校准正确、
        画面确实是棋盘）修正数应为个位数；对手投降 / 时间耗尽时天天象棋提前
        跳到结算界面，棋盘消失，CNN 会在非棋盘画面上自信地误判出大量超出规则
        上限的棋子 → 修正数激增（实测 36）。阈值 15 足够与正常识别分离。

        Returns:
            True = 识别质量崩溃，应把当前状态判定为"对局已结束"。
        """
        cnn = getattr(self, 'cnn', None)
        if cnn is None:
            return False
        return getattr(cnn, 'last_corrections', 0) >= threshold

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

    def analyze(self, fen, moves=None):
        if not self.engine._initialized:
            self.engine.initialize()
        return self.engine.analyze(
            fen, depth=self.depth, movetime=self.movetime, multi_pv=self.multi_pv,
            moves=moves,
        )

    # --- 一站式 ---

    def run_once(self, turn, moves=None):
        """执行一次完整识别 + 分析。

        Args:
            turn: 'w'=红方走 / 'b'=黑方走
            moves: 可选，引擎着法历史（标准方向 UCI），激活循环规则。

        Returns:
            dict: board / playing_red / fen / turn / result
        """
        board, playing_red = self.capture_board()
        fen = self.board_to_full_fen(board, playing_red, turn)
        result = self.analyze(fen, moves=moves)
        self.last = {
            'board': board,
            'playing_red': playing_red,
            'fen': fen,
            'turn': turn,
            'result': result,
        }
        return self.last

    def run_once_voted(self, turn, moves=None,
                       frames=VOTE_FRAMES, interval=VOTE_INTERVAL):
        """执行一次「多帧投票识别 + 分析」（[1] 键用，等价 run_once）。

        与 run_once 的唯一区别：识别走 capture_board_voted（多帧概率累加
        去噪，缓解单帧 CNN 偶发误判导致的"棋子认错 / 放错位置"）；
        FEN 生成、引擎分析、返回结构与 run_once 完全一致。

        Args:
            turn:  'w'=红方走 / 'b'=黑方走
            moves: 可选，引擎着法历史（标准方向 UCI），激活循环规则。
            frames/interval: 见 capture_board_voted。

        Returns:
            dict: board / playing_red / fen / turn / result
        """
        board, playing_red = self.capture_board_voted(frames=frames,
                                                     interval=interval)
        fen = self.board_to_full_fen(board, playing_red, turn)
        result = self.analyze(fen, moves=moves)
        self.last = {
            'board': board,
            'playing_red': playing_red,
            'fen': fen,
            'turn': turn,
            'result': result,
        }
        return self.last

    def run_auto(self, moves=None, fen=None):
        """识别 + 分析（半自动落子用）：turn 直接由我方执哪方推导。

        我方执红(playing_red=True) → turn='w'；我方执黑 → turn='b'。
        与 run_once 的区别：不依赖手动维护的 turn，避免"对方走没走"的状态混乱。

        Args:
            moves: 可选，引擎着法历史（标准方向 UCI）。喂完整历史给引擎
                   可激活长将/重复将军判负等循环规则（全自动对弈用）。
            fen:   可选，覆盖识别生成的 FEN（全自动模式喂"已确认局面"的
                   FEN，保证与 moves 严格一致）；None 时用识别结果生成。
        """
        board, playing_red = self.capture_board()
        turn = 'w' if playing_red else 'b'
        if fen is None:
            fen = self.board_to_full_fen(board, playing_red, turn)
        result = self.analyze(fen, moves=moves)
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

    def run_edited_once(self, turn, moves=None):
        """识别 + 人工检验 + 分析，返回与 run_once 相同结构的结果。"""
        board, playing_red = self.capture_and_edit()
        fen = self.board_to_full_fen(board, playing_red, turn)
        result = self.analyze(fen, moves=moves)
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
    eb = to_engine_board(board, playing_red)

    side_name = '红方' if data['turn'] == 'w' else '黑方'
    lines = []
    lines.append(f"轮次: {side_name}走")
    lines.append(f"FEN : {data['fen']}")

    if not result.bestmove:
        lines.append("⚠ 引擎未返回着法（可能已结束或局面异常）")
        return '\n'.join(lines)

    best_cn = uci_cn(result.bestmove, eb)
    best_score = format_score(result.candidates[0].score_cp, result.candidates[0].score_mate) \
        if result.candidates else '?'

    lines.append(f"最佳着法: {best_cn}  评分 {best_score}")
    if result.ponder:
        ponder_board = apply_move(eb, result.bestmove)
        lines.append(f"对方可能应着(Ponder): {uci_cn(result.ponder, ponder_board)}")

    if result.candidates:
        lines.append("")
        lines.append("候选变例 (MultiPV):")
        for c in result.candidates:
            score = format_score(c.score_cp, c.score_mate)
            pv = ' '.join(pv_to_cn(c.pv, eb)[:8])
            lines.append(f"  #{c.multipv} depth={c.depth:<3} {score:<10} {pv}")

    return '\n'.join(lines)
