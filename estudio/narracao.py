"""Fase 3a: narração de cada cena. Por padrão, com o Gemini TTS (cota gratuita); quando a cota
acaba, o episódio inteiro passa para a voz local (Kokoro, Apache 2.0, roda na sua máquina), para
não misturar duas vozes no mesmo vídeo. Cada cena vira um WAV em narracao/ e só é refeita se o
texto, a voz ou o estilo mudar. Para usar a própria voz numa cena, grave um WAV com o mesmo nome
por cima do gerado. Para voltar um episódio ao Gemini, apague narracao/motor.json."""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path

from . import gemini, recursos
from .config import RAIZ, Canal, Voz
from .episodio import Episodio, Manifesto, assinatura

MODELO_TTS_PADRAO = "gemini-3.8-flash-tts"
TAXA_PCM = 24_000  # o TTS devolve PCM 16 bits mono a 24 kHz
PYTHON_KOKORO = RAIZ / "ferramentas" / "kokoro" / ".venv" / "Scripts" / "python.exe"
WORKER_KOKORO = Path(__file__).with_name("kokoro_worker.py")
IDIOMA_KOKORO = {"en-US": "a", "en-GB": "b", "pt-BR": "p", "es-ES": "e", "fr-FR": "f", "it-IT": "i"}


def arquivo_cena(n: int) -> str:
    return f"cena_{n:02d}.wav"


def pedido(texto: str, voz: str, idioma: str, estilo: str, modelo: str) -> dict:
    return {
        "model": modelo,
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": texto,
            "annotations": [{"type": "speech_metadata", "style": estilo}],
        }]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav"},
        "generation_config": {"speech_config": [{"voice": voz, "language": idioma}]},
    }


def _wav(audio) -> bytes:
    """Bytes WAV da resposta (que pode vir em base64 e, em alguns formatos, sem cabeçalho)."""
    dados = audio.data
    dados = base64.b64decode(dados) if isinstance(dados, str) else bytes(dados)
    if dados[:4] == b"RIFF":
        return dados
    saida = io.BytesIO()
    with wave.open(saida, "wb") as w:
        w.setnchannels(getattr(audio, "channels", None) or 1)
        w.setsampwidth(2)
        w.setframerate(getattr(audio, "sample_rate", None) or TAXA_PCM)
        w.writeframes(dados)
    return saida.getvalue()


def duracao_wav(caminho: Path) -> float:
    with wave.open(str(caminho), "rb") as w:
        return w.getnframes() / w.getframerate()


class GeradorKokoro:
    """Chama o Kokoro no ambiente dele (Python 3.12); o modelo é carregado uma vez por leva."""

    def __call__(self, falas: list[dict], voz: Voz, log=print) -> None:
        if not PYTHON_KOKORO.exists():
            raise SystemExit('A voz local (Kokoro) não está instalada. Veja "Reserva local" no README.')
        with tempfile.TemporaryDirectory() as tmp:
            arquivo = Path(tmp) / "pedido.json"
            arquivo.write_text(json.dumps({
                "lang_code": IDIOMA_KOKORO.get(voz.idioma, "a"), "voz": voz.voz_local,
                "velocidade": voz.velocidade_local, "falas": falas}, ensure_ascii=False), encoding="utf-8")
            execucao = subprocess.run([str(PYTHON_KOKORO), str(WORKER_KOKORO), str(arquivo)], capture_output=True,
                                      text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
        if execucao.returncode != 0:
            raise RuntimeError(f"Kokoro falhou:\n{(execucao.stderr or execucao.stdout).strip()[-2000:]}")


def _motor(ep: Episodio, voz: Voz) -> str:
    registro = ep.ler(ep.narracao / "motor.json")
    return registro["motor"] if registro else voz.motor


def _falas(roteiro: dict):
    for n, cena in enumerate(roteiro["cenas"], 1):
        if cena["narracao"].strip():
            yield n, cena


def narrar(ep: Episodio, roteiro: dict, canal: Canal, cliente=None, log=print, kokoro=None) -> int:
    voz = canal.narracao[roteiro["idioma"]]
    if _motor(ep, voz) == "gemini":
        try:
            return _narrar_gemini(ep, roteiro, voz, cliente, log)
        except gemini.CotaGeminiEsgotada:
            if not voz.voz_local:
                raise
            log("  cota de narração do Gemini esgotada: este episódio inteiro passa para a voz local (Kokoro), "
                "para não misturar duas vozes")
            ep.gravar(ep.narracao / "motor.json", {"motor": "local", "motivo": "cota do Gemini TTS esgotada"})
    return _narrar_local(ep, roteiro, voz, kokoro or GeradorKokoro(), log)


def _narrar_gemini(ep: Episodio, roteiro: dict, voz: Voz, cliente, log) -> int:
    modelo = os.getenv("GEMINI_TTS_MODELO", MODELO_TTS_PADRAO)
    manifesto = Manifesto(ep.narracao)
    feitos = 0
    for n, cena in _falas(roteiro):
        texto = cena["narracao"].strip()
        estilo = voz.estilo + (f"; nesta fala: {cena['estilo_fala']}" if cena.get("estilo_fala") else "")
        nome = arquivo_cena(n)
        assinatura_ = assinatura(texto, voz.voz, voz.idioma, estilo, modelo)
        if manifesto.atual(nome, assinatura_):
            continue
        cliente = cliente or gemini.cliente()
        log(f"  narrando cena {n}/{len(roteiro['cenas'])}")
        interacao = gemini.criar(cliente, log=log, **pedido(texto, voz.voz, voz.idioma, estilo, modelo))
        if not interacao.output_audio:
            raise RuntimeError(f"o Gemini não devolveu áudio para a cena {n}")
        ep.narracao.mkdir(parents=True, exist_ok=True)
        (ep.narracao / nome).write_bytes(_wav(interacao.output_audio))
        manifesto.registrar(nome, assinatura_)
        feitos += 1
    return feitos


def _narrar_local(ep: Episodio, roteiro: dict, voz: Voz, kokoro, log) -> int:
    if not voz.voz_local:
        raise SystemExit(f'Defina "voz_local" em [narracao.{roteiro["idioma"]}] no canal.toml para usar a voz local.')
    manifesto = Manifesto(ep.narracao)
    falas, assinaturas = [], {}
    for n, cena in _falas(roteiro):
        texto, nome = cena["narracao"].strip(), arquivo_cena(n)
        assinaturas[nome] = assinatura(texto, "kokoro", voz.voz_local, voz.velocidade_local, voz.idioma)
        if not manifesto.atual(nome, assinaturas[nome]):
            falas.append({"texto": texto, "saida": str(ep.narracao / nome)})
    if falas:
        log(f"  narrando {len(falas)} cenas com a voz local ({voz.voz_local})")
        ep.narracao.mkdir(parents=True, exist_ok=True)
        with recursos.gpu():
            kokoro(falas, voz, log)
        for fala in falas:
            manifesto.registrar(Path(fala["saida"]).name, assinaturas[Path(fala["saida"]).name])
    return len(falas)


def pendentes(ep: Episodio, roteiro: dict) -> list[int]:
    """Cenas com texto que ainda não têm áudio."""
    return [n for n, c in enumerate(roteiro["cenas"], 1)
            if c["narracao"].strip() and not (ep.narracao / arquivo_cena(n)).exists()]
