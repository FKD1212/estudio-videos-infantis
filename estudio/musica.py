"""Fase 3c: trilha original de cada episódio, composta por IA na sua placa de vídeo com o
ACE-Step 1.5 (código e pesos MIT, uso comercial da música gerada liberado, treinado com música
licenciada, de domínio público e sintética).

Dos canais de sucesso aproveita-se o ESTILO — gênero, andamento, instrumentos, energia e forma
(intro curta, tema que se repete, ponte) — e nunca melodia, letra ou gravação de ninguém: isso
é protegido por direito autoral, e o Content ID mandaria a receita do vídeo para o dono.

Cada episódio gera algumas versões. Todas passam por medições objetivas (duração, volume,
buracos de silêncio) e são ouvidas pelo Gemini (agradável para crianças, qualidade, sem voz,
sem lembrar música conhecida); fica a mais bem avaliada. O estilo do episódio é escolhido pelo
desempenho medido no YouTube Analytics (metricas.py)."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import gemini, metricas, recursos
from .config import DADOS, RAIZ, Canal
from .episodio import Episodio, Manifesto, assinatura
from .montagem import ffmpeg
from .pesquisa import Video

PASTA_ACESTEP = RAIZ / "ferramentas" / "ACE-Step-1.5"
PYTHON_ACESTEP = PASTA_ACESTEP / ".venv" / "Scripts" / "python.exe"
WORKER = Path(__file__).with_name("acestep_worker.py")
MODELO_MUSICA = "acestep-v15-turbo"
LICENCA = "gerada com ACE-Step 1.5 (código e pesos MIT; uso comercial da música liberado)"
ARQUIVO_ESTILOS = DADOS / "estilos_musicais.json"
CLIMAS = ("calma", "alegre", "aventura")
MAX_PESQUISADOS_POR_CLIMA = 3
NOTA_MINIMA = 6          # de 0 a 10, nas notas do Gemini
SILENCIO_MAXIMO_S = 1.5  # buraco de silêncio no meio da faixa reprova
RODADAS = 2              # se nenhuma versão passar, tenta mais uma leva com outras sementes
_TOM = re.compile(r"^[A-G][#b]? (major|minor)$")


@dataclass
class Estilo:
    id: str
    clima: str
    descricao: str  # em inglês, para o gerador; genérica, sem nomes de músicas ou artistas
    bpm: int
    tom: str        # ex.: "C major", "Bb major", "A minor"
    origem: str = "padrão"


ESTILOS_PADRAO = [
    Estilo("alegre-ukulele", "alegre", "Cheerful instrumental children's music, ukulele strumming, glockenspiel "
           "melody, soft hand claps and light shaker, bright and bouncy, short intro then a simple catchy "
           "repeating theme", 112, "C major"),
    Estilo("alegre-pop", "alegre", "Upbeat instrumental kids pop, acoustic guitar, xylophone lead, light drums "
           "and bass, sunny and playful, simple repeating hook with a gentle bridge", 120, "G major"),
    Estilo("calma-caixinha", "calma", "Gentle instrumental lullaby for small children, music box and celesta "
           "melody, soft warm pads, slow and soothing, calm and cozy", 70, "F major"),
    Estilo("calma-violao", "calma", "Soft instrumental acoustic guitar fingerpicking with light piano and flute, "
           "warm and tender, relaxed storytelling background for kids", 80, "D major"),
    Estilo("aventura-orquestra", "aventura", "Playful orchestral adventure music for kids, pizzicato strings, "
           "flute and clarinet melody, light timpani and percussion, curious and exciting, builds gently",
           100, "D major"),
    Estilo("aventura-marcha", "aventura", "Light whimsical march for children, bouncy tuba and bassoon, "
           "woodwinds, snare brushes and glockenspiel, brave and funny, simple repeating theme", 108, "Bb major"),
]


# ---------- estilos aprendidos dos vídeos que se destacam ----------

_TEXTO = {"type": "string"}
ESQUEMA_PERFIL = {
    "type": "object",
    "properties": {
        "tem_musica": {"type": "boolean"},
        "papel": {"type": "string", "enum": ["trilha de fundo", "canção"]},
        "clima": {"type": "string", "enum": list(CLIMAS)},
        "genero": _TEXTO, "bpm": {"type": "integer"}, "tom": _TEXTO,
        "instrumentos": {"type": "array", "items": _TEXTO},
        "estrutura": _TEXTO, "energia": _TEXTO,
        "recursos_que_prendem": {"type": "array", "items": _TEXTO},
        "descricao_para_gerador": _TEXTO,
    },
    "required": ["tem_musica", "papel", "clima", "genero", "bpm", "tom", "instrumentos", "estrutura", "energia",
                 "recursos_que_prendem", "descricao_para_gerador"],
}


def prompt_perfil(v: Video) -> str:
    return (
        "Você é produtor musical de conteúdo infantil. Ouça a música deste vídeo (trilha de fundo ou canção "
        "principal) e descreva só o ESTILO, para compormos uma trilha instrumental ORIGINAL com o mesmo clima. "
        "Não transcreva melodia nem letra e não cite nomes de músicas, artistas, canais ou personagens.\n\n"
        f'Contexto: "{v.titulo}", {v.outlier or 0:.1f}x as views normais do canal.\n\n'
        'Campos: "tem_musica" (falso se o vídeo quase não tem música); "papel"; "clima"; "genero"; "bpm" '
        'aproximado; "tom" no formato "C major" ou "A minor"; "instrumentos"; "estrutura" (forma em termos '
        'genéricos: intro, tema, repetição, ponte, final); "energia"; "recursos_que_prendem" (palmas, contagem, '
        'pausas, mudanças de energia...); "descricao_para_gerador": EM INGLÊS, 1 a 3 frases, começando por '
        '"Instrumental", com gênero, instrumentos, clima e forma, genérica e sem nenhum nome próprio.'
    )


def pesquisar_estilos(videos: list[Video], cliente=None, log=print, arquivo: Path = ARQUIVO_ESTILOS) -> int:
    """Gemini ouve os vídeos escolhidos e guarda o perfil musical de cada um (pula os já ouvidos)."""
    salvos = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}
    modelo = os.getenv("GEMINI_MODELO", gemini.MODELO_PADRAO)
    feitos = 0
    for v in videos:
        if v.id in salvos:
            continue
        cliente = cliente or gemini.cliente()
        log(f"  ouvindo {v.nicho} [{v.idioma}] {v.outlier or 0:.1f}x  {v.titulo[:60]}")
        try:
            interacao = gemini.criar(cliente, log=log, model=modelo,
                                     input=[{"type": "text", "text": prompt_perfil(v)}, {"type": "video", "uri": v.url, "resolution": "low"}],
                                     response_format={"type": "text", "mime_type": "application/json",
                                                      "schema": ESQUEMA_PERFIL})
            perfil = gemini.extrair_json(interacao.output_text)
        except gemini.CotaGeminiEsgotada:
            raise
        except Exception as erro:  # um vídeo com problema não deve parar o lote
            log(f"    falhou: {erro}")
            continue
        salvos[v.id] = {"perfil": perfil, "video": {"titulo": v.titulo, "url": v.url, "nicho": v.nicho,
                                                     "idioma": v.idioma, "outlier": v.outlier}}
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(salvos, ensure_ascii=False, indent=2), encoding="utf-8")
        feitos += 1
    return feitos


def carregar_estilos(arquivo: Path = ARQUIVO_ESTILOS) -> list[Estilo]:
    """Estilos padrão + os aprendidos dos maiores outliers (até 3 por clima)."""
    estilos = list(ESTILOS_PADRAO)
    if not arquivo.exists():
        return estilos
    salvos = json.loads(arquivo.read_text(encoding="utf-8"))
    por_clima: dict[str, int] = {}
    for video_id, dados in sorted(salvos.items(), key=lambda kv: kv[1]["video"].get("outlier") or 0, reverse=True):
        p = dados["perfil"]
        clima, descricao = p.get("clima"), (p.get("descricao_para_gerador") or "").strip()
        if not p.get("tem_musica") or clima not in CLIMAS or not descricao or por_clima.get(clima, 0) >= MAX_PESQUISADOS_POR_CLIMA:
            continue
        tom = (p.get("tom") or "").strip()
        estilos.append(Estilo(
            id=f"{clima}-{video_id}", clima=clima, descricao=descricao,
            bpm=min(160, max(60, int(p.get("bpm") or 100))),
            tom=tom if _TOM.match(tom) else "C major", origem=f"pesquisa:{video_id}"))
        por_clima[clima] = por_clima.get(clima, 0) + 1
    return estilos


# ---------- geração ----------

class GeradorAceStep:
    """Chama o ACE-Step no ambiente dele (Python 3.12); o modelo é carregado uma vez por leva."""

    def __call__(self, faixas: list[dict], pasta: Path, log=print) -> dict[str, Path]:
        if not PYTHON_ACESTEP.exists():
            raise SystemExit('O ACE-Step não está instalado. Veja "Música por IA" no README.')
        pasta.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            pedido, resultado = Path(tmp) / "pedido.json", Path(tmp) / "resultado.json"
            pedido.write_text(json.dumps({
                "raiz_acestep": str(PASTA_ACESTEP), "modelo": MODELO_MUSICA, "pasta_saida": str(pasta),
                "resultado": str(resultado), "faixas": faixas}, ensure_ascii=False), encoding="utf-8")
            with recursos.gpu():
                execucao = subprocess.run(
                    [str(PYTHON_ACESTEP), str(WORKER), str(pedido)], capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    env={**os.environ, "PYTHONUTF8": "1", "ACESTEP_DISABLE_TQDM": "1"})
            if execucao.returncode != 0 or not resultado.exists():
                raise RuntimeError(f"ACE-Step falhou:\n{(execucao.stderr or execucao.stdout).strip()[-2500:]}")
            saida = json.loads(resultado.read_text(encoding="utf-8"))
        for r in saida:
            if "erro" in r:
                log(f"    {r['nome']}: {r['erro']}")
        return {r["nome"]: Path(r["arquivo"]) for r in saida if "arquivo" in r}


# ---------- avaliação ----------

@dataclass
class Medida:
    duracao_s: float
    lufs: float | None                      # volume percebido (integrado)
    pico_db: float | None
    silencios: list[tuple[float, float]] = field(default_factory=list)  # (início, duração)


def medir(arquivo: Path) -> Medida:
    saida = subprocess.run(
        [ffmpeg(), "-hide_banner", "-nostats", "-i", str(arquivo), "-af",
         "ebur128=framelog=quiet,volumedetect,silencedetect=n=-45dB:d=1", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stderr

    def numero(padrao: str) -> float | None:
        achado = re.findall(padrao, saida)
        return float(achado[-1]) if achado else None

    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", saida).groups()
    inicios = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", saida)]
    duracoes = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", saida)]
    return Medida(int(h) * 3600 + int(m) * 60 + float(s), numero(r"I:\s+(-?[\d.]+) LUFS"),
                  numero(r"max_volume: (-?[\d.]+) dB"), list(zip(inicios, duracoes)))


def problemas_objetivos(medida: Medida, pedida: float) -> list[str]:
    problemas = []
    if abs(medida.duracao_s - pedida) > 3:
        problemas.append(f"duração {medida.duracao_s:.0f} s em vez de {pedida:.0f} s")
    if medida.lufs is None or medida.lufs < -35:
        problemas.append("quase sem som")
    elif medida.lufs > -5:
        problemas.append("alta demais, provavelmente distorcida")
    for inicio, duracao in medida.silencios:
        if duracao >= SILENCIO_MAXIMO_S and inicio > 2 and inicio + duracao < medida.duracao_s - 3:
            problemas.append(f"buraco de {duracao:.1f} s de silêncio aos {inicio:.0f} s")
    return problemas


ESQUEMA_ESCUTA = {
    "type": "object",
    "properties": {
        "nota_para_criancas": {"type": "integer"}, "nota_qualidade": {"type": "integer"},
        "combina_com_o_clima": {"type": "boolean"}, "tem_voz_cantada": {"type": "boolean"},
        "lembra_musica_conhecida": _TEXTO, "comentario": _TEXTO,
    },
    "required": ["nota_para_criancas", "nota_qualidade", "combina_com_o_clima", "tem_voz_cantada",
                 "lembra_musica_conhecida", "comentario"],
}


def ouvir(arquivo: Path, estilo: Estilo, publico: str, cliente, log=print) -> dict:
    """O Gemini ouve a faixa e dá notas; vai em MP3 mono para gastar pouca cota."""
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = Path(tmp) / "faixa.mp3"
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(arquivo), "-ac", "1", "-b:a", "64k", str(mp3)],
                       check=True)
        dados = base64.b64encode(mp3.read_bytes()).decode()
    prompt = (
        f"Você avalia trilhas instrumentais de fundo para um canal infantil (público: {publico}). Ouça a "
        f'faixa. Clima desejado: "{estilo.clima}". Estilo pedido: {estilo.descricao}\n\n'
        '"nota_para_criancas" de 0 a 10: agradável, não irritante, não assustadora, adequada para crianças '
        'pequenas ouvirem por baixo de uma narração. "nota_qualidade" de 0 a 10: produção limpa, sem ruído, '
        'chiado, falhas, cortes, desafinação ou instrumentos estranhos. "tem_voz_cantada": se há voz cantando '
        'ou falando. "lembra_musica_conhecida": vazio, ou o nome da música conhecida cuja melodia esta faixa '
        'claramente reproduz. "comentario": uma frase.'
    )
    interacao = gemini.criar(
        cliente, log=log, model=os.getenv("GEMINI_MODELO", gemini.MODELO_PADRAO),
        input=[{"type": "text", "text": prompt}, {"type": "audio", "mime_type": "audio/mp3", "data": dados}],
        response_format={"type": "text", "mime_type": "application/json", "schema": ESQUEMA_ESCUTA})
    return gemini.extrair_json(interacao.output_text)


ROTULOS_CLIMA = {"alegre": "cheerful upbeat children's music", "calma": "calm gentle lullaby music",
                 "aventura": "playful adventurous orchestral music"}
ROTULOS_NATUREZA = ["instrumental music for children", "a person singing", "scary dark music",
                    "noise or distorted audio", "silence"]


NADA_LEMBRADO = {"", "-", "original", "nenhuma", "nenhum", "none", "no", "não", "nao", "n/a", "na", "null"}


def musica_lembrada(escuta: dict) -> str:
    """O nome da música conhecida que a faixa reproduz, ou vazio. O ouvinte às vezes preenche o
    campo com "original" ou "nenhuma" em vez de deixá-lo vazio."""
    valor = str(escuta.get("lembra_musica_conhecida") or "").strip()
    return "" if valor.lower().strip(" .!") in NADA_LEMBRADO else valor


def ouvir_local(arquivo: Path, estilo: Estilo, ouvido=None) -> dict:
    """Reserva para quando a cota do Gemini acaba: o "ouvido" local (CLAP) dá as mesmas notas, de forma
    mais simples. Ele não sabe dizer se a faixa lembra uma música conhecida."""
    from .ouvido import Ouvido, carregar_audio

    ouvido = ouvido or Ouvido.obter()
    audio = carregar_audio(arquivo, maximo_s=30, inicio_s=5)
    clima = ouvido.probabilidades(audio, list(ROTULOS_CLIMA.values()))
    natureza = ouvido.probabilidades(audio, ROTULOS_NATUREZA)
    voz, medo = natureza["a person singing"], natureza["scary dark music"]
    ruim = natureza["noise or distorted audio"] + natureza["silence"]
    return {
        "nota_para_criancas": max(0, round(10 * (1 - medo - ruim / 2))),
        "nota_qualidade": max(0, round(10 * (1 - ruim))),
        "combina_com_o_clima": max(clima, key=clima.get) == ROTULOS_CLIMA[estilo.clima],
        "tem_voz_cantada": voz > 0.35,
        "lembra_musica_conhecida": "",
        "comentario": f"avaliada pelo ouvido local: instrumental {natureza['instrumental music for children']:.0%}, "
                      f"voz {voz:.0%}, assustadora {medo:.0%}, ruído {ruim:.0%}",
    }


def avaliar(arquivo: Path, pedida: float, estilo: Estilo, publico: str, cliente, log=print) -> dict:
    medida = medir(arquivo)
    reprovado = problemas_objetivos(medida, pedida)
    escuta = None
    if not reprovado:  # só gasta cota do Gemini com o que passou nas medições
        try:
            escuta = ouvir(arquivo, estilo, publico, cliente, log)
        except gemini.CotaGeminiEsgotada:
            escuta = ouvir_local(arquivo, estilo)
        if escuta.get("tem_voz_cantada"):
            reprovado.append("tem voz")
        if lembra := musica_lembrada(escuta):
            reprovado.append(f"lembra uma música conhecida ({lembra})")
        for nota, rotulo in (("nota_para_criancas", "para crianças"), ("nota_qualidade", "de qualidade")):
            if int(escuta.get(nota, 0)) < NOTA_MINIMA:
                reprovado.append(f"nota {rotulo} {escuta.get(nota)}/10")
    nota = (int(escuta["nota_para_criancas"]) + int(escuta["nota_qualidade"]) + int(bool(escuta["combina_com_o_clima"]))
            if escuta else 0)
    return {"medida": asdict(medida), "escuta": escuta, "reprovado": reprovado, "nota": nota}


# ---------- trilha do episódio ----------

def duracao_pedida(duracao_video: float) -> int:
    """Arredonda para cima em blocos de 30 s: uma narração um pouco mais longa não obriga a recompor."""
    return int(min(600, max(30, math.ceil((duracao_video + 3) / 30) * 30)))


def _semente(nome: str, base: int, rodada: int, k: int) -> int:
    return int(hashlib.sha1(f"{nome}:{base}:{rodada}:{k}".encode()).hexdigest()[:8], 16)


def estilo_do_episodio(ep: Episodio, roteiro: dict, estilos: list[Estilo], episodios: list[Episodio]) -> Estilo:
    """Mantém o estilo já usado (a trilha não muda só porque as métricas mudaram); para um episódio
    novo, escolhe pelo desempenho medido, testando de vez em quando os estilos menos usados."""
    clima = roteiro.get("clima_musica") if roteiro.get("clima_musica") in CLIMAS else "alegre"
    do_clima = [e for e in estilos if e.clima == clima]
    anterior = (ep.ler(ep.trilha_info) or {}).get("estilo", {}).get("id")
    for e in do_clima:
        if e.id == anterior:
            return e
    escolhido = metricas.escolher([e.id for e in do_clima], metricas.desempenho_por_estilo(episodios), ep.nome)
    return next(e for e in do_clima if e.id == escolhido)


def gerar_trilha(ep: Episodio, roteiro: dict, canal: Canal, duracao_video: float, episodios: list[Episodio],
                 gerador=None, cliente=None, log=print, estilos: list[Estilo] | None = None) -> Path:
    estilo = estilo_do_episodio(ep, roteiro, estilos or carregar_estilos(), episodios)
    pedida = duracao_pedida(duracao_video)

    def avaliar_uma(arquivo: Path, cliente_) -> dict:
        return avaliar(arquivo, pedida, estilo, canal.publico, cliente_, log)

    return _compor(ep, roteiro, canal, estilo, pedida, {}, avaliar_uma, gerador, cliente, log)


def _compor(ep: Episodio, roteiro: dict, canal: Canal, estilo: Estilo, pedida: float, extra: dict,
            avaliar_uma, gerador, cliente, log) -> Path:
    """Compõe algumas versões, avalia cada uma e guarda a melhor em ep.trilha (refaz só se o pedido mudar)."""
    base = int(roteiro.get("semente_musica", 0))  # mude no roteiro.json para compor outra
    assinatura_ = (assinatura(asdict(estilo), pedida, MODELO_MUSICA, base, canal.musica.candidatos, extra) if extra
                   else assinatura(asdict(estilo), pedida, MODELO_MUSICA, base, canal.musica.candidatos))
    manifesto = Manifesto(ep.pasta_trilha)
    if manifesto.atual(ep.trilha.name, assinatura_):
        return ep.trilha

    gerador = gerador or GeradorAceStep()
    avaliacoes: list[dict] = []
    aprovadas: list[dict] = []
    for rodada in range(RODADAS):
        faixas = [{"nome": f"versao_{rodada + 1}_{k + 1}", "descricao": estilo.descricao, "bpm": estilo.bpm,
                   "tom": estilo.tom, "duracao": pedida, "semente": _semente(ep.nome, base, rodada, k), **extra}
                  for k in range(canal.musica.candidatos)]
        log(f"  compondo {len(faixas)} versões no estilo {estilo.id} ({pedida:g} s)...")
        arquivos = gerador(faixas, ep.pasta_trilha, log)
        for faixa in faixas:
            arquivo = arquivos.get(faixa["nome"])
            if arquivo is None:
                avaliacao = {"reprovado": ["a geração falhou"], "nota": 0}
            else:
                cliente = cliente or gemini.cliente()
                avaliacao = avaliar_uma(arquivo, cliente)
            avaliacao.update(nome=faixa["nome"], semente=faixa["semente"])
            avaliacoes.append(avaliacao)
            situacao = "reprovada: " + "; ".join(avaliacao["reprovado"]) if avaliacao["reprovado"] else f"nota {avaliacao['nota']}/21"
            log(f"    {faixa['nome']}: {situacao}")
        aprovadas = [a for a in avaliacoes if not a["reprovado"]]
        if aprovadas:
            break
    if not aprovadas:
        motivos = "\n  ".join(f"{a['nome']}: {'; '.join(a['reprovado'])}" for a in avaliacoes)
        raise SystemExit(f"Nenhuma versão da música passou na avaliação:\n  {motivos}\nMude \"semente_musica\" no "
                         "roteiro.json (ou o clima_musica) e rode de novo.")

    melhor = max(aprovadas, key=lambda a: a["nota"])
    shutil.copyfile(ep.pasta_trilha / f"{melhor['nome']}.wav", ep.trilha)
    ep.gravar(ep.trilha_info, {
        "estilo": asdict(estilo), "duracao_s": pedida, "modelo": MODELO_MUSICA, "licenca": LICENCA,
        **({"letra": extra["letra"]} if extra.get("letra") else {}),
        "escolhida": melhor["nome"], "aprovada": True, "avaliacoes": avaliacoes,
        "gerada_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    manifesto.registrar(ep.trilha.name, assinatura_)
    return ep.trilha


# ---------- canção (Shorts no formato música) ----------

ESTILOS_CANCAO = [
    Estilo("cancao-ukulele", "alegre", "Cheerful children's sing-along song, clear bright friendly female vocal, "
           "simple catchy melody, ukulele, glockenspiel, soft hand claps, light percussion, playful and warm",
           112, "C major"),
    Estilo("cancao-pop", "alegre", "Upbeat kids pop sing-along song, clear cheerful female vocal, acoustic guitar, "
           "xylophone, light drums and bass, bouncy chorus that repeats", 118, "G major"),
]

ESQUEMA_ESCUTA_CANCAO = {
    "type": "object",
    "properties": {
        "nota_para_criancas": {"type": "integer"}, "nota_qualidade": {"type": "integer"},
        "voz_clara": {"type": "boolean"}, "segue_a_letra": {"type": "boolean"},
        "lembra_musica_conhecida": _TEXTO, "comentario": _TEXTO,
    },
    "required": ["nota_para_criancas", "nota_qualidade", "voz_clara", "segue_a_letra",
                 "lembra_musica_conhecida", "comentario"],
}


def ouvir_cancao(arquivo: Path, letra: str, publico: str, cliente, log=print) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = Path(tmp) / "faixa.mp3"
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(arquivo), "-ac", "1", "-b:a", "64k", str(mp3)],
                       check=True)
        dados = base64.b64encode(mp3.read_bytes()).decode()
    prompt = (
        f"Você avalia canções curtas cantadas para um canal infantil (público: {publico}). Ouça a faixa. A letra "
        f"pedida foi:\n{letra}\n\n"
        '"nota_para_criancas" de 0 a 10: alegre, gostosa de cantar junto, nada assustador ou irritante. '
        '"nota_qualidade" de 0 a 10: voz afinada e natural, produção limpa, sem ruído, chiado, falhas ou palavras '
        'emboladas. "voz_clara": há voz cantando e dá para entender as palavras. "segue_a_letra": o que se canta '
        'corresponde, no essencial, à letra pedida. "lembra_musica_conhecida": vazio, ou o nome da música conhecida '
        'cuja melodia esta faixa claramente reproduz. "comentario": uma frase.'
    )
    interacao = gemini.criar(
        cliente, log=log, model=os.getenv("GEMINI_MODELO", gemini.MODELO_PADRAO),
        input=[{"type": "text", "text": prompt}, {"type": "audio", "mime_type": "audio/mp3", "data": dados}],
        response_format={"type": "text", "mime_type": "application/json", "schema": ESQUEMA_ESCUTA_CANCAO})
    return gemini.extrair_json(interacao.output_text)


def ouvir_cancao_local(arquivo: Path, ouvido=None) -> dict:
    """Reserva sem Gemini: o ouvido local confere que há canto e nada assustador ou ruidoso. Ele não
    entende a letra, então "segue_a_letra" fica como verdadeiro."""
    from .ouvido import Ouvido, carregar_audio

    ouvido = ouvido or Ouvido.obter()
    natureza = ouvido.probabilidades(carregar_audio(arquivo, maximo_s=30, inicio_s=2), ROTULOS_NATUREZA)
    voz, medo = natureza["a person singing"], natureza["scary dark music"]
    ruim = natureza["noise or distorted audio"] + natureza["silence"]
    return {
        "nota_para_criancas": max(0, round(10 * (1 - medo - ruim / 2))),
        "nota_qualidade": max(0, round(10 * (1 - ruim))),
        "voz_clara": voz > 0.35, "segue_a_letra": True, "lembra_musica_conhecida": "",
        "comentario": f"avaliada pelo ouvido local: voz {voz:.0%}, assustadora {medo:.0%}, ruído {ruim:.0%}",
    }


def avaliar_cancao(arquivo: Path, pedida: float, letra: str, publico: str, cliente, log=print) -> dict:
    medida = medir(arquivo)
    reprovado = problemas_objetivos(medida, pedida)
    escuta = None
    if not reprovado:
        try:
            escuta = ouvir_cancao(arquivo, letra, publico, cliente, log)
        except gemini.CotaGeminiEsgotada:
            escuta = ouvir_cancao_local(arquivo)
        if not escuta.get("voz_clara"):
            reprovado.append("sem voz clara")
        if not escuta.get("segue_a_letra"):
            reprovado.append("não canta a letra pedida")
        if lembra := musica_lembrada(escuta):
            reprovado.append(f"lembra uma música conhecida ({lembra})")
        for nota, rotulo in (("nota_para_criancas", "para crianças"), ("nota_qualidade", "de qualidade")):
            if int(escuta.get(nota, 0)) < NOTA_MINIMA:
                reprovado.append(f"nota {rotulo} {escuta.get(nota)}/10")
    nota = (int(escuta["nota_para_criancas"]) + int(escuta["nota_qualidade"]) + int(bool(escuta["segue_a_letra"]))
            if escuta else 0)
    return {"medida": asdict(medida), "escuta": escuta, "reprovado": reprovado, "nota": nota}


def gerar_cancao(ep: Episodio, roteiro: dict, canal: Canal, duracao: float, outros: list[Episodio],
                 gerador=None, cliente=None, log=print) -> Path:
    """Canção original com a letra do roteiro (formato música dos Shorts). O estilo é mantido se já
    existe; num Short novo, é escolhido pelo desempenho dos Shorts de música já publicados."""
    anterior = (ep.ler(ep.trilha_info) or {}).get("estilo", {}).get("id")
    ids = [e.id for e in ESTILOS_CANCAO]
    escolhido = anterior if anterior in ids else metricas.escolher(ids, metricas.desempenho_por_estilo(outros), ep.nome)
    estilo = next(e for e in ESTILOS_CANCAO if e.id == escolhido)
    letra, pedida = roteiro["letra"], round(duracao)

    def avaliar_uma(arquivo: Path, cliente_) -> dict:
        return avaliar_cancao(arquivo, pedida, letra, canal.publico, cliente_, log)

    idioma_voz = canal.narracao[roteiro["idioma"]].idioma.split("-")[0]
    return _compor(ep, roteiro, canal, estilo, pedida, {"letra": letra, "idioma_voz": idioma_voz},
                   avaliar_uma, gerador, cliente, log)
