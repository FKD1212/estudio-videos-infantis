import base64
import io
import re
import subprocess
import tempfile
import unittest
import wave
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from estudio import episodio, imagens, montagem, narracao, roteiro
from estudio.config import Canal, Personagem, Voz
from estudio.episodio import Episodio, Manifesto


def canal():
    return Canal(
        nome="Canal Teste", publico="2 a 5 anos", estilo_visual="flat cartoon",
        narracao={"pt": Voz("Sulafat", "pt-BR", "calorosa")},
        personagens=[Personagem("lulu", {"pt": "Lulu"}, "a white bunny", "curiosa"),
                     Personagem("bento", {"pt": "Bento"}, "an orange turtle", "calmo")],
    )


def roteiro_valido(**extra):
    r = {
        "titulo": "Lulu e as cores", "nicho": "historias", "idioma": "pt", "tema": "", "resumo": "",
        "aprendizado": "cores", "clima_musica": "alegre",
        "personagens_do_episodio": [{"id": "baleia", "nome": "Bia", "descricao_visual": "a blue whale", "semente": 0}],
        "cenas": [
            {"narracao": "Olá! <short pause> Que cor é essa?", "estilo_fala": "animada", "imagem": "Lulu waving",
             "personagens": ["lulu"], "texto_na_tela": "AZUL", "semente": 0},
            {"narracao": "", "duracao_s": 2, "estilo_fala": "", "imagem": "Bia and Lulu swimming",
             "personagens": ["baleia", "lulu"], "texto_na_tela": "", "semente": 0},
        ],
        "miniatura": {"imagem": "Lulu smiling", "personagens": ["lulu"], "texto": "Que cor?", "semente": 0},
        "youtube": {"titulo": "Lulu e as cores", "descricao": "Para os pais.", "tags": ["cores", "infantil"]},
    }
    r.update(extra)
    return r


def wav(segundos: float, taxa: int = 24_000) -> bytes:
    saida = io.BytesIO()
    with wave.open(saida, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(b"\x00\x00" * int(segundos * taxa))
    return saida.getvalue()


class TestRoteiro(unittest.TestCase):
    def test_roteiro_valido(self):
        self.assertEqual(roteiro.validar(roteiro_valido(), canal()), [])

    def test_validar_aponta_problemas(self):
        r = roteiro_valido()
        r["cenas"][0]["personagens"] = ["dragao"]
        r["cenas"][1].pop("duracao_s")
        r["cenas"][0]["texto_na_tela"] = "x" * 41
        r["personagens_do_episodio"].append({"id": "lulu", "descricao_visual": "outra"})
        r["youtube"]["titulo"] = "Cores <3"
        problemas = "\n".join(roteiro.validar(r, canal()))
        for trecho in ['"dragao" não existe', "cena 2: sem narração", "mais de 40", "já é um personagem fixo", '"<"']:
            self.assertIn(trecho, problemas)

    def test_idioma_sem_voz(self):
        self.assertIn("falta [narracao.en]", roteiro.validar(roteiro_valido(idioma="en"), canal())[0])

    def test_personagens_fixos_vem_do_canal(self):
        r = roteiro_valido(personagens_do_episodio=[
            {"id": "baleia", "nome": "Bia", "descricao_visual": "a blue whale"},
            {"id": "lulu", "nome": "Outra", "descricao_visual": "ignorada"}])
        elenco = roteiro.personagens(r, canal())
        self.assertEqual(elenco["lulu"]["descricao_visual"], "a white bunny")
        self.assertTrue(elenco["lulu"]["fixo"])
        self.assertFalse(elenco["baleia"]["fixo"])

    def test_normalizar_acrescenta_sementes_e_limpa(self):
        bruto = {"titulo": " T ", "cenas": [{"narracao": " oi ", "imagem": "x", "personagens": ["lulu"]}],
                 "youtube": {"titulo": "T", "descricao": "", "tags": ["a", " "]}}
        r = roteiro.normalizar(bruto, "historias", "pt", None)
        self.assertEqual(r["titulo"], "T")
        self.assertEqual(r["cenas"][0]["narracao"], "oi")
        self.assertEqual(r["cenas"][0]["semente"], 0)
        self.assertEqual(r["youtube"]["tags"], ["a"])
        self.assertEqual(r["clima_musica"], "alegre")

    def test_molde_prefere_idioma_e_omite_passo_a_passo(self):
        def analise(nicho, idioma, outlier):
            return {"video": {"nicho": nicho, "idioma": idioma, "outlier": outlier, "duracao_s": 240},
                    "estrutura": {"resumo_formato": f"formato {idioma} {outlier}", "molde_reutilizavel": ["SEGREDO"]}}
        estruturas = {"a": analise("historias", "en", 50), "b": analise("historias", "pt", 5),
                      "c": analise("musicas", "pt", 99)}
        molde = roteiro.molde_do_nicho(estruturas, "historias", "pt")
        self.assertIn("formato pt 5", molde)
        self.assertNotIn("formato en", molde)
        self.assertNotIn("SEGREDO", molde)
        self.assertIn("formato en 50", roteiro.molde_do_nicho(estruturas, "historias", "es"))

    def test_prompt(self):
        p = roteiro.montar_prompt(canal(), "historias", "pt", "dividir brinquedos", 4, "MOLDE", ["Episódio velho"])
        for trecho in ["dividir brinquedos", "MOLDE", "Episódio velho", 'id "lulu"', "cerca de 480 palavras", "17 cenas", "~8 palavras"]:
            self.assertIn(trecho, p)


class TestEpisodio(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(episodio.slug("Lulu e o Arco-Íris Perdido!"), "lulu-e-o-arco-iris-perdido")
        self.assertEqual(episodio.slug("???"), "episodio")

    def test_novo_e_localizar(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d)
            a = episodio.novo("historias", "pt", "Lulu", date(2026, 10, 11), raiz)
            b = episodio.novo("historias", "pt", "Lulu", date(2026, 10, 11), raiz)
            self.assertEqual(b.nome, "2026-10-11-historias-pt-lulu-2")
            a.salvar_roteiro({"titulo": "a"})
            b.salvar_roteiro({"titulo": "b"})
            self.assertEqual(episodio.localizar("lulu-2", raiz).nome, b.nome)
            with self.assertRaises(SystemExit):
                episodio.localizar("lulu", raiz)  # ambíguo
            self.assertEqual(episodio.localizar(None, raiz).nome, b.nome)

    def test_manifesto(self):
        with tempfile.TemporaryDirectory() as d:
            m = Manifesto(Path(d))
            self.assertFalse(m.atual("x.png", "1"))
            (Path(d) / "x.png").write_bytes(b"")
            m.registrar("x.png", "1")
            self.assertTrue(Manifesto(Path(d)).atual("x.png", "1"))
            self.assertFalse(Manifesto(Path(d)).atual("x.png", "2"))


class FakeTTS:
    def __init__(self, pcm_sem_cabecalho=False):
        self.textos = []
        self.pcm = pcm_sem_cabecalho
        self.interactions = self

    def create(self, **pedido):
        self.textos.append(pedido["input"][0]["content"][0]["text"])
        dados = wav(0.5)[44:] if self.pcm else wav(0.5)
        audio = SimpleNamespace(data=base64.b64encode(dados).decode(), sample_rate=24_000, channels=1)
        return SimpleNamespace(output_audio=audio)


class TestNarracao(unittest.TestCase):
    def test_narra_so_o_que_mudou(self):
        with tempfile.TemporaryDirectory() as d:
            ep, r, tts = Episodio(Path(d)), roteiro_valido(), FakeTTS()
            self.assertEqual(narracao.narrar(ep, r, canal(), cliente=tts, log=lambda *_: None), 1)
            self.assertEqual(narracao.narrar(ep, r, canal(), cliente=tts, log=lambda *_: None), 0)
            r["cenas"][0]["narracao"] = "Outro texto."
            self.assertEqual(narracao.narrar(ep, r, canal(), cliente=tts, log=lambda *_: None), 1)
            self.assertEqual(tts.textos, ["Olá! <short pause> Que cor é essa?", "Outro texto."])
            self.assertEqual(narracao.pendentes(ep, r), [])

    def test_pcm_sem_cabecalho_vira_wav(self):
        with tempfile.TemporaryDirectory() as d:
            ep = Episodio(Path(d))
            narracao.narrar(ep, roteiro_valido(), canal(), cliente=FakeTTS(pcm_sem_cabecalho=True), log=lambda *_: None)
            self.assertAlmostEqual(narracao.duracao_wav(ep.narracao / "cena_01.wav"), 0.5)


class FakeGerador:
    def __init__(self):
        self.chamadas = []

    def __call__(self, prompt, saida, tamanho, referencias, semente):
        self.chamadas.append((saida.name, [r.name for r in referencias], semente))
        saida.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", tamanho, "skyblue").save(saida)


class TestImagens(unittest.TestCase):
    def test_referencias_primeiro_e_refaz_so_o_que_mudou(self):
        with tempfile.TemporaryDirectory() as d:
            ep, r, g = Episodio(Path(d) / "ep"), roteiro_valido(), FakeGerador()
            personagens = Path(d) / "personagens"
            gerar = lambda: imagens.gerar(ep, r, canal(), gerador=g, log=lambda *_: None, pasta_personagens=personagens)
            self.assertEqual(gerar(), 5)
            self.assertEqual(g.chamadas[:2], [("lulu.png", [], 0), ("personagem_baleia.png", [], 0)])
            self.assertIn(("cena_02_1.png", ["personagem_baleia.png", "lulu.png"], 1), g.chamadas)
            self.assertTrue((ep.pasta / "revisao_imagens.jpg").exists())

            g.chamadas.clear()
            self.assertEqual(gerar(), 0)
            r["cenas"][0]["semente"] = 7
            self.assertEqual(gerar(), 1)
            self.assertEqual(g.chamadas, [("cena_01_1.png", ["lulu.png"], 701)])

    def test_prompt_de_cena(self):
        p = imagens.prompt_cena("Lulu waving.", [("Lulu", "a white bunny")], "flat cartoon")
        self.assertIn("reference image 1 is Lulu, a white bunny", p)
        self.assertIn("Style: flat cartoon.", p)


class TestMontagem(unittest.TestCase):
    def test_trechos_de_legenda(self):
        texto = "Olá! <long pause> " + "palavra " * 20 + "fim."
        trechos = montagem._trechos(texto, limite=40)
        self.assertEqual(trechos[0], "Olá!")
        self.assertTrue(all(len(t) <= 40 for t in trechos))
        self.assertNotIn("<", " ".join(trechos))

    def test_legendas_acompanham_as_cenas(self):
        r = roteiro_valido()
        r["cenas"].append({"narracao": "Tchau.", "imagem": "x", "personagens": [], "texto_na_tela": ""})
        tempos = [montagem.Tempo(1.9, 78), montagem.Tempo(0.0, 60), montagem.Tempo(1.0, 108)]
        srt = montagem.legendas(r, tempos)
        # "Olá!" tem 4 das 19 letras da fala de 1,9 s
        self.assertIn("1\n00:00:00,000 --> 00:00:00,400\nOlá!", srt)
        self.assertIn("00:00:00,400 --> 00:00:01,900\nQue cor é essa?", srt)
        self.assertIn("3\n00:00:04,600 --> 00:00:05,600\nTchau.", srt)  # 78+60 quadros = 4,6 s

    def test_tempos_em_quadros_inteiros(self):
        with tempfile.TemporaryDirectory() as d:
            ep = Episodio(Path(d))
            ep.narracao.mkdir()
            (ep.narracao / "cena_01.wav").write_bytes(wav(1.0))
            t1, t2 = montagem.calcular_tempos(ep, roteiro_valido())
            self.assertEqual((t1.fala_s, t1.quadros), (1.0, 48))         # 1 s + 0,6 s de pausa
            self.assertEqual((t2.fala_s, t2.quadros), (0.0, (2 + 2) * 30))  # duracao_s + final

    def test_escolha_de_musica(self):
        with tempfile.TemporaryDirectory() as d:
            pasta = Path(d)
            self.assertIsNone(montagem.escolher_musica(roteiro_valido(), "ep", pasta))
            (pasta / "calma").mkdir()
            (pasta / "calma" / "a.mp3").write_bytes(b"")
            self.assertEqual(montagem.escolher_musica(roteiro_valido(), "ep", pasta).name, "a.mp3")  # sem "alegre"
            (pasta / "alegre").mkdir()
            for nome in ("b.mp3", "c.ogg", "leia.txt"):
                (pasta / "alegre" / nome).write_bytes(b"")
            escolha = montagem.escolher_musica(roteiro_valido(), "ep", pasta)
            self.assertIn(escolha.name, {"b.mp3", "c.ogg"})
            self.assertEqual(montagem.escolher_musica(roteiro_valido(), "ep", pasta), escolha)

    def test_monta_video_de_verdade(self):
        with tempfile.TemporaryDirectory() as d:
            ep = Episodio(Path(d) / "ep")
            r = roteiro_valido()
            r["cenas"][1]["duracao_s"] = 0.5
            ep.narracao.mkdir(parents=True)
            (ep.narracao / "cena_01.wav").write_bytes(wav(0.4))
            ep.imagens.mkdir()
            for n, cor in ((1, "gold"), (2, "teal")):
                Image.new("RGB", imagens.TAMANHO_CENA, cor).save(ep.imagens / montagem.arquivo_imagem(n))
            musicas = Path(d) / "musica"
            musicas.mkdir()
            (musicas / "fundo.wav").write_bytes(wav(0.3, 44_100))
            c = canal()
            c.musica.fonte = "biblioteca"
            antigo, montagem.PASTA_MUSICA = montagem.PASTA_MUSICA, musicas
            try:
                video = montagem.montar(ep, r, c, log=lambda *_: None)
            finally:
                montagem.PASTA_MUSICA = antigo

            info = subprocess.run([montagem.ffmpeg(), "-hide_banner", "-i", str(video)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
            h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info).groups()
            self.assertAlmostEqual(float(s), 0.4 + 0.6 + 0.5 + 2.0, delta=0.1)
            self.assertIn("1920x1080", info)
            self.assertIn("Audio: aac", info)
            for arquivo in ("legendas.srt", "miniatura.jpg", "publicar.txt"):
                self.assertTrue((ep.pasta / arquivo).exists(), arquivo)
            publicar = (ep.pasta / "publicar.txt").read_text(encoding="utf-8")
            self.assertIn("Lulu e as cores", publicar)
            self.assertIn("fundo.wav", publicar)


if __name__ == "__main__":
    unittest.main()
