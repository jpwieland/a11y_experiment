#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_gpu_server.sh — Configura o ambiente no servidor GPU sem acesso root
#
# Hardware alvo: NVIDIA RTX 4090 (24 GB VRAM) | Ubuntu/Linux
# Python: usa o disponível no sistema (>= 3.10) ou instala via Miniforge
#
# O que o script faz:
#   1. Verifica GPU (nvidia-smi) e CUDA
#   2. Instala Python 3.11 via Miniforge se necessário (sem root)
#   3. Cria venv e instala dependências do projeto
#   4. Instala Node.js 20 via nvm (sem root) + pa11y + eslint-jsx-a11y
#   5. Instala Chromium via Playwright (sem root, ~/.cache/ms-playwright/)
#   6. Instala Ollama em ~/bin (sem root)
#   7. Baixa os 3 modelos 14B via ollama
#
# Uso:
#   bash scripts/setup_gpu_server.sh
#   bash scripts/setup_gpu_server.sh --models-only   # apenas baixar modelos
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="/scratch/jpvbwieland/a11y_experiment"
SCRATCH_DIR="/scratch/jpvbwieland"
OLLAMA_MODELS_DIR="$SCRATCH_DIR/ollama/models"
LOGS_DIR="$SCRATCH_DIR/logs"
NVM_DIR="$HOME/.nvm"
MINIFORGE_DIR="$HOME/miniforge3"

MODELS_ONLY="${1:-}"

# ── Cores ────────────────────────────────────────────────────────────────────
R='\033[0m'; BOLD='\033[1m'; GREEN='\033[92m'; YELLOW='\033[93m'; RED='\033[91m'; CYAN='\033[96m'
OK="${GREEN}✔${R}"; FAIL="${RED}✘${R}"; INFO="${CYAN}ℹ${R}"

header() { echo -e "\n${BOLD}═══ $1 ═══${R}"; }
ok()     { echo -e "  ${OK}  $1"; }
info()   { echo -e "  ${INFO}  $1"; }
warn()   { echo -e "  ${YELLOW}⚠${R}  $1"; }
fail()   { echo -e "  ${FAIL}  $1"; }

echo -e "\n${BOLD}════════════════════════════════════════════════════════${R}"
echo -e "${BOLD}  ♿  a11y-autofix — GPU Server Setup (sem root)${R}"
echo -e "${BOLD}  Projeto: $PROJECT_DIR${R}"
echo -e "${BOLD}════════════════════════════════════════════════════════${R}"

mkdir -p "$LOGS_DIR"

# ── 1. Verificar GPU ──────────────────────────────────────────────────────────
header "1. GPU"
if command -v nvidia-smi &>/dev/null; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
    ok "nvidia-smi OK"
else
    fail "nvidia-smi não encontrado. Verificar módulo CUDA."
    warn "Continuando sem confirmação de GPU..."
fi

# Verificar CUDA
if command -v nvcc &>/dev/null; then
    CUDA_VER=$(nvcc --version | grep "release" | sed 's/.*release //' | sed 's/,.*//')
    ok "CUDA $CUDA_VER encontrado"
elif [ -f /usr/local/cuda/bin/nvcc ]; then
    export PATH="/usr/local/cuda/bin:$PATH"
    export LD_LIBRARY_PATH="/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}"
    ok "CUDA encontrado em /usr/local/cuda"
else
    warn "nvcc não encontrado. PyTorch usará CUDA disponível via nvidia-smi."
fi

[[ "$MODELS_ONLY" == "--models-only" ]] && { header "Pulando para download de modelos..."; goto_models=1; } || goto_models=0

if [[ "$goto_models" -eq 0 ]]; then

# ── 2. Python ─────────────────────────────────────────────────────────────────
header "2. Python"
PYTHON_CMD=""
for cmd in python3.11 python3.12 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
            PYTHON_CMD="$cmd"
            ok "Python $ver encontrado: $(which $cmd)"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    warn "Python >= 3.10 não encontrado. Instalando Miniforge3..."
    cd /tmp
    wget -q "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh" \
         -O miniforge.sh
    bash miniforge.sh -b -p "$MINIFORGE_DIR"
    rm miniforge.sh
    export PATH="$MINIFORGE_DIR/bin:$PATH"
    conda create -n a11y python=3.11 -y -q
    PYTHON_CMD="$MINIFORGE_DIR/envs/a11y/bin/python"
    ok "Python 3.11 instalado via Miniforge: $PYTHON_CMD"
    echo "export PATH=\"$MINIFORGE_DIR/bin:\$PATH\"" >> ~/.bashrc
fi

# ── 3. venv do projeto ────────────────────────────────────────────────────────
header "3. Ambiente Python (venv)"
cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
    info "Criando venv..."
    $PYTHON_CMD -m venv .venv
fi
source .venv/bin/activate
ok "venv ativado: $(which python)"

# Upgrade pip
pip install --upgrade pip wheel setuptools -q

# PyTorch com CUDA (detecta versão automaticamente)
info "Instalando PyTorch com CUDA..."
CUDA_TAG="cu121"
if command -v nvcc &>/dev/null; then
    VER=$(nvcc --version | grep -oP 'V\d+\.\d+' | head -1 | tr -d 'V')
    MAJOR=$(echo $VER | cut -d. -f1)
    if   [[ "$MAJOR" -ge 12 ]]; then CUDA_TAG="cu121"
    elif [[ "$MAJOR" -eq 11 ]]; then CUDA_TAG="cu118"
    fi
fi
pip install torch torchvision torchaudio \
    --index-url "https://download.pytorch.org/whl/$CUDA_TAG" -q
ok "PyTorch instalado ($CUDA_TAG)"

# Verificar GPU no torch
python -c "import torch; print(f'  CUDA disponível: {torch.cuda.is_available()}, GPUs: {torch.cuda.device_count()}, Dispositivo: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"CPU\"}')"

# Dependências do projeto
info "Instalando dependências do projeto..."
pip install -e "." -q
ok "Dependências do projeto instaladas"

# ── 4. Node.js via nvm ────────────────────────────────────────────────────────
header "4. Node.js + npm packages"
export NVM_DIR="$NVM_DIR"

if [ ! -d "$NVM_DIR" ]; then
    info "Instalando nvm..."
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    ok "nvm instalado"
fi

# Carregar nvm
# shellcheck disable=SC1090
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

if ! command -v node &>/dev/null || [[ "$(node --version | grep -oP '\d+' | head -1)" -lt 18 ]]; then
    info "Instalando Node.js 20 LTS..."
    nvm install 20
    nvm use 20
    nvm alias default 20
fi
ok "Node.js $(node --version)"
ok "npm $(npm --version)"

# Instalar pacotes npm necessários
info "Instalando pa11y, ESLint e plugins..."
npm install -g \
    pa11y \
    eslint \
    eslint-plugin-jsx-a11y \
    @typescript-eslint/parser \
    @typescript-eslint/eslint-plugin \
    --silent
ok "pa11y: $(pa11y --version)"
ok "eslint: $(npx --no-install eslint --version)"

# ── 5. Playwright / Chromium ──────────────────────────────────────────────────
header "5. Playwright + Chromium"
info "Instalando Chromium (~150 MB)..."
# Playwright instala em ~/.cache/ms-playwright/ — sem root
python -m playwright install chromium 2>&1 | tail -3
ok "Chromium instalado ($(python -m playwright --version))"

# ── 6. Ollama (sem root) ───────────────────────────────────────────────────────
header "6. Ollama"
mkdir -p "$HOME/bin"
export PATH="$HOME/bin:$PATH"

if [ ! -f "$HOME/bin/ollama" ] || [[ "${FORCE_REINSTALL:-}" == "1" ]]; then
    info "Baixando Ollama binary (~50 MB)..."
    curl -fsSL "https://ollama.com/download/ollama-linux-amd64" \
         -o "$HOME/bin/ollama"
    chmod +x "$HOME/bin/ollama"
    ok "Ollama instalado em ~/bin/ollama"
else
    ok "Ollama já instalado: $("$HOME/bin/ollama" --version 2>&1 | head -1)"
fi

# Adicionar ao PATH no .bashrc e .bash_profile
for rc in ~/.bashrc ~/.bash_profile; do
    if ! grep -q 'export PATH="$HOME/bin:$PATH"' "$rc" 2>/dev/null; then
        echo 'export PATH="$HOME/bin:$PATH"' >> "$rc"
    fi
done

fi  # end skip-if-models-only

# ── 7. Iniciar servidor Ollama ────────────────────────────────────────────────
header "7. Servidor Ollama"
export PATH="$HOME/bin:$PATH"
export OLLAMA_MODELS="$OLLAMA_MODELS_DIR"
mkdir -p "$OLLAMA_MODELS_DIR"

# Verificar se já está rodando
if pgrep -x "ollama" &>/dev/null; then
    ok "Ollama já está rodando (PID $(pgrep -x ollama | head -1))"
else
    info "Iniciando Ollama em background..."
    nohup "$HOME/bin/ollama" serve \
        > "$LOGS_DIR/ollama.log" 2>&1 &
    OLLAMA_PID=$!
    echo "$OLLAMA_PID" > "$SCRATCH_DIR/.ollama.pid"
    info "Aguardando inicialização (10s)..."
    sleep 10
    if kill -0 "$OLLAMA_PID" 2>/dev/null; then
        ok "Ollama iniciado (PID $OLLAMA_PID)"
    else
        fail "Ollama falhou. Verificar log: $LOGS_DIR/ollama.log"
        cat "$LOGS_DIR/ollama.log" | tail -20
        exit 1
    fi
fi

# Verificar saúde
if curl -sf http://localhost:11434 &>/dev/null; then
    ok "Ollama API respondendo em http://localhost:11434"
else
    warn "API não responde ainda. Aguardando mais 15s..."
    sleep 15
    curl -sf http://localhost:11434 && ok "Ollama OK" || { fail "Ollama não responde"; exit 1; }
fi

# ── 8. Baixar modelos 14B ──────────────────────────────────────────────────────
header "8. Download dos modelos (3 × ~14-16B via ollama)"
echo ""
echo "  Modelos a baixar:"
echo "    1. qwen2.5-coder:14b      (~8.9 GB Q4_K_M)"
echo "    2. deepseek-coder-v2:16b  (~9.1 GB Q4_K_M)"
echo "    3. starcoder2:15b         (~9.1 GB Q4_K_M)"
echo "  Total: ~27 GB de disco"
echo ""

for model in "qwen2.5-coder:14b" "deepseek-coder-v2:16b" "starcoder2:15b"; do
    info "Baixando $model ..."
    if "$HOME/bin/ollama" pull "$model" 2>&1; then
        ok "$model pronto"
    else
        fail "Falha ao baixar $model. Verificar conexão e tentar manualmente:"
        echo "    ollama pull $model"
    fi
    echo ""
done

# ── Teste rápido ───────────────────────────────────────────────────────────────
header "9. Teste de sanidade"
info "Testando inferência com qwen2.5-coder:14b..."
RESPONSE=$("$HOME/bin/ollama" run qwen2.5-coder:14b \
    "Reply with one word only: what JSX attribute fixes missing alt text on an img?" \
    --nowordwrap 2>/dev/null | tr -d '\n' | head -c 100)
echo "  Resposta: $RESPONSE"
if echo "$RESPONSE" | grep -qi "alt"; then
    ok "Modelo respondeu corretamente"
else
    warn "Resposta não continha 'alt', mas modelo respondeu. Verificar manualmente."
fi

# ── Resumo ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════${R}"
echo -e "${GREEN}${BOLD}  ✔ Setup concluído!${R}"
echo ""
echo "  Para verificar GPU e modelos:"
echo "    nvidia-smi"
echo "    ollama list"
echo ""
echo "  Para iniciar o experimento:"
echo "    cd $PROJECT_DIR"
echo "    source .venv/bin/activate"
echo "    a11y-autofix experiment experiments/experiment_14b_comparison.yaml"
echo ""
echo "  Para monitorar:"
echo "    bash scripts/monitor_experiment.sh"
echo -e "${BOLD}════════════════════════════════════════════════════════${R}"
