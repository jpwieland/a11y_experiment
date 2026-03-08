#!/usr/bin/env bash
# =============================================================================
# fix_scanners.sh — Instala e valida pa11y, ESLint jsx-a11y e ChromeDriver
#
# Problema: runner_unavailable tool=pa11y + axe_json_parse_error
# Causa:   pa11y não no PATH | ESLint não instalado | ChromeDriver desatualizado
#
# Uso: bash fix_scanners.sh [--check-only]
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $*${NC}"; }
fail() { echo -e "${RED}❌ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
info() { echo -e "${BLUE}ℹ️  $*${NC}"; }

CHECK_ONLY=false
[[ "${1:-}" == "--check-only" ]] && CHECK_ONLY=true

# ─── Descobrir npm bin dir ────────────────────────────────────────────────────
# CORRETO: usar `npm config get prefix` e não `npm root -g`
#   npm root -g  → /home/user/.local/npm/lib/node_modules
#   npm prefix   → /home/user/.local/npm
#   npm bin      → /home/user/.local/npm/bin   ← precisa deste
get_npm_bin() {
    local npm_prefix
    npm_prefix=$(npm config get prefix 2>/dev/null) || { fail "npm não encontrado"; exit 1; }
    echo "${npm_prefix}/bin"
}

# ─── Adicionar npm bin ao PATH (sessão atual + persistência) ─────────────────
ensure_npm_in_path() {
    local npm_bin
    npm_bin=$(get_npm_bin)

    if echo "$PATH" | grep -q "$npm_bin"; then
        ok "npm bin já no PATH: $npm_bin"
        return 0
    fi

    warn "npm bin NÃO está no PATH: $npm_bin"

    if [[ "$CHECK_ONLY" == "true" ]]; then
        fail "Adicione ao PATH: export PATH=\"$npm_bin:\$PATH\""
        return 1
    fi

    # Adicionar à sessão atual
    export PATH="$npm_bin:$PATH"
    info "PATH atualizado para esta sessão"

    # Persistir no .bashrc
    local bashrc="$HOME/.bashrc"
    local profile="$HOME/.profile"
    local marker="# a11y-autofix npm PATH"
    local line="export PATH=\"$npm_bin:\$PATH\"  $marker"

    if ! grep -q "$marker" "$bashrc" 2>/dev/null; then
        echo "" >> "$bashrc"
        echo "$line" >> "$bashrc"
        ok "PATH adicionado ao $bashrc"
    else
        warn "PATH já estava no $bashrc (pulando)"
    fi

    if ! grep -q "$marker" "$profile" 2>/dev/null; then
        echo "" >> "$profile"
        echo "$line" >> "$profile"
        ok "PATH adicionado ao $profile"
    fi
}

# ─── PASSO 1: Verificar Node.js e npm ────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "  PASSO 1: Verificar Node.js e npm"
echo "════════════════════════════════════════════════════"

if command -v node &>/dev/null; then
    NODE_VER=$(node --version)
    ok "Node.js: $NODE_VER"
else
    fail "Node.js NÃO instalado!"
    echo "  Instale via: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs"
    exit 1
fi

if command -v npm &>/dev/null; then
    NPM_VER=$(npm --version)
    ok "npm: $NPM_VER"
else
    fail "npm NÃO encontrado!"
    exit 1
fi

NPM_BIN=$(get_npm_bin)
info "npm bin global: $NPM_BIN"

# ─── PASSO 2: Corrigir PATH para npm ─────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "  PASSO 2: Verificar PATH do npm"
echo "════════════════════════════════════════════════════"
ensure_npm_in_path

# ─── PASSO 3: Instalar pa11y ─────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "  PASSO 3: pa11y"
echo "════════════════════════════════════════════════════"

if command -v pa11y &>/dev/null; then
    PA11Y_VER=$(pa11y --version 2>&1 || echo "erro ao obter versão")
    ok "pa11y já instalado: $PA11Y_VER"
else
    warn "pa11y não encontrado"
    if [[ "$CHECK_ONLY" == "true" ]]; then
        fail "Instale: npm install -g pa11y"
    else
        info "Instalando pa11y..."
        npm install -g pa11y

        # Re-verificar após instalação
        if command -v pa11y &>/dev/null; then
            PA11Y_VER=$(pa11y --version 2>&1)
            ok "pa11y instalado com sucesso: $PA11Y_VER"
        else
            # Tentar caminho direto
            if [[ -f "$NPM_BIN/pa11y" ]]; then
                PA11Y_VER=$("$NPM_BIN/pa11y" --version 2>&1)
                ok "pa11y encontrado em $NPM_BIN: $PA11Y_VER"
                warn "PATH pode não ter sido atualizado — reinicie o terminal ou: source ~/.bashrc"
            else
                fail "pa11y instalado mas não encontrado. Verifique: $NPM_BIN"
                ls -la "$NPM_BIN/" | grep -i pa11y || echo "  (não listado)"
            fi
        fi
    fi
fi

# ─── PASSO 4: Instalar ESLint + jsx-a11y ─────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "  PASSO 4: ESLint + jsx-a11y"
echo "════════════════════════════════════════════════════"

ESLINT_OK=false
JSXA11Y_OK=false
TS_PARSER_OK=false

# Verificar ESLint
if command -v eslint &>/dev/null || npx eslint --version &>/dev/null 2>&1; then
    ESLINT_VER=$(npx eslint --version 2>&1 | head -1)
    ok "ESLint disponível: $ESLINT_VER"
    ESLINT_OK=true
else
    warn "ESLint não encontrado"
fi

# Verificar jsx-a11y
if npm list -g eslint-plugin-jsx-a11y 2>/dev/null | grep -q jsx-a11y; then
    ok "eslint-plugin-jsx-a11y instalado globalmente"
    JSXA11Y_OK=true
else
    warn "eslint-plugin-jsx-a11y não instalado globalmente"
fi

# Verificar @typescript-eslint/parser
if npm list -g @typescript-eslint/parser 2>/dev/null | grep -q typescript-eslint; then
    ok "@typescript-eslint/parser instalado globalmente"
    TS_PARSER_OK=true
else
    warn "@typescript-eslint/parser não instalado globalmente"
fi

if [[ "$ESLINT_OK" == "false" ]] || [[ "$JSXA11Y_OK" == "false" ]] || [[ "$TS_PARSER_OK" == "false" ]]; then
    if [[ "$CHECK_ONLY" == "true" ]]; then
        fail "Execute: npm install -g eslint eslint-plugin-jsx-a11y @typescript-eslint/parser @typescript-eslint/eslint-plugin"
    else
        info "Instalando ESLint + plugins..."
        npm install -g eslint \
                       eslint-plugin-jsx-a11y \
                       @typescript-eslint/parser \
                       @typescript-eslint/eslint-plugin

        # Re-verificar
        if npx eslint --version &>/dev/null 2>&1; then
            ESLINT_VER=$(npx eslint --version 2>&1 | head -1)
            ok "ESLint instalado: $ESLINT_VER"
        else
            fail "ESLint não funcionou após instalação"
        fi
    fi
fi

# ─── PASSO 5: Corrigir ChromeDriver (axe/playwright) ─────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "  PASSO 5: Chrome + ChromeDriver (axe-core)"
echo "════════════════════════════════════════════════════"

CHROME_VER=""
DRIVER_VER=""

if command -v google-chrome &>/dev/null; then
    CHROME_VER=$(google-chrome --version 2>&1 | grep -oP '\d+\.\d+\.\d+\.\d+' | head -1)
    ok "Google Chrome: $CHROME_VER"
elif command -v chromium-browser &>/dev/null; then
    CHROME_VER=$(chromium-browser --version 2>&1 | grep -oP '\d+\.\d+\.\d+\.\d+' | head -1)
    ok "Chromium: $CHROME_VER"
elif command -v chromium &>/dev/null; then
    CHROME_VER=$(chromium --version 2>&1 | grep -oP '\d+\.\d+\.\d+\.\d+' | head -1)
    ok "Chromium: $CHROME_VER"
else
    warn "Chrome/Chromium não encontrado no PATH"
fi

if command -v chromedriver &>/dev/null; then
    DRIVER_VER=$(chromedriver --version 2>&1 | grep -oP '\d+\.\d+\.\d+\.\d+' | head -1)
    info "ChromeDriver atual: $DRIVER_VER"

    CHROME_MAJOR=$(echo "$CHROME_VER" | cut -d. -f1)
    DRIVER_MAJOR=$(echo "$DRIVER_VER" | cut -d. -f1)

    if [[ -n "$CHROME_MAJOR" && -n "$DRIVER_MAJOR" && "$CHROME_MAJOR" != "$DRIVER_MAJOR" ]]; then
        warn "MISMATCH: Chrome $CHROME_MAJOR vs ChromeDriver $DRIVER_MAJOR"
        if [[ "$CHECK_ONLY" == "true" ]]; then
            fail "Execute: npx browser-driver-manager install chrome"
        else
            info "Instalando ChromeDriver compatível..."
            npx browser-driver-manager install chrome 2>&1 | tail -5
            DRIVER_VER_NEW=$(chromedriver --version 2>&1 | grep -oP '\d+\.\d+\.\d+\.\d+' | head -1)
            ok "ChromeDriver atualizado: $DRIVER_VER_NEW"
        fi
    else
        ok "Chrome ($CHROME_MAJOR) e ChromeDriver ($DRIVER_MAJOR) compatíveis"
    fi
else
    warn "ChromeDriver não encontrado"
    if [[ "$CHECK_ONLY" == "true" ]]; then
        fail "Execute: npx browser-driver-manager install chrome"
    else
        info "Instalando ChromeDriver..."
        npx browser-driver-manager install chrome 2>&1 | tail -5
        ok "ChromeDriver instalado"
    fi
fi

# ─── PASSO 6: Verificação funcional ──────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "  PASSO 6: Teste funcional"
echo "════════════════════════════════════════════════════"

# Teste pa11y
if command -v pa11y &>/dev/null; then
    # Criar HTML mínimo para testar
    TMP_HTML=$(mktemp /tmp/test_XXXXXX.html)
    cat > "$TMP_HTML" << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head><title>Test</title></head>
<body><img src="test.jpg"><button>Click</button></body>
</html>
EOF

    PA11Y_OUT=$(pa11y --reporter json "file://$TMP_HTML" 2>&1 | head -c 200 || true)
    rm -f "$TMP_HTML"

    if echo "$PA11Y_OUT" | grep -q '"code"'; then
        ok "pa11y funcionando (encontrou issues no HTML de teste ✓)"
    elif echo "$PA11Y_OUT" | grep -q '\[\]'; then
        ok "pa11y rodou (sem issues no HTML de teste)"
    else
        warn "pa11y rodou mas saída inesperada: ${PA11Y_OUT:0:100}"
    fi
fi

# Teste ESLint jsx-a11y — detecta versão e usa flat config (9+/10) ou legado (8)
ESLINT_MAJOR=$(npx eslint --version 2>/dev/null | grep -oP '^\D*\K\d+' | head -1 || echo "8")
info "ESLint major version: $ESLINT_MAJOR"

NPM_ROOT_G=$(npm root -g 2>/dev/null || echo "")

TMP_TSX=$(mktemp /tmp/test_XXXXXX.tsx)
cat > "$TMP_TSX" << 'EOF'
import React from 'react';
export const BadComponent = () => (
  <div onClick={() => console.log('click')}>
    <img src="logo.png" />
    <button>Submit</button>
  </div>
);
EOF

if [[ "$ESLINT_MAJOR" -ge 9 ]]; then
    # ── Flat config para ESLint 9+/10 ──
    info "Usando flat config (.cjs) para ESLint $ESLINT_MAJOR"
    TMP_CFG=$(mktemp /tmp/a11y_eslint_cfg_XXXXXX.cjs)
    cat > "$TMP_CFG" << 'CFGEOF'
"use strict";
let jsxA11y, tsParser;
try { jsxA11y = require("eslint-plugin-jsx-a11y"); } catch(e) { jsxA11y = { rules: {} }; }
try { tsParser = require("@typescript-eslint/parser"); } catch(e) { tsParser = null; }
const langOpts = { parserOptions: { ecmaVersion: 2022, ecmaFeatures: { jsx: true }, sourceType: "module" } };
if (tsParser) langOpts.parser = tsParser;
module.exports = [{
  files: ["**/*.tsx", "**/*.jsx", "**/*.ts", "**/*.js"],
  plugins: { "jsx-a11y": jsxA11y },
  languageOptions: langOpts,
  rules: { "jsx-a11y/alt-text": "error", "jsx-a11y/click-events-have-key-events": "error" }
}];
CFGEOF
    ESLINT_OUT=$(NODE_PATH="$NPM_ROOT_G" npx eslint --format json --config "$TMP_CFG" "$TMP_TSX" 2>/tmp/eslint_test_err.txt || true)
else
    # ── Config legado para ESLint 8 ──
    info "Usando config legado (.eslintrc.json) para ESLint $ESLINT_MAJOR"
    TMP_CFG=$(mktemp /tmp/.eslintrc_XXXXXX.json)
    cat > "$TMP_CFG" << 'CFGEOF'
{
  "root": true,
  "parser": "@typescript-eslint/parser",
  "parserOptions": { "ecmaVersion": 2022, "ecmaFeatures": {"jsx": true}, "sourceType": "module" },
  "plugins": ["jsx-a11y"],
  "rules": { "jsx-a11y/alt-text": "error", "jsx-a11y/click-events-have-key-events": "error" }
}
CFGEOF
    ESLINT_OUT=$(npx eslint --format json --no-eslintrc --config "$TMP_CFG" "$TMP_TSX" 2>/tmp/eslint_test_err.txt || true)
fi
rm -f "$TMP_TSX" "$TMP_CFG"

if echo "$ESLINT_OUT" | grep -q '"ruleId"'; then
    ISSUE_COUNT=$(echo "$ESLINT_OUT" | python3 -c "import sys,json; data=json.load(sys.stdin); print(sum(len(f['messages']) for f in data))" 2>/dev/null || echo "?")
    ok "ESLint jsx-a11y funcionando ($ISSUE_COUNT issues detectados no TSX de teste ✓)"
elif echo "$ESLINT_OUT" | grep -q '\[\]'; then
    warn "ESLint rodou mas não encontrou issues"
    if [[ -s /tmp/eslint_test_err.txt ]]; then
        info "Stderr: $(head -3 /tmp/eslint_test_err.txt)"
    fi
else
    fail "ESLint não funcionou corretamente"
    info "Saída: ${ESLINT_OUT:0:200}"
fi

# ─── RESUMO FINAL ─────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "  RESUMO"
echo "════════════════════════════════════════════════════"
echo ""

command -v pa11y &>/dev/null    && ok "pa11y:       $(pa11y --version 2>&1)"    || fail "pa11y:       NÃO DISPONÍVEL"
npx eslint --version &>/dev/null 2>&1 && ok "ESLint:      $(npx eslint --version 2>&1)" || fail "ESLint:      NÃO DISPONÍVEL"
npm list -g eslint-plugin-jsx-a11y 2>/dev/null | grep -q jsx-a11y && ok "jsx-a11y:    instalado" || fail "jsx-a11y:    NÃO INSTALADO"
command -v chromedriver &>/dev/null && ok "ChromeDriver: $(chromedriver --version 2>&1 | head -1)" || warn "ChromeDriver: não encontrado"

echo ""
if [[ "$CHECK_ONLY" == "false" ]]; then
    echo -e "${BLUE}Para aplicar o PATH na sessão atual:${NC}"
    echo "  source ~/.bashrc"
    echo ""
    echo -e "${BLUE}Para reescanear todos os projetos:${NC}"
    echo "  bash reset_scan.sh --yes --and-scan"
fi
