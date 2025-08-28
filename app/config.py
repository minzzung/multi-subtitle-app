# app/config.py
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
TASK_DIR = STORAGE_DIR / "tasks"
TASK_DIR.mkdir(parents=True, exist_ok=True)

# ---------- Server / Files ----------
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2048"))
DEFAULT_TARGET_LANGS = ["en"]

# ---------- Redis / Celery ----------
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/1")
BROKER_URL = os.getenv("BROKER_URL", REDIS_URL)
RESULT_BACKEND = os.getenv("RESULT_BACKEND", REDIS_URL)

# ---------- Glossary CSV ----------
# 예) data/표준단어사전_환경부표준사전_2024-11-11.csv
GLOSSARY_CSV = os.getenv("GLOSSARY_CSV", str(BASE_DIR / "data" / "병합_표준단어사전"))
GLOSSARY_TERM_COL = os.getenv("GLOSSARY_TERM_COL", "표준단어명")
GLOSSARY_DEF_COL = os.getenv("GLOSSARY_DEF_COL", "표준단어 설명")
GLOSSARY_ENCODING = os.getenv("GLOSSARY_ENCODING", "auto")

# ---------- ASR (Whisper) ----------
# "whisper" | "faster_whisper"
ASR_BACKEND = os.getenv("ASR_BACKEND", "whisper").lower()  # 기본: openai-whisper
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ko")     # 한국어만 추출

# Faster-Whisper 옵션
# 모델 크기 예: "base", "small", "medium", 경로도 가능
FASTER_WHISPER_MODEL = os.getenv("FASTER_WHISPER_MODEL", "medium")
# compute_type: "auto"(권장), "float16", "int8", "int8_float16", "float32" 등
FASTER_WHISPER_COMPUTE_TYPE = os.getenv("FASTER_WHISPER_COMPUTE_TYPE", "auto")

# ---------- Translation Runtime (Transformers/MarianMT) ----------
# auto | cpu | cuda
TORCH_DEVICE = os.getenv("TORCH_DEVICE", "auto").lower()
# auto | fp32 | fp16
TORCH_DTYPE = os.getenv("TORCH_DTYPE", "auto").lower()

