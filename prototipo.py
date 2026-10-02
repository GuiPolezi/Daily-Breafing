"""Serve o protótipo do site novo no próprio computador, para conferir o visual.

Só para desenvolvimento: em produção quem entrega os arquivos é o IIS. Escuta
apenas em 127.0.0.1 (ninguém da rede alcança) e entrega uma lista fechada de
arquivos, direto de onde eles já estão -- nada é copiado:

    /                    índice com os quatro públicos (só existe aqui)
    /<publico>.html      site/
    /*.css, /app.js      site/
    /assets/...          assets/chart.min.js e assets/fonts/*.woff2
    /dados/<publico>.json   dados/site/   (gerados por exportar.py)

Não consulta nenhuma API. Para atualizar os dados: `python exportar.py` (a
página aberta busca o arquivo de novo sozinha, em até 60 s).

Uso:
    python prototipo.py            # http://127.0.0.1:8765
    python prototipo.py 9000       # outra porta
"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

RAIZ = Path(__file__).resolve().parent
SITE = RAIZ / "site"
ASSETS = RAIZ / "assets"
DADOS = RAIZ / "dados" / "site"
PUBLICOS = ("suporte", "desenvolvimento", "diretor", "licencas")
TIPOS = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".woff2": "font/woff2"}


def rotas() -> dict[str, Path]:
    """Caminho da URL -> arquivo. O que não está aqui não é entregue."""
    mapa = {"/assets/chart.min.js": ASSETS / "chart.min.js"}
    for arquivo in SITE.iterdir():
        if arquivo.suffix in (".html", ".css", ".js"):
            mapa["/" + arquivo.name] = arquivo
    for fonte in (ASSETS / "fonts").glob("*.woff2"):
        mapa["/assets/fonts/" + fonte.name] = fonte
    for publico in PUBLICOS:
        mapa[f"/dados/{publico}.json"] = DADOS / f"{publico}.json"
    return mapa


def indice() -> bytes:
    itens = "".join(f'<li><a href="{p}.html">{p}</a></li>' for p in PUBLICOS)
    return ("<!doctype html><html lang='pt-BR'><meta charset='utf-8'><title>Protótipo</title>"
            "<link rel='stylesheet' href='tema-sino.css'><link rel='stylesheet' href='base.css'>"
            "<main><section class='faixa'><h1 class='faixa-titulo'>Protótipo do site novo</h1>"
            "<p class='faixa-sub'>Uma página por público. Este índice só existe no protótipo.</p>"
            f"<ul>{itens}</ul></section></main></html>").encode("utf-8")


class Pedido(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        caminho = urlsplit(self.path).path
        if caminho in ("/", "/index.html"):
            return self.responder(200, indice(), TIPOS[".html"])
        arquivo = rotas().get(caminho)
        try:
            corpo = arquivo.read_bytes() if arquivo else None
        except OSError:
            corpo = None
        if corpo is None:
            return self.responder(404, "não encontrado".encode("utf-8"), "text/plain; charset=utf-8")
        self.responder(200, corpo, TIPOS[arquivo.suffix])

    def responder(self, codigo: int, corpo: bytes, tipo: str) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, formato: str, *args) -> None:   # silencioso: só erros interessam
        if args and str(args[1])[:1] in ("4", "5"):
            sys.stderr.write("%s %s\n" % (args[1], args[0]))


def main() -> None:
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    faltam = [p for p in PUBLICOS if not (DADOS / f"{p}.json").exists()]
    if faltam:
        print(f"AVISO: sem dados de {', '.join(faltam)}; rode `python exportar.py` antes.")
    servidor = HTTPServer(("127.0.0.1", porta), Pedido)
    print(f"Protótipo em http://127.0.0.1:{porta}/  (Ctrl+C para parar)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
