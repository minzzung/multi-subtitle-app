# app/glossary.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional
import csv
import re

from .utils import normalize_text, extract_nouns
from .translation import translate_text_safe

# 언어 감지(설치 안 되어 있어도 동작하도록 옵셔널)
try:
    from langdetect import detect as _ld_detect
except Exception:
    _ld_detect = None  # optional


_ws = re.compile(r"\s+")
EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\.\-]*")


def _norm_key(s: str) -> str:
    """매칭 키: 소문자 + 공백/하이픈/점 제거"""
    s = normalize_text(s).lower()
    return _ws.sub("", s).replace("-", "").replace(".", "")


def _ngrams(tokens: List[str], n_min: int = 2, n_max: int = 4) -> List[str]:
    out: List[str] = []
    L = len(tokens)
    for n in range(n_min, n_max + 1):
        for i in range(L - n + 1):
            out.append("".join(tokens[i : i + n]))
    return out


@dataclass
class GlossaryEntry:
    # CSV의 표시용(항상 한글 컬럼 기반으로 보여줌)
    term_ko: str
    def_ko: str
    # 매칭 강화를 위한 보조 인덱스(영문명/약어가 CSV에 있다면 사용)
    term_en: Optional[str] = None
    abbr_en: Optional[str] = None


class Glossary:
    """
    - 화면 표시는 항상 CSV의 '표준단어명/표준단어 설명'(한글) 기반.
    - 매칭은 ko/en 인덱스를 모두 사용(약어는 '매칭 전용'으로만 활용, 표시엔 쓰지 않음).
    """

    def __init__(
        self,
        csv_path: Path,
        term_col: str = "표준단어명",
        def_col: str = "표준단어 설명",
        base_lang: str = "ko",
        encoding: str = "auto",
        en_term_col: str = "표준단어 영문명",
        en_abbr_col: str = "표준단어 영문약어명",
    ):
        self.csv_path = csv_path
        self.term_col = term_col
        self.def_col = def_col
        self.base_lang = base_lang
        self.encoding = encoding
        self.en_term_col = en_term_col
        self.en_abbr_col = en_abbr_col

        self.entries: List[GlossaryEntry] = []
        # ko/en 각각 정규화 키 → 인덱스 리스트
        self._index: Dict[str, Dict[str, List[int]]] = {"ko": {}, "en": {}}
        self._enc_used: Optional[str] = None

        self._load()

    # -------------------- Load & Index --------------------

    def _load(self) -> None:
        if not self.csv_path.exists():
            return

        if self.encoding and self.encoding != "auto":
            candidates = [self.encoding]
        else:
            candidates = ["utf-8-sig", "utf-8", "cp949", "ms949", "euc-kr"]

        rows: List[Dict[str, str]] = []
        last_err: Optional[Exception] = None
        for enc in candidates:
            try:
                with self.csv_path.open("r", encoding=enc, newline="") as f:
                    r = csv.DictReader(f)
                    for row in r:
                        rows.append(row)
                self._enc_used = enc
                break
            except Exception as e:
                last_err = e
                continue

        if not rows:
            raise RuntimeError(
                f"Failed to read CSV '{self.csv_path}'. Last error={last_err}"
            )

        for row in rows:
            ko = (row.get(self.term_col) or "").strip()
            if not ko:
                continue
            ko_def = (row.get(self.def_col) or "").strip()
            en = (row.get(self.en_term_col) or "").strip()
            abbr = (row.get(self.en_abbr_col) or "").strip()

            e = GlossaryEntry(
                term_ko=ko,
                def_ko=ko_def,
                term_en=en or None,
                abbr_en=abbr or None,
            )
            idx = len(self.entries)
            self.entries.append(e)

            # ko 인덱스
            key_ko = _norm_key(ko)
            if key_ko:
                self._index["ko"].setdefault(key_ko, []).append(idx)

            # en 인덱스(영문명/약어 모두 키로만 활용)
            for src in (en, abbr):
                if src:
                    self._index["en"].setdefault(_norm_key(src), []).append(idx)

    # -------------------- Matching --------------------

    def _match_ko(self, text_ko: str) -> List[int]:
        hits: List[int] = []
        seen: set[int] = set()

        toks_all = extract_nouns(text_ko, lang="ko")
        toks = [t for t in toks_all if re.fullmatch(r"[가-힣]{2,}", t)]

        # 1-그램
        for t in toks:
            for idx in self._index["ko"].get(_norm_key(t), []):
                if idx not in seen:
                    seen.add(idx)
                    hits.append(idx)

        # 2~4-그램
        for phrase in _ngrams(toks, 2, 4):
            for idx in self._index["ko"].get(_norm_key(phrase), []):
                if idx not in seen:
                    seen.add(idx)
                    hits.append(idx)

        return hits

    def _match_en(self, text_en: str) -> List[int]:
        hits: List[int] = []
        seen: set[int] = set()

        toks = [m.group(0).lower() for m in EN_TOKEN_RE.finditer(text_en)]
        STOP = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "for",
            "nor",
            "with",
            "from",
            "into",
            "onto",
            "over",
            "under",
            "between",
            "to",
            "of",
            "in",
            "on",
            "at",
            "by",
            "as",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
            "you",
            "your",
            "i",
            "we",
            "they",
            "he",
            "she",
            "them",
            "his",
            "her",
            "our",
            "their",
            "so",
            "then",
            "than",
        }
        toks = [t for t in toks if len(t) >= 2 and t not in STOP]

        for t in toks:
            for idx in self._index["en"].get(_norm_key(t), []):
                if idx not in seen:
                    seen.add(idx)
                    hits.append(idx)

        for phrase in _ngrams(toks, 2, 4):
            for idx in self._index["en"].get(_norm_key(phrase), []):
                if idx not in seen:
                    seen.add(idx)
                    hits.append(idx)

        return hits

    def _try_match(self, text: str, src_lang: Optional[str]) -> List[int]:
        """
        src_lang이 주어지면 우선 해당 언어 로직으로,
        없으면 langdetect로 추정 → en 피벗 → ko 피벗 순서로 안전 매칭.
        """
        s = (src_lang or "").strip().lower()

        # 0) 자동 감지
        if not s and _ld_detect:
            try:
                s = (_ld_detect(text) or "").lower()
            except Exception:
                s = ""

        # 1) ko/en 빠른 경로
        if s.startswith("ko"):
            m = self._match_ko(text)
            if m:
                return m
        if s.startswith("en"):
            m = self._match_en(text)
            if m:
                return m

        # 2) en으로 번역해서 en 매칭
        try:
            text_en = translate_text_safe([text], s or "ko", "en")[0]
            m = self._match_en(text_en)
            if m:
                return m
        except Exception:
            pass

        # 3) ko로 번역해서 ko 매칭
        try:
            text_ko = translate_text_safe([text], s or "en", "ko")[0]
            m = self._match_ko(text_ko)
            if m:
                return m
        except Exception:
            pass

        return []

    # -------------------- Public API --------------------

    def explain_in(
        self, text: str, target_lang: str, src_lang: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        text    : 현재 화면에 재생 중인 '자막 한 덩어리'
        src_lang: 위 text의 언어(없으면 자동 감지)
        target_lang: 화면에 표시할 언어(ko면 원문 그대로, 그 외엔 ko→target 번역)
        """
        idxs = self._try_match(text, src_lang)

        out: List[Dict[str, str]] = []
        seen: set[str] = set()

        for idx in idxs:
            e = self.entries[idx]

            # 화면 표시는 항상 CSV의 한글 열 기반
            if target_lang == "ko":
                term_disp = e.term_ko
                def_disp = e.def_ko
            else:
                term_disp = translate_text_safe([e.term_ko], "ko", target_lang)[0]
                def_disp = (
                    translate_text_safe([e.def_ko], "ko", target_lang)[0]
                    if e.def_ko
                    else ""
                )

            # 중복 방지
            k = _norm_key(term_disp)
            if k in seen:
                continue
            seen.add(k)

            out.append(
                {
                    "term": term_disp,             # (표시) 대상 언어
                    "term_original": e.term_ko,    # (원문) 한글
                    "base_lang": "ko",
                    "definition": def_disp,        # (표시) 대상 언어
                }
            )

        return out
