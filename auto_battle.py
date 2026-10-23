#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全自动对弈模块：差分状态机 + 分析落子。

流程：
    开局识别（自动判断我方执红/执黑）→ 状态机循环：
      等待对方：截屏差分检测棋盘变化 → 确认对方走完 → 轮到我方
      我方回合：引擎分析 → 自动点击落子 → 回读确认 → 回到等待对方

纯逻辑部分（diff_boards / single_move_from_diff / is_same_move /
contains_move / infer_orientation / AutoBattle.update_board / start / play_my_move）
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

v6 改动（落子确认改为"180 秒超时才暂停"）：
- play_my_move 不再按 max_retries 固定次数重试，改为持续「点击 → 回读」，
  直到超过 CONFIRM_TIMEOUT（默认 180 秒）仍无法确认才打印诊断并暂停。
- 偶发的"点击被吞 / 动画未稳 / 识别抖动"可在窗口内自动重试熬过去，
  不再几次失败就中断全自动对战。
"""

import os
import sys
import time
import traceback
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fen_utils import (fen_to_board, INITIAL_FEN, infer_orientation,  # noqa: E402
                       board_to_fen, coord_to_uci, to_engine_board)
from move_notation import apply_move                               # noqa: E402
from board_diff import (diff_boards, contains_move, moves_in_diff)  # noqa: E402

# 起点→终点点击间隔（秒）
CLICK_INTERVAL = 0.6
# 全自动轮询间隔（秒）
POLL_INTERVAL = 0.8
# 落子回读确认总超时（秒）：持续「点击→回读」直到超时仍无法确认才暂停
CONFIRM_TIMEOUT = 180.0

# Pikafish 象棋变体的"无合法着法"标记（将死/困毙），统一视为无着法
_NO_MOVE_TOKENS = ('', '(none)', '0000', '(pass)', 'nomove', 'none', '0')


def has_move(bestmove):
    """判断引擎着法是否有效（Pikafish 无着法时会输出 '(none)'/'0000' 等）。"""
    if not bestmove:
        return False
    return bestmove.strip().lower() not in _NO_MOVE_TOKENS


# 兼容别名：方向推断已上移至 fen_utils.infer_orientation（advisor 手动模式同样
# 使用），保留旧名供现有调用方/测试引用，逻辑完全一致。
_infer_orientation = infer_orientation


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


def _screen_move_to_uci(fy, fx, ty, tx, playing_red):
    """屏幕方向着法 (y,x) 顺序 → 标准方向 UCI。

    我方执红（红在屏幕下方）时屏幕方向 == 标准方向，直接转换；
    我方执黑时屏幕 = 标准方向旋转 180°，坐标须映射回标准方向
    （屏幕(y,x) ↔ 标准(9-y,8-x)，与 _apply_uci_on_screen 同一旋转约定）。

    Args:
        fy, fx, ty, tx: 屏幕方向 from/to（与 board_diff.moves_in_diff
                        返回的 (fy, fx, ty, tx) 顺序一致）。
        playing_red: 我方执红？

    Returns:
        标准方向 UCI，如 'b7e7'。
    """
    if playing_red:
        return coord_to_uci((fx, fy), (tx, ty))
    return coord_to_uci((8 - fx, 9 - fy), (8 - tx, 9 - ty))


# --- 落子回读诊断格式化（人工排查 confirm_failed 用） ---
_PIECE_CN = {
    'R': '红车', 'N': '红马', 'B': '红相', 'A': '红仕', 'K': '红帅',
    'C': '红炮', 'P': '红兵',
    'r': '黑车', 'n': '黑马', 'b': '黑象', 'a': '黑士', 'k': '黑将',
    'c': '黑炮', 'p': '黑卒',
}


def _piece_cn(piece):
    """棋子字符 → 中文名；None → '空'。"""
    if piece is None:
        return '空'
    return _PIECE_CN.get(piece, piece)


def _fmt_cells(cells):
    """变化格 [(y, x, old, new), ...] → 可读描述，如 '(7,1) 红炮→空 (7,4) 空→红炮'。

    y=行（0 为屏幕顶部），x=列（0..8，屏幕方向），与 AutoPlayer 的
    [点击] 格(r,c) 及 print_board_cn 同一坐标体系。
    """
    return '  '.join(f"({y},{x}) {_piece_cn(o)}→{_piece_cn(n)}"
                     for y, x, o, n in cells)


def _fmt_moves(moves):
    """候选着法 [(fy, fx, ty, tx), ...] → '(fy,fx)→(ty,tx)' 列表。"""
    return '  '.join(f"({fy},{fx})→({ty},{tx})"
                     for fy, fx, ty, tx in moves)


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
        self.start_fen = None        # 开局标准方向 FEN（含 turn，历史重放起点）
        self.move_history = []       # 标准方向 UCI 着法历史（供引擎循环规则）
        self._recent_candidates = deque(maxlen=self.confirm_times)
        self.my_turn = False
        self.move_count = 0          # 我方已走子数（超上限保护）

    # --- 开局 ---

    def start(self, random_opening=False):
        """开局：识别我方执哪方，初始化差分状态机。

        执红(playing_red=True) → 我方先行；执黑 → 若红方已经先走一步
        （棋盘已偏离开局局面）则直接轮到我方，否则等待对方先走。
        方向判定：CNN 红色比例 + 棋盘内容双重确认，
        校验不一致时以棋盘内容为准（开局看顶/底行整列满子，中盘看将/帅九宫位置）。

        Args:
            random_opening: 铜钱场等「随机中局」开局（非标准开局）时为 True。
                此时不能用 _red_already_moved()（它拿标准开局 INITIAL_FEN 做
                差分，随机中局下结果无意义），改按象棋恒定规则处理：红方恒先手
                —— 我方执红则先走，执黑直接等待红方先走一步。随机中局将/帅
                仍在九宫内，方向推断依旧可靠（见 infer_orientation）。

        Returns:
            (board, playing_red)
        """
        board, playing_red = self.advisor.capture_board()
        inferred = infer_orientation(board)
        if inferred is not None and inferred != playing_red:
            # 方向与开局局面不符 → 以开局局面为准修正（同步到底层 Bot）
            self.advisor.bot.playing_red = inferred
            playing_red = inferred
        self.playing_red = playing_red
        self.last_board = [row[:] for row in board]
        # 先确定轮次，再据此设置开局 turn（象棋红方恒先手）：
        #   执红 → 我方红先走(turn='w')
        #   执黑 → 红方未走仍轮红(turn='w')；红方已先走一步则轮黑(turn='b')
        # 修复 v5 中"执黑时 start_fen 的 turn 一律写死 'b'"：那会让
        # _current_turn() 在红方走完第一步后错误返回 'w'（应为 'b'），
        # 引擎按"该红方走"返回红方着法 → 程序把红方棋子当黑子点击 →
        # 落子回读确认失败（执黑必现的 confirm_failed）。
        if playing_red:
            self.my_turn = True
            start_turn = 'w'
        else:
            if random_opening:
                # 随机中局（铜钱场）：红方恒先手，黑方直接等待，无需对比标准开局
                self.my_turn = False
            else:
                self.my_turn = _red_already_moved(board, playing_red)
            start_turn = 'b' if self.my_turn else 'w'
        # v5: 开局 FEN + 着法历史（标准方向 UCI），喂给引擎激活长将/
        # 重复将军判负等循环规则。中途启动（红方已走若干步）时历史从空
        # 开始、循环规则对近期循环仍生效（详见 _reconcile_history 自愈）。
        self.start_fen = self.advisor.board_to_full_fen(
            board, playing_red, start_turn)
        self.move_history = []
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
                # v5: 对方着法转标准 UCI 追加进历史（供引擎循环规则）。
                # 未 start()（start_fen=None，如纯差分测试路径）时跳过。
                if self.start_fen is not None:
                    self.move_history.append(
                        _screen_move_to_uci(*best, self.playing_red))
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

    # --- 着法历史（v5：喂完整历史给引擎，激活循环规则） ---

    def _current_turn(self):
        """由开局 turn 与已走着法数推导当前轮到谁。"""
        if self.start_fen is None:
            return 'w'
        _, meta = fen_to_board(self.start_fen)
        start_turn = meta['turn']
        if len(self.move_history) % 2 == 0:
            return start_turn
        return 'b' if start_turn == 'w' else 'w'

    def _replay_board(self):
        """把 start_fen + move_history 重放为标准方向棋盘。"""
        board, _ = fen_to_board(self.start_fen)
        for uci in self.move_history:
            board = apply_move(board, uci)
        return board

    def _reconcile_history(self, board, playing_red):
        """历史自愈：确保 start_fen+move_history 与当前局面一致。

        重放局面（标准方向）与 board（屏幕方向 → 标准方向）逐格比对；
        不一致说明历史被识别噪声污染 / 中途启动历史不全 → 丢弃历史、
        以当前局面为起点重新累计，避免把矛盾的历史喂给引擎。

        Args:
            board: 当前已确认局面（屏幕方向）。
            playing_red: 我方执红？

        Returns:
            True: 历史可信；False: 已重置（start_fen=当前局面, history=[]）。
        """
        turn = self._current_turn()
        if self.start_fen is None:
            self.start_fen = self.advisor.board_to_full_fen(
                board, playing_red, turn)
            self.move_history = []
            return False
        replay = self._replay_board()
        current = to_engine_board(board, playing_red)
        if board_to_fen(replay) != board_to_fen(current):
            # 历史不可信 → 重置，以当前局面为起点重新累计
            self.start_fen = self.advisor.board_to_full_fen(
                board, playing_red, turn)
            self.move_history = []
            return False
        return True

    # --- 我方回合：分析 + 落子 + 回读确认 ---

    def play_my_move(self, confirm_timeout=CONFIRM_TIMEOUT, max_reads=8,
                     wait_anim=0.6, wait_read=0.4):
        """轮到我方：引擎分析 → 自动点击落子 → 回读确认落子生效。

        回读确认（v3）：实际变化格只要"包含"预期 from/to 两格变化即判定成功，
        多余变化格（识别噪声 / 对方同步落子 / 动画中间态）不参与判定，
        不再要求恰好 2 格 —— 修复 v2 中"我方落子 + 对方同步走子 = 4 格变化"
        导致的偶发"落子回读确认失败"。

        对比基准优先用已确认局面 self.last_board（回读与预期共用同一基准），
        避免"确认帧 → 落子前帧"时间差把对方下一步混进 diff。

        超时策略（v6）：不再按固定次数重试，而是持续「点击 → 回读」，直到
        超过 confirm_timeout 秒仍无法确认落子生效，才打印诊断并返回
        confirm_failed 交人工；期间任意一帧确认成功即立即返回 'ok'。
        这样偶发的"点击被吞 / 动画未稳 / 识别抖动"都能自动熬过去，
        不会像旧版那样重试几次就暂停全自动对战。

        Args（主要供测试缩短等待）:
            confirm_timeout: 落子确认总超时（秒），超时仍无法确认才暂停
            max_reads:   单次点击后回读时最多尝试读取次数（防动画中间态）
            wait_anim:   落子后等待动画稳定的秒数
            wait_read:   回读间隔秒数

        Returns:
            'no_move': 引擎无合法着法（对局结束）
            'ok': 落子成功并回读确认
            'confirm_failed': 超过 confirm_timeout 仍无法确认（需人工介入）
            'board_gone': 识别质量崩溃（棋盘疑似已切换为结算界面：对手投降/超时），
                          按对局结束处理，不应再落子
        """
        # v5: 历史自愈 + 喂完整着法历史给引擎（激活长将/重复将军判负）。
        # 自愈后 last_board 与 start_fen+move_history 重放严格一致，故 FEN
        # 用已确认局面生成，避免最新截图识别噪声污染喂给引擎的局面。
        if self.last_board is not None and self.playing_red is not None:
            self._reconcile_history(self.last_board, self.playing_red)
            fen = self.advisor.board_to_full_fen(
                self.last_board, self.playing_red, self._current_turn())
        else:
            fen = None
        data = self.advisor.run_auto(moves=self.move_history or None, fen=fen)
        # 识别质量崩溃 = 棋盘已不在画面（对手投降/超时跳转结算界面）：
        # 此时识别出的"局面"是垃圾，绝不能据此落子，直接按对局结束处理。
        if getattr(self.advisor, 'recognition_collapsed', lambda: False)():
            print("    [识别崩溃] 棋盘疑似已切换为结算界面（对手投降/超时），不再落子。")
            return 'board_gone'
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

        # v6: 持续「点击→回读」直到确认成功或超过 confirm_timeout 秒。
        # 旧版按 max_retries 固定次数重试，偶发的"点击被吞 / 动画未稳"
        # 会几次内误判失败并暂停全自动对战；现改为按时间兜底，给足窗口。
        start_time = time.time()
        last_actual = None
        while True:
            if time.time() - start_time >= confirm_timeout:
                # 超过总超时仍无法确认落子生效 → 打印诊断后交人工
                print(f"    [回读诊断] 超过 {confirm_timeout:g} 秒仍无法确认"
                      f"落子生效，暂停。")
                print(f"    [回读诊断] 引擎着法 uci={uci}"
                      f"（{'执红' if data['playing_red'] else '执黑'}）")
                print("    [回读诊断] 预期变化格: " + _fmt_cells(d_expected.changed))
                if last_actual is not None:
                    print("    [回读诊断] 实际变化格: "
                          + _fmt_cells(last_actual.changed))
                    cands = moves_in_diff(last_actual)
                    if cands:
                        shown = cands[:6]
                        extra = ('' if len(cands) <= 6
                                 else f" …（共 {len(cands)} 个）")
                        print("    [回读诊断] 实际可识别着法: "
                              + _fmt_moves(shown) + extra)
                    else:
                        print("    [回读诊断] 实际变化不构成任何同色 from→to 着法"
                              "（多为识别噪声 / 棋子颜色被认错）")
                else:
                    print("    [回读诊断] 读取不到稳定局面（连续识别失败）")
                break

            player.play_uci(uci, data['playing_red'])
            time.sleep(wait_anim)
            for _ in range(max_reads):
                try:
                    board2, _ = self.advisor.capture_board()
                except Exception:
                    print(f"    [回读] 截屏/识别异常（继续重试）:\n"
                          f"{traceback.format_exc()}")
                    break  # 识别异常 → 重试点击
                d_actual = diff_boards(before, board2)
                if contains_move(d_actual, d_expected):
                    # 基准 = 干净预期局面（before+uci），而不是带噪声的回读帧：
                    # 消除"噪声被写进基准 → 后续差分恒不稳"的假阳性来源
                    self.last_board = [row[:] for row in expected]
                    self.my_turn = False
                    self.move_count += 1
                    if self.start_fen is not None:
                        self.move_history.append(uci)
                    return 'ok'
                last_actual = d_actual
                if not d_actual.changed:
                    break  # 点击未生效 → 尽快重试点击
                time.sleep(wait_read)  # 动画/识别不稳，继续读
            # 未确认成功 → 循环继续（重新点击），直到超过 confirm_timeout

        # 兜底：确认失败先排除"棋盘已消失"（对手投降/超时跳结算界面），
        # 此时不是落子失败而是对局结束，交上层按 finished 处理。
        if getattr(self.advisor, 'recognition_collapsed', lambda: False)():
            print("    [识别崩溃] 回读阶段棋盘疑似已切换为结算界面（对手投降/超时）。")
            return 'board_gone'
        return 'confirm_failed'
