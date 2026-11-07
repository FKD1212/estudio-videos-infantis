"""Roteiros leves e engraçados: tom de humor em todo roteiro e contos tradicionais de domínio público
como ponto de partida, um diferente por episódio."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from estudio import conformidade, contos, fluxo, roteiro
from estudio.config import ConfigRoteiro
from estudio.episodio import Episodio
from tests.test_producao import canal, roteiro_valido


class TestContos(unittest.TestCase):
    def test_lista_coerente(self):
        ids = [c.id for c in contos.CONTOS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(c.titulo and c.origem and c.ideia for c in contos.CONTOS))

    def test_um_conto_diferente_ate_usar_todos(self):
        usados = []
        for k in range(len(contos.CONTOS)):
            usados.append(contos.proximo(usados, f"semente {k}").id)
        self.assertEqual(sorted(usados), sorted(c.id for c in contos.CONTOS))
        self.assertEqual(contos.proximo(usados, "a"), contos.proximo(usados, "a"))  # escolha estável

    def test_prompt_com_tom_e_conto_com_ajuste(self):
        conto = next(c for c in contos.CONTOS if c.id == "henny-penny")
        p = roteiro.montar_prompt(canal(), "animais", "pt", None, 4, "", [], conto)
        self.assertIn("0. Tom: leve e bem-humorado", p)
        self.assertIn('"Henny Penny"', p)
        self.assertIn("sem raposa", p)
        self.assertNotIn("Ponto de partida", roteiro.montar_prompt(canal(), "animais", "pt", None, 4, "", []))

    def test_tom_configuravel(self):
        c = canal()
        c.roteiro = ConfigRoteiro(tom="calmo e poético", contos=False)
        self.assertIn("0. Tom: calmo e poético.", roteiro.montar_prompt(c, "animais", "pt", None, 4, "", []))


class FakeRoteirista:
    def __init__(self):
        self.entradas = []
        self.interactions = self

    def create(self, **pedido):
        self.entradas.append(pedido["input"])
        return SimpleNamespace(output_text=json.dumps(roteiro_valido()))


class TestEpisodioNovo(unittest.TestCase):
    def test_registra_o_conto_e_avisa_a_revisao(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            raiz = Path(d) / "episodios"
            gemini = FakeRoteirista()
            c = canal()
            cfg = SimpleNamespace(nichos=[SimpleNamespace(nome="animais")])
            novo = lambda *a, **k: Episodio(raiz / "novo")  # noqa: E731
            with mock.patch("estudio.episodio.listar", return_value=[]), \
                    mock.patch("estudio.episodio.novo", side_effect=novo), \
                    mock.patch.object(roteiro.gemini, "cliente", return_value=gemini):
                ep = fluxo.novo_episodio(cfg, c, "animais", "pt", None, 4, Path("nichos.toml"), log=lambda *_: None)
            r = ep.carregar_roteiro()
            self.assertIn(r["inspiracao"], [x.id for x in contos.CONTOS])
            self.assertIn("Ponto de partida", gemini.entradas[0])
            self.assertIn("conto tradicional", conformidade.prompt_revisao(r, c, []))

            with mock.patch("estudio.episodio.listar", return_value=[]), \
                    mock.patch("estudio.episodio.novo", side_effect=lambda *a, **k: Episodio(raiz / "com-tema")), \
                    mock.patch.object(roteiro.gemini, "cliente", return_value=gemini):
                ep = fluxo.novo_episodio(cfg, c, "animais", "pt", "a hora do banho", 4, Path("nichos.toml"),
                                         log=lambda *_: None)
            self.assertNotIn("inspiracao", ep.carregar_roteiro())  # tema seu vale mais que um conto


if __name__ == "__main__":
    unittest.main()
