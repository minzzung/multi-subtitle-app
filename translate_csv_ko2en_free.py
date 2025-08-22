# app/translate_csv_ko2en_free.py
import os
import re
import json
import time
from pathlib import Path

import pandas as pd
from tqdm import tqdm
from deep_translator import GoogleTranslator

# -----------------------------
# 경로/설정
# -----------------------------
PROJECT_ROOT = Path(__file__).resolve().parent   # ← 스크립트가 있는 폴더를 루트로
DATA_DIR     = PROJECT_ROOT / "data"
OUT_DIR      = PROJECT_ROOT / "trans_data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 소스 컬럼 이름(우선순위)
SRC_NAME_HEADER = "표준단어명"
SRC_DESC_HEADER = "표준단어 설명"

# 헤더가 애매하면 C열(=index 2), F열(=index 5)을 각각 사용
NAME_FALLBACK_IDX = 2
DESC_FALLBACK_IDX = 5

# 추가될 결과 컬럼 이름
DST_NAME_HEADER = "표준단어 영문명"
DST_ABBR_HEADER = "표준단어 영문약어명"

# 캐시 파일 (중복 번역 최소화)
CACHE_PATH = OUT_DIR / ".ko2en_cache.json"

# 요청 사이 최소 대기 (우회용)
REQUEST_SLEEP_SEC = 0.05

# -----------------------------
# 한글만 찾아서 세그먼트 분리
# -----------------------------
RE_HANGUL = re.compile(r"[가-힣ᄀ-ᇂㄱ-ㅣ]+")

def has_korean(text: str) -> bool:
    return isinstance(text, str) and bool(RE_HANGUL.search(text))

def split_segments(text: str):
    if not isinstance(text, str) or not text:
        return [(False, text)]
    segments = []
    pos = 0
    for m in RE_HANGUL.finditer(text):
        s, e = m.span()
        if s > pos:
            segments.append((False, text[pos:s]))
        segments.append((True, text[s:e]))
        pos = e
    if pos < len(text):
        segments.append((False, text[pos:]))
    return segments

# -----------------------------
# 번역기(무료 GoogleTranslator)
# -----------------------------
class FreeKo2En:
    def __init__(self):
        self._new_engine()
    def _new_engine(self):
        self.engine = GoogleTranslator(source="ko", target="en")
    def translate(self, txt: str) -> str:
        for attempt in range(3):
            try:
                out = self.engine.translate(txt)
                return out if isinstance(out, str) else txt
            except Exception:
                self._new_engine()
                time.sleep(0.5 + attempt * 0.5)
        return txt

# -----------------------------
# 캐시 로드/저장
# -----------------------------
def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache: dict):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

# -----------------------------
# 셀 단위: 한글만 번역
# -----------------------------
def translate_korean_only(cell, translator: FreeKo2En, cache: dict):
    if not has_korean(cell):
        return cell
    segments = split_segments(cell)
    ko_chunks = [seg for is_ko, seg in segments if is_ko]
    uniq = list(dict.fromkeys(ko_chunks))
    to_do = [k for k in uniq if k not in cache]
    for k in to_do:
        cache[k] = translator.translate(k)
        time.sleep(REQUEST_SLEEP_SEC)
    out = []
    for is_ko, seg in segments:
        out.append(cache.get(seg, seg) if is_ko else seg)
    return "".join(out)

# -----------------------------
# CSV 한 개 처리
# -----------------------------
def process_csv(csv_path: Path, translator: FreeKo2En, cache: dict):
    # 인코딩 탐색
    df = None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            df = pd.read_csv(csv_path, encoding=enc)
            break
        except Exception:
            df = None
    if df is None:
        print(f"[SKIP] 읽기 실패: {csv_path.name}")
        return

    # 소스 컬럼 결정 (이름 우선 → 인덱스 대체)
    name_src_col = SRC_NAME_HEADER if SRC_NAME_HEADER in df.columns else (
        df.columns[NAME_FALLBACK_IDX] if NAME_FALLBACK_IDX < len(df.columns) else None
    )
    desc_src_col = SRC_DESC_HEADER if SRC_DESC_HEADER in df.columns else (
        df.columns[DESC_FALLBACK_IDX] if DESC_FALLBACK_IDX < len(df.columns) else None
    )

    if not name_src_col and not desc_src_col:
        print(f"[WARN] 번역 대상 컬럼(C/F or 헤더명)을 찾지 못함: {csv_path.name}")
        return

    print("[INFO] 소스 컬럼 → 결과 컬럼 매핑:")
    if name_src_col:
        print(f"  - {name_src_col} → {DST_NAME_HEADER}")
    if desc_src_col:
        print(f"  - {desc_src_col} → {DST_ABBR_HEADER}")

    # 결과 컬럼 생성(맨 끝에 추가)
    if name_src_col and name_src_col in df.columns:
        tqdm.pandas(desc=f"Translating {name_src_col} -> {DST_NAME_HEADER}")
        df[DST_NAME_HEADER] = df[name_src_col].progress_apply(
            lambda x: translate_korean_only(x, translator, cache)
        )

    if desc_src_col and desc_src_col in df.columns:
        tqdm.pandas(desc=f"Translating {desc_src_col} -> {DST_ABBR_HEADER}")
        df[DST_ABBR_HEADER] = df[desc_src_col].progress_apply(
            lambda x: translate_korean_only(x, translator, cache)
        )

    out_path = OUT_DIR / csv_path.name.replace(".csv", "_en.csv")
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[OK] {csv_path.name} → {out_path.relative_to(PROJECT_ROOT)}")

# -----------------------------
# 메인
# -----------------------------
def main():
    if not DATA_DIR.exists():
        print(f"[INFO] data 폴더가 없습니다: {DATA_DIR}")
        return

    cache = load_cache()
    translator = FreeKo2En()

    csv_files = sorted(DATA_DIR.glob("*.csv"))
    if not csv_files:
        print(f"[INFO] data 폴더에 CSV가 없습니다.")
        return

    for p in csv_files:
        process_csv(p, translator, cache)
        save_cache(cache)

if __name__ == "__main__":
    main()
