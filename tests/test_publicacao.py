import copy
import json
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httplib2
from googleapiclient.errors import HttpError

from estudio import conformidade, publicacao, roteiro
from estudio.config import ConfigPublicacao
from estudio.episodio import Episodio
from estudio.youtube import CotaEsgotada
from tests.test_producao import canal, roteiro_valido

SP = ZoneInfo("America/Sao_Paulo")
SEM_LOG = lambda *_: None  # noqa: E731


def erro_http(status: int, motivo: str) -> HttpError:
    return HttpError(httplib2.Response({"status": str(status)}),
                     json.dumps({"error": {"errors": [{"reason": motivo}]}}).encode())


class FakeGemini:
    """Responde à revisão com os problemas pedidos e guarda a entrada recebida."""

    def __init__(self, problemas=()):
        self.problemas, self.entradas = list(problemas), []
        self.interactions = self

    def create(self, **pedido):
        self.entradas.append(pedido["input"])
        return SimpleNamespace(output_text=json.dumps({
            "problemas": self.problemas, "principios_atendidos": ["Aprender e despertar curiosidade"],
            "resumo": "Episódio gentil e educativo."}))


class Pedido:
    def __init__(self, resultado, erro):
        self.resultado, self.erro = resultado, erro

    def execute(self):
        if self.erro:
            raise self.erro
        return self.resultado

    def next_chunk(self, num_retries=0):
        return None, self.execute()


class FakeYouTube:
    def __init__(self):
        self.chamadas, self.erros = [], {}

    def __getattr__(self, recurso):  # youtube.videos(), .thumbnails(), .captions(), .channels()
        fake = self

        class Recurso:
            def __getattr__(self, metodo):
                return lambda **kw: fake._pedido(f"{recurso}.{metodo}", kw)

        return lambda: Recurso()

    def _pedido(self, nome, kw):
        self.chamadas.append((nome, kw))
        n = sum(1 for c, _ in self.chamadas if c == "videos.insert")
        resultado = {
            "videos.insert": {"id": f"video{n}"},
            "videos.list": {"items": [{"status": {"uploadStatus": "uploaded", "privacyStatus": "private",
                                                  "publishAt": "2026-10-15T13:00:00Z", "madeForKids": True}}]},
            "channels.list": {"items": [{"id": "UC1", "brandingSettings": {"channel": {"title": "Canal",
                                                                                      "country": "BR"}},
                                         "status": {}}]},
            "playlists.list": {"items": []},
            "playlists.insert": {"id": "PL1"},
        }.get(nome, {})
        return Pedido(resultado, self.erros.get(nome))

    def argumentos(self, nome: str) -> dict:
        return next(kw for c, kw in self.chamadas if c == nome)

    def nomes(self, com_canal: bool = False):
        """Chamadas feitas; as de configuração do canal só se pedidas (têm teste próprio)."""
        return [c for c, _ in self.chamadas if com_canal or not c.startswith("channels.")]


def episodio_pronto(raiz: Path, nome: str = "2026-10-11-historias-pt-lulu", **roteiro_extra) -> Episodio:
    """Episódio produzido, com música licenciada e roteiro editado depois de gerado."""
    ep = Episodio(raiz / "episodios" / nome)
    r = roteiro_valido(**roteiro_extra)
    ep.salvar_roteiro(r)
    gerado = copy.deepcopy(r)
    gerado["titulo"] = "versão do Gemini"
    ep.gravar(ep.roteiro_gerado, gerado)
    ep.video.write_bytes(b"video")
    ep.miniatura.write_bytes(b"jpg")
    ep.legendas.write_text("1\n00:00:00,000 --> 00:00:01,000\nOlá!\n", encoding="utf-8")
    ep.gravar(ep.producao, {"musica": "alegre/sol.mp3", "duracao_s": 10})
    musica = raiz / "musica"
    musica.mkdir(exist_ok=True)
    (musica / "licencas.toml").write_text(
        '["alegre/sol.mp3"]\nfonte = "YouTube Audio Library"\ncredito = "Música: Sol, de Fulano"\n', encoding="utf-8")
    return ep


def aprovar(ep: Episodio, raiz: Path, gemini=None) -> None:
    conformidade.revisar(ep, ep.carregar_roteiro(), canal(), [], cliente=gemini or FakeGemini(),
                         log=SEM_LOG, pasta_musica=raiz / "musica")
    conformidade.aprovar(ep, ciente=True)


def regras(problemas):
    return {(p.gravidade, p.regra) for p in problemas}


class TestConformidade(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.raiz = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def checar(self, ep, outros=()):
        return conformidade.checar(ep, ep.carregar_roteiro(), canal(), list(outros), self.raiz / "musica")

    def test_episodio_em_ordem(self):
        self.assertEqual(self.checar(episodio_pronto(self.raiz)), [])

    def test_bloqueios(self):
        ep = episodio_pronto(self.raiz)
        r = ep.carregar_roteiro()
        r["youtube"]["tags"].append("Patrulha Canina")
        ep.salvar_roteiro(r)
        ep.gravar(ep.producao, {"musica": "alegre/pirata.mp3"})
        ep.legendas.unlink()
        achados = regras(self.checar(ep, [("outro", r)]))
        for regra in ("marcas de terceiros", "música", "entrega", "repetição"):
            self.assertIn((conformidade.BLOQUEIA, regra), achados)

    def test_avisos_e_palavra_inteira(self):
        ep = episodio_pronto(self.raiz)
        r = ep.carregar_roteiro()
        r["cenas"][0]["narracao"] = "A Lulu está chorando."
        r["cenas"][1]["imagem"] = "a diesel truck"  # "dies" não pode casar com "diesel"
        r["youtube"]["titulo"] = "LULU E AS CORES!!"
        ep.salvar_roteiro(r)
        ep.gravar(ep.roteiro_gerado, r)
        problemas = self.checar(ep)
        self.assertEqual({p.gravidade for p in problemas}, {conformidade.AVISO})
        self.assertIn(("aviso", "toque humano"), regras(problemas))
        detalhes = " ".join(p.detalhe for p in problemas)
        self.assertIn('"chorando"', detalhes)
        self.assertNotIn('"dies"', detalhes)
        self.assertEqual(sum(p.regra == "metadados" for p in problemas), 2)

    def test_promessa_de_saude_nos_metadados_bloqueia(self):
        ep = episodio_pronto(self.raiz)
        r = ep.carregar_roteiro()
        r["youtube"]["tags"].append("speech delay toddler songs")
        ep.salvar_roteiro(r)
        self.assertIn((conformidade.BLOQUEIA, "promessa de saúde"), regras(self.checar(ep)))

    def test_credito_vai_para_a_descricao(self):
        ep = episodio_pronto(self.raiz)
        texto = conformidade.descricao_final(ep, ep.carregar_roteiro(), self.raiz / "musica")
        self.assertTrue(texto.endswith("\n\nMúsica: Sol, de Fulano"))

    def test_revisao_da_ia_recebe_imagens(self):
        ep = episodio_pronto(self.raiz)
        (ep.pasta / "revisao_imagens.jpg").write_bytes(b"jpg")
        gemini = FakeGemini()
        conformidade.revisar(ep, ep.carregar_roteiro(), canal(), [("antigo", {"titulo": "Episódio antigo"})],
                             cliente=gemini, log=SEM_LOG, pasta_musica=self.raiz / "musica")
        entrada = gemini.entradas[0]
        self.assertEqual(sum(1 for item in entrada if item["type"] == "image"), 2)
        self.assertIn("Episódio antigo", entrada[0]["text"])
        self.assertIn("Falsamente educativo", entrada[0]["text"])

    def test_aprovacao(self):
        ep = episodio_pronto(self.raiz)
        with self.assertRaises(SystemExit):
            conformidade.aprovar(ep)  # sem revisão
        gemini = FakeGemini([{"gravidade": "bloqueia", "regra": "falsamente educativo", "onde": "cena 1",
                              "detalhe": "a cor dita não é a da imagem", "sugestao": "trocar"},
                             {"gravidade": "aviso", "regra": "ritmo", "onde": "cena 2", "detalhe": "longa",
                              "sugestao": ""}])
        conformidade.revisar(ep, ep.carregar_roteiro(), canal(), [], cliente=gemini, log=SEM_LOG,
                             pasta_musica=self.raiz / "musica")
        with self.assertRaises(SystemExit) as erro:
            conformidade.aprovar(ep, ciente=True)
        self.assertIn("--discordo-da-ia", str(erro.exception))
        with self.assertRaises(SystemExit) as erro:
            conformidade.aprovar(ep, discordo_da_ia="a imagem é azul mesmo")
        self.assertIn("--ciente", str(erro.exception))
        aprovacao = conformidade.aprovar(ep, ciente=True, discordo_da_ia="a imagem é azul mesmo")
        self.assertEqual(aprovacao["discordo_da_ia"], "a imagem é azul mesmo")
        self.assertTrue(conformidade.aprovado(ep))

        ep.video.write_bytes(b"video editado")
        self.assertFalse(conformidade.aprovado(ep))
        with self.assertRaises(SystemExit) as erro:
            conformidade.aprovar(ep, ciente=True)
        self.assertIn("mudou depois da revisão", str(erro.exception))

    def test_aprovacao_automatica(self):
        ep = episodio_pronto(self.raiz)
        self.assertFalse(conformidade.aprovar_automaticamente(ep)[0])  # sem revisão, não aprova
        conformidade.revisar(ep, ep.carregar_roteiro(), canal(), [], cliente=FakeGemini([
            {"gravidade": "aviso", "regra": "ritmo", "onde": "cena 2", "detalhe": "longa", "sugestao": ""}]),
            log=SEM_LOG, pasta_musica=self.raiz / "musica")
        ok, detalhe = conformidade.aprovar_automaticamente(ep)
        self.assertTrue(ok)
        self.assertEqual(ep.ler(ep.aprovacao)["modo"], "automatico")
        self.assertTrue(conformidade.aprovado(ep))

        bloqueado = episodio_pronto(self.raiz, "outro")
        conformidade.revisar(bloqueado, bloqueado.carregar_roteiro(), canal(), [], cliente=FakeGemini([
            {"gravidade": "bloqueia", "regra": "falsamente educativo", "onde": "cena 1", "detalhe": "errado",
             "sugestao": ""}]), log=SEM_LOG, pasta_musica=self.raiz / "musica")
        ok, detalhe = conformidade.aprovar_automaticamente(bloqueado)
        self.assertFalse(ok)
        self.assertIn("falsamente educativo", detalhe)
        self.assertFalse(bloqueado.aprovacao.exists())

    def test_bloqueio_das_regras_nao_tem_atalho(self):
        ep = episodio_pronto(self.raiz)
        ep.legendas.unlink()
        conformidade.revisar(ep, ep.carregar_roteiro(), canal(), [], cliente=FakeGemini(), log=SEM_LOG,
                             pasta_musica=self.raiz / "musica")
        with self.assertRaises(SystemExit):
            conformidade.aprovar(ep, ciente=True, discordo_da_ia="quero publicar")


class TestHashtagsECanal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.raiz = Path(self._tmp.name)
        estado = mock.patch.object(publicacao, "ARQUIVO_ESTADO_CANAL", self.raiz / "canal_youtube.json")
        estado.start()
        self.addCleanup(estado.stop)
        self.canal = canal()
        self.canal.youtube.hashtags = ["#LuluAndBenny"]
        self.canal.youtube.playlists = {"historias": "Stories with Lulu"}
        self.canal.youtube.descricao = "Welcome!\n\nMore text."
        self.canal.youtube.palavras_chave = ["kids", "animal sounds"]

    def tearDown(self):
        self._tmp.cleanup()

    def test_hashtags_normalizadas_ordenadas_e_no_limite(self):
        self.assertEqual(roteiro.normalizar_hashtags(["#Animal Sounds", "kids", "#kids", "##", "#ÁguaLimpa"]),
                         ["#AnimalSounds", "#kids", "#ÁguaLimpa"])
        r = roteiro_valido()
        r["youtube"]["hashtags"] = ["#AnimalSounds", "#FarmAnimals", "#Toddlers"]
        self.assertEqual(roteiro.hashtags_do_video(r, ["#LuluAndBenny"]),
                         ["#AnimalSounds", "#FarmAnimals", "#LuluAndBenny", "#Toddlers"])
        r["youtube"]["hashtags"] = [f"#tag{i}" for i in range(20)]
        self.assertEqual(len(roteiro.hashtags_do_video(r, ["#LuluAndBenny"])), 15)
        self.assertIn("mais de 15", " ".join(roteiro.validar(r, canal())))
        r["youtube"]["hashtags"] = ["#com espaço"]
        self.assertIn("sem espaço", " ".join(roteiro.validar(r, canal())))

    def test_hashtags_na_descricao_e_na_checagem_de_marcas(self):
        ep = episodio_pronto(self.raiz, youtube={"titulo": "T", "descricao": "Para os pais.", "tags": ["a"],
                                                  "hashtags": ["#AnimalSounds", "#PawPatrol"]})
        texto = conformidade.descricao_final(ep, ep.carregar_roteiro(), self.raiz / "musica", ["#LuluAndBenny"])
        self.assertTrue(texto.endswith("#AnimalSounds #PawPatrol #LuluAndBenny"))
        problemas = conformidade.checar(ep, ep.carregar_roteiro(), self.canal, [], self.raiz / "musica")
        self.assertIn(("bloqueia", "marcas de terceiros"), regras(problemas))

    def test_configura_o_canal_uma_vez(self):
        yt = FakeYouTube()
        self.assertTrue(publicacao.configurar_canal(yt, self.canal, log=SEM_LOG))
        corpo = yt.argumentos("channels.update")["body"]["brandingSettings"]["channel"]
        self.assertEqual((corpo["description"], corpo["keywords"], corpo["country"], corpo["defaultLanguage"]),
                         ("Welcome!\n\nMore text.", 'kids "animal sounds"', "BR", "en"))
        self.assertIn(("channels.update", {"part": "status", "body": {"id": "UC1", "status": {
            "selfDeclaredMadeForKids": True}}}), yt.chamadas)
        self.assertFalse(publicacao.configurar_canal(yt, self.canal, log=SEM_LOG))  # nada mudou: nem chama a API
        self.assertEqual(len(yt.chamadas), 3)

    def test_playlist_e_metadados_de_video_ja_enviado(self):
        ep = episodio_pronto(self.raiz)
        aprovar(ep, self.raiz)
        yt = FakeYouTube()
        publicacao.publicar([ep], self.canal, yt, agora=TestPublicar.AGORA, log=SEM_LOG, pasta_musica=self.raiz / "musica")
        self.assertIn("playlists.insert", yt.nomes())
        self.assertEqual(yt.argumentos("playlistItems.insert")["body"]["snippet"]["playlistId"], "PL1")
        registro = ep.ler(ep.publicacao)
        self.assertEqual(registro["playlist"], "PL1")
        self.assertTrue(yt.argumentos("videos.insert")["body"]["snippet"]["description"].endswith("#LuluAndBenny"))

        yt.chamadas.clear()
        r = ep.carregar_roteiro()
        r["youtube"]["hashtags"] = ["#Stories"]
        ep.salvar_roteiro(r)
        publicacao.publicar([ep], self.canal, yt, agora=TestPublicar.AGORA, log=SEM_LOG, pasta_musica=self.raiz / "musica")
        self.assertEqual(yt.nomes(), ["videos.update"])  # só os metadados: nada de reenviar, nem playlist de novo
        self.assertTrue(yt.argumentos("videos.update")["body"]["snippet"]["description"].endswith(
            "#Stories #LuluAndBenny"))


class TestDecisoesDaAutomacao(unittest.TestCase):
    def test_fila_em_andamento_e_bloqueados(self):
        from estudio import automacao

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            raiz = Path(d)
            agora = datetime(2026, 10, 13, 9, tzinfo=SP)
            agendado = episodio_pronto(raiz, "a-agendado")
            agendado.gravar(agendado.publicacao, {"publicar_em": "2026-10-15T10:00:00-03:00"})
            no_ar = episodio_pronto(raiz, "b-no-ar")
            no_ar.gravar(no_ar.publicacao, {"publicar_em": "2026-10-10T10:00:00-03:00"})
            aprovado_ = episodio_pronto(raiz, "c-aprovado")
            aprovar(aprovado_, raiz)
            comecado = episodio_pronto(raiz, "d-comecado")
            barrado = episodio_pronto(raiz, "e-barrado")
            conformidade.revisar(barrado, barrado.carregar_roteiro(), canal(), [], cliente=FakeGemini([
                {"gravidade": "bloqueia", "regra": "x", "onde": "y", "detalhe": "z", "sugestao": ""}]),
                log=SEM_LOG, pasta_musica=raiz / "musica")
            todos = [agendado, no_ar, aprovado_, comecado, barrado]

            self.assertEqual(automacao.fila(todos, agora), 2)  # agendado no futuro + aprovado
            self.assertEqual([e.nome for e in automacao.em_andamento(todos)], ["d-comecado"])
            self.assertTrue(automacao.bloqueado(barrado))
            self.assertEqual(automacao.precisa_de_novos(todos, agora, 3), 0)  # termina o começado antes
            self.assertEqual(automacao.precisa_de_novos([agendado, aprovado_, barrado], agora, 3), 1)
            self.assertEqual(automacao.precisa_de_novos([agendado, aprovado_], agora, 2), 0)


class TestAvisoDeConexao(unittest.TestCase):
    def test_lembra_de_reconectar_perto_dos_7_dias(self):
        with tempfile.TemporaryDirectory() as d:
            arquivo = Path(d) / "conexao.json"
            self.assertIn("ainda não conectado", publicacao.aviso_de_conexao(arquivo=arquivo))
            arquivo.write_text(json.dumps({"conectado_em": "2026-10-12T10:00:00+00:00"}))
            dia2 = datetime(2026, 10, 14, 11, tzinfo=timezone.utc)
            self.assertEqual(publicacao.aviso_de_conexao(dia2, arquivo), "Canal conectado há 2 dia(s).")
            self.assertIn("rode python -m estudio conectar",
                          publicacao.aviso_de_conexao(datetime(2026, 10, 18, 11, tzinfo=timezone.utc), arquivo))


class TestAgenda(unittest.TestCase):
    def test_respeita_margem_grade_e_maximo_por_semana(self):
        cfg = ConfigPublicacao(horarios=["ter 10:00", "qui 10:00", "sab 09:00"], max_por_semana=2)
        agora = datetime(2026, 10, 13, 9, 0, tzinfo=SP)          # terça, 9h: 10h está dentro da margem
        ocupados = [datetime(2026, 10, 12, 10, 0, tzinfo=SP)]    # segunda da mesma semana
        horarios = publicacao.proximos_horarios(cfg, ocupados, agora, 3)
        self.assertEqual([h.strftime("%a %d %H:%M") for h in horarios],
                         ["Thu 15 10:00", "Tue 20 10:00", "Thu 22 10:00"])

    def test_horario_invalido(self):
        with self.assertRaises(SystemExit):
            publicacao.proximos_horarios(ConfigPublicacao(horarios=["terça 10h"]), [], datetime.now(timezone.utc), 1)


class TestPublicar(unittest.TestCase):
    AGORA = datetime(2026, 10, 13, 9, 0, tzinfo=SP)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # o upload mantém o arquivo aberto
        self.raiz = Path(self._tmp.name)
        self.ep = episodio_pronto(self.raiz)
        self.yt = FakeYouTube()
        estado = mock.patch.object(publicacao, "ARQUIVO_ESTADO_CANAL", self.raiz / "canal_youtube.json")
        estado.start()
        self.addCleanup(estado.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def publicar(self, **kw):
        return publicacao.publicar([self.ep], canal(), self.yt, agora=self.AGORA, log=SEM_LOG,
                                   pasta_musica=self.raiz / "musica", **kw)

    def test_envia_aprovado_marcado_para_criancas_e_agendado(self):
        self.assertEqual(self.publicar(), [])  # ainda não aprovado
        aprovar(self.ep, self.raiz)
        (registro,) = self.publicar()
        self.assertEqual(self.yt.nomes(), ["videos.insert", "thumbnails.set", "captions.insert", "videos.list"])
        corpo = self.yt.argumentos("videos.insert")["body"]
        self.assertEqual(corpo["status"]["privacyStatus"], "private")
        self.assertTrue(corpo["status"]["selfDeclaredMadeForKids"])
        self.assertFalse(corpo["status"]["containsSyntheticMedia"])
        self.assertEqual(corpo["status"]["publishAt"], "2026-10-15T13:00:00Z")  # qui 10h em Brasília
        self.assertEqual(corpo["snippet"]["defaultLanguage"], "pt-BR")
        self.assertTrue(corpo["snippet"]["description"].endswith("Música: Sol, de Fulano"))
        self.assertEqual(self.yt.argumentos("captions.insert")["body"]["snippet"]["language"], "pt-BR")
        self.assertEqual(registro["url"], "https://youtu.be/video1")
        self.assertTrue(registro["miniatura"] and registro["legendas"])
        self.assertEqual(self.ep.ler(self.ep.publicacao)["status"]["madeForKids"], True)

        self.yt.chamadas.clear()
        self.assertEqual(self.publicar(), [])  # nada de novo: não sobe duplicado
        self.assertEqual(self.yt.nomes(), [])

    def test_miniatura_recusada_e_completada_depois(self):
        aprovar(self.ep, self.raiz)
        self.yt.erros["thumbnails.set"] = erro_http(403, "forbidden")
        (registro,) = self.publicar()
        self.assertFalse(registro["miniatura"])
        self.assertTrue(registro["legendas"])

        self.yt.erros.clear()
        self.yt.chamadas.clear()
        self.publicar()
        self.assertEqual(self.yt.nomes(), ["thumbnails.set", "videos.list"])
        self.assertTrue(self.ep.ler(self.ep.publicacao)["miniatura"])

    def test_cota_esgotada_para_sem_registrar(self):
        aprovar(self.ep, self.raiz)
        self.yt.erros["videos.insert"] = erro_http(403, "uploadLimitExceeded")
        with self.assertRaises(CotaEsgotada):
            self.publicar()
        self.assertFalse(self.ep.publicacao.exists())

    def test_alterado_depois_da_aprovacao_nao_sobe(self):
        aprovar(self.ep, self.raiz)
        self.ep.miniatura.write_bytes(b"outra miniatura")
        self.assertEqual(self.publicar(), [])
        self.assertEqual(self.yt.nomes(), [])

    def test_simular_nao_envia(self):
        aprovar(self.ep, self.raiz)
        self.assertEqual(self.publicar(simular=True), [])
        self.assertEqual(self.yt.nomes(), [])
        self.assertFalse(self.ep.publicacao.exists())

    def test_agenda(self):
        aprovar(self.ep, self.raiz)
        self.publicar()
        linhas = publicacao.agenda([self.ep], canal(), agora=self.AGORA)
        self.assertIn("15/10 10:00  agendado", linhas[0])
        self.assertIn("https://youtu.be/video1", linhas[0])
        self.assertIn("sáb 17/10 09:00", linhas[-1])


if __name__ == "__main__":
    unittest.main()
