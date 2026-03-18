# ─── Base image: Python 3.11 slim + Node.js 20 LTS ─────────────────────────
FROM python:3.11-slim AS base

# Metadata
LABEL maintainer="a11y-autofix"
LABEL description="a11y-autofix — acessibilidade automatizada com LLM local"

# Variáveis de ambiente para comportamento determinístico
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    NODE_VERSION=20

# ─── Dependências do sistema ─────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Node.js setup
    curl \
    gnupg \
    ca-certificates \
    # Chromium (para pa11y, axe, lighthouse, playwright)
    chromium \
    chromium-driver \
    # Dependências do Chromium
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    # Git (para dataset snapshots)
    git \
    # Utilitários
    jq \
    && rm -rf /var/lib/apt/lists/*

# ─── Node.js 20 LTS ──────────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ─── Ferramentas de acessibilidade (Node) ────────────────────────────────────
RUN npm install -g \
    pa11y@8.0.0 \
    @axe-core/cli@4.9.1 \
    lighthouse@12.2.1 \
    eslint@8.57.0 \
    eslint-plugin-jsx-a11y@6.8.0 \
    && npm cache clean --force

# ─── Build stage: instalar dependências Python ───────────────────────────────
FROM base AS builder

WORKDIR /build

COPY pyproject.toml ./
COPY a11y_autofix/ ./a11y_autofix/

RUN pip install --upgrade pip setuptools wheel \
    && pip install -e ".[dev]"

# ─── Playwright: instalar browsers para testes ───────────────────────────────
RUN python -m playwright install chromium --with-deps 2>/dev/null || \
    python -m playwright install chromium

# ─── Runtime stage ───────────────────────────────────────────────────────────
FROM base AS runtime

# Copiar instalação Python do builder
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin/a11y-autofix /usr/local/bin/a11y-autofix
COPY --from=builder /usr/local/bin/pytest /usr/local/bin/pytest
COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright

WORKDIR /workspace

# Copiar código fonte
COPY . .

# Configurações de ambiente para modo container
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright \
    CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage --disable-gpu" \
    A11Y_CONTAINER=1 \
    MAX_CONCURRENT_SCANS=4 \
    MAX_CONCURRENT_AGENTS=2 \
    SCAN_TIMEOUT=90 \
    AGENT_TIMEOUT=300

# Usuário não-root para segurança
RUN groupadd -r a11y && useradd -r -g a11y -d /workspace a11y \
    && chown -R a11y:a11y /workspace \
    && mkdir -p /workspace/a11y-report /workspace/experiment-results \
    && chown -R a11y:a11y /workspace/a11y-report /workspace/experiment-results

# Playwright precisa de permissões específicas no modo não-root
RUN chmod -R 755 /root/.cache/ms-playwright 2>/dev/null || true

USER a11y

# Porta para servidor HTTP de harness (interno)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import a11y_autofix; print('ok')" || exit 1

# Entrypoint padrão
ENTRYPOINT ["python", "-m", "a11y_autofix.cli"]
CMD ["--help"]
