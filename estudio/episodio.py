"""Pasta de cada episódio e o registro do que já foi gerado nela, para retomar sem refazer."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import DADOS

PASTA_EPISODIOS = DADOS / "episodios"
_assinaturas: dict[tuple[str, str], str] = {}  # (pasta, data e tamanho dos arquivos) -> assinatura


@dataclass
class Episodio:
    pasta: Path

    @property
    def nome(self) -> str:
        """O nome da pasta; num Short, episódio/short (os números dos Shorts se repetem entre episódios)."""
        return f"{self.pasta.parent.parent.name}/{self.pasta.name}" if self.e_short else self.pasta.name

    @property
    def e_short(self) -> bool:
        """Short vertical tirado de um episódio: fica em <episódio>/shorts/<short>."""
        return self.pasta.parent.name == "shorts"

    @property
    def origem(self) -> "Episodio":
        """O episódio de onde o Short saiu."""
        return Episodio(self.pasta.parent.parent)

    @property
    def pasta_shorts(self) -> Path:
        return self.pasta / "shorts"

    @property
    def arquivo_roteiro(self) -> Path:
        return self.pasta / "roteiro.json"

    @property
    def narracao(self) -> Path:
        return self.pasta / "narracao"

    @property
    def imagens(self) -> Path:
        return self.pasta / "imagens"

    @property
    def montagem(self) -> Path:
        return self.pasta / "montagem"

    @property
    def video(self) -> Path:
        return self.pasta / "video.mp4"

    @property
    def miniatura(self) -> Path:
        return self.pasta / "miniatura.jpg"

    @property
    def legendas(self) -> Path:
        return self.pasta / "legendas.srt"

    # Registros de cada etapa: roteiro como saiu do Gemini, o que a montagem usou, resultado da
    # revisão, aprovação humana e publicação.
    @property
    def roteiro_gerado(self) -> Path:
        return self.pasta / "roteiro_gerado.json"

    @property
    def producao(self) -> Path:
        return self.pasta / "producao.json"

    @property
    def revisao(self) -> Path:
        return self.pasta / "revisao.json"

    @property
    def aprovacao(self) -> Path:
        return self.pasta / "aprovacao.json"

    @property
    def publicacao(self) -> Path:
        return self.pasta / "publicacao.json"

    @property
    def metricas(self) -> Path:
        return self.pasta / "metricas.json"

    @property
    def pasta_trilha(self) -> Path:
        return self.pasta / "musica"

    @property
    def trilha(self) -> Path:
        return self.pasta_trilha / "trilha.wav"

    @property
    def trilha_info(self) -> Path:
        return self.pasta_trilha / "trilha.json"

    def ler(self, arquivo: Path) -> dict | None:
        return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else None

    def gravar(self, arquivo: Path, dados: dict) -> None:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

    def _entrega(self) -> tuple[Path, ...]:
        return self.video, self.miniatura, self.legendas, self.arquivo_roteiro, self.producao

    def estado_arquivos(self) -> list:
        """Data e tamanho do que vai para o YouTube: conferir isso é instantâneo, ler o vídeo não."""
        return [[a.name, a.stat().st_mtime_ns, a.stat().st_size] if a.exists() else [a.name, None, None]
                for a in self._entrega()]

    def assinatura_entrega(self) -> str:
        """Muda se qualquer coisa que vai para o YouTube mudar: a aprovação vale só para esta versão.
        Guardada por data e tamanho dos arquivos, para não reler vídeos que não mudaram."""
        chave = (str(self.pasta), json.dumps(self.estado_arquivos()))
        if chave not in _assinaturas:
            _assinaturas[chave] = assinatura(*[hashlib.sha1(a.read_bytes()).hexdigest() if a.exists() else None
                                               for a in self._entrega()])
        return _assinaturas[chave]

    def carregar_roteiro(self) -> dict:
        try:
            return json.loads(self.arquivo_roteiro.read_text(encoding="utf-8"))
        except json.JSONDecodeError as erro:
            raise SystemExit(f"{self.arquivo_roteiro} tem um erro de digitação perto da linha "
                             f"{erro.lineno}: {erro.msg}")

    def salvar_roteiro(self, roteiro: dict) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.arquivo_roteiro.write_text(json.dumps(roteiro, ensure_ascii=False, indent=2), encoding="utf-8")


def slug(texto: str, limite: int = 40) -> str:
    ascii_ = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:limite].rstrip("-") or "episodio"


def novo(nicho: str, idioma: str, titulo: str, hoje: date | None = None,
         raiz: Path = PASTA_EPISODIOS) -> Episodio:
    base = f"{hoje or date.today():%Y-%m-%d}-{nicho}-{idioma}-{slug(titulo)}"
    pasta, n = raiz / base, 2
    while pasta.exists():
        pasta, n = raiz / f"{base}-{n}", n + 1
    pasta.mkdir(parents=True)
    return Episodio(pasta)


def listar(raiz: Path = PASTA_EPISODIOS) -> list[Episodio]:
    """Episódios com roteiro, do mais antigo para o mais recente."""
    if not raiz.exists():
        return []
    return [Episodio(p) for p in sorted(raiz.iterdir()) if (p / "roteiro.json").exists()]


def listar_shorts(raiz: Path = PASTA_EPISODIOS) -> list[Episodio]:
    """Shorts de todos os episódios, na ordem do episódio e depois do número do Short."""
    if not raiz.exists():
        return []
    return [Episodio(p.parent) for p in sorted(raiz.glob("*/shorts/*/roteiro.json"))]


def novo_short(origem: Episodio, numero: int, formato: str, animal: str) -> Episodio:
    pasta = origem.pasta_shorts / f"{numero}-{formato}-{slug(animal, 20)}"
    pasta.mkdir(parents=True, exist_ok=False)
    return Episodio(pasta)


def localizar(nome: str | None, raiz: Path = PASTA_EPISODIOS) -> Episodio:
    """Pelo nome (ou parte dele); sem nome, o último criado."""
    episodios = listar(raiz)
    if not episodios:
        raise SystemExit("Nenhum episódio ainda. Crie um com: python -m estudio roteiro --nicho ...")
    if not nome:
        return max(episodios, key=lambda e: (e.arquivo_roteiro.stat().st_ctime, e.nome))
    achados = [e for e in episodios if e.nome == nome] or [e for e in episodios if nome in e.nome]
    if len(achados) != 1:
        opcoes = "\n  ".join(e.nome for e in (achados or episodios))
        raise SystemExit(f"'{nome}' {'é ambíguo' if achados else 'não encontrado'}. Episódios:\n  {opcoes}")
    return achados[0]


def assinatura(*partes) -> str:
    return hashlib.sha1(json.dumps(partes, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Manifesto:
    """Guarda a assinatura (hash dos parâmetros) de cada arquivo gerado numa pasta. Se o roteiro
    muda, só o que mudou é refeito; trocar um arquivo à mão (ex.: gravar a própria voz numa cena)
    também funciona, porque o arquivo existente é mantido enquanto a assinatura não muda."""

    def __init__(self, pasta: Path):
        self.pasta = pasta
        self.arquivo = pasta / "manifesto.json"
        self.dados: dict[str, str] = (
            json.loads(self.arquivo.read_text(encoding="utf-8")) if self.arquivo.exists() else {})

    def atual(self, nome: str, assinatura_: str) -> bool:
        return (self.pasta / nome).exists() and self.dados.get(nome) == assinatura_

    def registrar(self, nome: str, assinatura_: str) -> None:
        self.dados[nome] = assinatura_
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.arquivo.write_text(json.dumps(self.dados, indent=1, sort_keys=True), encoding="utf-8")
