#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 解释器：用大模型解释「引擎为什么推荐这一步」。

设计原则：
- 抽象基类 Explainer，便于后续接入任意模型（DeepSeek / OpenAI / Ollama ...）。
- 不阻塞主链路：无 API key 或调用失败时返回 None，主程序自动降级为"不解释"。
- 密钥优先级：显式传入 api_key > 环境变量 DEEPSEEK_API_KEY > DEFAULT_API_KEY。
"""

import os
from abc import ABC, abstractmethod

# 内置默认密钥（本地个人项目兜底用）；可通过环境变量 DEEPSEEK_API_KEY 覆盖，方便更换密钥。
DEFAULT_API_KEY = "sk-a551aadc6ed14c16b08c6f579379c94d"


class Explainer(ABC):
    """局面解释器抽象接口。"""

    @abstractmethod
    def explain(self, fen, bestmove_cn, bestmove_uci, score, pv_lines, side_name):
        """返回自然语言解释；无法解释时返回 None。"""
        raise NotImplementedError


class DeepSeekExplainer(Explainer):
    """DeepSeek（OpenAI 兼容）解释器。"""

    API_URL = "https://api.deepseek.com/chat/completions"
    MODEL = "deepseek-chat"

    def __init__(self, api_key=None, model=None, timeout=30):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or DEFAULT_API_KEY
        self.model = model or self.MODEL
        self.timeout = timeout

    def available(self):
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
            "temperature": 0.4,
            "max_tokens": 500,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(self.API_URL, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001
            return f"[解释失败] {type(exc).__name__}: {exc}"
