from __future__ import annotations

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

STORAGE_ROOT = Path(getattr(CFG, "STORAGE_ROOT", "storage"))
TASKS_DIR = STORAGE_ROOT / "tasks"
STATIC_DIR = Path("static")
TEMPLATES_DIR = Path("templates")
TASKS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Multi Subtitle App")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- 언어 코드 정규화/파싱 ---
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

def _parse_targets(raw: Optional[str]) -> List[str]:
    if not raw: return ["en"]
    out, seen = [], set()
    for x in raw.split(","):
        c = _canon(x)
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out or ["en"]

# --- Glossary ---
csv_path = Path(getattr(CFG, "GLOSSARY_CSV", "data/표준단어사전_환경부표준사전_2024-11-11.csv"))
glossary = Glossary(csv_path)

# --- 유틸 ---
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
        if p.exists(): return p
    for p in d.glob("*.*"):
        if p.suffix.lower() in (".mp4",".mov",".mkv",".webm",".m4v"): return p
    raise HTTPException(status_code=404, detail="video not found")

# --- 라우트 ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "eco_mileage": 0, "max_size": 2048})

@app.get("/status/{task_id}")
def get_status(task_id: str):
    sp = _task_dir(task_id) / "status.json"
    if not sp.exists():
        return JSONResponse({"state":"PENDING","progress":0.0,"message":"Queued","outputs":{}})
    import json
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
    if not p.exists(): raise HTTPException(status_code=404, detail="vtt not found")
    return FileResponse(str(p), media_type="text/vtt; charset=utf-8")

@app.get("/srt/{task_id}/{lang}", response_class=PlainTextResponse)
def get_srt(task_id: str, lang: str):
    lang = _canon(lang)
    p = _task_dir(task_id) / "srt" / f"sub_{lang}.srt"
    if not p.exists(): raise HTTPException(status_code=404, detail="srt not found")
    return PlainTextResponse(p.read_text(encoding="utf-8", errors="ignore"))

@app.get("/glossary")
def glossary_api(text: str, lang: str = "en", src_lang: Optional[str] = None):
    items = glossary.explain_in(text, target_lang=_canon(lang), src_lang=_canon(src_lang or lang))
    return JSONResponse({"items": items})

# --- 업로드 (Whisper 경로, 다중 타깃) ---
@app.post("/upload")
async def upload(file: UploadFile = File(...),
                 target_langs: str = Form("en"),
                 asr_model: str = Form("base"),
                 src_lang: str = Form("ko")):
    task_id = _new_task_id()
    tdir = _task_dir(task_id)

    ext = Path(file.filename or "original.mp4").suffix or ".mp4"
    dst = tdir / f"original{ext}"
    with dst.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    langs = _parse_targets(target_langs)
    transcribe_and_translate.delay(task_id, str(dst), langs, asr_model, _canon(src_lang))
    return JSONResponse({"task_id": task_id, "queued": True, "langs": langs})

# --- 업로드(SRT 동반, Whisper 생략, 다중 타깃) ---
@app.post("/upload_with_srt")
async def upload_with_srt(video: UploadFile = File(...),
                          srt: UploadFile = File(...),
                          srt_lang: str = Form("ko"),
                          target_langs: str = Form("en")):
    task_id = _new_task_id()
    tdir = _task_dir(task_id)

    v_ext = Path(video.filename or "original.mp4").suffix or ".mp4"
    v_dst = tdir / f"original{v_ext}"
    with v_dst.open("wb") as out:
        shutil.copyfileobj(video.file, out)

    srt_dst = tdir / "srt" / "uploaded.srt"
    with srt_dst.open("wb") as out:
        shutil.copyfileobj(srt.file, out)

    langs = _parse_targets(target_langs)
    translate_srt_only.delay(task_id, str(srt_dst), _canon(srt_lang), langs)
    return JSONResponse({"task_id": task_id, "queued": True, "langs": langs})
