"""Coletor de licenças do sistema interno.

Sistema ASP.NET MVC sem API — o coletor faz login como um navegador:
  1. GET na página de login  -> extrai o __RequestVerificationToken
  2. POST /Home/Login        -> autentica (campos: Usuario, Senha, token)
  3. GET na página das tabelas -> extrai "Vencimento Próximo" e "Vencidas"

Saída: dados/licencas.json
"""

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
VENCIDA_RECENTE_DIAS = 60
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

    # Vencidas recentes = ainda acionáveis; antigas = só contagem
    vencidas_recentes = [
        l for l in vencidas_prod
        if l["dias"] is not None and l["dias"] >= -VENCIDA_RECENTE_DIAS
    ]

    resultado = {
        "fonte": "licencas (sistema interno)",
        "data": date.today().isoformat(),
        "coletado_em": datetime.now().isoformat(),
        "vencendo_em_breve": sorted(proximas_prod, key=lambda l: l["dias"] or 999),
        "vencidas_recentes": sorted(vencidas_recentes, key=lambda l: l["dias"] or 0),
        "vencidas_antigas_total": len(vencidas_prod) - len(vencidas_recentes),
        "ignoradas_homolog_teste": len(proximas) + len(vencidas)
        - len(proximas_prod) - len(vencidas_prod),
    }

    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OK -> {SAIDA}")
    print(
        f"Vencendo em breve: {len(proximas_prod)} | "
        f"Vencidas recentes: {len(vencidas_recentes)} | "
        f"Antigas: {resultado['vencidas_antigas_total']}"
    )


if __name__ == "__main__":
    main()