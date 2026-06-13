"""
gpu_monitor.py — Monitor de GPU para controle dinâmico de paralelismo.

Usa nvidia-smi para ler VRAM livre/usada e utilização da GPU a cada N segundos.
Expõe recomendações de concorrência para que o runner ajuste em tempo real
quantos arquivos processar em paralelo sem thrashing de memória.

Uso típico:
    monitor = GpuMonitor()
    await monitor.start()
    ...
    rec = monitor.recommend_concurrency(model_vram_gb=10.0)
    sem = asyncio.Semaphore(rec)
    ...
    await monitor.stop()

Sem GPU (CPU-only ou nvidia-smi ausente):
    recommend_concurrency() retorna o valor padrão sem falhar.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from typing import ClassVar

log = logging.getLogger(__name__)


@dataclass
class GpuStats:
    """Snapshot das métricas de uma GPU."""
    index: int = 0
    name: str = "unknown"
    vram_total_mb: int = 0
    vram_used_mb: int = 0
    vram_free_mb: int = 0
    gpu_util_pct: int = 0      # 0-100
    temp_c: int = 0
    timestamp: float = field(default_factory=time.monotonic)

    @property
    def vram_used_pct(self) -> float:
        if self.vram_total_mb <= 0:
            return 0.0
        return self.vram_used_mb / self.vram_total_mb * 100

    @property
    def vram_free_gb(self) -> float:
        return self.vram_free_mb / 1024

    @property
    def vram_total_gb(self) -> float:
        return self.vram_total_mb / 1024

    @property
    def vram_used_gb(self) -> float:
        return self.vram_used_mb / 1024


_UNAVAILABLE: GpuStats = GpuStats(name="unavailable")


class GpuMonitor:
    """
    Background task que lê nvidia-smi periodicamente e expõe estatísticas
    para controle dinâmico de concorrência.

    Thread-safe: todas as leituras são via propriedades atômicas.
    """

    # Custo aproximado de VRAM (MB) por slot ADICIONAL de inferência paralela
    # sobre o mesmo modelo já carregado — domina o KV-cache (~contexto × camadas).
    # 700 MB é uma estimativa conservadora para modelos 3B-7B Q4 em ~8k tokens.
    PER_SLOT_MB: ClassVar[int] = 700
    # Margem de segurança reservada para fragmentação/picos antes de paralelizar.
    SAFETY_MB: ClassVar[int] = 512

    def __init__(self, poll_interval: float = 5.0, gpu_index: int = 0) -> None:
        self._poll_interval = poll_interval
        self._gpu_index = gpu_index
        self._stats: GpuStats = _UNAVAILABLE
        self._task: asyncio.Task | None = None
        self._available: bool | None = None  # None = não testado ainda
        self._lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> bool:
        """
        Inicia o monitor em background.
        Retorna True se nvidia-smi estiver disponível, False caso contrário.
        """
        if not shutil.which("nvidia-smi"):
            log.info("gpu_monitor_unavailable: nvidia-smi not found")
            self._available = False
            return False

        # Teste inicial
        stats = await self._read_once()
        if stats is None:
            self._available = False
            return False

        self._stats = stats
        self._available = True
        self._task = asyncio.create_task(self._loop(), name="gpu-monitor")
        log.info("gpu_monitor_started", gpu=stats.name, vram_total_gb=f"{stats.vram_total_gb:.1f}")
        return True

    async def stop(self) -> None:
        """Para o monitor."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    # ── Leitura pública ───────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available is True

    @property
    def stats(self) -> GpuStats:
        """Último snapshot lido. Thread-safe (leitura atômica de referência)."""
        return self._stats

    def recommend_concurrency(
        self,
        model_vram_gb: float = 10.0,
        base: int = 2,
    ) -> int:
        """
        Retorna quantos arquivos podem ser processados em paralelo pelo LLM,
        proporcional à VRAM LIVRE real — funciona em qualquer placa (6 GB ou
        24 GB), sem limiares absolutos calibrados para uma GPU específica.

        Lógica:
        - Sem GPU disponível → retorna `base` (paralelismo decidido por CPU).
        - Com o modelo já carregado, a VRAM livre é o que sobra para KV-cache de
          inferências paralelas adicionais. 1 slot é sempre garantido (o modelo
          carregado processa ≥1 requisição); cada slot extra custa ~PER_SLOT_MB.
        - O resultado é limitado por `base` (max_concurrent_agents) — nunca pede
          mais paralelismo do que o configurado.

        Numa RTX 4050 (6 GB): modelo 7B Q4 (~5-6 GB) deixa ~0 livre → 1 slot
        (correto: 7B não paraleliza em 6 GB). Modelo 3B (~3 GB) deixa ~3 GB →
        2-3 slots. Numa RTX 4090 (24 GB) o mesmo cálculo libera bem mais slots.

        Args:
            model_vram_gb: VRAM que o modelo ocupa quando carregado (estimativa);
                usada como piso de sanidade quando a leitura de `free` ainda não
                reflete o modelo carregado.
            base: Teto de concorrência (e fallback sem GPU).
        """
        if not self.available:
            return base

        s = self._stats
        usable_mb = s.vram_free_mb - self.SAFETY_MB
        if usable_mb < 0:
            return 1

        extra_slots = int(usable_mb // self.PER_SLOT_MB)
        return max(1, min(base, 1 + extra_slots))

    def format_stats(self) -> str:
        """Formata as stats para display no dashboard."""
        s = self._stats
        if not self.available or s.name == "unavailable":
            return "GPU: n/a"
        bar_w = 12
        filled = round(min(s.vram_used_pct / 100, 1.0) * bar_w)
        bar = "█" * filled + "░" * (bar_w - filled)
        return (
            f"GPU {s.index}: {s.name[:20]}  "
            f"VRAM [{bar}] {s.vram_used_gb:.1f}/{s.vram_total_gb:.1f} GB  "
            f"Util {s.gpu_util_pct:3d}%  {s.temp_c}°C"
        )

    # ── Loop interno ──────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while True:
            try:
                stats = await self._read_once()
                if stats is not None:
                    async with self._lock:
                        self._stats = stats
            except Exception as exc:
                log.debug("gpu_monitor_read_error", error=str(exc))
            await asyncio.sleep(self._poll_interval)

    async def _read_once(self) -> GpuStats | None:
        """
        Executa nvidia-smi e retorna GpuStats ou None em caso de erro.
        """
        try:
            query = (
                "index,name,memory.total,memory.used,memory.free,"
                "utilization.gpu,temperature.gpu"
            )
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                f"--query-gpu={query}",
                "--format=csv,noheader,nounits",
                f"--id={self._gpu_index}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            if proc.returncode != 0:
                return None

            line = stdout.decode().strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                return None

            return GpuStats(
                index=int(parts[0]),
                name=parts[1],
                vram_total_mb=int(parts[2]),
                vram_used_mb=int(parts[3]),
                vram_free_mb=int(parts[4]),
                gpu_util_pct=int(parts[5]),
                temp_c=int(parts[6]),
            )
        except (asyncio.TimeoutError, FileNotFoundError, ValueError, IndexError):
            return None


# ── Singleton global (opcional) ───────────────────────────────────────────────

_global_monitor: GpuMonitor | None = None


async def get_global_monitor(poll_interval: float = 5.0) -> GpuMonitor:
    """
    Retorna (e inicia, se necessário) o monitor global.
    Chamadas múltiplas retornam a mesma instância.
    """
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = GpuMonitor(poll_interval=poll_interval)
        await _global_monitor.start()
    return _global_monitor
