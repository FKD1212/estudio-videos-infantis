"""Extrai a estrutura (gancho, segmentos, ritmo, padrões de retenção) dos vídeos que se
destacam. O Gemini assiste o vídeo público direto pelo link do YouTube — nada é baixado."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import gemini
from .pesquisa import Video

FORMATO_RESPOSTA = """{
  "publico_idade": "faixa etária provável, ex.: 2-4 anos",
  "resumo_formato": "1-2 frases sobre o formato",
  "gancho": {"ate_segundo": 0, "o_que_acontece": "", "por_que_prende": ""},
  "segmentos": [
    {"inicio": "MM:SS", "fim": "MM:SS",
     "funcao": "abertura|apresentacao|desenvolvimento|musica|repeticao|interacao|surpresa|recompensa|transicao|encerramento",
     "descricao": "", "tecnica_de_retencao": ""}
  ],
  "ritmo": {"troca_de_cena_a_cada_s": 0, "variacao_de_energia": ""},
  "audio": {"narracao": "", "musica": "", "efeitos_sonoros": "", "repeticao": ""},
  "visual": {"estilo": "", "cores": "", "texto_na_tela": "", "movimento_de_camera": ""},
  "interacao_com_a_crianca": [""],
  "padroes_de_retencao": ["padrão reutilizável"],
  "pontos_fracos": ["onde a atenção provavelmente cai e por quê"],
  "molde_reutilizavel": ["passo genérico com duração aproximada"]
}"""

_TEXTO = {"type": "string"}
_TEXTOS = {"type": "array", "items": _TEXTO}


def _objeto(**propriedades) -> dict:
    return {"type": "object", "properties": propriedades, "required": list(propriedades)}


# A API exige o schema sempre que a resposta é JSON; ele espelha o FORMATO_RESPOSTA acima.
ESQUEMA = _objeto(
    publico_idade=_TEXTO,
    resumo_formato=_TEXTO,
    gancho=_objeto(ate_segundo={"type": "number"}, o_que_acontece=_TEXTO, por_que_prende=_TEXTO),
    segmentos={"type": "array", "items": _objeto(inicio=_TEXTO, fim=_TEXTO, funcao=_TEXTO, descricao=_TEXTO,
                                                 tecnica_de_retencao=_TEXTO)},
    ritmo=_objeto(troca_de_cena_a_cada_s={"type": "number"}, variacao_de_energia=_TEXTO),
    audio=_objeto(narracao=_TEXTO, musica=_TEXTO, efeitos_sonoros=_TEXTO, repeticao=_TEXTO),
    visual=_objeto(estilo=_TEXTO, cores=_TEXTO, texto_na_tela=_TEXTO, movimento_de_camera=_TEXTO),
    interacao_com_a_crianca=_TEXTOS,
    padroes_de_retencao=_TEXTOS,
    pontos_fracos=_TEXTOS,
    molde_reutilizavel=_TEXTOS,
)


def _prompt(v: Video) -> str:
    return (
        "Você é analista de retenção de conteúdo infantil no YouTube. Assista ao vídeo e descreva "
        "o FORMATO e a ESTRUTURA que fazem as crianças continuarem assistindo.\n"
        "Regras: não transcreva letras, falas ou roteiro; no molde reutilizável use passos "
        "genéricos, sem personagens, nomes, marcas ou músicas do vídeo — o objetivo é criar "
        "conteúdo original no mesmo formato, não uma cópia.\n\n"
        f'Contexto: título "{v.titulo}", {v.duracao_s // 60}min{v.duracao_s % 60:02d}s, '
        f"{v.views:,} views, {v.outlier or 0:.1f}x a mediana do próprio canal.\n\n"
        "Responda em português, APENAS com um JSON válido neste formato "
        "(tempos em MM:SS):\n" + FORMATO_RESPOSTA
    )


def candidatos(videos: list[Video], top: int, max_min: float,
               nicho: str | None = None, idioma: str | None = None) -> list[Video]:
    """Os `top` maiores outliers de cada nicho+idioma (ignorando vídeos longos demais, como
    compilações de 1 hora, que gastam a cota do Gemini e repetem a mesma estrutura)."""
    filtrados = sorted(
        (v for v in videos
         if v.outlier and v.duracao_s <= max_min * 60
         and (nicho is None or v.nicho == nicho) and (idioma is None or v.idioma == idioma)),
        key=lambda v: v.outlier, reverse=True,
    )
    escolhidos, por_grupo, vistos = [], {}, set()
    for v in filtrados:
        grupo = (v.nicho, v.idioma)
        if v.id in vistos or por_grupo.get(grupo, 0) >= top:
            continue
        escolhidos.append(v)
        vistos.add(v.id)
        por_grupo[grupo] = por_grupo.get(grupo, 0) + 1
    return escolhidos


def analisar(videos: list[Video], pasta: Path, log=print) -> int:
    cliente = gemini.cliente()
    modelo = os.getenv("GEMINI_MODELO", gemini.MODELO_PADRAO)
    pasta.mkdir(parents=True, exist_ok=True)
    feitos = 0
    for v in videos:
        arquivo = pasta / f"{v.id}.json"
        if arquivo.exists():
            continue
        log(f"  analisando {v.nicho} [{v.idioma}] {v.outlier:.1f}x  {v.titulo[:60]}")
        try:
            interacao = gemini.criar(
                cliente, log=log, model=modelo,
                input=[{"type": "text", "text": _prompt(v)}, {"type": "video", "uri": v.url, "resolution": "low"}],
                response_format={"type": "text", "mime_type": "application/json", "schema": ESQUEMA},
            )
            estrutura = gemini.extrair_json(interacao.output_text)
        except gemini.CotaGeminiEsgotada:
            raise
        except Exception as erro:  # um vídeo com problema não deve parar o lote
            log(f"    falhou: {erro}")
            continue
        arquivo.write_text(json.dumps({
            "video": asdict(v) | {"url": v.url},
            "modelo": gemini.modelo_usado(interacao, modelo),
            "analisado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "estrutura": estrutura,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        feitos += 1
    return feitos


def carregar_estruturas(pasta: Path) -> dict[str, dict]:
    if not pasta.exists():
        return {}
    return {a.stem: json.loads(a.read_text(encoding="utf-8")) for a in sorted(pasta.glob("*.json"))}
