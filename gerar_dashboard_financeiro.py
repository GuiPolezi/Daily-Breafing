"""Gera dashboard_financeiro.html: painel de licencas para o financeiro.

Le dados/licencas.json, historico/licencas.jsonl, historico/metricas.jsonl e
relatorio_financeiro.md. Uma unica fonte de negocio: o painel interno de
licencas. Nenhum dado de help desk, nenhum nome de tecnico -- por decisao de
privacidade esta pagina nunca exibe pessoas da equipe.

O que ela responde, que o diario nao responde:
  - qual a foto de hoje por faixa de prazo (vencida, ate 7 dias, ate 30...);
  - o que MUDOU desde o retrato anterior: renovou, venceu, entrou, saiu.
    Isso vem de historico/licencas.jsonl, escrito por arquivar.py.

Limite conhecido: a fonte expoe apenas cliente, sistema e vencimento. Nao ha
valor, contrato nem responsavel -- entao este painel e um radar de renovacao,
nao um painel financeiro de receita.

Identidade visual vem de dashboard_base.py. Nunca lanca excecao por dado
ausente: a secao degrada e o resto da pagina sai.

Configuracao (.env): DASHBOARD_DIAS_GRAFICO.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

from dashboard_base import (
    ABELHA_SVG, CHEVRON_SVG, CSS, FAVO_CHEIO, FAVO_COMPACTO, JS_CHARTS, JS_HEADER, JS_UI,
    RAIZ, TRACO_SVG, agrupar_licencas_por, badge_delta, barras_distribuicao, card_grafico,
    carregar_chart_js, carregar_fontes_css, carregar_gsap, cfg_int, coletar_nomes_tecnicos,
    comparar_licencas,
    data_do_briefing, dividir_briefing, esc, etiqueta_fonte, explica, faixas_de_prazo,
    favo_svg, fmt_num, grafico, json_inline, label_dia, ler_historico, ler_historico_licencas,
    ler_json, markdown_para_html, nota_secao, num_html, redigir_nomes, secao_vazia,
    serie_historico,
    serie_tem_dado, status_fonte, tag_fonte, titulo_secao,
)

RELATORIO = RAIZ / "relatorio_financeiro.md"
SAIDA = RAIZ / "dashboard_financeiro.html"
DIAS_GRAFICO = cfg_int("DASHBOARD_DIAS_GRAFICO", 30)

# Seis seções, as mesmas seis células do favo. O id "briefing" é mantido porque
# o JS reaproveitado depende dele (setas do slider); "evolucao", por causa dos gráficos.
FAVO_ORDEM_FIN = [
    ("destaques", "Panorama"), ("briefing", "Relatório"), ("radar", "Radar"),
    ("movimentacao", "Mudanças"), ("evolucao", "Evolução"), ("fontes", "Fonte"),
]
FAVO_NAV_FIN = {(-1, 1): "destaques", (0, -1): "briefing", (1, -1): "radar",
                (-1, 0): "movimentacao", (1, 0): "evolucao", (0, 0): "fontes"}

# Só o que o diário não tem. Nomes novos não colidem com as classes existentes.


def ler_relatorio() -> str | None:
    if not RELATORIO.exists():
        return None
    try:
        texto = RELATORIO.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return texto if texto.strip() else None


def lista_itens(licencas: dict | None) -> list[dict]:
    """Licenças de produção em risco: vencidas recentes + vencendo em breve."""
    if not licencas:
        return []
    saida = []
    for chave, estado in (("vencidas_recentes", "vencida"), ("vencendo_em_breve", "vencendo")):
        valores = licencas.get(chave)
        if isinstance(valores, list):
            saida.extend({**i, "estado": estado} for i in valores if isinstance(i, dict))
    saida.sort(key=lambda i: (i.get("dias") if i.get("dias") is not None else 9999))
    return saida


def bloco_movimentacao(titulo: str, itens: list, classe: str, campo_extra: str = "") -> str:
    """Lista curta de licenças que mudaram de estado entre dois retratos."""
    if not itens:
        corpo = '<p class="mov-vazio">Nenhuma nesta categoria desde o retrato anterior.</p>'
    else:
        linhas = []
        for i in itens[:10]:
            extra = ""
            if campo_extra and i.get(campo_extra):
                extra = f'{esc(i.get(campo_extra))} → '
            linhas.append(
                f'<li class="mov-item"><span class="cliente" title="{esc(i.get("cliente"))}">'
                f'{esc(i.get("cliente") or "—")}</span>'
                f'<span class="quando">{extra}{esc(i.get("vencimento") or "—")}</span></li>'
            )
        resto = (f'<li class="mov-item"><span class="cliente">e mais {len(itens) - 10}…</span></li>'
                 if len(itens) > 10 else "")
        corpo = f'<ul class="mov-lista">{"".join(linhas)}{resto}</ul>'
    return f"""
<article class="card kpi" data-scroll>
  <div class="kpi-cabeca"><h3 class="kpi-rotulo">{esc(titulo)}</h3>{tag_fonte("licencas")}</div>
  <p class="kpi-numero menor {classe}">{num_html(len(itens))}</p>
  {corpo}
</article>"""


def gerar_html() -> str:
    agora = datetime.now()
    licencas, erro = ler_json("licencas.json")
    fonte = {"rotulo": "Licenças", **status_fonte(licencas, erro, agora)}
    historico = ler_historico()
    retratos = ler_historico_licencas()
    relatorio = ler_relatorio()
    chart_js = carregar_chart_js()
    gsap_js = carregar_gsap()
    fontes_css = carregar_fontes_css()

    # Privacidade: esta pagina nunca exibe pessoa da equipe. Le helpdesk.json
    # SO para saber quais nomes esconder, e nao mostra nenhum numero dele.
    helpdesk_para_redigir, _ = ler_json("helpdesk.json")
    nomes_a_esconder = coletar_nomes_tecnicos(historico, helpdesk_para_redigir)

    itens = lista_itens(licencas)
    n_vencidas = len((licencas or {}).get("vencidas_recentes") or [])
    n_vencendo = len((licencas or {}).get("vencendo_em_breve") or [])
    antigas = (licencas or {}).get("vencidas_antigas_total")
    ignoradas = (licencas or {}).get("ignoradas_homolog_teste")

    hoje_iso = date.today().isoformat()
    anterior: dict = {}
    if historico:
        anterior = historico[-2] if historico[-1].get("data") == hoje_iso and len(historico) >= 2 else (
            historico[-1] if historico[-1].get("data") != hoje_iso else {})

    # ============================================================= 1. PANORAMA
    if licencas is None:
        secao_destaques = secao_vazia("destaques", "Panorama", "Fonte de licenças indisponível nesta geração.")
    else:
        faixas = faixas_de_prazo(itens)
        cartoes_faixa = []
        for i, (rotulo, qtd) in enumerate(faixas.items()):
            classe = " critica" if rotulo == "Vencidas" else (" urgente" if "até 7" in rotulo else "")
            cartoes_faixa.append(f"""
<article class="card kpi{classe}" style="--i:{i}">
  <h3 class="kpi-rotulo">{esc(rotulo)}</h3>
  <p class="kpi-numero">{num_html(qtd)}</p>
</article>""")
        secao_destaques = f"""
<section class="secao" id="destaques" data-scroll aria-labelledby="t-destaques">
  <div class="grade-destaques ampla bloco-elastico">
    {titulo_secao("Licenças", "destaques", ["Licenças", "Em Risco"])}
    <article class="card kpi kpi-primario" style="--i:0" aria-labelledby="c-venc">
      <div class="kpi-cabeca"><h3 class="kpi-rotulo" id="c-venc">Vencidas · ainda acionáveis</h3>{tag_fonte("licencas")}{etiqueta_fonte(fonte)}</div>
      <div class="kpi-corpo"><div class="kpi-linha">
        <p class="kpi-numero">{num_html(n_vencidas)}</p>
        <div class="kpi-juizo">{badge_delta(n_vencidas, anterior.get("lic_vencidas_recentes"), melhor="menor")}</div>
        <p class="kpi-secundario"><b>{num_html(n_vencendo)}</b> vencendo em breve</p>
      </div></div>
      {explica("lic_vencidas")}
    </article>
    <article class="card kpi" style="--i:1">
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Vencidas há mais tempo</h3>{tag_fonte("licencas")}</div>
      <p class="kpi-numero">{num_html(antigas)}</p>
      {explica("lic_antigas")}
    </article>
    <article class="card kpi" style="--i:2">
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Homologação e teste</h3>{tag_fonte("licencas")}</div>
      <p class="kpi-numero">{num_html(ignoradas)}</p>
      <p class="kpi-explica"><b>O que é:</b> licenças de homologação, teste e backup, excluídas de todas as
      contagens desta página por não representarem cliente em produção.</p>
    </article>
  </div>
  {nota_secao("licencas", "Painel web interno de licenças, lido uma vez por dia. "
                          "A fonte informa cliente, sistema e vencimento — não há valor nem contrato.")}
  <div class="grade faixa-prazo bloco-fixo">{''.join(cartoes_faixa)}</div>
</section>"""

    # ============================================================= 2. RELATÓRIO
    if relatorio is None:
        secao_briefing = secao_vazia("briefing", "Relatório", "Relatório indisponível (relatorio_financeiro.md ausente ou vazio).")
    else:
        # Mesmo que o relatorio cite um tecnico, ele nao chega ao financeiro.
        titulo_h1, slides = dividir_briefing(redigir_nomes(relatorio, nomes_a_esconder))
        data_brief = data_do_briefing(titulo_h1) or agora.strftime("%d/%m/%Y %H:%M")
        if not slides:
            slides = [{"titulo": "Relatório", "md": relatorio}]
        itens_slides, pontos = [], []
        for i, s in enumerate(slides):
            corpo = markdown_para_html(s["md"], base=4) or "<p>—</p>"
            itens_slides.append(
                f'<li class="slide" role="group" aria-roledescription="slide" aria-label="{i + 1} de {len(slides)}: {esc(s["titulo"])}">'
                f'<article class="card card-slide" data-scroll><h3 class="kpi-rotulo">{esc(s["titulo"])}</h3>'
                f'<div class="slide-corpo">{corpo}</div></article></li>')
            pontos.append(f'<button type="button" role="tab" aria-selected="{"true" if i == 0 else "false"}" aria-label="{esc(s["titulo"])}"></button>')
        secao_briefing = f"""
<section class="secao" id="briefing" aria-labelledby="t-briefing">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Relatório", "briefing")}<p class="lado carimbo-briefing">{esc(data_brief)}</p></header>
  <div class="slider bloco-elastico" aria-roledescription="carrossel" aria-label="Tópicos do relatório">
    <div class="slides-janela"><ul class="slides">{''.join(itens_slides)}</ul></div>
    <div class="slider-controles">
      <button class="seta" type="button" data-dir="-1" aria-label="Tópico anterior">‹</button>
      <div class="indicadores" role="tablist" aria-label="Tópicos">{''.join(pontos)}</div>
      <button class="seta" type="button" data-dir="1" aria-label="Próximo tópico">›</button>
    </div>
  </div>
</section>"""

    # ============================================================= 3. RADAR
    if not itens:
        secao_radar = secao_vazia("radar", "Radar", "Nenhuma licença vencida recentemente ou vencendo em breve.")
    else:
        linhas = []
        for it in itens:
            d = it.get("dias")
            vencida = it.get("estado") == "vencida"
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
                f'<td>{esc(it.get("cliente"))}</td><td>{esc(it.get("sistema"))}</td>'
                f'<td class="num">{esc(it.get("vencimento"))}</td><td class="num{urgente}">{prazo}</td></tr>')
        secao_radar = f"""
<section class="secao" id="radar" aria-labelledby="t-radar">
  <header class="secao-cabeca dividida bloco-fixo">
    {titulo_secao("Radar", "radar")}
    <p class="lado subtitulo">{len(itens)} licença(s) em risco · da mais urgente para a menos</p>
  </header>
  {nota_secao("licencas", "Ordenado pelo prazo: vencidas há mais tempo no topo, depois as que vencem antes.")}
  <div class="tabela-clara bloco-elastico" data-scroll tabindex="0" role="region" aria-label="Licenças em risco">
    <table class="tabela-lic"><caption class="sr-only">Licenças vencidas e vencendo, por urgência</caption>
      <thead><tr><th scope="col">Situação</th><th scope="col">Cliente</th><th scope="col">Sistema</th><th scope="col" class="num">Vencimento</th><th scope="col" class="num">Prazo</th></tr></thead>
      <tbody>{''.join(linhas)}</tbody></table>
  </div>
  <div class="grade larga graficos quatro bloco-elastico">
    <article class="card" data-scroll>
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Por sistema</h3>{tag_fonte("licencas")}</div>
      {barras_distribuicao(agrupar_licencas_por(itens, "sistema"), limite=8)}
    </article>
    <article class="card" data-scroll>
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Clientes com mais licenças em risco</h3>{tag_fonte("licencas")}</div>
      {barras_distribuicao(agrupar_licencas_por(itens, "cliente"), limite=8)}
    </article>
  </div>
</section>"""

    # ============================================================= 4. MUDANÇAS
    if len(retratos) < 2:
        quantos = len(retratos)
        secao_mov = secao_vazia(
            "movimentacao", "Mudanças",
            f"A comparação precisa de dois retratos diários e existe {quantos}. "
            "historico/licencas.jsonl ganha uma linha a cada execução de arquivar.py — "
            "a partir de amanhã esta seção começa a responder o que renovou e o que caiu.")
    else:
        anterior_retrato, atual_retrato = retratos[-2], retratos[-1]
        mov = comparar_licencas(anterior_retrato, atual_retrato)
        secao_mov = f"""
<section class="secao" id="movimentacao" data-scroll aria-labelledby="t-movimentacao">
  <header class="secao-cabeca dividida bloco-fixo">
    {titulo_secao("Mudanças", "movimentacao")}
    <p class="lado subtitulo">de {esc(label_dia(mov["data_antes"] or ""))} para {esc(label_dia(mov["data_agora"] or ""))}</p>
  </header>
  {nota_secao("licencas", "Comparação entre os dois retratos diários mais recentes. "
                          "Uma licença é identificada por cliente + sistema; o que muda é o vencimento.")}
  <div class="grade mov-grade bloco-elastico">
    {bloco_movimentacao("Renovadas", mov["renovadas"], "", campo_extra="vencimento_anterior")}
    {bloco_movimentacao("Venceram no período", mov["venceram"], "")}
    {bloco_movimentacao("Entraram na lista", mov["entraram"], "")}
    {bloco_movimentacao("Saíram da lista", mov["sairam"], "")}
  </div>
  <p class="aviso-escopo"><b>Como ler “saíram da lista”:</b> a licença deixou de aparecer nas tabelas
  de risco. Isso pode ser renovação por um prazo longo ou remoção no sistema de origem — a fonte não
  diz qual dos dois, então o painel não afirma.</p>
</section>"""

    # ============================================================= 5. EVOLUÇÃO
    serie = serie_historico(historico, DIAS_GRAFICO)
    n_dias = len(serie["labels"])
    graficos_payload: list[dict] = []
    if not n_dias:
        secao_evolucao = secao_vazia("evolucao", "Evolução", "Histórico indisponível (historico/metricas.jsonl vazio ou ausente).")
    else:
        cartoes = []
        for chave, id_canvas, titulo, cor in [
            ("lic_vencidas", "chartLicVenc", "Licenças vencidas recentes", "rubro"),
            ("lic_vencendo", "chartLicProx", "Licenças vencendo em breve", "mel"),
        ]:
            if not serie_tem_dado(serie, chave):
                continue
            graficos_payload.append(grafico(id_canvas, titulo, serie[chave], cor=cor))
            valores = [v for v in serie[chave] if v is not None]
            ult, ant = (valores[-1] if valores else None), (valores[-2] if len(valores) > 1 else None)
            topo = (f'<div class="kpi-linha"><p class="kpi-numero menor">{num_html(ult)}</p>'
                    f'<div class="kpi-juizo">{badge_delta(ult, ant, "vs. registro anterior", melhor="menor")}</div></div>')
            cartoes.append(card_grafico(id_canvas, titulo, f"Evolução: {titulo}", topo))
        if chart_js is None:
            corpo = '<p class="vazio bloco-elastico">Gráficos indisponíveis nesta geração (biblioteca não encontrada).</p>'
            graficos_payload = []
        elif not cartoes:
            corpo = '<p class="vazio bloco-elastico">Ainda não há série suficiente para desenhar gráficos.</p>'
        else:
            corpo = f'<div class="grade larga graficos quatro bloco-elastico">{"".join(cartoes)}</div>'
        linhas_serie = "".join(
            f'<tr><td>{esc(label_dia(d))}</td><td class="c">{fmt_num(v)}</td><td class="d">{fmt_num(p)}</td></tr>'
            for d, v, p in list(zip(serie["datas"], serie["lic_vencidas"], serie["lic_vencendo"]))[::-1])
        secao_evolucao = f"""
<section class="secao" id="evolucao" data-scroll aria-labelledby="t-evolucao">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Evolução", "evolucao")}<p class="lado subtitulo">últimos {DIAS_GRAFICO} dias · {n_dias} registrado(s)</p></header>
  {nota_secao("historico", "Série de historico/metricas.jsonl. Dias sem coleta não aparecem.")}
  {corpo}
  <div class="serie bloco-fixo">
    <div class="acoes"><button class="pilula" type="button" aria-expanded="false" aria-controls="serie-lic"><span class="pilula-texto">Ver dados da série</span>{CHEVRON_SVG}</button></div>
    <div class="expansivel" id="serie-lic"><div><div class="expansivel-pad">
      <table class="tabela-serie"><caption class="sr-only">Licenças por dia</caption>
        <thead><tr><th scope="col">Data</th><th scope="col" class="c">Vencidas recentes</th><th scope="col" class="d">Vencendo em breve</th></tr></thead>
        <tbody>{linhas_serie}</tbody></table>
    </div></div></div>
  </div>
</section>"""

    # ============================================================= 6. FONTE
    estado = fonte["estado"]
    rotulo_estado = {"ok": "Atualizada", "desatualizada": "Desatualizada", "indisponivel": "Indisponível"}[estado]
    classe_estado = {"ok": "ok", "desatualizada": "aviso", "indisponivel": "grave"}[estado]
    detalhe = {"ok": "dentro do limite de 24 h",
               "desatualizada": f"coleta {esc(fonte['detalhe'])} · limite de 24 h",
               "indisponivel": esc(fonte["detalhe"])}[estado]
    dt = fonte["coletado_em"]
    secao_fontes = f"""
<section class="secao" id="fontes" data-scroll aria-labelledby="t-fontes">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Fonte", "fontes")}<p class="lado subtitulo">uma fonte de negócio nesta página</p></header>
  <div class="grade grade-fontes bloco-elastico">
    <article class="card card-fonte kpi" style="--i:0">
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Sistema de licenças</h3><span class="kpi-fonte {classe_estado}">{rotulo_estado}</span></div>
      <p class="kpi-numero menor">{esc(dt.strftime("%H:%M")) if dt else '<span class="sem-dado">—</span>'}</p>
      <p class="kpi-legenda">coletado em {esc(dt.strftime("%d/%m/%Y")) if dt else "—"}</p>
      <p class="fonte-detalhe">{detalhe}</p>
      <ul class="mini-stats"><li><b>{fmt_num(n_vencendo)}</b><span>vencendo</span></li>
        <li><b>{fmt_num(n_vencidas)}</b><span>vencidas recentes</span></li>
        <li><b>{fmt_num(ignoradas)}</b><span>homolog./teste ignoradas</span></li></ul>
      <p class="kpi-explica">{tag_fonte("licencas")} Leitura do painel web interno, uma vez por dia,
      somente leitura. Campos disponíveis: cliente, sistema e data de vencimento.</p>
    </article>
    <article class="card card-fonte kpi" style="--i:1">
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Retratos diários</h3></div>
      <p class="kpi-numero menor">{num_html(len(retratos))}</p>
      <p class="kpi-legenda">dias em historico/licencas.jsonl</p>
      <p class="kpi-explica">{tag_fonte("historico")} É daqui que sai a seção Mudanças. Cada execução de
      arquivar.py grava um retrato do dia; com dois ou mais, dá para comparar.</p>
    </article>
  </div>
  <p class="nota-escura bloco-fixo">Esta página não exibe nenhum dado de help desk nem nome de integrante da equipe.
  Arquivo estático · sem dependências externas · pode ser enviado sozinho.</p>
</section>"""

    # ============================================================= cabeçalho
    gerado_em = agora.strftime("%d/%m/%Y %H:%M")
    data_dados = date.today().strftime("%d/%m/%Y")
    aviso = ""
    if estado == "desatualizada":
        aviso = '<a class="aviso" href="#fontes" data-alvo="fontes">fonte desatualizada</a>'
    elif estado == "indisponivel":
        aviso = '<a class="aviso grave" href="#fontes" data-alvo="fontes">fonte indisponível</a>'

    favo_cheio, _ = favo_svg(FAVO_CHEIO, passo=63, fonte=12, classe="favo-cheio", nav=FAVO_NAV_FIN, ordem=FAVO_ORDEM_FIN)
    favo_compacto, _ = favo_svg(FAVO_COMPACTO, passo=66, fonte=12, classe="favo-compacto", nav=FAVO_NAV_FIN, ordem=FAVO_ORDEM_FIN)
    menu_simples = "".join(f'<a href="#{id_}" data-alvo="{id_}">{esc(rotulo)}</a>' for id_, rotulo in FAVO_ORDEM_FIN)

    payload = {"labels": serie["labels"], "secao": "evolucao", "graficos": graficos_payload}
    script = f"<script>{JS_UI}</script>"
    if gsap_js is not None:
        script += f"\n<script>{gsap_js}</script>\n<script>{JS_HEADER}</script>"
    marcador_anim = (
        '<script>(function(){try{if(!(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches))'
        'document.documentElement.classList.add("anim")}catch(e){}})();</script>'
    ) if gsap_js is not None else ""
    if chart_js is not None and graficos_payload:
        script += f"\n<script>{chart_js}</script>\n<script>{JS_CHARTS.replace('__DATA__', json_inline(payload))}</script>"

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Licenças — Financeiro — {esc(data_dados)}</title>
<style>{fontes_css}{CSS}</style>
{marcador_anim}
</head>
<body>
<a class="pular" href="#destaques">Ir para o conteúdo</a>
<div class="palco">
<header class="topo">
  <a class="marca" href="#destaques" data-alvo="destaques" aria-label="Licenças — início">
    {ABELHA_SVG}
    <h1 class="wordmark"><span class="w" style="--traco-w:64%">Licenças{TRACO_SVG}</span><span class="w">Financeiro{TRACO_SVG}</span></h1>
  </a>
  <nav class="favo" aria-label="Seções do painel">
    {favo_cheio}
    {favo_compacto}
    <div class="menu-simples">{menu_simples}</div>
  </nav>
</header>
<p class="carimbo"><span>Gerado em <time datetime="{agora.strftime('%Y-%m-%dT%H:%M')}">{esc(gerado_em)}</time></span><span class="sep" aria-hidden="true">•</span><span>Dados de {esc(data_dados)}</span>{aviso}</p>
<main class="colmeia" id="colmeia">
{secao_destaques}
{secao_briefing}
{secao_radar}
{secao_mov}
{secao_evolucao}
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
    except Exception as e:  # último recurso: nunca deixar o financeiro sem página
        print(f"ERRO ao montar dashboard financeiro: {type(e).__name__}: {e}")
        conteudo = (
            "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'><title>Licenças</title></head>"
            f"<body><h1>Licenças</h1><p>Falha ao gerar o painel: {esc(type(e).__name__)}</p></body></html>")
    SAIDA.write_text(conteudo, encoding="utf-8")
    print(f"OK -> {SAIDA} ({len(conteudo.encode('utf-8')) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
