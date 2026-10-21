# 象棋 AI 助手（桌面版）

截屏识别「天天象棋」棋盘 → 重建局面 FEN → 皮卡鱼（Pikafish）引擎分析 → 输出中文出招建议，可选让大模型解释为什么这么走。支持手动逐步提示与全自动对弈。

> ⚠️ 本项目通过模拟鼠标/键盘操作游戏客户端，仅供学习研究，账号风险自负。

## 功能

- **屏幕识别**：CNN 15 类棋子分类（空格 + 红 7 类 + 黑 7 类），整盘 90 格一次性批量前向推理
- **局面重建**：识别结果转标准 FEN，并自动推断我方执红还是执黑
- **引擎分析**：对接 Pikafish UCI 协议，输出候选着法、评分与 PV 主线
- **中文记谱**：UCI（如 `b2e2`）转中文（如 `炮八平五`）
- **差分状态机**：全自动对弈时只盯"变化格"判断对方是否走完，对识别噪声容忍
- **LLM 解说**：DeepSeek 解释推荐着法；没有密钥时自动降级为不出解说，不影响出招
- **多套校准**：`人机对战` / `棋力测评` / `残局` 三套模板随时切换，也可自建
- **无限对战**：一局结束自动点"再来一局"并等到开局就绪，循环开局；反引号键随时紧急停止

## 环境要求

- **Windows**（依赖 `msvcrt` 键盘输入、Win32 截图与鼠标 API）
- Python 3.7+
- 依赖见 `requirements.txt`：

```bash
pip install -r requirements.txt
```

## 引擎与模型（不在仓库里，需自行准备）

以下内容被 `.gitignore` 排除，clone 下来是**没有**的：

| 路径 | 说明 |
|---|---|
| `engine/` | Pikafish 可执行文件 + `pikafish.nnue` 权重（约 53 MB）。`.gitignore` 屏蔽了 `engine/`、`*.exe`、`*.dll` |
| `xiangqi-bot/xiangqi_cnn.pt` | 棋子分类 CNN 权重（`*.pt` 被忽略）。没有权重时先按 `xiangqi_cnn.py` 采集样本训练 |
| `xiangqi-bot/templates/` | 模板匹配资源（模板兜底识别路径使用） |

> 早期仓库 `fenReturnSuggestion` 曾把 53 MB 的 NNUE 提交进 git，已归档为反例 —— 二进制大文件请走 Releases，不要进 git 历史。

## 运行

```bash
python main_merged.py
```

`start_ai.bat` 只是一层快捷启动，内部写死了本机虚拟环境路径（`ai象棋外挂项目pro - 副本\.venv\Scripts\python.exe`），换机器请改用上面的命令或自行修改该文件。

首次运行需要校准：按提示把鼠标移到棋盘两个对角，生成 `xiangqi-bot/calib.json`。

主菜单常用按键：

| 按键 | 作用 |
|---|---|
| `1` | 识别当前棋盘并给出建议 |
| `2` | 手动查看 AI 解说 |
| `E` | 切换「出建议后自动附带解说」 |
| `9` | AI 解说设置（密钥 / 模型 / 超时 / 随机性） |
| `F` | 单局全自动对弈 |
| `6` | 无限自动对战 |
| `` ` `` | 紧急停止（全局热键，任何焦点下都有效） |

## 目录与模块职责

| 文件 | 职责 |
|---|---|
| `main_merged.py` | 主入口，纯终端 CLI 菜单 |
| `advisor.py` | 编排层：识别 → 拼 FEN → 引擎分析 → 中文建议（含多帧投票识别） |
| `engine_wrapper.py` | Pikafish UCI 封装：subprocess 通信，FEN 进、结构化分析结果出 |
| `fen_utils.py` | FEN 解析/生成、执方推断、着法应用 |
| `move_notation.py` | UCI ↔ 中文记谱互转、评分格式化、PV 转中文 |
| `board_diff.py` | 两帧棋盘差分、判定变化格是否构成一步合法棋（纯函数，可独立单测） |
| `auto_battle.py` | 全自动对弈差分状态机（等待对方 / 我方落子 / 回读确认） |
| `infinite_battle.py` | 无限对战、预设坐标点击、开局就绪轮询 |
| `turn_detector.py` | 走子方管理（当前为手动指定） |
| `calib_manager.py` | 多套校准模板管理，切换时复制到生效的 `calib.json` |
| `hotkey.py` | 全局反引号键紧急停止检测 |
| `explainer.py` | 解说抽象基类 + DeepSeek 实现，失败自动降级 |
| `ai_config.py` | 解说默认值（API 地址 / 模型 / 参数） |
| `ai_settings.py` | 运行期设置持久化到 `ai_settings.json`，菜单内改完即生效 |
| `manual_edit.py` | 人工修正识别错的棋子 |
| `verify_e2e.py`、`verify_explainer.py` | 手动联调脚本 |
| `xiangqi-bot/xiangqi_bot.py` | 原始自动对弈 Bot：截图、窗口置顶、校准 |
| `xiangqi-bot/xiangqi_cnn.py` | CNN 棋子分类：样本采集 / 训练 / 推理 |
| `xiangqi-bot/calibrate_board.py` | 棋盘校准工具 |
| `tests/` | 18 个单元测试 |

## 测试

测试为 pytest 风格（`tests/` 下 18 个文件）：

```bash
pip install pytest
python -m pytest tests/ -q
```

多数用例只测纯逻辑（差分、记谱、FEN、状态判断），不启动引擎也不截图，因此没有游戏环境也能跑。

## 安全提示（重要）

- `ai_config.py` 与 `ai_settings.json` 中都出现过**明文 DeepSeek API Key**，且二者都已提交进 git 历史。
  建议：到 <https://platform.deepseek.com/> 吊销旧密钥、重新生成；之后改用环境变量 `DEEPSEEK_API_KEY`
  （优先级：显式传参 > 环境变量 > `ai_settings.json`），并把 `ai_settings.json` 加入 `.gitignore`。
- `.gitignore` 已屏蔽 `*.pt` / `engine/` / `*.exe`，请勿把大二进制提交进仓库。

## 相关仓库

- `fenReturnSuggestion` —— 早期版本（已归档）：从 FEN 直接问引擎要建议，已被本仓库取代。