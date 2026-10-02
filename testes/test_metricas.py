"""Testes das métricas e da exportação por público (metricas.py, exportar.py).

Sem rede e sem tocar em dados/: banco em memória, chave falsa, JSONs de
licenças/agenda numa pasta temporária.

Uso:
    python testes/test_metricas.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
CHAVE = "chave-falsa-de-teste"
os.environ["HELPDESK_API_KEY"] = CHAVE          # load_dotenv não sobrescreve
os.environ["HELPDESK_STATUS_EXCLUIDOS"] = "Fechado"

import banco  # noqa: E402
import exportar  # noqa: E402
import metricas  # noqa: E402

ch = metricas.ch
assert ch.API_KEY == CHAVE, "o teste não pode rodar com a chave real"
ch.DEV_NAMES = ["Dev Um", "Dev Dois"]
ch.STATUS_DEV = ["Com o Desenvolvedor", "Pendente"]
ch.STATUS_TRABALHO = ["Com o Desenvolvedor", "Em atendimento"]
ch.EQUIPES_DEV = []
ch.LOCAIS_INTERNOS = ["Sino"]

AGORA = datetime(2026, 10, 1, 16, 0)
SEGREDOS = ("Fulana Solicitante", "ASSUNTO-SECRETO", "Dev Um", "Tecnico Suporte", "Fabio")


def reg(cid, status="Aberto", **extra) -> dict:
    base = {"id": str(cid), "status": status, "aberto": int(status != "Fechado"), "tecnico": "Tecnico Suporte",
            "sistema": "Site", "natureza": "Corretivo", "local": "Câmara X", "assunto": "ASSUNTO-SECRETO",
            "solicitante": "Fulana Solicitante", "aberto_em": "2026-09-30T09:00", "atendimento_diario": 0}
    return {**base, **extra}


def banco_de_teste():
    con = banco.conectar(":memory:")
    registros = [
        reg(1, aberto_em="2026-09-30T09:00"),                                      # 1 dia
        reg(2, "Com o Desenvolvedor", tecnico="Dev Um Sobrenome", sistema="Siscam 9",
            aberto_em="2026-06-01T09:00"),                                         # 122 dias
        reg(3, "Pendente", tecnico="Dev Um Sobrenome", aberto_em="2026-09-10T09:00", natureza="Evolutivo"),
        reg(4, "Em atendimento", tecnico="Dev Dois", aberto_em="2026-09-01T09:00"),
        reg(5, "Fechado", aberto_em="2026-09-29T08:00", fechado_em="2026-09-30T08:00"),          # 24 h
        reg(6, "Fechado", aberto_em="2026-09-20T08:00", fechado_em="2026-09-30T08:00", sistema="Siscam 9"),
        reg(7, "Fechado", aberto_em="2026-09-30T10:00", fechado_em="2026-09-30T10:05",
            atendimento_diario=1, atendimento_tecnico="Fabio", local="Sino"),
        reg(8, "Aberto", aberto_em="2026-09-30T11:00", atendimento_diario=1, local="Sino"),     # ainda não fechou
        reg(9, "Fechado", aberto_em="2026-09-30T12:00", fechado_em="2026-10-01T09:00", local="Sino - Compilação"),
        reg(10, "Fechado", aberto_em="2026-09-30T12:00", fechado_em="2026-10-01T10:00", local="Sinop CM"),
        reg(11, "Fechado", aberto_em="2026-07-01T12:00", fechado_em="2026-07-02T10:00"),         # fora da janela
    ]
    banco.aplicar_coleta(con, momento="2026-10-01T15:00:00", registros=registros, fila_completa=True,
                         status_consultados=["Aberto"], modo="completa", requisicoes=19)
    banco.aplicar_coleta(con, momento="2026-10-01T15:50:00", registros=[reg(1, "Em atendimento")],
                         fila_completa=False, fila_consultada=False, status_consultados=[], modo="rapida",
                         avisos=["limite de requisições do Milldesk atingido após 1 chamadas"])
    return con


def teste_estoque():
    e = metricas.estoque(banco_de_teste(), AGORA)
    assert e["total_abertos"] == 5                       # 1, 2, 3, 4 e o atendimento ainda aberto (8)
    assert e["idade"] == {"ate_7_dias": 2, "de_8_a_30_dias": 2, "de_31_a_90_dias": 0,
                          "mais_de_90_dias": 1, "sem_data": 0}
    assert sum(e["idade"].values()) == e["total_abertos"]
    assert e["por_sistema"]["Siscam 9"] == {"abertos": 1, "acima_de_90_dias": 1, "mais_antigo_dias": 122,
                                            "por_natureza": {"Corretivo": 1}}
    assert sum(s["abertos"] for s in e["por_sistema"].values()) == e["total_abertos"]
    assert sum(e["por_status"].values()) == e["total_abertos"]
    dev = e["desenvolvimento"]
    assert dev["em_status_dev"] == 2 and dev["atribuidos_a_devs"] == 3
    assert dev["por_dev"]["Dev Um"]["abertos"] == 2 and dev["por_dev"]["Dev Um"]["em_trabalho"] == 1
    assert dev["por_dev"]["Dev Dois"] == {"abertos": 1, "acima_de_90_dias": 0, "mais_antigo_dias": 30,
                                          "em_trabalho": 1, "equipe": ch.EQUIPE_DEV_PADRAO,
                                          "por_sistema": {"Site": 1}, "por_status": {"Em atendimento": 1}}
    # a matriz dono x estágio soma a fila inteira, sem contar ninguém duas vezes
    assert sum(sum(l.values()) for l in e["tecnico_por_status"].values()) == e["total_abertos"]
    assert [c["id"] for c in e["mais_antigos"]][:2] == ["2", "4"]


def teste_fluxo_data_pelo_dia_do_fato():
    f = metricas.fluxo(banco_de_teste(), AGORA)
    assert f["inicio"] == "2026-09-02" and f["fim"] == "2026-10-01" and len(f["por_dia"]) == 30
    d30, d01 = f["por_dia"]["2026-09-30"], f["por_dia"]["2026-10-01"]
    # 30/09: abertos 1 e 10 (clientes; "Sinop CM" NÃO é interno) e 9 (interno: "Sino - Compilação")
    assert d30["criados_clientes"] == 2 and d30["criados_internos"] == 1
    assert d30["fechados"] == 2                          # 5 e 6 fecharam em 30/09
    assert d30["atendimentos"] == 1 and d30["atendimentos_por_tecnico"] == {"Fabio": 1}   # o 8 ainda está aberto
    assert d01["fechados"] == 2 and d01["criados_clientes"] == 0                           # 9 e 10 fecharam hoje
    assert f["por_dia"]["2026-09-29"]["criados_clientes"] == 1
    assert f["criados_por_cliente"]["Sinop CM"]["total"] == 1 and "Sino" not in f["criados_por_cliente"]
    assert sum(c["total"] for c in f["criados_por_cliente"].values()) == \
        sum(d["criados_clientes"] for d in f["por_dia"].values()) == sum(f["criados_por_sistema"].values())


def teste_interno_e_nome_exato_ou_com_separador():
    for local, esperado in [("Sino", True), ("sino", True), ("Sino - Compilação", True), ("SINO/TI", True),
                            ("Sinop CM", False), ("Casino CM", False), ("", False), (None, False)]:
        assert metricas.eh_interno(local) is esperado, local


def teste_resolucao_sem_atendimento_diario():
    r = metricas.resolucao(banco_de_teste(), AGORA)
    # 5 (24 h), 6 (240 h), 9 (21 h), 10 (22 h); o 7 é atendimento e o 11 fechou fora da janela
    assert r["fechados"] == 4 and r["mediana_horas"] == 23.0
    assert r["no_mesmo_dia"] == 3 and r["mais_de_7_dias"] == 1
    assert r["por_sistema"]["Siscam 9"] == {"fechados": 1, "mediana_horas": 240.0, "no_mesmo_dia": 0,
                                            "mais_de_7_dias": 1}
    vazio = metricas.resolucao(banco.conectar(":memory:"), AGORA)
    assert vazio["fechados"] == 0 and vazio["mediana_horas"] is None


def teste_frescor():
    f = metricas.frescor(banco_de_teste(), AGORA)
    assert f["ultima_coleta"] == "2026-10-01T15:50:00" and f["ultima_coleta_min"] == 10
    assert f["ultima_coleta_modo"] == "rapida"
    assert f["ultima_completa"] == "2026-10-01T15:00:00" and f["ultima_completa_min"] == 60
    assert f["limite_atingido"] is True and f["avisos_na_ultima_coleta"] == 1
    vazio = metricas.frescor(banco.conectar(":memory:"), AGORA)
    assert vazio["ultima_coleta"] is None and vazio["ultima_completa_min"] is None


def teste_eventos_recentes():
    ev = metricas.eventos_recentes(banco_de_teste())
    assert len(ev) == 1 and ev[0]["tipo"] == "status" and ev[0]["chamado_id"] == "1"
    assert ev[0]["de"] == "Aberto" and ev[0]["para"] == "Em atendimento"


def pasta_com(licencas, agenda) -> Path:
    pasta = Path(tempfile.mkdtemp())
    for nome, conteudo in (("licencas.json", licencas), ("agenda.json", agenda)):
        if conteudo is not None:
            (pasta / nome).write_text(conteudo if isinstance(conteudo, str) else json.dumps(conteudo),
                                      encoding="utf-8")
    return pasta


LICENCAS = {"coletado_em": "2026-10-01T08:12:00", "janela_dias": 30, "vencidas_antigas_total": 4,
            "vencendo_alem_da_janela": 2, "ignoradas_homolog_teste": 1,
            "vencendo_em_breve": [{"cliente": "Câmara Y", "sistema": "Siscam", "vencimento": "10/10/2026",
                                   "dias": 9, "responsavel": "Fulana Solicitante"}],
            "vencidas_recentes": []}
AGENDA = {"coletado_em": "2026-10-01T08:12:00", "data": "2026-10-01", "minutos_ocupados": 90,
          "eventos": [{"inicio": "09:00", "fim": "10:00", "titulo": "Reunião", "calendarios": ["Equipe"],
                       "meu": True, "minutos": 60}],
          "dia_inteiro": [{"titulo": "Férias", "calendarios": ["Equipe"]}]}


def exportado(licencas=LICENCAS, agenda=AGENDA, ranking=True, con="banco"):
    exportar.DADOS = pasta_com(licencas, agenda)
    exportar.MOSTRAR_RANKING = ranking
    return exportar.montar(AGORA, banco_de_teste() if con == "banco" else con)


def teste_quem_ve_o_que():
    p = exportado()
    assert set(p) == {"suporte", "desenvolvimento", "diretor", "licencas"}
    texto = {nome: json.dumps(c, ensure_ascii=False) for nome, c in p.items()}
    # suporte e desenvolvimento veem o chamado inteiro
    for nome in ("suporte", "desenvolvimento"):
        assert "ASSUNTO-SECRETO" in texto[nome] and "Fulana Solicitante" in texto[nome]
        assert set(p[nome]) == {"publico", "gerado_em", "chamados"}
    # diretor: agregados e nomes da equipe, nunca assunto nem solicitante
    assert "ASSUNTO-SECRETO" not in texto["diretor"] and "Fulana Solicitante" not in texto["diretor"]
    assert "mais_antigos" not in texto["diretor"] and "eventos" not in p["diretor"]["chamados"]
    assert "Dev Um" in texto["diretor"] and "Fabio" in texto["diretor"]
    assert p["diretor"]["chamados"]["estoque"]["total_abertos"] == 5
    assert p["diretor"]["agenda"]["eventos"][0]["titulo"] == "Reunião"
    assert "minutos_ocupados" not in texto["diretor"] and '"meu"' not in texto["diretor"]
    assert p["diretor"]["licencas_resumo"]["vencendo_em_breve"] == 1 and "Câmara Y" not in texto["diretor"]
    # licenças: só licenças, e só os campos permitidos
    assert set(p["licencas"]) == {"publico", "gerado_em", "licencas"}
    assert p["licencas"]["licencas"]["vencendo_em_breve"] == [
        {"cliente": "Câmara Y", "sistema": "Siscam", "vencimento": "10/10/2026", "dias": 9}]
    assert not any(s in texto["licencas"] for s in SEGREDOS)
    for t in texto.values():
        assert CHAVE not in t


def teste_diretor_sem_nomes_quando_a_flag_esta_desligada():
    try:
        texto = json.dumps(exportado(ranking=False)["diretor"], ensure_ascii=False)
    finally:
        exportar.MOSTRAR_RANKING = True
    assert not any(s in texto for s in SEGREDOS), [s for s in SEGREDOS if s in texto]
    assert '"atendimentos": 1' in texto                  # o número fica; o nome sai


def teste_recorte_do_diretor_e_lista_de_permissao():
    """Bloco novo em metricas.py não pode chegar ao diretor sozinho."""
    chamados = exportar.bloco_chamados(banco_de_teste(), AGORA)
    chamados["estoque"]["CAMPO_NOVO"] = "Fulana Solicitante"
    chamados["fluxo"]["CAMPO_NOVO"] = "Fulana Solicitante"
    chamados["CAMPO_NOVO"] = "Fulana Solicitante"
    assert "Fulana Solicitante" not in json.dumps(exportar.recorte_diretor(chamados), ensure_ascii=False)


def teste_fonte_ausente_ou_corrompida_nao_derruba():
    p = exportado(licencas=None, agenda="{isto nao e json")
    assert p["licencas"]["licencas"] == {"indisponivel": True}
    assert p["diretor"]["agenda"] == {"indisponivel": True}
    assert p["diretor"]["licencas_resumo"] == {"indisponivel": True}
    assert p["suporte"]["chamados"]["estoque"]["total_abertos"] == 5
    p = exportado(licencas=[1, 2], agenda={"erro": "token revogado", "eventos": [{"titulo": "x"}]})
    assert p["licencas"]["licencas"] == {"indisponivel": True} and p["diretor"]["agenda"] == {"indisponivel": True}

    class Quebrado:
        def execute(self, *a, **k):
            raise RuntimeError("database disk image is malformed")
    p = exportado(con=Quebrado())
    assert p["suporte"]["chamados"] == {"indisponivel": True}
    assert p["diretor"]["chamados"] == {"indisponivel": True}
    assert p["licencas"]["licencas"]["janela_dias"] == 30


def teste_gravar_e_atomico_e_json_valido():
    pasta = Path(tempfile.mkdtemp()) / "site"
    caminhos = exportar.gravar(exportado(), pasta)
    assert sorted(c.name for c in caminhos) == ["desenvolvimento.json", "diretor.json", "licencas.json",
                                                "suporte.json"]
    assert sorted(c.name for c in pasta.iterdir()) == sorted(c.name for c in caminhos)   # nenhum .tmp sobrando
    for c in caminhos:
        assert json.loads(c.read_text(encoding="utf-8"))["publico"] == c.stem


def banco_com(registros):
    con = banco.conectar(":memory:")
    banco.aplicar_coleta(con, momento="2026-10-01T15:00:00", registros=registros, fila_completa=True,
                         status_consultados=["Aberto"], modo="completa")
    return con


def teste_regras_de_dev_e_idade_nos_casos_de_borda():
    ch.DEV_NAMES = ["Ana", "Ana Paula"]                   # nomes que se sobrepõem: vence o primeiro da config
    try:
        e = metricas.estoque(banco_com([
            reg(1, "Em atendimento externo", tecnico="Ana Paula Souza", aberto_em="2026-09-01T09:00"),
            reg(2, "Em atendimento", tecnico="Ana Paula Souza", aberto_em="2026-05-01T09:00"),
            reg(3, "Aberto", tecnico="Ana Paula Souza", aberto_em="2026-12-25T09:00"),   # data no futuro
            reg(4, None, tecnico=None, sistema=None, aberto_em=None),
        ]), AGORA)
    finally:
        ch.DEV_NAMES = ["Dev Um", "Dev Dois"]
    ana = e["desenvolvimento"]["por_dev"]["Ana"]
    assert ana["abertos"] == 3 and e["desenvolvimento"]["por_dev"]["Ana Paula"]["abertos"] == 0
    assert ana["em_trabalho"] == 1                        # exato: "Em atendimento externo" não conta
    assert ana["mais_antigo_dias"] == 153 and ana["acima_de_90_dias"] == 1
    assert e["idade"]["ate_7_dias"] == 1 and e["idade"]["sem_data"] == 1   # futuro vira idade 0
    assert e["por_sistema"]["Outros"]["abertos"] == 1 and e["por_status"]["(sem informação)"] == 1
    assert e["tecnico_por_status"]["(sem técnico)"] == {"(sem informação)": 1}
    for bloco in (e["idade"], e["por_status"], e["por_tecnico"], e["por_natureza"]):
        assert sum(bloco.values()) == e["total_abertos"] == 4


def teste_fluxo_e_resolucao_nos_casos_de_borda():
    con = banco_com([
        reg(1, "Fechado", aberto_em="2026-09-20T08:00", fechado_em="2026-09-27T08:00"),    # 168 h exatas
        reg(2, "Fechado", aberto_em="2026-09-20T08:00", fechado_em="2026-09-27T09:00"),    # 169 h
        reg(3, "Fechado", aberto_em="2026-09-28T08:00", fechado_em="2026-09-27T08:00"),    # datas invertidas
        reg(4, "Fechado", aberto_em="2026-09-28T08:00", fechado_em=None),                  # fechado sem data
        reg(5, "Fechado", aberto_em="2026-09-02T00:00", fechado_em="2026-09-02T23:59", local="CAMARA  X"),
        reg(6, "Fechado", aberto_em="2026-09-01T23:59", fechado_em="2026-09-01T23:59"),    # véspera da janela
        reg(7, "Fechado", aberto_em="2026-09-28T08:00", fechado_em="garbage"),
    ])
    # um chamado que o banco diz aberto mas ainda carrega data de fechamento não é "fechado"
    con.execute("INSERT INTO chamados (id, status, aberto, aberto_em, fechado_em, atendimento_diario, local, "
                "primeiro_visto, ultimo_visto) VALUES ('8', 'Aberto', 1, '2026-09-10T08:00', '2026-09-27T10:00', "
                "0, 'Sino', 'x', 'x')")
    r = metricas.resolucao(con, AGORA)
    assert r["fechados"] == 3 and r["mais_de_7_dias"] == 1 and r["fechados_sem_data"] == 1   # 1, 2 e 5
    f = metricas.fluxo(con, AGORA)
    assert f["por_dia"]["2026-09-27"]["fechados"] == 3          # 1, 2 e o 3 (invertido conta como fechado no dia)
    assert f["por_dia"]["2026-09-02"] == {"criados_clientes": 1, "criados_internos": 0, "fechados": 1,
                                          "atendimentos": 0, "atendimentos_por_tecnico": {}}
    assert "2026-09-01" not in f["por_dia"]
    # "Câmara X" e "CAMARA  X" são o mesmo cliente; vale a primeira grafia vista
    assert list(f["criados_por_cliente"]) == ["Câmara X"] and f["criados_por_cliente"]["Câmara X"]["total"] == 6
    assert sum(d["criados_clientes"] for d in f["por_dia"].values()) == 6


def teste_frescor_tolera_relogio_e_avisos_estranhos():
    con = banco_com([reg(1)])
    con.execute("UPDATE coletas SET avisos = '123', momento = '2026-10-01T18:00:00'")
    f = metricas.frescor(con, AGORA)                             # coleta "no futuro" e avisos que não são lista
    assert f["ultima_coleta_min"] == 0 and f["avisos_na_ultima_coleta"] == 0


def teste_recorte_do_diretor_fecha_todos_os_niveis():
    for ranking in (True, False):
        exportar.MOSTRAR_RANKING = ranking
        try:
            chamados = exportar.bloco_chamados(banco_de_teste(), AGORA)
            e, f, r = chamados["estoque"], chamados["fluxo"], chamados["resolucao"]
            alvos = [chamados["frescor"], e, e["idade"], e["por_status"], e["por_natureza"],
                     e["desenvolvimento"], e["desenvolvimento"]["em_status_dev_por_sistema"], f,
                     f["criados_por_sistema"], f["criados_por_cliente"], r, r["por_sistema"],
                     *e["por_sistema"].values(), *f["por_dia"].values(), *f["criados_por_cliente"].values(),
                     *r["por_sistema"].values(), *e["desenvolvimento"]["por_dev"].values(),
                     *(b["por_natureza"] for b in e["por_sistema"].values()),
                     *(b["por_sistema"] for b in f["criados_por_cliente"].values()),
                     *(b["atendimentos_por_tecnico"] for b in f["por_dia"].values()),
                     *(b["por_status"] for b in e["desenvolvimento"]["por_dev"].values())]
            for alvo in alvos:
                alvo["CAMPO_NOVO"] = "Fulana Solicitante"
            texto = json.dumps(exportar.recorte_diretor(chamados), ensure_ascii=False)
        finally:
            exportar.MOSTRAR_RANKING = True
        assert "Fulana Solicitante" not in texto and "CAMPO_NOVO" not in texto, ranking
        assert "por_tecnico\"" not in texto.replace("atendimentos_por_tecnico", "") and "tecnico_por_status" not in texto


def teste_agenda_some_inteira_quando_a_flag_esconde_nomes():
    try:
        p = exportado(ranking=False)
    finally:
        exportar.MOSTRAR_RANKING = True
    assert p["diretor"]["agenda"] == {"oculta": True}
    assert "Reunião" not in json.dumps(p, ensure_ascii=False) and "Equipe" not in json.dumps(p, ensure_ascii=False)


def teste_flag_de_nomes_erra_para_o_lado_de_esconder():
    antes = os.environ.get("DASHBOARD_MOSTRAR_RANKING")
    try:
        for valor, mostra in [("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
                              ("nao", False), ("não", False), ("falso", False), ("'false'", False),
                              ("hide", False), ("oculto", False), ("", False), ("false # comentário", False),
                              ("true # comentário", False), ("tru", False),
                              ("true", True), ("TRUE", True), (" true ", True), ('"true"', True), ("1", True),
                              ("sim", True), ("yes", True), ("on", True)]:
            os.environ["DASHBOARD_MOSTRAR_RANKING"] = valor
            assert exportar._flag_ranking() is mostra, valor
        os.environ.pop("DASHBOARD_MOSTRAR_RANKING")
        carregar, exportar.RAIZ = exportar.RAIZ, Path(tempfile.mkdtemp())   # sem .env: a variável não existe
        try:
            assert exportar._flag_ranking() is True
        finally:
            exportar.RAIZ = carregar
    finally:
        os.environ.pop("DASHBOARD_MOSTRAR_RANKING", None)
        if antes is not None:
            os.environ["DASHBOARD_MOSTRAR_RANKING"] = antes


def teste_banco_vazio_ou_ausente_e_indisponivel_e_nao_zero_chamados():
    p = exportado(con=banco.conectar(":memory:"))                # banco sem nenhuma coleta
    assert p["suporte"]["chamados"] == {"indisponivel": True} and p["diretor"]["chamados"] == {"indisponivel": True}
    arquivo = banco.ARQUIVO
    banco.ARQUIVO = Path(tempfile.mkdtemp()) / "nao_existe" / "briefing.db"
    try:
        p = exportado(con=None)
        assert p["suporte"]["chamados"] == {"indisponivel": True}
        assert not banco.ARQUIVO.exists() and not banco.ARQUIVO.parent.exists()   # não cria banco vazio
    finally:
        banco.ARQUIVO = arquivo


def teste_campo_permitido_so_leva_valor_simples():
    """Lista ou dicionário pendurado num campo da lista de permissão não vai de carona."""
    carona = [{"x": "Fulana Solicitante"}]
    chamados = exportar.bloco_chamados(banco_de_teste(), AGORA)
    e, f = chamados["estoque"], chamados["fluxo"]
    chamados["frescor"]["ultima_coleta"] = carona
    e["total_abertos"] = e["tipo"] = carona
    e["desenvolvimento"]["em_status_dev"] = e["desenvolvimento"]["atribuidos_a_devs"] = carona
    f["inicio"] = f["fim"] = f["dias"] = f["tipo"] = carona
    f["por_dia"]["2026-09-30"]["fechados"] = carona
    chamados["resolucao"]["mediana_horas"] = carona
    d = exportar.recorte_diretor(chamados)
    assert "Fulana Solicitante" not in json.dumps(d, ensure_ascii=False)
    assert "total_abertos" not in d["estoque"] and "ultima_coleta" not in d["frescor"]
    assert d["fluxo"]["por_dia"]["2026-09-30"]["criados_clientes"] == 2      # o vizinho simples passa

    p = exportado(licencas={**LICENCAS, "janela_dias": carona, "coletado_em": {"x": "Fulana Solicitante"},
                            "vencidas_antigas_total": carona,
                            "vencendo_em_breve": [{"cliente": carona, "sistema": "Siscam", "dias": 9}, "lixo"]})
    assert "Fulana Solicitante" not in json.dumps([p["licencas"], p["diretor"]], ensure_ascii=False)
    assert p["licencas"]["licencas"]["vencendo_em_breve"] == [
        {"cliente": None, "sistema": "Siscam", "vencimento": None, "dias": 9}]
    assert p["licencas"]["licencas"]["janela_dias"] is None


def teste_sumido_da_fila_conta_como_aberto_e_fica_a_vista():
    con = banco_com([reg(1), reg(2), reg(3)])
    assert metricas.estoque(con, AGORA)["aguardando_confirmacao"] == 0
    banco.aplicar_coleta(con, momento="2026-10-01T15:30:00", registros=[reg(1)], fila_completa=True,
                         status_consultados=["Aberto"], modo="completa")       # 2 e 3 não vieram: 1ª ausência
    e = metricas.estoque(con, AGORA)
    assert e["total_abertos"] == 3 and e["aguardando_confirmacao"] == 2
    d = exportar.recorte_diretor(exportar.bloco_chamados(con, AGORA))
    assert d["estoque"]["aguardando_confirmacao"] == 2
    banco.aplicar_coleta(con, momento="2026-10-01T15:40:00", registros=[reg(1)], fila_completa=True,
                         status_consultados=["Aberto"], modo="completa")       # 2ª ausência: fecharam
    e = metricas.estoque(con, AGORA)
    assert e["total_abertos"] == 1 and e["aguardando_confirmacao"] == 0


def teste_agenda_so_leva_valor_simples():
    carona = [{"x": "Fulana Solicitante"}]
    agenda = {"coletado_em": carona, "data": {"x": "Fulana Solicitante"},
              "eventos": [{"inicio": carona, "titulo": "Reunião", "local": {"x": "Fulana Solicitante"},
                           "calendarios": ["Equipe", carona, 7]}],
              "dia_inteiro": [{"titulo": carona, "calendarios": "Fulana Solicitante"}]}
    ag = exportado(agenda=agenda)["diretor"]["agenda"]
    assert "Fulana Solicitante" not in json.dumps(ag, ensure_ascii=False)
    assert ag["eventos"][0]["titulo"] == "Reunião" and ag["eventos"][0]["calendarios"] == ["Equipe"]
    assert ag["eventos"][0]["inicio"] is None and ag["dia_inteiro"] == [{"titulo": None, "calendarios": []}]
    assert exportado()["diretor"]["agenda"]["eventos"][0]["calendarios"] == ["Equipe"]   # o normal não muda


def teste_leitura_do_banco_abre_e_fecha_a_transacao():
    con = banco_de_teste()
    vistos = []
    original = metricas.estoque

    def espia(c, agora):
        vistos.append(c.in_transaction)
        return original(c, agora)
    metricas.estoque = espia
    try:
        exportar.bloco_chamados(con, AGORA)
    finally:
        metricas.estoque = original
    assert vistos == [True] and not con.in_transaction     # tudo lido num instante só, e nada fica preso

    def quebra(c, agora):
        raise RuntimeError("falha no meio")
    metricas.estoque = quebra
    try:
        exportar.bloco_chamados(con, AGORA)
    except RuntimeError:
        pass
    else:
        raise AssertionError("deveria propagar a falha")
    finally:
        metricas.estoque = original
    assert not con.in_transaction
    vazio = banco.conectar(":memory:")
    try:
        exportar.bloco_chamados(vazio, AGORA)
    except LookupError:
        pass
    assert not vazio.in_transaction


def teste_cliente_exibe_a_grafia_do_menor_id_em_qualquer_ordem():
    for ordem in ([2, 10, 1], [1, 2, 10], [10, 1, 2]):
        grafia = {1: "Câmara X", 2: "CAMARA  X", 10: "camara x"}
        f = metricas.fluxo(banco_com([reg(i, local=grafia[i]) for i in ordem]), AGORA)
        assert list(f["criados_por_cliente"]) == ["Câmara X"], ordem
    # contagens em ordem decrescente
    f = metricas.fluxo(banco_com([reg(1, local="A"), reg(2, local="B", sistema="Siscam 9"),
                                  reg(3, local="B", sistema="Siscam 9")]), AGORA)
    assert list(f["criados_por_cliente"]) == ["B", "A"] and list(f["criados_por_sistema"]) == ["Siscam 9", "Site"]


def teste_data_futura_nao_entra_em_dia_nenhum_nem_derruba():
    con = banco_com([
        reg(1, aberto_em="2026-12-25T09:00"),                                             # aberto "no futuro"
        reg(2, "Fechado", aberto_em="2026-09-30T09:00", fechado_em="2026-12-25T09:00"),   # fechado "no futuro"
        reg(3, "Fechado", aberto_em="2026-12-24T09:00", fechado_em="2026-12-25T09:00"),
    ])
    f = metricas.fluxo(con, AGORA)
    assert max(f["por_dia"]) == "2026-10-01" == f["fim"]
    assert sum(d["criados_clientes"] for d in f["por_dia"].values()) == 1                 # só o 2
    assert sum(d["fechados"] for d in f["por_dia"].values()) == 0
    assert sum(c["total"] for c in f["criados_por_cliente"].values()) == 1
    assert metricas.estoque(con, AGORA)["mais_antigos"][0]["dias_aberto"] == 0


def teste_aviso_diz_quando_o_que_falta_e_a_chave():
    import contextlib
    import io

    def sem_chave():
        try:
            {}["HELPDESK_API_KEY"]
        except KeyError as e:
            raise exportar.ConfigAusente(f"{e.args[0]} não está no .env") from None
    original, exportar._metricas = exportar._metricas, sem_chave
    saida = io.StringIO()
    try:
        with contextlib.redirect_stdout(saida):
            p = exportado()
    finally:
        exportar._metricas = original
    assert p["suporte"]["chamados"] == {"indisponivel": True}
    assert p["licencas"]["licencas"]["janela_dias"] == 30                    # o resto sai
    assert "HELPDESK_API_KEY não está no .env" in saida.getvalue() and "banco" not in saida.getvalue()
    assert CHAVE not in saida.getvalue()


def teste_temporario_nasce_fora_da_pasta_de_saida():
    raiz = Path(tempfile.mkdtemp())
    pasta = raiz / "site"
    original, origens = os.replace, []

    def espia(a, b):
        origens.append(Path(a))
        return original(a, b)
    os.replace = espia
    try:
        exportar.gravar({"diretor": {"publico": "diretor"}}, pasta)
    finally:
        os.replace = original
    assert [o.parent for o in origens] == [raiz]              # o IIS nunca enxerga o arquivo pela metade


def teste_gravar_nao_deixa_temporario_na_pasta_nem_quando_falha():
    raiz = Path(tempfile.mkdtemp())
    pasta = raiz / "site"
    exportar.gravar({"diretor": {"publico": "diretor"}}, pasta)
    original = os.replace

    def falha(a, b):
        raise PermissionError("arquivo em uso")
    os.replace = falha
    try:
        exportar.gravar({"suporte": {"publico": "suporte", "segredo": "Fulana Solicitante"}}, pasta)
    except PermissionError:
        pass
    else:
        raise AssertionError("deveria propagar a falha")
    finally:
        os.replace = original
    assert sorted(c.name for c in pasta.iterdir()) == ["diretor.json"]       # nada novo na pasta de saída
    assert sorted(c.name for c in raiz.iterdir()) == ["site"]                # e nenhum temporário esquecido
    for nome in ("../fora", "a/b", "a.b", ""):
        try:
            exportar.gravar({nome: {}}, pasta)
        except ValueError:
            continue
        raise AssertionError(nome)


if __name__ == "__main__":
    testes = [(n, f) for n, f in sorted(globals().items()) if n.startswith("teste_") and callable(f)]
    falhas = 0
    for nome, funcao in testes:
        try:
            funcao()
            print(f"ok     {nome}")
        except Exception as e:  # mostra todas as falhas, não só a primeira
            falhas += 1
            print(f"FALHOU {nome}: {type(e).__name__}: {e!r}")
    print(f"\n{len(testes) - falhas} de {len(testes)} passaram")
    sys.exit(1 if falhas else 0)
