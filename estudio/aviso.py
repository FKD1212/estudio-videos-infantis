"""Avisos na área de notificação do Windows: é como a produção automática chama você quando
precisa (reconectar o canal, um episódio bloqueado pela revisão, um erro que se repete)."""
from __future__ import annotations

import os
import subprocess
import sys


def _ps(texto: str) -> str:
    return texto.replace("'", "''").replace("\n", " ")[:240]


def notificar(titulo: str, texto: str) -> None:
    if sys.platform != "win32" or os.getenv("SEM_AVISOS") == "1":
        return
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
        f"$n.Visible = $true; $n.ShowBalloonTip(15000, '{_ps(titulo)}', '{_ps(texto)}', 'Info'); "
        "Start-Sleep -Seconds 15; $n.Dispose()"
    )
    # Não espera: o aviso fica na tela enquanto o programa segue.
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
