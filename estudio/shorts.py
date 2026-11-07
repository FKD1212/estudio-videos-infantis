"""Shorts verticais tirados dos episódios. Cada Short pega um animal do episódio e conta de um
jeito próprio, num dos formatos:

- pergunta: o som real do bicho toca logo no primeiro segundo, sobre uma pista (sombra, rabinho);
  a criança tenta adivinhar e vem a revelação;
- curiosidade: abre com um fato verdadeiro e surpreendente sobre o bicho;
- musica: uma canção original, curta, para cantar junto (cantada pelo ACE-Step).

O formato de cada Short novo é escolhido pelo engajamento medido dos Shorts já publicados
(retenção, curtidas, compartilhamentos e inscritos por visualização), com a mesma amostragem
que escolhe o estilo das trilhas: o que engaja mais passa a sair mais, e os outros continuam
sendo testados de vez em quando.

Imagens novas na vertical (9:16), os mesmos personagens do episódio, e no fim a chamada para
o episódio completo: uma frase narrada sobre um cartão com a miniatura dele (cards e telas finais
não existem em conteúdo para crianças, e a API não define o "vídeo relacionado" do Short), mais
o link na descrição. O Short só vai ao ar depois do episódio de onde saiu."""
from __future__ import annotations

import os
import re
import shutil
from collections import Counter
from pathlib import Path

from . import conformidade, episodio, fluxo, gemini, metricas, montagem, musica, sons
from .config import Canal
from .episodio import Episodio, Manifesto
from .roteiro import LINGUA, _objeto, _TEXTO, _TEXTOS, normalizar_hashtags, tomadas

FORMATOS = ("pergunta", "curiosidade", "musica")
PALAVRAS_POR_SEGUNDO = 2.0   # voz calma, para crianças pequenas
CHAMADA_S = 5                # frase final + cartão do episódio completo
GANCHO_CANCAO_S = 3          # frase falada antes da canção
MIN_CANCAO_S = 20
ANTERIORES = 12              # aberturas de Shorts anteriores que o roteirista vê, para variar
CHAMADA_CARTAO = {"en": "WATCH THE\nFULL EPISODE", "pt": "ASSISTA AO\nEPISÓDIO COMPLETO"}
GANCHO_CANCAO = {"en": "Sing along with {dupla}!", "pt": "Vamos cantar com {dupla}!"}
CENA_MUDA_S = 2.5

ESQUEMA = _objeto(
    titulo=_TEXTO,
    resumo=_TEXTO,
    aprendizado=_TEXTO,
    cenas={"type": "array", "items": _objeto(
        narracao=_TEXTO, estilo_fala=_TEXTO, letra=_TEXTO,
        imagens={"type": "array", "items": _objeto(descricao=_TEXTO, personagens=_TEXTOS)},
        texto_na_tela=_TEXTO, som=_TEXTO)},
    letra=_TEXTO,
    chamada=_TEXTO,
    youtube=_objeto(titulo=_TEXTO, descricao=_TEXTO, tags=_TEXTOS, hashtags=_TEXTOS),
)


# ---------- o que vira Short ----------

def animais(roteiro: dict) -> list[str]:
    """Animais do episódio que têm som real (os que dão um bom Short), na ordem em que aparecem."""
    return list(dict.fromkeys(c["som"] for c in roteiro.get("cenas", []) if c.get("som")))


def do_episodio(origem: Episodio, shorts: list[Episodio] | None = None) -> list[Episodio]:
    todos = shorts if shorts is not None else episodio.listar_shorts()
    return [s for s in todos if s.origem.pasta == origem.pasta]


def desempenho_por_formato(shorts: list[Episodio]) -> dict[str, list[float]]:
    relativo = metricas.engajamento_relativo(shorts)
    por_formato: dict[str, list[float]] = {}
    for s in shorts:
        if s.nome in relativo:
            por_formato.setdefault(s.carregar_roteiro().get("formato", ""), []).append(relativo[s.nome])
    return por_formato


def proxima_ideia(origem: Episodio, canal: Canal, shorts: list[Episodio]) -> tuple[str, str] | None:
    """(animal, formato) do próximo Short deste episódio, ou None se ele já deu o que tinha.
    O formato vem do engajamento medido; o animal é o menos usado nos Shorts do episódio."""
    feitos = [s.carregar_roteiro() for s in do_episodio(origem, shorts)]
    bichos = animais(origem.carregar_roteiro())
    formatos = [f for f in canal.shorts.formatos if f in FORMATOS]
    if len(feitos) >= canal.shorts.por_episodio or not bichos or not formatos:
        return None
    usados = {(r.get("animal"), r.get("formato")) for r in feitos}
    vezes = Counter(r.get("animal") for r in feitos)
    escolhido = metricas.escolher(formatos, desempenho_por_formato(shorts), f"{origem.nome}:{len(feitos)}")
    for formato in [escolhido, *[f for f in formatos if f != escolhido]]:
        livres = [b for b in bichos if (b, formato) not in usados]
        if livres:
            return min(livres, key=lambda b: (vezes[b], bichos.index(b))), formato
    return None


def fontes(episodios: list[Episodio], canal: Canal, shorts: list[Episodio]) -> list[tuple[Episodio, tuple[str, str]]]:
    """Episódios prontos (aprovados ou já enviados) que ainda rendem Shorts, do mais antigo para o
    mais novo: os Shorts saem logo depois do episódio deles."""
    saida = []
    for ep in episodios:
        if not (ep.publicacao.exists() or conformidade.aprovado(ep)) or not ep.miniatura.exists():
            continue
        ideia = proxima_ideia(ep, canal, shorts)
        if ideia:
            saida.append((ep, ideia))
    return saida


# ---------- roteiro ----------

def _cenas_do_animal(roteiro: dict, animal: str) -> str:
    linhas = []
    for n, c in enumerate(roteiro.get("cenas", []), 1):
        texto = f"{c.get('narracao', '')} {' '.join(t.get('descricao', '') for t in tomadas(c))}"
        if c.get("som") == animal or animal in texto.lower():
            linhas.append(f"- cena {n}: {c.get('narracao', '')} | imagens: "
                          + " / ".join(t.get("descricao", "") for t in tomadas(c)))
    return "\n".join(linhas[:6])


def instrucoes_do_formato(formato: str, animal: str, lingua: str, segundos: int, dupla: str = "Lulu and Benny") -> str:
    if formato == "pergunta":
        return (
            f'FORMATO PERGUNTA (adivinhe o bicho pelo som). Cena 1: SEM narração ("narracao" vazio) e com "som": '
            f'"{animal}" — o som real toca logo no primeiro segundo; a imagem mostra só uma pista (sombra, silhueta, '
            'rabinho atrás de um arbusto), sem revelar o bicho; "texto_na_tela": pergunta curtíssima (ex.: "WHO IS '
            'IT?"). Cena 2: a narradora pergunta quem faz esse som e dá uma pista, terminando com <long pause> para '
            'a criança responder; imagem com outra pista, ainda sem revelar. Nas imagens antes da revelação, '
            '"personagens" só pode ter Lulu ou Benny, e a descrição não cita o nome nem mostra o rosto do bicho: '
            'só a pista. Cena 3: a revelação, animada (ex.: '
            f'"It\'s a duck!"), com "som": "{animal}" de novo, o bicho bem visível e "texto_na_tela" com o nome dele. '
            "Cena 4 (opcional): Lulu ou Benny imitam o som, de um jeito engraçado e gentil. Título, emoji e "
            "hashtags não podem entregar a resposta (nada de nome ou emoji do bicho).")
    if formato == "curiosidade":
        return (
            "FORMATO CURIOSIDADE. Cena 1: abre com uma pergunta-surpresa sobre um fato verdadeiro e encantador do "
            f'bicho ("Did you know...?"), com uma imagem que prende o olhar. Cenas seguintes: 2 fatos simples, '
            "verdadeiros e fáceis para crianças de 2 a 5 anos (sem números difíceis), cada um com uma imagem que "
            f'mostra o fato; em uma delas, "som": "{animal}". Só fatos amplamente conhecidos e corretos: na dúvida, '
            "escolha outro.")
    cancao_s = max(MIN_CANCAO_S, segundos - GANCHO_CANCAO_S - CHAMADA_S)
    return (
        "FORMATO MÚSICA (canção para cantar junto). Cena 1: gancho falado curtíssimo (até 8 palavras, ex.: "
        f'"Sing along with {dupla}!"), sem som. Depois, de 4 a 6 cenas SEM narração ("narracao" vazio), '
        'uma para cada par de versos: "letra" com os versos cantados naquela cena e uma imagem que mostra o que '
        f'eles dizem. Em "letra" (fora das cenas), a canção completa para o cantor: original, em {lingua}, cerca '
        f"de {round(cancao_s / 2.5)} versos curtos e simples para cerca de {cancao_s} segundos, com um refrão que se "
        f'repete e o som do bicho escrito como se canta (ex.: "Quack, quack!"); marque as partes com [verse] e '
        "[chorus]. O título anuncia a canção (é para cantar junto, não para adivinhar). Nada de melodia ou "
        "letra de música existente.")


def montar_prompt(canal: Canal, origem: dict, animal: str, formato: str, anteriores: list[str]) -> str:
    idioma = origem["idioma"]
    lingua, segundos = LINGUA[idioma], canal.shorts.segundos
    palavras = round((segundos - CHAMADA_S - 6) * PALAVRAS_POR_SEGUNDO)
    elenco = "\n".join(
        [f'- id "{p.id}": {p.nome(idioma)} — {p.personalidade}. Aparência: {p.descricao_visual}' for p in canal.personagens]
        + [f'- id "{p["id"]}": {p["nome"]} — {p["descricao_visual"]}' for p in origem.get("personagens_do_episodio", [])])
    titulo = origem.get("youtube", {}).get("titulo") or origem.get("titulo", "")
    dupla = " & ".join(p.nome(idioma) for p in canal.personagens[:2])
    partes = [
        f'Você escreve YouTube Shorts verticais para "{canal.nome}", canal infantil para crianças de {canal.publico}. '
        f'Este Short sai do episódio "{titulo}" ({origem.get("resumo", "")}) e é sobre o animal "{animal}". Ele '
        "precisa funcionar sozinho para quem nunca viu o canal.",
        "", "Como o animal aparece no episódio:", _cenas_do_animal(origem, animal) or "-",
        "", "Personagens (use pelo id, com a mesma aparência; se o episódio tem um personagem que é este animal, "
            "use esse personagem):", elenco,
        "", instrucoes_do_formato(formato, animal, lingua, segundos, dupla),
        "",
        "Regras:",
        f"0. Tom: {canal.roteiro.tom}. Num Short, a graça tem de chegar rápido.",
        f"1. Cerca de {segundos} segundos no total, de 3 a 7 cenas. Narração em {lingua}, frases curtas, no máximo "
        f"~{palavras} palavras somadas. O campo \"narracao\" é lido literalmente pela voz: sem emoji, sem nome de "
        "quem fala; pode usar <short pause> e <long pause>.",
        "2. Gancho nos 2 primeiros segundos: som, pergunta ou imagem que faça a criança olhar. Nada de "
        '"Hi friends" ou "welcome back".',
        '3. "imagens": 1 ou 2 por cena (troca a cada 2-3 segundos), "descricao" EM INGLÊS para um quadro VERTICAL '
        "9:16, com o assunto grande e centralizado (closes funcionam melhor) e sempre com o cenário do episódio "
        "(onde estão, cores, luz), nunca fundo liso, citando os personagens pelo nome; "
        'não descreva o estilo de desenho nem peça texto escrito. "personagens": ids de quem aparece.',
        f'4. "texto_na_tela": vazio ou até 3 palavras em {lingua}, em maiúsculas. "som": só o animal deste Short, '
        'nas cenas indicadas pelo formato; vazio nas outras. "letra": vazio fora do formato música.',
        "5. Nada assustador, triste ou perigoso; nada de marcas, personagens de outros, promessas de saúde ou "
        "pedidos para comprar.",
        f'6. "chamada": uma frase gentil (até 14 palavras), em {lingua}, convidando a assistir à história completa '
        f'com {dupla} no canal (pelo nome, nunca pelo id). Sem pedir para se inscrever ou curtir e sem falar em link ou clique. Varie o '
        "jeito de dizer.",
        f'7. "youtube": título em {lingua} com até 60 caracteres, curioso e honesto (pode ter 1 emoji); descrição '
        "de 1 a 2 frases para os pais, sem links (o link do episódio completo entra sozinho); de 5 a 10 tags; e de 2 "
        f'a 4 hashtags em {lingua} ("#" + palavras juntas), da mais importante para a menos.',
        f'8. "titulo": nome curto de trabalho em {lingua}; "resumo" e "aprendizado": internos, em português.',
    ]
    if anteriores:
        partes += ["", "Aberturas de Shorts anteriores do canal — comece de um jeito diferente:",
                   *[f"- {a}" for a in anteriores[-ANTERIORES:]]]
    partes += ["", "Responda apenas com o JSON pedido."]
    return "\n".join(partes)


def _abertura(roteiro: dict) -> str:
    primeira = next((c.get("narracao") or c.get("texto_na_tela") for c in roteiro.get("cenas", [])
                     if c.get("narracao") or c.get("texto_na_tela")), "")
    return f'{roteiro.get("formato", "")}: "{roteiro.get("youtube", {}).get("titulo", "")}" — {primeira[:90]}'


def marcar_pistas(cenas: list[dict], canal: Canal) -> None:
    """No formato pergunta, as imagens antes da revelação (a segunda cena com o som) são só pistas:
    a imagem de referência do bicho faria o gerador desenhá-lo inteiro. Ficam só os personagens
    fixos, e a tomada é marcada para não puxar personagens citados pelo nome."""
    com_som = [n for n, c in enumerate(cenas) if c.get("som")]
    revelacao = com_som[1] if len(com_som) > 1 else len(cenas) - 1
    nomes = {p.id: {p.id, *p.nomes.values()} for p in canal.personagens}
    for c in cenas[:revelacao]:
        for t in tomadas(c):
            # Personagem fixo só entra se a descrição o mostra: "Lulu" listada numa imagem de patinhas
            # atrás do arbusto vira uma coelha com pés de pato.
            t["personagens"] = [p for p in t.get("personagens", []) if p in nomes and any(
                re.search(rf"\b{re.escape(n)}\b", t.get("descricao", ""), re.I) for n in nomes[p])]
            t["pista"] = True


def normalizar(bruto: dict, origem_ep: Episodio, origem: dict, animal: str, formato: str, canal: Canal) -> dict:
    """Roteiro no mesmo formato dos episódios (a produção é a mesma), mais a chamada fixa no fim."""
    yt = bruto.get("youtube", {})
    cenas = []
    for c in bruto.get("cenas", []):
        letra = str(c.get("letra") or "").strip() if formato == "musica" else ""
        narracao = "" if letra else str(c.get("narracao") or "").strip()
        som = str(c.get("som") or "").strip().lower()
        cenas.append({
            "narracao": narracao, "estilo_fala": str(c.get("estilo_fala") or "").strip(),
            **({"letra": letra} if letra else {}),
            "imagens": [{"descricao": str(t.get("descricao") or "").strip(),
                         "personagens": [str(p).strip() for p in t.get("personagens", [])]} for t in tomadas(c)][:2],
            "texto_na_tela": str(c.get("texto_na_tela") or "").strip()[:40],
            "som": som if som == animal else "", "semente": 0,
        })
    extra = {}
    if formato == "pergunta":
        marcar_pistas(cenas, canal)
    if formato == "musica":
        cantadas = [c for c in cenas if c.get("letra")]
        cancao_s = max(MIN_CANCAO_S, canal.shorts.segundos - GANCHO_CANCAO_S - CHAMADA_S)
        for c in cantadas:  # as imagens acompanham a canção, que é composta com exatamente essa duração
            c["duracao_s"] = round(cancao_s / len(cantadas), 2)
        primeira_cantada = next((n for n, c in enumerate(cenas, 1) if c.get("letra")), 1)
        extra = {"letra": str(bruto.get("letra") or "").strip(), "musica_desde_cena": primeira_cantada}
        if primeira_cantada > 1 and not cenas[0]["narracao"]:  # o roteirista esqueceu o gancho falado
            dupla = " & ".join(p.nome(origem["idioma"]) for p in canal.personagens[:2])
            cenas[0]["narracao"] = GANCHO_CANCAO.get(origem["idioma"], GANCHO_CANCAO["en"]).format(dupla=dupla)
    for c in cenas:  # cena muda, sem som nem canto: fica um instante na tela em vez de travar a produção
        if not (c["narracao"] or c["som"] or c.get("letra") or c.get("duracao_s")):
            c["duracao_s"] = CENA_MUDA_S
    cenas.append({
        "narracao": str(bruto.get("chamada") or "").strip(), "estilo_fala": "warm, inviting",
        "imagens": [{"descricao": "invitation card with the full episode's thumbnail", "personagens": [], "fixa": True}],
        "texto_na_tela": "", "som": "", "semente": 0,
    })
    r = {
        "titulo": str(bruto.get("titulo") or "").strip(),
        "tipo": "short", "formato": formato, "animal": animal, "formato_video": "vertical",
        "episodio": origem_ep.nome, "nicho": origem.get("nicho", ""), "idioma": origem["idioma"], "tema": "",
        "resumo": str(bruto.get("resumo") or "").strip(),
        "aprendizado": str(bruto.get("aprendizado") or "").strip(),
        "clima_musica": origem.get("clima_musica", "alegre"),
        "personagens_do_episodio": origem.get("personagens_do_episodio", []),
        "cenas": cenas, **extra,
        "miniatura": {"imagem": "", "personagens": [], "texto": "", "semente": 0},
        "youtube": {"titulo": str(yt.get("titulo") or "").strip(), "descricao": str(yt.get("descricao") or "").strip(),
                    "tags": [str(t).strip() for t in yt.get("tags", []) if str(t).strip()],
                    "hashtags": normalizar_hashtags(yt.get("hashtags", []))[:4]},
    }
    nomes_certos(r, canal)
    return r


def nomes_certos(r: dict, canal: Canal) -> None:
    """O roteirista às vezes chama um personagem pelo id ("Bento") em vez do nome no idioma do vídeo
    ("Benny"): corrige no que é falado, cantado ou publicado."""
    idioma = r["idioma"]
    trocas = [(re.compile(rf"\b{re.escape(p.id)}\b", re.I), p.nome(idioma)) for p in canal.personagens
              if p.nome(idioma).lower() != p.id.lower()]

    def certo(texto: str) -> str:
        for padrao, nome in trocas:
            texto = padrao.sub(nome, texto)
        return texto

    for c in r["cenas"]:
        for campo in ("narracao", "letra", "texto_na_tela"):
            if c.get(campo):
                c[campo] = certo(c[campo])
        for t in tomadas(c):  # o gerador acha o personagem pelo nome ou pelo id; a revisão estranha a troca
            t["descricao"] = certo(t.get("descricao", ""))
    if r.get("letra"):
        r["letra"] = certo(r["letra"])
    for campo in ("titulo", "descricao"):
        r["youtube"][campo] = certo(r["youtube"][campo])


def problemas_do_formato(roteiro: dict) -> list[str]:
    problemas = []
    if roteiro["formato"] == "musica":
        if not roteiro.get("letra") or not any(c.get("letra") for c in roteiro["cenas"]):
            problemas.append("formato música sem letra")
    elif not any(c.get("som") for c in roteiro["cenas"]):
        problemas.append(f'nenhuma cena toca o som de "{roteiro["animal"]}"')
    if not roteiro["cenas"][-1]["narracao"]:
        problemas.append("falta a frase da chamada para o episódio completo")
    return problemas


def novo_short(origem: Episodio, animal: str, formato: str, canal: Canal, shorts: list[Episodio],
               cliente=None, log=print) -> Episodio:
    """Escreve o roteiro do Short (uma chamada ao Gemini, ou ao modelo local quando a cota acaba)."""
    r_origem = origem.carregar_roteiro()
    anteriores = [_abertura(s.carregar_roteiro()) for s in shorts]
    modelo = os.getenv("GEMINI_MODELO", gemini.MODELO_PADRAO)
    log(f"Escrevendo o Short ({formato}, {animal}) de {origem.nome}...")
    interacao = gemini.criar(
        cliente or gemini.cliente(), log=log, model=modelo,
        input=montar_prompt(canal, r_origem, animal, formato, anteriores),
        response_format={"type": "text", "mime_type": "application/json", "schema": ESQUEMA})
    r = normalizar(gemini.extrair_json(interacao.output_text), origem, r_origem, animal, formato, canal)
    r["gerado_por"] = gemini.modelo_usado(interacao, modelo)
    problemas = problemas_do_formato(r)
    if problemas:
        raise RuntimeError("o roteiro do Short veio incompleto: " + "; ".join(problemas))
    numero = len(do_episodio(origem, shorts)) + 1
    short = episodio.novo_short(origem, numero, formato, animal)
    short.salvar_roteiro(r)
    short.gravar(short.roteiro_gerado, r)
    log(f'Short "{r["youtube"]["titulo"]}" — {len(r["cenas"])} cenas ({r["gerado_por"]}): {short.pasta}')
    return short


# ---------- produção ----------

def copiar_personagens(short: Episodio) -> None:
    """As imagens de referência dos personagens do episódio valem para o Short (mesma aparência,
    sem gerar de novo)."""
    origem, destino = Manifesto(short.origem.imagens), Manifesto(short.imagens)
    for nome, assinatura_ in origem.dados.items():
        if nome.startswith("personagem_") and (origem.pasta / nome).exists() and not destino.atual(nome, assinatura_):
            short.imagens.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origem.pasta / nome, destino.pasta / nome)
            destino.registrar(nome, assinatura_)


def copiar_trilha(short: Episodio) -> None:
    """Pergunta e curiosidade usam a trilha (já avaliada) do episódio como fundo."""
    origem = short.origem
    if not origem.trilha.exists():
        raise SystemExit(f"O episódio {origem.nome} não tem trilha para o Short usar.")
    if not short.trilha.exists() or short.trilha.stat().st_size != origem.trilha.stat().st_size:
        short.pasta_trilha.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origem.trilha, short.trilha)
        shutil.copyfile(origem.trilha_info, short.trilha_info)


def cartao_chamada(short: Episodio, roteiro: dict, canal: Canal) -> Path:
    """O cartão da chamada final: a miniatura do episódio completo sobre ela mesma desfocada, com o
    convite escrito em cima e um botão de "play" embaixo (a parte de baixo fica livre: no app, é
    onde aparecem título e botões do Short)."""
    from PIL import Image, ImageDraw, ImageFilter

    n = len(roteiro["cenas"])
    saida = short.imagens / montagem.arquivo_imagem(n, 1)
    miniatura = short.origem.miniatura
    if saida.exists() and saida.stat().st_mtime >= miniatura.stat().st_mtime:
        return saida
    largura, altura = 864, 1536
    with Image.open(miniatura) as img:
        mini = img.convert("RGB")
    escala = altura / mini.height
    fundo = mini.resize((round(mini.width * escala), altura), Image.LANCZOS)
    esquerda = (fundo.width - largura) // 2
    fundo = fundo.crop((esquerda, 0, esquerda + largura, altura)).filter(ImageFilter.GaussianBlur(28))
    fundo = Image.blend(fundo, Image.new("RGB", fundo.size, (20, 20, 40)), 0.45)
    w = 700  # cabe inteiro mesmo com o movimento de câmera (zoom de 10%)
    frente = mini.resize((w, round(mini.height * w / mini.width)), Image.LANCZOS)
    mascara = Image.new("L", frente.size, 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, *frente.size), radius=36, fill=255)
    x, y = (largura - w) // 2, 520
    desenho = ImageDraw.Draw(fundo)
    desenho.rounded_rectangle((x - 8, y - 8, x + w + 8, y + frente.height + 8), radius=42, fill="white")
    fundo.paste(frente, (x, y), mascara)
    texto = CHAMADA_CARTAO.get(roteiro["idioma"], CHAMADA_CARTAO["en"])
    montagem._escrever(fundo, texto, (60, 250, largura - 60, 480), canal, 96)
    cy, r = y + frente.height + 150, 78  # botão de play
    desenho.ellipse((largura // 2 - r, cy - r, largura // 2 + r, cy + r), fill=(230, 40, 40))
    desenho.polygon([(largura // 2 - 26, cy - 40), (largura // 2 - 26, cy + 40), (largura // 2 + 44, cy)], fill="white")
    saida.parent.mkdir(parents=True, exist_ok=True)
    fundo.save(saida)
    return saida


def duracao_cancao(roteiro: dict) -> float:
    return sum(float(c.get("duracao_s") or 0) for c in roteiro["cenas"] if c.get("letra"))


def etapas_na_placa(short: Episodio, r: dict, canal: Canal, log=print) -> None:
    """Imagens verticais, narração, sons e (no formato música) a canção."""
    fluxo.validar(short, r, canal)
    copiar_personagens(short)
    cartao_chamada(short, r, canal)
    fluxo.gerar_imagens(short, r, canal, log)
    fluxo.narrar(short, r, canal, log)
    sons.garantir((c.get("som") for c in r["cenas"]), log=log)
    if r.get("formato") == "musica":
        musica.gerar_cancao(short, r, canal, duracao_cancao(r), episodio.listar_shorts(), log=log)
        info = short.ler(short.trilha_info)
        log(f"Canção: estilo {info['estilo']['id']}, versão {info['escolhida']} — {short.trilha}")
    else:
        copiar_trilha(short)


def finalizar(short: Episodio, r: dict, canal: Canal, log=print) -> None:
    """Montagem vertical, revisão e aprovação automática (a mesma dos episódios)."""
    fluxo.finalizar(short, r, canal, log)
