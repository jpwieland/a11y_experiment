#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  setup_and_run.sh — setup completo + execução do experimento a11y-autofix
#
#  Instala todas as dependências, valida o ambiente passo a passo e executa
#  o pipeline experimental. Informe problemas bloqueantes antes de prosseguir.
#
#  Uso:
#    bash setup_and_run.sh               # setup completo + experimento
#    bash setup_and_run.sh --only-setup  # só instalar, sem rodar o experimento
#    bash setup_and_run.sh --only-check  # só checar o ambiente, sem instalar
#    bash setup_and_run.sh --quick       # smoke test (1 modelo, 5 arq/projeto)
#    bash setup_and_run.sh --verbose     # mostra cada comando e sua saída completa
#    bash setup_and_run.sh --help
#
#  Compatível com: Ubuntu 20.04 / 22.04 / 24.04 (x86-64)
# ═══════════════════════════════════════════════════════════════════════════════

set -uo pipefail

# ── Cores e helpers ────────────────────────────────────────────────────────────
B=$'\033[1m'; R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'
C=$'\033[36m'; M=$'\033[35m'; N=$'\033[0m'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
SETUP_LOG="$LOG_DIR/setup_$(date +%Y%m%d_%H%M%S).log"

# Captura tudo no log mantendo saída na tela
exec > >(tee -a "$SETUP_LOG") 2>&1

sep()    { echo "${C}${B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"; }
title()  { sep; echo "${B}${C}  $1${N}"; sep; }
ok()     { echo "  ${G}${B}✓${N}  $1"; }
warn()   { echo "  ${Y}${B}⚠${N}  $1"; }
info()   { echo "  ${C}ℹ${N}  $1"; }
fail()   { echo "  ${R}${B}✗${N}  $1"; }
step()   { echo ""; echo "${B}▶ $1${N}"; }
die()    {
  echo ""
  echo "${R}${B}╔══════════════════════════════════════════════════════════════╗${N}"
  echo "${R}${B}║  ABORTADO                                                    ║${N}"
  echo "${R}${B}╚══════════════════════════════════════════════════════════════╝${N}"
  echo "  $1"
  echo ""
  echo "  Log completo: ${B}$SETUP_LOG${N}"
  exit 1
}

elapsed() { local s=$(( $(date +%s) - $1 )); printf "%dm%02ds" $((s/60)) $((s%60)); }

# Abre uma aba/janela de terminal para monitoramento (gnome-terminal → x-terminal-emulator → xterm).
# Em sessões sem ambiente gráfico (SSH/headless), apenas imprime o comando para rodar manualmente.
# Dedup: se um processo casando com o padrão $2 já roda, não abre aba duplicada.
# Desativável com NO_TABS=1.
open_term() {
  local term_title="$1" dedup_pattern="$2"; shift 2
  local cmd="$*"
  if [[ "${NO_TABS:-0}" == "1" ]]; then
    return 0
  fi
  if [[ -n "$dedup_pattern" ]] && pgrep -f "$dedup_pattern" &>/dev/null; then
    info "Monitor '$term_title' já aberto — reutilizando aba existente"
    return 0
  fi
  if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    info "Sem ambiente gráfico — para acompanhar '$term_title', rode em outro terminal:"
    info "  $cmd"
    return 0
  fi
  if command -v gnome-terminal &>/dev/null; then
    gnome-terminal --tab --title="$term_title" -- bash -c "$cmd; exec bash" &>/dev/null \
      && { info "Aba aberta: $term_title"; return 0; }
  fi
  if command -v x-terminal-emulator &>/dev/null; then
    x-terminal-emulator -T "$term_title" -e bash -c "$cmd; exec bash" &>/dev/null & disown
    info "Terminal aberto: $term_title"
    return 0
  fi
  if command -v xterm &>/dev/null; then
    xterm -T "$term_title" -e bash -c "$cmd; exec bash" &>/dev/null & disown
    info "Terminal aberto: $term_title"
    return 0
  fi
  info "Nenhum emulador de terminal encontrado — para acompanhar '$term_title', rode:"
  info "  $cmd"
}

# ── Flags ──────────────────────────────────────────────────────────────────────
ONLY_SETUP=0; ONLY_CHECK=0; QUICK=0; VERBOSE="${VERBOSE:-0}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only-setup) ONLY_SETUP=1 ;;
    --only-check) ONLY_CHECK=1 ;;
    --quick)      QUICK=1 ;;
    --verbose|-v) VERBOSE=1 ;;
    --help|-h)
      grep '^#' "$0" | head -16 | sed 's/^# \?//'
      exit 0 ;;
    *) echo "Flag desconhecida: $1"; exit 2 ;;
  esac
  shift
done

# Modo verboso: flags de silêncio viram vazias e cada comando é ecoado antes de rodar
if [[ $VERBOSE -eq 1 ]]; then
  APT_Q=""; PIP_Q=""
else
  APT_Q="-qq"; PIP_Q="-q"
fi

# vexec: executa um comando ecoando-o antes (apenas em modo verboso)
vexec() {
  if [[ $VERBOSE -eq 1 ]]; then
    echo "  ${M}\$ $*${N}"
  fi
  "$@"
}

T0_GLOBAL=$(date +%s)

echo ""
echo "${B}${C}╔══════════════════════════════════════════════════════════════════╗${N}"
echo "${B}${C}║   a11y-autofix — setup + execução experimental                  ║${N}"
echo "${B}${C}║   Ubuntu 20.04 / 22.04 / 24.04 (x86-64)                        ║${N}"
echo "${B}${C}╚══════════════════════════════════════════════════════════════════╝${N}"
echo "  Log: ${B}$SETUP_LOG${N}"
[[ $VERBOSE -eq 1 ]] && echo "  Modo: ${B}verboso${N} (comandos e saídas completas)"
echo ""

# ════════════════════════════════════════════════════════════════════════════════
# FASE 0 — validar sistema operacional
# ════════════════════════════════════════════════════════════════════════════════
title "Fase 0 — Verificando sistema operacional"

if [[ ! -f /etc/os-release ]]; then
  die "Não foi possível detectar o sistema operacional. Este script é para Ubuntu."
fi
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  warn "Sistema detectado: ${PRETTY_NAME:-$ID}. Este script foi testado no Ubuntu."
  warn "Pode funcionar em distros Debian-based, mas sem garantia."
  read -r -p "  Continuar mesmo assim? [s/N] " resp
  [[ "${resp,,}" == "s" ]] || die "Abortado pelo usuário."
fi
ok "Sistema: ${PRETTY_NAME:-Ubuntu}"

# Verificar arquitetura
ARCH=$(uname -m)
if [[ "$ARCH" != "x86_64" ]]; then
  die "Arquitetura $ARCH não suportada. Necessário x86_64 (amd64)."
fi
ok "Arquitetura: $ARCH"

# Verificar espaço em disco (mínimo 15 GB livres)
FREE_KB=$(df -k "$ROOT" | awk 'NR==2{print $4}')
FREE_GB=$(( FREE_KB / 1024 / 1024 ))
if [[ $FREE_KB -lt 15728640 ]]; then
  die "Espaço livre insuficiente: ${FREE_GB} GB disponíveis, mínimo 15 GB necessários."
fi
ok "Espaço livre: ${FREE_GB} GB"

# Verificar RAM (mínimo 8 GB)
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_MEM_GB=$(( TOTAL_MEM_KB / 1024 / 1024 ))
if [[ $TOTAL_MEM_KB -lt 8388608 ]]; then
  warn "RAM detectada: ${TOTAL_MEM_GB} GB. Recomendado: ≥ 16 GB. Pode haver OOM."
else
  ok "RAM: ${TOTAL_MEM_GB} GB"
fi

# Verificar se é root (não deve ser)
if [[ $EUID -eq 0 ]]; then
  warn "Rodando como root. Recomendado: usuário normal com sudo."
fi

# Verificar sudo
if ! sudo -n true 2>/dev/null; then
  info "Senha sudo pode ser solicitada durante o setup."
fi

# ════════════════════════════════════════════════════════════════════════════════
# FASE 1 — dependências de sistema (apt)
# ════════════════════════════════════════════════════════════════════════════════
title "Fase 1 — Dependências de sistema (apt)"

if [[ $ONLY_CHECK -eq 0 ]]; then
  step "Atualizando lista de pacotes (pode levar alguns minutos)..."
  [[ $VERBOSE -eq 0 ]] && info "Dica: use --verbose para ver a saída completa dos comandos"
  vexec sudo apt-get update $APT_Q || warn "apt update retornou erro — continuando."

  PKGS_NEEDED=()
  check_apt() {
    dpkg -s "$1" &>/dev/null || PKGS_NEEDED+=("$1")
  }

  check_apt git
  check_apt curl
  check_apt wget
  check_apt build-essential
  check_apt pciutils
  check_apt python3-pip
  check_apt python3-venv
  # Libs do Playwright/Chromium
  check_apt libnss3
  check_apt libatk1.0-0
  check_apt libatk-bridge2.0-0
  check_apt libcups2
  check_apt libdrm2
  check_apt libxkbcommon0
  check_apt libxcomposite1
  check_apt libxdamage1
  check_apt libxfixes3
  check_apt libxrandr2
  check_apt libgbm1
  check_apt libpango-1.0-0
  check_apt libcairo2

  # libasound2 foi renomeado para libasound2t64 no Ubuntu 24.04+
  # /etc/os-release já foi carregado na Fase 0 (. /etc/os-release)
  UBUNTU_VER="${VERSION_ID:-0}"
  UBUNTU_MAJOR="${UBUNTU_VER%%.*}"
  if [[ "$UBUNTU_MAJOR" -ge 24 ]]; then
    # Ubuntu 24.04+: pacote t64 (transição para 64-bit)
    if dpkg -s libasound2t64 &>/dev/null; then
      ok "libasound2t64 já instalado"
    else
      PKGS_NEEDED+=(libasound2t64)
    fi
  else
    check_apt libasound2
  fi

  if [[ ${#PKGS_NEEDED[@]} -gt 0 ]]; then
    info "Instalando: ${PKGS_NEEDED[*]}"
    vexec sudo apt-get install -y "${PKGS_NEEDED[@]}" || die "Falha ao instalar pacotes apt: ${PKGS_NEEDED[*]}"
    ok "Pacotes instalados: ${#PKGS_NEEDED[@]}"
  else
    ok "Todos os pacotes apt já presentes"
  fi
fi

# ── Verificar git ──────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
  fail "git não encontrado"
  die "Instale git: sudo apt-get install -y git"
fi
ok "git $(git --version | awk '{print $3}')"

# ════════════════════════════════════════════════════════════════════════════════
# FASE 2 — Python 3.11
# ════════════════════════════════════════════════════════════════════════════════
title "Fase 2 — Python 3.11"

# Detectar python3.11 ou python3 >= 3.11
PYTHON=""
for bin in python3.11 python3.12 python3; do
  if command -v "$bin" &>/dev/null; then
    VER=$("$bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
    MAJOR=${VER%%.*}; MINOR=${VER##*.}
    if [[ "$MAJOR" -eq 3 && "$MINOR" -ge 11 ]]; then
      PYTHON="$bin"; break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  if [[ $ONLY_CHECK -eq 1 ]]; then
    fail "Python 3.11+ não encontrado"
    die "Instale: sudo apt-get install -y python3.11 python3.11-venv python3.11-dev"
  fi

  step "Python 3.11 não encontrado — instalando via deadsnakes PPA..."
  vexec sudo apt-get install -y software-properties-common
  vexec sudo add-apt-repository -y ppa:deadsnakes/ppa
  vexec sudo apt-get update $APT_Q
  vexec sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3.11-distutils

  if ! command -v python3.11 &>/dev/null; then
    die "Falha ao instalar Python 3.11. Tente manualmente:
    sudo add-apt-repository ppa:deadsnakes/ppa
    sudo apt-get install python3.11 python3.11-venv"
  fi
  PYTHON=python3.11
fi

ok "Python: $PYTHON ($($PYTHON --version))"

# ════════════════════════════════════════════════════════════════════════════════
# FASE 3 — Node.js 18+
# ════════════════════════════════════════════════════════════════════════════════
title "Fase 3 — Node.js 18+"

NODE_OK=0
if command -v node &>/dev/null; then
  NODE_VER=$(node -e 'console.log(parseInt(process.versions.node))' 2>/dev/null)
  if [[ "$NODE_VER" -ge 18 ]]; then
    NODE_OK=1
    ok "Node.js: $(node --version)"
  else
    warn "Node.js $(node --version) encontrado, mas necessário >= 18"
  fi
fi

if [[ $NODE_OK -eq 0 ]]; then
  if [[ $ONLY_CHECK -eq 1 ]]; then
    fail "Node.js 18+ não encontrado"
    die "Instale Node.js 18+:
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs"
  fi

  step "Instalando Node.js 20 via NodeSource..."
  [[ $VERBOSE -eq 1 ]] && echo "  ${M}\$ curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -${N}"
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - \
    || die "Falha ao adicionar repositório NodeSource."
  vexec sudo apt-get install -y nodejs \
    || die "Falha ao instalar nodejs."
  ok "Node.js instalado: $(node --version)"
fi

# npm deve vir junto
if ! command -v npm &>/dev/null; then
  die "npm não encontrado após instalação do Node.js. Verifique a instalação."
fi
ok "npm: $(npm --version)"

# ════════════════════════════════════════════════════════════════════════════════
# FASE 4 — Ferramentas npm de scan de acessibilidade
# ════════════════════════════════════════════════════════════════════════════════
title "Fase 4 — Ferramentas npm (pa11y, axe, babel)"

NPM_ROOT=$(npm root -g 2>/dev/null || echo "")

install_npm_if_missing() {
  local pkg="$1" bin="${2:-}" label="${3:-$1}"
  local installed=0

  # Verificar por binário
  if [[ -n "$bin" ]] && command -v "$bin" &>/dev/null; then
    installed=1
  fi
  # Verificar por diretório no npm global
  if [[ -n "$NPM_ROOT" && -d "$NPM_ROOT/${pkg%%@*}" ]]; then
    installed=1
  fi

  if [[ $installed -eq 1 ]]; then
    ok "$label já instalado"
    return 0
  fi

  if [[ $ONLY_CHECK -eq 1 ]]; then
    fail "$label não encontrado"
    return 1
  fi

  step "Instalando $label..."
  vexec sudo env PATH="$PATH" npm install -g "$pkg" \
    || { fail "Falha ao instalar $label"; return 1; }
  ok "$label instalado"
}

NPM_FAILED=0
install_npm_if_missing "pa11y"          "pa11y"  "pa11y"          || NPM_FAILED=1
install_npm_if_missing "@axe-core/cli"  "axe"    "axe CLI"        || NPM_FAILED=1
install_npm_if_missing "axe-core"       ""       "axe-core"       || NPM_FAILED=1
install_npm_if_missing "@babel/parser"  ""       "@babel/parser"  || NPM_FAILED=1

# Re-ler NPM_ROOT após instalações
NPM_ROOT=$(npm root -g 2>/dev/null || echo "")

# Verificações detalhadas pós-instalação
step "Verificando ferramentas npm..."

if ! command -v pa11y &>/dev/null; then
  fail "pa11y não está no PATH"
  warn "Tente: sudo npm install -g pa11y"
  NPM_FAILED=1
else
  ok "pa11y: $(pa11y --version 2>/dev/null)"
fi

if ! command -v axe &>/dev/null; then
  fail "axe não está no PATH"
  warn "Tente: sudo npm install -g @axe-core/cli"
  NPM_FAILED=1
else
  ok "axe CLI: ok"
fi

if [[ -z "$NPM_ROOT" ]] || [[ ! -f "$NPM_ROOT/axe-core/axe.min.js" ]]; then
  fail "axe-core não encontrado em $NPM_ROOT/axe-core/axe.min.js"
  warn "Tente: sudo npm install -g axe-core"
  NPM_FAILED=1
else
  ok "axe-core: $NPM_ROOT/axe-core"
fi

if [[ -z "$NPM_ROOT" ]] || [[ ! -d "$NPM_ROOT/@babel/parser" ]]; then
  fail "@babel/parser não encontrado em $NPM_ROOT/@babel/parser"
  warn "Tente: sudo npm install -g @babel/parser"
  NPM_FAILED=1
else
  ok "@babel/parser: $NPM_ROOT/@babel/parser"
fi

if [[ $NPM_FAILED -eq 1 && $ONLY_CHECK -eq 1 ]]; then
  die "Ferramentas npm ausentes. Rode sem --only-check para instalar automaticamente."
fi

# ════════════════════════════════════════════════════════════════════════════════
# FASE 5 — GPU NVIDIA (drivers) + Ollama
# ════════════════════════════════════════════════════════════════════════════════
title "Fase 5 — GPU NVIDIA + Ollama (servidor de LLMs)"

# ── 5a. Detectar GPU física e garantir driver funcionando ─────────────────────
step "Verificando GPU NVIDIA..."

nvidia_smi_ok() { nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null; }

GPU_PRESENT=0
GPU_DETECTED=0
GPU_VRAM_GB=0
DRIVER_FIXED_NOW=0

# lspci enxerga o hardware mesmo sem driver instalado
if lspci 2>/dev/null | grep -qi nvidia; then
  GPU_PRESENT=1
  GPU_PCI=$(lspci 2>/dev/null | grep -iE 'vga|3d|display' | grep -i nvidia | head -1 | sed 's/^[^:]*[^:]: //')
  info "GPU NVIDIA no barramento PCI: ${GPU_PCI:-detectada}"
fi

if [[ $GPU_PRESENT -eq 1 ]] && ! nvidia_smi_ok; then
  if ! command -v nvidia-smi &>/dev/null; then
    # ── Driver ausente: instalar o recomendado pela Canonical ──
    warn "GPU presente, mas driver NVIDIA não instalado"
    if [[ $ONLY_CHECK -eq 1 ]]; then
      die "Instale o driver NVIDIA:
    sudo apt-get install -y ubuntu-drivers-common
    sudo ubuntu-drivers install
    sudo reboot"
    fi

    step "Instalando driver NVIDIA recomendado (ubuntu-drivers)..."
    vexec sudo apt-get install -y ubuntu-drivers-common \
      || die "Falha ao instalar ubuntu-drivers-common."
    info "Driver recomendado: $(ubuntu-drivers devices 2>/dev/null | awk '/recommended/{print $3}' | head -1)"
    vexec sudo ubuntu-drivers install \
      || vexec sudo ubuntu-drivers autoinstall \
      || die "Falha ao instalar o driver NVIDIA.
  Instale manualmente:
    ubuntu-drivers devices                    # listar drivers disponíveis
    sudo apt-get install -y nvidia-driver-XXX # versão 'recommended'
    sudo reboot"
    ok "Driver NVIDIA instalado"

    # Tentar carregar o módulo sem reboot
    sudo modprobe nvidia 2>/dev/null || true
    sleep 2
    if nvidia_smi_ok; then
      ok "Driver carregado sem necessidade de reboot"
      DRIVER_FIXED_NOW=1
    else
      echo ""
      warn "O driver foi instalado, mas o kernel precisa ser reiniciado para carregá-lo."
      read -r -t 60 -p "  Reinicializar agora? Após o boot, rode este script novamente. [s/N] " resp || resp="n"
      if [[ "${resp,,}" == "s" ]]; then
        info "Reinicializando..."
        sudo reboot
        exit 0
      fi
      die "Reinicie a máquina e execute este script novamente:
    sudo reboot
    bash setup_and_run.sh"
    fi

  else
    # ── Driver instalado, mas nvidia-smi não comunica com ele ──
    warn "nvidia-smi presente, mas falha ao comunicar com o driver"
    if [[ $ONLY_CHECK -eq 1 ]]; then
      die "Problema de comunicação com o driver NVIDIA. Tente:
    sudo modprobe nvidia     # carregar o módulo
    sudo reboot              # se persistir (resolve mismatch pós-upgrade)"
    fi

    step "Tentando carregar o módulo do kernel (modprobe nvidia)..."
    sudo modprobe nvidia 2>/dev/null || true
    sleep 2

    if nvidia_smi_ok; then
      ok "Módulo nvidia carregado — comunicação com o driver restabelecida"
      DRIVER_FIXED_NOW=1
    else
      # Diagnóstico para as causas mais comuns
      DIAG=""
      # 1. Mismatch kernel module × biblioteca (típico após apt upgrade sem reboot)
      if dmesg 2>/dev/null | grep -qi "nvidia.*mismatch" \
         || nvidia-smi 2>&1 | grep -qi "mismatch"; then
        DIAG="Versão do módulo do kernel difere da biblioteca instalada (atualização sem reboot)."
      fi
      # 2. Secure Boot bloqueando módulo não assinado
      if command -v mokutil &>/dev/null && mokutil --sb-state 2>/dev/null | grep -qi "enabled"; then
        DIAG="${DIAG}
    Secure Boot está ATIVO — pode estar bloqueando o módulo NVIDIA não assinado.
    Desative o Secure Boot na BIOS ou registre a chave MOK (mokutil --import)."
      fi
      die "Não foi possível restabelecer comunicação com o driver NVIDIA.
  ${DIAG:-Causa não identificada automaticamente.}
  Correção mais provável (resolve mismatch e módulo não carregado):
    sudo reboot
  Se persistir após reboot, reinstale o driver:
    sudo ubuntu-drivers install && sudo reboot
  Diagnóstico: dmesg | grep -i nvidia | tail -20"
    fi
  fi
fi

OLLAMA_OK=0
if command -v ollama &>/dev/null; then
  OLLAMA_OK=1
  ok "ollama binário: $(ollama --version 2>/dev/null | head -1)"
fi

if [[ $OLLAMA_OK -eq 0 ]]; then
  if [[ $ONLY_CHECK -eq 1 ]]; then
    fail "ollama não instalado"
    die "Instale com: curl -fsSL https://ollama.com/install.sh | sh"
  fi

  step "Instalando Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh \
    || die "Falha ao instalar Ollama. Tente manualmente:
    curl -fsSL https://ollama.com/install.sh | sh"
  ok "Ollama instalado"
fi

# Se o driver foi recuperado nesta sessão, o Ollama pode ter iniciado sem ver a GPU
if [[ $DRIVER_FIXED_NOW -eq 1 ]] && curl -s --max-time 3 http://localhost:11434/api/tags &>/dev/null; then
  step "Reiniciando Ollama para detectar a GPU recém-ativada..."
  if systemctl is-active ollama &>/dev/null 2>&1; then
    sudo systemctl restart ollama
  else
    pkill -f "ollama serve" 2>/dev/null || true
    sleep 2
    nohup ollama serve >> "$LOG_DIR/ollama.log" 2>&1 &
  fi
  sleep 5
fi

# Verificar se servidor está rodando
OLLAMA_RUNNING=0
if curl -s --max-time 5 http://localhost:11434/api/tags &>/dev/null; then
  OLLAMA_RUNNING=1
  ok "Servidor Ollama: rodando (http://localhost:11434)"
else
  warn "Servidor Ollama não está rodando"

  if [[ $ONLY_CHECK -eq 1 ]]; then
    die "Inicie o Ollama antes de continuar:
    ollama serve          # em terminal separado, OU
    systemctl start ollama  # se instalado como serviço"
  fi

  step "Iniciando Ollama em background..."
  # Tentar via systemctl primeiro
  if systemctl is-enabled ollama &>/dev/null 2>&1; then
    sudo systemctl start ollama
    sleep 5
  else
    # Iniciar manualmente
    nohup ollama serve >> "$LOG_DIR/ollama.log" 2>&1 &
    OLLAMA_BG_PID=$!
    info "Ollama iniciado (PID $OLLAMA_BG_PID) — log: $LOG_DIR/ollama.log"
    sleep 8
  fi

  # Verificar novamente
  if curl -s --max-time 10 http://localhost:11434/api/tags &>/dev/null; then
    OLLAMA_RUNNING=1
    ok "Servidor Ollama: rodando"
  else
    die "Não foi possível iniciar o servidor Ollama.
  Inicie manualmente em outro terminal:
    ollama serve
  Depois execute este script novamente."
  fi
fi

# Verificar GPU disponível para inferência
step "Verificando GPU para inferência..."
if nvidia_smi_ok; then
  GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)
  VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | awk '{printf "%.0f", $1}')
  GPU_VRAM_GB=$(( VRAM_MB / 1024 ))
  CUDA_VER=$(nvidia-smi 2>/dev/null | awk '/CUDA Version/{print $NF}' | head -1)
  ok "GPU: ${GPU_INFO}  |  VRAM: ${GPU_VRAM_GB} GB  |  CUDA: ${CUDA_VER}"
  GPU_DETECTED=1
  if [[ $GPU_VRAM_GB -lt 4 ]]; then
    warn "VRAM < 4 GB — modelos 7B podem não caber inteiramente na GPU"
  fi
  # Aba de monitoramento da GPU em tempo real (uma única, mesmo com re-runs)
  open_term "a11y — GPU Monitor" "watch -n 2 nvidia-smi" "watch -n 2 nvidia-smi"
else
  warn "GPU NVIDIA não detectada — Ollama usará CPU (experimento ~7× mais lento para modelos 7B)"
fi

# Verificar/baixar modelos
step "Verificando modelos necessários..."
MODELS_NEEDED=()
MODELS_AVAIL=$(curl -s http://localhost:11434/api/tags | python3 -c "
import json, sys
data = json.load(sys.stdin)
for m in data.get('models', []):
    print(m['name'])
" 2>/dev/null || echo "")

for MODEL in qwen2.5-coder:3b qwen2.5-coder:7b codellama:7b; do
  # Normalizar nome: qwen2.5-coder:3b pode aparecer como qwen2.5-coder:3b ou qwen2.5-coder-3b
  BASE="${MODEL%%:*}"
  TAG="${MODEL##*:}"
  if echo "$MODELS_AVAIL" | grep -qi "${BASE}"; then
    ok "Modelo disponível: $MODEL"
  else
    warn "Modelo ausente: $MODEL"
    MODELS_NEEDED+=("$MODEL")
  fi
done

if [[ ${#MODELS_NEEDED[@]} -gt 0 ]]; then
  if [[ $ONLY_CHECK -eq 1 ]]; then
    fail "${#MODELS_NEEDED[@]} modelo(s) ausente(s): ${MODELS_NEEDED[*]}"
    die "Baixe os modelos com:
$(for m in "${MODELS_NEEDED[@]}"; do echo "    ollama pull $m"; done)"
  fi

  echo ""
  echo "${Y}${B}  Modelos a baixar: ${MODELS_NEEDED[*]}${N}"
  echo "  Isso pode demorar 20–60 min dependendo da conexão (~2–4 GB cada)."
  echo ""

  for MODEL in "${MODELS_NEEDED[@]}"; do
    step "Baixando $MODEL..."
    T_MODEL=$(date +%s)
    ollama pull "$MODEL" || die "Falha ao baixar $MODEL.
  Verifique a conexão e tente novamente:
    ollama pull $MODEL"
    ok "$MODEL baixado em $(elapsed $T_MODEL)"
  done
fi

# Confirmar que Ollama usa GPU na inferência (carrega o modelo menor e consulta ollama ps)
if [[ $GPU_DETECTED -eq 1 ]]; then
  step "Confirmando inferência na GPU (qwen2.5-coder:3b)..."
  # Chamada bloqueante: garante que o modelo terminou de carregar antes de checar
  curl -s --max-time 180 http://localhost:11434/api/generate \
    -d '{"model":"qwen2.5-coder:3b","prompt":"ok","stream":false,"options":{"num_predict":1}}' \
    &>/dev/null || warn "Chamada de teste ao modelo falhou/expirou — verificando estado mesmo assim"

  PS_LINE=$(ollama ps 2>/dev/null | grep -i "qwen2.5-coder:3b" || true)
  PROC_INFO=$(echo "$PS_LINE" | grep -oE '[0-9]+%(/[0-9]+%)? *(CPU/GPU|GPU/CPU|GPU|CPU)' | head -1)

  if echo "$PS_LINE" | grep -q "100% GPU"; then
    ok "Inferência 100% na GPU confirmada (ollama ps: ${PROC_INFO})"
  elif echo "$PS_LINE" | grep -q "GPU"; then
    warn "Modelo parcialmente na GPU (ollama ps: ${PROC_INFO}) — VRAM insuficiente para offload total"
    warn "Modelos 7B ficarão mais lentos; o experimento ainda funciona."
  elif echo "$PS_LINE" | grep -q "CPU"; then
    die "GPU detectada, mas o Ollama está rodando o modelo em CPU (ollama ps: ${PROC_INFO}).
  Possíveis causas e correções:
    1. Driver NVIDIA sem suporte CUDA ativo  → nvidia-smi deve listar processos
    2. Ollama iniciado antes do driver       → sudo systemctl restart ollama
    3. Ollama sem suporte CUDA               → curl -fsSL https://ollama.com/install.sh | sh
  Diagnóstico: journalctl -u ollama --no-pager | grep -iE 'cuda|gpu' | tail -20"
  else
    warn "Não foi possível confirmar via 'ollama ps' (modelo já descarregado?)"
    warn "Verifique manualmente: ollama run qwen2.5-coder:3b 'teste' & ollama ps"
  fi
fi

# ════════════════════════════════════════════════════════════════════════════════
# FASE 6 — Ambiente Python (venv + pacote)
# ════════════════════════════════════════════════════════════════════════════════
title "Fase 6 — Ambiente Python (virtualenv)"

VENV="$ROOT/.venv"

if [[ ! -d "$VENV" ]]; then
  if [[ $ONLY_CHECK -eq 1 ]]; then
    fail "Virtualenv não encontrado em $VENV"
    die "Crie com: $PYTHON -m venv $VENV && source $VENV/bin/activate && pip install -e $ROOT"
  fi
  step "Criando virtualenv em $VENV..."
  $PYTHON -m venv "$VENV" \
    || die "Falha ao criar virtualenv.
  Verifique se python3-venv está instalado:
    sudo apt-get install -y python3.11-venv"
  ok "Virtualenv criado"
fi

# Ativar venv
source "$VENV/bin/activate"
ok "Virtualenv ativo: $VIRTUAL_ENV"

PYBIN="$VENV/bin/python"

# pip atualizado
if [[ $ONLY_CHECK -eq 0 ]]; then
  step "Atualizando pip..."
  vexec "$PYBIN" -m pip install --upgrade pip $PIP_Q \
    || warn "Falha ao atualizar pip — continuando."
fi

# Instalar o pacote
A11Y_OK=0
if "$PYBIN" -c "import a11y_autofix" &>/dev/null 2>&1; then
  A11Y_OK=1
  ok "Pacote a11y_autofix: instalado"
fi

if [[ $A11Y_OK -eq 0 ]]; then
  if [[ $ONLY_CHECK -eq 1 ]]; then
    fail "Pacote a11y_autofix não encontrado no venv"
    die "Instale com:
    source $VENV/bin/activate
    pip install -e $ROOT"
  fi

  step "Instalando a11y_autofix e dependências..."
  vexec "$PYBIN" -m pip install -e "$ROOT" \
    || die "Falha ao instalar o pacote.
  Verifique o pyproject.toml e tente:
    source $VENV/bin/activate
    pip install -e $ROOT"
  ok "Pacote instalado"
fi

# Verificar dependências críticas individualmente
step "Verificando dependências Python..."
DEPS_FAILED=0
check_py() {
  local pkg="$1" label="${2:-$1}" pip_pkg="${3:-$1}"
  if "$PYBIN" -c "import $pkg" &>/dev/null 2>&1; then
    ok "  $label"
  else
    fail "  $label — ausente"
    if [[ $ONLY_CHECK -eq 0 ]]; then
      vexec "$PYBIN" -m pip install "$pip_pkg" $PIP_Q && ok "  $label instalado" || { fail "  falha ao instalar $label"; DEPS_FAILED=1; }
    else
      DEPS_FAILED=1
    fi
  fi
}

check_py pydantic          "pydantic"
check_py pydantic_settings "pydantic-settings" "pydantic-settings"
check_py structlog         "structlog"
check_py yaml            "pyyaml"          "pyyaml"
check_py playwright      "playwright"
check_py openai          "openai"
check_py jinja2          "jinja2"
check_py typer           "typer"
check_py httpx           "httpx"
check_py aiohttp         "aiohttp"
check_py tenacity        "tenacity"
check_py orjson          "orjson"
check_py gitpython "gitpython (git)"

if [[ $DEPS_FAILED -eq 1 ]]; then
  die "Dependências Python ausentes. Tente:
  source $VENV/bin/activate
  pip install -e $ROOT"
fi

# ════════════════════════════════════════════════════════════════════════════════
# FASE 7 — Playwright + Chromium
# ════════════════════════════════════════════════════════════════════════════════
title "Fase 7 — Playwright + Chromium"

PLAYWRIGHT_OK=0
if "$PYBIN" -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.launch(headless=True)
b.close()
p.stop()
" &>/dev/null 2>&1; then
  PLAYWRIGHT_OK=1
  ok "Playwright + Chromium: funcionando"
fi

if [[ $PLAYWRIGHT_OK -eq 0 ]]; then
  if [[ $ONLY_CHECK -eq 1 ]]; then
    fail "Playwright/Chromium não funcionando"
    die "Instale browsers e dependências:
    source $VENV/bin/activate
    playwright install chromium
    playwright install-deps chromium"
  fi

  step "Instalando browser Chromium para Playwright..."
  vexec "$VENV/bin/playwright" install chromium \
    || die "Falha ao instalar Chromium.
  Tente manualmente:
    source $VENV/bin/activate
    playwright install chromium"

  step "Instalando dependências de sistema do Playwright..."
  vexec "$VENV/bin/playwright" install-deps chromium \
    || warn "playwright install-deps falhou — pode funcionar mesmo assim."

  # Verificar novamente
  if "$PYBIN" -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.launch(headless=True)
b.close()
p.stop()
" &>/dev/null 2>&1; then
    ok "Playwright + Chromium: funcionando"
  else
    fail "Playwright continua falhando após instalação"
    warn "Tente: playwright install-deps"
    warn "Ou instale manualmente as libs de sistema e rode novamente."
    # Não aborta — pode ser warning não-fatal em alguns ambientes
  fi
fi

# ════════════════════════════════════════════════════════════════════════════════
# FASE 8 — Sumário de verificação completo
# ════════════════════════════════════════════════════════════════════════════════
title "Fase 8 — Verificação final do ambiente"

ALL_OK=1
check_final() {
  local desc="$1"; shift
  if "$@" &>/dev/null 2>&1; then
    ok "$desc"
  else
    fail "$desc"
    ALL_OK=0
  fi
}

check_final "python 3.11+" \
  "$PYBIN" -c 'import sys; assert sys.version_info >= (3,11)'

check_final "pacote a11y_autofix" \
  "$PYBIN" -c 'import a11y_autofix'

check_final "pydantic (import + instalação)" \
  "$PYBIN" -c 'import pydantic; pydantic.BaseModel'

check_final "playwright (pacote python)" \
  "$PYBIN" -c 'import playwright'

check_final "pydantic + structlog + yaml" \
  "$PYBIN" -c 'import pydantic, structlog, yaml'

check_final "playwright + chromium" \
  "$PYBIN" -c '
from playwright.sync_api import sync_playwright
p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()'

check_final "node 18+" \
  node -e 'process.exit(parseInt(process.versions.node)>=18?0:1)'

check_final "pa11y no PATH" \
  sh -c 'command -v pa11y'

check_final "axe CLI no PATH" \
  sh -c 'command -v axe'

NPM_ROOT_NOW=$(npm root -g 2>/dev/null || echo "")
check_final "axe-core (npm global)" \
  test -f "$NPM_ROOT_NOW/axe-core/axe.min.js"

check_final "@babel/parser (npm global)" \
  test -d "$NPM_ROOT_NOW/@babel/parser"

check_final "ollama servidor" \
  curl -s --max-time 5 http://localhost:11434/api/tags

check_final "git" \
  sh -c 'command -v git'

check_final "disco ≥ 15 GB livres" \
  test "$FREE_KB" -gt 15728640

if command -v nvidia-smi &>/dev/null && nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null 2>&1; then
  ok "GPU NVIDIA detectada: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
else
  warn "GPU NVIDIA não detectada — inferência em CPU (~7× mais lento para modelos 7B)"
fi

# Modelos
for MODEL in qwen2.5-coder:3b qwen2.5-coder:7b codellama:7b; do
  BASE="${MODEL%%:*}"
  if curl -s http://localhost:11434/api/tags | grep -qi "${BASE}"; then
    ok "Modelo Ollama: $MODEL"
  else
    fail "Modelo Ollama ausente: $MODEL"
    warn "    Execute: ollama pull $MODEL"
    ALL_OK=0
  fi
done

echo ""
if [[ $ALL_OK -eq 0 ]]; then
  die "Ambiente incompleto — corrija os itens ✗ acima e execute o script novamente."
fi

echo "${G}${B}  ✓ Ambiente 100% verificado — $(elapsed $T0_GLOBAL) de setup${N}"

# ════════════════════════════════════════════════════════════════════════════════
# FASE 9 — Rodar o experimento
# ════════════════════════════════════════════════════════════════════════════════

if [[ $ONLY_SETUP -eq 1 || $ONLY_CHECK -eq 1 ]]; then
  echo ""
  echo "${G}${B}Setup concluído.${N} Para rodar o experimento:"
  echo "  source $VENV/bin/activate"
  echo "  ./run_full_experiment.sh --skip-discover"
  echo ""
  echo "  Log completo: ${B}$SETUP_LOG${N}"
  exit 0
fi

title "Fase 9 — Executando experimento"

# Garantir que o venv está ativo
source "$VENV/bin/activate"

# Modo quick: avisa sobre runtime
if [[ $QUICK -eq 1 ]]; then
  warn "MODO QUICK: 1 modelo × 1 repetição × 5 arq/projeto (~5 min)"
else
  echo ""
  echo "${Y}${B}  ATENÇÃO — Estimativa de tempo:${N}"
  echo "    qwen2.5-coder:3b  (3 reps) →   ~6 horas"
  echo "    qwen2.5-coder:7b  (3 reps) →  ~42 horas"
  echo "    codellama:7b      (3 reps) → ~110 horas"
  echo "    Scan dos projetos          →   ~4 horas"
  echo ""
  echo "  O experimento completo leva ~7 dias em GPU com 4 GB VRAM."
  echo "  Use Ctrl+C para pausar; re-execute com --skip-discover para retomar."
  echo ""
  read -r -t 15 -p "  Continuar? [S/n] " resp || resp="s"
  [[ "${resp,,}" == "n" ]] && { info "Abortado. Para rodar depois: ./run_full_experiment.sh --skip-discover"; exit 0; }
fi

RUN_ARGS=(--skip-discover)
[[ $QUICK -eq 1 ]] && RUN_ARGS+=(--quick)

echo ""
info "Chamando run_full_experiment.sh ${RUN_ARGS[*]}..."
echo ""

# Aba única de monitoramento: dashboard ao vivo (progresso por modelo/repetição, ETA).
# O loop reabre o dashboard se ele sair antes do experimento gerar dados.
open_term "a11y — Dashboard Experimento" "watch_experiment.py" \
  "cd '$ROOT' && sleep 15 && until '$VENV/bin/python' watch_experiment.py; do sleep 15; done"

# Inibir suspensão/idle do sistema durante o experimento (dias de execução em laptop):
# sem isso, tampa fechada ou idle do GNOME suspendem a máquina e matam o run.
INHIBIT=()
if command -v systemd-inhibit &>/dev/null; then
  INHIBIT=(systemd-inhibit --what=sleep:idle --who="a11y-autofix" --why="experimento em execução")
  info "Suspensão automática do sistema inibida durante o experimento (systemd-inhibit)"
fi

"${INHIBIT[@]}" bash "$ROOT/run_full_experiment.sh" "${RUN_ARGS[@]}"
RC=$?

echo ""
if [[ $RC -eq 0 ]]; then
  echo "${G}${B}╔══════════════════════════════════════════════════════════════╗${N}"
  echo "${G}${B}║  PIPELINE COMPLETO — $(elapsed $T0_GLOBAL) total             ${N}"
  echo "${G}${B}╚══════════════════════════════════════════════════════════════╝${N}"
  echo ""
  LATEST=$(ls -td "$ROOT/experiment-results/"*/ 2>/dev/null | head -1)
  if [[ -n "$LATEST" ]]; then
    echo "  Relatório: ${B}${LATEST}deep_report.html${N}"
    echo "  Abrir:     xdg-open ${LATEST}deep_report.html"
  fi
else
  die "run_full_experiment.sh falhou (rc=$RC).
  Para retomar de onde parou:
    source $VENV/bin/activate
    ./run_full_experiment.sh --skip-discover --from experiment"
fi

echo ""
echo "  Log completo: ${B}$SETUP_LOG${N}"
