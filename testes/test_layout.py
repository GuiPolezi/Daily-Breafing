"""Testes de layout das quatro páginas geradas.

Por que isto existe: as páginas são um *baralho de painéis de altura fixa*
(`.colmeia` tem `overflow:hidden` e cada `.secao` é `position:absolute; inset:0`).
Dentro de uma `.secao`, que é um flex column, todo filho direto é item flex com
`flex-shrink:1` por padrão. Quando um bloco não declara seu papel, o navegador
distribui o encolhimento sozinho: esmaga o que é flexível (os gráficos viram
tiras, porque o canvas é `position:absolute;inset:0;height:100%`) e deixa o que é
rígido transbordar por cima do vizinho. Foi assim que a seção Desenvolvimento
quebrou ao ganhar cards novos em 17/09/2026.

Os quatro invariantes:

  1. CONTRATO   — todo filho direto de `.secao` declara `bloco-fixo` ou
                  `bloco-elastico`. É a correção de raiz: sem isso o navegador
                  decide sozinho quem encolhe.
  2. ABSORVENTE — toda seção tem pelo menos um `bloco-elastico`, senão não há
                  quem absorva a sobra e o painel fica com um vão morto embaixo.
  3. GRADE ÚNICA— nenhuma grade de card definida fora de `dashboard_base.py`.
                  Ilha de CSS em gerador foi metade da causa desta quebra.
  4. DENSIDADE  — nenhuma seção pede mais que LIMITE_DENSIDADE telas de altura.

Sobre o teste 4: a `.secao` tem `overflow:auto`, então transbordar e rolar não é,
por si, defeito — o defeito era o esmagamento, que o contrato resolve. O teste 4 é
um **orçamento de projeto**, não de correção: um painel que se comporta como slide
e exige três telas de rolagem virou outra coisa. O limite é um julgamento de
design, está declarado abaixo e vale no menor viewport.

O modelo de altura é aproximado, com constantes declaradas no topo. Ele não
substitui abrir a página no navegador; ele pega sobrecarga de ordem de grandeza —
que é exatamente o que passou despercebido.

Uso:
    python testes/test_layout.py            # todas as páginas
    python testes/test_layout.py dashboard.html
"""

from __future__ import annotations

import math
import sys
from html.parser import HTMLParser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

PAGINAS = [
    "dashboard.html",
    "dashboard_diretor.html",
    "dashboard_licencas.html",
    "dashboard_semanal.html",
]

# Geradores que NÃO podem definir grade própria: o design mora na base.
GERADORES = [
    "gerar_dashboard.py",
    "gerar_dashboard_diretor.py",
    "gerar_dashboard_licencas.py",
    "gerar_dashboard_semanal.py",
]

CONTRATO = ("bloco-fixo", "bloco-elastico")

# ---------------------------------------------------------------------------
# Modelo de altura. Em px, conservador (o real tende a ser um pouco maior).
# ---------------------------------------------------------------------------
# O primeiro viewport é a máquina do Guilherme, medida no Chrome em 17/09/2026:
# é onde o layout quebrou, e é o caso que manda.
VIEWPORTS = [
    (1536, 639, "notebook 1536x639 (máquina de uso)"),
    (1920, 1080, "monitor 1920x1080"),
]

# Quantas "telas" de rolagem interna uma seção pode custar antes de deixar de ser
# um painel e virar uma página. Julgamento de design, não lei da física.
LIMITE_DENSIDADE = 2.0
# Uma secao .rolavel declara, de proposito, que e pagina e nao slide: ali o
# esmagamento (o defeito que o limite de 2.0 guarda) nao acontece mais, porque
# os blocos param de encolher. Continua havendo teto -- pagina infinita tambem e
# defeito -- e ele e o que este arquivo ja chamava de "outra coisa": tres telas.
LIMITE_DENSIDADE_ROLAVEL = 3.0

# Um bloco elástico pode comprimir até aqui. Abaixo disso não se lê mais nada.
MIN_ELASTICO = 140

MARGEM = 24  # --margem: clamp(14px,1.4vw,24px)

# Espelha os breakpoints de altura de dashboard_base.py. Mudou o CSS, mude aqui:
# (altura_maxima, --topo-h como (min,vh,max), --pad, --gap)
DEGRAUS_ALTURA = [
    (680, (116, 0.18, 170), 16, 14),
    (820, (140, 0.22, 230), 22, 18),
    (10 ** 6, (180, 0.29, 330), 36, 28),
]


def espacos(altura: int) -> tuple[float, float, float]:
    """(--topo-h, --pad, --gap) resolvidos para esta altura de viewport."""
    for teto, (tmin, tvh, tmax), pad, gap in DEGRAUS_ALTURA:
        if altura <= teto:
            return clamp(tmin, altura * tvh, tmax), pad, gap
    raise AssertionError("DEGRAUS_ALTURA precisa terminar com um teto infinito")

# Altura mínima de cada tipo de bloco fixo (filho direto da seção).
ALTURA_BLOCO = {
    "secao-cabeca": 96,
    "secao-nota": 34,
    "kpi-rotulo": 30,
    "kpi-explica": 40,
    "serie": 56,
    "nota-escura": 30,
    "tabela-clara": 220,
    "slider": 260,
    "vazio": 120,
    "card": 200,
}
ALTURA_CARD = {
    "grade": 190,
    "grade-dev": 190,
    "grade-destaques": 150,
    "grade-fontes": 140,
    "graficos": 260,
    "painel-exec": 150,
    "duas-colunas-secao": 260,
    "faixa-prazo": 110,
    "mov-grade": 200,
}
# Teto de colunas de uma grade (espelha o @media (min-width:1060px) de
# dashboard_base.py). Sem isso o modelo acha que cabem 6 colunas numa tela larga,
# conta menos linhas do que a pagina tem de verdade e subestima a altura.
COLUNA_MAX = {
    "painel-exec": 4,     # @media (min-width:1060px) em dashboard_base.py
    "grade-fontes": 3,    # repeat(3,minmax(0,1fr)) -- colunas fixas, nao auto
}
COLUNA_MIN = {
    "grade": 240,
    "grade-dev": 230,
    "grade-destaques": 260,
    "grade-fontes": 260,
    "graficos": 320,
    "painel-exec": 230,
    "duas-colunas-secao": 300,
    "faixa-prazo": 150,
    "mov-grade": 260,
}
GRADES = set(ALTURA_CARD)


class LeitorSecoes(HTMLParser):
    """Extrai as seções, seus filhos diretos e quantos cards cada grade tem."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pilha: list[str] = []
        self.secoes: dict[str, list[dict]] = {}
        self.rolaveis: set[str] = set()
        self.secao: str | None = None
        self._bloco: dict | None = None
        self._prof_bloco = -1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        classes = (d.get("class") or "").split()
        if tag == "section" and "secao" in classes and d.get("id"):
            self.secao = d["id"]
            self.secoes.setdefault(self.secao, [])
            if "rolavel" in classes:
                # Numa secao .rolavel o conteudo manda na altura e a secao rola;
                # bloco-elastico ali NAO comprime (ver dashboard_base.py).
                self.rolaveis.add(self.secao)
        elif self.secao and self.pilha and self.pilha[-1] == "section":
            self._bloco = {
                "tag": tag,
                "classes": classes,
                "cards": 0,
                # Toda grade carrega a classe base "grade" MAIS um modificador
                # ("painel-exec", "mov-grade"...). E o modificador que diz a
                # densidade real; pegar "grade" primeiro media a pagina errada.
                "grade": (next((c for c in classes if c in GRADES and c != "grade"), None)
                          or next((c for c in classes if c in GRADES), None)),
            }
            self._prof_bloco = len(self.pilha)
            self.secoes[self.secao].append(self._bloco)
        elif self._bloco is not None and "card" in classes:
            self._bloco["cards"] += 1
        self.pilha.append(tag)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.pilha) - 1, -1, -1):
            if self.pilha[i] == tag:
                if self.pilha[i] == "section":
                    self.secao = None
                if i <= self._prof_bloco:
                    self._bloco = None
                    self._prof_bloco = -1
                del self.pilha[i:]
                return


def clamp(minimo: float, preferido: float, maximo: float) -> float:
    return max(minimo, min(preferido, maximo))


def altura_util(largura: int, altura: int) -> float:
    """Altura de dentro de uma `.secao`, descontando topo, margem e padding."""
    topo, pad, _ = espacos(altura)
    return altura - topo - MARGEM - 2 * pad


def colunas(grade: str, largura: int, altura: int) -> int:
    _, pad, gap = espacos(altura)
    util = largura - 2 * MARGEM - 2 * pad
    minimo = COLUNA_MIN.get(grade, 240)
    cabem = max(1, int((util + gap) // (minimo + gap)))
    return min(cabem, COLUNA_MAX.get(grade, 99))


def altura_bloco(bloco: dict, largura: int, altura: int,
                 rolavel: bool = False) -> float:
    """Altura mínima estimada. Bloco elástico vale o mínimo que ele comprime --
    a menos que a seção role: ali ele vale a altura natural do conteúdo."""
    if "bloco-elastico" in bloco["classes"] and not (rolavel and bloco["grade"]):
        return MIN_ELASTICO
    if bloco["grade"]:
        g = bloco["grade"]
        n = max(bloco["cards"], 1)
        _, _, gap = espacos(altura)
        linhas = math.ceil(n / colunas(g, largura, altura))
        return linhas * ALTURA_CARD[g] + (linhas - 1) * gap
    for chave, h in ALTURA_BLOCO.items():
        if chave in bloco["classes"]:
            return h
    return 40


falhas: list[str] = []


def falhar(msg: str) -> None:
    falhas.append(msg)
    print(f"  FALHA  {msg}")


def teste_contrato(pagina: str, secoes: dict[str, list[dict]]) -> None:
    for secao, blocos in secoes.items():
        sem = [f'{b["tag"]}.{".".join(b["classes"]) or "(sem classe)"}'
               for b in blocos if not any(c in CONTRATO for c in b["classes"])]
        if sem:
            falhar(f"{pagina} #{secao}: {len(sem)} bloco(s) sem contrato de flex: {sem}")


def teste_absorvente(pagina: str, secoes: dict[str, list[dict]],
                     rolaveis: frozenset = frozenset()) -> None:
    for secao, blocos in secoes.items():
        if not blocos or secao in rolaveis:
            continue  # secao que rola nao tem sobra para alguem absorver
        if not any("bloco-elastico" in b["classes"] for b in blocos):
            falhar(f"{pagina} #{secao}: nenhum bloco-elastico — não há quem absorva a sobra")


def teste_densidade(pagina: str, secoes: dict[str, list[dict]],
                    rolaveis: frozenset = frozenset()) -> None:
    largura, altura, rotulo = VIEWPORTS[0]
    disponivel = altura_util(largura, altura)
    _, _, gap = espacos(altura)
    for secao, blocos in secoes.items():
        if not blocos:
            continue
        rola = secao in rolaveis
        total = (sum(altura_bloco(b, largura, altura, rola) for b in blocos)
                 + gap * (len(blocos) - 1))
        densidade = total / disponivel
        limite = LIMITE_DENSIDADE_ROLAVEL if rola else LIMITE_DENSIDADE
        if densidade > limite:
            detalhe = ", ".join(
                f'{b["grade"] or (b["classes"][0] if b["classes"] else b["tag"])}'
                f'={int(altura_bloco(b, largura, altura, rola))}'
                for b in blocos)
            falhar(f"{pagina} #{secao} em {rotulo}: {densidade:.1f} telas "
                   f"(limite {limite}{' rolável' if rola else ''}) — precisa de {int(total)}px, "
                   f"cabe {int(disponivel)}px. Blocos: {detalhe}")


def teste_grade_unica() -> None:
    for gerador in GERADORES:
        p = RAIZ / gerador
        if not p.exists():
            continue
        achados = [linha.strip() for linha in p.read_text(encoding="utf-8").splitlines()
                   if "display:grid" in linha.replace(" ", "")]
        if achados:
            falhar(f"{gerador}: define grade própria (o design mora em "
                   f"dashboard_base.py): {achados[:3]}")


def main() -> int:
    alvos = sys.argv[1:] or PAGINAS
    print("=" * 78)
    print("TESTES DE LAYOUT — contrato de flex, grade única e densidade")
    print("=" * 78)

    for nome in alvos:
        caminho = RAIZ / nome
        if not caminho.exists():
            print(f"\n{nome}: ausente (rode o gerador correspondente) — pulando")
            continue
        leitor = LeitorSecoes()
        leitor.feed(caminho.read_text(encoding="utf-8"))
        print(f"\n--- {nome}: {len(leitor.secoes)} seções ---")
        teste_contrato(nome, leitor.secoes)
        teste_absorvente(nome, leitor.secoes, frozenset(leitor.rolaveis))
        teste_densidade(nome, leitor.secoes, frozenset(leitor.rolaveis))

    print("\n--- grade única ---")
    teste_grade_unica()

    print("\n" + "=" * 78)
    if falhas:
        print(f"{len(falhas)} FALHA(S)")
        return 1
    print("TODOS OS TESTES PASSARAM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
