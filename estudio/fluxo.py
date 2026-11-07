"""As etapas de um episódio, usadas pelos comandos avulsos, pela tarefa diária e pelo produtor
contínuo: roteiro novo, etapas na placa de vídeo (imagens, narração, sons e trilha) e
finalização no processador (montagem, revisão e aprovação)."""
from __future__ import annotations

import re
from pathlib import Path

from . import conformidade, contos, episodio, estrutura, imagens, montagem, musica, narracao, roteiro, sons
from .config import DADOS

PASTA_ESTRUTURAS = DADOS / "estruturas"
TITULOS_ANTERIORES = 80  # quantos títulos o roteirista vê para não repetir tema
MAX_CORRECOES = 2        # vezes que a revisão pode mandar refazer imagens defeituosas de um vídeo
IMAGEM_CITADA = re.compile(r"(?<![\d.,])(\d{1,2})\.(\d)(?![\d.,])")  # "imagem 4.1", "[10.2]"


def novo_episodio(cfg, canal, nicho: str, idioma: str, tema: str | None, minutos: float,
                  arquivo_nichos: Path, log=print) -> episodio.Episodio:
    nichos = [n.nome for n in cfg.nichos]
    if nicho not in nichos:
        raise SystemExit(f"Nicho '{nicho}' não existe em {arquivo_nichos}. Opções: {', '.join(nichos)}")
    if idioma not in canal.narracao:
        raise SystemExit(f"Falta a voz [narracao.{idioma}] no canal.toml")
    molde = roteiro.molde_do_nicho(estrutura.carregar_estruturas(PASTA_ESTRUTURAS), nicho, idioma)
    if not molde:
        log(f"Aviso: nenhuma estrutura analisada para '{nicho}'. O roteiro sai sem as lições dos "
            f"vídeos que se destacam; rode antes: python -m estudio estrutura --nicho {nicho}")
    feitos = [e.carregar_roteiro() for e in episodio.listar()]
    anteriores = [r.get("titulo", "") for r in feitos][-TITULOS_ANTERIORES:]
    conto = None
    if canal.roteiro.contos and not tema:  # um tema seu vale mais que um conto
        conto = contos.proximo([r.get("inspiracao", "") for r in feitos], f"{nicho}:{idioma}:{len(feitos)}")
    log("Escrevendo o roteiro..." + (f' (a partir do conto "{conto.titulo}")' if conto else ""))
    r = roteiro.gerar(canal, nicho, idioma, tema, minutos, molde, anteriores, log=log, conto=conto)
    ep = episodio.novo(nicho, idioma, r["titulo"])
    ep.salvar_roteiro(r)
    ep.gravar(ep.roteiro_gerado, r)  # a revisão compara com ele para saber se você editou
    log(f'"{r["titulo"]}" — {len(r["cenas"])} cenas ({r["gerado_por"]}). Roteiro: {ep.arquivo_roteiro}')
    palavras, meta = roteiro.palavras_da_narracao(r), minutos * roteiro.PALAVRAS_POR_MINUTO
    if palavras < 0.7 * meta:
        log(f"Aviso: a narração ficou curta ({palavras} de ~{meta:.0f} palavras; o vídeo terá uns "
            f"{palavras / roteiro.PALAVRAS_POR_MINUTO + 0.5:.1f} min).")
    for problema in roteiro.validar(r, canal):
        log(f"  corrigir antes de produzir: {problema}")
    return ep


def validar(ep, r, canal) -> None:
    problemas = roteiro.validar(r, canal)
    if problemas:
        raise SystemExit(f"Corrija o {ep.arquivo_roteiro}:\n  " + "\n  ".join(problemas))


def gerar_imagens(ep, r, canal, log=print) -> None:
    feitos = imagens.gerar(ep, r, canal, log=log)
    log(f"Imagens: {feitos} novas em {ep.imagens} (revise todas em {ep.pasta / 'revisao_imagens.jpg'}; para "
        'trocar uma imagem, mude a "semente" da cena no roteiro.json e rode de novo)')


def narrar(ep, r, canal, log=print) -> None:
    feitos = narracao.narrar(ep, r, canal, log=log)
    log(f"Narração: {feitos} cenas novas em {ep.narracao}")


def compor_trilha(ep, r, canal, log=print) -> None:
    sons.garantir((c.get("som") for c in r["cenas"]), log=log)  # os sons alongam as cenas: vêm antes de medir
    duracao = sum(t.tela_s for t in montagem.calcular_tempos(ep, r))  # a trilha cobre o vídeo inteiro
    musica.gerar_trilha(ep, r, canal, duracao, episodio.listar(), log=log)
    info = ep.ler(ep.trilha_info)
    log(f"Trilha: estilo {info['estilo']['id']}, versão {info['escolhida']} — {ep.trilha}")


def montar(ep, r, canal, log=print) -> None:
    sons.garantir((c.get("som") for c in r["cenas"]), log=log)  # sons reais de animais que o roteiro pede
    video = montagem.montar(ep, r, canal, log=log)
    log(f"Vídeo: {video} (título, descrição e checklist em {ep.pasta / 'publicar.txt'})")


def etapas_na_placa(ep, r, canal, log=print) -> None:
    """Tudo o que usa a placa de vídeo: imagens, narração local, sons e trilha."""
    validar(ep, r, canal)
    gerar_imagens(ep, r, canal, log)  # primeiro o que é local: se a cota do Gemini acabar, as imagens ficam
    narrar(ep, r, canal, log)
    if canal.musica.fonte == "ia" and not r.get("musica"):
        compor_trilha(ep, r, canal, log)
    else:
        sons.garantir((c.get("som") for c in r["cenas"]), log=log)


def finalizar(ep, r, canal, log=print) -> None:
    """Montagem (processador), revisão e aprovação automática."""
    montar(ep, r, canal, log)
    if canal.publicacao.aprovacao == "automatica":
        aprovar_sozinho(ep, r, canal, log)


def produzir(ep, r, canal, log=print) -> None:
    etapas_na_placa(ep, r, canal, log)
    finalizar(ep, r, canal, log)


def outros(ep: episodio.Episodio) -> list[tuple[str, dict]]:
    """Os demais vídeos do mesmo tipo (episódios com episódios, Shorts com Shorts), para a revisão
    julgar repetição."""
    todos = episodio.listar_shorts() if ep.e_short else episodio.listar()
    return [(e.nome, e.carregar_roteiro()) for e in todos if e.nome != ep.nome]


def aprovar_sozinho(ep, r, canal, log=print) -> None:
    """Modo de aprovação automática: revisa (se esta versão ainda não foi revisada) e aprova se não
    houver bloqueio."""
    if ep.publicacao.exists() or conformidade.aprovado(ep) or not ep.video.exists():
        return
    revisao = ep.ler(ep.revisao)
    if not revisao or not revisao["com_ia"] or revisao["assinatura"] != ep.assinatura_entrega():
        log(f"Revisando {ep.nome}...")
        conformidade.revisar(ep, r, canal, outros(ep), log=log)
    if corrigir_imagens(ep, r, log):
        return  # o produtor refaz as imagens apontadas, monta e revisa de novo
    ok, detalhe = conformidade.aprovar_automaticamente(ep)
    log(f"{ep.nome}: {detalhe}")


def imagens_com_defeito(revisao: dict, r: dict) -> tuple[set[int], bool]:
    """Cenas cujas imagens a revisão da IA apontou (por número, como "4.1") e se apontou a miniatura.
    O cartão fixo da chamada dos Shorts não é gerado, então não entra."""
    cenas, miniatura = set(), False
    for p in revisao.get("problemas", []):
        if p.get("origem") != "ia":
            continue
        for n, _ in IMAGEM_CITADA.findall(p["detalhe"]):
            n = int(n)
            if 1 <= n <= len(r["cenas"]) and not any(t.get("fixa") for t in roteiro.tomadas(r["cenas"][n - 1])):
                cenas.add(n)
        miniatura |= "miniatura" in p["detalhe"].lower() and bool(r.get("miniatura", {}).get("imagem"))
    return cenas, miniatura


def corrigir_imagens(ep, r, log=print) -> bool:
    """Sem revisão humana, defeito de imagem apontado pela IA (deformação, personagem em dobro,
    pista que entrega a resposta) não fica para alguém resolver: troca a semente só dessas cenas,
    e o vídeo volta para a placa, a montagem e a revisão. Até MAX_CORRECOES vezes; depois disso,
    vale a decisão da revisão (avisos aprovam, bloqueio segura)."""
    feitas = int(r.get("correcoes_automaticas", 0))
    revisao = ep.ler(ep.revisao) or {}
    if feitas >= MAX_CORRECOES or revisao.get("assinatura") != ep.assinatura_entrega():
        return False
    cenas, miniatura = imagens_com_defeito(revisao, r)
    if not cenas and not miniatura:
        return False
    for n in cenas:
        r["cenas"][n - 1]["semente"] = int(r["cenas"][n - 1].get("semente", 0)) + 1
    if miniatura:
        r["miniatura"]["semente"] = int(r["miniatura"].get("semente", 0)) + 1
    r["correcoes_automaticas"] = feitas + 1
    ep.salvar_roteiro(r)
    alvo = ", ".join([f"cena {n}" for n in sorted(cenas)] + (["miniatura"] if miniatura else []))
    log(f"{ep.nome}: a revisão apontou defeito em imagens ({alvo}); refazendo só essas "
        f"(correção {feitas + 1} de {MAX_CORRECOES})")
    return True
