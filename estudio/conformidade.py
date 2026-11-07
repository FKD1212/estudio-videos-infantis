"""Fase 5a: conferência antes de publicar. Junta checagens automáticas das regras objetivas do
YouTube com uma revisão do Gemini contra os princípios de qualidade para conteúdo infantil e a
política de conteúdo inautêntico. O resultado fica em revisao.json; o episódio só é aprovado sem
bloqueios, e a aprovação vale apenas para a versão revisada (mudou algo, revisa de novo)."""
from __future__ import annotations

import base64
import difflib
import os
import re
import tomllib
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import gemini, sons
from .config import Canal
from .contos import CONTOS
from .episodio import Episodio, slug
from .montagem import PASTA_MUSICA
from .roteiro import LIMITE_DESCRICAO_BYTES, hashtags_do_video, tomadas, validar

BLOQUEIA, AVISO = "bloqueia", "aviso"
LIMITE_MINIATURA_BYTES = 2 * 1024 * 1024
NARRACAO_REPETIDA_BLOQUEIA = 0.30  # fração de trechos de 3 palavras em comum com outro episódio
NARRACAO_REPETIDA_AVISO = 0.15

# Marcas e personagens de outros. No título, descrição, tags ou miniatura é metadado enganoso
# (pegar carona na busca) e risco de direitos autorais. Só nomes inconfundíveis: nomes comuns
# como "Mônica" ou "Elsa" ficam de fora para não acusar personagens próprios.
MARCAS = [
    "peppa", "cocomelon", "bluey", "patrulha canina", "paw patrol", "baby shark", "pinkfong",
    "galinha pintadinha", "mundo bita", "luccas neto", "masha e o urso", "masha and the bear",
    "disney", "pixar", "pokemon*", "mickey", "minnie", "minecraft", "roblox", "homem-aranha",
    "homem aranha", "spider-man", "spiderman", "barbie", "hot wheels", "lego*", "patati patata",
    "bob zoom", "palavra cantada", "toy story", "super wings", "hey duggee", "blippi", "ms rachel",
    "little baby bum", "chuchu tv", "bebefinn", "bolofofos", "turma da monica", "pj masks",
    "super simple songs", "sonic the hedgehog", "dora aventureira", "dora the explorer",
    "daffy", "donald duck", "pato donald", "bugs bunny", "pernalonga", "patolino", "looney tunes",
    "scooby", "garfield", "snoopy", "pikachu", "shrek", "nemo", "simba", "olaf", "winnie the pooh",
    "ursinho pooh", "hello kitty", "smurf*", "minion*", "teletubbies", "bob esponja", "spongebob",
]

# Promessas de saúde, terapia ou desenvolvimento nos metadados: um desenho não trata atraso de fala
# nem autismo, e prometer isso é metadado enganoso (além de tocar em tema de saúde, que o YouTube
# trata com rigor).
PROMESSAS_DE_SAUDE = [
    "speech delay", "speech therapy", "language delay", "autism", "autistic", "adhd", "therapy", "therapeutic",
    "atraso de fala", "atraso na fala", "fonoaudiolog*", "autismo", "autista", "tdah", "terapia", "terapeutic*",
    "cura", "cure", "treatment", "tratamento", "diagnos*", "brain development", "desenvolvimento cerebral",
]

# Palavras que costumam aparecer em conteúdo angustiante (sofrimento, choro, susto). Viram só
# aviso: "medo do escuro" contado com cuidado é ótimo tema; um bichinho sangrando não é.
# Termos com * valem como começo de palavra (machucad* pega machucado e machucada).
SINAIS_DE_ANGUSTIA = [
    "chorando", "chorou", "choro", "lagrima*", "machucad*", "sangue", "sangrando", "ferid*", "morre",
    "morreu", "morte", "morto", "morta", "apavorad*", "terror", "monstro*", "gritando", "socorro",
    "abandonad*", "crying", "cried", "tears", "injur*", "blood*", "bleeding", "wounded", "dies", "died",
    "dead", "death", "terrified", "monster*", "screaming", "help me", "abandoned",
]

PRINCIPIOS = """Princípios do YouTube para conteúdo infantil de qualidade:
- Cuidar de si e dos outros (gentileza, amizade, autocuidado).
- Aprender e despertar curiosidade.
- Criatividade, brincadeira e imaginação.
- Habilidades e experiências da vida (resolver problemas, entender sentimentos).
- Mostrar o mundo (pessoas, lugares e origens diversas).

Conteúdo infantil de baixa qualidade (perde anúncios; canal focado nisso sai do Programa de Parcerias):
- Muito promocional (produtos, marcas, incentivo a comprar).
- Incentiva comportamentos ou atitudes negativas (perigo, bullying, desonestidade, desrespeito).
- Falsamente educativo (título ou miniatura educativos, mas conteúdo errado ou inútil).
- Difícil de acompanhar (confuso, sem começo, meio e fim claros).
- Sensacionalista ou enganoso (falso, exagerado, cheio de palavras-chave de busca).
- Uso estranho de personagens infantis (personagens conhecidos em situações impróprias ou arriscadas).

Política de conteúdo inautêntico (julho de 2026), que tira a monetização de canais com muito:
- Conteúdo genérico, repetitivo ou feito a partir de template, com pouca variação e sem arco narrativo.
- Conteúdo feito para angustiar ou manipular emocionalmente (ex.: animal sofrendo que depois é salvo)."""

_TEXTO = {"type": "string"}
ESQUEMA_REVISAO = {
    "type": "object",
    "properties": {
        "problemas": {"type": "array", "items": {
            "type": "object",
            "properties": {"gravidade": {"type": "string", "enum": [BLOQUEIA, AVISO]}, "regra": _TEXTO,
                           "onde": _TEXTO, "detalhe": _TEXTO, "sugestao": _TEXTO},
            "required": ["gravidade", "regra", "onde", "detalhe", "sugestao"]}},
        "principios_atendidos": {"type": "array", "items": _TEXTO},
        "resumo": _TEXTO,
    },
    "required": ["problemas", "principios_atendidos", "resumo"],
}


@dataclass
class Problema:
    gravidade: str        # bloqueia | aviso
    regra: str
    detalhe: str
    origem: str = "regra"  # regra (checagem automática) | ia (revisão do Gemini)

    def __str__(self) -> str:
        return f"[{self.regra}] {self.detalhe}" + (" (apontado pela IA)" if self.origem == "ia" else "")


# ---------- texto ----------

def _normal(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", sem_acento.lower())).strip()


def _achar(termos: list[str], texto: str) -> list[str]:
    """Termos presentes no texto como palavra inteira (ou começo de palavra, se terminam em *),
    ignorando acentos e maiúsculas."""
    texto = _normal(texto)
    achados = []
    for termo in termos:
        prefixo = termo.endswith("*")
        termo = termo.rstrip("*")
        if re.search(r"\b" + re.escape(_normal(termo)) + ("" if prefixo else r"\b"), texto):
            achados.append(termo)
    return achados


def _trigramas(texto: str) -> set[tuple[str, ...]]:
    palavras = re.findall(r"\w+", _normal(texto))
    return {tuple(palavras[i:i + 3]) for i in range(len(palavras) - 2)}


def _parecido(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _palavras_da_hashtag(tag: str) -> str:
    """#PawPatrol -> "Paw Patrol", para a checagem de marcas e promessas pegar hashtags também."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", tag.lstrip("#")).replace("_", " ")


def _sem_ajustes_automaticos(valor):
    """O roteiro sem sementes e contagem de correções: trocar a semente de uma imagem defeituosa é
    ajuste da máquina, não toque humano."""
    if isinstance(valor, dict):
        return {k: _sem_ajustes_automaticos(v) for k, v in valor.items() if k not in ("semente", "correcoes_automaticas")}
    if isinstance(valor, list):
        return [_sem_ajustes_automaticos(v) for v in valor]
    return valor


def _descricoes(cena: dict) -> str:
    return " ".join(t.get("descricao", "") for t in tomadas(cena))


def _narracao(roteiro: dict) -> str:
    return " ".join(c.get("narracao", "") for c in roteiro.get("cenas", []))


# ---------- música ----------

def carregar_licencas(pasta: Path = PASTA_MUSICA) -> dict[str, dict]:
    arquivo = pasta / "licencas.toml"
    if not arquivo.exists():
        return {}
    with open(arquivo, "rb") as f:
        return tomllib.load(f)


def credito_musica(ep: Episodio, pasta: Path = PASTA_MUSICA) -> str:
    producao = ep.ler(ep.producao) or {}
    musica = producao.get("musica")
    if not musica or producao.get("musica_origem") == "ia":  # trilha própria: nada a creditar
        return ""
    return carregar_licencas(pasta).get(musica, {}).get("credito", "").strip()


CHAMADA_LINK = {"en": "▶ Watch the full episode:", "pt": "▶ Episódio completo:"}


def link_do_episodio(short: Episodio) -> str:
    """Link do episódio de onde o Short saiu (antes do envio dele, um link do mesmo tamanho, para as
    checagens de tamanho da descrição)."""
    registro = short.origem.ler(short.origem.publicacao) or {}
    return registro.get("url") or "https://youtu.be/" + "x" * 11


def descricao_final(ep: Episodio, roteiro: dict, pasta: Path = PASTA_MUSICA, hashtags_canal=()) -> str:
    """Descrição do vídeo + link do episódio completo (Shorts) + crédito da música (se a licença pedir)
    + hashtags na última linha (nos Shorts, #Shorts entre as 3 que aparecem acima do título)."""
    credito = credito_musica(ep, pasta)
    link = f"{CHAMADA_LINK.get(roteiro['idioma'], CHAMADA_LINK['en'])} {link_do_episodio(ep)}" if ep.e_short else ""
    hashtags = hashtags_do_video(roteiro, (["#Shorts"] if ep.e_short else []) + list(hashtags_canal))
    return (roteiro["youtube"]["descricao"] + (f"\n\n{link}" if link else "") + (f"\n\n{credito}" if credito else "")
            + (f"\n\n{' '.join(hashtags)}" if hashtags else ""))


# ---------- checagens automáticas ----------

def checar(ep: Episodio, roteiro: dict, canal: Canal, outros: list[tuple[str, dict]],
           pasta_musica: Path = PASTA_MUSICA) -> list[Problema]:
    """Regras objetivas. `outros` são (nome, roteiro) dos demais episódios do canal."""
    problemas: list[Problema] = []

    def anotar(gravidade: str, regra: str, detalhe: str) -> None:
        problemas.append(Problema(gravidade, regra, detalhe))

    # Entrega completa e atual
    for arquivo, rotulo in ((ep.video, "o vídeo"), (ep.miniatura, "a miniatura"), (ep.legendas, "as legendas")):
        if not arquivo.exists():
            anotar(BLOQUEIA, "entrega", f"falta {rotulo} ({arquivo.name}); rode: python -m estudio produzir")
    if ep.video.exists() and ep.arquivo_roteiro.exists() and ep.arquivo_roteiro.stat().st_mtime > ep.video.stat().st_mtime:
        anotar(AVISO, "entrega", "o roteiro mudou depois da montagem; se mudou alguma cena, rode produzir de novo")
    if ep.miniatura.exists() and ep.miniatura.stat().st_size > LIMITE_MINIATURA_BYTES:
        anotar(BLOQUEIA, "entrega", "a miniatura passa de 2 MB, o limite do YouTube")

    # Metadados
    for problema in validar(roteiro, canal):
        anotar(BLOQUEIA, "metadados", problema)
    yt = roteiro.get("youtube", {})
    titulo = yt.get("titulo", "")
    letras = [c for c in titulo if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.6:
        anotar(AVISO, "metadados", "título quase todo em maiúsculas soa como caça-clique")
    if re.search(r"[!?]{2,}", titulo):
        anotar(AVISO, "metadados", "pontuação repetida no título soa como caça-clique")
    if len(descricao_final(ep, roteiro, pasta_musica, canal.youtube.hashtags).encode()) > LIMITE_DESCRICAO_BYTES:
        anotar(BLOQUEIA, "metadados", "a descrição com o crédito da música passa de 5.000 bytes")

    # Marcas de outros
    vitrine = " | ".join([titulo, yt.get("descricao", ""), " | ".join(yt.get("tags", [])),
                          roteiro.get("miniatura", {}).get("texto", ""),
                          " ".join(_palavras_da_hashtag(h) for h in hashtags_do_video(roteiro, canal.youtube.hashtags))])
    for marca in _achar(MARCAS, vitrine):
        anotar(BLOQUEIA, "marcas de terceiros",
               f'"{marca}" no título, descrição, tags ou miniatura: é metadado enganoso e risco de direitos autorais')
    for termo in _achar(PROMESSAS_DE_SAUDE, vitrine):
        anotar(BLOQUEIA, "promessa de saúde",
               f'"{termo}" no título, descrição, tags ou miniatura: o vídeo não é tratamento nem terapia; tire o termo')
    conteudo = " | ".join(
        [f"{c.get('narracao', '')} {_descricoes(c)} {c.get('texto_na_tela', '')}" for c in roteiro.get("cenas", [])]
        + [f"{p.get('nome', '')} {p.get('descricao_visual', '')}" for p in roteiro.get("personagens_do_episodio", [])])
    for marca in _achar(MARCAS, conteudo):
        anotar(AVISO, "marcas de terceiros", f'"{marca}" aparece nas cenas; personagem ou marca de outro não pode aparecer')

    # Conteúdo angustiante
    for n, cena in enumerate(roteiro.get("cenas", []), 1):
        for termo in _achar(SINAIS_DE_ANGUSTIA, f"{cena.get('narracao', '')} {_descricoes(cena)}"):
            anotar(AVISO, "conteúdo angustiante",
                   f'cena {n} fala em "{termo}": confira se não há sofrimento, susto ou choro dramático')

    # Repetição entre episódios (conteúdo em série)
    minha_narracao = _trigramas(_narracao(roteiro))
    for nome, outro in outros:
        semelhanca = _parecido(minha_narracao, _trigramas(_narracao(outro)))
        if semelhanca >= NARRACAO_REPETIDA_BLOQUEIA:
            anotar(BLOQUEIA, "repetição", f"{semelhanca:.0%} da narração repete o episódio {nome}")
        elif semelhanca >= NARRACAO_REPETIDA_AVISO:
            anotar(AVISO, "repetição", f"{semelhanca:.0%} da narração se parece com o episódio {nome}")
        titulo_outro = outro.get("youtube", {}).get("titulo", "")
        if titulo and difflib.SequenceMatcher(None, _normal(titulo), _normal(titulo_outro)).ratio() >= 0.85:
            anotar(AVISO, "repetição", f'título quase igual ao do episódio {nome} ("{titulo_outro}")')

    # Toque humano
    gerado = ep.ler(ep.roteiro_gerado)
    if gerado is not None and _sem_ajustes_automaticos(gerado) == _sem_ajustes_automaticos(roteiro):
        anotar(AVISO, "toque humano", "o roteiro está exatamente como o Gemini escreveu; revisar e pôr a sua "
                                      "mão na história é o que separa o canal de conteúdo feito em série")

    # Música licenciada: composta por IA e aprovada na avaliação, ou registrada em licencas.toml
    producao = ep.ler(ep.producao) or {}
    musica = producao.get("musica")
    if producao.get("musica_origem") == "ia":
        if not (ep.ler(ep.trilha_info) or {}).get("aprovada"):
            anotar(BLOQUEIA, "música", "a trilha composta por IA não tem a avaliação registrada; rode: "
                                       "python -m estudio trilha")
    elif musica and musica not in carregar_licencas(pasta_musica):
        anotar(BLOQUEIA, "música", f'"{musica}" não está em musica/licencas.toml; registre de onde veio a '
                                   "faixa (sem licença clara, o Content ID desvia a receita do vídeo)")

    # Sons de animais: só CC0 ou gravação própria, registrados no catálogo
    registro = sons.catalogo(sons.PASTA_SONS)
    for nome in producao.get("sons", []):
        if registro.get(slug(nome), {}).get("licenca") not in ("CC0", "própria"):
            anotar(BLOQUEIA, "sons", f'o som "{nome}" não tem licença CC0 ou própria em dados/sons/catalogo.json')
    if producao.get("sons_faltando"):
        anotar(AVISO, "sons", f"as cenas com {', '.join(producao['sons_faltando'])} saíram sem o som do animal "
                              "(falta FREESOUND_API_KEY no .env ou nenhum som passou na conferência)")
    return problemas


# ---------- revisão do Gemini ----------

def prompt_revisao(roteiro: dict, canal: Canal, anteriores: list[str]) -> str:
    cenas = "\n".join(
        f"Cena {n}: narração: {c.get('narracao') or '(sem fala)'} | imagens: "
        + " / ".join(f"[{n}.{k}] {t.get('descricao', '')}" for k, t in enumerate(tomadas(c), 1))
        + f" | texto na tela: {c.get('texto_na_tela') or '-'}"
        + (f" | depois da fala toca o som real de: {c['som']}" if c.get("som") else "")
        for n, c in enumerate(roteiro.get("cenas", []), 1))
    yt, mini = roteiro.get("youtube", {}), roteiro.get("miniatura", {})
    partes = [
        f'Você revisa vídeos do canal infantil "{canal.nome}" (público: {canal.publico}; marcado como '
        '"conteúdo para crianças"; roteiro, voz e imagens feitos com ajuda de IA e revisados pelo dono) '
        "antes da publicação. Avalie se o episódio cumpre as regras abaixo e se pode ser monetizado.",
        "", PRINCIPIOS, "",
        "Verifique também: se o que é ensinado está correto (cores, números, letras, fatos sobre animais e "
        "objetos); se título, descrição, tags e miniatura correspondem ao conteúdo, sem exagero; se há "
        "marcas ou personagens de terceiros; e, nas imagens anexadas, deformações, personagens que mudam de "
        "aparência entre as cenas, texto ilegível ou algo assustador ou impróprio.",
    ]
    idioma = roteiro.get("idioma", "")
    elenco = ([f"- {p.nome(idioma)}: {p.descricao_visual}" for p in canal.personagens]
              + [f"- {p.get('nome', p.get('id', ''))}: {p.get('descricao_visual', '')}"
                 for p in roteiro.get("personagens_do_episodio", [])])
    if elenco:
        partes += ["", "Personagens e como devem aparecer (é assim mesmo, não aponte como problema):", *elenco]
    conto = next((c for c in CONTOS if c.id == roteiro.get("inspiracao")), None)
    if conto:
        partes += ["", f'Este episódio reconta, com os personagens do canal, a ideia do conto tradicional "{conto.titulo}" '
                   f"({conto.origem}, domínio público). Isso é permitido; aponte só se copiar falas ou trechos de "
                   "versões modernas, ou se o ajuste para crianças pequenas não foi feito"
                   + (f" ({conto.ajuste})." if conto.ajuste else ".")]
    if roteiro.get("tipo") == "short":
        partes += ["", f'Este é um YouTube Short vertical (formato {roteiro.get("formato", "")}, sobre "{roteiro.get("animal", "")}") '
                   f'tirado do episódio {roteiro.get("episodio", "")}. A última cena é a chamada fixa para o episódio '
                   "completo, sobre um cartão com a miniatura dele: não é problema. Num Short, julgue também se o "
                   "começo prende nos primeiros segundos e se ele faz sentido sozinho."
                   + (" Confira com rigor se cada curiosidade é verdadeira." if roteiro.get("formato") == "curiosidade"
                      else "")
                   + (f' Letra da canção: {roteiro.get("letra", "")}' if roteiro.get("formato") == "musica" else "")]
    if anteriores:
        partes += ["", "Vídeos anteriores do canal (para julgar repetição):", *[f"- {t}" for t in anteriores[-15:]]]
    partes += [
        "", f"EPISÓDIO: {roteiro.get('titulo', '')}",
        f"Resumo: {roteiro.get('resumo', '')}", f"Aprendizado: {roteiro.get('aprendizado', '')}", "", cenas, "",
        f"Título no YouTube: {yt.get('titulo', '')}", f"Descrição: {yt.get('descricao', '')}",
        f"Tags: {', '.join(yt.get('tags', []))}", f"Texto da miniatura: {mini.get('texto', '')}", "",
        'Liste cada problema com "gravidade": "bloqueia" só quando o episódio claramente viola uma regra; '
        '"aviso" para o que merece atenção. Em "onde", cite a cena, a imagem ou o campo. Seja concreto e '
        'sugira a correção. Em "principios_atendidos", liste os princípios de qualidade que o episódio cumpre.',
    ]
    return "\n".join(partes)


def revisar_com_ia(ep: Episodio, roteiro: dict, canal: Canal, anteriores: list[str], cliente=None,
                   log=print) -> tuple[list[Problema], dict]:
    modelo = os.getenv("GEMINI_MODELO", gemini.MODELO_PADRAO)
    entrada = [{"type": "text", "text": prompt_revisao(roteiro, canal, anteriores)}]
    for arquivo, legenda in ((ep.miniatura, "Miniatura do vídeo:"),
                             (ep.pasta / "revisao_imagens.jpg", "Todas as cenas, numeradas:")):
        if arquivo.exists():
            entrada += [{"type": "text", "text": legenda},
                        {"type": "image", "mime_type": "image/jpeg",
                         "data": base64.b64encode(arquivo.read_bytes()).decode()}]
    interacao = gemini.criar(
        cliente or gemini.cliente(), log=log, model=modelo, input=entrada,
        response_format={"type": "text", "mime_type": "application/json", "schema": ESQUEMA_REVISAO},
    )
    bruto = gemini.extrair_json(interacao.output_text)
    problemas = []
    for item in bruto.get("problemas", []):
        detalhe = f"{item.get('onde', '')}: {item.get('detalhe', '')}".strip(": ")
        if item.get("sugestao"):
            detalhe += f" Sugestão: {item['sugestao']}"
        gravidade = item.get("gravidade") if item.get("gravidade") in (BLOQUEIA, AVISO) else AVISO
        problemas.append(Problema(gravidade, item.get("regra") or "qualidade", detalhe, origem="ia"))
    return problemas, {"modelo": gemini.modelo_usado(interacao, modelo), "resumo": bruto.get("resumo", ""),
                       "principios_atendidos": bruto.get("principios_atendidos", [])}


# ---------- revisão, aprovação ----------

def revisar(ep: Episodio, roteiro: dict, canal: Canal, outros: list[tuple[str, dict]], usar_ia: bool = True,
            cliente=None, log=print, pasta_musica: Path = PASTA_MUSICA) -> dict:
    problemas = checar(ep, roteiro, canal, outros, pasta_musica)
    ia = None
    if usar_ia:
        log("  revisão do Gemini (roteiro, miniatura e imagens)...")
        problemas_ia, ia = revisar_com_ia(ep, roteiro, canal, [o.get("titulo", "") for _, o in outros], cliente, log)
        problemas += problemas_ia
    resultado = {
        "revisado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "assinatura": ep.assinatura_entrega(),
        "com_ia": usar_ia,
        "ia": ia,
        "problemas": [asdict(p) for p in problemas],
    }
    ep.gravar(ep.revisao, resultado)
    return resultado


def aprovar(ep: Episodio, ciente: bool = False, discordo_da_ia: str = "") -> dict:
    """Registra a aprovação humana. Bloqueios das regras automáticas precisam ser corrigidos; um
    bloqueio só da IA pode ser contestado com um motivo, que fica registrado; avisos exigem ciência."""
    revisao = ep.ler(ep.revisao)
    if not revisao or not revisao["com_ia"]:
        raise SystemExit("Antes de aprovar, rode a revisão completa: python -m estudio revisar")
    if revisao["assinatura"] != ep.assinatura_entrega():
        raise SystemExit("O episódio mudou depois da revisão. Rode de novo: python -m estudio revisar")
    problemas = [Problema(**p) for p in revisao["problemas"]]
    bloqueios = [p for p in problemas if p.gravidade == BLOQUEIA and p.origem == "regra"]
    bloqueios_ia = [p for p in problemas if p.gravidade == BLOQUEIA and p.origem == "ia"]
    avisos = [p for p in problemas if p.gravidade == AVISO]

    def lista(itens):
        return "\n  ".join(str(p) for p in itens)

    if bloqueios:
        raise SystemExit(f"Não dá para aprovar; corrija e revise de novo:\n  {lista(bloqueios)}")
    if bloqueios_ia and not discordo_da_ia.strip():
        raise SystemExit(f"A revisão do Gemini bloqueou:\n  {lista(bloqueios_ia)}\nCorrija e revise de novo. Se "
                         'tiver certeza de que a IA errou: aprovar --discordo-da-ia "seu motivo"')
    if avisos and not ciente:
        raise SystemExit(f"Avisos a conferir:\n  {lista(avisos)}\nSe já conferiu, rode: aprovar --ciente")
    aprovacao = {
        "aprovado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "assinatura": revisao["assinatura"],
        "arquivos": ep.estado_arquivos(),
        "modo": "humano",
        "avisos_conferidos": [str(p) for p in avisos],
        "discordo_da_ia": discordo_da_ia.strip() if bloqueios_ia else "",
    }
    ep.gravar(ep.aprovacao, aprovacao)
    return aprovacao


def aprovar_automaticamente(ep: Episodio) -> tuple[bool, str]:
    """Aprovação sem revisão humana (aprovacao = "automatica" no canal.toml): passa se a revisão
    completa desta versão não tiver bloqueios. Avisos ficam registrados; qualquer bloqueio, das
    regras ou da IA, segura o episódio até alguém decidir."""
    revisao = ep.ler(ep.revisao)
    if not revisao or not revisao["com_ia"] or revisao["assinatura"] != ep.assinatura_entrega():
        return False, "falta a revisão completa desta versão"
    problemas = [Problema(**p) for p in revisao["problemas"]]
    bloqueios = [p for p in problemas if p.gravidade == BLOQUEIA]
    if bloqueios:
        return False, "bloqueado: " + "; ".join(str(p) for p in bloqueios)
    avisos = [str(p) for p in problemas if p.gravidade == AVISO]
    ep.gravar(ep.aprovacao, {
        "aprovado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "assinatura": revisao["assinatura"],
        "arquivos": ep.estado_arquivos(),
        "modo": "automatico",
        "avisos_conferidos": avisos,
        "discordo_da_ia": "",
    })
    return True, f"aprovado automaticamente ({len(avisos)} aviso(s) registrados em aprovacao.json)"


def aprovado(ep: Episodio) -> bool:
    aprovacao = ep.ler(ep.aprovacao)
    if not aprovacao:
        return False
    if aprovacao.get("arquivos") == ep.estado_arquivos():  # nada mudou desde a aprovação: não relê o vídeo
        return True
    return aprovacao["assinatura"] == ep.assinatura_entrega()
