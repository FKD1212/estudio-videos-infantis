"""Fase 2: roteiro original de um episódio. O Gemini escreve a partir do universo do canal
(canal.toml) e do que prende a atenção nos vídeos que se destacam no nicho (estruturas da
fase 1). O resultado é um roteiro.json para revisar e editar antes de produzir."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from . import gemini
from .config import Canal
from .contos import trecho_do_prompt

LINGUA = {"pt": "português do Brasil", "en": "inglês americano"}
PALAVRAS_POR_MINUTO = 120  # narração lenta, para crianças pequenas
PALAVRAS_POR_CENA = 28     # 2 a 3 frases por fala; cada fala tem de 1 a 3 imagens
PALAVRAS_POR_IMAGEM = 8    # ~3,5 s: os vídeos que se destacam trocam de imagem a cada 2-4 s
MAX_TOMADAS = 4
LIMITE_TEXTO_NA_TELA = 40
LIMITE_DESCRICAO_BYTES = 5000

_TEXTO = {"type": "string"}
_TEXTOS = {"type": "array", "items": _TEXTO}


def _objeto(**propriedades) -> dict:
    return {"type": "object", "properties": propriedades, "required": list(propriedades)}


ESQUEMA = _objeto(
    titulo=_TEXTO,
    resumo={"type": "string", "description": "a história em 2-3 frases"},
    aprendizado={"type": "string", "description": "o que a criança aprende"},
    clima_musica={"type": "string", "enum": ["calma", "alegre", "aventura"]},
    personagens_do_episodio={"type": "array", "items": _objeto(id=_TEXTO, nome=_TEXTO, descricao_visual=_TEXTO)},
    cenas={"type": "array", "items": _objeto(
        narracao=_TEXTO, estilo_fala=_TEXTO,
        imagens={"type": "array", "items": _objeto(descricao=_TEXTO, personagens=_TEXTOS)},
        texto_na_tela=_TEXTO, som=_TEXTO)},
    miniatura=_objeto(imagem=_TEXTO, personagens=_TEXTOS, texto=_TEXTO),
    youtube=_objeto(titulo=_TEXTO, descricao=_TEXTO, tags=_TEXTOS, hashtags=_TEXTOS),
)
HASHTAG = re.compile(r"#\w{2,40}")  # sem espaço nem pontuação; \w aceita letras acentuadas
MAX_HASHTAGS = 15  # acima disso o YouTube ignora todas


def molde_do_nicho(estruturas: dict[str, dict], nicho: str, idioma: str, maximo: int = 3) -> str:
    """O que prende a atenção nos maiores outliers do nicho (no idioma pedido; se não houver
    análise nele, em qualquer idioma). Fica de fora o passo a passo do vídeo original: o
    roteiro aprende o gancho e o ritmo, mas não reproduz a estrutura de ninguém."""
    do_nicho = [d for d in estruturas.values() if d["video"]["nicho"] == nicho]
    escolhidos = [d for d in do_nicho if d["video"]["idioma"] == idioma] or do_nicho
    escolhidos.sort(key=lambda d: d["video"].get("outlier") or 0, reverse=True)
    blocos = []
    for n, d in enumerate(escolhidos[:maximo], 1):
        v, e = d["video"], d["estrutura"]
        gancho, ritmo = e.get("gancho", {}), e.get("ritmo", {})
        blocos.append("\n".join([
            f"Referência {n} ({v.get('outlier') or 0:.0f}x as views normais do canal, "
            f"{v['duracao_s'] // 60} min, público {e.get('publico_idade', '?')}):",
            f"- Formato: {e.get('resumo_formato', '')}",
            f"- Gancho: {gancho.get('o_que_acontece', '')} — {gancho.get('por_que_prende', '')}",
            f"- Ritmo: troca de cena a cada ~{ritmo.get('troca_de_cena_a_cada_s', '?')} s; "
            f"{ritmo.get('variacao_de_energia', '')}",
            f"- O que prende: {'; '.join(e.get('padroes_de_retencao', []))}",
            f"- Onde a atenção cai (evite): {'; '.join(e.get('pontos_fracos', []))}",
        ]))
    return "\n\n".join(blocos)


def montar_prompt(canal: Canal, nicho: str, idioma: str, tema: str | None, minutos: float,
                  molde: str, anteriores: list[str], conto=None) -> str:
    lingua = LINGUA[idioma]
    palavras = round(minutos * PALAVRAS_POR_MINUTO)
    cenas = max(4, round(palavras / PALAVRAS_POR_CENA))
    fixos = "\n".join(
        f'- id "{p.id}": {p.nome(idioma)} — {p.personalidade}. Aparência: {p.descricao_visual}'
        for p in canal.personagens
    ) or "- (nenhum; crie os que a história precisar)"
    partes = [
        f'Você é roteirista de "{canal.nome}", um canal infantil do YouTube para crianças de '
        f'{canal.publico}. Escreva um episódio ORIGINAL de cerca de {minutos:g} minutos no nicho "{nicho}".',
        f"Tema pedido: {tema}" if tema else
        "Tema: escolha um tema novo e concreto, ligado ao dia a dia da criança.",
        "",
        "Personagens fixos do canal (use os que fizerem sentido, pelo id, mantendo a personalidade):",
        fixos,
    ]
    if conto:
        partes += ["", trecho_do_prompt(conto)]
    if anteriores:
        partes += ["", "Episódios já feitos — não repita o tema nem a estrutura deles:",
                   *[f"- {t}" for t in anteriores[-30:]]]
    if molde:
        partes += ["", "O que prende a atenção nos vídeos que mais se destacam neste nicho. Use como "
                   "aprendizado sobre gancho, ritmo, repetição e interação — NÃO copie formato, "
                   "personagens, falas ou músicas, e crie uma estrutura própria para esta história:",
                   "", molde]
    partes += [
        "",
        "Regras:",
        f"0. Tom: {canal.roteiro.tom}.",
        "1. História com começo, meio e fim: gancho nos primeiros 5 segundos, uma pergunta ou pequeno "
        "problema que a criança quer ver resolvido, e uma resolução satisfatória. Nada de lista solta de cenas.",
        "2. Um aprendizado claro e adequado à idade (uma palavra, um número, uma cor, um hábito, uma emoção).",
        "3. Nada assustador, angustiante ou perigoso: nenhum personagem ou animal sofrendo, sem choro "
        "dramático, sem sustos. Conflitos pequenos e gentis.",
        f"4. Linguagem para {canal.publico}: frases curtas, palavras simples, repetição intencional das "
        "palavras-chave e 1 a 3 perguntas diretas à criança, cada uma seguida de <long pause> para ela responder.",
        f'5. Narração em {lingua}, cerca de {palavras} palavras no total, em cerca de {cenas} cenas de 2 a 3 '
        'frases. O campo "narracao" é lido literalmente pela voz: sem indicação de cena, sem emoji, sem nome '
        "de quem fala. Pode usar com moderação as marcas <short pause>, <long pause> e <laugh>.",
        '6. "estilo_fala": como aquela fala deve soar, em poucas palavras (ex.: "sussurrando, curiosa").',
        f'7. "imagens": de 1 a 3 tomadas por cena, uma para cada ~{PALAVRAS_POR_IMAGEM} palavras de fala (a '
        "imagem troca a cada 3-4 segundos), mostrando momentos ou enquadramentos diferentes do que a fala conta "
        '(close, plano aberto, de cima, detalhe). Cada tomada tem "descricao" EM INGLÊS para um gerador de '
        "imagens (quem está, o que faz, expressão, cenário e enquadramento; cite os personagens pelo nome; não "
        'descreva o estilo de desenho, que já é fixo, nem peça texto escrito) e "personagens" (ids de quem '
        "aparece nela, fixos ou do episódio). Quando a fala pede para a criança adivinhar, a imagem não "
        "mostra a resposta antes da revelação (mostre só uma pista: uma sombra, um rabo, um arbusto mexendo).",
        '8. "som": nome EM INGLÊS de um animal real e comum (ex.: "cow", "rooster", "sheep") quando a cena '
        "mostra esse animal fazendo o seu som; o som real dele toca 2 vezes logo depois da fala. Prepare o som "
        'na fala (ex.: "Listen! What does the cow say?"). Vazio nas demais cenas; nada de máquinas ou vozes.',
        f'9. "texto_na_tela": vazio na maioria das cenas; só uma palavra, número ou frase curtíssima (até '
        f"{LIMITE_TEXTO_NA_TELA} caracteres) quando reforçar o aprendizado.",
        f'10. "personagens_do_episodio": no máximo 4 personagens novos, só deste episódio, com id curto sem '
        f"acento, nome em {lingua} (original: nunca nome de personagem conhecido de outra marca) e "
        "descricao_visual em inglês bem específica (cores, formato, roupa). Todo animal ou personagem que "
        "aparece em mais de uma imagem precisa estar aqui ou nos fixos, para manter a mesma aparência. "
        "Lista vazia se não precisar.",
        f'11. "miniatura": cena em inglês para a miniatura (personagem expressivo, fundo simples, alto '
        f"contraste), os ids de quem aparece nela e um texto de no máximo 4 palavras em {lingua}.",
        f'12. "youtube": título em {lingua} com até 70 caracteres, atraente e honesto (sem clickbait); '
        "descrição de 2 a 3 parágrafos curtos para os pais, sem links; 8 a 15 tags; e de 3 a 5 hashtags "
        f"em {lingua} (\"#\" seguido de uma palavra ou palavras juntas, sem espaço, ex.: #AnimalSounds), da "
        "mais para a menos importante: as 3 primeiras aparecem acima do título. Nada de promessas de saúde, "
        "terapia ou desenvolvimento (atraso de fala, autismo, TDAH...) nem nomes de outras marcas.",
        "",
        f'13. "titulo": nome curto de trabalho do episódio, em {lingua} (vira o nome da pasta); "resumo" e '
        '"aprendizado" são internos, para a equipe, em português.',
        "",
        "Responda apenas com o JSON pedido.",
    ]
    return "\n".join(partes)


def _texto(valor) -> str:
    return str(valor or "").strip()


def normalizar_hashtags(brutas) -> list[str]:
    """'#Animal Sounds' e 'animalsounds' viram '#AnimalSounds' e '#animalsounds'; repetidas saem."""
    vistas, saida = set(), []
    for bruta in brutas or []:
        palavras = re.findall(r"\w+", str(bruta))
        if not palavras:
            continue
        tag = "#" + (palavras[0] if len(palavras) == 1 else "".join(p[:1].upper() + p[1:] for p in palavras))
        if HASHTAG.fullmatch(tag) and tag.lower() not in vistas:
            vistas.add(tag.lower())
            saida.append(tag)
    return saida


def hashtags_do_video(roteiro: dict, do_canal: list[str]) -> list[str]:
    """As 2 mais importantes do episódio, as da série, depois o resto (as 3 primeiras ficam acima do
    título); no máximo 15, ou o YouTube ignora todas."""
    do_episodio = roteiro.get("youtube", {}).get("hashtags", [])
    return normalizar_hashtags([*do_episodio[:2], *do_canal, *do_episodio[2:]])[:MAX_HASHTAGS]


def tomadas(cena: dict) -> list[dict]:
    """As imagens de uma cena. Aceita também o formato antigo, com uma "imagem" só."""
    if cena.get("imagens"):
        return cena["imagens"]
    return [{"descricao": cena.get("imagem", ""), "personagens": cena.get("personagens", [])}]


def normalizar(bruto: dict, nicho: str, idioma: str, tema: str | None) -> dict:
    """Ordena os campos para leitura humana e acrescenta a "semente" de cada imagem (mude o número
    no roteiro.json para gerar outra versão daquela imagem)."""
    yt, mini = bruto.get("youtube", {}), bruto.get("miniatura", {})
    return {
        "titulo": _texto(bruto.get("titulo")),
        "nicho": nicho,
        "idioma": idioma,
        "tema": tema or "",
        "resumo": _texto(bruto.get("resumo")),
        "aprendizado": _texto(bruto.get("aprendizado")),
        "clima_musica": _texto(bruto.get("clima_musica")) or "alegre",
        "personagens_do_episodio": [
            {"id": _texto(p.get("id")), "nome": _texto(p.get("nome")),
             "descricao_visual": _texto(p.get("descricao_visual")), "semente": 0}
            for p in bruto.get("personagens_do_episodio", [])
        ],
        "cenas": [
            {"narracao": _texto(c.get("narracao")), "estilo_fala": _texto(c.get("estilo_fala")),
             "imagens": [{"descricao": _texto(t.get("descricao")),
                          "personagens": [_texto(p) for p in t.get("personagens", [])]} for t in tomadas(c)],
             "texto_na_tela": _texto(c.get("texto_na_tela")), "som": _texto(c.get("som")).lower(), "semente": 0}
            for c in bruto.get("cenas", [])
        ],
        "miniatura": {"imagem": _texto(mini.get("imagem")),
                      "personagens": [_texto(p) for p in mini.get("personagens", [])],
                      "texto": _texto(mini.get("texto")), "semente": 0},
        "youtube": {"titulo": _texto(yt.get("titulo")), "descricao": _texto(yt.get("descricao")),
                    "tags": [_texto(t) for t in yt.get("tags", []) if _texto(t)],
                    "hashtags": normalizar_hashtags(yt.get("hashtags", []))},
    }


def gerar(canal: Canal, nicho: str, idioma: str, tema: str | None, minutos: float, molde: str,
          anteriores: list[str], cliente=None, log=print, conto=None) -> dict:
    modelo = os.getenv("GEMINI_MODELO", gemini.MODELO_PADRAO)
    interacao = gemini.criar(
        cliente or gemini.cliente(), log=log, model=modelo,
        input=montar_prompt(canal, nicho, idioma, tema, minutos, molde, anteriores, conto),
        response_format={"type": "text", "mime_type": "application/json", "schema": ESQUEMA},
    )
    roteiro = normalizar(gemini.extrair_json(interacao.output_text), nicho, idioma, tema)
    if conto:
        roteiro["inspiracao"] = conto.id
    roteiro["gerado_por"] = gemini.modelo_usado(interacao, modelo)
    roteiro["gerado_em"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return roteiro


def palavras_da_narracao(roteiro: dict) -> int:
    return sum(len(re.sub(r"<[^>]*>", " ", c.get("narracao", "")).split()) for c in roteiro.get("cenas", []))


def personagens(roteiro: dict, canal: Canal) -> dict[str, dict]:
    """id -> nome, descrição visual e semente de cada personagem disponível no episódio. Os fixos
    vêm sempre do canal.toml, para que um ajuste lá valha para todos os episódios."""
    idioma = roteiro.get("idioma", "")
    todos = {p.id: {"nome": p.nome(idioma), "descricao_visual": p.descricao_visual,
                    "semente": p.semente, "fixo": True} for p in canal.personagens}
    for p in roteiro.get("personagens_do_episodio", []):
        todos.setdefault(p["id"], {"nome": p.get("nome", p["id"]), "descricao_visual": p.get("descricao_visual", ""),
                                   "semente": p.get("semente", 0), "fixo": False})
    return todos


def validar(roteiro: dict, canal: Canal) -> list[str]:
    """Problemas que impediriam a produção (o roteiro.json é editado à mão)."""
    problemas = []
    idioma = roteiro.get("idioma")
    if idioma not in canal.narracao:
        problemas.append(f'idioma "{idioma}" não tem voz no canal.toml (falta [narracao.{idioma}])')

    extras = roteiro.get("personagens_do_episodio", [])
    ids_extras = [p.get("id") for p in extras]
    for p in extras:
        if not p.get("id") or not _texto(p.get("descricao_visual")):
            problemas.append("personagens_do_episodio: todo personagem precisa de id e descricao_visual")
        elif canal.personagem(p["id"]):
            problemas.append(f'personagens_do_episodio: "{p["id"]}" já é um personagem fixo do canal')
    if len(set(ids_extras)) != len(ids_extras):
        problemas.append("personagens_do_episodio: ids repetidos")
    conhecidos = {p.id for p in canal.personagens} | set(ids_extras)

    def checar_personagens(onde: str, ids) -> None:
        for pid in ids:
            if pid not in conhecidos:
                problemas.append(f'{onde}: personagem "{pid}" não existe no canal.toml nem em personagens_do_episodio')

    cenas = roteiro.get("cenas") or []
    if not cenas:
        problemas.append("o roteiro não tem cenas")
    for n, c in enumerate(cenas, 1):
        imagens = tomadas(c)
        if any(not _texto(t.get("descricao")) for t in imagens):
            problemas.append(f"cena {n}: toda imagem precisa de descrição")
        if len(imagens) > MAX_TOMADAS:
            problemas.append(f"cena {n}: mais de {MAX_TOMADAS} imagens numa fala só")
        if not _texto(c.get("narracao")) and not c.get("duracao_s") and not _texto(c.get("som")):
            problemas.append(f'cena {n}: sem narração nem som, defina "duracao_s" (segundos na tela)')
        if len(_texto(c.get("texto_na_tela"))) > LIMITE_TEXTO_NA_TELA:
            problemas.append(f"cena {n}: texto_na_tela com mais de {LIMITE_TEXTO_NA_TELA} caracteres")
        som = _texto(c.get("som"))
        if som and not re.fullmatch(r"[a-z][a-z ]{1,30}", som):
            problemas.append(f'cena {n}: "som" deve ser o nome de um animal em inglês, ex.: "cow"')
        for t in imagens:
            checar_personagens(f"cena {n}", t.get("personagens", []))
    checar_personagens("miniatura", roteiro.get("miniatura", {}).get("personagens", []))

    yt = roteiro.get("youtube", {})
    titulo, descricao = _texto(yt.get("titulo")), _texto(yt.get("descricao"))
    if not 0 < len(titulo) <= 100:
        problemas.append("youtube.titulo precisa ter de 1 a 100 caracteres")
    if len(descricao.encode()) > LIMITE_DESCRICAO_BYTES:
        problemas.append("youtube.descricao passa do limite do YouTube (5.000 bytes; letra acentuada conta 2)")
    if "<" in titulo + descricao or ">" in titulo + descricao:
        problemas.append('o YouTube não aceita "<" nem ">" no título e na descrição')
    if len(",".join(yt.get("tags", []))) > 500:
        problemas.append("youtube.tags passam de 500 caracteres somadas")
    hashtags = yt.get("hashtags", [])
    if any(not HASHTAG.fullmatch(str(h)) for h in hashtags):
        problemas.append('youtube.hashtags: cada uma é "#" + palavra(s) juntas, sem espaço nem pontuação')
    if len(hashtags) > MAX_HASHTAGS:
        problemas.append(f"youtube.hashtags: mais de {MAX_HASHTAGS} (o YouTube ignoraria todas)")
    return problemas
