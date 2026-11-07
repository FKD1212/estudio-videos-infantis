"""Divisão do hardware na produção contínua. A placa de vídeo (12 GB) atende uma etapa por vez —
imagens, voz local, música e modelo de texto local não cabem juntos —, enquanto o processador
monta vídeos em paralelo. `gpu()` garante a vez, inclusive entre processos (a tarefa diária e o
produtor contínuo podem rodar ao mesmo tempo)."""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .config import CACHE

ARQUIVO_TRAVA = CACHE / "gpu.lock"
_trava_local = threading.RLock()  # reentrante: uma etapa que já tem a vez pode chamar outra
_estado = threading.local()


def nucleos_para_montagem() -> int:
    """Encodes de vídeo em paralelo: cada ffmpeg já usa vários núcleos, mas o filtro de movimento
    não; um quarto das linhas de execução aproveita o processador sem sufocar o resto."""
    return max(1, (os.cpu_count() or 4) // 4)


@contextmanager
def gpu(trava: Path | None = None):
    trava = trava or ARQUIVO_TRAVA
    with _trava_local:
        if getattr(_estado, "profundidade", 0):  # esta linha de execução já tem a vez
            _estado.profundidade += 1
            try:
                yield
            finally:
                _estado.profundidade -= 1
            return
        trava.parent.mkdir(parents=True, exist_ok=True)
        with open(trava, "a+b") as arquivo:
            _travar(arquivo)
            _estado.profundidade = 1
            try:
                yield
            finally:
                _estado.profundidade = 0
                _destravar(arquivo)


def _travar(arquivo) -> None:
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                arquivo.seek(0)
                msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(2)
    else:  # pragma: no cover - o projeto roda no Windows
        import fcntl

        fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX)


def _destravar(arquivo) -> None:
    if os.name == "nt":
        import msvcrt

        arquivo.seek(0)
        msvcrt.locking(arquivo.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover
        import fcntl

        fcntl.flock(arquivo.fileno(), fcntl.LOCK_UN)


def instancia_unica(nome: str, pasta: Path | None = None):
    """Trava sem espera para um processo que só pode rodar uma vez (o produtor contínuo). Devolve o
    arquivo travado (mantenha-o aberto enquanto rodar) ou None se outro processo já tem a trava."""
    caminho = (pasta or CACHE) / f"{nome}.lock"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    arquivo = open(caminho, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            arquivo.seek(0)
            msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover
            import fcntl

            fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        arquivo.close()
        return None
    return arquivo
