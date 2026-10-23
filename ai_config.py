#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 解说配置文件。

本文件集中存放「AI 解说」功能的所有设定，方便自行修改：

  API_KEY    必填。填入你的 DeepSeek API Key（留空则 AI 解说功能不可用，
             程序会提示先填入密钥）。
  其余为服务端 / 请求参数设定，一般不用改，按需调整即可。

修改后无需重装任何依赖，直接重启程序即生效。
"""

import os

# ===== 必填：API 密钥 =====
# 优先从环境变量 DEEPSEEK_API_KEY 读取；未设置时为空，AI 解说功能不可用。
# 如需本地临时使用，请通过环境变量注入，切勿把明文密钥提交进仓库。
# Windows 示例：set DEEPSEEK_API_KEY=你的密钥
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# ===== 服务端设定（一般不用改）=====
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

# ===== 请求参数设定（可按需调整）=====
TIMEOUT = 30          # 请求超时（秒）
TEMPERATURE = 0.4     # 输出随机性（0~2，越小越稳定）
MAX_TOKENS = 500      # 单次返回最大 token 数
