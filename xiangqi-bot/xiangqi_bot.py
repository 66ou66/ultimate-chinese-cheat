#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Xiangqi Bot - Auto-play 天天象棋 using Pikafish engine.

Usage:
  1. Open 天天象棋 in WeChat, start a game (initial position)
  2. python3 /tmp/xiangqi_bot.py
  3. Follow the calibration prompts (move mouse to 2 corners)
  4. Bot auto-plays! Ctrl+C to stop.
"""

import sys
import time
import os
import json
import numpy as np
import cv2
import pyautogui
import mss
import pygetwindow as gw
import ctypes
from ctypes import wintypes

pyautogui.FAILSAFE = True  # Move mouse to corner to abort

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CALIB_PATH = os.path.join(_SCRIPT_DIR, "calib.json")
TEMPLATE_DIR = os.path.join(_SCRIPT_DIR, "templates")

# Initial board layouts (screen space)
INIT_RED = [
    ['r','n','b','a','k','a','b','n','r'],
    [None]*9,
    [None,'c',None,None,None,None,None,'c',None],
    ['p',None,'p',None,'p',None,'p',None,'p'],
    [None]*9, [None]*9,
    ['P',None,'P',None,'P',None,'P',None,'P'],
    [None,'C',None,None,None,None,None,'C',None],
    [None]*9,
    ['R','N','B','A','K','A','B','N','R'],
]
INIT_BLACK = [
    ['R','N','B','A','K','A','B','N','R'],
    [None]*9,
    [None,'C',None,None,None,None,None,'C',None],
    ['P',None,'P',None,'P',None,'P',None,'P'],
    [None]*9, [None]*9,
    ['p',None,'p',None,'p',None,'p',None,'p'],
    [None,'c',None,None,None,None,None,'c',None],
    [None]*9,
    ['r','n','b','a','k','a','b','n','r'],
]


class Bot:
    def __init__(self):
        self.cols_logical = []
        self.rows_logical = []
        self.cell_w = 0
        self.cell_h = 0
        self.templates = {}
        self.patch_size = 0
        self.playing_red = True
        self.dpi_scale = self._get_windows_dpi_scale()
        self.win_hwnd = None
        self.win_x = 0
        self.win_y = 0
        self.win_w = 0
        self.win_h = 0
        self.cnn = None

    def _get_windows_dpi_scale(self):
        """Get Windows DPI scaling factor."""
        try:
            awareness = ctypes.c_int()
            ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
            hdc = ctypes.windll.user32.GetDC(0)
            dpi_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
            ctypes.windll.user32.ReleaseDC(0, hdc)
            return dpi_x / 96.0
        except Exception:
            return 1.0

    # --- Window & Screenshot ---

    def find_window(self):
        """Find TiantianXiangqi window on Windows."""
        windows = gw.getWindowsWithTitle('')
        for w in windows:
            if hasattr(w, '_hWnd'):
                cn = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(w._hWnd, cn, 256)
                if cn.value == 'QQChess':
                    self.win_hwnd = w._hWnd
                    self.win_x, self.win_y = w.left, w.top
                    self.win_w, self.win_h = w.width, w.height
                    print(f"  找到天天象棋窗口(QQChess): ({self.win_x},{self.win_y}) {self.win_w}x{self.win_h}")
                    return
        for w in windows:
            if not hasattr(w, '_hWnd') or not hasattr(w, 'title'):
                continue
            if '天天象棋' in w.title or '象棋' in w.title:
                self.win_hwnd = w._hWnd
                self.win_x, self.win_y = w.left, w.top
                self.win_w, self.win_h = w.width, w.height
                print(f"  找到窗口({w.title}): ({self.win_x},{self.win_y}) {self.win_w}x{self.win_h}")
                return
        raise RuntimeError("未找到天天象棋窗口，请确认游戏已打开！")

    def screenshot_for_processing(self):
        """Capture window using mss (Windows)."""
        if self.win_w <= 0 or self.win_h <= 0:
            raise RuntimeError("窗口尺寸无效，请先调用 find_window()")
        with mss.mss() as sct:
            screenshot = sct.grab({
                'left': self.win_x, 'top': self.win_y,
                'width': self.win_w, 'height': self.win_h,
            })
            img = np.array(screenshot)
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    def logical_to_pixel(self, lx, ly):
        """Convert logical screen coords to full-res pixel coords in window capture."""
        # Logical coords are absolute screen coords
        # Window capture starts at (win_x, win_y) in logical space
        px = (lx - self.win_x) * self.dpi_scale
        py = (ly - self.win_y) * self.dpi_scale
        return int(px), int(py)

    # --- Calibration ---

    def auto_calibrate(self, img):
        """Auto-detect board grid using CNN. Start from default ratios, fine-tune with grid search."""
        if not self.load_cnn():
            print("  Auto-calibrate: CNN not available")
            return False

        win_w = self._get_window_width()
        win_h = self._get_window_height()

        # Default ratios from known 天天象棋 layout
        DEFAULT_RX1, DEFAULT_RY1 = 0.2985, 0.1344
        DEFAULT_RX2, DEFAULT_RY2 = 0.7027, 0.9052

        def try_grid(rx1, ry1, rx2, ry2):
            """Try a grid configuration, return (total_confidence, non_empty_count)."""
            x1 = self.win_x + rx1 * win_w
            y1 = self.win_y + ry1 * win_h
            x2 = self.win_x + rx2 * win_w
            y2 = self.win_y + ry2 * win_h
            cw = (x2 - x1) / 8.0
            ch = (y2 - y1) / 9.0
            cols = [x1 + i * cw for i in range(9)]
            rows = [y1 + j * ch for j in range(10)]
            # Suppress FEN validation output during grid search
            import io
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                board = self.cnn.parse_board(
                    img, cols, rows, self.dpi_scale,
                    self.win_x, self.win_y, cw, ch)
            finally:
                sys.stdout = old_stdout
            # Score: sum of max confidence for non-empty cells
            total_conf = 0.0
            n_pieces = 0
            for r in range(10):
                for c in range(9):
                    if self.cnn._cell_probs[r][c] is not None:
                        conf = float(self.cnn._cell_probs[r][c].max())
                        if board[r][c] is not None:
                            total_conf += conf
                            n_pieces += 1
            return total_conf, n_pieces

        # First try default ratios
        best_score, best_n = try_grid(DEFAULT_RX1, DEFAULT_RY1, DEFAULT_RX2, DEFAULT_RY2)
        best_params = (DEFAULT_RX1, DEFAULT_RY1, DEFAULT_RX2, DEFAULT_RY2)

        # Fine-tune: grid search around default with small offsets
        STEP = 0.005  # ~0.5% of window size
        for dx in [-STEP, 0, STEP]:
            for dy in [-STEP, 0, STEP]:
                for ds in [-STEP, 0, STEP]:  # scale adjustment
                    if dx == 0 and dy == 0 and ds == 0:
                        continue
                    rx1 = DEFAULT_RX1 + dx
                    ry1 = DEFAULT_RY1 + dy
                    rx2 = DEFAULT_RX2 + dx + ds
                    ry2 = DEFAULT_RY2 + dy + ds
                    score, n = try_grid(rx1, ry1, rx2, ry2)
                    if score > best_score:
                        best_score = score
                        best_n = n
                        best_params = (rx1, ry1, rx2, ry2)

        rx1, ry1, rx2, ry2 = best_params
        x1 = self.win_x + rx1 * win_w
        y1 = self.win_y + ry1 * win_h
        x2 = self.win_x + rx2 * win_w
        y2 = self.win_y + ry2 * win_h

        self.cell_w = (x2 - x1) / 8.0
        self.cell_h = (y2 - y1) / 9.0
        self.cols_logical = [x1 + i * self.cell_w for i in range(9)]
        self.rows_logical = [y1 + j * self.cell_h for j in range(10)]

        print(f"  Auto-calibrate (CNN): {best_n} pieces, conf={best_score:.1f}, cell={self.cell_w:.1f}x{self.cell_h:.1f}")

        # Save calibration
        with open(CALIB_PATH, 'w') as f:
            json.dump({'rx1': rx1, 'ry1': ry1, 'rx2': rx2, 'ry2': ry2}, f)

        if best_n < 10:
            print("  Auto-calibrate: too few pieces detected, may be inaccurate")
            return False
        return True

    def calibrate(self):
        """手动标定：用户用鼠标指向棋盘左上角和右下角棋子中心，标定后自动验证。"""
        print("\n" + "=" * 55)
        print("  棋盘手动标定")
        print("=" * 55)
        print()
        print("  请确保天天象棋窗口已打开，显示完整开局棋盘（32子）")
        print()
        
        # --- 第一步：点击左上角 ---
        print("  [1/3] 将鼠标移到棋盘【左上角棋子中心】")
        print("        （红方9路车，即棋盘左上角的車）")
        print()
        for i in range(5, 0, -1):
            print(f"        倒计时 {i} 秒...", end="\r")
            time.sleep(1)
        x1, y1 = pyautogui.position()
        print(f"  ✓ 左上角: 屏幕坐标 ({x1}, {y1})                    ")
        print()
        
        # --- 第二步：点击右下角 ---
        print("  [2/3] 将鼠标移到棋盘【右下角棋子中心】")
        print("        （红方1路车，即棋盘右下角的車）")
        print()
        for i in range(5, 0, -1):
            print(f"        倒计时 {i} 秒...", end="\r")
            time.sleep(1)
        x2, y2 = pyautogui.position()
        print(f"  ✓ 右下角: 屏幕坐标 ({x2}, {y2})                    ")
        print()
        
        # --- 计算网格 ---
        self.cell_w = (x2 - x1) / 8.0
        self.cell_h = (y2 - y1) / 9.0
        self.cols_logical = [x1 + i * self.cell_w for i in range(9)]
        self.rows_logical = [y1 + j * self.cell_h for j in range(10)]
        
        print(f"  网格参数: 格子尺寸 {self.cell_w:.1f}×{self.cell_h:.1f} px")
        print(f"  列范围:   [{self.cols_logical[0]:.0f} .. {self.cols_logical[8]:.0f}]")
        print(f"  行范围:   [{self.rows_logical[0]:.0f} .. {self.rows_logical[9]:.0f}]")
        
        # --- 第三步：验证网格 ---
        print()
        print("  [3/3] 正在验证网格...")
        img = self.screenshot_for_processing()
        ok = self.validate_and_report_grid(img)
        
        if not ok:
            print()
            print("  ❌ 标定验证失败！可能原因：")
            print("     1. 鼠标没有精确指向棋子中心")
            print("     2. 窗口在倒计时期间移动了")
            print("     3. 天天象棋窗口不完全可见")
            print()
            print("  请重新运行标定。")
            return False
        
        # --- 保存标定 ---
        win_w = self._get_window_width()
        win_h = self._get_window_height()
        with open(CALIB_PATH, 'w') as f:
            json.dump({
                'rx1': (x1 - self.win_x) / win_w,
                'ry1': (y1 - self.win_y) / win_h,
                'rx2': (x2 - self.win_x) / win_w,
                'ry2': (y2 - self.win_y) / win_h,
            }, f)
        print(f"\n  ✓ 标定已保存到: {CALIB_PATH}")
        print(f"  ✓ 现在可以运行: python collect_batch.py")
        print("=" * 55)
        return True
    def save_calibration(self):
        """Save current calibration to file."""
        win_w = self._get_window_width()
        win_h = self._get_window_height()
        x1 = self.cols_logical[0]
        x2 = self.cols_logical[8]
        y1 = self.rows_logical[0]
        y2 = self.rows_logical[9]
        with open(CALIB_PATH, 'w') as fh:
            json.dump({
                'rx1': (x1 - self.win_x) / win_w,
                'ry1': (y1 - self.win_y) / win_h,
                'rx2': (x2 - self.win_x) / win_w,
                'ry2': (y2 - self.win_y) / win_h,
            }, fh)
        print(f"  校准已保存到: {CALIB_PATH}")

    def _get_window_width(self):
        return self.win_w

    def _get_window_height(self):
        return self.win_h

    def load_calibration(self):
        """Load calibration, auto-adapt to current window size/position."""
        if not os.path.exists(CALIB_PATH):
            return False
        try:
            with open(CALIB_PATH) as f:
                d = json.load(f)

            # Support both relative (new) and absolute (old) formats
            if 'rx1' in d:
                win_w = self._get_window_width()
                win_h = self._get_window_height()
                x1 = self.win_x + d['rx1'] * win_w
                y1 = self.win_y + d['ry1'] * win_h
                x2 = self.win_x + d['rx2'] * win_w
                y2 = self.win_y + d['ry2'] * win_h
            else:
                x1, y1, x2, y2 = d['x1'], d['y1'], d['x2'], d['y2']
                if d.get('win_x') != self.win_x or d.get('win_y') != self.win_y:
                    dx = self.win_x - d.get('win_x', 0)
                    dy = self.win_y - d.get('win_y', 0)
                    x1 += dx; y1 += dy; x2 += dx; y2 += dy

            self.cell_w = (x2 - x1) / 8.0
            self.cell_h = (y2 - y1) / 9.0
            self.cols_logical = [x1 + i * self.cell_w for i in range(9)]
            self.rows_logical = [y1 + j * self.cell_h for j in range(10)]
            print(f"  Loaded calibration: cell={self.cell_w:.1f}x{self.cell_h:.1f}")
            return True
        except Exception:
            return False

    def validate_grid(self, img):
        """验证当前网格标定：检查90个格子有多少能裁剪出有效图像块。
        
        Returns:
            (valid_count, total_count, details_dict)
            details_dict: {(r,c): patch_size} 只记录失败的格子
        """
        total = 0
        valid = 0
        failed_cells = {}
        
        radius = int(min(self.cell_w, self.cell_h) * self.dpi_scale * 0.30)
        h_img, w_img = img.shape[:2]
        
        for r in range(10):
            for c in range(9):
                total += 1
                px, py = self.logical_to_pixel(self.cols_logical[c], self.rows_logical[r])
                
                x1 = max(0, px - radius)
                y1 = max(0, py - radius)
                x2 = min(w_img, px + radius)
                y2 = min(h_img, py + radius)
                
                pw = x2 - x1
                ph = y2 - y1
                
                if pw >= 20 and ph >= 20:
                    valid += 1
                else:
                    failed_cells[(r, c)] = (pw, ph)
        
        return valid, total, failed_cells
    
    def validate_and_report_grid(self, img):
        """验证网格并打印详细报告。返回 True 表示标定合格。"""
        valid, total, failed = self.validate_grid(img)
        ratio = valid / total * 100
        
        print(f"\n  ╔══════════════════════════════╗")
        print(f"  ║  网格验证结果                ║")
        print(f"  ╠══════════════════════════════╣")
        print(f"  ║  有效格子: {valid:3d} / {total}  ({ratio:.0f}%)    ║")
        
        if ratio >= 85:
            print(f"  ║  状态: ✓ 标定合格            ║")
            print(f"  ╚══════════════════════════════╝")
            return True
        elif ratio >= 60:
            print(f"  ║  状态: ⚠ 标定勉强可用        ║")
            print(f"  ╚══════════════════════════════╝")
            print(f"  ⚠ 以下格子失败 (尺寸<20px):")
            for (r, c), (pw, ph) in sorted(failed.items()):
                print(f"     r{r}c{c}: {pw}x{ph}")
            return True  # 勉强允许
        else:
            print(f"  ║  状态: ✗ 标定失败!           ║")
            print(f"  ╚══════════════════════════════╝")
            print(f"  ✗ 失败格子 ({len(failed)} 个):")
            for (r, c), (pw, ph) in sorted(failed.items()):
                print(f"     r{r}c{c}: {pw}x{ph}")
            return False


    # --- Orientation & Templates ---

    def detect_orientation(self, img):
        """Compare red-color ratio on top vs bottom row to determine orientation.

        If bottom row has MORE red than top row -> red pieces at bottom -> user plays RED.
        If top row has MORE red than bottom row -> red pieces at top -> user plays BLACK.
        """
        ps = int(min(self.cell_w, self.cell_h) * self.dpi_scale * 0.45)
        h_img, w_img = img.shape[:2]

        def _row_red_ratio(row_idx):
            """Return average red-pixel ratio across key columns of a given grid row."""
            ratios = []
            for ci in [0, 4, 8]:
                px, py = self.logical_to_pixel(self.cols_logical[ci],
                                                self.rows_logical[row_idx])
                y1, y2 = max(0, py - ps), min(h_img, py + ps)
                x1, x2 = max(0, px - ps), min(w_img, px + ps)
                patch = img[y1:y2, x1:x2]
                if patch.size == 0:
                    ratios.append(0.0)
                    continue
                hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
                # Wider hue ranges to catch different red shades
                m1 = cv2.inRange(hsv, (0, 50, 50), (15, 255, 255))
                m2 = cv2.inRange(hsv, (165, 50, 50), (180, 255, 255))
                red_px = cv2.countNonZero(m1) + cv2.countNonZero(m2)
                ratios.append(red_px / max(1, patch.size // 3))
            return sum(ratios) / max(1, len(ratios))

        top_red = _row_red_ratio(0)
        bot_red = _row_red_ratio(9)

        # User plays RED if bottom row has more red than top row
        self.playing_red = bot_red > top_red

        print(f"  Orientation: top_red={top_red:.4f}  bot_red={bot_red:.4f}  "
              f"->  You play {'RED' if self.playing_red else 'BLACK'}")

    def capture_templates(self, img):
        """Capture templates from ALL initial positions (multiple per piece)."""
        os.makedirs(TEMPLATE_DIR, exist_ok=True)
        init = INIT_RED if self.playing_red else INIT_BLACK
        ps = int(min(self.cell_w, self.cell_h) * self.dpi_scale * 0.7)
        self.patch_size = ps
        self.templates = {}  # piece -> [tmpl1, tmpl2, ...]

        for r in range(10):
            for c in range(9):
                piece = init[r][c]
                if piece is None:
                    continue
                px, py = self.logical_to_pixel(self.cols_logical[c], self.rows_logical[r])
                patch = self._extract(img, px, py, ps)
                if patch is not None:
                    if piece not in self.templates:
                        self.templates[piece] = []
                    self.templates[piece].append(patch)

        # Empty cells from many positions (including edges)
        self.templates['_'] = []
        for er in [1, 4, 5, 8]:  # rows that are fully empty
            for ec in range(9):
                px, py = self.logical_to_pixel(self.cols_logical[ec], self.rows_logical[er])
                ep = self._extract(img, px, py, ps)
                if ep is not None:
                    self.templates['_'].append(ep)

        total = sum(len(v) for k, v in self.templates.items() if k != '_')
        print(f"  Templates: {len(self.templates)} types, {total} variants")

    def _extract(self, img, cx, cy, ps):
        h, w = img.shape[:2]
        x1, y1 = max(0, cx-ps), max(0, cy-ps)
        x2, y2 = min(w, cx+ps), min(h, cy+ps)
        p = img[y1:y2, x1:x2]
        return p if p.shape[0] >= ps and p.shape[1] >= ps else None

    # --- Color-based Piece Classifier (v2) ---

    def _extract_piece_center(self, img, px, py, radius=None):
        """Extract the circular piece region centered at (px, py)."""
        if radius is None:
            radius = int(min(self.cell_w, self.cell_h) * self.dpi_scale * 0.28)
        h, w = img.shape[:2]
        x1 = max(0, px - radius)
        y1 = max(0, py - radius)
        x2 = min(w, px + radius)
        y2 = min(h, py + radius)
        patch = img[y1:y2, x1:x2]
        if patch.shape[0] < radius or patch.shape[1] < radius:
            return None
        return patch

    def _find_piece_circle(self, img, px, py):
        """Detect the circular piece token using Hough circles.

        Returns (cx, cy, cr) in image coordinates if found, or None.
        The circle center is in ABSOLUTE image coordinates.
        """
        scale = min(self.cell_w, self.cell_h) * self.dpi_scale
        search_r = int(scale * 0.55)
        piece_r_min = int(scale * 0.22)
        piece_r_max = int(scale * 0.42)
        center_tol = int(scale * 0.25)

        h, w = img.shape[:2]
        x1 = max(0, px - search_r)
        y1 = max(0, py - search_r)
        x2 = min(w, px + search_r)
        y2 = min(h, py + search_r)
        patch = img[y1:y2, x1:x2]

        if patch.shape[0] < 30 or patch.shape[1] < 30:
            return None

        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        ph, pw = gray.shape[:2]

        # Try multiple blur sizes and Hough sensitivity levels
        for blur_k in [7, 5, 9]:
            blurred = cv2.GaussianBlur(gray, (blur_k, blur_k), 2.0)
            for p1, p2 in [(70, 30), (60, 25), (50, 22), (80, 35)]:
                circles = cv2.HoughCircles(
                    blurred, cv2.HOUGH_GRADIENT, dp=1.2,
                    minDist=search_r,
                    param1=p1, param2=p2,
                    minRadius=piece_r_min,
                    maxRadius=piece_r_max
                )
                if circles is not None:
                    # Find circle closest to patch center
                    best_circ = None
                    best_dist = float('inf')
                    for circ in circles[0]:
                        cx, cy, cr = circ
                        dist = np.sqrt((cx - pw / 2) ** 2 + (cy - ph / 2) ** 2)
                        if dist < center_tol and dist < best_dist:
                            best_dist = dist
                            best_circ = circ
                    if best_circ is not None:
                        abs_cx = int(best_circ[0] + x1)
                        abs_cy = int(best_circ[1] + y1)
                        abs_cr = int(best_circ[2])
                        return (abs_cx, abs_cy, abs_cr)
        return None

    def _has_piece_v2(self, img, px, py):
        """Check if the cell at (px, py) has a piece.

        Uses Hough circle detection as primary method, with a color-based
        fallback that checks for the characteristic piece ring color.
        """
        # Primary: Hough circle detection
        circle = self._find_piece_circle(img, px, py)
        if circle is not None:
            return True

        # Fallback: check for red/dark ring pixels in an annular region
        # This catches pieces that Hough misses (edge cases, partial visibility)
        scale = min(self.cell_w, self.cell_h) * self.dpi_scale
        outer_r = int(scale * 0.35)
        inner_r = int(scale * 0.22)

        h, w = img.shape[:2]
        x1 = max(0, px - outer_r)
        y1 = max(0, py - outer_r)
        x2 = min(w, px + outer_r)
        y2 = min(h, py + outer_r)
        patch = img[y1:y2, x1:x2]
        if patch.shape[0] < outer_r or patch.shape[1] < outer_r:
            return False

        ph, pw = patch.shape[:2]
        pcx, pcy = pw // 2, ph // 2

        # Create annular mask (ring where piece border would be)
        mask_outer = np.zeros((ph, pw), dtype=np.uint8)
        mask_inner = np.zeros((ph, pw), dtype=np.uint8)
        cv2.circle(mask_outer, (pcx, pcy), outer_r, 255, -1)
        cv2.circle(mask_inner, (pcx, pcy), inner_r, 255, -1)
        ring_mask = cv2.subtract(mask_outer, mask_inner)

        # Check for red ring pixels (red piece borders)
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        red_mask1 = cv2.inRange(hsv, (0, 80, 100), (12, 255, 200))
        red_mask2 = cv2.inRange(hsv, (168, 80, 100), (180, 255, 200))
        red_ring = cv2.bitwise_and(cv2.bitwise_or(red_mask1, red_mask2), ring_mask)
        red_count = cv2.countNonZero(red_ring)

        # Check for dark ring pixels (black piece borders)
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        dark_ring_vals = gray[ring_mask > 0]
        dark_count = int(np.sum(dark_ring_vals < 100)) if len(dark_ring_vals) > 0 else 0

        ring_total = max(1, cv2.countNonZero(ring_mask))
        red_ratio = red_count / ring_total
        dark_ratio = dark_count / ring_total

        # A piece border needs both red/dark ring AND bright center (piece token is convex)
        # Be strict to avoid false positives from board borders and adjacent piece edges
        center_mask = np.zeros((ph, pw), dtype=np.uint8)
        cv2.circle(center_mask, (pcx, pcy), int(scale * 0.10), 255, -1)
        center_vals = gray[center_mask > 0]
        center_brightness = float(np.mean(center_vals)) if len(center_vals) > 0 else 0
        ring_brightness = float(np.mean(dark_ring_vals)) if len(dark_ring_vals) > 0 else 0

        has_ring = red_ratio > 0.15 or dark_ratio > 0.25
        has_bright_center = center_brightness > 140 and center_brightness > ring_brightness + 5

        return has_ring and has_bright_center

    def _classify_color_v2(self, img, px, py):
        """Classify a piece as RED or BLACK based on text/ring color.

        Red pieces have red-colored Chinese characters and ring on a tan background.
        Black pieces have dark/black characters and ring on a tan background.

        Returns 'red' or 'black'.
        """
        # Use the piece center for color analysis
        radius = int(min(self.cell_w, self.cell_h) * self.dpi_scale * 0.22)

        # Try to use actual circle center if found
        circle = self._find_piece_circle(img, px, py)
        if circle is not None:
            px, py = circle[0], circle[1]

        h, w = img.shape[:2]
        x1 = max(0, px - radius)
        y1 = max(0, py - radius)
        x2 = min(w, px + radius)
        y2 = min(h, py + radius)
        patch = img[y1:y2, x1:x2]
        if patch.size == 0:
            return 'unknown'

        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)

        # Create circular mask
        ph, pw = patch.shape[:2]
        mask = np.zeros((ph, pw), dtype=np.uint8)
        cv2.circle(mask, (pw // 2, ph // 2), min(ph, pw) // 2, 255, -1)

        # Count red pixels (text/ring of red pieces)
        red_mask1 = cv2.inRange(hsv, (0, 70, 80), (12, 255, 255))
        red_mask2 = cv2.inRange(hsv, (168, 70, 80), (180, 255, 255))
        red_mask = cv2.bitwise_and(cv2.bitwise_or(red_mask1, red_mask2), mask)
        red_count = cv2.countNonZero(red_mask)

        # Count dark pixels (text/ring of black pieces)
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        dark_vals = gray[mask > 0]
        dark_count = int(np.sum(dark_vals < 80)) if len(dark_vals) > 0 else 0

        total = max(1, cv2.countNonZero(mask))
        red_ratio = red_count / total
        dark_ratio = dark_count / total

        if red_ratio > 0.03:
            return 'red'
        elif dark_ratio > 0.03:
            return 'black'
        else:
            return 'unknown'

    def _compute_feature_vector(self, img, px, py):
        """Compute a structural feature vector from the character on a piece.

        Uses multiple complementary features:
        1. Stroke density in a grid (captures overall character shape)
        2. Horizontal and vertical edge density (captures stroke orientations)
        3. Multi-scale: coarse (4x4) and fine (6x6) grids

        Returns a normalized feature vector.
        """
        radius = int(min(self.cell_w, self.cell_h) * self.dpi_scale * 0.18)

        # Use actual circle center if found
        circle = self._find_piece_circle(img, px, py)
        if circle is not None:
            px, py = circle[0], circle[1]

        h, w = img.shape[:2]
        x1 = max(0, px - radius)
        y1 = max(0, py - radius)
        x2 = min(w, px + radius)
        y2 = min(h, py + radius)
        patch = img[y1:y2, x1:x2]
        if patch.shape[0] < radius or patch.shape[1] < radius:
            return None

        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float32)
        ph, pw = gray.shape

        # Create circular mask
        mask = np.zeros((ph, pw), dtype=np.float32)
        cv2.circle(mask, (pw // 2, ph // 2), min(ph, pw) // 2, 1.0, -1)

        # Normalize brightness within mask
        masked_pixels = gray[mask > 0]
        if len(masked_pixels) == 0:
            return None
        local_mean = np.mean(masked_pixels)
        local_std = np.std(masked_pixels)
        if local_std < 5:
            return None

        # Normalized grayscale (mean-subtracted, std-normalized)
        norm_gray = (gray - local_mean) / local_std
        norm_gray = norm_gray * mask

        # Compute edge maps for orientation features
        sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3) * mask
        sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3) * mask

        features = []

        # Feature set 1: normalized brightness in grid cells (captures character structure)
        for gs in [4, 6]:  # multi-scale
            for gi in range(gs):
                for gj in range(gs):
                    y_start = int(gi * ph / gs)
                    y_end = int((gi + 1) * ph / gs)
                    x_start = int(gj * pw / gs)
                    x_end = int((gj + 1) * pw / gs)
                    region = norm_gray[y_start:y_end, x_start:x_end]
                    region_mask = mask[y_start:y_end, x_start:x_end]
                    mask_sum = np.sum(region_mask)
                    if mask_sum > 0:
                        features.append(float(np.sum(region) / mask_sum))
                    else:
                        features.append(0.0)

        # Feature set 2: horizontal vs vertical edge balance in grid cells
        for gi in range(4):
            for gj in range(4):
                y_start = int(gi * ph / 4)
                y_end = int((gi + 1) * ph / 4)
                x_start = int(gj * pw / 4)
                x_end = int((gj + 1) * pw / 4)

                sx = np.abs(sobel_x[y_start:y_end, x_start:x_end])
                sy = np.abs(sobel_y[y_start:y_end, x_start:x_end])
                region_mask = mask[y_start:y_end, x_start:x_end]
                mask_sum = np.sum(region_mask)
                if mask_sum > 0:
                    # Ratio of vertical to horizontal edges
                    sx_sum = float(np.sum(sx * region_mask))
                    sy_sum = float(np.sum(sy * region_mask))
                    total_edge = sx_sum + sy_sum + 1e-8
                    features.append(sx_sum / total_edge)  # horizontal dominance
                else:
                    features.append(0.5)

        vec = np.array(features, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 1e-8:
            vec = vec / norm
        return vec

    def _cosine_similarity(self, v1, v2):
        """Compute cosine similarity between two vectors."""
        n1 = np.linalg.norm(v1)
        n2 = np.linalg.norm(v2)
        if n1 < 1e-8 or n2 < 1e-8:
            return 0.0
        return float(np.dot(v1, v2) / (n1 * n2))

    def capture_feature_vectors(self, img):
        """Capture feature vectors from known piece positions in the initial layout.

        This builds self.piece_features: dict mapping piece code -> list of feature vectors
        and self.piece_color_map: dict mapping piece code -> 'red' or 'black'
        """
        init = INIT_RED if self.playing_red else INIT_BLACK
        self.piece_features = {}  # piece -> [vec1, vec2, ...]
        self.piece_color_map = {}

        for r in range(10):
            for c in range(9):
                piece = init[r][c]
                if piece is None:
                    continue
                px, py = self.logical_to_pixel(self.cols_logical[c], self.rows_logical[r])
                vec = self._compute_feature_vector(img, px, py)
                if vec is not None:
                    if piece not in self.piece_features:
                        self.piece_features[piece] = []
                    self.piece_features[piece].append(vec)
                    # Map piece to color
                    if piece.isupper():
                        self.piece_color_map[piece] = 'red' if self.playing_red else 'black'
                    else:
                        self.piece_color_map[piece] = 'black' if self.playing_red else 'red'

        total = sum(len(v) for v in self.piece_features.values())
        print(f"  Feature vectors: {len(self.piece_features)} types, {total} vectors")

    def capture_feature_vectors_from_board(self, img, board):
        """Rebuild feature vectors from current known board state.

        Uses only cells where we confidently know what piece is there.
        """
        self.piece_features = {}
        for r in range(10):
            for c in range(9):
                piece = board[r][c]
                if piece is None:
                    continue
                px, py = self.logical_to_pixel(self.cols_logical[c], self.rows_logical[r])
                vec = self._compute_feature_vector(img, px, py)
                if vec is not None:
                    if piece not in self.piece_features:
                        self.piece_features[piece] = []
                    self.piece_features[piece].append(vec)

    def identify_v2(self, img, px, py):
        """Identify the piece at pixel position (px, py) using color-based classification.

        Returns piece code (e.g. 'R', 'n', 'K') or None if empty.

        Algorithm:
        1. Check if cell has a piece (Hough circle + edge density fallback)
        2. Classify red vs black via HSV analysis
        3. Match character structure via stroke-density feature vector similarity
        """
        # Step 1: Is there a piece here?
        if not self._has_piece_v2(img, px, py):
            return None

        # Step 2: Red or Black?
        color = self._classify_color_v2(img, px, py)

        # Step 3: Match type via feature vector
        vec = self._compute_feature_vector(img, px, py)
        if vec is None:
            return None

        if not hasattr(self, 'piece_features') or not self.piece_features:
            return None

        # Filter candidates by color
        candidates = {}
        for piece, vecs in self.piece_features.items():
            piece_color = self.piece_color_map.get(piece, 'unknown')
            if piece_color == color or color == 'unknown':
                candidates[piece] = vecs

        if not candidates:
            # Fall back to all pieces
            candidates = self.piece_features

        # Find best match by cosine similarity
        best_piece = None
        best_sim = -1.0

        for piece, vecs in candidates.items():
            for ref_vec in vecs:
                sim = self._cosine_similarity(vec, ref_vec)
                if sim > best_sim:
                    best_sim = sim
                    best_piece = piece

        # Threshold check
        if best_sim < 0.60:
            return None

        return best_piece

    def parse_board_v2(self, img):
        """Parse the full board using identify_v2."""
        board = []
        for r in range(10):
            row = []
            for c in range(9):
                px, py = self.logical_to_pixel(self.cols_logical[c], self.rows_logical[r])
                p = self.identify_v2(img, px, py)
                row.append(p)
            board.append(row)
        return board

    # --- Move validation helpers ---


    def _masked_corr(self, patch, tmpl, mask):
        """Compute normalized correlation within circular mask."""
        p = patch[mask > 0].astype(np.float32)
        t = tmpl[mask > 0].astype(np.float32)
        if len(p) == 0 or p.std() < 1 or t.std() < 1:
            return 0.0
        pn = (p - p.mean()) / p.std()
        tn = (t - t.mean()) / t.std()
        return float(np.mean(pn * tn))

    def identify(self, img, px, py):
        patch = self._extract(img, px, py, self.patch_size)
        if patch is None:
            return None
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        if gray.std() < 5:
            return None

        h, w = patch.shape[:2]
        # Circular mask 閳?only compare inside the piece, ignore background
        mask = np.zeros((h, w), dtype=np.uint8)
        r = int(min(h, w) * 0.32)
        cv2.circle(mask, (w//2, h//2), r, 255, -1)
        # 3-channel mask for color images
        mask3 = cv2.merge([mask, mask, mask])

        best_piece, best_piece_sc = None, 0.0
        best_empty_sc = 0.0

        for pc, tmpls in self.templates.items():
            for tmpl in tmpls:
                t = cv2.resize(tmpl, (w, h))
                sc = self._masked_corr(patch, t, mask)
                if pc == '_':
                    best_empty_sc = max(best_empty_sc, sc)
                elif sc > best_piece_sc:
                    best_piece_sc, best_piece = sc, pc

        if best_empty_sc > best_piece_sc:
            return None
        if best_piece_sc - best_empty_sc < 0.08:
            return None
        return best_piece if best_piece_sc > 0.3 else None

    def parse_board(self, img):
        board = []
        for r in range(10):
            row = []
            for c in range(9):
                px, py = self.logical_to_pixel(self.cols_logical[c], self.rows_logical[r])
                p = self.identify(img, px, py)
                row.append(p)
            board.append(row)
        return board

    def _cell_has_piece_now(self, img, col, row):
        """Check if a cell currently has a piece using brightness."""
        px, py = self.logical_to_pixel(self.cols_logical[col], self.rows_logical[row])
        ps = self.patch_size // 2  # smaller region for center check
        h, w = img.shape[:2]
        x1, y1 = max(0, px-ps), max(0, py-ps)
        x2, y2 = min(w, px+ps), min(h, py+ps)
        patch = img[y1:y2, x1:x2]
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        # Pieces are brighter circles with text = higher std
        return gray.std() > 25


    def _fen_to_board(self, fen):
        """Convert FEN string to 10x9 board array."""
        rows = fen.split('/')
        if len(rows) != 10:
            return None
        board = []
        for row_str in rows:
            row = []
            for ch in row_str:
                if ch.isdigit():
                    row.extend([None] * int(ch))
                else:
                    row.append(ch)
            if len(row) != 9:
                return None
            board.append(row)
        if not self.playing_red:
            board = [row[::-1] for row in reversed(board)]
        return board







    def board_to_fen(self, board):
        if self.playing_red:
            fb = board
        else:
            fb = [row[::-1] for row in reversed(board)]
        parts = []
        for row in fb:
            s, e = "", 0
            for p in row:
                if p is None:
                    e += 1
                else:
                    if e: s += str(e); e = 0
                    s += p
            if e: s += str(e)
            parts.append(s)
        return "/".join(parts)


    # --- CNN ---

    def load_cnn(self):
        """Load CNN piece classifier if available."""
        if self.cnn:
            return True
        try:
            from xiangqi_cnn import PieceClassifierCNN, MODEL_PATH
            if os.path.exists(MODEL_PATH):
                self.cnn = PieceClassifierCNN(MODEL_PATH)
                print("  CNN模型已加载 (PyTorch)")
                return True
        except Exception as e:
            print(f"  CNN加载失败: {e}")
        return False

    def parse_board_cnn(self, img):
        """Parse entire board using CNN with double-shot for low confidence cells."""
        if not self.cnn:
            return None
        board = self.cnn.parse_board(
            img, self.cols_logical, self.rows_logical,
            self.dpi_scale, self.win_x, self.win_y,
            self.cell_w, self.cell_h)
        LOW_CONF = 0.90
        needs_retry = False
        for r in range(10):
            for c in range(9):
                if board[r][c] is not None and self.cnn._cell_probs[r][c] is not None:
                    if float(self.cnn._cell_probs[r][c].max()) < LOW_CONF:
                        needs_retry = True
                        break
            if needs_retry:
                break
        if needs_retry:
            probs1 = [[p.copy() if p is not None else None for p in row]
                      for row in self.cnn._cell_probs]
            time.sleep(0.3)
            img2 = self.screenshot_for_processing()
            board = self.cnn.parse_board(
                img2, self.cols_logical, self.rows_logical,
                self.dpi_scale, self.win_x, self.win_y,
                self.cell_w, self.cell_h)
            try:
                from xiangqi_cnn import CLASSES
            except ImportError:
                CLASSES = ['_','R','N','B','A','K','C','P','r','n','b','a','k','c','p']
            changes = 0
            for r in range(10):
                for c in range(9):
                    p1 = probs1[r][c]
                    p2 = self.cnn._cell_probs[r][c]
                    if p1 is None or p2 is None:
                        continue
                    if float(p1.max()) < LOW_CONF or float(p2.max()) < LOW_CONF:
                        avg = (p1 + p2) / 2
                        pred = int(avg.argmax())
                        cls = CLASSES[pred]
                        new_piece = None if cls == '_' else cls
                        if new_piece != board[r][c]:
                            changes += 1
                        board[r][c] = new_piece
                        self.cnn._cell_probs[r][c] = avg
            if changes:
                print(f"    Double-shot: corrected {changes} cell(s)")
                board = self.cnn._validate_board(board)
        return board

    def collect_training_data(self, img, board):
        """Collect labeled cell patches for CNN training."""
        from xiangqi_cnn import collect_from_screenshot
        import time
        session_id = int(time.time())
        return collect_from_screenshot(
            img, self.cols_logical, self.rows_logical, board,
            self.dpi_scale, self.win_x, self.win_y,
            self.cell_w, self.cell_h, session_id)
