#!/usr/bin/env python3
"""
Pikafish UCI 引擎通信模块

基于 pychess/src/ai/pikafish.py 核心逻辑精简改造：
- 去掉 ChessInfo/Move/Pos 等 GUI 依赖
- 保留 subprocess + UCI 协议通信链路
- 直接接受 FEN 字符串输入，返回结构化分析结果
"""

import subprocess
import os
import sys
import re
import time
import threading
import logging
from typing import List, Optional, Dict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# --- 数据结构 ---

@dataclass
class EngineInfo:
    """单条 info 线的解析结果。"""
    depth: int = 0
    seldepth: int = 0
    multipv: int = 1
    score_cp: Optional[int] = None
    score_mate: Optional[int] = None
    pv: List[str] = field(default_factory=list)
    time_ms: int = 0
    nodes: int = 0
    nps: int = 0
    raw: str = ''


@dataclass
class AnalysisResult:
    """完整分析结果。"""
    fen: str
    bestmove: str = ''
    ponder: str = ''
    infos: List[EngineInfo] = field(default_factory=list)
    candidates: List[EngineInfo] = field(default_factory=list)
    elapsed_ms: int = 0


# --- 引擎路径探测 ---

def _probe_engine(engine_path: str, timeout: float = 5.0) -> bool:
    """测试引擎是否能启动并响应 uci 命令。"""
    try:
        proc = subprocess.Popen(
            [engine_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
        proc.stdin.write('uci\n'); proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            if 'uciok' in line:
                proc.stdin.write('quit\n'); proc.stdin.flush()
                proc.wait(timeout=3)
                return True
        proc.kill()
    except Exception:
        pass
    return False


def find_engine(engine_dir: str = None) -> Optional[str]:
    """自动探测可用的 Pikafish 引擎二进制。"""
    if engine_dir is None:
        engine_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'engine')

    if sys.platform == 'win32':
        candidates = [
            'pikafish-avx2.exe', 'pikafish-bmi2.exe',
            'pikafish-sse41-popcnt.exe', 'pikafish-avx512.exe',
            'pikafish-avxvnni.exe', 'pikafish.exe',
        ]
        search_dir = os.path.join(engine_dir, 'Windows')
    elif sys.platform == 'darwin':
        candidates = ['pikafish-apple-silicon', 'pikafish']
        search_dir = os.path.join(engine_dir, 'MacOS')
    else:
        candidates = [
            'pikafish-avx2', 'pikafish-bmi2', 'pikafish-sse41-popcnt',
            'pikafish-avx512', 'pikafish',
        ]
        search_dir = os.path.join(engine_dir, 'Linux')

    search_paths = [os.path.join(search_dir, c) for c in candidates if os.path.exists(search_dir)]
    search_paths += [os.path.join(engine_dir, c) for c in candidates]

    for path in search_paths:
        if os.path.isfile(path) and _probe_engine(path):
            logger.info(f"Found engine: {path}")
            return path
    return None



# --- 核心引擎类 ---

class PikafishEngine:
    """Pikafish UCI 引擎封装 - 专注于局面分析。"""

    def __init__(self, engine_path: str = None):
        self.engine_path = engine_path
        self.process: Optional[subprocess.Popen] = None
        self._initialized = False
        self._lock = threading.Lock()

    def initialize(self) -> bool:
        """启动引擎并完成 UCI 握手。"""
        if self._initialized:
            return True
        if not self.engine_path:
            self.engine_path = find_engine()
        if not self.engine_path or not os.path.isfile(self.engine_path):
            raise FileNotFoundError(
                f"Pikafish引擎未找到。请放入 engine/ 目录或指定路径。"
                f"当前: {self.engine_path}"
            )
        logger.info(f"Starting engine: {self.engine_path}")
        self.process = subprocess.Popen(
            [self.engine_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self._send('uci')
        for _ in range(200):
            line = self._read()
            if not line or 'uciok' in line:
                break
        self._send('setoption name UCI_Variant value xiangqi')
        self._send('isready')
        for _ in range(50):
            line = self._read()
            if not line or 'readyok' in line:
                break
        self._initialized = True
        logger.info("Engine ready")
        return True

    def _send(self, command: str):
        if self.process and self.process.stdin:
            self.process.stdin.write(command + '\n')
            self.process.stdin.flush()

    def _read(self) -> Optional[str]:
        if self.process and self.process.stdout:
            try:
                return self.process.stdout.readline().strip()
            except Exception:
                return None
        return None

    def analyze(
        self, fen: str, depth: int = 20, movetime: int = 3000, multi_pv: int = 3,
    ) -> AnalysisResult:
        """发送FEN，等待引擎分析，返回结构化结果。"""
        if not self._initialized:
            self.initialize()

        with self._lock:
            if multi_pv > 1:
                self._send(f'setoption name MultiPV value {multi_pv}')
            self._send(f'position fen {fen}')

            if depth > 0 and movetime > 0:
                self._send(f'go depth {depth} movetime {movetime}')
            elif depth > 0:
                self._send(f'go depth {depth}')
            else:
                self._send(f'go movetime {movetime}')

            start_time = time.time()
            infos: List[EngineInfo] = []
            bestmove = ''
            ponder = ''

            while True:
                line = self._read()
                if not line:
                    break
                if line.startswith('info '):
                    infos.append(self._parse_info(line))
                elif line.startswith('bestmove '):
                    parts = line.split()
                    bestmove = parts[1] if len(parts) > 1 else ''
                    if len(parts) > 3 and parts[2] == 'ponder':
                        ponder = parts[3]
                    break

            elapsed_ms = int((time.time() - start_time) * 1000)
            candidates = self._extract_candidates(infos, multi_pv)

            return AnalysisResult(
                fen=fen, bestmove=bestmove, ponder=ponder,
                infos=infos, candidates=candidates, elapsed_ms=elapsed_ms,
            )

    def _parse_info(self, line: str) -> EngineInfo:
        """解析 info 行: depth, score cp/mate, pv, multipv。"""
        info = EngineInfo(raw=line)
        tokens = line.split()
        i = 1
        SKIP_KEYS = {'depth','seldepth','multipv','score','time',
                     'nodes','nps','hashfull','tbhits','currmove',
                     'currmovenumber','string','lowerbound','upperbound'}
        while i < len(tokens):
            t = tokens[i]
            if t == 'depth':
                info.depth = int(tokens[i+1]); i += 2
            elif t == 'seldepth':
                info.seldepth = int(tokens[i+1]); i += 2
            elif t == 'multipv':
                info.multipv = int(tokens[i+1]); i += 2
            elif t == 'score':
                i += 1
                if i < len(tokens):
                    if tokens[i] == 'cp':
                        info.score_cp = int(tokens[i+1]); i += 2
                    elif tokens[i] == 'mate':
                        info.score_mate = int(tokens[i+1]); i += 2
            elif t == 'pv':
                i += 1
                while i < len(tokens) and tokens[i] not in SKIP_KEYS:
                    info.pv.append(tokens[i]); i += 1
                continue
            elif t == 'time':
                info.time_ms = int(tokens[i+1]); i += 2
            elif t == 'nodes':
                info.nodes = int(tokens[i+1]); i += 2
            elif t == 'nps':
                info.nps = int(tokens[i+1]); i += 2
            else:
                i += 1
        return info

    def _extract_candidates(self, infos: List[EngineInfo], multi_pv: int) -> List[EngineInfo]:
        """从info列表中提取每个MultiPV路线最深的结果。"""
        if not infos:
            return []
        candidates: Dict[int, EngineInfo] = {}
        for info in infos:
            pv_idx = info.multipv
            if pv_idx not in candidates or info.depth >= candidates[pv_idx].depth:
                candidates[pv_idx] = info
        return [candidates[i] for i in sorted(candidates) if i <= multi_pv]

    def close(self):
        """关闭引擎。"""
        if self.process:
            try:
                self._send('quit')
                self.process.wait(timeout=3)
            except Exception:
                self.process.kill()
            self.process = None
        self._initialized = False

    def __del__(self):
        self.close()


# --- 自测 ---
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    engine = PikafishEngine()
    try:
        engine.initialize()
        fen = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1'
        result = engine.analyze(fen, depth=15, movetime=3000, multi_pv=3)
        print(f"\n{'='*60}")
        print(f"FEN: {result.fen}")
        print(f"Bestmove: {result.bestmove}  Ponder: {result.ponder}")
        print(f"Time: {result.elapsed_ms}ms")
        for c in result.candidates:
            sc = f"cp {c.score_cp}" if c.score_cp is not None else f"mate {c.score_mate}"
            print(f"  PV#{c.multipv} depth={c.depth} {sc} | {' '.join(c.pv[:6])}")
    finally:
        engine.close()

