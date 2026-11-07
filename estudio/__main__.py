"""Linha de comando: python -m estudio <comando>  (veja python -m estudio -h)"""
from __future__ import annotations

import argparse
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import (automacao, aviso, conformidade, episodio, estrutura, imagens, metricas, montagem, musica, narracao,
               publicacao, relatorio, roteiro, shorts, sons)
from .config import CACHE, DADOS, IDIOMAS, RAIZ, carregar_canal, carregar_nichos, chave
from .fluxo import PASTA_ESTRUTURAS
from .fluxo import aprovar_sozinho as _aprovar_sozinho
from .fluxo import compor_trilha as _trilha
from .fluxo import gerar_imagens as _imagens
from .fluxo import montar as _montar
from .fluxo import narrar as _narrar
from .fluxo import novo_episodio as _novo_episodio
from .fluxo import outros as _outros
from .fluxo import produzir as _produzir
from .gemini import CotaGeminiEsgotada
from .pesquisa import coletar, resumir_nichos
from .youtube import CotaEsgotada, YouTube


def _gerar_relatorio(cfg, videos) -> None:
    resumos = resumir_nichos(videos, cfg)
    relatorio.salvar_csvs(videos, resumos, DADOS)
    md = relatorio.gerar_markdown(videos, resumos, estrutura.carregar_estruturas(PASTA_ESTRUTURAS))
    (DADOS / "relatorio.md").write_text(md, encoding="utf-8")
    print(f"Relatório: {DADOS / 'relatorio.md'}")


def cmd_pesquisar(args, cfg) -> None:
    if args.somente:
        cfg.nichos = [n for n in cfg.nichos if n.nome == args.somente]
        if not cfg.nichos:
            raise SystemExit(f"Nicho '{args.somente}' não existe em {args.nichos}")
    yt = YouTube(chave("YOUTUBE_API_KEY"), CACHE / "youtube")
    print("Coletando...")
    try:
        videos = coletar(yt, cfg)
    finally:
        print(f"Cota da YouTube API gasta nesta execução: {yt.unidades_gastas} unidades (limite padrão 10.000/dia)")

    if args.somente and (DADOS / "pesquisa.json").exists():
        anteriores = [v for v in relatorio.carregar_pesquisa(DADOS) if v.nicho != args.somente]
        videos = anteriores + videos
    relatorio.salvar_pesquisa(videos, DADOS)
    _gerar_relatorio(cfg, videos)


def cmd_estrutura(args, cfg) -> None:
    videos = relatorio.carregar_pesquisa(DADOS)
    escolhidos = estrutura.candidatos(videos, args.top, args.max_min, args.nicho, args.idioma)
    print(f"{len(escolhidos)} vídeos selecionados (os já analisados são pulados).")
    feitos = estrutura.analisar(escolhidos, PASTA_ESTRUTURAS)
    print(f"{feitos} novas análises em {PASTA_ESTRUTURAS}")
    _gerar_relatorio(cfg, videos)


def cmd_relatorio(args, cfg) -> None:
    _gerar_relatorio(cfg, relatorio.carregar_pesquisa(DADOS))


def cmd_roteiro(args, cfg) -> None:
    _novo_episodio(cfg, carregar_canal(args.canal), args.nicho, args.idioma, args.tema, args.minutos, args.nichos)
    print("Revise e edite à vontade (é o momento de deixar a história com a sua cara). Depois:\n"
          "  python -m estudio produzir")


def _abrir(args) -> tuple[episodio.Episodio, dict, object]:
    canal = carregar_canal(args.canal)
    ep = episodio.localizar(args.episodio)
    r = ep.carregar_roteiro()
    problemas = roteiro.validar(r, canal)
    if problemas:
        raise SystemExit(f"Corrija o {ep.arquivo_roteiro}:\n  " + "\n  ".join(problemas))
    print(f"Episódio: {ep.nome}")
    return ep, r, canal


def cmd_narrar(args, cfg) -> None:
    _narrar(*_abrir(args))


def cmd_imagens(args, cfg) -> None:
    _imagens(*_abrir(args))


def cmd_montar(args, cfg) -> None:
    _montar(*_abrir(args))


def cmd_trilha(args, cfg) -> None:
    _trilha(*_abrir(args))


def cmd_produzir(args, cfg) -> None:
    _produzir(*_abrir(args))


def cmd_estilos(args, cfg) -> None:
    videos = relatorio.carregar_pesquisa(DADOS)
    escolhidos = estrutura.candidatos(videos, args.top, args.max_min, args.nicho, args.idioma)
    print(f"{len(escolhidos)} vídeos selecionados (os já ouvidos são pulados).")
    print(f"{musica.pesquisar_estilos(escolhidos)} perfis musicais novos em {musica.ARQUIVO_ESTILOS}")
    for e in musica.carregar_estilos():
        print(f"  {e.clima:9} {e.id:28} {e.bpm} bpm, {e.tom} ({e.origem})")


def cmd_metricas(args, cfg) -> None:
    episodios, lista_shorts = episodio.listar(), episodio.listar_shorts()
    if not args.sem_coletar:
        metricas.coletar(metricas.servico_analytics(), episodios + lista_shorts)
    relativo = metricas.desempenho_relativo(episodios)
    print("Episódio | dias no ar | views | % médio assistido | retenção aos 30 s | estilo da trilha")
    for ep in episodios:
        m = ep.ler(ep.metricas)
        if not m:
            continue
        estilo = (ep.ler(ep.trilha_info) or {}).get("estilo", {}).get("id", "-")
        ret30 = f"{m['retencao_30s']:.0%}" if m.get("retencao_30s") is not None else "-"
        comparacao = f" ({relativo[ep.nome]:.2f}x o típico do canal)" if ep.nome in relativo else " (ainda imaturo)"
        print(f"{ep.nome} | {m['dias_no_ar']} | {m['views']} | {m['media_assistida_pct']:.0f}%{comparacao} | {ret30} | {estilo}")
    por_estilo = metricas.desempenho_por_estilo(episodios)
    print(f"\nTrilhas (só vídeos com {metricas.MIN_DIAS}+ dias e {metricas.MIN_VIEWS}+ views contam; 1,00 = típico):")
    estilos = musica.carregar_estilos()
    for clima in musica.CLIMAS:
        ids = [e.id for e in estilos if e.clima == clima]
        chances = metricas.chance_de_ser_a_melhor(ids, por_estilo)
        print(f"  {clima}:")
        for id_ in sorted(ids, key=lambda i: -chances[i]):
            dados = por_estilo.get(id_, [])
            media = f"{sum(dados) / len(dados):.2f}" if dados else "-"
            print(f"    {id_:28} vídeos {len(dados):2}  desempenho {media:>4}  chance de ser a melhor {chances[id_]:.0%}")
    print("\nO próximo episódio de cada clima sorteia o estilo por essas chances: o melhor é usado mais, e os "
          "outros continuam sendo testados de vez em quando.")
    if lista_shorts:
        por_formato = shorts.desempenho_por_formato(lista_shorts)
        formatos = carregar_canal(args.canal).shorts.formatos
        chances = metricas.chance_de_ser_a_melhor(formatos, por_formato)
        print("\nShorts por formato (engajamento: metade retenção, metade curtidas/compartilhamentos/inscritos por "
              "view; 1,00 = típico):")
        for formato in sorted(formatos, key=lambda f: -chances[f]):
            dados = por_formato.get(formato, [])
            media = f"{sum(dados) / len(dados):.2f}" if dados else "-"
            print(f"    {formato:12} Shorts {len(dados):2}  engajamento {media:>4}  chance de ser o melhor {chances[formato]:.0%}")


def _situacao(ep: episodio.Episodio) -> str:
    registro = ep.ler(ep.publicacao)
    if registro:
        return f"enviado, vai ao ar em {registro['publicar_em'][:16].replace('T', ' ')}"
    if conformidade.aprovado(ep):
        return "aprovado, aguardando envio"
    if ep.aprovacao.exists():
        return "mudou depois da aprovação"
    revisao = ep.ler(ep.revisao)
    if revisao and revisao["assinatura"] == ep.assinatura_entrega():
        bloqueios = sum(p["gravidade"] == conformidade.BLOQUEIA for p in revisao["problemas"])
        return f"revisado, {bloqueios} bloqueio(s)" if bloqueios else "revisado, falta aprovar"
    return "vídeo pronto, falta revisar" if ep.video.exists() else "em produção"


def cmd_episodios(args, cfg) -> None:
    episodios = episodio.listar()
    print(publicacao.aviso_de_conexao())
    if not episodios:
        print("Nenhum episódio ainda.")
    situacoes = {ep.nome: _situacao(ep) for ep in episodios}
    resumo: dict[str, int] = {}
    for situacao in situacoes.values():
        resumo[situacao.split(",")[0]] = resumo.get(situacao.split(",")[0], 0) + 1
    print("Resumo: " + " | ".join(f"{k}: {v}" for k, v in resumo.items()))
    if automacao.pausado():
        print("Produção contínua PAUSADA (retome com: python -m estudio retomar)")
    mostrar = episodios if args.todos else episodios[-15:]
    if len(mostrar) < len(episodios):
        print(f"(últimos {len(mostrar)} de {len(episodios)}; todos com --todos)")
    for ep in mostrar:
        r = ep.carregar_roteiro()
        cenas = r.get("cenas", [])
        todas = [montagem.arquivo_imagem(n, k) for n, c in enumerate(cenas, 1)
                 for k in range(1, len(roteiro.tomadas(c)) + 1)]
        imgs = sum((ep.imagens / a).exists() for a in todas)
        faltam_falas = len(narracao.pendentes(ep, r))
        falas = sum(1 for c in cenas if c.get("narracao", "").strip())
        print(f"{ep.nome}\n  {r.get('titulo', '')} | narração {falas - faltam_falas}/{falas} | "
              f"imagens {imgs}/{len(todas)} | {situacoes[ep.nome]}")
        for short in shorts.do_episodio(ep):
            rs = short.carregar_roteiro()
            print(f"    Short {short.pasta.name}: \"{rs.get('youtube', {}).get('titulo', '')}\" | {_situacao(short)}")


def cmd_revisar(args, cfg) -> None:
    ep, r, canal = _abrir(args)
    resultado = conformidade.revisar(ep, r, canal, _outros(ep), usar_ia=not args.sem_ia)
    problemas = [conformidade.Problema(**p) for p in resultado["problemas"]]
    for gravidade, titulo in ((conformidade.BLOQUEIA, "BLOQUEIOS (impedem a aprovação)"),
                              (conformidade.AVISO, "Avisos")):
        itens = [p for p in problemas if p.gravidade == gravidade]
        if itens:
            print(f"\n{titulo}:\n  " + "\n  ".join(str(p) for p in itens))
    if resultado["ia"]:
        print(f"\nGemini: {resultado['ia']['resumo']}")
        if resultado["ia"]["principios_atendidos"]:
            print("Princípios de qualidade atendidos: " + "; ".join(resultado["ia"]["principios_atendidos"]))
    if not problemas:
        print("\nNenhum problema encontrado.")
    if args.sem_ia:
        print("\nRevisão rápida, sem o Gemini. Para poder aprovar, rode a completa: python -m estudio revisar")
    else:
        print(f"\nDetalhes em {ep.revisao}. Assista ao vídeo inteiro; se estiver tudo certo: python -m estudio aprovar")


def cmd_aprovar(args, cfg) -> None:
    ep, _, _ = _abrir(args)
    conformidade.aprovar(ep, ciente=args.ciente, discordo_da_ia=args.discordo_da_ia or "")
    print("Aprovado. O próximo envio automático (ou python -m estudio publicar) sobe e agenda o vídeo.")


def cmd_conectar(args, cfg) -> None:
    youtube = publicacao.servico(interativo=True, refazer=True)
    info = publicacao.canal_conectado(youtube)
    print(f'Conectado ao canal "{info["nome"]}" (id {info["id"]}). Os envios vão para este canal.')


def cmd_publicar(args, cfg) -> None:
    canal = carregar_canal(args.canal)
    todos = episodio.listar() + episodio.listar_shorts()
    if canal.publicacao.aprovacao == "automatica" and not args.simular:
        for ep in todos:  # vídeos produzidos que ainda não passaram pela aprovação
            _aprovar_sozinho(ep, ep.carregar_roteiro(), canal)
    alvo = [episodio.localizar(args.episodio)] if args.episodio else None
    youtube = None if args.simular else publicacao.servico()
    if youtube:
        print(f'Canal: {publicacao.canal_conectado(youtube)["nome"]}')
    feitos = publicacao.publicar(todos, canal, youtube, alvo=alvo, simular=args.simular)
    if feitos:
        print(f"{len(feitos)} episódio(s) enviados ou completados. Veja: python -m estudio agenda")


def cmd_automatico(args, cfg) -> None:
    """O que a tarefa diária do Windows roda: métricas, produção da fila de episódios e envio."""
    canal = carregar_canal(args.canal)
    auto = canal.automacao
    print(f"=== produção automática, {datetime.now():%d/%m/%Y %H:%M} ===")
    pendencias: list[str] = []

    def etapa(nome: str, funcao) -> bool:
        try:
            funcao()
            return True
        except (SystemExit, CotaEsgotada, CotaGeminiEsgotada) as erro:
            pendencias.append(f"{nome}: {erro}")
        except Exception:
            pendencias.append(f"{nome}: erro inesperado (detalhes em dados\\publicacao.log)")
            traceback.print_exc()
        print(f"  [{nome}] {pendencias[-1]}")
        return False

    etapa("métricas", lambda: metricas.coletar(metricas.servico_analytics(),
                                                episodio.listar() + episodio.listar_shorts()))
    if auto.ligada and not auto.continua:  # na produção contínua, quem produz é o produtor
        for ep in automacao.em_andamento(episodio.listar()):  # retoma o que ficou pela metade
            print(f"--- continuando {ep.nome}")
            etapa(f"produção de {ep.nome}", lambda ep=ep: _produzir(ep, ep.carregar_roteiro(), canal))
        agora = datetime.now(timezone.utc)
        for _ in range(min(auto.novos_por_execucao,
                           automacao.precisa_de_novos(episodio.listar(), agora, auto.manter_agendados))):
            print("--- episódio novo")

            def novo():
                ep = _novo_episodio(cfg, canal, auto.nicho, auto.idioma, None, auto.minutos, args.nichos)
                _produzir(ep, ep.carregar_roteiro(), canal)

            if not etapa("episódio novo", novo):
                break

    def enviar():
        if not auto.continua:  # na produção contínua, aprovar é com o produtor
            for ep in episodio.listar():
                _aprovar_sozinho(ep, ep.carregar_roteiro(), canal)
        publicacao.publicar(episodio.listar() + episodio.listar_shorts(), canal, publicacao.servico())

    print("--- envio")
    etapa("envio", enviar)
    print("\n".join(publicacao.agenda(episodio.listar() + episodio.listar_shorts(), canal)))

    bloqueados = [ep.nome for ep in episodio.listar() + episodio.listar_shorts() if automacao.bloqueado(ep)]
    if bloqueados:
        pendencias.append(f"revisão bloqueou {', '.join(bloqueados)} (veja revisao.json; corrija ou aprove "
                          "com --discordo-da-ia)")
    conexao = publicacao.aviso_de_conexao()
    if "conectar" in conexao:
        pendencias.append(conexao)
    if pendencias:
        aviso.notificar("Estúdio de vídeos: precisa de você", " | ".join(pendencias))
        print("Pendências:\n  " + "\n  ".join(pendencias))


def cmd_produtor(args, cfg) -> None:
    from .produtor import Produtor

    Produtor(cfg, args.canal, args.nichos).rodar()


def cmd_short(args, cfg) -> None:
    """Um Short agora, fora da fila: do episódio indicado (ou do mais antigo que ainda rende Shorts)."""
    canal = carregar_canal(args.canal)
    lista = episodio.listar_shorts()
    if args.episodio:
        origem = episodio.localizar(args.episodio)
        ideia = shorts.proxima_ideia(origem, canal, lista)
        if not ideia and not (args.animal and args.formato):
            raise SystemExit(f"{origem.nome} já deu {canal.shorts.por_episodio} Short(s) (por_episodio no canal.toml).")
    else:
        fontes = shorts.fontes(episodio.listar(), canal, lista)
        if not fontes:
            raise SystemExit("Nenhum episódio pronto que ainda renda Shorts.")
        origem, ideia = fontes[0]
    animal = args.animal or ideia[0]
    formato = args.formato or ideia[1]
    if animal not in shorts.animais(origem.carregar_roteiro()):
        raise SystemExit(f"{animal} não tem som no episódio. Opções: {', '.join(shorts.animais(origem.carregar_roteiro()))}")
    short = shorts.novo_short(origem, animal, formato, canal, lista)
    if args.so_roteiro:
        return
    r = short.carregar_roteiro()
    shorts.etapas_na_placa(short, r, canal)
    shorts.finalizar(short, r, canal)


def cmd_pausar(args, cfg) -> None:
    automacao.ARQUIVO_PAUSA.parent.mkdir(parents=True, exist_ok=True)
    automacao.ARQUIVO_PAUSA.write_text("pausado pelo comando pausar\n", encoding="utf-8")
    print("Produção contínua pausada: o que está em andamento termina a etapa atual e para. Envios programados "
          "continuam. Para voltar: python -m estudio retomar")


def cmd_retomar(args, cfg) -> None:
    automacao.ARQUIVO_PAUSA.unlink(missing_ok=True)
    print("Produção contínua retomada.")


def cmd_agenda(args, cfg) -> None:
    canal = carregar_canal(args.canal)
    episodios, lista_shorts = episodio.listar(), episodio.listar_shorts()
    print("\n".join(publicacao.agenda(episodios + lista_shorts, canal)))
    if canal.automacao.continua:
        agora = datetime.now(timezone.utc)
        faltam, fim = automacao.faltam_para_a_semana(episodios, canal, agora)
        faltam_shorts = automacao.faltam_shorts_para_a_semana(lista_shorts, canal, agora)
        andamento = len(automacao.em_andamento(episodios)) + len(automacao.em_andamento(lista_shorts))
        situacao = (f"faltam {faltam} episódio(s) e {faltam_shorts} Short(s) para cobrir até domingo, {fim:%d/%m}"
                    if faltam or faltam_shorts else
                    f"horários até domingo, {fim:%d/%m}, cobertos" + (f" ({andamento} terminando)" if andamento else
                                                                      "; parada até a semana virar"))
        print(f"Produção: {situacao}" + (" — PAUSADA (python -m estudio retomar)" if automacao.pausado() else ""))
    print(publicacao.aviso_de_conexao())


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="estudio", description="Estúdio de vídeos infantis.")
    ap.add_argument("--nichos", type=Path, default=RAIZ / "nichos.toml", help="arquivo de nichos")
    ap.add_argument("--canal", type=Path, default=RAIZ / "canal.toml", help="identidade do canal")
    sub = ap.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("pesquisar", help="coleta vídeos por nicho e calcula os outliers")
    p.add_argument("--somente", metavar="NICHO", help="pesquisa só este nicho e atualiza os dados existentes")
    p.set_defaults(func=cmd_pesquisar)

    e = sub.add_parser("estrutura", help="extrai a estrutura dos maiores outliers com o Gemini")
    e.add_argument("--top", type=int, default=3, help="vídeos por nicho e idioma (padrão: 3)")
    e.add_argument("--max-min", type=float, default=15, help="ignora vídeos mais longos que isso (padrão: 15)")
    e.add_argument("--nicho")
    e.add_argument("--idioma", choices=IDIOMAS)
    e.set_defaults(func=cmd_estrutura)

    r = sub.add_parser("relatorio", help="regera o relatório a partir dos dados salvos")
    r.set_defaults(func=cmd_relatorio)

    ro = sub.add_parser("roteiro", help="escreve o roteiro de um episódio novo")
    ro.add_argument("--nicho", required=True)
    ro.add_argument("--idioma", choices=IDIOMAS, default="pt")
    ro.add_argument("--tema", help='ideia do episódio, ex.: "Lulu aprende a dividir os brinquedos"')
    ro.add_argument("--minutos", type=float, default=4, help="duração aproximada (padrão: 4)")
    ro.set_defaults(func=cmd_roteiro)

    ajuda_ep = "nome (ou parte do nome) do episódio; padrão: o último criado"
    for nome, func, ajuda in [
        ("narrar", cmd_narrar, "gera a narração das cenas (Gemini TTS)"),
        ("imagens", cmd_imagens, "gera as imagens das cenas na placa de vídeo"),
        ("trilha", cmd_trilha, "compõe a trilha original do episódio com IA na placa de vídeo"),
        ("montar", cmd_montar, "monta vídeo, legendas, miniatura e texto de publicação"),
        ("produzir", cmd_produzir, "imagens + narração + trilha + montagem, refazendo só o que mudou"),
    ]:
        s = sub.add_parser(nome, help=ajuda)
        s.add_argument("episodio", nargs="?", help=ajuda_ep)
        s.set_defaults(func=func)

    ep = sub.add_parser("episodios", help="lista os episódios e em que etapa cada um está")
    ep.add_argument("--todos", action="store_true", help="lista todos, não só os últimos 15")
    ep.set_defaults(func=cmd_episodios)

    sh = sub.add_parser("short", help="faz um Short vertical agora, de um episódio pronto")
    sh.add_argument("--episodio", help="nome (ou parte) do episódio; sem isso, o mais antigo que ainda rende Shorts")
    sh.add_argument("--formato", choices=shorts.FORMATOS, help="sem isso, o escolhido pelo engajamento")
    sh.add_argument("--animal", help='animal do episódio (em inglês, ex.: "duck"); sem isso, o menos usado')
    sh.add_argument("--so-roteiro", action="store_true", help="só escreve o roteiro, sem produzir")
    sh.set_defaults(func=cmd_short)

    pr = sub.add_parser("produtor", help="produção contínua: episódio atrás de episódio, sem pausa")
    pr.set_defaults(func=cmd_produtor)
    pa = sub.add_parser("pausar", help="pausa a produção contínua (envios programados continuam)")
    pa.set_defaults(func=cmd_pausar)
    rt = sub.add_parser("retomar", help="retoma a produção contínua")
    rt.set_defaults(func=cmd_retomar)

    rv = sub.add_parser("revisar", help="confere as regras do YouTube antes de publicar")
    rv.add_argument("episodio", nargs="?", help=ajuda_ep)
    rv.add_argument("--sem-ia", action="store_true", help="só as checagens automáticas (não vale para aprovar)")
    rv.set_defaults(func=cmd_revisar)

    apr = sub.add_parser("aprovar", help="registra a sua aprovação do episódio revisado")
    apr.add_argument("episodio", nargs="?", help=ajuda_ep)
    apr.add_argument("--ciente", action="store_true", help="conferi os avisos da revisão")
    apr.add_argument("--discordo-da-ia", metavar="MOTIVO", help="aprova apesar de um bloqueio apontado só pela IA")
    apr.set_defaults(func=cmd_aprovar)

    co = sub.add_parser("conectar", help="autoriza o envio para o seu canal (abre o navegador uma vez)")
    co.set_defaults(func=cmd_conectar)

    pu = sub.add_parser("publicar", help="envia os episódios aprovados e agenda a publicação")
    pu.add_argument("episodio", nargs="?", help="só este episódio; padrão: todos os aprovados")
    pu.add_argument("--simular", action="store_true", help="mostra o que seria enviado e quando, sem enviar")
    pu.set_defaults(func=cmd_publicar)

    ag = sub.add_parser("agenda", help="vídeos agendados e publicados, e os próximos horários livres")
    ag.set_defaults(func=cmd_agenda)

    au = sub.add_parser("automatico", help="o que a tarefa diária roda: métricas, produção da fila e envio")
    au.set_defaults(func=cmd_automatico)

    es = sub.add_parser("estilos", help="aprende o estilo musical dos vídeos que se destacam (Gemini)")
    es.add_argument("--top", type=int, default=2, help="vídeos por nicho e idioma (padrão: 2)")
    es.add_argument("--max-min", type=float, default=15, help="ignora vídeos mais longos que isso (padrão: 15)")
    es.add_argument("--nicho")
    es.add_argument("--idioma", choices=IDIOMAS)
    es.set_defaults(func=cmd_estilos)

    me = sub.add_parser("metricas", help="busca a retenção dos seus vídeos e mostra o que está funcionando")
    me.add_argument("--sem-coletar", action="store_true", help="só mostra o que já foi coletado")
    me.set_defaults(func=cmd_metricas)

    args = ap.parse_args(argv)
    try:
        args.func(args, carregar_nichos(args.nichos))
    except (CotaEsgotada, CotaGeminiEsgotada) as erro:
        raise SystemExit(str(erro))


if __name__ == "__main__":
    main()
