#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostico completo dos scanners de acessibilidade.

Testa cada ferramenta individualmente em um componente React sintetico
com problemas conhecidos, mostra o resultado bruto e identifica exatamente
o que esta falhando e como corrigir.

Uso:
    python dataset/scripts/diagnose_scanners.py
    python dataset/scripts/diagnose_scanners.py --tool eslint
    python dataset/scripts/diagnose_scanners.py --tool pa11y
    python dataset/scripts/diagnose_scanners.py --tool playwright
    python dataset/scripts/diagnose_scanners.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ── Paleta ANSI ───────────────────────────────────────────────────────────────
R      = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"

OK   = f"{GREEN}✔ OK{R}"
FAIL = f"{RED}✘ FAIL{R}"
WARN = f"{YELLOW}⚠ WARN{R}"

# ── Componente React com problemas a11y conhecidos ────────────────────────────
# Tem intencionalmente:
#   - <img> sem alt         → image-alt (1.1.1)
#   - <button> vazio        → button-name (4.1.2)
#   - onClick sem onKeyDown → click-events-have-key-events (2.1.1)
#   - <a href="#">          → anchor-is-valid (4.1.2)
#   - contraste #aaa/#fff   → color-contrast (1.4.3) — cinza claro em branco
#   - <input> sem label     → label (1.3.1)
_TEST_COMPONENT_TSX = """\
// Componente de teste a11y — contém violacoes intencionais
export default function TestComponent() {
  return (
    <div>
      <img src="photo.jpg" />
      <button></button>
      <div onClick={() => console.log("click")}>Clique aqui</div>
      <a href="#">Link</a>
      <span style={{ color: "#aaa", backgroundColor: "#fff" }}>Texto fraco</span>
      <input type="text" placeholder="Nome" />
    </div>
  );
}
"""

# Versao simplificada para o harness HTML (sem TypeScript)
_TEST_COMPONENT_CLEAN = """\
function TestComponent() {
  return React.createElement('div', null,
    React.createElement('img', { src: 'photo.jpg' }),
    React.createElement('button', null),
    React.createElement('div', { onClick: function() {} }, 'Clique aqui'),
    React.createElement('a', { href: '#' }, 'Link'),
    React.createElement('span', {
      style: { color: '#aaa', backgroundColor: '#fff' }
    }, 'Texto fraco'),
    React.createElement('input', { type: 'text', placeholder: 'Nome' })
  );
}
var __Component = TestComponent;
"""


# ─── Utilitários ─────────────────────────────────────────────────────────────

def _sep(title: str = "") -> None:
    w = 72
    if title:
        inner = f"  {title}  "
        pad = (w - len(inner)) // 2
        print(f"\n{BOLD}{'─' * pad}{inner}{'─' * (w - pad - len(inner))}{R}")
    else:
        print(f"{DIM}{'─' * w}{R}")


def _run_sync(cmd: list[str], env: dict | None = None,
              timeout: int = 20) -> tuple[int, str, str]:
    """Executa comando síncrono e retorna (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", f"Comando nao encontrado: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", f"Timeout ({timeout}s)"
    except Exception as e:
        return -3, "", str(e)


# ─── Diagnóstico de ambiente ──────────────────────────────────────────────────

def diag_environment(verbose: bool) -> dict:
    _sep("AMBIENTE")
    results = {}

    IS_WIN = platform.system() == "Windows"
    print(f"  Sistema:     {platform.system()} {platform.release()}")
    print(f"  Python:      {sys.version.split()[0]}")
    print(f"  Cwd:         {os.getcwd()}")

    # Node
    rc, out, err = _run_sync(["node", "--version"])
    node_ver = out.strip() if rc == 0 else "nao encontrado"
    status = OK if rc == 0 else FAIL
    print(f"  Node.js:     {status}  {node_ver}")
    results["node"] = rc == 0

    # npm
    rc, out, err = _run_sync(["npm", "--version"])
    npm_ver = out.strip() if rc == 0 else "nao encontrado"
    status = OK if rc == 0 else FAIL
    print(f"  npm:         {status}  {npm_ver}")
    results["npm"] = rc == 0

    # npm root -g
    rc, out, err = _run_sync(["npm", "root", "-g"])
    npm_root = out.strip() if rc == 0 else ""
    status = OK if rc == 0 and npm_root else FAIL
    print(f"  npm root -g: {status}  {npm_root or 'falhou'}")
    results["npm_root"] = npm_root

    if npm_root and verbose:
        # Listar o que está instalado globalmente
        rc2, out2, _ = _run_sync(["npm", "list", "-g", "--depth=0"])
        if rc2 == 0:
            for line in out2.splitlines()[1:6]:
                print(f"             {DIM}{line}{R}")

    return results


def diag_eslint(verbose: bool, env_results: dict) -> dict:
    _sep("ESLINT + JSX-A11Y")
    results: dict = {}
    npm_root = env_results.get("npm_root", "")

    # 1. ESLint disponível?
    rc, out, err = _run_sync(["npx", "--no-install", "eslint", "--version"])
    if rc != 0:
        rc, out, err = _run_sync(["eslint", "--version"])
    eslint_ver = out.strip() if rc == 0 else None
    status = OK if eslint_ver else FAIL
    print(f"  ESLint:              {status}  {eslint_ver or 'nao encontrado'}")
    results["eslint_available"] = bool(eslint_ver)
    results["eslint_version"]   = eslint_ver

    if not eslint_ver:
        print(f"  {RED}  ACAO: npm install -g eslint{R}")
        return results

    # Versao major
    try:
        major = int(eslint_ver.lstrip("v").split(".")[0])
    except ValueError:
        major = 8
    results["eslint_major"] = major
    print(f"  ESLint major:        {CYAN}{major}{R}  (>=9 → flat config)")

    # 2. jsx-a11y plugin
    plugin_path = None
    if npm_root:
        candidates = [
            Path(npm_root) / "eslint-plugin-jsx-a11y",
            Path(npm_root) / "eslint-plugin-jsx-a11y" / "index.js",
        ]
        for c in candidates:
            if c.exists():
                plugin_path = str(c.parent if c.is_file() else c)
                break

    status = OK if plugin_path else FAIL
    print(f"  jsx-a11y plugin:     {status}  {plugin_path or 'nao encontrado em npm root -g'}")
    results["jsx_a11y_available"] = bool(plugin_path)
    if not plugin_path:
        print(f"  {RED}  ACAO: npm install -g eslint-plugin-jsx-a11y{R}")

    # 3. @typescript-eslint/parser
    ts_parser_path = None
    if npm_root:
        tp = Path(npm_root) / "@typescript-eslint" / "parser"
        if tp.exists():
            ts_parser_path = str(tp)

    status = OK if ts_parser_path else WARN
    print(f"  @typescript-eslint:  {status}  {ts_parser_path or 'nao encontrado (opcional)'}")
    results["ts_parser_available"] = bool(ts_parser_path)
    if not ts_parser_path:
        print(f"  {YELLOW}  ACAO (opcional): npm install -g @typescript-eslint/parser{R}")

    if not results.get("jsx_a11y_available"):
        print(f"\n  {YELLOW}Pulando teste de lint (plugin ausente){R}")
        return results

    # 4. Teste real: lint no componente de teste
    print(f"\n  Testando lint no componente sintetico...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        tsx_file = tmp / "Test.tsx"
        tsx_file.write_text(_TEST_COMPONENT_TSX, encoding="utf-8")

        env = {**os.environ, "FORCE_COLOR": "0", "NODE_PATH": npm_root}

        if major >= 9:
            # Flat config
            from a11y_autofix.scanner.eslint import _build_flat_config_cjs, _RULE_META
            rules = {rule: "error" for rule in _RULE_META}
            cfg_content = _build_flat_config_cjs(rules)
            cfg_path = tmp / "eslint.config.cjs"
            cfg_path.write_text(cfg_content, encoding="utf-8")

            cmd = [
                "npx", "--yes", "eslint",
                "--format", "json",
                "--config", str(cfg_path),
                str(tsx_file),
            ]
        else:
            from a11y_autofix.scanner.eslint import _LEGACY_ESLINT_CONFIG
            cfg_path = tmp / ".eslintrc.json"
            cfg_path.write_text(json.dumps(_LEGACY_ESLINT_CONFIG), encoding="utf-8")
            cmd = [
                "npx", "--yes", "eslint",
                "--format", "json",
                "--no-eslintrc",
                "--config", str(cfg_path),
                "--ext", ".tsx,.jsx",
                str(tsx_file),
            ]

        if verbose:
            print(f"  {DIM}  cmd: {' '.join(cmd[:5])} ...{R}")
            print(f"  {DIM}  NODE_PATH: {npm_root[:60]}...{R}")

        rc, out, err = _run_sync(cmd, env=env, timeout=30)

        if verbose and err.strip():
            for line in err.strip().splitlines()[:5]:
                print(f"  {DIM}  stderr: {line}{R}")

        if not out.strip():
            print(f"  Resultado:           {FAIL}  saida vazia (rc={rc})")
            if err.strip():
                print(f"  {RED}  Erro: {err.strip()[:200]}{R}")
            results["test_ok"] = False
            results["test_findings"] = 0
        else:
            try:
                data = json.loads(out)
                msgs = sum(len(r.get("messages", [])) for r in data if isinstance(r, dict))
                status = OK if msgs > 0 else WARN
                print(f"  Resultado:           {status}  {msgs} finding(s) no componente de teste")
                if msgs == 0:
                    print(f"  {YELLOW}  AVISO: esperado >=3 findings no componente de teste.{R}")
                    print(f"  {YELLOW}  Plugin pode estar instalado mas sem regras ativas.{R}")
                elif verbose:
                    for r in data:
                        for m in r.get("messages", [])[:3]:
                            print(f"  {DIM}  {m.get('ruleId','?')}: {m.get('message','')[:60]}{R}")
                results["test_ok"] = msgs > 0
                results["test_findings"] = msgs
            except json.JSONDecodeError:
                print(f"  Resultado:           {FAIL}  JSON invalido")
                if verbose:
                    print(f"  {DIM}  output: {out[:200]}{R}")
                results["test_ok"] = False
                results["test_findings"] = 0

    return results


def diag_pa11y(verbose: bool) -> dict:
    _sep("PA11Y")
    results: dict = {}

    # 1. pa11y disponível?
    pa11y_cmd = None
    for cmd in [["pa11y", "--version"], ["npx", "pa11y", "--version"]]:
        rc, out, _ = _run_sync(cmd, timeout=20)
        if rc == 0:
            pa11y_cmd = cmd[0]
            pa11y_ver = out.strip()
            break
    else:
        pa11y_ver = None

    status = OK if pa11y_ver else FAIL
    print(f"  pa11y:               {status}  {pa11y_ver or 'nao encontrado'}")
    results["pa11y_available"] = bool(pa11y_ver)
    results["pa11y_version"] = pa11y_ver

    if not pa11y_ver:
        print(f"  {RED}  ACAO: npm install -g pa11y{R}")
        return results

    # Versao major
    try:
        major = int((pa11y_ver or "").split(".")[0])
    except ValueError:
        major = 0
    results["pa11y_major"] = major

    # 2. Chromium disponível para pa11y?
    # pa11y usa puppeteer/playwright internamente
    rc, out, err = _run_sync(["node", "-e",
        "try { require('puppeteer'); console.log('puppeteer OK'); }"
        " catch(e) { try { require('playwright'); console.log('playwright OK'); }"
        " catch(e2) { console.log('nenhum'); } }"],
        timeout=10,
    )
    browser_engine = out.strip() if rc == 0 else "verificacao falhou"
    status = OK if "OK" in browser_engine else WARN
    print(f"  Browser engine:      {status}  {browser_engine}")

    # 3. Teste real: scan no componente de teste
    print(f"\n  Testando pa11y no componente sintetico...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Criar harness HTML minimal (sem CDN — HTML estatico simples)
        html_content = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Test</title></head>
<body>
  <img src="photo.jpg">
  <button></button>
  <a href="#">Link</a>
  <input type="text" placeholder="Nome">
  <span style="color:#aaa;background:#fff">Texto</span>
</body></html>"""
        html_file = tmp / "test.html"
        html_file.write_text(html_content, encoding="utf-8")
        url = f"file://{html_file.resolve()}"

        if major > 0 and major <= 6:
            cmd = [pa11y_cmd, "--reporter", "json", "--standard", "WCAG2AA",
                   "--timeout", "30000", url]
        else:
            cmd = [pa11y_cmd, "--reporter", "json", "--standard", "WCAG2AA",
                   "--timeout", "30000", url]

        if verbose:
            print(f"  {DIM}  cmd: {' '.join(cmd[:4])} ...{R}")

        rc, out, err = _run_sync(cmd, timeout=40)

        if verbose and err.strip():
            for line in err.strip().splitlines()[:3]:
                print(f"  {DIM}  stderr: {line}{R}")

        if rc not in (0, 2) or not out.strip():
            print(f"  Resultado:           {FAIL}  rc={rc}, saida vazia")
            if err.strip():
                print(f"  {RED}  Erro: {err.strip()[:200]}{R}")
            results["test_ok"] = False
            results["test_findings"] = 0
        else:
            try:
                data = json.loads(out)
                msgs = len(data) if isinstance(data, list) else 0
                status = OK if msgs > 0 else WARN
                print(f"  Resultado:           {status}  {msgs} finding(s) no HTML de teste")
                if msgs == 0:
                    print(f"  {YELLOW}  AVISO: esperado >=2 findings. pa11y pode estar bloqueado por CSP/CDN.{R}")
                elif verbose:
                    for m in data[:3]:
                        print(f"  {DIM}  {m.get('code','?')}: {m.get('message','')[:60]}{R}")
                results["test_ok"] = msgs > 0
                results["test_findings"] = msgs
            except json.JSONDecodeError:
                print(f"  Resultado:           {FAIL}  JSON invalido")
                results["test_ok"] = False
                results["test_findings"] = 0

    return results


def diag_playwright(verbose: bool) -> dict:
    _sep("PLAYWRIGHT + AXE-CORE")
    results: dict = {}

    # 1. playwright instalado?
    rc, out, _ = _run_sync([sys.executable, "-c",
        "import playwright; print(playwright.__version__)"], timeout=10)
    pw_ver = out.strip() if rc == 0 else None
    status = OK if pw_ver else FAIL
    print(f"  playwright (Python): {status}  {pw_ver or 'nao encontrado'}")
    results["playwright_available"] = bool(pw_ver)

    if not pw_ver:
        print(f"  {RED}  ACAO: pip install playwright && playwright install chromium{R}")
        return results

    # 2. Chromium browsers instalados?
    rc, out, err = _run_sync([sys.executable, "-m", "playwright", "install", "--dry-run"],
                             timeout=15)
    chromium_ok = "chromium" in (out + err).lower()
    # Tentar verificar diretamente
    rc2, out2, _ = _run_sync([sys.executable, "-c", """
import asyncio
from playwright.async_api import async_playwright
async def check():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=['--no-sandbox','--disable-gpu'])
        v = b.version
        await b.close()
        print('OK', v)
asyncio.run(check())
"""], timeout=20)
    chromium_ver = out2.strip() if rc2 == 0 else None
    status = OK if chromium_ver else FAIL
    print(f"  Chromium (launch):   {status}  {chromium_ver or 'falhou ao iniciar'}")
    results["chromium_ok"] = bool(chromium_ver)

    if not chromium_ver:
        print(f"  {RED}  ACAO: playwright install chromium{R}")
        if err.strip() and verbose:
            print(f"  {DIM}  {err.strip()[:200]}{R}")

    # 3. axe-core npm
    npm_root = ""
    rc, out, _ = _run_sync(["npm", "root", "-g"], timeout=10)
    if rc == 0:
        npm_root = out.strip()
    axe_path = None
    if npm_root:
        for sub in ["axe-core/axe.min.js", "axe-core/axe.js",
                    "@axe-core/cli/node_modules/axe-core/axe.min.js"]:
            p = Path(npm_root) / sub
            if p.exists():
                axe_path = str(p)
                break

    status = OK if axe_path else WARN
    print(f"  axe-core (npm):      {status}  {axe_path or 'nao encontrado (usara CDN fallback)'}")
    results["axe_local"] = bool(axe_path)

    if not results.get("chromium_ok"):
        print(f"\n  {YELLOW}Pulando teste Playwright (Chromium nao disponivel){R}")
        return results

    # 4. Teste real com Playwright + axe
    print(f"\n  Testando Playwright+axe no componente sintetico...")
    from a11y_autofix.utils.files import build_html_harness
    from a11y_autofix.utils.http_server import HarnessServer

    async def _run_pw_test() -> int:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            harness_html = build_html_harness(_TEST_COMPONENT_TSX, "Test.tsx")
            harness_path = tmp / "harness.html"
            harness_path.write_text(harness_html, encoding="utf-8")

            from a11y_autofix.scanner.playwright_axe import PlaywrightAxeRunner
            runner = PlaywrightAxeRunner()
            with HarnessServer(tmp) as server:
                url = server.url_for("harness.html")
                if verbose:
                    print(f"  {DIM}  harness URL: {url}{R}")
                findings = await runner.run(harness_path, "WCAG2AA", harness_url=url)
            return len(findings)

    try:
        count = asyncio.run(_run_pw_test())
        status = OK if count > 0 else WARN
        print(f"  Resultado:           {status}  {count} finding(s) no componente de teste")
        if count == 0:
            print(f"  {YELLOW}  AVISO: esperado >=2 findings. Checar se CDN esta acessivel.{R}")
        results["test_ok"] = count > 0
        results["test_findings"] = count
    except Exception as e:
        print(f"  Resultado:           {FAIL}  excecao: {str(e)[:120]}")
        results["test_ok"] = False
        results["test_findings"] = 0

    return results


# ─── Sumário e recomendações ──────────────────────────────────────────────────

def print_summary(all_results: dict) -> None:
    _sep("SUMARIO E ACOES RECOMENDADAS")

    env  = all_results.get("env", {})
    esl  = all_results.get("eslint", {})
    pa   = all_results.get("pa11y", {})
    pw   = all_results.get("playwright", {})

    tools_ok   = []
    tools_fail = []

    if pw.get("test_ok"):
        tools_ok.append("playwright+axe")
    else:
        tools_fail.append("playwright+axe")

    if pa.get("test_ok"):
        tools_ok.append("pa11y")
    else:
        tools_fail.append("pa11y")

    if esl.get("test_ok"):
        tools_ok.append("eslint")
    else:
        tools_fail.append("eslint")

    print(f"\n  Ferramentas funcionando:  {GREEN}{', '.join(tools_ok) or 'nenhuma'}{R}")
    print(f"  Ferramentas com problema: {RED}{', '.join(tools_fail) or 'nenhuma'}{R}")

    if not tools_fail:
        print(f"\n  {GREEN}{BOLD}✔  Todas as ferramentas estao operacionais.{R}")
        print(f"  O problema de 0% multi-tool pode ser simplesmente que os componentes")
        print(f"  escaneados ate agora sao simples e cada um so dispara em 1 ferramenta.")
        return

    print(f"\n  {BOLD}Acoes necessarias:{R}\n")

    # ESLint
    if "eslint" in tools_fail:
        if not esl.get("eslint_available"):
            print(f"  {RED}[ESLint]{R} Nao instalado:")
            print(f"    npm install -g eslint eslint-plugin-jsx-a11y @typescript-eslint/parser")
        elif not esl.get("jsx_a11y_available"):
            print(f"  {RED}[ESLint]{R} Plugin jsx-a11y ausente:")
            print(f"    npm install -g eslint-plugin-jsx-a11y @typescript-eslint/parser")
        elif esl.get("test_findings", 0) == 0:
            major = esl.get("eslint_major", 8)
            print(f"  {YELLOW}[ESLint]{R} Instalado (v{esl.get('eslint_version','?')}) mas sem findings.")
            if major >= 9:
                print(f"    Verificar se NODE_PATH esta sendo passado corretamente.")
                print(f"    Teste manual: NODE_PATH=$(npm root -g) npx eslint --config <cfg> <file>")
            print(f"    npm_root -g: {env.get('npm_root', 'desconhecido')}")
        print()

    # Pa11y
    if "pa11y" in tools_fail:
        if not pa.get("pa11y_available"):
            print(f"  {RED}[Pa11y]{R} Nao instalado:")
            print(f"    npm install -g pa11y")
        elif not pa.get("test_ok"):
            print(f"  {YELLOW}[Pa11y]{R} Instalado (v{pa.get('pa11y_version','?')}) mas sem findings.")
            print(f"    Pa11y usa Chromium internamente. Verificar:")
            print(f"    1. pa11y http://example.com  (teste manual)")
            print(f"    2. CHROMIUM_FLAGS=--no-sandbox pa11y <url>  (Linux)")
            IS_WIN = platform.system() == "Windows"
            if IS_WIN:
                print(f"    3. No Windows, tente: pa11y --reporter json file:///C:/caminho/test.html")
        print()

    # Playwright
    if "playwright+axe" in tools_fail:
        if not pw.get("playwright_available"):
            print(f"  {RED}[Playwright]{R} Nao instalado:")
            print(f"    pip install playwright && playwright install chromium")
        elif not pw.get("chromium_ok"):
            print(f"  {RED}[Playwright]{R} Chromium nao disponivel:")
            print(f"    playwright install chromium")
            print(f"    # ou: python -m playwright install chromium")
        elif not pw.get("test_ok"):
            print(f"  {YELLOW}[Playwright]{R} Chromium ok mas sem findings no teste.")
            print(f"    Checar se CDN (unpkg.com) esta acessivel para React/Babel.")
            print(f"    Verificar logs com --verbose para detalhes.")
        print()

    # Resumo de instalacao em 1 linha (Windows)
    IS_WIN = platform.system() == "Windows"
    missing_npm = []
    if "pa11y" in tools_fail and not pa.get("pa11y_available"):
        missing_npm.append("pa11y")
    if "eslint" in tools_fail and not esl.get("eslint_available"):
        missing_npm.extend(["eslint", "eslint-plugin-jsx-a11y", "@typescript-eslint/parser"])
    elif "eslint" in tools_fail and not esl.get("jsx_a11y_available"):
        missing_npm.extend(["eslint-plugin-jsx-a11y", "@typescript-eslint/parser"])

    if missing_npm:
        print(f"  {BOLD}Comando de instalacao unico:{R}")
        print(f"    npm install -g {' '.join(dict.fromkeys(missing_npm))}")
        print()


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnostico dos scanners de acessibilidade.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tool", choices=["all", "eslint", "pa11y", "playwright"],
        default="all", help="Ferramenta a diagnosticar (default: all)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Exibir detalhes de comandos e saidas brutas",
    )
    args = parser.parse_args()

    print(f"\n{BOLD}{'═' * 72}{R}")
    print(f"{BOLD}  ♿ a11y-autofix — Diagnostico de Scanners{R}")
    print(f"{BOLD}{'═' * 72}{R}")

    all_results: dict = {}

    all_results["env"] = diag_environment(args.verbose)

    if args.tool in ("all", "playwright"):
        all_results["playwright"] = diag_playwright(args.verbose)
    if args.tool in ("all", "pa11y"):
        all_results["pa11y"] = diag_pa11y(args.verbose)
    if args.tool in ("all", "eslint"):
        all_results["eslint"] = diag_eslint(args.verbose, all_results["env"])

    if args.tool == "all":
        print_summary(all_results)

    print(f"\n{BOLD}{'═' * 72}{R}\n")


if __name__ == "__main__":
    main()
