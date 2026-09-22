"""Coletor da agenda do dia (Google Calendar API v3).

Duas chamadas por execução: troca o refresh token por um access token e lista os
eventos de hoje. `singleEvents=true` faz o PRÓPRIO Google expandir os eventos
recorrentes -- sem isso seria preciso interpretar RRULE/EXDATE/RECURRENCE-ID
aqui, que falha em silêncio (a reunião semanal simplesmente some e ninguém vê).

Somente leitura (escopo calendar.readonly): nenhum evento é criado, alterado ou
apagado. Como todo coletor deste projeto, só lê.

Fuso: sai do SO via .astimezone(), nunca de zoneinfo. Nesta máquina (Windows,
Python 3.14 sem o pacote tzdata) ZoneInfo("America/Sao_Paulo") levanta
ZoneInfoNotFoundError, e usar fuso nomeado exigiria dependência nova. O fuso do
SO é o certo aqui de qualquer forma: a agenda é local.

Configuração no .env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
GOOGLE_REFRESH_TOKEN (todos obrigatórios; rode coletores/autorizar_google.py
uma vez para obter o refresh token), AGENDA_CALENDARIOS, AGENDA_EXPEDIENTE.

Saída: dados/agenda.json
"""

import json
import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3/calendars"

# Calendários a consultar, separados por ";". "primary" é o da própria conta.
CALENDARIOS = [
    c.strip() for c in os.getenv("AGENDA_CALENDARIOS", "primary").split(";") if c.strip()
]
# Faixa considerada "expediente" para calcular a maior janela livre do dia.
EXPEDIENTE = os.getenv("AGENDA_EXPEDIENTE", "08:00-18:00")
# Janela livre menor que isso não é janela, é intervalo entre reuniões.
JANELA_MINIMA_MIN = int(os.getenv("AGENDA_JANELA_MINIMA", "30") or 30)

SAIDA = RAIZ / "dados" / "agenda.json"

# Tipos de evento que só poluem a lista: "workingLocation" é o marcador de
# home office/escritório que o Google cria sozinho, não é compromisso.
TIPOS_IGNORADOS = {"workingLocation"}


def access_token() -> str:
    """Troca o refresh token por um access token de curta duração."""
    faltando = [n for n in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                            "GOOGLE_REFRESH_TOKEN") if not os.getenv(n, "").strip()]
    if faltando:
        raise RuntimeError(
            "faltam no .env: " + ", ".join(faltando)
            + " -- rode `python coletores/autorizar_google.py` uma vez"
        )
    resp = requests.post(TOKEN_URL, timeout=30, data={
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    })
    dados = resp.json() if resp.content else {}
    if resp.status_code != 200 or not dados.get("access_token"):
        motivo = dados.get("error_description") or dados.get("error") or resp.status_code
        # invalid_grant = token revogado (troca de senha, política do Workspace,
        # app removido da conta). A saída é reautorizar, não tentar de novo.
        raise RuntimeError(f"não consegui renovar o acesso ao Google: {motivo}")
    return dados["access_token"]


def eventos_crus(token: str, calendario: str, inicio: datetime, fim: datetime) -> list[dict]:
    """Eventos de um calendário na janela, já com as recorrências expandidas."""
    itens: list[dict] = []
    pagina = None
    url = f"{API}/{urllib.parse.quote(calendario, safe='')}/events"
    while True:
        params = {
            "timeMin": inicio.isoformat(),
            "timeMax": fim.isoformat(),
            "singleEvents": "true",     # o Google expande a recorrência
            "orderBy": "startTime",
            "maxResults": 250,
        }
        if pagina:
            params["pageToken"] = pagina
        resp = requests.get(url, params=params, timeout=30,
                            headers={"Authorization": f"Bearer {token}"})
        dados = resp.json() if resp.content else {}
        if resp.status_code != 200:
            erro = (dados.get("error") or {}).get("message") or resp.status_code
            raise RuntimeError(f"calendário {calendario!r}: {erro}")
        itens.extend(dados.get("items") or [])
        pagina = dados.get("nextPageToken")
        if not pagina:
            return itens


def recusado_por_mim(ev: dict) -> bool:
    """Fui convidado e recusei? Então não é compromisso meu hoje."""
    for a in ev.get("attendees") or []:
        if a.get("self") and a.get("responseStatus") == "declined":
            return True
    return False


def hhmm(momento: datetime) -> str:
    return momento.strftime("%H:%M")


def parse_expediente(bruto: str, dia: datetime) -> tuple[datetime, datetime]:
    """'08:00-18:00' vira (hoje às 08:00, hoje às 18:00). Valor inválido cai no padrão."""
    try:
        ini_txt, fim_txt = bruto.split("-", 1)
        ini_h, ini_m = (int(x) for x in ini_txt.strip().split(":"))
        fim_h, fim_m = (int(x) for x in fim_txt.strip().split(":"))
        ini = dia.replace(hour=ini_h, minute=ini_m)
        fim = dia.replace(hour=fim_h, minute=fim_m)
        if fim > ini:
            return ini, fim
    except (ValueError, AttributeError):
        pass
    return dia.replace(hour=8, minute=0), dia.replace(hour=18, minute=0)


def maior_janela_livre(ocupados: list[tuple[datetime, datetime]],
                       exp_ini: datetime, exp_fim: datetime) -> dict | None:
    """Maior buraco entre compromissos, dentro do expediente."""
    blocos: list[tuple[datetime, datetime]] = []
    for ini, fim in sorted(ocupados):
        ini, fim = max(ini, exp_ini), min(fim, exp_fim)
        if fim <= ini:
            continue
        if blocos and ini <= blocos[-1][1]:          # encosta ou sobrepõe: funde
            blocos[-1] = (blocos[-1][0], max(blocos[-1][1], fim))
        else:
            blocos.append((ini, fim))

    melhor: tuple[datetime, datetime] | None = None
    cursor = exp_ini
    for ini, fim in blocos + [(exp_fim, exp_fim)]:
        if ini > cursor and (melhor is None or ini - cursor > melhor[1] - melhor[0]):
            melhor = (cursor, ini)
        cursor = max(cursor, fim)

    if melhor is None:
        return None
    minutos = int((melhor[1] - melhor[0]).total_seconds() // 60)
    if minutos < JANELA_MINIMA_MIN:
        return None
    return {"inicio": hhmm(melhor[0]), "fim": hhmm(melhor[1]), "minutos": minutos}


def coletar() -> dict:
    agora = datetime.now().astimezone()
    dia_ini = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    dia_fim = dia_ini + timedelta(days=1)
    token = access_token()

    crus: list[dict] = []
    for calendario in CALENDARIOS:
        crus.extend(eventos_crus(token, calendario, dia_ini, dia_fim))

    eventos: list[dict] = []
    dia_inteiro: list[str] = []
    ocupados: list[tuple[datetime, datetime]] = []
    vistos: set[str] = set()

    for ev in crus:
        if ev.get("status") == "cancelled" or ev.get("eventType") in TIPOS_IGNORADOS:
            continue
        if recusado_por_mim(ev):
            continue
        # O mesmo evento pode vir de dois calendários (convite + agenda própria).
        chave = ev.get("iCalUID") or ev.get("id") or ""
        if chave and chave in vistos:
            continue
        vistos.add(chave)

        titulo = (ev.get("summary") or "(sem título)").strip()
        inicio, fim = ev.get("start") or {}, ev.get("end") or {}

        if inicio.get("date"):                      # evento de dia inteiro
            dia_inteiro.append(titulo)
            continue
        if not inicio.get("dateTime") or not fim.get("dateTime"):
            continue

        ini = datetime.fromisoformat(inicio["dateTime"]).astimezone()
        term = datetime.fromisoformat(fim["dateTime"]).astimezone()
        # Evento que atravessa a meia-noite é recortado no dia de hoje: sem isso
        # a conta de horas ocupadas estoura e o horário exibido mente.
        visivel_ini, visivel_fim = max(ini, dia_ini), min(term, dia_fim)
        if visivel_fim <= visivel_ini:
            continue
        ocupados.append((visivel_ini, visivel_fim))

        eventos.append({
            "inicio": hhmm(visivel_ini),
            "fim": hhmm(visivel_fim),
            "titulo": titulo,
            "local": (ev.get("location") or "").strip(),
            "reuniao_online": bool(ev.get("hangoutLink") or ev.get("conferenceData")),
            "minutos": int((visivel_fim - visivel_ini).total_seconds() // 60),
            "ja_passou": visivel_fim <= agora,
            "em_andamento": visivel_ini <= agora < visivel_fim,
            "comeca_antes_de_hoje": ini < dia_ini,
            "termina_depois_de_hoje": term > dia_fim,
        })

    eventos.sort(key=lambda e: e["inicio"])
    exp_ini, exp_fim = parse_expediente(EXPEDIENTE, dia_ini)

    return {
        "fonte": "agenda (Google Calendar)",
        "data": date.today().isoformat(),
        "coletado_em": datetime.now().isoformat(),
        "calendarios": CALENDARIOS,
        "expediente": EXPEDIENTE,
        "total": len(eventos),
        "minutos_ocupados": sum(e["minutos"] for e in eventos),
        "primeiro": eventos[0]["inicio"] if eventos else None,
        "ultimo": eventos[-1]["fim"] if eventos else None,
        "maior_janela_livre": maior_janela_livre(ocupados, exp_ini, exp_fim),
        "dia_inteiro": dia_inteiro,
        "eventos": eventos,
    }


def main() -> None:
    SAIDA.parent.mkdir(exist_ok=True)
    try:
        resultado = coletar()
    except Exception as e:
        # Grava o erro em vez de deixar o JSON de ONTEM no lugar: dado velho
        # passando por agenda de hoje é pior do que seção vazia. O briefing.bat
        # segue adiante ("AVISO: ... falhou, seguindo sem essa fonte").
        SAIDA.write_text(json.dumps({
            "fonte": "agenda (Google Calendar)",
            "data": date.today().isoformat(),
            "coletado_em": datetime.now().isoformat(),
            "erro": str(e),
            "total": 0,
            "eventos": [],
            "dia_inteiro": [],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"FALHA -> {e}", file=sys.stderr)
        raise SystemExit(1)

    SAIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK -> {SAIDA}")
    horas, minutos = divmod(resultado["minutos_ocupados"], 60)
    print(f"{resultado['total']} evento(s) hoje | {horas}h{minutos:02d} ocupadas"
          + (f" | primeiro {resultado['primeiro']}" if resultado["primeiro"] else ""))
    if resultado["dia_inteiro"]:
        print("Dia inteiro: " + ", ".join(resultado["dia_inteiro"]))
    janela = resultado["maior_janela_livre"]
    if janela:
        print(f"Maior janela livre: {janela['inicio']}-{janela['fim']} "
              f"({janela['minutos']} min)")


if __name__ == "__main__":
    main()
