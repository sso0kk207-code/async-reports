"""Один командой: API + настоящий Celery-воркер (файловый брокер, Redis не нужен).

python scripts/dev.py [--port 8000]
"""
import argparse
import subprocess
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # запуск из любой папки: пакет app должен находиться

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()

worker = subprocess.Popen([sys.executable, "-m", "celery", "-A", "app.worker", "worker", "--pool=solo", "--loglevel=warning", "--without-gossip", "--without-mingle"], cwd=ROOT)
try:
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="warning")
finally:
    worker.terminate()
    worker.wait(timeout=15)
