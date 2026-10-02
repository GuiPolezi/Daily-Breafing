"""Coletor de licenças do sistema interno.

Sistema ASP.NET MVC sem API — o coletor faz login como um navegador:
  1. GET na página de login  -> extrai o __RequestVerificationToken
  2. POST /Home/Login        -> autentica (campos: Usuario, Senha, token)
  3. GET na página das tabelas -> extrai "Vencimento Próximo" e "Vencidas"

Saída: dados/licencas.json
"""

from __future__ import annotations  # o servidor roda Python 3.8 (sem `X | None`)

import json
import os
import re
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ["LICENCAS_BASE_URL"]
USUARIO = os.environ["LICENCAS_USUARIO"]
SENHA = os.environ["LICENCAS_SENHA"]
PAGINA_TABELAS = os.getenv("LICENCAS_PAGINA", "/")  # onde ficam as tabelas
# Período (em dias) das duas listas de ação: vencidas nos últimos N dias e vencendo
# nos próximos N. Era 60 fixo só para as vencidas, e as "vencendo" dependiam do que a
# tabela do painel mostrasse; desde 28/09/2026 é 30 para os dois lados (decisão do
# Guilherme). Mudou o valor? O dia da troca vira degrau no histórico -- os dashboards
# detectam isso por `janela_dias` e suprimem a comparação.
try:
    JANELA_DIAS = max(1, int(os.getenv("LICENCAS_JANELA_DIAS", "30")))
except ValueError:
    JANELA_DIAS = 30
IGNORAR = [
    p.strip().lower()
    for p in os.getenv("LICENCAS_IGNORAR", "homologação;homologacao;teste;backup").split(";")
    if p.strip()
]

SAIDA = Path(__file__).resolve().parent.parent / "dados" / "licencas.json"


def fazer_login(sessao: requests.Session) -> None:
    # 1) Pega o token anti-CSRF do formulário
    pagina = sessao.get(f"{BASE_URL}/Home/Login", timeout=30)
    pagina.raise_for_status()
    soup = BeautifulSoup(pagina.text, "html.parser")
    campo_token = soup.find("input", {"name": "__RequestVerificationToken"})
    if not campo_token:
        raise RuntimeError("Não achei o __RequestVerificationToken na página de login.")

    # 2) Envia as credenciais
    resposta = sessao.post(
        f"{BASE_URL}/Home/Login",
        data={
            "__RequestVerificationToken": campo_token["value"],
            "Usuario": USUARIO,
            "Senha": SENHA,
        },
        timeout=30,
    )
    resposta.raise_for_status()

    # 3) Se a resposta ainda contém o campo de login, as credenciais falharam
    if 'name="Senha"' in resposta.text and 'name="Usuario"' in resposta.text:
        raise RuntimeError("Login falhou: verifique LICENCAS_USUARIO e LICENCAS_SENHA.")


def extrair_tabela(soup: BeautifulSoup, titulo: str) -> list[dict]:
    """Encontra a tabela pelo texto do <caption> e extrai as linhas."""
    for tabela in soup.find_all("table"):
        caption = tabela.find("caption")
        if not caption or titulo.lower() not in caption.get_text().lower():
            continue
        linhas = []
        for tr in tabela.select("tbody tr"):
            celulas = tr.find_all("td")
            if len(celulas) < 3:
                continue
            linhas.append(
                {
                    "cliente": celulas[0].get_text(strip=True),
                    "vencimento": celulas[1].get_text(strip=True),
                    "sistema": celulas[2].get_text(strip=True),
                }
            )
        return linhas
    raise RuntimeError(f"Tabela '{titulo}' não encontrada — confira LICENCAS_PAGINA.")


def dias_restantes(lic: dict) -> int | None:
    try:
        venc = datetime.strptime(lic["vencimento"], "%d/%m/%Y").date()
        return (venc - date.today()).days
    except ValueError:
        return None


def eh_ignorada(lic: dict) -> bool:
    nome = lic["cliente"].lower()
    return any(padrao in nome for padrao in IGNORAR)


def chave_licenca(lic: dict) -> tuple:
    return (lic.get("cliente"), lic.get("sistema"), lic.get("vencimento"))


def separar_vencidas(proximas: list[dict], vencidas: list[dict]) -> tuple[list[dict], list[dict]]:
    """Licença que vence HOJE ou antes é vencida -- e só vencida.

    No dia do vencimento o próprio sistema de licenças lista a licença nas DUAS
    tabelas ("Vencimento Próximo" e "Vencidas"); copiada assim, ela aparecia como
    vencida e como vencendo ao mesmo tempo (visto em 30/09/2026) e as contagens a
    somavam duas vezes. Regra (decisão do Guilherme, 30/09/2026):
      - o que está na tabela "Vencidas" sai de "Vencimento Próximo";
      - o que ficou em "Vencimento Próximo" com dias <= 0 vai para vencidas.
    Sem data legível continua em "próximas" (melhor aparecer sem prazo do que sumir).
    """
    ja_vencidas = {chave_licenca(l) for l in vencidas}
    vencidas_final = list(vencidas)
    proximas_final = []
    for lic in proximas:
        if chave_licenca(lic) in ja_vencidas:
            continue
        if lic.get("dias") is not None and lic["dias"] <= 0:
            vencidas_final.append(lic)
            ja_vencidas.add(chave_licenca(lic))
        else:
            proximas_final.append(lic)
    return proximas_final, vencidas_final


def main() -> None:
    sessao = requests.Session()
    fazer_login(sessao)

    pagina = sessao.get(f"{BASE_URL}{PAGINA_TABELAS}", timeout=30)
    pagina.raise_for_status()
    soup = BeautifulSoup(pagina.text, "html.parser")

    proximas = extrair_tabela(soup, "Vencimento Próximo")
    vencidas = extrair_tabela(soup, "Vencidas")

    for lic in proximas + vencidas:
        lic["dias"] = dias_restantes(lic)

    # Separa produção de homologação/teste
    proximas_prod = [l for l in proximas if not eh_ignorada(l)]
    vencidas_prod = [l for l in vencidas if not eh_ignorada(l)]
    # contado ANTES de separar_vencidas: a remoção das duplicatas não é homologação/teste
    ignoradas = len(proximas) + len(vencidas) - len(proximas_prod) - len(vencidas_prod)
    # Vence hoje (ou já venceu) = só vencida, nunca nas duas listas
    proximas_prod, vencidas_prod = separar_vencidas(proximas_prod, vencidas_prod)

    # Vencidas recentes = ainda acionáveis; antigas = só contagem
    vencidas_recentes = [
        l for l in vencidas_prod
        if l["dias"] is not None and l["dias"] >= -JANELA_DIAS
    ]
    # Vencendo = só os próximos JANELA_DIAS. Sem data legível fica na lista: melhor
    # aparecer sem prazo do que sumir em silêncio. O que passa da janela vira contagem.
    vencendo = [l for l in proximas_prod if l["dias"] is None or l["dias"] <= JANELA_DIAS]

    def por_prazo(padrao: int):
        # `dias or padrao` trataria 0 (vence hoje) como sem data; comparar com None não
        return lambda l: l["dias"] if l["dias"] is not None else padrao

    resultado = {
        "fonte": "licencas (sistema interno)",
        "data": date.today().isoformat(),
        "coletado_em": datetime.now().isoformat(),
        "janela_dias": JANELA_DIAS,
        "vencendo_em_breve": sorted(vencendo, key=por_prazo(999)),
        "vencidas_recentes": sorted(vencidas_recentes, key=por_prazo(0)),
        "vencidas_antigas_total": len(vencidas_prod) - len(vencidas_recentes),
        "vencendo_alem_da_janela": len(proximas_prod) - len(vencendo),
        "ignoradas_homolog_teste": ignoradas,
    }

    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OK -> {SAIDA}")
    print(
        f"Janela: {JANELA_DIAS} dias | "
        f"Vencendo em breve: {len(vencendo)} | "
        f"Vencidas recentes: {len(vencidas_recentes)} | "
        f"Antigas: {resultado['vencidas_antigas_total']}"
    )


if __name__ == "__main__":
    main()