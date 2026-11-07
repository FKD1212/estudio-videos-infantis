import os
import time
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from estudio.config import ConfigPesquisa, Nicho
from estudio.estrutura import candidatos
from estudio import gemini
from estudio.gemini import extrair_json
from estudio.pesquisa import coletar, duracao_em_segundos, resumir_nichos
from estudio.relatorio import gerar_markdown

AGORA = datetime(2026, 10, 10, tzinfo=timezone.utc)


def item(vid, canal, views, dias, duracao="PT5M", kids=True):
    return {
        "id": vid,
        "snippet": {
            "title": f"Vídeo {vid} | teste", "channelId": canal, "channelTitle": f"Canal {canal}",
            "publishedAt": (AGORA - timedelta(days=dias)).isoformat().replace("+00:00", "Z"),
            "thumbnails": {"high": {"url": f"https://i.ytimg.com/{vid}.jpg"}},
        },
        "statistics": {"viewCount": str(views)},
        "contentDetails": {"duration": duracao},
        "status": {"madeForKids": kids},
    }


class FakeYouTube:
    """Canal A: grande, normal ~1.000 views, com um hit de 50.000.
    Canal B: pequeno, normal ~200 views, com um hit de 10.000 e um Short."""

    def __init__(self):
        self.itens = {}
        self.uploads = {"A": [], "B": []}
        for i in range(10):
            self._add(item(f"a{i}", "A", 1000 + i, 30 + i), "A")
            self._add(item(f"b{i}", "B", 200 + i, 30 + i), "B")
        self._add(item("a_hit", "A", 50_000, 100), "A")
        self._add(item("b_hit", "B", 10_000, 50), "B")
        self._add(item("b_short", "B", 9_000, 40, duracao="PT45S"), "B")
        self._add(item("adulto", "A", 99_999, 40, kids=False), "A")
        self.canais_api = [
            {"id": "A", "statistics": {"subscriberCount": "2000000"},
             "contentDetails": {"relatedPlaylists": {"uploads": "UU_A"}}},
            {"id": "B", "statistics": {"subscriberCount": "5000"},
             "contentDetails": {"relatedPlaylists": {"uploads": "UU_B"}}},
        ]

    def _add(self, it, canal):
        self.itens[it["id"]] = it
        self.uploads[canal].append(it["id"])

    def buscar(self, termo, idioma, regiao, publicado_apos, duracao, maximo):
        return ["a_hit", "b_hit", "b_short", "adulto", "a1", "b1", "b2"]

    def videos(self, ids):
        return [self.itens[i] for i in sorted(set(ids))]

    def canais(self, ids):
        ids = set(ids)
        return [c for c in self.canais_api if c["id"] in ids]

    def uploads_recentes(self, playlist_id, maximo=50):
        return self.uploads[playlist_id.removeprefix("UU_")][:maximo]


def config():
    return ConfigPesquisa(nichos=[Nicho("historias", {"pt": ["historinha"]})])


class TestPesquisa(unittest.TestCase):
    def setUp(self):
        self.videos = coletar(FakeYouTube(), config(), agora=AGORA, log=lambda *_: None)
        self.por_id = {v.id: v for v in self.videos}

    def test_duracao(self):
        self.assertEqual(duracao_em_segundos("PT1H2M3S"), 3723)
        self.assertEqual(duracao_em_segundos("PT45S"), 45)
        self.assertEqual(duracao_em_segundos("P1DT1M"), 86460)
        self.assertEqual(duracao_em_segundos("P0D"), 0)
        self.assertEqual(duracao_em_segundos(None), 0)

    def test_descarta_nao_infantil(self):
        self.assertNotIn("adulto", self.por_id)

    def test_outlier_relativo_ao_proprio_canal(self):
        # Mediana de A inclui o próprio hit e o vídeo adulto (12 vídeos longos): (1005+1006)/2.
        self.assertAlmostEqual(self.por_id["a_hit"].outlier, 50_000 / 1005.5, places=3)
        self.assertAlmostEqual(self.por_id["b_hit"].outlier, 10_000 / 205, places=3)
        self.assertEqual(self.por_id["b_hit"].inscritos_canal, 5000)

    def test_short_sem_base_de_shorts_nao_tem_outlier(self):
        # O canal B só tem 1 Short: não dá para comparar com vídeos longos.
        self.assertIsNone(self.por_id["b_short"].outlier)

    def test_resumo_do_nicho(self):
        (r,) = resumir_nichos(self.videos, config())
        self.assertEqual(r.videos, 6)
        self.assertEqual(r.outliers, 2)
        self.assertAlmostEqual(r.pct_canais_pequenos, 4 / 6)
        self.assertAlmostEqual(r.potencial_canal_novo, (201 / 31 + 10_000 / 50) / 2)  # b2, b1, b_hit, b_short
        self.assertAlmostEqual(r.pct_shorts, 1 / 6)

    def test_candidatos_por_grupo_e_duracao(self):
        escolhidos = candidatos(self.videos, top=1, max_min=15)
        self.assertEqual([v.id for v in escolhidos], ["a_hit"])
        self.assertEqual(candidatos(self.videos, top=5, max_min=1), [])

    def test_markdown_escapa_titulos(self):
        md = gerar_markdown(self.videos, resumir_nichos(self.videos, config()), {})
        self.assertIn("Vídeo b_hit \\| teste", md)
        self.assertIn("| 1 | historias | pt |", md)

    def test_pedido_ao_gemini_tem_prazo(self):
        class Pendurado:
            def __init__(self, pendurados):
                self.interactions, self.pendurados, self.pedidos = self, pendurados, []

            def create(self, **pedido):
                self.pedidos.append(pedido["model"])
                if pedido["model"] in self.pendurados:
                    time.sleep(3)
                return "ok"

        gemini._esgotados.clear()
        self.addCleanup(gemini._esgotados.clear)
        inicio = time.monotonic()
        video = [{"type": "text", "text": "veja"}, {"type": "video", "uri": "x"}]
        with self.assertRaises(gemini.GeminiNaoRespondeu):  # vídeo pendurado: quem chama pula o vídeo
            gemini.criar(Pendurado({gemini.MODELO_PADRAO}), prazo_s=0.2, model=gemini.MODELO_PADRAO, input=video)
        self.assertLess(time.monotonic() - inicio, 2)

        cliente = Pendurado({"gemini-3.8-flash"})  # texto: o modelo que não responde dá lugar ao próximo
        self.assertEqual(gemini.criar(cliente, log=lambda *_: None, prazo_s=0.2, model=gemini.MODELO_PADRAO,
                                      input="oi"), "ok")
        self.assertEqual(cliente.pedidos, ["gemini-3.8-flash", "gemini-3.6-flash"])
        with self.assertRaises(gemini.CotaGeminiEsgotada):  # TTS sem resposta: quem chama usa a voz local
            gemini.criar(Pendurado({"tts"}), log=lambda *_: None, prazo_s=0.2, model="tts", input="oi")

    def test_limite_por_minuto_espera_e_os_outros_trocam_de_modelo(self):
        class Limite(Exception):
            status_code = 429

        class Cliente:
            def __init__(self, mensagens):
                self.interactions, self.mensagens, self.pedidos = self, list(mensagens), []

            def create(self, **pedido):
                self.pedidos.append(pedido["model"])
                if self.mensagens:
                    raise Limite(self.mensagens.pop(0))
                return "ok"

        gemini._esgotados.clear()
        self.addCleanup(gemini._esgotados.clear)
        cliente = Cliente(["Quota exceeded: requests per minute"])
        self.assertEqual(gemini.criar(cliente, log=lambda *_: None, espera_s=0, model=gemini.MODELO_PADRAO,
                                      input="oi"), "ok")
        self.assertEqual(cliente.pedidos, ["gemini-3.8-flash", "gemini-3.8-flash"])  # esperou e repetiu
        cliente = Cliente(["Your project has exceeded a quota."])  # sem dizer qual: não espera
        self.assertEqual(gemini.criar(cliente, log=lambda *_: None, espera_s=0, model=gemini.MODELO_PADRAO,
                                      input="oi"), "ok")
        self.assertEqual(cliente.pedidos, ["gemini-3.8-flash", "gemini-3.6-flash"])

    def _cliente_com_cota(self, esgotados):
        class CotaDoDia(Exception):
            status_code = 429

        class Cliente:
            def __init__(self):
                self.interactions, self.pedidos = self, []

            def create(self, **pedido):
                self.pedidos.append(pedido["model"])
                if pedido["model"] in esgotados:
                    raise CotaDoDia(f"limit: 20 requests per day for {pedido['model']}. Please retry in 3h2m")
                return "ok"

        gemini._esgotados.clear()
        self.addCleanup(gemini._esgotados.clear)
        return Cliente()

    def test_cota_diaria_passa_para_o_proximo_modelo(self):
        cliente = self._cliente_com_cota({"gemini-3.8-flash"})
        self.assertEqual(gemini.criar(cliente, log=lambda *_: None, model=gemini.MODELO_PADRAO, input="oi"), "ok")
        self.assertEqual(cliente.pedidos, ["gemini-3.8-flash", "gemini-3.6-flash"])
        gemini.criar(cliente, log=lambda *_: None, model=gemini.MODELO_PADRAO, input="oi")  # não insiste
        self.assertEqual(cliente.pedidos.count("gemini-3.8-flash"), 1)

        with mock.patch.dict(os.environ, {"SEM_MODELO_LOCAL": "1"}):
            with self.assertRaises(gemini.CotaGeminiEsgotada) as erro:
                gemini.criar(self._cliente_com_cota(set(gemini.MODELOS_TEXTO)), log=lambda *_: None,
                             model=gemini.MODELO_PADRAO, input="oi")
        self.assertIn("volta em 3h2m", str(erro.exception))

    def test_sem_cota_texto_vai_para_o_modelo_local(self):
        chamado = []
        local = lambda corpo: chamado.append(corpo) or gemini.RespostaLocal('{"a": 1}', "local:x")  # noqa: E731
        with mock.patch.object(gemini, "criar_local", local), mock.patch.dict(os.environ, {"SEM_MODELO_LOCAL": "0"}):
            resposta = gemini.criar(
                self._cliente_com_cota(set(gemini.MODELOS_TEXTO)), log=lambda *_: None, model=gemini.MODELO_PADRAO,
                input=[{"type": "text", "text": "revise"}, {"type": "image", "mime_type": "image/jpeg", "data": "QUJD"}],
                response_format={"type": "text", "mime_type": "application/json", "schema": {"type": "object"}})
            self.assertEqual(resposta.output_text, '{"a": 1}')
            mensagem = chamado[0]["messages"][0]
            self.assertEqual((mensagem["content"], mensagem["images"]), ("revise", ["QUJD"]))
            self.assertEqual((chamado[0]["format"], chamado[0]["keep_alive"]), ({"type": "object"}, 0))

            # vídeo, áudio e voz não têm equivalente local; modelo fora da cadeia (TTS) também não
            for pedido in ({"model": gemini.MODELO_PADRAO, "input": [{"type": "video", "uri": "x"}]},
                           {"model": gemini.MODELO_PADRAO, "input": "oi", "response_format": {"type": "audio"}},
                           {"model": "gemini-3.8-flash-tts", "input": "oi"}):
                with self.assertRaises(gemini.CotaGeminiEsgotada):
                    gemini.criar(self._cliente_com_cota(set(gemini.MODELOS_TEXTO) | {"gemini-3.8-flash-tts"}),
                                 log=lambda *_: None, **pedido)
            self.assertEqual(len(chamado), 1)

    def test_extrai_json_de_resposta_com_texto_extra(self):
        self.assertEqual(extrair_json('```json\n{"a": {"b": 1}}\n```'), {"a": {"b": 1}})
        with self.assertRaises(ValueError):
            extrair_json("sem json")


if __name__ == "__main__":
    unittest.main()
