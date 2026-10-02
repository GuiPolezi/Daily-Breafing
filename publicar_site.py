"""Publica o site novo na pasta que o IIS serve (SITE_DIR).

Copia uma lista FECHADA de arquivos, e nada além dela:

    site/*.html, *.css, *.js      -> <SITE_DIR>/
    assets/chart.min.js           -> <SITE_DIR>/assets/
    assets/fonts/*.woff2          -> <SITE_DIR>/assets/fonts/
    dados/site/<publico>.json     -> <SITE_DIR>/dados/

O banco (dados/briefing.db), o .env, o histórico e os JSONs crus dos coletores
NUNCA saem daqui. Não consulta nenhuma API.

Regras (as mesmas de publicar.py):
  - SITE_DIR vazio = publicação desligada (sai com 0).
  - Recusa destino que seja o projeto, esteja dentro dele ou o contenha.
  - Sobrescreve o CONTEÚDO do arquivo que já existe (shutil.copyfile), sem
    apagar e recriar: a permissão NTFS dada a cada arquivo de dados no servidor
    (quem vê o quê) sobrevive às publicações. Arquivo igual não é regravado.
  - Falha vira AVISO; nunca derruba a rodada.

A primeira publicação CRIA os arquivos, e arquivo novo herda a permissão da
pasta: restrinja cada dados/<publico>.json logo depois dela.

Configuração (.env): SITE_DIR.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

import publicar

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")

PUBLICOS = ("suporte", "desenvolvimento", "diretor", "licencas")
EXTENSOES_DO_SITE = (".html", ".css", ".js")


def destino_configurado() -> Path | None:
    bruto = os.getenv("SITE_DIR", "").strip().strip('"').strip("'")
    return Path(bruto) if bruto else None


def arquivos() -> list[tuple[Path, str]]:
    """(origem, caminho relativo no destino). O que não está aqui não é publicado."""
    lista = [(a, a.name) for a in sorted((RAIZ / "site").glob("*")) if a.is_file() and a.suffix in EXTENSOES_DO_SITE]
    lista.append((RAIZ / "assets" / "chart.min.js", "assets/chart.min.js"))
    lista += [(f, "assets/fonts/" + f.name) for f in sorted((RAIZ / "assets" / "fonts").glob("*.woff2"))]
    lista += [(RAIZ / "dados" / "site" / f"{p}.json", f"dados/{p}.json") for p in PUBLICOS]
    return lista


def publicar_site(destino: Path) -> tuple[list[str], list[str], list[str]]:
    """Devolve (copiados, já iguais, avisos)."""
    copiados: list[str] = []
    iguais: list[str] = []
    avisos: list[str] = []
    for origem, relativo in arquivos():
        alvo = destino / relativo
        try:
            conteudo = origem.read_bytes()
        except OSError:
            avisos.append(f"{relativo}: origem ausente (ainda não foi gerada) -- pulado")
            continue
        try:
            if alvo.exists() and alvo.read_bytes() == conteudo:
                iguais.append(relativo)
                continue
            alvo.parent.mkdir(parents=True, exist_ok=True)
            # sobrescreve o CONTEÚDO e mantém a permissão do arquivo no servidor. Não é
            # atômico: se o navegador ler no meio, a página fica com o dado anterior e
            # tenta de novo na busca seguinte.
            shutil.copyfile(str(origem), str(alvo))
        except OSError as e:
            avisos.append(f"{relativo}: falha ao copiar ({type(e).__name__})")
            continue
        copiados.append(relativo)
    return copiados, iguais, avisos


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    destino = destino_configurado()
    if destino is None:
        print("Publicação do site desligada (SITE_DIR vazio no .env).")
        return 0
    if publicar.encosta_no_projeto(destino):
        print("AVISO: SITE_DIR é a pasta do projeto, fica dentro dela ou a contém -- publicação "
              "recusada (o site serviria .env, dados/ e historico/).")
        return 1
    if not destino.is_dir():
        print(f"AVISO: pasta do site inacessível: {destino}")
        return 1
    copiados, iguais, avisos = publicar_site(destino)
    for aviso in avisos:
        print(f"AVISO: {aviso}")
    print(f"OK -> {destino}: {len(copiados)} arquivo(s) copiado(s), {len(iguais)} já em dia")
    return 0 if (copiados or iguais) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # último recurso: publicação nunca derruba a rodada
        print(f"AVISO: publicação do site falhou: {type(e).__name__}")
        sys.exit(1)
