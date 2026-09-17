"""Coletor de tickets do Milldesk.

API: https://v1.milldesk.com/api/:api_key/<endpoint>  (chave vai na URL)

Fluxo:
  1. listTicketStatus     -> lista os status existentes no Milldesk
  2. showTicketsByStatus  -> chamados de cada status MENOS os excluídos
     (regra global: "em aberto" = tudo que não está Fechado)
  3. ticketsByAgent       -> contagem geral por técnico (visão da equipe)
  4. Filtra os chamados pelos nomes em HELPDESK_AGENT_NAME (um ou vários,
     separados por ";" - ex.: "Seu Nome;Colega 1;Colega 2")

Os status consultados saem da própria API a cada execução: pega-se tudo e
subtrai-se HELPDESK_STATUS_EXCLUIDOS (default: "Fechado"). HELPDESK_STATUS_ABERTOS
ficou como fallback, usado só quando listTicketStatus não responde.
Rode `--listar-status` para ver os status existentes e `--listar-categorias` para
ver categorias/técnicos reais da fila (HELPDESK_SISTEMAS, HELPDESK_DEV_NAMES).

Somente leitura: nenhum endpoint de escrita é chamado. Os agregados novos
(fila por sistema, por status, por técnico, idade, bloco de desenvolvimento) são
calculados dos MESMOS tickets já baixados por showTicketsByStatus -- nenhuma
chamada extra à API.

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
# Fallback: usado apenas quando listTicketStatus nao responde (ver
# status_para_consultar). Desde a regra global abaixo, a fila NAO sai daqui.
STATUS_ABERTOS = [
    s.strip() for s in os.getenv("HELPDESK_STATUS_ABERTOS", "").split(";") if s.strip()
]
# REGRA GLOBAL (decisao do Guilherme, 17/09/2026): "chamado em aberto" passa a
# ser TUDO que existe no Milldesk MENOS estes status. Antes era o contrario --
# uma lista de inclusao com 6 status, que deixava "Rejeitado", "Aberto",
# "Em Analise" e "Aguardando Deploy" fora de toda conta do painel.
# A comparacao e EXATA (sem acento e sem caixa): "Fechado" nao casa com
# "Fechados". Nome configurado que nao existe na API vira aviso_status.
STATUS_EXCLUIDOS = [
    s.strip() for s in os.getenv("HELPDESK_STATUS_EXCLUIDOS", "Fechado").split(";")
    if s.strip()
]
# Status que significam trabalho ativo na mao de alguem. Somados e exibidos
# juntos no card de cada dev, por pedido do Guilherme.
STATUS_TRABALHO = [
    s.strip() for s in os.getenv(
        "HELPDESK_STATUS_TRABALHO", "Com o Desenvolvedor;Em atendimento",
    ).split(";")
    if s.strip()
]
# Desenvolvedores (nomes como no Milldesk, separados por ";"). Sem isso, o bloco
# de desenvolvimento sai vazio e o dashboard mostra "não configurado".
DEV_NAMES = [
    n.strip() for n in os.getenv("HELPDESK_DEV_NAMES", "").split(";") if n.strip()
]
# Equipes dentro do desenvolvimento. Mesmo formato do mapa de sistemas:
# "Website=Gustavo,Ketlyn,Caique;Mobile=Fulano". Quem nao casar com nenhuma
# equipe cai em HELPDESK_DEV_EQUIPE_PADRAO. Vazio = todo mundo no mesmo balde.
EQUIPE_DEV_PADRAO = os.getenv("HELPDESK_DEV_EQUIPE_PADRAO", "Sistemas")
# Status que significam "esta com o desenvolvimento" (para o funil de dev).
# ATENCAO: o Milldesk desta instalacao tem DOIS status quase iguais --
# "Com o Desenvolvedor" e "Com o Desenvolvimento". Os dois contam; esquecer o
# segundo escondia 77 chamados de 314. A comparacao e por trecho, sem acento e
# sem caixa, entao "com o desenvolv" pegaria os dois -- mas os nomes ficam
# explicitos aqui para nao dependerem de um prefixo comum continuar existindo.
# "Pendente" tambem entra: o Guilherme confirmou que significa parado aguardando
# o desenvolvimento, nao aguardando o cliente. "Aprovacao Interna" fica FORA,
# por decisao dele. Com isso, 279 dos 314 chamados (89%) estao com o dev.
STATUS_DEV = [
    s.strip() for s in os.getenv(
        "HELPDESK_STATUS_DEV",
        "Com o Desenvolvedor;Com o Desenvolvimento;Aguardando Testes;Pendente",
    ).split(";")
    if s.strip()
]
# Mapa sistema -> pedaços de texto procurados na categoria do chamado.
# Formato: "Rotulo=trecho1,trecho2;Outro Rotulo=trecho3". A ordem importa:
# o primeiro rótulo que casar vence. O que não casa cai em "Outros".
# Trechos medidos na fila real (--listar-categorias). A ordem importa: o
# primeiro rotulo que casa vence, entao os mais especificos vem antes.
SISTEMAS_PADRAO = (
    "Siscam 9=siscam 9,siscam9,siscam web,siscam-web;"
    "Siscam 8=siscam 8,siscam8;"
    "Site=site;"
    "Cartórios=cartorio;"
    "Painel de Votação=painel de votacao;"
    "APP Mobile=app mobile,aplicativo mobile;"
    "Legislação Digital=legisla;"
    "Gabinete=gabinete,recepcao;"
    "Infraestrutura=servidor de e-mail,servidor de email"
)


def _parse_sistemas(bruto: str) -> list[tuple[str, list[str]]]:
    """'Site=site;Siscam 9=siscam9,siscam web' -> [('Site', ['site']), ...]"""
    regras: list[tuple[str, list[str]]] = []
    for parte in bruto.split(";"):
        if "=" not in parte:
            continue
        rotulo, _, padroes = parte.partition("=")
        trechos = [normalizar(p) for p in padroes.split(",") if p.strip()]
        if rotulo.strip() and trechos:
            regras.append((rotulo.strip(), trechos))
    return regras



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


def status_para_consultar() -> tuple[list[str], list[str], str | None]:
    """Todos os status da API menos os de HELPDESK_STATUS_EXCLUIDOS.

    Devolve (consultar, disponiveis, aviso). Nunca levanta excecao: se
    listTicketStatus cair, volta o fallback HELPDESK_STATUS_ABERTOS com um
    aviso -- degradacao e requisito deste pipeline, nao cortesia.
    """
    try:
        brutos = listar_status()
    except Exception as e:  # rede, chave invalida, corpo inesperado
        return (list(STATUS_ABERTOS), [],
                f"listTicketStatus indisponivel ({e}); usando HELPDESK_STATUS_ABERTOS")

    disponiveis = [s for s in (str(d.get("status") or "").strip() for d in brutos) if s]
    if not disponiveis:
        return (list(STATUS_ABERTOS), [],
                "listTicketStatus devolveu lista vazia; usando HELPDESK_STATUS_ABERTOS")

    excluir = {normalizar(s) for s in STATUS_EXCLUIDOS}
    consultar = [s for s in disponiveis if normalizar(s) not in excluir]
    if not consultar:
        return (list(STATUS_ABERTOS), disponiveis,
                "todos os status foram excluidos; usando HELPDESK_STATUS_ABERTOS")

    # Nome configurado que nao existe na API nao exclui nada -- e o jeito mais
    # facil de a fila inchar em silencio ("Fechados" no lugar de "Fechado").
    achados = {normalizar(s) for s in disponiveis}
    fantasmas = [s for s in STATUS_EXCLUIDOS if normalizar(s) not in achados]
    aviso = None
    if fantasmas:
        aviso = ("HELPDESK_STATUS_EXCLUIDOS cita status que nao existem na API: "
                 + ", ".join(fantasmas))
    return consultar, disponiveis, aviso


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


# Avaliado aqui (e não junto do resto da config) porque depende de normalizar().
SISTEMAS = _parse_sistemas(os.getenv("HELPDESK_SISTEMAS", SISTEMAS_PADRAO))
ROTULO_OUTROS = "Outros"
# Mesmo parser, mesmo formato: "Rotulo=nome1,nome2". Casa por trecho do nome,
# sem acento e sem caixa -- "Ketlyn" acha "Ketlyn Izidorio".
EQUIPES_DEV = _parse_sistemas(os.getenv("HELPDESK_DEV_EQUIPES", ""))

# Quais sistemas ganham LISTA de chamados no JSON (nao a contagem -- essa e de
# todos, sempre). Guardar os 314 chamados de todos os sistemas fazia o
# helpdesk.json passar de 380 KB, e cada briefing le esse arquivo inteiro.
TICKETS_SISTEMAS = [
    s.strip() for s in os.getenv("HELPDESK_TICKETS_SISTEMAS", "Site;Siscam 9").split(";")
    if s.strip()
]
# Teto de cada lista de chamados. Os mais urgentes primeiro, entao o corte
# descarta os menos urgentes -- nunca os que importam.
TICKETS_LIMITE = int(os.getenv("HELPDESK_TICKETS_LIMITE", "25") or 25)


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


# ----------------------------------------------------------------------------
# Agregados da fila -- tudo calculado dos tickets JA baixados (sem chamada nova)
# ----------------------------------------------------------------------------
FORMATOS_DATA = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                 "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_data(valor) -> datetime | None:
    """Data do Milldesk em qualquer um dos formatos que a API mistura."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    for fmt in FORMATOS_DATA:
        try:
            return datetime.strptime(texto, fmt)
        except (ValueError, TypeError):
            continue
    return None


def dias_ate(valor) -> int | None:
    """Dias daqui ate a data (negativo = ja passou). None se nao der para ler."""
    dt = parse_data(valor)
    return None if dt is None else (dt.date() - date.today()).days


def equipe_do_dev(nome: str) -> str:
    """Equipe de um desenvolvedor (ex.: Website). Sem regra que case, vai para a padrao."""
    alvo = normalizar(nome)
    for rotulo, trechos in EQUIPES_DEV:
        if any(tr in alvo for tr in trechos):
            return rotulo
    return EQUIPE_DEV_PADRAO


def sistema_do_ticket(ticket: dict) -> str:
    """Rotulo de sistema do chamado. A CATEGORIA decide; a subcategoria e reserva.

    A categoria vem preenchida em 100% da fila e ja identifica o sistema. A
    subcategoria fala da natureza do trabalho ("Bug / Erro", "Desenvolvimento de
    Site", "Mobile") e so por acidente parece nome de sistema -- olhar as duas ao
    mesmo tempo faria um chamado de Siscam 8 com subcategoria "Desenvolvimento de
    Site" virar Site. Por isso a subcategoria so e consultada quando NENHUMA
    regra casou com a categoria.
    """
    categoria = normalizar(ticket.get("category", ""))
    for rotulo, trechos in SISTEMAS:
        if any(tr in categoria for tr in trechos):
            return rotulo
    subcategoria = normalizar(ticket.get("subcategory", ""))
    if subcategoria:
        for rotulo, trechos in SISTEMAS:
            if any(tr in subcategoria for tr in trechos):
                return rotulo
    return ROTULO_OUTROS


# Natureza do trabalho, lida da subcategoria. Na fila real "Bug / Erro" (119) e
# "Melhoria / Nova Funcao" (101) sao quase toda a fila: e a divisao entre apagar
# incendio e construir coisa nova, que e o que a diretoria quer enxergar.
# (rotulo, trechos procurados na subcategoria) -- primeiro que casa vence.
NATUREZAS = [
    ("Corretivo", ("bug", "erro", "falha", "lentidao", "travamento")),
    ("Evolutivo", ("melhoria", "nova funcao", "desenvolvimento de")),
    ("Implantação", ("implantacao", "migracao", "treinamento")),
    ("Configuração", ("configuracao", "parametro", "permiss", "certificado")),
]


def natureza_do_ticket(ticket: dict) -> str:
    """Corretivo, evolutivo, implantacao ou configuracao -- a partir da subcategoria."""
    alvo = normalizar(ticket.get("subcategory", ""))
    if not alvo:
        return "(sem informacao)"
    for rotulo, trechos in NATUREZAS:
        if any(tr in alvo for tr in trechos):
            return rotulo
    return "Outros"


def contar(tickets: list[dict], chave: str, vazio: str = "(sem informacao)") -> dict[str, int]:
    """Contagem por campo, em ordem decrescente."""
    contagem: dict[str, int] = {}
    for t in tickets:
        valor = str(t.get(chave) or "").strip() or vazio
        contagem[valor] = contagem.get(valor, 0) + 1
    return dict(sorted(contagem.items(), key=lambda kv: -kv[1]))


def contar_por(tickets: list[dict], funcao) -> dict[str, int]:
    """Igual a contar(), mas a chave vem de uma funcao (ex.: sistema_do_ticket)."""
    contagem: dict[str, int] = {}
    for t in tickets:
        valor = funcao(t)
        contagem[valor] = contagem.get(valor, 0) + 1
    return dict(sorted(contagem.items(), key=lambda kv: -kv[1]))


def dias_aberto(ticket: dict) -> int | None:
    """Ha quantos dias o chamado esta aberto (None quando nao da para ler a data)."""
    d = dias_ate(ticket.get("start"))
    return None if d is None else -d  # start esta no passado


def resumo_idade(tickets: list[dict]) -> dict:
    """Ha quanto tempo os chamados da fila estao abertos.

    Com o SLA fora do sistema (nao estava definido corretamente na origem), a
    idade do chamado e a UNICA medida de urgencia que sobra -- e a que decide
    quais chamados entram nas amostras.
    """
    faixas = {"ate_7_dias": 0, "de_8_a_30_dias": 0, "de_31_a_90_dias": 0,
              "mais_de_90_dias": 0, "sem_data": 0}
    for t in tickets:
        d = dias_ate(t.get("start"))
        if d is None:
            faixas["sem_data"] += 1
            continue
        idade = -d  # start esta no passado: dias_ate devolve negativo
        if idade <= 7:
            faixas["ate_7_dias"] += 1
        elif idade <= 30:
            faixas["de_8_a_30_dias"] += 1
        elif idade <= 90:
            faixas["de_31_a_90_dias"] += 1
        else:
            faixas["mais_de_90_dias"] += 1
    return faixas


def resumir_ticket(t: dict, tecnico: str = "") -> dict:
    """Resumo enxuto de um chamado (o JSON nao guarda os 42 campos crus)."""
    return {
        "id": t.get("id"),
        "tecnico": tecnico or str(t.get("agent") or "").strip(),
        "assunto": t.get("ticket"),
        "solicitante": t.get("requester"),
        "status": t.get("_status_consultado") or t.get("status"),
        "criado_em": t.get("start"),
        "dias_aberto": dias_aberto(t),
        "categoria": t.get("category"),
        "subcategoria": t.get("subcategory"),
        "sistema": sistema_do_ticket(t),
        "natureza": natureza_do_ticket(t),
        "grupo": t.get("group"),
        "prioridade": t.get("priority"),
        "urgencia": t.get("urgency"),
        "departamento": t.get("department"),
    }


def resumo_por_sistema(todos: list[dict]) -> dict[str, dict]:
    """Agregados por sistema calculados sobre a fila INTEIRA.

    Existe porque as listas em tickets_por_sistema sao amostras: contar SLA
    vencido ou achar o mais antigo percorrendo a amostra daria numero errado,
    e errado para menos -- o tipo de erro que ninguem percebe.
    """
    grupos: dict[str, list[dict]] = {}
    for t in todos:
        grupos.setdefault(sistema_do_ticket(t), []).append(t)
    return {
        sistema: {
            "abertos": len(lista),
            "mais_antigo_dias": max(
                (dias_aberto(t) or 0 for t in lista), default=None),
            "acima_de_90_dias": sum(1 for t in lista if (dias_aberto(t) or 0) > 90),
            "por_natureza": contar_por(lista, natureza_do_ticket),
        }
        for sistema, lista in sorted(grupos.items(), key=lambda kv: -len(kv[1]))
    }


def agregar_fila(todos: list[dict]) -> dict:
    """Recortes da fila inteira. Antes isso era jogado fora: so o total sobrevivia."""
    return {
        "por_sistema": contar_por(todos, sistema_do_ticket),
        "por_sistema_resumo": resumo_por_sistema(todos),
        "por_categoria": contar(todos, "category"),
        "por_subcategoria": contar(todos, "subcategory"),
        "por_natureza": contar_por(todos, natureza_do_ticket),
        "por_status": contar(todos, "_status_consultado"),
        "por_tecnico": contar(todos, "agent", vazio="(sem tecnico)"),
        "por_grupo": contar(todos, "group"),
        # Campo livre e sujo na origem ("TI", "Ti", "Informatica", "Informática",
        # "Tecnologia da Informacao e Comunicacao" sao o mesmo setor). Guardado
        # como veio, sem normalizar: nao use para decidir nada sem tratar antes.
        "por_departamento": contar(todos, "department"),
        "por_prioridade": contar(todos, "priority"),
        "por_urgencia": contar(todos, "urgency"),
        "por_tipo": contar(todos, "tickettype"),
        "idade": resumo_idade(todos),
    }


def bloco_desenvolvimento(todos: list[dict]) -> dict:
    """Chamados que estao com o desenvolvimento -- por dev, por sistema, por status.

    Dois recortes que nao se confundem:
      - por_dev: chamados atribuidos a quem esta em HELPDESK_DEV_NAMES;
      - em_status_dev: chamados em status de desenvolvimento (ex.: "Com o
        Desenvolvedor"), tenham dono ou nao.
    """
    alvo_status = [normalizar(s) for s in STATUS_DEV]

    def em_status_dev(t: dict) -> bool:
        atual = normalizar(t.get("_status_consultado") or t.get("status") or "")
        return any(s in atual for s in alvo_status)

    # Trabalho ativo = status EXATO em HELPDESK_STATUS_TRABALHO. Exato de
    # proposito: com a comparacao por trecho usada acima, um status futuro
    # como "Em atendimento externo" entraria na conta sem ninguem perceber.
    alvo_trabalho = {normalizar(s) for s in STATUS_TRABALHO}

    def em_trabalho_ativo(t: dict) -> bool:
        atual = normalizar(t.get("_status_consultado") or t.get("status") or "")
        return atual in alvo_trabalho

    def dev_do_ticket(t: dict) -> str:
        tecnico = normalizar(campo_tecnico(t))
        for nome in DEV_NAMES:
            if normalizar(nome) in tecnico:
                return nome
        return ""

    por_dev: dict[str, list[dict]] = {nome: [] for nome in DEV_NAMES}
    for t in todos:
        nome = dev_do_ticket(t)
        if nome:
            por_dev[nome].append(t)

    em_dev = [t for t in todos if em_status_dev(t)]
    dos_devs = [t for lista in por_dev.values() for t in lista]

    # Agrupa os devs por equipe. Um dev sem chamado em aberto continua aparecendo
    # com zero -- sumir da lista esconderia que ele existe.
    por_equipe: dict[str, dict] = {}
    for nome, lista in por_dev.items():
        equipe = equipe_do_dev(nome)
        bloco = por_equipe.setdefault(equipe, {"devs": [], "tickets": []})
        bloco["devs"].append(nome)
        bloco["tickets"].extend(lista)

    return {
        "configurado": bool(DEV_NAMES),
        "devs_monitorados": DEV_NAMES,
        "equipes_configuradas": [r for r, _ in EQUIPES_DEV],
        "equipe_padrao": EQUIPE_DEV_PADRAO,
        "por_equipe": {
            equipe: {
                "devs": bloco["devs"],
                "abertos": len(bloco["tickets"]),
                "por_sistema": contar_por(bloco["tickets"], sistema_do_ticket),
                "por_natureza": contar_por(bloco["tickets"], natureza_do_ticket),
                "acima_de_90_dias": sum(
                    1 for t in bloco["tickets"] if (dias_aberto(t) or 0) > 90),
            }
            for equipe, bloco in sorted(
                por_equipe.items(), key=lambda kv: -len(kv[1]["tickets"]))
        },
        "status_considerados_dev": STATUS_DEV,
        "status_considerados_trabalho": STATUS_TRABALHO,
        "total_atribuidos_a_devs": len(dos_devs),
        "total_em_status_dev": len(em_dev),
        "por_dev": {
            nome: {
                "abertos": len(lista),
                # Espelho explicito de "abertos": com a regra global, e o
                # total de chamados no nome do dev em qualquer status menos
                # Fechado. Campo proprio porque os prompts dos .bat e os
                # geradores citam nome de campo literalmente.
                "total_no_nome": len(lista),
                "em_trabalho": sum(1 for t in lista if em_trabalho_ativo(t)),
                "novos_hoje": sum(1 for t in lista if eh_de_hoje(t.get("start", ""))),
                "por_sistema": contar_por(lista, sistema_do_ticket),
                "por_natureza": contar_por(lista, natureza_do_ticket),
                "por_status": contar(lista, "_status_consultado"),
                "equipe": equipe_do_dev(nome),
                "acima_de_90_dias": sum(1 for t in lista if (dias_aberto(t) or 0) > 90),
                "mais_antigo_dias": max(
                    (dias_aberto(t) or 0 for t in lista), default=None),
            }
            for nome, lista in por_dev.items()
        },
        "em_status_dev_por_sistema": contar_por(em_dev, sistema_do_ticket),
        "em_status_dev_por_natureza": contar_por(em_dev, natureza_do_ticket),
        "em_status_dev_por_status": contar(em_dev, "_status_consultado"),
        # Amostra dos mais urgentes, nao a lista toda: total_em_status_dev acima
        # e que diz quantos existem de verdade.
        "tickets_em_status_dev": ordenar_por_antiguidade(
            [resumir_ticket(t) for t in em_dev])[:TICKETS_LIMITE],
        "tickets_em_status_dev_amostra_de": len(em_dev),
    }


def ordenar_por_antiguidade(resumos: list[dict]) -> list[dict]:
    """Aberto ha mais tempo primeiro.

    Era ordenado por SLA, mas o SLA nao estava definido corretamente na origem e
    saiu do sistema. A idade e o unico sinal de urgencia que sobra -- e e ela que
    decide quais chamados entram nas amostras, entao o corte descarta os mais
    recentes, nunca os mais antigos.
    """
    return sorted(resumos, key=lambda x: -(x.get("dias_aberto") or 0))


def tickets_dos_sistemas(todos: list[dict], rotulos: list[str]) -> dict[str, list[dict]]:
    """AMOSTRA dos chamados mais urgentes dos sistemas que o briefing cita.

    So os sistemas em HELPDESK_TICKETS_SISTEMAS, e no maximo TICKETS_LIMITE cada,
    os abertos ha mais tempo primeiro.
    As CONTAGENS de todos os sistemas continuam completas em fila.por_sistema e
    fila.por_sistema_resumo -- aqui e so o detalhe para citar chamado por id.
    """
    saida: dict[str, list[dict]] = {r: [] for r in rotulos}
    for t in todos:
        s = sistema_do_ticket(t)
        if s in saida:
            saida[s].append(resumir_ticket(t))
    return {r: ordenar_por_antiguidade(lista)[:TICKETS_LIMITE] for r, lista in saida.items()}


def main() -> None:
    # Modo utilitário: descobrir os status do seu Milldesk
    if "--listar-status" in sys.argv:
        print("Status existentes no seu Milldesk:")
        for s in listar_status():
            print(f"  - {s.get('status')}  ({s.get('description', '')})")
        print("\nCopie os que significam 'em aberto' para o .env, separados por ';'")
        print("Ex.: HELPDESK_STATUS_ABERTOS='Aberto;Em atendimento;Aguardando'")
        return

    # Modo utilitario: ver os valores REAIS da fila para configurar
    # HELPDESK_SISTEMAS e HELPDESK_DEV_NAMES sem chutar nome de categoria.
    if "--listar-categorias" in sys.argv:
        status_consultar, _, aviso = status_para_consultar()
        if aviso:
            print(f"AVISO: {aviso}\n")
        if not status_consultar:
            raise SystemExit(
                "Nenhum status para consultar: listTicketStatus falhou e "
                "HELPDESK_STATUS_ABERTOS esta vazio no .env."
            )
        excluidos = ", ".join(STATUS_EXCLUIDOS) or "nenhum"
        print(f"Consultando {len(status_consultar)} status (excluidos: {excluidos}):")
        print("  " + ", ".join(status_consultar) + "\n")
        fila: list[dict] = []
        for status in status_consultar:
            fila.extend(tickets_por_status(status))
        print(f"Fila com {len(fila)} chamados abertos.\n")
        for titulo, chave in [("CATEGORIAS", "category"), ("SUBCATEGORIAS", "subcategory"),
                              ("DEPARTAMENTOS", "department"), ("GRUPOS", "group"),
                              ("TECNICOS", "agent"), ("STATUS", "_status_consultado"),
                              ("TIPOS", "tickettype")]:
            print(f"--- {titulo} ---")
            for valor, qtd in list(contar(fila, chave).items())[:40]:
                print(f"  {qtd:5d}  {valor}")
            print()
        print("--- COMO O MAPA ATUAL CLASSIFICA (HELPDESK_SISTEMAS) ---")
        for rotulo, qtd in contar_por(fila, sistema_do_ticket).items():
            print(f"  {qtd:5d}  {rotulo}")
        print("\n--- NATUREZA DO TRABALHO (lida da subcategoria) ---")
        for rotulo, qtd in contar_por(fila, natureza_do_ticket).items():
            print(f"  {qtd:5d}  {rotulo}")
        print("\n--- QUANTOS ESTAO COM O DESENVOLVIMENTO (HELPDESK_STATUS_DEV) ---")
        alvo = [normalizar(s) for s in STATUS_DEV]
        em_dev = [t for t in fila
                  if any(s in normalizar(t.get("_status_consultado") or "") for s in alvo)]
        print(f"  {len(em_dev)} de {len(fila)} chamados")
        print("\nAjuste HELPDESK_SISTEMAS no .env se algo caiu em 'Outros' sem querer.")
        print("Formato: 'Rotulo=trecho1,trecho2;Outro=trecho3' (trecho casa sem acento e sem caixa).")
        return

    # 1) Busca os chamados de TODOS os status menos os excluidos. A lista sai da
    #    API a cada execucao: status novo criado no Milldesk entra sozinho, sem
    #    ninguem precisar lembrar de mexer no .env.
    status_consultar, status_disponiveis, aviso_status = status_para_consultar()
    if aviso_status:
        print(f"AVISO: {aviso_status}")
    if not status_consultar:
        raise SystemExit(
            "Nenhum status para consultar: listTicketStatus falhou e "
            "HELPDESK_STATUS_ABERTOS esta vazio no .env.\n"
            "Rode: python coletores/check_helpdesk.py --listar-status"
        )

    todos: list[dict] = []
    for status in status_consultar:
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
            "dias_aberto": dias_aberto(t),
            "categoria": t.get("category"),
            "prioridade": t.get("priority"),
            "urgencia": t.get("urgency"),
        }

    # 3) Visão geral da equipe (contagem por técnico)
    try:
        por_tecnico = chamar("ticketsByAgent")
    except Exception as e:
        por_tecnico = f"indisponível ({e})"

    agregados = agregar_fila(todos)
    resultado_por_sistema = agregados["por_sistema"]

    # Mesma fila, restrita aos status que ERAM consultados antes da regra global.
    # Existe so para o historico nao ganhar um degrau falso no dia da virada: e
    # esta serie que continua comparavel com os dias ja gravados.
    base_anterior = {normalizar(s) for s in STATUS_ABERTOS}
    total_base_anterior = sum(
        1 for t in todos
        if normalizar(t.get("_status_consultado") or "") in base_anterior
    ) if base_anterior else None

    resultado = {
        "fonte": "helpdesk (Milldesk)",
        "data": date.today().isoformat(),
        "coletado_em": datetime.now().isoformat(),
        "fila_total_abertos": len(todos),
        "fila_total_base_anterior": total_base_anterior,
        # Trilha de auditoria da regra "tudo menos Fechado": sem isso ninguem
        # consegue explicar por que o total mudou de um dia para o outro.
        "status_consultados": status_consultar,
        "status_disponiveis": status_disponiveis,
        "status_excluidos": STATUS_EXCLUIDOS,
        "aviso_status": aviso_status,
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
        # Recortes da fila inteira (os 42 campos de cada ticket ja estavam aqui;
        # antes so o total sobrevivia). Nenhuma chamada extra a API.
        "fila": agregados,
        "desenvolvimento": bloco_desenvolvimento(todos),
        "tickets_por_sistema": tickets_dos_sistemas(todos, TICKETS_SISTEMAS),
        "tickets_por_sistema_amostra_de": {
            r: resultado_por_sistema.get(r, 0) for r in TICKETS_SISTEMAS
        },
        "sistemas_configurados": [r for r, _ in SISTEMAS],
        "_debug_campos_do_primeiro_ticket": list(todos[0].keys()) if todos else [],
        "atendimentos_ultimo_dia_util": atendimentos_do_dia(ultimo_dia_util()),
    }

    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OK -> {SAIDA}")
    excluidos = ", ".join(STATUS_EXCLUIDOS) or "nenhum"
    print(f"Status consultados: {len(status_consultar)} de "
          f"{len(status_disponiveis) or len(status_consultar)} (excluidos: {excluidos})")
    if total_base_anterior is not None:
        print(f"Fila na base anterior (HELPDESK_STATUS_ABERTOS): {total_base_anterior}")
    detalhe = " | ".join(f"{n}: {len(l)}" for n, l in por_agente.items())
    print(f"Fila: {len(todos)} abertos | Monitorados: {len(meus)}" + (f" ({detalhe})" if detalhe else ""))
    sistemas = " | ".join(f"{k}: {v}" for k, v in resultado["fila"]["por_sistema"].items())
    print(f"Por sistema: {sistemas}")
    dev = resultado["desenvolvimento"]
    if dev["configurado"]:
        print(f"Desenvolvimento: {dev['total_atribuidos_a_devs']} atribuidos a devs | "
              f"{dev['total_em_status_dev']} em status de dev")
        for equipe, bloco in dev["por_equipe"].items():
            print(f"  equipe {equipe}: {bloco['abertos']} abertos "
                  f"({len(bloco['devs'])} devs) -> {bloco['por_sistema']}")
    else:
        print("Desenvolvimento: HELPDESK_DEV_NAMES nao configurado (bloco sai vazio)")
    idade = resultado["fila"]["idade"]
    print(f"Abertos ha mais de 90 dias: {idade['mais_de_90_dias']} de {len(todos)}")


if __name__ == "__main__":
    main()