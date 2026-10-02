"""Testes da rodada agendada e da publicação do site (agendada.py, publicar_site.py).

Sem rede, sem API e sem tocar em dados/ nem em pasta de servidor: banco e
destino em pastas temporárias.

Uso:
    python testes/test_agendada.py
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.pop("SITE_DIR", None)

import agendada  # noqa: E402
import banco  # noqa: E402
import publicar_site  # noqa: E402

agendada.EXPEDIENTE = (time(7, 0), time(19, 0))
agendada.SILENCIO = (time(8, 0), time(8, 30))
agendada.COMPLETA_MINUTOS, agendada.RECUO_MINUTOS, agendada.FONTES_MINUTOS = 60, 30, 60

SEXTA = datetime(2026, 10, 2, 10, 0)          # sexta-feira, 10:00


def em(hora: str, dia: int = 2) -> datetime:
    h, m = hora.split(":")
    return datetime(2026, 10, dia, int(h), int(m))


def estado(completa=None, limite=None, erro=None) -> dict:
    return {"ultima_completa": em(completa) if completa else None,
            "ultimo_limite": em(limite) if limite else None, "erro": erro}


def teste_decisao_do_milldesk():
    assert agendada.decidir(SEXTA, estado("09:30"))[0] == "rapida"
    assert agendada.decidir(SEXTA, estado("09:00"))[0] == "completa"
    assert agendada.decidir(SEXTA, estado("09:02"))[0] == "completa"       # folga do agendador
    assert agendada.decidir(SEXTA, estado("09:03"))[0] == "rapida"
    assert agendada.decidir(SEXTA, estado())[0] == "completa"
    assert agendada.decidir(SEXTA, {})[0] == "completa"
    # coleta "no futuro" (relógio acertado) conta como recém-feita, não dispara completa em série
    assert agendada.decidir(SEXTA, estado("11:00"))[0] == "rapida"


def teste_expediente_fim_de_semana_e_silencio():
    velho = {"ultima_completa": em("06:00", 1), "ultimo_limite": None, "erro": None}
    for hora, esperado in [("06:59", "nada"), ("07:00", "completa"), ("18:59", "completa"), ("19:00", "nada"),
                           ("08:00", "nada"), ("08:10", "nada"), ("08:29", "nada"), ("08:30", "completa")]:
        assert agendada.decidir(em(hora), velho)[0] == esperado, hora
    assert agendada.decidir(em("10:00", 3), velho) == ("nada", "fora do expediente")     # sábado
    assert agendada.decidir(em("10:00", 4), velho)[0] == "nada"                          # domingo
    assert agendada.decidir(em("10:00", 5), velho)[0] == "completa"                      # segunda
    silencio, agendada.SILENCIO = agendada.SILENCIO, None                                # janela desligada
    try:
        assert agendada.decidir(em("08:10"), velho)[0] == "completa"
    finally:
        agendada.SILENCIO = silencio


def teste_recuo_depois_do_limite():
    assert agendada.decidir(SEXTA, estado("08:30", limite="09:45"))[0] == "nada"          # 15 min depois do 429
    assert agendada.decidir(em("10:14"), estado("08:30", limite="09:45"))[0] == "nada"
    assert agendada.decidir(em("10:15"), estado("08:30", limite="09:45"))[0] == "completa"   # 30 min: tenta de novo
    assert agendada.decidir(SEXTA, estado("08:30"))[0] == "completa"


def teste_banco_ilegivel_nunca_vira_coleta_completa():
    """Erro de leitura não é "ainda não houve coleta": nesta rodada o Milldesk não é consultado."""
    assert agendada.decidir(SEXTA, estado(erro="OperationalError")) == (
        "nada", "o banco não pôde ser lido (OperationalError)")
    pasta = Path(tempfile.mkdtemp())
    (pasta / "lixo.db").write_text("isto não é um banco " * 50, encoding="utf-8")
    e = agendada.estado_do_banco(pasta / "lixo.db")
    assert e["erro"] and agendada.decidir(SEXTA, agendada.com_tentativas(e, {}))[0] == "nada"
    # banco travado por outra escrita
    arquivo = pasta / "travado.db"
    con = banco.conectar(arquivo)
    banco.aplicar_coleta(con, momento="2026-10-02T09:55:00", registros=[], fila_completa=True,
                         status_consultados=["Aberto"], modo="completa")
    con.execute("PRAGMA locking_mode = EXCLUSIVE")
    con.execute("BEGIN EXCLUSIVE")
    conectar = sqlite3.connect
    sqlite3.connect = lambda *a, **k: conectar(*a, **{**k, "timeout": 0.2})   # sem esperar 10 s no teste
    try:
        e = agendada.estado_do_banco(arquivo)
    finally:
        sqlite3.connect = conectar
        con.rollback()
        con.close()
    assert e["erro"] == "OperationalError" and agendada.decidir(SEXTA, e)[0] == "nada"
    assert agendada.decidir(SEXTA, agendada.estado_do_banco(arquivo))[0] == "rapida"      # destravou: volta ao normal


def teste_estado_do_banco():
    pasta = Path(tempfile.mkdtemp())
    arquivo = pasta / "briefing.db"
    assert agendada.estado_do_banco(arquivo) == {"ultima_completa": None, "ultimo_limite": None, "erro": None}
    assert not arquivo.exists()                                              # ler não cria o banco
    con = banco.conectar(arquivo)
    assert agendada.estado_do_banco(arquivo)["erro"] is None                 # criado, sem nenhuma coleta
    comum = dict(registros=[], fila_completa=True, status_consultados=["Aberto"])
    banco.aplicar_coleta(con, momento="2026-10-02T09:00:00", modo="completa", **comum)
    banco.aplicar_coleta(con, momento="2026-10-02T09:05:00", registros=[], fila_completa=False,
                         fila_consultada=False, status_consultados=[], modo="rapida")
    assert agendada.estado_do_banco(arquivo) == {"ultima_completa": em("09:00"), "ultimo_limite": None, "erro": None}
    # completa que veio INCOMPLETA também conta como tentativa: não repetir a cada 5 minutos
    banco.aplicar_coleta(con, momento="2026-10-02T10:00:00", registros=[], fila_completa=False,
                         status_consultados=[], modo="completa",
                         avisos=["limite de requisições do Milldesk atingido após 3 chamadas"])
    e = agendada.estado_do_banco(arquivo)
    assert e == {"ultima_completa": em("10:00"), "ultimo_limite": em("10:00"), "erro": None}
    assert agendada.decidir(em("10:05"), e)[0] == "nada" and agendada.decidir(em("10:30"), e)[0] == "rapida"
    assert agendada.decidir(em("11:00"), e)[0] == "completa"
    # uma coleta limpa DEPOIS do 429 (rodada à mão, por exemplo) não cancela o recuo
    banco.aplicar_coleta(con, momento="2026-10-02T10:04:00", registros=[], fila_completa=False,
                         fila_consultada=False, status_consultados=[], modo="rapida")
    con.close()
    e = agendada.estado_do_banco(arquivo)
    assert e["ultimo_limite"] == em("10:00") and agendada.decidir(em("10:05"), e)[0] == "nada"
    # banco antigo, SEM a coluna `modo`: vale fila_completa, e não é erro
    velho = pasta / "velho.db"
    c = sqlite3.connect(str(velho))
    c.execute("CREATE TABLE coletas (id INTEGER PRIMARY KEY, momento TEXT, fila_completa INTEGER, avisos TEXT)")
    c.execute("INSERT INTO coletas (momento, fila_completa) VALUES ('2026-10-01T15:49:27', 1)")
    c.execute("INSERT INTO coletas (momento, fila_completa) VALUES ('2026-10-01T15:55:31', 0)")
    c.commit()
    c.close()
    assert agendada.estado_do_banco(velho) == {"ultima_completa": datetime(2026, 10, 1, 15, 49, 27),
                                                "ultimo_limite": None, "erro": None}


def teste_tentativa_anotada_vale_mesmo_sem_linha_no_banco():
    """Completa que trava ou morre antes de gravar no banco não é repetida a cada 5 minutos."""
    do_banco = estado("09:00")
    assert agendada.decidir(SEXTA, agendada.com_tentativas(do_banco, {}))[0] == "completa"
    anotada = {"milldesk_completa": "2026-10-02T10:00:00"}
    for hora in ("10:05", "10:10", "10:55"):
        assert agendada.decidir(em(hora), agendada.com_tentativas(do_banco, anotada))[0] == "rapida", hora
    assert agendada.decidir(em("11:00"), agendada.com_tentativas(do_banco, anotada))[0] == "completa"
    # vale a mais recente das duas, e anotação ilegível não atrapalha
    assert agendada.com_tentativas(estado("10:30"), anotada)["ultima_completa"] == em("10:30")
    assert agendada.com_tentativas(estado(), anotada)["ultima_completa"] == em("10:00")
    for lixo in ({"milldesk_completa": "lixo"}, {"milldesk_completa": 7}, [], None):
        assert agendada.com_tentativas(do_banco, lixo)["ultima_completa"] == em("09:00")


def rodada_falsa(coletar_ok=True, gravar_ok=True, tentativas=None):
    """Roda agendada.rodada() com os passos trocados por espiões. Devolve (passos, tentativas gravadas)."""
    import contextlib
    import io
    passos, gravadas = [], []
    originais = (agendada.rodar, agendada.ler_tentativas, agendada.gravar_tentativas, agendada.estado_do_banco)
    agendada.rodar = lambda script, *a: passos.append((script, *a)) or (coletar_ok or script != "coletar.py")
    agendada.ler_tentativas = lambda: dict(tentativas or {})
    agendada.gravar_tentativas = lambda t: gravadas.append(dict(t)) or gravar_ok
    agendada.estado_do_banco = lambda: estado("09:00")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            agendada.rodada(SEXTA)
    finally:
        agendada.rodar, agendada.ler_tentativas, agendada.gravar_tentativas, agendada.estado_do_banco = originais
    return passos, gravadas


def teste_rodada_anota_antes_de_coletar_e_segue_quando_um_passo_falha():
    em_dia = {"licencas": "2026-10-02T09:50:00", "agenda": "2026-10-02T09:50:00"}
    passos, gravadas = rodada_falsa(coletar_ok=False, tentativas=em_dia)
    assert passos == [("coletar.py",), ("exportar.py",), ("publicar_site.py",)]       # coleta falhou, o resto roda
    assert gravadas[0]["milldesk_completa"] == "2026-10-02T10:00:00" == gravadas[0]["milldesk"]
    # a rodada seguinte lê a anotação e não repete a completa
    passos, _ = rodada_falsa(tentativas={**em_dia, **gravadas[0]})
    assert passos[0] == ("coletar.py", "--rapida")
    # sem conseguir anotar, a completa é rebaixada
    passos, _ = rodada_falsa(gravar_ok=False, tentativas=em_dia)
    assert passos[0] == ("coletar.py", "--rapida")
    # fontes vencidas rodam depois do Milldesk e antes de exportar
    passos, _ = rodada_falsa()
    assert [p[0] for p in passos] == ["coletar.py", "coletores/check_licencas.py", "coletores/check_agenda.py",
                                      "exportar.py", "publicar_site.py"]


def teste_trava_uma_rodada_por_vez():
    trava = Path(tempfile.mkdtemp()) / "sub" / "agendada.lock"
    assert agendada.pegar_trava(SEXTA, trava) is True and trava.exists()
    assert agendada.pegar_trava(SEXTA, trava) is False                        # outra rodada em execução
    agendada.soltar_trava(trava)
    assert not trava.exists() and agendada.pegar_trava(SEXTA, trava) is True
    futura = SEXTA.timestamp() + 3600                                         # relógio acertado para trás
    os.utime(str(trava), (futura, futura))
    assert agendada.pegar_trava(SEXTA, trava) is False
    velha = SEXTA.timestamp() - (agendada.TRAVA_VELHA_MINUTOS + 1) * 60       # rodada que morreu sem soltar
    os.utime(str(trava), (velha, velha))
    assert agendada.pegar_trava(SEXTA, trava) is True
    recente = SEXTA.timestamp() - 5 * 60
    os.utime(str(trava), (recente, recente))
    assert agendada.pegar_trava(SEXTA, trava) is False
    agendada.soltar_trava(trava)
    agendada.soltar_trava(trava)                                              # soltar duas vezes não quebra


def teste_fontes_contam_a_tentativa():
    assert agendada.fontes_a_coletar(SEXTA, {}) == ["licencas", "agenda"]
    t = {"licencas": "2026-10-02T09:30:00", "agenda": "2026-10-02T08:55:00"}
    assert agendada.fontes_a_coletar(SEXTA, t) == ["agenda"]
    assert agendada.fontes_a_coletar(SEXTA, {"licencas": "lixo", "agenda": None}) == ["licencas", "agenda"]
    assert agendada.fontes_a_coletar(SEXTA, ["nao", "e", "dict"]) == ["licencas", "agenda"]
    assert agendada.fontes_a_coletar(em("20:00"), {}) == [] and agendada.fontes_a_coletar(em("10:00", 3), {}) == []
    arquivo = Path(tempfile.mkdtemp()) / "sub" / "agendada.json"
    assert agendada.ler_tentativas(arquivo) == {}
    agendada.gravar_tentativas(t, arquivo)
    assert agendada.ler_tentativas(arquivo) == t
    arquivo.write_text("[1, 2]", encoding="utf-8")
    assert agendada.ler_tentativas(arquivo) == {}


def teste_janela_e_minutos_do_env():
    casos = {"09:00-17:30": (time(9, 0), time(17, 30)), "'09:00-17:30'  # comentário": (time(9, 0), time(17, 30)),
             "lixo": (time(7, 0), time(19, 0)), "25:00-26:00": (time(7, 0), time(19, 0)), "": None}
    for valor, esperado in casos.items():
        os.environ["TESTE_JANELA"] = valor
        assert agendada._janela("TESTE_JANELA", "07:00-19:00") == esperado, valor
    os.environ.pop("TESTE_JANELA")
    assert agendada._janela("TESTE_JANELA", "07:00-19:00") == (time(7, 0), time(19, 0))
    for valor, esperado in {"45": 45, "45 # c": 45, "0": 1, "-3": 1, "abc": 60, "": 60}.items():
        os.environ["TESTE_MIN"] = valor
        assert agendada._minutos("TESTE_MIN", 60) == esperado, valor
    os.environ.pop("TESTE_MIN")


def teste_passo_que_falha_ou_trava_nao_derruba():
    pasta = Path(tempfile.mkdtemp())
    (pasta / "bom.py").write_text("print('feito')", encoding="utf-8")
    (pasta / "ruim.py").write_text("raise SystemExit('ERRO de teste')", encoding="utf-8")
    (pasta / "lento.py").write_text("import time; time.sleep(30)", encoding="utf-8")
    raiz, tempo = agendada.RAIZ, agendada.TEMPO_MAXIMO_SEGUNDOS
    agendada.RAIZ, agendada.TEMPO_MAXIMO_SEGUNDOS = pasta, 2
    try:
        assert agendada.rodar("bom.py") is True
        assert agendada.rodar("ruim.py") is False
        assert agendada.rodar("nao_existe.py") is False
        assert agendada.rodar("lento.py") is False
    finally:
        agendada.RAIZ, agendada.TEMPO_MAXIMO_SEGUNDOS = raiz, tempo


def projeto_falso() -> Path:
    raiz = Path(tempfile.mkdtemp()) / "projeto"
    for rel, texto in {"site/suporte.html": "<p>s</p>", "site/app.js": "//js", "site/base.css": "/*css*/",
                       "site/segredo.txt": "NAO-PUBLICAR", "site/web.config.exemplo": "NAO-PUBLICAR",
                       "assets/chart.min.js": "//chart", "assets/gsap.min.js": "NAO-PUBLICAR",
                       "assets/fonts/Inter-400.woff2": "fonte", "assets/fonts/leia.txt": "NAO-PUBLICAR",
                       "dados/site/suporte.json": '{"publico": "suporte"}', "dados/site/diretor.json": "{}",
                       "dados/site/outro.json": "NAO-PUBLICAR", "dados/briefing.db": "NAO-PUBLICAR",
                       "dados/helpdesk.json": "NAO-PUBLICAR", ".env": "NAO-PUBLICAR"}.items():
        (raiz / rel).parent.mkdir(parents=True, exist_ok=True)
        (raiz / rel).write_text(texto, encoding="utf-8")
    return raiz


def teste_publica_so_a_lista_fechada():
    raiz, original = projeto_falso(), publicar_site.RAIZ
    destino = raiz.parent / "iis"
    destino.mkdir()
    publicar_site.RAIZ = raiz
    try:
        copiados, iguais, avisos = publicar_site.publicar_site(destino)
        assert sorted(copiados) == ["app.js", "assets/chart.min.js", "assets/fonts/Inter-400.woff2", "base.css",
                                    "dados/diretor.json", "dados/suporte.json", "suporte.html"] and iguais == []
        assert len(avisos) == 2 and all("origem ausente" in a for a in avisos)      # desenvolvimento e licencas
        publicados = sorted(str(p.relative_to(destino)).replace("\\", "/") for p in destino.rglob("*") if p.is_file())
        assert publicados == sorted(copiados)
        assert not any("NAO-PUBLICAR" in p.read_text(encoding="utf-8") for p in destino.rglob("*") if p.is_file())

        # segunda vez: nada mudou, nada é regravado
        copias = []
        copiar, shutil.copyfile = shutil.copyfile, lambda a, b: copias.append(b)
        try:
            copiados, iguais, _ = publicar_site.publicar_site(destino)
        finally:
            shutil.copyfile = copiar
        assert copiados == [] and copias == [] and len(iguais) == 7

        # só o que mudou é copiado, e por cima do MESMO arquivo (não apaga e recria: a permissão fica)
        (raiz / "dados/site/suporte.json").write_text('{"publico": "suporte", "n": 2}', encoding="utf-8")
        trocas = []
        trocar, os.replace = os.replace, lambda a, b: trocas.append(b)
        try:
            copiados, iguais, _ = publicar_site.publicar_site(destino)
        finally:
            os.replace = trocar
        assert copiados == ["dados/suporte.json"] and len(iguais) == 6 and trocas == []
        assert '"n": 2' in (destino / "dados/suporte.json").read_text(encoding="utf-8")
    finally:
        publicar_site.RAIZ = original


def teste_falha_de_copia_vira_aviso():
    raiz, original = projeto_falso(), publicar_site.RAIZ
    destino = raiz.parent / "iis"
    destino.mkdir()
    publicar_site.RAIZ = raiz
    copiar = shutil.copyfile

    def falha(a, b):
        if str(b).endswith("suporte.json"):
            raise PermissionError("acesso negado a \\\\servidor\\site")
        return copiar(a, b)
    shutil.copyfile = falha
    try:
        copiados, _, avisos = publicar_site.publicar_site(destino)
    finally:
        shutil.copyfile, publicar_site.RAIZ = copiar, original
    assert "dados/suporte.json" not in copiados and "dados/diretor.json" in copiados
    assert any("dados/suporte.json: falha ao copiar (PermissionError)" in a for a in avisos)


def teste_destino_recusado_desligado_ou_inacessivel():
    import contextlib
    import io

    def roda(valor):
        if valor is None:
            os.environ.pop("SITE_DIR", None)
        else:
            os.environ["SITE_DIR"] = valor
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            codigo = publicar_site.main()
        return codigo, saida.getvalue()
    try:
        assert roda(None) == (0, "Publicação do site desligada (SITE_DIR vazio no .env).\n")
        assert roda("   ")[0] == 0
        for dentro in (RAIZ, RAIZ / "site", RAIZ / "dados" / "site", RAIZ.parent):
            codigo, texto = roda(str(dentro))
            assert codigo == 1 and "recusada" in texto, dentro
        codigo, texto = roda(str(Path(tempfile.mkdtemp()) / "nao_existe"))
        assert codigo == 1 and "inacessível" in texto
    finally:
        os.environ.pop("SITE_DIR", None)


def teste_arquivos_do_projeto_real_sao_so_os_esperados():
    relativos = [r for _, r in publicar_site.arquivos()]
    assert len(relativos) == len(set(relativos))
    for r in relativos:
        assert r.endswith((".html", ".css", ".js", ".json", ".woff2")) and ".." not in r, r
        assert not r.startswith(("dados/site", "historico")) and "briefing.db" not in r and ".env" not in r, r
    assert [r for r in relativos if r.startswith("dados/")] == [f"dados/{p}.json" for p in publicar_site.PUBLICOS]
    for p in publicar_site.PUBLICOS:
        assert f"{p}.html" in relativos


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
