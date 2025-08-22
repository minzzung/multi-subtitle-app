# app/correctors.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any
import re

try:
    from pykospacing import Spacing
except Exception:
    Spacing = None  # type: ignore

try:
    from hanspell import spell_checker
except Exception:
    spell_checker = None  # type: ignore

try:
    import language_tool_python
except Exception:
    language_tool_python = None  # type: ignore

_WS_MULTI = re.compile(r"\s+")
def _norm(s: str) -> str:
    return _WS_MULTI.sub(" ", (s or "").strip())

@dataclass
class CorrStep:
    name: str
    before: str
    after: str
    meta: Dict[str, Any]

class CorrectionPipeline:
    """한국어 문장 교정: pykospacing → hanspell → LanguageTool (선택적, 순서/사용 설정 가능)"""
    def __init__(self, backends: List[str], mode: str = "stacked"):
        self.backends = [b.strip().lower() for b in backends if b.strip()]
        self.mode = (mode or "stacked").lower()
        self._spacing = None
        self._lt_tool = None

    def _ensure_spacing(self):
        if "pykospacing" in self.backends and Spacing and self._spacing is None:
            try:
                self._spacing = Spacing()
            except Exception:
                self._spacing = None

    def _ensure_languagetool(self):
        if "lt" in self.backends and language_tool_python and self._lt_tool is None:
            try:
                try:
                    self._lt_tool = language_tool_python.LanguageTool("ko")
                except Exception:
                    self._lt_tool = language_tool_python.LanguageTool("en-US")
            except Exception:
                self._lt_tool = None

    def correct_ko_line(self, text: str) -> (str, List[CorrStep]):
        steps: List[CorrStep] = []
        cur = _norm(text)

        def apply_step(name, new, meta=None):
            nonlocal cur, steps
            if new is not None and new != cur:
                steps.append(CorrStep(name=name, before=cur, after=new, meta=meta or {}))
                cur = new
                return True
            return False

        # 1) pykospacing
        self._ensure_spacing()
        if "pykospacing" in self.backends and self._spacing:
            try:
                new = self._spacing(cur)
                if apply_step("pykospacing", new) and self.mode == "first_hit":
                    return cur, steps
            except Exception as e:
                steps.append(CorrStep(name="pykospacing_error", before=cur, after=cur, meta={"error": str(e)}))

        # 2) hanspell
        if "hanspell" in self.backends and spell_checker:
            try:
                res = spell_checker.check(cur)
                new = (getattr(res, "checked", None) or cur)
                meta = {}
                try:
                    meta = {k: getattr(res, k, None) for k in ("errors", "original", "checked")}
                except Exception:
                    pass
                if apply_step("hanspell", new, meta) and self.mode == "first_hit":
                    return cur, steps
            except Exception as e:
                steps.append(CorrStep(name="hanspell_error", before=cur, after=cur, meta={"error": str(e)}))

        # 3) LanguageTool
        self._ensure_languagetool()
        if "lt" in self.backends and self._lt_tool:
            try:
                matches = self._lt_tool.check(cur)
                new = language_tool_python.utils.correct(cur, matches) if matches else cur
                if apply_step("languagetool", new, {"matches": len(matches)}) and self.mode == "first_hit":
                    return cur, steps
            except Exception as e:
                steps.append(CorrStep(name="languagetool_error", before=cur, after=cur, meta={"error": str(e)}))

        return cur, steps
