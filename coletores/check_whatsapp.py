"""Coletor de WhatsApp — leia antes de implementar.

SITUAÇÃO REAL (importante):

  * NÃO existe API oficial para ler mensagens de uma conta PESSOAL.
  * O caminho oficial é a WhatsApp Business Cloud API (Meta), que exige:
      - conta WhatsApp Business vinculada a um app da Meta;
      - um webhook: a Meta ENVIA as mensagens para um servidor seu
        (não dá para "consultar" — é push, não pull).
    Arquitetura: webhook recebe -> salva em dados/whatsapp_inbox.json ->
    este coletor só resume o arquivo.
  * Bibliotecas não-oficiais (whatsapp-web.js, Baileys) funcionam com conta
    normal, mas violam os termos da Meta e podem causar BANIMENTO do número.
    Não recomendado com número da empresa.

ALTERNATIVA PRAGMÁTICA: se o objetivo é só saber "tem conversa pendente?",
muitas equipes resolvem com o WhatsApp Business em um número dedicado +
Cloud API, ou migrando o atendimento para o help desk (muitos help desks
têm integração oficial com WhatsApp — verifique o seu!).

Este stub apenas resume um arquivo alimentado por webhook, se existir.

Saída: dados/whatsapp.json
"""

import json
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "dados"
INBOX = BASE / "whatsapp_inbox.json"   # alimentado pelo seu webhook
SAIDA = BASE / "whatsapp.json"


def main() -> None:
    if not INBOX.exists():
        resultado = {
            "fonte": "whatsapp",
            "data": date.today().isoformat(),
            "disponivel": False,
            "motivo": "Webhook da Cloud API ainda não configurado (ver docstring).",
        }
    else:
        mensagens = json.loads(INBOX.read_text())
        hoje = date.today().isoformat()
        de_hoje = [m for m in mensagens if str(m.get("timestamp", "")).startswith(hoje)]
        resultado = {
            "fonte": "whatsapp",
            "data": hoje,
            "disponivel": True,
            "conversas_nao_respondidas": len({m["de"] for m in de_hoje}),
            "mensagens_hoje": len(de_hoje),
        }

    BASE.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
    print(f"OK -> {SAIDA}")


if __name__ == "__main__":
    main()
