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
  --bg:#f5f5f7;--surface:#ffffff;--line:rgba(0,0,0,.06);--line-2:rgba(0,0,0,.11);--grid:#ececf1;
  --ink:#1d1d1f;--ink-2:#6e6e73;--ink-3:#86868b;
  --accent:#0071e3;--serie-1:#2a78d6;--serie-2:#eb6834;
  --ok:#1f9d55;--ok-ink:#177245;--ok-bg:#e6f6ec;
  --amber:#9a6700;--amber-bg:#fff3d6;--amber-line:#f5b400;
  --red:#c1272d;--red-bg:#fdecec;--red-line:#e5484d;
  --orange:#b4531e;--orange-bg:#fdeee4;
  --r:20px;--r-sm:12px;
  --shadow:0 1px 2px rgba(0,0,0,.03),0 10px 30px rgba(0,0,0,.05);
  --shadow-hover:0 2px 4px rgba(0,0,0,.04),0 20px 48px rgba(0,0,0,.09);
  --ease:cubic-bezier(.2,.7,.2,1);
}
*{box-sizing:border-box}
html{color-scheme:light;scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","SF Pro Display","Segoe UI Variable","Segoe UI",Inter,Roboto,"Helvetica Neue",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;padding-block:0 40px;padding-inline:0;overflow-x:hidden}
.wrap{max-width:1180px;margin:0 auto;padding-inline:clamp(16px,3vw,28px)}

/* fundo ambiente */
.ambient{position:fixed;inset:0;z-index:-1;overflow:hidden;pointer-events:none}
.ambient i{position:absolute;border-radius:50%;filter:blur(70px);opacity:.55;will-change:transform}
.ambient .b1{width:520px;height:520px;left:-140px;top:-160px;background:#cfe1ff;animation:drift 22s ease-in-out infinite}
.ambient .b2{width:460px;height:460px;right:-120px;top:120px;background:#ebe0ff;animation:drift 26s ease-in-out infinite reverse}
.ambient .b3{width:380px;height:380px;left:38%;top:55%;background:#ffe9d6;opacity:.4;animation:drift 30s ease-in-out infinite}

/* barra superior */
.bar{position:sticky;top:0;z-index:20;background:rgba(245,245,247,.72);-webkit-backdrop-filter:saturate(180%) blur(20px);backdrop-filter:saturate(180%) blur(20px);border-bottom:1px solid var(--line)}
.bar .in{max-width:1180px;margin:0 auto;padding:10px clamp(16px,3vw,28px);display:flex;align-items:center;gap:12px 18px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;font-weight:600;letter-spacing:-.01em;white-space:nowrap}
.brand .logo{width:26px;height:26px;border-radius:8px;background:linear-gradient(135deg,#5ac8fa,#0071e3 55%,#5e5ce6);box-shadow:0 2px 6px rgba(0,113,227,.3)}
nav.seg{display:flex;gap:2px;padding:3px;border-radius:999px;background:rgba(0,0,0,.05);margin-inline:auto;max-width:100%;overflow-x:auto;scrollbar-width:none}
nav.seg::-webkit-scrollbar{display:none}
nav.seg a{padding:6px 14px;border-radius:999px;color:var(--ink-2);text-decoration:none;font-size:.84rem;font-weight:500;white-space:nowrap;transition:color .3s var(--ease),background .3s var(--ease),box-shadow .3s var(--ease)}
nav.seg a:hover{color:var(--ink)}
nav.seg a.active{background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.1)}
.stamp{font-size:.78rem;color:var(--ink-2);padding:6px 12px;border-radius:999px;border:1px solid var(--line-2);background:rgba(255,255,255,.65);white-space:nowrap;font-variant-numeric:tabular-nums}
@media(max-width:720px){.bar .in{justify-content:space-between}nav.seg{order:3;flex-basis:100%;margin-inline:0}}

/* hero */
.hero{padding:46px 0 26px}
.hero h1{font-size:clamp(1.9rem,4.2vw,2.9rem);letter-spacing:-.035em;font-weight:700;margin:0;line-height:1.08;animation:fadeUp .8s var(--ease) both}
.hero p{color:var(--ink-2);margin:10px 0 0;font-size:1.05rem;animation:fadeUp .8s var(--ease) .12s both}

/* aviso */
.banner{display:flex;gap:12px;align-items:flex-start;padding:14px 18px;border-radius:16px;margin:0 0 22px;font-size:.93rem;line-height:1.5;border:1px solid;animation:fadeUp .8s var(--ease) .2s both}
.banner .ico{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;font-size:.85rem;font-weight:700;flex:none;background:color-mix(in srgb,currentColor 14%,transparent)}
.banner.red{background:rgba(253,236,236,.85);border-color:rgba(229,72,77,.35);color:var(--red)}
.banner.amber{background:rgba(255,243,214,.85);border-color:rgba(245,180,0,.45);color:var(--amber)}
.banner.muted{background:rgba(255,255,255,.7);border-color:var(--line-2);color:var(--ink-2)}

/* seções */
section{margin:38px 0;scroll-margin-top:76px}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px 16px;flex-wrap:wrap;margin:0 0 14px}
.sec-head h2{font-size:1.4rem;margin:0;font-weight:700;letter-spacing:-.025em}
.sec-head .kicker{font-size:.86rem;color:var(--ink-3)}

/* cards de destaque */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr));gap:14px}
.card{position:relative;min-width:0;background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:20px 22px 18px;box-shadow:var(--shadow);overflow:hidden;
  transition:transform .5s var(--ease),box-shadow .5s var(--ease),border-color .3s}
.card:hover{transform:translateY(-3px);box-shadow:var(--shadow-hover)}
.card .label{display:flex;align-items:center;gap:8px;font-size:.8rem;font-weight:600;color:var(--ink-2);letter-spacing:.01em;position:relative;z-index:1}
.card .label::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--ink-3);opacity:.45;flex:none}
.card.ok .label::before{background:var(--ok);opacity:1;box-shadow:0 0 0 3px rgba(31,157,85,.14)}
.card.amber .label::before{background:var(--amber-line);opacity:1;box-shadow:0 0 0 3px rgba(245,180,0,.18)}
.card.red .label::before{background:var(--red-line);opacity:1;animation:pulse 2.4s ease-in-out infinite}
.card.red{border-color:rgba(229,72,77,.3)}
.card.amber{border-color:rgba(245,180,0,.35)}
.card::after{content:"";position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity .4s}
.card.red::after{opacity:1;background:radial-gradient(120% 90% at 100% 0%,rgba(229,72,77,.12),transparent 60%)}
.card.amber::after{opacity:1;background:radial-gradient(120% 90% at 100% 0%,rgba(245,180,0,.16),transparent 60%)}
.card .value{font-size:2.5rem;font-weight:700;letter-spacing:-.035em;line-height:1.1;margin:10px 0 6px;font-variant-numeric:tabular-nums;position:relative;z-index:1}
.card .value .unit{font-size:1rem;font-weight:500;color:var(--ink-2);letter-spacing:0;margin-left:4px}
.card.muted .value{color:var(--ink-3)}
.card .sub{font-size:.86rem;color:var(--ink-2);line-height:1.45;position:relative;z-index:1}
.pill{display:inline-flex;align-items:center;gap:4px;margin-top:10px;padding:3px 10px;border-radius:999px;font-size:.76rem;font-weight:600;background:rgba(0,0,0,.05);color:var(--ink-2);position:relative;z-index:1;font-variant-numeric:tabular-nums}
.pill.good{background:var(--ok-bg);color:var(--ok-ink)}
.pill.bad{background:var(--red-bg);color:var(--red)}

/* painéis */
.panel{min-width:0;background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:20px 22px;box-shadow:var(--shadow);transition:box-shadow .5s var(--ease)}
.panel:hover{box-shadow:var(--shadow-hover)}
.panel h3{margin:0;font-size:1.02rem;font-weight:600;letter-spacing:-.01em}
.panel .hint{font-size:.82rem;color:var(--ink-3);margin:3px 0 14px}
.charts{display:grid;gap:14px}
@media(min-width:840px){.charts.two{grid-template-columns:1fr 1fr}}
.chart-box{position:relative;height:260px;width:100%}
.chart-box.tall{height:auto;min-height:220px}
.empty{color:var(--ink-3);padding:14px 0;margin:0;text-align:center}

/* detalhes (tabelas colapsáveis) */
details.dados{margin-top:12px}
details.dados summary{list-style:none;cursor:pointer;display:inline-flex;align-items:center;gap:8px;padding:6px 13px;border-radius:999px;font-size:.82rem;font-weight:500;color:var(--accent);background:rgba(0,113,227,.08);transition:background .3s,transform .3s var(--ease);user-select:none}
details.dados summary::-webkit-details-marker{display:none}
details.dados summary:hover{background:rgba(0,113,227,.14)}
details.dados summary::after{content:"";width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:translateY(-2px) rotate(45deg);transition:transform .35s var(--ease)}
details.dados[open] summary::after{transform:translateY(1px) rotate(-135deg)}
details.dados[open]>.table-wrap{animation:fadeUp .45s var(--ease)}
details.dados>.table-wrap{margin-top:12px}

/* tabelas */
.table-wrap{overflow-x:auto;border-radius:var(--r-sm);border:1px solid var(--line);background:#fff}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:.9rem}
table.lic{min-width:600px}
th{text-align:left;background:#fafafa;font-size:.73rem;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3);font-weight:600;padding:10px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
td{text-align:left;padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:middle}
tbody tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tbody tr{transition:background .25s}
tbody tr:hover{background:#f6f6f9}
.badge{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:.76rem;font-weight:600;white-space:nowrap}
.badge::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.st-critico{background:var(--red-bg);color:var(--red)}
.st-serio{background:var(--orange-bg);color:var(--orange)}
.st-atencao{background:var(--amber-bg);color:var(--amber)}
.st-ok{background:var(--ok-bg);color:var(--ok-ink)}
.st-off{background:#f0f0f3;color:var(--ink-2)}

/* briefing */
.briefing{line-height:1.65;padding:26px clamp(20px,3vw,34px)}
.briefing h2{font-size:1.3rem;margin:0 0 14px;letter-spacing:-.02em}
.briefing h3{font-size:1.05rem;margin:26px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--line);letter-spacing:-.01em}
.briefing h4{font-size:.95rem;margin:16px 0 4px}
.briefing ul,.briefing ol{padding-left:22px;margin:6px 0}
.briefing li{margin:4px 0}
.briefing li::marker{color:var(--ink-3)}
.briefing code{background:#f0f0f3;padding:1px 6px;border-radius:6px;font-size:.86em}
.briefing p{margin:8px 0}
.briefing strong{font-weight:600}
.briefing hr{border:0;border-top:1px solid var(--line);margin:18px 0}

/* rodapé / fontes */
footer{margin-top:40px;padding-top:22px;border-top:1px solid var(--line);color:var(--ink-2);font-size:.88rem;scroll-margin-top:76px}
footer .sec-head{margin-bottom:10px}
.fontes{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(270px,100%),1fr));gap:12px}
.fontes li{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;border-radius:14px;background:rgba(255,255,255,.75);border:1px solid var(--line);transition:transform .4s var(--ease),box-shadow .4s var(--ease)}
.fontes li:hover{transform:translateY(-2px);box-shadow:var(--shadow)}
.fontes strong{display:block;color:var(--ink);font-weight:600}
.fontes span.info{font-size:.82rem;color:var(--ink-2)}
.dot{width:10px;height:10px;border-radius:50%;margin-top:5px;flex:none;background:var(--ok);box-shadow:0 0 0 3px rgba(31,157,85,.16)}
.dot.desatualizada{background:var(--red-line);animation:pulse 2.4s ease-in-out infinite}
.dot.indisponivel{background:var(--ink-3);box-shadow:0 0 0 3px rgba(0,0,0,.06)}
.note{font-size:.8rem;color:var(--ink-3);margin:12px 0 0}

/* animações */
@keyframes fadeUp{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(229,72,77,.4)}70%{box-shadow:0 0 0 8px rgba(229,72,77,0)}}
@keyframes drift{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(50px,-36px) scale(1.08)}}
.js .reveal{opacity:0;transform:translateY(18px);transition:opacity .75s var(--ease),transform .75s var(--ease);transition-delay:var(--d,0s)}
.js .reveal.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){
  *{animation:none!important;transition:none!important}
  html{scroll-behavior:auto}
  .js .reveal{opacity:1;transform:none}
}
@media(max-width:520px){.card .value{font-size:2rem}}
@media print{
  body{background:#fff}.ambient,.bar{display:none}
  .panel,.card,.fontes li{box-shadow:none;break-inside:avoid}
  .js .reveal{opacity:1;transform:none}
  details.dados{display:none}
}
"""

# Script de interface: revelação ao rolar, contagem dos números, navegação ativa.
JS_UI = """
(function(){
  var rm = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var els = Array.prototype.slice.call(document.querySelectorAll(".reveal"));
  var mostrar = function(el){ el.classList.add("in"); };
  if ("IntersectionObserver" in window && !rm) {
    var io = new IntersectionObserver(function(es){
      es.forEach(function(e){ if (e.isIntersecting) { mostrar(e.target); io.unobserve(e.target); } });
    }, { threshold: .1, rootMargin: "0px 0px -5% 0px" });
    els.forEach(function(el){ io.observe(el); });
    setTimeout(function(){ els.forEach(mostrar); }, 3000);
  } else { els.forEach(mostrar); }

  var fmt = function(n){ return n.toLocaleString("pt-BR"); };
  Array.prototype.forEach.call(document.querySelectorAll("[data-n]"), function(el){
    var n = Number(el.getAttribute("data-n"));
    if (!isFinite(n) || rm || !window.requestAnimationFrame) return;
    var t0 = null, dur = 1100;
    el.textContent = "0";
    var tick = function(t){
      if (t0 === null) t0 = t;
      var p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(Math.round(n * e));
      if (p < 1) requestAnimationFrame(tick); else el.textContent = fmt(n);
    };
    requestAnimationFrame(tick);
    setTimeout(function(){ el.textContent = fmt(n); }, dur + 400);
  });

  var links = Array.prototype.slice.call(document.querySelectorAll("nav.seg a"));
  var alvos = links.map(function(a){ return document.querySelector(a.getAttribute("href")); }).filter(Boolean);
  if (alvos.length && "IntersectionObserver" in window) {
    var spy = new IntersectionObserver(function(es){
      es.forEach(function(e){
        if (!e.isIntersecting) return;
        links.forEach(function(a){ a.classList.toggle("active", a.getAttribute("href") === "#" + e.target.id); });
      });
    }, { rootMargin: "-35% 0px -55% 0px" });
    alvos.forEach(function(s){ spy.observe(s); });
  }
})();
"""

# Script dos gráficos: __DATA__ é substituído pelo JSON inline. Cada gráfico é
# criado quando o painel entra na tela, para a animação acontecer à vista.
JS_CHARTS = """
(function(){
  var D = __DATA__;
  if (typeof Chart === "undefined") return;
  var rm = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var cs = getComputedStyle(document.documentElement);
  var v = function(n){ return cs.getPropertyValue(n).trim(); };
  var ink2 = v("--ink-2"), ink3 = v("--ink-3"), grid = v("--grid"), s1 = v("--serie-1"), s2 = v("--serie-2");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = ink3;
  var anim = rm ? false : { duration: 900, easing: "easeOutQuart" };
  var tooltip = { backgroundColor: "rgba(29,29,31,.92)", padding: 12, cornerRadius: 12, displayColors: false,
    titleFont: { weight: "600" }, bodyFont: { size: 12 }, caretSize: 6 };
  var base = {
    responsive: true, maintainAspectRatio: false, animation: anim,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: false }, tooltip: tooltip },
    scales: {
      x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
      y: { beginAtZero: true, grid: { color: grid }, border: { display: false, dash: [4, 4] }, ticks: { precision: 0, padding: 6 } }
    }
  };
  var hex = function(c, a){
    var r = parseInt(c.slice(1,3),16), g = parseInt(c.slice(3,5),16), b = parseInt(c.slice(5,7),16);
    return "rgba(" + r + "," + g + "," + b + "," + a + ")";
  };
  var linha = function(el, label, dados, cor){
    var ctx = el.getContext("2d");
    var grad = ctx.createLinearGradient(0, 0, 0, el.parentNode.clientHeight || 260);
    grad.addColorStop(0, hex(cor, .22)); grad.addColorStop(1, hex(cor, 0));
    new Chart(el, { type: "line",
      data: { labels: D.labels, datasets: [{ label: label, data: dados, borderColor: cor, backgroundColor: grad, fill: true,
        borderWidth: 2, pointRadius: 3, pointHoverRadius: 6, pointBackgroundColor: cor, pointBorderColor: "#fff", pointBorderWidth: 2,
        tension: .35, spanGaps: false }] },
      options: base });
  };
  var barras = function(el){
    if (!D.rank.labels.length) return;
    var rotulos = { id: "rotulos", afterDatasetsDraw: function(c){
      var ctx = c.ctx, meta = c.getDatasetMeta(0);
      ctx.save(); ctx.fillStyle = ink2; ctx.font = "600 12px " + Chart.defaults.font.family;
      meta.data.forEach(function(b, i){
        var val = D.rank.valores[i]; if (val == null) return;
        if (D.rankHorizontal) { ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillText(val, b.x + 8, b.y); }
        else { ctx.textAlign = "center"; ctx.textBaseline = "bottom"; ctx.fillText(val, b.x, b.y - 6); }
      });
      ctx.restore();
    } };
    new Chart(el, { type: "bar",
      data: { labels: D.rank.labels, datasets: [{ label: D.rank.titulo, data: D.rank.valores, backgroundColor: s1,
        hoverBackgroundColor: hex(s1, .85), borderRadius: 6, borderSkipped: "start", maxBarThickness: 22, categoryPercentage: .7 }] },
      options: Object.assign({}, base, {
        indexAxis: D.rankHorizontal ? "y" : "x",
        layout: { padding: { right: D.rankHorizontal ? 40 : 0, top: D.rankHorizontal ? 0 : 20 } },
        interaction: { mode: "nearest", intersect: true },
        scales: D.rankHorizontal
          ? { x: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { precision: 0 } },
              y: { grid: { display: false }, border: { display: false }, ticks: { color: ink2 } } }
          : base.scales
      }),
      plugins: [rotulos] });
  };
  var fabricas = {
    chartFila: function(el){ linha(el, "Fila de chamados", D.fila, s1); },
    chartAtend: function(el){ linha(el, "Atendimentos fechados", D.atend, s2); },
    chartRank: barras
  };
  var pendentes = Object.keys(fabricas).map(function(id){ return document.getElementById(id); }).filter(Boolean);
  var montar = function(el){ if (el.dataset.feito) return; el.dataset.feito = "1"; fabricas[el.id](el); };
  if ("IntersectionObserver" in window && !rm) {
    var io = new IntersectionObserver(function(es){
      es.forEach(function(e){ if (e.isIntersecting) { montar(e.target); io.unobserve(e.target); } });
    }, { threshold: .15 });
    pendentes.forEach(function(el){ io.observe(el); });
    setTimeout(function(){ pendentes.forEach(montar); }, 3000);
  } else { pendentes.forEach(montar); }
})();
"""


def num_html(valor) -> str:
    """Número formatado; quando inteiro, ganha data-n para a contagem animada."""
    if valor is None:
        return "—"
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return esc(valor)
    return f'<span data-n="{n}">{fmt_num(n)}</span>'


def render_card(rotulo, valor, sub="", delta="", tom="", indice=0) -> str:
    return (
        f'<div class="card reveal {tom}" style="--d:{indice * 0.08:.2f}s"><div class="label">{esc(rotulo)}</div>'
        f'<div class="value">{valor}</div>'
        f'<div class="sub">{sub}</div>'
        + (delta or "")
        + "</div>"
    )


def delta_pill(atual, anterior, rotulo="vs. dia anterior", melhor="menor") -> str:
    """Variação em relação ao dia anterior. `melhor` diz qual direção é boa."""
    try:
        d = int(atual) - int(anterior)
    except (TypeError, ValueError):
        return ""
    if d == 0:
        return f'<span class="pill">sem variação {esc(rotulo)}</span>'
    subiu = d > 0
    bom = (subiu and melhor == "maior") or (not subiu and melhor == "menor")
    seta = "↑" if subiu else "↓"
    return f'<span class="pill {"good" if bom else "bad"}">{seta} {abs(d)} {esc(rotulo)}</span>'


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


def sec_head(titulo: str, kicker: str = "") -> str:
    k = f'<span class="kicker">{kicker}</span>' if kicker else ""
    return f'<div class="sec-head reveal"><h2>{esc(titulo)}</h2>{k}</div>'


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
            num_html(atend.get("total_atendimentos_fechados")),
            f"dia de referência: {esc(atend.get('dia') or '—')}",
            delta_pill(atend.get("total_atendimentos_fechados"), anterior.get("atend_total"), melhor="maior"),
            tom_fonte("helpdesk"), 0,
        )
        card_fila = render_card(
            "Fila de chamados",
            num_html(helpdesk.get("fila_total_abertos")),
            "chamados abertos no help desk",
            delta_pill(helpdesk.get("fila_total_abertos"), anterior.get("fila_abertos"), melhor="menor"),
            tom_fonte("helpdesk"), 1,
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
            num_html(meus),
            sub_meus,
            delta_pill(meus, anterior.get("meus_abertos"), melhor="menor"),
            tom_fonte("helpdesk", "ok" if meus == 0 else ""), 2,
        )
    else:
        card_atend = render_card("Atendimentos fechados (ontem)", "—", "fonte indisponível", "", "muted", 0)
        card_fila = render_card("Fila de chamados", "—", "fonte indisponível", "", "muted", 1)
        card_meus = render_card("Chamados abertos (meus/equipe)", "—", "fonte indisponível", "", "muted", 2)

    if licencas:
        tom_lic = "red" if n_vencidas > 0 else ("amber" if n_vencendo > 0 else "ok")
        if fontes["licencas"]["estado"] == "desatualizada":
            tom_lic = "red"
        card_lic = render_card(
            "Licenças em risco",
            f'{num_html(n_vencendo)}<span class="unit">vencendo</span>',
            f"<strong>{fmt_num(n_vencidas)}</strong> vencidas recentemente"
            + (f" · {fmt_num(vencidas_antigas)} antigas" if vencidas_antigas not in (None, "") else ""),
            delta_pill(n_vencidas, anterior.get("lic_vencidas_recentes"), "vencidas vs. dia anterior", melhor="menor"),
            tom_lic, 3,
        )
    else:
        card_lic = render_card("Licenças em risco", "—", "fonte indisponível", "", "muted", 3)

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
        banner = f'<div class="banner {tom}" role="status"><span class="ico">!</span><div>' + "<br>".join(partes) + "</div></div>"
    elif n_vencendo > 0:
        banner = (f'<div class="banner amber" role="status"><span class="ico">●</span>'
                  f'<div>{n_vencendo} licença(s) vencendo em breve; nenhuma vencida recente.</div></div>')

    # ------------------------------------------------------------------ séries
    serie = serie_historico(historico, DIAS_GRAFICO)
    rank = ranking_semanal(historico)
    tem_historico = bool(serie["labels"])

    aviso_graficos = ""
    if chart_js is None:
        aviso_graficos = '<p class="note reveal">Gráficos indisponíveis nesta geração (biblioteca de gráficos não encontrada). Os dados seguem nas tabelas abaixo.</p>'

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
  <div class="panel reveal"><h3>Fila de chamados abertos</h3><p class="hint">últimos {n_dias} dia(s) registrado(s)</p>
    <div class="chart-box"><canvas id="chartFila" role="img" aria-label="Evolução da fila de chamados"></canvas></div></div>
  <div class="panel reveal" style="--d:.1s"><h3>Atendimentos fechados por dia</h3><p class="hint">referente ao último dia útil de cada registro</p>
    <div class="chart-box"><canvas id="chartAtend" role="img" aria-label="Evolução de atendimentos fechados"></canvas></div></div>
</div>
<details class="dados reveal"><summary>Ver dados da série</summary>{tabela_hist}</details>"""
    else:
        secao_linhas = '<div class="panel reveal"><p class="empty">Histórico indisponível (historico/metricas.jsonl vazio ou ausente).</p></div>'

    # (d) barras: ranking por técnico OU total da equipe por dia
    if MOSTRAR_RANKING and rank["tecnicos"]:
        altura = max(200, 40 + 34 * len(rank["tecnicos"]))
        tabela_rank = render_tabela_simples(
            ["Técnico", "Atendimentos"], [[n, v] for n, v in zip(rank["tecnicos"], rank["valores"])], {1})
        secao_barras = f"""
<div class="panel reveal"><h3>Ranking semanal por técnico</h3>
  <p class="hint">soma dos últimos {len(rank['dias'])} dia(s) útil(eis) registrado(s): {esc(', '.join(rank['dias']))} · total {fmt_num(rank['total_periodo'])}</p>
  <div class="chart-box tall" style="height:{altura}px"><canvas id="chartRank" role="img" aria-label="Ranking semanal por técnico"></canvas></div>
  <details class="dados"><summary>Ver dados do ranking</summary>{tabela_rank}</details>
</div>"""
    elif rank["dias"]:
        tabela_rank = render_tabela_simples(
            ["Dia", "Atendimentos (equipe)"], [[d, t] for d, t in zip(rank["dias"], rank["totais"])], {1})
        secao_barras = f"""
<div class="panel reveal"><h3>Atendimentos da equipe por dia</h3>
  <p class="hint">últimos {len(rank['dias'])} dia(s) útil(eis) registrado(s) · ranking por técnico desativado (DASHBOARD_MOSTRAR_RANKING=false)</p>
  <div class="chart-box"><canvas id="chartRank" role="img" aria-label="Atendimentos da equipe por dia"></canvas></div>
  <details class="dados"><summary>Ver dados</summary>{tabela_rank}</details>
</div>"""
    else:
        secao_barras = '<div class="panel reveal"><p class="empty">Sem registros de atendimentos no histórico.</p></div>'

    # (e) tabela de licenças
    if licencas is None:
        secao_lic = '<div class="panel reveal"><p class="empty">Fonte indisponível: licenças.</p></div>'
    elif not lic_itens:
        secao_lic = '<div class="panel reveal"><p class="empty">Nenhuma licença vencida recentemente ou vencendo em breve.</p></div>'
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
<div class="panel reveal"><div class="table-wrap"><table class="lic">
<thead><tr><th>Status</th><th>Cliente</th><th>Sistema</th><th class="num">Vencimento</th><th class="num">Prazo</th></tr></thead>
<tbody>{''.join(linhas)}</tbody></table></div>{nota}</div>"""

    # (f) briefing
    if relatorio is None:
        secao_briefing = '<div class="panel reveal"><p class="empty">Briefing indisponível (relatorio.md ausente ou vazio).</p></div>'
    else:
        texto = relatorio if MOSTRAR_RANKING else redigir_nomes(relatorio, nomes_tecnicos)
        secao_briefing = f'<div class="panel briefing reveal">{markdown_para_html(texto)}</div>'

    # (g) rodapé
    itens_fontes = []
    for i, (chave, f) in enumerate(fontes.items()):
        estado = f["estado"]
        rotulo_estado = {"ok": "ok", "desatualizada": "desatualizada", "indisponivel": "indisponível"}[estado]
        itens_fontes.append(
            f'<li class="reveal" style="--d:{i * 0.08:.2f}s"><span class="dot {estado}"></span><div><strong>{esc(f["rotulo"])}</strong>'
            f'<span class="info">coletado em {esc(fmt_dt(f["coletado_em"]))} · {rotulo_estado} ({esc(f["detalhe"])})</span></div></li>'
        )
    if email:
        extra_email = (f'<p class="note reveal">E-mail: {fmt_num(email.get("nao_lidos"))} não lidos · '
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
    script = f"<script>{JS_UI}</script>"
    if chart_js is not None:
        script += f"\n<script>{chart_js}</script>\n<script>{JS_CHARTS.replace('__DATA__', json_inline(payload))}</script>"

    gerado_em = agora.strftime("%d/%m/%Y %H:%M")
    data_dados = date.today().strftime("%d/%m/%Y")
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Briefing diário — {esc(gerado_em)}</title>
<style>{CSS}</style>
<script>document.documentElement.classList.add("js");</script>
</head>
<body>
<div class="ambient" aria-hidden="true"><i class="b1"></i><i class="b2"></i><i class="b3"></i></div>

<header class="bar">
  <div class="in">
    <div class="brand"><span class="logo"></span>Briefing diário</div>
    <nav class="seg" aria-label="Seções">
      <a href="#destaques" class="active">Destaques</a>
      <a href="#evolucao">Evolução</a>
      <a href="#produtividade">Produtividade</a>
      <a href="#licencas">Licenças</a>
      <a href="#briefing">Briefing</a>
      <a href="#fontes">Fontes</a>
    </nav>
    <span class="stamp">Gerado em {esc(gerado_em)}</span>
  </div>
</header>

<div class="wrap">
<div class="hero">
  <h1>Briefing diário</h1>
  <p>Gerado em {esc(gerado_em)} · dados de {esc(data_dados)}</p>
</div>
{banner}

<section id="destaques" aria-label="Destaques do dia">
  {sec_head("Destaques do dia")}
  <div class="cards">{cards_html}</div>
</section>

<section id="evolucao" aria-label="Evolução">
  {sec_head("Evolução", f"últimos {DIAS_GRAFICO} dias")}
  {aviso_graficos}
  {secao_linhas}
</section>

<section id="produtividade" aria-label="Produtividade semanal">
  {sec_head("Produtividade semanal")}
  {secao_barras}
</section>

<section id="licencas" aria-label="Licenças críticas">
  {sec_head("Licenças críticas")}
  {secao_lic}
</section>

<section id="briefing" aria-label="Briefing do dia">
  {sec_head("Briefing do dia")}
  {secao_briefing}
</section>

<footer id="fontes">
  {sec_head("Status das fontes")}
  <ul class="fontes">{''.join(itens_fontes)}</ul>
  {extra_email}
  <p class="note reveal">Arquivo estático gerado por gerar_dashboard.py. Sem dependências externas; pode ser copiado sozinho.</p>
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
