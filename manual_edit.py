#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
终端交互式棋盘编辑器 - 用于人工修正CNN识别错误的棋子位置。

进入方式: 在 main_fen.py 中按 [2]
操作说明:
  ↑↓←→          移动光标
  1-7            放置红方棋子 (1=车 2=马 3=相 4=仕 5=帅 6=炮 7=兵)
  Shift+1~7      放置黑方棋子 (1=车 2=马 3=象 4=士 5=将 6=炮 7=卒)
  0 / 空格       清空当前光标格
  C              清空整个棋盘
  R              重置为CNN自动识别结果
  Enter          确认并输出FEN
  Esc            取消返回主菜单
"""

import os
import sys
import msvcrt

# --- 常量 ---

PIECE_CN = {
    'R': '车', 'N': '马', 'B': '相', 'A': '仕', 'K': '帅', 'C': '炮', 'P': '兵',
    'r': '车', 'n': '马', 'b': '象', 'a': '士', 'k': '将', 'c': '炮', 'p': '卒',
}

RED_CLR = '\033[31m'
DIM_CLR = '\033[90m'
RST_CLR = '\033[0m'
BOLD = '\033[1m'
CYAN = '\033[36m'
WHITE = '\033[97m'
BG_CYAN = '\033[46m'

NUM_TO_RED = {'1':'R','2':'N','3':'B','4':'A','5':'K','6':'C','7':'P'}
SHIFT_TO_BLK = {'!':'r','@':'n','#':'b','$':'a','%':'k','^':'c','&':'p'}

RED_PIECES = set('RNBAKCP')
BLACK_PIECES = set('rnbakcp')


def get_key():
    """读取单个按键, 返回 (key_type, key_value). 兼容传统和现代终端。"""
    ch = msvcrt.getch()
    if ch == b'\r': return 'enter', None
    if ch == b' ': return 'space', None
    if ch == b'\x08': return 'backspace', None

    # 传统 Windows 方向键（数字小键盘 / 旧控制台）
    if ch in (b'\xe0', b'\x00'):
        ch2 = msvcrt.getch()
        m = {b'H': ('up', None), b'P': ('down', None), b'K': ('left', None),
             b'M': ('right', None), b'S': ('delete', None)}
        return m.get(ch2, ('unknown', ch2))

    # 现代终端 VT 转义序列（如 VS Code / Windows Terminal）
    # 方向键发送: \x1b[A \x1b[B \x1b[C \x1b[D
    if ch == b'\x1b':
        # 短暂轮询等待，确保转义序列后续字节已到达缓冲区
        import time as _time
        deadline = _time.time() + 0.1  # 100ms 超时
        while not msvcrt.kbhit() and _time.time() < deadline:
            pass
        if msvcrt.kbhit():          # 后面还有字符 → 是方向键/功能键
            ch2 = msvcrt.getch()
            if ch2 == b'[':
                ch3 = msvcrt.getch()
                m = {b'A': ('up', None), b'B': ('down', None),
                     b'C': ('right', None), b'D': ('left', None),
                     b'H': ('home', None), b'F': ('end', None)}
                return m.get(ch3, ('unknown', ch3))
            # \x1b 后跟非 [，按 Alt+key 处理
            return 'char', ch2.decode('utf-8', errors='replace')
        return 'esc', None           # \x1b 单独 → 真正的 Esc

    try:
        return 'char', ch.decode('utf-8', errors='replace')
    except:
        return 'unknown', ch



class ManualBoardEditor:
    """终端交互式棋盘编辑器。board[r][c]: r=0→顶部(黑底线), r=9→底部(红底线)."""

    def __init__(self, board, playing_red=True, bot=None):
        self.board = [row[:] for row in board]
        self.original_board = [row[:] for row in board]
        self.playing_red = playing_red
        self.bot = bot
        self.cursor_r = 0
        self.cursor_c = 0
        self.H = 10
        self.W = 9
        self._first = True

    def run(self):
        """主循环. 返回修正后的 board 或 None(取消)."""
        self._first = True
        while True:
            self._draw()
            self._first = False
            kt, kv = get_key()

            if kt == 'enter':
                return self.board
            if kt == 'esc':
                return None

            if kt == 'up':       self.cursor_r = max(0, self.cursor_r - 1)
            elif kt == 'down':   self.cursor_r = min(self.H - 1, self.cursor_r + 1)
            elif kt == 'left':   self.cursor_c = max(0, self.cursor_c - 1)
            elif kt == 'right':  self.cursor_c = min(self.W - 1, self.cursor_c + 1)

            elif kt == 'char':
                ch = kv
                if ch in NUM_TO_RED:
                    self.board[self.cursor_r][self.cursor_c] = NUM_TO_RED[ch]
                elif ch in SHIFT_TO_BLK:
                    self.board[self.cursor_r][self.cursor_c] = SHIFT_TO_BLK[ch]
                elif ch == '0':
                    self.board[self.cursor_r][self.cursor_c] = None
                elif ch.upper() == 'C':
                    for r in range(self.H):
                        for c in range(self.W):
                            self.board[r][c] = None
                elif ch.upper() == 'R':
                    self.board = [row[:] for row in self.original_board]
                    self.cursor_r = 0
                    self.cursor_c = 0

            elif kt in ('space', 'delete'):
                self.board[self.cursor_r][self.cursor_c] = None

            if not self._first:
                os.system('cls')

    def _cell_text(self, r, c):
        """生成单个格子的显示文本: ' 车 ' 或 ' 十 '，光标格加青色背景。"""
        p = self.board[r][c]
        cur = (r == self.cursor_r and c == self.cursor_c)

        # 确定显示字符和颜色
        if p is None:
            ch = '十'
            fg = ''
        elif p in RED_PIECES:
            ch = PIECE_CN.get(p, p)
            fg = RED_CLR
        else:
            ch = PIECE_CN.get(p, p)
            fg = DIM_CLR

        if cur:
            # 青色背景 + 前景色 + 字符 + 重置
            return f'{BG_CYAN}{fg} {ch} {RST_CLR}'
        else:
            return f'{fg}  {ch} {RST_CLR}' if fg else f'  {ch} '

    def _count(self):
        rc = bc = 0
        for r in range(self.H):
            for c in range(self.W):
                p = self.board[r][c]
                if p in RED_PIECES: rc += 1
                elif p in BLACK_PIECES: bc += 1
        return rc, bc

    def _draw(self):
        """绘制棋盘 - 与自动识别结果相同的简洁格式。"""
        rc, bc = self._count()
        cl = chr(ord('a') + self.cursor_c)
        cp = self.board[self.cursor_r][self.cursor_c]
        if cp:
            sd = '红方' if cp in RED_PIECES else '黑方'
            st = f"光标: {cl}{self.cursor_r}  |  {sd}{PIECE_CN.get(cp,cp)}({cp})"
        else:
            st = f"光标: {cl}{self.cursor_r}  |  空格"

        print()
        print("=" * 50)
        print(f"  {BOLD}人工检测模式 - 手动修正棋盘{RST_CLR}")
        print(f"  {st}  |  {RED_CLR}红{rc}{RST_CLR} {DIM_CLR}黑{bc}{RST_CLR}")
        print("=" * 50)

        # 列标签
        print("     a  b  c  d  e  f  g  h  i")

        # 棋盘行
        for r in range(self.H):
            line = f"  {r} "
            for c in range(self.W):
                line += self._cell_text(r, c)
            print(line)

        print("=" * 50)
        print()
        print(f"  {WHITE}[1-7]{RST_CLR}红方  {WHITE}[Shift+1-7]{RST_CLR}黑方  {WHITE}[0]{RST_CLR}清除  {WHITE}[C]{RST_CLR}清盘  {WHITE}[R]{RST_CLR}重置")
        print(f"  {WHITE}[Enter]{RST_CLR}确认  {WHITE}[Esc]{RST_CLR}取消")
        print()
        print(f"  {RED_CLR}红方:{RST_CLR} [1]车 [2]马 [3]相 [4]仕 [5]帅 [6]炮 [7]兵")
        print(f"  {DIM_CLR}黑方:{RST_CLR} [1]车 [2]马 [3]象 [4]士 [5]将 [6]炮 [7]卒")
        print()
