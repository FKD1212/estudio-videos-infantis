"""Fase 4: monta o vídeo — cada imagem com um movimento lento de câmera, a narração e a música
de fundo, que abaixa sozinha quando a narradora fala. Gera também as legendas (.srt), a
miniatura e o texto para colar no YouTube Studio. Usa o ffmpeg que vem no pacote
imageio-ffmpeg, então não há nada para instalar no Windows."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import recursos, sons
from .config import RAIZ, Canal
from .episodio import Episodio, Manifesto, assinatura
from .narracao import arquivo_cena, duracao_wav
from .roteiro import tomadas

LARGURA, ALTURA, FPS = 1920, 1080, 30
VERTICAL = (1080, 1920)  # Shorts
PAUSA_APOS_FALA_S = 0.6
PAUSA_APOS_SOM_S = 0.5
SEM_FALA_S = 3.0
FINAL_S = 2.0          # a última imagem fica mais um pouco, enquanto a música termina
ZOOM = 0.10            # quanto a câmera aproxima ou afasta ao longo de uma cena
MOVIMENTOS = ("aproximar", "direita", "afastar", "esquerda")
VOLUME_MUSICA = 0.18   # antes do abaixamento automático sob a narração
VOLUME_CANCAO = 0.9    # Short no formato música: a canção é o conteúdo, não o fundo
PASTA_MUSICA = RAIZ / "musica"
EXTENSOES_MUSICA = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
FONTES = ["C:/Windows/Fonts/seguibl.ttf", "C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/arialbd.ttf"]
_MARCAS_DE_FALA = re.compile(r"<[^>]*>")  # <long pause>, <laugh>...


def dimensoes(roteiro: dict) -> tuple[int, int]:
    return VERTICAL if roteiro.get("formato_video") == "vertical" else (LARGURA, ALTURA)


def arquivo_imagem(n: int, k: int = 1) -> str:
    """k-ésima imagem (tomada) da cena n."""
    return f"cena_{n:02d}_{k}.png"


@dataclass
class Tempo:
    fala_s: float
    quadros: int
    som_s: float = 0.0  # repetições do som do animal, logo depois da fala

    @property
    def tela_s(self) -> float:
        return self.quadros / FPS

    @property
    def inicio_som_s(self) -> float:
        return self.fala_s + PAUSA_APOS_FALA_S if self.fala_s else 0.0


def calcular_tempos(ep: Episodio, roteiro: dict) -> list[Tempo]:
    """Cada cena fica na tela o tempo da fala, mais uma pausa, mais o som do animal se houver (ou
    "duracao_s", se maior), arredondado para quadros inteiros para o áudio não escorregar em relação
    à imagem."""
    tempos = []
    cenas = roteiro["cenas"]
    for n, cena in enumerate(cenas, 1):
        fala = 0.0
        if cena["narracao"].strip():
            wav = ep.narracao / arquivo_cena(n)
            if not wav.exists():
                raise SystemExit(f"Falta a narração da cena {n}. Rode: python -m estudio narrar")
            fala = duracao_wav(wav)
        som = sons.duracao_bloco(cena.get("som", ""), sons.PASTA_SONS)
        conteudo = (fala + PAUSA_APOS_FALA_S if fala else 0) + (som + PAUSA_APOS_SOM_S if som else 0)
        definida = float(cena.get("duracao_s") or 0)
        tela = max(conteudo, definida) if conteudo else definida or SEM_FALA_S
        if n == len(cenas):
            tela += FINAL_S
        tempos.append(Tempo(fala, round(tela * FPS), som))
    return tempos


def dividir_quadros(total: int, partes: int) -> list[int]:
    """Reparte os quadros de uma cena entre as suas imagens (a última leva o resto)."""
    base = total // partes
    return [base] * (partes - 1) + [total - base * (partes - 1)]


# ---------- legendas ----------

def _trechos(texto: str, limite: int = 84) -> list[str]:
    """Divide a fala em legendas de até duas linhas: por frase e, se preciso, por vírgula ou palavra."""
    texto = re.sub(r"\s+", " ", _MARCAS_DE_FALA.sub(" ", texto)).strip()
    trechos = []
    for frase in re.split(r"(?<=[.!?…])\s+", texto):
        while len(frase) > limite:
            corte = frase.rfind(", ", 0, limite) + 1
            if corte < limite // 3:
                corte = frase.rfind(" ", 0, limite)
            if corte <= 0:
                corte = limite
            trechos.append(frase[:corte].strip())
            frase = frase[corte:].strip()
        if frase:
            trechos.append(frase)
    return trechos


def _duas_linhas(texto: str, largura: int = 42) -> str:
    if len(texto) <= largura or " " not in texto:
        return texto
    meio = len(texto) // 2
    espacos = [i for i, c in enumerate(texto) if c == " "]
    corte = min(espacos, key=lambda i: abs(i - meio))
    return texto[:corte] + "\n" + texto[corte + 1:]


def _tempo_srt(s: float) -> str:
    ms = round(s * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s_, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s_:02d},{ms:03d}"


def legendas(roteiro: dict, tempos: list[Tempo]) -> str:
    """Cada trecho ganha uma fatia da fala da cena proporcional ao número de letras."""
    blocos, inicio = [], 0.0
    for cena, t in zip(roteiro["cenas"], tempos):
        trechos = _trechos(cena["narracao"])
        letras = sum(len(x) for x in trechos)
        a = inicio
        for trecho in trechos:
            b = a + t.fala_s * len(trecho) / letras
            blocos.append(f"{len(blocos) + 1}\n{_tempo_srt(a)} --> {_tempo_srt(b)}\n{_duas_linhas(trecho)}\n")
            a = b
        inicio += t.tela_s
    return "\n".join(blocos)


# ---------- imagem ----------

def _fonte(canal: Canal, tamanho: int):
    from PIL import ImageFont

    for caminho in ([canal.fonte] if canal.fonte else []) + FONTES:
        if Path(caminho).exists():
            return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default(tamanho)


def _escrever(imagem, texto: str, caixa: tuple[int, int, int, int], canal: Canal, tamanho: int) -> None:
    """Texto branco com contorno escuro, centralizado na caixa, diminuindo até caber."""
    from PIL import ImageDraw

    desenho = ImageDraw.Draw(imagem)
    x0, y0, x1, y1 = caixa
    while True:
        fonte, contorno = _fonte(canal, tamanho), max(2, tamanho // 12)
        e, t, d, b = desenho.multiline_textbbox((0, 0), texto, font=fonte, stroke_width=contorno, align="center")
        if (d - e <= x1 - x0 and b - t <= y1 - y0) or tamanho <= 20:
            break
        tamanho = int(tamanho * 0.9)
    desenho.multiline_text(
        (x0 + (x1 - x0 - (d - e)) / 2 - e, y0 + (y1 - y0 - (b - t)) / 2 - t), texto, font=fonte,
        fill="white", stroke_width=contorno, stroke_fill=(35, 35, 50), align="center")


def preparar_quadro(origem: Path, texto: str, saida: Path, canal: Canal,
                    tamanho: tuple[int, int] = (LARGURA, ALTURA)) -> None:
    from PIL import Image

    largura, altura = tamanho
    with Image.open(origem) as img:
        quadro = img.convert("RGB").resize(tamanho, Image.LANCZOS)
    if texto:
        # No alto: embaixo ficam as legendas do YouTube (e, nos Shorts, título e botões), e o
        # movimento corta pouco das bordas.
        caixa = ((int(largura * 0.1), int(altura * 0.08), int(largura * 0.9), int(altura * 0.30)) if largura > altura
                 else (int(largura * 0.08), int(altura * 0.10), int(largura * 0.92), int(altura * 0.24)))
        _escrever(quadro, texto, caixa, canal, 150)
    quadro.save(saida)


def miniatura(ep: Episodio, roteiro: dict, canal: Canal) -> Path:
    from PIL import Image

    base = ep.imagens / "miniatura_base.png"
    if not base.exists():
        base = ep.imagens / arquivo_imagem(1)
    vertical = dimensoes(roteiro) == VERTICAL
    with Image.open(base) as img:
        mini = img.convert("RGB").resize((1080, 1920) if vertical else (1280, 720), Image.LANCZOS)
    texto = roteiro.get("miniatura", {}).get("texto", "").strip()
    if texto:
        _escrever(mini, _duas_linhas(texto.upper(), 14), (40, 30, 1240, 330), canal, 170)
    saida = ep.miniatura
    mini.save(saida, quality=90)  # o YouTube aceita até 2 MB
    return saida


# ---------- vídeo ----------

def ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        caminho = shutil.which("ffmpeg")
        if not caminho:
            raise SystemExit("ffmpeg não encontrado. Instale: .\\.venv\\Scripts\\python.exe -m pip install imageio-ffmpeg")
        return caminho


def _rodar(argumentos: list[str]) -> None:
    resultado = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *argumentos],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
    if resultado.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou:\n{resultado.stderr.strip()[-2000:]}")


def filtro_movimento(n: int, quadros: int, tamanho: tuple[int, int] = (LARGURA, ALTURA)) -> str:
    """Zoom ou panorâmica lenta. A imagem é ampliada 2x antes, o que deixa o movimento suave."""
    largura, altura = tamanho
    p = f"on/{max(quadros - 1, 1)}"  # progresso de 0 a 1 ao longo da cena
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    z, x, y = {
        "aproximar": (f"1+{ZOOM}*{p}", cx, cy),
        "afastar": (f"1+{ZOOM}-{ZOOM}*{p}", cx, cy),
        "direita": (f"1+{ZOOM}", f"(iw-iw/zoom)*{p}", cy),
        "esquerda": (f"1+{ZOOM}", f"(iw-iw/zoom)*(1-{p})", cy),
    }[MOVIMENTOS[(n - 1) % len(MOVIMENTOS)]]
    return (f"scale={2 * largura}:{2 * altura}:flags=lanczos,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={quadros}:s={largura}x{altura}:fps={FPS}")


def _clipe(quadro: Path, saida: Path, n: int, quadros: int, primeiro: bool, ultimo: bool,
           tamanho: tuple[int, int] = (LARGURA, ALTURA)) -> None:
    filtro = filtro_movimento(n, quadros, tamanho)
    if primeiro:
        filtro += ",fade=t=in:st=0:d=0.5"
    if ultimo:
        filtro += f",fade=t=out:st={max(quadros / FPS - 1, 0):.3f}:d=1"
    _rodar(["-i", str(quadro), "-vf", filtro + ",format=yuv420p", "-frames:v", str(quadros),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS), str(saida)])


def escolher_musica(roteiro: dict, chave: str, pasta: Path = PASTA_MUSICA) -> Path | None:
    """A faixa definida em "musica" no roteiro; senão, uma da subpasta do clima (musica/alegre,
    musica/calma...) ou de qualquer subpasta. A escolha é fixa por episódio."""
    if roteiro.get("musica"):
        faixa = RAIZ / roteiro["musica"]
        if not faixa.exists():
            raise SystemExit(f"Música do roteiro não encontrada: {faixa}")
        return faixa

    def faixas(p: Path) -> list[Path]:
        return sorted(a for a in p.rglob("*") if a.suffix.lower() in EXTENSOES_MUSICA) if p.exists() else []

    opcoes = faixas(pasta / roteiro.get("clima_musica", "")) or faixas(pasta)
    if not opcoes:
        return None
    return opcoes[int(hashlib.sha1(chave.encode()).hexdigest(), 16) % len(opcoes)]


def segmentos_de_audio(ep: Episodio, roteiro: dict, tempos: list[Tempo]) -> list[tuple[Path | None, float]]:
    """Vozes e sons em pedaços de duração exata, na ordem: fala da cena, repetições do som do
    animal, silêncio até a próxima cena. None é silêncio."""
    segmentos: list[tuple[Path | None, float]] = []
    for n, (cena, t) in enumerate(zip(roteiro["cenas"], tempos), 1):
        usado = 0.0
        if t.fala_s:
            duracao = t.inicio_som_s if t.som_s else t.tela_s
            segmentos.append((ep.narracao / arquivo_cena(n), duracao))
            usado += duracao
        if t.som_s:
            for _ in range(sons.REPETICOES):
                segmentos.append((sons.arquivo(cena["som"]), t.som_s / sons.REPETICOES))
            usado += t.som_s
        if t.tela_s - usado > 0.0005:
            segmentos.append((None, t.tela_s - usado))
    return segmentos


def _juntar(ep: Episodio, roteiro: dict, tempos: list[Tempo], musica: Path | None) -> None:
    """Imagens em sequência + vozes e sons (cada fala no início da sua cena) + música com abaixamento."""
    entradas = ["-f", "concat", "-safe", "0", "-i", str(ep.montagem / "clipes.txt")]
    partes, rotulos, k = [], [], 1
    for i, (arquivo, duracao) in enumerate(segmentos_de_audio(ep, roteiro, tempos)):
        if arquivo:
            entradas += ["-i", str(arquivo)]
            partes.append(f"[{k}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                          f"apad=whole_dur={duracao:.4f},atrim=0:{duracao:.4f}[s{i}]")
            k += 1
        else:
            partes.append(f"anullsrc=r=48000:cl=stereo:d={duracao:.4f}[s{i}]")
        rotulos.append(f"[s{i}]")
    partes.append("".join(rotulos) + f"concat=n={len(rotulos)}:v=0:a=1[narr]")
    total = sum(t.tela_s for t in tempos)
    final = f"afade=t=out:st={max(total - 2, 0):.3f}:d=2,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]"
    if musica:
        # Short em formato música: a canção toca uma vez, alta, a partir da cena marcada (depois do
        # gancho falado); a trilha de fundo comum repete até o fim, baixa.
        cancao = roteiro.get("formato") == "musica"
        atraso = round(1000 * sum(t.tela_s for t in tempos[:max(0, int(roteiro.get("musica_desde_cena", 1)) - 1)]))
        entradas += [*([] if cancao else ["-stream_loop", "-1"]), "-i", str(musica)]
        ajuste = (f"volume={VOLUME_CANCAO},adelay={atraso}|{atraso},apad" if cancao else f"volume={VOLUME_MUSICA}")
        partes += [
            "[narr]asplit=2[voz][chave]",
            f"[{k}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,{ajuste}[mus]",
            "[mus][chave]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=600[fundo]",
            f"[voz][fundo]amix=inputs=2:duration=first:normalize=0,{final}",
        ]
    else:
        partes.append(f"[narr]{final}")
    _rodar([*entradas, "-filter_complex", ";".join(partes), "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
            "-movflags", "+faststart", str(ep.video)])


def _hash_arquivo(caminho: Path) -> str:
    return hashlib.sha1(caminho.read_bytes()).hexdigest()


def texto_publicacao(roteiro: dict, canal: Canal, musica: Path | None, origem: str = "biblioteca") -> str:
    yt, idioma = roteiro["youtube"], canal.narracao[roteiro["idioma"]].idioma
    linhas = [
        "TÍTULO", yt["titulo"], "",
        "DESCRIÇÃO", yt["descricao"], "",
        "TAGS", ", ".join(yt["tags"]), "",
        "ARQUIVOS", "- vídeo: video.mp4", "- miniatura: miniatura.jpg", f"- legendas ({idioma}): legendas.srt", "",
    ]
    if musica and origem == "ia":
        linhas += ["MÚSICA: trilha original composta por IA para este episódio (musica/trilha.json)", ""]
    elif musica:
        nome = musica.relative_to(RAIZ) if musica.is_relative_to(RAIZ) else musica
        linhas += [f"MÚSICA: {nome}", "Se a faixa pedir atribuição, cole o crédito no fim da descrição.", ""]
    linhas += [
        "ANTES DE APROVAR",
        "[ ] Assisti ao vídeo inteiro: imagem estranha, pronúncia errada, texto com erro",
        "",
        "SE FOR ENVIAR À MÃO PELO YOUTUBE STUDIO (o comando publicar já faz tudo isto)",
        '[ ] Público: "Sim, é conteúdo para crianças"',
        f"[ ] Idioma do vídeo e das legendas: {idioma}",
        "[ ] Conteúdo alterado ou sintético: a marcação é exigida quando o vídeo parece real; desenho "
        "animado claramente irreal não precisa (confira a regra atual no Studio)",
    ]
    return "\n".join(linhas) + "\n"


def montar(ep: Episodio, roteiro: dict, canal: Canal, log=print) -> Path:
    cenas = roteiro["cenas"]
    faltam = [f"{n}.{k}" for n, c in enumerate(cenas, 1) for k in range(1, len(tomadas(c)) + 1)
              if not (ep.imagens / arquivo_imagem(n, k)).exists()]
    if faltam:
        raise SystemExit(f"Faltam as imagens {', '.join(faltam)}. Rode: python -m estudio imagens")
    tempos = calcular_tempos(ep, roteiro)
    tamanho = dimensoes(roteiro)

    ep.montagem.mkdir(parents=True, exist_ok=True)
    manifesto = Manifesto(ep.montagem)
    clipes: list[str] = []
    pendentes = []
    total = sum(len(tomadas(c)) for c in cenas)
    for n, (cena, t) in enumerate(zip(cenas, tempos), 1):
        # As imagens de uma fala dividem o tempo dela; cada uma com um movimento de câmera diferente.
        for k, quadros in enumerate(dividir_quadros(t.quadros, len(tomadas(cena))), 1):
            g = len(clipes) + 1
            origem, nome = ep.imagens / arquivo_imagem(n, k), f"clipe_{n:02d}_{k}.mp4"
            primeiro, ultimo = g == 1, g == total
            clipes.append(nome)
            assinatura_ = assinatura(_hash_arquivo(origem), cena["texto_na_tela"], canal.fonte, quadros,
                                     filtro_movimento(g, quadros, tamanho), primeiro, ultimo)
            if not manifesto.atual(nome, assinatura_):
                pendentes.append((n, k, g, origem, nome, cena["texto_na_tela"], quadros, primeiro, ultimo, assinatura_))

    def fazer(trabalho) -> None:
        n, k, g, origem, nome, texto, quadros, primeiro, ultimo, _ = trabalho
        quadro = ep.montagem / f"quadro_{n:02d}_{k}.png"
        preparar_quadro(origem, texto, quadro, canal, tamanho)
        _clipe(quadro, ep.montagem / nome, g, quadros, primeiro, ultimo, tamanho)

    if pendentes:  # clipes independentes: vários encodes ao mesmo tempo aproveitam o processador
        log(f"  {len(pendentes)} clipes, {recursos.nucleos_para_montagem()} de cada vez")
        with ThreadPoolExecutor(max_workers=recursos.nucleos_para_montagem()) as executor:
            list(executor.map(fazer, pendentes))
        for trabalho in pendentes:
            manifesto.registrar(trabalho[4], trabalho[-1])
    (ep.montagem / "clipes.txt").write_text("".join(f"file '{c}'\n" for c in clipes), encoding="utf-8")

    # Ordem: faixa escolhida à mão no roteiro > trilha composta por IA > biblioteca em musica/
    if not roteiro.get("musica") and canal.musica.fonte == "ia":
        if not ep.trilha.exists():
            raise SystemExit("Falta a trilha do episódio. Rode: python -m estudio trilha")
        musica, origem = ep.trilha, "ia"
    else:
        musica, origem = escolher_musica(roteiro, ep.nome, PASTA_MUSICA), "biblioteca"
        if not musica:
            log(f"  aviso: nenhuma música em {PASTA_MUSICA}; o vídeo sai só com a narração")
    log("  juntando cenas, narração e música...")
    _juntar(ep, roteiro, tempos, musica)
    ep.legendas.write_text(legendas(roteiro, tempos), encoding="utf-8")
    miniatura(ep, roteiro, canal)
    (ep.pasta / "publicar.txt").write_text(texto_publicacao(roteiro, canal, musica, origem), encoding="utf-8")
    ep.gravar(ep.producao, {
        "musica": ep.trilha.relative_to(ep.pasta).as_posix() if origem == "ia" else nome_musica(musica),
        "musica_origem": origem if musica else None,
        "sons": sorted({c["som"] for c, t in zip(cenas, tempos) if t.som_s}),
        "sons_faltando": sorted({c["som"] for c, t in zip(cenas, tempos) if c.get("som") and not t.som_s}),
        "duracao_s": round(sum(t.tela_s for t in tempos), 2),
    })
    return ep.video


def nome_musica(faixa: Path | None) -> str | None:
    """Caminho relativo à pasta musica/ (é a chave em musica/licencas.toml)."""
    if faixa is None:
        return None
    return (faixa.relative_to(PASTA_MUSICA) if faixa.is_relative_to(PASTA_MUSICA) else faixa).as_posix()
