"""Dispara um toast nativo do Windows ao final do briefing.

Lê os JSONs já gerados em dados/ e monta um resumo curto, por exemplo:
  "Briefing pronto: 52 atendimentos ontem, fila 319, 30 licenças em risco"

Usa PowerShell (WinRT ToastNotification) via subprocess: sem dependência nova.
Qualquer falha é reportada no stdout e NÃO derruba o briefing.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

RAIZ = Path(__file__).resolve().parent
DADOS = RAIZ / "dados"
TITULO = "Briefing diário"

# AppId de um app já registrado no Windows (o próprio PowerShell), exigido pelo
# ToastNotificationManager para exibir a notificação sem instalar nada.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def ler_json(nome: str) -> dict:
    caminho = DADOS / nome
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def montar_resumo() -> str:
    helpdesk = ler_json("helpdesk.json")
    licencas = ler_json("licencas.json")
    atend = helpdesk.get("atendimentos_ultimo_dia_util") or {}
    if not isinstance(atend, dict):
        atend = {}

    partes = []
    total = atend.get("total_atendimentos_fechados")
    partes.append(f"{total} atendimentos ontem" if total is not None else "atendimentos: sem dados")

    fila = helpdesk.get("fila_total_abertos")
    partes.append(f"fila {fila}" if fila is not None else "fila: sem dados")

    if licencas:
        risco = len(licencas.get("vencendo_em_breve") or []) + len(licencas.get("vencidas_recentes") or [])
        partes.append(f"{risco} licenças em risco")
    else:
        partes.append("licenças: sem dados")

    return "Briefing pronto: " + ", ".join(partes)


def enviar_toast(titulo: str, mensagem: str) -> None:
    script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = @"
<toast><visual><binding template="ToastGeneric"><text>{xml_escape(titulo)}</text><text>{xml_escape(mensagem)}</text></binding></visual></toast>
"@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($toast)
"""
    # -EncodedCommand evita problemas de aspas e acentuação na linha de comando.
    codificado = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    resultado = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-EncodedCommand", codificado],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if resultado.returncode != 0:
        erro = (resultado.stderr or resultado.stdout or "").strip().splitlines()
        raise RuntimeError(erro[0] if erro else f"powershell saiu com codigo {resultado.returncode}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    try:
        resumo = montar_resumo()
    except Exception as e:  # nunca derrubar o briefing
        resumo = "Briefing pronto"
        print(f"AVISO: nao foi possivel montar o resumo ({type(e).__name__}: {e})")
    try:
        enviar_toast(TITULO, resumo)
        print(f"Notificacao enviada: {resumo}")
        return 0
    except Exception as e:
        print(f"AVISO: notificacao falhou ({type(e).__name__}: {e}). Briefing segue normalmente.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
