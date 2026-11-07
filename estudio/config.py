"""Configuração: chaves de API (.env), nichos a pesquisar (nichos.toml) e a identidade do
canal (canal.toml)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"
CACHE = RAIZ / ".cache"

load_dotenv(RAIZ / ".env")

# idioma -> regionCode usado na busca
IDIOMAS = {"pt": "BR", "en": "US"}


@dataclass
class Nicho:
    nome: str
    termos: dict[str, list[str]]  # idioma -> termos de busca


@dataclass
class ConfigPesquisa:
    janela_dias: int = 365
    resultados_por_termo: int = 50
    duracao: str = "any"
    apenas_infantil: bool = True
    canal_pequeno_max_inscritos: int = 100_000
    outlier_minimo: float = 3.0
    videos_base_canal: int = 50
    nichos: list[Nicho] = field(default_factory=list)


def carregar_nichos(caminho: Path) -> ConfigPesquisa:
    with open(caminho, "rb") as f:
        dados = tomllib.load(f)
    nichos = [
        Nicho(n["nome"], {idioma: n[idioma] for idioma in IDIOMAS if n.get(idioma)})
        for n in dados.get("nicho", [])
    ]
    return ConfigPesquisa(**dados.get("pesquisa", {}), nichos=nichos)


@dataclass
class Personagem:
    id: str
    nomes: dict[str, str]   # idioma -> nome
    descricao_visual: str   # em inglês, para o gerador de imagens
    personalidade: str = ""
    semente: int = 0        # mude para gerar outra versão da imagem de referência

    def nome(self, idioma: str) -> str:
        return self.nomes.get(idioma) or next(iter(self.nomes.values()), self.id)


@dataclass
class Voz:
    voz: str     # voz pré-pronta do Gemini TTS
    idioma: str  # código BCP-47, ex.: pt-BR
    estilo: str  # como a narradora fala, em linguagem natural
    motor: str = "gemini"         # gemini (com reserva local quando a cota acaba) | local (sempre Kokoro)
    voz_local: str = ""           # voz do Kokoro, ex.: af_heart (inglês), pf_dora (português)
    velocidade_local: float = 0.9  # um pouco mais devagar, para crianças pequenas


@dataclass
class ConfigPublicacao:
    horarios: list[str] = field(default_factory=lambda: ["ter 10:00", "qui 10:00", "sab 09:00"])
    fuso: str = "America/Sao_Paulo"
    max_por_semana: int = 3
    categoria: str = "27"              # 27 = Educação, 1 = Filmes e animação
    conteudo_sintetico: bool = False   # marcação de conteúdo alterado ou sintético realista
    aprovacao: str = "humana"          # humana (comando aprovar) | automatica (aprova se a revisão não bloquear)
    antecedencia_dias: int = 14        # só envia episódios cujo horário esteja a até tantos dias (o resto fica no estoque)


@dataclass
class ConfigMusica:
    fonte: str = "ia"   # ia (trilha original gerada na placa) | biblioteca (faixas em musica/)
    candidatos: int = 3  # versões geradas por episódio; fica a mais bem avaliada


@dataclass
class ConfigAutomacao:
    ligada: bool = False
    nicho: str = "animais"
    idioma: str = "en"
    minutos: float = 4
    manter_agendados: int = 3    # episódios prontos ou agendados a manter na fila
    novos_por_execucao: int = 1  # quantos episódios novos, no máximo, a cada execução diária
    continua: bool = False       # produção pelo produtor contínuo (comando produtor); a tarefa diária só envia
    semanas_adiantadas: int = 1  # o produtor cobre os horários até o fim desta semana + N e para
    roteiros_adiantados: int = 2  # roteiros escritos à frente, esperando a placa de vídeo
    espaco_minimo_gb: float = 20  # abaixo disso de espaço livre, a produção espera e avisa


@dataclass
class ConfigYouTube:
    """Como o canal se apresenta: audiência infantil, descrição, palavras-chave e playlists."""
    feito_para_criancas: bool = True
    idioma: str = "en"
    descricao: str = ""
    palavras_chave: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)  # entram em todo vídeo, depois das do episódio
    playlists: dict[str, str] = field(default_factory=dict)  # nicho -> título da playlist da série


TOM_PADRAO = (
    "leve e bem-humorado do começo ao fim, para fazer a criança rir: surpresas bobas, mal-entendidos gentis, "
    "sons engraçados, exageros (um espirro gigante que espalha as folhas), um bordão que se repete três vezes "
    "com uma virada na terceira, e a dupla cômica dos personagens fixos (use as personalidades deles). Pelo "
    "menos dois momentos feitos para rir. Ninguém ri DE ninguém, todos riem juntos; nada de dor, susto ou "
    "zombaria"
)


@dataclass
class ConfigRoteiro:
    """O jeito das histórias: o tom de todo roteiro e se cada episódio parte de um conto tradicional de
    domínio público (estudio/contos.py), recontado do jeito do canal."""
    tom: str = TOM_PADRAO
    contos: bool = True


@dataclass
class ConfigShorts:
    """Shorts verticais tirados de cada episódio: um animal por Short, num formato escolhido pelo
    engajamento medido (pergunta, curiosidade ou música), com chamada para o episódio completo."""
    ligado: bool = False
    horarios: list[str] = field(default_factory=lambda: [f"{d} 17:00" for d in ("seg", "ter", "qua", "qui", "sex", "sab", "dom")])
    max_por_semana: int = 7
    por_episodio: int = 3      # Shorts no máximo de cada episódio (um animal diferente em cada, se der)
    formatos: list[str] = field(default_factory=lambda: ["pergunta", "curiosidade", "musica"])
    segundos: int = 40         # duração-alvo; o YouTube aceita até 3 min, mas os curtos seguram mais
    playlist: str = ""         # playlist só de Shorts (vazio: nenhuma)


@dataclass
class Canal:
    nome: str
    publico: str
    estilo_visual: str
    narracao: dict[str, Voz]  # idioma -> voz
    personagens: list[Personagem]
    fonte: str = ""
    publicacao: ConfigPublicacao = field(default_factory=ConfigPublicacao)
    musica: ConfigMusica = field(default_factory=ConfigMusica)
    automacao: ConfigAutomacao = field(default_factory=ConfigAutomacao)
    youtube: ConfigYouTube = field(default_factory=ConfigYouTube)
    shorts: ConfigShorts = field(default_factory=ConfigShorts)
    roteiro: ConfigRoteiro = field(default_factory=ConfigRoteiro)

    def personagem(self, id_: str) -> Personagem | None:
        return next((p for p in self.personagens if p.id == id_), None)


def carregar_canal(caminho: Path) -> Canal:
    with open(caminho, "rb") as f:
        dados = tomllib.load(f)
    publicacao = dados.get("publicacao", {})
    if "categoria" in publicacao:
        publicacao["categoria"] = str(publicacao["categoria"])
    return Canal(
        **dados["canal"],
        narracao={idioma: Voz(**v) for idioma, v in dados.get("narracao", {}).items()},
        personagens=[Personagem(**p) for p in dados.get("personagem", [])],
        publicacao=ConfigPublicacao(**publicacao),
        musica=ConfigMusica(**dados.get("musica", {})),
        automacao=ConfigAutomacao(**dados.get("automacao", {})),
        youtube=ConfigYouTube(**dados.get("youtube", {})),
        shorts=ConfigShorts(**dados.get("shorts", {})),
        roteiro=ConfigRoteiro(**dados.get("roteiro", {})),
    )


def chave(nome: str) -> str:
    valor = os.getenv(nome)
    if not valor:
        raise SystemExit(f"Defina {nome} no arquivo .env (veja .env.example).")
    return valor
