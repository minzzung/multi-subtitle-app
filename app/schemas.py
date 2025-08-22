# app/schemas.py
from __future__ import annotations
from typing import List, Dict
from pydantic import BaseModel

class UploadResponse(BaseModel):
    task_id: str
    filename: str
    target_langs: List[str]

class StatusResponse(BaseModel):
    state: str
    progress: float
    message: str
    outputs: Dict

class GlossaryItem(BaseModel):
    term: str
    term_original: str
    base_lang: str
    definition: str

class GlossaryResponse(BaseModel):
    items: List[GlossaryItem]
