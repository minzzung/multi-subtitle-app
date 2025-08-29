# app/translation.py
from __future__ import annotations

from typing import List, Union, Tuple, Iterable, Optional
import os
import torch

# transformers
from transformers import MarianMTModel, MarianTokenizer
from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

# ─────────────────────────────────────────────────────────────────────────────
# 전역 설정
# ─────────────────────────────────────────────────────────────────────────────
_DEVICE = "cuda" if torch.cuda.is_available() and os.getenv("TORCH_DEVICE", "auto") in ("auto", "cuda") else "cpu"

# Marian 모델 캐시: { "src-tgt": (tokenizer, model) }
_MARIAN: dict[str, Tuple[MarianTokenizer, MarianMTModel]] = {}

# m2m100 싱글톤
_M2M_TOK: Optional[M2M100Tokenizer] = None
_M2M_MOD: Optional[M2M100ForConditionalGeneration] = None

# 간단 번역 캐시(중복 호출 절감): { (backend, src, tgt, text): out_text }
_MEMO: dict[Tuple[str, str, str, str], str] = {}

# 허용 언어/별칭 정리
_ALIAS = {
    "kr": "ko", "kor": "ko",
    "jp": "ja", "jap": "ja",
    "cn": "zh", "chs": "zh", "chi": "zh", "zh-cn": "zh", "zh_cn": "zh", "zh-hans": "zh",
    "zh-tw": "zh", "zh_tw": "zh", "zh-hant": "zh",
}
def _canon_lang(x: str) -> str:
    if not x:
        return "en"
    x = x.strip().lower()
    return _ALIAS.get(x, x)

def _ensure_list(x: Union[str, List[str]]) -> Tuple[List[str], bool]:
    """입력이 str이면 [str]로 바꿔 처리하고, 나중에 되돌리기 위해 플래그 반환"""
    if isinstance(x, str):
        return [x], True
    return list(x), False

def _memo_get(backend: str, src: str, tgt: str, texts: Iterable[str]) -> Tuple[List[str], List[int], List[str]]:
    """캐시 조회: 반환 (캐시결과리스트, 미번역인덱스, 미번역텍스트리스트)"""
    outs: List[str] = []
    need_idx: List[int] = []
    need_txt: List[str] = []
    for i, t in enumerate(texts):
        key = (backend, src, tgt, t or "")
        if key in _MEMO:
            outs.append(_MEMO[key])
        else:
            outs.append(None)  # placeholder
            need_idx.append(i)
            need_txt.append(t or "")
    return outs, need_idx, need_txt

def _memo_set(backend: str, src: str, tgt: str, idx: List[int], texts_in: List[str], texts_out: List[str], outs_arr: List[Optional[str]]):
    for j, i in enumerate(idx):
        key = (backend, src, tgt, texts_in[j] or "")
        _MEMO[key] = texts_out[j]
        outs_arr[i] = texts_out[j]

# ─────────────────────────────────────────────────────────────────────────────
# MarianMT
# ─────────────────────────────────────────────────────────────────────────────
def _load_marian(src: str, tgt: str) -> Tuple[MarianTokenizer, MarianMTModel]:
    pair = f"{src}-{tgt}"
    if pair in _MARIAN:
        return _MARIAN[pair]
    model_name = f"Helsinki-NLP/opus-mt-{src}-{tgt}"
    tok = MarianTokenizer.from_pretrained(model_name)
    mod = MarianMTModel.from_pretrained(model_name).to(_DEVICE)
    _MARIAN[pair] = (tok, mod)
    return tok, mod

def _translate_marian(texts: List[str], src: str, tgt: str) -> List[str]:
    tok, mod = _load_marian(src, tgt)
    batch = tok(texts, return_tensors="pt", padding=True, truncation=True).to(_DEVICE)
    with torch.no_grad():
        gen = mod.generate(**batch, max_new_tokens=int(os.getenv("MT_MAX_NEW_TOKENS", "256")))
    return [tok.decode(g, skip_special_tokens=True) for g in gen]

# ─────────────────────────────────────────────────────────────────────────────
# m2m100_418M
# ─────────────────────────────────────────────────────────────────────────────
def _load_m2m() -> Tuple[M2M100Tokenizer, M2M100ForConditionalGeneration]:
    global _M2M_TOK, _M2M_MOD
    if _M2M_TOK is None or _M2M_MOD is None:
        name = os.getenv("M2M100_MODEL_NAME", "facebook/m2m100_418M")
        _M2M_TOK = M2M100Tokenizer.from_pretrained(name)
        _M2M_MOD = M2M100ForConditionalGeneration.from_pretrained(name).to(_DEVICE)
    return _M2M_TOK, _M2M_MOD

def _translate_m2m(texts: List[str], src: str, tgt: str) -> List[str]:
    tok, mod = _load_m2m()
    tok.src_lang = src
    batch = tok(texts, return_tensors="pt", padding=True, truncation=True).to(_DEVICE)
    with torch.no_grad():
        gen = mod.generate(**batch, forced_bos_token_id=tok.get_lang_id(tgt), max_new_tokens=int(os.getenv("MT_MAX_NEW_TOKENS", "256")))
    return [tok.decode(g, skip_special_tokens=True) for g in gen]

# ─────────────────────────────────────────────────────────────────────────────
# Public APIs
# ─────────────────────────────────────────────────────────────────────────────
def translate_text(texts: Union[str, List[str]], src_lang: str, tgt_lang: str) -> Union[str, List[str]]:
    """
    직접 번역(Marian 단일 호출). 실패 시 예외를 던집니다.
    """
    arr, was_str = _ensure_list(texts)
    src = _canon_lang(src_lang)
    tgt = _canon_lang(tgt_lang)

    # 캐시 확인 (Marian)
    cached, need_idx, need_txt = _memo_get("marian", src, tgt, arr)
    if need_idx:
        outs = _translate_marian(need_txt, src, tgt)
        _memo_set("marian", src, tgt, need_idx, need_txt, outs, cached)

    out = [x if x is not None else "" for x in cached]
    return out[0] if was_str else out

def translate_text_safe(texts: Union[str, List[str]], src_lang: str, tgt_lang: str) -> Union[str, List[str]]:
    """
    안전 번역 파이프라인:
      1) MarianMT(src→tgt) 직접 시도
      2) 실패 시 MarianMT pivot: (src→en) → (en→tgt)  *src/tgt가 이미 en이면 생략
      3) 그래도 실패하면 m2m100_418M(src→tgt)
      4) 모두 실패하면 원문 그대로 반환
    """
    arr, was_str = _ensure_list(texts)
    src = _canon_lang(src_lang)
    tgt = _canon_lang(tgt_lang)

    # 1) Marian 직접
    try:
        cached, need_idx, need_txt = _memo_get("marian", src, tgt, arr)
        if need_idx:
            outs = _translate_marian(need_txt, src, tgt)
            _memo_set("marian", src, tgt, need_idx, need_txt, outs, cached)
        out = [x if x is not None else "" for x in cached]
        return out[0] if was_str else out
    except Exception as e1:
        pass

    # 2) Marian pivot (src→en→tgt)
    try:
        if src != "en" and tgt != "en":
            # src→en
            cached1, need_idx1, need_txt1 = _memo_get("marian", src, "en", arr)
            if need_idx1:
                outs1 = _translate_marian(need_txt1, src, "en")
                _memo_set("marian", src, "en", need_idx1, need_txt1, outs1, cached1)
            mid = [x if x is not None else "" for x in cached1]

            # en→tgt
            cached2, need_idx2, need_txt2 = _memo_get("marian", "en", tgt, mid)
            if need_idx2:
                outs2 = _translate_marian(need_txt2, "en", tgt)
                _memo_set("marian", "en", tgt, need_idx2, need_txt2, outs2, cached2)
            out = [x if x is not None else "" for x in cached2]
            return out[0] if was_str else out
        # else: src==en 또는 tgt==en 인 경우 위 직접 번역이 이미 실패했으므로 pivot 생략
    except Exception as e2:
        pass

    # 3) m2m100 fallback
    try:
        cached3, need_idx3, need_txt3 = _memo_get("m2m", src, tgt, arr)
        if need_idx3:
            outs3 = _translate_m2m(need_txt3, src, tgt)
            _memo_set("m2m", src, tgt, need_idx3, need_txt3, outs3, cached3)
        out = [x if x is not None else "" for x in cached3]
        return out[0] if was_str else out
    except Exception as e3:
        # 로그만 남기고
        print(f"[translate] all backends failed; return original. src={src}, tgt={tgt}")

    # 4) 모두 실패 → 원문 반환
    return texts
