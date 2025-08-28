# app/translation.py
from __future__ import annotations
from functools import lru_cache
from typing import List, Tuple
import re
import torch
from transformers import MarianMTModel, MarianTokenizer

# ---- 선택적 런타임 설정 (config 연동) ----
try:
    from . import config as CFG
    _WANTED_DEVICE = CFG.TORCH_DEVICE
    _WANTED_DTYPE  = CFG.TORCH_DTYPE
except Exception:
    _WANTED_DEVICE = "auto"
    _WANTED_DTYPE  = "auto"

# ---- 언어 코드 정규화 ----
_ALIAS = {
    "kr": "ko", "kor": "ko",
    "jp": "ja", "jap": "ja",
    "cn": "zh", "chs": "zh", "chi": "zh",
    "vn": "vi", "viet": "vi",
    "tai": "th",
    "fil": "tl", "ph": "tl", "phi": "tl",
    "uzb": "uz",
}
def _canon(code: str) -> str:
    c = (code or "").strip().lower()
    return _ALIAS.get(c, c)

def _pick_device_dtype() -> Tuple[str, torch.dtype]:
    use_cuda = torch.cuda.is_available() and (_WANTED_DEVICE in ("auto", "cuda"))
    if use_cuda:
        dt = torch.float16 if _WANTED_DTYPE in ("auto", "fp16") else torch.float32
        return "cuda", dt
    return "cpu", torch.float32

_DEVICE, _DTYPE = _pick_device_dtype()

# ---- Marian 모델 매핑 (직접/피벗용) ----
# 직접 지원: ko<->en
# 기타 대상어(zh/vi/ja/th/uz/tl)는 en과의 쌍으로만 두고,
# translate_text_safe()에서 ko->en->target 영어 피벗 경로로 처리.
MODEL_MAP = {
    ("ko","en"): "Helsinki-NLP/opus-mt-ko-en",
    ("en","ko"): "Helsinki-NLP/opus-mt-en-ko",

    ("en","zh"): "Helsinki-NLP/opus-mt-en-zh",
    ("zh","en"): "Helsinki-NLP/opus-mt-zh-en",

    ("en","vi"): "Helsinki-NLP/opus-mt-en-vi",
    ("vi","en"): "Helsinki-NLP/opus-mt-vi-en",

    ("en","ja"): "Helsinki-NLP/opus-mt-en-jap",  # opus 모델은 'jap' 토큰 사용
    ("ja","en"): "Helsinki-NLP/opus-mt-ja-en",

    ("en","th"): "Helsinki-NLP/opus-mt-en-th",
    ("th","en"): "Helsinki-NLP/opus-mt-th-en",

    ("en","uz"): "Helsinki-NLP/opus-mt-en-uz",
    ("uz","en"): "Helsinki-NLP/opus-mt-uz-en",

    ("en","tl"): "Helsinki-NLP/opus-mt-en-tl",
    ("tl","en"): "Helsinki-NLP/opus-mt-tl-en",
}

@lru_cache(maxsize=32)
def _load_marian(src: str, tgt: str):
    s, t = _canon(src), _canon(tgt)
    name = MODEL_MAP.get((s, t))
    if not name:
        raise ValueError(f"Unsupported Marian pair: {s}->{t}")
    tok = MarianTokenizer.from_pretrained(name)
    model = MarianMTModel.from_pretrained(name)
    model.to(_DEVICE, dtype=_DTYPE).eval()
    return tok, model

def _translate_marian(texts: List[str], src: str, tgt: str, max_len=512) -> List[str]:
    s, t = _canon(src), _canon(tgt)
    tok, model = _load_marian(s, t)
    batch = tok(texts, return_tensors="pt", padding=True, truncation=True, max_length=max_len)
    batch = {k: v.to(_DEVICE) for k, v in batch.items()}
    with torch.no_grad():
        gen = model.generate(
            **batch,
            max_length=max_len,
            num_beams=4,
            no_repeat_ngram_size=3,
            repetition_penalty=1.2,
            length_penalty=1.0,
            early_stopping=True,
        )
    return tok.batch_decode(gen, skip_special_tokens=True)

# ---- 간단 분절기: 긴 문장 안전 분할 ----
_KO_BOUND = re.compile(r"(다\.|요\.|[.!?])\s+")
_EN_BOUND = re.compile(r"([.!?])\s+")
def _split_heuristic(text: str, lang: str, max_len=160) -> List[str]:
    text = (text or "").strip()
    if not text:
        return [text]
    lang = (lang or "").strip().lower()

    txt = _KO_BOUND.sub(r"\1\n", text) if lang.startswith("ko") else _EN_BOUND.sub(r"\1\n", text)
    parts = [p.strip() for p in txt.split("\n") if p.strip()]

    out: List[str] = []
    for p in parts:
        if len(p) <= max_len:
            out.append(p)
        else:
            # 공백 기준 추가 분할
            out.extend(re.findall(r".{1,%d}(?:\s|$)" % max_len, p))
    return [x.strip() for x in out if x.strip()]

# ---- Public API ----
def translate_text(texts: List[str], src: str, tgt: str, max_len=512) -> List[str]:
    s, t = _canon(src), _canon(tgt)
    out: List[str] = []
    for line in texts:
        chunks = _split_heuristic(line, s, max_len=160)
        piece_out: List[str] = []
        for ch in chunks:
            piece = _translate_marian([ch], s, t, max_len=256)[0]
            piece_out.append(piece)
        out.append(" ".join(piece_out))
    return out

def translate_text_safe(texts: List[str], src: str, tgt: str, max_len=512) -> List[str]:
    s, t = _canon(src), _canon(tgt)
    # 1) 직접 번역
    try:
        return translate_text(texts, s, t, max_len)
    except Exception:
        pass
    # 2) 영어 피벗 (ko->en->target 또는 target->en->ko 등)
    try:
        if s != "en" and t != "en":
            mid = translate_text(texts, s, "en", max_len)
            return translate_text(mid, "en", t, max_len)
    except Exception:
        pass
    # 3) 최종 폴백: 원문 반환(안전)
    return texts
