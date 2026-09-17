"""Gera dashboard_semanal.html: painel estático e autocontido do briefing semanal.

Lê historico/metricas.jsonl e relatorio_semanal.md (não lê dados/ nem chama
nenhuma API) e escreve um único HTML com o mesmo visual "favo de mel" do
dashboard diário. CSS, JS, favo, abelha, conversor de markdown e helpers são
importados de dashboard_base.py: mudou o design lá, muda aqui também. Nunca
lança exceção por dado ausente: cada seção degrada e o restante é gerado.

Regras do recorte (as mesmas do prompt em briefing_semanal.bat):
  - só linhas com 'data' nos últimos 12 dias corridos contados a partir de hoje;
  - semana atual = os 5 registros mais recentes; anterior = os registros antes deles;
  - atendimentos: linhas com o mesmo atend_dia_ref contam uma vez só (vale a mais recente);
  - campo nulo: o dia fica fora daquela conta;
  - comparação com a semana anterior só com pelo menos 3 dias registrados nela.

Configuração (.env): DASHBOARD_MOSTRAR_RANKING, a mesma do dashboard diário.
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dashboard_base import (
    ABELHA_SVG, CSS, FAVO_CHEIO, FAVO_COMPACTO, JS_CHARTS, JS_HEADER, JS_UI, MOSTRAR_RANKING, TRACO_SVG,
    badge_delta, carregar_chart_js, carregar_fontes_css, grafico, carregar_gsap, coletar_nomes_tecnicos,
    dividir_briefing, esc, favo_svg, fmt_num, inline_md, json_inline, label_dia, ler_historico, markdown_para_html,
    num_html, redigir_nomes, render_ranking, secao_vazia, titulo_secao,
)

RAIZ = Path(__file__).resolve().parent
RELATORIO_SEMANAL = RAIZ / "relatorio_semanal.md"
SAIDA = RAIZ / "dashboard_semanal.html"

JANELA_DIAS = 12
DIAS_SEMANA = 5
MIN_DIAS_ANTERIOR = 3

# Mesmas 6 posições do favo diário; os ids "briefing" e "evolucao" são mantidos porque o JS reaproveitado
# depende deles (setas do slider e montagem dos gráficos).
FAVO_ORDEM_SEMANAL = [
    ("destaques", "Destaques"), ("briefing", "Relatório"), ("evolucao", "Evolução"),
    ("eficacia", "Eficácia"), ("comparacao", "Semanas"), ("dias", "Dia a dia"),
]
FAVO_NAV_SEMANAL = {(-1, 1): "destaques", (1, 0): "evolucao", (-1, 0): "eficacia",
                    (1, -1): "dias", (0, -1): "briefing", (0, 0): "comparacao"}

# Só o que o diário não tem. Nomes novos não colidem com as classes do ranking (.linha, .barra, .valor...).


# ----------------------------------------------------------------------------
# Leitura e recorte
# ----------------------------------------------------------------------------
def ler_relatorio_semanal() -> tuple[str | None, datetime | None]:
    """(texto, data de modificação). texto=None quando o arquivo falta, está vazio ou ilegível."""
    if not RELATORIO_SEMANAL.exists():
        return None, None
    try:
        texto = RELATORIO_SEMANAL.read_text(encoding="utf-8")
        modificado = datetime.fromtimestamp(RELATORIO_SEMANAL.stat().st_mtime)
    except (OSError, UnicodeDecodeError, ValueError):
        return None, None
    return (texto if texto.strip() else None), modificado


def inteiro(valor) -> int | None:
    if isinstance(valor, bool):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def data_iso(texto) -> date | None:
    try:
        return date.fromisoformat(str(texto)[:10])
    except ValueError:
        return None


def fmt_data(iso: str | None) -> str:
    d = data_iso(iso)
    return d.strftime("%d/%m/%Y") if d else "—"


def fmt_dec(valor: float | None) -> str:
    return "—" if valor is None else f"{valor:.1f}".replace(".", ",")


def recortar(historico: list[dict], hoje: date) -> list[dict]:
    """Linhas dos últimos JANELA_DIAS dias corridos, em ordem de data. Cada uma ganha '_conta_atend':
    False quando uma linha mais recente tem o mesmo atend_dia_ref (repetição do mesmo dia de trabalho,
    por exemplo briefing rodado no sábado e na segunda, ambos apontando para sexta)."""
    inicio = hoje - timedelta(days=JANELA_DIAS)
    recorte = []
    for r in historico:
        d = data_iso(r.get("data"))
        if d is not None and inicio <= d <= hoje:
            recorte.append({**r, "_dia": d})
    recorte.sort(key=lambda r: r["_dia"])
    ultimo_por_ref: dict[str, int] = {}
    for i, r in enumerate(recorte):
        ref = r.get("atend_dia_ref")
        chave = str(ref).strip() if ref not in (None, "") else f"data:{r['data']}"
        ultimo_por_ref[chave] = i
    vencedores = set(ultimo_por_ref.values())
    for i, r in enumerate(recorte):
        r["_conta_atend"] = i in vencedores
    return recorte


def resumo_semana(regs: list[dict]) -> dict:
    atend = [(r, inteiro(r.get("atend_total"))) for r in regs if r["_conta_atend"]]
    atend = [(r, v) for r, v in atend if v is not None]
    soma = sum(v for _, v in atend)
    por_tecnico: dict[str, int] = {}
    for r in regs:
        pt = r.get("atend_por_tecnico")
        if not r["_conta_atend"] or not isinstance(pt, dict):
            continue
        for nome, qtd in pt.items():
            q = inteiro(qtd)
            if q is not None:
                por_tecnico[str(nome)] = por_tecnico.get(str(nome), 0) + q

    def extremos(campo: str) -> dict | None:
        vals = [(r, inteiro(r.get(campo))) for r in regs]
        vals = [(r, v) for r, v in vals if v is not None]
        if not vals:
            return None
        return {"ini": vals[0][1], "ini_data": vals[0][0]["data"],
                "fim": vals[-1][1], "fim_data": vals[-1][0]["data"], "n": len(vals)}

    return {
        "registros": regs,
        "n": len(regs),
        "primeira": regs[0]["data"] if regs else None,
        "ultima": regs[-1]["data"] if regs else None,
        "atend_soma": soma if atend else None,
        "atend_n": len(atend),
        "atend_media": soma / len(atend) if atend else None,
        "atend_dias": [(str(r.get("atend_dia_ref") or label_dia(r["data"])), v) for r, v in atend],
        "por_tecnico": sorted(por_tecnico.items(), key=lambda kv: kv[1], reverse=True),
        "fila": extremos("fila_abertos"),
        "lic_vencidas": extremos("lic_vencidas_recentes"),
        "lic_vencendo": extremos("lic_vencendo"),
    }


# ----------------------------------------------------------------------------
# Renderização
# ----------------------------------------------------------------------------
SEM_DADO = '<span class="sem-dado">—</span>'


def badge_decimal(atual: float | None, anterior: float | None, rotulo: str, melhor: str = "maior") -> str:
    """Como badge_delta, para médias com uma casa decimal."""
    if atual is None or anterior is None:
        return ""
    d = round(atual - anterior, 1)
    if d == 0:
        return f'<span class="badge neutro"><span aria-hidden="true">=</span><b>0</b> {esc(rotulo)}</span>'
    subiu = d > 0
    bom = (subiu and melhor == "maior") or (not subiu and melhor == "menor")
    seta = "↑" if subiu else "↓"
    return f'<span class="badge {"bom" if bom else ""}"><span aria-hidden="true">{seta}</span><b>{fmt_dec(abs(d))}</b> {esc(rotulo)}</span>'


def gerar_html() -> str:
    agora = datetime.now()
    historico = ler_historico()
    relatorio, relatorio_em = ler_relatorio_semanal()
    chart_js = carregar_chart_js()
    gsap_js = carregar_gsap()
    fontes_css = carregar_fontes_css()

    recorte = recortar(historico, agora.date())
    atual = resumo_semana(recorte[-DIAS_SEMANA:])
    anterior = resumo_semana(recorte[:-DIAS_SEMANA])
    comparavel = anterior["n"] >= MIN_DIAS_ANTERIOR
    nomes_tecnicos = coletar_nomes_tecnicos(historico, None)
    sem_historico = f"Sem registros nos últimos {JANELA_DIAS} dias em historico/metricas.jsonl."
    periodo = f"{label_dia(atual['primeira'])} a {fmt_data(atual['ultima'])}" if recorte else "—"

    def cabeca_kpi(id_: str, rotulo: str) -> str:
        return f'<div class="kpi-cabeca"><h3 class="kpi-rotulo" id="{id_}">{rotulo}</h3></div>'

    # ================================================================ 1. DESTAQUES
    if not recorte:
        secao_destaques = secao_vazia("destaques", "Destaques da semana", sem_historico)
    else:
        soma = atual["atend_soma"]
        por_tecnico = atual["por_tecnico"]
        if MOSTRAR_RANKING and por_tecnico:
            quebra = '<span class="sep" aria-hidden="true">•</span>'.join(
                f"<span>{esc(n)} <b>{fmt_num(v)}</b></span>" for n, v in por_tecnico)
            lado_largo = (f'<div class="kpi-quebra" aria-label="Atendimentos por técnico"><span class="titulo">Por técnico</span>'
                          f'<p class="lista">{quebra}</p></div>')
        elif por_tecnico:
            lado_largo = f'<p class="kpi-rodape"><b>{len(por_tecnico)}</b> técnicos na soma</p>'
        else:
            lado_largo = ""
        if soma is None:
            linha_atend = '<p class="kpi-numero"><span class="sem-dado">—</span></p><p class="kpi-vazio">Sem atendimentos registrados na semana.</p>'
        else:
            badge_sem = badge_delta(soma, anterior["atend_soma"], "vs. semana anterior", melhor="maior") if comparavel else ""
            dias_media = "1 dia" if atual["atend_n"] == 1 else f"{atual['atend_n']} dias"
            linha_atend = (f'<p class="kpi-numero">{num_html(soma)}</p><div class="kpi-juizo">{badge_sem}</div>'
                           f'<p class="kpi-secundario"><b>{fmt_dec(atual["atend_media"])}</b> por dia (média de {dias_media})</p>')
        card_largo = f"""
<article class="card kpi kpi-primario" style="--i:0" aria-labelledby="c-atend">
  {cabeca_kpi("c-atend", "Atendimentos da semana")}
  <div class="kpi-corpo">
    <div class="kpi-linha">{linha_atend}</div>
    {lado_largo}
  </div>
</article>"""

        fila = atual["fila"]
        if fila:
            varios = fila["n"] > 1
            badge_fila = badge_delta(fila["fim"], fila["ini"], "vs. início da semana", melhor="menor") if varios else ""
            rodape_fila = (f'<b>{fmt_num(fila["ini"])}</b> em {esc(label_dia(fila["ini_data"]))} → '
                           f'<b>{fmt_num(fila["fim"])}</b> em {esc(label_dia(fila["fim_data"]))}') if varios else \
                f'um único registro na semana · <b>{esc(label_dia(fila["fim_data"]))}</b>'
            card_fila = f"""
<article class="card kpi" style="--i:1" aria-labelledby="c-fila">
  {cabeca_kpi("c-fila", "Fila de chamados")}
  <p class="kpi-numero">{num_html(fila["fim"])}</p>
  <div class="kpi-juizo">{badge_fila}</div>
  <p class="kpi-rodape">{rodape_fila}</p>
</article>"""
        else:
            card_fila = f"""
<article class="card kpi" style="--i:1" aria-labelledby="c-fila">
  {cabeca_kpi("c-fila", "Fila de chamados")}
  <p class="kpi-numero">{SEM_DADO}</p>
  <p class="kpi-rodape">sem fila registrada na semana</p>
</article>"""

        legenda_dias = "dia registrado" if atual["n"] == 1 else "dias registrados"
        anterior_txt = f' · semana anterior: <b>{anterior["n"]}</b> dia(s)' if anterior["n"] else ""
        card_periodo = f"""
<article class="card kpi" style="--i:2" aria-labelledby="c-periodo">
  {cabeca_kpi("c-periodo", "Período coberto")}
  <div class="kpi-linha"><p class="kpi-numero">{num_html(atual["n"])}</p><p class="kpi-legenda">{legenda_dias} de {DIAS_SEMANA}</p></div>
  <p class="kpi-rodape"><b>{esc(periodo)}</b>{anterior_txt}</p>
</article>"""

        venc, vencendo = atual["lic_vencidas"], atual["lic_vencendo"]
        badge_lic = badge_delta(venc["fim"], venc["ini"], "vs. início da semana", melhor="menor") if venc and venc["n"] > 1 else ""
        rodape_lic = (f'<p class="kpi-rodape"><b>{fmt_num(venc["ini"])}</b> vencidas recentes em {esc(label_dia(venc["ini_data"]))}</p>'
                      if venc and venc["n"] > 1 else "")
        card_lic = f"""
<article class="card kpi kpi-dupla" style="--i:3" aria-labelledby="c-lic">
  {cabeca_kpi("c-lic", "Licenças · fim da semana")}
  <div class="kpi-par">
    <div class="kpi-medida"><p class="kpi-numero">{num_html(venc["fim"]) if venc else SEM_DADO}</p><p class="kpi-legenda">vencidas recentes</p></div>
    <div class="kpi-medida"><p class="kpi-numero menor">{num_html(vencendo["fim"]) if vencendo else SEM_DADO}</p><p class="kpi-legenda">vencendo em breve</p></div>
  </div>
  <div class="kpi-juizo">{badge_lic}</div>
  {rodape_lic}
</article>"""

        secao_destaques = f"""
<section class="secao" id="destaques" data-scroll aria-labelledby="t-destaques">
  <div class="grade-destaques bloco-elastico">
    {titulo_secao("Destaques da semana", "destaques", ["Destaques", "Da Semana"])}
    {card_largo}{card_fila}{card_periodo}{card_lic}
  </div>
</section>"""

    # ================================================================ 2. RELATÓRIO
    ultima_data = data_iso(historico[-1]["data"]) if historico else None
    relatorio_velho = bool(relatorio_em and ultima_data and relatorio_em.date() < ultima_data)
    if relatorio is None:
        secao_briefing = secao_vazia("briefing", "Relatório da Semana", "Relatório indisponível (relatorio_semanal.md ausente ou vazio).")
    else:
        texto = relatorio if MOSTRAR_RANKING else redigir_nomes(relatorio, nomes_tecnicos)
        _, slides = dividir_briefing(texto)
        if not slides:
            slides = [{"titulo": "Relatório", "md": texto}]
        nota_rel = ""
        if slides[0]["titulo"] == "Briefing":  # trecho antes do primeiro '## ' (dividir_briefing o chama de "Briefing")
            intro = slides[0]["md"].strip()
            curta = len(intro) <= 240 and not re.search(r"^\s*([-*+|#]|\d+[.)])", intro, flags=re.M)
            if curta and len(slides) > 1:
                # linha de metadado ("Gerado em ...") não merece um slide inteiro: vira nota sob o carrossel
                nota_rel = f'<p class="nota-escura bloco-fixo">{inline_md(" ".join(intro.split()))}</p>'
                slides = slides[1:]
            else:
                slides[0]["titulo"] = "Visão geral"
        itens_slides, pontos = [], []
        for i, s in enumerate(slides):
            corpo_md = s["md"]
            n_itens = len(re.findall(r"^\s*[-*+]\s+", corpo_md, flags=re.M))
            duas = " duas-colunas" if ((n_itens >= 5 or len(corpo_md) > 650)
                                       and not re.search(r"^\s*\d+[.)]\s+", corpo_md, flags=re.M)
                                       and not re.search(r"^\s*\|", corpo_md, flags=re.M)) else ""
            corpo = markdown_para_html(corpo_md, base=4) or "<p>—</p>"
            itens_slides.append(
                f'<li class="slide" role="group" aria-roledescription="slide" aria-label="{i + 1} de {len(slides)}: {esc(s["titulo"])}">'
                f'<article class="card card-slide" data-scroll><h3 class="kpi-rotulo">{esc(s["titulo"])}</h3><div class="slide-corpo{duas}">{corpo}</div></article></li>'
            )
            pontos.append(f'<button type="button" role="tab" aria-selected="{"true" if i == 0 else "false"}" aria-label="{esc(s["titulo"])}"></button>')
        carimbo_rel = relatorio_em.strftime("%d/%m/%Y %H:%M") if relatorio_em else ""
        secao_briefing = f"""
<section class="secao" id="briefing" aria-labelledby="t-briefing">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Relatório da Semana", "briefing")}<p class="lado carimbo-briefing">{esc(carimbo_rel)}</p></header>
  <div class="slider bloco-elastico" aria-roledescription="carrossel" aria-label="Tópicos do relatório semanal">
    <div class="slides-janela"><ul class="slides">{''.join(itens_slides)}</ul></div>
    <div class="slider-controles">
      <button class="seta" type="button" data-dir="-1" aria-label="Tópico anterior">‹</button>
      <div class="indicadores" role="tablist" aria-label="Tópicos">{''.join(pontos)}</div>
      <button class="seta" type="button" data-dir="1" aria-label="Próximo tópico">›</button>
    </div>
  </div>
  {nota_rel}
</section>"""

    # ================================================================ 3. EVOLUÇÃO
    serie = {
        "labels": [label_dia(r["data"]) for r in recorte],
        "fila": [inteiro(r.get("fila_abertos")) for r in recorte],
        # repetição do mesmo dia de referência vira lacuna, como na soma da semana
        "atend": [inteiro(r.get("atend_total")) if r["_conta_atend"] else None for r in recorte],
    }
    if not recorte:
        secao_evolucao = secao_vazia("evolucao", "Evolução", sem_historico)
    else:
        def ultimos(vals: list) -> tuple:
            v = [x for x in vals if x is not None]
            return (v[-1] if v else None, v[-2] if len(v) > 1 else None)
        fila_ult, fila_ant = ultimos(serie["fila"])
        atend_ult, atend_ant = ultimos(serie["atend"])
        if chart_js is None:
            graficos = ('<p class="vazio bloco-elastico">Gráficos indisponíveis nesta geração (biblioteca de gráficos não encontrada). '
                        'Os dados seguem na seção Dia a dia.</p>')
        else:
            graficos = f"""
<div class="grade larga graficos bloco-elastico">
  <article class="card card-grafico" style="--i:0">
    <div class="kpi-cabeca"><h3 class="kpi-rotulo">Fila de chamados abertos</h3></div>
    <div class="kpi-linha"><p class="kpi-numero menor">{num_html(fila_ult)}</p><div class="kpi-juizo">{badge_delta(fila_ult, fila_ant, "vs. registro anterior", melhor="menor")}</div><p class="kpi-legenda">último registro · {esc(label_dia(recorte[-1]["data"]))}</p></div>
    <div class="grafico-caixa"><canvas id="chartFila" role="img" aria-label="Evolução da fila de chamados abertos nos últimos {JANELA_DIAS} dias"></canvas></div>
  </article>
  <article class="card card-grafico" style="--i:1">
    <div class="kpi-cabeca"><h3 class="kpi-rotulo">Atendimentos fechados por dia</h3></div>
    <div class="kpi-linha"><p class="kpi-numero menor">{num_html(atend_ult)}</p><div class="kpi-juizo">{badge_delta(atend_ult, atend_ant, "vs. registro anterior", melhor="maior")}</div><p class="kpi-legenda">último dia útil registrado</p></div>
    <div class="grafico-caixa"><canvas id="chartAtend" role="img" aria-label="Evolução de atendimentos fechados por dia nos últimos {JANELA_DIAS} dias"></canvas></div>
  </article>
</div>"""
        secao_evolucao = f"""
<section class="secao" id="evolucao" data-scroll aria-labelledby="t-evolucao">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Evolução", "evolucao")}<p class="lado subtitulo">últimos {JANELA_DIAS} dias · {len(recorte)} registro(s)</p></header>
  {graficos}
</section>"""

    # ================================================================ 4. EFICÁCIA
    if MOSTRAR_RANKING and atual["por_tecnico"]:
        titulo_rank = "Ranking semanal por técnico"
        nomes_rank = [n for n, _ in atual["por_tecnico"]]
        valores_rank = [v for _, v in atual["por_tecnico"]]
    elif atual["atend_dias"]:
        titulo_rank = "Atendimentos da equipe por dia"
        nomes_rank = [d for d, _ in atual["atend_dias"]]
        valores_rank = [v for _, v in atual["atend_dias"]]
    else:
        titulo_rank, nomes_rank, valores_rank = "", [], []
    if not nomes_rank:
        secao_eficacia = secao_vazia("eficacia", "Eficácia", "Sem atendimentos registrados na semana atual.")
    else:
        dias_txt = ", ".join(d for d, _ in atual["atend_dias"])
        nota_rank = "" if MOSTRAR_RANKING else " · ranking por técnico desativado"
        secao_eficacia = f"""
<section class="secao" id="eficacia" data-scroll aria-labelledby="t-eficacia">
  <header class="secao-cabeca dividida bloco-fixo">
    {titulo_secao("Eficácia", "eficacia")}
    <div class="lado"><div class="kpi-medida" title="{esc(dias_txt)}"><p class="kpi-numero menor">{num_html(atual["atend_soma"])}</p><p class="kpi-legenda">atendimentos fechados em {atual["atend_n"]} dia(s) útil(eis)</p></div></div>
  </header>
  <article class="card card-ranking bloco-elastico" style="--i:0" data-scroll>
    <div class="kpi-cabeca"><h3 class="kpi-rotulo">{esc(titulo_rank)}</h3><p class="kpi-legenda">soma da semana atual · {esc(periodo)}{esc(nota_rank)}</p></div>
    {render_ranking(nomes_rank, valores_rank)}
  </article>
</section>"""

    # ================================================================ 5. SEMANAS
    if not recorte:
        secao_comparacao = secao_vazia("comparacao", "Semanas", sem_historico)
    elif not comparavel:
        secao_comparacao = secao_vazia(
            "comparacao", "Semanas",
            f"Comparação indisponível: a semana anterior tem {anterior['n']} dia(s) registrado(s) nos últimos "
            f"{JANELA_DIAS} dias, e são necessários pelo menos {MIN_DIAS_ANTERIOR}.")
    else:
        def card_cmp(i: int, id_: str, rotulo: str, valor: str, badge: str, rodape: str) -> str:
            return f"""
<article class="card card-fonte kpi" style="--i:{i}" aria-labelledby="{id_}">
  {cabeca_kpi(id_, rotulo)}
  <p class="kpi-numero">{valor}</p>
  <div class="kpi-juizo">{badge}</div>
  <p class="kpi-rodape">{rodape}</p>
</article>"""
        fila_a, fila_p = atual["fila"], anterior["fila"]
        cards_cmp = (
            card_cmp(0, "s-atend", "Atendimentos",
                     num_html(atual["atend_soma"]) if atual["atend_soma"] is not None else SEM_DADO,
                     badge_delta(atual["atend_soma"], anterior["atend_soma"], "vs. semana anterior", melhor="maior"),
                     f'semana anterior: <b>{fmt_num(anterior["atend_soma"])}</b> em {anterior["atend_n"]} dia(s)')
            + card_cmp(1, "s-media", "Média diária",
                       fmt_dec(atual["atend_media"]) if atual["atend_media"] is not None else SEM_DADO,
                       badge_decimal(atual["atend_media"], anterior["atend_media"], "vs. semana anterior", melhor="maior"),
                       f'semana anterior: <b>{fmt_dec(anterior["atend_media"])}</b> por dia')
            + card_cmp(2, "s-fila", "Fila no fim da semana",
                       num_html(fila_a["fim"]) if fila_a else SEM_DADO,
                       badge_delta(fila_a["fim"] if fila_a else None, fila_p["fim"] if fila_p else None, "vs. semana anterior", melhor="menor"),
                       f'semana anterior: <b>{fmt_num(fila_p["fim"]) if fila_p else "—"}</b>'
                       + (f' em {esc(label_dia(fila_p["fim_data"]))}' if fila_p else ""))
        )
        faixa = (f'atual {esc(label_dia(atual["primeira"]))}–{esc(label_dia(atual["ultima"]))} · '
                 f'anterior {esc(label_dia(anterior["primeira"]))}–{esc(label_dia(anterior["ultima"]))}')
        secao_comparacao = f"""
<section class="secao" id="comparacao" data-scroll aria-labelledby="t-comparacao">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Semanas", "comparacao")}<p class="lado subtitulo">{faixa}</p></header>
  <div class="grade grade-fontes bloco-elastico">{cards_cmp}</div>
</section>"""

    # ================================================================ 6. DIA A DIA
    if not recorte:
        secao_dias = secao_vazia("dias", "Dia a dia", sem_historico)
    else:
        inicio_atual = len(recorte) - atual["n"]
        linhas = []
        for i in range(len(recorte) - 1, -1, -1):
            r = recorte[i]
            na_atual = i >= inicio_atual
            if r["_conta_atend"]:
                atend_td = fmt_num(r.get("atend_total"))
            else:
                atend_td = '<span class="apagado" title="Mesmo dia de referência de um registro mais recente; não entra na soma">repetido</span>'
            linhas.append(
                f'<tr><td class="num">{esc(fmt_data(r["data"]))}</td>'
                f'<td><span class="pill {"atual" if na_atual else "anterior"}">{"Semana atual" if na_atual else "Anterior"}</span></td>'
                f'<td class="num">{fmt_num(r.get("fila_abertos"))}</td><td class="num">{fmt_num(r.get("meus_abertos"))}</td>'
                f'<td class="num">{atend_td}</td><td>{esc(r.get("atend_dia_ref") or "—")}</td>'
                f'<td class="num">{fmt_num(r.get("lic_vencidas_recentes"))}</td><td class="num">{fmt_num(r.get("lic_vencendo"))}</td></tr>'
            )
        sintese = ('<div class="lado">'
                   f'<div class="kpi-medida"><p class="kpi-numero menor">{num_html(len(recorte))}</p><p class="kpi-legenda">registros em {JANELA_DIAS} dias</p></div>'
                   f'<div class="kpi-medida"><p class="kpi-numero menor">{num_html(atual["n"])}</p><p class="kpi-legenda">semana atual</p></div>'
                   f'<div class="kpi-medida"><p class="kpi-numero menor">{num_html(anterior["n"])}</p><p class="kpi-legenda">semana anterior</p></div>'
                   '</div>')
        secao_dias = f"""
<section class="secao" id="dias" aria-labelledby="t-dias">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Dia a dia", "dias")}{sintese}</header>
  <div class="tabela-clara bloco-elastico" data-scroll tabindex="0" role="region" aria-label="Tabela de registros diários">
    <table class="tabela-lic"><caption class="sr-only">Registros diários dos últimos {JANELA_DIAS} dias, do mais recente ao mais antigo</caption>
      <thead><tr><th scope="col" class="num">Data</th><th scope="col">Semana</th><th scope="col" class="num">Fila</th><th scope="col" class="num">Abertos da equipe</th><th scope="col" class="num">Atendimentos</th><th scope="col">Dia de referência</th><th scope="col" class="num">Vencidas recentes</th><th scope="col" class="num">Vencendo</th></tr></thead>
      <tbody>{''.join(linhas)}</tbody></table>
  </div>
</section>"""

    # ================================================================ cabeçalho
    gerado_em = agora.strftime("%d/%m/%Y %H:%M")
    avisos = ""
    if relatorio is None:
        avisos += '<a class="aviso grave" href="#briefing" data-alvo="briefing">relatório indisponível</a>'
    elif relatorio_velho:
        avisos += (f'<a class="aviso" href="#briefing" data-alvo="briefing">relatório desatualizado · '
                   f'de {esc(relatorio_em.strftime("%d/%m"))}</a>')
    if not recorte:
        avisos += '<a class="aviso grave" href="#dias" data-alvo="dias">histórico sem registros recentes</a>'
    semana_txt = f"Semana de {esc(periodo)}" if recorte else "Semana sem registros"

    favo_cheio, _ = favo_svg(FAVO_CHEIO, passo=63, fonte=12, classe="favo-cheio", nav=FAVO_NAV_SEMANAL, ordem=FAVO_ORDEM_SEMANAL)
    favo_compacto, _ = favo_svg(FAVO_COMPACTO, passo=66, fonte=12, classe="favo-compacto", nav=FAVO_NAV_SEMANAL, ordem=FAVO_ORDEM_SEMANAL)
    menu_simples = "".join(f'<a href="#{id_}" data-alvo="{id_}">{esc(rotulo)}</a>' for id_, rotulo in FAVO_ORDEM_SEMANAL)

    script = f"<script>{JS_UI}</script>"
    if gsap_js is not None:
        script += f"\n<script>{gsap_js}</script>\n<script>{JS_HEADER}</script>"
    marcador_anim = (
        '<script>(function(){try{if(!(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches))'
        'document.documentElement.classList.add("anim")}catch(e){}})();</script>'
    ) if gsap_js is not None else ""
    if chart_js is not None and recorte:
        payload_graficos = {
            "labels": serie.get("labels") or [],
            "secao": "evolucao",
            "graficos": [
                grafico("chartFila", "Fila de chamados", serie.get("fila") or [], cor="azul"),
                grafico("chartAtend", "Atendimentos fechados", serie.get("atend") or [], cor="rubro"),
            ],
        }
        script += f"\n<script>{chart_js}</script>\n<script>{JS_CHARTS.replace('__DATA__', json_inline(payload_graficos))}</script>"

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Briefing Semanal — {esc(fmt_data(atual["ultima"]) if recorte else agora.strftime("%d/%m/%Y"))}</title>
<style>{fontes_css}{CSS}</style>
{marcador_anim}
</head>
<body>
<a class="pular" href="#destaques">Ir para o conteúdo</a>
<div class="palco">
<header class="topo">
  <a class="marca" href="#destaques" data-alvo="destaques" aria-label="Briefing Semanal — início">
    {ABELHA_SVG}
    <h1 class="wordmark"><span class="w" style="--traco-w:50%">Briefing{TRACO_SVG}</span><span class="w">Semanal{TRACO_SVG}</span></h1>
  </a>
  <nav class="favo" aria-label="Seções do briefing semanal">
    {favo_cheio}
    {favo_compacto}
    <div class="menu-simples">{menu_simples}</div>
  </nav>
</header>
<p class="carimbo"><span>Gerado em <time datetime="{agora.strftime('%Y-%m-%dT%H:%M')}">{esc(gerado_em)}</time></span><span class="sep" aria-hidden="true">•</span><span>{semana_txt}</span>{avisos}</p>
<main class="colmeia" id="colmeia">
{secao_destaques}
{secao_briefing}
{secao_evolucao}
{secao_eficacia}
{secao_comparacao}
{secao_dias}
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
    except Exception as e:  # último recurso: nunca deixar o briefing semanal sem página
        print(f"ERRO ao montar dashboard semanal: {type(e).__name__}: {e}")
        conteudo = (
            "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'><title>Briefing semanal</title></head>"
            f"<body><h1>Briefing semanal</h1><p>Falha ao gerar o dashboard semanal: {esc(type(e).__name__)}</p></body></html>"
        )
    SAIDA.write_text(conteudo, encoding="utf-8")
    print(f"OK -> {SAIDA} ({len(conteudo.encode('utf-8')) // 1024} KB, ranking={'on' if MOSTRAR_RANKING else 'off'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
