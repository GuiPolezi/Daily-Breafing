"""Coletor de tickets do Milldesk.

API: https://v1.milldesk.com/api/:api_key/<endpoint>  (chave vai na URL)

Fluxo:
  1. listTicketStatus     -> lista os status existentes (para você configurar)
  2. showTicketsByStatus  -> chamados de cada status "aberto"
  3. ticketsByAgent       -> contagem geral por técnico (visão da equipe)
  4. Filtra os chamados pelo seu nome (HELPDESK_AGENT_NAME)

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

load_dotenv()

API_KEY = os.environ["HELPDESK_API_KEY"]
AGENT_NAME = os.getenv("HELPDESK_AGENT_NAME", "")          # seu nome como aparece no Milldesk
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

    # 2) Separa os seus
    meus = [t for t in todos if AGENT_NAME.lower() in campo_tecnico(t).lower()] if AGENT_NAME else []

    def resumir(t: dict) -> dict:
        return {
            "id": t.get("id"),
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
        "fila_total_abertos": len(todos),
        "meus_abertos": len(meus),
        "meus_novos_hoje": sum(1 for t in meus if eh_de_hoje(t.get("start", ""))),
        "meus_tickets": [resumir(t) for t in meus],
        "contagem_por_tecnico": por_tecnico,
        "_debug_campos_do_primeiro_ticket": list(todos[0].keys()) if todos else [],
    }

    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OK -> {SAIDA}")
    print(f"Fila: {len(todos)} abertos | Seus: {len(meus)}")


if __name__ == "__main__":
    main()