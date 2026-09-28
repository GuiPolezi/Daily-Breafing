"""Gera dashboard_licencas.html: o briefing de licencas.

Le dados/licencas.json, historico/licencas.jsonl, historico/metricas.jsonl e
relatorio_licencas.md. Uma unica fonte de negocio: o painel interno de
licencas. Nenhum dado de help desk, nenhum nome de tecnico -- por decisao de
privacidade esta pagina nunca exibe pessoas da equipe.

O que ela responde, que o diario nao responde:
  - qual a foto de hoje por faixa de prazo (vencida, ate 7 dias, ate 30...);
  - o que MUDOU desde o retrato anterior: renovou, venceu, entrou, saiu.
    Isso vem de historico/licencas.jsonl, escrito por arquivar.py.

Limite conhecido: a fonte expoe apenas cliente, sistema e vencimento. Nao ha
valor, contrato nem responsavel -- entao este painel e um radar de renovacao,
nao um painel de receita.

Identidade visual vem de dashboard_base.py. Nunca lanca excecao por dado
ausente: a secao degrada e o resto da pagina sai.

Configuracao (.env): DASHBOARD_DIAS_GRAFICO.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

from dashboard_base import (
    CHEVRON_SVG, CSS, CSS_SINO, JS_CHARTS, JS_HEADER, JS_UI,
    RAIZ, badge_delta, card_grafico,
    carregar_chart_js, carregar_fontes_css, carregar_gsap, cfg_int, coletar_nomes_tecnicos,
    comparar_licencas,
    data_do_briefing, dividir_briefing, esc,
    aviso_janela_mudou, fmt_num, grupo_fonte, janela_licencas, janela_licencas_comparavel,
    logo_sino_licencas, seta_delta, grafico, json_inline, label_dia, ler_historico, ler_historico_licencas,
    ler_json, markdown_para_html, menu_licencas, nota_secao, num_html, redigir_nomes, secao_vazia,
    serie_historico,
    serie_tem_dado, status_fonte, tag_fonte, titulo_secao,
)

RELATORIO = RAIZ / "relatorio_licencas.md"
SAIDA = RAIZ / "dashboard_licencas.html"
DIAS_GRAFICO = cfg_int("DASHBOARD_DIAS_GRAFICO", 30)

# Seis seções, seis blocos do menu. O id "briefing" é mantido porque o JS
# reaproveitado depende dele (setas do slider); "evolucao", por causa dos gráficos.
# O bloco "Licenças" leva à seção "radar", que segue com o título Radar (decisão do
# Guilherme, 28/09/2026). A ordem das seções na página acompanha a do menu -- é ela
# que as setas, a roda do mouse e a rolagem do celular percorrem.
MENU_ORDEM = [
    ("destaques", "Panorama"), ("radar", "Licenças"), ("briefing", "Relatório"),
    ("movimentacao", "Mudanças"), ("evolucao", "Evolução"), ("fontes", "Fonte"),
]

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


def destaque_vencimento(itens: list, cabe, mais_recente: bool) -> dict | None:
    """O vencimento em destaque: data mais recente (vencidas) ou mais próxima (vencendo).

    Na data escolhida pode haver vários clientes -- é comum. Um vai em destaque (o
    de mais sistemas afetados; empate, ordem alfabética) e os outros vão em `outros`,
    para o card avisar em vez de esconder (decisão do Guilherme, 28/09/2026).
    """
    validos = [i for i in (itens or []) if isinstance(i, dict) and isinstance(i.get("dias"), int) and cabe(i["dias"])]
    if not validos:
        return None
    alvo = (max if mais_recente else min)(i["dias"] for i in validos)
    por_cliente: dict[str, list[str]] = {}
    vencimento = ""
    for i in validos:
        if i["dias"] != alvo:
            continue
        vencimento = vencimento or str(i.get("vencimento") or "")
        cliente = str(i.get("cliente") or "").strip() or "(cliente não informado)"
        sistema = str(i.get("sistema") or "").strip() or "(sistema não informado)"
        lista = por_cliente.setdefault(cliente, [])
        if sistema not in lista:
            lista.append(sistema)
    ordem = sorted(por_cliente, key=lambda c: (-len(por_cliente[c]), c.casefold()))
    return {"vencimento": vencimento, "cliente": ordem[0], "sistemas": por_cliente[ordem[0]],
            "outros": ordem[1:]}


def card_contagem(titulo: str, sub: str, valor: int, anterior_valor, tom: str) -> str:
    """Card de contagem do Panorama: número grande, seta e badge contra o dia anterior."""
    seta = seta_delta(valor, anterior_valor, melhor="menor")
    badge = badge_delta(valor, anterior_valor, melhor="menor")
    return f"""
<article class="card lic-card {tom}">
  <div class="lic-card-cabeca"><div><h3 class="lic-card-titulo">{esc(titulo)}</h3>
    <p class="lic-card-sub">{esc(sub)}</p></div></div>
  <p class="lic-kpi-num">{num_html(valor)}{seta}</p>
  {f'<div class="lic-kpi-juizo">{badge}</div>' if badge else ""}
</article>"""


def card_vencimento(titulo: str, sub: str, destaque: dict | None, vazio: str, tom: str) -> str:
    """Card do vencimento em destaque: data, cliente, sistemas e os outros clientes da data."""
    if destaque is None:
        corpo, data = f'<p class="lic-vazio">{esc(vazio)}</p>', ""
    else:
        LIMITE = 3  # sistemas listados; o resto vira "+N" (o painel tem altura fixa)
        sist = destaque["sistemas"]
        itens = "".join(f"<li>{esc(x)}</li>" for x in sist[:LIMITE])
        if len(sist) > LIMITE:
            itens += f"<li>+ {len(sist) - LIMITE} outro(s)</li>"
        outros = destaque["outros"]
        extra = (f'<p class="lic-venc-extra">+ {len(outros)} cliente(s) na mesma data: '
                 f'{esc(", ".join(outros))}</p>') if outros else ""
        corpo = f"""<div class="lic-venc-corpo">
    <div><p class="lic-venc-rotulo">Cliente:</p><p class="lic-venc-cliente">{esc(destaque["cliente"])}</p></div>
    <span class="lic-venc-divisor" aria-hidden="true"></span>
    <div class="lic-venc-sistemas"><p class="lic-venc-rotulo">Sistemas Afetados</p><ul>{itens}</ul></div>
  </div>{extra}"""
        data = f'<p class="lic-card-data">{esc(destaque["vencimento"])}</p>'
    return f"""
<article class="card lic-card {tom}">
  <div class="lic-card-cabeca"><div><h3 class="lic-card-titulo">{esc(titulo)}</h3>
    <p class="lic-card-sub">{esc(sub)}</p></div>{data}</div>
  {corpo}
</article>"""


def agrupar_por_cliente_data(itens, mais_recente: bool) -> list[dict]:
    """Uma entrada por cliente + data de vencimento, com os sistemas daquela data.

    Vencidas: `mais_recente` -> maior `dias` primeiro (a que acabou de vencer).
    Vencendo: menor `dias` primeiro. Sem data legível vai para o fim nas duas.
    """
    grupos: dict[tuple, dict] = {}
    for i in (itens or []):
        if not isinstance(i, dict):
            continue
        cliente = str(i.get("cliente") or "").strip() or "(cliente não informado)"
        venc = str(i.get("vencimento") or "").strip()
        g = grupos.setdefault((cliente, venc), {"cliente": cliente, "vencimento": venc,
                                                "dias": i.get("dias"), "sistemas": []})
        sistema = str(i.get("sistema") or "").strip() or "(sistema não informado)"
        if sistema not in g["sistemas"]:
            g["sistemas"].append(sistema)

    def chave(g: dict):
        d = g["dias"] if isinstance(g["dias"], int) else None
        sem_data = d is None
        ordem = 0 if sem_data else (-d if mais_recente else d)
        return (sem_data, ordem, g["cliente"].casefold())
    return sorted(grupos.values(), key=chave)


def faixa_grupo_lic(rotulo: str, tom: str, texto: str) -> str:
    """Faixa que abre um grupo de cards de licença: pílula colorida + explicação."""
    return (f'<div class="faixa-grupo bloco-fixo"><span class="pilula-fonte lic-pilula {tom}">'
            f'<span class="ponto" aria-hidden="true"></span>{esc(rotulo)}</span>'
            f'<p class="faixa-grupo-texto">{esc(texto)}</p></div>')


def grade_lic(grupos: list[dict], estado: str, vazio: str) -> str:
    """Grade de cards (cliente, data, prazo e sistemas) de um grupo de licenças."""
    if not grupos:
        return f'<p class="lic-grupo-vazio bloco-fixo">{esc(vazio)}</p>'
    cards = []
    for g in grupos:
        d = g["dias"] if isinstance(g["dias"], int) else None
        dias = ""
        if estado == "vencendo":
            dias = ("sem data" if d is None else "Hoje" if d == 0 else f"Em {d} d")
            dias = f'<span class="lic-item-dias">{esc(dias)}</span>'
        chips = "".join(f'<span class="lic-chip">{esc(x)}</span>' for x in g["sistemas"])
        cards.append(f"""
<article class="lic-item {estado}">
  <div class="lic-item-topo"><h3 class="lic-item-cliente">{esc(g["cliente"])}</h3>
    <p class="lic-item-prazo">{esc(g["vencimento"] or "—")}{dias}</p></div>
  <p class="lic-item-rotulo">Sistemas</p>
  <div class="lic-item-sistemas">{chips}</div>
</article>""")
    return f'<div class="grade lic-grade bloco-elastico">{"".join(cards)}</div>'


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
    ignoradas = (licencas or {}).get("ignoradas_homolog_teste")

    hoje_iso = date.today().isoformat()
    anterior: dict = {}
    if historico:
        anterior = historico[-2] if historico[-1].get("data") == hoje_iso and len(historico) >= 2 else (
            historico[-1] if historico[-1].get("data") != hoje_iso else {})

    # ============================================================= 1. PANORAMA
    # Quatro cards (design do Guilherme, 28/09/2026): contagem à esquerda, o vencimento
    # em destaque à direita. Linha de cima = o que já venceu; de baixo = o que vem aí.
    if licencas is None:
        secao_destaques = secao_vazia("destaques", "Panorama do Dia", "Fonte de licenças indisponível nesta geração.")
    else:
        janela = janela_licencas(licencas)
        # coleta anterior a `janela_dias`: as vencidas eram de 60 dias e as vencendo
        # não tinham corte -- o texto não pode prometer "próximos 60"
        tem_janela = bool((licencas or {}).get("janela_dias"))
        sub_vencendo = (f"Licenças que vencerão nos próximos {janela} dias" if tem_janela
                        else "Licenças com vencimento próximo no painel")
        texto_faixa = ("Licenças de produção, sem homologação e sem teste. " +
                       (f"Vencidas nos últimos {janela} dias e vencendo nos próximos {janela}." if tem_janela
                        else f"Vencidas nos últimos {janela} dias; vencendo, o que o painel listar."))
        # período mudou desde ontem (ex.: 60 -> 30)? a variação é da régua: sem badge
        lic_comparavel = janela_licencas_comparavel(licencas, anterior)
        aviso_janela = ("" if lic_comparavel else
                        f'<p class="secao-nota bloco-fixo">{esc(aviso_janela_mudou(licencas))}</p>')
        vencidas = (licencas or {}).get("vencidas_recentes") or []
        vencendo = (licencas or {}).get("vencendo_em_breve") or []
        # o último que venceu = maior `dias` negativo; o próximo = menor `dias` >= 0
        ultimo = destaque_vencimento(vencidas, lambda d: d < 0, mais_recente=True)
        proximo = destaque_vencimento(vencendo, lambda d: d >= 0, mais_recente=False)
        cards = [
            card_contagem("Licenças Vencidas", f"Licenças que venceram nos últimos {janela} dias",
                          n_vencidas, anterior.get("lic_vencidas_recentes") if lic_comparavel else None, "ouro"),
            card_vencimento("Último Vencimento", "Cliente mais recente que teve a licença vencida",
                            ultimo, f"Nenhuma licença venceu nos últimos {janela} dias.", "ouro"),
            card_contagem("Licenças Vencendo em Breve", sub_vencendo,
                          n_vencendo, anterior.get("lic_vencendo") if lic_comparavel else None, "gelo"),
            card_vencimento("Próximo Vencimento", "Próximo cliente a ter sua licença vencida",
                            proximo, "Nenhuma licença com vencimento próximo.", "gelo"),
        ]
        secao_destaques = f"""
<section class="secao" id="destaques" data-scroll aria-labelledby="t-destaques">
  <header class="secao-cabeca empilhada bloco-fixo">
    {titulo_secao("Panorama do Dia", "destaques")}
    <p class="subtitulo">{esc(date.today().strftime("%d/%m/%Y"))}</p>
  </header>
  {grupo_fonte("licencas", texto_faixa, rotulo="Licença")}
  {aviso_janela}
  <div class="panorama-lic bloco-elastico">{''.join(cards)}</div>
</section>"""

    # ============================================================= 2. RELATÓRIO
    if relatorio is None:
        secao_briefing = secao_vazia("briefing", "Relatório", "Relatório indisponível (relatorio_licencas.md ausente ou vazio).")
    else:
        # Mesmo que o relatorio cite um tecnico, ele nao chega ao briefing de licencas.
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

    # ============================================================= 3. LICENÇAS (id "radar")
    # Cards por cliente + data (design do Guilherme, 28/09/2026): vencidas da mais recente
    # para a mais antiga, vencendo da mais próxima para a mais distante. O id segue
    # "radar" (menu e âncoras); o título passou a ser "Licenças", como no menu.
    if not itens:
        secao_radar = secao_vazia("radar", "Licenças", "Nenhuma licença vencida recentemente ou vencendo em breve.")
    else:
        janela_r = janela_licencas(licencas)
        tem_janela_r = bool((licencas or {}).get("janela_dias"))
        grupos_venc = agrupar_por_cliente_data((licencas or {}).get("vencidas_recentes"), mais_recente=True)
        grupos_prox = agrupar_por_cliente_data((licencas or {}).get("vencendo_em_breve"), mais_recente=False)
        texto_venc = f"Licenças que venceram nos últimos {janela_r} dias · da vencida mais recente para a mais antiga"
        texto_prox = ((f"Licenças que vão vencer nos próximos {janela_r} dias" if tem_janela_r
                       else "Licenças com vencimento próximo no painel") + " · da mais próxima para a mais distante")
        secao_radar = f"""
<section class="secao rolavel" id="radar" data-scroll aria-labelledby="t-radar">
  <header class="secao-cabeca empilhada bloco-fixo">{titulo_secao("Licenças", "radar")}</header>
  {faixa_grupo_lic("Vencidas", "", texto_venc)}
  {grade_lic(grupos_venc, "vencida", f"Nenhuma licença venceu nos últimos {janela_r} dias.")}
  {faixa_grupo_lic("Vencendo", "vencendo", texto_prox)}
  {grade_lic(grupos_prox, "vencendo", "Nenhuma licença com vencimento próximo.")}
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
  {'<p class="secao-nota bloco-fixo">O período das listas mudou entre os dois retratos; o retrato anterior foi '
   'refiltrado pela régua nova antes da comparação, então nada aparece como “saiu” só por causa da troca.</p>'
   if mov["regua_ajustada"] else ""}
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

    menu_html = menu_licencas(MENU_ORDEM)

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
<title>SINO Licenças — {esc(data_dados)}</title>
<style>{fontes_css}{CSS}{CSS_SINO}</style>
{marcador_anim}
</head>
<body class="pag-licencas">
<a class="pular" href="#destaques">Ir para o conteúdo</a>
<div class="palco">
<header class="topo">
  {logo_sino_licencas()}
  {menu_html}
</header>
<p class="carimbo"><span>Gerado em <time datetime="{agora.strftime('%Y-%m-%dT%H:%M')}">{esc(gerado_em)}</time></span><span class="sep" aria-hidden="true">•</span><span>Dados de {esc(data_dados)}</span>{aviso}</p>
<main class="colmeia" id="colmeia">
{secao_destaques}
{secao_radar}
{secao_briefing}
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
    except Exception as e:  # último recurso: nunca deixar o briefing de licenças sem página
        print(f"ERRO ao montar dashboard de licenças: {type(e).__name__}: {e}")
        conteudo = (
            "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'><title>Licenças</title></head>"
            f"<body><h1>Licenças</h1><p>Falha ao gerar o painel: {esc(type(e).__name__)}</p></body></html>")
    SAIDA.write_text(conteudo, encoding="utf-8")
    print(f"OK -> {SAIDA} ({len(conteudo.encode('utf-8')) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
