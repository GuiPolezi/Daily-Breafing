"""Gera dashboard.html: painel estático e autocontido do briefing diário.

Lê historico/metricas.jsonl, os JSONs de dados/ e relatorio.md e escreve um
único arquivo HTML com CSS, dados e Chart.js embutidos (funciona offline e
copiado sozinho para qualquer pasta). Nunca lança exceção por dado ausente:
cada seção degrada para "fonte indisponível" e o restante é gerado.

Configuração (.env):
  DASHBOARD_MOSTRAR_RANKING=true|false  (default true)
  DASHBOARD_DIAS_GRAFICO=30             (default 30)
"""

from __future__ import annotations

import html
import json
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
SAIDA = RAIZ / "dashboard.html"

# Baixado UMA vez para assets/; depois disso o dashboard não depende de rede.
CHART_JS_URL = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"
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


def carregar_chart_js() -> str | None:
    """Conteúdo do Chart.js para embutir. Baixa uma única vez se faltar."""
    if not CHART_JS.exists():
        try:
            ASSETS.mkdir(exist_ok=True)
            with urllib.request.urlopen(CHART_JS_URL, timeout=10) as resp:
                conteudo = resp.read().decode("utf-8")
            CHART_JS.write_text(conteudo, encoding="utf-8")
            print(f"Chart.js baixado para {CHART_JS}")
        except Exception as e:  # sem rede: segue sem gráficos
            print(f"AVISO: Chart.js indisponivel ({type(e).__name__}); dashboard sem graficos")
            return None
    try:
        js = CHART_JS.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not js.strip():
        return None
    # Segurança ao embutir em <script>: nunca fechar a tag por acidente.
    return js.replace("</script", "<\\/script")


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
# Markdown -> HTML (conversão manual básica)
# ----------------------------------------------------------------------------
def inline_md(texto: str) -> str:
    t = esc(texto)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<em>\1</em>", t)
    return t


def markdown_para_html(md: str) -> str:
    saida: list[str] = []
    lista_aberta: str | None = None  # "ul" | "ol"
    paragrafo: list[str] = []

    def fechar_lista():
        nonlocal lista_aberta
        if lista_aberta:
            saida.append(f"</{lista_aberta}>")
            lista_aberta = None

    def fechar_paragrafo():
        nonlocal paragrafo
        if paragrafo:
            saida.append("<p>" + " ".join(paragrafo) + "</p>")
            paragrafo = []

    for linha in md.splitlines():
        bruto = linha.rstrip()
        if not bruto.strip():
            fechar_paragrafo()
            fechar_lista()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", bruto)
        if m:
            fechar_paragrafo()
            fechar_lista()
            nivel = min(len(m.group(1)) + 1, 4)  # h1 do md vira h2 (o h1 é do dashboard)
            saida.append(f"<h{nivel}>{inline_md(m.group(2))}</h{nivel}>")
            continue
        m = re.match(r"^\s*[-*+]\s+(.*)$", bruto)
        if m:
            fechar_paragrafo()
            if lista_aberta != "ul":
                fechar_lista()
                saida.append("<ul>")
                lista_aberta = "ul"
            saida.append(f"<li>{inline_md(m.group(1))}</li>")
            continue
        m = re.match(r"^\s*\d+[.)]\s+(.*)$", bruto)
        if m:
            fechar_paragrafo()
            if lista_aberta != "ol":
                fechar_lista()
                saida.append("<ol>")
                lista_aberta = "ol"
            saida.append(f"<li>{inline_md(m.group(1))}</li>")
            continue
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", bruto):
            fechar_paragrafo()
            fechar_lista()
            saida.append("<hr>")
            continue
        if lista_aberta and bruto.startswith("  "):
            # continuação de item de lista
            saida[-1] = saida[-1][:-5] + " " + inline_md(bruto.strip()) + "</li>"
            continue
        fechar_lista()
        paragrafo.append(inline_md(bruto.strip()))

    fechar_paragrafo()
    fechar_lista()
    return "\n".join(saida)


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
# Renderização
# ----------------------------------------------------------------------------
CSS = """
:root{
  --bg:#f5f4f0;--surface:#ffffff;--border:#e6e4de;--grid:#eceae4;
  --ink:#151515;--ink-2:#52514e;--ink-3:#8a8880;
  --accent:#2a78d6;--serie-1:#2a78d6;--serie-2:#eb6834;
  --ok:#0ca30c;--ok-bg:#e9f7e9;--amber:#b87400;--amber-bg:#fff4d6;--amber-line:#fab219;
  --red:#b32b2b;--red-bg:#fbe9e9;--red-line:#d03b3b;--orange:#b04f27;--orange-bg:#fdeee6;
  --radius:12px;--shadow:0 1px 2px rgba(0,0,0,.05);
}
*{box-sizing:border-box}
html{color-scheme:light}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  padding-block:0 32px;padding-inline:16px}
.wrap{max-width:1120px;margin:0 auto}
header.top{padding:28px 0 12px;display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:8px 16px}
header.top h1{font-size:1.5rem;margin:0;letter-spacing:-.01em}
header.top .meta{color:var(--ink-2);font-size:.9rem}
.banner{border-radius:var(--radius);padding:12px 16px;margin:8px 0 16px;font-size:.95rem;display:flex;gap:10px;align-items:flex-start}
.banner.red{background:var(--red-bg);border:1px solid var(--red-line);color:var(--red)}
.banner.amber{background:var(--amber-bg);border:1px solid var(--amber-line);color:var(--amber)}
.banner.muted{background:#f0efec;border:1px solid var(--border);color:var(--ink-2)}
section{margin:22px 0}
section>h2{font-size:1.05rem;margin:0 0 10px;color:var(--ink);font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(210px,100%),1fr));gap:12px}
.card{min-width:0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow);
  border-left-width:5px;border-left-color:var(--border);min-height:106px}
.card .label{font-size:.8rem;color:var(--ink-2);text-transform:uppercase;letter-spacing:.04em}
.card .value{font-size:2rem;font-weight:700;line-height:1.15;margin:4px 0 2px;font-variant-numeric:tabular-nums}
.card .sub{font-size:.85rem;color:var(--ink-2)}
.card .delta{font-size:.8rem;color:var(--ink-3);margin-top:2px}
.card.red{border-left-color:var(--red-line);background:linear-gradient(90deg,var(--red-bg),var(--surface) 55%)}
.card.amber{border-left-color:var(--amber-line);background:linear-gradient(90deg,var(--amber-bg),var(--surface) 55%)}
.card.ok{border-left-color:var(--ok)}
.card.muted .value{color:var(--ink-3)}
.panel{min-width:0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow)}
.panel h3{margin:0 0 4px;font-size:.95rem;font-weight:600}
.panel .hint{font-size:.8rem;color:var(--ink-2);margin:0 0 8px}
.charts{display:grid;grid-template-columns:1fr;gap:12px}
@media(min-width:820px){.charts.two{grid-template-columns:1fr 1fr}}
.chart-box{position:relative;height:250px;width:100%}
.chart-box.tall{height:auto;min-height:220px}
.empty{color:var(--ink-3);font-style:italic;padding:12px 0}
details.dados{margin-top:8px;font-size:.85rem}
details.dados summary{cursor:pointer;color:var(--accent)}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.9rem}
table.lic{min-width:560px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}
th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--ink-2);font-weight:600;background:#faf9f6}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tbody tr:hover{background:#faf9f6}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:999px;font-size:.78rem;font-weight:600;white-space:nowrap}
.badge::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.st-critico{background:var(--red-bg);color:var(--red)}
.st-serio{background:var(--orange-bg);color:var(--orange)}
.st-atencao{background:var(--amber-bg);color:var(--amber)}
.st-ok{background:var(--ok-bg);color:#0a6f0a}
.st-off{background:#f0efec;color:var(--ink-2)}
.briefing{line-height:1.6}
.briefing h2{font-size:1.15rem;margin:4px 0 12px}
.briefing h3{font-size:1rem;margin:18px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--border)}
.briefing h4{font-size:.95rem;margin:14px 0 4px}
.briefing ul,.briefing ol{padding-left:22px;margin:6px 0}
.briefing li{margin:3px 0}
.briefing code{background:#f0efec;padding:1px 5px;border-radius:4px;font-size:.88em}
.briefing p{margin:8px 0}
footer{margin-top:28px;padding-top:14px;border-top:1px solid var(--border);color:var(--ink-2);font-size:.85rem}
footer ul{list-style:none;padding:0;margin:8px 0 0;display:flex;flex-wrap:wrap;gap:8px 20px}
footer li{display:flex;align-items:center;gap:8px}
footer .dot{width:9px;height:9px;border-radius:50%;background:var(--ok);flex:none}
footer .dot.desatualizada{background:var(--red-line)}
footer .dot.indisponivel{background:var(--ink-3)}
.note{font-size:.8rem;color:var(--ink-3);margin-top:6px}
@media(max-width:520px){.card .value{font-size:1.6rem}header.top h1{font-size:1.25rem}}
@media print{body{background:#fff}.panel,.card{box-shadow:none}}
"""


def render_card(rotulo, valor, sub="", delta="", tom="") -> str:
    return (
        f'<div class="card {tom}"><div class="label">{esc(rotulo)}</div>'
        f'<div class="value">{valor}</div>'
        f'<div class="sub">{sub}</div>'
        + (f'<div class="delta">{delta}</div>' if delta else "")
        + "</div>"
    )


def delta_txt(atual, anterior, rotulo="vs. dia anterior") -> str:
    try:
        d = int(atual) - int(anterior)
    except (TypeError, ValueError):
        return ""
    if d == 0:
        return f"sem variação {rotulo}"
    sinal = "+" if d > 0 else "−"
    return f"{sinal}{abs(d)} {rotulo}"


def render_tabela_simples(cabecalhos: list[str], linhas: list[list], num_cols: set[int] = frozenset()) -> str:
    th = "".join(f'<th class="{"num" if i in num_cols else ""}">{esc(c)}</th>' for i, c in enumerate(cabecalhos))
    tr = "".join(
        "<tr>" + "".join(
            f'<td class="{"num" if i in num_cols else ""}">{fmt_num(v) if i in num_cols else esc(v)}</td>'
            for i, v in enumerate(l)
        ) + "</tr>"
        for l in linhas
    )
    return f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


def gerar_html() -> str:
    agora = datetime.now()
    historico = ler_historico()
    relatorio = ler_relatorio()
    chart_js = carregar_chart_js()

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

    # --- registros anterior/atual do histórico (para deltas)
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

    # --- nomes de técnicos (para redigir quando ranking desligado)
    nomes_tecnicos = coletar_nomes_tecnicos(historico, helpdesk)

    # ------------------------------------------------------------------ cards
    hd_ruim = fontes["helpdesk"]["estado"] != "ok"
    lic_ruim = fontes["licencas"]["estado"] != "ok"

    def tom_fonte(chave, base=""):
        est = fontes[chave]["estado"]
        if est == "desatualizada":
            return "red"
        if est == "indisponivel":
            return "muted"
        return base

    if helpdesk:
        card_atend = render_card(
            "Atendimentos fechados (ontem)",
            fmt_num(atend.get("total_atendimentos_fechados")),
            f"dia de referência: {esc(atend.get('dia') or '—')}",
            delta_txt(atend.get("total_atendimentos_fechados"), anterior.get("atend_total")),
            tom_fonte("helpdesk"),
        )
        card_fila = render_card(
            "Fila de chamados",
            fmt_num(helpdesk.get("fila_total_abertos")),
            "chamados abertos no help desk",
            delta_txt(helpdesk.get("fila_total_abertos"), anterior.get("fila_abertos")),
            tom_fonte("helpdesk"),
        )
        meus = helpdesk.get("meus_abertos")
        por_agente = helpdesk.get("por_agente") if isinstance(helpdesk.get("por_agente"), dict) else {}
        equipe = len(por_agente) > 1
        sub_meus = f"novos hoje: {fmt_num(helpdesk.get('meus_novos_hoje'))}"
        if equipe and MOSTRAR_RANKING:
            quebra = " · ".join(f"{esc(n)} {fmt_num((v or {}).get('abertos'))}" for n, v in por_agente.items())
            sub_meus += f"<br>{quebra}"
        elif equipe:
            sub_meus += f" · {len(por_agente)} agentes monitorados"
        card_meus = render_card(
            "Chamados abertos da equipe" if equipe else "Meus chamados abertos",
            fmt_num(meus),
            sub_meus,
            delta_txt(meus, anterior.get("meus_abertos")),
            tom_fonte("helpdesk", "ok" if meus == 0 else ""),
        )
    else:
        card_atend = render_card("Atendimentos fechados (ontem)", "—", "fonte indisponível", "", "muted")
        card_fila = render_card("Fila de chamados", "—", "fonte indisponível", "", "muted")
        card_meus = render_card("Chamados abertos (meus/equipe)", "—", "fonte indisponível", "", "muted")

    if licencas:
        tom_lic = "red" if n_vencidas > 0 else ("amber" if n_vencendo > 0 else "ok")
        if fontes["licencas"]["estado"] == "desatualizada":
            tom_lic = "red"
        card_lic = render_card(
            "Licenças em risco",
            f"{fmt_num(n_vencendo)} <span style='font-size:1rem;font-weight:500;color:var(--ink-2)'>vencendo</span>",
            f"<strong>{fmt_num(n_vencidas)}</strong> vencidas recentemente"
            + (f" · {fmt_num(vencidas_antigas)} antigas" if vencidas_antigas not in (None, "") else ""),
            delta_txt(n_vencidas, anterior.get("lic_vencidas_recentes"), "vencidas vs. dia anterior"),
            tom_lic,
        )
    else:
        card_lic = render_card("Licenças em risco", "—", "fonte indisponível", "", "muted")

    cards_html = card_atend + card_fila + card_meus + card_lic

    # ------------------------------------------------------------------ banner
    banner = ""
    if fontes_desatualizadas or fontes_indisponiveis or n_vencidas > 0:
        partes = []
        if fontes_desatualizadas:
            partes.append("Fonte desatualizada (coleta há mais de 24 h): " + ", ".join(esc(x) for x in fontes_desatualizadas))
        if fontes_indisponiveis:
            partes.append("Fonte indisponível: " + ", ".join(esc(x) for x in fontes_indisponiveis))
        if n_vencidas > 0:
            partes.append(f"{n_vencidas} licença(s) vencida(s) recentemente")
        tom = "red" if (fontes_desatualizadas or n_vencidas > 0) else "muted"
        banner = f'<div class="banner {tom}"><span>⚠</span><div>' + "<br>".join(partes) + "</div></div>"
    elif n_vencendo > 0:
        banner = f'<div class="banner amber"><span>●</span><div>{n_vencendo} licença(s) vencendo em breve; nenhuma vencida recente.</div></div>'

    # ------------------------------------------------------------------ séries
    serie = serie_historico(historico, DIAS_GRAFICO)
    rank = ranking_semanal(historico)
    tem_historico = bool(serie["labels"])

    aviso_graficos = ""
    if chart_js is None:
        aviso_graficos = '<p class="note">Gráficos indisponíveis nesta geração (biblioteca de gráficos não encontrada). Os dados seguem nas tabelas abaixo.</p>'

    # (c) linhas: dois painéis (um por medida), eixos independentes
    if tem_historico:
        tabela_hist = render_tabela_simples(
            ["Data", "Fila de chamados", "Atendimentos fechados"],
            [[label_dia(d), f, a] for d, f, a in zip(serie["datas"], serie["fila"], serie["atend"])][::-1],
            {1, 2},
        )
        n_dias = len(serie["labels"])
        secao_linhas = f"""
<div class="charts two">
  <div class="panel"><h3>Fila de chamados abertos</h3><p class="hint">últimos {n_dias} dia(s) registrado(s)</p>
    <div class="chart-box"><canvas id="chartFila" role="img" aria-label="Evolução da fila de chamados"></canvas></div></div>
  <div class="panel"><h3>Atendimentos fechados por dia</h3><p class="hint">referente ao último dia útil de cada registro</p>
    <div class="chart-box"><canvas id="chartAtend" role="img" aria-label="Evolução de atendimentos fechados"></canvas></div></div>
</div>
<details class="dados"><summary>Ver dados da série</summary>{tabela_hist}</details>"""
    else:
        secao_linhas = '<div class="panel"><p class="empty">Histórico indisponível (historico/metricas.jsonl vazio ou ausente).</p></div>'

    # (d) barras: ranking por técnico OU total da equipe por dia
    if MOSTRAR_RANKING and rank["tecnicos"]:
        altura = max(200, 40 + 34 * len(rank["tecnicos"]))
        tabela_rank = render_tabela_simples(
            ["Técnico", "Atendimentos"], [[n, v] for n, v in zip(rank["tecnicos"], rank["valores"])], {1})
        secao_barras = f"""
<div class="panel"><h3>Ranking semanal por técnico</h3>
  <p class="hint">soma dos últimos {len(rank['dias'])} dia(s) útil(eis) registrado(s): {esc(', '.join(rank['dias']))} · total {fmt_num(rank['total_periodo'])}</p>
  <div class="chart-box tall" style="height:{altura}px"><canvas id="chartRank" role="img" aria-label="Ranking semanal por técnico"></canvas></div>
  <details class="dados"><summary>Ver dados do ranking</summary>{tabela_rank}</details>
</div>"""
    elif rank["dias"]:
        tabela_rank = render_tabela_simples(
            ["Dia", "Atendimentos (equipe)"], [[d, t] for d, t in zip(rank["dias"], rank["totais"])], {1})
        secao_barras = f"""
<div class="panel"><h3>Atendimentos da equipe por dia</h3>
  <p class="hint">últimos {len(rank['dias'])} dia(s) útil(eis) registrado(s) · ranking por técnico desativado (DASHBOARD_MOSTRAR_RANKING=false)</p>
  <div class="chart-box"><canvas id="chartRank" role="img" aria-label="Atendimentos da equipe por dia"></canvas></div>
  <details class="dados"><summary>Ver dados</summary>{tabela_rank}</details>
</div>"""
    else:
        secao_barras = '<div class="panel"><p class="empty">Sem registros de atendimentos no histórico.</p></div>'

    # (e) tabela de licenças
    if licencas is None:
        secao_lic = '<div class="panel"><p class="empty">Fonte indisponível: licenças.</p></div>'
    elif not lic_itens:
        secao_lic = '<div class="panel"><p class="empty">Nenhuma licença vencida recentemente ou vencendo em breve.</p></div>'
    else:
        linhas = []
        for it in lic_itens:
            cls, rot = classificar_licenca(it)
            d = it["dias"]
            if d is None:
                dias_txt = "—"
            elif d < 0:
                dias_txt = f"há {abs(d)} d"
            elif d == 0:
                dias_txt = "hoje"
            else:
                dias_txt = f"em {d} d"
            linhas.append(
                f"<tr><td><span class='badge {cls}'>{rot}</span></td><td>{esc(it['cliente'])}</td>"
                f"<td>{esc(it['sistema'])}</td><td class='num'>{esc(it['vencimento'])}</td><td class='num'>{dias_txt}</td></tr>"
            )
        nota = ""
        if vencidas_antigas not in (None, ""):
            nota = f'<p class="note">Além destas, {fmt_num(vencidas_antigas)} licença(s) vencida(s) há mais tempo não são listadas aqui.</p>'
        secao_lic = f"""
<div class="panel"><div class="table-wrap"><table class="lic">
<thead><tr><th>Status</th><th>Cliente</th><th>Sistema</th><th class="num">Vencimento</th><th class="num">Prazo</th></tr></thead>
<tbody>{''.join(linhas)}</tbody></table></div>{nota}</div>"""

    # (f) briefing
    if relatorio is None:
        secao_briefing = '<div class="panel"><p class="empty">Briefing indisponível (relatorio.md ausente ou vazio).</p></div>'
    else:
        texto = relatorio if MOSTRAR_RANKING else redigir_nomes(relatorio, nomes_tecnicos)
        secao_briefing = f'<div class="panel briefing">{markdown_para_html(texto)}</div>'

    # (g) rodapé
    itens_fontes = []
    for chave, f in fontes.items():
        estado = f["estado"]
        rotulo_estado = {"ok": "ok", "desatualizada": "desatualizada", "indisponivel": "indisponível"}[estado]
        itens_fontes.append(
            f'<li><span class="dot {estado}"></span><strong>{esc(f["rotulo"])}</strong>'
            f'<span>coletado em {esc(fmt_dt(f["coletado_em"]))} · {rotulo_estado} ({esc(f["detalhe"])})</span></li>'
        )
    if email:
        extra_email = (f'<p class="note">E-mail: {fmt_num(email.get("nao_lidos"))} não lidos · '
                       f'{fmt_num(email.get("recebidos_hoje"))} recebidos hoje · {fmt_num(email.get("spam_hoje"))} spam hoje</p>')
    else:
        extra_email = ""

    # ------------------------------------------------------------------ JS
    payload = {
        "labels": serie["labels"],
        "fila": serie["fila"],
        "atend": serie["atend"],
        "rank": ({"labels": rank["tecnicos"], "valores": rank["valores"], "titulo": "Atendimentos"}
                 if MOSTRAR_RANKING else {"labels": rank["dias"], "valores": rank["totais"], "titulo": "Atendimentos (equipe)"}),
        "rankHorizontal": MOSTRAR_RANKING,
    }
    script = ""
    if chart_js is not None:
        script = f"""
<script>{chart_js}</script>
<script>
(function(){{
  const D = {json_inline(payload)};
  if (typeof Chart === "undefined") return;
  const cs = getComputedStyle(document.documentElement);
  const v = n => cs.getPropertyValue(n).trim();
  const ink2 = v("--ink-2"), grid = v("--grid"), s1 = v("--serie-1"), s2 = v("--serie-2");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = ink2;
  const base = {{
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {{ mode: "index", intersect: false }},
    plugins: {{ legend: {{ display: false }}, tooltip: {{ backgroundColor: "#151515", padding: 10, cornerRadius: 6 }} }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }} }},
      y: {{ beginAtZero: true, grid: {{ color: grid }}, border: {{ display: false }}, ticks: {{ precision: 0 }} }}
    }}
  }};
  const linha = (id, label, dados, cor) => {{
    const el = document.getElementById(id); if (!el) return;
    new Chart(el, {{ type: "line",
      data: {{ labels: D.labels, datasets: [{{ label, data: dados, borderColor: cor, backgroundColor: cor,
        borderWidth: 2, pointRadius: 4, pointHoverRadius: 6, pointBorderColor: "#fff", pointBorderWidth: 2, tension: .25, spanGaps: false }}] }},
      options: base }});
  }};
  linha("chartFila", "Fila de chamados", D.fila, s1);
  linha("chartAtend", "Atendimentos fechados", D.atend, s2);

  const elRank = document.getElementById("chartRank");
  if (elRank && D.rank.labels.length) {{
    const rotulos = {{ id: "rotulos", afterDatasetsDraw(c) {{
      const {{ ctx }} = c; const meta = c.getDatasetMeta(0);
      ctx.save(); ctx.fillStyle = ink2; ctx.font = "600 12px " + Chart.defaults.font.family;
      meta.data.forEach((b, i) => {{
        const val = D.rank.valores[i]; if (val == null) return;
        if (D.rankHorizontal) {{ ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillText(val, b.x + 6, b.y); }}
        else {{ ctx.textAlign = "center"; ctx.textBaseline = "bottom"; ctx.fillText(val, b.x, b.y - 4); }}
      }});
      ctx.restore();
    }} }};
    new Chart(elRank, {{ type: "bar",
      data: {{ labels: D.rank.labels, datasets: [{{ label: D.rank.titulo, data: D.rank.valores, backgroundColor: s1,
        borderRadius: 4, borderSkipped: "start", maxBarThickness: 22, categoryPercentage: .7 }}] }},
      options: Object.assign({{}}, base, {{
        indexAxis: D.rankHorizontal ? "y" : "x",
        layout: {{ padding: {{ right: D.rankHorizontal ? 36 : 0, top: D.rankHorizontal ? 0 : 18 }} }},
        interaction: {{ mode: "nearest", intersect: true }},
        scales: D.rankHorizontal
          ? {{ x: {{ beginAtZero: true, grid: {{ color: grid }}, border: {{ display: false }}, ticks: {{ precision: 0 }} }},
              y: {{ grid: {{ display: false }} }} }}
          : base.scales
      }}),
      plugins: [rotulos] }});
  }}
}})();
</script>"""

    gerado_em = agora.strftime("%d/%m/%Y %H:%M")
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Briefing diário — {esc(gerado_em)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <h1>Briefing diário</h1>
  <div class="meta">Gerado em {esc(gerado_em)} · dados de {esc(date.today().strftime('%d/%m/%Y'))}</div>
</header>
{banner}

<section aria-label="Destaques do dia">
  <h2>Destaques do dia</h2>
  <div class="cards">{cards_html}</div>
</section>

<section aria-label="Evolução">
  <h2>Evolução dos últimos {DIAS_GRAFICO} dias</h2>
  {aviso_graficos}
  {secao_linhas}
</section>

<section aria-label="Produtividade semanal">
  <h2>Produtividade semanal</h2>
  {secao_barras}
</section>

<section aria-label="Licenças críticas">
  <h2>Licenças críticas</h2>
  {secao_lic}
</section>

<section aria-label="Briefing do dia">
  <h2>Briefing do dia</h2>
  {secao_briefing}
</section>

<footer>
  <strong>Status das fontes</strong>
  <ul>{''.join(itens_fontes)}</ul>
  {extra_email}
  <p class="note">Arquivo estático gerado por gerar_dashboard.py. Sem dependências externas; pode ser copiado sozinho.</p>
</footer>
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
