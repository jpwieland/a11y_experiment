"""
Corpus sintético de componentes React com violações WCAG injetadas.

Cada fixture contém:
  - violated.tsx  : componente com violações específicas
  - correct.tsx   : versão correta (gold standard)
  - metadata.yaml : metadados das violações injetadas

Uso metodológico (C2.1):
  Permite calcular recall real (sem depender de scanners como árbitro)
  e fix_accuracy real (comparando com o código correto esperado).
"""
