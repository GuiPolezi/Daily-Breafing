"""Publica os dashboards gerados na pasta do site no IIS.

Copia os dashboard*.html já gerados (não gera nada) para PUBLICAR_DIR -- em
geral o compartilhamento da pasta do site no servidor, ex.: \\\\SERVIDOR\\relatorios$
-- e escreve lá um index.html com o link de cada página e quando ela foi gerada.

Só HTML sai daqui: nada de dados/, historico/, relatórios .md ou .env. O site
serve o que estiver nessa pasta, então ela nunca pode ser a pasta do projeto.

Regras:
  - PUBLICAR_DIR vazio = publicação desligada (sai com 0, sem aviso de erro).
  - Copia sobrescrevendo o CONTEÚDO do arquivo existente (shutil.copyfile), sem
    apagar e recriar: assim a permissão NTFS dada a um arquivo no servidor (ex.:
    dashboard_diretor.html só para a diretoria) sobrevive às publicações.
  - Degradação: servidor fora do ar, pasta sem permissão, página que não foi
    gerada -- tudo vira AVISO. Sai com 1 só se nenhuma página foi publicada,
    para o .bat mostrar o aviso; nunca derruba o pipeline.

Configuração (.env): PUBLICAR_DIR.
"""

from __future__ import annotations

import html
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")

# (arquivo, título, para quem é) -- na ordem em que aparecem no index.html
PAGINAS = [
    ("dashboard.html", "Briefing Diário", "Operação do dia: help desk, desenvolvimento, licenças e agenda"),
    ("dashboard_diretor.html", "Briefing da Diretoria", "Leitura executiva do dia, em nível agregado"),
    ("dashboard_licencas.html", "Briefing de Licenças", "Radar de renovação: vencidas, vencendo e mudanças"),
    ("dashboard_semanal.html", "Relatório Semanal", "Fechamento da semana para a equipe"),
]


def destino_configurado() -> Path | None:
    bruto = os.getenv("PUBLICAR_DIR", "").strip().strip('"')
    return Path(bruto) if bruto else None


def mesmo_lugar(a: Path, b: Path) -> bool:
    """Mesma pasta no disco, mesmo por caminhos diferentes (UNC da própria máquina, atalho)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def encosta_no_projeto(destino: Path) -> bool:
    """O destino é o projeto, está DENTRO dele ou CONTÉM o projeto?

    Qualquer um dos três faz o site no IIS enxergar o projeto (.env com credenciais,
    dados/, historico/). Compara pelo caminho resolvido e, para pegar UNC que aponta
    para esta mesma máquina (\\\\localhost\\c$\\...), pela identidade no disco.
    """
    raiz = RAIZ.resolve()
    try:
        d = destino.resolve()
    except OSError:
        d = destino
    if d == raiz or d.is_relative_to(raiz) or raiz.is_relative_to(d):
        return True
    if any(mesmo_lugar(c, raiz) for c in (destino, *destino.parents)):   # destino dentro do projeto
        return True
    return any(mesmo_lugar(destino, c) for c in (raiz, *raiz.parents))  # projeto dentro do destino


def pagina_index(publicadas: list[tuple[str, str, str, datetime]], agora: datetime) -> str:
    """index.html autocontido (sem CSS/JS externo), na paleta do tema semanal."""
    cartoes = "".join(
        f'<a class="cartao" href="{html.escape(arquivo)}">'
        f'<span class="nome">{html.escape(titulo)}</span>'
        f'<span class="desc">{html.escape(desc)}</span>'
        f'<span class="quando">gerado em {gerado:%d/%m/%Y às %H:%M}</span></a>'
        for arquivo, titulo, desc, gerado in publicadas
    ) or '<p class="vazio">Nenhuma página publicada ainda.</p>'
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Briefings — Sino</title>
<style>
:root{{--verde:#09512F;--folha:#F3F8F4;--tinta:#0B0F0D;--cinza:#6F7A73;--borda:rgba(9,81,47,.14)}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;background:var(--verde);color:var(--tinta);
  font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;display:flex;align-items:center;justify-content:center;padding:24px}}
main{{width:min(880px,100%);background:var(--folha);border-radius:24px;padding:clamp(24px,4vw,44px)}}
h1{{margin:0 0 6px;font-size:clamp(26px,4vw,38px);letter-spacing:-.02em;color:var(--verde)}}
.sub{{margin:0 0 28px;color:var(--cinza);font-size:14px}}
.grade{{display:flex;flex-wrap:wrap;gap:14px}}
.cartao{{flex:1 1 260px;display:flex;flex-direction:column;gap:6px;padding:20px 22px;border-radius:16px;background:#fff;
  border:1px solid var(--borda);text-decoration:none;color:inherit;transition:transform .2s,box-shadow .2s,border-color .2s}}
.cartao:hover,.cartao:focus-visible{{transform:translateY(-2px);border-color:var(--verde);box-shadow:0 10px 24px -14px rgba(9,81,47,.45);outline:none}}
.nome{{font-weight:800;font-size:19px;color:var(--verde)}}
.desc{{font-size:14px}}
.quando{{margin-top:auto;padding-top:6px;font-size:12.5px;color:var(--cinza)}}
.vazio{{color:var(--cinza)}}
</style>
</head>
<body>
<main>
  <h1>Briefings</h1>
  <p class="sub">Publicado em {agora:%d/%m/%Y às %H:%M}</p>
  <div class="grade">{cartoes}</div>
</main>
</body>
</html>
"""


def publicar(destino: Path, agora: datetime) -> tuple[list[str], list[str], list[str]]:
    """Copia as páginas e escreve o index. Devolve (publicadas agora, já em dia, avisos)."""
    publicadas_info: list[tuple[str, str, str, datetime]] = []
    publicadas: list[str] = []
    em_dia: list[str] = []
    avisos: list[str] = []
    for arquivo, titulo, desc in PAGINAS:
        origem = RAIZ / arquivo
        alvo = destino / arquivo
        if not origem.exists():
            avisos.append(f"{arquivo} não existe (ainda não foi gerado) -- pulado")
            continue
        # Só publica o que foi GERADO depois da última publicação (copyfile não copia a
        # data, então a do destino é a hora em que foi publicado). Sem isso, um gerador
        # que falhou hoje republicaria a página de ontem como nova, e o semanal
        # sobrescreveria no servidor as páginas diárias com versões mais velhas.
        try:
            if alvo.exists() and origem.stat().st_mtime <= alvo.stat().st_mtime:
                em_dia.append(arquivo)
                continue  # entra no index pelo que já está no destino
        except OSError:
            pass
        try:
            # sobrescreve o CONTEÚDO, mantém a ACL do arquivo no servidor. Não é atômico:
            # por um instante o IIS pode servir o arquivo sendo copiado -- aceito, porque
            # temporário + os.replace recriaria o arquivo e perderia a permissão por página.
            shutil.copyfile(origem, alvo)
        except OSError as e:
            avisos.append(f"{arquivo}: falha ao copiar ({e})")
            continue
        publicadas.append(arquivo)
        publicadas_info.append((arquivo, titulo, desc, datetime.fromtimestamp(origem.stat().st_mtime)))
    # o index lista o que ESTÁ no destino (inclusive página publicada em outra execução),
    # para não sumir o link da semanal quando só o diário rodou
    ja_la = {a for a, *_ in publicadas_info}
    for arquivo, titulo, desc in PAGINAS:
        alvo, origem = destino / arquivo, RAIZ / arquivo
        if arquivo not in ja_la and alvo.exists():
            try:
                # "gerado em": a data da página local quando ela é a mesma que está lá
                # (em dia); senão, a hora em que foi publicada
                base = origem if arquivo in em_dia else alvo
                publicadas_info.append((arquivo, titulo, desc, datetime.fromtimestamp(base.stat().st_mtime)))
            except OSError:
                pass
    ordem = {a: i for i, (a, *_) in enumerate(PAGINAS)}
    publicadas_info.sort(key=lambda p: ordem[p[0]])
    try:
        (destino / "index.html").write_text(pagina_index(publicadas_info, agora), encoding="utf-8")
    except OSError as e:
        avisos.append(f"index.html: falha ao escrever ({e})")
    return publicadas, em_dia, avisos


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    destino = destino_configurado()
    if destino is None:
        print("Publicação desligada (PUBLICAR_DIR vazio no .env).")
        return 0
    if encosta_no_projeto(destino):
        print("AVISO: PUBLICAR_DIR é a pasta do projeto, fica dentro dela ou a contém -- publicação "
              "recusada (o site serviria .env, dados/ e historico/).")
        return 1
    if not destino.is_dir():
        print(f"AVISO: pasta de publicação inacessível: {destino} "
              "(servidor fora do ar, compartilhamento errado ou sem permissão)")
        return 1
    publicadas, em_dia, avisos = publicar(destino, datetime.now())
    for aviso in avisos:
        print(f"AVISO: {aviso}")
    print(f"OK -> {destino}: {len(publicadas)} página(s) publicada(s)"
          + (f" ({', '.join(publicadas)})" if publicadas else "")
          + (f", {len(em_dia)} já em dia" if em_dia else ""))
    # 1 (aviso no .bat) só se NENHUMA página está no servidor; "nada novo" não é falha
    return 0 if (publicadas or em_dia) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # último recurso: publicação nunca derruba o briefing
        print(f"AVISO: publicação falhou: {type(e).__name__}: {e}")
        sys.exit(1)
