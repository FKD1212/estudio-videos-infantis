"""Um "ouvido" local: o CLAP (LAION, Apache 2.0) compara um áudio com descrições em texto e diz
qual combina mais. Serve para conferir se um som baixado é mesmo do animal pedido e, quando a cota
do Gemini acaba, para avaliar as versões da trilha. Roda na CPU (é pequeno) para não disputar a
placa de vídeo com a geração de imagens."""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from .montagem import ffmpeg

MODELO = "laion/clap-htsat-unfused"
TAXA = 48_000
_trava = threading.Lock()


def carregar_audio(arquivo: Path, maximo_s: float = 10.0, inicio_s: float = 0.0):
    """Áudio mono 48 kHz em float32 (numpy), decodificado pelo ffmpeg."""
    import numpy as np

    bruto = subprocess.run(
        [ffmpeg(), "-loglevel", "error", "-ss", f"{inicio_s}", "-t", f"{maximo_s}", "-i", str(arquivo),
         "-ac", "1", "-ar", str(TAXA), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(bruto, dtype=np.float32).copy()


class Ouvido:
    _unico: Ouvido | None = None

    def __init__(self):
        from transformers import ClapModel, ClapProcessor
        from transformers.utils import logging as log_transformers

        log_transformers.disable_progress_bar()
        self.modelo = ClapModel.from_pretrained(MODELO).eval()
        self.processador = ClapProcessor.from_pretrained(MODELO)

    @classmethod
    def obter(cls) -> Ouvido:
        """O modelo é carregado uma vez por execução (mesmo com várias linhas de trabalho)."""
        with _trava:
            if cls._unico is None:
                cls._unico = cls()
        return cls._unico

    def probabilidades(self, audio, rotulos: list[str]) -> dict[str, float]:
        """Probabilidade de cada rótulo descrever o áudio (somam 1)."""
        import torch

        entradas = self.processador(text=rotulos, audio=[audio], sampling_rate=TAXA, return_tensors="pt", padding=True)
        with torch.no_grad():
            chances = self.modelo(**entradas).logits_per_audio.softmax(dim=-1)[0]
        return {r: float(p) for r, p in zip(rotulos, chances)}
