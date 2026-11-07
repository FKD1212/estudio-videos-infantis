"""Shorts verticais: de onde saem, roteiro, imagens e cartão na vertical, agenda depois do episódio,
descrição com o link, engajamento por formato e a vez deles na produção contínua."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from estudio import automacao, conformidade, episodio, imagens, metricas, montagem, musica, produtor, publicacao, roteiro, shorts
from estudio.config import ConfigShorts, Personagem
from estudio.episodio import Episodio, Manifesto
from tests.test_producao import FakeGerador, canal, roteiro_valido
from tests.test_publicacao import SP

SEM_LOG = lambda *_: None  # noqa: E731


def canal_com_shorts():
    c = canal()
    c.shorts = ConfigShorts(ligado=True)
    return c


def episodio_com_animais(raiz: Path, nome: str = "2026-10-11-animais-pt-fazenda", publicar_em: datetime | None = None) -> Episodio:
    """Episódio pronto (enviado), com dois animais com som, miniatura, trilha avaliada e um personagem próprio."""
    ep = Episodio(raiz / "episodios" / nome)
    r = roteiro_valido()
    r["cenas"][0]["som"] = "duck"
    r["cenas"][1]["som"] = "cow"
    ep.salvar_roteiro(r)
    Image.new("RGB", (1280, 720), "orange").save(ep.miniatura)
    ep.pasta_trilha.mkdir(parents=True)
    ep.trilha.write_bytes(b"trilha")
    ep.gravar(ep.trilha_info, {"estilo": {"id": "alegre-ukulele"}, "aprovada": True})
    ep.imagens.mkdir()
    Image.new("RGB", (64, 64), "blue").save(ep.imagens / "personagem_baleia.png")
    Manifesto(ep.imagens).registrar("personagem_baleia.png", "assinatura-da-baleia")
    quando = publicar_em or datetime(2026, 10, 13, 10, 0, tzinfo=SP)
    ep.gravar(ep.publicacao, {"publicar_em": quando.isoformat(), "url": "https://youtu.be/EPISODIO123"})
    return ep


def short_feito(origem: Episodio, numero: int, animal: str, formato: str, **extra) -> Episodio:
    s = episodio.novo_short(origem, numero, formato, animal)
    s.salvar_roteiro({"tipo": "short", "animal": animal, "formato": formato, "idioma": "pt", "cenas": [], **extra})
    return s


def bruto(formato: str = "pergunta") -> dict:
    cenas = [
        {"narracao": "", "estilo_fala": "", "letra": "", "som": "duck", "texto_na_tela": "QUEM É?",
         "imagens": [{"descricao": "A duck shadow behind a bush", "personagens": ["baleia", "lulu"]}]},
        {"narracao": "Quem faz esse som? <long pause>", "estilo_fala": "curiosa", "letra": "", "som": "cow",
         "texto_na_tela": "", "imagens": [{"descricao": "Lulu listening", "personagens": ["lulu"]}]},
        {"narracao": "É um pato!", "estilo_fala": "animada", "letra": "", "som": "duck", "texto_na_tela": "PATO",
         "imagens": [{"descricao": "A happy duck", "personagens": []}, {"descricao": "Lulu laughing", "personagens": ["lulu"]},
                     {"descricao": "extra", "personagens": []}]},
    ]
    letra = ""
    if formato == "musica":
        cenas = [{"narracao": "Cante com a Lulu!", "estilo_fala": "", "letra": "", "som": "", "texto_na_tela": "",
                  "imagens": [{"descricao": "Lulu singing", "personagens": ["lulu"]}]}] + [
            {"narracao": "isto não deve ser narrado", "estilo_fala": "", "letra": f"Verso {k}, quack quack!", "som": "",
             "texto_na_tela": "", "imagens": [{"descricao": f"Duck dancing {k}", "personagens": []}]} for k in range(1, 5)]
        letra = "[verse]\nVerso 1, quack quack!\n[chorus]\nQuack quack!"
    return {"titulo": "Quem é?", "resumo": "Short do pato", "aprendizado": "o pato faz quack", "cenas": cenas,
            "letra": letra, "chamada": "Veja a história inteira com a Lulu e o Bento!",
            "youtube": {"titulo": "Quem faz quack? 🦆", "descricao": "Um Short para adivinhar.", "tags": ["pato"],
                        "hashtags": ["#Pato", "#SonsDeAnimais"]}}


class FakeGeminiShort:
    def __init__(self, resposta: dict):
        self.resposta, self.entradas = resposta, []
        self.interactions = self

    def create(self, **pedido):
        self.entradas.append(pedido["input"])
        return SimpleNamespace(output_text=json.dumps(self.resposta))


class TestDeOndeSaem(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.raiz = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_um_animal_diferente_por_short_ate_o_limite(self):
        origem, c, lista = episodio_com_animais(self.raiz), canal_com_shorts(), []
        self.assertEqual(shorts.animais(origem.carregar_roteiro()), ["duck", "cow"])
        for numero in range(1, 4):
            animal, formato = shorts.proxima_ideia(origem, c, lista)
            lista.append(short_feito(origem, numero, animal, formato))
        ideias = [(s.carregar_roteiro()["animal"], s.carregar_roteiro()["formato"]) for s in lista]
        self.assertEqual([a for a, _ in ideias[:2]], ["duck", "cow"])  # o menos usado primeiro
        self.assertEqual(len(set(ideias)), 3)  # nunca o mesmo animal no mesmo formato
        self.assertIsNone(shorts.proxima_ideia(origem, c, lista))
        self.assertEqual(shorts.fontes([origem], c, lista), [])
        self.assertEqual(lista[0].nome, f"{origem.nome}/1-{ideias[0][1]}-duck")  # nome único entre episódios
        self.assertEqual(episodio.listar_shorts(self.raiz / "episodios"), lista)

    def test_formato_que_engaja_mais_sai_mais(self):
        origem = episodio_com_animais(self.raiz)
        lista = []
        for k, (formato, pct, curtidas) in enumerate([("pergunta", 140, 12), ("pergunta", 150, 10),
                                                      ("pergunta", 135, 11), ("curiosidade", 60, 1),
                                                      ("curiosidade", 55, 2), ("musica", 70, 2), ("musica", 65, 1)], 1):
            s = short_feito(origem, k, "duck", formato)
            s.gravar(s.metricas, {"dias_no_ar": 10, "views": 200, "media_assistida_pct": pct, "curtidas": curtidas,
                                  "compartilhamentos": 0, "inscritos": 0})
            lista.append(s)
        relativo = metricas.engajamento_relativo(lista)
        self.assertGreater(relativo[lista[0].nome], 1.5)
        desempenho = shorts.desempenho_por_formato(lista)
        escolhas = [metricas.escolher(list(shorts.FORMATOS), desempenho, f"s{k}") for k in range(100)]
        self.assertGreater(escolhas.count("pergunta"), 80)

    def test_so_episodio_pronto_rende_short(self):
        origem = episodio_com_animais(self.raiz)
        origem.publicacao.unlink()  # ainda em produção
        self.assertEqual(shorts.fontes([origem], canal_com_shorts(), []), [])


class TestRoteiroDoShort(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.origem = episodio_com_animais(Path(self._tmp.name))
        self.r_origem = self.origem.carregar_roteiro()

    def tearDown(self):
        self._tmp.cleanup()

    def test_pergunta_ganha_chamada_e_so_o_som_do_animal(self):
        c = canal_com_shorts()
        r = shorts.normalizar(bruto(), self.origem, self.r_origem, "duck", "pergunta", c)
        self.assertEqual((r["tipo"], r["formato_video"], r["episodio"]), ("short", "vertical", self.origem.nome))
        self.assertEqual([x["som"] for x in r["cenas"]], ["duck", "", "duck", ""])  # o "cow" saiu
        # antes da revelação, só pistas: sem a referência do bicho, e a Lulu só onde a descrição a mostra
        self.assertEqual([t.get("pista") for x in r["cenas"][:3] for t in x["imagens"]], [True, True, None, None])
        self.assertEqual(r["cenas"][1]["imagens"][0]["personagens"], ["lulu"])
        self.assertEqual(r["cenas"][0]["imagens"][0]["personagens"], [])
        self.assertEqual(len(r["cenas"][2]["imagens"]), 2)  # no máximo 2 imagens por cena
        chamada = r["cenas"][-1]
        self.assertTrue(chamada["imagens"][0]["fixa"])
        self.assertEqual(chamada["narracao"], "Veja a história inteira com a Lulu e o Bento!")
        self.assertEqual(r["personagens_do_episodio"], self.r_origem["personagens_do_episodio"])
        self.assertEqual(roteiro.validar(r, c), [])  # cena só com som, sem narração, vale
        self.assertEqual(shorts.problemas_do_formato(r), [])

    def test_musica_divide_a_cancao_entre_as_cenas_cantadas(self):
        c = canal_com_shorts()
        r = shorts.normalizar(bruto("musica"), self.origem, self.r_origem, "duck", "musica", c)
        cantadas = [x for x in r["cenas"] if x.get("letra")]
        self.assertEqual(len(cantadas), 4)
        self.assertTrue(all(x["narracao"] == "" for x in cantadas))
        self.assertAlmostEqual(shorts.duracao_cancao(r), c.shorts.segundos - shorts.GANCHO_CANCAO_S - shorts.CHAMADA_S,
                               delta=0.05)
        self.assertEqual(r["musica_desde_cena"], 2)
        self.assertIn("[chorus]", r["letra"])
        self.assertEqual(roteiro.validar(r, c), [])
        self.assertEqual(shorts.problemas_do_formato(r), [])

    def test_personagem_pelo_nome_do_idioma_e_nao_pelo_id(self):
        c = canal_com_shorts()
        c.personagens[1] = Personagem("bento", {"pt": "Bento", "en": "Benny"}, "an orange turtle", "calmo")
        r = {"idioma": "en", "cenas": [{"narracao": "Join Lulu and Bento!", "imagens": [{"descricao": "BENTO waving"}]}],
             "letra": "Bento sings", "youtube": {"titulo": "Bento's song", "descricao": "With Bento."}}
        shorts.nomes_certos(r, c)
        self.assertEqual(r["cenas"][0]["narracao"], "Join Lulu and Benny!")
        self.assertEqual(r["cenas"][0]["imagens"][0]["descricao"], "Benny waving")
        self.assertEqual((r["letra"], r["youtube"]["titulo"]), ("Benny sings", "Benny's song"))

    def test_gancho_esquecido_e_cena_muda_nao_travam(self):
        c = canal_com_shorts()
        resposta = bruto("musica")
        resposta["cenas"][0]["narracao"] = ""
        r = shorts.normalizar(resposta, self.origem, self.r_origem, "duck", "musica", c)
        self.assertEqual(r["cenas"][0]["narracao"], "Vamos cantar com Lulu & Bento!")
        resposta = bruto()
        resposta["cenas"][1]["narracao"] = ""
        r = shorts.normalizar(resposta, self.origem, self.r_origem, "duck", "pergunta", c)
        self.assertEqual(r["cenas"][1]["duracao_s"], shorts.CENA_MUDA_S)
        self.assertEqual(roteiro.validar(r, c), [])

    def test_prompt_por_formato_e_sem_repetir_aberturas(self):
        c = canal_com_shorts()
        p = shorts.montar_prompt(c, self.r_origem, "duck", "pergunta", ['pergunta: "Velho" — Escute só!'])
        self.assertIn("FORMATO PERGUNTA", p)
        self.assertIn("Escute só!", p)
        self.assertIn("VERTICAL", p)
        self.assertIn("FORMATO MÚSICA", shorts.montar_prompt(c, self.r_origem, "duck", "musica", []))
        self.assertIn("Did you know", shorts.montar_prompt(c, self.r_origem, "duck", "curiosidade", []))

    def test_novo_short_grava_roteiro_na_pasta_do_episodio(self):
        gemini = FakeGeminiShort(bruto())
        s = shorts.novo_short(self.origem, "duck", "pergunta", canal_com_shorts(), [], cliente=gemini, log=SEM_LOG)
        self.assertTrue(s.e_short)
        self.assertEqual(s.origem.pasta, self.origem.pasta)
        self.assertEqual(s.carregar_roteiro(), s.ler(s.roteiro_gerado))
        self.assertIn("duck", gemini.entradas[0])

    def test_roteiro_incompleto_nao_vira_short(self):
        resposta = bruto()
        for cena in resposta["cenas"]:
            cena["som"] = ""
        with self.assertRaises(RuntimeError):
            shorts.novo_short(self.origem, "duck", "pergunta", canal_com_shorts(), [],
                              cliente=FakeGeminiShort(resposta), log=SEM_LOG)
        self.assertFalse(self.origem.pasta_shorts.exists())


class TestNaVertical(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.raiz = Path(self._tmp.name)
        self.origem = episodio_com_animais(self.raiz)
        self.c = canal_com_shorts()
        self.r = shorts.normalizar(bruto(), self.origem, self.origem.carregar_roteiro(), "duck", "pergunta", self.c)
        self.short = episodio.novo_short(self.origem, 1, "pergunta", "duck")
        self.short.salvar_roteiro(self.r)

    def tearDown(self):
        self._tmp.cleanup()

    def test_imagens_verticais_sem_gerar_o_cartao_nem_os_personagens_do_episodio(self):
        shorts.copiar_personagens(self.short)
        g = FakeGerador()
        imagens.gerar(self.short, self.r, self.c, gerador=g, log=SEM_LOG, pasta_personagens=self.raiz / "personagens")
        geradas = [nome for nome, _, _ in g.chamadas]
        self.assertNotIn("personagem_baleia.png", geradas)  # veio do episódio
        self.assertNotIn(montagem.arquivo_imagem(len(self.r["cenas"])), geradas)  # o cartão é montado à parte
        with Image.open(self.short.imagens / "cena_01_1.png") as img:
            self.assertEqual(img.size, imagens.TAMANHO_VERTICAL)

    def test_cartao_da_chamada(self):
        cartao = shorts.cartao_chamada(self.short, self.r, self.c)
        self.assertEqual(cartao.name, montagem.arquivo_imagem(len(self.r["cenas"])))
        with Image.open(cartao) as img:
            self.assertEqual(img.size, (864, 1536))

    def test_montagem_vertical(self):
        self.assertEqual(montagem.dimensoes(self.r), (1080, 1920))
        self.assertIn("s=1080x1920", montagem.filtro_movimento(1, 30, montagem.dimensoes(self.r)))
        quadro = self.raiz / "quadro.png"
        Image.new("RGB", imagens.TAMANHO_VERTICAL, "white").save(self.raiz / "img.png")
        montagem.preparar_quadro(self.raiz / "img.png", "PATO", quadro, self.c, (1080, 1920))
        with Image.open(quadro) as img:
            self.assertEqual(img.size, (1080, 1920))

    def test_trilha_do_episodio_vira_fundo(self):
        shorts.copiar_trilha(self.short)
        self.assertEqual(self.short.trilha.read_bytes(), b"trilha")
        self.assertTrue(self.short.ler(self.short.trilha_info)["aprovada"])


class TestCancao(unittest.TestCase):
    def test_letra_vai_para_o_compositor(self):
        with tempfile.TemporaryDirectory() as d:
            origem = episodio_com_animais(Path(d))
            c = canal_com_shorts()
            r = shorts.normalizar(bruto("musica"), origem, origem.carregar_roteiro(), "duck", "musica", c)
            s = episodio.novo_short(origem, 1, "musica", "duck")
            pedidos = []

            def gerador(faixas, pasta, log):
                pedidos.extend(faixas)
                pasta.mkdir(parents=True, exist_ok=True)
                for f in faixas:
                    (pasta / f"{f['nome']}.wav").write_bytes(b"x")
                return {f["nome"]: pasta / f"{f['nome']}.wav" for f in faixas}

            aprovada = {"reprovado": [], "nota": 20}
            with mock.patch.object(musica, "avaliar_cancao", return_value=dict(aprovada)):
                musica.gerar_cancao(s, r, c, shorts.duracao_cancao(r), [], gerador=gerador, cliente=object(), log=SEM_LOG)
            self.assertEqual(pedidos[0]["letra"], r["letra"])
            self.assertEqual(pedidos[0]["idioma_voz"], "pt")
            self.assertEqual(pedidos[0]["duracao"], round(shorts.duracao_cancao(r)))
            info = s.ler(s.trilha_info)
            self.assertEqual(info["letra"], r["letra"])
            self.assertIn(info["estilo"]["id"], [e.id for e in musica.ESTILOS_CANCAO])


class TestPublicacaoDosShorts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.raiz = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_agenda_depois_do_episodio_e_um_por_horario(self):
        c = canal_com_shorts()
        a = episodio_com_animais(self.raiz, "a", datetime(2026, 10, 13, 10, 0, tzinfo=SP))  # terça
        b = episodio_com_animais(self.raiz, "b", datetime(2026, 10, 15, 10, 0, tzinfo=SP))  # quinta
        sem_episodio = episodio_com_animais(self.raiz, "c")
        sem_episodio.publicacao.unlink()
        novos = [short_feito(b, 1, "duck", "pergunta"), short_feito(a, 1, "duck", "pergunta"),
                 short_feito(a, 2, "cow", "musica"), short_feito(sem_episodio, 1, "duck", "pergunta")]
        plano = publicacao.agendar_shorts(novos, novos, c, datetime(2026, 10, 12, 12, 0, tzinfo=SP))
        horarios = {s.nome: q for s, q in plano}
        self.assertEqual(len(plano), 3)  # o do episódio ainda não enviado espera
        self.assertEqual(horarios["a/1-pergunta-duck"], datetime(2026, 10, 13, 17, 0, tzinfo=SP))
        self.assertEqual(horarios["a/2-musica-cow"], datetime(2026, 10, 14, 17, 0, tzinfo=SP))
        self.assertEqual(horarios["b/1-pergunta-duck"], datetime(2026, 10, 15, 17, 0, tzinfo=SP))

    def test_descricao_tem_link_do_episodio_e_shorts_entre_as_hashtags(self):
        origem = episodio_com_animais(self.raiz)
        c = canal_com_shorts()
        r = shorts.normalizar(bruto(), origem, origem.carregar_roteiro(), "duck", "pergunta", c)
        s = episodio.novo_short(origem, 1, "pergunta", "duck")
        descricao = conformidade.descricao_final(s, r, self.raiz / "musica", ["#LuluAndBenny"])
        self.assertIn("▶ Episódio completo: https://youtu.be/EPISODIO123", descricao)
        self.assertEqual(descricao.splitlines()[-1].split()[:3], ["#Pato", "#SonsDeAnimais", "#Shorts"])

    def test_demanda_da_semana(self):
        c = canal_com_shorts()
        agora = datetime(2026, 10, 12, 12, 0, tzinfo=SP)
        # horários de 12/10 (17h ainda dá) a 25/10 (semanas_adiantadas = 1): 14 vagas
        self.assertEqual(automacao.faltam_shorts_para_a_semana([], c, agora), 14)
        origem = episodio_com_animais(self.raiz)
        feitos = [short_feito(origem, k, "duck", f) for k, f in enumerate(shorts.FORMATOS, 1)]  # em produção
        self.assertEqual(automacao.faltam_shorts_para_a_semana(feitos, c, agora), 11)
        c.shorts.ligado = False
        self.assertEqual(automacao.faltam_shorts_para_a_semana(feitos, c, agora), 0)


class TestNaProducao(unittest.TestCase):
    def test_placa_e_montagem_atendem_shorts_depois_dos_episodios(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            origem = episodio_com_animais(Path(d))
            s = short_feito(origem, 1, "duck", "pergunta")
            p = produtor.Produtor(cfg=None, arquivo_canal=None, arquivo_nichos=None, saida=SEM_LOG)
            with mock.patch("estudio.episodio.listar", return_value=[]), \
                    mock.patch("estudio.episodio.listar_shorts", return_value=[s]), \
                    mock.patch.object(shorts, "etapas_na_placa") as placa, mock.patch.object(shorts, "finalizar") as fim:
                self.assertTrue(p.passo_placa(canal_com_shorts(), SEM_LOG))
                self.assertTrue(p.passo_montagem(canal_com_shorts(), SEM_LOG))
            placa.assert_called_once()
            fim.assert_called_once()

    def test_roteiro_escreve_short_quando_os_episodios_estao_cobertos(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            origem = episodio_com_animais(Path(d))
            c = canal_com_shorts()
            c.automacao.semanas_adiantadas = 0
            p = produtor.Produtor(cfg=None, arquivo_canal=None, arquivo_nichos=None, saida=SEM_LOG)
            p.agora = lambda: datetime(2026, 10, 12, 12, 0, tzinfo=SP)
            with mock.patch("estudio.episodio.listar", return_value=[origem]), \
                    mock.patch("estudio.episodio.listar_shorts", return_value=[]), \
                    mock.patch.object(automacao, "faltam_para_a_semana", return_value=(0, datetime(2026, 10, 18, tzinfo=SP))), \
                    mock.patch.object(shorts, "novo_short") as novo:
                self.assertTrue(p.passo_roteiro(c, SEM_LOG))
            self.assertEqual(novo.call_args.args[:3], (origem, "duck", novo.call_args.args[2]))


if __name__ == "__main__":
    unittest.main()
