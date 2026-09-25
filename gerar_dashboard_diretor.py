"""Gera dashboard_diretor.html: leitura executiva do dia, para a diretoria.

Le as mesmas fontes do briefing diario (dados/*.json + historico) e o texto de
relatorio_diretor.md. A diferenca nao e de dados, e de altitude:

  - o diario e operacional: lista chamado por chamado, com id e idade;
  - este e agregado: volume, divisao por sistema, produtividade de suporte e de
    desenvolvimento, e para onde a curva esta indo.

Por isso aqui NAO existe tabela de chamado individual. Nomes de tecnico e de
desenvolvedor aparecem (decisao explicita para esta pagina), respeitando
DASHBOARD_MOSTRAR_RANKING.

Identidade visual vem de dashboard_base.py. Nunca lanca excecao por dado
ausente: a secao degrada e o resto da pagina sai.

Configuracao (.env): DASHBOARD_MOSTRAR_RANKING, DASHBOARD_DIAS_GRAFICO,
DASHBOARD_SISTEMAS_DESTAQUE.
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path

from dashboard_base import (
    AVISO_BASE_MUDOU, CSS, CSS_SINO, JS_CHARTS, JS_HEADER_SINO, JS_UI,
    LIMITE_SISTEMAS, MOSTRAR_RANKING, RAIZ, badge_delta, barras_distribuicao,
    base_status_comparavel, card_grafico, card_sino,
    cards_equipes_dev,
    carregar_chart_js, carregar_fontes_css, carregar_gsap, cfg_int, coletar_nomes_tecnicos,
    data_do_briefing, dividir_briefing, esc, explica, fmt_num,
    grafico, grupo_fonte, json_inline, ler_historico, ler_json, logo_sino, markdown_para_html,
    menu_grade, nota_secao,
    num_html, ranking_semanal, redigir_nomes, render_ranking, secao_agenda, secao_vazia, serie_historico,
    serie_tem_dado, seta_delta, status_fonte, tabela_sistemas, tag_fonte, titulo_secao,
)
from gerar_dashboard import CAMPO_HISTORICO_SISTEMA, SERIE_SISTEMA, SISTEMAS_DESTAQUE, dic

RELATORIO = RAIZ / "relatorio_diretor.md"
SAIDA = RAIZ / "dashboard_diretor.html"
DIAS_GRAFICO = cfg_int("DASHBOARD_DIAS_GRAFICO", 30)

FONTES = [
    ("helpdesk", "Help desk", "helpdesk.json"),
    ("licencas", "Licenças", "licencas.json"),
    ("agenda", "Agenda", "agenda.json"),
]

# Menu em blocos (tema SINO). SEMPRE duas linhas: as colunas saem da contagem
# (ver menu_grade abaixo), porque uma terceira linha transborda o .topo, que
# tem altura fixa. Com 6 itens dá 3 colunas, como no mockup original.
# "fontes" voltou ao menu, como último bloco (decisão do Guilherme, 23/09/2026;
# em 21/09 tinha saído). Com 8 itens ficam 4 colunas, a mesma largura dos 7.
# Os ids "briefing" e "evolucao" são mantidos porque o JS reaproveitado depende
# deles (slider e gráficos).
MENU_ORDEM = [
    ("destaques", "Panorama"), ("briefing", "Leitura"), ("suporte", "Suporte"),
    ("desenvolvimento", "Desenv."), ("desenv-analise", "Análise"),
    ("agenda", "Agenda"), ("evolucao", "Tendência"), ("fontes", "Fontes"),
]

# Rótulos curtos do mockup para os sistemas em destaque. Sistema fora do mapa
# cai no formato genérico -- DASHBOARD_SISTEMAS_DESTAQUE é configurável.
ROTULO_SISTEMA = {
    "Site": "Tickets Site - Aberto",
    "Siscam 9": "Tickets Sis. 9 - Aberto",
    "Siscam 8": "Tickets Sis. 8 - Aberto",
}



def ler_relatorio() -> str | None:
    if not RELATORIO.exists():
        return None
    try:
        texto = RELATORIO.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return texto if texto.strip() else None


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

    helpdesk, licencas = dados["helpdesk"], dados["licencas"]
    atend = dic((helpdesk or {}).get("atendimentos_ultimo_dia_util"))
    secao_agenda_html = secao_agenda(dados["agenda"])
    fila = dic((helpdesk or {}).get("fila"))
    por_sistema = dic(fila.get("por_sistema"))
    idade = dic(fila.get("idade"))
    dev = dic((helpdesk or {}).get("desenvolvimento"))
    por_dev = dic(dev.get("por_dev"))

    hoje_iso = date.today().isoformat()
    anterior: dict = {}
    if historico:
        anterior = historico[-2] if historico[-1].get("data") == hoje_iso and len(historico) >= 2 else (
            historico[-1] if historico[-1].get("data") != hoje_iso else {})

    # A fila de hoje e a de ontem saem da mesma régua de status? Quando não saem,
    # o badge de comparação some: o número mudou por configuração, não por operação.
    base_comparavel = base_status_comparavel(helpdesk, anterior)
    CHAVES_DA_FILA = {
        "fila_abertos", "meus_abertos", "fila_site", "fila_siscam9", "fila_siscam8",
        "fila_mais_90", "fila_ate_7", "fila_corretivo", "fila_evolutivo",
        "dev_atribuidos", "dev_em_status",
    }

    def valor_anterior(chave: str | None):
        """Valor do dia anterior, ou None quando a base de status mudou."""
        if not chave:
            return None
        if not base_comparavel and chave in CHAVES_DA_FILA:
            return None
        return anterior.get(chave)

    aviso_base = "" if base_comparavel else f'<p class="secao-nota bloco-fixo">{AVISO_BASE_MUDOU}</p>'

    n_vencidas = len((licencas or {}).get("vencidas_recentes") or [])
    n_vencendo = len((licencas or {}).get("vencendo_em_breve") or [])
    nomes_tecnicos = coletar_nomes_tecnicos(historico, helpdesk)
    rank = ranking_semanal(historico)
    # Gráficos de todas as seções (Análise e Tendência); cada um diz a sua.
    graficos_payload: list[dict] = []

    # ============================================================= 1. PANORAMA
    # Dois grupos, cada um aberto por uma faixa que declara a fonte (grupo_fonte):
    # é ela que diz de onde vêm os números dos cards abaixo, no lugar da etiqueta
    # que antes se repetia dentro de cada card.
    def par(atual, chave_ou_valor, melhor: str, do_historico: bool = False):
        """(seta, badge) de um card. Um único valor anterior alimenta os dois."""
        ant = anterior.get(chave_ou_valor) if do_historico else valor_anterior(chave_ou_valor)
        return (seta_delta(atual, ant, melhor=melhor),
                badge_delta(atual, ant, melhor=melhor))

    v_atend = atend.get("total_atendimentos_fechados")
    s_atend, b_atend = par(v_atend, "atend_total", "maior", do_historico=True)
    v_fila = (helpdesk or {}).get("fila_total_abertos")
    s_fila, b_fila = par(v_fila, "fila_abertos", "menor")

    cartoes = [
        card_sino("Atendimentos Fechados", v_atend, sub="Equipe Suporte",
                  seta=s_atend, delta=b_atend,
                  rodape=f'último dia útil: <b>{esc(atend.get("dia") or "—")}</b>',
                  explicacao=explica("atend_fechados"), indice=0),
        card_sino("Total de Tickets na Fila", v_fila, seta=s_fila, delta=b_fila,
                  explicacao=explica("fila_total"), indice=1),
    ]
    for i, rotulo_sis in enumerate(SISTEMAS_DESTAQUE):
        valor = por_sistema.get(rotulo_sis)
        seta_s, badge_s = par(valor, CAMPO_HISTORICO_SISTEMA.get(rotulo_sis), "menor")
        cartoes.append(card_sino(
            ROTULO_SISTEMA.get(rotulo_sis, f"Tickets {rotulo_sis} - Aberto"), valor,
            seta=seta_s if valor is not None else "",
            delta=badge_s if valor is not None else "",
            rodape="" if valor is not None else "recorte por sistema ainda não coletado",
            explicacao=explica("fila_sistema"), indice=2 + i))

    v_dev = dev.get("total_atribuidos_a_devs")
    s_dev, b_dev = par(v_dev, "dev_atribuidos", "menor")
    cartoes.append(card_sino(
        "Tickets com Devs.", v_dev, seta=s_dev, delta=b_dev,
        rodape=f'<b>{fmt_num(dev.get("total_em_status_dev"))}</b> em status de desenvolvimento' if dev else "",
        explicacao=explica("dev_atribuidos"), indice=2 + len(SISTEMAS_DESTAQUE)))

    # Sem técnico no campo do chamado. Sai da contagem da fila inteira
    # (fila.por_tecnico), não da amostra. Sem série no histórico: sem badge.
    por_tecnico_fila = fila.get("por_tecnico")
    v_sem = (por_tecnico_fila.get("(sem tecnico)", 0)
             if isinstance(por_tecnico_fila, dict) else None)
    cartoes.append(card_sino(
        "Tickets sem atribuição", v_sem,
        rodape="nenhum técnico responsável" if v_sem is not None else "",
        explicacao=explica("sem_atribuicao"), indice=3 + len(SISTEMAS_DESTAQUE)))

    v_90 = idade.get("mais_de_90_dias")
    s_90, b_90 = par(v_90, "fila_mais_90", "menor")
    cartoes.append(card_sino(
        "Em aberto +90 dias", v_90, seta=s_90, delta=b_90,
        rodape="envelhecimento da fila" if idade else "",
        explicacao=explica("idade_90"), indice=4 + len(SISTEMAS_DESTAQUE)))

    s_lic, b_lic = par(n_vencidas, "lic_vencidas_recentes", "menor", do_historico=True)
    card_licenca = card_sino(
        "Licenças Vencidas", n_vencidas, seta=s_lic, delta=b_lic,
        rodape=f"<b>{fmt_num(n_vencendo)}</b> vencendo em breve",
        explicacao=explica("lic_vencidas"), indice=0)

    secao_destaques = f"""
<section class="secao rolavel" id="destaques" data-scroll aria-labelledby="t-destaques">
  <header class="secao-cabeca empilhada bloco-fixo">
    {titulo_secao("Panorama do Dia", "destaques")}
    <p class="subtitulo">{esc(date.today().strftime("%d/%m/%Y"))}</p>
  </header>
  {aviso_base}
  {grupo_fonte("milldesk", "Volume operacional do dia. Cada card diz o que é e como o número é "
                           "contado; os chamados individuais ficam no briefing operacional, não aqui.",
               rotulo="Mildesk")}
  <div class="grade painel-exec bloco-elastico">{''.join(cartoes)}</div>
  {grupo_fonte("licencas", "Licenças de produção, sem homologação e sem teste.", rotulo="Licenças")}
  <div class="grade painel-exec bloco-elastico">{card_licenca}</div>
</section>"""

    # ============================================================= 2. LEITURA
    if relatorio is None:
        secao_briefing = secao_vazia("briefing", "Leitura do dia",
                                     "Relatório indisponível (relatorio_diretor.md ausente ou vazio).")
    else:
        texto = relatorio if MOSTRAR_RANKING else redigir_nomes(relatorio, nomes_tecnicos)
        titulo_h1, slides = dividir_briefing(texto)
        data_brief = data_do_briefing(titulo_h1) or agora.strftime("%d/%m/%Y %H:%M")
        if not slides:
            slides = [{"titulo": "Leitura do dia", "md": texto}]
        # O trecho antes do primeiro '## ' vira um slide "Briefing" (fonte e hora
        # da coleta) que a diretoria não precisa ver -- decisão do Guilherme,
        # 25/09/2026. Mesmo corte do semanal. Se for o único slide, fica.
        if len(slides) > 1 and slides[0]["titulo"] == "Briefing":
            slides = slides[1:]
        itens_slides, pontos = [], []
        for i, s in enumerate(slides):
            corpo_md = s["md"]
            n_itens = len(re.findall(r"^\s*[-*+]\s+", corpo_md, flags=re.M))
            duas = " duas-colunas" if (n_itens >= 6 or len(corpo_md) > 780) else ""
            corpo = markdown_para_html(corpo_md, base=4) or "<p>—</p>"
            itens_slides.append(
                f'<li class="slide" role="group" aria-roledescription="slide" aria-label="{i + 1} de {len(slides)}: {esc(s["titulo"])}">'
                f'<article class="card card-slide" data-scroll><h3 class="kpi-rotulo">{esc(s["titulo"])}</h3>'
                f'<div class="slide-corpo leitura-exec{duas}">{corpo}</div></article></li>')
            pontos.append(f'<button type="button" role="tab" aria-selected="{"true" if i == 0 else "false"}" aria-label="{esc(s["titulo"])}"></button>')
        secao_briefing = f"""
<section class="secao" id="briefing" aria-labelledby="t-briefing">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Leitura do dia", "briefing")}<p class="lado carimbo-briefing">{esc(data_brief)}</p></header>
  <div class="slider bloco-elastico" aria-roledescription="carrossel" aria-label="Tópicos da leitura">
    <div class="slides-janela"><ul class="slides">{''.join(itens_slides)}</ul></div>
    <div class="slider-controles">
      <button class="seta" type="button" data-dir="-1" aria-label="Tópico anterior">‹</button>
      <div class="indicadores" role="tablist" aria-label="Tópicos">{''.join(pontos)}</div>
      <button class="seta" type="button" data-dir="1" aria-label="Próximo tópico">›</button>
    </div>
  </div>
</section>"""

    # ============================================================= 3. SUPORTE
    if MOSTRAR_RANKING and rank["tecnicos"]:
        titulo_rank, nomes_rank, valores_rank = "Atendimentos por técnico", rank["tecnicos"], rank["valores"]
    elif rank["dias"]:
        titulo_rank, nomes_rank, valores_rank = "Atendimentos da equipe por dia", rank["dias"], rank["totais"]
    else:
        titulo_rank, nomes_rank, valores_rank = "", [], []
    if not nomes_rank:
        secao_suporte = secao_vazia("suporte", "Suporte", "Sem registros de atendimentos no histórico.")
    else:
        # Um card por técnico monitorado (HELPDESK_AGENT_NAME). "abertos" vem do
        # coletor e já conta todos os status menos os excluídos (hoje só Fechado).
        # Com o ranking desligado, o nome vira "Técnico N" — mesma regra dos demais.
        por_agente = dic((helpdesk or {}).get("por_agente"))
        agentes = (helpdesk or {}).get("agentes_monitorados")
        agentes = agentes if isinstance(agentes, list) else []  # string corrompida iteraria letra a letra
        cards_tecnicos = [
            card_sino(f"Tickets com {nome.split()[0]}" if MOSTRAR_RANKING else f"Técnico {i + 1}",
                      dic(por_agente.get(nome)).get("abertos"),
                      explicacao=explica("tickets_tecnico"), indice=i)
            for i, nome in enumerate(agentes)
            if isinstance(nome, str) and nome.strip()
        ]
        grade_tecnicos = (f'<div class="grade painel-exec bloco-fixo">{"".join(cards_tecnicos)}</div>'
                          if cards_tecnicos else "")
        secao_suporte = f"""
<section class="secao rolavel" id="suporte" data-scroll aria-labelledby="t-suporte">
  <header class="secao-cabeca dividida bloco-fixo">
    {titulo_secao("Suporte", "suporte")}
    <div class="lado"><div class="kpi-medida"><p class="kpi-numero menor">{num_html(rank["total_periodo"])}</p>
      <p class="kpi-legenda">atendimentos fechados em {len(rank["dias"])} dia(s) útil(eis)</p></div></div>
  </header>
  {nota_secao("milldesk", "Produtividade do suporte e estado da fila em aberto.")}
  <article class="card card-ranking bloco-elastico" data-scroll>
    <div class="kpi-cabeca"><h3 class="kpi-rotulo">{esc(titulo_rank)}</h3>{tag_fonte("historico")}</div>
    {render_ranking(nomes_rank, valores_rank)}
    {explica("ranking")}
  </article>
  {grade_tecnicos}
</section>"""

    # ============================================================= 4. DESENVOLVIMENTO
    if not dev:
        secao_dev = secao_vazia("desenvolvimento", "Desenvolvimento",
                                "Bloco de desenvolvimento ainda não coletado "
                                "(campo desenvolvimento em dados/helpdesk.json).")
    elif not por_dev and not dev.get("total_em_status_dev"):
        secao_dev = secao_vazia("desenvolvimento", "Desenvolvimento",
                                "Nenhum desenvolvedor configurado em HELPDESK_DEV_NAMES.")
    else:
        carga = {}
        cards_dev = []
        # Ordenados do maior para o menor: a diretoria lê de cima para baixo.
        ordenados = sorted(por_dev.items(), key=lambda kv: -(dic(kv[1]).get("abertos") or 0))
        for i, (nome, bloco) in enumerate(ordenados):
            b = dic(bloco)
            rotulo = nome if MOSTRAR_RANKING else f"Desenvolvedor {i + 1}"
            carga[rotulo] = b.get("abertos")
            # total_no_nome é espelho de abertos; o fallback mantém a página de pé
            # com um helpdesk.json gravado antes desses campos existirem.
            total_nome = b.get("total_no_nome", b.get("abertos"))
            em_trabalho = b.get("em_trabalho")
            outros = (total_nome - em_trabalho
                      if isinstance(total_nome, int) and isinstance(em_trabalho, int) else None)
            antigo = b.get("mais_antigo_dias")
            linha_trabalho = ""
            if em_trabalho is not None:
                linha_trabalho = (
                    f'<p class="kpi-mini destaque-trabalho">'
                    f'<span><b>{fmt_num(em_trabalho)}</b> em trabalho ativo</span>'
                    f'<span><b>{fmt_num(outros)}</b> em outros status</span></p>')
            chips = "".join(
                f'<span class="chip">{esc(k)} <b>{fmt_num(v)}</b></span>'
                for k, v in list(dic(b.get("por_sistema")).items())[:LIMITE_SISTEMAS])
            cards_dev.append(f"""
<article class="card kpi card-dev" style="--i:{i}" data-scroll>
  <div class="kpi-cabeca"><p class="dev-nome">{esc(rotulo)}</p></div>
  <p class="kpi-numero menor">{num_html(total_nome)}</p>
  <p class="kpi-legenda">no nome do dev<br><b>{fmt_num(b.get("novos_hoje"))}</b> novos hoje</p>
  {linha_trabalho}
  <p class="kpi-mini"><span><b>{fmt_num(b.get("acima_de_90_dias"))}</b> há mais de 90 dias</span>
     <span><b>{fmt_num(antigo) if antigo is not None else "—"}</b> dias o mais antigo</span></p>
  <div class="dev-sistemas">{chips or '<span class="dist-vazio">sem quebra por sistema</span>'}</div>
</article>""")
        # Carga por desenvolvedor: barra vertical (Chart.js). Rótulo em lista de
        # palavras quebra o nome em linhas em vez de girar ou cortar. Sem a
        # biblioteca, volta às barras horizontais de HTML puro.
        carga_num = {k: v for k, v in carga.items() if isinstance(v, (int, float))}
        if chart_js is not None and carga_num:
            graficos_payload.append(grafico(
                "chartCargaDev", "Chamados no nome", list(carga_num.values()), tipo="barra",
                cor="oliva", labels=[str(k).split() or [str(k)] for k in carga_num],
                secao="desenv-analise"))
            corpo_carga = ('<div class="grafico-caixa"><canvas id="chartCargaDev" role="img" '
                           'aria-label="Carga por desenvolvedor: '
                           + esc(", ".join(f"{k} {v}" for k, v in carga_num.items())) + '"></canvas></div>')
        else:
            corpo_carga = barras_distribuicao(carga, limite=10)
        card_carga = f"""<article class="card card-grafico" data-scroll>
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Carga por desenvolvedor</h3></div>
      {corpo_carga}
      {explica("dev_atribuidos")}
    </article>"""
        # Sistemas sem card no Panorama: sem esta tabela, a página não mostrava
        # quantos chamados eles têm em aberto.
        tabela = tabela_sistemas(fila, SISTEMAS_DESTAQUE)
        bloco_sistemas = (f'<h3 class="kpi-rotulo bloco-fixo">Demais sistemas · chamados em aberto</h3>'
                          f'{tabela}{explica("fila_sistema")}') if tabela else ""
        secao_dev = f"""
<section class="secao rolavel" id="desenvolvimento" data-scroll aria-labelledby="t-desenvolvimento">
  <header class="secao-cabeca dividida bloco-fixo">
    {titulo_secao("Desenvolvimento", "desenvolvimento")}
    <div class="lado">
      <div class="kpi-medida"><p class="kpi-numero menor">{num_html(dev.get("total_atribuidos_a_devs"))}</p><p class="kpi-legenda">Tickets atribuídos a desenvolvedores</p></div>
      <div class="kpi-medida"><p class="kpi-numero menor">{num_html(dev.get("total_em_status_dev"))}</p><p class="kpi-legenda">Tickets em status de desenvolvimento</p></div>
    </div>
  </header>
  {nota_secao("milldesk", "Dois recortes da mesma fila: chamados com um desenvolvedor como responsável, "
                          "e chamados parados em status de desenvolvimento (com dono ou sem).")}
  {cards_equipes_dev(dic(dev.get("por_equipe")), mostrar_nomes=MOSTRAR_RANKING)}
  {explica("dev_equipe")}
  {bloco_sistemas}
  <h3 class="kpi-rotulo bloco-fixo">Por desenvolvedor</h3>
  <div class="grade grade-dev bloco-elastico">{''.join(cards_dev) or '<p class="vazio bloco-elastico">Nenhum chamado atribuído aos desenvolvedores configurados.</p>'}</div>
  {explica("dev_em_trabalho")}
</section>

<section class="secao" id="desenv-analise" data-scroll aria-labelledby="t-desenv-analise">
  <header class="secao-cabeca dividida bloco-fixo">
    {titulo_secao("Análise do desenvolvimento", "desenv-analise")}
    <div class="lado">
      <div class="kpi-medida"><p class="kpi-numero menor">{num_html(dev.get("total_em_status_dev"))}</p><p class="kpi-legenda">em status de desenvolvimento</p></div>
    </div>
  </header>
  <div class="grade duas-colunas-secao bloco-elastico">
    {card_carga}
    <article class="card" data-scroll>
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Em desenvolvimento · por sistema</h3></div>
      {barras_distribuicao(dic(dev.get("em_status_dev_por_sistema")), limite=LIMITE_SISTEMAS)}
      {explica("fila_sistema")}
    </article>
  </div>
</section>"""

    # ============================================================= 5. TENDÊNCIA
    serie = serie_historico(historico, DIAS_GRAFICO)
    n_dias = len(serie["labels"])
    if not n_dias:
        secao_evolucao = secao_vazia("evolucao", "Tendência", "Histórico indisponível (historico/metricas.jsonl vazio ou ausente).")
    else:
        candidatos = [
            ("fila", "chartFila", "Fila de chamados abertos", "azul", "menor"),
            ("atend", "chartAtend", "Atendimentos fechados por dia", "rubro", "maior"),
            ("corretivo", "chartCorretivo", "Fila corretiva (bugs e falhas)", "rubro", "menor"),
            ("evolutivo", "chartEvolutivo", "Fila evolutiva (melhorias)", "verde", "maior"),
        ]
        for rotulo_sis in SISTEMAS_DESTAQUE:
            chave = SERIE_SISTEMA.get(rotulo_sis)
            if chave:
                candidatos.insert(2, (chave, f"chart{chave.title()}", f"Fila · {rotulo_sis}", "oliva", "menor"))
        cartoes_g, graficos_tend = [], []
        for chave, id_canvas, titulo, cor, melhor in candidatos:
            if not serie_tem_dado(serie, chave):
                continue
            graficos_tend.append(grafico(id_canvas, titulo, serie[chave], cor=cor))
            valores = [v for v in serie[chave] if v is not None]
            ult, ant = (valores[-1] if valores else None), (valores[-2] if len(valores) > 1 else None)
            topo = (f'<div class="kpi-linha"><p class="kpi-numero menor">{num_html(ult)}</p>'
                    f'<div class="kpi-juizo">{badge_delta(ult, ant, "vs. registro anterior", melhor=melhor)}</div></div>')
            cartoes_g.append(card_grafico(id_canvas, titulo, f"Tendência: {titulo}", topo))
        if chart_js is None:
            corpo = '<p class="vazio bloco-elastico">Gráficos indisponíveis nesta geração (biblioteca não encontrada).</p>'
            graficos_tend = []
        elif not cartoes_g:
            corpo = '<p class="vazio bloco-elastico">Ainda não há série suficiente para desenhar gráficos.</p>'
        else:
            corpo = f'<div class="grade larga graficos quatro bloco-elastico">{"".join(cartoes_g)}</div>'
        graficos_payload.extend(graficos_tend)
        secao_evolucao = f"""
<section class="secao rolavel" id="evolucao" data-scroll aria-labelledby="t-evolucao">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Tendência", "evolucao")}<p class="lado subtitulo">últimos {DIAS_GRAFICO} dias · {n_dias} registrado(s)</p></header>
  {nota_secao("historico", "Série de historico/metricas.jsonl, uma linha por dia. Dias sem coleta não aparecem; "
                           "métricas novas só existem a partir do dia em que passaram a ser gravadas.")}
  {corpo}
</section>"""

    # ============================================================= 6. FONTES
    rotulo_estado = {"ok": "Atualizada", "desatualizada": "Desatualizada", "indisponivel": "Indisponível"}
    classe_estado = {"ok": "ok", "desatualizada": "aviso", "indisponivel": "grave"}
    origem_da_fonte = {"helpdesk": "milldesk", "licencas": "licencas"}
    from dashboard_base import FONTES_INFO
    cards_fontes = []
    for i, (chave, f) in enumerate(fontes.items()):
        estado = f["estado"]
        dt = f["coletado_em"]
        origem = origem_da_fonte.get(chave, "")
        cards_fontes.append(f"""
<article class="card card-fonte kpi" style="--i:{i}">
  <div class="kpi-cabeca"><h3 class="kpi-rotulo">{esc(f["rotulo"])}</h3><span class="kpi-fonte {classe_estado[estado]}">{rotulo_estado[estado]}</span></div>
  <p class="kpi-numero menor">{esc(dt.strftime("%H:%M")) if dt else '<span class="sem-dado">—</span>'}</p>
  <p class="kpi-legenda">coletado em {esc(dt.strftime("%d/%m/%Y")) if dt else "—"}</p>
  <p class="kpi-explica">{tag_fonte(origem) if origem else ""} {esc(FONTES_INFO.get(origem, ("", "", ""))[2])}</p>
</article>""")
    secao_fontes = f"""
<section class="secao" id="fontes" data-scroll aria-labelledby="t-fontes">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Fontes", "fontes")}<p class="lado subtitulo">{sum(1 for f in fontes.values() if f["estado"] == "ok")} de {len(fontes)} atualizadas</p></header>
  <div class="grade grade-fontes bloco-elastico">{''.join(cards_fontes)}</div>
  <p class="nota-escura bloco-fixo">Coleta somente leitura, uma vez por dia. Arquivo estático, sem dependências externas.</p>
</section>"""

    # ============================================================= cabeçalho
    gerado_em = agora.strftime("%d/%m/%Y %H:%M")
    data_dados = date.today().strftime("%d/%m/%Y")
    desatualizadas = [f["rotulo"] for f in fontes.values() if f["estado"] == "desatualizada"]
    indisponiveis = [f["rotulo"] for f in fontes.values() if f["estado"] == "indisponivel"]
    avisos = ""
    if desatualizadas:
        avisos += f'<a class="aviso" href="#fontes" data-alvo="fontes">{len(desatualizadas)} fonte(s) desatualizada(s)</a>'
    if indisponiveis:
        avisos += f'<a class="aviso grave" href="#fontes" data-alvo="fontes">{len(indisponiveis)} fonte(s) indisponível(is)</a>'

    marca_html = logo_sino(alvo="destaques", titulo="SINO Gestão")
    # DUAS LINHAS, SEMPRE. O menu vive no .topo, que tem altura FIXA
    # (--topo-h) e nenhum overflow declarado: uma terceira linha nao encolhe
    # nada, ela transborda por cima do conteudo. Em 1536x639 -- o notebook que
    # o CLAUDE.md registra como o que ja quebrou o layout -- --topo-h e 116px e
    # tres linhas medem 131px. Com 6 itens dava 3 colunas (o valor historico);
    # a formula mantem isso e absorve sozinha o proximo item do menu.
    menu_html = menu_grade(MENU_ORDEM, colunas=-(-len(MENU_ORDEM) // 2))

    payload = {"labels": serie["labels"], "secao": "evolucao", "graficos": graficos_payload}
    script = f"<script>{JS_UI}</script>"
    if gsap_js is not None:
        script += f"\n<script>{gsap_js}</script>\n<script>{JS_HEADER_SINO}</script>"
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
<title>SINO Gestão — Diretoria — {esc(data_dados)}</title>
<style>{fontes_css}{CSS}{CSS_SINO}</style>
{marcador_anim}
</head>
<body>
<a class="pular" href="#destaques">Ir para o conteúdo</a>
<div class="palco">
<header class="topo">
  {marca_html}
  {menu_html}
</header>
<p class="carimbo"><span>Gerado em <time datetime="{agora.strftime('%Y-%m-%dT%H:%M')}">{esc(gerado_em)}</time></span><span class="sep" aria-hidden="true">•</span><span>Dados de {esc(data_dados)}</span>{avisos}</p>
<main class="colmeia" id="colmeia">
{secao_destaques}
{secao_briefing}
{secao_suporte}
{secao_dev}
{secao_agenda_html}
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
    except Exception as e:  # último recurso: nunca deixar a diretoria sem página
        print(f"ERRO ao montar dashboard da diretoria: {type(e).__name__}: {e}")
        conteudo = (
            "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'><title>Briefing Diretoria</title></head>"
            f"<body><h1>Briefing Diretoria</h1><p>Falha ao gerar o painel: {esc(type(e).__name__)}</p></body></html>")
    SAIDA.write_text(conteudo, encoding="utf-8")
    print(f"OK -> {SAIDA} ({len(conteudo.encode('utf-8')) // 1024} KB, ranking={'on' if MOSTRAR_RANKING else 'off'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
