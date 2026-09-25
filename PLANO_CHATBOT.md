# Plano: chatbot de IA no briefing

> Arquivo de contexto para retomar o trabalho. Criado em 2026-09-23 (qua).
> **Status: PLANO EM REVISÃO. Nada implementado.** Falta aprovação da Fase 1
> e decidir a questão de privacidade do Gemini (seção 4).

## 1. O que o Guilherme pediu

Um botão "Perguntar à IA" no painel que responda perguntas livres sobre
**todas** as informações das fontes, por exemplo:
*"Quantas solicitações foram criadas de Holambra?"*

- **Onde:** `dashboard.html` (operação) e `dashboard_diretor.html`.
  **Nunca** no briefing de licenças, que não pode mostrar nomes de pessoas.
- **Quem usa:** o diretor, **no PC dele, na mesma rede** da empresa.
- **Dados:** coletar **todos** os chamados, inclusive os fechados, com **todos os
  campos**, num arquivo separado `dados/chamados.json`.
- **Servidor:** roda **no PC do Guilherme**, em segundo plano, sempre ligado.
- **IA:** o Guilherme quer usar uma **API gratuita (Gemini)**. Antes a escolha
  tinha sido a API da Anthropic; veja a seção 4.
- **Enviar dados para a IA:** o Guilherme disse que é permitido. Mas o plano
  gratuito do Gemini **treina com os dados**, e isso é uma questão diferente
  (seção 4).

## 2. Por que o HTML estático não basta

- **Chave de API no HTML vaza.** O arquivo é copiado, e qualquer um vê a chave
  no código-fonte. Além disso, a regra do projeto diz que segredo só fica no `.env`.
- **A IA não pode fazer as contas.** Um LLM contando itens num JSON erra. Já
  aconteceu neste projeto: o agente escreveu 132 onde o dado dizia 131.
  **Os números saem do Python; a IA só entende a pergunta e redige.**
- **Os dados atuais não respondem.** O `helpdesk.json` só tem agregados, amostras
  de até 25 chamados e a fila aberta. "Holambra" aparecia 1 vez, dentro de uma amostra.

Arquitetura:

```
dashboard*.html (navegador do diretor)
   │ botão "Perguntar à IA"  →  http://NOME-DO-PC-DO-GUILHERME:PORTA
   ▼
servidor_chat.py (PC do Guilherme, tarefa agendada, lê o .env)
   │ 1. manda a pergunta + as definições das ferramentas para a IA
   │ 2. a IA escolhe: contar_chamados(local="Holambra CM", periodo=...)
   │ 3. o PYTHON executa sobre dados/chamados.json (número exato)
   │ 4. a IA redige a resposta com o resultado
   ▼
dados/chamados.json (todos os chamados, gerado pelo coletor novo)
```

## 3. Achados da sondagem do Milldesk (2026-09-23, somente leitura)

Autorizada pelo Guilherme. Feita com a `chamar()` de `coletores/check_helpdesk.py`,
sem alterar nada e sem gravar em `dados/`.

| Achado | Consequência |
|---|---|
| O "Local" do Milldesk é o campo **`location`** (ex.: `"Holambra CM"`), com **`id_local`** numérico. Relação 1:1, preenchido em 100% dos chamados | O filtro por local é confiável. Holambra teve 4 chamados de 24/08 a 22/09 |
| `showTicketsByStatus({"status": "Fechado"})` devolve **0** chamados | Esse endpoint **não** traz os fechados |
| `showTicketsPerPeriod({"start","end"})` devolve **abertos E fechados**: 912 chamados em 30 dias (823 fechados), em ~1 s | É o endpoint da coleta completa |
| 30 dias com todos os campos = **~1,3 MB**. `description` pesa ~27% | O histórico inteiro deve dar alguns MB. Medir no primeiro carregamento |
| Janeiro de 2024: 12 chamados. Janeiro de 2022: 9. Janeiro de 2019: 5. Hoje: ~900 por mês | **Não se sabe** se o histórico antigo é esparso ou se o período filtra por outra data. O primeiro carregamento confirma |
| `location = "Sino"`: 445 de 912 | São chamados internos da própria empresa |
| 98 locais distintos em 30 dias | — |
| Não há endpoint conhecido de "chamado por id" | Um chamado antigo que fecha depois só é atualizado por uma rebusca por período |

**Campos de um chamado** (41 em `showTicketsByStatus`, e `showTicketsPerPeriod`
acrescenta `workflow_fields`):
`agent, analysis, analysistime, asset, category, change, charge_hour, contactphone,
contract, department, description, end, endtime, group, id, id_local, impact,
impactdetails, level, location, manner, observation, priority, problem, reopening,
requester, resolution, satisfaction, satisfactioncomment, serial,
slasexpirationdate, solution, stage, start, starttime, status, subcategory,
ticket, tickettype, urgency, worked_hour, workflow_fields`

Campos com **dados pessoais**: `contactphone`, `requester`, `agent`, `description`,
`observation`, `satisfactioncomment`.

Endpoints conhecidos (todos de leitura): `listTicketStatus`, `showTicketsByStatus`,
`showTicketsPerPeriod`, `ticketsByAgent`. A API devolve HTTP 200 mesmo com erro;
use sempre `chamar()`.

## 4. Gemini gratuito: dá, mas com uma condição séria (DECISÃO PENDENTE)

**É tecnicamente possível.** A API do Gemini tem um plano gratuito e suporta
function calling, que é o que as ferramentas de contagem usam. Da arquitetura,
só muda a camada que conversa com a IA.

**O problema:** no plano gratuito, **o Google usa o que é enviado e o que é
respondido para treinar e melhorar os produtos dele**, e **revisores humanos
podem ler** o conteúdo. A própria documentação pede para não mandar
informação sensível, confidencial ou pessoal no plano gratuito. No plano pago,
não usa. Fontes:
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/terms_preview
- https://ai.google.dev/gemini-api/docs/logs-policy

**Outros riscos do gratuito:** os limites de uso são por projeto e **já foram
reduzidos pelo Google** mais de uma vez (há relatos no fórum). Para poucas
perguntas por dia costuma bastar, mas pode parar sem aviso.
Limites: https://ai.google.dev/gemini-api/docs/rate-limits

**Caminhos possíveis (o Guilherme decide):**
1. **Gemini gratuito + saída higienizada** (recomendado se tiver que ser grátis):
   as ferramentas devolvem para a IA só contagens, locais, sistemas, status e
   datas. **Nunca** `contactphone`, `requester`, `description`, `observation` nem
   `satisfactioncomment`. Nomes de técnicos trocados por rótulos ("Técnico 1") e
   restaurados no servidor antes de mostrar ao diretor. Custo zero; o que vai
   para o Google é praticamente só estatística.
2. **Gemini pago**: com o faturamento ativo, o Google não treina com os dados.
   Custo baixo por pergunta.
3. **Anthropic (pago)**: a escolha anterior; não treina com dados da API.

O servidor deve ter uma camada de provedor trocável (`CHAT_PROVEDOR=gemini|anthropic`)
para mudar de ideia sem refazer nada.

## 5. Plano por fases (nível XL: chave, rede, dados pessoais)

Cada fase: plano aprovado → implementação → revisão independente com **dois
revisores (correção + segurança)** → só então a próxima.

### Fase 1: coleta completa
- **Arquivo novo:** `coletores/check_chamados.py` → `dados/chamados.json`, com
  todos os chamados e todos os campos.
- **Primeira execução (carga inicial):** histórico inteiro, mês a mês, via
  `showTicketsPerPeriod`, com pausa entre as chamadas.
- **Diária (no `briefing.bat`):** rebusca os últimos N dias (`CHAMADOS_DIAS_REBUSCA`,
  sugerido 60) e todos os abertos (`showTicketsByStatus`).
- **Semanal:** rebusca o histórico inteiro, que é o que atualiza chamado antigo
  que fechou depois.
- **Somente leitura**, degradação obrigatória: se falhar, mantém o arquivo
  anterior e o pipeline segue. Grava `fonte`, `data`, `coletado_em`.
- `dados/chamados.json` já é ignorado pelo git (`dados/*.json`). **Os briefings
  (`.bat`/`claude -p`) e os HTML NUNCA leem esse arquivo.**
- Validação: conferir a contagem de Holambra contra o Milldesk na tela.

### Fase 2: `servidor_chat.py`
- Só biblioteca padrão (`http.server` + `urllib`), **sem dependência nova**.
- Ferramentas (Python conta, IA redige): `contar_chamados(filtros)`,
  `distribuicao(campo, filtros)`, `serie_por_mes(filtros)`,
  `listar_chamados(filtros, limite)`, `detalhe_do_chamado(id)`, `resumo_do_dia()`
  (lê `licencas.json`, `agenda.json`, `helpdesk.json`).
  Filtros: local, sistema, status, técnico, categoria, período, texto.
- Prompt do sistema: nunca citar número que não veio de uma ferramenta.
- **Segurança:** senha obrigatória (`CHAT_SENHA`) digitada no painel e guardada
  no navegador do diretor (nunca no HTML); limite de tamanho e de perguntas por
  minuto; CORS aceitando origem `null` (página aberta via `file://`); log em
  `chat.log` (git-ignorado).
- `DASHBOARD_MOSTRAR_RANKING=false` esconde nomes também nas respostas.

### Fase 3: botão nas páginas
- Componente compartilhado em `dashboard_base.py` (botão + painel lateral, tema
  SINO), usado por `gerar_dashboard.py` e `gerar_dashboard_diretor.py`.
- `CHAT_URL` (ex.: `http://NOME-DO-PC:8765`) entra no HTML na geração.
- Servidor fora do ar → "chat indisponível"; o resto da página funciona.

### Fase 4: serviço em segundo plano
- `instalar_servico_chat.bat`: tarefa agendada "ao fazer logon", `pythonw.exe
  servidor_chat.py` (sem janela), reinício automático se cair.
- `remover_servico_chat.bat` desfaz.
- Regra no firewall do Windows: porta liberada **só para o IP do PC do diretor**
  (`CHAT_IP_PERMITIDO`).
- `.bat` sem acentos e em CRLF, como os demais.

### Configuração nova (`.env` + linha placeholder no `.env.exemplo`)
`CHAT_PROVEDOR`, `GEMINI_API_KEY` (ou `ANTHROPIC_API_KEY`), `CHAT_MODELO`,
`CHAT_SENHA`, `CHAT_PORTA`, `CHAT_URL`, `CHAT_IP_PERMITIDO`, `CHAMADOS_DIAS_REBUSCA`.

## 6. Riscos conhecidos
- PC do Guilherme desligado ou hibernando → o chat do diretor para. Talvez seja
  preciso ajustar a energia do Windows.
- IP do PC pode mudar (DHCP) → usar o nome do PC. Se a rede não resolver nomes,
  pedir IP fixo à TI.
- Servidor exposto na rede com dados internos → senha + firewall por IP são
  obrigatórios, não opcionais.
- Carga extra no Milldesk de produção (carga inicial e rebusca semanal) → pausa
  entre as chamadas; tudo somente leitura.
- Limites do Gemini gratuito podem mudar sem aviso.

## 7. O que falta decidir antes de começar
1. **Aprovar a Fase 1.**
2. **Gemini:** gratuito + higienização, Gemini pago ou Anthropic (seção 4).
3. **Nome ou IP do PC do diretor** (para a regra do firewall; só na Fase 4).

## 8. Pendências de outros assuntos desta sessão
- **Guilherme sem atendimentos em 22/09** (Leitura do diretor mostrava só
  Roberto, Fabio e Luiz Araujo). Causa não confirmada. Suspeita: os
  atendimentos foram registrados de um jeito que não casa nem com o solicitante
  "Atendimento Diário" nem com a tag `AtendimentoDiario` no título (ex.: tag com
  espaço), e caíram nos 11 "chamados normais" do dia. Com `showTicketsPerPeriod`
  de 22/09 (que agora sabemos que traz tudo) dá para confirmar, mas é preciso
  autorização para consultar a produção.
- `testes/test_layout.py`: falha antiga `dashboard_licencas.html #movimentacao`
  (`p.aviso-escopo` sem `bloco-fixo`/`bloco-elastico`). Não corrigida.
- Opcional: comentário em `coletar_nomes_tecnicos()` (`dashboard_base.py`)
  dizendo que a segurança com `agentes_monitorados`/`devs_monitorados` corrompidos
  depende da redundância com `por_agente`/`por_dev`.

## 9. Já feito nesta sessão (para contexto)
- Diretor: menu reordenado (Agenda antes de Tendência) + "Fontes" no menu;
  contraste do texto sobre o verde (explicações, "Histórico local", rótulos soltos).
- `dashboard.html` migrado para o tema SINO (logo "SINO / Operação", menu em
  blocos, Destaques em `card_sino`, seções que rolam). Aprovado por revisor
  independente.
- `coletar_nomes_tecnicos()` não derruba mais a página com campo de tipo errado.
  Aprovado por revisor independente.
- Nada foi commitado (o Guilherme commita).
