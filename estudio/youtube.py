"""Cliente mínimo da YouTube Data API v3, com cache em disco e contagem de cota."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import requests

API = "https://www.googleapis.com/youtube/v3/"
# Unidades de cota por chamada. A cota padrão é 10.000 unidades/dia por projeto.
CUSTO = {"search": 100, "videos": 1, "channels": 1, "playlistItems": 1}


class CotaEsgotada(RuntimeError):
    pass


class NaoEncontrado(RuntimeError):
    pass


class YouTube:
    def __init__(self, api_key: str, pasta_cache: Path, validade_horas: float = 24):
        self.api_key = api_key
        self.pasta_cache = pasta_cache
        self.validade_s = validade_horas * 3600
        self.unidades_gastas = 0
        self.sessao = requests.Session()
        pasta_cache.mkdir(parents=True, exist_ok=True)

    def _get(self, recurso: str, **params) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        assinatura = json.dumps([recurso, params], sort_keys=True)
        arquivo = self.pasta_cache / (hashlib.sha1(assinatura.encode()).hexdigest() + ".json")
        if arquivo.exists() and time.time() - arquivo.stat().st_mtime < self.validade_s:
            return json.loads(arquivo.read_text(encoding="utf-8"))

        resp = self.sessao.get(API + recurso, params={**params, "key": self.api_key}, timeout=30)
        if resp.status_code == 403 and "quotaExceeded" in resp.text:
            raise CotaEsgotada(
                "Cota diária da YouTube API esgotada. Ela reinicia à meia-noite (horário do Pacífico); "
                "rode de novo depois — o que já foi coletado está em cache."
            )
        if resp.status_code == 404:
            raise NaoEncontrado(f"{recurso}: {params}")
        if not resp.ok:
            raise RuntimeError(f"YouTube API {recurso}: HTTP {resp.status_code} - {resp.text[:300]}")

        self.unidades_gastas += CUSTO[recurso]
        dados = resp.json()
        arquivo.write_text(json.dumps(dados), encoding="utf-8")
        return dados

    def buscar(self, termo: str, idioma: str, regiao: str, publicado_apos: str,
               duracao: str = "any", maximo: int = 50) -> list[str]:
        """IDs dos vídeos mais vistos para o termo, publicados após a data (RFC 3339)."""
        ids: list[str] = []
        token = None
        while len(ids) < maximo:
            dados = self._get(
                "search", part="id", q=termo, type="video", order="viewCount",
                relevanceLanguage=idioma, regionCode=regiao, publishedAfter=publicado_apos,
                videoDuration=duracao, maxResults=min(50, maximo - len(ids)), pageToken=token,
            )
            ids += [item["id"]["videoId"] for item in dados.get("items", [])]
            token = dados.get("nextPageToken")
            if not token:
                break
        return ids

    def videos(self, ids) -> list[dict]:
        return self._em_lotes("videos", ids, "snippet,statistics,contentDetails,status")

    def canais(self, ids) -> list[dict]:
        return self._em_lotes("channels", ids, "snippet,statistics,contentDetails")

    def uploads_recentes(self, playlist_id: str, maximo: int = 50) -> list[str]:
        try:
            dados = self._get("playlistItems", part="contentDetails",
                              playlistId=playlist_id, maxResults=min(50, maximo))
        except NaoEncontrado:
            return []
        return [item["contentDetails"]["videoId"] for item in dados.get("items", [])]

    def _em_lotes(self, recurso: str, ids, part: str) -> list[dict]:
        # Ordenado para que a mesma consulta caia sempre no mesmo arquivo de cache.
        ids = sorted(set(ids))
        itens: list[dict] = []
        for i in range(0, len(ids), 50):
            itens += self._get(recurso, part=part, id=",".join(ids[i:i + 50])).get("items", [])
        return itens
