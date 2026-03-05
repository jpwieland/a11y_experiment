#!/usr/bin/env python3
"""
Script para baixar modelos recomendados via Ollama.

Execute: python scripts/download_models.py
Ou com filtro: python scripts/download_models.py --size 7b
"""

from __future__ import annotations

import argparse
import subprocess
import sys


RECOMMENDED_MODELS = [
    # (model_id, description, size_gb_approx)
    ("qwen2.5-coder:7b", "Qwen 2.5 Coder 7B — melhor equilíbrio qualidade/velocidade", 4.7),
    ("qwen2.5-coder:14b", "Qwen 2.5 Coder 14B — alta qualidade", 9.0),
    ("deepseek-coder-v2:16b", "DeepSeek Coder V2 16B — excelente para código", 10.5),
    ("codellama:7b-instruct", "CodeLlama 7B Instruct — baseline clássico", 3.8),
    ("llama3.1:8b-instruct-q4_K_M", "Llama 3.1 8B — propósito geral", 4.9),
]


def pull_model(model_id: str) -> bool:
    """Baixa um modelo via ollama pull."""
    print(f"\n  Baixando: {model_id}")
    result = subprocess.run(
        ["ollama", "pull", model_id],
        capture_output=False,
        text=True,
        check=False,
    )
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa modelos recomendados via Ollama")
    parser.add_argument("--size", help="Filtrar por tamanho: 7b, 14b, 16b")
    parser.add_argument("--dry-run", action="store_true", help="Apenas lista, não baixa")
    args = parser.parse_args()

    print("\n♿ a11y-autofix — Download de Modelos Recomendados\n" + "═" * 50)

    # Verificar ollama
    try:
        result = subprocess.run(["ollama", "--version"], capture_output=True, text=True, check=False)
        print(f"✓ Ollama: {result.stdout.strip()}\n")
    except FileNotFoundError:
        print("✗ Ollama não encontrado. Instale em: https://ollama.com")
        sys.exit(1)

    models_to_download = RECOMMENDED_MODELS
    if args.size:
        models_to_download = [(m, d, s) for m, d, s in RECOMMENDED_MODELS if args.size in m]

    print("Modelos a baixar:\n")
    for model_id, description, size_gb in models_to_download:
        print(f"  [{size_gb:.1f} GB] {model_id}")
        print(f"           {description}")

    total_gb = sum(s for _, _, s in models_to_download)
    print(f"\nTotal: ~{total_gb:.1f} GB")

    if args.dry_run:
        print("\n[dry-run] Nenhum download realizado.")
        return

    # Confirmação
    answer = input(f"\nBaixar {len(models_to_download)} modelo(s)? [s/N] ").strip().lower()
    if answer not in ("s", "sim", "y", "yes"):
        print("Cancelado.")
        return

    print("\nIniciando downloads...\n")
    success_count = 0
    for model_id, description, _ in models_to_download:
        ok = pull_model(model_id)
        if ok:
            print(f"  ✓ {model_id}")
            success_count += 1
        else:
            print(f"  ✗ {model_id} — falhou")

    print(f"\n{'═' * 50}")
    print(f"Concluído: {success_count}/{len(models_to_download)} modelos baixados")

    if success_count > 0:
        print("\nVerifique com: a11y-autofix models list")


if __name__ == "__main__":
    main()
