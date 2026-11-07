"""Avisos na área de notificação do Windows: é como a produção automática chama você quando
precisa (reconectar o canal, um episódio bloqueado pela revisão, um erro que se repete)."""
from __future__ import annotations

import os
import subprocess
import sys


def _limpo(texto: str) -> str:
    return " ".join(str(texto).split())[:240]


# Título e texto entram por variáveis de ambiente, nunca colados no script: assim nenhum caractere
# deles (aspas retas ou curvas, $, crase) é lido como código pelo PowerShell.
SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
    "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
    "$n.Visible = $true; $n.ShowBalloonTip(15000, $env:AVISO_TITULO, $env:AVISO_TEXTO, 'Info'); "
    "Start-Sleep -Seconds 15; $n.Dispose()"
)


def notificar(titulo: str, texto: str) -> None:
    if sys.platform != "win32" or os.getenv("SEM_AVISOS") == "1":
        return
    # Não espera: o aviso fica na tela enquanto o programa segue.
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", SCRIPT],
                     env={**os.environ, "AVISO_TITULO": _limpo(titulo), "AVISO_TEXTO": _limpo(texto)},
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
