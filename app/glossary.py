from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import csv, re, sys

from .translation import translate_text_safe

# ───────────────────────── Normalization ─────────────────────────
_WS = re.compile(r"\s+")
ENG_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\.\-]*")

def _normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[／/]", " ", s)
    s = re.sub(r"[(){}\[\]·•∙ㆍ]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s

def _norm_key(s: str) -> str:
    s = _normalize_text(s).lower()
    s = _WS.sub("", s)
    s = s.replace("-", "").replace(".", "").replace(",", "")
    return s

# ───────────────────────── Script/Lang detection ─────────────────
# 간단/안전한 스크립트 판별(정규식): 한글/라틴/가나/가타카나/한자
_RE_HANGUL = re.compile(r"[\uac00-\ud7a3]")
_RE_LATIN  = re.compile(r"[A-Za-z]")
_RE_HIRAGANA = re.compile(r"[\u3040-\u309f]")
_RE_KATAKANA = re.compile(r"[\u30a0-\u30ff]")
_RE_CJK     = re.compile(r"[\u4e00-\u9fff]")

def detect_script_lang(s: str) -> str:
    """문자열의 주 스크립트를 ko/en/ja/zh/other 로 추정."""
    t = s or ""
    # 비율로 대충 판단
    counts = {
        "ko": len(_RE_HANGUL.findall(t)),
        "en": len(_RE_LATIN.findall(t)),
        "ja": len(_RE_HIRAGANA.findall(t)) + len(_RE_KATAKANA.findall(t)),
        "zh": len(_RE_CJK.findall(t)),
    }
    # 동률/전부0이면 other
    top = max(counts, key=counts.get)
    return top if counts[top] > 0 else "other"

# ───────────────────────── Data model ───────────────────────────
@dataclass
class GlossaryItem:
    term: str
    term_original: str
    definition: str
    src_lang: str = "ko"
    tgt_lang: str = "ko"

# ───────────────────────── Glossary core ────────────────────────
class Glossary:
    """
    매칭 규칙(요청사항):
      - '동일한 언어(스크립트)'만 매칭
        (cue 텍스트 스크립트 == 용어 스크립트 일 때만 후보)
      - 1) 정규화 키 정확 일치
        2) 없으면 '부분 포함' 느슨 매칭 (여전히 동일 스크립트 제한)
      - 표시 언어는 target_lang으로 번역 (term/definition)
    """
    def __init__(self, csv_path: Path,
                 term_col: str = "표준단어명",
                 def_col: str = "표준단어 설명",
                 encoding: str = "utf-8-sig"):
        self.csv_path = Path(csv_path)
        self.term_col = term_col
        self.def_col = def_col
        self.encoding = encoding

        self.rows: List[Dict[str, str]] = []                 # [{"name","desc","lang","key"}...]
        self.idx_by_lang: Dict[str, Dict[str, List[int]]] = { # lang -> norm_key -> [idx]
            "ko": {}, "en": {}, "ja": {}, "zh": {}, "other": {}
        }
        self._enc_used: Optional[str] = None
        self._load()

    # ── load CSV ────────────────────────────────────────────────
    def _load(self) -> None:
        if not self.csv_path.exists():
            print(f"[Glossary] CSV not found: {self.csv_path}", file=sys.stderr)
            return

        tried = []
        for enc in (self.encoding, "utf-8-sig", "utf-8", "cp949"):
            if not enc:
                continue
            try:
                with self.csv_path.open("r", encoding=enc, newline="") as f:
                    rd = csv.DictReader(f)
                    fields = [c.strip() for c in (rd.fieldnames or [])]
                    cols = {c: c for c in fields}

                    term_key = (
                        cols.get(self.term_col) or cols.get("용어") or cols.get("term")
                        or cols.get("표준단어명") or (fields[0] if fields else None)
                    )
                    def_key  = (
                        cols.get(self.def_col)  or cols.get("설명") or cols.get("definition")
                        or cols.get("표준단어 설명") or (fields[-1] if fields else None)
                    )
                    if not term_key:
                        raise RuntimeError("No term column detected")

                    self.rows.clear()
                    for d in self.idx_by_lang.values():
                        d.clear()

                    for i, row in enumerate(rd):
                        name = (row.get(term_key) or "").strip()
                        if not name:
                            continue
                        desc = (row.get(def_key) or "").strip() if def_key else ""
                        lang = detect_script_lang(name)
                        key  = _norm_key(name)
                        self.rows.append({"name": name, "desc": desc, "lang": lang, "key": key})
                        if key:
                            self.idx_by_lang.setdefault(lang, {}).setdefault(key, []).append(i)

                    self._enc_used = enc
                    print(f"[Glossary] Loaded {len(self.rows)} rows from {self.csv_path} (encoding={enc})")
                    return
            except Exception as e:
                tried.append(f"{enc}: {e}")

        print(f"[Glossary] Failed to load CSV: {self.csv_path} | tried={tried}", file=sys.stderr)

    # ── candidate keys by text (same-script only later) ────────
    def _candidate_keys(self, text: str) -> List[str]:
        text = _normalize_text(text or "")
        if not text:
            return []
        toks: List[str] = []
        toks += [t for t in re.split(r"\s+", text) if t]
        toks += [m.group(0) for m in ENG_TOKEN.finditer(text)]
        keys = set()
        for t in toks:
            keys.add(_norm_key(t))
        for n in (2, 3, 4):
            for i in range(0, max(0, len(toks) - n + 1)):
                keys.add(_norm_key("".join(toks[i:i+n])))
        keys = {k for k in keys if len(k) >= 2}
        return list(keys)

    # ── main API ────────────────────────────────────────────────
    def explain_in(self, text: str, target_lang: str = "ko",
                   src_lang: Optional[str] = None, limit: int = 20) -> List[dict]:
        """
        text(현재 자막 문장)의 스크립트를 추정(또는 src_lang 사용)해서
        '같은 스크립트'의 용어만 대상으로 매칭.
        출력은 target_lang으로 번역(표시 목적).
        """
        target_lang = (target_lang or "ko").lower()
        text_lang = (src_lang or detect_script_lang(text or "")).lower()
        if text_lang not in self.idx_by_lang:
            text_lang = "other"

        out: List[GlossaryItem] = []
        seen = set()

        # 1) 정규화 키 정확 일치 (same-script dict만 사용)
        cand = self._candidate_keys(text)
        idx_map = self.idx_by_lang.get(text_lang, {})
        for k in cand:
            if not k or k in seen:
                continue
            seen.add(k)
            for i in idx_map.get(k, []):
                row = self.rows[i]
                disp_term, disp_def = self._display_pair(row["name"], row["desc"], row["lang"], target_lang)
                out.append(GlossaryItem(
                    term=disp_term, term_original=row["name"],
                    definition=disp_def, src_lang=row["lang"], tgt_lang=target_lang
                ))
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break

        if out:
            return [o.__dict__ for o in out]

        # 2) 부분 포함 느슨 매칭 (여전히 same-script로만)
        text_norm = _norm_key(text)
        if not text_norm:
            return []
        for i, row in enumerate(self.rows):
            if row["lang"] != text_lang:
                continue
            key = row["key"]
            if key and key in text_norm:
                disp_term, disp_def = self._display_pair(row["name"], row["desc"], row["lang"], target_lang)
                out.append(GlossaryItem(
                    term=disp_term, term_original=row["name"],
                    definition=disp_def, src_lang=row["lang"], tgt_lang=target_lang
                ))
                if len(out) >= limit:
                    break

        return [o.__dict__ for o in out]

    # ── translate-for-display helper ────────────────────────────
    def _display_pair(self, name: str, desc: str, name_lang: str, target_lang: str) -> Tuple[str, str]:
        """표시 언어(target_lang)로 변환. 실패 시 원문 그대로."""
        if (target_lang or "ko").lower() == (name_lang or "ko").lower():
            return name, desc
        try:
            name_t = translate_text_safe(name, name_lang, target_lang)
            desc_t = translate_text_safe(desc, name_lang, target_lang) if desc else desc
            return name_t, (desc_t or "")
        except Exception:
            return name, desc
