#!/usr/bin/env bash
# =============================================================================
# fix_scanners.sh — Instala, valida e repara todas as ferramentas de scan
#
# Ferramentas gerenciadas:
#   - pa11y          (accessibility via puppeteer)
#   - axe-core CLI   (@axe-core/cli)
#   - ESLint + jsx-a11y + @typescript-eslint (análise estática JSX)
#   - Playwright     (chromium headless via Python)
#   - axe-core npm   (para injeção local pelo Playwright runner)
#
# Uso:
#   bash fix_scanners.sh             # instala e valida tudo
#   bash fix_scanners.sh --check-only # só diagnóstico, sem instalar
#
# =============================================================================
set -euo pipefail

# ─── Cores ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m';  CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✅ $*${NC}"; }
fail() { echo -e "${RED}  ❌ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️  $*${NC}"; }
info() { echo -e "${BLUE}  ℹ️  $*${NC}"; }
hdr()  { echo -e "\n${CYAN}══════════════════════════════════════════════${NC}"; \
         echo -e "${CYAN}  $*${NC}"; \
         echo -e "${CYAN}══════════════════════════════════════════════${NC}"; }

CHECK_ONLY=false
[[ "${1:-}" == "--check-only" ]] && CHECK_ONLY=true

# ─── npm bin helper ───────────────────────────────────────────────────────────
get_npm_bin() {
    # CORRETO: npm config get prefix retorna /home/user/.local/npm
    # e o bin dir é ${prefix}/bin — NÃO usar `npm root -g` que dá lib/node_modules
    local npm_prefix
    npm_prefix=$(npm config get prefix 2>/dev/null) || { fail "npm não encontrado"; exit 1; }
    echo "${npm_prefix}/bin"
}

ensure_npm_in_path() {
    local npm_bin
    npm_bin=$(get_npm_bin)
    if echo "$PATH" | grep -q "$npm_bin"; then
        ok "npm bin no PATH: $npm_bin"
        return 0
    fi
    warn "npm bin NÃO no PATH: $npm_bin"
    if [[ "$CHECK_ONLY" == "true" ]]; then
        fail "Adicione: export PATH=\"$npm_bin:\$PATH\""
        return 1
    fi
    export PATH="$npm_bin:$PATH"
    local marker="# a11y-autofix npm PATH"
    local line="export PATH=\"$npm_bin:\$PATH\"  $marker"
    for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.zshrc"; do
        [[ -f "$rc" ]] || continue
        if ! grep -q "$marker" "$rc" 2>/dev/null; then
            { echo ""; echo "$line"; } >> "$rc"
            ok "PATH adicionado a $rc"
        fi
    done
    info "PATH atualizado (sessão atual). Para persistir: source ~/.bashrc"
}

npm_install_global() {
    # npm install -g com --prefix explícito quando prefix não está no PATH padrão
    local pkg="$1"
    npm install -g "$pkg" 2>&1 | tail -3
}

# ─── PASSO 1: Node.js e npm ───────────────────────────────────────────────────
hdr "PASSO 1: Node.js e npm"

if command -v node &>/dev/null; then
    NODE_VER=$(node --version)
    ok "Node.js: $NODE_VER"
else
    fail "Node.js NÃO instalado!"
    echo "  Instale via: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs"
    exit 1
fi

if command -v npm &>/dev/null; then
    ok "npm: $(npm --version)"
else
    fail "npm NÃO encontrado!"
    exit 1
fi

NPM_BIN=$(get_npm_bin)
NPM_ROOT=$(npm root -g 2>/dev/null || echo "")
info "npm prefix: $(npm config get prefix 2>/dev/null)"
info "npm bin:    $NPM_BIN"
info "npm root:   $NPM_ROOT"

# ─── PASSO 2: PATH ───────────────────────────────────────────────────────────
hdr "PASSO 2: PATH do npm"
ensure_npm_in_path

# ─── PASSO 3: pa11y ──────────────────────────────────────────────────────────
hdr "PASSO 3: pa11y"

PA11Y_CMD=""
if command -v pa11y &>/dev/null; then
    PA11Y_CMD="pa11y"
elif [[ -f "$NPM_BIN/pa11y" ]]; then
    PA11Y_CMD="$NPM_BIN/pa11y"
elif npx pa11y --version &>/dev/null 2>&1; then
    PA11Y_CMD="npx pa11y"
fi

if [[ -n "$PA11Y_CMD" ]]; then
    PA11Y_VER=$($PA11Y_CMD --version 2>&1 | head -1)
    ok "pa11y: $PA11Y_VER (cmd: $PA11Y_CMD)"
else
    warn "pa11y não encontrado"
    if [[ "$CHECK_ONLY" == "false" ]]; then
        info "Instalando pa11y globalmente..."
        npm_install_global pa11y
        # Recarregar PATH
        export PATH="$NPM_BIN:$PATH"
        if command -v pa11y &>/dev/null; then
            ok "pa11y instalado: $(pa11y --version 2>&1)"
        elif [[ -f "$NPM_BIN/pa11y" ]]; then
            ok "pa11y instalado em $NPM_BIN (adicione ao PATH)"
        else
            fail "Falha ao instalar pa11y"
        fi
    else
        fail "Instale: npm install -g pa11y"
    fi
fi

# ─── PASSO 4: axe-core (npm local para Playwright runner) ────────────────────
hdr "PASSO 4: axe-core (npm)"

AXE_PATH=""
if [[ -n "$NPM_ROOT" ]]; then
    for candidate in "$NPM_ROOT/axe-core/axe.min.js" "$NPM_ROOT/@axe-core/cli/node_modules/axe-core/axe.min.js"; do
        if [[ -f "$candidate" ]]; then
            AXE_PATH="$candidate"
            break
        fi
    done
fi

if [[ -n "$AXE_PATH" ]]; then
    AXE_SIZE=$(du -h "$AXE_PATH" | cut -f1)
    ok "axe-core (local): $AXE_PATH ($AXE_SIZE)"
else
    warn "axe-core não encontrado no npm global"
    if [[ "$CHECK_ONLY" == "false" ]]; then
        info "Instalando axe-core + @axe-core/cli globalmente..."
        npm_install_global "axe-core @axe-core/cli"
        # Re-verificar
        if [[ -n "$NPM_ROOT" && -f "$NPM_ROOT/axe-core/axe.min.js" ]]; then
            ok "axe-core instalado: $NPM_ROOT/axe-core/axe.min.js"
        elif npx --yes @axe-core/cli --version &>/dev/null 2>&1; then
            ok "axe-core CLI disponível via npx"
        else
            warn "axe-core pode não estar disponível localmente (CDN será usado como fallback)"
        fi
    else
        warn "Instale: npm install -g axe-core @axe-core/cli"
        info "  Sem axe local → Playwright usará CDN fallback (mais lento)"
    fi
fi

# ─── PASSO 5: ESLint + plugins ───────────────────────────────────────────────
hdr "PASSO 5: ESLint + jsx-a11y"

ESLINT_OK=false
JSXA11Y_OK=false
TS_PARSER_OK=false

if npx eslint --version &>/dev/null 2>&1; then
    ESLINT_VER=$(npx eslint --version 2>&1 | head -1)
    ESLINT_MAJOR=$(echo "$ESLINT_VER" | grep -oP '\d+' | head -1)
    ok "ESLint: $ESLINT_VER (major: $ESLINT_MAJOR)"
    ESLINT_OK=true
else
    warn "ESLint não encontrado"
    ESLINT_MAJOR=0
fi

if npm list -g eslint-plugin-jsx-a11y 2>/dev/null | grep -q "eslint-plugin-jsx-a11y"; then
    JSXA11Y_VER=$(npm list -g eslint-plugin-jsx-a11y 2>/dev/null | grep "eslint-plugin-jsx-a11y" | grep -oP '[\d.]+' | head -1)
    ok "eslint-plugin-jsx-a11y: $JSXA11Y_VER"
    JSXA11Y_OK=true
else
    warn "eslint-plugin-jsx-a11y NÃO instalado globalmente"
fi

if npm list -g @typescript-eslint/parser 2>/dev/null | grep -q "typescript-eslint"; then
    ok "@typescript-eslint/parser: instalado"
    TS_PARSER_OK=true
else
    warn "@typescript-eslint/parser NÃO instalado globalmente"
fi

if [[ "$ESLINT_OK" == "false" ]] || [[ "$JSXA11Y_OK" == "false" ]] || [[ "$TS_PARSER_OK" == "false" ]]; then
    if [[ "$CHECK_ONLY" == "false" ]]; then
        info "Instalando ESLint + plugins jsx-a11y + TypeScript parser..."
        npm install -g \
            eslint \
            eslint-plugin-jsx-a11y \
            @typescript-eslint/parser \
            @typescript-eslint/eslint-plugin 2>&1 | tail -5

        if npx eslint --version &>/dev/null 2>&1; then
            ESLINT_VER=$(npx eslint --version 2>&1 | head -1)
            ESLINT_MAJOR=$(echo "$ESLINT_VER" | grep -oP '\d+' | head -1)
            ok "ESLint instalado: $ESLINT_VER"
        else
            fail "Falha ao instalar ESLint"
        fi
    else
        fail "Instale: npm install -g eslint eslint-plugin-jsx-a11y @typescript-eslint/parser @typescript-eslint/eslint-plugin"
    fi
fi

# ─── PASSO 6: Chrome / Chromium ───────────────────────────────────────────────
hdr "PASSO 6: Chrome/Chromium"

CHROME_CMD=""
CHROME_VER=""
for cmd in google-chrome google-chrome-stable chromium-browser chromium; do
    if command -v "$cmd" &>/dev/null; then
        CHROME_CMD="$cmd"
        CHROME_VER=$($cmd --version 2>&1 | grep -oP '\d+\.\d+\.\d+\.\d+' | head -1 || echo "?")
        ok "Chrome/Chromium: $CHROME_VER (cmd: $cmd)"
        break
    fi
done
[[ -z "$CHROME_CMD" ]] && warn "Chrome/Chromium não encontrado no PATH"

# ChromeDriver
if command -v chromedriver &>/dev/null; then
    DRIVER_VER=$(chromedriver --version 2>&1 | grep -oP '\d+\.\d+\.\d+\.\d+' | head -1 || echo "?")
    CHROME_MAJOR=$(echo "$CHROME_VER" | cut -d. -f1)
    DRIVER_MAJOR=$(echo "$DRIVER_VER" | cut -d. -f1)
    info "ChromeDriver: $DRIVER_VER"
    if [[ -n "$CHROME_MAJOR" && -n "$DRIVER_MAJOR" && "$CHROME_MAJOR" != "$DRIVER_MAJOR" ]]; then
        warn "MISMATCH: Chrome $CHROME_MAJOR vs ChromeDriver $DRIVER_MAJOR"
        if [[ "$CHECK_ONLY" == "false" ]]; then
            info "Atualizando ChromeDriver..."
            npx browser-driver-manager install chrome 2>&1 | tail -3
            ok "ChromeDriver atualizado: $(chromedriver --version 2>&1 | head -1)"
        else
            fail "Execute: npx browser-driver-manager install chrome"
        fi
    else
        ok "Chrome e ChromeDriver compatíveis (major: $CHROME_MAJOR)"
    fi
else
    warn "ChromeDriver não encontrado"
    if [[ "$CHECK_ONLY" == "false" ]]; then
        info "Instalando ChromeDriver..."
        npx browser-driver-manager install chrome 2>&1 | tail -3
        ok "ChromeDriver instalado"
    else
        warn "Execute: npx browser-driver-manager install chrome"
    fi
fi

# ─── PASSO 7: Playwright (Python) ────────────────────────────────────────────
hdr "PASSO 7: Playwright (Python)"

if python3 -c "import playwright" &>/dev/null 2>&1; then
    PW_VER=$(python3 -c "import playwright; print(getattr(playwright,'__version__','?'))" 2>/dev/null || echo "?")
    ok "playwright Python: $PW_VER"

    # Verificar se chromium está instalado
    if python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(args=['--no-sandbox'])
    b.close()
print('ok')
" &>/dev/null 2>&1; then
        ok "playwright chromium: disponível"
    else
        warn "playwright chromium NÃO disponível"
        if [[ "$CHECK_ONLY" == "false" ]]; then
            info "Instalando chromium para playwright..."
            python3 -m playwright install chromium 2>&1 | tail -3
            ok "playwright chromium instalado"
        else
            fail "Execute: python3 -m playwright install chromium"
        fi
    fi
else
    warn "playwright Python NÃO instalado"
    if [[ "$CHECK_ONLY" == "false" ]]; then
        info "Instalando playwright..."
        pip install playwright 2>&1 | tail -3
        python3 -m playwright install chromium 2>&1 | tail -3
        ok "playwright instalado"
    else
        fail "Execute: pip install playwright && python3 -m playwright install chromium"
    fi
fi

# ─── PASSO 8: Testes funcionais ───────────────────────────────────────────────
hdr "PASSO 8: Testes funcionais"

# ── 8a: pa11y via HTTP simples ────────────────────────────────────────────────
if [[ -n "$PA11Y_CMD" ]] || command -v pa11y &>/dev/null || npx pa11y --version &>/dev/null 2>&1; then
    info "Testando pa11y com arquivo HTML simples..."
    TMP_DIR=$(mktemp -d)
    TMP_HTML="$TMP_DIR/test.html"
    cat > "$TMP_HTML" << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head><title>Test</title></head>
<body>
  <img src="test.jpg">
  <button>Click</button>
  <a href="">Empty link</a>
</body>
</html>
EOF

    # Determinar comando pa11y resolvido (usa $PA11Y_CMD se disponível)
    _PA11Y_TEST_CMD="${PA11Y_CMD:-pa11y}"

    # Tentar pa11y com --timeout aumentado (CDN pode ser lento)
    PA11Y_OUT=$($_PA11Y_TEST_CMD --reporter json \
                      --timeout 60000 \
                      --wait 500 \
                      --chromium-flags "--no-sandbox --disable-dev-shm-usage --disable-gpu" \
                      "file://$TMP_HTML" 2>/dev/null || \
               npx pa11y --reporter json \
                         --timeout 60000 \
                         --wait 500 \
                         --chromium-flags "--no-sandbox --disable-dev-shm-usage --disable-gpu" \
                         "file://$TMP_HTML" 2>/dev/null || echo "[]")
    rm -rf "$TMP_DIR"

    if echo "$PA11Y_OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))" &>/dev/null 2>&1; then
        COUNT=$(echo "$PA11Y_OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))")
        if [[ "$COUNT" -gt 0 ]]; then
            ok "pa11y funcional: $COUNT issues detectados no HTML de teste ✓"
        else
            warn "pa11y rodou mas encontrou 0 issues (inesperado no HTML de teste)"
        fi
    else
        warn "pa11y rodou mas saída não é JSON válido"
        info "Saída: ${PA11Y_OUT:0:150}"
    fi
else
    warn "pa11y indisponível — pulando teste funcional"
fi

# ── 8b: ESLint jsx-a11y com TSX de teste ─────────────────────────────────────
info "Testando ESLint jsx-a11y com componente TSX..."

ESLINT_MAJOR_NUM="${ESLINT_MAJOR:-0}"
NPM_ROOT_G=$(npm root -g 2>/dev/null || echo "")

TMP_DIR=$(mktemp -d)
TMP_TSX="$TMP_DIR/BadComponent.tsx"
cat > "$TMP_TSX" << 'EOF'
import React from 'react';
export const BadComponent = () => (
  <div onClick={() => console.log('click')}>
    <img src="logo.png" />
    <button></button>
    <a href="">Empty anchor</a>
    <div role="button">Div with role</div>
  </div>
);
EOF

ESLINT_OUT=""
if [[ "$ESLINT_MAJOR_NUM" -ge 9 ]]; then
    TMP_CFG="$TMP_DIR/a11y_cfg.cjs"
    cat > "$TMP_CFG" << 'CFGEOF'
"use strict";
let jsxA11y, tsParser;
try { jsxA11y = require("eslint-plugin-jsx-a11y"); } catch(e) { jsxA11y = { rules: {} }; }
try { tsParser = require("@typescript-eslint/parser"); } catch(e) { tsParser = null; }
const langOpts = {
  parserOptions: { ecmaVersion: 2022, ecmaFeatures: { jsx: true }, sourceType: "module" }
};
if (tsParser) langOpts.parser = tsParser;
const availableRules = new Set(Object.keys(jsxA11y.rules || {}));
const allRules = {
  "jsx-a11y/alt-text": "error",
  "jsx-a11y/click-events-have-key-events": "error",
  "jsx-a11y/anchor-has-content": "error",
  "jsx-a11y/interactive-supports-focus": "error"
};
const rules = Object.fromEntries(
  Object.entries(allRules).filter(([k]) => availableRules.has(k.replace('jsx-a11y/','')))
);
module.exports = [{
  files: ["**/*.tsx","**/*.jsx","**/*.ts","**/*.js"],
  plugins: { "jsx-a11y": jsxA11y },
  languageOptions: langOpts,
  rules,
}];
CFGEOF
    ESLINT_OUT=$(NODE_PATH="$NPM_ROOT_G" npx eslint --format json --config "$TMP_CFG" "$TMP_TSX" 2>/tmp/eslint_test_err.txt || true)
else
    TMP_CFG="$TMP_DIR/.eslintrc.json"
    cat > "$TMP_CFG" << 'CFGEOF'
{
  "root": true,
  "parser": "@typescript-eslint/parser",
  "parserOptions": { "ecmaVersion": 2022, "ecmaFeatures": {"jsx": true}, "sourceType": "module" },
  "plugins": ["jsx-a11y"],
  "rules": {
    "jsx-a11y/alt-text": "error",
    "jsx-a11y/click-events-have-key-events": "error",
    "jsx-a11y/anchor-has-content": "error",
    "jsx-a11y/interactive-supports-focus": "error"
  }
}
CFGEOF
    ESLINT_OUT=$(npx eslint --format json --no-eslintrc --config "$TMP_CFG" "$TMP_TSX" 2>/tmp/eslint_test_err.txt || true)
fi
rm -rf "$TMP_DIR"

if echo "$ESLINT_OUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
msgs = sum(len(f.get('messages', [])) for f in data)
print(msgs)
" &>/dev/null 2>&1; then
    ESLINT_COUNT=$(echo "$ESLINT_OUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
msgs = sum(len(f.get('messages', [])) for f in data)
print(msgs)
")
    if [[ "$ESLINT_COUNT" -gt 0 ]]; then
        ok "ESLint jsx-a11y funcional: $ESLINT_COUNT issues detectados no TSX de teste ✓"
    else
        warn "ESLint rodou mas encontrou 0 issues (esperado ≥ 2 no TSX de teste)"
        [[ -s /tmp/eslint_test_err.txt ]] && info "Stderr: $(head -2 /tmp/eslint_test_err.txt)"
    fi
else
    fail "ESLint não produziu JSON válido"
    info "stdout: ${ESLINT_OUT:0:200}"
    [[ -s /tmp/eslint_test_err.txt ]] && info "stderr: $(head -3 /tmp/eslint_test_err.txt)"
fi

# ── 8c: Playwright + axe-core ────────────────────────────────────────────────
info "Testando Playwright + axe-core..."

PLAYWRIGHT_TEST_RESULT=$(python3 - << 'PYEOF' 2>/dev/null || echo "error"
import asyncio, json, sys
async def test():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("playwright_not_installed")
        return

    # Procurar axe local
    import subprocess, pathlib
    axe_path = None
    try:
        npm_root = subprocess.check_output(["npm", "root", "-g"], text=True, timeout=10).strip()
        for p in [pathlib.Path(npm_root) / "axe-core" / "axe.min.js",
                  pathlib.Path(npm_root) / "@axe-core" / "cli" / "node_modules" / "axe-core" / "axe.min.js"]:
            if p.exists():
                axe_path = str(p)
                break
    except Exception:
        pass

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
        page = await browser.new_page()
        try:
            await page.set_content("""<!DOCTYPE html>
<html lang="en"><head><title>Test</title></head>
<body><img src="x.png"><button></button></body></html>""")
            if axe_path:
                await page.add_script_tag(path=axe_path)
            else:
                await page.add_script_tag(url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js")
                await page.wait_for_function("window.axe !== undefined", timeout=15000)
            result = await page.evaluate("""
async () => {
  const r = await window.axe.run(document, { runOnly: { type:'tag', values:['wcag2a','wcag2aa'] } });
  return r.violations.length;
}""")
            print(f"ok:{result}")
        except Exception as e:
            print(f"error:{e}")
        finally:
            await browser.close()

asyncio.run(test())
PYEOF
)

if echo "$PLAYWRIGHT_TEST_RESULT" | grep -q "^ok:"; then
    PW_VIOLATIONS=$(echo "$PLAYWRIGHT_TEST_RESULT" | grep -oP '\d+')
    if [[ "$PW_VIOLATIONS" -gt 0 ]]; then
        ok "Playwright + axe-core funcional: $PW_VIOLATIONS violations detectados ✓"
    else
        warn "Playwright rodou mas encontrou 0 violations (esperado ≥ 1)"
    fi
elif echo "$PLAYWRIGHT_TEST_RESULT" | grep -q "playwright_not_installed"; then
    warn "Playwright não está instalado no Python"
    [[ "$CHECK_ONLY" == "false" ]] && pip install playwright &>/dev/null && python3 -m playwright install chromium &>/dev/null && ok "playwright instalado"
else
    warn "Playwright encontrou erro: $PLAYWRIGHT_TEST_RESULT"
fi

# ─── RESUMO FINAL ─────────────────────────────────────────────────────────────
hdr "RESUMO FINAL"

echo ""
printf "  %-30s" "pa11y:"
command -v pa11y &>/dev/null \
    && echo -e "${GREEN}✅ $(pa11y --version 2>&1)${NC}" \
    || (npx pa11y --version &>/dev/null 2>&1 \
        && echo -e "${GREEN}✅ via npx$(NC)" \
        || echo -e "${RED}❌ NÃO DISPONÍVEL${NC}")

printf "  %-30s" "ESLint:"
npx eslint --version &>/dev/null 2>&1 \
    && echo -e "${GREEN}✅ $(npx eslint --version 2>&1)${NC}" \
    || echo -e "${RED}❌ NÃO DISPONÍVEL${NC}"

printf "  %-30s" "jsx-a11y (global):"
npm list -g eslint-plugin-jsx-a11y 2>/dev/null | grep -q jsx-a11y \
    && echo -e "${GREEN}✅ instalado${NC}" \
    || echo -e "${RED}❌ NÃO INSTALADO${NC}"

printf "  %-30s" "@typescript-eslint/parser:"
npm list -g @typescript-eslint/parser 2>/dev/null | grep -q typescript \
    && echo -e "${GREEN}✅ instalado${NC}" \
    || echo -e "${YELLOW}⚠️  não encontrado${NC}"

printf "  %-30s" "axe-core (npm local):"
[[ -n "$AXE_PATH" ]] \
    && echo -e "${GREEN}✅ $AXE_PATH${NC}" \
    || echo -e "${YELLOW}⚠️  não encontrado (CDN será usado)${NC}"

printf "  %-30s" "Playwright (Python):"
python3 -c "import playwright; print(playwright.__version__)" &>/dev/null 2>&1 \
    && echo -e "${GREEN}✅ $(python3 -c "import playwright; print(playwright.__version__)" 2>/dev/null)${NC}" \
    || echo -e "${YELLOW}⚠️  não instalado${NC}"

printf "  %-30s" "ChromeDriver:"
command -v chromedriver &>/dev/null \
    && echo -e "${GREEN}✅ $(chromedriver --version 2>&1 | head -1)${NC}" \
    || echo -e "${YELLOW}⚠️  não encontrado${NC}"

echo ""
echo -e "${BLUE}─────────────────────────────────────────────────${NC}"
if [[ "$CHECK_ONLY" == "false" ]]; then
    echo -e "${BLUE}Para aplicar PATH na sessão atual:${NC}"
    echo "    source ~/.bashrc"
    echo ""
    echo -e "${BLUE}Para reescanear todos os projetos:${NC}"
    echo "    bash reset_scan.sh --yes --and-scan"
    echo ""
    echo -e "${BLUE}Para ver relatório de findings coletados:${NC}"
    echo "    python dataset/scripts/findings_report.py"
fi
echo ""
