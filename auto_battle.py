#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全自动对弈模块：差分状态机 + 分析落子。

流程：
    开局识别（自动判断我方执红/执黑）→ 状态机循环：
      等待对方：截屏差分检测棋盘变化 → 确认对方走完 → 轮到我方
      我方回合：引擎分析 → 自动点击落子 → 回读确认 → 回到等待对方

纯逻辑部分（diff_boards / single_move_from_diff / is_same_move /
contains_move / _infer_orientation / AutoBattle.update_board / start / play_my_move）
不依赖 pyautogui / 引擎 / 窗口，可独立单测；落子依赖在方法内 lazy import。

v2 重写要点（相对 v1 的"全盘 90 格精确指纹匹配"）：
- v1 用 board_placement() 要求整盘指纹逐格相等，对 CNN 识别噪声零容忍，
  导致等待对方永远无法稳定确认、落子回读永远 confirm_failed。
- v2 改为"变化格定位"：只关心 from/to 两个变化格是否构成一步合法棋
  （from 有→空、to 空/被吃→有、同色），其余格子的识别噪声不参与判定；
  并用"连续 N 帧同一变化"多帧投票过滤偶发抖动。
- 落子回读只在"棋盘无变化（点击未生效）"时才重试点击，
  避免棋已落下仍盲目重复点击把局面点乱。

v3 修复（"偶发落子回读确认失败"）：
- 回读判定从"恰好 2 个变化格"放宽为"包含预期 from/to 两格变化即可"
  （contains_move）：我方落子 + 对方同步走子 = 4 格变化时不再误判失败。
- 回读对比基准优先用已确认局面 self.last_board，消除
  "确认帧 → 落子前帧"时间差把对方下一步混进 diff 的窗口。
- update_board 校验"变化格必须是对方颜色的子走的"，避免把己方残留/
  人工手误/方向误判当成对方已走。
- 回读对不上预期时，先兜底重读 2 帧做最后一次确认，仍失败才交人工。

v4 修复（"等待对方卡死，一直在找窗口/60 秒无进展"）：
- 根因：update_board 用 single_move_from_diff 要求"恰好 2 个变化格"，
  而 CNN 识别噪声会让 diff 恒大于 2 格（且每帧噪声格位置随机），
  于是对方着法永远无法被确认，状态机卡在"等待对方"直至超时。
- 改为 moves_in_diff 枚举候选着法 + 滑动窗口投票：真实着法每帧稳定
  出现、快速凑够 confirm_times 票；随机噪声每帧不同、凑不够票数。
- 落子回读确认成功后，基准改用"干净预期局面"（before+uci），不再
  把带噪声的回读帧写进 last_board，从源头减少后续差分的假阳性。
"""

import os
import sys
import time
import traceback
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fen_utils import fen_to_board, INITIAL_FEN                     # noqa: E402
from move_notation import apply_move                               # noqa: E402
from board_diff import (diff_boards, contains_move, moves_in_diff)  # noqa: E402

# 起点→终点点击间隔（秒）
CLICK_INTERVAL = 0.6
# 全自动轮询间隔（秒）
POLL_INTERVAL = 0.8

# Pikafish 象棋变体的"无合法着法"标记（将死/困毙），统一视为无着法
_NO_MOVE_TOKENS = ('', '(none)', '0000', '(pass)', 'nomove', 'none', '0')


def has_move(bestmove):
    """判断引擎着法是否有效（Pikafish 无着法时会输出 '(none)'/'0000' 等）。"""
    if not bestmove:
        return False
    return bestmove.strip().lower() not in _NO_MOVE_TOKENS


def _infer_orientation(board):
    """开局局面（标准列位满子）时，从棋子颜色可靠推断我方执红/执黑。

    顶行全为黑、底行全为红 → 红在屏幕下方 → 我方执红(True)；
    顶行全为红、底行全为黑 → 红在屏幕上方 → 我方执黑(False)；
    非标准开局（中盘/底行有缺）→ 返回 None，沿用已锁定方向，避免中盘误判。
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


def _initial_screen_board(playing_red):
    """标准开局局面在【屏幕方向】下的棋盘。

    我方执红（红在屏幕下方）→ 屏幕方向 == 标准方向，直接返回；
    我方执黑（红在屏幕上方）→ 屏幕方向 = 标准方向旋转 180°。
    """
    init, _ = fen_to_board(INITIAL_FEN)
    if playing_red:
        return init
    return [row[::-1] for row in reversed(init)]


def _red_already_moved(board, playing_red):
    """判断红方是否已经先走一步（启动时执黑用）。

    把当前【屏幕方向】棋盘与标准开局局面做差分：若差分里存在一条
    from 格旧子是红方（大写）的着法，说明红方已落过子（正常是先手第一步，
    也可能是中盘的某一步红棋）。有则返回 True（现在轮到我方黑方走），
    否则返回 False（仍是开局未动，等待红方先走）。

    噪声容忍与 update_board 一致：用 moves_in_diff 枚举候选单步，
    单个噪声格无法构成 from→to 完整着法，不会误判。
    """
    init = _initial_screen_board(playing_red)
    diff = diff_boards(init, board)
    for fy, fx, _, _ in moves_in_diff(diff):
        piece = init[fy][fx]
        if piece and piece.isupper():
            return True
    return False


def _apply_uci_on_screen(board, uci, playing_red):
    """在【屏幕方向】棋盘上应用一步 UCI 走法，返回新棋盘。

    屏幕方向 == 标准方向（红方在屏幕下方，即我方执红）时直接 apply_move；
    我方执黑时屏幕方向 = 标准方向旋转 180°，须先转成标准方向、落子、再转回屏幕方向。
    旋转逻辑与 xiangqi_bot.Bot.board_to_fen 保持一致。
    """
    if playing_red:
        return apply_move(board, uci)
    std = [row[::-1] for row in reversed(board)]
    std = apply_move(std, uci)
    return [row[::-1] for row in reversed(std)]


class AutoBattle:
    """全自动对弈状态机（v2：变化格定位 + 多帧投票）。

    关键状态：
      - playing_red: 我方执红（屏幕下方是红方）？
      - my_turn: 当前是否轮到我方走
      - last_board: 已确认的局面（差分对比基准，不再用 90 格精确指纹）
      - _recent_candidates: 最近 confirm_times 帧的候选对方着法集合
        （滑动窗口投票，防动画/识别抖动与随机噪声格）
      - move_count: 我方累计走子数（超 max_moves 保护）
    """

    def __init__(self, advisor, click_interval=CLICK_INTERVAL,
                 poll_interval=POLL_INTERVAL, confirm_times=2,
                 max_moves=200):
        self.advisor = advisor
        self.click_interval = click_interval
        self.poll_interval = poll_interval
        self.confirm_times = confirm_times
        self.max_moves = max_moves
        self.playing_red = None
        self.last_board = None       # 已确认的局面（对比基准）
        self._recent_candidates = deque(maxlen=self.confirm_times)
        self.my_turn = False
        self.move_count = 0          # 我方已走子数（超上限保护）

    # --- 开局 ---

    def start(self):
        """开局：识别我方执哪方，初始化差分状态机。

        执红(playing_red=True) → 我方先行；执黑 → 若红方已经先走一步
        （棋盘已偏离开局局面）则直接轮到我方，否则等待对方先走。
        方向判定：CNN 红色比例 + 开局局面底线校验双重确认，
        校验不一致时以开局局面（顶/底行整列满子）为准。

        Returns:
            (board, playing_red)
        """
        board, playing_red = self.advisor.capture_board()
        inferred = _infer_orientation(board)
        if inferred is not None and inferred != playing_red:
            # 方向与开局局面不符 → 以开局局面为准修正（同步到底层 Bot）
            self.advisor.bot.playing_red = inferred
            playing_red = inferred
        self.playing_red = playing_red
        self.last_board = [row[:] for row in board]
        # 执红（先手）→ 立即轮到我方；
        # 执黑（后手）→ 若红方已经先走一步（棋盘已偏离开局局面）则直接轮到
        # 我方，否则等待红方先走。修复"红方先动一步后再启动脚本"时脚本
        # 卡在等待、需手动走一步才能继续的问题。
        if playing_red:
            self.my_turn = True
        else:
            self.my_turn = _red_already_moved(board, playing_red)
        return board, playing_red

    # --- 差分检测（等待对方时每轮调用）---

    def update_board(self, board):
        """喂入一次识别结果，做差分检测。

        对方走完（同一单步变化在最近 confirm_times 帧的滑动窗口内累计
        出现 confirm_times 次，且必须是"对方颜色"的子走的）→
        my_turn=True 并返回 True。
        未检测到稳定变化 / 变化是己方颜色（上一次落子残留/人工手误/
        识别噪声）/ 仍轮不到我方 → 返回 False。

        与 v2/v3 的区别（v4）：不再要求 diff 恰好 2 个变化格，改用
        moves_in_diff 枚举所有候选单步 + 滑动窗口投票。CNN 识别噪声会让
        diff 恒大于 2 格（且每帧噪声格位置随机），旧逻辑因此永远等不到
        对方着法 —— 这正是"等待对方卡死、一直找窗口/60 秒无进展"的根因。
        """
        if self.my_turn:
            return False
        diff = diff_boards(self.last_board, board)
        if diff.changed:
            # 枚举所有候选单步，并只保留"对方颜色"的子走的（防止把
            # 己方残留/手误/方向误判当成对方走完而带偏轮次状态）
            candidates = [
                m for m in moves_in_diff(diff) if self._is_opponent_move(m)
            ]
        else:
            candidates = []
        # 滑动窗口投票：deque(maxlen=confirm_times) 自动裁剪最旧帧。
        # 真实着法每帧稳定出现 → 快速凑够票数；CNN 随机噪声格每帧
        # 位置不同 → 各自最多 1 票，永远凑不够 → 既不漏检也不误检。
        self._recent_candidates.append(candidates)
        votes = {}
        for frame in self._recent_candidates:
            for m in frame:
                votes[m] = votes.get(m, 0) + 1
        if candidates:
            best = max(candidates, key=lambda m: votes[m])
            if votes[best] >= self.confirm_times:
                self.last_board = [row[:] for row in board]
                self._recent_candidates.clear()
                self.my_turn = True
                return True
        return False

    def _is_opponent_move(self, move):
        """变化格构成的单步是否是"对方颜色"的棋子走的。

        方向未知（playing_red=None）时不限制，保留旧行为；
        否则红=大写、黑=小写，对方颜色与己方相反。
        """
        if self.playing_red is None:
            return True
        fy, fx, _, _ = move
        piece = self.last_board[fy][fx]
        is_red_piece = bool(piece) and piece.isupper()
        return is_red_piece != self.playing_red

    # --- 我方回合：分析 + 落子 + 回读确认 ---

    def play_my_move(self, max_retries=3, max_reads=8,
                     wait_anim=0.6, wait_read=0.4):
        """轮到我方：引擎分析 → 自动点击落子 → 回读确认落子生效。

        回读确认（v3）：实际变化格只要"包含"预期 from/to 两格变化即判定成功，
        多余变化格（识别噪声 / 对方同步落子 / 动画中间态）不参与判定，
        不再要求恰好 2 格 —— 修复 v2 中"我方落子 + 对方同步走子 = 4 格变化"
        导致的偶发"落子回读确认失败"。

        对比基准优先用已确认局面 self.last_board（回读与预期共用同一基准），
        避免"确认帧 → 落子前帧"时间差把对方下一步混进 diff。

        重试策略（v2）：只在回读发现"棋盘无变化（点击未生效）"时才重试点击；
        若棋盘有变化但一直对不上预期，最后兜底再重读 2 帧确认一次，
        仍失败才返回 confirm_failed 交人工，避免把局面点乱。

        Args（主要供测试缩短等待）:
            max_retries: 点击未生效（无变化）时的重试次数
            max_reads:   回读时最多尝试读取次数（防动画中间态）
            wait_anim:   落子后等待动画稳定的秒数
            wait_read:   回读间隔秒数

        Returns:
            'no_move': 引擎无合法着法（对局结束）
            'ok': 落子成功并回读确认
            'confirm_failed': 回读确认失败（需人工介入）
        """
        data = self.advisor.run_auto()
        result = data['result']
        if not has_move(result.bestmove):
            return 'no_move'
        uci = result.bestmove
        # 回读基准：优先用已确认局面（引擎分析仍用 run_auto 的最新截图）
        before = [row[:] for row in (self.last_board or data['board'])]
        expected = _apply_uci_on_screen(before, uci, data['playing_red'])
        d_expected = diff_boards(before, expected)

        from auto_player import AutoPlayer
        player = AutoPlayer(self.advisor.bot, click_interval=self.click_interval)

        for _ in range(max_retries):
            player.play_uci(uci, data['playing_red'])
            time.sleep(wait_anim)
            no_change = False
            last_actual = None
            for _ in range(max_reads):
                try:
                    board2, _ = self.advisor.capture_board()
                except Exception:
                    print(f"    [回读] 截屏/识别异常（按\"点击未生效\"处理）:\n"
                          f"{traceback.format_exc()}")
                    no_change = True
                    break
                d_actual = diff_boards(before, board2)
                if contains_move(d_actual, d_expected):
                    # 基准 = 干净预期局面（before+uci），而不是带噪声的回读帧：
                    # 消除"噪声被写进基准 → 后续差分恒不稳"的假阳性来源
                    self.last_board = [row[:] for row in expected]
                    self.my_turn = False
                    self.move_count += 1
                    return 'ok'
                last_actual = d_actual
                if not d_actual.changed:
                    no_change = True
                    break  # 点击未生效 → 重试点击
                time.sleep(wait_read)  # 动画/识别不稳，继续读
            if no_change:
                continue  # 点击未生效 → 重试点击
            # 棋盘有变化但对不上预期：兜底再连读 2 帧做最后一次确认
            # （回读窗口内每帧都混噪声/对方抢步时，稳定后帧内已含预期 from/to）
            confirmed = False
            for _ in range(2):
                try:
                    board2, _ = self.advisor.capture_board()
                except Exception:
                    break
                if contains_move(diff_boards(before, board2), d_expected):
                    self.last_board = [row[:] for row in expected]
                    self.my_turn = False
                    self.move_count += 1
                    confirmed = True
                    break
                time.sleep(0.3)
            if confirmed:
                return 'ok'
            # 确实对不上预期 → 不盲目重复点击，打印诊断后交人工
            print(f"    [回读诊断] 预期变化格={d_expected.changed}")
            if last_actual is not None:
                print(f"    [回读诊断] 实际变化格={last_actual.changed}")
            else:
                print("    [回读诊断] 读取不到稳定局面（连续识别失败）")
            break
        return 'confirm_failed'
