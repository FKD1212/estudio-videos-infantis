"""Fase 5b: envio automático para o YouTube. Sobe os episódios aprovados com "conteúdo para
crianças" marcado, miniatura e legendas, e agenda cada um no próximo horário livre do
canal.toml, sem passar do máximo por semana. O YouTube torna o vídeo público na hora marcada."""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import conformidade
from .config import CACHE, DADOS, RAIZ, Canal, ConfigPublicacao
from .episodio import Episodio, assinatura
from .montagem import PASTA_MUSICA
from .youtube import CotaEsgotada

ESCOPOS = [
    "https://www.googleapis.com/auth/youtube.upload",         # enviar vídeos e miniaturas
    "https://www.googleapis.com/auth/youtube.force-ssl",      # enviar legendas e conferir o status
    "https://www.googleapis.com/auth/yt-analytics.readonly",  # ler retenção e visualizações
]
ARQUIVO_CLIENTE = RAIZ / "client_secret.json"
ARQUIVO_TOKEN = CACHE / "youtube_oauth.json"
ARQUIVO_CONEXAO = CACHE / "youtube_conexao.json"  # quando o canal foi conectado
ARQUIVO_ESTADO_CANAL = DADOS / "canal_youtube.json"  # o que já foi aplicado ao canal e as playlists
VALIDADE_EM_TESTE_DIAS = 7  # app do Google Cloud "Em teste": o acesso expira e precisa de novo "conectar"
MARGEM = timedelta(hours=2)  # tempo para o YouTube processar o vídeo antes de ir ao ar
DIAS = {"seg": 0, "ter": 1, "qua": 2, "qui": 3, "sex": 4, "sab": 5, "sáb": 5, "dom": 6}
NOMES_DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
NOME_LEGENDA = {"pt": "Português", "en": "English"}
MOTIVOS_DE_COTA = {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"}


# ---------- acesso ao canal ----------

def servico(interativo: bool = False, refazer: bool = False):
    """Cliente da YouTube API autorizado no seu canal."""
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=credenciais(interativo, refazer), cache_discovery=False)


def credenciais(interativo: bool = False, refazer: bool = False):
    """Acesso ao seu canal. Só abre o navegador em `conectar`; nas execuções automáticas usa o
    token salvo, que se renova sozinho."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credencial = None
    salvo = json.loads(ARQUIVO_TOKEN.read_text(encoding="utf-8")) if ARQUIVO_TOKEN.exists() else None
    # Um token de antes de uma permissão nova (ex.: métricas) precisa de um novo "conectar".
    if salvo and not refazer and set(ESCOPOS) <= set(salvo.get("scopes") or []):
        credencial = Credentials.from_authorized_user_info(salvo, ESCOPOS)
        if not credencial.valid and credencial.refresh_token:
            try:
                credencial.refresh(Request())
            except RefreshError:
                credencial = None
    if not credencial or not credencial.valid:
        if not interativo:
            raise SystemExit(
                "O acesso ao canal não foi feito, expirou ou precisa de uma permissão nova. Rode: python -m estudio "
                f"conectar (com o app do Google Cloud \"Em teste\", isso acontece a cada {VALIDADE_EM_TESTE_DIAS} dias)")
        if not ARQUIVO_CLIENTE.exists():
            raise SystemExit(f'Falta o arquivo {ARQUIVO_CLIENTE.name}. Veja "Upload automático" no README.')
        from google_auth_oauthlib.flow import InstalledAppFlow

        fluxo = InstalledAppFlow.from_client_secrets_file(str(ARQUIVO_CLIENTE), ESCOPOS)
        credencial = fluxo.run_local_server(port=0, prompt="consent")
        ARQUIVO_CONEXAO.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_CONEXAO.write_text(json.dumps({"conectado_em": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    ARQUIVO_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_TOKEN.write_text(credencial.to_json(), encoding="utf-8")
    return credencial


def aviso_de_conexao(agora: datetime | None = None, arquivo: Path | None = None) -> str:
    """Lembrete para quem usa o app do Google Cloud "Em teste", em que o acesso dura 7 dias."""
    arquivo = arquivo or ARQUIVO_CONEXAO
    if not arquivo.exists():
        return "Canal ainda não conectado. Rode: python -m estudio conectar"
    conectado = datetime.fromisoformat(json.loads(arquivo.read_text(encoding="utf-8"))["conectado_em"])
    dias = ((agora or datetime.now(timezone.utc)) - conectado).days
    if dias >= VALIDADE_EM_TESTE_DIAS - 1:
        return (f"Canal conectado há {dias} dias. Se o app do Google Cloud estiver \"Em teste\", o acesso expira "
                f"com {VALIDADE_EM_TESTE_DIAS} dias: rode python -m estudio conectar")
    return f"Canal conectado há {dias} dia(s)."


def canal_conectado(youtube) -> dict:
    itens = _executar(youtube.channels().list(part="snippet", mine=True)).get("items", [])
    if not itens:
        raise SystemExit("Essa conta Google não tem canal no YouTube.")
    return {"id": itens[0]["id"], "nome": itens[0]["snippet"]["title"]}


def _motivo(erro) -> str:
    try:
        return json.loads(erro.content)["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def _executar(pedido, metodo: str = "execute", **kwargs):
    """Executa um pedido da API; cota esgotada vira CotaEsgotada (para tudo e retoma amanhã)."""
    from googleapiclient.errors import HttpError

    try:
        return getattr(pedido, metodo)(**kwargs)
    except HttpError as erro:
        if _motivo(erro) in MOTIVOS_DE_COTA:
            raise CotaEsgotada("Cota diária da YouTube API esgotada. Ela reinicia à meia-noite do Pacífico; "
                               "o que faltou é enviado na próxima execução.") from erro
        if _motivo(erro) == "accessNotConfigured":
            link = re.search(r"https://console\.developers\.google\.com/apis/api/[^\s\"'.]+(?:\.[^\s\"'.]+)*/overview\?project=\d+",
                             erro.content.decode(errors="replace") if isinstance(erro.content, bytes) else str(erro.content))
            raise SystemExit("Essa API não está ativada no seu projeto do Google Cloud. Ative em: "
                             f"{link.group(0) if link else 'console.cloud.google.com > APIs e serviços > Biblioteca'} "
                             "(clique em \"Ativar\", espere uns minutos e rode de novo)") from erro
        raise


# ---------- agenda ----------

def _ler_horario(texto: str) -> tuple[int, time]:
    try:
        dia, hora = texto.split()
        horas, minutos = hora.split(":")
        return DIAS[dia.lower()], time(int(horas), int(minutos))
    except (ValueError, KeyError):
        raise SystemExit(f'Horário inválido em canal.toml: "{texto}" (use, por exemplo, "ter 10:00")')


def _semana(quando: datetime) -> tuple[int, int]:
    return quando.isocalendar()[:2]


def proximos_horarios(cfg: ConfigPublicacao, ocupados: list[datetime], agora: datetime,
                      quantidade: int) -> list[datetime]:
    """Os próximos horários livres da grade, sem passar de max_por_semana em nenhuma semana
    (contando o que já está agendado ou publicado)."""
    fuso = ZoneInfo(cfg.fuso)
    grade = sorted((_ler_horario(h) for h in cfg.horarios), key=lambda h: h[1])
    por_semana = Counter(_semana(q.astimezone(fuso)) for q in ocupados)
    tomados = {q.astimezone(timezone.utc) for q in ocupados}
    escolhidos: list[datetime] = []
    dia = agora.astimezone(fuso).date()
    for _ in range(366):
        for dia_da_semana, hora in grade:
            if len(escolhidos) == quantidade:
                return escolhidos
            quando = datetime.combine(dia, hora, fuso)
            if (dia.weekday() != dia_da_semana or quando < agora + MARGEM
                    or quando.astimezone(timezone.utc) in tomados or por_semana[_semana(quando)] >= cfg.max_por_semana):
                continue
            escolhidos.append(quando)
            por_semana[_semana(quando)] += 1
        dia += timedelta(days=1)
    return escolhidos


def horarios_de_shorts(canal: Canal) -> ConfigPublicacao:
    """A grade dos Shorts ([shorts] no canal.toml), com o fuso e as regras de [publicacao]."""
    return replace(canal.publicacao, horarios=canal.shorts.horarios, max_por_semana=canal.shorts.max_por_semana)


def agendar_shorts(novos: list[Episodio], todos: list[Episodio], canal: Canal,
                   agora: datetime) -> list[tuple[Episodio, datetime]]:
    """Horário de cada Short aprovado: o próximo livre da grade dos Shorts depois que o episódio de
    onde ele saiu for ao ar (antes disso o link da chamada levaria a um vídeo privado). Os Shorts
    do episódio mais antigo vão primeiro."""
    cfg = horarios_de_shorts(canal)
    ocupados = [datetime.fromisoformat(r["publicar_em"]) for e in todos if e.e_short and (r := e.ler(e.publicacao))]
    prontos = []
    for short in novos:
        origem = short.origem.ler(short.origem.publicacao)
        if origem:  # o episódio já subiu (e tem link)
            prontos.append((datetime.fromisoformat(origem["publicar_em"]), short))
    plano = []
    for depois_de, short in sorted(prontos, key=lambda x: (x[0], x[1].nome)):
        horario = proximos_horarios(cfg, ocupados, max(agora, depois_de - MARGEM), 1)
        if horario:
            plano.append((short, horario[0]))
            ocupados.append(horario[0])
    return plano


def _titulo_playlist(ep: Episodio, roteiro: dict, canal: Canal | None) -> str | None:
    if not canal:
        return None
    if ep.e_short:
        return canal.shorts.playlist or None
    return canal.youtube.playlists.get(roteiro.get("nicho", ""))


# ---------- envio ----------

def corpo(roteiro: dict, canal: Canal, quando: datetime, descricao: str) -> dict:
    yt, cfg = roteiro["youtube"], canal.publicacao
    snippet = {"title": yt["titulo"], "description": descricao, "categoryId": cfg.categoria,
               "defaultLanguage": canal.narracao[roteiro["idioma"]].idioma}
    if yt["tags"]:
        snippet["tags"] = yt["tags"]
    return {
        "snippet": snippet,
        "status": {
            "privacyStatus": "private",  # obrigatório para agendar; vira público em publishAt
            "publishAt": quando.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "selfDeclaredMadeForKids": True,
            "containsSyntheticMedia": cfg.conteudo_sintetico,
            "license": "youtube",
            "embeddable": True,
        },
    }


def enviar_video(youtube, ep: Episodio, corpo_: dict, log=print) -> str:
    from googleapiclient.http import MediaFileUpload

    midia = MediaFileUpload(str(ep.video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    pedido = youtube.videos().insert(part="snippet,status", body=corpo_, media_body=midia)
    resposta, ultimo = None, -1
    while resposta is None:
        progresso, resposta = _executar(pedido, "next_chunk", num_retries=5)
        if progresso and int(progresso.progress() * 4) != ultimo:
            ultimo = int(progresso.progress() * 4)
            log(f"    {progresso.progress():.0%} enviado")
    return resposta["id"]


# ---------- canal e playlists ----------

def _estado(arquivo: Path | None = None) -> dict:
    arquivo = arquivo or ARQUIVO_ESTADO_CANAL
    return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}


def _gravar_estado(estado: dict, arquivo: Path | None = None) -> None:
    arquivo = arquivo or ARQUIVO_ESTADO_CANAL
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def configurar_canal(youtube, canal: Canal, log=print, arquivo: Path | None = None) -> bool:
    """Aplica a seção [youtube] do canal.toml ao canal: descrição, palavras-chave, idioma e audiência
    "conteúdo para crianças". Só chama a API quando algo mudou desde a última vez."""
    from googleapiclient.errors import HttpError

    y = canal.youtube
    desejado = assinatura(y.descricao, y.palavras_chave, y.idioma, y.feito_para_criancas)
    estado = _estado(arquivo)
    if estado.get("canal") == desejado:
        return False
    atual = _executar(youtube.channels().list(part="brandingSettings,status", mine=True))["items"][0]
    branding = atual.get("brandingSettings", {})
    dados = dict(branding.get("channel", {}))  # a API apaga o que não for reenviado
    if y.descricao:
        dados["description"] = y.descricao
    if y.palavras_chave:
        dados["keywords"] = " ".join(f'"{p}"' if " " in p else p for p in y.palavras_chave)
    dados["defaultLanguage"] = y.idioma
    corpo_ = {"channel": dados, **({"watch": branding["watch"]} if "watch" in branding else {})}
    _executar(youtube.channels().update(part="brandingSettings", body={"id": atual["id"], "brandingSettings": corpo_}))
    log("  canal: descrição, palavras-chave e idioma atualizados")
    if y.feito_para_criancas and not atual.get("status", {}).get("selfDeclaredMadeForKids"):
        try:
            _executar(youtube.channels().update(part="status", body={
                "id": atual["id"], "status": {"selfDeclaredMadeForKids": True}}))
            log("  canal marcado como conteúdo para crianças")
        except HttpError as erro:
            estado["criancas_pela_api"] = False
            log(f"  a API não aceitou marcar o canal como infantil ({_motivo(erro) or erro.resp.status}). Marque no "
                "YouTube Studio: Configurações > Canal > Configurações avançadas > Público > \"Sim, definir este "
                "canal como conteúdo para crianças\" (cada vídeo já sobe marcado como infantil)")
    estado["canal"] = desejado
    _gravar_estado(estado, arquivo)
    return True


def garantir_playlist(youtube, titulo: str, canal: Canal, log=print, arquivo: Path | None = None) -> str:
    """A playlist da série: reaproveita a que já existe com esse título ou cria uma pública."""
    estado = _estado(arquivo)
    ids = estado.setdefault("playlists", {})
    if titulo not in ids:
        itens = _executar(youtube.playlists().list(part="snippet", mine=True, maxResults=50)).get("items", [])
        existente = next((i["id"] for i in itens if i["snippet"]["title"] == titulo), None)
        if not existente:
            apresentacao = canal.youtube.descricao.split("\n\n")[0]
            existente = _executar(youtube.playlists().insert(part="snippet,status", body={
                "snippet": {"title": titulo, "description": apresentacao, "defaultLanguage": canal.youtube.idioma},
                "status": {"privacyStatus": "public"}}))["id"]
            log(f"  playlist criada: {titulo}")
        ids[titulo] = existente
        _gravar_estado(estado, arquivo)
    return ids[titulo]


def sincronizar_metadados(youtube, ep: Episodio, registro: dict, roteiro: dict, canal: Canal,
                          pasta_musica: Path = PASTA_MUSICA, log=print) -> bool:
    """Leva ao YouTube título, descrição, hashtags e tags que mudaram depois do envio (o vídeo não é
    reenviado). Antes, confere as regras de metadados: marca de terceiros ou promessa de saúde não sobe."""
    snippet = corpo(roteiro, canal, datetime.fromisoformat(registro["publicar_em"]),
                    conformidade.descricao_final(ep, roteiro, pasta_musica, canal.youtube.hashtags))["snippet"]
    desejado = assinatura(snippet)
    if registro.get("metadados") == desejado:
        return False
    bloqueios = [p for p in conformidade.checar(ep, roteiro, canal, [], pasta_musica) if p.gravidade == conformidade.BLOQUEIA
                 and p.regra in ("metadados", "marcas de terceiros", "promessa de saúde")]
    if bloqueios:
        log(f"  {ep.nome}: metadados novos não enviados: " + "; ".join(str(p) for p in bloqueios))
        return False
    _executar(youtube.videos().update(part="snippet", body={"id": registro["video_id"], "snippet": snippet}))
    registro.update(metadados=desejado, titulo=snippet["title"])
    ep.gravar(ep.publicacao, registro)
    log(f"  {ep.nome}: título, descrição e hashtags atualizados no YouTube")
    return True


def _completar(youtube, ep: Episodio, registro: dict, roteiro: dict, canal: Canal | None = None, log=print) -> None:
    """Miniatura, legendas, playlist da série e conferência do status. Falha em uma delas não perde o
    vídeo: fica marcada no publicacao.json e é tentada de novo na próxima execução."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    video_id = registro["video_id"]
    titulo_playlist = _titulo_playlist(ep, roteiro, canal)
    if titulo_playlist and not registro.get("playlist"):
        try:
            id_playlist = garantir_playlist(youtube, titulo_playlist, canal, log)
            _executar(youtube.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": id_playlist, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}))
            registro["playlist"] = id_playlist
            log(f"    adicionado à playlist \"{titulo_playlist}\"")
        except HttpError as erro:
            log(f"    playlist não atualizada ({_motivo(erro) or erro.resp.status}); tento de novo na próxima vez.")
    if not registro["miniatura"]:
        try:
            _executar(youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(
                str(ep.miniatura), mimetype="image/jpeg")))
            registro["miniatura"] = True
        except HttpError as erro:
            log(f"    miniatura não enviada ({_motivo(erro) or erro.resp.status}). Miniatura própria exige o "
                "canal verificado por telefone em youtube.com/verify; tento de novo na próxima vez.")
    if not registro["legendas"]:
        idioma = roteiro["idioma"]
        try:
            _executar(youtube.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": video_id, "language": registro["idioma"],
                                  "name": NOME_LEGENDA.get(idioma, idioma), "isDraft": False}},
                media_body=MediaFileUpload(str(ep.legendas), mimetype="application/octet-stream")))
            registro["legendas"] = True
        except HttpError as erro:
            log(f"    legendas não enviadas ({_motivo(erro) or erro.resp.status}); tento de novo na próxima vez.")
    itens = _executar(youtube.videos().list(part="status", id=video_id)).get("items", [])
    status = itens[0]["status"] if itens else {}
    registro["status"] = status
    ep.gravar(ep.publicacao, registro)
    if status.get("uploadStatus") in ("rejected", "failed"):
        motivo = status.get("rejectionReason") or status.get("failureReason") or "?"
        log(f"    ATENÇÃO: o YouTube recusou o vídeo ({motivo}). Veja no YouTube Studio.")
    elif status and not status.get("publishAt") and status.get("privacyStatus") != "public":
        log("    ATENÇÃO: o vídeo ficou privado sem data de publicação. Confira no YouTube Studio.")


def publicar(todos: list[Episodio], canal: Canal, youtube, alvo: list[Episodio] | None = None,
             agora: datetime | None = None, simular: bool = False, log=print,
             pasta_musica: Path = PASTA_MUSICA) -> list[dict]:
    """Envia os episódios aprovados ainda não enviados (de `alvo`, ou de todos) e completa os
    envios que ficaram pela metade. Devolve os registros de publicação desta execução."""
    agora = agora or datetime.now(timezone.utc)
    registros = {ep.nome: ep.ler(ep.publicacao) for ep in todos}
    ocupados = [datetime.fromisoformat(r["publicar_em"]) for r in registros.values() if r]
    candidatos = alvo if alvo is not None else todos
    feitos: list[dict] = []

    if not simular:
        try:
            configurar_canal(youtube, canal, log)
        except Exception as erro:  # a apresentação do canal não pode travar os envios
            log(f"  canal não configurado agora ({erro}); tento de novo na próxima vez")

    for ep in candidatos:  # já enviados: metadados novos e envios pela metade (miniatura, legenda, playlist)
        registro = registros.get(ep.nome)
        if not registro or simular:
            continue
        roteiro = ep.carregar_roteiro()
        sincronizar_metadados(youtube, ep, registro, roteiro, canal, pasta_musica, log)
        tem_playlist = registro.get("playlist") or not _titulo_playlist(ep, roteiro, canal)
        if not (registro["miniatura"] and registro["legendas"] and tem_playlist):
            log(f"  completando {ep.nome}")
            _completar(youtube, ep, registro, roteiro, canal, log)
            feitos.append(registro)

    novos = []
    for ep in candidatos:
        if registros.get(ep.nome) or not ep.aprovacao.exists():
            continue
        if not conformidade.aprovado(ep):
            log(f"  {ep.nome}: mudou depois da aprovação; rode revisar e aprovar de novo")
            continue
        novos.append(ep)
    novos.sort(key=lambda e: e.ler(e.aprovacao)["aprovado_em"])
    if not novos and not feitos:
        log("  nenhum episódio aprovado aguardando envio")
    episodios_novos = [ep for ep in novos if not ep.e_short]
    horarios = proximos_horarios(canal.publicacao, ocupados, agora, len(episodios_novos))

    fuso = ZoneInfo(canal.publicacao.fuso)
    horizonte = agora + timedelta(days=canal.publicacao.antecedencia_dias)
    em_estoque = 0

    def enviar(ep: Episodio, quando: datetime) -> None:
        nonlocal em_estoque
        if quando > horizonte:  # sobe só quando o horário dele estiver perto; até lá, segue ajustável
            em_estoque += 1
            return
        roteiro = ep.carregar_roteiro()
        quando_local = quando.astimezone(fuso)
        if simular:
            log(f"  {ep.nome} -> vai ao ar em {quando_local:%d/%m/%Y %H:%M}")
            return
        log(f"  enviando {ep.nome} (vai ao ar em {quando_local:%d/%m/%Y %H:%M})")
        corpo_ = corpo(roteiro, canal, quando, conformidade.descricao_final(
            ep, roteiro, pasta_musica, canal.youtube.hashtags))
        video_id = enviar_video(youtube, ep, corpo_, log)
        registro = {
            "metadados": assinatura(corpo_["snippet"]),
            "video_id": video_id,
            "url": f"https://youtu.be/{video_id}",
            "titulo": roteiro["youtube"]["titulo"],
            "idioma": canal.narracao[roteiro["idioma"]].idioma,
            "publicar_em": quando_local.isoformat(),
            "enviado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "assinatura": ep.ler(ep.aprovacao)["assinatura"],
            "miniatura": ep.e_short,  # o Short usa um quadro do próprio vídeo
            "legendas": False,
        }
        ep.gravar(ep.publicacao, registro)  # gravado já: se algo falhar depois, o vídeo não sobe duplicado
        _completar(youtube, ep, registro, roteiro, canal, log)
        feitos.append(registro)

    for ep, quando in zip(episodios_novos, horarios):
        enviar(ep, quando)
    if canal.shorts.ligado:  # depois dos episódios: um Short só sobe quando o episódio dele já subiu
        for short, quando in agendar_shorts([ep for ep in novos if ep.e_short], todos, canal, agora):
            enviar(short, quando)
    if em_estoque:
        log(f"  {em_estoque} vídeo(s) aprovados no estoque: sobem quando o horário deles estiver a até "
            f"{canal.publicacao.antecedencia_dias} dias")
    if len(horarios) < len(episodios_novos):
        log(f"  {len(episodios_novos) - len(horarios)} episódio(s) sem horário livre no próximo ano; amplie [publicacao] "
            "no canal.toml")
    return feitos


def agenda(todos: list[Episodio], canal: Canal, agora: datetime | None = None) -> list[str]:
    agora = agora or datetime.now(timezone.utc)
    fuso = ZoneInfo(canal.publicacao.fuso)
    linhas, ocupados, shorts_no_ar, shorts_agendados, ocupados_shorts = [], [], 0, [], []
    for ep in todos:
        registro = ep.ler(ep.publicacao)
        if registro and ep.e_short:
            quando = datetime.fromisoformat(registro["publicar_em"])
            ocupados_shorts.append(quando)
            if quando <= agora:
                shorts_no_ar += 1
            else:
                shorts_agendados.append((quando, registro["titulo"]))
        elif registro:
            quando = datetime.fromisoformat(registro["publicar_em"])
            ocupados.append(quando)
            situacao = "publicado" if quando <= agora else "agendado"
            pendente = "" if registro["miniatura"] and registro["legendas"] else " (falta miniatura ou legenda)"
            linhas.append((quando, f"{quando.astimezone(fuso):%d/%m %H:%M}  {situacao:9}  {registro['titulo']}  "
                                   f"{registro['url']}{pendente}"))
    linhas = [texto for _, texto in sorted(linhas)]
    aguardando = [ep.nome for ep in todos if not ep.e_short and not ep.publicacao.exists() and conformidade.aprovado(ep)]
    livres = proximos_horarios(canal.publicacao, ocupados, agora, 3)
    exemplos = ", ".join(aguardando[:3]) + (" ..." if len(aguardando) > 3 else "")
    linhas.append(f"Aprovados no estoque, esperando horário: {len(aguardando)}" + (f" ({exemplos})" if aguardando else ""))
    linhas.append("Próximos horários livres: " + ", ".join(
        f"{NOMES_DIAS[q.weekday()]} {q.astimezone(fuso):%d/%m %H:%M}" for q in livres))
    if canal.shorts.ligado:
        estoque = sum(1 for ep in todos if ep.e_short and not ep.publicacao.exists() and conformidade.aprovado(ep))
        proximo = min(shorts_agendados, default=None)
        linhas.append(f"Shorts: {shorts_no_ar} no ar, {len(shorts_agendados)} agendados, {estoque} no estoque"
                      + (f"; próximo {proximo[0].astimezone(fuso):%d/%m %H:%M} \"{proximo[1]}\"" if proximo else ""))
        livres_shorts = proximos_horarios(horarios_de_shorts(canal), ocupados_shorts, agora, 3)
        linhas.append("Próximos horários livres de Shorts: " + ", ".join(
            f"{NOMES_DIAS[q.weekday()]} {q.astimezone(fuso):%d/%m %H:%M}" for q in livres_shorts))
    return linhas
