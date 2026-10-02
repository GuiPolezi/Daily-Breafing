"""Coleta incremental do Milldesk para o banco local (sistema novo).

Roda ao lado do pipeline antigo, sem tocar nele: não grava em dados/*.json nem
em historico/. Feito para ser chamado várias vezes por dia por tarefa agendada.

O Milldesk LIMITA requisições (HTTP 429; medido em 01/10/2026: duas coletas
completas seguidas não passam, e o saldo volta devagar). Por isso há dois modos:

  rápida (--rapida)  1 requisição: showTicketsPerPeriod dos últimos N dias DE
                     ABERTURA (o endpoint filtra por `start`). Traz chamado
                     novo, fechamento e mudança dos chamados recentes, que é
                     onde está quase todo o movimento. Pode rodar com frequência.
  completa (padrão)  a rebusca acima + a fila aberta inteira (showTicketsByStatus
                     de cada status não excluído, ~17 requisições). É a única
                     que enxerga chamado antigo e a única que conclui que um
                     chamado fechou por ter sumido da fila. Rodar espaçada.

Ao primeiro 429 a coleta PARA: insistir só gasta o saldo. O que já veio é
gravado e a coleta fica marcada como incompleta.

Somente leitura no Milldesk. As regras de status, sistema, natureza e
atendimento diário são as de coletores/check_helpdesk.py, importadas -- não há
segunda cópia para divergir.

Uso:
    python coletar.py              # completa, rebusca COLETA_DIAS_REBUSCA dias
    python coletar.py --rapida     # só a rebusca
    python coletar.py --dias 60    # carga inicial (ou alargar a janela depois)

Não rode duas ao mesmo tempo: a tarefa agendada deve ser criada com "não iniciar
nova instância se já estiver em execução". O banco trava a escrita, mas uma
coleta lenta que termine depois de outra gravaria dado mais velho por cima.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "coletores"))

import banco  # noqa: E402
import check_helpdesk as ch  # noqa: E402  (carrega o .env e exige HELPDESK_API_KEY)


def _numero(nome: str, padrao: float, minimo: float) -> float:
    try:
        return max(minimo, float(os.getenv(nome, str(padrao))))
    except ValueError:
        return padrao


DIAS_REBUSCA = int(_numero("COLETA_DIAS_REBUSCA", 7, 1))
# Intervalo entre duas requisições da mesma coleta. Não aumenta o saldo, mas
# evita a rajada de ~20 chamadas em 2 segundos.
PAUSA_SEGUNDOS = _numero("COLETA_PAUSA_SEGUNDOS", 1, 0)
# Tamanho de cada pedido da rebusca. 30 dias (~900 chamados) respondem em ~1 s.
DIAS_POR_PEDIDO = 30


class LimiteAtingido(Exception):
    """O Milldesk respondeu 429: a coleta para aqui."""


class Milldesk:
    """As chamadas de uma coleta: conta, espaça e reconhece o limite."""

    def __init__(self) -> None:
        self.requisicoes = 0
        # Fica marcado além de levantar a exceção: quem chama por dentro de
        # check_helpdesk (que engole Exception) também precisa saber que parou.
        self.limite: str | None = None

    def chamar(self, endpoint: str, params: dict | None = None):
        if self.limite:
            raise LimiteAtingido(self.limite)       # nenhuma requisição depois do 429
        if self.requisicoes and PAUSA_SEGUNDOS:
            time.sleep(PAUSA_SEGUNDOS)
        self.requisicoes += 1
        try:
            return ch.chamar(endpoint, params)
        except Exception as e:
            resposta = getattr(e, "response", None)
            if getattr(resposta, "status_code", None) == 429:
                self.limite = ch.sem_chave(e)
                raise LimiteAtingido(self.limite) from None
            raise


def _iso(valor) -> str | None:
    dt = ch.parse_data(valor)
    return dt.isoformat(timespec="minutes") if dt else None


def registro_do_chamado(t: dict) -> dict:
    """Chamado cru da API -> as colunas de banco.CAMPOS.

    A descrição não é guardada (texto livre, com dado pessoal): dela só sai o
    técnico do atendimento diário, pela mesma regra do coletor antigo.
    """
    status = str(t.get("_status_consultado") or t.get("status") or "").strip()
    excluidos = {ch.normalizar(s) for s in ch.STATUS_EXCLUIDOS}
    aberto = ch.normalizar(status) not in excluidos

    atendimento = ch.eh_atendimento_diario(t)
    tecnico_atendimento = None
    if atendimento:
        if ch.normalizar(ch.SOLICITANTE_ATENDIMENTO) in ch.normalizar(t.get("requester", "")):
            tecnico_atendimento = ch.tecnico_da_descricao(t) or None
        else:
            tecnico_atendimento = ch.tecnico_canonico(ch.campo_tecnico(t))[0] or None

    return {
        "id": str(t.get("id")),
        "assunto": t.get("ticket"),
        "solicitante": t.get("requester"),
        "local": " ".join(str(t.get("location") or "").split()) or None,
        "categoria": t.get("category"),
        "subcategoria": t.get("subcategory"),
        "sistema": ch.sistema_do_ticket(t),
        "natureza": ch.natureza_do_ticket(t),
        "tecnico": str(t.get("agent") or "").strip() or None,
        "status": status or None,
        "aberto": int(aberto),
        "prioridade": t.get("priority"),
        "urgencia": t.get("urgency"),
        "tipo": t.get("tickettype"),
        "grupo": t.get("group"),
        "departamento": t.get("department"),
        "aberto_em": _iso(t.get("starttime")) or _iso(t.get("start")),
        # só de chamado fechado: alguns chamados ABERTOS trazem `endtime`
        # (reabertos); e `end` vem vazio em muito chamado já fechado
        "fechado_em": None if aberto else (_iso(t.get("endtime")) or _iso(t.get("end"))),
        "atendimento_diario": int(atendimento),
        "atendimento_tecnico": tecnico_atendimento,
    }


def buscar_periodo(api: Milldesk, dias: int, hoje: date | None = None
                   ) -> tuple[list[dict], tuple[str, str], list[str]]:
    """Chamados ABERTOS nos últimos `dias` dias (inclui os já fechados)."""
    fim = hoje or date.today()
    inicio = fim - timedelta(days=dias - 1)
    chamados: list[dict] = []
    avisos: list[str] = []
    de = inicio
    while de <= fim:
        ate = min(de + timedelta(days=DIAS_POR_PEDIDO - 1), fim)
        try:
            resposta = api.chamar("showTicketsPerPeriod", {"start": de.isoformat(), "end": ate.isoformat()})
            if not isinstance(resposta, list):
                raise ValueError("resposta não é uma lista de chamados")
            chamados.extend(resposta)
        except LimiteAtingido:
            # os pedaços que já vieram ficam; a janela é que não fica completa
            avisos.append(f"rebusca interrompida em {de:%d/%m}-{ate:%d/%m} pelo limite de requisições")
            break
        except Exception as e:
            avisos.append(f"período {de:%d/%m}-{ate:%d/%m} não respondeu ({ch.sem_chave(e)})")
        de = ate + timedelta(days=1)
    return chamados, (inicio.isoformat(), fim.isoformat()), avisos


def buscar_fila(api: Milldesk) -> tuple[list[dict], list[str], bool, list[str]]:
    """Fila aberta. Devolve (chamados, status consultados, completa?, avisos).

    Se o limite estourar em qualquer ponto, devolve a fila VAZIA e incompleta:
    fila pela metade só confundiria a coleta seguinte.
    """
    def listar() -> list:
        dados = api.chamar("listTicketStatus")
        return dados if isinstance(dados, list) else [dados]

    status, disponiveis, aviso = ch.status_para_consultar(listar)
    if api.limite:
        return [], [], False, []
    avisos = [aviso] if aviso else []
    # Fila vinda do fallback (API sem resposta, ou tudo excluído) serve para
    # atualizar, não para concluir que alguém fechou. Só é completa quando é
    # exatamente "tudo que a API listou menos os excluídos".
    excluidos = {ch.normalizar(s) for s in ch.STATUS_EXCLUIDOS}
    completa = bool(status) and status == [s for s in disponiveis if ch.normalizar(s) not in excluidos]
    chamados: list[dict] = []
    for s in status:
        try:
            resposta = api.chamar("showTicketsByStatus", {"status": s})
            # Só uma LISTA de chamados com id é resposta válida. Corpo vazio que
            # não é lista ({} ou null) ou um aviso em HTTP 200 é falha: tratá-lo
            # como "status sem chamados" fecharia quem está nele.
            if not isinstance(resposta, list) or any(
                    not isinstance(t, dict) or t.get("id") in (None, "") for t in resposta):
                raise ValueError("resposta não é uma lista de chamados")
            for t in resposta:
                t["_status_consultado"] = s
            chamados.extend(resposta)
        except LimiteAtingido:
            return [], status, False, avisos
        except Exception as e:
            completa = False
            avisos.append(f"status {s!r} não respondeu ({ch.sem_chave(e)})")
    if completa and not chamados:
        # todos os status vazios com HTTP 200 parece mais pane da API do que
        # fila zerada: não serve de prova de que os chamados fecharam
        completa = False
        avisos.append("a fila veio vazia em todos os status: tratada como incompleta")
    return chamados, status, completa, avisos


def juntar(primeiro: list[dict], depois: list[dict]) -> tuple[list[dict], list[str]]:
    """Um registro por id. Quem foi lido DEPOIS vence o empate (é o mais novo)."""
    por_id: dict[str, dict] = {}
    repetidos = sem_id = sem_status = 0
    for origem in (primeiro, depois):
        desta: set[str] = set()
        for t in origem:
            cid = t.get("id")
            if cid in (None, ""):
                sem_id += 1
            elif not str(t.get("_status_consultado") or t.get("status") or "").strip():
                # sem status não dá para saber se está aberto: não pode
                # sobrescrever o que já se sabe nem reabrir um chamado fechado
                sem_status += 1
            else:
                repetidos += str(cid) in desta
                desta.add(str(cid))
                por_id[str(cid)] = t
    avisos = []
    if repetidos:
        avisos.append(f"{repetidos} chamados vieram mais de uma vez na mesma consulta (mudaram durante a coleta)")
    if sem_id:
        avisos.append(f"{sem_id} chamados sem id foram ignorados")
    if sem_status:
        avisos.append(f"{sem_status} chamados vieram sem status e foram ignorados")
    return [registro_do_chamado(t) for t in por_id.values()], avisos


def coletar(con, dias: int = DIAS_REBUSCA, agora: datetime | None = None, rapida: bool = False) -> dict:
    agora = agora or datetime.now()
    api = Milldesk()
    fila: list[dict] = []
    status: list[str] = []
    completa = False
    # A rebusca vem primeiro: é 1 requisição e vale mais que qualquer status
    # sozinho. Se o limite estourar no meio da fila, ela já está garantida.
    periodo, janela, avisos = buscar_periodo(api, dias, agora.date())
    # janela só é registrada quando veio inteira: é ela que a próxima coleta
    # usa para separar "carga de dias antigos" de chamado novo de verdade
    intervalo = None if avisos else janela
    if not rapida and not api.limite:
        fila, status, completa, avisos_fila = buscar_fila(api)
        avisos = avisos + avisos_fila
    if api.limite:
        avisos.append(f"limite de requisições do Milldesk atingido após {api.requisicoes} "
                      f"chamadas; coleta interrompida ({api.limite})")
    registros, avisos_juntar = juntar(periodo, fila)
    resultado = banco.aplicar_coleta(
        con, momento=agora.isoformat(timespec="seconds"), registros=registros,
        fila_completa=completa, fila_consultada=not rapida, status_consultados=status,
        total_abertos=sum(r["aberto"] for r in registros) if completa else None,
        # rebusca que não respondeu nada é "não sei", não "zero chamados no período"
        total_periodo=len(periodo) if periodo or intervalo else None, periodo=intervalo, avisos=avisos + avisos_juntar,
        modo="rapida" if rapida else "completa", requisicoes=api.requisicoes)
    return {**resultado, "requisicoes": api.requisicoes}


def main() -> None:
    dias = DIAS_REBUSCA
    if "--dias" in sys.argv:
        try:
            dias = max(1, int(sys.argv[sys.argv.index("--dias") + 1]))
        except (IndexError, ValueError):
            raise SystemExit("uso: python coletar.py [--rapida] [--dias N]")
    rapida = "--rapida" in sys.argv
    con = banco.conectar()
    try:
        r = coletar(con, dias, rapida=rapida)
    finally:
        con.close()
    print(f"OK -> {banco.ARQUIVO} (coleta {r['coleta_id']}, {'rápida' if rapida else 'completa'}"
          + (", linha de base" if r["linha_de_base"] else "") + f", {r['requisicoes']} requisições)")
    print(f"Novos: {r['novos']} | Atualizados: {r['atualizados']} | Eventos: {r['eventos']} "
          f"| Fechados inferidos: {r['fechados_inferidos']}")
    for aviso in r["avisos"]:
        print(f"AVISO: {aviso}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # sem traceback: ele imprimiria a URL com a chave (ver check_helpdesk.sem_chave)
        raise SystemExit(f"ERRO em coletar.py: {type(e).__name__}: {ch.sem_chave(e)}") from None
