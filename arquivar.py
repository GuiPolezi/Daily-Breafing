"""Arquiva as métricas-chave do dia em historico/metricas.jsonl.

Uma linha JSON por dia (memória compacta do agente). Se rodar duas vezes
no mesmo dia, a linha do dia é substituída pela mais recente.

Também escreve historico/licencas.jsonl: um retrato diário do conjunto de
licenças (cliente + sistema + vencimento), que é o que permite responder
"o que renovou, o que caiu e o que entrou novo" comparando dois dias. O
coletor de licenças já baixa tudo isso — aqui só guardamos a série.

E escreve historico/atribuicoes.jsonl: um retrato diário de QUAIS chamados
estão com cada dev (só ids). O Milldesk não tem histórico de atribuição; o
chamado que aparece com o dev e não estava com ele no retrato anterior conta
como atribuído (dev_atribuidos_novos em metricas.jsonl).

Campos novos nunca podem quebrar a leitura das linhas antigas: quem lê usa
.get(), nunca indexação direta.
"""

from __future__ import annotations  # o servidor roda Python 3.8 (sem `X | None`)

import json
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DADOS = RAIZ / "dados"
HISTORICO = RAIZ / "historico"
ARQUIVO = HISTORICO / "metricas.jsonl"
ARQUIVO_LICENCAS = HISTORICO / "licencas.jsonl"
ARQUIVO_ATRIBUICOES = HISTORICO / "atribuicoes.jsonl"
# Retrato anterior mais velho que isto não serve de base: depois de uma pausa na
# coleta, tudo o que entrou no intervalo cairia num dia só e inflaria a semana.
# 5 dias cobre fim de semana e feriado prolongado (sexta -> quarta).
ATRIBUICOES_BASE_MAX_DIAS = 5


def ler(nome: str) -> dict:
    caminho = DADOS / nome
    if not caminho.exists():
        return {}
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def dic(valor) -> dict:
    """Garante dict: campo ausente ou de tipo inesperado vira {}."""
    return valor if isinstance(valor, dict) else {}


def gravar_linha_do_dia(arquivo: Path, registro: dict) -> int:
    """Append-only com substituição por dia: uma linha por 'data'."""
    HISTORICO.mkdir(exist_ok=True)
    linhas: list[str] = []
    if arquivo.exists():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            try:
                if json.loads(linha).get("data") == registro["data"]:
                    continue  # a do dia é substituída
            except json.JSONDecodeError:
                continue  # linha corrompida é descartada, o resto sobrevive
            linhas.append(linha)
    linhas.append(json.dumps(registro, ensure_ascii=False))
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return len(linhas)


def metricas_do_dia(helpdesk: dict, licencas: dict) -> dict:
    atend = dic(helpdesk.get("atendimentos_ultimo_dia_util"))
    fila = dic(helpdesk.get("fila"))
    por_sistema = dic(fila.get("por_sistema"))
    por_natureza = dic(fila.get("por_natureza"))
    idade = dic(fila.get("idade"))
    dev = dic(helpdesk.get("desenvolvimento"))

    return {
        "data": date.today().isoformat(),
        # Help desk (fila)
        "fila_abertos": helpdesk.get("fila_total_abertos"),
        # Mesma fila no recorte de status que valia antes de 17/09/2026 ("tudo
        # menos Fechado" substituiu uma lista de 6 status). E a serie que segue
        # comparavel com os dias anteriores; fila_abertos tem um degrau naquele
        # dia. fila_status_qtd marca a virada: quando muda de um dia para o
        # outro, os dashboards suprimem o badge de comparacao.
        "fila_abertos_base_anterior": helpdesk.get("fila_total_base_anterior"),
        "fila_status_qtd": len(helpdesk.get("status_consultados") or []) or None,
        "meus_abertos": helpdesk.get("meus_abertos"),
        # Fila por sistema (Milldesk, campo category)
        "fila_site": por_sistema.get("Site"),
        "fila_siscam9": por_sistema.get("Siscam 9"),
        "fila_siscam8": por_sistema.get("Siscam 8"),
        "fila_por_sistema": por_sistema or None,
        # Natureza do trabalho (subcategoria): corretivo = apagar incendio,
        # evolutivo = construir coisa nova. A razao entre os dois ao longo do
        # tempo e o que mostra se a equipe esta saindo do modo reativo.
        "fila_corretivo": por_natureza.get("Corretivo"),
        "fila_evolutivo": por_natureza.get("Evolutivo"),
        "fila_por_natureza": por_natureza or None,
        # Envelhecimento da fila. Substituiu o SLA, que nao estava definido
        # corretamente na origem e saiu do sistema.
        "fila_mais_90": idade.get("mais_de_90_dias"),
        "fila_ate_7": idade.get("ate_7_dias"),
        # Desenvolvimento
        "dev_atribuidos": dev.get("total_atribuidos_a_devs"),
        "dev_em_status": dev.get("total_em_status_dev"),
        "dev_por_equipe": {
            equipe: dic(bloco).get("abertos")
            for equipe, bloco in dic(dev.get("por_equipe")).items()
        } or None,
        "dev_por_pessoa": {
            nome: dic(bloco).get("abertos")
            for nome, bloco in dic(dev.get("por_dev")).items()
        } or None,
        "dev_por_pessoa_trabalho": {
            nome: dic(bloco).get("em_trabalho")
            for nome, bloco in dic(dev.get("por_dev")).items()
        } or None,
        # Produtividade (referente ao último dia útil)
        "atend_dia_ref": atend.get("dia"),
        "atend_total": atend.get("total_atendimentos_fechados"),
        "atend_por_tecnico": atend.get("por_tecnico"),
        # Chamados abertos por cliente (location) no mesmo dia de referência, com a
        # quebra por sistema: {cliente: {total, por_sistema}}. Linhas antes de
        # 30/09/2026 não têm; o semanal soma só os dias que têm. {} = dia coletado
        # sem nenhum chamado de cliente; None = dado ausente (não é zero).
        "criados_por_cliente": (dic(atend.get("chamados_criados")).get("por_cliente")
                                if isinstance(dic(atend.get("chamados_criados")).get("por_cliente"), dict)
                                else None),
        # Dias cobertos pela contagem acima (segunda = sexta..domingo). Auditoria.
        "criados_periodo": dic(dic(atend.get("chamados_criados")).get("periodo")) or None,
        # "Fila de Chamados" do semanal: chamados abertos por clientes no período
        # (sem internos e sem atendimento diário) e a quebra por sistema.
        **criados_do_dia(dic(atend.get("chamados_criados"))),
        # Licenças
        "lic_vencendo": len(licencas.get("vencendo_em_breve") or []),
        "lic_vencidas_recentes": len(licencas.get("vencidas_recentes") or []),
        # período das duas listas (dias). Linhas antigas não têm: eram 60 para as
        # vencidas e sem corte para as vencendo. É o que denuncia o degrau da troca.
        "lic_janela_dias": licencas.get("janela_dias"),
    }


def criados_do_dia(criados: dict) -> dict:
    """criados_total e criados_por_sistema; None nos dois quando a coleta não trouxe o bloco."""
    por_cliente = criados.get("por_cliente")
    if not isinstance(por_cliente, dict):
        return {"criados_total": None, "criados_por_sistema": None}
    por_sistema: dict[str, int] = {}
    for bloco in por_cliente.values():
        for sistema, qtd in dic(dic(bloco).get("por_sistema")).items():
            if isinstance(qtd, int) and not isinstance(qtd, bool):
                por_sistema[sistema] = por_sistema.get(sistema, 0) + qtd
    total = criados.get("total")
    if not isinstance(total, int) or isinstance(total, bool):
        return {"criados_total": None, "criados_por_sistema": None}  # dado inválido = ausente, nos dois
    return {
        "criados_total": total,
        "criados_por_sistema": dict(sorted(por_sistema.items(), key=lambda kv: -kv[1])),
    }


def ids_por_dev(helpdesk: dict) -> dict[str, list[str]] | None:
    """{dev: [ids em aberto]} da coleta de hoje; None se o coletor não trouxe (versão antiga)."""
    por_dev = dic(dic(helpdesk.get("desenvolvimento")).get("por_dev"))
    ids = {nome: bloco["ids_abertos"] for nome, bloco in por_dev.items()
           if isinstance(bloco, dict) and isinstance(bloco.get("ids_abertos"), list)}
    return ids or None


def retrato_anterior(arquivo: Path, hoje: str) -> dict | None:
    """Último retrato com data ANTERIOR a hoje (rodar duas vezes no dia não compara consigo mesmo)."""
    if not arquivo.exists():
        return None
    melhor = None
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(linha)
        except json.JSONDecodeError:
            continue
        data = r.get("data") if isinstance(r, dict) else None
        if isinstance(data, str) and data < hoje and isinstance(r.get("ids_por_dev"), dict):
            if melhor is None or data > melhor["data"]:
                melhor = r
    return melhor


def regua_status(helpdesk: dict) -> int | None:
    """Quantos status entraram na fila hoje (o mesmo marcador de fila_status_qtd)."""
    return len(helpdesk.get("status_consultados") or []) or None


def motivo_sem_base(anterior: dict | None, hoje_iso: str, regua_hoje: int | None) -> str | None:
    """Por que o retrato anterior NÃO serve para medir atribuição (None = serve).

    Diferença de retratos só mede atribuição se os dois retratos saíram da mesma
    régua e estão perto um do outro. Senão o "novo" é degrau falso, igual ao da fila
    em 17/09/2026: status novo no Milldesk (ou HELPDESK_STATUS_EXCLUIDOS editado) faz
    chamados aparecerem no nome do dev sem ninguém ter atribuído nada.
    """
    if not anterior or not isinstance(anterior.get("ids_por_dev"), dict):
        return "primeiro retrato (linha de base)"
    if anterior.get("regua_status") != regua_hoje:
        return "a régua de status mudou desde o retrato anterior"
    try:
        dias = (date.fromisoformat(hoje_iso) - date.fromisoformat(str(anterior.get("data")))).days
    except ValueError:
        return "data do retrato anterior ilegível"
    if dias > ATRIBUICOES_BASE_MAX_DIAS:
        return f"retrato anterior tem {dias} dias (máximo {ATRIBUICOES_BASE_MAX_DIAS})"
    return None


def atribuicoes_novas(hoje: dict[str, list[str]], anterior: dict | None,
                      hoje_iso: str | None = None, regua_hoje: int | None = None) -> dict:
    """Chamados que entraram na carga de cada dev desde o retrato anterior.

    Sem base válida (primeiro dia, régua de status diferente, retrato velho demais),
    tudo vira None e o motivo vai em dev_atribuidos_aviso: melhor "sem dado" do que um
    número inflado. Dev que não existia no retrato anterior (acabou de entrar na
    config) também fica None -- senão a carga inteira dele pareceria atribuída num dia.
    """
    motivo = motivo_sem_base(anterior, hoje_iso or date.today().isoformat(), regua_hoje)
    if motivo:
        return {"dev_atribuidos_novos": {nome: None for nome in hoje},
                "dev_atribuidos_desde": None, "dev_atribuidos_aviso": motivo}
    base = anterior["ids_por_dev"]
    novos = {}
    for nome, ids in hoje.items():
        antes = base.get(nome)
        novos[nome] = None if not isinstance(antes, list) else len(set(map(str, ids)) - set(map(str, antes)))
    return {"dev_atribuidos_novos": novos, "dev_atribuidos_desde": anterior.get("data")}


def retrato_licencas(licencas: dict) -> dict | None:
    """Retrato do dia: cada licença com seu estado, para comparar dia a dia."""
    if not licencas:
        return None
    vencendo = licencas.get("vencendo_em_breve") or []
    vencidas = licencas.get("vencidas_recentes") or []
    if not isinstance(vencendo, list) or not isinstance(vencidas, list):
        return None

    itens = []
    for lista, estado in ((vencendo, "vencendo"), (vencidas, "vencida")):
        for lic in lista:
            if not isinstance(lic, dict):
                continue
            itens.append({
                "cliente": lic.get("cliente"),
                "sistema": lic.get("sistema"),
                "vencimento": lic.get("vencimento"),
                "dias": lic.get("dias"),
                "estado": estado,
            })
    return {
        "data": date.today().isoformat(),
        "coletado_em": licencas.get("coletado_em"),
        "janela_dias": licencas.get("janela_dias"),
        "total_vencendo": len(vencendo),
        "total_vencidas_recentes": len(vencidas),
        "vencidas_antigas_total": licencas.get("vencidas_antigas_total"),
        "ignoradas_homolog_teste": licencas.get("ignoradas_homolog_teste"),
        "itens": itens,
    }


def main() -> None:
    helpdesk = ler("helpdesk.json")
    licencas = ler("licencas.json")

    metricas = metricas_do_dia(helpdesk, licencas)
    ids_hoje = ids_por_dev(helpdesk)
    if ids_hoje is not None:
        metricas = {**metricas, **atribuicoes_novas(
            ids_hoje, retrato_anterior(ARQUIVO_ATRIBUICOES, metricas["data"]),
            metricas["data"], regua_status(helpdesk))}
    total = gravar_linha_do_dia(ARQUIVO, metricas)
    print(f"OK -> {ARQUIVO} ({total} dias registrados)")

    retrato = retrato_licencas(licencas)
    if retrato is None:
        print(f"AVISO: sem dados de licenças; {ARQUIVO_LICENCAS.name} não foi atualizado")
    else:
        total_lic = gravar_linha_do_dia(ARQUIVO_LICENCAS, retrato)
        print(f"OK -> {ARQUIVO_LICENCAS} ({total_lic} dias, {len(retrato['itens'])} licenças hoje)")

    if ids_hoje is None:
        print(f"AVISO: coleta sem ids por dev; {ARQUIVO_ATRIBUICOES.name} não foi atualizado")
    else:
        dias = gravar_linha_do_dia(ARQUIVO_ATRIBUICOES, {
            "data": metricas["data"],
            "coletado_em": helpdesk.get("coletado_em"),
            # régua do retrato: se mudar até o próximo, aquela diferença não é atribuição
            "regua_status": regua_status(helpdesk),
            "ids_por_dev": ids_hoje,
        })
        print(f"OK -> {ARQUIVO_ATRIBUICOES} ({dias} dias)")


if __name__ == "__main__":
    main()
