#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 解释器：用大模型解释「引擎为什么推荐这一步」。

设计原则：
- 抽象基类 Explainer，便于后续接入任意模型（DeepSeek / OpenAI / Ollama ...）。
- 不阻塞主链路：无 API key 或调用失败时返回 None，主程序自动降级为"不解释"。
- 所有设定（API 密钥 / 服务端地址 / 模型名 / 请求参数）集中管理：
  默认值在 ai_config.py；运行时可在主菜单 [9]「AI 解说设置」中修改，
  保存到 ai_settings.json 并立即生效，无需重启、无需手改源码。
- 密钥优先级：显式传入 api_key > 环境变量 DEEPSEEK_API_KEY > 配置文件（ai_settings）。
"""

import os
from abc import ABC, abstractmethod

from ai_settings import get_settings  # noqa: E402


class Explainer(ABC):
    """局面解释器抽象接口。"""

    @abstractmethod
    def explain(self, fen, bestmove_cn, bestmove_uci, score, pv_lines, side_name):
        """返回自然语言解释；无法解释时返回 None。"""
        raise NotImplementedError


class DeepSeekExplainer(Explainer):
    """DeepSeek（OpenAI 兼容）解释器。

    每次 available()/explain() 前都会重新读取 ai_settings（默认值 +
    ai_settings.json 覆盖），因此在 [9] AI 设置菜单中修改参数后，
    下一次调用立即生效，无需重启程序。
    """

    def __init__(self, api_key=None, model=None, timeout=None):
        # 显式传入参数优先（测试/特殊场景用），None 表示走配置文件
        self._api_key_override = api_key
        self._model_override = model
        self._timeout_override = timeout
        # 当前生效值（供 available / explain / 外部读取，如 verify_explainer）
        self.api_key = None
        self.model = None
        self.timeout = None
        self.api_url = None
        self.temperature = None
        self.max_tokens = None
        self._refresh()

    def _refresh(self):
        """把当前生效设置刷新到实例属性。

        密钥来源：显式传入 > 环境变量 DEEPSEEK_API_KEY > ai_settings
        （默认值来自 ai_config.py，可被 ai_settings.json 覆盖）。
        """
        s = get_settings()
        self.api_key = (self._api_key_override
                        or os.environ.get("DEEPSEEK_API_KEY")
                        or s.get('api_key') or None)
        self.model = self._model_override or s.get('model')
        self.timeout = (self._timeout_override
                        if self._timeout_override is not None
                        else s.get('timeout'))
        self.api_url = s.get('api_url')
        self.temperature = s.get('temperature')
        self.max_tokens = s.get('max_tokens')

    def available(self):
        self._refresh()
        return bool(self.api_key)

    def _build_prompt(self, fen, bestmove_cn, bestmove_uci, score, pv_lines, side_name):
        pv_text = "\n".join(pv_lines) if pv_lines else "(无)"
        return (
            "你是中国象棋特级大师教练，面向业余棋手做通俗易懂的讲解。\n\n"
            "重要规则：下面这步着法是由顶级引擎（Pikafish）深度计算后给出的最佳着法，"
            "你【只能】解释它为什么好，绝对不许改写这步、不许另给建议、不许质疑引擎结论。\n\n"
            f"当前轮到：{side_name}\n"
            f"局面 FEN：{fen}\n"
            f"引擎推荐着法：{bestmove_cn}（UCI: {bestmove_uci}），评分：{score}\n"
            f"候选变例：\n{pv_text}\n\n"
            "请用中文，控制在 200 字以内，分三点回答：\n"
            "1. 当前形势判断（谁占优、关键点在哪）\n"
            "2. 引擎为什么推荐这步（战术/战略意图，可结合评分与变例）\n"
            "3. 后续计划与对手可能的应对\n"
            "只输出这三点内容，不要客套话。"
        )

    def explain(self, fen, bestmove_cn, bestmove_uci, score, pv_lines, side_name):
        self._refresh()
        if not self.api_key:
            return None

        import requests

        prompt = self._build_prompt(fen, bestmove_cn, bestmove_uci, score, pv_lines, side_name)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是一位耐心的中国象棋教练。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(self.api_url, json=payload, headers=headers,
                                 timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001
            return f"[解释失败] {type(exc).__name__}: {exc}"
