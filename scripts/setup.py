#!/usr/bin/env python3
"""
Setup automático do sistema a11y-autofix.

Instala todas as dependências necessárias e configura o ambiente.
Execute: python scripts/setup.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Executa comando com saída formatada."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=False, text=True, check=check)


def check(name: str, cmd: list[str]) -> tuple[bool, str]:
    """Verifica se um programa está instalado."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            version = result.stdout.strip() or result.stderr.strip()
            return True, version[:60]
        return False, "not working"
    except FileNotFoundError:
        return False, "not found"


def main() -> None:
    print("\n♿ a11y-autofix Setup\n" + "═" * 40)

    # 1. Verificar Python
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 10):
        print(f"✗ Python {py_version} detectado. Requer >= 3.10")
        sys.exit(1)
    print(f"✓ Python {py_version}")

    # 2. Instalar dependências Python
    print("\n[1/5] Instalando dependências Python...")
    run([sys.executable, "-m", "pip", "install", "-e", ".[dev]", "--quiet"])
    print("✓ Dependências Python instaladas")

    # 3. Verificar Node.js
    print("\n[2/5] Verificando Node.js...")
    ok, version = check("node", ["node", "--version"])
    if ok:
        print(f"✓ Node.js {version}")
    else:
        print("✗ Node.js não encontrado")
        print("  Instale em: https://nodejs.org")
        print("  (Necessário para pa11y, axe-core, lighthouse)")

    # 4. Instalar ferramentas de acessibilidade
    print("\n[3/5] Instalando ferramentas de acessibilidade...")

    if shutil.which("node"):
        tools = [
            ("pa11y", ["npm", "install", "-g", "pa11y"]),
            ("@axe-core/cli", ["npm", "install", "-g", "@axe-core/cli"]),
            ("lighthouse", ["npm", "install", "-g", "lighthouse"]),
        ]
        for tool_name, install_cmd in tools:
            ok, _ = check(tool_name, [tool_name.split("/")[-1], "--version"])
            if ok:
                print(f"✓ {tool_name} já instalado")
            else:
                print(f"  Instalando {tool_name}...")
                result = subprocess.run(install_cmd, capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    print(f"✓ {tool_name} instalado")
                else:
                    print(f"⚠ Falha ao instalar {tool_name}: {result.stderr[:100]}")
    else:
        print("⚠ Node.js não disponível — pulando ferramentas Node.js")

    # 5. Instalar Playwright + Chromium
    print("\n[4/5] Configurando Playwright...")
    try:
        run([sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            check=False)
        print("✓ Playwright Chromium configurado")
    except Exception as e:
        print(f"⚠ Playwright: {e}")

    # 6. Criar .env se não existir
    print("\n[5/5] Configurando .env...")
    env_path = Path(".env")
    env_example = Path(".env.example")
    if not env_path.exists():
        if env_example.exists():
            import shutil as sh
            sh.copy(env_example, env_path)
            print("✓ .env criado a partir de .env.example")
        else:
            env_path.write_text(
                "DEFAULT_MODEL=qwen2.5-coder-7b\n"
                "LOG_LEVEL=INFO\n",
                encoding="utf-8",
            )
            print("✓ .env criado com configurações padrão")
    else:
        print("✓ .env já existe")

    # Verificação final
    print("\n" + "═" * 40)
    print("Setup concluído!")
    print("\nPróximos passos:")
    print("  1. Instale um modelo LLM local:")
    print("     ollama pull qwen2.5-coder:7b")
    print("")
    print("  2. Verifique os modelos disponíveis:")
    print("     a11y-autofix models list")
    print("")
    print("  3. Teste um scan:")
    print("     a11y-autofix fix ./seu-projeto/src --dry-run")
    print("")
    print("  4. Execute correção completa:")
    print("     a11y-autofix fix ./seu-projeto/src --model qwen2.5-coder-7b")
    print("")
    print("  5. Execute um experimento:")
    print("     a11y-autofix experiment experiments/qwen_vs_deepseek.yaml")


if __name__ == "__main__":
    main()
