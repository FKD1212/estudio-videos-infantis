"""Acesso ao Gemini na cota gratuita: cliente, limites de uso e leitura de JSON.

O plano gratuito tem limite DIÁRIO por modelo (em out/2026, 20 pedidos/dia no gemini-3.8-flash).
Quando o do modelo principal acaba, o pedido passa para o próximo modelo gratuito, que tem cota
própria. Quando todos acabam, pedidos de texto (com ou sem imagens) vão para um modelo local no
Ollama — mais lento, mas sem limite. Pedidos com vídeo ou áudio não têm equivalente local: o
programa para e avisa, e o que ficou pronto não é refeito."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import chave

# Modelos de texto gratuitos, do melhor para o mais simples; cada um tem a sua cota diária.
MODELOS_TEXTO = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
MODELO_PADRAO = MODELOS_TEXTO[0]
# Prazo de um pedido: alguns vídeos do YouTube deixam o pedido pendurado, e o tempo limite da
# própria biblioteca não segura isso.
PRAZO_S = 300

# Reserva local (Ollama). gemma4:26b lê texto e imagens e cabe na RTX 5070 dividindo com a RAM.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODELO_LOCAL = os.getenv("MODELO_LOCAL", "gemma4:26b")
PRAZO_LOCAL_S = 1800  # um roteiro inteiro no modelo local leva alguns minutos

_esgotados: set[str] = set()  # modelos cuja cota diária acabou nesta execução


class CotaGeminiEsgotada(RuntimeError):
    pass


class GeminiNaoRespondeu(TimeoutError):
    pass


def cliente():
    from google import genai

    # A biblioteca repetiria sozinha os erros de cota obedecendo "tente de novo em 23 h" (parece
    # travado), e cada repetição gasta cota. Aqui ela só repete erro do servidor.
    return genai.Client(api_key=chave("GEMINI_API_KEY"),
                        http_options={"retry_options": {"attempts": 3, "http_status_codes": [500, 502, 503, 504]}})


def _status(erro: Exception) -> int | None:
    return getattr(erro, "status_code", None) or getattr(erro, "code", None)


def _texto_do_erro(erro: Exception) -> str:
    return f"{getattr(erro, 'message', '')} {getattr(erro, 'body', '')} {erro}"


def _limite_por_minuto(erro: Exception) -> bool:
    """Só o limite por minuto vale esperar; qualquer outro 429 (por dia, ou "cota excedida" sem dizer
    qual) faz passar ao próximo modelo."""
    return bool(re.search(r"per ?minute|perminute|per-minute", _texto_do_erro(erro), re.I))


def _tem_video(pedido: dict) -> bool:
    entrada = pedido.get("input")
    return isinstance(entrada, list) and any(isinstance(i, dict) and i.get("type") == "video" for i in entrada)


def _quando_volta(erro: Exception) -> str:
    achado = re.search(r"retry in ([\dhms.]+)", _texto_do_erro(erro))
    return f" (volta em {achado.group(1)})" if achado else ""


def cadeia(modelo: str) -> list[str]:
    """O modelo pedido e, se for o padrão (e GEMINI_MODELO não fixar outro), os reservas."""
    if modelo == MODELO_PADRAO and not os.getenv("GEMINI_MODELO"):
        return list(MODELOS_TEXTO)
    return [modelo]


def _com_prazo(funcao, prazo_s: float):
    """Roda `funcao` numa thread à parte e desiste depois do prazo (a thread abandonada morre
    com o programa)."""
    resultado: dict = {}

    def rodar():
        try:
            resultado["valor"] = funcao()
        except BaseException as erro:  # devolvido para quem chamou
            resultado["erro"] = erro

    linha = threading.Thread(target=rodar, daemon=True)
    linha.start()
    linha.join(prazo_s)
    if linha.is_alive():
        raise GeminiNaoRespondeu(f"o Gemini não respondeu em {prazo_s:.0f} s")
    if "erro" in resultado:
        raise resultado["erro"]
    return resultado["valor"]


def criar(cliente, log=print, tentativas: int = 3, espera_s: float = 60, prazo_s: float = PRAZO_S, **pedido):
    """interactions.create respeitando a cota gratuita: espera quando é o limite por minuto e passa
    ao próximo modelo gratuito quando a cota dele acaba ou quando ele não responde no prazo. Sem
    nenhum modelo disponível, texto vai para o modelo local; o resto levanta CotaGeminiEsgotada (quem
    chama decide: usar a voz ou o ouvido local, por exemplo). Pedido com vídeo que não responde
    levanta GeminiNaoRespondeu na hora: costuma ser aquele vídeo, e quem chama só o pula."""
    pedido.setdefault("timeout", prazo_s)
    ultimo_erro = None
    todos = cadeia(pedido["model"])
    modelos = [m for m in todos if m not in _esgotados]
    for i, modelo in enumerate(modelos):
        pedido["model"] = modelo
        proximo = f"; usando o {modelos[i + 1]}" if i + 1 < len(modelos) else ""
        for tentativa in range(1, tentativas + 1):
            try:
                return _com_prazo(lambda: cliente.interactions.create(**pedido), prazo_s)
            except GeminiNaoRespondeu as erro:
                if _tem_video(pedido):
                    raise
                ultimo_erro = erro
                _esgotados.add(modelo)
                log(f"    o {modelo} não respondeu em {prazo_s:.0f} s{proximo}")
                break
            except Exception as erro:
                if _status(erro) != 429:
                    raise
                ultimo_erro = erro
                if _limite_por_minuto(erro) and tentativa < tentativas:
                    log(f"    limite por minuto do {modelo}; aguardando {espera_s:.0f}s...")
                    time.sleep(espera_s)
                    continue
                _esgotados.add(modelo)
                log(f"    cota do {modelo} esgotada{_quando_volta(erro)}{proximo}")
                break
    corpo_local = para_ollama(pedido) if len(todos) > 1 else None  # só a cadeia de texto tem reserva local
    if corpo_local is not None and os.getenv("SEM_MODELO_LOCAL") != "1":
        log(f"    nenhum modelo gratuito do Gemini disponível; usando o modelo local {MODELO_LOCAL} (mais lento)")
        return criar_local(corpo_local)
    raise CotaGeminiEsgotada(
        f"Gemini indisponível: cota gratuita esgotada ou sem resposta ({', '.join(todos)})"
        f"{_quando_volta(ultimo_erro) if ultimo_erro else ''}. Rode de novo depois — o que já ficou pronto está "
        "salvo e não será refeito. Os limites da sua conta aparecem em https://ai.dev/rate-limit"
    ) from ultimo_erro


# ---------- reserva local (Ollama) ----------

@dataclass
class RespostaLocal:
    """Imita o que o resto do código usa de uma resposta do Gemini."""
    output_text: str
    model: str


def para_ollama(pedido: dict) -> dict | None:
    """Converte um pedido do Gemini para o Ollama; None se ele tiver vídeo, áudio ou pedir voz."""
    formato = pedido.get("response_format") or {}
    if formato.get("type", "text") != "text":
        return None
    entrada = pedido.get("input", "")
    textos, imagens = ([entrada], []) if isinstance(entrada, str) else ([], [])
    if not isinstance(entrada, str):
        for item in entrada:
            if item.get("type") == "text":
                textos.append(item["text"])
            elif item.get("type") == "image" and item.get("data"):
                imagens.append(item["data"])
            else:
                return None
    mensagem = {"role": "user", "content": "\n\n".join(textos)}
    if imagens:
        mensagem["images"] = imagens
    corpo = {"model": MODELO_LOCAL, "messages": [mensagem], "stream": False,
             "keep_alive": 0,  # libera a placa logo depois: imagens e música também precisam dela
             "options": {"num_ctx": 16384}}
    if formato.get("schema"):
        corpo["format"] = formato["schema"]
    elif formato.get("mime_type") == "application/json":
        corpo["format"] = "json"
    return corpo


def _executavel_ollama() -> str | None:
    padrao = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return shutil.which("ollama") or (str(padrao) if padrao.exists() else None)


def garantir_ollama(sessao=None) -> None:
    """Liga o Ollama se ele estiver instalado e desligado, e confere se o modelo local está baixado."""
    import requests

    sessao = sessao or requests
    for tentativa in range(20):
        try:
            modelos = [m["name"] for m in sessao.get(f"{OLLAMA_URL}/api/tags", timeout=3).json().get("models", [])]
            break
        except Exception:
            executavel = _executavel_ollama()
            if tentativa == 0 and executavel:
                subprocess.Popen([executavel, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            elif not executavel:
                raise CotaGeminiEsgotada("Cota do Gemini esgotada e o Ollama não está instalado para a reserva local.")
            time.sleep(1)
    else:
        raise CotaGeminiEsgotada("Cota do Gemini esgotada e o Ollama não respondeu.")
    if MODELO_LOCAL not in modelos and f"{MODELO_LOCAL}:latest" not in modelos:
        raise CotaGeminiEsgotada(f"Cota do Gemini esgotada e o modelo local não está baixado. Rode: ollama pull {MODELO_LOCAL}")


def criar_local(corpo: dict, sessao=None) -> RespostaLocal:
    import requests

    sessao = sessao or requests
    garantir_ollama(sessao)
    from . import recursos

    with recursos.gpu():  # o modelo local ocupa a placa: espera a vez das imagens e da música
        resposta = sessao.post(f"{OLLAMA_URL}/api/chat", json=corpo, timeout=PRAZO_LOCAL_S)
    resposta.raise_for_status()
    return RespostaLocal(resposta.json()["message"]["content"], f"local:{corpo['model']}")


def modelo_usado(interacao, pedido_modelo: str) -> str:
    return getattr(interacao, "model", None) or pedido_modelo


def extrair_json(texto: str) -> dict:
    m = re.search(r"\{.*\}", texto or "", re.S)
    if not m:
        raise ValueError(f"resposta sem JSON: {texto[:200]!r}")
    return json.loads(m.group(0))
