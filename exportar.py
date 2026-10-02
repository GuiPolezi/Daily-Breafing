"""Gera um arquivo de dados por PÚBLICO do site novo (dados/site/<publico>.json).

Quem vê o quê é decidido AQUI, no dado, e não na tela: cada público recebe um
arquivo próprio, montado campo a campo (lista de permissão), e no servidor a
permissão NTFS é por arquivo. Esconder coisa por JavaScript não é controle.

  suporte          tudo, inclusive chamado por chamado (assunto, solicitante)
  desenvolvimento  igual ao suporte por enquanto (restrição ainda a definir)
  diretor          só agregados de suporte e desenvolvimento + a agenda;
                   nenhum assunto de chamado, nenhum solicitante
  licencas         só licenças; nenhum nome de pessoa, com ou sem flag

Não consulta nenhuma API: lê dados/briefing.db (coletar.py) e os JSONs que os
coletores de licenças e agenda já gravam. Fonte ausente ou corrompida vira
bloco `{"indisponivel": true}`; o resto sai normalmente.

Uso:
    python exportar.py
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import banco

RAIZ = Path(__file__).resolve().parent
DADOS = RAIZ / "dados"
SAIDA = DADOS / "site"


def _flag_ranking() -> bool:
    """False esconde nomes da equipe. Só as grafias de "sim" mostram: qualquer
    outro valor (erro de digitação, vazio, "false # comentário") erra para o
    lado de esconder. Sem a variável no .env, mostra."""
    try:
        from dotenv import load_dotenv
        load_dotenv(RAIZ / ".env")
    except ImportError:
        pass
    valor = os.getenv("DASHBOARD_MOSTRAR_RANKING", "true").strip().strip("'").strip('"').lower()
    return valor in ("true", "1", "sim", "yes", "on")


MOSTRAR_RANKING = _flag_ranking()

INDISPONIVEL = {"indisponivel": True}
# Campos de uma licença que podem sair (cliente é a organização, não uma pessoa).
CAMPOS_LICENCA = ("cliente", "sistema", "vencimento", "dias")
CAMPOS_EVENTO_AGENDA = ("inicio", "fim", "titulo", "local", "marcador", "calendarios", "reuniao_online")
CAMPOS_DIA_INTEIRO = ("titulo", "calendarios")
# O que o diretor recebe de cada bloco de metricas.py (lista de permissão).
CAMPOS_FRESCOR = ("gerado_em", "ultima_coleta", "ultima_coleta_modo", "ultima_coleta_min", "ultima_completa",
                  "ultima_completa_min", "historico_desde", "avisos_na_ultima_coleta", "limite_atingido")
CAMPOS_RESUMO = ("abertos", "acima_de_90_dias", "mais_antigo_dias")
CAMPOS_DIA = ("criados_clientes", "criados_internos", "fechados", "atendimentos")
CAMPOS_RESOLUCAO = ("fechados", "mediana_horas", "no_mesmo_dia", "mais_de_7_dias")


class ConfigAusente(Exception):
    """Falta uma variável no .env (não é problema do banco)."""


def _simples(valor):
    """O valor, se for texto/número/booleano; senão None. Lista ou dicionário
    pendurado num campo permitido não passa de carona."""
    return valor if valor is None or isinstance(valor, (str, int, float, bool)) else None


def _metricas():
    """`metricas` carrega check_helpdesk, que exige a chave da API no ambiente."""
    try:
        import metricas
    except KeyError as e:
        raise ConfigAusente(f"{e.args[0]} não está no .env") from None
    return metricas


def ler_json(nome: str) -> dict | None:
    try:
        dados = json.loads((DADOS / nome).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dados if isinstance(dados, dict) else None


def bloco_licencas() -> dict:
    bruto = ler_json("licencas.json")
    if bruto is None:
        return dict(INDISPONIVEL)

    def lista(chave: str) -> list[dict]:
        itens = bruto.get(chave)
        return [{c: _simples(lic.get(c)) for c in CAMPOS_LICENCA}
                for lic in (itens if isinstance(itens, list) else []) if isinstance(lic, dict)]

    return {
        "tipo": "estoque",
        "coletado_em": _simples(bruto.get("coletado_em")),
        "janela_dias": _simples(bruto.get("janela_dias")),
        "vencendo_em_breve": lista("vencendo_em_breve"),
        "vencidas_recentes": lista("vencidas_recentes"),
        "vencidas_antigas_total": _simples(bruto.get("vencidas_antigas_total")),
        "vencendo_alem_da_janela": _simples(bruto.get("vencendo_alem_da_janela")),
    }


def bloco_agenda() -> dict:
    if not MOSTRAR_RANKING:
        # Título e calendário de evento são nomes de pessoas, e não dá para
        # tirá-los de dentro de um título: com a flag de esconder nomes, a agenda
        # inteira fica de fora.
        return {"oculta": True}
    bruto = ler_json("agenda.json")
    if bruto is None or bruto.get("erro"):
        return dict(INDISPONIVEL)
    def lista(chave: str, campos: tuple) -> list[dict]:
        itens = bruto.get(chave)
        return [{c: campo(c, e.get(c)) for c in campos}
                for e in (itens if isinstance(itens, list) else []) if isinstance(e, dict)]

    def campo(nome: str, valor):
        if nome == "calendarios":   # o único campo que é lista: só de textos
            return [x for x in valor if isinstance(x, str)] if isinstance(valor, list) else []
        return _simples(valor)

    # A agenda é a da empresa (os calendários da equipe), decisão do Guilherme
    # em 01/10/2026: fica só na página do diretor. "Horas ocupadas" e "janela
    # livre" são do calendário principal de quem coleta e não saem daqui.
    return {
        "coletado_em": _simples(bruto.get("coletado_em")),
        "data": _simples(bruto.get("data")),
        "eventos": lista("eventos", CAMPOS_EVENTO_AGENDA),
        "dia_inteiro": lista("dia_inteiro", CAMPOS_DIA_INTEIRO),
    }


def bloco_chamados(con, agora: datetime) -> dict:
    """Tudo que sai do banco, no nível de detalhe do suporte.

    `metricas` é importado aqui dentro: ele carrega check_helpdesk, que exige a
    chave da API no ambiente. Se faltar, é esta fonte que fica indisponível --
    licenças e agenda saem do mesmo jeito.
    """
    metricas = _metricas()
    # uma leitura só: sem isto, uma coleta gravando no meio deixaria estoque e
    # fluxo de instantes diferentes no mesmo arquivo
    con.execute("BEGIN")
    try:
        if con.execute("SELECT COUNT(*) FROM coletas").fetchone()[0] == 0:
            raise LookupError("banco sem nenhuma coleta")   # vazio não é "zero chamados"
        return {
            "frescor": metricas.frescor(con, agora),
            "estoque": metricas.estoque(con, agora),
            "fluxo": metricas.fluxo(con, agora),
            "resolucao": metricas.resolucao(con, agora),
            "eventos": metricas.eventos_recentes(con),
        }
    finally:
        con.rollback()


def recorte_diretor(chamados: dict) -> dict:
    """Só agregados: sem lista de chamados, sem eventos, sem solicitante.

    Montado campo a campo, em TODOS os níveis, de propósito -- campo novo em
    metricas.py NÃO chega ao diretor sem alguém decidir aqui que pode.
    """
    def so(origem, campos: tuple) -> dict:
        """Só os campos listados, e só valor simples: uma lista ou um dicionário
        pendurado num campo permitido não passa de carona."""
        if not isinstance(origem, dict):
            return {}
        return {c: origem[c] for c in campos if c in origem and _simples(origem[c]) is origem[c]}

    def numeros(origem: dict) -> dict:
        """{rótulo: contagem}: só passa o que é número."""
        if not isinstance(origem, dict):
            return {}
        return {str(k): v for k, v in origem.items() if isinstance(v, int) and not isinstance(v, bool)}

    estoque, fluxo, resol = chamados["estoque"], chamados["fluxo"], chamados["resolucao"]
    dev = estoque["desenvolvimento"]
    saida = {
        "frescor": so(chamados["frescor"], CAMPOS_FRESCOR),
        "estoque": {
            **so(estoque, ("tipo", "total_abertos", "aguardando_confirmacao")),
            "idade": numeros(estoque["idade"]),
            "por_sistema": {s: {**so(b, CAMPOS_RESUMO), "por_natureza": numeros(b.get("por_natureza"))}
                            for s, b in estoque["por_sistema"].items() if isinstance(b, dict)},
            "por_status": numeros(estoque["por_status"]),
            "por_natureza": numeros(estoque["por_natureza"]),
            "desenvolvimento": {
                **so(dev, ("em_status_dev", "atribuidos_a_devs")),
                "em_status_dev_por_sistema": numeros(dev["em_status_dev_por_sistema"]),
                "status_considerados_dev": [str(x) for x in dev["status_considerados_dev"]],
                "status_considerados_trabalho": [str(x) for x in dev["status_considerados_trabalho"]],
            },
        },
        "fluxo": {
            **so(fluxo, ("tipo", "inicio", "fim", "dias")),
            "por_dia": {dia: so(bloco, CAMPOS_DIA) for dia, bloco in fluxo["por_dia"].items()},
            "criados_por_cliente": {c: {**so(b, ("total",)), "por_sistema": numeros(b.get("por_sistema"))}
                                    for c, b in fluxo["criados_por_cliente"].items() if isinstance(b, dict)},
            "criados_por_sistema": numeros(fluxo["criados_por_sistema"]),
        },
        "resolucao": {**so(resol, ("tipo", "dias", "fechados_sem_data") + CAMPOS_RESOLUCAO),
                      "por_sistema": {s: so(b, CAMPOS_RESOLUCAO) for s, b in resol["por_sistema"].items()
                                      if isinstance(b, dict)}},
    }
    if MOSTRAR_RANKING:   # nome de técnico e de dev só com a flag ligada
        saida["estoque"]["desenvolvimento"]["por_dev"] = {
            nome: {**so(b, CAMPOS_RESUMO + ("em_trabalho", "equipe")),
                   "por_sistema": numeros(b.get("por_sistema")), "por_status": numeros(b.get("por_status"))}
            for nome, b in dev["por_dev"].items() if isinstance(b, dict)}
        for dia, bloco in fluxo["por_dia"].items():
            saida["fluxo"]["por_dia"][dia]["atendimentos_por_tecnico"] = numeros(
                bloco.get("atendimentos_por_tecnico") if isinstance(bloco, dict) else None)
    return saida


def montar(agora: datetime | None = None, con=None) -> dict[str, dict]:
    """{publico: conteúdo}. Nunca levanta por fonte ausente."""
    agora = agora or datetime.now()
    chamados = diretor = None
    try:
        proprio = con is None
        if proprio and not banco.ARQUIVO.exists():
            raise FileNotFoundError("banco ainda não criado")   # conectar() criaria um vazio
        con = con or banco.conectar()
        try:
            chamados = bloco_chamados(con, agora)
        finally:
            if proprio:
                con.close()
        diretor = recorte_diretor(chamados)
    except Exception as e:   # banco ausente/corrompido: o site mostra a fonte fora do ar
        chamados = diretor = None
        motivo = (f"configuração ausente ({e})" if isinstance(e, ConfigAusente)
                  else f"banco indisponível ({type(e).__name__})")
        print(f"AVISO: {motivo}; páginas de chamados saem vazias")

    gerado = agora.isoformat(timespec="seconds")
    return {
        "suporte": {"publico": "suporte", "gerado_em": gerado, "chamados": chamados or dict(INDISPONIVEL)},
        "desenvolvimento": {"publico": "desenvolvimento", "gerado_em": gerado,
                            "chamados": chamados or dict(INDISPONIVEL)},
        "diretor": {"publico": "diretor", "gerado_em": gerado,
                    "chamados": diretor or dict(INDISPONIVEL),
                    "licencas_resumo": resumo_licencas(), "agenda": bloco_agenda()},
        "licencas": {"publico": "licencas", "gerado_em": gerado, "licencas": bloco_licencas()},
    }


def resumo_licencas() -> dict:
    """Para o diretor: só as contagens."""
    lic = bloco_licencas()
    if lic.get("indisponivel"):
        return lic
    return {"coletado_em": lic["coletado_em"], "janela_dias": lic["janela_dias"],
            "vencendo_em_breve": len(lic["vencendo_em_breve"]),
            "vencidas_recentes": len(lic["vencidas_recentes"]),
            "vencidas_antigas_total": lic["vencidas_antigas_total"]}


def gravar(publicos: dict[str, dict], pasta: Path = SAIDA) -> list[Path]:
    """Grava cada arquivo inteiro ou não grava: temporário + troca atômica.

    `pasta` é área de PREPARO, privada (dados/site). NUNCA aponte para a pasta
    que o IIS serve: a troca atômica recria o arquivo, e arquivo recriado herda
    a permissão da pasta -- a restrição NTFS por público sumiria a cada
    execução. A publicação copia o conteúdo por cima do arquivo que já existe
    (shutil.copyfile, como publicar.py faz), preservando a permissão.
    O temporário fica FORA de `pasta` e é apagado mesmo se a gravação falhar.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    gravados = []
    for nome, conteudo in publicos.items():
        if not nome.isidentifier():
            raise ValueError(f"nome de público inválido: {nome!r}")
        destino = pasta / f"{nome}.json"
        descritor, temporario = tempfile.mkstemp(dir=str(pasta.parent), prefix=".exportar-", suffix=".tmp")
        try:
            with os.fdopen(descritor, "w", encoding="utf-8") as f:
                f.write(json.dumps(conteudo, ensure_ascii=False))
            os.replace(temporario, str(destino))
        finally:
            if os.path.exists(temporario):
                os.unlink(temporario)
        gravados.append(destino)
    return gravados


def main() -> None:
    for caminho in gravar(montar()):
        print(f"OK -> {caminho} ({caminho.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
