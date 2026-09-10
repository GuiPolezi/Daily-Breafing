"""Coletor de vencimentos de licenças no sistema interno da empresa.

Como o sistema é proprietário, há 3 caminhos possíveis (do melhor ao pior):

  1. API interna — pergunte ao time que mantém o sistema se existe.
     É o caminho mais estável. O exemplo abaixo assume esse cenário.

  2. Export agendado — se o sistema exporta CSV/Excel, baixe o arquivo
     e leia com pandas. Também é estável.

  3. Web scraping — o script faz login e extrai da página HTML
     (requests + BeautifulSoup, ou Playwright se a página usa JavaScript).
     Funciona, mas quebra quando o layout muda. Último recurso.

DICA: cole nesse arquivo um exemplo da resposta da API (ou o HTML da página)
e peça ao Claude Code para completar a extração — é o tipo de tarefa
que ele resolve bem.

Saída: dados/licencas.json
"""

import json
import os
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ["LICENCAS_BASE_URL"]
TOKEN = os.environ["LICENCAS_TOKEN"]
DIAS_ALERTA = int(os.getenv("LICENCAS_DIAS_ALERTA", "30"))

SAIDA = Path(__file__).resolve().parent.parent / "dados" / "licencas.json"


def buscar_licencas() -> list[dict]:
    """ADAPTE AQUI. Exemplo assumindo uma API interna que retorna JSON:
    [{"nome": "...", "cliente": "...", "vencimento": "2026-10-01"}, ...]
    """
    resp = requests.get(
        f"{BASE_URL}/api/licencas",
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    hoje = date.today()
    licencas = buscar_licencas()

    def dias_restantes(lic: dict) -> int:
        venc = datetime.fromisoformat(lic["vencimento"]).date()
        return (venc - hoje).days

    vencidas = [l for l in licencas if dias_restantes(l) < 0]
    vencendo = [l for l in licencas if 0 <= dias_restantes(l) <= DIAS_ALERTA]

    for lic in vencidas + vencendo:
        lic["dias_restantes"] = dias_restantes(lic)

    resultado = {
        "fonte": "licencas",
        "data": hoje.isoformat(),
        "total": len(licencas),
        "vencidas": sorted(vencidas, key=lambda l: l["dias_restantes"]),
        "vencendo_em_ate_30_dias": sorted(vencendo, key=lambda l: l["dias_restantes"]),
    }

    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
    print(f"OK -> {SAIDA}")


if __name__ == "__main__":
    main()
