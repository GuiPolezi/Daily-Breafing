"""Coletor de e-mail via IMAP.

Conta: não lidos na caixa de entrada, recebidos hoje e itens na pasta de spam.
Funciona com Gmail (imap.gmail.com), Outlook (outlook.office365.com) e
servidores corporativos. Para Gmail/Outlook, gere uma "senha de aplicativo".

Saída: dados/email.json
"""

import imaplib
import ssl
import json
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

HOST = os.environ["EMAIL_IMAP_HOST"]          # ex.: imap.gmail.com
USER = os.environ["EMAIL_USER"]
PASSWORD = os.environ["EMAIL_APP_PASSWORD"]   # senha de aplicativo!
SPAM_FOLDER = os.getenv("EMAIL_SPAM_FOLDER", "[Gmail]/Spam")  # Outlook: "Junk"

SAIDA = Path(__file__).resolve().parent.parent / "dados" / "email.json"


def contar(mail: imaplib.IMAP4_SSL, pasta: str, criterio: str) -> int:
    status, _ = mail.select(f'"{pasta}"', readonly=True)
    if status != "OK":
        return -1  # pasta não encontrada
    status, resposta = mail.search(None, criterio)
    if status != "OK" or not resposta[0]:
        return 0
    return len(resposta[0].split())


def assuntos_nao_lidos(mail: imaplib.IMAP4_SSL, limite: int = 10) -> list[str]:
    """Pega os assuntos dos últimos e-mails não lidos (para o resumo)."""
    import email
    from email.header import decode_header

    mail.select("INBOX", readonly=True)
    status, resposta = mail.search(None, "UNSEEN")
    if status != "OK" or not resposta[0]:
        return []
    ids = resposta[0].split()[-limite:]
    assuntos = []
    for msg_id in reversed(ids):
        _, dados = mail.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
        msg = email.message_from_bytes(dados[0][1])
        bruto = msg.get("Subject", "(sem assunto)")
        partes = decode_header(bruto)
        assunto = "".join(
            p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p
            for p, enc in partes
        )
        assuntos.append({"de": msg.get("From", "?"), "assunto": assunto})
    return assuntos


def conectar() -> imaplib.IMAP4:
    """Tenta SSL direto (993); se falhar, usa STARTTLS (143)."""
    porta = int(os.getenv("EMAIL_IMAP_PORT", "0"))  # 0 = detectar sozinho

    if porta == 993 or porta == 0:
        try:
            return imaplib.IMAP4_SSL(HOST, 993)
        except ssl.SSLError:
            if porta == 993:
                raise  # usuário forçou 993, não mascarar o erro
            print("Porta 993 (SSL) falhou, tentando STARTTLS na 143...")

    mail = imaplib.IMAP4(HOST, porta or 143)
    mail.starttls(ssl.create_default_context())
    return mail

def main() -> None:
    hoje = date.today().strftime("%d-%b-%Y")  # formato IMAP: 09-Sep-2026
    mail = conectar()
    mail.login(USER, PASSWORD)

    resultado = {
        "fonte": "email",
        "data": date.today().isoformat(),
        "nao_lidos": contar(mail, "INBOX", "UNSEEN"),
        "recebidos_hoje": contar(mail, "INBOX", f"SINCE {hoje}"),
        "spam_total": contar(mail, SPAM_FOLDER, "ALL"),
        "spam_hoje": contar(mail, SPAM_FOLDER, f"SINCE {hoje}"),
        "ultimos_nao_lidos": assuntos_nao_lidos(mail),
    }
    mail.logout()

    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK -> {SAIDA}")


if __name__ == "__main__":
    main()
