"""Fase 3b: imagens geradas na própria placa de vídeo com o FLUX.2 [klein] 4B — licença
Apache 2.0 (uso comercial liberado) e nenhum custo por imagem. Cada personagem ganha uma imagem
de referência, passada junto em todas as cenas em que aparece para manter a mesma aparência."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import recursos
from .config import DADOS, Canal
from .episodio import Episodio, Manifesto, assinatura
from .montagem import arquivo_imagem
from .roteiro import personagens, tomadas

MODELO_IMAGEM = "black-forest-labs/FLUX.2-klein-4B"
TAMANHO_CENA = (1536, 864)  # 16:9; a montagem amplia para 1920x1080
TAMANHO_VERTICAL = (864, 1536)  # 9:16 dos Shorts; a montagem amplia para 1080x1920
TAMANHO_REFERENCIA = (1024, 1024)
MAX_REFERENCIAS = 3         # mais que isso deixa a geração lenta e pesada
PASTA_PERSONAGENS = DADOS / "personagens"
SEM_TEXTO = "No text, letters, numbers, captions, logos or watermarks anywhere in the image."


def prompt_referencia(descricao: str, estilo: str) -> str:
    return (f"Character design reference: {descricao}. Full body, front view, standing, friendly "
            f"expression, centered on a plain white background. Style: {estilo}. {SEM_TEXTO}")


def prompt_cena(descricao: str, elenco: list[tuple[str, str]], estilo: str, vertical: bool = False) -> str:
    partes = [descricao.rstrip(". ") + "."]
    if vertical:
        partes.append("Tall vertical 9:16 frame: the main subject large and centered, nothing important at the "
                      "very top or bottom edges.")
    if elenco:
        partes.append("Keep each character exactly as in its reference image: " + "; ".join(
            f"reference image {k} is {nome}, {visual}" for k, (nome, visual) in enumerate(elenco, 1)) + ".")
    partes += [f"Style: {estilo}.", SEM_TEXTO]
    return " ".join(partes)


class GeradorFlux:
    """Carrega o modelo uma vez (cerca de 1 minuto) e gera quantas imagens precisar."""

    def __init__(self, modelo: str, log=print):
        try:
            import torch
            from diffusers import Flux2KleinPipeline
            from diffusers.utils import logging as log_diffusers
            from transformers.utils import logging as log_transformers
        except ImportError:
            raise SystemExit("Faltam os pacotes de GPU. Veja \"Imagens na placa de vídeo\" no README.")
        if not torch.cuda.is_available():
            raise SystemExit("O PyTorch não encontrou a placa de vídeo NVIDIA. Veja \"Imagens na placa de vídeo\" no README.")
        log("  carregando o modelo de imagem (na primeira vez baixa ~16 GB)...")
        log_diffusers.disable_progress_bar()
        log_transformers.disable_progress_bar()
        self.torch = torch
        self.pipe = Flux2KleinPipeline.from_pretrained(modelo, dtype=torch.bfloat16)
        self.pipe.enable_model_cpu_offload()  # só a parte em uso fica na placa: cabe em 12 GB
        self.pipe.set_progress_bar_config(disable=True)

    def __call__(self, prompt: str, saida: Path, tamanho: tuple[int, int],
                 referencias: list[Path], semente: int) -> None:
        from PIL import Image

        refs = [Image.open(r).convert("RGB") for r in referencias]
        imagem = self.pipe(
            prompt=prompt, image=refs or None, width=tamanho[0], height=tamanho[1],
            num_inference_steps=4, guidance_scale=1.0,  # valores do modelo destilado
            generator=self.torch.Generator("cpu").manual_seed(semente),
        ).images[0]
        saida.parent.mkdir(parents=True, exist_ok=True)
        imagem.save(saida)
        self.torch.cuda.empty_cache()  # devolve a memória da placa para a música e a voz


_geradores: dict[str, GeradorFlux] = {}


def gerador_compartilhado(modelo: str, log=print) -> GeradorFlux:
    """Na produção contínua o modelo fica carregado (na RAM) entre um episódio e outro."""
    if modelo not in _geradores:
        _geradores[modelo] = GeradorFlux(modelo, log)
    return _geradores[modelo]


def liberar_compartilhados() -> bool:
    """Tira o modelo da memória quando a produção para (semana coberta); o próximo episódio carrega
    de novo (~20 s). Devolve se havia algo carregado."""
    if not _geradores:
        return False
    torch = next(iter(_geradores.values())).torch
    _geradores.clear()
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    return True


def _quem_aparece(item: dict, descricao: str, elenco: dict[str, dict]) -> list[str]:
    """Os personagens listados na imagem e também os citados pelo nome na descrição: sem a imagem
    de referência de quem é citado, o gerador costuma desenhar outro personagem em dobro. Numa
    "pista" (Short de adivinhar), só os listados: a referência do bicho entregaria a resposta."""
    if item.get("pista"):
        return list(item.get("personagens", []))[:MAX_REFERENCIAS]
    citados = [pid for pid, p in elenco.items() if pid not in item.get("personagens", [])
               and any(re.search(rf"\b{re.escape(nome)}\b", descricao, re.I) for nome in (p["nome"], pid))]
    return list(dict.fromkeys([*item.get("personagens", []), *citados]))[:MAX_REFERENCIAS]


def gerar(ep: Episodio, roteiro: dict, canal: Canal, gerador=None, log=print,
          pasta_personagens: Path = PASTA_PERSONAGENS) -> int:
    """Gera o que falta ou mudou: referências dos personagens, cenas e base da miniatura."""
    modelo = os.getenv("MODELO_IMAGEM", MODELO_IMAGEM)
    elenco = personagens(roteiro, canal)
    cenas, mini = roteiro["cenas"], roteiro.get("miniatura", {})
    vertical = roteiro.get("formato_video") == "vertical"
    tamanho = TAMANHO_VERTICAL if vertical else TAMANHO_CENA
    # Cada imagem de uma fala tem a sua semente; mudar a "semente" da cena refaz todas elas. Imagens
    # "fixas" (a chamada para o episódio completo dos Shorts) são montadas fora daqui.
    pedidos = [(arquivo_imagem(n, k), t["descricao"], _quem_aparece(t, t["descricao"], elenco),
                c.get("semente", 0) * 100 + k)
               for n, c in enumerate(cenas, 1) for k, t in enumerate(tomadas(c), 1) if not t.get("fixa")]
    if mini.get("imagem"):
        pedidos.append(("miniatura_base.png", mini["imagem"], _quem_aparece(mini, mini["imagem"], elenco),
                        mini.get("semente", 0)))

    def gerar_um(prompt, saida, tamanho, refs, semente):
        nonlocal gerador  # o modelo só é carregado se houver algo a gerar, e uma vez por processo
        with recursos.gpu():
            if gerador is None:
                gerador = gerador_compartilhado(modelo, log)
            gerador(prompt, saida, tamanho, refs, semente)

    feitos = 0
    manifesto_canal, manifesto_ep = Manifesto(pasta_personagens), Manifesto(ep.imagens)
    referencias: dict[str, tuple[Path, str]] = {}  # id -> (arquivo, assinatura)
    for pid in dict.fromkeys(pid for _, _, ids, _ in pedidos for pid in ids):
        p = elenco[pid]
        # Os fixos ficam fora do episódio e são reaproveitados por todos os episódios.
        manifesto, nome = ((manifesto_canal, f"{pid}.png") if p["fixo"] else (manifesto_ep, f"personagem_{pid}.png"))
        prompt = prompt_referencia(p["descricao_visual"], canal.estilo_visual)
        assinatura_ = assinatura(prompt, p["semente"], modelo, TAMANHO_REFERENCIA)
        if not manifesto.atual(nome, assinatura_):
            log(f"  personagem: {p['nome']}")
            gerar_um(prompt, manifesto.pasta / nome, TAMANHO_REFERENCIA, [], p["semente"])
            manifesto.registrar(nome, assinatura_)
            feitos += 1
        referencias[pid] = (manifesto.pasta / nome, assinatura_)

    for nome, descricao, ids, semente in pedidos:
        prompt = prompt_cena(descricao, [(elenco[i]["nome"], elenco[i]["descricao_visual"]) for i in ids],
                             canal.estilo_visual, vertical and nome != "miniatura_base.png")
        assinatura_ = assinatura(prompt, [referencias[i][1] for i in ids], semente, modelo, tamanho)
        if manifesto_ep.atual(nome, assinatura_):
            continue
        log(f"  imagem: {nome}")
        gerar_um(prompt, ep.imagens / nome, tamanho, [referencias[i][0] for i in ids], semente)
        manifesto_ep.registrar(nome, assinatura_)
        feitos += 1

    folha_de_revisao(ep, cenas, vertical)
    return feitos


def folha_de_revisao(ep: Episodio, cenas: list[dict], vertical: bool = False) -> Path | None:
    """Todas as imagens numa só, numeradas como cena.imagem (3.2 = cena 3, 2ª imagem)."""
    from PIL import Image, ImageDraw

    arquivos = [(f"{n}.{k}", ep.imagens / arquivo_imagem(n, k))
                for n, c in enumerate(cenas, 1) for k in range(1, len(tomadas(c)) + 1)]
    arquivos = [(n, a) for n, a in arquivos if a.exists()]
    if not arquivos:
        return None
    colunas, (lw, lh) = (6, (216, 384)) if vertical else (4, (384, 216))
    folha = Image.new("RGB", (colunas * lw, -(-len(arquivos) // colunas) * lh), "white")
    desenho = ImageDraw.Draw(folha)
    for i, (rotulo, arquivo) in enumerate(arquivos):
        x, y = (i % colunas) * lw, (i // colunas) * lh
        with Image.open(arquivo) as img:
            folha.paste(img.convert("RGB").resize((lw, lh)), (x, y))
        desenho.rectangle([x, y, x + 56, y + 26], fill="black")
        desenho.text((x + 6, y + 3), rotulo, fill="white", font_size=18)
    saida = ep.pasta / "revisao_imagens.jpg"
    folha.save(saida, quality=85)
    return saida
