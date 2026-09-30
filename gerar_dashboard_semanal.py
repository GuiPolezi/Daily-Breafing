"""Gera dashboard_semanal.html: painel estático e autocontido do briefing semanal.

Lê historico/metricas.jsonl e relatorio_semanal.md (não lê dados/ nem chama
nenhuma API) e escreve um único HTML no tema "Relatório Semanal" (mockup de
30/09/2026: fundo verde, folha clara com o menu no canto, marca d'água). O CSS
do tema é CSS_SEMANAL, em dashboard_base.py, junto com JS, conversor de markdown
e helpers. Seções: Destaques da Semana, Sobre a Semana (id "briefing"), Evolução
e Eficácia. Nunca lança exceção por dado ausente: cada seção degrada e o
restante é gerado.

"Cliente que mais criou chamado" soma criados_por_cliente (campo location do
Milldesk, gravado por arquivar.py a partir de 30/09/2026); linha sem o campo fica
fora da conta e o bloco diz em quantos dias da semana a contagem entrou.

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
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

from dashboard_base import (
    CSS, CSS_SEMANAL, JS_CHARTS, JS_HEADER, JS_SEMANAL, JS_SEMANAL_SOBRE, JS_UI, MOSTRAR_RANKING,
    badge_delta, carregar_chart_js, carregar_fontes_css, carregar_fontes_inter_css, grafico, carregar_gsap, coletar_nomes_tecnicos,
    dividir_briefing, esc, fmt_num, inline_md, json_inline, label_dia, ler_historico, markdown_para_html,
    num_html, redigir_nomes, render_ranking, secao_vazia, titulo_secao,
)

RAIZ = Path(__file__).resolve().parent
RELATORIO_SEMANAL = RAIZ / "relatorio_semanal.md"
SAIDA = RAIZ / "dashboard_semanal.html"

JANELA_DIAS = 12
DIAS_SEMANA = 5
MIN_DIAS_ANTERIOR = 3

# Menu do tema "Relatório Semanal" (mockup de 30/09/2026). Os ids "briefing" e "evolucao" são mantidos
# porque o JS reaproveitado depende deles (setas do slider e montagem dos gráficos). As seções
# "Semanas" e "Dia a dia" saíram nesta mudança, por decisão do Guilherme.
ORDEM_SEMANAL = [
    ("destaques", "Destaques da Semana"), ("briefing", "Sobre a Semana"),
    ("evolucao", "Evolução"), ("eficacia", "Eficácia"),
]


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
    except (TypeError, ValueError, OverflowError):
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
        "criados": criados_da_semana(regs),
        "carga": carga_da_semana(regs),
        "lic_vencidas": extremos("lic_vencidas_recentes"),
        "lic_vencendo": extremos("lic_vencendo"),
        "clientes": clientes_da_semana(regs),
    }


def chave_cliente(nome) -> str:
    """Mesma regra do coletor (chave_local): sem acento, sem caixa, espaços colapsados."""
    texto = unicodedata.normalize("NFD", str(nome))
    return " ".join("".join(c for c in texto if unicodedata.category(c) != "Mn").lower().split())


def linhas_por_referencia(regs: list[dict], tem_dado) -> tuple[dict[str, dict], set[str]]:
    """Uma linha por dia de referência (atend_dia_ref; sem ele, a data): vale a MAIS RECENTE
    QUE TEM o dado. Se o briefing rodou no sábado (ok) e de novo na segunda com a coleta
    falhando (None), o sábado não pode ser descartado. Devolve ({ref: linha}, todas as refs)."""
    por_ref: dict[str, dict] = {}
    refs: set[str] = set()
    for r in regs:
        ref = str(r.get("atend_dia_ref") or "").strip() or f"data:{r['data']}"
        refs.add(ref)
        if tem_dado(r):
            por_ref[ref] = r
    return por_ref, refs


def maior(contagem: dict[str, int]) -> tuple[str, int] | None:
    """Chave com o maior valor; empate decide pela ordem alfabética (estável entre gerações)."""
    return min(contagem.items(), key=lambda kv: (-kv[1], kv[0])) if contagem else None


def criados_da_semana(regs: list[dict]) -> dict:
    """"Fila de Chamados": chamados abertos por CLIENTES na semana (sem internos e sem
    atendimento diário -- o coletor já tira os dois). Soma criados_total por dia de
    referência; a segunda-feira traz sexta..domingo (criados_periodo)."""
    por_ref, refs = linhas_por_referencia(regs, lambda r: inteiro(r.get("criados_total")) is not None)
    total = sum(inteiro(r["criados_total"]) for r in por_ref.values())
    por_sistema: dict[str, int] = {}
    for r in por_ref.values():
        ps = r.get("criados_por_sistema")
        for sis, q in (ps if isinstance(ps, dict) else {}).items():
            v = inteiro(q)
            if v is not None:
                por_sistema[str(sis)] = por_sistema.get(str(sis), 0) + v
    dias = len(por_ref)
    topo = maior(por_sistema)
    return {"total": total if dias else None, "dias": dias, "possiveis": len(refs),
            "media": total / dias if dias else None,
            "sistema": topo[0] if topo else None, "sistema_qtd": topo[1] if topo else None}


def carga_da_semana(regs: list[dict]) -> dict:
    """"Carga por Dev": chamados que ENTRARAM na carga de cada dev na semana. Cada linha do
    histórico traz dev_atribuidos_novos = diferença entre o retrato de ids do dia e o
    anterior (arquivar.py), então somar as linhas não conta nada duas vezes. Dev sem nenhum
    dia com número fica None (sem base), não zero. Todo dev configurado aparece, mesmo com 0."""
    soma: dict[str, int] = {}
    nomes: set[str] = set()
    dias = 0
    for r in regs:
        novos = r.get("dev_atribuidos_novos")
        if isinstance(r.get("dev_por_pessoa"), dict):
            nomes.update(str(n) for n in r["dev_por_pessoa"])
        if not isinstance(novos, dict):
            continue
        nomes.update(str(n) for n in novos)
        contou = False
        for nome, v in novos.items():
            q = inteiro(v)
            if q is not None:
                soma[str(nome)] = soma.get(str(nome), 0) + q
                contou = True
        dias += contou
    pares = [(n, soma.get(n)) for n in nomes]
    pares.sort(key=lambda kv: (-(kv[1] if kv[1] is not None else -1), kv[0]))
    return {"devs": pares, "dias": dias, "possiveis": len(regs)}


def clientes_da_semana(regs: list[dict]) -> dict:
    """Soma criados_por_cliente dos dias da semana (mesma regra dos atendimentos: dia de referência
    repetido conta uma vez). Devolve o cliente com mais chamados e o sistema mais afetado nele.
    'dias' = quantos dias entraram (linhas anteriores a 30/09/2026 não têm o campo)."""
    # O mesmo cliente pode vir grafado diferente em dias diferentes ("Câmara X" / "CAMARA X"):
    # soma pela forma normalizada e exibe a grafia que mais apareceu na semana.
    total: dict[str, int] = {}
    sistemas: dict[str, dict[str, int]] = {}
    grafias: dict[str, dict[str, int]] = {}
    # Dia de referência repetido conta uma vez (ver linhas_por_referencia).
    por_ref, refs = linhas_por_referencia(regs, lambda r: isinstance(r.get("criados_por_cliente"), dict))
    dias = len(por_ref)
    for linha in por_ref.values():
        for cliente, bloco in linha["criados_por_cliente"].items():
            if not isinstance(bloco, dict):
                continue
            q = inteiro(bloco.get("total"))
            if q is None:
                continue
            chave = chave_cliente(cliente)
            grafias.setdefault(chave, {})
            grafias[chave][str(cliente)] = grafias[chave].get(str(cliente), 0) + q
            total[chave] = total.get(chave, 0) + q
            por_sis = sistemas.setdefault(chave, {})
            ps = bloco.get("por_sistema")
            for sis, qs in (ps if isinstance(ps, dict) else {}).items():
                v = inteiro(qs)
                if v is not None:
                    por_sis[str(sis)] = por_sis.get(str(sis), 0) + v
    possiveis = len(refs)
    if not total:
        return {"dias": dias, "possiveis": possiveis, "cliente": None}
    # maior total; empate decide pela ordem alfabética (estável entre gerações) e é avisado
    chave, qtd = min(total.items(), key=lambda kv: (-kv[1], kv[0]))
    empatados = sum(1 for v in total.values() if v == qtd) - 1
    cliente = min(grafias[chave].items(), key=lambda kv: (-kv[1], kv[0]))[0]
    por_sis = sistemas.get(chave) or {}
    sistema = min(por_sis.items(), key=lambda kv: (-kv[1], kv[0])) if por_sis else None
    return {"dias": dias, "possiveis": possiveis, "cliente": cliente, "total": qtd, "empatados": empatados,
            "sistema": sistema[0] if sistema else None, "sistema_qtd": sistema[1] if sistema else None}


# ----------------------------------------------------------------------------
# Renderização
# ----------------------------------------------------------------------------
SEM_DADO = '<span class="sem-dado">—</span>'


def nome_curto(nome: str) -> str:
    """'Rafael Fernando Sandalo' -> 'Rafael S' (cabe na coluna). Nome de uma palavra fica como está."""
    partes = str(nome).split()
    return partes[0] if len(partes) < 2 else f"{partes[0]} {partes[-1][0]}"


def rotulo_com_seta(texto: str, titulo: str = "") -> str:
    """Rótulo cinza com a seta fina embaixo ("Por Técnico", "Carga por Dev"), como no mockup."""
    t = f' title="{esc(titulo)}"' if titulo else ""
    return (f'<div class="sem-por"{t}><p class="sem-rotulo">{esc(texto)}</p>'
            '<span class="sem-seta" aria-hidden="true"></span></div>')


def lista_pessoas(pares: list, rotulo_aria: str, classe: str = "", curto: bool = False, anonimo: str = "") -> str:
    """Colunas nome-em-cima / número-embaixo. `anonimo` troca o nome por "<anonimo> N"
    (DASHBOARD_MOSTRAR_RANKING=false esconde nome de pessoa em todo lugar)."""
    itens = []
    for i, (nome, valor) in enumerate(pares, 1):
        visivel = f"{anonimo} {i}" if anonimo else (nome_curto(nome) if curto else str(nome))
        titulo = "" if anonimo else f' title="{esc(nome)}"'
        numero = num_html(valor) if valor is not None else SEM_DADO
        itens.append(f'<li class="sem-pessoa"{titulo}><span class="sem-pessoa-nome">{esc(visivel)}</span>'
                     f'<span class="sem-pessoa-num">{numero}</span></li>')
    return f'<ul class="sem-pessoas {classe}" aria-label="{esc(rotulo_aria)}">{"".join(itens)}</ul>'


def bloco_atendimentos(atual: dict, anterior: dict, comparavel: bool) -> str:
    soma = atual["atend_soma"]
    if soma is None:
        kpi = (f'<p class="sem-num">{SEM_DADO}</p>'
               '<div class="sem-lado"><p class="sem-nota">Sem atendimentos registrados na semana.</p></div>')
    else:
        badge = badge_delta(soma, anterior["atend_soma"], "vs. semana anterior", melhor="maior") if comparavel else ""
        dias = "1 dia" if atual["atend_n"] == 1 else f'{atual["atend_n"]} dias'
        kpi = (f'<p class="sem-num">{num_html(soma)}</p>'
               f'<div class="sem-lado">{badge}<p class="sem-media" title="média de {dias}">'
               f'<b>{fmt_dec(atual["atend_media"])}</b> por dia</p></div>')
    tecnicos = ""
    if atual["por_tecnico"]:
        tecnicos = (rotulo_com_seta("Por Técnico", "soma da semana, técnico por técnico")
                    + lista_pessoas(atual["por_tecnico"], "Atendimentos da semana por técnico",
                                    anonimo="" if MOSTRAR_RANKING else "Técnico"))
    return (f'<div class="sem-bloco sem-anima" style="--i:0"><h3 class="sem-rotulo">Total Atendimentos</h3>'
            f'<div class="sem-kpi">{kpi}{tecnicos}</div></div>')


def bloco_fila(atual: dict, anterior: dict) -> str:
    """Chamados CRIADOS por clientes na semana -- não o estoque em aberto (esse segue no
    gráfico da Evolução)."""
    c, a = atual["criados"], anterior["criados"]
    if c["total"] is None:
        kpi = (f'<p class="sem-num">{SEM_DADO}</p><div class="sem-lado"><p class="sem-nota">'
               'Ainda sem contagem de chamados criados nesta semana (começa na próxima coleta).'
               '</p></div>')
    else:
        # Só compara semana completa com semana completa: com dias faltando a diferença seria falsa.
        comparavel = (a["dias"] >= MIN_DIAS_ANTERIOR and a["dias"] == a["possiveis"]
                      and c["dias"] == c["possiveis"])
        badge = badge_delta(c["total"], a["total"], "vs. semana anterior", melhor="menor") if comparavel else ""
        linhas = ('<p class="sem-linha-fila" title="média por dia útil de referência (a segunda inclui o fim de semana)">'
                  f'<b>{fmt_dec(c["media"])}</b> por dia</p>')
        if c["sistema"]:
            linhas += ('<p class="sem-linha-fila" title="sistema com mais chamados criados na semana">'
                       f'<b>{fmt_num(c["sistema_qtd"])}</b> em {esc(c["sistema"])}</p>')
        if c["dias"] < c["possiveis"]:
            linhas += f'<p class="sem-nota">contagem em {c["dias"]} de {c["possiveis"]} dia(s) da semana</p>'
        kpi = f'<p class="sem-num">{num_html(c["total"])}</p><div class="sem-lado">{badge}{linhas}</div>'
    return ('<div class="sem-bloco sem-esq sem-anima" style="--i:1" '
            'title="chamados abertos por clientes na semana (sem internos e sem atendimento diário)">'
            f'<h3 class="sem-rotulo">Fila de Chamados</h3><div class="sem-kpi">{kpi}</div></div>')


def bloco_cliente(atual: dict) -> str:
    c = atual["clientes"]
    if c["cliente"] is None:
        motivo = ("Nenhum chamado de cliente nos dias com contagem." if c["dias"] else
                  "Sem contagem por cliente nos dias desta semana (campo criados_por_cliente ausente no histórico).")
        corpo = f'<p class="sem-cliente-nome">—</p><p class="sem-nota">{esc(motivo)}</p>'
    else:
        sistema = (f'<p class="sem-detalhe">Sistema mais afetado: <b>{esc(c["sistema"])}</b></p>'
                   if c["sistema"] else "")
        notas = []
        if c["dias"] < c["possiveis"]:
            notas.append(f'contagem em {c["dias"]} de {c["possiveis"]} dia(s) da semana')
        if c["empatados"]:
            notas.append(f'empatado com mais {c["empatados"]} cliente(s)')
        nota = f'<p class="sem-nota">{esc(" · ".join(notas))}</p>' if notas else ""
        corpo = (f'<p class="sem-cliente-nome">{esc(c["cliente"])}</p>'
                 f'<div><p class="sem-detalhe">Total: <b>{fmt_num(c["total"])}</b></p>{sistema}</div>{nota}')
    return (f'<div class="sem-bloco sem-cliente sem-anima" style="--i:2"><h3 class="sem-rotulo">Cliente que mais criou chamado</h3>'
            f'{corpo}</div>')


def bloco_carga(atual: dict) -> str:
    """Chamados que foram atribuídos a cada dev na semana (entraram na carga dele)."""
    carga = atual["carga"]
    rotulo = rotulo_com_seta("Carga por Dev", "chamados atribuídos a cada dev nesta semana")
    if not carga["devs"]:
        return ('<div class="sem-carga sem-anima" style="--i:3">' + rotulo
                + '<p class="sem-nota">Sem devs registrados na semana.</p></div>')
    nota = ""
    if carga["dias"] == 0:
        nota = ('<p class="sem-nota" title="o primeiro dia de coleta com o retrato de chamados por dev é só a '
                'linha de base">Ainda sem atribuições contadas (a conta começa no 2º dia de coleta).</p>')
    elif carga["dias"] < carga["possiveis"]:
        nota = f'<p class="sem-nota">atribuições contadas em {carga["dias"]} de {carga["possiveis"]} dia(s) da semana</p>'
    # a nota vai na linha do rótulo: com ou sem ela, o bloco tem a mesma altura
    return ('<div class="sem-carga sem-anima" style="--i:3">'
            + f'<div class="sem-carga-topo">{rotulo}{nota}</div>'
            + lista_pessoas(carga["devs"], "Chamados atribuídos por dev na semana", classe="sem-devs", curto=True,
                            anonimo="" if MOSTRAR_RANKING else "DEV")
            + '</div>')


# ---- Sobre a Semana: slider com a coluna de temas à direita (mockup de 30/09/2026) ----
PALAVRAS_MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "a", "o", "as", "os", "com", "em", "na", "no", "por", "para"}
# Rótulo curto da coluna de temas para as seções que o prompt de briefing_semanal.bat pede.
# Casa o título INTEIRO (sem acento/caixa): por prefixo, "Atendimentos por sistema" viraria
# "Atend. Semana". Título fora da lista cai no rotulo_curto() genérico.
ROTULOS_TEMAS = {
    "periodo coberto": "Período Coberto", "atendimentos da semana": "Atend. Semana",
    "ranking de tecnicos": "Ranking Técnicos", "fila de chamados": "Fila Chamados",
    "licencas": "Licenças", "comparacao com a semana anterior": "Comparação",
}


def titulo_tema(texto: str) -> str:
    """'Período coberto' -> 'Período Coberto' (preposições ficam minúsculas)."""
    palavras = str(texto).split()
    return " ".join(p if (i and p.lower() in PALAVRAS_MINUSCULAS) else p[:1].upper() + p[1:]
                    for i, p in enumerate(palavras))


def rotulo_curto(texto: str) -> str:
    rotulo = ROTULOS_TEMAS.get(chave_cliente(texto).rstrip(".:"))
    if rotulo:
        return rotulo
    significativas = [p for p in str(texto).split() if p.lower() not in PALAVRAS_MINUSCULAS]
    return titulo_tema(" ".join(significativas[:2]) or str(texto))


SETA_ESQ = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M15 4 7 12l8 8" '
            'fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')
SETA_DIR = SETA_ESQ.replace("M15 4 7 12l8 8", "M9 4l8 8-8 8")


def secao_sobre_semana(slides: list[dict], nota: str, carimbo: str) -> str:
    """Slider do relatório semanal. Sem a classe .slider de propósito: quem anima é o
    JS_SEMANAL_SOBRE (GSAP), não o slider genérico do JS_UI. O id "briefing" fica."""
    n = len(slides)
    paineis, temas, pontos = [], [], []
    for i, s in enumerate(slides):
        on = i == 0
        titulo = titulo_tema(s["titulo"])
        corpo = markdown_para_html(s["md"], base=4) or "<p>—</p>"
        paineis.append(
            f'<article class="sem-slide{" ativo" if on else ""}" id="sem-slide-{i}" role="tabpanel" '
            f'aria-labelledby="sem-tema-{i}" aria-roledescription="slide" aria-label="{i + 1} de {n}: {esc(titulo)}" '
            f'{"" if on else "aria-hidden=\"true\" inert "}data-scroll tabindex="0">'
            f'<div class="sem-slide-miolo"><h3 class="sem-slide-titulo">{esc(titulo)}</h3>'
            f'<div class="sem-slide-corpo">{corpo}</div></div></article>')
        temas.append(
            f'<button type="button" class="sem-tema{" ativo" if on else ""}" role="tab" id="sem-tema-{i}" '
            f'aria-controls="sem-slide-{i}" aria-selected="{"true" if on else "false"}" tabindex="{0 if on else -1}" '
            f'title="{esc(titulo)}"><span>{esc(rotulo_curto(s["titulo"]))}</span></button>')
        pontos.append(f'<button type="button" class="sem-ponto{" ativo" if on else ""}" data-ir="{i}" '
                      f'aria-label="Ir para {esc(titulo)}"{" aria-current=\"true\"" if on else ""}></button>')
    carimbo_txt = f'relatório de {esc(carimbo)}' if carimbo else ""
    return f"""
<section class="secao sem-sobre" id="briefing" aria-labelledby="t-briefing">
  <header class="sem-cabeca bloco-fixo" title="{carimbo_txt}">{titulo_secao("Sobre a Semana", "briefing")}</header>
  <div class="sem-slider bloco-elastico" data-sem-slider aria-roledescription="carrossel" aria-label="Tópicos do relatório semanal">
    <div class="sem-slides">{''.join(paineis)}</div>
  </div>
  <div class="sem-controles bloco-fixo">
    <button type="button" class="sem-nav-seta" data-dir="-1" aria-label="Tópico anterior" disabled>{SETA_ESQ}</button>
    <div class="sem-pontos">{''.join(pontos)}</div>
    <button type="button" class="sem-nav-seta" data-dir="1" aria-label="Próximo tópico"{" disabled" if n < 2 else ""}>{SETA_DIR}</button>
  </div>
  {nota}
  <div class="sem-temas bloco-fixo" role="tablist" aria-label="Tópicos do relatório" aria-orientation="vertical">
    <span class="sem-temas-marca" aria-hidden="true"></span>{''.join(temas)}
  </div>
</section>"""


def secao_destaques_semana(atual: dict, anterior: dict, comparavel: bool) -> str:
    return f"""
<section class="secao" id="destaques" data-scroll aria-labelledby="t-destaques">
  <header class="sem-cabeca bloco-fixo">{titulo_secao("Destaques da Semana", "destaques")}</header>
  <div class="sem-corpo bloco-elastico">
    <div class="sem-linha">{bloco_atendimentos(atual, anterior, comparavel)}</div>
    <div class="sem-linha">{bloco_fila(atual, anterior)}{bloco_cliente(atual)}</div>
    {bloco_carga(atual)}
  </div>
</section>"""


def gerar_html() -> str:
    agora = datetime.now()
    historico = ler_historico()
    relatorio, relatorio_em = ler_relatorio_semanal()
    chart_js = carregar_chart_js()
    gsap_js = carregar_gsap()
    fontes_css = carregar_fontes_css() + carregar_fontes_inter_css()

    recorte = recortar(historico, agora.date())
    atual = resumo_semana(recorte[-DIAS_SEMANA:])
    anterior = resumo_semana(recorte[:-DIAS_SEMANA])
    comparavel = anterior["n"] >= MIN_DIAS_ANTERIOR
    nomes_tecnicos = coletar_nomes_tecnicos(historico, None)
    sem_historico = f"Sem registros nos últimos {JANELA_DIAS} dias em historico/metricas.jsonl."
    periodo = f"{label_dia(atual['primeira'])} a {fmt_data(atual['ultima'])}" if recorte else "—"

    # ================================================================ 1. DESTAQUES
    if not recorte:
        secao_destaques = secao_vazia("destaques", "Destaques da Semana", sem_historico)
    else:
        secao_destaques = secao_destaques_semana(atual, anterior, comparavel)

    # ================================================================ 2. RELATÓRIO
    ultima_data = data_iso(historico[-1]["data"]) if historico else None
    relatorio_velho = bool(relatorio_em and ultima_data and relatorio_em.date() < ultima_data)
    if relatorio is None:
        secao_briefing = secao_vazia("briefing", "Sobre a Semana", "Relatório indisponível (relatorio_semanal.md ausente ou vazio).")
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
                nota_rel = f'<p class="sem-nota sem-sobre-nota bloco-fixo">{inline_md(" ".join(intro.split()))}</p>'
                slides = slides[1:]
            else:
                slides[0]["titulo"] = "Visão geral"
        carimbo_rel = relatorio_em.strftime("%d/%m/%Y %H:%M") if relatorio_em else ""
        secao_briefing = secao_sobre_semana(slides, nota_rel, carimbo_rel)

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

    # ================================================================ moldura
    gerado_em = agora.strftime("%d/%m/%Y %H:%M")
    avisos = ""
    if relatorio is None:
        avisos += '<a class="aviso grave" href="#briefing" data-alvo="briefing">relatório indisponível</a>'
    elif relatorio_velho:
        avisos += (f'<a class="aviso" href="#briefing" data-alvo="briefing">relatório desatualizado · '
                   f'de {esc(relatorio_em.strftime("%d/%m"))}</a>')
    if not recorte:
        avisos += '<span class="aviso grave">histórico sem registros recentes</span>'
    semana_txt = f"Semana de {esc(periodo)}" if recorte else "Semana sem registros"
    dados_de = fmt_data(atual["ultima"]) if recorte else "—"
    menu = "".join(f'<a href="#{id_}" data-alvo="{id_}">{esc(rotulo)}</a>' for id_, rotulo in ORDEM_SEMANAL)

    # JS_SEMANAL_SOBRE roda com ou sem GSAP (sem ele, a troca de slide é instantânea)
    script = f"<script>{JS_UI}</script>\n<script>{JS_SEMANAL_SOBRE}</script>"
    if gsap_js is not None:
        # A contagem dos números passa para o GSAP (JS_SEMANAL): marca os [data-n] ANTES do JS_UI,
        # que só conta os que não têm data-contado. Se o GSAP falhar, o texto já é o valor final.
        script = ('<script>Array.prototype.forEach.call(document.querySelectorAll("[data-n]"),'
                  'function(el){el.setAttribute("data-contado","gsap")});</script>\n' + script)
        script += f"\n<script>{gsap_js}</script>\n<script>{JS_HEADER}</script>\n<script>{JS_SEMANAL}</script>"
    # "anim"/"sem-entrada" escondem o que o GSAP vai revelar; o setTimeout é a rede de segurança
    # caso o script nunca chegue a rodar (a página não pode ficar sem menu).
    marcador_anim = (
        '<script>(function(){try{if(!(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches)){'
        'var r=document.documentElement;r.classList.add("anim","sem-entrada");'
        'setTimeout(function(){r.classList.remove("sem-entrada")},4000)}}catch(e){}})();</script>'
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

    # Marca d'água em Inter Black, na proporção NATURAL do texto. viewBox = a tinta das letras
    # medida no Chrome (a 100px: x 4..1105,5; do acento do "Ó" a 1 abaixo da linha de base),
    # então as letras encostam nas duas bordas da tela. textLength só garante o encaixe se a
    # fonte cair no fallback; com Inter o ajuste é nulo (sem achatar).
    marca = ('<svg class="sem-marca" viewBox="4 53 1101.5 99" preserveAspectRatio="xMidYMax meet" '
             'aria-hidden="true" focusable="false"><defs><linearGradient id="sem-marca-g" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="#11693F"/><stop offset="1" stop-color="#0C5A35"/></linearGradient></defs>'
             '<text x="0" y="150" font-size="100" textLength="1107" lengthAdjust="spacingAndGlyphs" '
             'fill="url(#sem-marca-g)">RELATÓRIO SEMANAL</text></svg>')

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relatório Semanal — {esc(fmt_data(atual["ultima"]) if recorte else agora.strftime("%d/%m/%Y"))}</title>
<style>{fontes_css}{CSS}{CSS_SEMANAL}</style>
{marcador_anim}
</head>
<body class="pag-semanal">
<a class="pular" href="#destaques">Ir para o conteúdo</a>
<div class="palco">
<h1 class="sr-only">Relatório Semanal · {semana_txt}</h1>
<div class="sem-moldura">
<main class="colmeia" id="colmeia">
{secao_destaques}
{secao_briefing}
{secao_evolucao}
{secao_eficacia}
</main>
<nav class="sem-menu" aria-label="Seções do relatório semanal">{menu}</nav>
</div>
<p class="sem-rodape"><span>Gerado em <time datetime="{agora.strftime('%Y-%m-%dT%H:%M')}">{esc(gerado_em)}</time></span><span class="sep" aria-hidden="true">•</span><span>Dados de {esc(dados_de)}</span>{avisos}</p>
{marca}
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
