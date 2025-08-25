# app/translation.py
from __future__ import annotations
from functools import lru_cache
from typing import List, Tuple
import re
import torch
from transformers import MarianMTModel, MarianTokenizer, AutoTokenizer, AutoModelForSeq2SeqLM

try:
    from . import config as CFG
    _WANTED_DEVICE = CFG.TORCH_DEVICE
    _WANTED_DTYPE  = CFG.TORCH_DTYPE
except Exception:
    _WANTED_DEVICE = "auto"
    _WANTED_DTYPE  = "auto"

# -------- 언어코드 정규화 --------
_ALIAS = {
    "kr":"ko","kor":"ko",
    "jp":"ja","jap":"ja",
    "cn":"zh","chs":"zh","chi":"zh",
    "vn":"vi","viet":"vi",
    "tai":"th",
    "fil":"tl","ph":"tl","phi":"tl",
    "uzb":"uz",
}
def _canon(code: str) -> str:
    c = (code or "").strip().lower()
    return _ALIAS.get(c, c)

def _pick_device_dtype() -> Tuple[str, torch.dtype]:
    use_cuda = torch.cuda.is_available() and (_WANTED_DEVICE in ("auto","cuda"))
    if use_cuda:
        dt = torch.float16 if _WANTED_DTYPE in ("auto","fp16") else torch.float32
        return "cuda", dt
    return "cpu", torch.float32

_DEVICE, _DTYPE = _pick_device_dtype()

# -------- Marian 모델 매핑 --------
MODEL_MAP = {
    ("ko","en"): "Helsinki-NLP/opus-mt-ko-en",
    ("en","ko"): "Helsinki-NLP/opus-mt-en-ko",

    ("en","zh"): "Helsinki-NLP/opus-mt-en-zh",
    ("zh","en"): "Helsinki-NLP/opus-mt-zh-en",

    ("en","vi"): "Helsinki-NLP/opus-mt-en-vi",
    ("vi","en"): "Helsinki-NLP/opus-mt-vi-en",

    ("en","ja"): "Helsinki-NLP/opus-mt-en-jap",
    ("ja","en"): "Helsinki-NLP/opus-mt-ja-en",

    ("en","th"): "Helsinki-NLP/opus-mt-en-th",
    ("th","en"): "Helsinki-NLP/opus-mt-th-en",

    ("en","uz"): "Helsinki-NLP/opus-mt-en-uz",
    ("uz","en"): "Helsinki-NLP/opus-mt-uz-en",

    ("en","tl"): "Helsinki-NLP/opus-mt-en-tl",
    ("tl","en"): "Helsinki-NLP/opus-mt-tl-en",
}

# M2M100를 우선 쓰고 싶은 타깃(품질/지원 이슈 대응)
PREFER_M2M_TARGETS = {"ja","zh","th","vi","tl","uz"}

@lru_cache(maxsize=32)
def _load_marian(src: str, tgt: str):
    key = (_canon(src), _canon(tgt))
    name = MODEL_MAP.get(key)
    if not name:
        raise ValueError(f"Unsupported Marian pair: {key[0]}->{key[1]}")
    tok = MarianTokenizer.from_pretrained(name)
    model = MarianMTModel.from_pretrained(name)
    model.to(_DEVICE, dtype=_DTYPE).eval()
    return tok, model

_M2M_ID = "facebook/m2m100_418M"

@lru_cache(maxsize=1)
def _load_m2m():
    tok = AutoTokenizer.from_pretrained(_M2M_ID)
    model = AutoModelForSeq2SeqLM.from_pretrained(_M2M_ID)
    model.to(_DEVICE, dtype=_DTYPE).eval()
    return tok, model

def _translate_m2m(texts: List[str], src: str, tgt: str, max_len=512) -> List[str]:
    s, t = _canon(src), _canon(tgt)
    tok, model = _load_m2m()
    tok.src_lang = s
    inputs = tok(texts, return_tensors="pt", padding=True, truncation=True, max_length=max_len).to(_DEVICE)
    forced_bos = tok.get_lang_id(t)
    with torch.no_grad():
        gen = model.generate(
            **inputs,
            forced_bos_token_id=forced_bos,
            max_length=max_len,
            num_beams=4,
            no_repeat_ngram_size=3,
            repetition_penalty=1.2,
            length_penalty=1.0,
            early_stopping=True,
        )
    return tok.batch_decode(gen, skip_special_tokens=True)

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
    out = tok.batch_decode(gen, skip_special_tokens=True)
    return out

# ----- 룩비하인드 없는 분절기 (오류 해결) -----
_KO_BOUND = re.compile(r"(다\.|요\.|[.!?])\s+")
_EN_BOUND = re.compile(r"([.!?])\s+")
def _split_heuristic(text: str, lang: str, max_len=160) -> List[str]:
    text = text.strip()
    if not text:
        return [text]
    lang = _canon(lang)

    # 문장 경계에 줄바꿈 삽입 → split
    if lang == "ko":
        txt = _KO_BOUND.sub(r"\1\n", text)
    else:
        txt = _EN_BOUND.sub(r"\1\n", text)
    parts = [p.strip() for p in txt.split("\n") if p.strip()]

    # 너무 길면 공백 기준 추가 분할
    out: List[str] = []
    for p in parts:
        if len(p) <= max_len:
            out.append(p)
        else:
            out.extend(re.findall(r".{1,%d}(?:\s|$)" % max_len, p))
    return [x.strip() for x in out if x.strip()]

def translate_text(texts: List[str], src: str, tgt: str, max_len=512) -> List[str]:
    s, t = _canon(src), _canon(tgt)
    prefer_m2m = t in PREFER_M2M_TARGETS and s != t
    out: List[str] = []
    for line in texts:
        chunks = _split_heuristic(line, s, max_len=160)
        piece_out: List[str] = []
        for ch in chunks:
            try:
                if prefer_m2m:
                    piece = _translate_m2m([ch], s, t, max_len=256)[0]
                else:
                    piece = _translate_marian([ch], s, t, max_len=256)[0]
            except Exception:
                # Marian 실패 시 M2M 폴백
                piece = _translate_m2m([ch], s, t, max_len=256)[0]
            piece_out.append(piece)
        out.append(" ".join(piece_out))
    return out

def translate_text_safe(texts: List[str], src: str, tgt: str, max_len=512) -> List[str]:
    s, t = _canon(src), _canon(tgt)
    try:
        return translate_text(texts, s, t, max_len)
    except Exception:
        pass
    try:
        if s != "en" and t != "en":
            mid = translate_text(texts, s, "en", max_len)
            return translate_text(mid, "en", t, max_len)
    except Exception:
        pass
    try:
        return _translate_m2m(texts, s, t, max_len)
    except Exception:
        return texts
