"""Sons reais de animais, para o formato "que som faz o animal?". Vêm do Freesound, só com licença
CC0 (domínio público: uso comercial liberado, sem exigir crédito). Cada candidato é ouvido pelo
"ouvido" local (CLAP) para confirmar que é mesmo o animal, e o trecho mais forte é recortado.

Precisa de uma chave gratuita do Freesound (FREESOUND_API_KEY no .env). Sem ela, as cenas com som
saem só com a narração e a revisão avisa. Para usar um som seu, grave dados/sons/<animal>.wav e
registre-o em dados/sons/catalogo.json com "licenca": "própria"."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from .config import DADOS
from .episodio import slug

PASTA_SONS = DADOS / "sons"
API = "https://freesound.org/apiv2/search/text/"
DURACAO_S = 2.0          # trecho recortado de cada som
REPETICOES = 2           # o som toca duas vezes depois da fala
INTERVALO_S = 0.35       # entre uma repetição e outra
CANDIDATOS = 6
CONFIANCA_MINIMA = 0.5   # chance mínima, segundo o CLAP, de ser o animal pedido
# O som característico de cada animal: melhora a busca ("cow moo" em vez de "cow") e a conferência.
VOCALIZACAO = {
    "cow": ("moo", "mooing"), "duck": ("quack", "quacking"), "rooster": ("crow", "crowing"),
    "chicken": ("cluck", "clucking"), "hen": ("cluck", "clucking"), "sheep": ("baa", "bleating"),
    "lamb": ("baa", "bleating"), "goat": ("bleat", "bleating"), "pig": ("oink", "oinking"),
    "horse": ("neigh", "neighing"), "donkey": ("bray", "braying"), "dog": ("bark", "barking"),
    "puppy": ("bark", "barking"), "cat": ("meow", "meowing"), "kitten": ("meow", "meowing"),
    "lion": ("roar", "roaring"), "elephant": ("trumpet", "trumpeting"), "frog": ("croak", "croaking"),
    "owl": ("hoot", "hooting"), "turkey": ("gobble", "gobbling"), "goose": ("honk", "honking"),
    "bird": ("chirp", "chirping"), "bee": ("buzz", "buzzing"), "monkey": ("chatter", "chattering"),
}
# Contra o que cada som é comparado. Para crianças, som de animal sofrendo ou assustador não serve.
OUTROS = ["people talking", "music", "background noise", "a car engine", "an electronic sound effect",
          "an animal in pain or distress", "a scary sound", "birds singing", "a dog barking"]


def arquivo(nome: str) -> Path:
    return PASTA_SONS / f"{slug(nome)}.wav"


def catalogo(pasta: Path = PASTA_SONS) -> dict[str, dict]:
    caminho = pasta / "catalogo.json"
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}


def _salvar_catalogo(dados: dict, pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "catalogo.json").write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def disponivel(nome: str, pasta: Path = PASTA_SONS) -> bool:
    return (pasta / f"{slug(nome)}.wav").exists() and slug(nome) in catalogo(pasta)


def buscar(animal: str, chave: str, sessao) -> list[dict]:
    """Sons CC0 do animal, primeiro pela etiqueta dele (mais certeiro), depois por texto."""
    termo = f"{animal} {VOCALIZACAO.get(animal, ('',))[0]}".strip()
    base = 'license:"Creative Commons 0" duration:[0.5 TO 30]'
    for filtro in (f"{base} tag:{animal.split()[-1]}", base):
        # A chave vai no cabeçalho, não na URL: erros de conexão imprimem a URL inteira no log.
        resposta = sessao.get(API, timeout=30, headers={"Authorization": f"Token {chave}"}, params={
            "query": termo, "page_size": CANDIDATOS * 2, "sort": "score",
            "filter": filtro, "fields": "id,name,username,license,duration,previews,url"})
        resposta.raise_for_status()
        resultados = resposta.json().get("results", [])
        if resultados:
            return resultados
    return []


def recortar(origem: Path, destino: Path, duracao: float = DURACAO_S) -> None:
    """Fica com o trecho de `duracao` segundos de maior energia, com volume ajustado e sem estalos."""
    import numpy as np

    from .montagem import ffmpeg
    from .ouvido import TAXA, carregar_audio

    audio = carregar_audio(origem, maximo_s=30)
    janela = int(TAXA * duracao)
    if len(audio) > janela:
        energia = np.convolve(audio.astype(np.float64) ** 2, np.ones(TAXA // 20), mode="same")
        inicio = int(np.argmax(np.convolve(energia, np.ones(janela), mode="valid")))
        audio = audio[inicio:inicio + janela]
    pico = float(np.max(np.abs(audio))) or 1.0
    audio = audio * (0.7 / pico)  # pico em -3 dB
    rampa = min(len(audio) // 4, TAXA // 50)  # 20 ms de entrada e saída suaves
    if rampa:
        audio[:rampa] *= np.linspace(0, 1, rampa)
        audio[-rampa:] *= np.linspace(1, 0, rampa)
    destino.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(TAXA), "-ac", "1", "-i", "-",
                    "-c:a", "pcm_s16le", str(destino)], input=audio.astype(np.float32).tobytes(), check=True)


def confianca(arquivo_som: Path, animal: str, ouvido) -> float:
    from .ouvido import carregar_audio

    alvo = f"a {animal} {VOCALIZACAO[animal][1]}" if animal in VOCALIZACAO else f"the sound of a {animal}"
    rotulos = [alvo] + [o for o in OUTROS if animal not in o]
    return ouvido.probabilidades(carregar_audio(arquivo_som), rotulos)[alvo]


def obter(animal: str, log=print, sessao=None, ouvido=None, pasta: Path = PASTA_SONS) -> bool:
    """Garante dados/sons/<animal>.wav. Devolve False se não houver chave ou som aprovado."""
    if disponivel(animal, pasta):
        return True
    chave = os.getenv("FREESOUND_API_KEY")
    if not chave:
        return False
    import requests

    from .ouvido import Ouvido

    sessao = sessao or requests.Session()
    ouvido = ouvido or Ouvido.obter()
    melhor = None
    with tempfile.TemporaryDirectory() as tmp:
        for item in buscar(animal, chave, sessao)[:CANDIDATOS]:
            bruto, recorte = Path(tmp) / f"{item['id']}.mp3", Path(tmp) / f"{item['id']}.wav"
            bruto.write_bytes(sessao.get(item["previews"]["preview-hq-mp3"], timeout=60).content)
            recortar(bruto, recorte)
            chance = confianca(recorte, animal, ouvido)
            if chance >= CONFIANCA_MINIMA and (melhor is None or chance > melhor[0]):
                melhor = (chance, item, recorte.read_bytes())
    if melhor is None:
        log(f"    nenhum som de \"{animal}\" passou na conferência do ouvido local")
        return False
    chance, item, dados = melhor
    (pasta / f"{slug(animal)}.wav").parent.mkdir(parents=True, exist_ok=True)
    (pasta / f"{slug(animal)}.wav").write_bytes(dados)
    registro = catalogo(pasta)
    registro[slug(animal)] = {"animal": animal, "fonte": "Freesound", "pagina": item.get("url", ""),
                              "titulo": item.get("name", ""), "autor": item.get("username", ""),
                              "licenca": "CC0", "confianca": round(chance, 3)}
    _salvar_catalogo(registro, pasta)
    log(f"    som de \"{animal}\": {item.get('name', '')} ({chance:.0%} de confiança)")
    return True


_trava = threading.Lock()  # na produção contínua duas linhas de trabalho podem buscar sons ao mesmo tempo


def garantir(nomes, log=print, pasta: Path = PASTA_SONS, **kw) -> list[str]:
    """Busca os sons que faltam; devolve os que continuam faltando."""
    faltando = []
    with _trava:
        for nome in dict.fromkeys(n for n in nomes if n):
            if not obter(nome, log=log, pasta=pasta, **kw):
                faltando.append(nome)
    if faltando and not os.getenv("FREESOUND_API_KEY"):
        log(f"  aviso: sem FREESOUND_API_KEY no .env, as cenas com {', '.join(faltando)} saem sem o som do animal")
    return faltando


def duracao_bloco(nome: str, pasta: Path = PASTA_SONS) -> float:
    """Tempo que as repetições do som ocupam depois da fala (0 se o som não estiver disponível)."""
    if not nome or not disponivel(nome, pasta):
        return 0.0
    import wave

    with wave.open(str(pasta / f"{slug(nome)}.wav")) as w:
        return REPETICOES * (w.getnframes() / w.getframerate() + INTERVALO_S)
