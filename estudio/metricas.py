"""Fase 6: métricas reais dos seus vídeos (YouTube Analytics, gratuito) e o ajuste que vem delas.
Cada vídeo publicado ganha um metricas.json com visualizações, porcentagem média assistida e a
curva de retenção. O desempenho é comparado com a mediana do próprio canal, e é essa comparação
que decide, por exemplo, qual estilo de trilha usar no próximo episódio."""
from __future__ import annotations

import math
import random
import statistics
from datetime import date, datetime, timedelta, timezone

from .episodio import Episodio
from .publicacao import _executar

MIN_DIAS = 7     # antes disso o vídeo ainda está sendo distribuído
MIN_VIEWS = 30   # abaixo disso a porcentagem assistida é só ruído
ESPERA_DADOS_DIAS = 2  # o Analytics leva uns 2 dias para consolidar


def servico_analytics():
    from googleapiclient.discovery import build

    from .publicacao import credenciais

    return build("youtubeAnalytics", "v2", credentials=credenciais(), cache_discovery=False)


def _tabela(resposta: dict) -> list[dict]:
    colunas = [c["name"] for c in resposta.get("columnHeaders", [])]
    return [dict(zip(colunas, linha)) for linha in resposta.get("rows", []) or []]


def _no_ponto(curva: list[tuple[float, float]], fracao: float) -> float | None:
    """Retenção (audienceWatchRatio) num ponto do vídeo, interpolando a curva."""
    if not curva:
        return None
    for (x0, y0), (x1, y1) in zip(curva, curva[1:]):
        if x0 <= fracao <= x1:
            return y0 + (y1 - y0) * (fracao - x0) / (x1 - x0) if x1 > x0 else y0
    return curva[-1][1] if fracao > curva[-1][0] else curva[0][1]


def coletar(analytics, episodios: list[Episodio], hoje: date | None = None, log=print) -> int:
    """Atualiza o metricas.json dos episódios publicados há pelo menos 2 dias."""
    hoje = hoje or date.today()
    alvo = []
    for ep in episodios:
        registro = ep.ler(ep.publicacao)
        if registro and date.fromisoformat(registro["publicar_em"][:10]) <= hoje - timedelta(days=ESPERA_DADOS_DIAS):
            alvo.append((ep, registro))
    if not alvo:
        return 0
    inicio = min(date.fromisoformat(r["publicar_em"][:10]) for _, r in alvo).isoformat()
    consulta = {"ids": "channel==MINE", "startDate": inicio, "endDate": hoje.isoformat()}
    resumo = {linha["video"]: linha for linha in _tabela(_executar(analytics.reports().query(
        **consulta, dimensions="video", filters="video==" + ",".join(r["video_id"] for _, r in alvo),
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,shares,subscribersGained")))}
    for ep, registro in alvo:
        linha = resumo.get(registro["video_id"], {})
        curva = [(float(p["elapsedVideoTimeRatio"]), float(p["audienceWatchRatio"])) for p in _tabela(_executar(
            analytics.reports().query(**consulta, dimensions="elapsedVideoTimeRatio", metrics="audienceWatchRatio",
                                      filters=f"video=={registro['video_id']}")))]
        duracao = (ep.ler(ep.producao) or {}).get("duracao_s") or 0
        ep.gravar(ep.metricas, {
            "coletado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dias_no_ar": (hoje - date.fromisoformat(registro["publicar_em"][:10])).days,
            "views": int(linha.get("views", 0)),
            "minutos_assistidos": float(linha.get("estimatedMinutesWatched", 0)),
            "media_assistida_s": float(linha.get("averageViewDuration", 0)),
            "media_assistida_pct": float(linha.get("averageViewPercentage", 0)),
            "curtidas": int(linha.get("likes", 0)),
            "compartilhamentos": int(linha.get("shares", 0)),
            "inscritos": int(linha.get("subscribersGained", 0)),
            "retencao_30s": _no_ponto(curva, 30 / duracao) if duracao else None,
            "curva_retencao": curva,
        })
    log(f"  métricas atualizadas de {len(alvo)} vídeo(s)")
    return len(alvo)


def maduros(episodios: list[Episodio]) -> list[tuple[Episodio, dict]]:
    """Episódios com dados suficientes para comparar."""
    return [(ep, m) for ep in episodios if (m := ep.ler(ep.metricas))
            and m["dias_no_ar"] >= MIN_DIAS and m["views"] >= MIN_VIEWS and m["media_assistida_pct"] > 0]


def desempenho_relativo(episodios: list[Episodio]) -> dict[str, float]:
    """nome do episódio -> porcentagem média assistida ÷ mediana do canal (1,0 = típico)."""
    dados = maduros(episodios)
    if not dados:
        return {}
    mediana = statistics.median(m["media_assistida_pct"] for _, m in dados)
    return {ep.nome: m["media_assistida_pct"] / mediana for ep, m in dados}


def engajamento_relativo(videos: list[Episodio]) -> dict[str, float]:
    """nome -> engajamento ÷ mediana do canal (1,0 = típico), para Shorts: metade retenção (porcentagem
    média assistida, que no Short passa de 100% quando ele é revisto) e metade interação por
    visualização (curtida 1, compartilhamento 2, inscrito 3)."""
    dados = maduros(videos)
    if not dados:
        return {}

    def interacao(m: dict) -> float:
        return (m.get("curtidas", 0) + 2 * m.get("compartilhamentos", 0) + 3 * m.get("inscritos", 0)) / m["views"]

    mediana_ret = statistics.median(m["media_assistida_pct"] for _, m in dados)
    mediana_int = statistics.median(interacao(m) for _, m in dados)
    saida = {}
    for ep, m in dados:
        retencao = m["media_assistida_pct"] / mediana_ret
        saida[ep.nome] = (retencao + interacao(m) / mediana_int) / 2 if mediana_int > 0 else retencao
    return saida


def desempenho_por_estilo(episodios: list[Episodio]) -> dict[str, list[float]]:
    relativo = desempenho_relativo(episodios)
    por_estilo: dict[str, list[float]] = {}
    for ep in episodios:
        info = ep.ler(ep.trilha_info)
        if info and ep.nome in relativo:
            por_estilo.setdefault(info["estilo"]["id"], []).append(relativo[ep.nome])
    return por_estilo


# Escolha com amostragem de Thompson: cada opção é sorteada pela sua média provável, e a incerteza
# de quem tem poucos dados dá chance de ser testada. Com o tempo, a que segura mais a audiência
# passa a ser escolhida quase sempre, sem parar de testar as outras de vez em quando.
PRIOR_PESO = 2      # quantos vídeos "típicos" (1,0) cada opção carrega antes de ter dados
RUIDO = 0.20        # variação normal de um vídeo para outro, em desempenho relativo


def _posterior(resultados: list[float]) -> tuple[float, float]:
    n = len(resultados)
    media = (sum(resultados) + PRIOR_PESO * 1.0) / (n + PRIOR_PESO)
    return media, RUIDO / math.sqrt(n + PRIOR_PESO)


def escolher(opcoes: list[str], desempenho: dict[str, list[float]], semente: str) -> str:
    sorteio = random.Random(semente)
    return max(opcoes, key=lambda o: sorteio.gauss(*_posterior(desempenho.get(o, []))))


def chance_de_ser_a_melhor(opcoes: list[str], desempenho: dict[str, list[float]], sorteios: int = 4000) -> dict[str, float]:
    sorteio = random.Random(0)
    posteriores = {o: _posterior(desempenho.get(o, [])) for o in opcoes}
    vitorias = dict.fromkeys(opcoes, 0)
    for _ in range(sorteios):
        vitorias[max(opcoes, key=lambda o: sorteio.gauss(*posteriores[o]))] += 1
    return {o: v / sorteios for o, v in vitorias.items()}
