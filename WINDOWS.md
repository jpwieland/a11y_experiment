# Guia de Instalação e Uso no Windows

Este guia cobre a configuração completa do **a11y-autofix** no Windows 10/11,
incluindo aceleração via GPU NVIDIA (CUDA) e uso de GPU AMD via WSL2.

---

## Índice

1. [Requisitos de sistema](#1-requisitos-de-sistema)
2. [Instalação das dependências base](#2-instalação-das-dependências-base)
3. [Configurar política do PowerShell](#3-configurar-política-do-powershell)
4. [Executar o setup automático](#4-executar-o-setup-automático)
5. [Configurar GPU NVIDIA (CUDA)](#5-configurar-gpu-nvidia-cuda)
6. [Configurar GPU AMD via WSL2](#6-configurar-gpu-amd-via-wsl2)
7. [Instalar e configurar o Ollama](#7-instalar-e-configurar-o-ollama)
8. [Verificar e testar a instalação](#8-verificar-e-testar-a-instalação)
9. [Uso diário no Windows](#9-uso-diário-no-windows)
10. [Scripts PowerShell disponíveis](#10-scripts-powershell-disponíveis)
11. [Equivalência de comandos bash → PowerShell](#11-equivalência-de-comandos-bash--powershell)
12. [Resolução de problemas](#12-resolução-de-problemas)
13. [GPU: guia detalhado de performance](#13-gpu-guia-detalhado-de-performance)

---

## 1. Requisitos de sistema

| Componente | Mínimo | Recomendado |
|---|---|---|
| Windows | 10 (64-bit, build 19041+) | 11 (build 22621+) |
| RAM | 16 GB | 32 GB |
| Disco | 40 GB livres | 100 GB livres (SSD) |
| GPU NVIDIA | GTX 1060 6 GB | RTX 3060 12 GB+ |
| CUDA | 11.8+ | 12.x |
| Python | 3.10 | 3.12 |
| Node.js | 18 LTS | 20 LTS |
| PowerShell | 5.1 | 7.4+ |

> **Sem GPU:** o projeto roda em CPU, mas inferência LLM será muito mais lenta
> (10–60 minutos por tarefa vs. 1–3 minutos com GPU).

---

## 2. Instalação das dependências base

### 2.1 Python 3.12

```powershell
# Opção A: via winget (recomendado)
winget install Python.Python.3.12

# Opção B: via Microsoft Store
# Abra a Store e busque "Python 3.12"

# Opção C: instalador oficial
# https://www.python.org/downloads/windows/
# IMPORTANTE: marque "Add Python to PATH" durante a instalação
```

Verificar:
```powershell
python --version
# deve retornar: Python 3.12.x
```

### 2.2 Node.js 20 LTS

```powershell
# Opção A: via winget
winget install OpenJS.NodeJS.LTS

# Opção B: instalador oficial
# https://nodejs.org/en/download/
```

Verificar:
```powershell
node --version   # v20.x.x
npm --version    # 10.x.x
```

### 2.3 Git

```powershell
winget install Git.Git
```

### 2.4 Visual C++ Build Tools (para alguns pacotes Python)

```powershell
winget install Microsoft.VisualStudio.2022.BuildTools
# OU instale o Visual Studio 2022 Community com workload "Desktop development with C++"
```

---

## 3. Configurar política do PowerShell

Por padrão o Windows bloqueia scripts `.ps1`. Execute **uma vez** como Administrador:

```powershell
# Abra PowerShell como Administrador e execute:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Confirme com `S` quando solicitado.

---

## 4. Executar o setup automático

```powershell
# Navegue até o diretório do projeto
cd C:\caminho\para\a11y_experiment

# Setup completo (detecta GPU automaticamente)
.\setup.ps1

# Opções disponíveis:
.\setup.ps1 -NoModels      # pula download dos modelos (mais rápido)
.\setup.ps1 -NoGpu         # força modo CPU
.\setup.ps1 -CI            # modo não-interativo (para automação)
```

O script executa 14 passos automaticamente:
1. Verifica Python ≥ 3.10
2. Cria `.venv\`
3. Instala dependências Python (`pip install -e .[dev]`)
4. Instala extras científicos (psutil, numpy, scipy)
5. Detecta GPU (NVIDIA via nvidia-smi)
6. Configura backend GPU
7. Instala Node.js tools (pa11y, axe-core, lighthouse)
8. Instala Playwright + Chromium
9. Cria/configura `.env`
10. Cria diretórios de trabalho
11. Configura Ollama
12. Baixa modelos recomendados
13. Preflight check de hardware
14. Resumo final

---

## 5. Configurar GPU NVIDIA (CUDA)

### 5.1 Instalar CUDA Toolkit

1. Acesse: https://developer.nvidia.com/cuda-downloads
2. Selecione: Windows → x86_64 → 11/10 → exe (local)
3. Instale o CUDA 12.x (recomendado) ou 11.8+

Verificar:
```powershell
nvidia-smi
# deve mostrar sua GPU e "CUDA Version: 12.x"

nvcc --version
# deve mostrar "release 12.x"
```

### 5.2 Instalar cuDNN (opcional, melhora performance)

1. Acesse: https://developer.nvidia.com/cudnn
2. Faça login com conta NVIDIA Developer
3. Baixe cuDNN para sua versão de CUDA
4. Copie os arquivos para `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.x\`

### 5.3 Configurar variáveis de ambiente para CUDA

Execute como Administrador:
```powershell
# Definir CUDA_VISIBLE_DEVICES
[System.Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES', '0', 'Machine')

# Para múltiplas GPUs, use: '0,1' ou '0'
# Para desabilitar GPU: 'NoDevFiles' ou '-1'
```

### 5.4 Verificar GPU no Ollama

```powershell
# Iniciar Ollama (se não estiver rodando)
ollama serve

# Em outro terminal, verificar status
ollama ps

# Baixar e testar um modelo
ollama pull qwen2.5-coder:7b
ollama run qwen2.5-coder:7b "Hello, are you using GPU?"
```

No output do `ollama serve` você deve ver algo como:
```
msg="inference compute" id=... library=cuda compute=8.6 driver=12.x name="NVIDIA GeForce RTX..."
```

### 5.5 Monitorar uso da GPU durante inferência

```powershell
# Em um terminal separado, monitorar GPU em tempo real
nvidia-smi dmon -s u -d 1

# Ou usar o Task Manager (Ctrl+Shift+Esc) → aba "Performance" → "GPU"
```

---

## 6. Configurar GPU AMD via WSL2

O vLLM e ROCm têm suporte limitado no Windows nativo. A solução recomendada é
usar **WSL2** (Windows Subsystem for Linux 2).

### 6.1 Instalar WSL2 com Ubuntu

```powershell
# Como Administrador:
wsl --install -d Ubuntu-22.04

# Reinicie o computador quando solicitado
# Na primeira inicialização, crie usuário/senha do Ubuntu
```

### 6.2 Instalar ROCm dentro do WSL2

```bash
# Dentro do terminal WSL2 (Ubuntu):

# Adicionar repositório ROCm
wget https://repo.radeon.com/amdgpu-install/6.1/ubuntu/jammy/amdgpu-install_6.1.60101-1_all.deb
sudo apt install ./amdgpu-install_6.1.60101-1_all.deb
sudo amdgpu-install --usecase=rocm

# Verificar
rocm-smi
```

### 6.3 Executar o projeto dentro do WSL2

```bash
# No terminal WSL2, navegar até o projeto
# (Windows C:\ é montado em /mnt/c/)
cd /mnt/c/caminho/para/a11y_experiment

# Executar setup Linux normalmente
bash setup.sh
```

### 6.4 Acessar serviços WSL2 do Windows

O Ollama rodando no WSL2 pode ser acessado do Windows em `http://localhost:11434`.
Configure no `.env`:
```env
LLM_BASE_URL=http://localhost:11434
```

---

## 7. Instalar e configurar o Ollama

### 7.1 Instalação

```powershell
# Via winget (recomendado)
winget install Ollama.Ollama

# Ou baixe o instalador em: https://ollama.com/download/windows
```

### 7.2 Iniciar o Ollama

O Ollama no Windows pode ser iniciado de três formas:

```powershell
# Opção A: aplicativo na bandeja do sistema
# Procure "Ollama" no menu Iniciar e execute

# Opção B: linha de comando
ollama serve

# Opção C: como serviço do Windows (se instalado via instalador)
# Ele inicia automaticamente com o Windows
```

### 7.3 Configurar GPU no Ollama (Windows)

Diferente do Linux/macOS, o Ollama no Windows lê variáveis de ambiente do **sistema**.
Execute como Administrador:

```powershell
# Para GPU NVIDIA:
[System.Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES', '0', 'Machine')
[System.Environment]::SetEnvironmentVariable('OLLAMA_GPU_OVERHEAD', '268435456', 'Machine')
[System.Environment]::SetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS', '1', 'Machine')

# Reiniciar o serviço Ollama para aplicar
Stop-Process -Name "ollama" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
```

### 7.4 Baixar modelos recomendados

```powershell
# Ativar venv primeiro
.\.venv\Scripts\Activate.ps1

# Modelo mínimo (CPU ou GPU com 6+ GB VRAM)
ollama pull qwen2.5-coder:7b

# Modelo médio (requer 12+ GB VRAM)
ollama pull qwen2.5-coder:14b

# Modelo avançado (requer 20+ GB VRAM)
ollama pull deepseek-coder-v2:16b

# Verificar modelos disponíveis
ollama list
```

### 7.5 Testar conectividade

```powershell
# Testar se Ollama responde
Invoke-WebRequest -Uri "http://localhost:11434/" -UseBasicParsing

# Via CLI do projeto
a11y-autofix models test qwen2.5-coder-7b
```

---

## 8. Verificar e testar a instalação

### 8.1 Ativar o ambiente virtual

```powershell
# Sempre que abrir um novo terminal:
.\.venv\Scripts\Activate.ps1

# Verificar que o venv está ativo (prompt muda para "(a11y-autofix)")
```

### 8.2 Verificar hardware

```powershell
a11y-autofix hardware
```

Saída esperada:
```
Hardware Preflight Check
  Python      3.12.x    OK
  GPU         NVIDIA RTX 3060 (12 GB)   OK
  Ollama      rodando (CUDA)            OK
  pa11y       6.x.x                     OK
  axe-core    4.x.x                     OK
  playwright  Chromium disponível       OK
```

### 8.3 Verificar scanners

```powershell
.\fix_scanners.ps1 -CheckOnly
```

### 8.4 Scan de teste (sem modificar arquivos)

```powershell
# Scan em arquivo de exemplo
a11y-autofix fix .\tests\fixtures\sample_components --dry-run

# Listar modelos disponíveis
a11y-autofix models list
```

---

## 9. Uso diário no Windows

### 9.1 Fluxo básico

```powershell
# 1. Abrir terminal PowerShell
# 2. Navegar até o projeto
cd C:\caminho\para\a11y_experiment

# 3. Ativar venv
.\.venv\Scripts\Activate.ps1

# 4. Garantir que Ollama está rodando
# (abrir aplicativo Ollama na bandeja OU:)
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
Start-Sleep -Seconds 3

# 5. Executar comandos
a11y-autofix fix .\src --model qwen2.5-coder-7b
```

### 9.2 Executar experimentos

```powershell
# Experimento comparativo
a11y-autofix experiment run .\experiments\qwen_vs_deepseek.yaml

# Com verbose
a11y-autofix experiment run .\experiments\all_models_comparison.yaml --verbose
```

### 9.3 Coleta do dataset

```powershell
# Definir token do GitHub
$env:GITHUB_TOKEN = "ghp_seu_token_aqui"

# Pipeline completo
.\collect.ps1

# Apenas uma fase
.\collect.ps1 -Phase scan

# A partir de uma fase
.\collect.ps1 -From scan

# Verificar estado
.\collect.ps1 -Status
```

### 9.4 Reparar scanners

```powershell
# Diagnóstico sem instalar
.\fix_scanners.ps1 -CheckOnly

# Instalar e reparar tudo
.\fix_scanners.ps1
```

---

## 10. Scripts PowerShell disponíveis

| Script | Equivalente bash | Descrição |
|---|---|---|
| `setup.ps1` | `setup.sh` | Configuração completa do ambiente |
| `collect.ps1` | `collect.sh` | Pipeline de coleta do dataset |
| `fix_scanners.ps1` | `fix_scanners.sh` | Instala/repara ferramentas de scan |
| `reset_scan.ps1` | `reset_scan.sh` | Reset parcial (mantém snapshots) |
| `reset_all.ps1` | `reset_all.sh` | Reset completo do dataset |

### Parâmetros comuns

```powershell
# setup.ps1
.\setup.ps1 -NoModels          # pular download de modelos
.\setup.ps1 -NoGpu             # forçar CPU
.\setup.ps1 -CI                # modo não-interativo

# collect.ps1
.\collect.ps1 -Phase snapshot  # fase específica
.\collect.ps1 -From scan       # a partir de uma fase
.\collect.ps1 -Workers 4       # número de workers
.\collect.ps1 -DryRun          # simular sem executar
.\collect.ps1 -Status          # mostrar estado atual

# fix_scanners.ps1
.\fix_scanners.ps1 -CheckOnly  # só diagnóstico

# reset_scan.ps1
.\reset_scan.ps1 -DryRun       # simular
.\reset_scan.ps1 -Yes          # sem confirmação
.\reset_scan.ps1 -AndScan      # resetar e re-escanear

# reset_all.ps1
.\reset_all.ps1 -DryRun        # simular
```

---

## 11. Equivalência de comandos bash → PowerShell

| bash (Linux/macOS) | PowerShell (Windows) |
|---|---|
| `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| `export VAR=value` | `$env:VAR = "value"` |
| `echo $VAR` | `Write-Host $env:VAR` ou `$env:VAR` |
| `which python` | `Get-Command python` |
| `ls -la` | `Get-ChildItem` ou `dir` |
| `rm -rf dir/` | `Remove-Item -Recurse -Force dir\` |
| `mkdir -p dir/sub/` | `New-Item -ItemType Directory -Path "dir\sub" -Force` |
| `cat file.txt` | `Get-Content file.txt` |
| `grep pattern file` | `Select-String pattern file` |
| `bash script.sh` | `.\script.ps1` |
| `./script.sh arg` | `.\script.ps1 -Arg value` |
| `python3 script.py` | `python script.py` |
| `pip install pkg` | `pip install pkg` (igual) |
| `ollama serve &` | `Start-Process ollama -Args "serve" -WindowStyle Hidden` |

---

## 12. Resolução de problemas

### "A execução de scripts foi desabilitada"

```powershell
# Solução: configurar política de execução
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### "'python' não é reconhecido como comando"

```powershell
# Verificar se Python está no PATH
$env:PATH -split ";" | Where-Object { $_ -match "python" }

# Adicionar ao PATH da sessão
$env:PATH = "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312;$env:PATH"

# Para persistir, adicione ao PATH do sistema via:
# Painel de Controle > Sistema > Configurações avançadas > Variáveis de ambiente
```

### "'npm' não é reconhecido"

```powershell
# Após instalar Node.js, reiniciar o terminal
# Se ainda não funcionar:
$env:PATH = "C:\Program Files\nodejs;$env:PATH"
```

### "Ollama não encontrou GPU"

```powershell
# 1. Verificar que nvidia-smi funciona
nvidia-smi

# 2. Verificar variáveis de ambiente
[System.Environment]::GetEnvironmentVariable('CUDA_VISIBLE_DEVICES', 'Machine')

# 3. Reiniciar Ollama após definir variáveis
Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
ollama serve

# 4. Verificar no log do Ollama
# Clique com botão direito no ícone do Ollama na bandeja > "View Logs"
```

### "pa11y não encontrado após instalação"

```powershell
# Verificar npm prefix
npm config get prefix
# Geralmente: C:\Users\<usuario>\AppData\Roaming\npm

# Adicionar ao PATH da sessão
$npmPath = npm config get prefix
$env:PATH = "$npmPath;$env:PATH"

# Verificar se pa11y está lá
Test-Path "$npmPath\pa11y.cmd"
```

### "Playwright não consegue abrir Chromium"

```powershell
# Reinstalar browsers do Playwright
python -m playwright install chromium --with-deps

# Se ainda falhar, instalar dependências manualmente
python -m playwright install-deps chromium
```

### "Erro de encoding UTF-8 no PowerShell"

```powershell
# Configurar encoding no perfil do PowerShell
# Adicione ao $PROFILE:
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Verificar/criar o perfil:
if (-not (Test-Path $PROFILE)) { New-Item -Path $PROFILE -Force }
notepad $PROFILE
```

### "Timeout durante download de modelos"

```powershell
# Aumentar timeout do Ollama
$env:OLLAMA_REQUEST_TIMEOUT = "300"

# Ou baixar manualmente com progresso visível
ollama pull qwen2.5-coder:7b
```

---

## 13. GPU: guia detalhado de performance

### 13.1 Monitorar uso de GPU em tempo real

```powershell
# nvidia-smi com atualização a cada 1 segundo
nvidia-smi dmon -s mu -d 1

# Ou usar GPU-Z (interface gráfica): https://www.techpowerup.com/gpuz/
# Ou usar HWiNFO64: https://www.hwinfo.com/
```

### 13.2 Otimizar VRAM para diferentes modelos

| Modelo | VRAM necessária | Quantização recomendada |
|---|---|---|
| qwen2.5-coder:7b | 6–8 GB | Q4_K_M (padrão Ollama) |
| qwen2.5-coder:14b | 10–12 GB | Q4_K_M |
| deepseek-coder-v2:16b | 12–16 GB | Q4_K_M |
| qwen2.5-coder:32b | 20–24 GB | Q4_K_M |

Para forçar uma quantização específica:
```powershell
ollama pull qwen2.5-coder:14b-instruct-q4_K_M
```

### 13.3 Configurações avançadas de GPU no Ollama

Edite as variáveis de ambiente do sistema (como Administrador):

```powershell
# Overhead de VRAM reservado para o sistema (em bytes)
# Aumente se o Ollama crashar com OOM
[System.Environment]::SetEnvironmentVariable('OLLAMA_GPU_OVERHEAD', '536870912', 'Machine')  # 512 MB

# Número máximo de layers na GPU (-1 = todas)
# Reduza se tiver pouca VRAM
[System.Environment]::SetEnvironmentVariable('OLLAMA_NUM_GPU', '33', 'Machine')  # 33 layers na GPU

# Apenas 1 modelo carregado por vez (economiza VRAM)
[System.Environment]::SetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS', '1', 'Machine')

# Habilitar Flash Attention (reduz uso de VRAM ~30%)
[System.Environment]::SetEnvironmentVariable('OLLAMA_FLASH_ATTENTION', '1', 'Machine')

# Após alterar, reiniciar o Ollama
Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
```

### 13.4 Usar vLLM no WSL2 para modelos grandes (32B+)

Para modelos de 32B+, o vLLM no WSL2 oferece melhor throughput que o Ollama:

```bash
# Dentro do WSL2 com NVIDIA GPU:
pip install vllm

# Iniciar servidor vLLM
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-Coder-32B-Instruct \
    --dtype auto \
    --tensor-parallel-size 1 \
    --port 8000
```

Configure no `.env` do projeto (Windows):
```env
# Modelo via vLLM no WSL2
# O WSL2 expõe serviços em localhost do Windows automaticamente
VLLM_BASE_URL=http://localhost:8000/v1
```

E no `models.yaml`:
```yaml
qwen2.5-coder-32b:
  backend: vllm
  base_url: "http://localhost:8000/v1"
  model_id: "Qwen/Qwen2.5-Coder-32B-Instruct"
```

### 13.5 Benchmark de referência

Tempos típicos para processar 1 arquivo React com 10 issues de acessibilidade:

| Hardware | Modelo | Tempo/arquivo |
|---|---|---|
| CPU (Ryzen 9 5900X) | qwen2.5-coder:7b | ~8 min |
| RTX 3060 12GB | qwen2.5-coder:7b | ~45 seg |
| RTX 3060 12GB | qwen2.5-coder:14b | ~90 seg |
| RTX 4090 24GB | deepseek-coder-v2:16b | ~60 seg |
| RTX 4090 24GB | qwen2.5-coder:32b | ~2 min |

### 13.6 Multi-GPU (SLI / NVLink)

Para sistemas com múltiplas GPUs NVIDIA:

```powershell
# Usar todas as GPUs disponíveis
[System.Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES', '0,1', 'Machine')

# Para vLLM com tensor parallelism (dentro WSL2):
# python -m vllm.entrypoints.openai.api_server \
#     --model Qwen/Qwen2.5-Coder-32B-Instruct \
#     --tensor-parallel-size 2    # número de GPUs
```

---

## Referência rápida

```powershell
# Setup inicial (uma vez)
.\setup.ps1

# Ativar ambiente (toda vez que abrir terminal)
.\.venv\Scripts\Activate.ps1

# Verificar hardware
a11y-autofix hardware

# Scan de teste
a11y-autofix fix .\src --dry-run

# Experimento completo
a11y-autofix experiment run .\experiments\qwen_vs_deepseek.yaml

# Coleta de dataset
.\collect.ps1

# Reparar scanners
.\fix_scanners.ps1

# Reset parcial (mantém snapshots)
.\reset_scan.ps1

# Reset total
.\reset_all.ps1 -DryRun   # verificar primeiro
.\reset_all.ps1             # executar
```
