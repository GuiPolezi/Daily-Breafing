"""Gera dashboard.html: painel estatico e autocontido do briefing diario.

Le dados/*.json, historico/metricas.jsonl e relatorio.md e escreve um unico HTML
sem dependencia de rede. Identidade visual, helpers e componentes de render vem
de dashboard_base.py -- mudou o design la, mudou aqui e nas demais paginas.

Nunca lanca excecao por dado ausente: a fonte vira "indisponivel" e o resto sai.
Os blocos novos do helpdesk (fila por sistema, desenvolvimento) so aparecem
quando o coletor ja gravou os campos correspondentes; sem eles, a secao avisa.

Configuracao (.env): DASHBOARD_MOSTRAR_RANKING, DASHBOARD_DIAS_GRAFICO,
DASHBOARD_SISTEMAS_DESTAQUE, DASHBOARD_SISTEMAS_BRIEFING.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from dashboard_base import (
    AVISO_BASE_MUDOU, CHEVRON_SVG, CSS, CSS_SINO, FONTES_INFO,
    JS_CHARTS, JS_HEADER_SINO, JS_UI,
    LIMITE_SISTEMAS, MOSTRAR_RANKING, RAIZ, badge_delta, barras_distribuicao,
    base_status_comparavel, card_grafico, card_sino, secao_agenda,
    cards_equipes_dev,
    carregar_chart_js, carregar_fontes_css, carregar_gsap, cfg_int, classificar_licenca,
    coletar_nomes_tecnicos, data_do_briefing, dividir_briefing, esc, explica,
    fmt_num, grafico, grupo_fonte, json_inline, label_dia, ler_historico, ler_json, logo_sino,
    markdown_para_html, menu_grade, nota_secao, num_html, ranking_semanal, redigir_nomes,
    render_ranking, secao_vazia, serie_historico, serie_tem_dado, seta_delta, status_fonte,
    tabela_licencas, tabela_sistemas, tabela_tickets, tag_fonte, titulo_secao,
)

RELATORIO = RAIZ / "relatorio.md"
SAIDA = RAIZ / "dashboard.html"

# (chave, rótulo, arquivo)
FONTES = [
    ("helpdesk", "Help desk", "helpdesk.json"),
    ("licencas", "Licenças", "licencas.json"),
    ("agenda", "Agenda", "agenda.json"),
]
DIAS_GRAFICO = cfg_int("DASHBOARD_DIAS_GRAFICO", 30)


def cfg_lista(nome: str, padrao: str) -> list[str]:
    """Lista separada por ';' vinda do .env, com default sensato."""
    bruto = os.getenv(nome) or padrao
    return [p.strip() for p in bruto.split(";") if p.strip()]


# Sistemas que ganham card próprio nos Destaques (rótulos de HELPDESK_SISTEMAS).
SISTEMAS_DESTAQUE = cfg_lista("DASHBOARD_SISTEMAS_DESTAQUE", "Site;Siscam 9;Siscam 8")
# Sistemas que ganham tabela de chamados no Briefing (o Siscam 8 fica de fora).
SISTEMAS_BRIEFING = cfg_lista("DASHBOARD_SISTEMAS_BRIEFING", "Site;Siscam 9")

# Rótulo de sistema -> campo correspondente no histórico, para o badge de comparação.
CAMPO_HISTORICO_SISTEMA = {"Site": "fila_site", "Siscam 9": "fila_siscam9", "Siscam 8": "fila_siscam8"}
# Rótulo de sistema -> chave da série temporal, para os gráficos de evolução.
SERIE_SISTEMA = {"Site": "site", "Siscam 9": "siscam9", "Siscam 8": "siscam8"}


def dic(valor) -> dict:
    """Garante dict: campo ausente ou de tipo inesperado vira {}."""
    return valor if isinstance(valor, dict) else {}


def ler_relatorio() -> str | None:
    if not RELATORIO.exists():
        return None
    try:
        texto = RELATORIO.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return texto if texto.strip() else None


# Secoes do menu -- so do briefing diario; cada pagina define as suas.
# Menu em blocos do tema SINO (o mesmo do diretor). SEMPRE duas linhas: as
# colunas saem da contagem (9 itens -> 5 colunas), porque uma terceira linha
# transborda o .topo, que tem altura fixa.
MENU_ORDEM = [
    ("destaques", "Destaques"), ("briefing", "Briefing"), ("desenvolvimento", "Desenv."),
    ("desenv-analise", "Análise"), ("licencas", "Licenças"), ("evolucao", "Evolução"),
    ("eficacia", "Suporte"), ("agenda", "Agenda"), ("fontes", "Fontes"),
]


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

    helpdesk, licencas = dados["helpdesk"], dados["licencas"]
    secao_agenda_html = secao_agenda(dados["agenda"])
    atend = dic((helpdesk or {}).get("atendimentos_ultimo_dia_util"))

    # --- blocos novos do helpdesk (ausentes até o coletor rodar de novo)
    fila = dic((helpdesk or {}).get("fila"))
    por_sistema = dic(fila.get("por_sistema"))
    idade = dic(fila.get("idade"))
    por_status = dic(fila.get("por_status"))
    resumo_sistema = dic(fila.get("por_sistema_resumo"))
    amostra_de = dic((helpdesk or {}).get("tickets_por_sistema_amostra_de"))
    dev = dic((helpdesk or {}).get("desenvolvimento"))
    por_dev = dic(dev.get("por_dev"))
    tickets_sistema = dic((helpdesk or {}).get("tickets_por_sistema"))

    # --- registro anterior do histórico (para os badges de comparação)
    hoje_iso = date.today().isoformat()
    anterior: dict = {}
    if historico:
        anterior = historico[-2] if historico[-1].get("data") == hoje_iso and len(historico) >= 2 else (
            historico[-1] if historico[-1].get("data") != hoje_iso else {})

    # --- a fila de hoje e a de ontem saem da mesma régua de status?
    # Quando não saem, comparar os dois dias é mentira: o número mudou porque a
    # configuração mudou. As métricas derivadas da fila perdem o badge.
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

    # --- licenças
    lic_itens = tabela_licencas(licencas)
    n_vencendo = len((licencas or {}).get("vencendo_em_breve") or [])
    n_vencidas = len((licencas or {}).get("vencidas_recentes") or [])
    vencidas_antigas = (licencas or {}).get("vencidas_antigas_total")

    fontes_desatualizadas = [f["rotulo"] for f in fontes.values() if f["estado"] == "desatualizada"]
    fontes_indisponiveis = [f["rotulo"] for f in fontes.values() if f["estado"] == "indisponivel"]

    nomes_tecnicos = coletar_nomes_tecnicos(historico, helpdesk)

    def talvez_redigir(texto: str) -> str:
        """Esconde nome de técnico quando o ranking está desligado."""
        return texto if MOSTRAR_RANKING else redigir_nomes(texto, nomes_tecnicos)

    # ================================================================ 1. DESTAQUES
    # Mesma anatomia do Panorama do diretor (card_sino): título verde centrado,
    # número com seta, julgamento, rodapé e explicação. A fonte não se repete em
    # cada card -- quem a declara é a faixa do grupo (grupo_fonte).
    def par(atual, chave: str | None, melhor: str, do_historico: bool = False):
        """(seta, badge) de um card. Um único valor anterior alimenta os dois."""
        ant = anterior.get(chave) if (do_historico and chave) else valor_anterior(chave)
        return (seta_delta(atual, ant, melhor=melhor),
                badge_delta(atual, ant, melhor=melhor))

    sem_help = "Sem dados do help desk nesta geração."
    equipe_abertos = (helpdesk or {}).get("meus_abertos")
    novos = (helpdesk or {}).get("meus_novos_hoje")
    agentes = dic((helpdesk or {}).get("por_agente"))
    n_agentes = len(agentes)
    if not helpdesk:
        rodape_equipe = sem_help
    elif agentes and MOSTRAR_RANKING:
        quebra = " · ".join(f"{esc(n)} <b>{fmt_num(dic(v).get('abertos'))}</b>" for n, v in agentes.items())
        rodape_equipe = (f'<b>{num_html(novos)}</b> {"novo hoje" if novos == 1 else "novos hoje"}'
                         f'<br>{quebra}')
    else:
        rodape_equipe = (f'<b>{num_html(novos)}</b> {"novo hoje" if novos == 1 else "novos hoje"}'
                         + (f' · <b>{n_agentes}</b> técnicos monitorados' if n_agentes else ""))
    s_eq, b_eq = par(equipe_abertos, "meus_abertos", "menor")
    v_fila = (helpdesk or {}).get("fila_total_abertos")
    s_fila, b_fila = par(v_fila, "fila_abertos", "menor")
    v_atend = atend.get("total_atendimentos_fechados")
    s_atend, b_atend = par(v_atend, "atend_total", "maior", do_historico=True)

    cartoes = [
        card_sino("Equipe Monitorada", equipe_abertos, sub="chamados em aberto",
                  seta=s_eq, delta=b_eq, rodape=rodape_equipe,
                  explicacao=explica("equipe_abertos", f"Hoje são {n_agentes} técnicos monitorados." if n_agentes else ""),
                  indice=0),
        card_sino("Fila de Chamados", v_fila, sub="total em aberto", seta=s_fila, delta=b_fila,
                  rodape="" if helpdesk else sem_help,
                  explicacao=explica("fila_total"), indice=1),
        card_sino("Atendimentos Fechados", v_atend, sub="Equipe Suporte", seta=s_atend, delta=b_atend,
                  rodape=f'último dia útil: <b>{esc(atend.get("dia") or "—")}</b>',
                  explicacao=explica("atend_fechados"), indice=2),
    ]

    # --- cards por sistema (Site / Siscam 9 / Siscam 8), recorte da mesma fila
    for i, rotulo in enumerate(SISTEMAS_DESTAQUE):
        valor = por_sistema.get(rotulo)
        seta_s, badge_s = par(valor, CAMPO_HISTORICO_SISTEMA.get(rotulo), "menor")
        # Os números vêm do resumo, calculado sobre a fila inteira. A lista de
        # chamados é uma amostra dos mais urgentes: contar por ela daria menos.
        resumo = dic(resumo_sistema.get(rotulo))
        if valor is None:
            rodape = "recorte por sistema ainda não coletado"
        elif resumo:
            antigo = resumo.get("mais_antigo_dias")
            rodape = (f'<b>{fmt_num(resumo.get("acima_de_90_dias"))}</b> há mais de 90 dias · '
                      f'<b>{fmt_num(antigo) if antigo is not None else "—"}</b> dias o mais antigo')
        else:
            rodape = ""
        cartoes.append(card_sino(
            f"Fila · {rotulo}", valor,
            seta=seta_s if valor is not None else "",
            delta=badge_s if valor is not None else "",
            rodape=rodape, explicacao=explica("fila_sistema"), indice=3 + i))

    sem_recorte = not por_sistema
    aviso_recorte = ""
    if sem_recorte and helpdesk:
        aviso_recorte = ('<p class="secao-nota bloco-fixo">A divisão da fila por sistema aparece depois da próxima coleta '
                         '(o campo <b>fila.por_sistema</b> ainda não existe em dados/helpdesk.json).</p>')

    secao_destaques = f"""
<section class="secao rolavel" id="destaques" data-scroll aria-labelledby="t-destaques">
  <header class="secao-cabeca empilhada bloco-fixo">
    {titulo_secao("Destaques do Dia", "destaques")}
    <p class="subtitulo">{esc(date.today().strftime("%d/%m/%Y"))}</p>
  </header>
  {aviso_recorte}{aviso_base}
  {grupo_fonte("milldesk", "Fila de chamados em aberto e atendimentos do Mildesk. Cada card diz o que é "
                           "e como o número é contado.", rotulo="Mildesk")}
  <div class="grade painel-exec bloco-elastico">{''.join(cartoes)}</div>
</section>"""

    # ================================================================ 2. BRIEFING
    if relatorio is None:
        secao_briefing = secao_vazia("briefing", "Briefing do Dia", "Briefing indisponível (relatorio.md ausente ou vazio).")
    else:
        texto = talvez_redigir(relatorio)
        titulo_h1, slides = dividir_briefing(texto)
        data_brief = data_do_briefing(titulo_h1) or agora.strftime("%d/%m/%Y %H:%M")
        if not slides:
            slides = [{"titulo": "Briefing", "md": texto}]

        # Slide extra: os chamados em aberto dos sistemas que o briefing cita nominalmente.
        blocos_sistema = []
        for rotulo in SISTEMAS_BRIEFING:
            lista = tickets_sistema.get(rotulo) if isinstance(tickets_sistema.get(rotulo), list) else []
            total = por_sistema.get(rotulo)
            mostrados = min(len(lista), 8)
            de_quantos = amostra_de.get(rotulo, total)
            nota = (f' <span class="kpi-legenda">mostrando {mostrados} de {fmt_num(de_quantos)}, '
                    f'os abertos há mais tempo</span>' if lista else "")
            cabeca = (f'<h4 class="kpi-rotulo">{esc(rotulo)} · <b>{fmt_num(total) if total is not None else "—"}</b>'
                      f' em aberto{nota}</h4>')
            blocos_sistema.append(f'<div class="bloco-sistema">{cabeca}'
                                  f'{tabela_tickets(lista, limite=8, com_tecnico=MOSTRAR_RANKING)}</div>')
        if blocos_sistema and (por_sistema or any(tickets_sistema.values())):
            slides.append({
                "titulo": "Em aberto · " + " e ".join(SISTEMAS_BRIEFING),
                "html": (f'{nota_secao("milldesk", "Chamados em aberto destes sistemas, os abertos há mais tempo primeiro.")}'
                         f'{"".join(blocos_sistema)}'),
            })

        itens_slides, pontos = [], []
        for i, s in enumerate(slides):
            if s.get("html"):
                corpo, duas = s["html"], ""
            else:
                corpo_md = s["md"]
                n_itens = len(re.findall(r"^\s*[-*+]\s+", corpo_md, flags=re.M))
                duas = " duas-colunas" if (n_itens >= 5 or len(corpo_md) > 650) and not re.search(r"^\s*\d+[.)]\s+", corpo_md, flags=re.M) else ""
                corpo = markdown_para_html(corpo_md, base=4) or "<p>—</p>"
            itens_slides.append(
                f'<li class="slide" role="group" aria-roledescription="slide" aria-label="{i + 1} de {len(slides)}: {esc(s["titulo"])}">'
                f'<article class="card card-slide" data-scroll><h3 class="kpi-rotulo">{esc(s["titulo"])}</h3>'
                f'<div class="slide-corpo{duas}">{corpo}</div></article></li>'
            )
            pontos.append(f'<button type="button" role="tab" aria-selected="{"true" if i == 0 else "false"}" aria-label="{esc(s["titulo"])}"></button>')
        secao_briefing = f"""
<section class="secao" id="briefing" aria-labelledby="t-briefing">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Briefing do Dia", "briefing")}<p class="lado carimbo-briefing">{esc(data_brief)}</p></header>
  <div class="slider bloco-elastico" aria-roledescription="carrossel" aria-label="Tópicos do briefing">
    <div class="slides-janela"><ul class="slides">{''.join(itens_slides)}</ul></div>
    <div class="slider-controles">
      <button class="seta" type="button" data-dir="-1" aria-label="Tópico anterior">‹</button>
      <div class="indicadores" role="tablist" aria-label="Tópicos">{''.join(pontos)}</div>
      <button class="seta" type="button" data-dir="1" aria-label="Próximo tópico">›</button>
    </div>
  </div>
</section>"""

    # ================================================================ 3. DESENVOLVIMENTO
    if not dev:
        secao_dev = secao_vazia(
            "desenvolvimento", "Desenvolvimento",
            "Bloco de desenvolvimento ainda não coletado (campo desenvolvimento em dados/helpdesk.json).")
    elif not dev.get("configurado") and not dev.get("total_em_status_dev"):
        secao_dev = secao_vazia(
            "desenvolvimento", "Desenvolvimento",
            "Nenhum desenvolvedor configurado. Preencha HELPDESK_DEV_NAMES no .env "
            "(rode `python coletores/check_helpdesk.py --listar-categorias` para ver os nomes reais).")
    else:
        cards_dev = []
        for i, (nome, bloco) in enumerate(sorted(por_dev.items(), key=lambda kv: -(dic(kv[1]).get("abertos") or 0))):
            b = dic(bloco)
            nome_exibido = esc(nome) if MOSTRAR_RANKING else f"Desenvolvedor {i + 1}"
            chips = "".join(
                f'<span class="chip">{esc(k)} <b>{fmt_num(v)}</b></span>'
                for k, v in list(dic(b.get("por_sistema")).items())[:LIMITE_SISTEMAS])
            antigo = b.get("mais_antigo_dias")
            # total_no_nome é espelho de abertos; o fallback mantém a página de pé
            # com um helpdesk.json gravado antes desses campos existirem.
            total_nome = b.get("total_no_nome", b.get("abertos"))
            em_trabalho = b.get("em_trabalho")
            outros = (total_nome - em_trabalho
                      if isinstance(total_nome, int) and isinstance(em_trabalho, int) else None)
            linha_trabalho = ""
            if em_trabalho is not None:
                linha_trabalho = (
                    f'<p class="kpi-mini destaque-trabalho">'
                    f'<span><b>{fmt_num(em_trabalho)}</b> em trabalho ativo</span>'
                    f'<span><b>{fmt_num(outros)}</b> em outros status</span></p>')
            cards_dev.append(f"""
<article class="card kpi card-dev" style="--i:{i}" data-scroll>
  <div class="kpi-cabeca"><p class="dev-nome">{nome_exibido}</p></div>
  <p class="kpi-numero menor">{num_html(total_nome)}</p>
  <p class="kpi-legenda">no nome do dev<br><b>{fmt_num(b.get("novos_hoje"))}</b> novos hoje</p>
  {linha_trabalho}
  <p class="kpi-mini"><span><b>{fmt_num(b.get("acima_de_90_dias"))}</b> há mais de 90 dias</span>
     <span><b>{fmt_num(antigo) if antigo is not None else "—"}</b> dias o mais antigo</span></p>
  <div class="dev-sistemas">{chips or '<span class="dist-vazio">sem quebra por sistema</span>'}</div>
</article>""")

        painel_sistema = barras_distribuicao(dic(dev.get("em_status_dev_por_sistema")), limite=LIMITE_SISTEMAS)
        tickets_dev = dev.get("tickets_em_status_dev") if isinstance(dev.get("tickets_em_status_dev"), list) else []
        dev_amostra_de = dev.get("tickets_em_status_dev_amostra_de") or dev.get("total_em_status_dev")
        # Sistemas sem card nos Destaques: sem esta tabela, a página não mostrava
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
  {nota_secao("milldesk", "Dois recortes diferentes da mesma fila: chamados que têm um desenvolvedor como responsável, "
                          "e chamados parados em status de desenvolvimento (tenham dono ou não).")}
  <h3 class="kpi-rotulo bloco-fixo">Por equipe</h3>
  {cards_equipes_dev(dic(dev.get("por_equipe")), mostrar_nomes=MOSTRAR_RANKING)}
  {explica("dev_equipe")}
  {bloco_sistemas}
  <h3 class="kpi-rotulo bloco-fixo">Por desenvolvedor</h3>
  <div class="grade grade-dev bloco-elastico">{''.join(cards_dev) or '<p class="vazio bloco-elastico">Nenhum chamado atribuído aos desenvolvedores configurados.</p>'}</div>
  {explica("dev_em_trabalho")}
</section>

<section class="secao rolavel" id="desenv-analise" data-scroll aria-labelledby="t-desenv-analise">
  <header class="secao-cabeca dividida bloco-fixo">
    {titulo_secao("Análise do desenvolvimento", "desenv-analise")}
    <div class="lado">
      <div class="kpi-medida"><p class="kpi-numero menor">{num_html(dev.get("total_em_status_dev"))}</p><p class="kpi-legenda">em status de desenvolvimento</p></div>
    </div>
  </header>
  <div class="grade larga graficos quatro bloco-elastico">
    <article class="card" data-scroll>
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Em status de desenvolvimento · por sistema</h3></div>
      {painel_sistema}
      {explica("fila_sistema")}
    </article>
    <article class="card" data-scroll>
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Em desenvolvimento · corretivo x evolutivo</h3></div>
      {barras_distribuicao(dic(dev.get("em_status_dev_por_natureza")), limite=6, criticos=("Corretivo",))}
      {explica("natureza")}
    </article>
  </div>
  <div class="serie bloco-fixo">
    <div class="acoes"><button class="pilula" type="button" aria-expanded="false" aria-controls="dev-tickets"><span class="pilula-texto">Ver chamados em status de desenvolvimento ({fmt_num(min(len(tickets_dev), 20))} de {fmt_num(dev_amostra_de)})</span>{CHEVRON_SVG}</button></div>
    <div class="expansivel" id="dev-tickets"><div><div class="expansivel-pad">
      {tabela_tickets(tickets_dev, limite=20, com_tecnico=MOSTRAR_RANKING)}
    </div></div></div>
  </div>
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
<section class="secao rolavel" id="licencas" aria-labelledby="t-licencas">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Licenças", "licencas")}{sintese}</header>
  {nota_secao("licencas", "Painel web interno de licenças. Homologação e teste ficam de fora; "
                          "'vencidas recentes' são as dos últimos 60 dias, ainda acionáveis.")}
  <div class="tabela-clara bloco-elastico" data-scroll tabindex="0" role="region" aria-label="Tabela de licenças em risco">
    <table class="tabela-lic"><caption class="sr-only">Licenças vencidas recentemente e vencendo em breve, por urgência</caption>
      <thead><tr><th scope="col">Status</th><th scope="col">Cliente</th><th scope="col">Sistema</th><th scope="col" class="num">Vencimento</th><th scope="col" class="num">Prazo</th></tr></thead>
      <tbody>{''.join(linhas)}</tbody></table>
  </div>
</section>"""

    # ================================================================ 5. EVOLUÇÃO
    serie = serie_historico(historico, DIAS_GRAFICO)
    rank = ranking_semanal(historico)
    n_dias = len(serie["labels"])
    graficos_payload: list[dict] = []
    if not n_dias:
        secao_evolucao = secao_vazia("evolucao", "Evolução", "Histórico indisponível (historico/metricas.jsonl vazio ou ausente).")
    else:
        def ultimos(vals: list) -> tuple:
            v = [x for x in vals if x is not None]
            return (v[-1] if v else None, v[-2] if len(v) > 1 else None)

        data_ult = label_dia(serie["datas"][-1]) if serie["datas"] else "—"

        def topo_grafico(chave: str, melhor: str, legenda: str) -> str:
            ult, ant = ultimos(serie[chave])
            return (f'<div class="kpi-linha"><p class="kpi-numero menor">{num_html(ult)}</p>'
                    f'<div class="kpi-juizo">{badge_delta(ult, ant, "vs. registro anterior", melhor=melhor)}</div>'
                    f'<p class="kpi-legenda">{esc(legenda)}</p></div>')

        # (chave da série, id do canvas, título, cor, melhor, legenda)
        candidatos = [
            ("fila", "chartFila", "Fila de chamados abertos", "azul", "menor", f"último registro · {data_ult}"),
            ("atend", "chartAtend", "Atendimentos fechados por dia", "rubro", "maior", "último dia útil registrado"),
            ("equipe", "chartEquipe", "Abertos da equipe monitorada", "oliva", "menor", f"último registro · {data_ult}"),
            ("corretivo", "chartCorretivo", "Fila corretiva (bugs e falhas)", "rubro", "menor", f"último registro · {data_ult}"),
            ("evolutivo", "chartEvolutivo", "Fila evolutiva (melhorias)", "verde", "maior", f"último registro · {data_ult}"),
        ]
        for rotulo_sis in SISTEMAS_DESTAQUE:
            chave = SERIE_SISTEMA.get(rotulo_sis)
            if chave:
                candidatos.insert(2, (chave, f"chart{chave.title()}", f"Fila · {rotulo_sis}", "mel", "menor",
                                      f"último registro · {data_ult}"))

        cartoes = []
        for chave, id_canvas, titulo, cor, melhor, legenda in candidatos:
            if not serie_tem_dado(serie, chave):
                continue
            graficos_payload.append(grafico(id_canvas, titulo, serie[chave], tipo="area", cor=cor))
            cartoes.append(card_grafico(id_canvas, titulo, f"Evolução: {titulo}",
                                        topo_grafico(chave, melhor, legenda)))

        # Distribuição atual da fila por sistema (foto de hoje, não série)
        if por_sistema:
            rotulos = list(por_sistema.keys())[:LIMITE_SISTEMAS]
            valores = [por_sistema[r] for r in rotulos]
            graficos_payload.append(grafico("chartSistemas", "Fila por sistema", valores,
                                            tipo="barra-h", cor="oliva", labels=rotulos))
            cartoes.append(card_grafico("chartSistemas", "Fila por sistema · hoje",
                                        "Distribuição atual da fila por sistema"))

        if chart_js is None:
            corpo_evolucao = ('<p class="vazio bloco-elastico">Gráficos indisponíveis nesta geração (biblioteca de gráficos não encontrada). '
                              'Os dados seguem na tabela abaixo.</p>')
            graficos_payload = []
        elif not cartoes:
            corpo_evolucao = '<p class="vazio bloco-elastico">Ainda não há série suficiente para desenhar gráficos.</p>'
        else:
            corpo_evolucao = f'<div class="grade larga graficos quatro bloco-elastico">{"".join(cartoes)}</div>'

        linhas_serie = "".join(
            f'<tr><td>{esc(label_dia(d))}</td><td class="c">{fmt_num(f)}</td><td class="d">{fmt_num(a)}</td>'
            f'<td class="num">{fmt_num(s)}</td><td class="num">{fmt_num(v)}</td></tr>'
            for d, f, a, s, v in list(zip(serie["datas"], serie["fila"], serie["atend"], serie["site"], serie["siscam9"]))[::-1]
        )
        secao_evolucao = f"""
<section class="secao rolavel" id="evolucao" data-scroll aria-labelledby="t-evolucao">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Evolução", "evolucao")}<p class="lado subtitulo">últimos {DIAS_GRAFICO} dias · {n_dias} registrado(s)</p></header>
  {nota_secao("historico", "Série montada de historico/metricas.jsonl, uma linha por dia. "
                           "Dias sem coleta não aparecem; métricas novas só existem a partir do dia em que passaram a ser gravadas.")}
  {corpo_evolucao}
  <div class="serie bloco-fixo">
  <div class="acoes"><button class="pilula" type="button" aria-expanded="false" aria-controls="serie-dados"><span class="pilula-texto">Ver dados da série</span>{CHEVRON_SVG}</button></div>
  <div class="expansivel" id="serie-dados"><div><div class="expansivel-pad">
    <table class="tabela-serie"><caption class="sr-only">Dados da série por dia</caption>
      <thead><tr><th scope="col">Data</th><th scope="col" class="c">Fila total</th><th scope="col" class="d">Atend. fechados</th><th scope="col" class="num">Site</th><th scope="col" class="num">Siscam 9</th></tr></thead>
      <tbody>{linhas_serie}</tbody></table>
  </div></div></div>
  </div>
</section>"""

    # ================================================================ 6. SUPORTE (eficácia)
    if MOSTRAR_RANKING and rank["tecnicos"]:
        titulo_rank, nomes_rank, valores_rank = "Ranking Semanal por Técnico", rank["tecnicos"], rank["valores"]
    elif rank["dias"]:
        titulo_rank, nomes_rank, valores_rank = "Atendimentos da equipe por dia", rank["dias"], rank["totais"]
    else:
        titulo_rank, nomes_rank, valores_rank = "", [], []
    if not nomes_rank:
        secao_eficacia = secao_vazia("eficacia", "Suporte", "Sem registros de atendimentos no histórico.")
    else:
        dias_txt = ", ".join(rank["dias"])
        nota_rank = "" if MOSTRAR_RANKING else " · ranking por técnico desativado"
        painel_idade = barras_distribuicao({
            "Até 7 dias": idade.get("ate_7_dias"), "8 a 30 dias": idade.get("de_8_a_30_dias"),
            "31 a 90 dias": idade.get("de_31_a_90_dias"), "Mais de 90 dias": idade.get("mais_de_90_dias"),
        }, limite=4, criticos=("Mais de 90 dias",)) if idade else ""
        painel_status = barras_distribuicao(por_status, limite=6) if por_status else ""
        blocos_fila = ""
        if painel_idade or painel_status:
            blocos_fila = f"""
  <div class="grade larga graficos quatro bloco-elastico">
    <article class="card" data-scroll>
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Idade dos chamados na fila</h3>{tag_fonte("milldesk")}</div>
      {painel_idade or '<p class="dist-vazio">Sem dados de idade.</p>'}
      {explica("idade_fila")}
    </article>
    <article class="card" data-scroll>
      <div class="kpi-cabeca"><h3 class="kpi-rotulo">Fila por status</h3>{tag_fonte("milldesk")}</div>
      {painel_status or '<p class="dist-vazio">Sem dados de status.</p>'}
      {explica("fila_total")}
    </article>
  </div>"""
        secao_eficacia = f"""
<section class="secao rolavel" id="eficacia" data-scroll aria-labelledby="t-eficacia">
  <header class="secao-cabeca dividida bloco-fixo">
    {titulo_secao("Suporte", "eficacia")}
    <div class="lado"><div class="kpi-medida" title="{esc(dias_txt)}"><p class="kpi-numero menor">{num_html(rank["total_periodo"])}</p><p class="kpi-legenda">atendimentos fechados em {len(rank["dias"])} dia(s) útil(eis)</p></div></div>
  </header>
  {nota_secao("milldesk", "Produtividade do suporte: atendimentos fechados por técnico, mais o estado da fila em aberto.")}
  <article class="card card-ranking bloco-elastico" style="--i:0" data-scroll>
    <div class="kpi-cabeca"><h3 class="kpi-rotulo">{esc(titulo_rank)}</h3><p class="kpi-legenda">soma dos últimos {len(rank["dias"])} dia(s) útil(eis) registrado(s){esc(nota_rank)}</p></div>
    {render_ranking(nomes_rank, valores_rank)}
    {explica("ranking")}
  </article>
  {blocos_fila}
</section>"""

    # ================================================================ 7. FONTES
    rotulo_estado = {"ok": "Atualizada", "desatualizada": "Desatualizada", "indisponivel": "Indisponível"}
    classe_estado = {"ok": "ok", "desatualizada": "aviso", "indisponivel": "grave"}
    origem_da_fonte = {"helpdesk": "milldesk", "licencas": "licencas"}
    cards_fontes = []
    for i, (chave, f) in enumerate(fontes.items()):
        estado = f["estado"]
        d = dados[chave] or {}
        stats = ""
        if chave == "helpdesk" and d:
            agentes_lista = d.get("agentes_monitorados") if isinstance(d.get("agentes_monitorados"), list) else []
            stats = (f'<ul class="mini-stats"><li><b>{fmt_num(d.get("fila_total_abertos"))}</b><span>na fila</span></li>'
                     f'<li><b>{fmt_num(d.get("meus_abertos"))}</b><span>abertos da equipe</span></li>'
                     f'<li><b>{fmt_num(len(agentes_lista)) if agentes_lista else "—"}</b><span>técnicos monitorados</span></li></ul>')
        elif chave == "licencas" and d:
            stats = (f'<ul class="mini-stats"><li><b>{fmt_num(n_vencendo)}</b><span>vencendo</span></li>'
                     f'<li><b>{fmt_num(n_vencidas)}</b><span>vencidas recentes</span></li>'
                     f'<li><b>{fmt_num(d.get("ignoradas_homolog_teste"))}</b><span>homolog./teste ignoradas</span></li></ul>')
        detalhe = {"ok": "dentro do limite de 24 h", "desatualizada": f"coleta {esc(f['detalhe'])} · limite de 24 h",
                   "indisponivel": esc(f["detalhe"])}[estado]
        dt = f["coletado_em"]
        hora = esc(dt.strftime("%H:%M")) if dt else '<span class="sem-dado">—</span>'
        data_coleta = esc(dt.strftime("%d/%m/%Y")) if dt else "—"
        origem = origem_da_fonte.get(chave, "")
        descricao_fonte = FONTES_INFO.get(origem, ("", "", ""))[2]
        cards_fontes.append(f"""
<article class="card card-fonte kpi" style="--i:{i}" aria-labelledby="f-{chave}">
  <div class="kpi-cabeca"><h3 class="kpi-rotulo" id="f-{chave}">{esc(f["rotulo"])}</h3><span class="kpi-fonte {classe_estado[estado]}">{rotulo_estado[estado]}</span></div>
  <p class="kpi-numero menor">{hora}</p>
  <p class="kpi-legenda">coletado em {data_coleta}</p>
  <p class="fonte-detalhe">{detalhe}</p>
  {stats}
  <p class="kpi-explica">{tag_fonte(origem) if origem else ""} {esc(descricao_fonte)}</p>
</article>""")
    secao_fontes = f"""
<section class="secao rolavel" id="fontes" data-scroll aria-labelledby="t-fontes">
  <header class="secao-cabeca dividida bloco-fixo">{titulo_secao("Fontes", "fontes")}<p class="lado subtitulo">{sum(1 for f in fontes.values() if f["estado"] == "ok")} de {len(fontes)} fontes atualizadas · coleta de hoje</p></header>
  <div class="grade grade-fontes bloco-elastico">{''.join(cards_fontes)}</div>
  <p class="nota-escura bloco-fixo">Arquivo estático gerado por gerar_dashboard.py · sem dependências externas · pode ser copiado sozinho.</p>
</section>"""

    # ================================================================ cabeçalho
    gerado_em = agora.strftime("%d/%m/%Y %H:%M")
    data_dados = date.today().strftime("%d/%m/%Y")
    avisos = ""
    if fontes_desatualizadas:
        avisos += f'<a class="aviso" href="#fontes" data-alvo="fontes">{len(fontes_desatualizadas)} fonte(s) desatualizada(s)</a>'
    if fontes_indisponiveis:
        avisos += f'<a class="aviso grave" href="#fontes" data-alvo="fontes">{len(fontes_indisponiveis)} fonte(s) indisponível(is)</a>'

    # Marca e menu do tema SINO, os mesmos do diretor. Só o subtítulo do hover
    # muda ("Operação"), para as duas páginas não se confundirem.
    marca_html = logo_sino(alvo="destaques", titulo="SINO Operação", sub="Operação")
    menu_html = menu_grade(MENU_ORDEM, colunas=-(-len(MENU_ORDEM) // 2))

    payload = {"labels": serie["labels"], "secao": "evolucao", "graficos": graficos_payload}
    script = f"<script>{JS_UI}</script>"
    if gsap_js is not None:
        script += f"\n<script>{gsap_js}</script>\n<script>{JS_HEADER_SINO}</script>"
    # marca html.anim antes da primeira pintura (só quando há GSAP e sem movimento reduzido)
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
<title>SINO Operação — Briefing Diário — {esc(data_dados)}</title>
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
{secao_dev}
{secao_licencas}
{secao_agenda_html}
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
