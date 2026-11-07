"""Produção automática: as decisões do que a tarefa diária do Windows faz sem nenhum comando seu
(a execução fica em `python -m estudio automatico`).

1. Busca as métricas dos vídeos publicados (elas escolhem o estilo das próximas trilhas).
2. Termina os episódios começados, retomando de onde pararam, e aprova os que a revisão não bloquear.
3. Se a fila (agendados para o futuro + aprovados esperando envio) estiver abaixo de
   [automacao] manter_agendados, escreve e produz episódios novos, no máximo novos_por_execucao.
4. Envia os aprovados, agenda nos próximos horários livres e atualiza canal, playlists e metadados.

Cada etapa é independente: cota esgotada ou queda de rede numa não impede as outras, e tudo continua
na execução seguinte. O que só você resolve vira um aviso do Windows."""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import conformidade
from .config import DADOS
from .episodio import Episodio

ARQUIVO_PAUSA = DADOS / "PAUSAR"  # existindo, o produtor contínuo espera (comandos pausar/retomar)


def pausado(arquivo: Path | None = None) -> bool:
    return (arquivo or ARQUIVO_PAUSA).exists()


def espaco_livre_gb(pasta: Path = DADOS) -> float:
    pasta.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(pasta).free / 1e9


def _assinatura_roteiro(ep: Episodio) -> str:
    return hashlib.sha1(ep.arquivo_roteiro.read_bytes()).hexdigest()


def _etapas(ep: Episodio) -> dict:
    return ep.ler(ep.pasta / "etapas.json") or {}


def marcar_placa_pronta(ep: Episodio) -> None:
    """Imagens, narração, sons e trilha feitos para esta versão do roteiro."""
    ep.gravar(ep.pasta / "etapas.json", {**_etapas(ep), "placa": _assinatura_roteiro(ep)})


def placa_pronta(ep: Episodio) -> bool:
    return _etapas(ep).get("placa") == _assinatura_roteiro(ep)


def registrar_erro(ep: Episodio, mensagem: str) -> None:
    """Depois de falhar várias vezes, o episódio sai da fila até o roteiro mudar (ou o erro ser apagado)."""
    ep.gravar(ep.pasta / "etapas.json", {**_etapas(ep), "erro": mensagem[:500], "erro_no_roteiro": _assinatura_roteiro(ep)})


def com_erro(ep: Episodio) -> bool:
    etapas = _etapas(ep)
    return bool(etapas.get("erro")) and etapas.get("erro_no_roteiro") == _assinatura_roteiro(ep)


def limpar_intermediarios(ep: Episodio) -> int:
    """Depois de aprovado, apaga o que dá para refazer (clipes, quadros, versões da trilha não usadas):
    a produção contínua encheria o disco. Ficam imagens, narração, trilha e vídeo. Devolve os bytes livres."""
    liberados = 0
    candidatos = [*ep.montagem.glob("clipe_*.mp4"), *ep.montagem.glob("quadro_*.png"),
                  *ep.pasta_trilha.glob("versao_*.wav")]
    for arquivo in candidatos:
        liberados += arquivo.stat().st_size
        arquivo.unlink()
    return liberados


def bloqueado(ep: Episodio) -> bool:
    """Revisão completa da versão atual com bloqueio: precisa de alguém decidir."""
    if ep.publicacao.exists() or conformidade.aprovado(ep):
        return False
    revisao = ep.ler(ep.revisao)
    return bool(revisao and revisao["com_ia"] and revisao["assinatura"] == ep.assinatura_entrega()
                and any(p["gravidade"] == conformidade.BLOQUEIA for p in revisao["problemas"]))


def em_andamento(episodios: list[Episodio]) -> list[Episodio]:
    """Começados e ainda sem aprovação (bloqueados pela revisão ou com erro repetido ficam de fora:
    esperam você)."""
    return [ep for ep in episodios if not ep.publicacao.exists() and not conformidade.aprovado(ep)
            and not bloqueado(ep) and not com_erro(ep)]


def para_a_placa(episodios: list[Episodio]) -> list[Episodio]:
    """Na fila da placa de vídeo, do mais antigo para o mais novo."""
    return [ep for ep in em_andamento(episodios) if not placa_pronta(ep)]


def marcar_finalizado(ep: Episodio) -> None:
    """Montado e revisado; no modo de aprovação humana, fica esperando o comando aprovar. Vale para
    esta passagem pela placa: se a revisão mandar refazer imagens, a placa refaz e a montagem volta."""
    etapas = _etapas(ep)
    ep.gravar(ep.pasta / "etapas.json", {**etapas, "finalizado": ep.estado_arquivos(),
                                          "finalizado_com_placa": etapas.get("placa")})


def finalizado(ep: Episodio) -> bool:
    etapas = _etapas(ep)
    return etapas.get("finalizado") == ep.estado_arquivos() and etapas.get("finalizado_com_placa") == etapas.get("placa")


def para_finalizar(episodios: list[Episodio]) -> list[Episodio]:
    """Com a parte da placa pronta, esperando montagem, revisão e aprovação."""
    return [ep for ep in em_andamento(episodios) if placa_pronta(ep) and not finalizado(ep)]


def fila(episodios: list[Episodio], agora: datetime) -> int:
    """Episódios garantidos para os próximos horários: agendados no futuro + aprovados ainda não enviados."""
    total = 0
    for ep in episodios:
        registro = ep.ler(ep.publicacao)
        if registro:
            total += datetime.fromisoformat(registro["publicar_em"]) > agora
        elif conformidade.aprovado(ep):
            total += 1
    return total


def fim_da_semana_alvo(agora: datetime, fuso: str, semanas_adiantadas: int) -> datetime:
    """Domingo, 23:59, da semana (segunda a domingo) que o produtor precisa deixar coberta."""
    zona = ZoneInfo(fuso)
    hoje = agora.astimezone(zona).date()
    domingo = hoje + timedelta(days=6 - hoje.weekday() + 7 * max(0, semanas_adiantadas))
    return datetime.combine(domingo, time(23, 59), zona)


def vagas_ate(episodios: list[Episodio], cfg, agora: datetime, fim: datetime) -> int:
    """Horários da grade `cfg`, daqui até `fim`, ainda sem vídeo agendado."""
    from .publicacao import proximos_horarios

    ocupados = [datetime.fromisoformat(r["publicar_em"]) for r in (ep.ler(ep.publicacao) for ep in episodios) if r]
    semanas = (fim - agora).days // 7 + 2
    livres = proximos_horarios(cfg, ocupados, agora, semanas * max(1, len(cfg.horarios)))
    return sum(quando <= fim for quando in livres)


def faltam_para_a_semana(episodios: list[Episodio], canal, agora: datetime) -> tuple[int, datetime]:
    """Quantos episódios novos começar para cobrir os horários até o fim da semana-alvo (os aprovados
    no estoque e os que já estão em produção contam), e até quando a semana-alvo vai."""
    fim = fim_da_semana_alvo(agora, canal.publicacao.fuso, canal.automacao.semanas_adiantadas)
    prontos = sum(1 for ep in episodios if not ep.publicacao.exists() and conformidade.aprovado(ep))
    return max(0, vagas_ate(episodios, canal.publicacao, agora, fim) - prontos - len(em_andamento(episodios))), fim


def faltam_shorts_para_a_semana(shorts: list[Episodio], canal, agora: datetime) -> int:
    """O mesmo para os Shorts, na grade de [shorts]."""
    if not canal.shorts.ligado:
        return 0
    from .publicacao import horarios_de_shorts

    fim = fim_da_semana_alvo(agora, canal.publicacao.fuso, canal.automacao.semanas_adiantadas)
    prontos = sum(1 for s in shorts if not s.publicacao.exists() and conformidade.aprovado(s))
    return max(0, vagas_ate(shorts, horarios_de_shorts(canal), agora, fim) - prontos - len(em_andamento(shorts)))


def precisa_de_novos(episodios: list[Episodio], agora: datetime, manter: int) -> int:
    """Quantos episódios novos começar agora: nenhum enquanto houver um em andamento."""
    if em_andamento(episodios):
        return 0
    return max(0, manter - fila(episodios, agora))
