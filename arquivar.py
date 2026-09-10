"""Arquiva as métricas-chave do dia em historico/metricas.jsonl.

Uma linha JSON por dia (memória compacta do agente). Se rodar duas vezes
no mesmo dia, a linha do dia é substituída pela mais recente.
"""

import json
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DADOS = RAIZ / "dados"
HISTORICO = RAIZ / "historico"
ARQUIVO = HISTORICO / "metricas.jsonl"


def ler(nome: str) -> dict:
    caminho = DADOS / nome
    if not caminho.exists():
        return {}
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def main() -> None:
    email = ler("email.json")
    helpdesk = ler("helpdesk.json")
    licencas = ler("licencas.json")
    atend = helpdesk.get("atendimentos_ultimo_dia_util", {})

    metricas = {
        "data": date.today().isoformat(),
        # E-mail
        "email_nao_lidos": email.get("nao_lidos"),
        "email_recebidos": email.get("recebidos_hoje"),
        "email_spam": email.get("spam_hoje"),
        # Help desk (fila)
        "fila_abertos": helpdesk.get("fila_total_abertos"),
        "meus_abertos": helpdesk.get("meus_abertos"),
        # Produtividade (referente ao último dia útil)
        "atend_dia_ref": atend.get("dia"),
        "atend_total": atend.get("total_atendimentos_fechados"),
        "atend_por_tecnico": atend.get("por_tecnico"),
        # Licenças
        "lic_vencendo": len(licencas.get("vencendo_em_breve", [])),
        "lic_vencidas_recentes": len(licencas.get("vencidas_recentes", [])),
    }

    HISTORICO.mkdir(exist_ok=True)
    linhas = []
    if ARQUIVO.exists():
        linhas = [
            l for l in ARQUIVO.read_text(encoding="utf-8").splitlines()
            if l.strip() and json.loads(l).get("data") != metricas["data"]
        ]
    linhas.append(json.dumps(metricas, ensure_ascii=False))
    ARQUIVO.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"OK -> {ARQUIVO} ({len(linhas)} dias registrados)")


if __name__ == "__main__":
    main()