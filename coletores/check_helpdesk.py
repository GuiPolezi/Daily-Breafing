"""Coletor de tickets do Milldesk.

API: https://v1.milldesk.com/api/:api_key/<endpoint>  (chave vai na URL)

Fluxo:
  1. listTicketStatus     -> lista os status existentes (para você configurar)
  2. showTicketsByStatus  -> chamados de cada status "aberto"
  3. ticketsByAgent       -> contagem geral por técnico (visão da equipe)
  4. Filtra os chamados pelos nomes em HELPDESK_AGENT_NAME (um ou vários,
     separados por ";" - ex.: "Seu Nome;Colega 1;Colega 2")

Primeira execução: rode `python coletores/check_helpdesk.py --listar-status`
para ver os status do seu Milldesk e preencher HELPDESK_STATUS_ABERTOS no .env.

Saída: dados/helpdesk.json
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

import unicodedata
from datetime import timedelta
import re

load_dotenv()

API_KEY = os.environ["HELPDESK_API_KEY"]
# Nomes como aparecem no Milldesk; vários separados por ";" (você + equipe)
AGENT_NAMES = [
    n.strip() for n in os.getenv("HELPDESK_AGENT_NAME", "").split(";") if n.strip()
]
STATUS_ABERTOS = [
    s.strip() for s in os.getenv("HELPDESK_STATUS_ABERTOS", "").split(";") if s.strip()
]

BASE = f"https://v1.milldesk.com/api/{API_KEY}"
SAIDA = Path(__file__).resolve().parent.parent / "dados" / "helpdesk.json"


def chamar(endpoint: str, params: dict | None = None):
    resp = requests.get(f"{BASE}/{endpoint}", params=params, timeout=30)
    resp.raise_for_status()
    dados = resp.json()
    # A API retorna HTTP 200 até para chave inválida; o erro vem no corpo
    if isinstance(dados, dict) and dados.get("error"):
        raise RuntimeError(f"Erro da API Milldesk em {endpoint}: {dados['error']}")
    return dados


def listar_status() -> list[dict]:
    dados = chamar("listTicketStatus")
    return dados if isinstance(dados, list) else [dados]


def tickets_por_status(status: str) -> list[dict]:
    dados = chamar("showTicketsByStatus", {"status": status})
    if not isinstance(dados, list):
        dados = [dados] if dados else []
    for t in dados:
        t["_status_consultado"] = status
    return dados


def campo_tecnico(ticket: dict) -> str:
    """Descobre em qual campo está o nome do técnico (a doc não deixa claro)."""
    for chave in ("agent", "technician", "tecnico", "responsible", "operator"):
        if ticket.get(chave):
            return str(ticket[chave])
    return ""


def eh_de_hoje(valor: str) -> bool:
    hoje = date.today()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(valor).strip(), fmt).date() == hoje
        except (ValueError, TypeError):
            continue
    return False


SOLICITANTE_ATENDIMENTO = os.getenv("HELPDESK_SOLICITANTE_ATENDIMENTO", "Atendimento Diário")


def normalizar(texto: str) -> str:
    """minúsculas e sem acentos, p/ comparar 'Diario' com 'Diário'."""
    texto = unicodedata.normalize("NFD", str(texto))
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").lower().strip()


def ultimo_dia_util(referencia: date | None = None) -> date:
    """Dia útil anterior: seg -> sex; ter-sáb -> dia anterior; dom -> sex."""
    dia = (referencia or date.today()) - timedelta(days=1)
    while dia.weekday() >= 5:  # 5=sábado, 6=domingo
        dia -= timedelta(days=1)
    return dia

PADRAO_TECNICO = re.compile(
    r"T[ée]cnico\s*[:\-]\s*(.*?)\s*(?:<br\s*/?>|\r|\n|Cliente\s*:|Finalizado|Resumo\s*:|$)",
    re.IGNORECASE,
)


def tecnico_da_descricao(ticket: dict) -> str:
    """Extrai o nome do técnico da linha 'Técnico: Fulano' na descrição."""
    match = PADRAO_TECNICO.search(str(ticket.get("description", "")))
    return match.group(1).strip() if match else ""


def atendimentos_do_dia(dia: date) -> dict:
    """Tickets FECHADOS criados no dia pelo solicitante de atendimento diário."""
    tickets = []
    formato_usado = None
    erros = []
    # ISO primeiro (já confirmado que a API aceita); os demais são fallback
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        data_str = dia.strftime(fmt)
        try:
            resposta = chamar("showTicketsPerPeriod", {"start": data_str, "end": data_str})
        except RuntimeError as e:
            erros.append(f"{fmt} -> {e}")
            continue
        formato_usado = fmt
        if isinstance(resposta, list):
            tickets = resposta
        break

    if formato_usado is None:
        return {"erro": "Nenhum formato de data aceito pela API", "tentativas": erros}

    # Filtro 1: solicitante "Atendimento Diario"
    alvo = normalizar(SOLICITANTE_ATENDIMENTO)
    do_solicitante = [t for t in tickets if alvo in normalizar(t.get("requester", ""))]

    # Filtro 2: status Fechado (se o endpoint retornar o campo)
    aviso_status = None
    if any("status" in t for t in do_solicitante):
        atendimentos = [t for t in do_solicitante if normalizar(t.get("status", "")) == "fechado"]
        nao_fechados = len(do_solicitante) - len(atendimentos)
    else:
        atendimentos = do_solicitante
        nao_fechados = 0
        aviso_status = "endpoint não retorna 'status'; filtro de Fechado não aplicado"

    # Contagem por técnico (extraído da descrição)
    por_tecnico: dict[str, int] = {}
    sem_tecnico = []
    for t in atendimentos:
        tecnico = tecnico_da_descricao(t)
        if not tecnico:
            sem_tecnico.append(t.get("id"))
            tecnico = "(não identificado)"
        por_tecnico[tecnico] = por_tecnico.get(tecnico, 0) + 1

    resultado = {
        "dia": dia.strftime("%d/%m/%Y") + f" ({['seg','ter','qua','qui','sex','sab','dom'][dia.weekday()]})",
        "total_atendimentos_fechados": len(atendimentos),
        "por_tecnico": dict(sorted(por_tecnico.items(), key=lambda kv: -kv[1])),
        "do_solicitante_mas_nao_fechados": nao_fechados,
        "chamados_normais_abertos_no_dia": len(tickets) - len(do_solicitante),
        "tickets_sem_tecnico_na_descricao": sem_tecnico,
    }
    if aviso_status:
        resultado["aviso"] = aviso_status
    return resultado


def main() -> None:
    # Modo utilitário: descobrir os status do seu Milldesk
    if "--listar-status" in sys.argv:
        print("Status existentes no seu Milldesk:")
        for s in listar_status():
            print(f"  - {s.get('status')}  ({s.get('description', '')})")
        print("\nCopie os que significam 'em aberto' para o .env, separados por ';'")
        print("Ex.: HELPDESK_STATUS_ABERTOS='Aberto;Em atendimento;Aguardando'")
        return

    if not STATUS_ABERTOS:
        raise SystemExit(
            "Configure HELPDESK_STATUS_ABERTOS no .env primeiro.\n"
            "Rode: python coletores/check_helpdesk.py --listar-status"
        )

    # 1) Busca os chamados de todos os status abertos
    todos: list[dict] = []
    for status in STATUS_ABERTOS:
        todos.extend(tickets_por_status(status))

    # 2) Separa os chamados dos agentes monitorados (você + equipe)
    #    Comparação sem acentos e sem maiúsculas: "Fabio" casa com "Fábio".
    def agente_do_ticket(t: dict) -> str:
        tecnico = normalizar(campo_tecnico(t))
        for nome in AGENT_NAMES:
            if normalizar(nome) in tecnico:
                return nome
        return ""

    por_agente: dict[str, list[dict]] = {nome: [] for nome in AGENT_NAMES}
    for t in todos:
        nome = agente_do_ticket(t)
        if nome:
            por_agente[nome].append(t)
    meus = [t for lista in por_agente.values() for t in lista]

    def resumir(t: dict) -> dict:
        return {
            "id": t.get("id"),
            "tecnico": agente_do_ticket(t),
            "assunto": t.get("ticket"),
            "solicitante": t.get("requester"),
            "status": t.get("_status_consultado"),
            "criado_em": t.get("start"),
            "sla_expira": t.get("slasexpirationdate"),
            "categoria": t.get("category"),
            "prioridade": t.get("priority"),
            "urgencia": t.get("urgency"),
        }

    # 3) Visão geral da equipe (contagem por técnico)
    try:
        por_tecnico = chamar("ticketsByAgent")
    except Exception as e:
        por_tecnico = f"indisponível ({e})"

    resultado = {
        "fonte": "helpdesk (Milldesk)",
        "data": date.today().isoformat(),
        "coletado_em": datetime.now().isoformat(),
        "fila_total_abertos": len(todos),
        # "meus_*" = soma de todos os agentes em HELPDESK_AGENT_NAME
        "agentes_monitorados": AGENT_NAMES,
        "meus_abertos": len(meus),
        "meus_novos_hoje": sum(1 for t in meus if eh_de_hoje(t.get("start", ""))),
        "meus_tickets": [resumir(t) for t in meus],
        "por_agente": {
            nome: {
                "abertos": len(lista),
                "novos_hoje": sum(1 for t in lista if eh_de_hoje(t.get("start", ""))),
            }
            for nome, lista in por_agente.items()
        },
        "contagem_por_tecnico": por_tecnico,
        "_debug_campos_do_primeiro_ticket": list(todos[0].keys()) if todos else [],
        "atendimentos_ultimo_dia_util": atendimentos_do_dia(ultimo_dia_util()),
    }

    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OK -> {SAIDA}")
    detalhe = " | ".join(f"{n}: {len(l)}" for n, l in por_agente.items())
    print(f"Fila: {len(todos)} abertos | Monitorados: {len(meus)}" + (f" ({detalhe})" if detalhe else ""))


if __name__ == "__main__":
    main()