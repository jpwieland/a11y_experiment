"""Registro eficiente de prompts e respostas do LLM para auditoria e debug.

Formato: JSONL, um arquivo por modelo, append atômico (sem lock necessário
em contexto single-writer). Entradas compactas: sem indentação, uma por linha.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PromptLogger:
    """Salva cada chamada LLM como uma linha JSON num arquivo por modelo."""

    def __init__(self, log_dir: Path, model_id: str) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        safe = model_id.replace("/", "_").replace(":", "_")
        self._path = log_dir / f"{safe}.jsonl"

    def append(
        self,
        *,
        file: str,
        attempt: int,
        system_prompt: str,
        user_prompt: str,
        response: str,
        tokens_prompt: int | None,
        tokens_completion: int | None,
        time_s: float | None,
        success: bool,
        extra: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "file": file,
            "attempt": attempt,
            "system": system_prompt,
            "user": user_prompt,
            "response": response,
            "tokens_prompt": tokens_prompt,
            "tokens_completion": tokens_completion,
            "time_s": round(time_s, 3) if time_s is not None else None,
            "success": success,
        }
        if extra:
            entry.update(extra)
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # nunca interrompe o experimento por falha de log
