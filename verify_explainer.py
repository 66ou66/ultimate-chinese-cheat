#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 解说验证：不依赖屏幕/引擎，直接调用 DeepSeek API。

用法：python verify_explainer.py

会真实消耗一次 DeepSeek API 调用额度。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from explainer import DeepSeekExplainer  # noqa: E402

FEN_INIT = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1'


def main():
    explainer = DeepSeekExplainer()
    if not explainer.available():
        print("❌ 未配置密钥，AI 解说不可用。")
        return 1

    print(f"[AI] API_URL={explainer.API_URL}")
    print(f"[AI] MODEL  ={explainer.model}")
    print(f"[AI] 密钥前 8 位: {explainer.api_key[:8]}...")

    text = explainer.explain(
        fen=FEN_INIT,
        bestmove_cn="炮二平五 (h2e2)",
        bestmove_uci="h2e2",
        score="+0 分",
        pv_lines=[
            "#1 炮二平五 (h2e2) 马8进7 (b0c2) 车一平二 (h9h7) (+0 分)",
            "#2 马八进七 (b0c2) 炮2平5 (b7e7) (+0 分)",
        ],
        side_name="红方",
    )

    print("\n" + "=" * 60)
    print("AI 解说返回：")
    print("=" * 60)
    print(text)
    print("=" * 60)

    if not text or text.startswith("[解释失败]"):
        print("\n❌ AI 解说调用失败。")
        return 1

    print("\n✅ AI 解说验证通过（密钥有效，返回中文解说）")
    return 0


if __name__ == '__main__':
    sys.exit(main())
