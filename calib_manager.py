#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验功能模块：多棋盘（校准模板）管理。

背景：人机模式校准好的 calib.json，在实战棋盘/窗口下会失效，每次要重新校准。
本模块把校准模板以 <名称>.json 存放在 xiangqi-bot/calibs/ 下，
选择模板时把其内容复制到 xiangqi-bot/calib.json（当前生效文件），
不改动 Bot / advisor 的加载逻辑，切模板后立即生效。
"""

import os
import sys
import json
import re

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_XIANGQI_BOT_DIR = os.path.join(_SCRIPT_DIR, 'xiangqi-bot')
if _XIANGQI_BOT_DIR not in sys.path:
    sys.path.insert(0, _XIANGQI_BOT_DIR)

DEFAULT_CALIBS_DIR = os.path.join(_XIANGQI_BOT_DIR, 'calibs')
DEFAULT_CALIB_PATH = os.path.join(_XIANGQI_BOT_DIR, 'calib.json')

_INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


def sanitize_name(name):
    """把文件名非法字符替换为下划线，并去掉首尾空白/点。"""
    name = str(name or '').strip()
    name = _INVALID_NAME_CHARS.sub('_', name)
    return name.strip('_. ')


class CalibStore:
    """校准模板存储：list / load / save / delete / rename / activate / import。

    可传入自定义目录，便于单元测试。
    """

    def __init__(self, calibs_dir=None, calib_path=None):
        self.calibs_dir = calibs_dir or DEFAULT_CALIBS_DIR
        self.calib_path = calib_path or DEFAULT_CALIB_PATH
        self.marker_path = os.path.join(self.calibs_dir, '_active.txt')

    def _profile_path(self, name):
        return os.path.join(self.calibs_dir, sanitize_name(name) + '.json')

    # --- 查询 ---

    def list_profiles(self):
        """返回模板名称列表（按名称排序，跳过 _active.txt 等内部文件）。"""
        if not os.path.isdir(self.calibs_dir):
            return []
        names = []
        for fn in os.listdir(self.calibs_dir):
            if fn.startswith('_'):
                continue
            if fn.endswith('.json'):
                names.append(fn[:-len('.json')])
        return sorted(names)

    def load_profile(self, name):
        path = self._profile_path(name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def active_name(self):
        """返回当前生效模板名；无则 None。"""
        if not os.path.exists(self.marker_path):
            return None
        try:
            with open(self.marker_path, 'r', encoding='utf-8') as f:
                name = f.read().strip()
            return name or None
        except Exception:
            return None

    # --- 写操作 ---

    def save_profile(self, name, data):
        name = sanitize_name(name)
        if not name:
            raise ValueError("模板名称不能为空")
        os.makedirs(self.calibs_dir, exist_ok=True)
        path = self._profile_path(name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def delete_profile(self, name):
        path = self._profile_path(name)
        if not os.path.exists(path):
            return False
        os.remove(path)
        if self.active_name() == sanitize_name(name):
            self._clear_active()
        return True

    def rename_profile(self, old_name, new_name):
        old_name = sanitize_name(old_name)
        new_name = sanitize_name(new_name)
        if not new_name:
            raise ValueError("新名称不能为空")
        old_path = self._profile_path(old_name)
        new_path = self._profile_path(new_name)
        if not os.path.exists(old_path):
            return False
        if old_path == new_path:
            return True
        if os.path.exists(new_path):
            raise ValueError(f"名称已存在: {new_name}")
        os.rename(old_path, new_path)
        if self.active_name() == old_name:
            self._write_active(new_name)
        return True

    def activate(self, name):
        """把模板内容复制到 calib.json 并标记为当前生效。"""
        data = self.load_profile(name)
        if data is None:
            return False
        os.makedirs(self.calibs_dir, exist_ok=True)
        with open(self.calib_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self._write_active(sanitize_name(name))
        return True

    def import_current(self, name):
        """把当前 calib.json 内容另存为一个模板（首次进入时保留已有校准）。"""
        if not self.current_exists():
            return False
        try:
            with open(self.calib_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return False
        self.save_profile(name, data)
        return True

    # --- 内部 ---

    def _write_active(self, name):
        os.makedirs(self.calibs_dir, exist_ok=True)
        with open(self.marker_path, 'w', encoding='utf-8') as f:
            f.write(name)

    def _clear_active(self):
        if os.path.exists(self.marker_path):
            try:
                os.remove(self.marker_path)
            except OSError:
                pass

    def current_exists(self):
        """当前 calib.json 是否存在（未被任何模板管理时也仍可使用）。"""
        return os.path.exists(self.calib_path)


def run_calibration():
    """运行 OpenCV 交互式拖框校准（复用 calibrate_board 工具）。

    Returns:
        成功返回相对比例 dict {rx1, ry1, rx2, ry2}；取消/失败返回 None。
    """
    # 延迟导入，避免纯 CRUD 单测时加载 cv2 / pyautogui 等 GUI 依赖
    from xiangqi_bot import Bot
    from calibrate_board import BoardCalibrator

    bot = Bot()
    try:
        bot.find_window()
    except RuntimeError as e:
        print(f"\n  ❌ {e}")
        print("  请先打开天天象棋并进入棋盘界面。")
        return None

    print(f"\n  窗口: ({bot.win_x},{bot.win_y}) {bot.win_w}x{bot.win_h}, "
          f"DPI={bot.dpi_scale:.1f}")

    print("  正在截屏...")
    try:
        img = bot.screenshot_for_processing()
    except Exception as e:
        print(f"\n  ❌ 截屏失败: {e}")
        print("  请确保天天象棋窗口完全可见（不被其他窗口遮挡）。")
        return None

    print(f"  截图尺寸: {img.shape[1]}x{img.shape[0]} (物理像素)")
    print("  提示: 请拖拽蓝色矩形框住棋盘的【最外层网格线区域】")

    calibrator = BoardCalibrator(bot, img)
    ok = calibrator.run()
    if not ok:
        print("\n  ❌ 校准已取消，未保存。")
        return None

    # 与 calibrate_board.BoardCalibrator.save_calibration 完全一致的比例计算
    dpi = bot.dpi_scale
    sl = bot.win_x + calibrator.rect[0] / dpi
    st = bot.win_y + calibrator.rect[1] / dpi
    sr = bot.win_x + calibrator.rect[2] / dpi
    sb = bot.win_y + calibrator.rect[3] / dpi
    ratios = {
        'rx1': (sl - bot.win_x) / bot.win_w,
        'ry1': (st - bot.win_y) / bot.win_h,
        'rx2': (sr - bot.win_x) / bot.win_w,
        'ry2': (sb - bot.win_y) / bot.win_h,
    }
    cell_w = (sr - sl) / 8.0
    cell_h = (sb - st) / 9.0

    print("\n  🎉 校准完成！")
    print(f"  网格参数: {cell_w:.1f} x {cell_h:.1f} px/格 (屏幕逻辑坐标)")
    print(f"  比例: rx1={ratios['rx1']:.4f} ry1={ratios['ry1']:.4f} "
          f"rx2={ratios['rx2']:.4f} ry2={ratios['ry2']:.4f}")
    return ratios


if __name__ == '__main__':
    # 独立运行：仅打印当前模板列表与状态
    store = CalibStore()
    names = store.list_profiles()
    active = store.active_name()
    print("已保存的棋盘模板:")
    if names:
        for n in names:
            mark = "  √ 当前" if n == active else ""
            print(f"  - {n}{mark}")
    else:
        print("  (无)")
    print(f"当前生效: {active or '(calib.json 未被模板管理)'}")

