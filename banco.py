"""Banco local do sistema novo (SQLite, biblioteca padrão).

Guarda um registro por chamado (atualizado por id a cada coleta) e um registro
por MUDANÇA observada (status, dono, fechamento, reabertura). É o que o Milldesk
não tem: ele só responde "como está agora", e o painel antigo reconstruía o
passado comparando fotos diárias.

Este módulo não conhece a API: recebe chamados já normalizados (ver
`coletar.registro_do_chamado`) e decide o que mudou. Por isso é testável sem
rede (testes/test_banco.py).

O arquivo fica em dados/briefing.db: `dados/` inteiro está no .gitignore e
nunca é publicado.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ARQUIVO = RAIZ / "dados" / "briefing.db"

# Campos de um chamado, na ordem das colunas. `id` é a chave.
CAMPOS = (
    "id", "assunto", "solicitante", "local", "categoria", "subcategoria",
    "sistema", "natureza", "tecnico", "status", "aberto", "prioridade",
    "urgencia", "tipo", "grupo", "departamento", "aberto_em", "fechado_em",
    "atendimento_diario", "atendimento_tecnico",
)

# Fechamento inferido: chamado aberto que deixou de vir em toda consulta. Uma
# ausência só não basta -- chamado que muda de status no meio da coleta escapa
# das duas listas, e a API pode devolver um status vazio por engano. Só fecha
# quem faltou em AUSENCIAS_PARA_FECHAR coletas completas seguidas.
AUSENCIAS_PARA_FECHAR = 2
# Sumiço em massa pede uma confirmação a mais (e um aviso). Não é um bloqueio:
# as ausências continuam contando, então uma limpeza real da fila é aceita na
# coleta seguinte em vez de travar o banco para sempre.
AUSENCIAS_EM_MASSA = 3
SUMICO_MAX_ABSOLUTO = 20
SUMICO_MAX_FRACAO = 0.30

ESQUEMA = f"""
CREATE TABLE IF NOT EXISTS chamados (
    {", ".join(c + (" TEXT PRIMARY KEY" if c == "id" else " INTEGER" if c in ("aberto", "atendimento_diario") else " TEXT") for c in CAMPOS)},
    fechamento_inferido INTEGER NOT NULL DEFAULT 0,
    ausencias INTEGER NOT NULL DEFAULT 0,
    primeiro_visto TEXT NOT NULL,
    ultimo_visto TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chamados_aberto ON chamados (aberto);
CREATE INDEX IF NOT EXISTS chamados_aberto_em ON chamados (aberto_em);

CREATE TABLE IF NOT EXISTS coletas (
    id INTEGER PRIMARY KEY,
    fonte TEXT NOT NULL,
    momento TEXT NOT NULL,
    fila_completa INTEGER NOT NULL,
    status_consultados TEXT,
    total_abertos INTEGER,
    total_periodo INTEGER,
    periodo_inicio TEXT,
    periodo_fim TEXT,
    avisos TEXT,
    modo TEXT,
    requisicoes INTEGER
);

CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY,
    chamado_id TEXT NOT NULL,
    tipo TEXT NOT NULL,
    de TEXT,
    para TEXT,
    momento TEXT NOT NULL,
    coleta_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS eventos_momento ON eventos (momento);
CREATE INDEX IF NOT EXISTS eventos_chamado ON eventos (chamado_id);
"""


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    """Abre (e cria, se preciso) o banco. `":memory:"` serve aos testes."""
    alvo = ARQUIVO if caminho is None else caminho
    if alvo != ":memory:":
        Path(alvo).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(alvo), timeout=60)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    # colunas que entraram depois da primeira coleta real (banco já existente)
    existentes = {l["name"] for l in con.execute("PRAGMA table_info(coletas)")}
    for coluna, tipo in (("modo", "TEXT"), ("requisicoes", "INTEGER")):
        if coluna not in existentes:
            try:
                con.execute(f"ALTER TABLE coletas ADD COLUMN {coluna} {tipo}")
            except sqlite3.OperationalError as e:
                # outra coleta abriu o banco no mesmo instante e já criou a coluna
                if "duplicate column name" not in str(e):
                    raise
    con.commit()
    return con


def _ultima_fila_completa(con: sqlite3.Connection, fonte: str) -> tuple[set[str], str | None] | None:
    """(status consultados, início da rebusca) da última coleta com a fila completa."""
    linha = con.execute(
        "SELECT status_consultados FROM coletas WHERE fonte = ? AND fila_completa = 1 "
        "ORDER BY id DESC LIMIT 1", (fonte,)).fetchone()
    if linha is None:
        return None
    try:
        status = set(json.loads(linha["status_consultados"] or "[]"))
    except (TypeError, ValueError):
        status = set()
    # a janela vem da última rebusca que respondeu INTEIRA (só essas gravam o início)
    janela = con.execute(
        "SELECT periodo_inicio FROM coletas WHERE fonte = ? AND periodo_inicio IS NOT NULL "
        "ORDER BY id DESC LIMIT 1", (fonte,)).fetchone()
    return status, janela["periodo_inicio"] if janela else None


def aplicar_coleta(con: sqlite3.Connection, *, momento: str, registros: list[dict],
                   fila_completa: bool, status_consultados: list[str],
                   total_abertos: int | None = None, total_periodo: int | None = None,
                   periodo: tuple[str, str] | None = None, avisos: list[str] | None = None,
                   fonte: str = "milldesk", fila_consultada: bool = True,
                   modo: str | None = None, requisicoes: int | None = None) -> dict:
    """Grava uma coleta: atualiza os chamados e registra o que mudou.

    `registros` são os chamados vistos agora (fila aberta + rebusca por período),
    um por id. Tudo numa transação: ou a coleta entra inteira, ou não entra.

    Regras:
      - Até existir uma coleta com a fila completa, tudo é LINHA DE BASE: grava
        os chamados e não gera evento (senão a fila inteira pareceria aberta
        naquele minuto).
      - Chamado aberto que não veio em lugar nenhum acumula uma ausência por
        coleta completa; com AUSENCIAS_PARA_FECHAR seguidas, fechou (inferido).
        Coleta incompleta, ou com menos status que a anterior, não conta ausência.
        Coleta rápida (`fila_consultada=False`) nem olhou a fila: quem não veio
        nela simplesmente não foi consultado, e não há o que avisar.
      - Chamado fechado e ANTERIOR à janela que a coleta passada rebuscou não gera
        "entrou": é só a janela que cresceu (carga de mais dias), não novidade.
    """
    avisos = list(avisos or [])
    resumo = {"novos": 0, "atualizados": 0, "eventos": 0, "fechados_inferidos": 0}

    with con:
        # trava de escrita ANTES de ler a coleta anterior: duas execuções
        # sobrepostas não podem as duas se achar a linha de base
        con.execute("BEGIN IMMEDIATE")
        anterior = _ultima_fila_completa(con, fonte)
        linha_de_base = anterior is None
        status_antes, janela_antes = anterior or (set(), None)

        coleta_id = con.execute(
            "INSERT INTO coletas (fonte, momento, fila_completa, status_consultados, "
            "total_abertos, total_periodo, periodo_inicio, periodo_fim, modo, requisicoes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fonte, momento, int(fila_completa), json.dumps(sorted(status_consultados), ensure_ascii=False),
             total_abertos, total_periodo, *(periodo or (None, None)), modo, requisicoes)).lastrowid

        def evento(chamado_id: str, tipo: str, de, para) -> None:
            if linha_de_base:
                return
            con.execute("INSERT INTO eventos (chamado_id, tipo, de, para, momento, coleta_id) "
                        "VALUES (?,?,?,?,?,?)", (chamado_id, tipo, de, para, momento, coleta_id))
            resumo["eventos"] += 1

        vistos: set[str] = set()
        for reg in registros:
            cid = str(reg["id"])
            vistos.add(cid)
            aberto = bool(reg.get("aberto"))
            antes = con.execute("SELECT * FROM chamados WHERE id = ?", (cid,)).fetchone()
            if antes is None:
                valores = [cid] + [reg.get(c) for c in CAMPOS[1:]]
                con.execute(
                    f"INSERT INTO chamados ({', '.join(CAMPOS)}, primeiro_visto, ultimo_visto) "
                    f"VALUES ({', '.join('?' * len(CAMPOS))}, ?, ?)", (*valores, momento, momento))
                resumo["novos"] += 1
                antigo = (not aberto and janela_antes and (reg.get("aberto_em") or "9") < janela_antes)
                if not antigo:
                    evento(cid, "entrou", None, reg.get("status"))
                continue

            if antes["aberto"] and not aberto:
                evento(cid, "fechou", antes["status"], reg.get("status"))
            elif not antes["aberto"] and aberto:
                evento(cid, "reabriu", antes["status"], reg.get("status"))
            elif aberto and antes["status"] != reg.get("status"):
                # só entre abertos: fechado inferido que depois aparece como
                # "Fechado" na rebusca é a mesma notícia, não uma segunda
                evento(cid, "status", antes["status"], reg.get("status"))
            if aberto and (antes["tecnico"] or "") != (reg.get("tecnico") or ""):
                evento(cid, "dono", antes["tecnico"], reg.get("tecnico"))

            novo = {c: reg.get(c) for c in CAMPOS[1:]}
            inferido = 0
            if not aberto and not novo["fechado_em"]:
                # a API costuma fechar sem data: fica a que já se sabia (inclusive
                # a inferida), em vez de apagar a única informação que existe
                novo["fechado_em"] = antes["fechado_em"]
                inferido = antes["fechamento_inferido"]
            con.execute(
                f"UPDATE chamados SET {', '.join(c + ' = ?' for c in CAMPOS[1:])}, "
                "fechamento_inferido = ?, ausencias = 0, ultimo_visto = ? WHERE id = ?",
                (*novo.values(), inferido, momento, cid))
            resumo["atualizados"] += 1

        # Quem estava aberto e não apareceu em nenhuma das duas consultas.
        sumidos = [l["id"] for l in con.execute("SELECT id FROM chamados WHERE aberto = 1")
                   if l["id"] not in vistos] if fila_consultada else []
        faltando = sorted(status_antes - set(status_consultados))
        if sumidos and not fila_completa:
            avisos.append(f"fila incompleta: {len(sumidos)} chamados sem confirmação, ausência não contada")
        elif sumidos and faltando:
            avisos.append("status deixaram de ser consultados (" + ", ".join(faltando)
                          + f"): {len(sumidos)} chamados sumiram, ausência não contada")
        elif sumidos:
            abertos_antes = len(sumidos) + sum(1 for r in registros if r.get("aberto"))
            teto = max(SUMICO_MAX_ABSOLUTO, int(abertos_antes * SUMICO_MAX_FRACAO))
            limiar = AUSENCIAS_PARA_FECHAR
            if len(sumidos) > teto:
                limiar = AUSENCIAS_EM_MASSA
                avisos.append(f"{len(sumidos)} chamados sumiram de uma vez (teto {teto}): "
                              f"só fecham com {limiar} ausências seguidas")
            for cid in sumidos:
                linha = con.execute("SELECT status, ausencias FROM chamados WHERE id = ?", (cid,)).fetchone()
                if linha["ausencias"] + 1 < limiar:
                    con.execute("UPDATE chamados SET ausencias = ausencias + 1 WHERE id = ?", (cid,))
                    continue
                evento(cid, "fechou", linha["status"], None)
                con.execute("UPDATE chamados SET aberto = 0, fechamento_inferido = 1, ausencias = 0, "
                            "fechado_em = ? WHERE id = ?", (momento, cid))
                resumo["fechados_inferidos"] += 1

        con.execute("UPDATE coletas SET avisos = ? WHERE id = ?",
                    (json.dumps(avisos, ensure_ascii=False) if avisos else None, coleta_id))

    return {**resumo, "coleta_id": coleta_id, "linha_de_base": linha_de_base, "avisos": avisos}
