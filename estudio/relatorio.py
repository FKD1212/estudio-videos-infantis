"""Grava a pesquisa (JSON + CSVs para Excel) e gera o relatório em Markdown."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path

from .pesquisa import ResumoNicho, Video


def salvar_pesquisa(videos: list[Video], pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "pesquisa.json").write_text(
        json.dumps([asdict(v) for v in videos], ensure_ascii=False, indent=1), encoding="utf-8")


def carregar_pesquisa(pasta: Path) -> list[Video]:
    arquivo = pasta / "pesquisa.json"
    if not arquivo.exists():
        raise SystemExit("Nenhuma pesquisa encontrada. Rode primeiro: python -m estudio pesquisar")
    return [Video(**d) for d in json.loads(arquivo.read_text(encoding="utf-8"))]


def _celula(valor) -> str:
    # CSV no padrão do Excel em português: ";" separa colunas, "," separa decimais.
    if isinstance(valor, float):
        return f"{valor:.2f}".replace(".", ",")
    if isinstance(valor, list):
        return ", ".join(valor)
    return "" if valor is None else str(valor)


def _gravar_csv(caminho: Path, cabecalho: list[str], linhas) -> None:
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cabecalho)
        w.writerows([_celula(c) for c in linha] for linha in linhas)


def salvar_csvs(videos: list[Video], resumos: list[ResumoNicho], pasta: Path) -> None:
    colunas_v = [f.name for f in fields(Video)] + ["url"]
    _gravar_csv(pasta / "videos.csv", colunas_v,
                ([getattr(v, c) for c in colunas_v] for v in videos))
    colunas_n = [f.name for f in fields(ResumoNicho)]
    _gravar_csv(pasta / "nichos.csv", colunas_n,
                ([getattr(r, c) for c in colunas_n] for r in resumos))


def _n(x: float | int | None) -> str:
    return "-" if x is None else f"{x:,.0f}".replace(",", ".")


def _d(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _texto(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def gerar_markdown(videos: list[Video], resumos: list[ResumoNicho],
                   estruturas: dict[str, dict], top: int = 10) -> str:
    linhas = [
        f"# Pesquisa de nichos infantis — {date.today():%d/%m/%Y}",
        "",
        "## Ranking de nichos",
        "",
        "| # | Nicho | Idioma | Potencial canal novo (views/dia) | Mediana views/dia | "
        "Vídeos de canais pequenos | Views nos 3 maiores canais | Outliers | Duração mediana | Shorts |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(resumos, 1):
        linhas.append(
            f"| {i} | {r.nicho} | {r.idioma} | {_n(r.potencial_canal_novo)} | {_n(r.mediana_views_dia)} | "
            f"{_pct(r.pct_canais_pequenos)} | {_pct(r.concentracao_top3)} | {r.outliers} | "
            f"{_d(r.duracao_mediana_min)} min | {_pct(r.pct_shorts)} |"
        )
    linhas += [
        "",
        "**Como ler:** *Potencial canal novo* é a mediana de views/dia dos vídeos de canais pequenos "
        "que aparecem entre os mais vistos — quanto maior, mais o nicho recompensa quem está começando "
        "(é zero quando há menos de 3 desses vídeos). *Views nos 3 maiores canais* alto indica nicho "
        "dominado. *Outliers* são vídeos com views muito acima da mediana do próprio canal — é deles "
        "que vem a estrutura a estudar.",
        "",
        "## Vídeos que se destacam",
    ]

    grupos: dict[tuple[str, str], list[Video]] = {}
    for v in videos:
        grupos.setdefault((v.nicho, v.idioma), []).append(v)
    for r in resumos:
        destaque = sorted((v for v in grupos[(r.nicho, r.idioma)] if v.outlier),
                          key=lambda v: v.outlier, reverse=True)[:top]
        if not destaque:
            continue
        linhas += [
            "", f"### {r.nicho} [{r.idioma}]", "",
            "| Outlier | Views | Views/dia | Duração | Canal (inscritos) | Título |",
            "|---:|---:|---:|---:|---|---|",
        ]
        for v in destaque:
            linhas.append(
                f"| {_d(v.outlier)}x | {_n(v.views)} | {_n(v.views_por_dia)} | "
                f"{v.duracao_s // 60}:{v.duracao_s % 60:02d} | {_texto(v.canal)} ({_n(v.inscritos_canal)}) | "
                f"[{_texto(v.titulo)}]({v.url}) |"
            )

    if estruturas:
        linhas += ["", "## Estruturas analisadas"]
        for dados in estruturas.values():
            v, e = dados["video"], dados["estrutura"]
            gancho = e.get("gancho", {})
            linhas += [
                "", f"### [{_texto(v['titulo'])}]({v['url']})",
                f"{v['nicho']} [{v['idioma']}] · {_d(v['outlier'])}x · {e.get('publico_idade', '?')}",
                "",
                f"**Formato:** {e.get('resumo_formato', '')}",
                "",
                f"**Gancho (até {gancho.get('ate_segundo', '?')}s):** {gancho.get('o_que_acontece', '')} "
                f"— {gancho.get('por_que_prende', '')}",
                "",
                "**Padrões de retenção:**",
                *[f"- {p}" for p in e.get("padroes_de_retencao", [])],
                "",
                "**Molde reutilizável:**",
                *[f"{i}. {p}" for i, p in enumerate(e.get("molde_reutilizavel", []), 1)],
            ]
    return "\n".join(linhas) + "\n"
