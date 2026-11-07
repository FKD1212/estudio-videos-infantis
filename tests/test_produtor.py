"""Produção contínua: em que etapa cada episódio está, estoque, limpeza, travas e as linhas de trabalho."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from estudio import automacao, conformidade, fluxo, produtor, publicacao, recursos
from estudio.episodio import Episodio
from tests.test_producao import canal
from tests.test_publicacao import SP, FakeGemini, FakeYouTube, aprovar, episodio_pronto

SEM_LOG = lambda *_: None  # noqa: E731


class TestEtapas(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.raiz = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_placa_montagem_erro_e_mudanca_no_roteiro(self):
        ep = episodio_pronto(self.raiz, "a")
        self.assertEqual(automacao.para_a_placa([ep]), [ep])
        automacao.marcar_placa_pronta(ep)
        self.assertEqual((automacao.para_a_placa([ep]), automacao.para_finalizar([ep])), ([], [ep]))
        automacao.marcar_finalizado(ep)  # montado e revisado, esperando aprovação humana
        self.assertEqual(automacao.para_finalizar([ep]), [])

        r = ep.carregar_roteiro()  # roteiro editado: volta para a placa (que só refaz o que mudou)
        r["cenas"][0]["narracao"] = "Outra fala."
        ep.salvar_roteiro(r)
        self.assertEqual(automacao.para_a_placa([ep]), [ep])

        automacao.registrar_erro(ep, "placa: sem memória")
        self.assertTrue(automacao.com_erro(ep))
        self.assertEqual(automacao.em_andamento([ep]), [])
        r["cenas"][0]["narracao"] = "Mais uma."
        ep.salvar_roteiro(r)
        self.assertFalse(automacao.com_erro(ep))  # mudou o roteiro: tenta de novo

    def test_revisao_manda_refazer_imagens_defeituosas(self):
        ep = episodio_pronto(self.raiz, "d")
        automacao.marcar_placa_pronta(ep)
        gemini = FakeGemini([{"gravidade": "bloqueia", "regra": "imagem", "onde": "imagem 2.1",
                              "detalhe": "personagem em dobro", "sugestao": "refazer"}])
        original = conformidade.revisar

        def revisar(e, r, c, o, log=print, **_):
            return original(e, r, c, o, cliente=gemini, log=log, pasta_musica=self.raiz / "musica")

        with mock.patch.object(fluxo, "outros", return_value=[]), \
                mock.patch.object(conformidade, "revisar", side_effect=revisar):
            for tentativa in range(1, fluxo.MAX_CORRECOES + 1):
                r = ep.carregar_roteiro()
                fluxo.aprovar_sozinho(ep, r, canal(), log=SEM_LOG)
                r = ep.carregar_roteiro()
                self.assertEqual((r["cenas"][1]["semente"], r["correcoes_automaticas"]), (tentativa, tentativa))
                self.assertFalse(conformidade.aprovado(ep))
                automacao.marcar_finalizado(ep)
                self.assertFalse(automacao.placa_pronta(ep))  # volta para a placa
                automacao.marcar_placa_pronta(ep)
                self.assertEqual(automacao.para_finalizar([ep]), [ep])  # e depois para a montagem
            fluxo.aprovar_sozinho(ep, ep.carregar_roteiro(), canal(), log=SEM_LOG)
        self.assertEqual(ep.carregar_roteiro()["correcoes_automaticas"], fluxo.MAX_CORRECOES)  # parou de tentar
        self.assertTrue(automacao.bloqueado(ep))  # e o bloqueio fica para alguém decidir

    def test_ajuste_de_semente_nao_conta_como_toque_humano(self):
        ep = episodio_pronto(self.raiz, "e")
        ep.gravar(ep.roteiro_gerado, ep.carregar_roteiro())
        r = ep.carregar_roteiro()
        r["cenas"][0]["semente"], r["correcoes_automaticas"] = 3, 1
        ep.salvar_roteiro(r)
        regras = {p.regra for p in conformidade.checar(ep, r, canal(), [], self.raiz / "musica")}
        self.assertIn("toque humano", regras)

    def test_limpeza_mantem_o_que_vai_para_o_youtube(self):
        ep = episodio_pronto(self.raiz, "b")
        ep.montagem.mkdir(parents=True)
        (ep.montagem / "clipe_01_1.mp4").write_bytes(b"x" * 1000)
        (ep.montagem / "quadro_01_1.png").write_bytes(b"x" * 500)
        ep.pasta_trilha.mkdir(parents=True)
        (ep.pasta_trilha / "versao_1_1.wav").write_bytes(b"x" * 300)
        (ep.pasta_trilha / "trilha.wav").write_bytes(b"x")
        aprovar(ep, self.raiz)
        self.assertEqual(automacao.limpar_intermediarios(ep), 1800)
        self.assertTrue((ep.pasta_trilha / "trilha.wav").exists() and ep.video.exists())
        self.assertTrue(conformidade.aprovado(ep))

    def test_aprovacao_conferida_sem_reler_o_video(self):
        ep = episodio_pronto(self.raiz, "c")
        aprovar(ep, self.raiz)
        with mock.patch.object(Episodio, "assinatura_entrega", side_effect=AssertionError("releu o vídeo")):
            self.assertTrue(conformidade.aprovado(ep))
        ep.video.write_bytes(b"outro video")
        self.assertFalse(conformidade.aprovado(ep))


class TestEstoque(unittest.TestCase):
    def test_so_envia_o_que_esta_dentro_da_antecedencia(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            raiz = Path(d)
            eps = []
            for i in range(5):
                ep = episodio_pronto(raiz, f"ep{i}")
                aprovar(ep, raiz)
                eps.append(ep)
            c = canal()
            c.publicacao.antecedencia_dias = 7  # ter/qui/sáb: na primeira semana cabem 3
            yt = FakeYouTube()
            with mock.patch.object(publicacao, "ARQUIVO_ESTADO_CANAL", raiz / "estado.json"):
                feitos = publicacao.publicar(eps, c, yt, agora=datetime(2026, 10, 12, 9, tzinfo=SP), log=SEM_LOG,
                                             pasta_musica=raiz / "musica")
            self.assertEqual(len(feitos), 3)
            self.assertEqual(sum(ep.publicacao.exists() for ep in eps), 3)


class TestTravaDaPlaca(unittest.TestCase):
    def test_vale_entre_processos(self):
        with tempfile.TemporaryDirectory() as d:
            trava = Path(d) / "gpu.lock"
            script = ("import sys, time; sys.path.insert(0, r'%s'); from pathlib import Path; from estudio import recursos\n"
                      "with recursos.gpu(Path(r'%s')):\n    print('entrou', flush=True); time.sleep(3)\n"
                      % (Path.cwd(), trava))
            outro = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
            self.assertEqual(outro.stdout.readline().strip(), "entrou")
            inicio = time.monotonic()
            with recursos.gpu(trava):
                esperou = time.monotonic() - inicio
            outro.wait()
            outro.stdout.close()
            self.assertGreater(esperou, 1.0)  # esperou o outro processo soltar a placa

    def test_um_produtor_por_vez(self):
        with tempfile.TemporaryDirectory() as d:
            primeiro = recursos.instancia_unica("produtor", Path(d))
            self.assertIsNotNone(primeiro)
            script = ("import sys; sys.path.insert(0, r'%s'); from pathlib import Path; from estudio import recursos\n"
                      "print(recursos.instancia_unica('produtor', Path(r'%s')) is None)" % (Path.cwd(), d))
            saida = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True).stdout.strip()
            self.assertEqual(saida, "True")
            primeiro.close()
            segundo = recursos.instancia_unica("produtor", Path(d))
            self.assertIsNotNone(segundo)
            segundo.close()


SEGUNDA = datetime(2026, 10, 12, 12, 0, tzinfo=SP)  # grade ter/qui/sáb: 13, 15, 17, 20, 22 e 24/10


def agendar(ep: Episodio, quando: datetime) -> None:
    ep.gravar(ep.publicacao, {"publicar_em": quando.isoformat()})


class TestSemanaAlvo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.raiz = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_fim_da_semana(self):
        fim = automacao.fim_da_semana_alvo
        self.assertEqual(fim(SEGUNDA, "America/Sao_Paulo", 1), datetime(2026, 10, 25, 23, 59, tzinfo=SP))
        self.assertEqual(fim(SEGUNDA, "America/Sao_Paulo", 0), datetime(2026, 10, 18, 23, 59, tzinfo=SP))
        domingo_a_noite = datetime(2026, 10, 18, 22, 0, tzinfo=SP)
        self.assertEqual(fim(domingo_a_noite, "America/Sao_Paulo", 0), datetime(2026, 10, 18, 23, 59, tzinfo=SP))

    def test_conta_agendados_estoque_e_em_producao(self):
        a, b, pronto, fazendo = (episodio_pronto(self.raiz, n) for n in ("a", "b", "c", "d"))
        agendar(a, datetime(2026, 10, 13, 10, 0, tzinfo=SP))
        agendar(b, datetime(2026, 10, 15, 10, 0, tzinfo=SP))
        aprovar(pronto, self.raiz)
        c = canal()
        todos = [a, b, pronto, fazendo]
        self.assertEqual(automacao.faltam_para_a_semana(todos, c, SEGUNDA)[0], 2)  # 6 horários - 2 - 1 - 1
        c.automacao.semanas_adiantadas = 0
        self.assertEqual(automacao.faltam_para_a_semana(todos, c, SEGUNDA)[0], 0)  # só 13, 15 e 17
        c.publicacao.max_por_semana = 2
        c.automacao.semanas_adiantadas = 1
        self.assertEqual(automacao.faltam_para_a_semana(todos, c, SEGUNDA)[0], 0)  # 13, 15, 20 e 22


class TestLinhasDeTrabalho(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.raiz = Path(self._tmp.name)
        self.ep = episodio_pronto(self.raiz, "x")
        self.episodios = [self.ep]
        self.shorts = []
        for alvo, lista in (("listar", self.episodios), ("listar_shorts", self.shorts)):  # nunca os dados reais
            remendo = mock.patch(f"estudio.episodio.{alvo}", side_effect=lambda lista=lista: list(lista))
            remendo.start()
            self.addCleanup(remendo.stop)
        for alvo, nome, valor in ((produtor, "ARQUIVO_ESTADO", self.raiz / "produtor.json"),
                                  (automacao, "ARQUIVO_PAUSA", self.raiz / "PAUSAR")):  # independe da pausa real
            remendo = mock.patch.object(alvo, nome, valor)
            remendo.start()
            self.addCleanup(remendo.stop)
        self.p = produtor.Produtor(cfg=None, arquivo_canal=None, arquivo_nichos=None, saida=SEM_LOG)
        self.p.agora = lambda: SEGUNDA

    def tearDown(self):
        self._tmp.cleanup()

    def test_placa_marca_e_montagem_aprova_e_limpa(self):
        with mock.patch.object(fluxo, "etapas_na_placa") as placa:
            self.assertTrue(self.p.passo_placa(canal(), SEM_LOG))
        placa.assert_called_once()
        self.assertTrue(automacao.placa_pronta(self.ep))
        self.assertFalse(self.p.passo_placa(canal(), SEM_LOG))  # nada mais na fila da placa

        def finalizar(ep, r, c, log):
            aprovar(ep, self.raiz)

        with mock.patch.object(fluxo, "finalizar", side_effect=finalizar), \
                mock.patch.object(automacao, "limpar_intermediarios", return_value=0) as limpar:
            self.assertTrue(self.p.passo_montagem(canal(), SEM_LOG))
        limpar.assert_called_once()
        self.assertFalse(self.p.passo_montagem(canal(), SEM_LOG))

    def test_falha_repetida_tira_da_fila(self):
        with mock.patch.object(fluxo, "etapas_na_placa", side_effect=RuntimeError("placa sem memória")):
            for _ in range(produtor.TENTATIVAS):
                self.p.passo_placa(canal(), SEM_LOG)
        self.assertTrue(automacao.com_erro(self.ep))
        self.assertEqual(automacao.para_a_placa([self.ep]), [])

    def test_roteiro_espera_espaco_e_fila_da_placa(self):
        c = canal()
        c.automacao.roteiros_adiantados = 1
        with mock.patch.object(fluxo, "novo_episodio") as novo:
            self.assertFalse(self.p.passo_roteiro(c, SEM_LOG))  # já há 1 esperando a placa
            c.automacao.roteiros_adiantados = 2
            with mock.patch.object(automacao, "espaco_livre_gb", return_value=5):
                self.assertFalse(self.p.passo_roteiro(c, SEM_LOG))  # disco quase cheio
            self.assertTrue(self.p.passo_roteiro(c, SEM_LOG))
        novo.assert_called_once()

    def test_para_quando_a_semana_esta_coberta_e_avisa_uma_vez(self):
        c = canal()
        c.automacao.semanas_adiantadas = 0  # só 13, 15 e 17/10
        self.episodios += [episodio_pronto(self.raiz, n) for n in ("y", "z")]
        aprovar(self.episodios[1], self.raiz)
        aprovar(self.episodios[2], self.raiz)
        with mock.patch.object(fluxo, "novo_episodio") as novo, mock.patch.object(produtor.aviso, "notificar") as aviso:
            self.assertFalse(self.p.passo_roteiro(c, SEM_LOG))  # 2 prontos + 1 em produção = 3
            aviso.assert_not_called()  # ainda terminando o "x"
            aprovar(self.ep, self.raiz)
            self.assertFalse(self.p.passo_roteiro(c, SEM_LOG))
            self.assertFalse(self.p.passo_roteiro(c, SEM_LOG))
            aviso.assert_called_once()
            self.assertIn("18/10", aviso.call_args.args[1])

            for ep, dia in zip(self.episodios, (13, 15, 17)):  # a tarefa diária agendou o estoque
                agendar(ep, datetime(2026, 10, dia, 10, 0, tzinfo=SP))
            self.p.agora = lambda: datetime(2026, 10, 19, 0, 5, tzinfo=SP)  # a semana virou
            self.assertTrue(self.p.passo_roteiro(c, SEM_LOG))
        novo.assert_called_once()

    def test_erro_que_se_repete_vai_uma_vez_para_o_registro_e_avisa(self):
        linhas, chamadas = [], []
        self.p._saida = linhas.append

        def passo(c, log):
            chamadas.append(1)
            if len(chamadas) == 3:
                self.p.parar.set()
            raise ValueError("canal.toml inválido")

        with mock.patch.object(produtor, "carregar_canal", return_value=canal()), \
                mock.patch.object(produtor, "OCIOSO_S", 0), mock.patch.object(produtor.aviso, "notificar") as aviso:
            self.p._laco("roteiro", passo)
        self.assertEqual(len(chamadas), 3)
        self.assertEqual(sum("erro inesperado" in linha for linha in linhas), 1)
        aviso.assert_called_once()

    def test_versao_do_codigo(self):
        arquivo = self.raiz / "modulo.py"
        arquivo.write_text("x = 1")
        antes = produtor.versao_do_codigo(self.raiz)
        os.utime(arquivo, (antes + 5, antes + 5))
        self.assertNotEqual(produtor.versao_do_codigo(self.raiz), antes)

    def test_placa_ociosa_tira_o_modelo_da_memoria(self):
        automacao.marcar_placa_pronta(self.ep)
        with mock.patch.object(produtor.imagens, "liberar_compartilhados", return_value=True) as liberar:
            self.assertFalse(self.p.passo_placa(canal(), SEM_LOG))
            liberar.assert_not_called()  # acabou de trabalhar
            self.p._placa_livre_desde -= produtor.LIBERAR_MODELO_APOS_S + 1
            self.assertFalse(self.p.passo_placa(canal(), SEM_LOG))
            liberar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
