"""Base compartilhada dos dashboards (diario, semanal, diretor, financeiro).

Guarda o que nao pertence a um briefing especifico: identidade visual "favo de
mel" (CSS, JS, SVGs, fontes), leitura tolerante a falhas de dados/ e do
historico, conversor de markdown, helpers de formatacao e os componentes de
render (badge, ranking, tabela de licencas, titulo de secao).

Quem consome: gerar_dashboard.py, gerar_dashboard_semanal.py e os geradores das
paginas por publico. Mudou o design aqui, mudou em todas -- rode todos os
geradores depois de mexer.

Nada aqui pode lancar excecao por dado ausente: a secao degrada, a pagina sai.

Configuracao (.env): DASHBOARD_MOSTRAR_RANKING.
"""

from __future__ import annotations

import base64
import html
import json
import math
import os
import re
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent
DADOS = RAIZ / "dados"
HISTORICO = RAIZ / "historico" / "metricas.jsonl"
ASSETS = RAIZ / "assets"
CHART_JS = ASSETS / "chart.min.js"
GSAP_JS = ASSETS / "gsap.min.js"

# Baixado UMA vez para assets/; depois disso o dashboard não depende de rede.
CHART_JS_URL = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"
GSAP_JS_URL = "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.15.0/gsap.min.js"
LIMITE_DESATUALIZADA = timedelta(hours=24)

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


def celulas_md(linha: str) -> list[str]:
    """Células de uma linha de tabela markdown ('| a | b |'); '\\|' é barra literal."""
    miolo = linha.strip()
    if miolo.startswith("|"):
        miolo = miolo[1:]
    if miolo.endswith("|") and not miolo.endswith("\\|"):
        miolo = miolo[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", miolo)]


def tabela_md_para_html(linhas: list[str]) -> str:
    """Tabela markdown (GFM): cabeçalho quando a 2ª linha é separadora (---, :--, --:, :-:)."""
    def separadora(cels: list[str]) -> bool:
        return bool(cels) and all(re.fullmatch(r":?-+:?", c) for c in cels)

    grade = [celulas_md(l) for l in linhas]
    cabeca: list[str] | None = None
    alinhamentos: list[str] = []
    if len(grade) >= 2 and separadora(grade[1]):
        cabeca = grade[0]
        alinhamentos = ["al-c" if c.startswith(":") and c.endswith(":") else "al-d" if c.endswith(":") else ""
                        for c in grade[1]]
        grade = grade[2:]
    corpo = [cels for cels in grade if not separadora(cels)]
    if cabeca is None and not corpo:
        return ""
    colunas = max(len(cels) for cels in ([cabeca] if cabeca else []) + corpo)

    def classe(i: int) -> str:
        a = alinhamentos[i] if i < len(alinhamentos) else ""
        return f' class="{a}"' if a else ""

    def completar(cels: list[str]) -> list[str]:
        return cels + [""] * (colunas - len(cels))

    partes = ['<div class="tabela-md"><table>']
    if cabeca:
        partes.append("<thead><tr>" + "".join(
            f'<th scope="col"{classe(i)}>{inline_md(c)}</th>' for i, c in enumerate(completar(cabeca))) + "</tr></thead>")
    partes.append("<tbody>" + "".join(
        "<tr>" + "".join(f"<td{classe(i)}>{inline_md(c)}</td>" for i, c in enumerate(completar(cels))) + "</tr>"
        for cels in corpo) + "</tbody>")
    partes.append("</table></div>")
    return "".join(partes)


def markdown_para_html(md: str, base: int = 3) -> str:
    """Converte um trecho de markdown: títulos (a partir de h{base}), parágrafos,
    listas com sub-itens (por indentação), linhas de continuação e tabelas."""
    saida: list[str] = []
    pilha: list[tuple[int, str]] = []  # (indentação, "ul"|"ol") das listas abertas; o <li> fica aberto
    paragrafo: list[str] = []
    tabela: list[str] = []  # linhas '| ... |' consecutivas

    def fechar_paragrafo():
        nonlocal paragrafo
        if paragrafo:
            saida.append("<p>" + " ".join(paragrafo) + "</p>")
            paragrafo = []

    def fechar_listas(ate_indent: int = -1):
        while pilha and pilha[-1][0] > ate_indent:
            _, tag = pilha.pop()
            saida.append(f"</li></{tag}>")

    def fechar_tabela():
        nonlocal tabela
        if tabela:
            saida.append(tabela_md_para_html(tabela))
            tabela = []

    for linha in md.splitlines():
        bruto = linha.rstrip()
        if re.match(r"^\s*\|.*\|$", bruto):
            fechar_paragrafo()
            fechar_listas()
            tabela.append(bruto)
            continue
        fechar_tabela()
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

    fechar_tabela()
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
    """Todo nome de pessoa que aparece nos dados -- base para esconder quando preciso.

    Junta técnicos de atendimento, agentes monitorados e desenvolvedores. É
    deliberadamente abrangente: é melhor esconder um nome a mais do que deixar
    vazar um a menos na página de quem não deve vê-los.
    """
    nomes: set[str] = set()
    for r in historico:
        for chave in ("atend_por_tecnico", "dev_por_pessoa"):
            pt = r.get(chave)
            if isinstance(pt, dict):
                nomes.update(str(k) for k in pt)
    if helpdesk:
        pt = (helpdesk.get("atendimentos_ultimo_dia_util") or {}).get("por_tecnico")
        if isinstance(pt, dict):
            nomes.update(str(k) for k in pt)
        for item in helpdesk.get("contagem_por_tecnico") or []:
            if isinstance(item, dict) and item.get("agent"):
                nomes.add(str(item["agent"]))
        if isinstance(helpdesk.get("por_agente"), dict):
            nomes.update(str(k) for k in helpdesk["por_agente"])
        for nome in helpdesk.get("agentes_monitorados") or []:
            nomes.add(str(nome))
        desenvolvimento = helpdesk.get("desenvolvimento")
        if isinstance(desenvolvimento, dict):
            if isinstance(desenvolvimento.get("por_dev"), dict):
                nomes.update(str(k) for k in desenvolvimento["por_dev"])
            for nome in desenvolvimento.get("devs_monitorados") or []:
                nomes.add(str(nome))
        fila = helpdesk.get("fila")
        if isinstance(fila, dict) and isinstance(fila.get("por_tecnico"), dict):
            nomes.update(str(k) for k in fila["por_tecnico"])
    return {n for n in nomes if n.strip() and not n.startswith("(")}


def redigir_nomes(texto: str, nomes: set[str]) -> str:
    """Substitui nomes de técnicos no briefing quando o ranking está desativado."""
    for nome in sorted(nomes, key=len, reverse=True):
        texto = re.sub(re.escape(nome), "[técnico]", texto, flags=re.IGNORECASE)
        primeiro = nome.split()[0]
        if len(primeiro) >= 4:
            texto = re.sub(rf"\b{re.escape(primeiro)}\b", "[técnico]", texto, flags=re.IGNORECASE)
    return texto


# Campos do histórico que viram série temporal. Chave da série -> campo em
# metricas.jsonl. Linha antiga sem o campo devolve None e o gráfico abre buraco.
SERIES_HISTORICO = {
    "fila": "fila_abertos",
    "atend": "atend_total",
    "equipe": "meus_abertos",
    "site": "fila_site",
    "siscam9": "fila_siscam9",
    "siscam8": "fila_siscam8",
    "corretivo": "fila_corretivo",
    "evolutivo": "fila_evolutivo",
    "idade_90": "fila_mais_90",
    "dev": "dev_atribuidos",
    "dev_status": "dev_em_status",
    "email_nao_lidos": "email_nao_lidos",
    "lic_vencendo": "lic_vencendo",
    "lic_vencidas": "lic_vencidas_recentes",
}


def serie_historico(historico: list[dict], dias: int) -> dict:
    """Séries diárias do histórico. Campo ausente na linha vira None (buraco no gráfico)."""
    recorte = historico[-dias:]
    serie = {
        "labels": [label_dia(r["data"]) for r in recorte],
        "datas": [r["data"] for r in recorte],
    }
    for chave, campo in SERIES_HISTORICO.items():
        serie[chave] = [r.get(campo) for r in recorte]
    return serie


def serie_tem_dado(serie: dict, chave: str) -> bool:
    """True quando a série tem pelo menos um valor não nulo -- senão não vale gráfico."""
    return any(v is not None for v in serie.get(chave) or [])


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
FAVO_CHEIO = [(-2, 1), (-2, 2), (-1, 0), (-1, 1), (-1, 2), (0, -2), (0, -1), (0, 0), (0, 1),
              (1, -2), (1, -1), (1, 0), (1, 1), (2, -3), (2, -2), (2, -1), (3, -2), (3, -1)]
FAVO_COMPACTO = [(0, -1), (1, -1), (-1, 0), (0, 0), (1, 0), (-1, 1), (0, 1), (2, -1), (-2, 1)]
FAVO_ANGULO = 44.75


def favo_svg(celulas: list[tuple[int, int]], passo: float, fonte: float, classe: str,
             nav: dict[tuple[int, int], str],
             ordem: list[tuple[str, str]]) -> tuple[str, float]:
    """SVG do favo. Devolve (svg, altura em px na escala 1:1).
    nav/ordem sao as seções do menu: cada dashboard passa as suas (obrigatorio, para nenhuma
    pagina herdar em silêncio o menu de outra)."""
    mapa_nav = nav  # "nav" é reutilizado abaixo como lista das células
    rotulos = dict(ordem)
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

    decorativas = sorted((c for c in centros if mapa_nav.get(c) is None), key=lambda c: centros[c][0])
    borda = [c for c in decorativas if not sob_vertice(c)]
    internas = [c for c in decorativas if sob_vertice(c) and all(mapa_nav.get(o) is None for o in sob_vertice(c))]

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
        alvo = mapa_nav.get(c)
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
/* SECAO ROLAVEL -- o conteudo manda na altura e a secao rola.
   .secao ja tem overflow:auto; o que faltava era os blocos pararem de encolher.
   Item flex nasce com flex-shrink:1, entao numa secao cheia o navegador
   comprimia os cards ate o texto quebrar dentro deles, em vez de deixar a
   secao rolar. Aqui bloco-elastico passa a valer "altura natural".
   As classes do contrato continuam as mesmas (testes/test_layout.py). */
.secao.rolavel>.bloco-elastico{flex:0 0 auto;min-height:auto}
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
.grade-destaques{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));grid-template-rows:auto 1fr;gap:var(--gap)}
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
.graficos{display:grid;grid-template-columns:1fr 1fr;gap:var(--gap)}
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
/* tabelas markdown dentro do slide: mesma linguagem das tabelas claras, com borda (card e tabela têm o mesmo fundo) */
.slide-corpo .tabela-md{overflow-x:auto;margin:2px 0 12px;border:1px solid var(--tabela-linha);border-radius:16px;break-inside:avoid;scrollbar-width:thin}
.slide-corpo table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.slide-corpo th{background:var(--tabela-cabeca);color:var(--tabela-muted);font-size:11.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;text-align:left;padding:10px 16px;border-bottom:1px solid var(--tabela-linha);white-space:nowrap}
.slide-corpo td{padding:9px 16px;border-top:1px solid var(--tabela-linha);color:var(--tabela-ink)}
.slide-corpo tbody tr:first-child td{border-top:0}
.slide-corpo .al-c{text-align:center}
.slide-corpo .al-d{text-align:right}
.slider-controles{flex:0 0 auto;display:flex;align-items:center;justify-content:center;gap:18px}
.seta{background:none;border:0;color:var(--oliva);font:400 34px/1 var(--serif);cursor:pointer;padding:2px 12px;border-radius:12px;transition:transform .2s var(--ease),opacity .2s,background .2s}
.seta:hover{transform:scale(1.15);background:rgba(40,54,24,.08)}
.seta:disabled{opacity:.4;cursor:default;transform:none;background:none}
.indicadores{display:flex;gap:12px}
.indicadores button{width:clamp(28px,3vw,40px);height:5px;border-radius:999px;border:0;background:rgba(96,108,56,.45);cursor:pointer;padding:0;transition:background .3s,transform .3s}
.indicadores button:hover{background:rgba(96,108,56,.7)}
.indicadores button[aria-selected=true]{background:var(--oliva);transform:scaleY(1.3)}

/* Fontes */
.grade-fontes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--gap);align-content:start}
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

/* ---- explicitação da fonte: toda métrica diz de onde vem e o que conta ---- */
.tag-fonte{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;letter-spacing:.03em;
  text-transform:uppercase;padding:2px 9px;border-radius:999px;background:var(--neutro-bg);color:var(--tinta-2);white-space:nowrap}
.tag-fonte::before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.7}
.tag-fonte.milldesk{background:#dcebf3;color:#1d5f80}
.tag-fonte.imap{background:#e9e4f4;color:#4b3f75}
.tag-fonte.licencas{background:var(--ambar-bg);color:var(--ambar-ink)}
.tag-fonte.gcal{background:#e2efe4;color:#2f5d3a}
.kpi-explica{margin-top:10px;padding-top:9px;border-top:1px solid var(--divisoria);
  font-size:var(--t-meta);line-height:1.45;color:var(--tinta-2)}
.kpi-explica b{color:var(--tinta);font-weight:700}
.kpi-explica .como{display:block;margin-top:3px;opacity:.85}
.secao-nota{margin:2px 0 14px;font-size:var(--t-meta);line-height:1.5;color:var(--carimbo);max-width:78ch}
.secao-nota .tag-fonte{margin-right:6px;vertical-align:1px}

/* ---- barras de distribuição (fila por sistema, por status, por dev) ---- */
.dist{display:grid;gap:9px;margin-top:14px}
.dist-linha{display:grid;grid-template-columns:minmax(78px,26%) 1fr auto;align-items:center;gap:10px}
.dist-rotulo{font-size:var(--t-meta);font-weight:700;color:var(--tinta-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dist-trilho{position:relative;height:9px;border-radius:999px;background:var(--neutro-bg);overflow:hidden}
.dist-preenche{position:absolute;inset:0 auto 0 0;width:0;border-radius:999px;background:var(--oliva);
  transition:width .85s var(--ease) calc(.15s + var(--i,0)*70ms)}
.secao.ativa .dist-preenche{width:calc(var(--v) / var(--max) * 100%)}
.dist-preenche.critico{background:var(--vermelho)}
.dist-preenche.aviso{background:var(--mel)}
.dist-valor{font-size:var(--t-meta);font-weight:800;color:var(--tinta);font-variant-numeric:tabular-nums;min-width:3ch;text-align:right}
.dist-vazio{font-size:var(--t-meta);color:var(--tinta-2);font-style:italic}

/* ---- grade de cards por sistema (Site / Siscam 9 / Siscam 8) ---- */
.grade-sistemas{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:var(--gap)}
.card-sistema .kpi-numero{font-size:var(--t-num-2)}
.card-sistema .kpi-mini{display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:8px;font-size:var(--t-meta);color:var(--tinta-2)}
.card-sistema .kpi-mini b{color:var(--tinta)}
.card-sistema.destaque{outline:2px solid var(--mel);outline-offset:-2px}

/* ---- tabela de chamados (Site / Siscam 9 no briefing) ---- */
.tabela-tickets{width:100%;border-collapse:collapse;font-size:var(--t-meta)}
.tabela-tickets th{position:sticky;top:0;z-index:1;background:var(--tabela-cabeca);text-align:left;
  padding:9px 12px;font-weight:800;color:var(--tabela-ink);white-space:nowrap}
.tabela-tickets td{padding:9px 12px;border-top:1px solid var(--tabela-linha);color:var(--tabela-ink);vertical-align:top}
.tabela-tickets tbody tr:hover{background:var(--tabela-hover)}
.tabela-tickets .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tabela-tickets .assunto{max-width:34ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tabela-tickets .id{font-weight:800;color:var(--tinta-2)}
.tabela-tickets .atrasado{color:var(--verm-ink);font-weight:800}

/* ---- cards de desenvolvedor ---- */
.grade-dev{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:var(--gap)}
.card-dev .dev-nome{font:800 var(--t-card)/1.15 var(--sans);letter-spacing:-.01em;color:var(--tinta)}
.card-dev .kpi-mini{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:var(--t-meta);color:var(--tinta-2)}
.card-dev .kpi-mini b{color:var(--tinta)}
/* Trabalho ativo: a unica linha do card que fala do agora, nao do acumulado. */
.card-dev .destaque-trabalho{padding-top:8px;border-top:1px solid var(--divisoria)}
.card-dev .destaque-trabalho b{color:var(--oliva-escuro)}
.card-dev .dev-sistemas{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}
.chip{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:999px;
  background:var(--neutro-bg);color:var(--tinta-2);font-size:11.5px;font-weight:700}
.chip b{color:var(--tinta)}

/* ---- grade com dois gráficos por linha, até quatro ---- */
.graficos.quatro{grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr))}
/* destaques com mais de quatro cards: linhas extras em vez de esmagar as duas fixas */
.grade-destaques.ampla{grid-template-rows:auto auto;grid-auto-rows:minmax(clamp(130px,17vh,190px),auto);align-content:start}
.grade-destaques.ampla .card{min-height:0}
.bloco-sistema+.bloco-sistema{margin-top:18px}
.bloco-sistema h4{margin-bottom:8px}

/* ==== CONTRATO DE LAYOUT ===================================================
   A .secao e um flex column de ALTURA FIXA (position:absolute;inset:0). Todo
   filho direto declara seu papel:
     .bloco-fixo      ocupa o que precisa e NAO encolhe;
     .bloco-elastico  absorve a sobra e pode comprimir.
   Sem isso todo filho e flex-shrink:1 e o navegador distribui o encolhimento
   sozinho: esmaga o flexivel (o canvas do grafico vira tira, porque e
   position:absolute;inset:0;height:100%) e deixa o rigido transbordar por cima
   do vizinho. Foi a causa da quebra de 17/09/2026.
   Estas regras vem DEPOIS de todos os componentes de proposito: a classe do
   HTML precisa vencer o flex declarado dentro de qualquer grade.
   Coberto por testes/test_layout.py -- nao remova sem rodar os testes.       */
.secao>.bloco-fixo{flex:0 0 auto;min-width:0}
.secao>.bloco-elastico{flex:1 1 auto;min-height:0;min-width:0}

/* ==== GRADE UNICA DE CARDS =================================================
   Uma definicao para as quatro paginas. A densidade muda por --card-min, nunca
   por uma grade nova: grade propria dentro de gerador foi metade da causa da
   quebra (o diretor e o financeiro tinham as suas). O min(100%,...) impede a
   coluna de estourar a linha em tela estreita.                               */
.grade{display:grid;gap:var(--gap);align-content:start;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,var(--card-min,240px)),1fr))}
.grade.estreita{--card-min:150px}
.grade.densa{--card-min:200px}
.grade.larga{--card-min:320px}

/* Grades que moravam nos geradores, trazidas para a base. Agora sao so a
   densidade da grade padrao -- a mecanica e uma so. */
.painel-exec{--card-min:230px;--colunas-max:4;
  /* auto-FILL, nao auto-fit: com auto-fit as trilhas vazias colapsam e um card
     sozinho (o de licencas) estica pela largura inteira da pagina. */
  grid-template-columns:repeat(auto-fill,minmax(min(100%,var(--card-min)),1fr))}
/* Mesma especificidade de .grade (uma classe): quem vence e a ORDEM. Esta regra
   precisa vir DEPOIS de .grade -- nao mova para cima. */
@media (min-width:1060px){
  /* Teto de 4 colunas: sem isso, numa tela larga os 7 cards entram todos na
     mesma linha, cada um estreito demais para o titulo caber. */
  .painel-exec{grid-template-columns:repeat(var(--colunas-max),minmax(0,1fr))}
}
.duas-colunas-secao{--card-min:300px}
.faixa-prazo{--card-min:150px;margin-top:4px}
.mov-grade{--card-min:260px}

/* ---- vindo de CSS_DIRETOR ---- */
.card-exec{display:flex;flex-direction:column;justify-content:space-between}
.card-exec .kpi-numero{font-size:var(--t-num-2)}
.card-exec .kpi-rotulo{color:var(--oliva)}
.leitura-exec{font-size:clamp(15px,1.7vh,18px);line-height:1.62}
.leitura-exec li{margin-bottom:7px}

/* ---- vindo de CSS_FINANCEIRO ---- */
.faixa-prazo .card{padding:clamp(14px,1.5vw,22px)}
.faixa-prazo .kpi-numero{font-size:var(--t-num-2)}
.faixa-prazo .critica{background:var(--verm-bg)}
.faixa-prazo .urgente{background:var(--ambar-bg)}
.mov-lista{margin-top:10px;display:grid;gap:7px}
.mov-item{display:flex;justify-content:space-between;gap:10px;font-size:var(--t-meta);
  padding-bottom:6px;border-bottom:1px solid var(--divisoria)}
.mov-item:last-child{border-bottom:0}
.mov-item .cliente{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mov-item .quando{color:var(--tinta-2);white-space:nowrap;font-variant-numeric:tabular-nums}
.mov-vazio{font-size:var(--t-meta);color:var(--tinta-2);font-style:italic;margin-top:8px}
.aviso-escopo{margin-top:auto;font-size:var(--t-meta);line-height:1.5;color:var(--carimbo);
  background:var(--ambar-bg);border-radius:14px;padding:11px 15px}

/* ---- agenda do dia (Google Calendar) ---- */
.agenda-lista{overflow:auto;display:grid;gap:8px;align-content:start}
.agenda-item{display:grid;grid-template-columns:auto 1fr auto;gap:4px 12px;align-items:baseline;
  padding-bottom:7px;border-bottom:1px solid var(--divisoria)}
.agenda-item:last-child{border-bottom:0}
.agenda-item.passou{opacity:.55}
.agenda-item.agora{background:var(--ambar-bg);border-radius:12px;padding:8px 11px;border-bottom:0}
.agenda-hora{font-variant-numeric:tabular-nums;font-weight:700;white-space:nowrap}
.agenda-titulo{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.agenda-dono{color:var(--tinta-2);font-size:var(--t-meta);white-space:nowrap}
.agenda-meta{grid-column:2/-1;color:var(--tinta-2);font-size:var(--t-meta)}
.agenda-faixa{font-size:var(--t-meta);line-height:1.5;color:var(--carimbo);
  background:var(--ambar-bg);border-radius:14px;padding:10px 14px;margin-top:10px}
.agenda-faixa b{font-variant-numeric:tabular-nums}

/* ---- vindo de CSS_SEMANAL ---- */
.pill.atual{background:var(--bom-bg);color:var(--bom)}
.pill.anterior{background:var(--neutro-bg);color:var(--tinta-2)}
.tabela-lic .apagado{color:var(--tabela-muted);font-style:italic}

/* ==== DENSIDADE POR ALTURA DE TELA =========================================
   O projeto so tinha breakpoint de LARGURA. Num notebook de 639px de altura o
   topo come 185px e sobram ~357px de painel util -- foi exatamente ali que o
   layout quebrou. Aqui o topo e os espacos cedem antes do conteudo.          */
@media (max-height:820px){
  :root{--topo-h:clamp(140px,22vh,230px);--gap:18px;--pad:22px;--pad-card:20px}
}
@media (max-height:680px){
  :root{--topo-h:clamp(116px,18vh,170px);--gap:14px;--pad:16px;--pad-card:16px}
  .grade{--card-min:200px}
  .grade.larga{--card-min:280px}
  .grade.densa{--card-min:180px}
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
  if (!D || !D.graficos || !D.graficos.length) return;
  var doc = document, win = window;
  var rm = !!(win.matchMedia && win.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var palco = function(){ return !win.matchMedia || win.matchMedia("(min-width: 900px)").matches; };
  var cs = getComputedStyle(doc.documentElement);
  var v = function(n, padrao){ return cs.getPropertyValue(n).trim() || padrao; };
  var marfim = v("--marfim", "#fefae0");
  // paleta nomeada: o Python manda o nome da cor, nao o hex
  var CORES = {
    azul:   [v("--azul", "#277fae"),        v("--azul", "#277fae")],
    rubro:  [v("--rubro", "#e0301e"),       v("--rubro-area", "#a33e20")],
    oliva:  [v("--oliva", "#606c38"),       v("--oliva", "#606c38")],
    mel:    [v("--mel", "#de9628"),         v("--mel", "#de9628")],
    verde:  [v("--verde-texto", "#9bb45c"), v("--verde-texto", "#9bb45c")]
  };
  var cor = function(nome){ return CORES[nome] || CORES.azul; };
  var rgba = function(c, a){
    var r = parseInt(c.slice(1, 3), 16), g = parseInt(c.slice(3, 5), 16), b = parseInt(c.slice(5, 7), 16);
    return "rgba(" + r + "," + g + "," + b + "," + a + ")";
  };
  Chart.defaults.font.family = getComputedStyle(doc.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.font.weight = "600";
  Chart.defaults.color = "#5c6446";  // --tinta-2: ticks e rotulos sobre o card claro
  var anim = rm ? false : { duration: 900, easing: "easeOutQuart" };
  var tooltip = { backgroundColor: "#283618", titleColor: "#b8d86e", bodyColor: "#fefae0", padding: 12, cornerRadius: 12,
    displayColors: false, titleFont: { weight: "700" }, bodyFont: { size: 12 }, caretSize: 6 };
  var escalas = function(horizontal){
    return {
      x: { beginAtZero: !!horizontal, grid: { display: !!horizontal, color: "rgba(40,54,24,.14)", tickLength: 0 },
           border: { display: false }, ticks: { precision: 0, maxRotation: 0, autoSkip: true, maxTicksLimit: 8, padding: 8 } },
      y: { beginAtZero: !horizontal, grid: { display: !horizontal, color: "rgba(40,54,24,.14)", tickLength: 0 },
           border: { display: false, dash: [3, 4] }, ticks: { precision: 0, padding: 10, maxTicksLimit: 7 } }
    };
  };
  var base = function(horizontal){
    return { responsive: true, maintainAspectRatio: false, animation: anim,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: tooltip },
      scales: escalas(horizontal), layout: { padding: { top: 8, right: 10 } } };
  };

  function area(el, g){
    var c = cor(g.cor), rotulos = g.labels || D.labels, muitos = rotulos.length > 40;
    new Chart(el, { type: "line",
      data: { labels: rotulos, datasets: [{ label: g.rotulo, data: g.dados, borderColor: c[0], fill: "origin", borderWidth: 2.5,
        backgroundColor: function(ctx){
          var ca = ctx.chart.chartArea;
          if (!ca) return rgba(c[1], .9);
          var gr = ctx.chart.ctx.createLinearGradient(0, ca.top, 0, ca.bottom);
          gr.addColorStop(0, rgba(c[1], g.opacidade || .9)); gr.addColorStop(1, rgba(c[1], .05));
          return gr;
        },
        pointRadius: muitos ? 0 : 3.5, pointHoverRadius: 7, pointBackgroundColor: c[0],
        pointBorderColor: marfim, pointBorderWidth: 2, tension: .25, spanGaps: false }] },
      options: base(false) });
  }

  function barra(el, g){
    var c = cor(g.cor), horizontal = g.tipo === "barra-h";
    var o = base(horizontal);
    o.indexAxis = horizontal ? "y" : "x";
    o.interaction = { mode: "nearest", intersect: true };
    new Chart(el, { type: "bar",
      data: { labels: g.labels || D.labels, datasets: [{ label: g.rotulo, data: g.dados,
        backgroundColor: rgba(c[0], .82), hoverBackgroundColor: c[0],
        borderRadius: 7, borderSkipped: false, maxBarThickness: horizontal ? 22 : 38 }] },
      options: o });
  }

  var feito = false;
  function montar(){
    if (feito) return;
    var algum = false;
    for (var i = 0; i < D.graficos.length; i++) {
      if (doc.getElementById(D.graficos[i].id)) { algum = true; break; }
    }
    if (!algum) return;
    feito = true;
    D.graficos.forEach(function(g){
      var el = doc.getElementById(g.id);
      if (!el) return;
      try { (g.tipo === "barra" || g.tipo === "barra-h") ? barra(el, g) : area(el, g); }
      catch (e) { /* um grafico com problema nao derruba os outros */ }
    });
  }
  var SECAO = D.secao || "evolucao";
  doc.addEventListener("secao:ativa", function(e){ if (e.detail && e.detail.id === SECAO) montar(); });
  // carga direta com #evolucao: o evento inicial pode ter sido emitido antes deste ouvinte existir
  var jaAtiva = doc.querySelector(".secao.ativa");
  if ((jaAtiva && jaAtiva.id === SECAO) || (location.hash || "").slice(1) === SECAO) montar();
  if (win.matchMedia) {  // janela redimensionada para o modo empilhado antes de visitar a secao
    var mq = win.matchMedia("(min-width: 900px)");
    var aoMudar = function(){ if (!mq.matches) montar(); };
    if (mq.addEventListener) mq.addEventListener("change", aoMudar); else if (mq.addListener) mq.addListener(aoMudar);
  }
  if (!palco()) {
    var alvo = null;
    for (var k = 0; k < D.graficos.length && !alvo; k++) alvo = doc.getElementById(D.graficos[k].id);
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


def base_status_comparavel(helpdesk: dict | None, anterior: dict) -> bool:
    """A fila de hoje e a do dia anterior saem do mesmo conjunto de status?

    Quando HELPDESK_STATUS_EXCLUIDOS muda -- e no dia em que a regra global
    entrou em vigor -- o total salta sem que nada tenha acontecido na operação.
    Mostrar "+X vs. dia anterior" nesse caso é mentira estatística: quem gerou o
    número foi a configuração, não a fila. Nesses dias o badge some.

    Linha antiga do histórico não tem fila_status_qtd; como a coleta de hoje tem,
    a ausência já é a prova de que a base mudou.
    """
    hoje = len((helpdesk or {}).get("status_consultados") or []) or None
    if not hoje:
        return True  # coleta anterior à regra: nada a comparar de diferente
    return hoje == (anterior or {}).get("fila_status_qtd")


AVISO_BASE_MUDOU = (
    "A comparação com o dia anterior está suspensa nesta seção: o conjunto de status "
    "consultados no Milldesk mudou, então o total de hoje e o de ontem não saem da "
    "mesma régua. Os badges voltam na próxima coleta."
)


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
    cab = (f'<header class="secao-cabeca dividida bloco-fixo">'
           f'{titulo_secao(titulo, id_)}</header>')
    return (f'<section class="secao" id="{id_}" data-scroll aria-labelledby="t-{id_}">{cab}'
            f'<p class="vazio bloco-elastico">{mensagem}</p></section>')


def secao_agenda(agenda: dict | None, id_: str = "agenda", titulo: str = "Agenda") -> str:
    """Seção 'Agenda do dia'. Usada pelo diário e pelo diretor -- uma só, de
    propósito: foi o CSS duplicado entre páginas que quebrou o layout antes.

    Tolera agenda ausente, com erro ou sem evento: nunca levanta exceção, como
    exige a degradação do pipeline. Todo filho direto de .secao declara o
    contrato de flex (bloco-fixo / bloco-elastico).
    """
    # Com AGENDA_CALENDARIOS='*' o nome do calendário É o nome de uma pessoa
    # ("Roberto", "Ketlyn", "Guilherme Polezi"), e o id de um calendário pessoal
    # é um e-mail. Com o ranking desligado, nada disso pode chegar ao HTML --
    # inclusive dentro de texto livre como `erro` e `aviso`, que citam o
    # calendário pelo nome ou pelo id. redigir_nomes() não alcança aqui.
    mostrar_donos = MOSTRAR_RANKING
    detalhe_oculto = " O detalhe está em dados/agenda.json (omitido aqui porque cita calendários)."

    if not isinstance(agenda, dict) or not agenda:
        return secao_vazia(id_, titulo, "Fonte indisponível: agenda.")
    if agenda.get("erro"):
        return secao_vazia(id_, titulo, f"Fonte indisponível: {agenda['erro']}"
                           if mostrar_donos else "Fonte indisponível: agenda." + detalhe_oculto)

    # `or []` só cobre valor falsy. Uma string não vazia, um dict, ou uma lista
    # com itens que não são dict passariam direto e quebrariam no primeiro
    # .get() -- e a exceção NÃO ficaria contida nesta seção: sobe e derruba a
    # página inteira, trocando e-mail, Milldesk e licenças por uma página de
    # erro. Degradação é por seção neste projeto, então filtramos na entrada.
    def so_dicts(valor) -> list[dict]:
        return [x for x in valor if isinstance(x, dict)] if isinstance(valor, list) else []

    def inteiro(valor) -> int:
        try:
            return int(valor)
        except (TypeError, ValueError):
            return 0

    eventos = so_dicts(agenda.get("eventos"))
    dia_inteiro = so_dicts(agenda.get("dia_inteiro"))
    if not eventos and not dia_inteiro:
        return secao_vazia(id_, titulo, "Nenhum compromisso hoje.")

    # Mesmo bloco ".lado" que as outras seções usam no cabeçalho -- nenhuma
    # classe nova: componente novo por página foi o que fez o layout divergir.
    def medida(valor, legenda: str) -> str:
        return (f'<div class="kpi-medida"><p class="kpi-numero menor">{esc(valor)}</p>'
                f'<p class="kpi-legenda">{esc(legenda)}</p></div>')

    horas, minutos = divmod(inteiro(agenda.get("minutos_ocupados")), 60)
    medidas = [medida(len(eventos), "compromissos hoje")]
    if agenda.get("total_meus") is not None and inteiro(agenda["total_meus"]) != len(eventos):
        medidas.append(medida(inteiro(agenda["total_meus"]), "na sua agenda"))
    medidas.append(medida(f"{horas}h{minutos:02d}", "ocupadas"))
    sintese = f'<div class="lado">{"".join(medidas)}</div>'

    faixa_dia = ""
    if dia_inteiro:
        itens = "; ".join(
            f"{esc(d.get('titulo', ''))}" + (
                f" ({esc(', '.join(str(c) for c in d['calendarios']))})"
                if mostrar_donos and isinstance(d.get("calendarios"), list)
                and d["calendarios"] else "")
            for d in dia_inteiro)
        if itens:
            faixa_dia = f'<p class="agenda-faixa bloco-fixo"><b>Dia inteiro:</b> {itens}</p>'

    linhas = []
    for e in eventos:
        classes = "agenda-item"
        if e.get("em_andamento"):
            classes += " agora"
        elif e.get("ja_passou"):
            classes += " passou"
        # Marcador de duração zero mostra um horário só -- "14:30–14:30" seria ruído.
        hora = esc(e.get("inicio") or "")
        if not e.get("marcador") and e.get("fim"):
            hora += "–" + esc(e["fim"])
        if e.get("comeca_antes_de_hoje"):
            hora = "←" + hora
        if e.get("termina_depois_de_hoje"):
            hora += "→"

        brutos = e.get("calendarios")
        donos = ([str(d) for d in brutos if d]
                 if mostrar_donos and isinstance(brutos, list) else [])
        meta = [p for p in (esc(e.get("local") or ""),
                            "reunião online" if e.get("reuniao_online") else "") if p]
        linhas.append(
            f'<div class="{classes}">'
            f'<span class="agenda-hora">{hora}</span>'
            f'<span class="agenda-titulo" title="{esc(e.get("titulo") or "")}">'
            f'{esc(e.get("titulo") or "(sem título)")}</span>'
            f'<span class="agenda-dono">{esc(" · ".join(donos))}</span>'
            + (f'<span class="agenda-meta">{" · ".join(meta)}</span>' if meta else "")
            + "</div>")

    janela = agenda.get("maior_janela_livre")
    janela = janela if isinstance(janela, dict) else {}
    faixa_janela = ""
    if janela.get("inicio"):
        jh, jm = divmod(inteiro(janela.get("minutos")), 60)
        faixa_janela = (
            f'<p class="agenda-faixa bloco-fixo"><b>Maior janela livre:</b> '
            f'{esc(janela["inicio"])}–{esc(janela["fim"])} '
            f'({jh}h{jm:02d}) · expediente {esc(agenda.get("expediente") or "")}</p>')

    lista_cal = agenda.get("calendarios")
    quantos = len(lista_cal) if isinstance(lista_cal, list) else 0
    principal = agenda.get("calendario_principal")
    if not mostrar_donos:
        principal = None       # o nome do calendário principal também é de pessoa
    texto_nota = (f"{quantos} calendário(s) consultado(s)"
                  + (f"; horas ocupadas e janela livre são só de “{principal}”." if principal
                     else "; horas ocupadas e janela livre saem só do calendário principal."))
    if agenda.get("aviso"):
        # O aviso cita os calendários pelo nome -- mesmo gate dos donos.
        texto_nota += (f" AVISO: {agenda['aviso']}" if mostrar_donos
                       else " AVISO: a configuração de calendários precisa de atenção."
                            + detalhe_oculto)
    return f"""
<section class="secao" id="{id_}" data-scroll aria-labelledby="t-{id_}">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao(titulo, id_)}{sintese}</header>
  {nota_secao("gcal", texto_nota)}
  {faixa_dia}
  <div class="agenda-lista bloco-elastico" data-scroll tabindex="0" role="region" aria-label="Compromissos de hoje">
    {"".join(linhas) or '<p class="vazio">Nenhum compromisso com horário hoje.</p>'}
  </div>
  {faixa_janela}
</section>"""


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


# ----------------------------------------------------------------------------
# Explicitação da fonte -- toda métrica exibida diz de onde veio e como é contada
# ----------------------------------------------------------------------------
# chave -> (rótulo curto, classe CSS, descrição da fonte)
FONTES_INFO: dict[str, tuple[str, str, str]] = {
    "milldesk": ("Milldesk", "milldesk",
                 "Help desk da empresa (API v1). Chamados abertos, status, categoria, "
                 "subcategoria, técnico responsável, prioridade e há quanto tempo estão abertos."),
    "imap": ("Caixa de e-mail", "imap",
             "Leitura direta da caixa por IMAP: mensagens não lidas, recebidas hoje e spam."),
    "licencas": ("Sistema de licenças", "licencas",
                 "Painel web interno de licenças: cliente, sistema e data de vencimento."),
    "historico": ("Histórico local", "",
                  "historico/metricas.jsonl — uma linha por dia, gravada por arquivar.py "
                  "a partir das coletas anteriores."),
    "gcal": ("Google Agenda", "gcal",
             "Google Calendar API, somente leitura. Eventos de hoje dos calendários que a "
             "conta enxerga; recorrências expandidas pelo próprio Google."),
}

# chave -> (fonte, o que a métrica é, como ela é calculada)
METRICAS: dict[str, tuple[str, str, str]] = {
    "agenda_eventos": ("gcal", "Compromissos de hoje nos calendários consultados.",
                       "Google Calendar API com singleEvents=true (o Google expande as "
                       "recorrências). Fora: cancelados, os que você recusou e o marcador "
                       "automático de local de trabalho. Evento que aparece em vários "
                       "calendários vira uma linha só, com todos os donos."),
    "agenda_ocupado": ("gcal", "Tempo do dia já comprometido na SUA agenda.",
                       "Soma a duração dos eventos do calendário principal "
                       "(AGENDA_CALENDARIO_PRINCIPAL). Os demais calendários aparecem na "
                       "lista mas não entram nesta conta, senão o número viraria o tempo "
                       "da empresa inteira. Marcador de duração zero não soma."),
    "agenda_janela": ("gcal", "Maior bloco livre seguido dentro do expediente.",
                      "Funde os compromissos do calendário principal e procura o maior "
                      "buraco dentro de AGENDA_EXPEDIENTE. Buraco menor que "
                      "AGENDA_JANELA_MINIMA não conta como janela."),
    "fila_total": ("milldesk", "Todos os chamados em aberto no Milldesk, de todos os técnicos.",
                   "Em aberto aqui quer dizer: qualquer status que não seja Fechado. O coletor pede a "
                   "lista de status à própria API e consulta todos, menos os de HELPDESK_STATUS_EXCLUIDOS."),
    "fila_sistema": ("milldesk", "Chamados em aberto do sistema, dentro da mesma fila total.",
                     "Classificados pela categoria do chamado (campo category), segundo o mapa HELPDESK_SISTEMAS."),
    "equipe_abertos": ("milldesk", "Chamados em aberto atribuídos aos técnicos monitorados.",
                       "Filtra a fila pelo campo Técnico do chamado contra os nomes de "
                       "HELPDESK_AGENT_NAME (comparação por trecho, sem acento e sem caixa)."),
    "atend_fechados": ("milldesk", "Atendimentos fechados no último dia útil.",
                       "Chamados do solicitante de atendimento diário, com status Fechado, criados naquele dia; "
                       "o técnico sai da linha 'Técnico:' da descrição."),
    "dev_atribuidos": ("milldesk", "Chamados em aberto atribuídos aos desenvolvedores.",
                       "Filtra a fila pelo campo Técnico do chamado contra os nomes de "
                       "HELPDESK_DEV_NAMES, em qualquer status que não seja Fechado."),
    "dev_total_nome": ("milldesk", "Tudo que está no nome do desenvolvedor, em qualquer status menos Fechado.",
                       "Campo Técnico do chamado igual ao nome do dev; é o número grande do card."),
    "dev_em_trabalho": ("milldesk", "Do total do desenvolvedor, quanto está em trabalho ativo agora.",
                        "Chamados cujo status é exatamente um dos de HELPDESK_STATUS_TRABALHO "
                        "(hoje: Com o Desenvolvedor e Em atendimento), somados. O resto do total "
                        "está parado em espera, teste, aprovação ou deploy."),
    "dev_equipe": ("milldesk", "Carga de cada equipe de desenvolvimento.",
                   "Agrupa os desenvolvedores de HELPDESK_DEV_NAMES pelas equipes de "
                   "HELPDESK_DEV_EQUIPES; quem não está em nenhuma cai na equipe padrão. "
                   "A quebra por sistema mostra se a equipe está mesmo trabalhando no que é dela."),
    "dev_status": ("milldesk", "Chamados parados em status de desenvolvimento, com dono ou sem.",
                   "Chamados cujo status está em HELPDESK_STATUS_DEV (ex.: Com o Desenvolvedor, Aguardando Testes)."),
    # A explicação visível não menciona SLA de propósito: ele saiu do sistema e
    # citá-lo na tela reintroduz a confusão. O porquê está no CLAUDE.md.
    "idade_90": ("milldesk", "Chamados que estão abertos há mais de 90 dias.",
                 "Diferença entre hoje e a data de abertura do chamado. É a medida de urgência "
                 "usada em todo o painel, inclusive para escolher quais chamados são listados."),
    "idade_fila": ("milldesk", "Há quanto tempo os chamados da fila estão abertos.",
                   "Diferença entre hoje e o campo start de cada chamado."),
    "natureza": ("milldesk", "Que tipo de trabalho a fila representa: corrigir o que quebrou "
                             "(corretivo) ou construir o que foi pedido (evolutivo).",
                 "Lido da subcategoria do chamado: 'Bug / Erro' e 'Lentidão ou Travamento' viram "
                 "corretivo; 'Melhoria / Nova Função' vira evolutivo; implantação, migração, "
                 "treinamento e configuração entram em faixas próprias."),
    "grupo": ("milldesk", "Agrupamento de produto definido no próprio Milldesk.",
              "Campo group do chamado, como veio da origem. Cerca de um quinto da fila não tem "
              "esse campo preenchido — por isso o sistema é classificado pela categoria, não por aqui."),
    "lic_vencidas": ("licencas", "Licenças de produção vencidas há pouco tempo — ainda acionáveis.",
                     "Tabela 'Vencidas' do painel, sem homologação/teste, limitada aos últimos 60 dias."),
    "lic_vencendo": ("licencas", "Licenças de produção com vencimento próximo.",
                     "Tabela 'Vencimento Próximo' do painel, sem homologação/teste."),
    "lic_antigas": ("licencas", "Licenças vencidas há mais de 60 dias, fora da lista de ação.",
                    "Contagem do que sobra da tabela 'Vencidas' além da janela de 60 dias."),
    "email_nao_lidos": ("imap", "Mensagens não lidas na caixa de entrada.",
                        "Busca UNSEEN na pasta INBOX."),
    "email_recebidos": ("imap", "Mensagens recebidas hoje.",
                        "Busca por data de hoje na pasta INBOX."),
    "email_spam": ("imap", "Mensagens que caíram no spam hoje.",
                   "Busca por data de hoje na pasta configurada em EMAIL_SPAM_FOLDER."),
    "evolucao": ("historico", "Série diária das métricas já coletadas.",
                 "Uma linha por dia em historico/metricas.jsonl; dias sem coleta não aparecem."),
    "ranking": ("historico", "Atendimentos fechados por técnico, somados nos dias úteis registrados.",
                "Soma de atend_por_tecnico do histórico; dias que repetem o mesmo dia de referência contam uma vez."),
}


def tag_fonte(chave: str) -> str:
    """Etiqueta com o nome da fonte de origem (Milldesk, IMAP, licenças...)."""
    rotulo, classe, descricao = FONTES_INFO.get(chave, (chave, "", ""))
    classe = f" {classe}" if classe else ""
    return f'<span class="tag-fonte{classe}" title="{esc(descricao)}">{esc(rotulo)}</span>'


def explica(chave: str, extra: str = "") -> str:
    """Linha de explicitação: o que a métrica é e como é calculada.

    Métrica desconhecida devolve string vazia -- nunca quebra a página.
    """
    if chave not in METRICAS:
        return f'<p class="kpi-explica bloco-fixo">{esc(extra)}</p>' if extra else ""
    _, o_que, como = METRICAS[chave]
    complemento = f" {esc(extra)}" if extra else ""
    return (f'<p class="kpi-explica bloco-fixo"><b>O que é:</b> {esc(o_que)}{complemento}'
            f'<span class="como"><b>Como é contado:</b> {esc(como)}</span></p>')


def nota_secao(chave_fonte: str, texto: str) -> str:
    """Nota no alto da seção dizendo de qual fonte ela toda vem."""
    return f'<p class="secao-nota bloco-fixo">{tag_fonte(chave_fonte)}{esc(texto)}</p>'


def barras_distribuicao(dados: dict, limite: int = 8, criticos: tuple = ()) -> str:
    """Barras horizontais de uma distribuição (fila por sistema, por status...).

    Tolera dict vazio, valores não numéricos e chaves None.
    """
    if not isinstance(dados, dict) or not dados:
        return '<p class="dist-vazio">Sem dados para distribuir.</p>'
    itens = [(str(k), v) for k, v in dados.items() if isinstance(v, (int, float))]
    if not itens:
        return '<p class="dist-vazio">Sem dados para distribuir.</p>'
    itens.sort(key=lambda kv: -kv[1])
    itens = itens[:limite]
    maximo = max(v for _, v in itens) or 1
    linhas = []
    for i, (rotulo, valor) in enumerate(itens):
        classe = " critico" if rotulo in criticos else ""
        linhas.append(
            f'<div class="dist-linha" style="--i:{i}">'
            f'<span class="dist-rotulo" title="{esc(rotulo)}">{esc(rotulo)}</span>'
            f'<span class="dist-trilho"><span class="dist-preenche{classe}" '
            f'style="--v:{valor};--max:{maximo}"></span></span>'
            f'<span class="dist-valor">{fmt_num(valor)}</span></div>'
        )
    return f'<div class="dist">{"".join(linhas)}</div>'


def tabela_tickets(tickets: list, limite: int = 12, com_tecnico: bool = True) -> str:
    """Tabela enxuta de chamados: id, assunto, status, técnico e idade.

    Sem coluna de SLA: a origem não tinha esse prazo definido corretamente e ele
    saiu do sistema. A idade é o que marca urgência aqui.
    """
    if not isinstance(tickets, list) or not tickets:
        return '<p class="vazio">Nenhum chamado em aberto neste recorte.</p>'
    col_tecnico = "<th scope=\"col\">Técnico</th>" if com_tecnico else ""
    linhas = []
    for t in tickets[:limite]:
        if not isinstance(t, dict):
            continue
        dias = t.get("dias_aberto")
        # acima de 90 dias vira destaque: é o único sinal de urgência que sobrou
        classe_dias = ' class="num atrasado"' if (dias or 0) > 90 else ' class="num"'
        celula_tecnico = f'<td>{esc(t.get("tecnico") or "—")}</td>' if com_tecnico else ""
        linhas.append(
            f'<tr><td class="id">#{esc(t.get("id"))}</td>'
            f'<td class="assunto" title="{esc(t.get("assunto"))}">{esc(t.get("assunto") or "—")}</td>'
            f'<td>{esc(t.get("status") or "—")}</td>'
            f"{celula_tecnico}"
            f'<td{classe_dias}>{fmt_num(dias) if dias is not None else "—"}</td></tr>'
        )
    cabeca = (f'<thead><tr><th scope="col">ID</th><th scope="col">Assunto</th>'
              f'<th scope="col">Status</th>{col_tecnico}'
              f'<th scope="col" class="num">Dias aberto</th></tr></thead>')
    return (f'<table class="tabela-tickets">{cabeca}<tbody>{"".join(linhas)}</tbody></table>')


def cards_equipes_dev(por_equipe: dict, mostrar_nomes: bool = True) -> str:
    """Um card por equipe de desenvolvimento, com a quebra por sistema.

    A quebra por sistema é o ponto: mostra se a equipe está trabalhando no que é
    dela. Uma equipe de website com metade da carga em outro sistema é um sinal,
    não um detalhe. Dict vazio devolve aviso, nunca exceção.
    """
    if not isinstance(por_equipe, dict) or not por_equipe:
        return '<p class="dist-vazio">Nenhuma equipe de desenvolvimento configurada.</p>'
    cards = []
    for i, (equipe, bloco) in enumerate(por_equipe.items()):
        b = bloco if isinstance(bloco, dict) else {}
        devs = b.get("devs") if isinstance(b.get("devs"), list) else []
        if mostrar_nomes and devs:
            linha_devs = f'<p class="kpi-legenda">{esc(", ".join(str(d) for d in devs))}</p>'
        else:
            linha_devs = f'<p class="kpi-legenda">{len(devs)} desenvolvedor(es)</p>'
        chips = "".join(
            f'<span class="chip">{esc(k)} <b>{fmt_num(v)}</b></span>'
            for k, v in list((b.get("por_sistema") or {}).items())[:5])
        natureza = "".join(
            f'<span class="chip">{esc(k)} <b>{fmt_num(v)}</b></span>'
            for k, v in list((b.get("por_natureza") or {}).items())[:3])
        cards.append(f"""
<article class="card kpi card-dev" style="--i:{i}" data-scroll>
  <div class="kpi-cabeca"><p class="dev-nome">{esc(equipe)}</p>{tag_fonte("milldesk")}</div>
  <p class="kpi-numero menor">{num_html(b.get("abertos"))}</p>
  {linha_devs}
  <p class="kpi-mini"><span><b>{fmt_num(b.get("acima_de_90_dias"))}</b> abertos há mais de 90 dias</span></p>
  <div class="dev-sistemas">{chips or '<span class="dist-vazio">sem quebra por sistema</span>'}</div>
  <div class="dev-sistemas">{natureza}</div>
</article>""")
    return f'<div class="grade grade-dev bloco-elastico">{"".join(cards)}</div>'


def card_grafico(id_canvas: str, titulo: str, descricao: str, corpo_topo: str = "") -> str:
    """Card padrão de gráfico: título, linha de números e a caixa do canvas."""
    return f"""
<article class="card card-grafico" data-scroll>
  <div class="kpi-cabeca"><h3 class="kpi-rotulo">{esc(titulo)}</h3></div>
  {corpo_topo}
  <div class="grafico-caixa"><canvas id="{esc(id_canvas)}" role="img" aria-label="{esc(descricao)}"></canvas></div>
</article>"""


# ----------------------------------------------------------------------------
# Histórico de licenças -- retrato diário, para responder "o que mudou"
# ----------------------------------------------------------------------------
HISTORICO_LICENCAS = RAIZ / "historico" / "licencas.jsonl"


def ler_historico_licencas() -> list[dict]:
    """Retratos diários de licencas.jsonl, do mais antigo ao mais recente.

    Linha corrompida é descartada em silêncio: o resto da série continua válido.
    """
    if not HISTORICO_LICENCAS.exists():
        return []
    registros: list[dict] = []
    try:
        bruto = HISTORICO_LICENCAS.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    for linha in bruto.splitlines():
        if not linha.strip():
            continue
        try:
            r = json.loads(linha)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and r.get("data"):
            registros.append(r)
    registros.sort(key=lambda r: r.get("data") or "")
    return registros


def data_br(texto) -> date | None:
    """Data no formato dd/mm/aaaa do painel de licenças."""
    try:
        return datetime.strptime(str(texto).strip(), "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def chave_licenca(item: dict) -> tuple:
    """Identidade de uma licença: cliente + sistema (o vencimento é o que muda)."""
    return (str(item.get("cliente") or "").strip().lower(),
            str(item.get("sistema") or "").strip().lower())


def indexar_licencas(retrato: dict) -> dict[tuple, dict]:
    itens = (retrato or {}).get("itens")
    if not isinstance(itens, list):
        return {}
    return {chave_licenca(i): i for i in itens if isinstance(i, dict)}


def comparar_licencas(antes: dict, agora: dict) -> dict:
    """O que mudou entre dois retratos: renovadas, que venceram, que entraram e que saíram.

    'Renovada' = mesma licença com vencimento adiado. 'Saiu da lista' pode ser
    renovação para muito longe ou remoção no sistema -- o rótulo não afirma qual.
    """
    a, b = indexar_licencas(antes), indexar_licencas(agora)
    renovadas, venceram, entraram, sairam = [], [], [], []

    for k, novo in b.items():
        velho = a.get(k)
        if velho is None:
            entraram.append(novo)
            continue
        d_velho, d_novo = data_br(velho.get("vencimento")), data_br(novo.get("vencimento"))
        if d_velho and d_novo and d_novo > d_velho:
            renovadas.append({**novo, "vencimento_anterior": velho.get("vencimento")})
        elif velho.get("estado") == "vencendo" and novo.get("estado") == "vencida":
            venceram.append(novo)

    for k, velho in a.items():
        if k not in b:
            sairam.append(velho)

    return {"renovadas": renovadas, "venceram": venceram, "entraram": entraram, "sairam": sairam,
            "data_antes": (antes or {}).get("data"), "data_agora": (agora or {}).get("data")}


def agrupar_licencas_por(itens: list, campo: str) -> dict[str, int]:
    """Contagem de licenças por sistema ou por cliente, em ordem decrescente."""
    if not isinstance(itens, list):
        return {}
    contagem: dict[str, int] = {}
    for i in itens:
        if not isinstance(i, dict):
            continue
        valor = str(i.get(campo) or "").strip() or "(sem informação)"
        contagem[valor] = contagem.get(valor, 0) + 1
    return dict(sorted(contagem.items(), key=lambda kv: -kv[1]))


def faixas_de_prazo(itens: list) -> dict[str, int]:
    """Licenças agrupadas pela urgência do prazo -- a leitura que o financeiro faz."""
    faixas = {"Vencidas": 0, "Vence em até 7 dias": 0, "Vence em 8 a 30 dias": 0,
              "Vence em 31 a 60 dias": 0, "Mais de 60 dias": 0}
    for i in itens or []:
        if not isinstance(i, dict):
            continue
        d = i.get("dias")
        if d is None:
            continue
        if d < 0:
            faixas["Vencidas"] += 1
        elif d <= 7:
            faixas["Vence em até 7 dias"] += 1
        elif d <= 30:
            faixas["Vence em 8 a 30 dias"] += 1
        elif d <= 60:
            faixas["Vence em 31 a 60 dias"] += 1
        else:
            faixas["Mais de 60 dias"] += 1
    return faixas


def grafico(id_canvas: str, rotulo: str, dados: list, tipo: str = "area",
            cor: str = "azul", labels: list | None = None) -> dict:
    """Descritor de um gráfico para o payload do JS (JS_CHARTS monta a partir disso)."""
    d = {"id": id_canvas, "rotulo": rotulo, "dados": dados, "tipo": tipo, "cor": cor}
    if labels is not None:
        d["labels"] = labels
    return d


# =============================================================================
# TEMA SINO -- usado APENAS por gerar_dashboard_diretor.py
# =============================================================================
# Camada aplicada DEPOIS de CSS, no mesmo <style>: as variaveis de paleta sao
# redefinidas e os componentes novos (marca SINO, menu em blocos, faixa de
# grupo, card centralizado) sao acrescentados. Nada aqui renomeia, altera ou
# apaga o que ja existe -- por isso o diario, o financeiro e o semanal seguem
# no tema mel/favo sem tocar em uma linha.
#
# Paleta (definida pelo Guilherme, 21/09/2026):
#   #0C6E47 verde principal (painel, menu, rotulos)
#   #09512F verde escuro   (texto sobre ouro, carimbo, degrade do losango)
#   #F6C445 ouro           (data do painel, bloco de menu ativo)
#   #F5FAF7 gelo           (fundo da pagina, vazado do losango)
#   #0F1E16 carvao         (texto dentro do card)

# Losango da marca: quadrado arredondado girado 45 graus, vazado no centro.
# Um path so, com fill-rule evenodd -- o vazado deixa o fundo da pagina passar,
# entao ele funciona sobre qualquer superficie.
LOSANGO_SVG = """<svg class="sino-losango-svg" viewBox="0 0 100 100" aria-hidden="true" focusable="false">
<defs><linearGradient id="sino-grad" x1="0" y1="0" x2=".85" y2="1">
<stop offset="0" stop-color="#0C6E47"/><stop offset="1" stop-color="#09512F"/></linearGradient></defs>
<g transform="rotate(45 50 50)"><path fill="url(#sino-grad)" fill-rule="evenodd" d="M32 17h36a15 15 0 0 1 15 15v36a15 15 0 0 1-15 15H32a15 15 0 0 1-15-15V32a15 15 0 0 1 15-15Zm12 20h12a7 7 0 0 1 7 7v12a7 7 0 0 1-7 7H44a7 7 0 0 1-7-7V44a7 7 0 0 1 7-7Z"/></g>
</svg>"""


def logo_sino(alvo: str = "destaques", titulo: str = "SINO Gestão") -> str:
    """Marca do tema SINO: losango + "SINO", com o espaço do "Gestão" reservado.

    O espaço do subtítulo existe no layout desde o primeiro quadro (altura fixa,
    opacidade zero). É o que garante que a animação de hover não empurre nada:
    o losango e o "Gestão" animam DENTRO de caixas que nunca mudam de tamanho.
    """
    return f"""
<a class="marca marca-sino" href="#{esc(alvo)}" data-alvo="{esc(alvo)}" aria-label="{esc(titulo)} — início">
  <span class="sino-logo">{LOSANGO_SVG}</span>
  <span class="sino-texto">
    <span class="sino-nome">SINO</span>
    <span class="sino-sub"><span class="sino-sub-txt">Gestão</span></span>
  </span>
</a>"""


def menu_grade(ordem: list[tuple[str, str]], colunas: int = 3) -> str:
    """Menu em blocos arredondados (grade). Cada bloco é um [data-alvo], então o
    JS_UI já cuida do estado ativo -- nenhum JS novo para navegar.

    A classe .grid-item é a do Staggered Grid Reveal (gsapify); .grid é o
    contêiner que a animação referencia.
    """
    itens = "".join(
        f'<a class="grid-item" href="#{esc(id_)}" data-alvo="{esc(id_)}">{esc(rotulo)}</a>'
        for id_, rotulo in ordem)
    # O CSS ja traz 3 colunas literais. Só escrevemos estilo inline quando fugir
    # do padrão -- e aqui também com contagem literal, nunca var() dentro de
    # repeat(), que é frágil e falha para a declaração inteira quando não resolve.
    estilo = ("" if colunas == 3 else
              f' style="grid-template-columns:repeat({int(colunas)},var(--bloco-w))"')
    return (f'<nav class="menu-sino-caixa" aria-label="Seções do painel">'
            f'<div class="menu-sino grid"{estilo}>{itens}</div></nav>')


def pilula_fonte(chave: str, rotulo: str | None = None) -> str:
    """Pílula branca com ponto: a fonte de um grupo de cards.

    Mesmo registro de FONTES_INFO usado por tag_fonte() -- o rótulo curto é só
    apresentação; a descrição completa continua no title.
    """
    padrao, classe, descricao = FONTES_INFO.get(chave, (chave, "", ""))
    classe = f" {classe}" if classe else ""
    return (f'<span class="pilula-fonte{classe}" title="{esc(descricao)}">'
            f'<span class="ponto" aria-hidden="true"></span>{esc(rotulo or padrao)}</span>')


def grupo_fonte(chave: str, texto: str, rotulo: str | None = None) -> str:
    """Faixa que abre um grupo de cards: pílula da fonte + nota, na mesma linha."""
    return (f'<div class="faixa-grupo bloco-fixo">{pilula_fonte(chave, rotulo)}'
            f'<p class="faixa-grupo-texto">{esc(texto)}</p></div>')


def seta_delta(atual, anterior, melhor: str = "menor") -> str:
    """Seta de direção ao lado do número. A cor vem do julgamento, não do sinal."""
    try:
        d = int(atual) - int(anterior)
    except (TypeError, ValueError):
        return ""
    if d == 0:
        return ""
    subiu = d > 0
    bom = (subiu and melhor == "maior") or (not subiu and melhor == "menor")
    return (f'<span class="kpi-seta {"bom" if bom else "ruim"}" aria-hidden="true">'
            f'{"&#8593;" if subiu else "&#8595;"}</span>')


def card_sino(titulo: str, valor, sub: str = "", rodape: str = "", delta: str = "",
              seta: str = "", explicacao: str = "", indice: int = 0) -> str:
    """Card do tema SINO: título verde centralizado, número, julgamento, explicação.

    A fonte do dado não aparece no card: quem a declara é a faixa do grupo
    (grupo_fonte), que vale para todos os cards abaixo dela.
    """
    return f"""
<article class="card kpi card-sino" style="--i:{indice}">
  <h3 class="sino-card-titulo">{esc(titulo)}</h3>
  {f'<p class="sino-card-sub">{esc(sub)}</p>' if sub else ""}
  <p class="kpi-numero sino-num">{num_html(valor)}{seta}</p>
  {f'<div class="kpi-juizo sino-juizo">{delta}</div>' if delta else ""}
  {f'<p class="kpi-rodape sino-rodape">{rodape}</p>' if rodape else ""}
  {explicacao}
</article>"""


CSS_SINO = """
/* ==== TEMA SINO ============================================================
   Aplicado depois de CSS e so no dashboard_diretor. Primeiro a paleta (os
   mesmos nomes de variavel da base, com valores novos), depois os componentes
   que so existem aqui.                                                       */
:root{
  --verde-sino:#0C6E47;--verde-fundo:#09512F;--ouro:#F6C445;--gelo:#F5FAF7;--carvao:#0F1E16;
  --creme:#F5FAF7;--mel:#0C6E47;--mel-claro:#F6C445;--mel-hover:#0a5c3b;--mel-deco:#0C6E47;
  --oliva:#0C6E47;--oliva-escuro:#09512F;--verde:#D8EEE2;--verde-suave:#CFE8DC;--verde-texto:#1F8E5F;
  --marfim:#F5FAF7;--titulo:#F5FAF7;--carimbo:#09512F;--tinta:#0F1E16;--traco:#F6C445;
  --card:#ffffff;--tinta-2:#4C5F55;--divisoria:rgba(15,30,22,.12);--neutro-bg:rgba(12,110,71,.08);--bom-bg:#DCF0E5;
  --sombra-card:0 12px 30px -16px rgba(9,81,47,.5);
  --vermelho:#CC3327;--verm-bg:#FDECEA;--verm-ink:#B3231C;--ambar-bg:#FDF3D6;--ambar-ink:#7A5A00;--bom:#0C6E47;
  --azul:#0C6E47;--rubro:#CC3327;--rubro-area:#9C2A22;
  --tabela-bg:#ffffff;--tabela-cabeca:#EDF6F1;--tabela-linha:rgba(15,30,22,.10);--tabela-ink:#0F1E16;
  --tabela-muted:#4C5F55;--tabela-hover:rgba(12,110,71,.05);
  --r-colmeia:clamp(26px,3vw,44px);--r-card:clamp(18px,1.8vw,26px);
}
/* a entrada animada esconde os elementos DESTE cabecalho, nao os do tema mel */
.anim .sino-logo,.anim .sino-nome,.anim .menu-sino .grid-item{opacity:0}

/* ---- marca: losango + SINO / Gestao ---------------------------------------
   As duas caixas (.sino-logo e .sino-sub) tem tamanho fixo e nunca mudam: o
   losango e o "Gestao" animam DENTRO delas. Por isso o hover nao empurra nada. */
.marca-sino{align-items:center;gap:clamp(10px,1.4vw,24px)}
.sino-logo{position:relative;flex:0 0 auto;display:block;
  width:clamp(50px,8.6vh,100px);height:clamp(50px,8.6vh,100px)}
.sino-losango-svg{position:absolute;inset:0;width:100%;height:100%;display:block;overflow:visible;
  filter:drop-shadow(0 8px 16px rgba(9,81,47,.22))}
.sino-texto{position:relative;display:flex;flex-direction:column;justify-content:center;min-width:0}
.sino-nome{display:block;font:800 clamp(26px,min(5.4vh,3.1vw),54px)/1 var(--sans);
  letter-spacing:-.035em;color:var(--verde-sino)}
/* "Gestao" e ABSOLUTO, pendurado abaixo de "SINO": fica fora do fluxo, entao a
   altura de .sino-texto passa a ser so a do "SINO" e o align-items:center da
   .marca-sino centraliza o SINO com o icone. Enquanto ele contava no fluxo, a
   coluna (SINO + linha reservada) e que ficava centrada, e o SINO subia.
   Fora do fluxo o ganho de "nao empurra nada" fica ainda mais forte: agora ele
   nao ocupa espaco algum, em nenhum momento.                                 */
.sino-sub{position:absolute;top:100%;left:0;display:block;height:1.24em;overflow:visible;
  font-size:clamp(13px,min(2.4vh,1.35vw),24px);line-height:1.24}
.sino-sub-txt{display:inline-block;font:700 1em/1.24 var(--sans);letter-spacing:-.02em;
  color:var(--verde-fundo);opacity:0;transform-origin:0 50%;will-change:transform,opacity}
/* sem GSAP (ou com movimento reduzido) o mesmo efeito, em CSS e sem reflow */
html:not(.gsap) .sino-losango-svg{transition:opacity .35s var(--ease),transform .45s var(--ease)}
html:not(.gsap) .sino-sub-txt{transition:opacity .35s var(--ease) .1s}
html:not(.gsap) .sino-logo:hover .sino-losango-svg,
html:not(.gsap) .marca-sino:focus-visible .sino-losango-svg{opacity:0;transform:scale(.28) rotate(135deg)}
html:not(.gsap) .sino-logo:hover ~ .sino-texto .sino-sub-txt,
html:not(.gsap) .marca-sino:focus-visible .sino-sub-txt{opacity:1}

/* ---- menu em blocos (Staggered Grid Reveal) ---- */
.menu-sino-caixa{flex:0 0 auto;display:flex;align-items:center}
/* COLUNA COM LARGURA EXPLICITA, NAO 1fr -- nao troque de volta.
   Este grid vive dentro de .menu-sino-caixa, que e flex:0 0 auto: largura
   shrink-to-fit, ou seja, dimensionamento INTRINSECO. Em minmax(0,1fr) a funcao
   de minimo e 0 (nao e intrinseca), entao a base da trilha nunca cresce com o
   conteudo; sob restricao de max-content a fracao fr resolve para 0 e as tres
   colunas ficam com 0px. O resultado e o menu sumir: os seis blocos se empilham
   num filete de ~19px (so as duas lacunas) no canto direito.
   O .grade da base usa 1fr sem problema porque mora dentro de .secao, que e
   position:absolute;inset:0 -- largura DEFINIDA. Aqui nao ha.               */
.menu-sino{display:grid;--bloco-w:clamp(78px,7vw,116px);
  grid-template-columns:repeat(3,var(--bloco-w));
  gap:clamp(6px,.9vh,12px);justify-items:stretch}
.menu-sino .grid-item{display:flex;align-items:center;justify-content:center;text-align:center;
  min-width:0;min-height:clamp(36px,6.2vh,58px);
  padding:6px clamp(7px,.8vw,14px);border-radius:clamp(10px,1vw,16px);
  background:var(--verde-sino);color:#fff;text-decoration:none;
  font:700 clamp(10.5px,1.5vh,13.5px)/1.15 var(--sans);letter-spacing:-.01em;
  box-shadow:0 8px 18px -12px rgba(9,81,47,.65);
  transition:background .22s var(--ease),color .22s var(--ease),box-shadow .22s var(--ease)}
/* TRANSFORM FICA FORA DA TRANSICAO ATE O GSAP SOLTAR -- nao devolva para a linha
   de cima. Transition em transform num elemento que o GSAP anima por transform e
   conflito documentado, e foi o que escondeu o menu: a entrada ficou presa em
   transform:scale(0,0) com opacity:1 -- a propriedade COM transition congelou no
   estado inicial, a SEM transition (opacity) terminou normalmente. O JS poe
   .pronto quando a entrada acaba e devolve os blocos ao CSS (clearProps); dai em
   diante o transform pode transicionar sem disputar com ninguem. Mesmo padrao do
   losango, que resolve isso por html:not(.gsap).                              */
.menu-sino.pronto .grid-item{transition:background .22s var(--ease),color .22s var(--ease),
  box-shadow .22s var(--ease),transform .22s var(--ease)}
.menu-sino .grid-item:hover{background:#0f8154;transform:translateY(-2px);box-shadow:0 12px 22px -12px rgba(9,81,47,.75)}
.menu-sino .grid-item.ativa{background:var(--ouro);color:var(--verde-fundo);box-shadow:0 10px 20px -12px rgba(122,90,0,.6)}
.menu-sino .grid-item.ativa:hover{background:#f7cd5f}
.menu-sino .grid-item:focus-visible{outline:2px solid var(--verde-fundo);outline-offset:3px}

/* ---- carimbo e cabecalho de secao ---- */
.carimbo{font-weight:700;color:var(--verde-fundo)}
.secao .subtitulo{color:var(--ouro)}
.titulo-secao{letter-spacing:-.015em}
.secao-cabeca.empilhada{display:block}
.secao-cabeca.empilhada .subtitulo{margin-top:.1em}

/* ---- faixa que abre um grupo de cards ---- */
.faixa-grupo{display:flex;align-items:center;flex-wrap:wrap;gap:8px clamp(12px,1.4vw,22px);margin:2px 0 0}
.faixa-grupo-texto{font-size:var(--t-meta);line-height:1.5;font-weight:600;color:rgba(245,250,247,.9);max-width:86ch}
.pilula-fonte{display:inline-flex;align-items:center;gap:8px;flex:0 0 auto;
  background:#fff;color:var(--verde-sino);border-radius:999px;
  padding:7px clamp(14px,1.2vw,20px);font:700 var(--t-meta)/1.2 var(--sans);letter-spacing:.01em;
  box-shadow:0 8px 18px -14px rgba(9,81,47,.7)}
.pilula-fonte .ponto{width:8px;height:8px;border-radius:50%;background:var(--verde-sino)}
.pilula-fonte.licencas{color:var(--ambar-ink)}
.pilula-fonte.licencas .ponto{background:var(--ouro)}

/* ---- card do tema SINO ---- */
.card-sino{align-items:center;text-align:center;gap:6px;padding:clamp(16px,1.7vw,26px)}
.sino-card-titulo{font:800 var(--t-card)/1.15 var(--sans);letter-spacing:-.02em;
  color:var(--verde-sino);text-wrap:balance}
.sino-card-sub{font:700 var(--t-meta)/1.3 var(--sans);color:var(--tinta-2)}
.sino-num{display:flex;align-items:baseline;justify-content:center;gap:.14em;font-size:var(--t-num-2)}
.kpi-seta{font:700 .42em/1 var(--sans);letter-spacing:0;color:var(--bom)}
.kpi-seta.ruim{color:var(--verm-ink)}
.sino-juizo{justify-content:center;min-height:0}
.card-sino .badge{align-self:center}
.sino-rodape{font-size:var(--t-meta);text-align:center}
.card-sino .kpi-explica{width:100%;text-align:left;margin-top:auto}
.card-sino:hover{transform:translateY(-3px);box-shadow:0 20px 38px -18px rgba(9,81,47,.6)}

/* ---- componentes da base que tinham cor cravada ---- */
.pilula:hover{background:#c6e8d7}
.slide-corpo em{color:var(--verde-sino)}
.dist-preenche.aviso{background:var(--ouro)}

/* ---- telas estreitas ---- */
@media (max-width:899px){
  .sino-logo{width:54px;height:54px}
  .sino-nome{font-size:26px}
  .sino-sub{font-size:13px}
  .menu-sino-caixa{flex:1 0 100%}
  /* aqui 1fr e correto: .menu-sino-caixa e flex:1 0 100% e o grid tem width:100%,
     ou seja, largura definida -- o caso oposto ao de cima. */
  .menu-sino{width:100%;grid-template-columns:repeat(3,minmax(0,1fr))}
  .menu-sino .grid-item{min-width:0;min-height:38px}
  .faixa-grupo-texto{max-width:none}
}

/* ---- impressao: nada escondido pela animacao ---- */
@media print{
  .sino-logo,.sino-nome,.menu-sino .grid-item{opacity:1 !important;transform:none !important}
  .sino-sub{display:none}
  .menu-sino-caixa{display:none}
}
"""


# --- JS: cabecalho do tema SINO (entrada, Staggered Grid Reveal, hover da marca) ---
JS_HEADER_SINO = """
(function(){
  "use strict";
  var doc = document, win = window, raiz = doc.documentElement;
  var soltar = function(){ raiz.classList.remove("anim"); };
  var rm = !!(win.matchMedia && win.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var g = win.gsap;
  if (!g || rm) { soltar(); return; }   // sem GSAP o efeito vira transicao CSS (html:not(.gsap))
  raiz.classList.add("gsap");
  try {
    var caixa = doc.querySelector(".sino-logo");
    var losango = doc.querySelector(".sino-losango-svg");
    var nome = doc.querySelector(".sino-nome");
    var sub = doc.querySelector(".sino-sub");
    var subTxt = doc.querySelector(".sino-sub-txt");
    var marca = doc.querySelector(".marca-sino");
    var menu = doc.querySelector(".menu-sino");
    var blocos = doc.querySelectorAll(".menu-sino .grid-item");
    if (!caixa || !losango || !nome || !sub || !subTxt || !marca) { soltar(); return; }
    soltar();  // o CSS volta ao estado final agora; os "from" abaixo aplicam o inicial no mesmo quadro

    // --- entrada da pagina ---
    // O menu usa o Staggered Grid Reveal (gsapify) tal como publicado, sem o
    // scrollTrigger: o assets/gsap.min.js e o core e nao traz o plugin, e o menu
    // ja nasce visivel no topo -- nao havia o que disparar por scroll.
    var entrada = g.timeline({ defaults: { ease: "power3.out" } })
     .from(losango, { scale: .25, rotation: -140, opacity: 0, duration: .95,
                      transformOrigin: "50% 50%", ease: "back.out(1.5)" }, 0)
     .from(nome, { yPercent: 38, opacity: 0, duration: .75 }, .2)
     .from(blocos, { scale: 0, opacity: 0, duration: .4,
                     stagger: { amount: .6, from: "center" }, ease: "back.out(1.7)",
                     clearProps: "transform,opacity" }, .35);

    // O MENU NAO PODE DEPENDER DA ANIMACAO PARA EXISTIR.
    // Quando a entrada acaba, os blocos voltam a ser 100% CSS: sem transform
    // inline (senao o :hover, que tambem usa transform, nao teria efeito) e com
    // .pronto no contenedor, que devolve o transform para a transicao do hover.
    // O setTimeout e rede de seguranca, nao enfeite: se a linha do tempo travar
    // -- foi o que aconteceu com a transition em transform --, o menu aparece
    // assim mesmo, estatico, em vez de sumir.
    var liberar = function(){
      g.killTweensOf(blocos);
      g.set(blocos, { clearProps: "transform,opacity,translate,rotate,scale" });
      if (menu) menu.classList.add("pronto");
    };
    entrada.eventCallback("onComplete", liberar);
    win.setTimeout(liberar, 2400);

    // --- hover: o losango se transforma no escrito "Gestao" ---
    // Quem escuta o mouse e a CAIXA do losango, que nunca se move; o que anima e
    // o SVG dentro dela. Se o gatilho fosse o proprio SVG, ele sairia de baixo do
    // cursor no meio da animacao e o hover piscaria.
    var tl = null, pendente = null;
    function destino(){
      var a = caixa.getBoundingClientRect(), b = sub.getBoundingClientRect();
      return { x: (b.left + b.height * .34) - (a.left + a.width / 2),
               y: (b.top + b.height / 2) - (a.top + a.height / 2) };
    }
    function montar(){
      var d = destino();
      return g.timeline({ paused: true })
        .to(losango, { x: d.x, y: d.y, rotation: 135, scale: .16, opacity: 0,
                       duration: .55, ease: "power2.inOut", transformOrigin: "50% 50%" }, 0)
        .fromTo(subTxt, { opacity: 0, scale: .45, xPercent: -10 },
                        { opacity: 1, scale: 1, xPercent: 0, duration: .5,
                          ease: "back.out(1.7)", transformOrigin: "0% 50%" }, .22);
    }
    // UM ESTADO SO: aberto = mouse em cima OU foco de TECLADO.
    // Antes o pointer e o foco mandavam na mesma linha do tempo por conta
    // propria. Clicar no logo tambem da foco ao link, entao depois do clique o
    // foco dizia "aberto" enquanto o pointerleave mandava fechar: as duas ordens
    // se atropelavam e a volta do icone saia engasgada. Com :focus-visible o
    // clique de mouse nao conta como foco, e so sobra uma fonte de verdade.
    var sobre = false, focado = false;
    function sincronizar(){
      var aberto = sobre || focado;
      if (!tl) {
        if (!aberto) return;          // nada a fechar: a linha do tempo nem existe
        tl = montar();
      }
      tl.timeScale(aberto ? 1 : 1.35);  // a volta e um pouco mais rapida que a ida
      if (aberto) tl.play(); else tl.reverse();
    }
    caixa.addEventListener("pointerenter", function(){ sobre = true; sincronizar(); });
    caixa.addEventListener("pointerleave", function(){ sobre = false; sincronizar(); });
    marca.addEventListener("focus", function(){
      var teclado = true;
      try { teclado = marca.matches(":focus-visible"); } catch (e) {}
      focado = teclado;
      sincronizar();
    });
    marca.addEventListener("blur", function(){ focado = false; sincronizar(); });

    // redimensionou: a geometria do destino mudou; a linha do tempo e refeita na proxima vez
    win.addEventListener("resize", function(){
      if (!tl) return;
      win.clearTimeout(pendente);
      pendente = win.setTimeout(function(){
        if (!tl) return;
        tl.pause(0).kill(); tl = null;
        g.set(losango, { clearProps: "all" });
        g.set(subTxt, { clearProps: "all" });
        sincronizar();   // se o mouse ainda estiver em cima, remonta e reabre
      }, 180);
    });
  } catch (e) { soltar(); }
})();
"""
