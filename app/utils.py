# app/utils.py
from __future__ import annotations
from pathlib import Path
import json, re
from typing import Any, List

def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

def write_json(p: Path, obj: Any):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def srt_to_vtt_text(srt_text: str) -> str:
    # 간단 변환: 헤더 + ',' -> '.'
    lines = srt_text.replace("\r\n", "\n").split("\n")
    out = ["WEBVTT", ""]
    tpat = re.compile(r"(\d{2}:\d{2}:\d{2}),(\d{3})")
    for ln in lines:
        out.append(tpat.sub(r"\1.\2", ln))
    return "\n".join(out)

_WS = re.compile(r"\s+")
def normalize_text(s: str) -> str:
    s = (s or "").replace("\u200b", "").strip()
    return _WS.sub(" ", s)

# --- very light token extraction (no external NLP) ---
_EN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-\.]*")
def extract_nouns(text: str, lang: str = "ko") -> List[str]:
    text = normalize_text(text)
    if lang == "en":
        toks = [m.group(0) for m in _EN_WORD.finditer(text)]
        STOP = {
            "the","a","an","and","or","but","for","nor","with","from","into","onto","over","under","between",
            "to","of","in","on","at","by","as","is","are","was","were","be","been","being","this","that","these","those",
            "it","its","you","your","i","we","they","he","she","them","his","her","our","their","so","then","than",
        }
        return [t for t in toks if len(t) >= 2 and t.lower() not in STOP]
    # ko: 2자 이상 한글 시퀀스
    return re.findall(r"[가-힣]{2,}", text)
