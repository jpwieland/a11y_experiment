#Requires -Version 5.1
# ============================================================
#  fix_scanners.ps1 -- Instala, valida e repara ferramentas de scan
#  Equivalente ao fix_scanners.sh para Windows (PowerShell)
#
#  Uso:
#    .\fix_scanners.ps1              # instala e valida tudo
#    .\fix_scanners.ps1 -CheckOnly   # so diagnostico, sem instalar
# ============================================================

[CmdletBinding()]
param(
    [switch]$CheckOnly,
    # Aceita --check-only (estilo Linux/bash) como alias de -CheckOnly
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ExtraArgs
)
# Suporte ao estilo Linux: .\fix_scanners.ps1 --check-only
if ($ExtraArgs -contains "--check-only") { $CheckOnly = $true }

$ErrorActionPreference = "Continue"  # npm/node gravam stderr sem ser erros reais

# UTF-8 no console para suportar saida Unicode dos scripts Python
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8           = "1"
$env:PYTHONIOENCODING     = "utf-8"
$env:NODE_NO_WARNINGS     = "1"

# ── Cores ────────────────────────────────────────────────────
function Ok   { param([string]$M); Write-Host "  [OK] $M" -ForegroundColor Green }
function Fail { param([string]$M); Write-Host "  [FAIL] $M" -ForegroundColor Red }
function Warn { param([string]$M); Write-Host "  [AVISO] $M" -ForegroundColor Yellow }
function Info { param([string]$M); Write-Host "  [INFO] $M" -ForegroundColor Cyan }
function Hdr  { param([string]$M)
    Write-Host ""
    Write-Host ("=" * 50) -ForegroundColor Magenta
    Write-Host "  $M" -ForegroundColor Magenta
    Write-Host ("=" * 50) -ForegroundColor Magenta
}

function Has { param([string]$Cmd); return [bool](Get-Command $Cmd -ErrorAction SilentlyContinue) }

# ── Caminhos do projeto ───────────────────────────────────────
$ProjectRoot = $PSScriptRoot
$VenvPython  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

# ── PASSO 1: Node.js e npm ────────────────────────────────────
Hdr "PASSO 1: Node.js e npm"

if (Has "node") {
    Ok "Node.js: $(node --version)"
} else {
    Fail "Node.js NAO instalado!"
    Write-Host "  Instale em: https://nodejs.org/en/download/"
    Write-Host "  OU: winget install OpenJS.NodeJS.LTS"
    exit 1
}

if (Has "npm") {
    Ok "npm: $(npm --version)"
} else {
    Fail "npm NAO encontrado!"
    exit 1
}

$NpmPrefix = (npm config get prefix 2>$null).Trim()
$NpmBin    = $NpmPrefix  # No Windows, o prefix JA e o bin dir para executaveis
Info "npm prefix: $NpmPrefix"

# Garantir npm no PATH
if ($env:PATH -notlike "*$NpmPrefix*") {
    Warn "npm prefix NAO no PATH: $NpmPrefix"
    if (-not $CheckOnly) {
        $env:PATH = "$NpmPrefix;$env:PATH"
        Info "npm prefix adicionado ao PATH da sessao"
        Info "Para persistir, adicione ao PATH do sistema em Painel de Controle > Variaveis de Ambiente"
    } else {
        Fail "Adicione ao PATH: $NpmPrefix"
    }
} else {
    Ok "npm bin no PATH: $NpmPrefix"
}

# ── PASSO 2: pa11y ────────────────────────────────────────────
Hdr "PASSO 2: pa11y"

$Pa11yCmd = ""
if (Has "pa11y") {
    $Pa11yCmd = "pa11y"
    $ver = (pa11y --version 2>$null).Trim()
    Ok "pa11y: $ver"
} else {
    Warn "pa11y nao encontrado"
    if (-not $CheckOnly) {
        Info "Instalando pa11y globalmente..."
        npm install -g pa11y 2>&1 | Select-Object -Last 3
        if (Has "pa11y") {
            $Pa11yCmd = "pa11y"
            Ok "pa11y instalado: $(pa11y --version 2>$null)"
        } else {
            Fail "Falha ao instalar pa11y -- tente: npm install -g pa11y"
        }
    } else {
        Fail "Instale: npm install -g pa11y"
    }
}

# ── PASSO 3: axe-core ─────────────────────────────────────────
Hdr "PASSO 3: axe-core (npm)"

# Procurar axe-core no npm global
$NpmRoot = (npm root -g 2>$null).Trim()
$AxePath = ""
if ($NpmRoot) {
    $candidates = @(
        (Join-Path $NpmRoot "axe-core\axe.min.js"),
        (Join-Path $NpmRoot "@axe-core\cli\node_modules\axe-core\axe.min.js")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $AxePath = $c; break }
    }
}

if ($AxePath) {
    $sz = [math]::Round((Get-Item $AxePath).Length / 1KB, 0)
    Ok "axe-core (local): $AxePath  (${sz} KB)"
} else {
    Warn "axe-core nao encontrado no npm global"
    if (-not $CheckOnly) {
        Info "Instalando axe-core + @axe-core/cli..."
        npm install -g axe-core "@axe-core/cli" 2>&1 | Select-Object -Last 3

        # Re-verificar
        $NpmRoot = (npm root -g 2>$null).Trim()
        $axeCheck = Join-Path $NpmRoot "axe-core\axe.min.js"
        if (Test-Path $axeCheck) {
            Ok "axe-core instalado: $axeCheck"
            $AxePath = $axeCheck
        } else {
            Warn "axe-core pode nao estar disponivel localmente (CDN sera usado como fallback)"
        }
    } else {
        Warn "Instale: npm install -g axe-core @axe-core/cli"
    }
}

# ── PASSO 4: ESLint + plugins ─────────────────────────────────
Hdr "PASSO 4: ESLint + jsx-a11y"

$EslintOk = $false
$JsxA11yOk = $false
$TsParserOk = $false
$EslintMajor = 0

try {
    $eslintVer = (npx eslint --version 2>$null).Trim()
    if ($eslintVer) {
        $EslintMajor = [int]($eslintVer -replace '^v(\d+).*','$1')
        Ok "ESLint: $eslintVer (major: $EslintMajor)"
        $EslintOk = $true
    }
} catch {
    Warn "ESLint nao encontrado"
}

try {
    $listOut = npm list -g eslint-plugin-jsx-a11y 2>$null
    if ($listOut -match "eslint-plugin-jsx-a11y") {
        Ok "eslint-plugin-jsx-a11y: instalado"
        $JsxA11yOk = $true
    } else {
        Warn "eslint-plugin-jsx-a11y NAO instalado globalmente"
    }
} catch {
    Warn "eslint-plugin-jsx-a11y NAO instalado"
}

try {
    $listOut = npm list -g "@typescript-eslint/parser" 2>$null
    if ($listOut -match "typescript-eslint") {
        Ok "@typescript-eslint/parser: instalado"
        $TsParserOk = $true
    } else {
        Warn "@typescript-eslint/parser NAO instalado"
    }
} catch {
    Warn "@typescript-eslint/parser NAO instalado"
}

if (-not $EslintOk -or -not $JsxA11yOk -or -not $TsParserOk) {
    if (-not $CheckOnly) {
        Info "Instalando ESLint + plugins..."
        npm install -g eslint eslint-plugin-jsx-a11y "@typescript-eslint/parser" "@typescript-eslint/eslint-plugin" 2>&1 | Select-Object -Last 5
        try {
            $eslintVer = (npx eslint --version 2>$null).Trim()
            Ok "ESLint instalado: $eslintVer"
        } catch {
            Fail "Falha ao instalar ESLint"
        }
    } else {
        Fail "Instale: npm install -g eslint eslint-plugin-jsx-a11y @typescript-eslint/parser @typescript-eslint/eslint-plugin"
    }
}

# ── PASSO 5: Playwright (Python) ──────────────────────────────
Hdr "PASSO 5: Playwright (Python)"

if (-not (Test-Path $VenvPython)) {
    Warn ".venv nao encontrado --usando python do sistema"
    $PyCmd = "python"
} else {
    $PyCmd = $VenvPython
}

$pwInstalled = $false
try {
    & $PyCmd -c "import playwright" 2>$null
    $pwVer = & $PyCmd -c @"
try:
    from importlib.metadata import version as _v
    print(_v('playwright'))
except Exception:
    print('instalado')
"@
    Ok "playwright Python: $pwVer"
    $pwInstalled = $true
} catch {
    Warn "playwright Python NAO instalado"
    if (-not $CheckOnly) {
        Info "Instalando playwright..."
        & $PyCmd -m pip install playwright 2>&1 | Select-Object -Last 3
        $pwInstalled = $true
    } else {
        Fail "Execute: pip install playwright"
    }
}

if ($pwInstalled) {
    # Verificar se chromium esta instalado
    $pwChromiumOk = $false
    try {
        $testScript = @"
import asyncio
async def test():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        await b.close()
        print('ok')
asyncio.run(test())
"@
        $result = & $PyCmd -c $testScript 2>$null
        if ($result -eq "ok") {
            Ok "playwright chromium: disponivel"
            $pwChromiumOk = $true
        }
    } catch {}

    if (-not $pwChromiumOk) {
        Warn "playwright chromium NAO disponivel"
        if (-not $CheckOnly) {
            Info "Instalando chromium para playwright..."
            & $PyCmd -m playwright install chromium --with-deps 2>&1 | Select-Object -Last 3
            Ok "playwright chromium instalado"
        } else {
            Fail "Execute: python -m playwright install chromium --with-deps"
        }
    }
}

# ── PASSO 6: Testes funcionais ────────────────────────────────
Hdr "PASSO 6: Testes funcionais"

# 6a: pa11y com servidor HTTP local
if ($Pa11yCmd -or (Has "pa11y")) {
    Info "Testando pa11y com servidor HTTP local..."
    $TmpDir  = Join-Path $env:TEMP "a11y_test_$(Get-Random)"
    $TmpHtml = Join-Path $TmpDir "test.html"
    New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null

    @"
<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
  <img src="test.jpg">
  <button></button>
  <input type="text" name="q">
  <a href="#"></a>
</body>
</html>
"@ | Out-File -FilePath $TmpHtml -Encoding UTF8

    # Iniciar servidor HTTP Python em porta aleatoria
    $serverJob = Start-Job -ScriptBlock {
        param($Dir)
        & python -m http.server 18765 --directory $Dir 2>&1
    } -ArgumentList $TmpDir

    Start-Sleep -Milliseconds 800

    try {
        $Pa11yTest = if ($Pa11yCmd) { $Pa11yCmd } else { "pa11y" }
        $pa11yOut = & $Pa11yTest --reporter json --standard WCAG2AA --timeout 30000 "http://127.0.0.1:18765/test.html" 2>$null
        $count = ($pa11yOut | python -c "import sys,json; d=json.load(sys.stdin); print(len(d))" 2>$null)
        if ($count -and [int]$count -gt 0) {
            Ok "pa11y funcional: $count issues detectados no HTML de teste"
        } else {
            Warn "pa11y encontrou 0 issues no HTML de teste (esperado >=1)"
            Warn "  Resultado: $($pa11yOut | Select-Object -First 2)"
        }
    } catch {
        Warn "pa11y teste falhou: $_"
    } finally {
        Stop-Job $serverJob -ErrorAction SilentlyContinue
        Remove-Job $serverJob -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
    }
}

# 6b: ESLint jsx-a11y
Info "Testando ESLint jsx-a11y com componente TSX..."
$TmpDir2 = Join-Path $env:TEMP "a11y_eslint_$(Get-Random)"
$TmpTsx  = Join-Path $TmpDir2 "BadComponent.tsx"
New-Item -ItemType Directory -Path $TmpDir2 -Force | Out-Null

@"
import React from 'react';
export const BadComponent = () => (
  <div onClick={() => console.log('click')}>
    <img src="logo.png" />
    <button></button>
    <a href="">Empty anchor</a>
  </div>
);
"@ | Out-File -FilePath $TmpTsx -Encoding UTF8

try {
    if ($EslintMajor -ge 9) {
        $TmpCfg = Join-Path $TmpDir2 "a11y_cfg.cjs"
        @"
"use strict";
let jsxA11y = { rules: {} }, tsParser = null;
try { jsxA11y = require("eslint-plugin-jsx-a11y"); } catch(e) {}
try { tsParser = require("@typescript-eslint/parser"); } catch(e) {}
const langOpts = { parserOptions: { ecmaVersion: 2022, ecmaFeatures: { jsx: true }, sourceType: "module" } };
if (tsParser) langOpts.parser = tsParser;
const availableRules = new Set(Object.keys(jsxA11y.rules || {}));
const allRules = { "jsx-a11y/alt-text": "error", "jsx-a11y/anchor-has-content": "error" };
const rules = Object.fromEntries(Object.entries(allRules).filter(([k]) => availableRules.has(k.replace('jsx-a11y/',''))));
module.exports = [{ files: ["**/*.tsx"], plugins: { "jsx-a11y": jsxA11y }, languageOptions: langOpts, rules }];
"@ | Out-File -FilePath $TmpCfg -Encoding UTF8

        $NpmRootG = (npm root -g 2>$null).Trim()
        $env:NODE_PATH = $NpmRootG
        $eslintOut = npx eslint --format json --config $TmpCfg $TmpTsx 2>$null
    } else {
        $TmpCfg = Join-Path $TmpDir2 ".eslintrc.json"
        @'
{
  "root": true,
  "parser": "@typescript-eslint/parser",
  "parserOptions": { "ecmaVersion": 2022, "ecmaFeatures": {"jsx": true}, "sourceType": "module" },
  "plugins": ["jsx-a11y"],
  "rules": { "jsx-a11y/alt-text": "error", "jsx-a11y/anchor-has-content": "error" }
}
'@ | Out-File -FilePath $TmpCfg -Encoding UTF8
        $eslintOut = npx eslint --format json --no-eslintrc --config $TmpCfg $TmpTsx 2>$null
    }

    $count = $eslintOut | python -c "import sys,json; d=json.load(sys.stdin); print(sum(len(f.get('messages',[])) for f in d))" 2>$null
    if ($count -and [int]$count -gt 0) {
        Ok "ESLint jsx-a11y funcional: $count issues detectados no TSX de teste"
    } else {
        Warn "ESLint rodou mas encontrou 0 issues (esperado >= 1)"
    }
} catch {
    Warn "ESLint teste falhou: $_"
} finally {
    Remove-Item -Recurse -Force $TmpDir2 -ErrorAction SilentlyContinue
}

# 6c: Playwright + axe-core
Info "Testando Playwright + axe-core..."

$pwTestScript = @"
import asyncio, json

async def test():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print('playwright_not_installed')
        return

    import pathlib
    axe_path = None
    try:
        import subprocess
        npm_root = subprocess.check_output(['npm', 'root', '-g'], text=True, timeout=10).strip()
        for p in [pathlib.Path(npm_root) / 'axe-core' / 'axe.min.js',
                  pathlib.Path(npm_root) / '@axe-core' / 'cli' / 'node_modules' / 'axe-core' / 'axe.min.js']:
            if p.exists():
                axe_path = str(p)
                break
    except Exception:
        pass

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await page.set_content('''<!DOCTYPE html><html lang="en"><head><title>T</title></head>
<body><img src="x.png"><button></button></body></html>''')
            if axe_path:
                await page.add_script_tag(path=axe_path)
            else:
                await page.add_script_tag(url='https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js')
                await page.wait_for_function('window.axe !== undefined', timeout=15000)
            result = await page.evaluate('''async () => {
  const r = await window.axe.run(document, { runOnly: { type:"tag", values:["wcag2a","wcag2aa"] } });
  return r.violations.length;
}''')
            print(f'ok:{result}')
        except Exception as e:
            print(f'error:{e}')
        finally:
            await browser.close()

asyncio.run(test())
"@

try {
    $pwResult = & $PyCmd -c $pwTestScript 2>$null
    if ($pwResult -match "^ok:(\d+)$") {
        $violations = $Matches[1]
        if ([int]$violations -gt 0) {
            Ok "Playwright + axe-core funcional: $violations violations detectados"
        } else {
            Warn "Playwright rodou mas encontrou 0 violations (esperado >= 1)"
        }
    } elseif ($pwResult -eq "playwright_not_installed") {
        Warn "Playwright nao esta instalado no Python"
    } else {
        Warn "Playwright encontrou erro: $pwResult"
    }
} catch {
    Warn "Playwright teste falhou: $_"
}

# ── RESUMO FINAL ──────────────────────────────────────────────
Hdr "RESUMO FINAL"
Write-Host ""

$items = @(
    @{Label="pa11y"; Check={ Has "pa11y" }; Fix="npm install -g pa11y"},
    @{Label="ESLint"; Check={ try { npx eslint --version 2>$null; $true } catch { $false } }; Fix="npm install -g eslint"},
    @{Label="jsx-a11y (global)"; Check={ (npm list -g eslint-plugin-jsx-a11y 2>$null) -match "jsx-a11y" }; Fix="npm install -g eslint-plugin-jsx-a11y"},
    @{Label="@typescript-eslint/parser"; Check={ (npm list -g "@typescript-eslint/parser" 2>$null) -match "typescript" }; Fix="npm install -g @typescript-eslint/parser"},
    @{Label="Playwright (Python)"; Check={ try { & $PyCmd -c "import playwright" 2>$null; $true } catch { $false } }; Fix="pip install playwright"},
    @{Label="axe-core (npm local)"; Check={ [bool]$AxePath }; Fix="npm install -g axe-core @axe-core/cli"}
)

foreach ($item in $items) {
    $label = $item.Label.PadRight(30)
    $ok = try { & $item.Check } catch { $false }
    if ($ok) {
        Write-Host "  $label" -NoNewline; Write-Host "OK" -ForegroundColor Green
    } else {
        Write-Host "  $label" -NoNewline; Write-Host "NAO DISPONIVEL  (fix: $($item.Fix))" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Para aplicar PATH na sessao atual, reinicie o terminal." -ForegroundColor Cyan
Write-Host "Para reescanear todos os projetos: .\reset_scan.ps1 -Yes -AndScan" -ForegroundColor Cyan
Write-Host ""
