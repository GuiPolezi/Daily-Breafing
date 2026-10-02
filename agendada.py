"""Uma rodada do sistema novo, feita para a tarefa agendada (a cada 5 minutos).

Uma tarefa só, de propósito: é este script que decide o que fazer em cada
rodada, e assim duas coletas nunca consultam o Milldesk ao mesmo tempo.

  1. Decide a coleta do Milldesk (`decidir`):
       - fora do expediente (COLETA_EXPEDIENTE, segunda a sexta): não faz nada;
       - dentro do silêncio (COLETA_SILENCIO): não consulta o Milldesk -- é a
         hora em que o briefing antigo coleta, com a mesma chave;
       - a última coleta bateu no limite (HTTP 429) há menos de
         COLETA_RECUO_MINUTOS: não consulta, para o saldo se recompor;
       - a última coleta completa tem COLETA_COMPLETA_MINUTOS ou mais: completa;
       - senão: rápida (1 requisição).
  2. Licenças e agenda: cada coletor roda no máximo uma vez a cada
     COLETA_FONTES_MINUTOS (conta a TENTATIVA, para fonte fora do ar não ser
     martelada a cada 5 minutos).
  3. exportar.py e publicar_site.py, sempre que dentro do expediente.

Cada passo roda como processo separado, com tempo máximo: passo que falha ou
trava vira AVISO e os seguintes rodam. Este script não consulta API nenhuma.

Três travas contra gastar o limite do Milldesk à toa:
  - a TENTATIVA de coleta é anotada em dados/agendada.json ANTES de rodar: uma
    completa que trave ou morra sem gravar nada no banco não é repetida a cada
    5 minutos;
  - banco que existe mas não pôde ser lido (travado, corrompido) = nenhuma
    consulta ao Milldesk nesta rodada, nunca "ainda não houve coleta";
  - dados/agendada.lock: uma rodada por vez, mesmo com alguém rodando à mão.

Uso:
    python agendada.py             # uma rodada
    python agendada.py --simular   # só diz o que faria, sem rodar nada
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:   # sem python-dotenv valem os padrões e as variáveis do ambiente
    def load_dotenv(*a, **k):
        return False

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")

BANCO = RAIZ / "dados" / "briefing.db"
ESTADO = RAIZ / "dados" / "agendada.json"
TRAVA = RAIZ / "dados" / "agendada.lock"
# os cinco passos no pior caso somam 25 minutos: trava mais velha que isto é de rodada que morreu
TRAVA_VELHA_MINUTOS = 40
# em quantas coletas recentes procurar o aviso de limite (HTTP 429)
COLETAS_RECENTES = 30
TEMPO_MAXIMO_SEGUNDOS = 300
# (nome, script): fontes que não são o Milldesk
FONTES = (("licencas", "coletores/check_licencas.py"), ("agenda", "coletores/check_agenda.py"))


def _minutos(nome: str, padrao: int) -> int:
    try:
        return max(1, int(os.getenv(nome, str(padrao)).split("#")[0].strip()))
    except ValueError:
        return padrao


def _janela(nome: str, padrao: str) -> tuple[time, time] | None:
    """"HH:MM-HH:MM" -> (início, fim). Vazio desliga a janela; valor ilegível vale o padrão."""
    bruto = os.getenv(nome, padrao).split("#")[0].strip().strip("'").strip('"')
    if not bruto:
        return None
    for texto in (bruto, padrao):
        try:
            inicio, fim = (datetime.strptime(p.strip(), "%H:%M").time() for p in texto.split("-"))
            return inicio, fim
        except ValueError:
            continue
    return None


EXPEDIENTE = _janela("COLETA_EXPEDIENTE", "07:00-19:00")
SILENCIO = _janela("COLETA_SILENCIO", "08:00-08:30")
COMPLETA_MINUTOS = _minutos("COLETA_COMPLETA_MINUTOS", 60)
RECUO_MINUTOS = _minutos("COLETA_RECUO_MINUTOS", 30)
FONTES_MINUTOS = _minutos("COLETA_FONTES_MINUTOS", 60)
# a tarefa não dispara no segundo exato: sem folga, "60 minutos" viraria 65
FOLGA_MINUTOS = 2


def _dentro(janela: tuple[time, time] | None, agora: datetime) -> bool:
    return janela is not None and janela[0] <= agora.time() < janela[1]


def _momento(iso) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def estado_do_banco(arquivo: Path = BANCO) -> dict:
    """O que o banco sabe das coletas, lido sem escrever (e sem criar o arquivo).

    Banco que não existe é "nunca houve coleta". Banco que existe e não pôde ser
    lido é `erro`: quem decide não pode confundir os dois.
    """
    vazio = {"ultima_completa": None, "ultimo_limite": None, "erro": None}
    if not Path(arquivo).exists():
        return vazio
    try:
        con = sqlite3.connect(Path(arquivo).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            colunas = {l[1] for l in con.execute("PRAGMA table_info(coletas)")}
            if not colunas:
                return vazio                    # banco criado, ainda sem a tabela
            # a TENTATIVA de completa é o que conta: se ela veio incompleta, repetir
            # a cada 5 minutos gastaria ~19 requisições por vez. Banco antigo não
            # tem a coluna `modo`: vale só fila_completa.
            filtro = "modo = 'completa' OR fila_completa = 1" if "modo" in colunas else "fila_completa = 1"
            completa = con.execute(f"SELECT momento FROM coletas WHERE {filtro} ORDER BY id DESC LIMIT 1").fetchone()
            recentes = con.execute("SELECT momento, avisos FROM coletas ORDER BY id DESC LIMIT ?",
                                   (COLETAS_RECENTES,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        return {**vazio, "erro": type(e).__name__}
    limite = next((m for m, avisos in recentes if "limite de requisições" in str(avisos or "")), None)
    return {"ultima_completa": _momento(completa[0]) if completa else None,
            "ultimo_limite": _momento(limite), "erro": None}


def com_tentativas(estado: dict, tentativas: dict) -> dict:
    """Junta o que o banco registrou com as tentativas anotadas antes de rodar:
    vale a mais recente das duas."""
    anotada = _momento(tentativas.get("milldesk_completa") if isinstance(tentativas, dict) else None)
    conhecidas = [m for m in (estado.get("ultima_completa"), anotada) if m is not None]
    return {**estado, "ultima_completa": max(conhecidas) if conhecidas else None}


def decidir(agora: datetime, estado: dict) -> tuple[str, str]:
    """('nada' | 'rapida' | 'completa', motivo)."""
    if agora.weekday() >= 5 or not _dentro(EXPEDIENTE, agora):
        return "nada", "fora do expediente"
    if _dentro(SILENCIO, agora):
        return "nada", "horário reservado ao briefing antigo"
    if estado.get("erro"):
        return "nada", f"o banco não pôde ser lido ({estado['erro']})"

    def idade(momento: datetime | None) -> float | None:
        # relógio atrasado ou coleta "no futuro" conta como recém-feita
        return None if momento is None else max(0.0, (agora - momento).total_seconds() / 60)

    desde_limite = idade(estado.get("ultimo_limite"))
    if desde_limite is not None and desde_limite < RECUO_MINUTOS:
        return "nada", f"uma coleta bateu no limite do Milldesk há {int(desde_limite)} min; aguardando {RECUO_MINUTOS}"
    desde_completa = idade(estado.get("ultima_completa"))
    if desde_completa is None:
        return "completa", "ainda não houve coleta completa"
    if desde_completa >= COMPLETA_MINUTOS - FOLGA_MINUTOS:
        return "completa", f"última completa há {int(desde_completa)} min"
    return "rapida", f"última completa há {int(desde_completa)} min"


def pegar_trava(agora: datetime, arquivo: Path = TRAVA) -> bool:
    """Uma rodada por vez. Trava de rodada que morreu (mais velha que
    TRAVA_VELHA_MINUTOS) é descartada."""
    arquivo = Path(arquivo)
    for _ in range(2):
        try:
            arquivo.parent.mkdir(parents=True, exist_ok=True)
            descritor = os.open(str(arquivo), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                idade = (agora.timestamp() - arquivo.stat().st_mtime) / 60
                if idade < TRAVA_VELHA_MINUTOS:      # trava "do futuro" (relógio acertado) conta como recente
                    return False
                arquivo.unlink()
            except OSError:
                return False
            continue
        except OSError:
            return False
        with os.fdopen(descritor, "w") as f:
            f.write(agora.isoformat(timespec="seconds"))
        return True
    return False


def soltar_trava(arquivo: Path = TRAVA) -> None:
    try:
        Path(arquivo).unlink()
    except OSError:
        pass


def fontes_a_coletar(agora: datetime, tentativas: dict) -> list[str]:
    """Nomes das fontes cuja última TENTATIVA tem FONTES_MINUTOS ou mais."""
    if agora.weekday() >= 5 or not _dentro(EXPEDIENTE, agora):
        return []
    devidas = []
    for nome, _ in FONTES:
        ultima = _momento(tentativas.get(nome) if isinstance(tentativas, dict) else None)
        if ultima is None or ultima > agora or (agora - ultima).total_seconds() / 60 >= FONTES_MINUTOS - FOLGA_MINUTOS:
            devidas.append(nome)
    return devidas


def ler_tentativas(arquivo: Path = ESTADO) -> dict:
    try:
        dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def gravar_tentativas(tentativas: dict, arquivo: Path = ESTADO) -> bool:
    try:
        Path(arquivo).parent.mkdir(parents=True, exist_ok=True)
        Path(arquivo).write_text(json.dumps(tentativas), encoding="utf-8")
        return True
    except OSError as e:
        print(f"AVISO: não gravei {Path(arquivo).name} ({type(e).__name__})")
        return False


def rodar(script: str, *args: str) -> bool:
    """Roda um passo. Devolve se terminou bem; nunca levanta."""
    comando = [sys.executable, str(RAIZ / script), *args]
    try:
        feito = subprocess.run(comando, cwd=str(RAIZ), timeout=TEMPO_MAXIMO_SEGUNDOS,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        print(f"AVISO: {script} passou de {TEMPO_MAXIMO_SEGUNDOS} s e foi interrompido")
        return False
    except OSError as e:
        print(f"AVISO: {script} não rodou ({type(e).__name__})")
        return False
    saida = feito.stdout.decode("utf-8", errors="replace").strip()
    if saida:
        print("\n".join("    " + linha for linha in saida.splitlines()))
    if feito.returncode != 0:
        print(f"AVISO: {script} terminou com código {feito.returncode}")
    return feito.returncode == 0


def rodada(agora: datetime) -> None:
    tentativas = ler_tentativas()
    acao, motivo = decidir(agora, com_tentativas(estado_do_banco(), tentativas))
    fontes = fontes_a_coletar(agora, tentativas)
    print(f"    Milldesk: {acao} ({motivo}) | fontes: {', '.join(fontes) or 'nenhuma'}")
    if acao == "nada" and motivo == "fora do expediente":
        return
    if acao != "nada":
        carimbo = agora.isoformat(timespec="seconds")
        tentativas["milldesk"] = carimbo
        if acao == "completa":
            tentativas["milldesk_completa"] = carimbo
        # anotada ANTES de rodar: coleta que trava ou morre sem gravar no banco também conta
        if not gravar_tentativas(tentativas) and acao == "completa":
            print("AVISO: sem conseguir anotar a tentativa, a completa vira rápida (não repetir ~19 requisições)")
            acao = "rapida"
        rodar("coletar.py", *(["--rapida"] if acao == "rapida" else []))
    for nome, script in FONTES:
        if nome in fontes:
            tentativas[nome] = agora.isoformat(timespec="seconds")
            gravar_tentativas(tentativas)       # antes de rodar: coletor que trava também conta
            rodar(script)
    rodar("exportar.py")
    rodar("publicar_site.py")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    agora = datetime.now()
    if "--simular" in sys.argv:
        tentativas = ler_tentativas()
        acao, motivo = decidir(agora, com_tentativas(estado_do_banco(), tentativas))
        print(f"=== {agora:%d/%m/%Y %H:%M:%S} | SIMULAÇÃO | Milldesk: {acao} ({motivo}) | fontes: "
              f"{', '.join(fontes_a_coletar(agora, tentativas)) or 'nenhuma'}")
        return 0
    print(f"=== {agora:%d/%m/%Y %H:%M:%S}")
    if not pegar_trava(agora):
        print("    outra rodada ainda está em execução; esta não faz nada")
        return 0
    try:
        rodada(agora)
    finally:
        soltar_trava()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:   # último recurso: a tarefa agendada nunca fica em erro por isto
        print(f"AVISO: rodada falhou: {type(e).__name__}")
        sys.exit(1)
