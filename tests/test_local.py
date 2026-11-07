"""Várias imagens por fala, sons de animais e as reservas locais (voz, ouvido)."""
import json
import re
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from estudio import conformidade, gemini, imagens, montagem, musica, narracao, roteiro, sons
from estudio.episodio import Episodio
from tests.test_musica import tom
from tests.test_producao import canal, roteiro_valido, wav

SEM_LOG = lambda *_: None  # noqa: E731


def cena(narracao_="", imagens_=("Lulu waving",), som="", **extra):
    return {"narracao": narracao_, "estilo_fala": "", "texto_na_tela": "", "som": som, "semente": 0,
            "imagens": [{"descricao": d, "personagens": ["lulu"]} for d in imagens_], **extra}


class TestRoteiroComTomadas(unittest.TestCase):
    def test_normaliza_formato_novo_e_antigo(self):
        bruto = {"cenas": [
            {"narracao": "Moo!", "imagens": [{"descricao": "a cow", "personagens": []},
                                            {"descricao": "Lulu laughing", "personagens": ["lulu"]}], "som": " Cow "},
            {"narracao": "Oi", "imagem": "old style", "personagens": ["lulu"]}]}
        r = roteiro.normalizar(bruto, "animais", "en", None)
        self.assertEqual([len(c["imagens"]) for c in r["cenas"]], [2, 1])
        self.assertEqual(r["cenas"][0]["som"], "cow")
        self.assertEqual(r["cenas"][1]["imagens"], [{"descricao": "old style", "personagens": ["lulu"]}])

    def test_validacao_de_tomadas_e_som(self):
        r = roteiro_valido()
        r["cenas"][0] = cena("Hi!", imagens_=["a", "b", "c", "d", "e"], som="Cow!")
        problemas = " | ".join(roteiro.validar(r, canal()))
        self.assertIn("mais de 4 imagens", problemas)
        self.assertIn('"som" deve ser', problemas)
        r["cenas"][0] = cena("Hi!", imagens_=["a", ""], som="cow")
        self.assertIn("toda imagem precisa de descrição", " ".join(roteiro.validar(r, canal())))

    def test_imagens_por_tomada(self):
        with tempfile.TemporaryDirectory() as d:
            ep = Episodio(Path(d) / "ep")
            r = roteiro_valido(cenas=[cena("Hi!", imagens_=["one", "two"]), cena("Bye!")])
            chamadas = []

            def gerador(prompt, saida, tamanho, refs, semente):
                chamadas.append((saida.name, semente))
                saida.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", tamanho, "white").save(saida)

            imagens.gerar(ep, r, canal(), gerador=gerador, log=SEM_LOG, pasta_personagens=Path(d) / "p")
            self.assertEqual([c for c in chamadas if c[0].startswith("cena")],
                             [("cena_01_1.png", 1), ("cena_01_2.png", 2), ("cena_02_1.png", 1)])
            self.assertTrue((ep.pasta / "revisao_imagens.jpg").exists())

    def test_personagem_citado_pelo_nome_entra_como_referencia(self):
        with tempfile.TemporaryDirectory() as d:
            ep = Episodio(Path(d) / "ep")
            r = roteiro_valido(cenas=[{"narracao": "Hi", "texto_na_tela": "", "som": "", "semente": 0, "imagens": [
                {"descricao": "Bento walking behind Lulu", "personagens": ["bento"]}]}])
            refs = {}

            def gerador(prompt, saida, tamanho, referencias, semente):
                refs[saida.name] = [a.name for a in referencias]
                saida.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", tamanho, "white").save(saida)

            imagens.gerar(ep, r, canal(), gerador=gerador, log=SEM_LOG, pasta_personagens=Path(d) / "p")
            self.assertEqual(refs["cena_01_1.png"], ["bento.png", "lulu.png"])


class TestSonsNaMontagem(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        raiz = Path(self._tmp.name)
        self.pasta_sons = raiz / "sons"
        self.pasta_sons.mkdir()
        tom(self.pasta_sons / "cow.wav", 1.0)
        (self.pasta_sons / "catalogo.json").write_text(json.dumps({"cow": {"animal": "cow", "licenca": "CC0"}}))
        patcher = mock.patch.object(sons, "PASTA_SONS", self.pasta_sons)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ep = Episodio(raiz / "ep")
        self.ep.narracao.mkdir(parents=True)
        (self.ep.narracao / "cena_01.wav").write_bytes(wav(0.4))
        self.ep.imagens.mkdir()
        self.r = roteiro_valido(cenas=[cena("What does the cow say?", imagens_=["cow", "Lulu"], som="cow"),
                                       cena(duracao_s=0.5)])
        self.ep.salvar_roteiro(self.r)
        for nome, cor in (("cena_01_1.png", "brown"), ("cena_01_2.png", "white"), ("cena_02_1.png", "green")):
            Image.new("RGB", imagens.TAMANHO_CENA, cor).save(self.ep.imagens / nome)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tempo_da_cena_inclui_o_som(self):
        t1, t2 = montagem.calcular_tempos(self.ep, self.r)
        self.assertAlmostEqual(t1.som_s, 2 * (1.0 + sons.INTERVALO_S), places=2)
        self.assertEqual(t1.quadros, round((0.4 + 0.6 + t1.som_s + 0.5) * 30))
        self.assertEqual(montagem.dividir_quadros(t1.quadros, 2), [t1.quadros // 2, t1.quadros - t1.quadros // 2])
        trechos = montagem.segmentos_de_audio(self.ep, self.r, [t1, t2])
        self.assertEqual([a.name if a else None for a, _ in trechos], ["cena_01.wav", "cow.wav", "cow.wav", None, None])
        self.assertAlmostEqual(sum(d for _, d in trechos), t1.tela_s + t2.tela_s, places=3)

    def test_monta_com_varias_imagens_e_som(self):
        c = canal()
        c.musica.fonte = "biblioteca"
        with mock.patch.object(montagem, "PASTA_MUSICA", Path(self._tmp.name) / "sem_musica"):
            video = montagem.montar(self.ep, self.r, c, log=SEM_LOG)
        clipes = (self.ep.montagem / "clipes.txt").read_text()
        self.assertEqual(clipes.count("file"), 3)
        info = subprocess.run([montagem.ffmpeg(), "-hide_banner", "-i", str(video)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stderr
        segundos = float(re.search(r"Duration: \d+:\d+:([\d.]+)", info).group(1))
        tempos = montagem.calcular_tempos(self.ep, self.r)
        self.assertAlmostEqual(segundos, sum(t.tela_s for t in tempos), delta=0.1)
        producao = self.ep.ler(self.ep.producao)
        self.assertEqual((producao["sons"], producao["sons_faltando"]), (["cow"], []))

        problemas = conformidade.checar(self.ep, self.r, c, [], Path(self._tmp.name))
        self.assertNotIn("sons", {p.regra for p in problemas})
        (self.pasta_sons / "catalogo.json").write_text(json.dumps({"cow": {"licenca": "CC BY-SA 4.0"}}))
        problemas = conformidade.checar(self.ep, self.r, c, [], Path(self._tmp.name))
        self.assertIn(("bloqueia", "sons"), {(p.gravidade, p.regra) for p in problemas})

    def test_som_indisponivel_nao_trava_a_producao(self):
        self.r["cenas"][0]["som"] = "lion"
        t1, _ = montagem.calcular_tempos(self.ep, self.r)
        self.assertEqual(t1.som_s, 0)
        self.assertEqual(sons.garantir(["lion"], log=SEM_LOG, pasta=self.pasta_sons), ["lion"])  # sem chave


class FakeOuvido:
    def __init__(self, chances):
        self.chances = chances  # chance do primeiro rótulo, uma por chamada

    def probabilidades(self, audio, rotulos):
        primeiro = self.chances.pop(0)
        resto = (1 - primeiro) / (len(rotulos) - 1)
        return {r: (primeiro if i == 0 else resto) for i, r in enumerate(rotulos)}


class TestBuscaDeSons(unittest.TestCase):
    def test_recorta_o_trecho_mais_forte(self):
        with tempfile.TemporaryDirectory() as d:
            origem = Path(d) / "longo.wav"
            filtro = "anullsrc=r=48000:cl=mono:d=3[a];sine=f=300:d=1[b];[a][b]concat=n=2:v=0:a=1"
            subprocess.run([montagem.ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", filtro, str(origem)],
                           check=True)
            sons.recortar(origem, Path(d) / "corte.wav", duracao=1.0)
            with wave.open(str(Path(d) / "corte.wav")) as w:
                self.assertAlmostEqual(w.getnframes() / w.getframerate(), 1.0, places=2)
            from estudio.ouvido import carregar_audio

            audio = carregar_audio(Path(d) / "corte.wav")
            self.assertGreater(float(abs(audio).max()), 0.6)  # pegou o tom, não o silêncio

    def test_escolhe_o_candidato_que_o_ouvido_confirma(self):
        with tempfile.TemporaryDirectory() as d:
            pasta = Path(d)
            bruto = tom(pasta / "fonte.wav", 3).read_bytes()

            class Sessao:
                def get(self, url, **kw):
                    if "apiv2" in url:
                        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"results": [
                            {"id": 1, "name": "tractor", "username": "a", "url": "u1", "previews": {"preview-hq-mp3": "p1"}},
                            {"id": 2, "name": "cow moo", "username": "b", "url": "u2", "previews": {"preview-hq-mp3": "p2"}}]})
                    return SimpleNamespace(content=bruto)

            with mock.patch.dict("os.environ", {"FREESOUND_API_KEY": "chave"}):
                ok = sons.obter("cow", log=SEM_LOG, sessao=Sessao(), ouvido=FakeOuvido([0.2, 0.93]), pasta=pasta)
            self.assertTrue(ok)
            entrada = sons.catalogo(pasta)["cow"]
            self.assertEqual((entrada["titulo"], entrada["licenca"], entrada["confianca"]), ("cow moo", "CC0", 0.93))
            self.assertTrue((pasta / "cow.wav").exists())
            self.assertTrue(sons.obter("cow", log=SEM_LOG, pasta=pasta))  # já está no catálogo: nada a buscar


class CotaDoDia(Exception):
    status_code = 429


class TestReservasLocais(unittest.TestCase):
    def setUp(self):
        gemini._esgotados.clear()
        self.addCleanup(gemini._esgotados.clear)

    def test_narracao_passa_para_a_voz_local_e_fica_nela(self):
        class TTSsemCota:
            def __init__(self):
                self.interactions, self.chamadas = self, 0

            def create(self, **pedido):
                self.chamadas += 1
                raise CotaDoDia("limit: 10 requests per day. Please retry in 5h")

        falas_locais = []

        def kokoro(falas, voz, log):
            falas_locais.extend(f["texto"] for f in falas)
            for f in falas:
                Path(f["saida"]).write_bytes(wav(0.3))

        c = canal()
        c.narracao["pt"].voz_local = "pf_dora"
        with tempfile.TemporaryDirectory() as d:
            ep, r, tts = Episodio(Path(d)), roteiro_valido(), TTSsemCota()
            self.assertEqual(narracao.narrar(ep, r, c, cliente=tts, log=SEM_LOG, kokoro=kokoro), 1)
            self.assertEqual(ep.ler(ep.narracao / "motor.json")["motor"], "local")
            self.assertTrue((ep.narracao / "cena_01.wav").exists())
            chamadas = tts.chamadas
            self.assertEqual(narracao.narrar(ep, r, c, cliente=tts, log=SEM_LOG, kokoro=kokoro), 0)
            self.assertEqual(tts.chamadas, chamadas)  # não volta a tentar o Gemini neste episódio

    def test_sem_voz_local_configurada_a_cota_esgotada_aparece(self):
        class TTSsemCota:
            def __init__(self):
                self.interactions = self

            def create(self, **pedido):
                raise CotaDoDia("limit per day")

        with tempfile.TemporaryDirectory() as d, self.assertRaises(gemini.CotaGeminiEsgotada):
            narracao.narrar(Episodio(Path(d)), roteiro_valido(), canal(), cliente=TTSsemCota(), log=SEM_LOG)

    def test_trilha_avaliada_pelo_ouvido_local_quando_a_cota_acaba(self):
        class SemCota:
            def __init__(self):
                self.interactions = self

            def create(self, **pedido):
                raise CotaDoDia("limit per day")

        # clima: "alegre" é o primeiro rótulo (70%); natureza: instrumental é o primeiro (90%)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("estudio.ouvido.Ouvido.obter", return_value=FakeOuvido([0.7, 0.9])):
            resultado = musica.avaliar(tom(Path(d) / "f.wav", 30), 30, musica.ESTILOS_PADRAO[0], "", SemCota(), SEM_LOG)
        self.assertEqual(resultado["reprovado"], [])
        self.assertIn("ouvido local", resultado["escuta"]["comentario"])
        self.assertTrue(resultado["escuta"]["combina_com_o_clima"])


if __name__ == "__main__":
    unittest.main()
