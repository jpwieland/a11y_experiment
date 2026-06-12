"""Runner para Playwright com injeção de axe-core.

Estratégia de robustez:
1. Serve o harness via HTTP local (sem file://) → CDN carrega sem restrições
2. Usa wait_until='domcontentloaded' → retorna rápido, sem aguardar CDN
3. Depois, espera window.React estar disponível (com timeout gracioso)
4. Injeta axe-core a partir do npm local — sem dependência de rede para axe
5. Fallback para CDN se axe-core não estiver instalado localmente
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding
from a11y_autofix.scanner.base import BaseRunner

log = structlog.get_logger(__name__)

# URL CDN de fallback para axe-core (usado só se npm local não encontrar)
_AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"

# Timeouts
_GOTO_TIMEOUT_MS = 60_000        # 60s para navegação inicial
_REACT_WAIT_TIMEOUT_MS = 25_000  # 25s esperando React carregar via CDN
_AXE_RUN_TIMEOUT_MS = 30_000     # 30s para execução do axe-core
_RENDER_WAIT_MS = 600            # 600ms após React carregar (renderização)

# Script para executar axe-core e retornar resultados como JSON
#
# Tags incluídas: wcag2a/aa, wcag21a/aa, wcag22aa + best-practice
# (best-practice inclui regras úteis: heading-order, label-content-name-mismatch, etc.)
#
# Regras DESABILITADAS — page-level false positives em harness de componente isolado:
#   - page-has-heading-one : componentes não são páginas; nunca têm <h1> de página
#   - landmark-one-main    : o harness já provê role="main"; componentes não precisam
#   - skip-link / bypass   : mecanismo de navegação de página, irrelevante em componente
#   - region               : conteúdo já está dentro do role="main" do harness
#   - document-title       : o harness já define <title>; regra não se aplica ao componente
#
# Essas regras geravam ~99% dos findings (1 por arquivo) ao mapear para WCAG 2.4.6
# via fallback em detection.py, contaminando todo o dataset com falsos positivos.
_AXE_RUN_SCRIPT = """
async () => {
  const results = await window.axe.run(document, {
    runOnly: {
      type: 'tag',
      values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice']
    },
    rules: {
      'page-has-heading-one': { enabled: false },
      'landmark-one-main':    { enabled: false },
      'skip-link':            { enabled: false },
      'bypass':               { enabled: false },
      'region':               { enabled: false },
      'document-title':       { enabled: false },
    },
    reporter: 'v2',
    resultTypes: ['violations', 'incomplete']
  });
  return JSON.stringify({
    violations: results.violations || [],
    incomplete: results.incomplete || []
  });
}
"""

# Candidatos para encontrar axe-core instalado via npm
_AXE_NPM_SUBPATHS = [
    "axe-core/axe.min.js",
    "axe-core/axe.js",
    "@axe-core/cli/node_modules/axe-core/axe.min.js",
]

# Cores por nível de impacto
_IMPACT_COLORS: dict[str, str] = {
    "critical": "#b71c1c",
    "serious":  "#c62828",
    "moderate": "#e65100",
    "minor":    "#f57f17",
}


# Padding ao redor do elemento no crop (estilo Lighthouse element thumbnail)
_ELEMENT_CROP_PADDING_PX = 16
# Dimensões mínimas/máximas do crop para evitar imagens inúteis
_ELEMENT_CROP_MIN_SIZE_PX = 24
_ELEMENT_CROP_MAX_W_PX = 1280
_ELEMENT_CROP_MAX_H_PX = 800
# Máximo de crops por arquivo (1 por node, limitado para conter custo)
_MAX_ELEMENT_CROPS = 20

# Script que coleta bounding rects de uma lista de seletores.
# Retorna [{selector, x, y, width, height}] apenas para elementos visíveis.
#
# IMPORTANTE: a busca é restrita ao subtree de #root (container do
# componente). Seletores genéricos como 'span' ou 'button' vindos do axe
# casariam com o chrome do harness (título, badges) e os crops mostrariam
# elementos errados.
_COLLECT_RECTS_SCRIPT = """
(selectors) => {
  const scope = document.getElementById('root') || document;
  const out = [];
  for (const sel of selectors) {
    let els;
    try { els = scope.querySelectorAll(sel); } catch (e) { continue; }
    if (!els || els.length === 0) {
      // Fallback: seletores prefixados com #root não casam via subtree
      try { els = document.querySelectorAll(sel); } catch (e) { continue; }
      // Aceitar apenas matches dentro de #root
      els = Array.from(els).filter(el => scope === document || scope.contains(el));
      if (els.length === 0) continue;
    }
    const el = els[0];
    const r = el.getBoundingClientRect();
    if (r.width <= 0 && r.height <= 0) continue;
    out.push({
      selector: sel,
      x: r.x + window.scrollX,
      y: r.y + window.scrollY,
      width: r.width,
      height: r.height,
    });
  }
  return out;
}
"""


async def _capture_element_crops(
    page: object,
    violations: list[dict],
    elements_dir: "Path",
    file_stem: str,
    log: object,
) -> dict[str, dict]:
    """
    Captura um crop PNG por elemento violador (estilo Lighthouse).

    Para cada seletor único nas violações:
    1. Coleta o bounding rect via JS (coordenadas absolutas da página)
    2. Captura page.screenshot(clip=rect + padding) → PNG individual

    Deve ser chamado ANTES da injeção de highlights, para que o crop
    mostre o elemento como o usuário o vê (o destaque fica no full-page).

    Returns:
        Dict selector → {"crop_path": str, "rect": {x, y, width, height}}.
    """
    captures: dict[str, dict] = {}

    # Seletores únicos, preservando ordem de severidade
    seen: set[str] = set()
    selectors: list[str] = []
    for v in violations:
        sel = v.get("selector", "")
        if sel and sel not in seen:
            seen.add(sel)
            selectors.append(sel)
    selectors = selectors[:_MAX_ELEMENT_CROPS]
    if not selectors:
        return captures

    try:
        rects: list[dict] = await page.evaluate(_COLLECT_RECTS_SCRIPT, selectors)  # type: ignore[attr-defined]
    except Exception as e:
        log.debug("element_rects_failed", error=str(e)[:200])  # type: ignore[attr-defined]
        return captures

    if not rects:
        return captures

    elements_dir.mkdir(parents=True, exist_ok=True)

    # Dimensões da página para clamp do clip
    try:
        page_size: dict = await page.evaluate(  # type: ignore[attr-defined]
            "() => ({ w: document.documentElement.scrollWidth,"
            "         h: document.documentElement.scrollHeight })"
        )
    except Exception:
        page_size = {"w": 1280, "h": 800}

    for idx, rect in enumerate(rects, start=1):
        sel = rect["selector"]
        pad = _ELEMENT_CROP_PADDING_PX
        x = max(0.0, rect["x"] - pad)
        y = max(0.0, rect["y"] - pad)
        w = min(rect["width"] + 2 * pad, _ELEMENT_CROP_MAX_W_PX)
        h = min(rect["height"] + 2 * pad, _ELEMENT_CROP_MAX_H_PX)
        # Clamp aos limites da página (clip fora da página gera erro)
        w = min(w, max(1.0, page_size.get("w", 1280) - x))
        h = min(h, max(1.0, page_size.get("h", 800) - y))
        if w < _ELEMENT_CROP_MIN_SIZE_PX or h < _ELEMENT_CROP_MIN_SIZE_PX:
            # Elemento minúsculo: expandir o crop para dar contexto visual
            grow_w = max(0.0, _ELEMENT_CROP_MIN_SIZE_PX * 3 - w)
            grow_h = max(0.0, _ELEMENT_CROP_MIN_SIZE_PX * 3 - h)
            x = max(0.0, x - grow_w / 2)
            y = max(0.0, y - grow_h / 2)
            w = min(w + grow_w, max(1.0, page_size.get("w", 1280) - x))
            h = min(h + grow_h, max(1.0, page_size.get("h", 800) - y))

        crop_path = elements_dir / f"{file_stem}_el{idx}.png"
        try:
            await page.screenshot(  # type: ignore[attr-defined]
                path=str(crop_path),
                clip={"x": x, "y": y, "width": w, "height": h},
                type="png",
            )
            captures[sel] = {
                "crop_path": str(crop_path),
                "rect": {
                    "x": round(rect["x"], 1),
                    "y": round(rect["y"], 1),
                    "width": round(rect["width"], 1),
                    "height": round(rect["height"], 1),
                },
            }
        except Exception as e:
            log.debug("element_crop_failed", selector=sel[:60], error=str(e)[:150])  # type: ignore[attr-defined]

    log.debug("element_crops_captured", count=len(captures))  # type: ignore[attr-defined]
    return captures


async def _capture_highlighted_screenshot(
    page: object,
    screenshot_path: "Path",
    screenshot_harness_url: "str | None",
    axe_data: dict,
    log: object,
) -> dict[str, dict]:
    """
    Navega para o harness de display, aguarda carregamento completo,
    captura crops por elemento (estilo Lighthouse), injeta highlighting
    visual nas violações e captura screenshot full-page.

    Estratégia de espera:
    1. goto com wait_until='domcontentloaded' → HTML parseado
    2. Aguarda dois frames de animação → garante pintura (paint) concluída
    3. wait_for_timeout(500ms) → buffer extra para CSS/fonts/layout
    4. Verifica que o body tem altura > 0 antes de capturar

    Returns:
        Dict selector → {"crop_path", "rect"} dos crops por elemento
        (vazio em caso de falha — screenshots nunca quebram o scan).
    """
    element_captures: dict[str, dict] = {}
    try:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]

        # ── 1. Navegar para o harness de display ────────────────────────
        if screenshot_harness_url:
            # Harness Angular é auto-contido (sem CDN) → domcontentloaded é suficiente.
            # Não usar networkidle: causa timeout de 45s quando o harness antigo tinha
            # links de CDN. O harness atual embute todo o CSS inline.
            wait_strategy = "domcontentloaded"
            nav_timeout = 20_000

            try:
                await page.goto(  # type: ignore[attr-defined]
                    screenshot_harness_url,
                    wait_until=wait_strategy,
                    timeout=nav_timeout,
                )
                log.debug("screenshot_harness_loaded", strategy=wait_strategy)  # type: ignore[attr-defined]
            except Exception as nav_err:
                log.warning("screenshot_nav_failed", error=str(nav_err)[:200])  # type: ignore[attr-defined]
        # else: mantém a página do axe harness (React já está renderizado)

        # ── 2. Aguardar carregamento completo + pintura ──────────────────
        # Dois requestAnimationFrame garantem que o browser concluiu a pintura.
        # O CSS inline (incluindo variáveis de projeto) pode precisar de um
        # ou dois frames para ser aplicado após domcontentloaded.
        await page.evaluate(  # type: ignore[attr-defined]
            "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        )
        # Buffer: scripts inline (badge injection, onerror) precisam de tempo
        # para executar após o DOM estar pronto.
        await page.wait_for_timeout(1200)  # type: ignore[attr-defined]

        # Verificar que o body tem altura real (evitar screenshot vazio)
        try:
            await page.wait_for_function(  # type: ignore[attr-defined]
                "() => document.body && document.body.scrollHeight > 20",
                timeout=5_000,
            )
        except Exception:
            pass  # prossegue mesmo se timeout

        # ── 3. Coletar violações para destacar ──────────────────────────
        def _extract_nodes(items: list) -> list[dict]:
            out: list[dict] = []
            for v in items:
                if not isinstance(v, dict):
                    continue
                impact = str(v.get("impact") or "moderate")
                rule_id = str(v.get("id") or "")
                desc = str(v.get("description") or "")[:100]
                help_url = str(v.get("helpUrl") or "")
                for node in v.get("nodes", [])[:5]:
                    target = node.get("target", [])
                    if not target:
                        continue
                    last = target[-1]
                    sel = last if isinstance(last, str) else str(last)
                    failures = str(node.get("failureSummary") or desc)[:120]
                    out.append({
                        "selector": sel,
                        "rule": rule_id,
                        "impact": impact,
                        "description": desc,
                        "failures": failures,
                        "helpUrl": help_url,
                    })
            return out

        # Highlights: apenas violações CONFIRMADAS (o full-page destacado
        # deve refletir o que é violação de fato, como no Lighthouse)
        violations = _extract_nodes(axe_data.get("violations", []))

        # Crops: violações + incomplete (potenciais). Incomplete também
        # viram findings ([Potential]) e issues de outras ferramentas
        # podem referenciar o mesmo elemento — ex.: imagem com asset
        # quebrado cai em incomplete no axe.
        crop_targets = violations + _extract_nodes(
            [i for i in axe_data.get("incomplete", [])
             if isinstance(i, dict) and i.get("nodes")]
        )

        # ── 4. Crops por elemento (ANTES do highlight, estilo Lighthouse) ──
        # O crop mostra o elemento limpo; o destaque visual fica apenas no
        # screenshot full-page. Lighthouse usa o mesmo modelo: boundingRect
        # por node + thumbnail recortado do screenshot da página.
        if crop_targets:
            element_captures = await _capture_element_crops(
                page=page,
                violations=crop_targets,
                elements_dir=screenshot_path.parent / "elements",  # type: ignore[union-attr]
                file_stem=screenshot_path.stem,  # type: ignore[union-attr]
                log=log,
            )

        # ── 5. Injetar highlighting ──────────────────────────────────────
        if violations:
            highlight_js = _build_highlight_script(violations)
            try:
                await page.evaluate(highlight_js)  # type: ignore[attr-defined]
                await page.evaluate(  # type: ignore[attr-defined]
                    "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                )
                await page.wait_for_timeout(300)  # type: ignore[attr-defined]
            except Exception as hi_err:
                log.debug("screenshot_highlight_failed", error=str(hi_err)[:200])  # type: ignore[attr-defined]

        # ── 6. Screenshot full-page ──────────────────────────────────────
        await page.screenshot(  # type: ignore[attr-defined]
            path=str(screenshot_path),
            full_page=True,
            type="png",
        )
        log.debug("screenshot_saved", path=str(screenshot_path), violations=len(violations))  # type: ignore[attr-defined]

    except Exception as e:
        log.warning("screenshot_capture_failed", error=str(e)[:300])  # type: ignore[attr-defined]

    return element_captures


def _build_highlight_script(violations: list[dict]) -> str:
    """
    Gera JavaScript que destaca visualmente os elementos com violações
    e adiciona um painel de legenda ao final da página.
    """
    import json as _json

    v_json = _json.dumps(violations, ensure_ascii=False)
    colors_json = _json.dumps(_IMPACT_COLORS)

    # NOTA: {{ / }} são escapes do f-string Python para { / } literais no JS
    return f"""
(function() {{
  var violations = {v_json};
  var COLORS = {colors_json};
  var matched = [];
  var counter = 0;

  // Escopo: restringir aos elementos do componente (#root), evitando
  // destacar o chrome do harness (título, badges de preview)
  var scope = document.getElementById('root') || document;

  // ── Destacar cada elemento violado ──────────────────────────────
  violations.forEach(function(v) {{
    var color = COLORS[v.impact] || COLORS['moderate'];
    var els;
    try {{ els = scope.querySelectorAll(v.selector); }} catch(e) {{ return; }}
    if (!els || els.length === 0) {{
      try {{ els = document.querySelectorAll(v.selector); }} catch(e) {{ return; }}
      els = Array.prototype.filter.call(els, function(el) {{
        return scope === document || scope.contains(el);
      }});
    }}
    if (!els || els.length === 0) return;

    els.forEach(function(el) {{
      counter++;
      var num = counter;

      // Contorno vermelho intenso + fundo tingido
      el.style.outline = '3px solid ' + color;
      el.style.outlineOffset = '4px';
      el.style.boxShadow = '0 0 0 6px ' + color + '33';
      el.style.position = 'relative';
      el.style.zIndex = '1';

      // Badge numerado no canto superior-direito
      var badge = document.createElement('span');
      badge.textContent = num;
      badge.title = '[' + v.impact.toUpperCase() + '] ' + v.rule + ': ' + v.description;
      badge.style.cssText = [
        'position:absolute',
        'top:-10px',
        'right:-10px',
        'background:' + color,
        'color:#fff',
        'border-radius:50%',
        'width:22px',
        'height:22px',
        'font-size:12px',
        'font-weight:bold',
        'font-family:sans-serif',
        'display:inline-flex',
        'align-items:center',
        'justify-content:center',
        'z-index:9999',
        'box-shadow:0 1px 4px rgba(0,0,0,0.4)',
        'pointer-events:none',
        'line-height:1',
      ].join(';');
      el.appendChild(badge);
    }});

    if (els.length > 0) {{
      matched.push({{ num: counter - els.length + 1, last: counter, v: v, count: els.length }});
    }}
  }});

  if (matched.length === 0) return;

  // ── Painel de legenda ────────────────────────────────────────────
  var panel = document.createElement('div');
  panel.style.cssText = [
    'margin:16px',
    'padding:0',
    'background:#fff',
    'border:2px solid #b71c1c',
    'border-radius:8px',
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif',
    'font-size:12px',
    'overflow:hidden',
    'box-shadow:0 2px 12px rgba(0,0,0,0.15)',
  ].join(';');

  var header = document.createElement('div');
  header.style.cssText = 'background:#b71c1c;color:#fff;padding:10px 14px;font-weight:bold;font-size:13px';
  header.textContent = '⚠  ' + counter + ' violação(ões) de acessibilidade encontrada(s)';
  panel.appendChild(header);

  var list = document.createElement('ol');
  list.style.cssText = 'margin:0;padding:12px 12px 12px 28px;';

  matched.forEach(function(m) {{
    var color = COLORS[m.v.impact] || COLORS['moderate'];
    var li = document.createElement('li');
    li.style.cssText = 'margin-bottom:8px;line-height:1.5;';

    var badge = document.createElement('span');
    badge.style.cssText = 'display:inline-block;background:' + color + ';color:#fff;border-radius:3px;padding:1px 6px;font-size:11px;font-weight:bold;margin-right:6px;vertical-align:middle';
    badge.textContent = m.v.impact.toUpperCase();

    var code = document.createElement('code');
    code.style.cssText = 'background:#f5f5f5;padding:1px 5px;border-radius:3px;font-size:11px;margin-right:4px';
    code.textContent = m.v.rule;

    var txt = document.createElement('span');
    txt.textContent = m.v.description;

    var fail = document.createElement('div');
    fail.style.cssText = 'color:#555;font-size:11px;margin-top:2px;padding-left:4px;border-left:2px solid ' + color;
    fail.textContent = m.v.failures;

    li.appendChild(badge);
    li.appendChild(code);
    li.appendChild(txt);
    li.appendChild(fail);
    list.appendChild(li);
  }});

  panel.appendChild(list);

  var root = document.getElementById('root') || document.body;
  root.appendChild(panel);
}})();
"""


class PlaywrightAxeRunner(BaseRunner):
    """
    Runner que usa Playwright + axe-core para análise profunda de acessibilidade.

    Carrega o componente React no Chromium headless via HTTP local,
    injeta axe-core (preferencialmente do npm local) e executa análise completa
    incluindo violações WCAG 2.1/2.2 e melhores práticas.
    """

    tool = ScanTool.PLAYWRIGHT

    def __init__(self) -> None:
        # Cache para localização do axe-core
        self._local_axe_path: Path | None = None
        self._axe_resolved = False
        # Cache para versão
        self._playwright_version: str | None = None

    async def _find_local_axe(self) -> Path | None:
        """
        Busca axe-core nos módulos npm globais.

        Evita dependência de CDN durante a análise, tornando o sistema
        robusto para ambientes sem internet ou com CDN lento.
        """
        if self._axe_resolved:
            return self._local_axe_path
        self._axe_resolved = True

        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "root", "-g",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return None

            npm_root = Path(stdout.decode().strip())
            for subpath in _AXE_NPM_SUBPATHS:
                candidate = npm_root / subpath
                if candidate.exists():
                    log.debug("playwright_axe_local_found", path=str(candidate))
                    self._local_axe_path = candidate
                    return candidate

        except (FileNotFoundError, asyncio.TimeoutError, OSError) as e:
            log.debug("playwright_axe_local_search_failed", error=str(e))

        log.debug("playwright_axe_using_cdn_fallback")
        return None

    async def available(self) -> bool:
        """Verifica se Playwright está instalado com Chromium disponível."""
        try:
            from playwright.async_api import async_playwright  # noqa: F401
            return True
        except ImportError:
            return False

    async def version(self) -> str:
        """Retorna versão do Playwright."""
        if self._playwright_version is not None:
            return self._playwright_version
        try:
            import playwright
            ver = getattr(playwright, "__version__", "unknown")
            self._playwright_version = ver
            return ver
        except ImportError:
            return "unknown"

    async def safe_run(
        self,
        harness_path: Path,
        wcag: str,
        harness_url: str | None = None,
        framework: str = "react",
        viewport: "dict[str, int] | None" = None,
        forced_colors: bool = False,
        screenshot_path: "Path | None" = None,
        screenshot_harness_url: "str | None" = None,
        out_meta: "dict | None" = None,
    ) -> list[ToolFinding]:
        """Wraps run() com tratamento de erros, aceitando parâmetro framework."""
        try:
            return await self.run(
                harness_path, wcag, harness_url,
                framework=framework,
                viewport=viewport,
                forced_colors=forced_colors,
                screenshot_path=screenshot_path,
                screenshot_harness_url=screenshot_harness_url,
                out_meta=out_meta,
            )
        except Exception as e:
            log.warning("runner_safe_run_failed", tool=self.tool.value, error=str(e)[:300])
            return []

    async def run(
        self,
        harness_path: Path,
        wcag: str,
        harness_url: str | None = None,
        framework: str = "react",
        viewport: "dict[str, int] | None" = None,
        forced_colors: bool = False,
        screenshot_path: "Path | None" = None,
        screenshot_harness_url: "str | None" = None,
        out_meta: "dict | None" = None,
    ) -> list[ToolFinding]:
        """
        Executa axe-core via Playwright no harness HTML.

        Args:
            harness_path: Caminho do arquivo HTML harness.
            wcag: Nível WCAG (ex: 'WCAG2AA').
            harness_url: URL HTTP para acessar o harness (preferido sobre file://).
                         Fornecida pelo orquestrador via HarnessServer.
            viewport: Dimensões de viewport, ex: {"width": 375, "height": 667}.
                      Se None, usa desktop padrão 1280×800.
            forced_colors: Se True, emula modo de alto contraste (forced-colors: active).

        Returns:
            Lista de ToolFinding com violações encontradas.
        """
        from playwright.async_api import async_playwright
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        version = await self.version()

        # Preferir URL HTTP local; fallback para file://
        url = harness_url or f"file://{harness_path.resolve()}"
        log.debug("playwright_navigating", url=url[:80])

        # Buscar axe-core local antes de abrir o browser (async, com cache)
        local_axe = await self._find_local_axe()

        findings: list[ToolFinding] = []

        effective_viewport = viewport or {"width": 1280, "height": 800}
        log.debug(
            "playwright_scan_mode",
            viewport=effective_viewport,
            forced_colors=forced_colors,
        )

        # PLAYWRIGHT_HEADLESS=false → abre browser visível (útil para debug de rendering)
        headless = os.environ.get("PLAYWRIGHT_HEADLESS", "true").strip().lower() != "false"
        if not headless:
            log.info("playwright_headed_mode", hint="Browser visível ativo — PLAYWRIGHT_HEADLESS=false")

        async with async_playwright() as p:
            # Chrome flags:
            # --no-sandbox: necessário em ambientes Linux/Docker
            # --disable-dev-shm-usage: evita crashes em /dev/shm pequeno
            # --disable-gpu: headless sem GPU (ignorado em headed mode)
            # --disable-web-security: permite scripts CDN de file:// se necessário
            # --allow-file-access-from-files: permite leitura de file:// locais
            browser = await p.chromium.launch(
                headless=headless,
                slow_mo=200 if not headless else 0,  # anima ações no modo headed
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-web-security",
                    "--allow-file-access-from-files",
                    "--disable-extensions",
                    "--disable-background-timer-throttling",
                ],
            )

            try:
                context = await browser.new_context(
                    viewport=effective_viewport,
                )
                page = await context.new_page()

                # Silenciar erros de console esperados (React, Babel, etc.)
                page.on("console", lambda _: None)
                page.on("pageerror", lambda _: None)

                # ── Passo 1: Navegar para o harness ─────────────────────────
                # domcontentloaded: retorna assim que o HTML é parseado,
                # sem esperar scripts CDN externos (que podem demorar)
                try:
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=_GOTO_TIMEOUT_MS,
                    )
                except PlaywrightTimeout:
                    log.warning("playwright_goto_timeout", url=url[:80])
                    await browser.close()
                    return []
                except Exception as e:
                    log.warning("playwright_goto_error", error=str(e)[:200])
                    await browser.close()
                    return []

                # ── Passo 1b: Emular modo de alto contraste ──────────────────
                # forced-colors: active simula Windows High Contrast Mode.
                # Deve ser aplicado ANTES de aguardar o framework para que
                # o CSS condicional do componente seja avaliado corretamente.
                if forced_colors:
                    try:
                        await page.emulate_media(forced_colors="active")
                        log.debug("playwright_high_contrast_emulated")
                    except Exception as e:
                        log.warning("playwright_high_contrast_failed", error=str(e)[:200])

                # ── Passo 2: Aguardar framework carregar ──────────────────────
                react_loaded = False
                if framework == "angular":
                    # Harness Angular é HTML estático puro — sem scripts CDN.
                    # Pequena pausa para garantir que o DOM está completamente
                    # parseado antes de injetar axe-core.
                    await page.wait_for_timeout(200)
                    log.debug("playwright_angular_static_harness")
                else:
                    # React: aguarda window.React/ReactDOM carregarem via CDN.
                    try:
                        await page.wait_for_function(
                            "(typeof window.React !== 'undefined') && "
                            "(typeof window.ReactDOM !== 'undefined')",
                            timeout=_REACT_WAIT_TIMEOUT_MS,
                        )
                        react_loaded = True
                        log.debug("playwright_react_loaded")
                        # Aguardar renderização do componente (Babel precisa processar JSX)
                        await page.wait_for_timeout(_RENDER_WAIT_MS)
                    except PlaywrightTimeout:
                        log.debug(
                            "playwright_react_not_loaded",
                            hint="Continuando com DOM estático — "
                                 "axe ainda detecta issues estruturais",
                        )

                log.debug("playwright_react_status", loaded=react_loaded, framework=framework)

                # ── Passo 2b: Registrar status de renderização ───────────────
                # Covariável de validade: issues detectados em página que não
                # renderizou refletem o harness, não o componente real.
                # Angular: harness estático sempre "renderiza".
                # React: __a11yHarnessReady é setado pelo harness após o mount
                # bem-sucedido; fallback: #root tem filhos não-placeholder.
                if out_meta is not None:
                    try:
                        if framework == "angular":
                            out_meta["render_success"] = True
                        else:
                            out_meta["render_success"] = bool(await page.evaluate(
                                "() => window.__a11yHarnessReady === true"
                            ))
                    except Exception:
                        out_meta["render_success"] = None

                # ── Passo 3: Injetar axe-core ────────────────────────────────
                try:
                    if local_axe and local_axe.exists():
                        # Injeção local: sem dependência de rede, mais rápida
                        await page.add_script_tag(path=str(local_axe))
                        log.debug("playwright_axe_injected", source="local_npm")
                    else:
                        # Fallback CDN (requer internet)
                        await page.add_script_tag(url=_AXE_CDN)
                        log.debug("playwright_axe_injected", source="cdn")
                except Exception as e:
                    log.warning("playwright_axe_inject_failed", error=str(e)[:200])
                    await browser.close()
                    return []

                # ── Passo 4: Executar análise axe-core ──────────────────────
                # page.evaluate() não aceita timeout= como kwarg no Playwright Python.
                # Usamos set_default_timeout() para configurar o timeout da página
                # antes da chamada; PlaywrightTimeout será levantado se excedido.
                try:
                    page.set_default_timeout(_AXE_RUN_TIMEOUT_MS)
                    result_json: str = await page.evaluate(_AXE_RUN_SCRIPT)
                    data: dict = json.loads(result_json)
                except PlaywrightTimeout:
                    log.warning("playwright_axe_run_timeout")
                    await browser.close()
                    return []
                except Exception as e:
                    log.warning("playwright_axe_run_error", error=str(e)[:200])
                    await browser.close()
                    return []

                # ── Passo 4.5: Screenshots (full-page destacado + crops) ──────
                element_captures: dict[str, dict] = {}
                if screenshot_path is not None:
                    element_captures = await _capture_highlighted_screenshot(
                        page=page,
                        screenshot_path=screenshot_path,
                        screenshot_harness_url=screenshot_harness_url,
                        axe_data=data,
                        log=log,
                    )

            except Exception as e:
                log.warning("playwright_run_error", error=str(e)[:200])
                await browser.close()
                return []

            await browser.close()

        # ── Passo 5: Converter resultados para ToolFinding ───────────────────
        all_violations = list(data.get("violations", []))
        # Incluir 'incomplete' como potential findings (moderado)
        for item in data.get("incomplete", []):
            if isinstance(item, dict):
                # Marcar como potencial (não confirmado)
                item = {**item, "_incomplete": True}
                # Só inclui incomplete se tem nodes específicos
                if item.get("nodes"):
                    all_violations.append(item)

        for violation in all_violations:
            if not isinstance(violation, dict):
                continue

            rule_id = violation.get("id", "unknown")
            is_incomplete = violation.get("_incomplete", False)
            # incomplete → impacto menor (moderate)
            impact = "moderate" if is_incomplete else (violation.get("impact") or "moderate")
            description = violation.get("description", "")
            help_url = violation.get("helpUrl", "")
            wcag_criteria = self._extract_wcag_from_tags(
                violation.get("tags", [])
            )

            for node in violation.get("nodes", []):
                if not isinstance(node, dict):
                    continue

                target = node.get("target", [])
                selector = ""
                if target and isinstance(target, list):
                    last = target[-1]
                    selector = last if isinstance(last, str) else str(last)

                failure = node.get("failureSummary", description)
                if is_incomplete:
                    failure = f"[Potential] {failure}"

                # Anexar captura visual do elemento (estilo Lighthouse)
                capture = element_captures.get(selector)

                finding = ToolFinding(
                    tool=self.tool,
                    tool_version=version,
                    rule_id=str(rule_id),
                    wcag_criteria=wcag_criteria,
                    message=str(failure),
                    selector=selector,
                    context=str(node.get("html", ""))[:500],
                    impact=str(impact),
                    help_url=str(help_url),
                    screenshot_path=capture["crop_path"] if capture else None,
                    bounding_rect=capture["rect"] if capture else None,
                )
                findings.append(finding)

        log.debug(
            "playwright_axe_findings",
            count=len(findings),
            react_loaded=react_loaded,
        )
        return findings

    def _extract_wcag_from_tags(self, tags: list[object]) -> str | None:
        """Extrai critério WCAG das tags axe-core. Ex: 'wcag143' → '1.4.3'."""
        for tag in tags:
            if not isinstance(tag, str):
                continue
            # Padrão de 3 dígitos: wcag143 → 1.4.3
            match = re.match(r"wcag(\d)(\d)(\d)$", tag)
            if match:
                return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
            # Padrão de 2 dígitos: wcag21 → 2.1
            match2 = re.match(r"wcag(\d)(\d{1,2})$", tag)
            if match2:
                return f"{match2.group(1)}.{match2.group(2)}"
        return None
