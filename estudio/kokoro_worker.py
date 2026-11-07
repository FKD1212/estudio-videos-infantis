"""Roda DENTRO do ambiente do Kokoro (Python 3.12), chamado por estudio/narracao.py: é a voz
local, usada quando a cota de narração do Gemini acaba (ou sempre, se o canal.toml pedir). Lê um
pedido JSON e grava um WAV por fala. Só usa a biblioteca padrão, numpy, soundfile e o Kokoro."""
import json
import re
import sys
from pathlib import Path

TAXA = 24_000
PAUSAS = {"short pause": 0.35, "long pause": 1.2}  # as mesmas marcas que o roteiro usa para o Gemini


def main(caminho_pedido: str) -> None:
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    pedido = json.loads(Path(caminho_pedido).read_text(encoding="utf-8"))
    pipeline = KPipeline(lang_code=pedido["lang_code"], repo_id="hexgrad/Kokoro-82M")
    for fala in pedido["falas"]:
        pedacos = []
        for parte in re.split(r"(<[^>]*>)", fala["texto"]):
            marca = re.fullmatch(r"<\s*([^>]*?)\s*>", parte.strip())
            if marca:  # <long pause> vira silêncio; <laugh> e outras marcas são ignoradas
                pedacos.append(np.zeros(int(TAXA * PAUSAS.get(marca.group(1).lower(), 0)), np.float32))
            elif parte.strip():
                for _, _, audio in pipeline(parte.strip(), voice=pedido["voz"], speed=pedido["velocidade"]):
                    pedacos.append(np.asarray(audio, dtype=np.float32))
        audio = np.concatenate(pedacos) if pedacos else np.zeros(TAXA // 10, np.float32)
        sf.write(fala["saida"], audio, TAXA, subtype="PCM_16")
        print(f"fala pronta: {Path(fala['saida']).name}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
