"""Arquiva as métricas-chave do dia em historico/metricas.jsonl.

Uma linha JSON por dia (memória compacta do agente). Se rodar duas vezes
no mesmo dia, a linha do dia é substituída pela mais recente.

Também escreve historico/licencas.jsonl: um retrato diário do conjunto de
licenças (cliente + sistema + vencimento), que é o que permite responder
"o que renovou, o que caiu e o que entrou novo" comparando dois dias. O
coletor de licenças já baixa tudo isso — aqui só guardamos a série.

Campos novos nunca podem quebrar a leitura das linhas antigas: quem lê usa
.get(), nunca indexação direta.
"""

import json
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DADOS = RAIZ / "dados"
HISTORICO = RAIZ / "historico"
ARQUIVO = HISTORICO / "metricas.jsonl"
ARQUIVO_LICENCAS = HISTORICO / "licencas.jsonl"


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
        # Licenças
        "lic_vencendo": len(licencas.get("vencendo_em_breve") or []),
        "lic_vencidas_recentes": len(licencas.get("vencidas_recentes") or []),
    }


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
    total = gravar_linha_do_dia(ARQUIVO, metricas)
    print(f"OK -> {ARQUIVO} ({total} dias registrados)")

    retrato = retrato_licencas(licencas)
    if retrato is None:
        print(f"AVISO: sem dados de licenças; {ARQUIVO_LICENCAS.name} não foi atualizado")
    else:
        total_lic = gravar_linha_do_dia(ARQUIVO_LICENCAS, retrato)
        print(f"OK -> {ARQUIVO_LICENCAS} ({total_lic} dias, {len(retrato['itens'])} licenças hoje)")


if __name__ == "__main__":
    main()
