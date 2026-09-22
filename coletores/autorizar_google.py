"""Autorização única do Google Calendar (OAuth 2.0, fluxo de app instalado).

Roda UMA vez, na sua máquina, com navegador. Abre a tela de consentimento do
Google, recebe o código de volta num servidor local (stdlib), troca por um
refresh token e imprime para você colar no .env. Depois disso o check_agenda.py
roda sozinho, headless, para sempre -- refresh token não expira por tempo.

Pré-requisito: no Google Cloud Console, com a Google Calendar API ativada, criar
uma credencial "ID do cliente OAuth" do tipo **App para computador**. Ela dá o
client_id e o client_secret. O tipo "App para computador" é o que permite o
redirecionamento para localhost em porta qualquer, que é o que este script usa.

Escopo pedido: calendar.readonly -- somente leitura, nada é criado ou alterado.

Uso:  python coletores/autorizar_google.py
"""

import http.server
import json
import os
import secrets
import socket
import sys
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
ESCOPO = "https://www.googleapis.com/auth/calendar.readonly"

PAGINA_OK = """<!doctype html><meta charset="utf-8">
<title>Pronto</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem">
<h1>Autorizacao concluida</h1>
<p>Pode fechar esta aba e voltar para o terminal.</p></body>"""

PAGINA_ERRO = """<!doctype html><meta charset="utf-8">
<title>Falhou</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem">
<h1>Autorizacao nao concluida</h1>
<p>Volte ao terminal para ver o motivo.</p></body>"""


class Captura(http.server.BaseHTTPRequestHandler):
    """Recebe o redirecionamento do Google e guarda o código na classe."""

    codigo: str | None = None
    estado: str | None = None
    erro: str | None = None

    def do_GET(self) -> None:  # noqa: N802 (nome exigido por BaseHTTPRequestHandler)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        Captura.codigo = (query.get("code") or [None])[0]
        Captura.estado = (query.get("state") or [None])[0]
        Captura.erro = (query.get("error") or [None])[0]
        ok = bool(Captura.codigo) and not Captura.erro
        corpo = (PAGINA_OK if ok else PAGINA_ERRO).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *_args) -> None:
        """Silencia o log do servidor -- a URL traz o código de autorização."""


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit(
            "Faltam GOOGLE_CLIENT_ID e/ou GOOGLE_CLIENT_SECRET no .env.\n"
            "Crie uma credencial OAuth do tipo 'App para computador' no Google\n"
            "Cloud Console (com a Google Calendar API ativada) e copie os dois\n"
            "valores para o .env. Veja o .env.exemplo."
        )

    porta = porta_livre()
    redirect_uri = f"http://127.0.0.1:{porta}"
    estado = secrets.token_urlsafe(24)   # protege contra resposta forjada

    url = AUTORIZAR + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ESCOPO,
        "access_type": "offline",   # é o que faz o Google devolver refresh token
        "prompt": "consent",        # força vir refresh token mesmo se já autorizado
        "state": estado,
    })

    print("Abrindo o navegador para você autorizar o acesso de LEITURA à agenda.")
    print("Se nada abrir, cole esta URL no navegador:\n")
    print(url + "\n")
    webbrowser.open(url)

    servidor = http.server.HTTPServer(("127.0.0.1", porta), Captura)
    servidor.timeout = 300
    print(f"Aguardando o retorno do Google em {redirect_uri} (5 min)...")
    servidor.handle_request()
    servidor.server_close()

    if Captura.erro:
        raise SystemExit(f"O Google recusou: {Captura.erro}")
    if not Captura.codigo:
        raise SystemExit("Nenhum código recebido (tempo esgotado ou aba fechada).")
    if Captura.estado != estado:
        raise SystemExit("Estado divergente: resposta não confere com o pedido.")

    resp = requests.post(TOKEN, timeout=30, data={
        "code": Captura.codigo,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    dados = resp.json() if resp.content else {}
    if resp.status_code != 200 or "refresh_token" not in dados:
        motivo = dados.get("error_description") or dados.get("error") or resp.status_code
        raise SystemExit(
            f"Troca do código pelo token falhou: {motivo}\n"
            "Se o erro for 'invalid_grant', rode de novo: o código só vale uma vez."
        )

    print("\n" + "=" * 68)
    print("Cole a linha abaixo no seu .env (junto de GOOGLE_CLIENT_ID/SECRET):\n")
    print(f"GOOGLE_REFRESH_TOKEN={dados['refresh_token']}")
    print("=" * 68)
    print("\nEsse token é um segredo: vale como acesso de leitura à sua agenda.")
    print("Depois de colar, teste com:  python coletores/check_agenda.py")


if __name__ == "__main__":
    main()
