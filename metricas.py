"""Métricas do sistema novo, calculadas do banco local (dados/briefing.db).

Uma camada só: tudo que o site mostra sai daqui, e nada é calculado no
navegador nem por IA. Cada bloco declara o que é:

  ESTOQUE  foto de agora (chamados em aberto). Vale para o instante da última
           coleta COMPLETA; a coleta rápida só atualiza os chamados recentes.
  FLUXO    o que aconteceu em cada dia, datado pelo dia do FATO (abertura ou
           fechamento do chamado), nunca pelo dia em que foi coletado.

Limite que o site precisa mostrar (`historico_desde`): fechamento de chamado
só é conhecido para o que o banco viu. Antes da primeira coleta, só há os
chamados abertos na janela da carga inicial.

As regras de dev, status e equipe são as de coletores/check_helpdesk.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "coletores"))

import check_helpdesk as ch  # noqa: E402

FAIXAS_IDADE = (("ate_7_dias", 7), ("de_8_a_30_dias", 30), ("de_31_a_90_dias", 90))
AMOSTRA_ANTIGOS = 25
EVENTOS_RECENTES = 60


def _dia(iso: str | None) -> date | None:
    try:
        return date.fromisoformat((iso or "")[:10])
    except ValueError:
        return None


def _momento(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso or "")
    except ValueError:
        return None


def _contar(linhas, chave, vazio: str = "(sem informação)") -> dict[str, int]:
    """Contagem em ordem decrescente. `chave` é nome de coluna ou função."""
    saida: dict[str, int] = {}
    for l in linhas:
        valor = (chave(l) if callable(chave) else l[chave]) or vazio
        saida[valor] = saida.get(valor, 0) + 1
    return dict(sorted(saida.items(), key=lambda kv: -kv[1]))


def eh_interno(local: str | None) -> bool:
    """O local (cliente) é a própria empresa?

    Vale o nome exato de HELPDESK_LOCAIS_INTERNOS ou ele seguido de separador:
    "Sino" e "Sino - Compilação" são internos; "Sinop CM" é um cliente.
    """
    alvo = ch.chave_local(local)
    for interno in ch.LOCAIS_INTERNOS:
        base = ch.chave_local(interno)
        if base and (alvo == base or re.match(re.escape(base) + r"[^0-9a-z]", alvo)):
            return True
    return False


def _sistema(l) -> str:
    return l["sistema"] or "Outros"


def _dev(tecnico: str | None) -> str | None:
    alvo = ch.normalizar(tecnico or "")
    for nome in ch.DEV_NAMES:
        if alvo and ch.normalizar(nome) in alvo:
            return nome
    return None


def _em_status_dev(status: str | None) -> bool:
    atual = ch.normalizar(status or "")
    return any(ch.normalizar(s) in atual for s in ch.STATUS_DEV)


def _em_trabalho(status: str | None) -> bool:
    return ch.normalizar(status or "") in {ch.normalizar(s) for s in ch.STATUS_TRABALHO}


def frescor(con: sqlite3.Connection, agora: datetime) -> dict:
    """Há quanto tempo o dado não é atualizado, e se a última coleta teve problema."""
    def minutos(momento: str | None) -> int | None:
        m = _momento(momento)
        return None if m is None else max(0, int((agora - m).total_seconds() // 60))

    ultima = con.execute("SELECT momento, modo, avisos FROM coletas ORDER BY id DESC LIMIT 1").fetchone()
    completa = con.execute("SELECT momento FROM coletas WHERE fila_completa = 1 ORDER BY id DESC LIMIT 1").fetchone()
    primeira = con.execute("SELECT MIN(momento) FROM coletas WHERE fila_completa = 1").fetchone()[0]
    try:
        avisos = json.loads(ultima["avisos"]) if ultima and ultima["avisos"] else []
    except ValueError:
        avisos = []
    if not isinstance(avisos, list):
        avisos = []
    return {
        "gerado_em": agora.isoformat(timespec="seconds"),
        "ultima_coleta": ultima["momento"] if ultima else None,
        "ultima_coleta_modo": ultima["modo"] if ultima else None,
        "ultima_coleta_min": minutos(ultima["momento"]) if ultima else None,
        "ultima_completa": completa["momento"] if completa else None,
        "ultima_completa_min": minutos(completa["momento"]) if completa else None,
        "historico_desde": primeira,
        # só a quantidade: o texto do aviso cita nome de status e fica no banco
        "avisos_na_ultima_coleta": len(avisos),
        "limite_atingido": any("limite de requisições" in str(a) for a in avisos),
    }


def estoque(con: sqlite3.Connection, agora: datetime) -> dict:
    """ESTOQUE: os chamados em aberto agora."""
    hoje = agora.date()
    abertos = con.execute("SELECT * FROM chamados WHERE aberto = 1").fetchall()

    def idade(l) -> int | None:
        d = _dia(l["aberto_em"])
        return None if d is None else max(0, (hoje - d).days)   # data no futuro conta como hoje

    def resumo(linhas) -> dict:
        idades = [i for i in map(idade, linhas) if i is not None]
        return {"abertos": len(linhas), "acima_de_90_dias": sum(i > 90 for i in idades),
                "mais_antigo_dias": max(idades, default=None)}

    faixas = {nome: 0 for nome, _ in FAIXAS_IDADE}
    faixas.update(mais_de_90_dias=0, sem_data=0)
    for l in abertos:
        i = idade(l)
        if i is None:
            faixas["sem_data"] += 1
        else:
            faixas[next((nome for nome, teto in FAIXAS_IDADE if i <= teto), "mais_de_90_dias")] += 1

    por_sistema: dict[str, list] = {}
    for l in abertos:
        por_sistema.setdefault(_sistema(l), []).append(l)

    por_dev: dict[str, list] = {nome: [] for nome in ch.DEV_NAMES}
    for l in abertos:
        nome = _dev(l["tecnico"])
        if nome:
            por_dev[nome].append(l)
    em_dev = [l for l in abertos if _em_status_dev(l["status"])]

    # Dono x estágio numa matriz só: os dois totais soltos (atribuídos a devs,
    # em status de dev) se sobrepõem e não são partes de um todo.
    matriz: dict[str, dict[str, int]] = {}
    for l in abertos:
        linha = matriz.setdefault(l["tecnico"] or "(sem técnico)", {})
        status = l["status"] or "(sem informação)"
        linha[status] = linha.get(status, 0) + 1

    antigos = sorted((l for l in abertos if idade(l) is not None), key=lambda l: -idade(l))[:AMOSTRA_ANTIGOS]
    return {
        "tipo": "estoque",
        "total_abertos": len(abertos),
        # sumiram da fila na última conferência, mas ainda não faltaram vezes o
        # bastante para o banco dar como fechados: seguem contados como abertos
        "aguardando_confirmacao": sum(1 for l in abertos if l["ausencias"]),
        "idade": faixas,
        "por_sistema": {s: {**resumo(ls), "por_natureza": _contar(ls, "natureza")}
                        for s, ls in sorted(por_sistema.items(), key=lambda kv: -len(kv[1]))},
        "por_status": _contar(abertos, "status"),
        "por_natureza": _contar(abertos, "natureza"),
        "por_tecnico": _contar(abertos, "tecnico", "(sem técnico)"),
        "por_prioridade": _contar(abertos, "prioridade"),
        "tecnico_por_status": dict(sorted(matriz.items(), key=lambda kv: -sum(kv[1].values()))),
        "desenvolvimento": {
            "status_considerados_dev": list(ch.STATUS_DEV),
            "status_considerados_trabalho": list(ch.STATUS_TRABALHO),
            "em_status_dev": len(em_dev),
            "em_status_dev_por_sistema": _contar(em_dev, _sistema),
            "atribuidos_a_devs": sum(len(ls) for ls in por_dev.values()),
            "por_dev": {nome: {**resumo(ls), "em_trabalho": sum(_em_trabalho(l["status"]) for l in ls),
                               "equipe": ch.equipe_do_dev(nome), "por_sistema": _contar(ls, _sistema),
                               "por_status": _contar(ls, "status")}
                        for nome, ls in por_dev.items()},
        },
        # detalhe de chamado: só entra nos públicos que podem ver solicitante
        "mais_antigos": [{"id": l["id"], "assunto": l["assunto"], "solicitante": l["solicitante"],
                          "local": l["local"], "sistema": l["sistema"], "status": l["status"],
                          "tecnico": l["tecnico"], "dias_aberto": idade(l)} for l in antigos],
    }


def fluxo(con: sqlite3.Connection, agora: datetime, dias: int = 30) -> dict:
    """FLUXO: por dia do fato, nos últimos `dias` dias (hoje incluído)."""
    hoje = agora.date()
    inicio = hoje - timedelta(days=dias - 1)
    serie = {(inicio + timedelta(days=n)).isoformat(): {
        "criados_clientes": 0, "criados_internos": 0, "fechados": 0,
        "atendimentos": 0, "atendimentos_por_tecnico": {}} for n in range(dias)}
    por_cliente: dict[str, dict] = {}
    criados_por_sistema: dict[str, int] = {}

    corte = inicio.isoformat()
    # ordem fixa: a grafia exibida do cliente é a do chamado de menor id, sempre
    for l in con.execute("SELECT * FROM chamados WHERE aberto_em >= ? OR fechado_em >= ? "
                         "ORDER BY CAST(id AS INTEGER), id", (corte, corte)):
        aberto, fechado = _dia(l["aberto_em"]), _dia(l["fechado_em"])
        dia_aberto = serie.get(aberto.isoformat()) if aberto else None
        if l["atendimento_diario"]:
            # atendimento = registro do técnico, contado no dia em que foi feito, já fechado
            if dia_aberto is not None and not l["aberto"]:
                dia_aberto["atendimentos"] += 1
                tecnico = l["atendimento_tecnico"] or "(não identificado)"
                dia_aberto["atendimentos_por_tecnico"][tecnico] = \
                    dia_aberto["atendimentos_por_tecnico"].get(tecnico, 0) + 1
            continue
        if dia_aberto is not None:
            if eh_interno(l["local"]):
                dia_aberto["criados_internos"] += 1
            else:
                dia_aberto["criados_clientes"] += 1
                # campo livre: "Câmara X" e "CAMARA  X" são o mesmo cliente. Agrupa pela
                # forma sem acento/caixa e exibe a primeira grafia vista.
                bloco = por_cliente.setdefault(ch.chave_local(l["local"]), {
                    "nome": l["local"] or "(sem local)", "total": 0, "por_sistema": {}})
                bloco["total"] += 1
                sistema = _sistema(l)
                bloco["por_sistema"][sistema] = bloco["por_sistema"].get(sistema, 0) + 1
                criados_por_sistema[sistema] = criados_por_sistema.get(sistema, 0) + 1
        if fechado and not l["aberto"] and fechado.isoformat() in serie:
            serie[fechado.isoformat()]["fechados"] += 1

    return {
        "tipo": "fluxo",
        "inicio": inicio.isoformat(), "fim": hoje.isoformat(), "dias": dias,
        "por_dia": serie,
        "criados_por_cliente": {b["nome"]: {"total": b["total"], "por_sistema": b["por_sistema"]}
                                for b in sorted(por_cliente.values(), key=lambda b: -b["total"])},
        "criados_por_sistema": dict(sorted(criados_por_sistema.items(), key=lambda kv: -kv[1])),
    }


def resolucao(con: sqlite3.Connection, agora: datetime, dias: int = 30) -> dict:
    """FLUXO: tempo entre abrir e fechar, dos chamados FECHADOS nos últimos `dias` dias.

    Sem atendimento diário (abre e fecha na hora, distorceria tudo). Fechamento
    inferido entra com a hora em que a coleta notou a ausência (erro de até o
    intervalo entre duas coletas completas).
    """
    corte = (agora.date() - timedelta(days=dias - 1)).isoformat()
    tempos: list[float] = []
    por_sistema: dict[str, list[float]] = {}
    for l in con.execute("SELECT sistema, aberto_em, fechado_em FROM chamados WHERE aberto = 0 "
                         "AND atendimento_diario = 0 AND fechado_em >= ?", (corte,)):
        a, f = _momento(l["aberto_em"]), _momento(l["fechado_em"])
        if a is None or f is None or f < a:
            continue
        horas = (f - a).total_seconds() / 3600
        tempos.append(horas)
        por_sistema.setdefault(l["sistema"] or "Outros", []).append(horas)

    def resumo(valores: list[float]) -> dict:
        if not valores:
            return {"fechados": 0, "mediana_horas": None, "no_mesmo_dia": 0, "mais_de_7_dias": 0}
        return {"fechados": len(valores), "mediana_horas": round(statistics.median(valores), 1),
                "no_mesmo_dia": sum(v <= 24 for v in valores), "mais_de_7_dias": sum(v > 168 for v in valores)}

    # Fechado sem data de fechamento não entra em "fechados por dia" nem aqui: o
    # número fica à vista para ninguém achar que a conta está completa.
    sem_data = con.execute("SELECT COUNT(*) FROM chamados WHERE aberto = 0 AND atendimento_diario = 0 "
                           "AND fechado_em IS NULL").fetchone()[0]
    return {"tipo": "fluxo", "dias": dias, **resumo(tempos), "fechados_sem_data": sem_data,
            "por_sistema": {s: resumo(v) for s, v in sorted(por_sistema.items(), key=lambda kv: -len(kv[1]))}}


def eventos_recentes(con: sqlite3.Connection, limite: int = EVENTOS_RECENTES) -> list[dict]:
    """As últimas mudanças observadas (mais nova primeiro). Tem assunto de chamado."""
    return [dict(l) for l in con.execute(
        "SELECT e.momento, e.tipo, e.de, e.para, e.chamado_id, c.assunto, c.sistema, c.tecnico "
        "FROM eventos e LEFT JOIN chamados c ON c.id = e.chamado_id ORDER BY e.id DESC LIMIT ?", (limite,))]
