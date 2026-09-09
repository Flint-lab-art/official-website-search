"""文件日志：面板级 INFO + 引擎级 DEBUG 都落盘到 logs/run_YYYYMMDD.log。
面板日志（server.STATE.log）走 info；引擎内部关键过程走 debug，
便于排查「页面实际状态 vs 判定结果」脱节的问题。"""

import os
import time

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


def _write(level, text):
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        path = os.path.join(_LOG_DIR, f"run_{time.strftime('%Y%m%d')}.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}][{level}] {text}\n")
    except Exception:
        pass


def info(text):
    """面板可见级别的日志（任务开始/结果等），与 UI 同步"""
    _write("INFO", text)


def debug(engine, text):
    """引擎级调试日志（仅入文件，不上面板）"""
    _write("DEBUG", f"[{engine}] {text}")


def error(engine, text):
    """异常级日志，带堆栈"""
    _write("ERROR", f"[{engine}] {text}")
