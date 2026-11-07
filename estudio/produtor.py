"""Produção contínua: episódio atrás de episódio, sem pausa, até cobrir os horários de publicação
desta semana e da seguinte ([automacao] semanas_adiantadas); aí para e volta sozinho quando a semana
vira e faltam episódios. Enquanto produz, são três linhas de trabalho ao mesmo tempo para nenhuma
parte do computador ficar parada:

- roteiro: começa episódios novos só enquanto faltar episódio para a semana-alvo, com até
  [automacao] roteiros_adiantados roteiros à frente (Gemini; quando a cota acaba, o modelo local,
  que espera a vez da placa);
- placa: imagens, narração, sons e trilha, um episódio por vez, sem parar;
- montagem: monta o vídeo (vários encodes em paralelo no processador), revisa, aprova e apaga os
  intermediários.

O envio não é daqui: a tarefa diária sobe os aprovados nos horários programados. Tudo é retomável:
se o computador desligar, cada linha continua do ponto em que o episódio estava. Para pausar:
`python -m estudio pausar` (e `retomar`)."""
from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import automacao, aviso, conformidade, episodio, fluxo, imagens, recursos, shorts
from .config import DADOS, carregar_canal
from .gemini import CotaGeminiEsgotada

OCIOSO_S = 20             # sem trabalho, espera isso antes de olhar de novo
ESPERA_SEM_COTA_S = 1800  # sem Gemini nem modelo local para o roteiro/revisão, tenta de novo em 30 min
TENTATIVAS = 3            # falhas seguidas num episódio antes de tirá-lo da fila (e avisar)
AVISO_REPETIDO_S = 3600   # o mesmo aviso do Windows, no máximo uma vez por hora
ESPERA_SEMANA_COBERTA_S = 300  # semana coberta: confere de novo a cada 5 min (episódio bloqueado, grade nova)
ESPERA_SHORT_FALHOU_S = 600    # roteiro de Short veio incompleto: tenta de novo em 10 min (sem gastar a cota)
LIBERAR_MODELO_APOS_S = 600    # placa sem trabalho há 10 min: tira o modelo de imagem da memória
ARQUIVO_ESTADO = DADOS / "produtor.json"  # até quando a semana já foi anunciada como coberta
PASTA_CODIGO = Path(__file__).parent
CONFERIR_CODIGO_S = 30   # código do estúdio mudou: termina as etapas em andamento e reinicia com ele


def versao_do_codigo(pasta: Path = PASTA_CODIGO) -> float:
    return max(arquivo.stat().st_mtime for arquivo in pasta.glob("*.py"))


class Produtor:
    def __init__(self, cfg, arquivo_canal: Path, arquivo_nichos: Path, saida=print):
        self.cfg, self.arquivo_canal, self.arquivo_nichos, self._saida = cfg, arquivo_canal, arquivo_nichos, saida
        self.parar = threading.Event()
        self._falhas: dict[tuple[str, str], int] = {}
        self._espera_ate: dict[str, float] = {}
        self._avisos: dict[str, float] = {}
        self.feitos = {"roteiro": 0, "placa": 0, "montagem": 0}
        self._placa_livre_desde = time.monotonic()

    # ---------- infraestrutura ----------

    def agora(self) -> datetime:
        return datetime.now(timezone.utc)

    def _log(self, faixa: str):
        return lambda msg: self._saida(f"[{datetime.now():%d/%m %H:%M:%S} {faixa}] {msg}")

    def _avisar(self, chave: str, texto: str) -> None:
        if time.monotonic() - self._avisos.get(chave, -AVISO_REPETIDO_S) >= AVISO_REPETIDO_S:
            self._avisos[chave] = time.monotonic()
            aviso.notificar("Estúdio de vídeos: produção", texto)

    def _falhou(self, faixa: str, ep: episodio.Episodio, erro: BaseException, log) -> None:
        chave = (faixa, ep.nome)
        self._falhas[chave] = self._falhas.get(chave, 0) + 1
        log(f"{ep.nome}: falhou ({self._falhas[chave]}/{TENTATIVAS}): {erro}")
        if self._falhas[chave] >= TENTATIVAS:
            automacao.registrar_erro(ep, f"{faixa}: {erro}")
            self._falhas.pop(chave, None)
            self._avisar(f"erro-{ep.nome}", f"{ep.nome} saiu da fila depois de {TENTATIVAS} falhas ({erro}). "
                         "Detalhes em dados\\produtor.log; mude o roteiro ou apague etapas.json para tentar de novo.")

    def _laco(self, faixa: str, passo) -> None:
        log = self._log(faixa)
        ultimo_erro = ""
        while not self.parar.is_set():
            if automacao.pausado():
                self.parar.wait(OCIOSO_S)
                continue
            if time.monotonic() < self._espera_ate.get(faixa, 0):
                self.parar.wait(OCIOSO_S)
                continue
            try:
                trabalhou = passo(carregar_canal(self.arquivo_canal), log)  # relê o canal.toml a cada passo
            except Exception as erro:
                texto = traceback.format_exc()
                if texto == ultimo_erro:  # o mesmo erro de novo: avisa, sem encher o registro
                    self._avisar(f"inesperado-{faixa}", f"A produção ({faixa}) está parada por um erro que se "
                                 f"repete: {erro}. Detalhes em dados\\produtor.log.")
                else:
                    log("erro inesperado:\n" + texto)
                ultimo_erro, trabalhou = texto, False
            else:
                ultimo_erro = ""
            if trabalhou:
                self.feitos[faixa] += 1
            else:
                self.parar.wait(OCIOSO_S)

    def rodar(self) -> None:
        log = self._log("produtor")
        self._trava = recursos.instancia_unica("produtor")
        if self._trava is None:
            log("outro produtor já está rodando; este fica de reserva e assume se o outro fechar")
        while self._trava is None:
            time.sleep(60)
            self._trava = recursos.instancia_unica("produtor")
        log("produção contínua iniciada (pausar: python -m estudio pausar)")
        linhas = [threading.Thread(target=self._laco, args=(nome, passo), name=nome, daemon=True)
                  for nome, passo in (("roteiro", self.passo_roteiro), ("placa", self.passo_placa),
                                      ("montagem", self.passo_montagem))]
        for linha in linhas:
            linha.start()
        versao, conferido = versao_do_codigo(), time.monotonic()
        try:
            while any(linha.is_alive() for linha in linhas):
                time.sleep(1)
                if not self.parar.is_set() and time.monotonic() - conferido >= CONFERIR_CODIGO_S:
                    conferido = time.monotonic()
                    if versao_do_codigo() != versao:
                        log("código do estúdio atualizado: termino as etapas em andamento e reinicio com ele")
                        self.parar.set()
        except KeyboardInterrupt:
            log("parando: as etapas em andamento ficam salvas e continuam na próxima vez")
            self.parar.set()

    # ---------- as três linhas ----------

    def _semana_coberta(self, episodios, lista_shorts, fim: datetime, agora: datetime, log) -> None:
        """Avisa uma vez por semana-alvo, quando o último vídeo dela termina."""
        if automacao.em_andamento(episodios) or automacao.em_andamento(lista_shorts):
            return
        estado = json.loads(ARQUIVO_ESTADO.read_text(encoding="utf-8")) if ARQUIVO_ESTADO.exists() else {}
        if estado.get("coberta_ate") == fim.isoformat():
            return

        def no_estoque(videos) -> int:
            return sum(1 for v in videos if not v.publicacao.exists() and conformidade.aprovado(v))

        hoje = agora.astimezone(fim.tzinfo).date()
        segunda = hoje + timedelta(days=7 - hoje.weekday())
        texto = (f"Posts até domingo, {fim:%d/%m}, prontos ({no_estoque(episodios)} episódio(s) e "
                 f"{no_estoque(lista_shorts)} Short(s) no estoque). A produção parou e volta sozinha quando faltar "
                 f"vídeo para a semana seguinte (confere a partir de segunda, {segunda:%d/%m}).")
        log(texto)
        aviso.notificar("Estúdio de vídeos: semana pronta", texto)
        ARQUIVO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_ESTADO.write_text(json.dumps({**estado, "coberta_ate": fim.isoformat()}), encoding="utf-8")

    def passo_roteiro(self, canal, log) -> bool:
        auto = canal.automacao
        episodios, lista_shorts = episodio.listar(), episodio.listar_shorts()
        agora = self.agora()
        faltam, fim = automacao.faltam_para_a_semana(episodios, canal, agora)
        faltam_shorts = automacao.faltam_shorts_para_a_semana(lista_shorts, canal, agora)
        fontes = shorts.fontes(episodios, canal, lista_shorts) if faltam_shorts else []
        if not faltam and not fontes:  # Shorts que faltam e ainda não têm episódio pronto esperam por ele
            if not faltam_shorts or not automacao.em_andamento(episodios):  # nenhum episódio a caminho
                self._semana_coberta(episodios, lista_shorts, fim, agora, log)
            self._espera_ate["roteiro"] = time.monotonic() + ESPERA_SEMANA_COBERTA_S
            return False
        livre = automacao.espaco_livre_gb()
        if livre < auto.espaco_minimo_gb:
            log(f"só {livre:.0f} GB livres (mínimo {auto.espaco_minimo_gb:.0f}); produção esperando espaço")
            self._avisar("disco", f"Disco quase cheio ({livre:.0f} GB livres): a produção está esperando. Libere "
                         "espaço (episódios antigos já publicados em dados\\episodios podem ir para outro disco).")
            return False
        try:
            if faltam and len(automacao.para_a_placa(episodios)) < auto.roteiros_adiantados:
                fluxo.novo_episodio(self.cfg, canal, auto.nicho, auto.idioma, None, auto.minutos, self.arquivo_nichos, log)
                if faltam > 1:
                    log(f"faltam mais {faltam - 1} episódio(s) para cobrir até domingo, {fim:%d/%m}")
                return True
            if fontes and len(automacao.para_a_placa(lista_shorts)) < auto.roteiros_adiantados:
                origem, (animal, formato) = fontes[0]
                shorts.novo_short(origem, animal, formato, canal, lista_shorts, log=log)
                return True
        except CotaGeminiEsgotada as erro:
            log(f"sem modelo para escrever agora ({erro}); tento de novo em 30 min")
            self._espera_ate["roteiro"] = time.monotonic() + ESPERA_SEM_COTA_S
        except RuntimeError as erro:
            log(f"{erro}; tento de novo em 10 min")
            self._espera_ate["roteiro"] = time.monotonic() + ESPERA_SHORT_FALHOU_S
        return False

    def passo_placa(self, canal, log) -> bool:
        fila = automacao.para_a_placa(episodio.listar()) or automacao.para_a_placa(episodio.listar_shorts())
        if not fila:
            if (time.monotonic() - self._placa_livre_desde > LIBERAR_MODELO_APOS_S
                    and imagens.liberar_compartilhados()):
                log("placa sem trabalho: modelo de imagem fora da memória até o próximo episódio")
            return False
        ep = fila[0]
        log(f"--- {ep.nome}: imagens, narração, sons e " + ("canção" if ep.e_short else "trilha"))
        try:
            (shorts.etapas_na_placa if ep.e_short else fluxo.etapas_na_placa)(ep, ep.carregar_roteiro(), canal, log)
        except (Exception, SystemExit) as erro:
            self._falhou("placa", ep, erro, log)
            return True  # tenta o próximo da fila em vez de esperar
        finally:
            self._placa_livre_desde = time.monotonic()
        automacao.marcar_placa_pronta(ep)
        return True

    def passo_montagem(self, canal, log) -> bool:
        fila = automacao.para_finalizar(episodio.listar()) or automacao.para_finalizar(episodio.listar_shorts())
        if not fila:
            return False
        ep = fila[0]
        log(f"--- {ep.nome}: montagem, revisão e aprovação")
        try:
            (shorts.finalizar if ep.e_short else fluxo.finalizar)(ep, ep.carregar_roteiro(), canal, log)
        except CotaGeminiEsgotada as erro:
            log(f"sem modelo para revisar agora ({erro}); tento de novo em 30 min")
            self._espera_ate["montagem"] = time.monotonic() + ESPERA_SEM_COTA_S
            return False
        except (Exception, SystemExit) as erro:
            self._falhou("montagem", ep, erro, log)
            return True
        automacao.marcar_finalizado(ep)
        if conformidade.aprovado(ep):
            log(f"{ep.nome}: pronto para os horários programados; {automacao.limpar_intermediarios(ep) / 1e6:.0f} MB "
                "de intermediários apagados")
        elif automacao.bloqueado(ep):
            self._avisar(f"bloqueio-{ep.nome}", f"A revisão bloqueou {ep.nome} (veja revisao.json). A produção segue "
                         "com os próximos.")
        return True
