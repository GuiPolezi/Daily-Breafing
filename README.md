# Briefing Diário — Agente de resumo matinal

Um agente que roda todo dia de manhã, coleta informações de várias fontes
(e-mail, licenças, help desk, WhatsApp) e gera um relatório único em Markdown
usando o Claude Code em modo não-interativo (headless).

## Como funciona (arquitetura)

```
┌─────────────┐   ┌──────────────┐   ┌───────────────┐
│  Coletores  │──▶│ dados/*.json │──▶│  Claude Code  │──▶ relatorio.md
│  (Python)   │   │  (fatos)     │   │  (síntese)    │
└─────────────┘   └──────────────┘   └───────────────┘
```

1. **Coletores** (`coletores/*.py`): scripts Python simples e determinísticos.
   Cada um consulta UMA fonte e salva um JSON em `dados/`.
2. **Claude Code headless** (`briefing.sh`): lê os JSONs e escreve um
   relatório em português, destacando o que é urgente (licenças vencendo,
   tickets antigos sem resposta, etc.).
3. **Cron**: agenda tudo para rodar, por exemplo, às 7h30 de segunda a sexta.

Por que essa separação? Coleta de dados é tarefa determinística — script
tradicional é mais barato e confiável. A IA entra onde ela agrega valor de
verdade: **interpretar, priorizar e resumir**.

## Setup

```bash
cd briefing-diario
python3 -m venv .venv && source .venv/bin/activate
pip install requests python-dotenv

cp .env.exemplo .env
# edite o .env com suas credenciais
```

No help desk, `HELPDESK_AGENT_NAME` aceita um ou vários nomes separados por `;`
(você e os integrantes da equipe, escritos como aparecem no Milldesk). Os campos
`meus_abertos`/`meus_novos_hoje` passam a somar todos os nomes, e `por_agente`
traz a quebra individual. A comparação ignora acentos e maiúsculas.

Teste cada coletor individualmente antes de agendar:

```bash
python coletores/check_email.py
python coletores/check_helpdesk.py
python coletores/check_licencas.py   # requer adaptação ao seu sistema
cat dados/*.json
```

Depois teste o pipeline completo:

```bash
./briefing.sh
cat relatorio.md
```

## Agendamento (rodar em segundo plano)

Adicione ao cron (`crontab -e`), para rodar às 7h30 em dias úteis:

```cron
30 7 * * 1-5 cd /caminho/para/briefing-diario && ./briefing.sh >> briefing.log 2>&1
```

Alternativas: Agendador de Tarefas do Windows, GitHub Actions (schedule),
ou systemd timer. O resultado pode ser enviado para você por e-mail,
Telegram ou Slack — veja o final do `briefing.sh`.

## Dashboard HTML e notificação (Windows)

Ao final do `briefing.bat`, além do `relatorio.md`, são executados:

1. `gerar_dashboard.py` — gera `dashboard.html`, um único arquivo autocontido
   (dados, CSS e Chart.js embutidos). Funciona offline e pode ser copiado
   sozinho para qualquer pasta ou servidor. Contém cards do dia, evolução da
   fila e dos atendimentos, ranking semanal por técnico, tabela de licenças
   críticas, o briefing do dia e o status de cada fonte.
   - O Chart.js é lido de `assets/chart.min.js`; se o arquivo faltar, é baixado
     uma única vez. Sem rede e sem o arquivo, o dashboard sai sem gráficos
     (as tabelas continuam).
   - Se um JSON de `dados/` faltar ou estiver corrompido, a seção correspondente
     mostra "fonte indisponível" e o restante é gerado normalmente.
   - `dashboard.html` contém dados internos e está no `.gitignore`.
2. `notificar.py` — dispara um toast nativo do Windows via PowerShell (sem
   dependência nova) com o resumo: atendimentos de ontem, fila e licenças em
   risco. Falha na notificação não interrompe o briefing.
3. `start "" dashboard.html` — abre o dashboard no navegador padrão.

Configuração opcional no `.env`:

```
DASHBOARD_MOSTRAR_RANKING=true   # false: sem nomes de técnicos no HTML (só total da equipe)
DASHBOARD_DIAS_GRAFICO=30        # dias do histórico exibidos nos gráficos
```

Para gerar o dashboard manualmente: `python gerar_dashboard.py`.

## Sobre cada fonte

| Fonte      | Dificuldade | Caminho                                                        |
|------------|-------------|----------------------------------------------------------------|
| E-mail     | Fácil       | IMAP (funciona com Gmail, Outlook, e-mail corporativo)          |
| Help desk  | Fácil       | API REST (Zendesk, Freshdesk, GLPI, Movidesk... todos têm)      |
| Licenças   | Média       | API interna se existir; senão scraping (frágil) ou export CSV   |
| WhatsApp   | Difícil     | Só oficial via WhatsApp Business Cloud API + webhook. Sem API oficial p/ conta pessoal |

## Segurança

- Nunca commite o `.env` (já está no `.gitignore`).
- Use senhas de aplicativo (Gmail/Outlook exigem para IMAP), nunca sua senha principal.
- Os coletores são somente-leitura: o agente não responde e-mails nem fecha tickets. Comece assim; expanda depois que confiar no sistema.
