"""Coleta vídeos infantis por nicho, mede quanto cada um supera o normal do próprio canal
(score de outlier) e resume a oportunidade de cada nicho para um canal novo."""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import IDIOMAS, ConfigPesquisa

LIMITE_SHORTS_S = 180         # Shorts podem ter até 3 minutos
IDADE_MINIMA_BASE_DIAS = 14   # vídeos mais novos ainda estão acumulando views
MINIMO_VIDEOS_BASE = 5        # abaixo disso a mediana do canal não diz nada


@dataclass
class Video:
    id: str
    titulo: str
    canal_id: str
    canal: str
    publicado_em: str
    duracao_s: int
    views: int
    likes: int | None
    comentarios: int | None
    para_criancas: bool | None
    idioma_audio: str | None
    tags: list[str] = field(default_factory=list)
    thumbnail: str = ""
    nicho: str = ""
    idioma: str = ""
    inscritos_canal: int | None = None
    mediana_canal: float | None = None
    outlier: float | None = None
    views_por_dia: float = 0.0

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"

    @property
    def formato(self) -> str:
        return "short" if self.duracao_s <= LIMITE_SHORTS_S else "longo"


@dataclass
class ResumoNicho:
    nicho: str
    idioma: str
    videos: int
    potencial_canal_novo: float  # mediana de views/dia dos vídeos de canais pequenos
    mediana_views_dia: float
    pct_canais_pequenos: float
    concentracao_top3: float     # fatia das views que fica com os 3 maiores canais
    outliers: int
    duracao_mediana_min: float
    pct_shorts: float


_DURACAO = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def duracao_em_segundos(iso: str | None) -> int:
    m = _DURACAO.fullmatch(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def dias_desde(iso: str, agora: datetime) -> float:
    return (agora - datetime.fromisoformat(iso)).total_seconds() / 86400


def _int(valor) -> int | None:
    return int(valor) if valor is not None else None


def _melhor_thumbnail(thumbs: dict) -> str:
    for tamanho in ("maxres", "standard", "high", "medium", "default"):
        if tamanho in thumbs:
            return thumbs[tamanho]["url"]
    return ""


def video_de_api(item: dict, agora: datetime) -> Video:
    sn, st = item["snippet"], item.get("statistics", {})
    v = Video(
        id=item["id"],
        titulo=sn["title"],
        canal_id=sn["channelId"],
        canal=sn["channelTitle"],
        publicado_em=sn["publishedAt"],
        duracao_s=duracao_em_segundos(item.get("contentDetails", {}).get("duration")),
        views=int(st.get("viewCount", 0)),
        likes=_int(st.get("likeCount")),
        comentarios=_int(st.get("commentCount")),
        para_criancas=item.get("status", {}).get("madeForKids"),
        idioma_audio=sn.get("defaultAudioLanguage"),
        tags=sn.get("tags", []),
        thumbnail=_melhor_thumbnail(sn.get("thumbnails", {})),
    )
    v.views_por_dia = v.views / max(dias_desde(v.publicado_em, agora), 1)
    return v


def medianas_do_canal(videos: list[Video], agora: datetime) -> dict[str, float | None]:
    """Mediana de views do canal separada por formato: um Short só é comparado com Shorts,
    um vídeo longo só com vídeos longos."""
    por_formato: dict[str, list[int]] = {"short": [], "longo": []}
    for v in videos:
        if v.duracao_s > 0 and dias_desde(v.publicado_em, agora) >= IDADE_MINIMA_BASE_DIAS:
            por_formato[v.formato].append(v.views)
    return {
        formato: statistics.median(views) if len(views) >= MINIMO_VIDEOS_BASE else None
        for formato, views in por_formato.items()
    }


def coletar(yt, cfg: ConfigPesquisa, agora: datetime | None = None, log=print) -> list[Video]:
    agora = agora or datetime.now(timezone.utc)
    publicado_apos = (agora - timedelta(days=cfg.janela_dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resultado: list[Video] = []

    for nicho in cfg.nichos:
        for idioma, termos in nicho.termos.items():
            ids: list[str] = []
            for termo in termos:
                ids += yt.buscar(termo, idioma, IDIOMAS[idioma], publicado_apos,
                                 cfg.duracao, cfg.resultados_por_termo)
            videos = [video_de_api(item, agora) for item in yt.videos(ids)]
            if cfg.apenas_infantil:
                # Descarta só o que a API confirma não ser infantil.
                videos = [v for v in videos if v.para_criancas is not False]
            for v in videos:
                v.nicho, v.idioma = nicho.nome, idioma
            resultado += videos
            log(f"  {nicho.nome} [{idioma}]: {len(videos)} vídeos")

    _calcular_outliers(yt, resultado, cfg, agora, log)
    return resultado


def _calcular_outliers(yt, videos: list[Video], cfg: ConfigPesquisa, agora: datetime, log) -> None:
    canais = {c["id"]: c for c in yt.canais(v.canal_id for v in videos)}
    log(f"  medindo o normal de {len(canais)} canais...")
    medianas: dict[str, dict[str, float | None]] = {}
    for n, (canal_id, canal) in enumerate(canais.items(), 1):
        playlist = canal["contentDetails"]["relatedPlaylists"]["uploads"]
        recentes = [video_de_api(item, agora)
                    for item in yt.videos(yt.uploads_recentes(playlist, cfg.videos_base_canal))]
        medianas[canal_id] = medianas_do_canal(recentes, agora)
        if n % 25 == 0:
            log(f"    {n}/{len(canais)}")

    for v in videos:
        st = canais.get(v.canal_id, {}).get("statistics", {})
        v.inscritos_canal = None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount"))
        v.mediana_canal = medianas.get(v.canal_id, {}).get(v.formato)
        v.outlier = v.views / v.mediana_canal if v.mediana_canal else None


def resumir_nichos(videos: list[Video], cfg: ConfigPesquisa) -> list[ResumoNicho]:
    grupos: dict[tuple[str, str], list[Video]] = defaultdict(list)
    for v in videos:
        grupos[(v.nicho, v.idioma)].append(v)

    resumos = []
    for (nicho, idioma), vs in grupos.items():
        pequenos = [v for v in vs if v.inscritos_canal is not None
                    and v.inscritos_canal < cfg.canal_pequeno_max_inscritos]
        views_por_canal: Counter[str] = Counter()
        for v in vs:
            views_por_canal[v.canal_id] += v.views
        total = sum(views_por_canal.values())
        top3 = sum(n for _, n in views_por_canal.most_common(3))
        resumos.append(ResumoNicho(
            nicho=nicho,
            idioma=idioma,
            videos=len(vs),
            potencial_canal_novo=(statistics.median(v.views_por_dia for v in pequenos)
                                  if len(pequenos) >= 3 else 0.0),
            mediana_views_dia=statistics.median(v.views_por_dia for v in vs),
            pct_canais_pequenos=len(pequenos) / len(vs),
            concentracao_top3=top3 / total if total else 0.0,
            outliers=sum(1 for v in vs if v.outlier and v.outlier >= cfg.outlier_minimo),
            duracao_mediana_min=statistics.median(v.duracao_s for v in vs) / 60,
            pct_shorts=sum(1 for v in vs if v.formato == "short") / len(vs),
        ))
    return sorted(resumos, key=lambda r: r.potencial_canal_novo, reverse=True)
