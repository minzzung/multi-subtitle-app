# app/main.py
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config as CFG
from .glossary import Glossary
from .tasks import transcribe_and_translate, translate_srt_only
from .translation import translate_text_safe

# ─────────────────────────────────────────────────────────────
# 경로/디렉터리
# ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_ROOT = Path(getattr(CFG, "STORAGE_ROOT", str(BASE_DIR / "storage")))
TASKS_DIR = STORAGE_ROOT / "tasks"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

TASKS_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# FastAPI
# ─────────────────────────────────────────────────────────────
app = FastAPI(title="Multi Subtitle App")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ─────────────────────────────────────────────────────────────
# Glossary 인스턴스 (CSV 경로/컬럼/인코딩은 config 사용)
# ─────────────────────────────────────────────────────────────
csv_path = Path(CFG.GLOSSARY_CSV)
glossary = Glossary(
    csv_path=csv_path,
    term_col=CFG.GLOSSARY_TERM_COL,
    def_col=CFG.GLOSSARY_DEF_COL,
    encoding=CFG.GLOSSARY_ENCODING,
)

# ─────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────
_ALIAS = {"kr":"ko","kor":"ko","jp":"ja","jap":"ja","cn":"zh","chs":"zh","chi":"zh","fil":"tl"}
def _canon(code: str) -> str:
    code = (code or "").strip().lower()
    return _ALIAS.get(code, code)

def _parse_targets(raw: Optional[str]) -> List[str]:
    if not raw:
        return ["en"]
    seen, out = set(), []
    for x in raw.split(","):
        c = _canon(x)
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out or ["en"]

def _new_task_id() -> str:
    return uuid.uuid4().hex[:12]

def _task_dir(task_id: str) -> Path:
    d = TASKS_DIR / task_id
    (d / "srt").mkdir(parents=True, exist_ok=True)
    (d / "vtt").mkdir(parents=True, exist_ok=True)
    return d

def _guess_video_path(task_id: str) -> Path:
    d = _task_dir(task_id)
    for ext in (".mp4",".mov",".mkv",".webm",".m4v"):
        p = d / f"original{ext}"
        if p.exists():
            return p
    for p in d.glob("*.*"):
        if p.suffix.lower() in (".mp4",".mov",".mkv",".webm",".m4v"):
            return p
    raise HTTPException(status_code=404, detail="video not found")

# ─────────────────────────────────────────────────────────────
# 시작 시 예열: 번역 백엔드 한 번 깨워두기(초기 지연 감소)
# ─────────────────────────────────────────────────────────────
@app.on_event("startup")
async def warmup():
    try:
        translate_text_safe(["warmup"], "ko", "en")
        translate_text_safe(["warmup"], "en", "ko")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "max_size": CFG.MAX_FILE_SIZE_MB})

@app.get("/status/{task_id}")
def get_status(task_id: str):
    sp = _task_dir(task_id) / "status.json"
    if not sp.exists():
        return JSONResponse({"state":"PENDING","progress":0.0,"message":"Queued","outputs":{}})
    try:
        return JSONResponse(json.loads(sp.read_text(encoding="utf-8")))
    except Exception:
        return JSONResponse({"state":"PENDING","progress":0.0,"message":"Queued","outputs":{}})

@app.get("/video/{task_id}")
def get_video(task_id: str):
    return FileResponse(str(_guess_video_path(task_id)), media_type="video/mp4")

@app.get("/vtt/{task_id}/{lang}")
def get_vtt(task_id: str, lang: str):
    lang = _canon(lang)
    p = _task_dir(task_id) / "vtt" / f"sub_{lang}.vtt"
    if not p.exists():
        raise HTTPException(status_code=404, detail="vtt not found")
    return FileResponse(str(p), media_type="text/vtt; charset=utf-8")

@app.get("/srt/{task_id}/{lang}", response_class=PlainTextResponse)
def get_srt(task_id: str, lang: str):
    lang = _canon(lang)
    p = _task_dir(task_id) / "srt" / f"sub_{lang}.srt"
    if not p.exists():
        raise HTTPException(status_code=404, detail="srt not found")
    return PlainTextResponse(p.read_text(encoding="utf-8", errors="ignore"))

# ── Glossary 스트리밍
@app.get("/glossary")
def glossary_api(text: str, lang: str = "ko", src_lang: Optional[str] = None):
    """
    text: 현재 cue의 자막 텍스트
    lang: 표시 언어(플레이어에서 사용자가 고른 언어)
    src_lang: 매칭 언어(현재 트랙의 언어)
    """
    items = glossary.explain_in(
        text,
        target_lang=_canon(lang),                  # ← 표시용 번역 언어
        src_lang=_canon(src_lang or lang),        # ← 매칭은 트랙의 실제 언어로
    )
    return JSONResponse({"items": items})

# ── SRT 전체 일괄 Glossary (필요시)
def _iter_srt_blocks(srt_text: str):
    blocks = srt_text.replace("\r\n","\n").strip().split("\n\n")
    for b in blocks:
        if not b.strip(): 
            continue
        lines = b.split("\n")
        idx=None; i=0
        if lines and lines[0].strip().isdigit():
            idx=lines[0].strip(); i=1
        if i>=len(lines) or "-->" not in lines[i]:
            continue
        text = "\n".join(lines[i+1:]).strip()
        yield (idx or lines[i], text)

@app.get("/glossary_srt/{task_id}/{lang}")
def glossary_srt(task_id: str, lang: str):
    """특정 언어 SRT를 cue 단위로 스캔하여 Glossary 항목을 반환 (Glossary는 target_lang으로 번역)"""
    lang = _canon(lang)
    p = _task_dir(task_id) / "srt" / f"sub_{lang}.srt"
    if not p.exists():
        raise HTTPException(status_code=404, detail="srt not found")
    srt_text = p.read_text(encoding="utf-8", errors="ignore")

    result = {}
    for idx, timing, body in _iter_srt_blocks(srt_text):
        cue_id = idx or timing
        # ✅ 여기 수정: target_lang은 자막 선택 언어, src_lang은 생략
        items = glossary.explain_in(body, target_lang=lang)
        result[cue_id] = items

    return JSONResponse({"items_by_id": result})

# ── 업로드(ASR 포함)
@app.post("/upload")
async def upload(file: UploadFile = File(...),
                 target_langs: str = Form("en"),
                 asr_model: str = Form(getattr(CFG,"WHISPER_MODEL","base")),
                 src_lang: str = Form("ko")):
    task_id = _new_task_id()
    ext = Path(file.filename or "original.mp4").suffix or ".mp4"
    dst = (_task_dir(task_id) / f"original{ext}").resolve()
    with dst.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    langs = _parse_targets(target_langs)
    transcribe_and_translate.apply_async(
        args=[task_id, str(dst), langs, _canon(asr_model or "base"), _canon(src_lang)],
        queue="msapp_queue"
    )
    return JSONResponse({"task_id": task_id, "queued": True, "langs": langs})

# ── 업로드(SRT만 번역)
@app.post("/upload_with_srt")
async def upload_with_srt(video: UploadFile = File(...),
                          srt: UploadFile = File(...),
                          srt_lang: str = Form("ko"),
                          target_langs: str = Form("en")):
    task_id = _new_task_id()
    v_ext = Path(video.filename or "original.mp4").suffix or ".mp4"
    v_dst = (_task_dir(task_id) / f"original{v_ext}").resolve()
    with v_dst.open("wb") as out:
        shutil.copyfileobj(video.file, out)
    srt_dst = (_task_dir(task_id) / "srt" / "uploaded.srt").resolve()
    with srt_dst.open("wb") as out:
        shutil.copyfileobj(srt.file, out)

    langs = _parse_targets(target_langs)
    translate_srt_only.apply_async(
        args=[task_id, str(srt_dst), _canon(srt_lang), langs],
        queue="msapp_queue"
    )
    return JSONResponse({"task_id": task_id, "queued": True, "langs": langs})
