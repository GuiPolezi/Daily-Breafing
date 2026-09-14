"""Gera dashboard.html: painel estático e autocontido do briefing diário.

Lê historico/metricas.jsonl, os JSONs de dados/ e relatorio.md e escreve um
único arquivo HTML com CSS, dados, Chart.js e a fonte serifada embutidos
(funciona offline e copiado sozinho para qualquer pasta). Visual "favo de mel"
(design/): tela cheia, navegação por seções na roda do mouse, menu hexagonal. Nunca lança exceção por dado ausente:
cada seção degrada para "fonte indisponível" e o restante é gerado.

Configuração (.env):
  DASHBOARD_MOSTRAR_RANKING=true|false  (default true)
  DASHBOARD_DIAS_GRAFICO=30             (default 30)
"""

from __future__ import annotations

import base64
import html
import json
import math
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent
DADOS = RAIZ / "dados"
HISTORICO = RAIZ / "historico" / "metricas.jsonl"
RELATORIO = RAIZ / "relatorio.md"
ASSETS = RAIZ / "assets"
CHART_JS = ASSETS / "chart.min.js"
GSAP_JS = ASSETS / "gsap.min.js"
SAIDA = RAIZ / "dashboard.html"

# Baixado UMA vez para assets/; depois disso o dashboard não depende de rede.
CHART_JS_URL = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"
GSAP_JS_URL = "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.15.0/gsap.min.js"
LIMITE_DESATUALIZADA = timedelta(hours=24)

# (chave, rótulo, arquivo)
FONTES = [
    ("email", "E-mail", "email.json"),
    ("helpdesk", "Help desk", "helpdesk.json"),
    ("licencas", "Licenças", "licencas.json"),
]

load_dotenv(RAIZ / ".env")


# ----------------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------------
def cfg_bool(nome: str, padrao: bool) -> bool:
    valor = os.getenv(nome)
    if valor is None or not valor.strip():
        return padrao
    return valor.strip().lower() in ("1", "true", "sim", "yes", "on")


def cfg_int(nome: str, padrao: int) -> int:
    try:
        v = int(os.getenv(nome, "").strip() or padrao)
        return v if v > 0 else padrao
    except ValueError:
        return padrao


MOSTRAR_RANKING = cfg_bool("DASHBOARD_MOSTRAR_RANKING", True)
DIAS_GRAFICO = cfg_int("DASHBOARD_DIAS_GRAFICO", 30)


# ----------------------------------------------------------------------------
# Leitura tolerante a falhas
# ----------------------------------------------------------------------------
def ler_json(nome: str) -> tuple[dict | None, str | None]:
    """Retorna (dados, erro). dados=None quando o arquivo falta ou está corrompido."""
    caminho = DADOS / nome
    if not caminho.exists():
        return None, "arquivo não encontrado"
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return None, f"arquivo inválido ({type(e).__name__})"
    if not isinstance(dados, dict):
        return None, "formato inesperado"
    return dados, None


def ler_historico() -> list[dict]:
    if not HISTORICO.exists():
        return []
    registros = []
    try:
        linhas = HISTORICO.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    for linha in linhas:
        if not linha.strip():
            continue
        try:
            r = json.loads(linha)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and isinstance(r.get("data"), str):
            registros.append(r)
    registros.sort(key=lambda r: r["data"])
    return registros


def ler_relatorio() -> str | None:
    if not RELATORIO.exists():
        return None
    try:
        texto = RELATORIO.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return texto if texto.strip() else None


def carregar_js_embutido(caminho: Path, url: str, nome: str, sem: str) -> str | None:
    """Conteúdo de uma biblioteca JS para embutir. Baixa uma única vez se faltar."""
    if not caminho.exists():
        try:
            ASSETS.mkdir(exist_ok=True)
            with urllib.request.urlopen(url, timeout=10) as resp:
                conteudo = resp.read().decode("utf-8")
            caminho.write_text(conteudo, encoding="utf-8")
            print(f"{nome} baixado para {caminho}")
        except Exception as e:  # sem rede: segue sem a biblioteca
            print(f"AVISO: {nome} indisponivel ({type(e).__name__}); dashboard {sem}")
            return None
    try:
        js = caminho.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not js.strip():
        return None
    # Segurança ao embutir em <script>: nunca fechar a tag por acidente.
    return js.replace("</script", "<\\/script")


def carregar_chart_js() -> str | None:
    """Chart.js para os gráficos."""
    return carregar_js_embutido(CHART_JS, CHART_JS_URL, "Chart.js", "sem graficos")


def carregar_gsap() -> str | None:
    """GSAP para as animações do cabeçalho."""
    return carregar_js_embutido(GSAP_JS, GSAP_JS_URL, "GSAP", "sem animacoes")


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def esc(valor) -> str:
    return html.escape("" if valor is None else str(valor), quote=True)


def fmt_num(valor) -> str:
    if valor is None:
        return "—"
    try:
        return f"{int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return esc(valor)


def parse_dt(texto) -> datetime | None:
    if not isinstance(texto, str) or not texto.strip():
        return None
    try:
        dt = datetime.fromisoformat(texto.strip())
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%d/%m/%Y %H:%M") if dt else "—"


def label_dia(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m")
    except ValueError:
        return iso


def json_inline(obj) -> str:
    """JSON seguro para embutir dentro de <script>."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def status_fonte(dados: dict | None, erro: str | None, agora: datetime) -> dict:
    """Classifica a fonte: ok | desatualizada | indisponivel."""
    if dados is None:
        return {"estado": "indisponivel", "coletado_em": None, "detalhe": erro or "indisponível"}
    dt = parse_dt(dados.get("coletado_em"))
    if dt is None:
        return {"estado": "desatualizada", "coletado_em": None, "detalhe": "coletado_em ausente"}
    idade = agora - dt
    if idade > LIMITE_DESATUALIZADA:
        horas = int(idade.total_seconds() // 3600)
        return {"estado": "desatualizada", "coletado_em": dt, "detalhe": f"há {horas} h"}
    return {"estado": "ok", "coletado_em": dt, "detalhe": "atualizada"}


# ----------------------------------------------------------------------------
# Markdown -> HTML (conversão manual básica, com listas aninhadas) e slides
# ----------------------------------------------------------------------------
def inline_md(texto: str) -> str:
    t = esc(texto)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<em>\1</em>", t)
    return t


def markdown_para_html(md: str, base: int = 3) -> str:
    """Converte um trecho de markdown: títulos (a partir de h{base}), parágrafos,
    listas com sub-itens (por indentação) e linhas de continuação."""
    saida: list[str] = []
    pilha: list[tuple[int, str]] = []  # (indentação, "ul"|"ol") das listas abertas; o <li> fica aberto
    paragrafo: list[str] = []

    def fechar_paragrafo():
        nonlocal paragrafo
        if paragrafo:
            saida.append("<p>" + " ".join(paragrafo) + "</p>")
            paragrafo = []

    def fechar_listas(ate_indent: int = -1):
        while pilha and pilha[-1][0] > ate_indent:
            _, tag = pilha.pop()
            saida.append(f"</li></{tag}>")

    for linha in md.splitlines():
        bruto = linha.rstrip()
        if not bruto.strip():
            fechar_paragrafo()  # linha vazia não fecha a lista (o briefing espaça itens)
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", bruto)
        if m:
            fechar_paragrafo()
            fechar_listas()
            nivel = min(len(m.group(1)) + base - 1, 6)
            saida.append(f"<h{nivel}>{inline_md(m.group(2))}</h{nivel}>")
            continue
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", bruto):
            fechar_paragrafo()
            fechar_listas()
            saida.append("<hr>")
            continue
        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", bruto)
        if m:
            fechar_paragrafo()
            indent = len(m.group(1).expandtabs(4))
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            if pilha and indent > pilha[-1][0]:
                saida.append(f"<{tag}>")  # lista aninhada dentro do item aberto
                pilha.append((indent, tag))
            else:
                fechar_listas(indent)
                if pilha and pilha[-1][0] == indent:
                    if pilha[-1][1] == tag:
                        saida.append("</li>")
                    else:
                        _, anterior = pilha.pop()
                        saida.append(f"</li></{anterior}><{tag}>")
                        pilha.append((indent, tag))
                else:
                    saida.append(f"<{tag}>")
                    pilha.append((indent, tag))
            saida.append(f"<li>{inline_md(m.group(3))}")
            continue
        if pilha and bruto[:1].isspace():
            saida.append(" " + inline_md(bruto.strip()))  # continuação do item
            continue
        fechar_listas()
        paragrafo.append(inline_md(bruto.strip()))

    fechar_paragrafo()
    fechar_listas()
    return "\n".join(saida)


def dividir_briefing(md: str) -> tuple[str | None, list[dict]]:
    """Separa o relatório em (título do h1, slides). Um slide por título '## '.
    Sem '## ', devolve um único slide com todo o conteúdo."""
    titulo_h1: str | None = None
    slides: list[dict] = []
    atual: str | None = None
    buffer: list[str] = []

    def empurrar():
        corpo = "\n".join(buffer).strip("\n")
        if atual is not None or corpo.strip():
            slides.append({"titulo": atual, "md": corpo})

    for linha in md.splitlines():
        m1 = re.match(r"^#\s+(.*)$", linha)
        if m1 and titulo_h1 is None and atual is None and not any(l.strip() for l in buffer):
            titulo_h1 = m1.group(1).strip()
            continue
        m2 = re.match(r"^##\s+(.*)$", linha)
        if m2:
            empurrar()
            atual = m2.group(1).strip()
            buffer = []
            continue
        buffer.append(linha)
    empurrar()

    for s in slides:
        t = re.sub(r"^\s*\d+\s*[.)\-–—:]\s*", "", s["titulo"] or "").strip() or "Briefing"
        s["titulo"] = t[:1].upper() + t[1:]
    return titulo_h1, slides


def data_do_briefing(titulo_h1: str | None) -> str | None:
    """Extrai '11/09/2026 (sex) 08:13' de '# Briefing diário — 11/09/2026 (sex) 08:13'."""
    if not titulo_h1:
        return None
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4}.*)$", titulo_h1)
    return m.group(1).strip() if m else None


# ----------------------------------------------------------------------------
# Montagem dos dados
# ----------------------------------------------------------------------------
def coletar_nomes_tecnicos(historico: list[dict], helpdesk: dict | None) -> set[str]:
    nomes: set[str] = set()
    for r in historico:
        pt = r.get("atend_por_tecnico")
        if isinstance(pt, dict):
            nomes.update(str(k) for k in pt)
    if helpdesk:
        pt = (helpdesk.get("atendimentos_ultimo_dia_util") or {}).get("por_tecnico")
        if isinstance(pt, dict):
            nomes.update(str(k) for k in pt)
        for item in helpdesk.get("contagem_por_tecnico") or []:
            if isinstance(item, dict) and item.get("agent"):
                nomes.add(str(item["agent"]))
    return {n for n in nomes if n.strip()}


def redigir_nomes(texto: str, nomes: set[str]) -> str:
    """Substitui nomes de técnicos no briefing quando o ranking está desativado."""
    for nome in sorted(nomes, key=len, reverse=True):
        texto = re.sub(re.escape(nome), "[técnico]", texto, flags=re.IGNORECASE)
        primeiro = nome.split()[0]
        if len(primeiro) >= 4:
            texto = re.sub(rf"\b{re.escape(primeiro)}\b", "[técnico]", texto, flags=re.IGNORECASE)
    return texto


def serie_historico(historico: list[dict], dias: int) -> dict:
    recorte = historico[-dias:]
    return {
        "labels": [label_dia(r["data"]) for r in recorte],
        "fila": [r.get("fila_abertos") for r in recorte],
        "atend": [r.get("atend_total") for r in recorte],
        "datas": [r["data"] for r in recorte],
    }


def ranking_semanal(historico: list[dict]) -> dict:
    """Soma atend_por_tecnico dos últimos 5 dias úteis registrados (sem duplicar dia de referência)."""
    por_ref: dict[str, dict] = {}
    for r in historico:
        if isinstance(r.get("atend_por_tecnico"), dict) or r.get("atend_total") is not None:
            chave = str(r.get("atend_dia_ref") or r["data"])
            por_ref[chave] = r  # mantém o registro mais recente de cada dia de referência
    ultimos = list(por_ref.values())[-5:]
    soma: dict[str, int] = {}
    totais_dia = []
    for r in ultimos:
        pt = r.get("atend_por_tecnico") if isinstance(r.get("atend_por_tecnico"), dict) else {}
        for nome, qtd in pt.items():
            try:
                soma[str(nome)] = soma.get(str(nome), 0) + int(qtd)
            except (TypeError, ValueError):
                continue
        total = r.get("atend_total")
        if total is None and pt:
            total = sum(v for v in pt.values() if isinstance(v, (int, float)))
        totais_dia.append({"dia": str(r.get("atend_dia_ref") or label_dia(r["data"])), "total": total})
    ordenado = sorted(soma.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "dias": [d["dia"] for d in totais_dia],
        "totais": [d["total"] for d in totais_dia],
        "tecnicos": [n for n, _ in ordenado],
        "valores": [v for _, v in ordenado],
        "total_periodo": sum(v for v in soma.values()),
    }


def tabela_licencas(licencas: dict | None) -> list[dict]:
    if not licencas:
        return []
    itens = []
    for grupo, lista in (("vencida", "vencidas_recentes"), ("vencendo", "vencendo_em_breve")):
        for it in licencas.get(lista) or []:
            if not isinstance(it, dict):
                continue
            try:
                dias = int(it.get("dias"))
            except (TypeError, ValueError):
                dias = None
            itens.append({
                "cliente": it.get("cliente") or "—",
                "sistema": it.get("sistema") or "—",
                "vencimento": it.get("vencimento") or "—",
                "dias": dias,
                "grupo": grupo,
            })
    itens.sort(key=lambda i: (i["dias"] is None, i["dias"] if i["dias"] is not None else 0))
    return itens


def classificar_licenca(item: dict) -> tuple[str, str]:
    """-> (classe css, rótulo)."""
    d = item["dias"]
    if item["grupo"] == "vencida" or (d is not None and d < 0):
        return "st-critico", "Vencida"
    if d is not None and d <= 7:
        return "st-serio", "Urgente (≤ 7 dias)"
    return "st-atencao", "Vencendo"


# ----------------------------------------------------------------------------
# Renderização — identidade visual "favo de mel" (mockups em design/)
# ----------------------------------------------------------------------------
FONTES_DIR = ASSETS / "fonts"
# Fonte serifada display (Instrument Serif, OFL). Embutida em base64 quando o
# arquivo existe em assets/fonts; sem ele, cai na pilha de sistema (Georgia…).
FONTE_SERIF = [("normal", "InstrumentSerif-Regular.woff2"), ("italic", "InstrumentSerif-Italic.woff2")]


def carregar_fontes_css() -> str:
    blocos = []
    for estilo, nome in FONTE_SERIF:
        try:
            dados = (FONTES_DIR / nome).read_bytes()
        except OSError:
            continue
        if not dados:
            continue
        b64 = base64.b64encode(dados).decode("ascii")
        blocos.append(
            "@font-face{font-family:'Instrument Serif';font-style:%s;font-weight:400;font-display:swap;"
            "src:url(data:font/woff2;base64,%s) format('woff2')}" % (estilo, b64)
        )
    return "\n".join(blocos)


# --- Menu favo de mel -------------------------------------------------------
# Malha "pointy-top" rotacionada -15,25° (medida nos mockups). Eixo u a 44,75°,
# eixo v a 104,75°; coordenadas (u, v) das células, com (0,0) = Fontes.
FAVO_ORDEM = [
    ("destaques", "Destaques"), ("briefing", "Briefing"), ("licencas", "Licenças"),
    ("evolucao", "Evolução"), ("eficacia", "Eficácia"), ("fontes", "Fontes"),
]
FAVO_NAV = {(-1, 1): "destaques", (1, 0): "evolucao", (-1, 0): "eficacia",
            (1, -1): "licencas", (0, -1): "briefing", (0, 0): "fontes"}
FAVO_CHEIO = [(-2, 1), (-2, 2), (-1, 0), (-1, 1), (-1, 2), (0, -2), (0, -1), (0, 0), (0, 1),
              (1, -2), (1, -1), (1, 0), (1, 1), (2, -3), (2, -2), (2, -1), (3, -2), (3, -1)]
FAVO_COMPACTO = [(0, -1), (1, -1), (-1, 0), (0, 0), (1, 0), (-1, 1), (0, 1), (2, -1), (-2, 1)]
FAVO_ANGULO = 44.75


def favo_svg(celulas: list[tuple[int, int]], passo: float, fonte: float, classe: str) -> tuple[str, float]:
    """SVG do favo. Devolve (svg, altura em px na escala 1:1)."""
    rotulos = dict(FAVO_ORDEM)
    a, b = math.radians(FAVO_ANGULO), math.radians(FAVO_ANGULO + 60)
    r_nav = (passo - 4) / math.sqrt(3)  # 4 px de vão entre células vizinhas
    r_deco = r_nav * 0.9                # decorativos um pouco menores: os de navegação sobressaem
    margem = 10.0
    centros = {c: (passo * (c[0] * math.cos(a) + c[1] * math.cos(b)),
                   passo * (c[0] * math.sin(a) + c[1] * math.sin(b))) for c in celulas}

    def vertices(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
        return [(cx + r * math.cos(math.radians(FAVO_ANGULO - 30 + 60 * k)),
                 cy + r * math.sin(math.radians(FAVO_ANGULO - 30 + 60 * k))) for k in range(6)]

    todos = [p for (cx, cy) in centros.values() for p in vertices(cx, cy, r_nav)]
    min_x, min_y = min(p[0] for p in todos), min(p[1] for p in todos)
    largura = max(p[0] for p in todos) - min_x + 2 * margem
    altura = max(p[1] for p in todos) - min_y + 2 * margem

    def local(x: float, y: float) -> tuple[float, float]:
        return x - min_x + margem, y - min_y + margem

    def pontos(cx: float, cy: float, r: float) -> str:
        return " ".join("%.1f,%.1f" % local(x, y) for x, y in vertices(cx, cy, r))

    # entrada em onda radial (GSAP): atraso de cada célula proporcional à distância ao centro do conjunto
    centro_x = sum(cx for cx, _ in centros.values()) / len(centros)
    centro_y = sum(cy for _, cy in centros.values()) / len(centros)
    dist = {c: math.hypot(cx - centro_x, cy - centro_y) for c, (cx, cy) in centros.items()}
    dist_max = max(dist.values()) or 1.0

    # mel escorrendo. Gotas de borda: nada sob o vértice inferior, o pingo cai longe. Gotas internas: o vértice
    # aponta para o vão entre duas células decorativas de baixo, o pingo pousa nelas. Nunca sobre célula de navegação.
    ang_baixo = math.radians(FAVO_ANGULO + 30)  # vértice mais baixo da célula (74,75°)

    def sob_vertice(c: tuple[int, int]) -> list[tuple[int, int]]:
        cx, cy = centros[c]
        vx, vy = cx + r_deco * math.cos(ang_baixo), cy + r_deco * math.sin(ang_baixo)
        return [o for o, (ox, oy) in centros.items() if o != c and abs(ox - vx) < passo * 0.6 and oy > vy - passo * 0.15]

    decorativas = sorted((c for c in centros if FAVO_NAV.get(c) is None), key=lambda c: centros[c][0])
    borda = [c for c in decorativas if not sob_vertice(c)]
    internas = [c for c in decorativas if sob_vertice(c) and all(FAVO_NAV.get(o) is None for o in sob_vertice(c))]

    def espalhar(lista: list[tuple[int, int]], n: int) -> list[tuple[int, int]]:
        if n <= 0 or not lista:
            return []
        if len(lista) <= n:
            return list(lista)
        if n == 1:
            return [lista[len(lista) // 2]]
        salto = (len(lista) - 1) / (n - 1)
        return [lista[round(i * salto)] for i in range(n)]

    grande = len(celulas) > 12
    com_gota: dict[tuple[int, int], str] = {c: "borda" for c in espalhar(borda, 3 if grande else 2)}
    for _ in range(2 if grande else 1):  # internas: as mais afastadas das gotas já escolhidas
        livres = [c for c in internas if c not in com_gota]
        if not livres:
            break
        if com_gota:
            melhor = max(livres, key=lambda c: min(math.dist(centros[c], centros[e]) for e in com_gota))
        else:
            melhor = livres[len(livres) // 2]
        com_gota[melhor] = "interna"
    tamanhos = {"borda": [(16.0, 3.6), (13.0, 3.2), (11.0, 2.8)], "interna": [(10.0, 2.6), (8.5, 2.3)]}  # (comprimento, meia-largura)

    def gota(cx: float, cy: float, r: float, comp: float, meia: float, tipo: str) -> str:
        vx, vy = local(*vertices(cx, cy, r)[1])  # vértice mais baixo da célula (74,75°)
        x0, y0 = local(cx, cy)
        caminho = f'd="M{x0:.1f},{y0 + r * 0.15:.1f} Q{vx - 1.5:.1f},{(y0 + vy) / 2:.1f} {vx:.1f},{vy:.1f}"'
        fio, fluxo = f'<path class="fio" {caminho}/>', f'<path class="fluxo" {caminho}/>'
        corpo = (f'<path class="mel" d="M{vx:.1f},{vy:.1f} '
                 f'C{vx - meia * .6:.1f},{vy + comp * .35:.1f} {vx - meia:.1f},{vy + comp * .55:.1f} {vx - meia:.1f},{vy + comp - meia:.1f} '
                 f'A{meia:.1f},{meia:.1f} 0 0 0 {vx + meia:.1f},{vy + comp - meia:.1f} '
                 f'C{vx + meia:.1f},{vy + comp * .55:.1f} {vx + meia * .6:.1f},{vy + comp * .35:.1f} {vx:.1f},{vy:.1f}Z"/>')
        brilho = (f'<ellipse class="brilho" cx="{vx - meia * .35:.1f}" cy="{vy + comp - meia - 1:.1f}" '
                  f'rx="{meia * .3:.1f}" ry="{meia * .55:.1f}"/>')
        pingo = f'<circle class="pingo" cx="{vx:.1f}" cy="{vy + comp + 1:.1f}" r="{meia * .55:.1f}"/>'
        return f'<g class="gota {tipo}">{fio}{fluxo}<g class="bojo">{corpo}{brilho}</g>{pingo}</g>'

    deco, nav, gotas = [], [], []
    for c, (cx, cy) in centros.items():
        alvo = FAVO_NAV.get(c)
        d = f"{0.45 * dist[c] / dist_max:.2f}"
        if alvo is None:
            cheia = " cheia" if c in com_gota else ""
            deco.append(f'<polygon class="cel deco{cheia}" data-d="{d}" points="{pontos(cx, cy, r_deco)}"/>')
        else:
            tx, ty = local(cx, cy)
            nav.append(
                f'<a class="cel nav" href="#{alvo}" data-alvo="{alvo}" data-d="{d}" aria-label="Ir para {esc(rotulos[alvo])}">'
                f'<polygon points="{pontos(cx, cy, r_nav)}"/>'
                f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="{fonte:g}" text-anchor="middle" dominant-baseline="central">{esc(rotulos[alvo])}</text></a>'
            )
    contagem = {"borda": 0, "interna": 0}
    for c, tipo in com_gota.items():
        comp, meia = tamanhos[tipo][contagem[tipo] % len(tamanhos[tipo])]
        contagem[tipo] += 1
        gotas.append(gota(*centros[c], r_deco, comp, meia, tipo))

    # gradientes e sombra ficam dentro de cada SVG (os dois favos coexistem no DOM), ids sufixados pela classe
    def grad(nome: str, de: str, ate: str) -> str:
        return (f'<linearGradient id="g-{nome}-{classe}" x1="0" y1="0" x2=".25" y2="1">'
                f'<stop offset="0" stop-color="{de}"/><stop offset="1" stop-color="{ate}"/></linearGradient>')

    defs = (
        "<defs>"
        + grad("nav", "#eba93b", "#d48b1e") + grad("deco", "#ecb14f", "#dc9a30")
        + grad("cheia", "#fbcb4e", "#eaa221") + grad("ativa", "#ffdc6a", "#f8bf30") + grad("gota", "#f7bb33", "#df8a17")
        + f'<filter id="sombra-{classe}" x="-20%" y="-20%" width="140%" height="160%" color-interpolation-filters="sRGB">'
        '<feDropShadow dx="0" dy="1.5" stdDeviation="1" flood-color="#7a4d0c" flood-opacity=".22"/>'
        '<feDropShadow dx="0" dy="7" stdDeviation="7" flood-color="#8a5a12" flood-opacity=".22"/>'
        "</filter></defs>"
    )
    estilo = (f"--favo-h:{altura:.0f}px;--g-nav:url(#g-nav-{classe});--g-deco:url(#g-deco-{classe});"
              f"--g-cheia:url(#g-cheia-{classe});--g-ativa:url(#g-ativa-{classe});--g-gota:url(#g-gota-{classe})")
    svg = (f'<svg class="favo-svg {classe}" viewBox="0 0 {largura:.0f} {altura:.0f}" style="{estilo}" aria-hidden="false">'
           + defs + f'<g class="favo-corpo" filter="url(#sombra-{classe})">'
           + "".join(deco) + "".join(nav) + "".join(gotas) + "</g></svg>")
    return svg, altura


ABELHA_SVG = """<svg class="abelha" viewBox="0 0 100 104" aria-hidden="true" focusable="false">
<defs>
<linearGradient id="mel-g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fbc93a"/><stop offset=".55" stop-color="#f5a11f"/><stop offset="1" stop-color="#ef6f1a"/></linearGradient>
</defs>
<g class="abelha-voo">
<g fill="none" stroke-linecap="round" stroke-linejoin="round">
<ellipse cx="50" cy="66" rx="12.5" ry="25" stroke="#f9c04a" stroke-opacity=".5" stroke-width="8.5"/>
<g class="asa asa-e">
<ellipse cx="27" cy="43" rx="21.5" ry="11.5" transform="rotate(-14 27 43)" stroke="#f9c04a" stroke-opacity=".5" stroke-width="8.5"/>
<ellipse cx="27" cy="43" rx="21.5" ry="11.5" transform="rotate(-14 27 43)" stroke="url(#mel-g)" stroke-width="5"/>
<path d="M12 39c4-5 12-8 20-7" stroke="#fff1b8" stroke-opacity=".65" stroke-width="1.4"/>
</g>
<g class="asa asa-d">
<ellipse cx="73" cy="43" rx="21.5" ry="11.5" transform="rotate(14 73 43)" stroke="#f9c04a" stroke-opacity=".5" stroke-width="8.5"/>
<ellipse cx="73" cy="43" rx="21.5" ry="11.5" transform="rotate(14 73 43)" stroke="url(#mel-g)" stroke-width="5"/>
<path d="M88 39c-4-5-12-8-20-7" stroke="#fff1b8" stroke-opacity=".65" stroke-width="1.4"/>
</g>
<g stroke="url(#mel-g)">
<path d="M44 22c-3-6-8-9-14-11" stroke-width="3.4"/>
<path d="M56 22c3-6 8-9 14-11" stroke-width="3.4"/>
<ellipse cx="50" cy="66" rx="12.5" ry="25" stroke-width="5.6"/>
<path d="M41 37q9-7 18 0" stroke-width="3.6"/>
</g>
<path d="M40 60c-1 8 2 18 9 24" stroke="#fff1b8" stroke-opacity=".65" stroke-width="1.4"/>
</g>
<g fill="url(#mel-g)"><circle cx="29.5" cy="10" r="3.4"/><circle cx="70.5" cy="10" r="3.4"/><ellipse cx="50" cy="27" rx="8.6" ry="7.6"/></g>
</g>
</svg>"""

TRACO_SVG = ('<svg class="traco" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
             '<rect width="1" height="1" shape-rendering="crispEdges"/></svg>')

CHEVRON_SVG = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" '
               'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>')


# --- CSS ----------------------------------------------------------------------
CSS = """
:root{
  /* paleta extraída dos mockups (design/) */
  --creme:#f9e0a3;--mel:#de9628;--mel-claro:#fdce4c;--mel-hover:#efb13d;--mel-deco:#e3a139;
  --oliva:#606c38;--oliva-escuro:#283618;--verde:#b8d86e;--verde-suave:#adcb67;--verde-texto:#9bb45c;
  --marfim:#fefae0;--titulo:#f6e3c5;--carimbo:#433c2c;--tinta:#1f1f1f;--traco:#f3991f;
  /* cards claros: papéis de cor sobre --card (contrastes medidos sobre #fefae0) */
  --card:#fefae0;--tinta-2:#5c6446;--divisoria:rgba(40,54,24,.14);--neutro-bg:rgba(40,54,24,.08);--bom-bg:#e3eecd;
  --sombra-card:0 10px 30px -18px rgba(40,54,24,.35);
  --vermelho:#e0261b;--verm-bg:#fdecec;--verm-ink:#c1272d;--ambar-bg:#fff3d6;--ambar-ink:#855800;--bom:#3f6d17;
  --azul:#277fae;--rubro:#e0301e;--rubro-area:#a33e20;
  --tabela-bg:#fefae0;--tabela-cabeca:#f4eecf;--tabela-linha:rgba(40,54,24,.12);--tabela-ink:#1f1f1f;--tabela-muted:#5c6446;--tabela-hover:rgba(40,54,24,.05);
  --serif:"Instrument Serif","Bodoni MT",Didot,"Playfair Display",Georgia,"Times New Roman",serif;
  --sans:system-ui,-apple-system,"Segoe UI Variable","Segoe UI",Inter,Roboto,"Helvetica Neue",Arial,sans-serif;
  --ease:cubic-bezier(.22,.61,.36,1);--ease-out:cubic-bezier(.16,1,.3,1);
  /* ritmo: escala 1.25 a partir de 15px (12 / 15 / 19 / 24 / 30 / 37 / 47 / 58 / 73) */
  --t-meta:12.5px;--t-corpo:15px;
  --t-hero:clamp(38px,min(7.6vh,4.6vw),82px);--t-num:clamp(56px,min(10vh,5.2vw),112px);--t-num-2:clamp(30px,min(5.6vh,3vw),60px);
  --t-rotulo:clamp(11.5px,1.6vh,14px);--t-num-3:clamp(20px,min(3vh,1.6vw),30px);
  --t-card:clamp(17px,min(2.6vh,1.35vw),24px);
  --topo-h:clamp(180px,29vh,330px);
  --margem:clamp(14px,1.4vw,24px);--pad:clamp(18px,2.2vw,36px);--gap:clamp(14px,1.8vw,28px);--pad-card:clamp(18px,2vw,30px);
  --r-colmeia:clamp(30px,3vw,48px);--r-card:clamp(24px,2.4vw,36px);
}
*,*::before,*::after{box-sizing:border-box}
html{color-scheme:light;-webkit-text-size-adjust:100%;height:100%}
body{margin:0;min-height:100%;overflow:hidden;background:var(--creme);color:var(--tinta);font:var(--t-corpo)/1.5 var(--sans);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
h1,h2,h3,h4,p,ul,ol,figure{margin:0}
ul,ol{padding:0;list-style:none}
button{font:inherit;color:inherit}
a{color:inherit}
:focus-visible{outline:2px solid var(--oliva-escuro);outline-offset:3px}
.card :focus-visible{outline-color:var(--oliva-escuro)}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.pular{position:absolute;left:12px;top:-64px;z-index:100;background:var(--oliva-escuro);color:var(--marfim);padding:8px 16px;border-radius:999px;text-decoration:none;font-weight:700}
.pular:focus{top:12px}

/* ---- palco: fundo, header e container ficam parados; só o conteúdo troca ---- */
.palco{display:flex;flex-direction:column;height:100vh;height:100dvh;padding:0 var(--margem) var(--margem)}
.topo{flex:0 0 auto;height:var(--topo-h);display:flex;align-items:center;justify-content:space-between;gap:24px;padding:0 clamp(8px,1.6vw,28px)}
.marca{display:flex;align-items:center;gap:clamp(12px,1.6vw,28px);text-decoration:none;color:inherit}
.abelha{height:clamp(72px,14vh,150px);width:auto;flex:0 0 auto;filter:drop-shadow(0 6px 10px rgba(222,150,40,.25))}
.wordmark{font:800 clamp(26px,min(4.4vh,2.4vw),42px)/1.05 var(--sans);letter-spacing:-.02em;color:#000}
.wordmark .w{display:block;position:relative;width:max-content}
.wordmark .traco{position:absolute;left:0;top:53%;height:3px;width:var(--traco-w,100%);display:block;overflow:visible;fill:var(--traco);pointer-events:none}
/* entrada animada (GSAP): html.anim esconde até o script assumir; sem JS ou sem GSAP a classe não existe */
.anim .abelha,.anim .wordmark .w,.anim .favo-svg{opacity:0}
.carimbo{flex:0 0 auto;margin:0 0 8px calc(var(--pad) - 2px);font-size:var(--t-meta);font-weight:700;color:var(--carimbo);letter-spacing:.01em;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center}
.carimbo .sep{opacity:.55}
.carimbo .aviso{background:var(--ambar-bg);color:var(--ambar-ink);padding:2px 10px;border-radius:999px;text-decoration:none}
.carimbo .aviso.grave{background:var(--verm-bg);color:var(--verm-ink)}

/* ---- menu favo de mel ---- */
.favo{flex:0 0 auto;display:flex;align-items:center;justify-content:flex-end;height:100%;min-width:0}
.favo-svg{height:min(var(--favo-h),calc(var(--topo-h) - 12px));width:auto;overflow:visible;display:block}
.favo-cheio{display:none}
@media (min-height:960px) and (min-width:1180px){.favo-cheio{display:block}.favo-compacto{display:none}}
/* células: gradiente sutil (mais claro no topo) + filete claro na borda; a sombra é um filtro só, no grupo .favo-corpo */
.favo-svg polygon{stroke:rgba(255,241,205,.34);stroke-width:1;stroke-linejoin:round}
.cel.nav polygon{fill:var(--g-nav,var(--mel))}
.cel.deco{fill:var(--g-deco,var(--mel-deco))}
.cel.deco.cheia{fill:var(--g-cheia,var(--mel-claro))}
.cel.nav{cursor:pointer;transition:transform .2s var(--ease),filter .25s var(--ease);outline:none}
/* origem central só no fallback CSS: com GSAP a origem já vem embutida na matriz do atributo transform */
html:not(.gsap) .cel.nav{transform-box:fill-box;transform-origin:center}
.cel.nav text{fill:var(--oliva-escuro);font-family:var(--sans);font-weight:700;letter-spacing:-.01em;pointer-events:none;user-select:none}
/* sem GSAP o hover escala por CSS; com GSAP a escala é dele (transform CSS sobrescreveria o atributo) */
html:not(.gsap) .cel.nav:hover,html:not(.gsap) .cel.nav:focus-visible{transform:scale(1.09)}
.cel.nav:hover,.cel.nav:focus-visible{filter:brightness(1.07) drop-shadow(0 5px 7px rgba(67,60,44,.32))}
.cel.nav:focus-visible polygon{stroke:var(--oliva-escuro);stroke-width:2}
.cel.nav.ativa polygon{fill:var(--g-ativa,var(--mel-claro))}
.cel.nav.ativa{filter:drop-shadow(0 3px 5px rgba(67,60,44,.28))}
/* mel escorrendo: fio na face da célula, gota com brilho; o pingo que cai só existe com GSAP */
.gota .fio{fill:none;stroke:rgba(235,150,30,.5);stroke-width:2.2;stroke-linecap:round}
.gota .fluxo{fill:none;stroke:rgba(255,214,110,.9);stroke-width:2.4;stroke-linecap:round;opacity:0}
.gota .mel{fill:var(--g-gota,var(--mel))}
.gota .brilho{fill:#fff3c4;opacity:.55}
.gota .pingo{fill:#e9961c;opacity:0}
.menu-simples{display:none}

/* ---- container amarelo e seções ---- */
.colmeia{position:relative;flex:1 1 auto;min-height:0;background:var(--mel);border-radius:var(--r-colmeia);overflow:hidden;isolation:isolate}
.secao{position:absolute;inset:0;padding:var(--pad);display:flex;flex-direction:column;gap:var(--gap);opacity:0;visibility:hidden;transform:translateY(34px);transition:opacity .5s var(--ease),transform .65s var(--ease),visibility 0s linear .65s;overflow:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:rgba(40,54,24,.35) transparent;outline:none}
.secao.antes{transform:translateY(-34px)}
.secao.ativa{opacity:1;visibility:visible;transform:none;transition-delay:.06s,.06s,0s;z-index:1}
.titulo-secao{font:400 var(--t-hero)/.95 var(--serif);color:var(--titulo);letter-spacing:-.01em;text-wrap:balance}
.titulo-secao .l{display:block}
.subtitulo{font:400 clamp(18px,2.4vh,26px)/1.2 var(--serif);color:rgba(246,227,197,.8)}
/* cabeçalho único de seção: título serif à esquerda, contexto (texto ou números) à direita, na mesma linha de base.
   Números do lado ficam sobre o mel, por isso em oliva-escuro (5,2:1) e não em marfim (2,3:1). */
.secao-cabeca{flex:0 0 auto}
.secao-cabeca.dividida{display:flex;justify-content:space-between;align-items:flex-end;gap:12px var(--gap);flex-wrap:wrap}
.secao-cabeca .lado{display:flex;align-items:flex-end;justify-content:flex-end;flex-wrap:wrap;gap:8px clamp(20px,2.6vw,44px);text-align:right;margin-left:auto;padding-bottom:.3em}
.secao-cabeca.dividida .titulo-secao{line-height:1.05}  /* descendentes (ç, g) não podem ficar sob o conteúdo seguinte */
.secao-cabeca .lado .kpi-numero{color:var(--oliva-escuro)}
.secao-cabeca .lado .kpi-legenda{color:rgba(40,54,24,.62)}
.carimbo-briefing{font:400 clamp(20px,3vh,30px)/1 var(--serif);color:var(--titulo);white-space:nowrap}
.nota-escura{flex:0 0 auto;font-size:13px;font-weight:600;color:rgba(40,54,24,.7)}
.vazio{flex:1 1 auto;display:flex;align-items:center;justify-content:center;text-align:center;font:400 clamp(20px,3vh,28px)/1.3 var(--serif);color:rgba(246,227,197,.85);padding:var(--pad)}

/* ---- cards ---- */
.card{background:var(--card);color:var(--tinta);border-radius:var(--r-card);padding:var(--pad-card);display:flex;flex-direction:column;gap:12px;min-width:0;min-height:0;position:relative;box-shadow:var(--sombra-card);transition:transform .2s var(--ease),box-shadow .2s var(--ease)}
.card:hover{transform:translateY(-3px);box-shadow:0 18px 36px -12px rgba(40,54,24,.5)}
@keyframes entrar{from{opacity:0;transform:translateY(16px)}}
/* pílula de julgamento (delta): fundo tintado pela direção, nunca marfim sobre marfim */
.badge{display:inline-flex;align-items:center;gap:5px;align-self:flex-start;background:var(--verm-bg);color:var(--verm-ink);border-radius:999px;padding:4px 12px;font-size:12px;font-weight:600;line-height:1.4;white-space:nowrap}
.badge b{font-size:14px;font-weight:700}
.badge.bom{background:var(--bom-bg);color:var(--bom)}
.badge.neutro{background:var(--neutro-bg);color:var(--oliva)}

/* Destaques */
.grade-destaques{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));grid-template-rows:auto 1fr;gap:var(--gap)}
.grade-destaques .card{min-height:auto}
.grade-destaques .titulo-secao{align-self:start;padding-top:.12em}
/* anatomia única dos KPIs: rótulo em caixa alta, número (marfim, itálico 800), linha de julgamento (delta), rodapé de contexto.
   O estado da fonte é etiqueta na linha do rótulo, metadado separado do delta de negócio. */
.kpi-primario{grid-column:2 / span 2}
.kpi{gap:clamp(8px,1.2vh,14px)}
.kpi-cabeca{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px 12px}
.kpi-rotulo{font:700 var(--t-rotulo)/1.3 var(--sans);color:var(--oliva);letter-spacing:.09em;text-transform:uppercase}
.kpi-fonte{margin-left:auto;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;line-height:1.5;padding:2px 10px;border-radius:999px;background:var(--ambar-bg);color:var(--ambar-ink);white-space:nowrap}
.kpi-fonte.grave{background:var(--verm-bg);color:var(--verm-ink)}
.kpi-numero{font:italic 800 var(--t-num)/.9 var(--sans);color:var(--tinta);letter-spacing:-.045em;font-variant-numeric:tabular-nums;white-space:nowrap;padding-right:.06em}
.kpi-numero.menor{font-size:var(--t-num-2)}
.kpi-numero .sem-dado{color:rgba(31,31,31,.3)}
.kpi-linha{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px clamp(16px,1.8vw,32px)}
.kpi-juizo{display:flex;flex-wrap:wrap;align-items:center;gap:8px;min-height:26px}
.kpi-secundario{font-size:clamp(14px,1.9vh,17px);font-weight:600;color:var(--tinta-2);white-space:nowrap}
.kpi-secundario b{font:italic 800 clamp(24px,3.6vh,38px)/1 var(--sans);color:var(--tinta);letter-spacing:-.03em;margin-right:.12em}
.kpi-par{display:flex;align-items:flex-end;flex-wrap:wrap;gap:8px clamp(20px,2.6vw,44px)}
.kpi-legenda{font-size:clamp(13px,1.7vh,15px);font-weight:600;color:var(--tinta-2);margin-top:2px}
.kpi-rodape{margin-top:auto;font-size:clamp(13px,1.7vh,15px);font-weight:600;color:var(--tinta-2);line-height:1.4}
.kpi-rodape b{color:var(--tinta);font-weight:700}
.kpi-primario .kpi-corpo{margin-top:auto;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:10px clamp(20px,3vw,56px)}
.kpi-quebra{display:flex;flex-direction:column;align-items:flex-end;gap:5px;font-size:clamp(13px,1.7vh,15px);font-weight:600;color:var(--tinta-2);line-height:1.4}
.kpi-quebra .lista{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:4px 14px}
.kpi-quebra b{color:var(--tinta);font-weight:700}
.kpi-quebra .sep{color:rgba(40,54,24,.3)}
.kpi-quebra .titulo{color:var(--oliva);text-transform:uppercase;letter-spacing:.07em;font-size:11.5px}
.kpi-vazio{font:400 clamp(16px,2.2vh,20px)/1.3 var(--serif);color:var(--tinta-2)}

/* Evolução */
.graficos{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:1fr 1fr;gap:var(--gap)}
.card-grafico{gap:8px}
.card-grafico .kpi-linha{gap:4px clamp(12px,1.4vw,24px)}
.grafico-caixa{position:relative;flex:1 1 auto;min-height:120px;transition:height .5s var(--ease)}
.grafico-caixa canvas{position:absolute;inset:0;width:100% !important;height:100% !important}
.secao.aberto .graficos{flex:0 0 auto}
.serie{flex:0 0 auto;display:flex;flex-direction:column}
.acoes{flex:0 0 auto}
.pilula{background:var(--verde);color:var(--oliva);font-weight:700;font-size:14px;border:0;border-radius:999px;padding:10px 20px;display:inline-flex;gap:10px;align-items:center;cursor:pointer;transition:transform .2s var(--ease),box-shadow .2s var(--ease),background .2s var(--ease)}
.pilula:hover{background:#c4e07f;transform:translateY(-2px);box-shadow:0 10px 20px -10px rgba(40,54,24,.6)}
.pilula svg{transition:transform .35s var(--ease)}
.pilula[aria-expanded=true] svg{transform:rotate(180deg)}
.expansivel{flex:0 0 auto;position:relative;overflow:hidden;height:0;opacity:0;transition:height .55s var(--ease),opacity .4s var(--ease)}
.expansivel.aberto{opacity:1}
.expansivel-pad{padding-top:var(--gap)}
.tabela-serie{width:100%;border-collapse:separate;border-spacing:0;background:var(--tabela-bg);border-radius:20px;overflow:hidden;color:var(--tabela-ink);font-weight:600;font-size:14px;box-shadow:var(--sombra-card)}
.tabela-serie th{background:var(--tabela-cabeca);color:var(--tabela-muted);text-align:left;padding:14px 22px;font-size:11.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;border-bottom:1px solid var(--tabela-linha)}
.tabela-serie td{padding:12px 22px;border-top:1px solid var(--tabela-linha);font-variant-numeric:tabular-nums}
.tabela-serie tbody tr:first-child td{border-top:0}
.tabela-serie .c{text-align:center}
.tabela-serie .d{text-align:right}
.tabela-serie tbody tr{transition:background .2s}
.tabela-serie tbody tr:hover{background:var(--tabela-hover)}

/* Eficácia */
.card-ranking{flex:1 1 auto;min-height:0;padding:clamp(20px,3vh,40px) clamp(20px,3vw,48px);overflow:auto;scrollbar-width:thin;scrollbar-color:rgba(40,54,24,.3) transparent}
.card-ranking .ranking{min-height:calc(var(--n) * 40px + 30px)}
.ranking{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:max-content minmax(0,1fr);grid-template-rows:minmax(0,1fr) auto;column-gap:clamp(14px,2vw,26px)}
.nomes{grid-row:1;grid-column:1;display:flex;flex-direction:column;justify-content:space-evenly;text-align:right}
.nomes span{height:clamp(24px,4.2vh,34px);line-height:clamp(24px,4.2vh,34px);font-weight:700;font-size:clamp(14px,2.2vh,18px);color:var(--tinta);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:clamp(120px,14vw,240px)}
.trilhos{grid-row:1;grid-column:2;position:relative;display:flex;flex-direction:column;justify-content:space-evenly}
.grade-ranking{position:absolute;inset:0;background-image:linear-gradient(to right,var(--divisoria) 1px,transparent 1px);background-size:calc(100% / var(--divs)) 100%;background-repeat:repeat-x;box-shadow:inset -1px 0 var(--divisoria);pointer-events:none}
.linha{position:relative;display:flex;align-items:center;gap:10px;height:clamp(24px,4.2vh,34px)}
.barra{display:block;height:100%;width:0;background:var(--oliva);border-radius:999px;transition:width .9s var(--ease) calc(.2s + var(--i,0)*90ms)}
.secao.ativa .barra{width:calc(var(--v) / var(--max) * 100%)}
.valor{font:italic 800 var(--t-num-3)/1 var(--sans);color:var(--tinta);letter-spacing:-.02em;opacity:0;transition:opacity .4s calc(.7s + var(--i,0)*90ms);font-variant-numeric:tabular-nums}
.secao.ativa .valor{opacity:1}
.eixo{grid-row:2;grid-column:2;position:relative;height:30px}
.eixo span{position:absolute;left:calc(var(--p) * 100%);transform:translateX(-50%);top:8px;font-size:13px;font-weight:600;color:var(--tinta-2);font-variant-numeric:tabular-nums}

/* Licenças */
.tabela-clara{flex:1 1 auto;min-height:0;position:relative;overflow:auto;background:var(--tabela-bg);border-radius:20px;box-shadow:var(--sombra-card);scrollbar-width:thin;scrollbar-color:rgba(0,0,0,.2) transparent}
.tabela-lic{width:100%;border-collapse:separate;border-spacing:0;color:var(--tabela-ink);font-size:14px}
.tabela-lic th{position:sticky;top:0;z-index:1;background:var(--tabela-cabeca);color:var(--tabela-muted);font-size:11.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;text-align:left;padding:14px 18px;border-bottom:1px solid var(--tabela-linha)}
.tabela-lic td{padding:12px 18px;border-bottom:1px solid var(--tabela-linha);vertical-align:middle}
.tabela-lic tbody tr{transition:background .15s}
.tabela-lic tbody tr:hover{background:var(--tabela-hover)}
.tabela-lic .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tabela-lic .urgente{font-weight:700;color:var(--verm-ink)}
.pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:3px 10px;font-size:12px;font-weight:600;white-space:nowrap}
.pill::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.pill.vencida{background:var(--verm-bg);color:var(--verm-ink)}
.pill.vencendo{background:var(--ambar-bg);color:var(--ambar-ink)}

/* Briefing (slider) */
.slider{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;gap:12px}
.slides-janela{flex:1 1 auto;min-height:0;position:relative;overflow:hidden;border-radius:var(--r-card)}
.slides{display:flex;height:100%;transition:transform .55s var(--ease);will-change:transform}
.slide{flex:0 0 100%;min-width:0;height:100%;display:flex}
/* com GSAP (html.gsap) os slides ficam empilhados e a troca é um fade com profundidade; sem GSAP a faixa acima continua */
html.gsap .slides-janela{background:var(--card);box-shadow:var(--sombra-card)}  /* superfície fixa: só o conteúdo cruza no fade */
html.gsap .slides{display:block;position:relative;transition:none;transform:none !important}
html.gsap .slide{position:absolute;inset:0;opacity:0;visibility:hidden;transform-origin:50% 50%}
html.gsap .slide.ativo{opacity:1;visibility:visible}
.card-slide{flex:1 1 auto;min-width:0;overflow:auto;gap:14px;padding:clamp(20px,3vh,36px) clamp(22px,3vw,44px);scrollbar-width:thin;scrollbar-color:rgba(40,54,24,.3) transparent}
.card-slide:hover{transform:none;box-shadow:var(--sombra-card)}
.card-slide .kpi-rotulo{font-size:clamp(13px,1.9vh,16px)}  /* card de leitura: rótulo não pode ser menor que o corpo */
.slide-corpo{font-size:clamp(13.5px,1.8vh,15px);line-height:1.5;color:var(--tinta)}
.slide-corpo.duas-colunas{columns:2;column-gap:40px}
.slide-corpo li{position:relative;padding-left:18px;margin:0 0 10px;break-inside:avoid}
.slide-corpo ul>li::before{content:"";position:absolute;left:0;top:.52em;width:8px;height:8px;border-radius:50%;background:var(--oliva)}
.slide-corpo ol{counter-reset:item}
.slide-corpo ol>li{counter-increment:item;padding-left:26px}
.slide-corpo ol>li::before{content:counter(item) ".";position:absolute;left:0;top:0;font-weight:800;color:var(--oliva)}
.slide-corpo li ul,.slide-corpo li ol{margin-top:8px}
.slide-corpo li li{margin-bottom:6px;font-size:.95em}
.slide-corpo li li::before{width:6px;height:6px;background:rgba(96,108,56,.6)}
.slide-corpo strong{color:var(--tinta);font-weight:700}
.slide-corpo em{color:var(--oliva)}
.slide-corpo code{font-family:ui-monospace,Consolas,"Cascadia Mono",monospace;font-size:.9em;background:rgba(40,54,24,.08);padding:1px 5px;border-radius:5px}
.slide-corpo p{margin-bottom:10px}
.slide-corpo h4,.slide-corpo h5{color:var(--oliva);margin:6px 0 8px;font-size:1.05em}
.slide-corpo hr{border:0;border-top:1px solid var(--divisoria);margin:10px 0}
.slider-controles{flex:0 0 auto;display:flex;align-items:center;justify-content:center;gap:18px}
.seta{background:none;border:0;color:var(--oliva);font:400 34px/1 var(--serif);cursor:pointer;padding:2px 12px;border-radius:12px;transition:transform .2s var(--ease),opacity .2s,background .2s}
.seta:hover{transform:scale(1.15);background:rgba(40,54,24,.08)}
.seta:disabled{opacity:.4;cursor:default;transform:none;background:none}
.indicadores{display:flex;gap:12px}
.indicadores button{width:clamp(28px,3vw,40px);height:5px;border-radius:999px;border:0;background:rgba(96,108,56,.45);cursor:pointer;padding:0;transition:background .3s,transform .3s}
.indicadores button:hover{background:rgba(96,108,56,.7)}
.indicadores button[aria-selected=true]{background:var(--oliva);transform:scaleY(1.3)}

/* Fontes */
.grade-fontes{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--gap);align-content:start}
.card-fonte{min-height:clamp(180px,30vh,320px)}
.kpi-fonte.ok{background:var(--verde);color:var(--oliva-escuro)}
.fonte-detalhe{font-size:clamp(13px,1.7vh,15px);font-weight:600;color:var(--tinta-2)}
.mini-stats{display:flex;gap:22px;margin-top:auto;padding-top:12px;border-top:1px solid var(--divisoria);flex-wrap:wrap}
.mini-stats li{display:flex;flex-direction:column}
.mini-stats b{font:italic 800 var(--t-num-3)/1 var(--sans);color:var(--tinta);letter-spacing:-.03em;font-variant-numeric:tabular-nums;padding-right:.06em}
.mini-stats span{font-size:12px;color:var(--tinta-2);font-weight:600;margin-top:4px}

/* entrada escalonada dos cards (só no modo palco) */
@media (min-width:900px){
  .secao.ativa .card{animation:entrar .6s var(--ease-out) backwards;animation-delay:calc(.14s + var(--i,0)*70ms)}
}
@media (min-width:900px) and (max-width:1199px){
  .slide-corpo.duas-colunas{columns:1}
  .grade-fontes{grid-template-columns:repeat(2,minmax(0,1fr))}
}

/* ---- telas estreitas: rolagem normal, seções empilhadas, menu simples ---- */
@media (max-width:899px){
  body{overflow:auto}
  .palco{display:block;height:auto;padding:0 12px 16px}
  .topo{position:sticky;top:0;z-index:20;height:auto;flex-wrap:wrap;background:var(--creme);padding:12px 4px;gap:10px 16px}
  .abelha{height:52px}
  .wordmark{font-size:22px}
  .wordmark .traco{height:2px}
  .favo{height:auto;flex:1 0 100%;justify-content:flex-start}
  .favo-svg{display:none !important}
  .menu-simples{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none;padding-bottom:2px;width:100%}
  .menu-simples::-webkit-scrollbar{display:none}
  .menu-simples a{flex:0 0 auto;background:var(--oliva);color:var(--marfim);text-decoration:none;font-size:13px;font-weight:700;padding:7px 14px;border-radius:999px;transition:background .2s,color .2s}
  .menu-simples a.ativa{background:var(--mel);color:var(--oliva-escuro)}
  .carimbo{margin:10px 4px 8px}
  .colmeia{overflow:visible;border-radius:28px;padding:6px 0}
  .secao{position:static;opacity:1;visibility:visible;transform:none;transition:none;overflow:visible;padding:22px 18px;scroll-margin-top:150px;gap:18px}
  .secao+.secao{border-top:1px solid rgba(40,54,24,.15)}
  .titulo-secao{font-size:clamp(36px,10vw,52px)}
  .grade-destaques{grid-template-columns:1fr;grid-template-rows:none}
  .kpi-primario{grid-column:auto}
  .kpi-numero{font-size:clamp(44px,12vw,64px)}
  .kpi-numero.menor{font-size:clamp(28px,7vw,40px)}
  .kpi-quebra{align-items:flex-start}
  .kpi-quebra .lista{justify-content:flex-start}
  .graficos{grid-template-columns:1fr}
  .grafico-caixa{height:240px !important}
  .secao-cabeca.dividida{flex-direction:column;align-items:flex-start}
  .secao-cabeca .lado{width:100%;margin-left:0;justify-content:flex-start;text-align:left;padding-bottom:0}
  .carimbo-briefing{white-space:normal;font-size:22px}
  .card-ranking{min-height:320px}
  .nomes span{max-width:110px}
  .tabela-clara{max-height:70vh}
  .slides-janela{height:min(70vh,560px)}
  .slide-corpo.duas-colunas{columns:1}
  .grade-fontes{grid-template-columns:1fr}
  .card-fonte{min-height:0}
}

/* ---- movimento reduzido: fade rápido, sem deslocamentos ---- */
@media (prefers-reduced-motion:reduce){
  .secao{transition:opacity .15s linear;transform:none !important}
  .secao.ativa{transition-delay:0s}
  .secao.ativa .card{animation:none}
  .card,.cel.nav,.pilula,.seta,.slides,.expansivel,.barra,.valor,.grafico-caixa,.indicadores button{transition:none !important}
  .cel.nav:hover,.card:hover,.pilula:hover,.seta:hover{transform:none}
}

@media print{
  body{overflow:visible;background:#fff}
  .palco{display:block;height:auto}
  .topo{height:auto}
  .abelha,.wordmark .w,.wordmark .traco{opacity:1 !important;transform:none !important}
  .favo,.slider-controles,.acoes{display:none}
  .colmeia{overflow:visible;background:none}
  .secao{position:static;opacity:1;visibility:visible;transform:none;overflow:visible;break-inside:avoid;background:var(--mel);border-radius:24px;margin-bottom:12px}
  .slides-janela{overflow:visible}
  .expansivel{height:auto !important;opacity:1}
  .slides{display:block;transform:none !important}
  html.gsap .slide{position:static;opacity:1;visibility:visible}
  .slide{height:auto;margin-bottom:12px}
  .card{break-inside:avoid}
}
"""


# --- JS: navegação por seções, menu, slider, tabela expansível ---------------
JS_UI = """
(function(){
  "use strict";
  var doc = document, win = window;
  var rm = !!(win.matchMedia && win.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var mqPalco = win.matchMedia ? win.matchMedia("(min-width: 900px)") : { matches: true };
  var secoes = Array.prototype.slice.call(doc.querySelectorAll(".secao"));
  var ids = secoes.map(function(s){ return s.id; });
  var links = Array.prototype.slice.call(doc.querySelectorAll("[data-alvo]"));
  var atual = 0, travado = false, ultimaInterna = 0;
  var palco = function(){ return !!mqPalco.matches; };
  var limitar = function(n){ return Math.max(0, Math.min(secoes.length - 1, n)); };
  var emitir = function(nome, detalhe){
    try { doc.dispatchEvent(new CustomEvent(nome, { detail: detalhe })); } catch (e) {}
  };

  function marcarMenu(id){
    links.forEach(function(a){
      var on = a.getAttribute("data-alvo") === id;
      a.classList.toggle("ativa", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  function aplicarModo(){
    secoes.forEach(function(s, i){
      if (palco()) {
        var on = i === atual;
        s.classList.toggle("ativa", on);
        s.classList.toggle("antes", i < atual);
        s.classList.toggle("depois", i > atual);
        s.inert = !on;
        if (on) s.removeAttribute("aria-hidden"); else s.setAttribute("aria-hidden", "true");
      } else {
        s.classList.add("ativa");
        s.classList.remove("antes", "depois");
        s.inert = false;
        s.removeAttribute("aria-hidden");
      }
    });
  }
  function mostrar(n, opts){
    opts = opts || {};
    n = limitar(n);
    if (n === atual && !opts.forcar) return;
    atual = n;
    aplicarModo();
    marcarMenu(ids[n]);
    if (palco() && secoes[n]) secoes[n].scrollTop = 0;
    if (!opts.semHash && win.history && win.history.replaceState) {
      try { win.history.replaceState(null, "", "#" + ids[n]); } catch (e) {}
    }
    emitir("secao:ativa", { id: ids[n], indice: n });
    travado = true;
    win.setTimeout(function(){ travado = false; }, rm ? 200 : 900);
  }
  // true se algum ancestral [data-scroll] ainda pode rolar na direção dy
  function rolavelAncestral(el, dy){
    while (el && el !== doc.body && el.nodeType === 1) {
      if (el.hasAttribute("data-scroll")) {
        if (dy > 0 ? el.scrollTop + el.clientHeight < el.scrollHeight - 1 : el.scrollTop > 0) return true;
      }
      el = el.parentNode;
    }
    return false;
  }

  // roda do mouse: uma seção por gesto (com debounce), sem mover fundo/header/container
  win.addEventListener("wheel", function(e){
    if (!palco() || e.ctrlKey) return;
    var dy = e.deltaY;
    if (e.deltaMode === 1) dy *= 16; else if (e.deltaMode === 2) dy *= 400;
    if (Math.abs(dy) < Math.abs(e.deltaX)) return;
    if (rolavelAncestral(e.target, dy)) { ultimaInterna = Date.now(); return; }
    e.preventDefault();
    if (travado || Date.now() - ultimaInterna < 550 || Math.abs(dy) < 6) return;
    mostrar(atual + (dy > 0 ? 1 : -1));
  }, { passive: false });

  // teclado: setas/PageUp/PageDown/Home/End trocam a seção; Tab segue normal
  win.addEventListener("keydown", function(e){
    if (!palco() || e.altKey || e.ctrlKey || e.metaKey) return;
    var t = e.target, tag = t && t.tagName ? t.tagName.toLowerCase() : "";
    if (tag === "input" || tag === "textarea" || tag === "select" || (t && t.isContentEditable)) return;
    var k = e.key, espaco = k === " " || k === "Spacebar";
    if (espaco && (tag === "button" || tag === "a" || tag === "summary")) return;
    if (k === "ArrowDown" || k === "PageDown" || (espaco && !e.shiftKey)) {
      if (rolavelAncestral(t, 1)) return;
      e.preventDefault(); mostrar(atual + 1);
    } else if (k === "ArrowUp" || k === "PageUp" || (espaco && e.shiftKey)) {
      if (rolavelAncestral(t, -1)) return;
      e.preventDefault(); mostrar(atual - 1);
    } else if (k === "Home") { e.preventDefault(); mostrar(0); }
    else if (k === "End") { e.preventDefault(); mostrar(secoes.length - 1); }
  });

  // toque vertical (telas touch em modo palco)
  var toqueX = null, toqueY = null;
  doc.addEventListener("touchstart", function(e){
    if (e.touches.length === 1) { toqueX = e.touches[0].clientX; toqueY = e.touches[0].clientY; }
  }, { passive: true });
  doc.addEventListener("touchend", function(e){
    if (!palco() || toqueY === null) return;
    var t = e.changedTouches[0], dy = toqueY - t.clientY, dx = toqueX - t.clientX;
    toqueX = toqueY = null;
    if (Math.abs(dy) < 60 || Math.abs(dy) < Math.abs(dx) || rolavelAncestral(e.target, dy)) return;
    mostrar(atual + (dy > 0 ? 1 : -1));
  }, { passive: true });

  links.forEach(function(a){
    a.addEventListener("click", function(e){
      var i = ids.indexOf(a.getAttribute("data-alvo"));
      if (i < 0) return;
      if (palco()) { e.preventDefault(); mostrar(i); }
      else marcarMenu(ids[i]);
    });
  });
  win.addEventListener("hashchange", function(){
    var i = ids.indexOf((location.hash || "").slice(1));
    if (i >= 0 && i !== atual) mostrar(i, { semHash: true });
  });
  if (mqPalco.addEventListener) mqPalco.addEventListener("change", aplicarModo);
  else if (mqPalco.addListener) mqPalco.addListener(aplicarModo);

  // contagem animada dos números (uma vez por elemento, quando a seção dele aparece)
  function contar(raiz){
    var fmt = function(n){ return n.toLocaleString("pt-BR"); };
    Array.prototype.forEach.call((raiz || doc).querySelectorAll("[data-n]:not([data-contado])"), function(el){
      el.setAttribute("data-contado", "1");
      var n = Number(el.getAttribute("data-n"));
      if (!isFinite(n) || rm || !win.requestAnimationFrame || n === 0) return;
      var t0 = null, dur = 900;
      var tick = function(t){
        if (t0 === null) t0 = t;
        var p = Math.min(1, (t - t0) / dur), ease = 1 - Math.pow(1 - p, 3);
        el.textContent = fmt(Math.round(n * ease));
        if (p < 1) win.requestAnimationFrame(tick); else el.textContent = fmt(n);
      };
      el.textContent = "0";
      win.requestAnimationFrame(tick);
    });
  }
  doc.addEventListener("secao:ativa", function(e){ var s = e.detail && doc.getElementById(e.detail.id); if (s) contar(s); });

  // slider do briefing
  (function(){
    var slider = doc.querySelector(".slider");
    if (!slider) return;
    var faixa = slider.querySelector(".slides");
    var slides = Array.prototype.slice.call(faixa.children);
    var setas = slider.querySelectorAll(".seta");
    var pontos = Array.prototype.slice.call(slider.querySelectorAll(".indicadores button"));
    var i = 0, n = slides.length;
    var itensTodos = Array.prototype.slice.call(slider.querySelectorAll(".kpi-rotulo, .slide-corpo > ul > li, .slide-corpo > ol > li, .slide-corpo > p"));
    var comGsap = function(){ return !rm && !!win.gsap && doc.documentElement.classList.contains("gsap"); };
    // troca "fade através da profundidade": o card atual afunda e desfoca no sentido contrário, o novo nasce um pouco
    // maior e deslocado no sentido do movimento e assenta; rótulo e tópicos do novo sobem em cascata
    function transicao(de, para, dir){
      if (!comGsap()) { faixa.style.transform = "translateX(" + (-para * 100) + "%)"; return; }
      var g = win.gsap, sai = slides[de], entra = slides[para];
      var card = entra.querySelector(".card-slide");
      if (card) card.scrollTop = 0;
      g.killTweensOf(slides); g.killTweensOf(itensTodos);
      g.set(itensTodos, { clearProps: "all" });
      g.set(slides, { clearProps: "all" });  // tudo volta ao CSS: só o .ativo fica visível
      if (de === para || !dir) return;
      var itens = Array.prototype.slice.call(entra.querySelectorAll(".kpi-rotulo, .slide-corpo > ul > li, .slide-corpo > ol > li, .slide-corpo > p")).slice(0, 10);
      g.set(sai, { visibility: "visible", opacity: 1, x: 0, scale: 1 });
      g.set(entra, { visibility: "visible", opacity: 0, x: 22 * dir, scale: 1.015, filter: "blur(2px)" });
      g.timeline()
        .to(sai, { opacity: 0, scale: .985, x: -18 * dir, filter: "blur(3px)", duration: .42, ease: "power2.in" }, 0)
        .set(sai, { clearProps: "all" })
        .to(entra, { opacity: 1, scale: 1, x: 0, filter: "blur(0px)", duration: .65, ease: "power3.out" }, .2)
        .from(itens, { y: 14, opacity: 0, duration: .55, ease: "power3.out", stagger: .045 }, .3)
        .set(entra, { clearProps: "all" })
        .set(itens, { clearProps: "all" });
    }
    function ir(k, focar){
      var anterior = i;
      i = Math.max(0, Math.min(n - 1, k));
      var dir = i > anterior ? 1 : (i < anterior ? -1 : 0);
      slides.forEach(function(s, j){
        var on = j === i;
        s.classList.toggle("ativo", on);
        s.inert = !on;
        s.setAttribute("aria-hidden", on ? "false" : "true");
      });
      pontos.forEach(function(p, j){
        p.setAttribute("aria-selected", j === i ? "true" : "false");
        p.tabIndex = j === i ? 0 : -1;
      });
      transicao(anterior, i, dir);
      if (dir && comGsap() && pontos[i]) win.gsap.fromTo(pontos[i], { scaleX: .6 }, { scaleX: 1, duration: .5, ease: "back.out(2.5)", clearProps: "transform" });
      if (setas[0]) setas[0].disabled = i === 0;
      if (setas[1]) setas[1].disabled = i === n - 1;
      if (focar && pontos[i]) pontos[i].focus();
    }
    Array.prototype.forEach.call(setas, function(b){
      b.addEventListener("click", function(){
        var d = Number(b.getAttribute("data-dir") || 1);
        if (comGsap()) win.gsap.fromTo(b, { x: 0 }, { x: 4 * d, duration: .12, yoyo: true, repeat: 1, ease: "power1.inOut", clearProps: "transform" });
        ir(i + d);
      });
    });
    pontos.forEach(function(p, j){ p.addEventListener("click", function(){ ir(j); }); });
    slider.addEventListener("keydown", function(e){
      if (e.key === "ArrowLeft") { e.preventDefault(); ir(i - 1, true); }
      else if (e.key === "ArrowRight") { e.preventDefault(); ir(i + 1, true); }
    });
    win.addEventListener("keydown", function(e){
      if (!palco() || ids[atual] !== "briefing" || slider.contains(e.target)) return;
      if (e.key === "ArrowLeft") ir(i - 1); else if (e.key === "ArrowRight") ir(i + 1);
    });
    var x0 = null, y0 = null;
    slider.addEventListener("touchstart", function(e){ x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
    slider.addEventListener("touchend", function(e){
      if (x0 === null) return;
      var dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
      x0 = y0 = null;
      if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) ir(i + (dx < 0 ? 1 : -1));
    }, { passive: true });
    ir(0);
  })();

  // "Ver dados da série": expande/recolhe a tabela com animação
  (function(){
    var btn = doc.querySelector(".pilula[aria-controls]");
    if (!btn) return;
    var alvo = doc.getElementById(btn.getAttribute("aria-controls"));
    if (!alvo) return;
    var secao = btn.closest(".secao"), texto = btn.querySelector(".pilula-texto");
    var caixas = secao ? Array.prototype.slice.call(secao.querySelectorAll(".grafico-caixa")) : [];
    alvo.inert = true;
    // anima a altura das caixas de gráfico (de/para o tamanho natural), sem pulos
    function animarCaixas(abrir){
      var alvoPx = Math.round(Math.max(140, Math.min(230, win.innerHeight * 0.2)));
      caixas.forEach(function(cx){
        var atual = cx.getBoundingClientRect().height;
        if (rm || !palco()) { cx.style.height = abrir ? alvoPx + "px" : ""; return; }
        if (abrir) cx.dataset.alturaAntes = String(Math.round(atual));
        var destino = abrir ? alvoPx : Number(cx.dataset.alturaAntes || 0);
        cx.style.transition = "none"; cx.style.height = atual + "px"; void cx.offsetHeight; cx.style.transition = "";
        cx.style.height = destino + "px";
        if (!abrir) {
          var limpar = function(){ cx.style.height = ""; cx.removeEventListener("transitionend", limpar); };
          cx.addEventListener("transitionend", limpar);
          win.setTimeout(limpar, 700);
        }
      });
    }
    var fimAuto = function(){ if (alvo.classList.contains("aberto")) alvo.style.height = "auto"; };
    alvo.addEventListener("transitionend", function(e){ if (e.propertyName === "height") fimAuto(); });
    function animarAlvo(abrir){
      if (rm) { alvo.style.height = abrir ? "auto" : "0px"; return; }
      if (abrir) {
        alvo.style.height = "0px"; void alvo.offsetHeight;
        alvo.style.height = alvo.scrollHeight + "px";
        win.setTimeout(fimAuto, 700);
      } else {
        alvo.style.height = alvo.getBoundingClientRect().height + "px"; void alvo.offsetHeight;
        alvo.style.height = "0px";
      }
    }
    btn.addEventListener("click", function(){
      var abrir = btn.getAttribute("aria-expanded") !== "true";
      btn.setAttribute("aria-expanded", abrir ? "true" : "false");
      alvo.classList.toggle("aberto", abrir);
      alvo.inert = !abrir;
      animarAlvo(abrir);
      animarCaixas(abrir);
      if (secao) secao.classList.toggle("aberto", abrir);
      if (texto) texto.textContent = abrir ? "Ocultar dados da série" : "Ver dados da série";
      if (abrir) win.setTimeout(function(){
        try { alvo.scrollIntoView({ behavior: rm ? "auto" : "smooth", block: "nearest" }); } catch (e) {}
      }, rm ? 0 : 380);
    });
  })();

  // estado inicial (hash ou primeira seção); a classe .ativa entra após o primeiro
  // quadro para a transição de entrada acontecer
  var h = ids.indexOf((location.hash || "").slice(1));
  atual = h >= 0 ? h : 0;
  secoes.forEach(function(s, i){ s.classList.toggle("antes", i < atual); s.classList.toggle("depois", i > atual); });
  var ligar = function(){
    // com #hash na URL o navegador rola o container até a âncora antes do JS assumir; devolve ao topo
    var colmeia = doc.getElementById("colmeia");
    if (colmeia) colmeia.scrollTop = 0;
    aplicarModo();
    marcarMenu(ids[atual]);
    emitir("secao:ativa", { id: ids[atual], indice: atual });
    if (!palco()) contar();
  };
  if (win.requestAnimationFrame && !rm) win.requestAnimationFrame(function(){ win.requestAnimationFrame(ligar); });
  else ligar();
  win.addEventListener("load", function(){ var c = doc.getElementById("colmeia"); if (c) c.scrollTop = 0; });
})();
"""

# --- JS: entrada do cabeçalho (abelha, letreiro e favo), hover do favo, parallax do mouse — requer GSAP ---
JS_HEADER = """
(function(){
  "use strict";
  var doc = document, win = window, raiz = doc.documentElement;
  var soltar = function(){ raiz.classList.remove("anim"); };
  var rm = !!(win.matchMedia && win.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var g = win.gsap;
  if (!g || rm) { soltar(); return; }
  raiz.classList.add("gsap");  // o CSS deixa a escala do hover por conta do GSAP
  try {
    var abelha = doc.querySelector(".abelha"), voo = doc.querySelector(".abelha-voo");
    var asaE = doc.querySelector(".abelha .asa-e"), asaD = doc.querySelector(".abelha .asa-d");
    var wordmark = doc.querySelector(".wordmark"), marca = doc.querySelector(".marca");
    var palavras = doc.querySelectorAll(".wordmark .w"), tracos = doc.querySelectorAll(".wordmark .traco");
    if (!abelha || !voo || !wordmark || !palavras.length) { soltar(); return; }
    // o CSS volta ao estado final agora; os "from" abaixo aplicam o estado inicial no mesmo quadro (sem piscar)
    soltar();

    // asas: batida curta em torno da junção com o corpo (na chegada e ao passar o mouse na marca)
    var batendo = null;
    function bater(vezes){
      if (!asaE || !asaD || (batendo && batendo.isActive())) return;
      batendo = g.timeline();
      batendo.to(asaE, { rotation: -9, svgOrigin: "48 38", duration: .07, yoyo: true, repeat: vezes * 2 - 1, ease: "sine.inOut" }, 0)
             .to(asaD, { rotation: 9, svgOrigin: "52 38", duration: .07, yoyo: true, repeat: vezes * 2 - 1, ease: "sine.inOut" }, 0)
             .set([asaE, asaD], { rotation: 0 });
    }

    var tl = g.timeline({ defaults: { ease: "expo.out" } });
    // abelha: chega da esquerda em arco, assenta e bate as asas
    tl.from(voo, { x: -140, y: 60, rotation: -22, scale: .85, opacity: 0, duration: 1.15, ease: "power3.out", svgOrigin: "50 52" }, 0)
      .add(function(){ bater(5); }, .4)
      // letreiro: cada palavra sobe e assenta; o tachado desenha da esquerda para a direita
      .from(palavras, { yPercent: 55, opacity: 0, letterSpacing: ".08em", duration: .9, stagger: .14 }, .45)
      .from(tracos, { scaleX: 0, transformOrigin: "0 50%", duration: .55, stagger: .14, ease: "power2.inOut" }, .95)
      // repouso: flutuação lenta e contínua
      .add(function(){ g.to(voo, { y: "+=4", duration: 2.6, yoyo: true, repeat: -1, ease: "sine.inOut" }); });

    // favo de mel: anima só a variante visível agora (a outra está em display:none)
    var favo = Array.prototype.filter.call(doc.querySelectorAll(".favo-svg"), function(s){ return s.getBoundingClientRect().width > 0; })[0];
    if (favo) {
      var corpo = favo.querySelector(".favo-corpo"), cels = favo.querySelectorAll(".cel"), gotas = favo.querySelectorAll(".gota");
      var atraso = function(i, el){ return parseFloat(el.getAttribute("data-d")) || 0; };
      // o conjunto assenta enquanto as células surgem em onda do centro para fora; depois as gotas crescem
      tl.from(corpo, { y: 14, rotation: -3, transformOrigin: "50% 50%", duration: 1.1, ease: "power3.out" }, .3)
        .from(cels, { scale: .55, opacity: 0, transformOrigin: "50% 50%", duration: .8, ease: "back.out(1.7)", stagger: atraso }, .3)
        .from(gotas, { scaleY: 0, opacity: 0, transformOrigin: "50% 0%", duration: 1.1, ease: "power2.inOut", stagger: .15 }, 1.0)
        .add(function(){
          // ciclo contínuo de cada gota, defasado das outras: solta o pingo, escorre de novo, cresce, espera cheia
          // (gota de borda: pingo cai longe e some; interna: pousa na célula de baixo)
          Array.prototype.forEach.call(gotas, function(gota, i){
            var fluxo = gota.querySelector(".fluxo"), bojo = gota.querySelector(".bojo"), pingo = gota.querySelector(".pingo");
            if (!fluxo || !bojo || !pingo) return;
            var interna = gota.classList.contains("interna");
            var L = fluxo.getTotalLength(), queda = interna ? 9 : 26;
            var esperaCheia = [1.6, 2.6, 1.1, 3.1, 2.1][i % 5], esperaVazia = [.9, 1.4, 1.1, .7, 1.6][i % 5];
            g.set(fluxo, { strokeDasharray: L, strokeDashoffset: L });
            g.timeline({ repeat: -1, delay: .6 + i * 1.3 })
              .to(bojo, { scaleY: 1.14, scaleX: .95, transformOrigin: "50% 0%", duration: .55, ease: "power2.in" })
              .set(pingo, { y: 0, scale: 1, opacity: 1, transformOrigin: "50% 0%" })
              .to(bojo, { scaleY: .55, scaleX: 1, transformOrigin: "50% 0%", duration: .9, ease: "elastic.out(1, .45)" }, "<")
              .to(pingo, { y: queda, duration: interna ? .38 : .75, ease: "power1.in" }, "<")
              .to(pingo, { opacity: 0, duration: interna ? .22 : .3, ease: "power1.out" }, interna ? "<.2" : "<.45")
              .fromTo(fluxo, { strokeDashoffset: L, opacity: 1 }, { strokeDashoffset: 0, duration: 1.3, ease: "power1.inOut" }, "+=" + esperaVazia)
              .to(fluxo, { opacity: 0, duration: .7, ease: "power1.out" }, ">-.2")
              .to(bojo, { scaleY: 1, scaleX: 1, transformOrigin: "50% 0%", duration: 2.6, ease: "sine.inOut" }, "<-.6")
              .to({}, { duration: esperaCheia });
          });
        });
    }

    // hover das células: a célula se eleva (escala leve + sobe 2 px) e as vizinhas recuam, sem cobrir rótulos
    var centro = function(el){ var bb = el.getBBox(); return { x: bb.x + bb.width / 2, y: bb.y + bb.height / 2, w: bb.width }; };
    var vizinhas = function(cel){
      var c = centro(cel), lista = [];
      Array.prototype.forEach.call(cel.ownerSVGElement.querySelectorAll(".cel"), function(o){
        if (o === cel) return;
        var d = centro(o);
        if (Math.hypot(d.x - c.x, d.y - c.y) < c.w * 1.25) lista.push(o);
      });
      return lista;
    };
    Array.prototype.forEach.call(doc.querySelectorAll(".favo-svg .cel.nav"), function(cel){
      cel.addEventListener("pointerenter", function(){
        g.to(cel, { scale: 1.08, y: -2, transformOrigin: "50% 50%", duration: .45, ease: "back.out(2.2)", overwrite: "auto" });
        g.to(vizinhas(cel), { scale: .965, transformOrigin: "50% 50%", duration: .5, ease: "power2.out", overwrite: "auto" });
      });
      cel.addEventListener("pointerleave", function(){
        g.to([cel].concat(vizinhas(cel)), { scale: 1, y: 0, transformOrigin: "50% 50%", duration: .55, ease: "power3.out", overwrite: "auto" });
      });
    });

    var fino = !!(win.matchMedia && win.matchMedia("(hover: hover) and (pointer: fine)").matches);
    if (marca && fino) marca.addEventListener("pointerenter", function(){ bater(4); });

    // parallax do mouse: só em modo palco e com ponteiro fino; touch e telas estreitas ficam parados
    if (fino) {
      var mq = win.matchMedia("(min-width: 900px)");
      var opc = { duration: .7, ease: "power3.out" };
      var ax = g.quickTo(abelha, "x", opc), ay = g.quickTo(abelha, "y", opc);
      var wx = g.quickTo(wordmark, "x", opc), wy = g.quickTo(wordmark, "y", opc);
      var fx = g.quickTo(".favo-svg", "x", opc), fy = g.quickTo(".favo-svg", "y", opc);  // favo em outra profundidade
      var mover = function(nx, ny){ ax(nx * 9); ay(ny * 7); wx(nx * 4); wy(ny * 3); fx(nx * -5); fy(ny * -4); };
      win.addEventListener("pointermove", function(e){
        if (!mq.matches || e.pointerType === "touch") return;
        mover((e.clientX / win.innerWidth) * 2 - 1, (e.clientY / win.innerHeight) * 2 - 1);
      }, { passive: true });
      raiz.addEventListener("mouseleave", function(){ mover(0, 0); });
      var aoMudar = function(){ if (!mq.matches) mover(0, 0); };
      if (mq.addEventListener) mq.addEventListener("change", aoMudar); else if (mq.addListener) mq.addListener(aoMudar);
    }
  } catch (e) { soltar(); }
})();
"""

# Gráficos (Chart.js embutido). __DATA__ é substituído pelo JSON inline. Os
# gráficos são criados quando a seção Evolução aparece (para animar à vista).
JS_CHARTS = """
(function(){
  "use strict";
  var D = __DATA__;
  if (typeof Chart === "undefined") return;
  var doc = document, win = window;
  var rm = !!(win.matchMedia && win.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var palco = function(){ return !win.matchMedia || win.matchMedia("(min-width: 900px)").matches; };
  var cs = getComputedStyle(doc.documentElement);
  var v = function(n, padrao){ return cs.getPropertyValue(n).trim() || padrao; };
  var azul = v("--azul", "#277fae"), rubro = v("--rubro", "#e0301e"), rubroArea = v("--rubro-area", "#a33e20"), marfim = v("--marfim", "#fefae0");
  var rgba = function(c, a){
    var r = parseInt(c.slice(1, 3), 16), g = parseInt(c.slice(3, 5), 16), b = parseInt(c.slice(5, 7), 16);
    return "rgba(" + r + "," + g + "," + b + "," + a + ")";
  };
  Chart.defaults.font.family = getComputedStyle(doc.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.font.weight = "600";
  Chart.defaults.color = "#5c6446";  // --tinta-2: ticks e rótulos sobre o card claro
  var anim = rm ? false : { duration: 900, easing: "easeOutQuart" };
  var tooltip = { backgroundColor: "#283618", titleColor: "#b8d86e", bodyColor: "#fefae0", padding: 12, cornerRadius: 12,
    displayColors: false, titleFont: { weight: "700" }, bodyFont: { size: 12 }, caretSize: 6 };
  var escalas = {
    x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8, padding: 8 } },
    y: { beginAtZero: true, grid: { color: "rgba(40,54,24,.14)", tickLength: 0 }, border: { display: false, dash: [3, 4] },
         ticks: { precision: 0, padding: 10, maxTicksLimit: 7 } }
  };
  function area(el, rotulo, dados, cor, corArea, a0, a1){
    var muitos = D.labels.length > 40;
    new Chart(el, { type: "line",
      data: { labels: D.labels, datasets: [{ label: rotulo, data: dados, borderColor: cor, fill: "origin", borderWidth: 2.5,
        backgroundColor: function(ctx){
          var ca = ctx.chart.chartArea;
          if (!ca) return rgba(corArea, a0);
          var g = ctx.chart.ctx.createLinearGradient(0, ca.top, 0, ca.bottom);
          g.addColorStop(0, rgba(corArea, a0)); g.addColorStop(1, rgba(corArea, a1));
          return g;
        },
        pointRadius: muitos ? 0 : 3.5, pointHoverRadius: 7, pointBackgroundColor: cor, pointBorderColor: marfim, pointBorderWidth: 2,
        tension: .25, spanGaps: false }] },
      options: { responsive: true, maintainAspectRatio: false, animation: anim, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: tooltip }, scales: escalas, layout: { padding: { top: 8, right: 10 } } } });
  }
  var feito = false;
  function montar(){
    if (feito) return;
    var fila = doc.getElementById("chartFila"), atend = doc.getElementById("chartAtend");
    if (!fila && !atend) return;
    feito = true;
    if (fila) area(fila, "Fila de chamados", D.fila, azul, azul, .95, .06);
    if (atend) area(atend, "Atendimentos fechados", D.atend, rubro, rubroArea, .85, .05);
  }
  doc.addEventListener("secao:ativa", function(e){ if (e.detail && e.detail.id === "evolucao") montar(); });
  // carga direta com #evolucao: o evento inicial pode ter sido emitido antes deste ouvinte existir
  var jaAtiva = doc.querySelector(".secao.ativa");
  if ((jaAtiva && jaAtiva.id === "evolucao") || (location.hash || "").slice(1) === "evolucao") montar();
  if (win.matchMedia) {  // janela redimensionada para o modo empilhado antes de visitar Evolução
    var mq = win.matchMedia("(min-width: 900px)");
    var aoMudar = function(){ if (!mq.matches) montar(); };
    if (mq.addEventListener) mq.addEventListener("change", aoMudar); else if (mq.addListener) mq.addListener(aoMudar);
  }
  if (!palco()) {
    var alvo = doc.getElementById("chartFila") || doc.getElementById("chartAtend");
    if (alvo && "IntersectionObserver" in win && !rm) {
      var io = new IntersectionObserver(function(es){ es.forEach(function(x){ if (x.isIntersecting) { montar(); io.disconnect(); } }); }, { threshold: .1 });
      io.observe(alvo);
      win.setTimeout(montar, 4000);
    } else montar();
  }
})();
"""


# --- Helpers de renderização ---------------------------------------------------
def num_html(valor) -> str:
    """Número formatado; quando inteiro, ganha data-n para a contagem animada."""
    if valor is None:
        return "—"
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return esc(valor)
    return f'<span data-n="{n}">{fmt_num(n)}</span>'


def badge_delta(atual, anterior, rotulo="vs. dia anterior", melhor="menor") -> str:
    """Comparação com o dia anterior (metricas.jsonl). `melhor` diz qual direção é boa."""
    try:
        d = int(atual) - int(anterior)
    except (TypeError, ValueError):
        return ""
    if d == 0:
        return f'<span class="badge neutro"><span aria-hidden="true">=</span><b>0</b> {esc(rotulo)}</span>'
    subiu = d > 0
    bom = (subiu and melhor == "maior") or (not subiu and melhor == "menor")
    seta = "↑" if subiu else "↓"
    return f'<span class="badge {"bom" if bom else ""}"><span aria-hidden="true">{seta}</span><b>{abs(d)}</b> {esc(rotulo)}</span>'


def etiqueta_fonte(estado: dict) -> str:
    """Etiqueta no canto do card quando a fonte está desatualizada ou indisponível (metadado, separado do delta)."""
    if estado["estado"] == "desatualizada":
        return f'<span class="kpi-fonte aviso">fonte desatualizada · coleta {esc(estado["detalhe"])}</span>'
    if estado["estado"] == "indisponivel":
        return '<span class="kpi-fonte grave">fonte indisponível</span>'
    return ""


def eixo_bonito(maximo: int) -> tuple[int, int]:
    """(topo do eixo, passo) com no máximo 8 divisões e folga à direita (38 -> 40)."""
    if maximo <= 0:
        return 5, 1
    bruto = maximo / 8
    mag = 10 ** math.floor(math.log10(bruto)) if bruto >= 1 else 1
    passo = 1
    for m in (1, 2, 5, 10):
        passo = int(m * mag)
        if maximo / passo <= 8:
            break
    topo = int(math.ceil(maximo / passo) * passo)
    if topo <= maximo:
        topo += passo
    return topo, passo


def titulo_secao(texto: str, id_: str, linhas: list[str] | None = None) -> str:
    if linhas:
        interno = "".join(f'<span class="l">{esc(l)}</span>' for l in linhas)
    else:
        interno = esc(texto)
    return f'<h2 class="titulo-secao" id="t-{id_}">{interno}</h2>'


def secao_vazia(id_: str, titulo: str, mensagem: str) -> str:
    cab = f'<header class="secao-cabeca dividida">{titulo_secao(titulo, id_)}</header>'
    return (f'<section class="secao" id="{id_}" data-scroll aria-labelledby="t-{id_}">{cab}'
            f'<p class="vazio">{mensagem}</p></section>')


def render_ranking(nomes: list, valores: list) -> str:
    vals = []
    for v in valores:
        try:
            vals.append(max(0, int(v)))
        except (TypeError, ValueError):
            vals.append(0)
    topo, passo = eixo_bonito(max(vals) if vals else 0)
    divs = max(1, topo // passo)
    nomes_html = "".join(f'<span title="{esc(n)}">{esc(n)}</span>' for n in nomes)
    linhas = "".join(
        f'<div class="linha" style="--v:{v};--i:{i}" title="{esc(n)}: {fmt_num(v)}">'
        f'<span class="barra"></span><span class="valor">{fmt_num(v)}</span></div>'
        for i, (n, v) in enumerate(zip(nomes, vals))
    )
    ticks = "".join(f'<span style="--p:{k * passo / topo:.4f}">{fmt_num(k * passo)}</span>' for k in range(divs + 1))
    return (f'<div class="ranking" style="--max:{topo};--divs:{divs};--n:{len(vals)}" role="img" '
            f'aria-label="Barras: {esc(", ".join(f"{n} {v}" for n, v in zip(nomes, vals)))}">'
            f'<div class="nomes">{nomes_html}</div>'
            f'<div class="trilhos"><div class="grade-ranking" aria-hidden="true"></div>{linhas}</div>'
            f'<div class="eixo" aria-hidden="true">{ticks}</div></div>')


# --- Montagem da página ------------------------------------------------------
def gerar_html() -> str:
    agora = datetime.now()
    historico = ler_historico()
    relatorio = ler_relatorio()
    chart_js = carregar_chart_js()
    gsap_js = carregar_gsap()
    fontes_css = carregar_fontes_css()

    fontes: dict[str, dict] = {}
    dados: dict[str, dict | None] = {}
    for chave, rotulo, arquivo in FONTES:
        d, erro = ler_json(arquivo)
        dados[chave] = d
        fontes[chave] = {"rotulo": rotulo, **status_fonte(d, erro, agora)}

    email, helpdesk, licencas = dados["email"], dados["helpdesk"], dados["licencas"]
    atend = (helpdesk or {}).get("atendimentos_ultimo_dia_util") or {}
    if not isinstance(atend, dict):
        atend = {}

    # --- registro anterior do histórico (para os badges de comparação)
    hoje_iso = date.today().isoformat()
    anterior: dict = {}
    if historico:
        anterior = historico[-2] if historico[-1].get("data") == hoje_iso and len(historico) >= 2 else (
            historico[-1] if historico[-1].get("data") != hoje_iso else {})

    # --- licenças
    lic_itens = tabela_licencas(licencas)
    n_vencendo = len((licencas or {}).get("vencendo_em_breve") or [])
    n_vencidas = len((licencas or {}).get("vencidas_recentes") or [])
    vencidas_antigas = (licencas or {}).get("vencidas_antigas_total")

    fontes_desatualizadas = [f["rotulo"] for f in fontes.values() if f["estado"] == "desatualizada"]
    fontes_indisponiveis = [f["rotulo"] for f in fontes.values() if f["estado"] == "indisponivel"]

    nomes_tecnicos = coletar_nomes_tecnicos(historico, helpdesk)

    # ================================================================ 1. DESTAQUES
    # Anatomia única: rótulo (caixa alta) + etiqueta de fonte, número, linha de julgamento (delta), rodapé de contexto.
    def cabeca_kpi(id_: str, rotulo: str, fonte: dict) -> str:
        return f'<div class="kpi-cabeca"><h3 class="kpi-rotulo" id="{id_}">{rotulo}</h3>{etiqueta_fonte(fonte)}</div>'

    if helpdesk:
        meus = helpdesk.get("meus_abertos")
        novos = helpdesk.get("meus_novos_hoje")
        por_agente = helpdesk.get("por_agente") if isinstance(helpdesk.get("por_agente"), dict) else {}
        equipe = len(por_agente) > 1
        if equipe and MOSTRAR_RANKING:
            quebra = '<span class="sep" aria-hidden="true">•</span>'.join(
                f"<span>{esc(n)} <b>{fmt_num((v or {}).get('abertos'))}</b></span>" for n, v in por_agente.items())
            lado_largo = (f'<div class="kpi-quebra" aria-label="Abertos por técnico"><span class="titulo">Por técnico</span>'
                          f'<p class="lista">{quebra}</p></div>')
        elif equipe:
            lado_largo = f'<p class="kpi-rodape"><b>{len(por_agente)}</b> agentes monitorados</p>'
        else:
            lado_largo = ""
        card_largo = f"""
<article class="card kpi kpi-primario" style="--i:0" aria-labelledby="c-abertos">
  {cabeca_kpi("c-abertos", "Chamados abertos · suporte" if equipe else "Meus chamados abertos", fontes["helpdesk"])}
  <div class="kpi-corpo">
    <div class="kpi-linha">
      <p class="kpi-numero">{num_html(meus)}</p>
      <div class="kpi-juizo">{badge_delta(meus, anterior.get("meus_abertos"), melhor="menor")}</div>
      <p class="kpi-secundario"><b>{num_html(novos)}</b> {"novo hoje" if novos == 1 else "novos hoje"}</p>
    </div>
    {lado_largo}
  </div>
</article>"""
        card_fila = f"""
<article class="card kpi" style="--i:1" aria-labelledby="c-fila">
  {cabeca_kpi("c-fila", "Fila de chamados", fontes["helpdesk"])}
  <p class="kpi-numero">{num_html(helpdesk.get("fila_total_abertos"))}</p>
  <div class="kpi-juizo">{badge_delta(helpdesk.get("fila_total_abertos"), anterior.get("fila_abertos"), melhor="menor")}</div>
  <p class="kpi-rodape">chamados abertos no help desk</p>
</article>"""
        card_atend = f"""
<article class="card kpi" style="--i:2" aria-labelledby="c-atend">
  {cabeca_kpi("c-atend", "Atendimentos fechados", fontes["helpdesk"])}
  <p class="kpi-numero">{num_html(atend.get("total_atendimentos_fechados"))}</p>
  <div class="kpi-juizo">{badge_delta(atend.get("total_atendimentos_fechados"), anterior.get("atend_total"), melhor="maior")}</div>
  <p class="kpi-rodape">último dia útil · <b>{esc(atend.get("dia") or "—")}</b></p>
</article>"""
    else:
        card_largo = f"""
<article class="card kpi kpi-primario" style="--i:0" aria-labelledby="c-abertos">
  {cabeca_kpi("c-abertos", "Chamados abertos · suporte", fontes["helpdesk"])}
  <div class="kpi-linha"><p class="kpi-numero"><span class="sem-dado">—</span></p><p class="kpi-vazio">Sem dados do help desk nesta geração.</p></div>
</article>"""
        card_fila = f"""
<article class="card kpi" style="--i:1" aria-labelledby="c-fila">
  {cabeca_kpi("c-fila", "Fila de chamados", fontes["helpdesk"])}
  <p class="kpi-numero"><span class="sem-dado">—</span></p>
  <p class="kpi-rodape">chamados abertos no help desk</p>
</article>"""
        card_atend = f"""
<article class="card kpi" style="--i:2" aria-labelledby="c-atend">
  {cabeca_kpi("c-atend", "Atendimentos fechados", fontes["helpdesk"])}
  <p class="kpi-numero"><span class="sem-dado">—</span></p>
  <p class="kpi-rodape">último dia útil · —</p>
</article>"""

    par_lic = ('<div class="kpi-par">'
               '<div class="kpi-medida"><p class="kpi-numero">{vencidas}</p><p class="kpi-legenda">vencidas recentes</p></div>'
               '<div class="kpi-medida"><p class="kpi-numero menor">{vencendo}</p><p class="kpi-legenda">vencendo em breve</p></div>'
               '</div>')
    if licencas:
        rodape_lic = (f'<p class="kpi-rodape"><b>{fmt_num(vencidas_antigas)}</b> vencidas há mais tempo (fora da janela)</p>'
                      if vencidas_antigas not in (None, "") else "")
        card_lic = f"""
<article class="card kpi kpi-dupla" style="--i:3" aria-labelledby="c-lic">
  {cabeca_kpi("c-lic", "Licenças", fontes["licencas"])}
  {par_lic.format(vencidas=num_html(n_vencidas), vencendo=num_html(n_vencendo))}
  <div class="kpi-juizo">{badge_delta(n_vencidas, anterior.get("lic_vencidas_recentes"), melhor="menor")}</div>
  {rodape_lic}
</article>"""
    else:
        card_lic = f"""
<article class="card kpi kpi-dupla" style="--i:3" aria-labelledby="c-lic">
  {cabeca_kpi("c-lic", "Licenças", fontes["licencas"])}
  {par_lic.format(vencidas='<span class="sem-dado">—</span>', vencendo='<span class="sem-dado">—</span>')}
</article>"""

    secao_destaques = f"""
<section class="secao" id="destaques" data-scroll aria-labelledby="t-destaques">
  <div class="grade-destaques">
    {titulo_secao("Destaques do dia", "destaques", ["Destaques", "Do Dia"])}
    {card_largo}{card_fila}{card_atend}{card_lic}
  </div>
</section>"""

    # ================================================================ 2. EVOLUÇÃO
    serie = serie_historico(historico, DIAS_GRAFICO)
    rank = ranking_semanal(historico)
    n_dias = len(serie["labels"])
    if not n_dias:
        secao_evolucao = secao_vazia("evolucao", "Evolução", "Histórico indisponível (historico/metricas.jsonl vazio ou ausente).")
    else:
        def ultimos(vals: list) -> tuple:
            v = [x for x in vals if x is not None]
            return (v[-1] if v else None, v[-2] if len(v) > 1 else None)
        fila_ult, fila_ant = ultimos(serie["fila"])
        atend_ult, atend_ant = ultimos(serie["atend"])
        data_ult = label_dia(serie["datas"][-1]) if serie["datas"] else "—"
        if chart_js is None:
            graficos = ('<p class="vazio">Gráficos indisponíveis nesta geração (biblioteca de gráficos não encontrada). '
                        'Os dados seguem na tabela abaixo.</p>')
        else:
            graficos = f"""
<div class="graficos">
  <article class="card card-grafico" style="--i:0">
    <div class="kpi-cabeca"><h3 class="kpi-rotulo">Fila de chamados abertos</h3></div>
    <div class="kpi-linha"><p class="kpi-numero menor">{num_html(fila_ult)}</p><div class="kpi-juizo">{badge_delta(fila_ult, fila_ant, "vs. registro anterior", melhor="menor")}</div><p class="kpi-legenda">último registro · {esc(data_ult)}</p></div>
    <div class="grafico-caixa"><canvas id="chartFila" role="img" aria-label="Evolução da fila de chamados abertos"></canvas></div>
  </article>
  <article class="card card-grafico" style="--i:1">
    <div class="kpi-cabeca"><h3 class="kpi-rotulo">Atendimentos fechados por dia</h3></div>
    <div class="kpi-linha"><p class="kpi-numero menor">{num_html(atend_ult)}</p><div class="kpi-juizo">{badge_delta(atend_ult, atend_ant, "vs. registro anterior", melhor="maior")}</div><p class="kpi-legenda">último dia útil registrado</p></div>
    <div class="grafico-caixa"><canvas id="chartAtend" role="img" aria-label="Evolução de atendimentos fechados por dia"></canvas></div>
  </article>
</div>"""
        linhas_serie = "".join(
            f'<tr><td>{esc(label_dia(d))}</td><td class="c">{fmt_num(f)}</td><td class="d">{fmt_num(a)}</td></tr>'
            for d, f, a in list(zip(serie["datas"], serie["fila"], serie["atend"]))[::-1]
        )
        secao_evolucao = f"""
<section class="secao" id="evolucao" data-scroll aria-labelledby="t-evolucao">
  <header class="secao-cabeca dividida">{titulo_secao("Evolução", "evolucao")}<p class="lado subtitulo">últimos {DIAS_GRAFICO} dias · {n_dias} registrado(s)</p></header>
  {graficos}
  <div class="serie">
  <div class="acoes"><button class="pilula" type="button" aria-expanded="false" aria-controls="serie-dados"><span class="pilula-texto">Ver dados da série</span>{CHEVRON_SVG}</button></div>
  <div class="expansivel" id="serie-dados"><div><div class="expansivel-pad">
    <table class="tabela-serie"><caption class="sr-only">Dados da série: fila de chamados e atendimentos fechados por dia</caption>
      <thead><tr><th scope="col">Data</th><th scope="col" class="c">Fila de chamados</th><th scope="col" class="d">Atendimentos fechados</th></tr></thead>
      <tbody>{linhas_serie}</tbody></table>
  </div></div></div>
  </div>
</section>"""

    # ================================================================ 3. EFICÁCIA
    if MOSTRAR_RANKING and rank["tecnicos"]:
        titulo_rank, nomes_rank, valores_rank = "Ranking Semanal por Técnico", rank["tecnicos"], rank["valores"]
    elif rank["dias"]:
        titulo_rank, nomes_rank, valores_rank = "Atendimentos da equipe por dia", rank["dias"], rank["totais"]
    else:
        titulo_rank, nomes_rank, valores_rank = "", [], []
    if not nomes_rank:
        secao_eficacia = secao_vazia("eficacia", "Eficácia", "Sem registros de atendimentos no histórico.")
    else:
        dias_txt = ", ".join(rank["dias"])
        nota_rank = "" if MOSTRAR_RANKING else " · ranking por técnico desativado"
        secao_eficacia = f"""
<section class="secao" id="eficacia" data-scroll aria-labelledby="t-eficacia">
  <header class="secao-cabeca dividida">
    {titulo_secao("Eficácia", "eficacia")}
    <div class="lado"><div class="kpi-medida" title="{esc(dias_txt)}"><p class="kpi-numero menor">{num_html(rank["total_periodo"])}</p><p class="kpi-legenda">atendimentos fechados em {len(rank["dias"])} dia(s) útil(eis)</p></div></div>
  </header>
  <article class="card card-ranking" style="--i:0" data-scroll>
    <div class="kpi-cabeca"><h3 class="kpi-rotulo">{esc(titulo_rank)}</h3><p class="kpi-legenda">soma dos últimos {len(rank["dias"])} dia(s) útil(eis) registrado(s){esc(nota_rank)}</p></div>
    {render_ranking(nomes_rank, valores_rank)}
  </article>
</section>"""

    # ================================================================ 4. LICENÇAS
    if licencas is None:
        secao_licencas = secao_vazia("licencas", "Licenças", "Fonte indisponível: licenças.")
    elif not lic_itens:
        secao_licencas = secao_vazia("licencas", "Licenças", "Nenhuma licença vencida recentemente ou vencendo em breve.")
    else:
        linhas = []
        for it in lic_itens:
            cls, _ = classificar_licenca(it)
            vencida = cls == "st-critico"
            d = it["dias"]
            if d is None:
                prazo = "—"
            elif d < 0:
                prazo = f"há {abs(d)} d"
            elif d == 0:
                prazo = "hoje"
            else:
                prazo = f"em {d} d"
            urgente = " urgente" if (not vencida and d is not None and d <= 7) else ""
            linhas.append(
                f'<tr><td><span class="pill {"vencida" if vencida else "vencendo"}">{"Vencida" if vencida else "Vencendo"}</span></td>'
                f'<td>{esc(it["cliente"])}</td><td>{esc(it["sistema"])}</td>'
                f'<td class="num">{esc(it["vencimento"])}</td><td class="num{urgente}">{prazo}</td></tr>'
            )
        sintese = ('<div class="lado">'
                   f'<div class="kpi-medida"><p class="kpi-numero menor">{num_html(n_vencidas)}</p><p class="kpi-legenda">vencidas recentes</p></div>'
                   f'<div class="kpi-medida"><p class="kpi-numero menor">{num_html(n_vencendo)}</p><p class="kpi-legenda">vencendo em breve</p></div>'
                   f'<div class="kpi-medida"><p class="kpi-numero menor">{num_html(vencidas_antigas)}</p><p class="kpi-legenda">vencidas há mais tempo, fora da lista</p></div>'
                   '</div>')
        secao_licencas = f"""
<section class="secao" id="licencas" aria-labelledby="t-licencas">
  <header class="secao-cabeca dividida">{titulo_secao("Licenças", "licencas")}{sintese}</header>
  <div class="tabela-clara" data-scroll tabindex="0" role="region" aria-label="Tabela de licenças em risco">
    <table class="tabela-lic"><caption class="sr-only">Licenças vencidas recentemente e vencendo em breve, por urgência</caption>
      <thead><tr><th scope="col">Status</th><th scope="col">Cliente</th><th scope="col">Sistema</th><th scope="col" class="num">Vencimento</th><th scope="col" class="num">Prazo</th></tr></thead>
      <tbody>{''.join(linhas)}</tbody></table>
  </div>
</section>"""

    # ================================================================ 5. BRIEFING
    if relatorio is None:
        secao_briefing = secao_vazia("briefing", "Briefing do Dia", "Briefing indisponível (relatorio.md ausente ou vazio).")
    else:
        texto = relatorio if MOSTRAR_RANKING else redigir_nomes(relatorio, nomes_tecnicos)
        titulo_h1, slides = dividir_briefing(texto)
        data_brief = data_do_briefing(titulo_h1) or agora.strftime("%d/%m/%Y %H:%M")
        if not slides:
            slides = [{"titulo": "Briefing", "md": texto}]
        itens_slides, pontos = [], []
        for i, s in enumerate(slides):
            corpo_md = s["md"]
            n_itens = len(re.findall(r"^\s*[-*+]\s+", corpo_md, flags=re.M))
            duas = " duas-colunas" if (n_itens >= 5 or len(corpo_md) > 650) and not re.search(r"^\s*\d+[.)]\s+", corpo_md, flags=re.M) else ""
            corpo = markdown_para_html(corpo_md, base=4) or "<p>—</p>"
            itens_slides.append(
                f'<li class="slide" role="group" aria-roledescription="slide" aria-label="{i + 1} de {len(slides)}: {esc(s["titulo"])}">'
                f'<article class="card card-slide" data-scroll><h3 class="kpi-rotulo">{esc(s["titulo"])}</h3><div class="slide-corpo{duas}">{corpo}</div></article></li>'
            )
            pontos.append(f'<button type="button" role="tab" aria-selected="{"true" if i == 0 else "false"}" aria-label="{esc(s["titulo"])}"></button>')
        secao_briefing = f"""
<section class="secao" id="briefing" aria-labelledby="t-briefing">
  <header class="secao-cabeca dividida">{titulo_secao("Briefing do Dia", "briefing")}<p class="lado carimbo-briefing">{esc(data_brief)}</p></header>
  <div class="slider" aria-roledescription="carrossel" aria-label="Tópicos do briefing">
    <div class="slides-janela"><ul class="slides">{''.join(itens_slides)}</ul></div>
    <div class="slider-controles">
      <button class="seta" type="button" data-dir="-1" aria-label="Tópico anterior">‹</button>
      <div class="indicadores" role="tablist" aria-label="Tópicos">{''.join(pontos)}</div>
      <button class="seta" type="button" data-dir="1" aria-label="Próximo tópico">›</button>
    </div>
  </div>
</section>"""

    # ================================================================ 6. FONTES
    rotulo_estado = {"ok": "Atualizada", "desatualizada": "Desatualizada", "indisponivel": "Indisponível"}
    classe_estado = {"ok": "ok", "desatualizada": "aviso", "indisponivel": "grave"}
    cards_fontes = []
    for i, (chave, f) in enumerate(fontes.items()):
        estado = f["estado"]
        d = dados[chave] or {}
        stats = ""
        if chave == "email" and d:
            stats = (f'<ul class="mini-stats"><li><b>{fmt_num(d.get("nao_lidos"))}</b><span>não lidos</span></li>'
                     f'<li><b>{fmt_num(d.get("recebidos_hoje"))}</b><span>recebidos hoje</span></li>'
                     f'<li><b>{fmt_num(d.get("spam_hoje"))}</b><span>spam hoje</span></li></ul>')
        elif chave == "helpdesk" and d:
            agentes = d.get("agentes_monitorados") if isinstance(d.get("agentes_monitorados"), list) else []
            stats = (f'<ul class="mini-stats"><li><b>{fmt_num(d.get("fila_total_abertos"))}</b><span>na fila</span></li>'
                     f'<li><b>{fmt_num(d.get("meus_abertos"))}</b><span>abertos da equipe</span></li>'
                     f'<li><b>{fmt_num(len(agentes)) if agentes else "—"}</b><span>agentes monitorados</span></li></ul>')
        elif chave == "licencas" and d:
            stats = (f'<ul class="mini-stats"><li><b>{fmt_num(n_vencendo)}</b><span>vencendo</span></li>'
                     f'<li><b>{fmt_num(n_vencidas)}</b><span>vencidas recentes</span></li>'
                     f'<li><b>{fmt_num(d.get("ignoradas_homolog_teste"))}</b><span>homolog./teste ignoradas</span></li></ul>')
        detalhe = {"ok": "dentro do limite de 24 h", "desatualizada": f"coleta {esc(f['detalhe'])} · limite de 24 h",
                   "indisponivel": esc(f["detalhe"])}[estado]
        dt = f["coletado_em"]
        hora = esc(dt.strftime("%H:%M")) if dt else '<span class="sem-dado">—</span>'
        data_coleta = esc(dt.strftime("%d/%m/%Y")) if dt else "—"
        cards_fontes.append(f"""
<article class="card card-fonte kpi" style="--i:{i}" aria-labelledby="f-{chave}">
  <div class="kpi-cabeca"><h3 class="kpi-rotulo" id="f-{chave}">{esc(f["rotulo"])}</h3><span class="kpi-fonte {classe_estado[estado]}">{rotulo_estado[estado]}</span></div>
  <p class="kpi-numero menor">{hora}</p>
  <p class="kpi-legenda">coletado em {data_coleta}</p>
  <p class="fonte-detalhe">{detalhe}</p>
  {stats}
</article>""")
    secao_fontes = f"""
<section class="secao" id="fontes" data-scroll aria-labelledby="t-fontes">
  <header class="secao-cabeca dividida">{titulo_secao("Fontes", "fontes")}<p class="lado subtitulo">{sum(1 for f in fontes.values() if f["estado"] == "ok")} de {len(fontes)} fontes atualizadas · coleta de hoje</p></header>
  <div class="grade-fontes">{''.join(cards_fontes)}</div>
  <p class="nota-escura">Arquivo estático gerado por gerar_dashboard.py · sem dependências externas · pode ser copiado sozinho.</p>
</section>"""

    # ================================================================ cabeçalho
    gerado_em = agora.strftime("%d/%m/%Y %H:%M")
    data_dados = date.today().strftime("%d/%m/%Y")
    avisos = ""
    if fontes_desatualizadas:
        avisos += f'<a class="aviso" href="#fontes" data-alvo="fontes">{len(fontes_desatualizadas)} fonte(s) desatualizada(s)</a>'
    if fontes_indisponiveis:
        avisos += f'<a class="aviso grave" href="#fontes" data-alvo="fontes">{len(fontes_indisponiveis)} fonte(s) indisponível(is)</a>'

    favo_cheio, _ = favo_svg(FAVO_CHEIO, passo=63, fonte=12, classe="favo-cheio")
    favo_compacto, _ = favo_svg(FAVO_COMPACTO, passo=66, fonte=12, classe="favo-compacto")
    menu_simples = "".join(f'<a href="#{id_}" data-alvo="{id_}">{esc(rotulo)}</a>' for id_, rotulo in FAVO_ORDEM)

    payload = {"labels": serie["labels"], "fila": serie["fila"], "atend": serie["atend"]}
    script = f"<script>{JS_UI}</script>"
    if gsap_js is not None:
        script += f"\n<script>{gsap_js}</script>\n<script>{JS_HEADER}</script>"
    # marca html.anim antes da primeira pintura (só quando há GSAP e sem movimento reduzido)
    marcador_anim = (
        '<script>(function(){try{if(!(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches))'
        'document.documentElement.classList.add("anim")}catch(e){}})();</script>'
    ) if gsap_js is not None else ""
    if chart_js is not None and n_dias:
        script += f"\n<script>{chart_js}</script>\n<script>{JS_CHARTS.replace('__DATA__', json_inline(payload))}</script>"

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Briefing Diário — {esc(data_dados)}</title>
<style>{fontes_css}{CSS}</style>
{marcador_anim}
</head>
<body>
<a class="pular" href="#destaques">Ir para o conteúdo</a>
<div class="palco">
<header class="topo">
  <a class="marca" href="#destaques" data-alvo="destaques" aria-label="Briefing Diário — início">
    {ABELHA_SVG}
    <h1 class="wordmark"><span class="w" style="--traco-w:50%">Briefing{TRACO_SVG}</span><span class="w">Diário{TRACO_SVG}</span></h1>
  </a>
  <nav class="favo" aria-label="Seções do briefing">
    {favo_cheio}
    {favo_compacto}
    <div class="menu-simples">{menu_simples}</div>
  </nav>
</header>
<p class="carimbo"><span>Gerado em <time datetime="{agora.strftime('%Y-%m-%dT%H:%M')}">{esc(gerado_em)}</time></span><span class="sep" aria-hidden="true">•</span><span>Dados de {esc(data_dados)}</span>{avisos}</p>
<main class="colmeia" id="colmeia">
{secao_destaques}
{secao_briefing}
{secao_licencas}
{secao_evolucao}
{secao_eficacia}
{secao_fontes}
</main>
</div>
{script}
</body>
</html>
"""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    try:
        conteudo = gerar_html()
    except Exception as e:  # último recurso: nunca deixar o briefing sem dashboard
        print(f"ERRO ao montar dashboard: {type(e).__name__}: {e}")
        conteudo = (
            "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'><title>Briefing diário</title></head>"
            f"<body><h1>Briefing diário</h1><p>Falha ao gerar o dashboard: {esc(type(e).__name__)}</p></body></html>"
        )
    SAIDA.write_text(conteudo, encoding="utf-8")
    print(f"OK -> {SAIDA} ({len(conteudo.encode('utf-8')) // 1024} KB, ranking={'on' if MOSTRAR_RANKING else 'off'}, dias={DIAS_GRAFICO})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
