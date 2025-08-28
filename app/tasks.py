from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from celery import Celery

from . import config as CFG
from .translation import translate_text_safe

try:
    import srt as srt_lib
except Exception:
    srt_lib = None

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BROKER_URL = getattr(CFG, "CELERY_BROKER_URL", None) or getattr(CFG, "REDIS_URL", None) or "redis://127.0.0.1:6379/1"
RESULT_URL = getattr(CFG, "CELERY_RESULT_BACKEND", None) or BROKER_URL

celery_app = Celery("multi-subtitle-app", broker=BROKER_URL, backend=RESULT_URL)
celery_app.conf.update(
    task_default_queue="msapp_queue",
    task_routes={
        "app.tasks.transcribe_and_translate": {"queue": "msapp_queue"},
        "app.tasks.translate_srt_only": {"queue": "msapp_queue"},
    },
    worker_hijack_root_logger=False,
)

STORAGE_ROOT = Path(getattr(CFG, "STORAGE_ROOT", "storage"))

def _task_dir(task_id: str) -> Path:
    d = STORAGE_ROOT / "tasks" / task_id
    (d / "srt").mkdir(parents=True, exist_ok=True)
    (d / "vtt").mkdir(parents=True, exist_ok=True)
    return d

def _status_path(task_id: str) -> Path:
    return _task_dir(task_id) / "status.json"

def _write_json(path: Path, data: Dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _read_json(path: Path, default: Dict | None = None) -> Dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default or {}
    return default or {}

def _update_status(task_id: str, state: str = "STARTED", progress: float = 0.0, message: str = "", outputs: Dict | None = None):
    sp = _status_path(task_id)
    cur = _read_json(sp, {"state": "PENDING", "progress": 0.0, "message": "", "outputs": {}})
    cur.update(
        {"state": state, "progress": round(float(progress), 3), "message": message,
         "outputs": outputs or cur.get("outputs", {}), "updated_at": datetime.utcnow().isoformat() + "Z"}
    )
    _write_json(sp, cur)

# ---------- SRT/VTT ----------
def segments_to_srt(segments: List[Tuple[float, float, str]]) -> str:
    if srt_lib:
        subs = []
        for i, (st, et, tx) in enumerate(segments, 1):
            subs.append(srt_lib.Subtitle(index=i, start=srt_lib.timedelta(seconds=st), end=srt_lib.timedelta(seconds=et), content=tx))
        return srt_lib.compose(subs)

    def _fmt(t):
        ms = int(round((t - int(t)) * 1000))
        s = int(t) % 60; m = (int(t)//60) % 60; h = int(t)//3600
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    lines = []
    for i, (st, et, tx) in enumerate(segments, 1):
        lines += [str(i), f"{_fmt(st)} --> {_fmt(et)}", tx, ""]
    return "\n".join(lines)

_SRT_TIME = re.compile(r"^\s*\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}\s*$")

def srt_to_vtt(srt_text: str) -> str:
    """
    엄격한 WebVTT로 변환 (인덱스라인 제거, 콤마→점, 헤더 추가).
    """
    blocks = srt_text.replace("\r\n", "\n").strip().split("\n\n")
    out = ["WEBVTT", ""]
    for b in blocks:
        if not b.strip():
            continue
        lines = b.split("\n")
        # 첫 줄이 숫자면(인덱스) 제거
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines:
            continue
        # 타임라인
        if not _SRT_TIME.match(lines[0]):
            # 잘못된 블록은 스킵
            continue
        timing = lines[0].replace(",", ".")
        content = "\n".join(lines[1:]).strip()
        out.append(timing)
        out.append(content)
        out.append("")  # 블록 구분
    return "\n".join(out).strip() + "\n"

def save_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

# ---------- ASR ----------
def transcribe_whisper(src_path: str, model_size: str = "base") -> List[Tuple[float, float, str]]:
    import torch, whisper
    device = "cuda" if torch.cuda.is_available() and getattr(CFG, "TORCH_DEVICE", "auto") in ("auto","cuda") else "cpu"
    model = whisper.load_model(model_size, device=device)
    result = model.transcribe(src_path, language="ko", fp16=(device=="cuda"), verbose=False)
    out = []
    for seg in result.get("segments", []):
        out.append((float(seg.get("start",0.0)), float(seg.get("end",0.0)), (seg.get("text") or "").strip()))
    return out

def transcribe_faster_whisper(src_path: str, model_size: str = "base") -> List[Tuple[float, float, str]]:
    from faster_whisper import WhisperModel
    compute_type = getattr(CFG, "FASTER_WHISPER_COMPUTE_TYPE", "auto")
    model = WhisperModel(model_size, device="auto", compute_type=compute_type)
    segments, _ = model.transcribe(src_path, language="ko", vad_filter=True)
    return [(float(s.start), float(s.end), (s.text or "").strip()) for s in segments]

def do_asr(src_path: str, model_size: str = "base") -> List[Tuple[float, float, str]]:
    return transcribe_faster_whisper(src_path, model_size) if (getattr(CFG,"ASR_BACKEND","whisper")=="faster_whisper") \
           else transcribe_whisper(src_path, model_size)

# ---------- 번역 ----------
def _extract_dialog_lines(block: str):
    lines = block.split("\n")
    i = 0
    if lines and lines[0].strip().isdigit(): i = 1
    if i >= len(lines) or "-->" not in lines[i]: return [], [], lines
    dialog = lines[i+1:]; idx = list(range(i+1, len(lines)))
    return dialog, idx, lines

def srt_text_translate(text: str, src_lang: str, tgt_lang: str) -> str:
    blocks = text.replace("\r\n","\n").split("\n\n")
    new=[]
    for b in blocks:
        if not b.strip(): continue
        dialog, idx, lines = _extract_dialog_lines(b)
        if not dialog: new.append(b); continue
        tlines = translate_text_safe(dialog, src_lang, tgt_lang)
        for j,pos in enumerate(idx): lines[pos] = tlines[j]
        new.append("\n".join(lines))
    return "\n\n".join(new) + "\n"

# ---------- TASKS (다중 타깃) ----------
@celery_app.task(name="app.tasks.transcribe_and_translate")
def transcribe_and_translate(task_id: str, src_path: str, target_langs: List[str], asr_model: str, src_lang: str="ko"):
    try:
        tdir = _task_dir(task_id)

        # 1) ASR
        backend = getattr(CFG,"ASR_BACKEND","whisper")
        _update_status(task_id,"STARTED",0.05,f"ASR ({'Whisper' if backend=='whisper' else 'Faster-Whisper'}) extracting…")
        segments = do_asr(src_path, model_size=asr_model or "base")
        srt_ko = segments_to_srt(segments)
        save_text(tdir / "srt" / "sub_ko.srt", srt_ko)

        # 2) ko.vtt  (교정 단계 제거)
        vtt_map = {"ko": f"/vtt/{task_id}/ko"}
        save_text(tdir / "vtt" / "sub_ko.vtt", srt_to_vtt(srt_ko))
        _update_status(task_id,"STARTED",0.5,"Korean track ready", {"vtt": vtt_map})

        # 3) 번역(모든 선택)
        n = max(1, len(target_langs))
        for i, tl in enumerate(target_langs, start=1):
            prog = 0.5 + 0.49*(i/n)
            if tl == "ko":
                vtt_map["ko"] = f"/vtt/{task_id}/ko"
            else:
                srt_t = srt_text_translate(srt_ko, "ko", tl)
                save_text(tdir / "srt" / f"sub_{tl}.srt", srt_t)
                save_text(tdir / "vtt" / f"sub_{tl}.vtt", srt_to_vtt(srt_t))
                vtt_map[tl] = f"/vtt/{task_id}/{tl}"
            _update_status(task_id,"STARTED",prog,f"Translated {i}/{n} ({tl})", {"vtt": vtt_map})

        _update_status(task_id,"SUCCESS",1.0,"Completed",{"vtt": vtt_map})
        return {"vtt": vtt_map}
    except Exception as e:
        log.exception("ASR+Translate failed: %s", e)
        _update_status(task_id,"FAILURE",1.0,f"Error: {e}")
        raise

@celery_app.task(name="app.tasks.translate_srt_only")
def translate_srt_only(task_id: str, src_srt_path: str, src_lang: str, target_langs: List[str]):
    try:
        tdir = _task_dir(task_id)
        srt_src = Path(src_srt_path).read_text(encoding="utf-8", errors="ignore")
        src_lang = (src_lang or "ko").lower()

        # (교정 제거) 원본 SRT를 그대로 사용
        save_text(tdir / "srt" / f"sub_{src_lang}.srt", srt_src)
        vtt_map = {src_lang: f"/vtt/{task_id}/{src_lang}"}
        save_text(tdir / "vtt" / f"sub_{src_lang}.vtt", srt_to_vtt(srt_src))
        _update_status(task_id,"STARTED",0.5,"Source track ready",{"vtt": vtt_map})

        n = max(1, len(target_langs))
        for i, tl in enumerate(target_langs, start=1):
            prog = 0.5 + 0.49*(i/n)
            if tl == src_lang:
                vtt_map[tl] = f"/vtt/{task_id}/{tl}"
            else:
                srt_t = srt_text_translate(srt_src, src_lang, tl)
                save_text(tdir / "srt" / f"sub_{tl}.srt", srt_t)
                save_text(tdir / "vtt" / f"sub_{tl}.vtt", srt_to_vtt(srt_t))
                vtt_map[tl] = f"/vtt/{task_id}/{tl}"
            _update_status(task_id,"STARTED",prog,f"Translated {i}/{n} ({tl})", {"vtt": vtt_map})

        _update_status(task_id,"SUCCESS",1.0,"Completed",{"vtt": vtt_map})
        return {"vtt": vtt_map}
    except Exception as e:
        log.exception("Translate-only failed: %s", e)
        _update_status(task_id,"FAILURE",1.0,f"Error: {e}")
        raise
