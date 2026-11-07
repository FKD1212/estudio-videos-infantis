import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from estudio import conformidade, imagens, metricas, montagem, musica
from estudio.episodio import Episodio
from tests.test_producao import canal, roteiro_valido, wav
from tests.test_publicacao import Pedido, episodio_pronto

SEM_LOG = lambda *_: None  # noqa: E731


def tom(arquivo: Path, segundos: float, silencio_no_meio: float = 0.0) -> Path:
    """Faixa de teste: um tom de 440 Hz, opcionalmente com um buraco de silêncio no meio."""
    if silencio_no_meio:
        metade = (segundos - silencio_no_meio) / 2
        filtro = (f"sine=f=440:d={metade}[a];anullsrc=r=48000:cl=mono:d={silencio_no_meio}[b];"
                  f"sine=f=440:d={metade}[c];[a][b][c]concat=n=3:v=0:a=1")
    else:
        filtro = f"sine=f=440:d={segundos}"
    subprocess.run([montagem.ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", filtro, "-ar", "48000",
                    str(arquivo)], check=True)
    return arquivo


class FakeGerador:
    """Faz faixas de teste; `duracoes` força a duração de algumas versões (para simular defeitos)."""

    def __init__(self, duracoes=None):
        self.duracoes, self.chamadas = duracoes or {}, []

    def __call__(self, faixas, pasta, log=print):
        self.chamadas.append([f["nome"] for f in faixas])
        pasta.mkdir(parents=True, exist_ok=True)
        return {f["nome"]: tom(pasta / f"{f['nome']}.wav", self.duracoes.get(f["nome"], f["duracao"])) for f in faixas}


class FakeOuvinte:
    """Gemini falso: devolve as notas na ordem pedida e guarda o que recebeu."""

    def __init__(self, *respostas):
        self.respostas, self.entradas = list(respostas), []
        self.interactions = self

    def create(self, **pedido):
        self.entradas.append(pedido["input"])
        resposta = {"nota_para_criancas": 8, "nota_qualidade": 8, "combina_com_o_clima": True,
                    "tem_voz_cantada": False, "lembra_musica_conhecida": "", "comentario": "ok"}
        resposta.update(self.respostas.pop(0) if self.respostas else {})
        return SimpleNamespace(output_text=json.dumps(resposta))


class TestAvaliacao(unittest.TestCase):
    def test_medicoes_objetivas(self):
        with tempfile.TemporaryDirectory() as d:
            boa = musica.medir(tom(Path(d) / "boa.wav", 30))
            self.assertAlmostEqual(boa.duracao_s, 30, delta=0.1)
            self.assertEqual(musica.problemas_objetivos(boa, 30), [])
            self.assertIn("duração", musica.problemas_objetivos(boa, 60)[0])
            buraco = musica.medir(tom(Path(d) / "buraco.wav", 30, silencio_no_meio=3))
            self.assertTrue(any("silêncio" in p for p in musica.problemas_objetivos(buraco, 30)))

    def test_escuta_reprova_voz_e_musica_conhecida(self):
        with tempfile.TemporaryDirectory() as d:
            faixa = tom(Path(d) / "f.wav", 30)
            estilo = musica.ESTILOS_PADRAO[0]
            ouvinte = FakeOuvinte({}, {"lembra_musica_conhecida": "Brilha, brilha estrelinha"},
                                  {"tem_voz_cantada": True, "nota_qualidade": 4})
            boa = musica.avaliar(faixa, 30, estilo, "2 a 5 anos", ouvinte, SEM_LOG)
            self.assertEqual((boa["reprovado"], boa["nota"]), ([], 17))
            self.assertIn("lembra uma música conhecida", musica.avaliar(faixa, 30, estilo, "", ouvinte, SEM_LOG)["reprovado"][0])
            self.assertEqual(len(musica.avaliar(faixa, 30, estilo, "", ouvinte, SEM_LOG)["reprovado"]), 2)
            audio = ouvinte.entradas[0][1]
            self.assertEqual((audio["type"], audio["mime_type"]), ("audio", "audio/mp3"))

    def test_nao_gasta_gemini_com_faixa_defeituosa(self):
        with tempfile.TemporaryDirectory() as d:
            ouvinte = FakeOuvinte()
            resultado = musica.avaliar(tom(Path(d) / "f.wav", 10), 30, musica.ESTILOS_PADRAO[0], "", ouvinte, SEM_LOG)
            self.assertTrue(resultado["reprovado"])
            self.assertEqual(ouvinte.entradas, [])


class TestTrilha(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = Episodio(Path(self._tmp.name) / "ep")
        self.ep.salvar_roteiro(roteiro_valido())

    def tearDown(self):
        self._tmp.cleanup()

    def gerar(self, gerador, ouvinte, roteiro=None):
        return musica.gerar_trilha(self.ep, roteiro or roteiro_valido(), canal(), 20, [], gerador=gerador,
                                   cliente=ouvinte, log=SEM_LOG)

    def test_escolhe_a_melhor_versao_aprovada(self):
        gerador = FakeGerador({"versao_1_1": 12})  # a 1ª sai curta demais e nem vai para o Gemini
        ouvinte = FakeOuvinte({"nota_qualidade": 7}, {"nota_qualidade": 9})
        trilha = self.gerar(gerador, ouvinte)
        info = self.ep.ler(self.ep.trilha_info)
        self.assertTrue(trilha.exists())
        self.assertEqual(info["escolhida"], "versao_1_3")
        self.assertTrue(info["aprovada"])
        self.assertEqual(info["duracao_s"], 30)
        self.assertEqual(info["estilo"]["clima"], "alegre")
        self.assertEqual(len(ouvinte.entradas), 2)

        self.gerar(gerador, ouvinte)  # nada mudou: não recompõe
        self.assertEqual(len(gerador.chamadas), 1)
        self.gerar(gerador, ouvinte, roteiro_valido(semente_musica=1))  # pediu outra trilha
        self.assertEqual(len(gerador.chamadas), 2)
        self.assertEqual(self.ep.ler(self.ep.trilha_info)["estilo"], info["estilo"])  # mantém o estilo

    def test_segunda_rodada_e_desistencia(self):
        ruim = {"nota_para_criancas": 3}
        gerador = FakeGerador()
        trilha = self.gerar(gerador, FakeOuvinte(ruim, ruim, ruim, {}))
        self.assertTrue(trilha.exists())
        self.assertEqual(len(gerador.chamadas), 2)
        self.assertEqual(self.ep.ler(self.ep.trilha_info)["escolhida"], "versao_2_1")
        with self.assertRaises(SystemExit):
            self.gerar(FakeGerador(), FakeOuvinte(*[ruim] * 6), roteiro_valido(semente_musica=9))

    def test_montagem_usa_a_trilha_e_conformidade_aceita(self):
        r = roteiro_valido()
        r["cenas"][1]["duracao_s"] = 0.5
        self.ep.narracao.mkdir(parents=True)
        (self.ep.narracao / "cena_01.wav").write_bytes(wav(0.4))
        self.ep.imagens.mkdir()
        for n in (1, 2):
            Image.new("RGB", imagens.TAMANHO_CENA, "pink").save(self.ep.imagens / montagem.arquivo_imagem(n))
        with self.assertRaises(SystemExit):
            montagem.montar(self.ep, r, canal(), log=SEM_LOG)  # fonte "ia" sem trilha
        self.gerar(FakeGerador(), FakeOuvinte())
        montagem.montar(self.ep, r, canal(), log=SEM_LOG)
        producao = self.ep.ler(self.ep.producao)
        self.assertEqual((producao["musica"], producao["musica_origem"]), ("musica/trilha.wav", "ia"))
        self.assertIn("composta por IA", (self.ep.pasta / "publicar.txt").read_text(encoding="utf-8"))
        problemas = conformidade.checar(self.ep, r, canal(), [], Path(self._tmp.name) / "musica")
        self.assertNotIn("música", {p.regra for p in problemas})
        self.assertEqual(conformidade.credito_musica(self.ep), "")


class TestEstilos(unittest.TestCase):
    def test_carrega_pesquisados_com_limites(self):
        perfil = {"tem_musica": True, "clima": "calma", "descricao_para_gerador": "Instrumental soft piano",
                  "bpm": 300, "tom": "Dó maior"}
        salvos = {f"v{i}": {"perfil": dict(perfil), "video": {"outlier": float(i)}} for i in range(5)}
        salvos["sem"] = {"perfil": dict(perfil, tem_musica=False), "video": {"outlier": 99.0}}
        with tempfile.TemporaryDirectory() as d:
            arquivo = Path(d) / "estilos.json"
            arquivo.write_text(json.dumps(salvos), encoding="utf-8")
            pesquisados = [e for e in musica.carregar_estilos(arquivo) if e.origem != "padrão"]
        self.assertEqual([e.id for e in pesquisados], ["calma-v4", "calma-v3", "calma-v2"])
        self.assertEqual((pesquisados[0].bpm, pesquisados[0].tom), (160, "C major"))

    def test_estilos_padrao_tem_tom_valido(self):
        for e in musica.ESTILOS_PADRAO:
            self.assertRegex(e.tom, musica._TOM)
            self.assertIn(e.clima, musica.CLIMAS)

    def test_escolha_aprende_com_o_desempenho(self):
        opcoes = ["a", "b", "c"]
        desempenho = {"a": [1.3] * 8, "b": [0.8] * 8}  # "c" nunca testado
        escolhas = [metricas.escolher(opcoes, desempenho, f"ep{i}") for i in range(300)]
        self.assertGreater(escolhas.count("a"), 200)
        self.assertGreater(escolhas.count("c"), 0)  # continua testando quem tem poucos dados
        self.assertEqual(metricas.escolher(opcoes, desempenho, "ep1"), metricas.escolher(opcoes, desempenho, "ep1"))
        chances = metricas.chance_de_ser_a_melhor(opcoes, desempenho)
        self.assertAlmostEqual(sum(chances.values()), 1)
        self.assertGreater(chances["a"], chances["c"])
        self.assertGreater(chances["c"], chances["b"])


class FakeAnalytics:
    def __init__(self, resumo, curvas):
        self.resumo, self.curvas, self.chamadas = resumo, curvas, []

    def reports(self):
        return self

    def query(self, **kw):
        self.chamadas.append(kw)
        if kw["dimensions"] == "video":
            cab = ["video", "views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage"]
            linhas = [[vid, *valores] for vid, valores in self.resumo.items()]
        else:
            cab = ["elapsedVideoTimeRatio", "audienceWatchRatio"]
            linhas = self.curvas[kw["filters"].removeprefix("video==")]
        return Pedido({"columnHeaders": [{"name": c} for c in cab], "rows": linhas}, None)


class TestMetricas(unittest.TestCase):
    def test_coleta_e_desempenho_por_estilo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            raiz = Path(d)
            eps = []
            for i, (estilo, pct) in enumerate([("alegre-pop", 60), ("alegre-pop", 50), ("alegre-ukulele", 30)]):
                ep = episodio_pronto(raiz, f"ep{i}")
                ep.gravar(ep.publicacao, {"video_id": f"v{i}", "publicar_em": "2026-10-01T10:00:00-03:00"})
                ep.gravar(ep.producao, {"duracao_s": 120})
                ep.gravar(ep.trilha_info, {"estilo": {"id": estilo}})
                eps.append(ep)
            novo = episodio_pronto(raiz, "novo")
            novo.gravar(novo.publicacao, {"video_id": "v9", "publicar_em": "2026-10-20T10:00:00-03:00"})
            curva = [[0.0, 1.0], [0.5, 0.6], [1.0, 0.2]]
            analytics = FakeAnalytics({"v0": [100, 90, 60, 60], "v1": [80, 60, 50, 50], "v2": [10, 5, 30, 30]},
                                      {f"v{i}": curva for i in range(3)})
            self.assertEqual(metricas.coletar(analytics, eps + [novo], hoje=date(2026, 10, 21), log=SEM_LOG), 3)
            self.assertIn("video==v0,v1,v2", analytics.chamadas[0]["filters"])
            m = eps[0].ler(eps[0].metricas)
            self.assertEqual((m["views"], m["dias_no_ar"], m["media_assistida_pct"]), (100, 20, 60.0))
            self.assertAlmostEqual(m["retencao_30s"], 0.8)  # 30 s de 120 s = 25% da curva
            self.assertFalse(novo.metricas.exists())

            # o ep2 tem só 10 views: fica de fora; mediana de 60 e 50 = 55
            self.assertEqual(metricas.desempenho_por_estilo(eps), {"alegre-pop": [60 / 55, 50 / 55]})


if __name__ == "__main__":
    unittest.main()
