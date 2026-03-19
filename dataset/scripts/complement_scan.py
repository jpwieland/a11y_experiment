#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Complement Scan — corrige ferramentas com problema e preenche os dados faltantes.

Fluxo:
  1. DIAGNÓSTICO  — testa cada ferramenta com um componente real
  2. CORREÇÃO     — instala/configura o que estiver faltando
  3. PLANEJAMENTO — identifica projetos que têm dados só do playwright+axe
  4. SCAN         — re-escaneia esses projetos com as ferramentas ausentes
  5. MERGE        — funde os novos findings nos arquivos existentes (sem duplicatas)
  6. REBUILD      — reconstrói dataset_findings.jsonl consolidado

Uso:
    python dataset/scripts/complement_scan.py              # fluxo completo
    python dataset/scripts/complement_scan.py --dry-run   # só diagnóstico
    python dataset/scripts/complement_scan.py --workers 3
    python dataset/scripts/complement_scan.py --tools eslint pa11y
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
SNAPSHOTS_DIR = DATASET_ROOT / "snapshots"
RESULTS_DIR = DATASET_ROOT / "results"
CATALOG_PATH = DATASET_ROOT / "catalog" / "projects.yaml"

sys.path.insert(0, str(REPO_ROOT))

# ── Paleta ANSI ───────────────────────────────────────────────────────────────
R      = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BLUE   = "\033[94m"
MAGENTA = "\033[95m"
UP     = "\033[F"   # cursor up 1 linha
CLR    = "\033[K"   # limpar até fim da linha

OK   = f"{GREEN}{BOLD}✔{R}"
FAIL = f"{RED}{BOLD}✘{R}"
WARN = f"{YELLOW}⚠{R}"
INFO = f"{CYAN}ℹ{R}"

# Componente React com problemas a11y conhecidos (para teste de ferramenta)
_TEST_TSX = """\
export default function TestComponent() {
  return (
    <div>
      <img src="photo.jpg" />
      <button></button>
      <div onClick={() => {}}>Clique aqui</div>
      <a href="#">Link</a>
      <input type="text" placeholder="Nome" />
    </div>
  );
}
"""

# ── Estado compartilhado entre workers e display ──────────────────────────────
_state: dict[str, Any] = {
    "phase": "init",        # init | diagnosing | fixing | planning | scanning | done
    "phase_label": "",
    "active": {},           # pid → {pct, files_done, files_total, new_by_tool}
    "completed": [],        # [{pid, new_total, by_tool, duration_s}]
    "errors": [],           # [{pid, msg}]
    "total_projects": 0,
    "done_projects": 0,
    "new_total": 0,
    "new_by_tool": defaultdict(int),
    "start_time": time.time(),
    "stop_display": False,
    "diag": {},             # tool → {ok, version, msg}
    "fix_log": [],          # mensagens do processo de fix
    "tools_to_add": [],     # ferramentas que serão adicionadas
    "lock": threading.Lock(),
}

_DISPLAY_LINES = 0  # quantas linhas o display ocupou na última renderização


# ─── Utilidades ───────────────────────────────────────────────────────────────

def _w() -> int:
    try:
        return min(os.get_terminal_size().columns, 100)
    except OSError:
        return 88

def _bar(v: float, total: float, w: int = 24, color: str = CYAN) -> str:
    if total <= 0:
        return DIM + "░" * w + R
    filled = round(min(v / total, 1.0) * w)
    return color + "█" * filled + DIM + "░" * (w - filled) + R

def _elapsed(t0: float) -> str:
    s = int(time.time() - t0)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return (f"{h}h{m:02d}m" if h else f"{m}m{sec:02d}s")

def _eta(t0: float, done: int, total: int) -> str:
    if done <= 0 or total <= 0:
        return "—"
    elapsed = time.time() - t0
    remaining = elapsed / done * (total - done)
    m, s = int(remaining) // 60, int(remaining) % 60
    return f"{m}m{s:02d}s"

_IS_WINDOWS = platform.system() == "Windows"


def _run(cmd: list[str], env: dict | None = None,
         timeout: int = 30) -> tuple[int, str, str]:
    """Executa comando com suporte a Windows (.cmd não é resolvido sem shell=True)."""
    try:
        if _IS_WINDOWS:
            # No Windows, subprocess sem shell=True não resolve .cmd do PATH
            # (npx.cmd, eslint.cmd, pa11y.cmd). Usar shell=True + string resolve.
            cmd_str = subprocess.list2cmdline(cmd)
            r = subprocess.run(cmd_str, shell=True, capture_output=True, text=True,
                               timeout=timeout, env=env)
        else:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", "timeout"
    except Exception as e:
        return -3, "", str(e)


# ─── Fase 1: Diagnóstico ───────────────────────────────────────────────────────

async def _test_eslint() -> dict:
    """Testa ESLint + jsx-a11y com componente real."""
    rc, ver, _ = _run(["npx", "--no-install", "eslint", "--version"])
    if rc != 0:
        rc, ver, _ = _run(["eslint", "--version"])
    if rc != 0:
        return {"ok": False, "version": None, "findings": 0,
                "msg": "ESLint não encontrado"}

    version = ver.strip()
    major = int(version.lstrip("v").split(".")[0]) if version else 8

    # Verificar plugin
    rc2, npm_root, _ = _run(["npm", "root", "-g"])
    npm_root = npm_root.strip() if rc2 == 0 else ""
    plugin_ok = bool(npm_root and (Path(npm_root) / "eslint-plugin-jsx-a11y").exists())
    if not plugin_ok:
        return {"ok": False, "version": version, "findings": 0, "npm_root": npm_root,
                "msg": f"eslint-plugin-jsx-a11y não encontrado em {npm_root or '(npm root -g falhou)'}"}

    # Teste real
    from a11y_autofix.scanner.eslint import EslintRunner
    runner = EslintRunner()
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "Test.tsx"
        f.write_text(_TEST_TSX, encoding="utf-8")
        findings = await runner.run_on_source(f, "WCAG2AA")

    return {"ok": len(findings) > 0, "version": version, "findings": len(findings),
            "npm_root": npm_root, "major": major,
            "msg": f"{len(findings)} findings no teste" if findings else
                   "0 findings — plugin pode estar mal configurado"}


async def _test_pa11y() -> dict:
    """Testa pa11y com HTML estático."""
    rc, ver, _ = _run(["pa11y", "--version"])
    if rc != 0:
        rc, ver, _ = _run(["npx", "pa11y", "--version"])
    if rc != 0:
        return {"ok": False, "version": None, "findings": 0,
                "msg": "pa11y não encontrado"}

    version = ver.strip()
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "test.html"
        html.write_text(
            '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<title>T</title></head><body>'
            '<img src="x.jpg"><button></button>'
            '<input type="text"><a href="#">L</a></body></html>',
            encoding="utf-8",
        )
        url = f"file://{html.resolve()}"
        rc2, out, err = _run(["pa11y", "--reporter", "json",
                               "--standard", "WCAG2AA",
                               "--timeout", "20000", url], timeout=30)

    if rc2 not in (0, 2) or not out.strip():
        return {"ok": False, "version": version, "findings": 0,
                "msg": f"rc={rc2}  {err.strip()[:120]}"}
    try:
        data = json.loads(out)
        n = len(data) if isinstance(data, list) else 0
        return {"ok": n > 0, "version": version, "findings": n,
                "msg": f"{n} findings no teste"}
    except Exception:
        return {"ok": False, "version": version, "findings": 0,
                "msg": "JSON inválido na saída"}


async def _test_playwright() -> dict:
    """Verifica playwright+axe."""
    try:
        import playwright
        version = getattr(playwright, "__version__", "?")
    except ImportError:
        return {"ok": False, "version": None, "findings": 0,
                "msg": "playwright não instalado"}

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
            await b.close()
        return {"ok": True, "version": version, "findings": -1,
                "msg": "Chromium OK"}
    except Exception as e:
        return {"ok": False, "version": version, "findings": 0,
                "msg": f"Chromium falhou: {str(e)[:80]}"}


async def diagnose() -> dict[str, dict]:
    with _state["lock"]:
        _state["phase"] = "diagnosing"
        _state["phase_label"] = "Testando ferramentas..."

    results = {}
    for name, coro in [
        ("playwright+axe", _test_playwright()),
        ("pa11y",          _test_pa11y()),
        ("eslint",         _test_eslint()),
    ]:
        with _state["lock"]:
            _state["diag"][name] = {"ok": None, "msg": "testando..."}
        result = await coro
        with _state["lock"]:
            _state["diag"][name] = result
        results[name] = result

    return results


# ─── Fase 2: Correção automática ──────────────────────────────────────────────

def _npm_install_global(*packages: str) -> tuple[bool, str]:
    """Instala pacotes npm globalmente e retorna (sucesso, output)."""
    cmd = ["npm", "install", "-g", *packages]
    rc, out, err = _run(cmd, timeout=120)
    combined = (out + err).strip()
    return rc == 0, combined


async def fix_tools(diag: dict[str, dict]) -> list[str]:
    """
    Tenta corrigir automaticamente as ferramentas com problema.
    Retorna lista de ferramentas que conseguimos corrigir.
    """
    fixed = []

    def _log(msg: str) -> None:
        with _state["lock"]:
            _state["fix_log"].append(msg)

    # ESLint
    eslint = diag.get("eslint", {})
    if not eslint.get("ok"):
        if not eslint.get("version"):
            # ESLint não instalado
            _log("Instalando ESLint + jsx-a11y + @typescript-eslint/parser...")
            ok, out = _npm_install_global(
                "eslint",
                "eslint-plugin-jsx-a11y",
                "@typescript-eslint/parser",
            )
            _log(f"  {'✔' if ok else '✘'} npm install: {out[:120]}")
            if ok:
                fixed.append("eslint")
        else:
            # ESLint existe mas plugin ausente
            _log("Instalando eslint-plugin-jsx-a11y + @typescript-eslint/parser...")
            ok, out = _npm_install_global(
                "eslint-plugin-jsx-a11y",
                "@typescript-eslint/parser",
            )
            _log(f"  {'✔' if ok else '✘'} npm install: {out[:120]}")
            if ok:
                fixed.append("eslint")

    # Pa11y
    pa11y = diag.get("pa11y", {})
    if not pa11y.get("ok"):
        if not pa11y.get("version"):
            _log("Instalando pa11y...")
            ok, out = _npm_install_global("pa11y")
            _log(f"  {'✔' if ok else '✘'} npm install: {out[:120]}")
            if ok:
                fixed.append("pa11y")
        else:
            _log(f"pa11y {pa11y['version']} instalado mas sem findings no teste.")
            _log("  Isso pode ser problema de Chromium/CDN. Tentando mesmo assim.")
            fixed.append("pa11y")

    return fixed


# ─── Fase 3: Planejamento ─────────────────────────────────────────────────────

def find_projects_to_complement(tools_to_add: list[str]) -> list[dict]:
    """
    Encontra projetos já escaneados onde as ferramentas indicadas
    não contribuíram com nenhum finding.

    Retorna lista de {id, path, snapshot_path, missing_tools, existing_count}.
    """
    projects = []
    tool_values = {
        "eslint":         "eslint-jsx-a11y",
        "pa11y":          "pa11y",
        "playwright+axe": "playwright+axe",
        "axe-core":       "axe-core",
    }

    for proj_dir in sorted(RESULTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue
        fp = proj_dir / "findings.jsonl"
        if not fp.exists():
            continue

        existing: list[dict] = []
        tools_seen: set[str] = set()
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                f = json.loads(line)
                existing.append(f)
                for t in (f.get("found_by") or []):
                    tools_seen.add(str(t))
            except Exception:
                pass

        # Quais das ferramentas que queremos adicionar estão ausentes?
        missing = [
            t for t in tools_to_add
            if tool_values.get(t, t) not in tools_seen
        ]
        if not missing:
            continue

        snapshot = SNAPSHOTS_DIR / proj_dir.name
        if not snapshot.exists():
            continue

        projects.append({
            "id": proj_dir.name,
            "results_dir": proj_dir,
            "snapshot_path": snapshot,
            "missing_tools": missing,
            "existing_count": len(existing),
            "tools_seen": sorted(tools_seen),
        })

    return projects


# ─── Fase 4: Scan complementar ────────────────────────────────────────────────

async def complement_project(
    proj: dict,
    workers: int = 4,
) -> dict:
    """
    Roda apenas as ferramentas ausentes no projeto e retorna novos findings.
    """
    from a11y_autofix.config import Settings
    from a11y_autofix.scanner.orchestrator import MultiToolScanner
    from a11y_autofix.utils.files import find_react_files
    from dataset.scripts.scan import issue_to_scan_finding

    pid        = proj["id"]
    snap       = proj["snapshot_path"]
    missing    = set(proj["missing_tools"])
    result_dir = proj["results_dir"]

    # Configurar settings com APENAS as ferramentas ausentes
    settings = Settings(
        use_pa11y        ="pa11y"         in missing,
        use_axe          =False,           # axe-core CLI é lento; playwright já cobre
        use_playwright   =False,           # já tem dados
        use_eslint       ="eslint"         in missing,
        use_lighthouse   =False,
        min_tool_consensus=1,
        max_concurrent_scans=workers,
    )

    scanner = MultiToolScanner(settings)
    files   = find_react_files(snap)

    new_by_tool: dict[str, int] = defaultdict(int)
    total_files = len(files)

    with _state["lock"]:
        _state["active"][pid] = {
            "pct": 0, "files_done": 0, "files_total": total_files,
            "new_by_tool": dict(new_by_tool),
        }

    # Commit do snapshot
    pinned = ""
    sm = result_dir / "summary.json"
    if sm.exists():
        try:
            summary_data = json.loads(sm.read_text(encoding="utf-8"))
            pinned = summary_data.get("pinned_commit", "")
        except Exception:
            pass

    all_new_findings: list[Any] = []

    # ── Integração com live_progress.py ──────────────────────────────────────
    _progress_path = result_dir / "scan_progress.json"
    _live_path     = RESULTS_DIR / "live_findings.jsonl"
    _live_lock     = threading.Lock()
    _scan_start    = datetime.now(tz=timezone.utc).isoformat()

    def _write_progress(files_done: int, issues_so_far: int) -> None:
        try:
            _progress_path.write_text(
                json.dumps({
                    "project_id":    pid,
                    "status":        "scanning",
                    "total_files":   total_files,
                    "files_done":    files_done,
                    "issues_so_far": issues_so_far,
                    "started_at":    _scan_start,
                    "last_update":   datetime.now(tz=timezone.utc).isoformat(),
                    "complement":    True,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    _write_progress(0, 0)

    def _on_file_done(scan_result: Any) -> None:
        nonlocal new_by_tool
        for issue in (scan_result.issues or []):
            for tool in issue.found_by:
                tv = tool.value if hasattr(tool, "value") else str(tool)
                new_by_tool[tv] += 1

        # Atualiza estado interno (display do complement_scan)
        with _state["lock"]:
            active = _state["active"].get(pid, {})
            done    = active.get("files_done", 0) + 1
            iss_acc = active.get("issues_so_far", 0) + len(scan_result.issues or [])
            _state["active"][pid] = {
                "pct":           done / total_files * 100 if total_files else 100,
                "files_done":    done,
                "files_total":   total_files,
                "issues_so_far": iss_acc,
                "new_by_tool":   dict(new_by_tool),
            }

        # Escreve scan_progress.json → live_progress.py lê isso
        with _state["lock"]:
            fd  = _state["active"].get(pid, {}).get("files_done", 0)
            iss = _state["active"].get(pid, {}).get("issues_so_far", 0)
        _write_progress(fd, iss)

        # Appenda ao live_findings.jsonl → live_progress.py lê isso
        lines = []
        for issue in (scan_result.issues or []):
            file_obj = getattr(scan_result, "file", None)
            fname = file_obj.name if hasattr(file_obj, "name") else str(file_obj or "?")
            lines.append(json.dumps({
                "project_id":    pid,
                "file":          fname,
                "wcag_criteria": getattr(issue, "wcag_criteria", None),
                "issue_type":    issue.issue_type.value
                                 if hasattr(issue.issue_type, "value")
                                 else str(issue.issue_type),
                "impact":        getattr(issue, "impact", "moderate"),
                "confidence":    issue.confidence.value
                                 if hasattr(issue.confidence, "value")
                                 else str(getattr(issue, "confidence", "low")),
                "found_by":      [t.value if hasattr(t, "value") else str(t)
                                  for t in (issue.found_by or [])],
                "ts":            time.time(),
                "complement":    True,
            }, ensure_ascii=False))
        if lines:
            with _live_lock:
                try:
                    with open(_live_path, "a", encoding="utf-8") as fp:
                        fp.write("\n".join(lines) + "\n")
                except Exception:
                    pass

    scan_results = await scanner.scan_files(files, "WCAG2AA", on_file_done=_on_file_done)

    # Remove scan_progress.json → projeto sai do painel "Em andamento"
    try:
        _progress_path.unlink(missing_ok=True)
    except Exception:
        pass

    for sr in scan_results:
        for issue in (sr.issues or []):
            sf = issue_to_scan_finding(issue, pid, pinned)
            all_new_findings.append(sf)

    return {
        "pid": pid,
        "new_findings": all_new_findings,
        "new_by_tool": dict(new_by_tool),
    }


# ─── Fase 5: Merge ────────────────────────────────────────────────────────────

def merge_into_project(
    proj: dict,
    new_findings: list[Any],
    new_by_tool: dict[str, int],
) -> int:
    """
    Funde novos findings nos arquivos existentes do projeto.
    Usa finding_id como chave de deduplicação.
    Retorna número de findings realmente adicionados.
    """
    from dataset.schema.models import ScanFinding

    result_dir = proj["results_dir"]
    fp = result_dir / "findings.jsonl"

    # Carregar existentes
    existing_raw: list[dict] = []
    existing_ids: set[str] = set()
    for line in fp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            existing_raw.append(d)
            existing_ids.add(d.get("finding_id", ""))
        except Exception:
            pass

    # Filtrar apenas genuinamente novos
    added = []
    for sf in new_findings:
        fid = sf.finding_id if hasattr(sf, "finding_id") else sf.get("finding_id", "")
        if fid and fid not in existing_ids:
            added.append(sf)
            existing_ids.add(fid)

    if not added:
        return 0

    # Reescrever findings.jsonl com os novos appended
    with open(fp, "a", encoding="utf-8") as f:
        for sf in added:
            if hasattr(sf, "model_dump"):
                row = sf.model_dump(mode="json")
            else:
                row = dict(sf)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Recalcular summary.json
    sm_path = result_dir / "summary.json"
    all_findings = existing_raw + [
        sf.model_dump(mode="json") if hasattr(sf, "model_dump") else dict(sf)
        for sf in added
    ]
    _rewrite_summary(sm_path, all_findings, new_by_tool)

    return len(added)


def _rewrite_summary(sm_path: Path, all_findings: list[dict],
                     extra_tools: dict[str, int]) -> None:
    """Recalcula e reescreve summary.json."""
    from collections import defaultdict as dd
    by_criterion: dict = dd(int)
    by_type: dict = dd(int)
    by_impact: dict = dd(int)
    by_principle: dict = dd(int)
    files_set: set = set()
    tools_succeeded: set = set()

    PRIN = {"1": "perceivable", "2": "operable",
            "3": "understandable", "4": "robust"}

    for f in all_findings:
        crit = f.get("wcag_criteria") or ""
        itype = f.get("issue_type", "other")
        impact = f.get("impact", "moderate")
        fpath = f.get("file", "")
        if crit:
            by_criterion[crit] += 1
            p = PRIN.get(crit.split(".")[0], "unknown")
            by_principle[p] += 1
        by_type[itype] += 1
        by_impact[impact] += 1
        if fpath:
            files_set.add(fpath)
        for t in (f.get("found_by") or []):
            tools_succeeded.add(str(t))

    existing = {}
    if sm_path.exists():
        try:
            existing = json.loads(sm_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    existing.update({
        "total_issues":       len(all_findings),
        "files_with_issues":  len(files_set),
        "by_criterion":       dict(by_criterion),
        "by_type":            dict(by_type),
        "by_impact":          dict(by_impact),
        "by_principle":       dict(by_principle),
        "tools_succeeded":    sorted(tools_succeeded),
        # Atualiza scan_date para que live_progress.py ordene corretamente
        # na lista de "Recém concluídos" após o complement scan
        "scan_date":          datetime.now(tz=timezone.utc).isoformat(),
        "complement_updated": True,
    })
    sm_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False),
                       encoding="utf-8")


# ─── Fase 6: Rebuild consolidado ─────────────────────────────────────────────

def rebuild_consolidated() -> int:
    """Regera dataset_findings.jsonl a partir de todos os findings.jsonl."""
    out_path = RESULTS_DIR / "dataset_findings.jsonl"
    total = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for proj_dir in sorted(RESULTS_DIR.iterdir()):
            if not proj_dir.is_dir():
                continue
            fp = proj_dir / "findings.jsonl"
            if fp.exists():
                for line in fp.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        out.write(line + "\n")
                        total += 1
    return total


# ─── Display em tempo real ────────────────────────────────────────────────────

def _render_display() -> list[str]:
    """Gera as linhas do display. Chamado pelo thread de display."""
    W = _w()
    lines: list[str] = []

    def add(s: str = "") -> None:
        lines.append(s + CLR)

    with _state["lock"]:
        phase      = _state["phase"]
        label      = _state["phase_label"]
        active     = dict(_state["active"])
        completed  = list(_state["completed"])[-6:]
        errors     = list(_state["errors"])[-3:]
        total      = _state["total_projects"]
        done       = _state["done_projects"]
        new_total  = _state["new_total"]
        new_by_tool = dict(_state["new_by_tool"])
        t0         = _state["start_time"]
        diag       = dict(_state["diag"])
        fix_log    = list(_state["fix_log"])
        tools_add  = list(_state["tools_to_add"])

    # ── Cabeçalho ────────────────────────────────────────────────────────────
    add(f"{BOLD}{'─' * W}{R}")
    add(f"{BOLD}  ♿ a11y-autofix — Complement Scan{R}")
    ts = time.strftime("%H:%M:%S")
    add(f"{DIM}  {ts}  │  {label or phase}{R}")
    add(f"{BOLD}{'─' * W}{R}")

    # ── Diagnóstico ──────────────────────────────────────────────────────────
    if diag or phase in ("diagnosing", "fixing"):
        add(f"\n  {BOLD}Ferramentas:{R}")
        for tool in ["playwright+axe", "pa11y", "eslint"]:
            d = diag.get(tool, {})
            ok = d.get("ok")
            ver = d.get("version") or ""
            msg = d.get("msg") or ""
            n   = d.get("findings", 0)
            if ok is None:
                ic = f"{CYAN}…{R}"
                color = DIM
            elif ok:
                ic = OK
                color = GREEN
            else:
                ic = FAIL
                color = RED if tool in tools_add else DIM

            ver_str = f"  {DIM}v{ver}{R}" if ver else ""
            n_str   = (f"  {GREEN}+{n} findings no teste{R}" if ok and n > 0 else
                       f"  {DIM}{msg[:50]}{R}" if msg else "")
            add(f"  {ic}  {color}{tool:<22}{R}{ver_str}{n_str}")

    # Logs de fix
    if fix_log and phase == "fixing":
        add()
        for line in fix_log[-4:]:
            icon = OK if "✔" in line else (FAIL if "✘" in line else f"{CYAN}→{R}")
            add(f"  {icon}  {DIM}{line[:W-8]}{R}")

    if phase in ("init", "diagnosing", "fixing", "planning"):
        return lines

    # ── Progresso geral ──────────────────────────────────────────────────────
    add()
    bar = _bar(done, total, w=28)
    pct = done / total * 100 if total else 0
    add(f"  {bar}  {CYAN}{done}/{total}{R}  ({pct:.0f}%)  "
        f"elapsed: {_elapsed(t0)}  ETA: {_eta(t0, done, total)}")

    # Novos findings acumulados
    tool_parts = "  ".join(
        f"{DIM}{t}:{R}{GREEN}+{c}{R}" for t, c in sorted(new_by_tool.items()) if c
    ) or f"{DIM}nenhum ainda{R}"
    add(f"  Novos findings:  {tool_parts}  {BOLD}total: +{new_total}{R}")

    # ── Em andamento ─────────────────────────────────────────────────────────
    if active:
        add()
        add(f"  {CYAN}{'─' * (W - 4)}{R}")
        add(f"  {CYAN}{BOLD}  Em andamento  ({len(active)} projeto(s)){R}")
        add(f"  {CYAN}{'─' * (W - 4)}{R}")
        col_proj = 44
        for pid, info in list(active.items())[:5]:
            pct_p = info.get("pct", 0)
            fd    = info.get("files_done", 0)
            ft    = info.get("files_total", 0)
            by_t  = info.get("new_by_tool", {})

            mini_bar = _bar(fd, ft, w=14, color=YELLOW)
            tool_str = "  ".join(
                f"{DIM}{t.split('+')[0]}:{R}{YELLOW}{c}{R}"
                for t, c in sorted(by_t.items()) if c
            ) or f"{DIM}…{R}"
            short_pid = pid[:col_proj]
            add(f"  {short_pid:<{col_proj}}  {mini_bar}  {pct_p:3.0f}%  "
                f"{DIM}{fd}/{ft}{R}  {tool_str}")

    # ── Recém concluídos ─────────────────────────────────────────────────────
    if completed:
        add()
        add(f"  {GREEN}{'─' * (W - 4)}{R}")
        add(f"  {GREEN}{BOLD}  Recém concluídos{R}")
        add(f"  {GREEN}{'─' * (W - 4)}{R}")
        for item in reversed(completed):
            pid   = item["pid"][:44]
            n     = item["new_count"]
            dur   = item.get("duration_s", 0)
            bt    = item.get("by_tool", {})
            bt_str = "  ".join(
                f"{DIM}{t.split('+')[0]}:+{c}{R}"
                for t, c in sorted(bt.items()) if c
            ) or f"{DIM}nenhum novo{R}"
            dur_str = f"{int(dur // 60)}m{int(dur % 60):02d}s"
            color = GREEN if n > 0 else DIM
            add(f"  {color}{pid:<44}{R}  {BOLD}+{n}{R}  ({bt_str})  {DIM}{dur_str}{R}")

    # Erros
    if errors:
        add()
        for err in errors:
            add(f"  {RED}✘ {err['pid'][:40]}  {err['msg'][:40]}{R}")

    return lines


_display_lock = threading.Lock()


def _display_thread() -> None:
    """Thread dedicado ao display em tempo real (atualiza a cada 0.4s)."""
    global _DISPLAY_LINES

    def _clear_prev(n: int) -> None:
        if n > 0:
            print(f"\033[{n}A", end="")  # subir n linhas

    while not _state["stop_display"]:
        with _display_lock:
            lines = _render_display()
            _clear_prev(_DISPLAY_LINES)
            output = "\n".join(lines)
            print(output, flush=True)
            _DISPLAY_LINES = len(lines)
        time.sleep(0.4)

    # Render final
    with _display_lock:
        lines = _render_display()
        _clear_prev(_DISPLAY_LINES)
        print("\n".join(lines), flush=True)
        _DISPLAY_LINES = len(lines)


# ─── Orquestrador principal ────────────────────────────────────────────────────

async def run(
    tools_filter: list[str] | None,
    workers: int,
    dry_run: bool,
    auto_fix: bool,
) -> None:
    # Iniciar display
    disp = threading.Thread(target=_display_thread, daemon=True)
    disp.start()

    try:
        # ── Fase 1: Diagnóstico ───────────────────────────────────────────
        with _state["lock"]:
            _state["phase"] = "diagnosing"
            _state["phase_label"] = "Testando ferramentas com componente real..."

        diag = await diagnose()
        await asyncio.sleep(0.5)  # deixar display atualizar

        # Determinar ferramentas com problema
        broken = [t for t, d in diag.items()
                  if not d.get("ok") and t != "playwright+axe"]

        if tools_filter:
            # Usuário especificou ferramentas explicitamente
            tools_to_add = tools_filter
        elif broken:
            # Há ferramentas quebradas: tentar corrigir e adicionar
            tools_to_add = broken
        else:
            # Todas OK — complementar com as que funcionam além do playwright
            # (pode haver projetos que foram escaneados antes do pa11y/eslint estarem
            # disponíveis e portanto têm dados só do playwright+axe)
            tools_to_add = [
                t for t, d in diag.items()
                if d.get("ok") and t not in ("playwright+axe",)
            ]

        with _state["lock"]:
            _state["tools_to_add"] = tools_to_add

        if not tools_to_add:
            with _state["lock"]:
                _state["phase"] = "done"
                _state["phase_label"] = "Nenhuma ferramenta de complemento disponível."
            await asyncio.sleep(1)
            return

        # ── Fase 2: Correção ──────────────────────────────────────────────
        if auto_fix and broken:
            with _state["lock"]:
                _state["phase"] = "fixing"
                _state["phase_label"] = f"Instalando: {', '.join(broken)}..."

            fixed = await fix_tools(diag)
            # Re-diagnosticar os que foram corrigidos
            if fixed:
                diag2 = await diagnose()
                diag.update(diag2)
                still_broken = [t for t in tools_to_add if not diag.get(t, {}).get("ok")]
                if still_broken:
                    with _state["lock"]:
                        _state["fix_log"].append(
                            f"⚠ Ainda com problema após fix: {', '.join(still_broken)}"
                        )

        await asyncio.sleep(0.6)

        if dry_run:
            with _state["lock"]:
                _state["phase"] = "done"
                _state["phase_label"] = (
                    f"Dry-run concluído. Ferramentas a adicionar: {', '.join(tools_to_add)}"
                )
            await asyncio.sleep(1)
            return

        # ── Verificar se as ferramentas estão realmente disponíveis ──────
        # Re-diagnosticar para garantir estado atual (pode ter sido corrigido acima)
        diag_final = await diagnose()
        still_unavailable = [
            t for t in tools_to_add
            if not diag_final.get(t, {}).get("ok")
        ]
        if still_unavailable:
            with _state["lock"]:
                _state["phase"] = "done"
                _state["phase_label"] = (
                    f"ABORTADO — ferramentas ainda indisponíveis: "
                    f"{', '.join(still_unavailable)}. "
                    f"Instale manualmente (veja instruções abaixo) e rode com --no-fix."
                )
            await asyncio.sleep(2)
            print(f"\n{RED}{BOLD}Ferramentas não encontradas: {', '.join(still_unavailable)}{R}\n")
            print(f"{YELLOW}Instale manualmente (terminal como Administrador no Windows):{R}\n")
            if "eslint" in still_unavailable:
                print(f"  npm install -g eslint eslint-plugin-jsx-a11y @typescript-eslint/parser")
            if "pa11y" in still_unavailable:
                print(f"  npm install -g pa11y")
            print(f"\nDepois rode:")
            print(f"  python dataset/scripts/complement_scan.py --no-fix --workers 3\n")
            return

        # ── Fase 3: Planejamento ──────────────────────────────────────────
        with _state["lock"]:
            _state["phase"] = "planning"
            _state["phase_label"] = "Identificando projetos a complementar..."

        projects = find_projects_to_complement(tools_to_add)

        with _state["lock"]:
            _state["total_projects"] = len(projects)
            _state["phase"] = "scanning"
            _state["phase_label"] = (
                f"Complementando {len(projects)} projetos com: "
                f"{', '.join(tools_to_add)}"
            )

        if not projects:
            with _state["lock"]:
                _state["phase"] = "done"
                _state["phase_label"] = "Nenhum projeto precisa de complemento."
            await asyncio.sleep(1)
            return

        # ── Fase 4+5: Scan + Merge ────────────────────────────────────────
        sem = asyncio.Semaphore(max(1, workers // 2))

        async def _process(proj: dict) -> None:
            t0_proj = time.time()
            pid = proj["id"]
            try:
                async with sem:
                    result = await complement_project(proj, workers=workers)

                new_count = merge_into_project(
                    proj,
                    result["new_findings"],
                    result["new_by_tool"],
                )
                dur = time.time() - t0_proj

                with _state["lock"]:
                    _state["active"].pop(pid, None)
                    _state["completed"].append({
                        "pid": pid, "new_count": new_count,
                        "by_tool": result["new_by_tool"],
                        "duration_s": dur,
                    })
                    _state["done_projects"] += 1
                    _state["new_total"] += new_count
                    for t, c in result["new_by_tool"].items():
                        _state["new_by_tool"][t] += c

            except Exception as e:
                dur = time.time() - t0_proj
                with _state["lock"]:
                    _state["active"].pop(pid, None)
                    _state["errors"].append({"pid": pid, "msg": str(e)[:60]})
                    _state["done_projects"] += 1

        await asyncio.gather(*[_process(p) for p in projects])

        # ── Fase 6: Rebuild consolidado ───────────────────────────────────
        with _state["lock"]:
            _state["phase_label"] = "Reconstruindo dataset_findings.jsonl..."

        total_consolidated = await asyncio.get_event_loop().run_in_executor(
            None, rebuild_consolidated
        )

        with _state["lock"]:
            added = _state["new_total"]
            _state["phase"] = "done"
            _state["phase_label"] = (
                f"Concluído. +{added} findings adicionados  │  "
                f"{total_consolidated} total no dataset."
            )

        await asyncio.sleep(1.5)

    finally:
        _state["stop_display"] = True
        disp.join(timeout=2)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Complementa dados faltantes de pa11y/ESLint nos projetos já escaneados.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Só diagnosticar e planejar; não executar scan nem modificar arquivos.",
    )
    parser.add_argument(
        "--no-fix", action="store_true",
        help="Não tentar instalar ferramentas automaticamente.",
    )
    parser.add_argument(
        "--tools", nargs="+",
        choices=["eslint", "pa11y", "playwright+axe", "axe-core"],
        default=None,
        help="Forçar scan com ferramentas específicas (padrão: as que falharam no diagnóstico).",
    )
    parser.add_argument(
        "--workers", type=int, default=3, metavar="N",
        help="Número de projetos em paralelo (default: 3).",
    )
    args = parser.parse_args()

    print(f"\n{BOLD}{'═' * _w()}{R}")
    print(f"{BOLD}  ♿ a11y-autofix — Complement Scan{R}")
    print(f"{BOLD}{'═' * _w()}{R}\n")

    try:
        asyncio.run(run(
            tools_filter=args.tools,
            workers=args.workers,
            dry_run=args.dry_run,
            auto_fix=not args.no_fix,
        ))
    except KeyboardInterrupt:
        _state["stop_display"] = True
        print(f"\n\n{YELLOW}  Interrompido pelo usuário.{R}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
