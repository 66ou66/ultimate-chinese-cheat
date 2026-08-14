# -*- coding: utf-8 -*-
"""
天天象棋棋盘校准工具 (OpenCV 交互版)

用法: python calibrate_board.py

交互说明:
  左键拖拽 → 重新画矩形框
  右键拖拽 → 平移视图
  滚轮     → 缩放 (1x~5x)，以鼠标位置为锚点
  ↑↓←→     → 平移矩形 ±1px
  Shift+↑↓←→ → 平移矩形 ±5px
  Ctrl+↑↓←→  → 调整右下角 ±1px
  Ctrl+Shift+↑↓←→ → 调整右下角 ±5px
  Enter    → 确认保存并退出
  Esc      → 取消退出（不保存）
  R        → 重置为初始矩形
  H        → 显示/隐藏帮助文字
"""

import sys
import os
import json
import ctypes
import numpy as np
import cv2

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from xiangqi_bot import Bot

# ============================================================
# 常量
# ============================================================
# 默认网格比例（沿用 auto_calibrate 的预设值）
DEFAULT_RX1, DEFAULT_RY1 = 0.2985, 0.1344
DEFAULT_RX2, DEFAULT_RY2 = 0.7027, 0.9052

# 缩放范围
MIN_ZOOM = 1.0
MAX_ZOOM = 5.0
ZOOM_STEP = 1.0          # 滚轮缩放步长
ZOOM_STEP_FINE = 0.25    # +/- 键缩放步长

# 画布 padding（用于平移越界时的黑边）
CANVAS_PAD = 300

# 颜色（BGR）
COLOR_RECT       = (255, 120, 0)    # 蓝色-橙色
COLOR_RECT_FILL  = (255, 200, 100)  # 矩形半透明填充
COLOR_GRID       = (0, 0, 255)      # 红色网格线
COLOR_INTERSECT  = (255, 255, 0)    # 青色交点
COLOR_HANDLE     = (0, 255, 0)      # 绿色四角手柄
COLOR_DRAG_RECT  = (0, 255, 255)    # 黄色拖拽中临时矩形
COLOR_TEXT_WHITE = (255, 255, 255)  # 白色文字
COLOR_TEXT_GRAY  = (180, 180, 180)  # 灰色帮助文字
COLOR_STATUS_BG  = (35, 35, 35)     # 状态栏背景

# 最小矩形尺寸（低于此值不绘制网格）
MIN_RECT_SIZE = 50

# OpenCV 窗口名
WINDOW_NAME = "棋盘校准 - Board Calibration"


# ============================================================
# BoardCalibrator 类
# ============================================================
class BoardCalibrator:
    """交互式棋盘校准器。

    坐标体系：
      截图坐标 (screenshot/physical)  ← rect 存储在此层
          ↑ × zoom + pad − pan
      画布显示坐标 (canvas/display)   ← cv2.imshow 坐标
    """

    def __init__(self, bot, img):
        self.bot = bot
        self.img = img
        self.img_h, self.img_w = img.shape[:2]

        # 初始矩形：默认比例 × 截图尺寸（物理像素）
        self.rect = [
            DEFAULT_RX1 * self.img_w,
            DEFAULT_RY1 * self.img_h,
            DEFAULT_RX2 * self.img_w,
            DEFAULT_RY2 * self.img_h,
        ]
        self.initial_rect = list(self.rect)

        # 视图状态
        self.zoom = 3.0
        self.pan_x = 0.0
        self.pan_y = 0.0

        # 交互状态
        self.dragging = False
        self.drag_start = (0.0, 0.0)       # screenshot 坐标
        self.drag_rect = None               # [x1,y1,x2,y2]
        self.panning = False
        self.pan_start_display = (0, 0)
        self.pan_start_offset = (0.0, 0.0)
        self.show_help = True

    # ----------------------------------------------------------
    # 坐标转换
    # ----------------------------------------------------------
    def screenshot_to_display(self, sx, sy):
        dx = sx * self.zoom + CANVAS_PAD - self.pan_x
        dy = sy * self.zoom + CANVAS_PAD - self.pan_y
        return int(dx), int(dy)

    def display_to_screenshot(self, dx, dy):
        sx = (dx + self.pan_x - CANVAS_PAD) / self.zoom
        sy = (dy + self.pan_y - CANVAS_PAD) / self.zoom
        return sx, sy

    # ----------------------------------------------------------
    # 矩形工具
    # ----------------------------------------------------------
    def clamp_rect(self):
        r = self.rect
        r[0] = max(0.0, min(float(self.img_w), r[0]))
        r[1] = max(0.0, min(float(self.img_h), r[1]))
        r[2] = max(0.0, min(float(self.img_w), r[2]))
        r[3] = max(0.0, min(float(self.img_h), r[3]))
        if r[2] < r[0]:
            r[0], r[2] = r[2], r[0]
        if r[3] < r[1]:
            r[1], r[3] = r[3], r[1]

    def is_valid_rect(self):
        r = self.rect
        return (r[2] - r[0]) >= MIN_RECT_SIZE and (r[3] - r[1]) >= MIN_RECT_SIZE

    # ----------------------------------------------------------
    # 绘制
    # ----------------------------------------------------------
    def build_canvas(self):
        """构建画布：缩放后的图像 + padding 黑边。"""
        disp_w = int(self.img_w * self.zoom)
        disp_h = int(self.img_h * self.zoom)
        zoomed = cv2.resize(self.img, (disp_w, disp_h),
                            interpolation=cv2.INTER_LINEAR)
        canvas_h = disp_h + 2 * CANVAS_PAD
        canvas_w = disp_w + 2 * CANVAS_PAD
        canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
        canvas[CANVAS_PAD:CANVAS_PAD + disp_h,
               CANVAS_PAD:CANVAS_PAD + disp_w] = zoomed
        return canvas

    def draw_overlay(self, canvas):
        """在画布上绘制所有叠加元素。"""
        rl, rt, rr, rb = self.rect

        # 蓝色矩形框（半透明填充 + 实线边框）
        dl, dt = self.screenshot_to_display(rl, rt)
        dr, db = self.screenshot_to_display(rr, rb)
        overlay = canvas.copy()
        cv2.rectangle(overlay, (dl, dt), (dr, db), COLOR_RECT_FILL, -1)
        cv2.addWeighted(overlay, 0.15, canvas, 0.85, 0, canvas)
        cv2.rectangle(canvas, (dl, dt), (dr, db), COLOR_RECT, 2)

        # 拖拽中的临时矩形
        if self.dragging and self.drag_rect:
            drl, drt, drr, drb = self.drag_rect
            ddl, ddt = self.screenshot_to_display(drl, drt)
            ddr, ddb = self.screenshot_to_display(drr, drb)
            cv2.rectangle(canvas, (ddl, ddt), (ddr, ddb), COLOR_DRAG_RECT, 1)

        if not self.is_valid_rect():
            self._draw_status(canvas)
            self._draw_help(canvas)
            return canvas

        cell_w = (rr - rl) / 8.0
        cell_h = (rb - rt) / 9.0

        # 红色网格线
        for i in range(9):
            ix = rl + i * cell_w
            p1 = self.screenshot_to_display(ix, rt)
            p2 = self.screenshot_to_display(ix, rb)
            cv2.line(canvas, p1, p2, COLOR_GRID, 1)
        for j in range(10):
            iy = rt + j * cell_h
            p1 = self.screenshot_to_display(rl, iy)
            p2 = self.screenshot_to_display(rr, iy)
            cv2.line(canvas, p1, p2, COLOR_GRID, 1)

        # 青色交点 × 标记
        cross_sz = max(2, int(self.zoom * 0.8))
        for i in range(9):
            for j in range(10):
                ix, iy = rl + i * cell_w, rt + j * cell_h
                dx, dy = self.screenshot_to_display(ix, iy)
                cv2.line(canvas, (dx - cross_sz, dy - cross_sz),
                         (dx + cross_sz, dy + cross_sz), COLOR_INTERSECT, 1)
                cv2.line(canvas, (dx - cross_sz, dy + cross_sz),
                         (dx + cross_sz, dy - cross_sz), COLOR_INTERSECT, 1)

        # 绿色四角手柄
        handle_sz = max(3, int(self.zoom * 1.2))
        for (cx, cy) in [(rl, rt), (rr, rt), (rl, rb), (rr, rb)]:
            dx, dy = self.screenshot_to_display(cx, cy)
            cv2.rectangle(canvas,
                          (dx - handle_sz, dy - handle_sz),
                          (dx + handle_sz, dy + handle_sz),
                          COLOR_HANDLE, -1)

        self._draw_status(canvas, cell_w, cell_h)
        self._draw_help(canvas)
        return canvas

    def _draw_status(self, canvas, cell_w=None, cell_h=None):
        ch, cw = canvas.shape[:2]
        rl, rt, rr, rb = self.rect
        bar_top = ch - 32
        cv2.rectangle(canvas, (0, bar_top), (cw, ch), COLOR_STATUS_BG, -1)
        line = (f"缩放: {self.zoom:.1f}x  |  "
                f"矩形: ({rl:.0f},{rt:.0f}) -> ({rr:.0f},{rb:.0f})")
        if cell_w is not None and cell_h is not None:
            line += f"  |  网格: {cell_w:.1f} x {cell_h:.1f} px/格"
        cv2.putText(canvas, line, (8, ch - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_TEXT_WHITE, 1)

    def _draw_help(self, canvas):
        if not self.show_help:
            return
        ch, cw = canvas.shape[:2]
        help_lines = [
            "左拖: 画矩形 | 右拖: 平移 | 滚轮: 缩放 | +/-: 微缩放",
            "方向键: 移±1 | Shift+方向键: 移±5 | Ctrl+方向键: 调右下角",
            "Ctrl+Shift+方向键: 调右下角±5 | Enter: 确认 | Esc: 取消",
            "R: 重置 | H: 隐藏帮助",
        ]
        y_base = ch - 38
        for i, line in enumerate(help_lines):
            y = y_base - (len(help_lines) - 1 - i) * 16
            cv2.putText(canvas, line, (8, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, COLOR_TEXT_GRAY, 1)



    # ----------------------------------------------------------
    # 鼠标回调
    # ----------------------------------------------------------
    def mouse_callback(self, event, x, y, flags, param):
        """OpenCV 鼠标事件。x,y 是画布显示坐标。"""
        # 滚轮缩放（以鼠标位置为锚点）
        if event == cv2.EVENT_MOUSEWHEEL:
            if flags > 0:
                new_zoom = min(MAX_ZOOM, self.zoom + ZOOM_STEP)
            else:
                new_zoom = max(MIN_ZOOM, self.zoom - ZOOM_STEP)
            if new_zoom == self.zoom:
                return
            anchor_sx, anchor_sy = self.display_to_screenshot(x, y)
            self.pan_x = (anchor_sx * new_zoom + CANVAS_PAD
                          - (anchor_sx * self.zoom + CANVAS_PAD - self.pan_x))
            self.pan_y = (anchor_sy * new_zoom + CANVAS_PAD
                          - (anchor_sy * self.zoom + CANVAS_PAD - self.pan_y))
            self.zoom = new_zoom
            return

        # 左键拖拽 = 重画矩形
        if event == cv2.EVENT_LBUTTONDOWN:
            self.dragging = True
            sx, sy = self.display_to_screenshot(x, y)
            self.drag_start = (sx, sy)
            self.drag_rect = [sx, sy, sx, sy]

        elif event == cv2.EVENT_MOUSEMOVE:
            if self.dragging:
                sx, sy = self.display_to_screenshot(x, y)
                self.drag_rect = [
                    min(self.drag_start[0], sx),
                    min(self.drag_start[1], sy),
                    max(self.drag_start[0], sx),
                    max(self.drag_start[1], sy)]
            elif self.panning:
                self.pan_x = (self.pan_start_offset[0]
                              - (x - self.pan_start_display[0]))
                self.pan_y = (self.pan_start_offset[1]
                              - (y - self.pan_start_display[1]))

        elif event == cv2.EVENT_LBUTTONUP:
            if self.dragging and self.drag_rect:
                w = self.drag_rect[2] - self.drag_rect[0]
                h = self.drag_rect[3] - self.drag_rect[1]
                if w >= 5 and h >= 5:
                    self.rect = list(self.drag_rect)
                    self.clamp_rect()
            self.dragging = False
            self.drag_rect = None

        # 右键拖拽 = 平移视图
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.panning = True
            self.pan_start_display = (x, y)
            self.pan_start_offset = (self.pan_x, self.pan_y)

        elif event == cv2.EVENT_RBUTTONUP:
            self.panning = False
    # ----------------------------------------------------------
    # 主循环
    # ----------------------------------------------------------
    def run(self):
        """运行交互校准主循环。返回 True=确认, False=取消。"""
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(WINDOW_NAME, self.mouse_callback)
        cv2.resizeWindow(WINDOW_NAME, 1200, 800)

        while True:
            canvas = self.build_canvas()
            canvas = self.draw_overlay(canvas)
            cv2.imshow(WINDOW_NAME, canvas)

            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                cv2.destroyAllWindows()
                return False

            key = cv2.waitKeyEx(30)
            if key == -1:
                continue

            shift = bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
            ctrl  = bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)
            step = 5 if shift else 1

            if key in (13, 0x0D):          # Enter
                cv2.destroyAllWindows()
                return True
            elif key == 27:                 # Esc
                cv2.destroyAllWindows()
                return False
            elif key in (ord('r'), ord('R')):  # 重置
                self.rect = list(self.initial_rect)
                self.zoom = 3.0
                self.pan_x = 0.0
                self.pan_y = 0.0
                continue
            elif key in (ord('h'), ord('H')):
                self.show_help = not self.show_help
                continue
            elif key in (ord('+'), ord('=')):
                self.zoom = min(MAX_ZOOM, self.zoom + ZOOM_STEP_FINE)
                continue
            elif key in (ord('-'), ord('_')):
                self.zoom = max(MIN_ZOOM, self.zoom - ZOOM_STEP_FINE)
                continue

            # 方向键（Windows waitKeyEx 实测值）
            if key == 2490368:              # ↑
                if ctrl:
                    self.rect[3] -= step
                else:
                    self.rect[1] -= step
                    self.rect[3] -= step
            elif key == 2621440:            # ↓
                if ctrl:
                    self.rect[3] += step
                else:
                    self.rect[1] += step
                    self.rect[3] += step
            elif key == 2424832:            # ←
                if ctrl:
                    self.rect[2] -= step
                else:
                    self.rect[0] -= step
                    self.rect[2] -= step
            elif key == 2555904:            # →
                if ctrl:
                    self.rect[2] += step
                else:
                    self.rect[0] += step
                    self.rect[2] += step

            self.clamp_rect()

        cv2.destroyAllWindows()
        return False

    # ----------------------------------------------------------
    # 保存校准
    # ----------------------------------------------------------
    def save_calibration(self):
        """rect（截图物理坐标）→ 屏幕逻辑坐标 → bot.save_calibration()。"""
        dpi = self.bot.dpi_scale
        sl = self.bot.win_x + self.rect[0] / dpi
        st = self.bot.win_y + self.rect[1] / dpi
        sr = self.bot.win_x + self.rect[2] / dpi
        sb = self.bot.win_y + self.rect[3] / dpi

        cw = (sr - sl) / 8.0
        ch = (sb - st) / 9.0
        self.bot.cols_logical = [sl + i * cw for i in range(9)]
        self.bot.rows_logical = [st + j * ch for j in range(10)]
        self.bot.save_calibration()


# ============================================================
# main
# ============================================================
def main():
    print("=" * 55)
    print("  天天象棋棋盘校准工具 (OpenCV 交互版)")
    print("=" * 55)
    print()
    print("  操作说明:")
    print("    左键拖拽       → 重画矩形框")
    print("    右键拖拽       → 平移视图")
    print("    滚轮 / +/-     → 缩放 (1.0x ~ 5.0x)")
    print("    ↑↓←→          → 平移矩形 ±1px")
    print("    Shift+↑↓←→    → 平移矩形 ±5px")
    print("    Ctrl+↑↓←→     → 调整右下角 ±1px")
    print("    Ctrl+Shift+↑↓←→ → 调整右下角 ±5px")
    print("    Enter          → 确认保存")
    print("    Esc            → 取消退出（不修改 calib.json）")
    print("    R              → 重置矩形")
    print("    H              → 显示/隐藏帮助")
    print()

    input("  准备好后按 Enter 开始校准...")

    # 1. 创建 Bot 并查找窗口
    bot = Bot()
    try:
        bot.find_window()
    except RuntimeError as e:
        print(f"\n  ❌ {e}")
        sys.exit(1)

    print(f"\n  窗口: ({bot.win_x},{bot.win_y}) {bot.win_w}x{bot.win_h}, "
          f"DPI={bot.dpi_scale:.1f}")

    # 2. 截屏
    print("  正在截屏...")
    try:
        img = bot.screenshot_for_processing()
    except Exception as e:
        print(f"\n  ❌ 截屏失败: {e}")
        print("  请确保天天象棋窗口完全可见（不被其他窗口遮挡）。")
        sys.exit(1)

    print(f"  截图尺寸: {img.shape[1]}x{img.shape[0]} (物理像素)")
    print()
    print("  提示: 请拖拽蓝色矩形框住棋盘的【最外层网格线区域】")
    print("        蓝色矩形 = 棋盘网格的精确边界")

    # 3. 交互校准
    calibrator = BoardCalibrator(bot, img)
    success = calibrator.run()

    if not success:
        print("\n  ❌ 校准已取消，calib.json 未修改。")
        sys.exit(0)

    # 4. 保存
    calibrator.save_calibration()

    # 5. 打印结果
    cell_w_px = (calibrator.rect[2] - calibrator.rect[0]) / 8.0
    cell_h_px = (calibrator.rect[3] - calibrator.rect[1]) / 9.0
    cell_w = (bot.cols_logical[8] - bot.cols_logical[0]) / 8.0
    cell_h = (bot.rows_logical[9] - bot.rows_logical[0]) / 9.0

    print()
    print("  🎉 校准完成！")
    print(f"  网格参数: {cell_w_px:.1f} x {cell_h_px:.1f} px/格 (截图坐标系)")
    print(f"  屏幕网格: {cell_w:.1f} x {cell_h:.1f} px/格 (逻辑坐标)")
    print(f"  列: [{bot.cols_logical[0]:.0f} .. {bot.cols_logical[8]:.0f}]")
    print(f"  行: [{bot.rows_logical[0]:.0f} .. {bot.rows_logical[9]:.0f}]")

    calib_path = os.path.join(_SCRIPT_DIR, 'calib.json')
    if os.path.exists(calib_path):
        with open(calib_path) as f:
            ratios = json.load(f)
        print(f"  比例: rx1={ratios['rx1']:.4f} ry1={ratios['ry1']:.4f} "
              f"rx2={ratios['rx2']:.4f} ry2={ratios['ry2']:.4f}")

    print()
    print("  现在可以运行:")
    print("     python collect_batch.py")
    print("     python main_fen.py")


if __name__ == '__main__':
    main()


