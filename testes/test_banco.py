"""Testes do banco e da coleta incremental (banco.py, coletar.py).

Sem rede e sem tocar em dados/: banco em memória, chave falsa e a função que
fala com o Milldesk substituída por uma que devolve chamados inventados.

Uso:
    python testes/test_banco.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
CHAVE = "chave-falsa-de-teste"
os.environ["HELPDESK_API_KEY"] = CHAVE          # load_dotenv não sobrescreve
os.environ["HELPDESK_STATUS_EXCLUIDOS"] = "Fechado"
os.environ["HELPDESK_STATUS_ABERTOS"] = "Aberto"

import banco  # noqa: E402
import coletar  # noqa: E402

ch = coletar.ch
assert ch.API_KEY == CHAVE, "o teste não pode rodar com a chave real"
coletar.PAUSA_SEGUNDOS = 0                      # sem espera entre chamadas falsas

STATUS = ["Aberto", "Em atendimento"]


def reg(cid, status="Aberto", tecnico="Ana", **extra) -> dict:
    return {"id": str(cid), "status": status, "aberto": int(status != "Fechado"),
            "tecnico": tecnico, **extra}


def aplicar(con, momento, registros, **kw):
    kw.setdefault("fila_completa", True)
    kw.setdefault("status_consultados", STATUS)
    return banco.aplicar_coleta(con, momento=momento, registros=registros, **kw)


def eventos(con, coleta_id=None) -> list[tuple]:
    sql = "SELECT chamado_id, tipo, de, para FROM eventos"
    args = ()
    if coleta_id is not None:
        sql, args = sql + " WHERE coleta_id = ?", (coleta_id,)
    return [tuple(l) for l in con.execute(sql + " ORDER BY id", args)]


def abertos(con) -> set[str]:
    return {l["id"] for l in con.execute("SELECT id FROM chamados WHERE aberto = 1")}


def base(n=3):
    con = banco.conectar(":memory:")
    r = aplicar(con, "2026-10-01T08:00:00", [reg(i) for i in range(1, n + 1)])
    return con, r


def teste_linha_de_base_nao_gera_evento():
    con, r = base()
    assert r["linha_de_base"] and r["novos"] == 3 and r["eventos"] == 0
    assert eventos(con) == [] and abertos(con) == {"1", "2", "3"}


def teste_mudancas_viram_eventos():
    con, _ = base()
    r = aplicar(con, "2026-10-01T08:10:00", [
        reg(1, "Em atendimento"),             # mudou status
        reg(2, tecnico="Bia"),                # mudou dono
        reg(3, "Fechado"),                    # fechou (visto na rebusca)
        reg(4),                               # chamado novo
    ])
    assert not r["linha_de_base"]
    assert eventos(con, r["coleta_id"]) == [
        ("1", "status", "Aberto", "Em atendimento"),
        ("2", "dono", "Ana", "Bia"),
        ("3", "fechou", "Aberto", "Fechado"),
        ("4", "entrou", None, "Aberto"),
    ]
    assert abertos(con) == {"1", "2", "4"}


def teste_coleta_igual_nao_gera_evento():
    con, _ = base()
    r = aplicar(con, "2026-10-01T08:10:00", [reg(i) for i in (1, 2, 3)])
    assert r["eventos"] == 0 and r["atualizados"] == 3


def teste_sumico_vira_fechamento_inferido_na_segunda_ausencia():
    con, _ = base()
    r = aplicar(con, "2026-10-01T08:10:00", [reg(1), reg(2)])
    assert r["fechados_inferidos"] == 0 and r["eventos"] == 0 and abertos(con) == {"1", "2", "3"}
    r = aplicar(con, "2026-10-01T08:20:00", [reg(1), reg(2)])
    assert r["fechados_inferidos"] == 1
    assert eventos(con, r["coleta_id"]) == [("3", "fechou", "Aberto", None)]
    linha = con.execute("SELECT * FROM chamados WHERE id = '3'").fetchone()
    assert linha["aberto"] == 0 and linha["fechamento_inferido"] == 1
    assert linha["fechado_em"] == "2026-10-01T08:20:00"
    # a rebusca confirma depois SEM data (o normal da API): nenhum evento novo e
    # a data inferida não é apagada
    r2 = aplicar(con, "2026-10-01T08:30:00", [reg(1), reg(2), reg(3, "Fechado")])
    linha = con.execute("SELECT * FROM chamados WHERE id = '3'").fetchone()
    assert r2["eventos"] == 0 and linha["status"] == "Fechado"
    assert linha["fechado_em"] == "2026-10-01T08:20:00" and linha["fechamento_inferido"] == 1
    # quando a data real aparece, ela vence e deixa de ser inferência
    aplicar(con, "2026-10-01T08:40:00", [reg(1), reg(2), reg(3, "Fechado", fechado_em="2026-10-01T08:05")])
    linha = con.execute("SELECT * FROM chamados WHERE id = '3'").fetchone()
    assert linha["fechamento_inferido"] == 0 and linha["fechado_em"] == "2026-10-01T08:05"


def teste_ausencia_unica_nao_fecha_nem_gera_par_falso():
    """Chamado que escapa de uma coleta (mudou de status no meio) e volta."""
    con, _ = base()
    aplicar(con, "2026-10-01T08:10:00", [reg(1), reg(2)])
    r = aplicar(con, "2026-10-01T08:20:00", [reg(1), reg(2), reg(3, "Em atendimento")])
    assert eventos(con) == [("3", "status", "Aberto", "Em atendimento")]
    # voltou: a contagem zera, uma nova ausência isolada também não fecha
    r = aplicar(con, "2026-10-01T08:30:00", [reg(1), reg(2)])
    assert r["fechados_inferidos"] == 0 and abertos(con) == {"1", "2", "3"}


def teste_reabertura():
    con, _ = base()
    aplicar(con, "2026-10-01T08:10:00", [reg(1), reg(2), reg(3, "Fechado")])
    r = aplicar(con, "2026-10-01T08:20:00", [reg(1), reg(2), reg(3, "Em atendimento")])
    assert eventos(con, r["coleta_id"]) == [("3", "reabriu", "Fechado", "Em atendimento")]


def teste_fila_incompleta_nao_infere():
    con, _ = base()
    r = aplicar(con, "2026-10-01T08:10:00", [reg(1)], fila_completa=False)
    assert r["fechados_inferidos"] == 0 and abertos(con) == {"1", "2", "3"}
    assert any("incompleta" in a for a in r["avisos"])
    # dez coletas incompletas seguidas não contam ausência nenhuma
    for _ in range(10):
        aplicar(con, "2026-10-01T08:15:00", [reg(1)], fila_completa=False)
    assert abertos(con) == {"1", "2", "3"}
    assert aplicar(con, "2026-10-01T08:20:00", [reg(1)])["fechados_inferidos"] == 0
    assert aplicar(con, "2026-10-01T08:30:00", [reg(1)])["fechados_inferidos"] == 2


def teste_status_removido_nao_infere():
    con, _ = base()
    for _ in range(5):
        r = aplicar(con, "2026-10-01T08:10:00", [reg(1)], status_consultados=["Aberto"], fila_completa=False)
    assert abertos(con) == {"1", "2", "3"}
    r = aplicar(con, "2026-10-01T08:20:00", [reg(1)], status_consultados=["Aberto"])
    assert r["fechados_inferidos"] == 0 and abertos(con) == {"1", "2", "3"}
    assert any("deixaram de ser consultados" in a for a in r["avisos"])
    # a régua menor só passa a valer depois de aceita como coleta completa
    aplicar(con, "2026-10-01T08:30:00", [reg(1)], status_consultados=["Aberto"])
    r = aplicar(con, "2026-10-01T08:40:00", [reg(1)], status_consultados=["Aberto"])
    assert r["fechados_inferidos"] == 2


def teste_sumico_em_massa_pede_uma_ausencia_a_mais_e_nao_trava():
    """40 de 100 fecham de verdade e depois mais um por coleta (caso do revisor)."""
    con, _ = base(100)
    vivos = list(range(1, 61))
    r = aplicar(con, "2026-10-01T08:10:00", [reg(i) for i in vivos])
    assert r["fechados_inferidos"] == 0 and any("de uma vez" in a for a in r["avisos"])
    vivos.pop()                                                   # mais um fecha
    r = aplicar(con, "2026-10-01T08:20:00", [reg(i) for i in vivos])
    assert r["fechados_inferidos"] == 0 and len(abertos(con)) == 100
    vivos.pop()
    r = aplicar(con, "2026-10-01T08:30:00", [reg(i) for i in vivos])
    assert r["fechados_inferidos"] == 40, r                       # os 40 na 3ª ausência
    vivos.pop()
    r = aplicar(con, "2026-10-01T08:40:00", [reg(i) for i in vivos])
    r = aplicar(con, "2026-10-01T08:50:00", [reg(i) for i in vivos])
    assert abertos(con) == {str(i) for i in vivos} and len(vivos) == 57
    assert sum(1 for e in eventos(con) if e[1] == "fechou") == 43


def teste_status_vazio_uma_vez_nao_fecha_fila_pequena():
    """API devolve um status vazio com HTTP 200 numa coleta: ninguém fecha."""
    con, _ = base(10)
    r = aplicar(con, "2026-10-01T08:10:00", [])
    assert r["fechados_inferidos"] == 0
    r = aplicar(con, "2026-10-01T08:20:00", [reg(i) for i in range(1, 11)])
    assert eventos(con) == [] and len(abertos(con)) == 10


def teste_linha_de_base_depois_de_coletas_incompletas():
    con = banco.conectar(":memory:")
    aplicar(con, "2026-10-01T08:00:00", [reg(1), reg(2), reg(3)], fila_completa=False)
    r = aplicar(con, "2026-10-01T08:10:00", [reg(1, "Em atendimento"), reg(2, tecnico="Bia"),
                                              reg(3, "Fechado"), reg(4)])
    assert r["linha_de_base"] and eventos(con) == [] and r["eventos"] == 0
    r = aplicar(con, "2026-10-01T08:20:00", [reg(1), reg(2, tecnico="Bia"), reg(4)])
    assert not r["linha_de_base"] and eventos(con) == [("1", "status", "Em atendimento", "Aberto")]


def teste_janela_maior_nao_inunda_de_eventos():
    con = banco.conectar(":memory:")
    aplicar(con, "2026-10-01T08:00:00", [reg(1)], periodo=("2026-09-25", "2026-10-01"))
    r = aplicar(con, "2026-10-01T08:10:00", [
        reg(1),
        reg(2, "Fechado", aberto_em="2026-08-10T09:00"),   # antigo e fechado: só carga
        reg(3, "Fechado", aberto_em="2026-09-30T09:00"),   # abriu e fechou entre coletas
        reg(4, "Aberto", aberto_em="2026-08-10T09:00"),    # antigo que REABRIU: é notícia
    ], periodo=("2026-08-03", "2026-10-01"))
    assert eventos(con, r["coleta_id"]) == [("3", "entrou", None, "Fechado"), ("4", "entrou", None, "Aberto")]
    assert r["novos"] == 3


def teste_falha_no_meio_desfaz_a_coleta():
    con, _ = base()
    try:
        aplicar(con, "2026-10-01T08:10:00", [reg(1, "Em atendimento"), {"sem": "id"}])
    except KeyError:
        pass
    else:
        raise AssertionError("registro sem id deveria falhar")
    assert eventos(con) == [] and con.execute("SELECT COUNT(*) FROM coletas").fetchone()[0] == 1
    assert con.execute("SELECT status FROM chamados WHERE id = '1'").fetchone()[0] == "Aberto"


# ---------------------------------------------------------------- coletar.py

def cru(cid, status, **extra) -> dict:
    return {"id": str(cid), "ticket": f"assunto {cid}", "requester": "Fulano", "status": status,
            "category": "Siscam 9 WEB", "subcategory": "Bug / Erro", "agent": "Ana",
            "location": "Câmara  X", "start": "30/09/2026", "starttime": "30/09/2026 08:28",
            "end": None, "endtime": None, **extra}


def api_falsa(fila: dict[str, list[dict]], periodo, falhar=()):
    pedidos = []

    def chamar(endpoint, params=None):
        pedidos.append((endpoint, dict(params or {})))
        if endpoint in falhar or (params or {}).get("status") in falhar:
            raise ConnectionError(f"Max retries exceeded with url: /api/{CHAVE}/{endpoint}")
        if endpoint == "listTicketStatus":
            return [{"status": s} for s in [*fila, "Fechado"]]
        if endpoint == "showTicketsByStatus":
            return [dict(t) for t in fila[params["status"]]]
        if endpoint == "showTicketsPerPeriod":
            return [dict(t) for t in (periodo(params) if callable(periodo) else periodo)]
        raise AssertionError(endpoint)

    ch.chamar = chamar
    return pedidos


AGORA = datetime(2026, 10, 1, 9, 0)


def teste_registro_do_chamado():
    r = coletar.registro_do_chamado(cru(7, "Fechado", endtime="30/09/2026 17:02"))
    assert r["aberto"] == 0 and r["sistema"] == "Siscam 9" and r["natureza"] == "Corretivo"
    assert r["local"] == "Câmara X" and r["aberto_em"] == "2026-09-30T08:28"
    assert r["fechado_em"] == "2026-09-30T17:02" and r["atendimento_diario"] == 0
    assert set(r) == set(banco.CAMPOS)
    aberto = coletar.registro_do_chamado(cru(8, "Aberto", endtime="30/09/2026 08:28"))
    assert aberto["aberto"] == 1 and aberto["fechado_em"] is None
    diario = coletar.registro_do_chamado(cru(
        9, "Fechado", requester="Atendimento Diário", description="Técnico: Fabio<br>Cliente: Y"))
    assert diario["atendimento_diario"] == 1 and diario["atendimento_tecnico"] == "Fabio"


def teste_coleta_junta_fila_e_periodo():
    fila = {"Aberto": [cru(1, "Aberto"), cru(2, "Aberto")], "Em atendimento": [cru(3, "Em atendimento")]}
    con = banco.conectar(":memory:")
    pedidos = api_falsa(fila, [cru(2, "Aberto"), cru(5, "Fechado")])
    r = coletar.coletar(con, dias=7, agora=AGORA)
    assert r["linha_de_base"] and r["novos"] == 4 and abertos(con) == {"1", "2", "3"}
    periodo = [p for e, p in pedidos if e == "showTicketsPerPeriod"]
    assert periodo == [{"start": "2026-09-25", "end": "2026-10-01"}], periodo
    coleta = con.execute("SELECT * FROM coletas").fetchone()
    assert coleta["total_abertos"] == 3 and coleta["total_periodo"] == 2 and coleta["fila_completa"] == 1

    # o 1 some da fila; o 3 muda de status no meio da coleta e vem em dois status
    fila = {"Aberto": [cru(2, "Aberto"), cru(3, "Aberto")], "Em atendimento": [cru(3, "Em atendimento")]}
    api_falsa(fila, [cru(2, "Aberto"), cru(5, "Fechado")])
    r = coletar.coletar(con, dias=7, agora=AGORA)
    assert any("mais de uma vez" in a for a in r["avisos"]) and r["fechados_inferidos"] == 0
    r = coletar.coletar(con, dias=7, agora=AGORA)
    assert abertos(con) == {"2", "3"} and r["fechados_inferidos"] == 1


def teste_rebusca_sem_status_nao_reabre_nem_sobrescreve():
    fila = {"Aberto": [cru(1, "Aberto")], "Em atendimento": []}
    con = banco.conectar(":memory:")
    api_falsa(fila, [cru(5, "Fechado")])
    coletar.coletar(con, dias=7, agora=AGORA)
    api_falsa(fila, [cru(5, None), cru(1, "")])
    r = coletar.coletar(con, dias=7, agora=AGORA)
    assert abertos(con) == {"1"} and r["eventos"] == 0
    assert con.execute("SELECT status FROM chamados WHERE id = '1'").fetchone()[0] == "Aberto"
    assert any("sem status" in a for a in r["avisos"])


def teste_trava_de_escrita_vem_antes_de_ler_a_coleta_anterior():
    con, _ = base()
    comandos: list[str] = []
    con.set_trace_callback(comandos.append)
    aplicar(con, "2026-10-01T08:10:00", [reg(1), reg(2), reg(3)])
    con.set_trace_callback(None)
    trava = next(i for i, c in enumerate(comandos) if c.startswith("BEGIN IMMEDIATE"))
    leitura = next(i for i, c in enumerate(comandos) if "FROM coletas" in c)
    assert trava < leitura, comandos[:4]


def teste_api_toda_vazia_nao_fecha_fila_pequena():
    """Pane: todos os status e a rebusca vazios com HTTP 200, várias coletas seguidas."""
    fila = {"Aberto": [cru(i, "Aberto") for i in range(1, 11)], "Em atendimento": []}
    con = banco.conectar(":memory:")
    api_falsa(fila, [])
    coletar.coletar(con, dias=7, agora=AGORA)
    api_falsa({"Aberto": [], "Em atendimento": []}, [])
    for _ in range(4):
        r = coletar.coletar(con, dias=7, agora=AGORA)
    assert len(abertos(con)) == 10 and r["fechados_inferidos"] == 0
    assert any("vazia" in a for a in r["avisos"])
    api_falsa(fila, [])
    assert coletar.coletar(con, dias=7, agora=AGORA)["eventos"] == 0


def teste_carga_que_falha_no_meio_nao_inunda_na_nova_tentativa():
    fila = {"Aberto": [cru(1, "Aberto")], "Em atendimento": []}
    antigo = cru(50, "Fechado", start="10/08/2026", starttime="10/08/2026 09:00")
    con = banco.conectar(":memory:")
    api_falsa(fila, [])
    coletar.coletar(con, dias=7, agora=AGORA)                       # janela desde 25/09
    api_falsa(fila, [], falhar=["showTicketsPerPeriod"])
    coletar.coletar(con, dias=60, agora=AGORA)                      # carga larga falha
    assert con.execute("SELECT periodo_inicio FROM coletas ORDER BY id DESC").fetchone()[0] is None
    api_falsa(fila, lambda p: [antigo] if p["start"] == "2026-08-03" else [])
    r = coletar.coletar(con, dias=60, agora=AGORA)                  # nova tentativa
    assert r["novos"] == 1 and r["eventos"] == 0, r


def teste_corpo_estranho_em_http_200_nao_fecha_ninguem():
    """Um status responde {"message": ...} em vez da lista: é falha, não fila vazia."""
    fila = {"Aberto": [cru(1, "Aberto")], "Em atendimento": [cru(3, "Em atendimento")]}
    con = banco.conectar(":memory:")
    api_falsa(fila, [])
    coletar.coletar(con, dias=7, agora=AGORA)
    ch.chamar = lambda e, p=None: ({"message": "tente mais tarde"} if (p or {}).get("status") == "Em atendimento"
                                   else [{"status": s} for s in [*fila, "Fechado"]] if e == "listTicketStatus"
                                   else [dict(t) for t in fila.get((p or {}).get("status"), [])])
    for _ in range(4):
        r = coletar.coletar(con, dias=7, agora=AGORA)
    assert abertos(con) == {"1", "3"} and r["fechados_inferidos"] == 0
    assert con.execute("SELECT fila_completa FROM coletas ORDER BY id DESC").fetchone()[0] == 0
    assert eventos(con) == []


def teste_corpo_vazio_que_nao_e_lista_e_falha():
    """{} ou null no lugar da lista não é 'status sem chamados'."""
    fila = {"Aberto": [cru(1, "Aberto")], "Em atendimento": [cru(3, "Em atendimento")]}
    for corpo in ({}, None, "", 0):
        con = banco.conectar(":memory:")
        api_falsa(fila, [])
        coletar.coletar(con, dias=7, agora=AGORA)
        original = ch.chamar
        ch.chamar = lambda e, p=None: corpo if (p or {}).get("status") == "Em atendimento" else original(e, p)
        for _ in range(3):
            r = coletar.coletar(con, dias=7, agora=AGORA)
        assert abertos(con) == {"1", "3"} and eventos(con) == [], repr(corpo)
        assert any("Em atendimento" in a for a in r["avisos"]), repr(corpo)


class Erro429(Exception):
    def __init__(self):
        super().__init__(f"429 Client Error: Too Many Requests for url: https://x/api/{CHAVE}/y")
        self.response = type("R", (), {"status_code": 429})()


def teste_limite_para_a_coleta_na_hora():
    fila = {"Aberto": [cru(1, "Aberto")], "Em atendimento": [cru(3, "Em atendimento")], "Pendente": []}
    con = banco.conectar(":memory:")
    api_falsa(fila, [])
    coletar.coletar(con, dias=7, agora=AGORA)
    pedidos = []

    def chamar(endpoint, params=None):
        pedidos.append(endpoint)
        if (params or {}).get("status") == "Em atendimento":
            raise Erro429()
        if endpoint == "listTicketStatus":
            return [{"status": s} for s in [*fila, "Fechado"]]
        if endpoint == "showTicketsPerPeriod":
            return [cru(1, "Em atendimento"), cru(9, "Aberto")]
        return [dict(t) for t in fila[params["status"]]]

    ch.chamar = chamar
    for _ in range(3):
        pedidos.clear()
        r = coletar.coletar(con, dias=7, agora=AGORA)
        # rebusca, lista de status, 'Aberto', 'Em atendimento' (429) -- e mais nada
        assert pedidos == ["showTicketsPerPeriod", "listTicketStatus", "showTicketsByStatus",
                           "showTicketsByStatus"], pedidos
        assert r["requisicoes"] == 4 and r["fechados_inferidos"] == 0
        assert any("limite" in a for a in r["avisos"]) and CHAVE not in str(r["avisos"])
    # a rebusca, que veio antes do limite, foi aproveitada; ninguém fechou
    assert abertos(con) == {"1", "3", "9"}
    assert ("1", "status", "Aberto", "Em atendimento") in eventos(con)
    linha = con.execute("SELECT fila_completa, modo, requisicoes, total_abertos FROM coletas ORDER BY id DESC").fetchone()
    assert tuple(linha) == (0, "completa", 4, None)


def api_com_limite(fila, periodo, estoura):
    """API falsa que devolve 429 quando `estoura(endpoint, params)` é verdadeiro."""
    pedidos = []

    def chamar(endpoint, params=None):
        pedidos.append((endpoint, dict(params or {})))
        if estoura(endpoint, params or {}):
            raise Erro429()
        if endpoint == "listTicketStatus":
            return [{"status": s} for s in [*fila, "Fechado"]]
        if endpoint == "showTicketsPerPeriod":
            return [dict(t) for t in periodo(params)]
        return [dict(t) for t in fila[params["status"]]]

    ch.chamar = chamar
    return pedidos


def teste_limite_na_lista_de_status_nao_cai_no_fallback():
    """429 em listTicketStatus era engolido e a coleta seguia pedindo os status do fallback."""
    fila = {"Aberto": [cru(1, "Aberto")], "Em atendimento": [cru(3, "Em atendimento")]}
    con = banco.conectar(":memory:")
    api_falsa(fila, [])
    coletar.coletar(con, dias=7, agora=AGORA)
    pedidos = api_com_limite(fila, lambda p: [cru(9, "Aberto")], lambda e, p: e == "listTicketStatus")
    r = coletar.coletar(con, dias=7, agora=AGORA)
    assert [e for e, _ in pedidos] == ["showTicketsPerPeriod", "listTicketStatus"], pedidos
    assert r["requisicoes"] == 2 and r["fechados_inferidos"] == 0 and abertos(con) == {"1", "3", "9"}
    assert sum("limite" in a for a in r["avisos"]) == 1 and CHAVE not in str(r["avisos"])
    assert con.execute("SELECT fila_completa FROM coletas ORDER BY id DESC").fetchone()[0] == 0


def teste_limite_na_rebusca_nao_consulta_a_fila_e_guarda_o_que_veio():
    fila = {"Aberto": [cru(1, "Aberto")]}
    con = banco.conectar(":memory:")
    pedidos = api_com_limite(fila, lambda p: [cru(50, "Fechado", start="10/08/2026", starttime="10/08/2026 09:00")],
                             lambda e, p: p.get("start") == "2026-09-02")
    r = coletar.coletar(con, dias=60, agora=AGORA)
    assert [e for e, _ in pedidos] == ["showTicketsPerPeriod", "showTicketsPerPeriod"], pedidos
    assert r["novos"] == 1 and r["requisicoes"] == 2          # o 1º pedaço ficou
    linha = con.execute("SELECT periodo_inicio, total_periodo, fila_completa FROM coletas").fetchone()
    assert tuple(linha) == (None, 1, 0)                        # janela incompleta não vira régua


def teste_rapida_barrada_grava_periodo_vazio_e_nao_zero():
    con = banco.conectar(":memory:")
    api_com_limite({"Aberto": []}, lambda p: [cru(1, "Aberto")], lambda e, p: True)
    coletar.coletar(con, dias=7, agora=AGORA, rapida=True)
    assert tuple(con.execute("SELECT periodo_inicio, total_periodo FROM coletas").fetchone()) == (None, None)
    # período que respondeu sem nenhum chamado é zero de verdade
    api_falsa({"Aberto": []}, [])
    coletar.coletar(con, dias=7, agora=AGORA, rapida=True)
    assert con.execute("SELECT total_periodo FROM coletas ORDER BY id DESC").fetchone()[0] == 0


def teste_pausa_entre_requisicoes():
    fila = {"Aberto": [cru(1, "Aberto")], "Em atendimento": []}
    con = banco.conectar(":memory:")
    api_falsa(fila, [])
    esperas = []
    dormir, coletar.time.sleep, coletar.PAUSA_SEGUNDOS = coletar.time.sleep, esperas.append, 1.5
    try:
        r = coletar.coletar(con, dias=7, agora=AGORA)
    finally:
        coletar.time.sleep, coletar.PAUSA_SEGUNDOS = dormir, 0
    assert r["requisicoes"] == 4 and esperas == [1.5] * 3      # uma espera antes de cada chamada, menos a 1ª


def teste_migracao_tolera_coluna_criada_por_outra_coleta():
    import sqlite3

    class Corrida(sqlite3.Connection):
        """Faz o PRAGMA mentir que as colunas não existem, como se outra coleta as criasse logo depois."""
        def execute(self, sql, *a):
            if sql.startswith("PRAGMA table_info(coletas)"):
                return super().execute("SELECT 'id' AS name")
            return super().execute(sql, *a)

    original = sqlite3.connect
    sqlite3.connect = lambda *a, **k: original(*a, factory=Corrida, **k)
    try:
        con = banco.conectar(":memory:")           # ESQUEMA já cria modo/requisicoes; o ALTER duplica
    finally:
        sqlite3.connect = original
    assert aplicar(con, "2026-10-01T08:00:00", [reg(1)], modo="completa")["novos"] == 1


def teste_coleta_rapida_faz_uma_requisicao_e_nao_conta_ausencia():
    fila = {"Aberto": [cru(1, "Aberto"), cru(2, "Aberto")], "Em atendimento": [cru(3, "Em atendimento")]}
    con = banco.conectar(":memory:")
    api_falsa(fila, [cru(2, "Aberto")])
    coletar.coletar(con, dias=7, agora=AGORA)
    pedidos = api_falsa(fila, [cru(2, "Fechado", endtime="01/10/2026 08:50"), cru(7, "Aberto")])
    for _ in range(5):
        r = coletar.coletar(con, dias=7, agora=AGORA, rapida=True)
    assert [e for e, _ in pedidos] == ["showTicketsPerPeriod"] * 5
    assert r["avisos"] == [] and r["requisicoes"] == 1 and r["fechados_inferidos"] == 0
    # 1 e 3 são antigos: a rápida não os vê e não pode concluir nada sobre eles
    assert abertos(con) == {"1", "3", "7"}
    assert eventos(con) == [("2", "fechou", "Aberto", "Fechado"), ("7", "entrou", None, "Aberto")]
    assert con.execute("SELECT MAX(ausencias) FROM chamados").fetchone()[0] == 0
    # a rápida não vira régua: a próxima completa ainda compara com a completa anterior
    api_falsa({"Aberto": [cru(1, "Aberto")]}, [])
    r = coletar.coletar(con, dias=7, agora=AGORA)
    assert any("deixaram de ser consultados" in a for a in r["avisos"])


def teste_rapida_antes_de_qualquer_completa_e_linha_de_base():
    con = banco.conectar(":memory:")
    api_falsa({"Aberto": [cru(1, "Aberto")]}, [cru(1, "Aberto"), cru(5, "Fechado")])
    r = coletar.coletar(con, dias=7, agora=AGORA, rapida=True)
    assert r["linha_de_base"] and eventos(con) == []
    r = coletar.coletar(con, dias=7, agora=AGORA)
    assert r["linha_de_base"] and eventos(con) == []


def teste_fila_lida_depois_vence_a_rebusca():
    """Chamado fechado na rebusca e reaberto antes de a fila ser lida: vale a fila."""
    con = banco.conectar(":memory:")
    api_falsa({"Aberto": [cru(1, "Aberto")]}, [cru(1, "Fechado", endtime="01/10/2026 08:50")])
    coletar.coletar(con, dias=7, agora=AGORA)
    linha = con.execute("SELECT aberto, status, fechado_em FROM chamados WHERE id = '1'").fetchone()
    assert tuple(linha) == (1, "Aberto", None)


def teste_banco_antigo_ganha_as_colunas_novas():
    import sqlite3
    import tempfile
    caminho = Path(tempfile.mkdtemp()) / "velho.db"
    velho = sqlite3.connect(str(caminho))
    velho.executescript(banco.ESQUEMA)                       # esquema atual ...
    velho.executescript("ALTER TABLE coletas RENAME TO c2; CREATE TABLE coletas AS SELECT id, fonte, momento, "
                        "fila_completa, status_consultados, total_abertos, total_periodo, periodo_inicio, "
                        "periodo_fim, avisos FROM c2; DROP TABLE c2;")  # ... sem modo/requisicoes
    velho.execute("INSERT INTO coletas (id, fonte, momento, fila_completa, status_consultados) "
                  "VALUES (1, 'milldesk', '2026-10-01T15:49:27', 1, ?)", ('["Aberto", "Em atendimento"]',))
    velho.commit()
    velho.close()
    for _ in range(2):                                       # abrir duas vezes não quebra
        con = banco.conectar(caminho)
        colunas = {l["name"] for l in con.execute("PRAGMA table_info(coletas)")}
        assert {"modo", "requisicoes"} <= colunas
        r = aplicar(con, "2026-10-01T16:30:00", [reg(1)], modo="completa", requisicoes=19)
        assert not r["linha_de_base"]
        con.close()


def teste_publicar_guarda_do_projeto_sem_api_do_python_39():
    import inspect
    import publicar
    assert "is_relative_to" not in inspect.getsource(publicar).replace("Path.is_relative_to", "")
    raiz = publicar.RAIZ.resolve()
    casos = {raiz: True, raiz / "dados": True, raiz.parent: True, raiz.parent / "outro": False,
             Path(raiz.anchor) / "inetpub" / "wwwroot": False}
    for destino, esperado in casos.items():
        assert publicar.encosta_no_projeto(destino) is esperado, destino
    for a, b in [(raiz / "x", raiz), (raiz, raiz), (raiz, raiz / "x"), (raiz.parent / "y", raiz)]:
        assert publicar.dentro_de(a, b) == a.is_relative_to(b) if hasattr(a, "is_relative_to") else True


def teste_fallback_com_tudo_excluido_nao_e_fila_completa():
    con = banco.conectar(":memory:")
    api_falsa({"Aberto": [cru(1, "Aberto")], "Em atendimento": [cru(3, "Em atendimento")]}, [])
    coletar.coletar(con, dias=7, agora=AGORA)
    excluidos = ch.STATUS_EXCLUIDOS
    ch.STATUS_EXCLUIDOS = ["Aberto", "Em atendimento", "Fechado"]   # sobra só o fallback ('Aberto')
    try:
        for _ in range(3):
            r = coletar.coletar(con, dias=7, agora=AGORA)
    finally:
        ch.STATUS_EXCLUIDOS = excluidos
    completa = con.execute("SELECT fila_completa FROM coletas WHERE id = ?", (r["coleta_id"],)).fetchone()[0]
    assert completa == 0 and r["fechados_inferidos"] == 0


def teste_carga_inicial_em_pedacos():
    con = banco.conectar(":memory:")
    pedidos = api_falsa({"Aberto": []}, [])
    coletar.coletar(con, dias=60, agora=AGORA)
    periodo = [p for e, p in pedidos if e == "showTicketsPerPeriod"]
    assert periodo == [{"start": "2026-08-03", "end": "2026-09-01"},
                       {"start": "2026-09-02", "end": "2026-10-01"}], periodo


def teste_falha_da_api_nao_fecha_nada_nem_vaza_chave():
    fila = {"Aberto": [cru(1, "Aberto")], "Em atendimento": [cru(3, "Em atendimento")]}
    con = banco.conectar(":memory:")
    api_falsa(fila, [])
    coletar.coletar(con, dias=7, agora=AGORA)
    for falhar in (["Em atendimento"], ["showTicketsPerPeriod"], ["listTicketStatus"]):
        api_falsa(fila, [], falhar=falhar)
        r = coletar.coletar(con, dias=7, agora=AGORA)
        assert abertos(con) == {"1", "3"}, falhar
        assert r["avisos"] and r["fechados_inferidos"] == 0, falhar
        gravado = con.execute("SELECT avisos FROM coletas WHERE id = ?", (r["coleta_id"],)).fetchone()[0]
        assert CHAVE not in gravado and CHAVE not in str(r["avisos"]) and "***" in gravado, falhar


if __name__ == "__main__":
    testes = [(n, f) for n, f in sorted(globals().items()) if n.startswith("teste_") and callable(f)]
    falhas = 0
    for nome, funcao in testes:
        try:
            funcao()
            print(f"ok     {nome}")
        except Exception as e:  # mostra todas as falhas, não só a primeira
            falhas += 1
            print(f"FALHOU {nome}: {type(e).__name__}: {e}")
    print(f"\n{len(testes) - falhas} de {len(testes)} passaram")
    sys.exit(1 if falhas else 0)
