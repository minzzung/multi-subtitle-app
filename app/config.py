from __future__ import annotations
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 저장 루트
STORAGE_ROOT = os.getenv("STORAGE_ROOT", str(BASE_DIR / "storage"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2048"))

# Celery / Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/1")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

# Whisper
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
ASR_BACKEND = os.getenv("ASR_BACKEND", "faster_whisper")
FASTER_WHISPER_COMPUTE_TYPE = os.getenv("FASTER_WHISPER_COMPUTE_TYPE", "auto")
TORCH_DEVICE = os.getenv("TORCH_DEVICE", "auto")

# ── Glossary 설정 (경로/컬럼/인코딩 고정) ─────────────────────────────────────
# 폴더 스크린샷과 정확히 맞춤: data/병합_표준단어사전.csv
GLOSSARY_CSV = os.getenv("GLOSSARY_CSV", str(BASE_DIR / "data" / "병합_표준단어사전.csv"))

# CSV 컬럼명이 다를 수 있어 자동 탐지하지만, 기본 힌트도 명시
GLOSSARY_TERM_COL = os.getenv("GLOSSARY_TERM_COL", "표준단어명")
GLOSSARY_DEF_COL  = os.getenv("GLOSSARY_DEF_COL", "표준단어 설명")

# 한글 CSV는 BOM이 있을 수 있어 utf-8-sig 기본, 안 되면 cp949로 자동 재시도
GLOSSARY_ENCODING = os.getenv("GLOSSARY_ENCODING", "utf-8-sig")
