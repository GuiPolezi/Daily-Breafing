# Análise do sistema atual (base para o sistema novo)

> Arquivo de contexto. Criado em 2026-10-01 a partir da leitura de `coletores/`,
> `arquivar.py`, dos quatro `.bat`, dos geradores e de `dados/` + `historico/` reais.
> **Nada foi executado contra o Milldesk e nenhum código foi alterado.**
> Leia este arquivo ANTES de planejar o sistema novo. Complementa `CLAUDE.md`
> (regras e armadilhas) e `PLANO_CHATBOT.md` (achados da API, chatbot).

## 0. Para onde o sistema vai (intenção do Guilherme, 01/10/2026)

- Nasceu como resumo da primeira hora do dia, para uma pessoa. Agora a ideia é
  **escalar para outros setores**.
- O modelo "roda o `.bat` de manhã e gera HTML" não garante congruência: os dados
  são da coleta das 8h e boa parte é do dia anterior. Ele quer dados **em tempo
  real ou próximo disso**, num **website**.
- **Prioridade declarada: ótima visualização de dados.** O sistema novo é julgado
  por isso.
- O Claude roda na conta **pessoal** dele e não será conectado em outras máquinas
  da empresa. Se IA for necessária no sistema novo, tem que ser por API de
  servidor (Gemini foi citado).

## 1. Arquitetura atual, em uma página

```
briefing.bat (manual/agendado, 1x por manhã, no PC do Guilherme)
  coletores/check_helpdesk.py  -> dados/helpdesk.json   (Milldesk, API REST)
  coletores/check_licencas.py  -> dados/licencas.json   (scraping de HTML com login)
  coletores/check_agenda.py    -> dados/agenda.json     (Google Calendar API)
  arquivar.py                  -> historico/*.jsonl     (1 linha por dia)
  claude -p  x3 (diário, diretor, licenças) -> relatorio*.md
  gerar_dashboard*.py          -> dashboard*.html (400 KB+ cada, autocontido)
  publicar.py                  -> pasta do site no IIS (Autenticação do Windows)
```

Características: batch, sem servidor, sem banco, estado em arquivos JSON/JSONL,
HTML estático com tudo embutido (CSS, JS, fontes em base64, dados inline).

### O que cada coleta custa
- **Milldesk** (`check_helpdesk.py`): 1 `listTicketStatus` + 16 `showTicketsByStatus`
  (um por status não fechado) + 1 `ticketsByAgent` + 1 ou 2 `showTicketsPerPeriod`
  = **~19-20 GETs**, sequenciais, sem paginação. A fila tem ~315 chamados. A sondagem
  de 23/09 mediu ~1 s para 30 dias de `showTicketsPerPeriod` (912 chamados).
  Ou seja: a coleta inteira é barata. **Frequência não é o gargalo.**
- **Licenças**: 3 requisições (GET login, POST login, GET página) e parse de duas
  tabelas HTML por `<caption>`.
- **Agenda**: 1 token + 1 lista de calendários + 1 chamada por calendário
  (~0,7 s cada; com 26 calendários, ~19 s). É a coleta mais lenta.

### Endpoints do Milldesk em uso (todos de leitura)
`listTicketStatus`, `showTicketsByStatus`, `showTicketsPerPeriod`, `ticketsByAgent`.
Não se conhece: endpoint por id, webhook, paginação, limite de requisições,
filtro "alterado desde". **Tudo isso precisa ser perguntado ao Milldesk/à
documentação antes de desenhar o tempo real** (ver seção 5).

## 2. Que dado é "de agora" e que dado é "de ontem"

Esta é a raiz da sensação de dados entrelaçados. Um mesmo JSON e uma mesma linha
do histórico misturam três relógios:

| Tipo | Exemplos | Relógio |
|---|---|---|
| **Estoque** (foto) | `fila_total_abertos`, `fila.*`, `desenvolvimento.*`, `meus_abertos`, licenças | instante da coleta (~08:12) |
| **Fluxo** (o que aconteceu) | `atendimentos_ultimo_dia_util`, `chamados_criados` | **dia útil anterior** (na segunda: sexta, ou sexta..domingo) |
| **Diferença de fotos** | `dev_atribuidos_novos`, mudanças de licença | intervalo entre duas coletas, de tamanho variável |

A linha de `metricas.jsonl` é indexada pela **data da execução** (`data`), mas
carrega o fluxo do dia anterior (`atend_dia_ref`). Consequências já presentes:
- o semanal precisa deduplicar por `atend_dia_ref`, e essa regra vive em dois
  lugares (código e prompt);
- gráfico com eixo `data` plota a fila do dia D ao lado dos atendimentos de D-1;
- "hoje" no painel significa "hoje às 8h" para estoque e "ontem" para produtividade.

**No sistema novo: toda métrica precisa declarar se é estoque ou fluxo, e de qual
intervalo. O eixo do tempo de um fluxo é o dia em que o fato ocorreu, nunca o dia
em que foi coletado.**

## 3. Problemas de lógica encontrados

Ordenados por gravidade. "Lido no código" = não reproduzido em execução.

### 3.1 Graves

**A. A chave da API pode vazar para o JSON, para o relatório e para o site.**
A chave vai no caminho da URL (`/api/<chave>/<endpoint>`). Quando `requests`
falha (HTTP 4xx/5xx, timeout, DNS), a mensagem da exceção inclui a URL inteira.
Três lugares gravam `{e}` em dado que segue adiante:
`status_para_consultar()` → `aviso_status`; `ticketsByAgent` →
`contagem_por_tecnico = "indisponível (...)"`; `chamados_criados_do_periodo()` →
`aviso`. O prompt do `briefing.bat` manda citar `aviso_status` literalmente
("AVISO DE COLETA: <texto>"), o texto vai para `relatorio.md`, para o
`dashboard.html` e de lá para o IIS. Também vai para o console/`briefing.log`.
Lido no código. Vale para o sistema atual, não só para o novo: sanitizar a
mensagem (trocar a chave por `***`) antes de gravar.

**B. Fluxo coletado uma vez só: dia sem execução vira buraco para sempre.**
`atendimentos_do_dia(ultimo_dia_util())` olha só um dia. Se o `.bat` não rodar
numa terça, os atendimentos de segunda nunca entram no histórico. E não há
feriado na conta: depois de um feriado, coleta-se o feriado (zero) e o dia útil
de verdade se perde. `chamados_criados` tem o mesmo defeito ("encadeia sem
buraco" só vale se rodar todo dia útil).

**C. Atendimento de fim de semana nunca é contado; chamado criado é.**
Na segunda, `atendimentos` cobre só a sexta, e `chamados_criados` cobre
sexta..domingo. Dois períodos diferentes dentro do mesmo bloco
`atendimentos_ultimo_dia_util`, cujo campo `dia` diz só "sexta".

**D. Atendimento só conta se já estava Fechado no momento da coleta.**
O filtro `status == fechado` é aplicado uma vez, na manhã seguinte. Um
atendimento fechado depois disso some (aparece só em
`do_solicitante_mas_nao_fechados` daquele dia e nunca é revisitado). Além disso,
**não se sabe por qual data o `showTicketsPerPeriod` filtra** (abertura?
fechamento?). O próprio código admite isso em `chamados_criados_por_cliente`.
Toda a produtividade depende dessa resposta.

### 3.2 Médios

**E. Fila montada por 16 chamadas não atômicas, sem deduplicar por id.**
`todos.extend(...)` por status. Chamado que muda de status no meio da coleta
pode entrar duas vezes ou nenhuma. Raro às 8h; com coleta frequente em horário
comercial deixa de ser raro.

**F. `Sino - Compilação` conta como cliente.** `HELPDESK_LOCAIS_INTERNOS` compara
exato com `Sino`; o local `Sino - Compilação` passou (visto em `criados_por_cliente`
de 01/10/2026, com `internos_excluidos: 0`). **Confirmar com o Guilherme se é
interno** antes de mexer.

**G. Três conjuntos sobrepostos apresentados como se fossem partes de um todo.**
`fila_total_abertos` (315), `total_atribuidos_a_devs` (277, por *pessoa*),
`total_em_status_dev` (258, por *status*), `meus_abertos` (22, por *pessoa*, outra
lista). Não somam, não são partição, e um chamado pode estar em três. "X% da
fila está com o desenvolvimento" muda conforme qual dos dois se usa. O sistema
novo deve escolher UMA dimensão por visualização (dono OU estágio) e mostrar a
outra como quebra.

**H. Regras de comparação de texto inconsistentes para a mesma coisa.**
Status de dev: por **trecho** (`s in atual`). Status de trabalho: **exato**.
Status excluído: **exato**. Dev e agente: por **trecho**, primeiro que casa
vence. Técnico canônico: **exato**. Sistema: por **trecho** com ordem importando
(`Site=site` casa com qualquer categoria que contenha "site"). Cada uma foi
decidida num dia diferente por um motivo local; nenhuma está errada hoje, mas o
conjunto é frágil e não tem teste. Sistema novo: ids quando a API der
(`id_local` existe), e uma tabela de mapeamento única.

**I. `novos_hoje` é sempre ~0.** Conta chamados com `start` = hoje, medido às 8h.
Aparece em cards do diário e do diretor ("N novos hoje"). Métrica sem valor no
modelo batch; passa a fazer sentido só com tempo real.

**J. `contagem_por_tecnico` é total histórico, não fila.** `ticketsByAgent`
devolve o acumulado de todos os tempos (1718, 859...). No código serve só para
coletar nomes a esconder. Está no JSON que o Claude lê, ao lado de
`fila.por_tecnico` (em aberto): convite a confusão.

**K. `chamados_normais_abertos_no_dia` tem nome que promete mais do que entrega.**
É "tudo que veio do endpoint menos atendimento diário", inclui fechados e
depende da data desconhecida do item D.

**L. Marcador de régua pela QUANTIDADE de status.** `fila_status_qtd` = 16. Se um
status for criado e outro removido no mesmo dia, o número não muda e o degrau
passa sem aviso. Deveria ser o conjunto (ou um hash dele).

**M. O texto e os cards calculam a mesma coisa por caminhos diferentes.** O prompt
pede ao Claude média de 10 dias, soma semanal, ranking, diferença de fila. O
Python calcula essas mesmas coisas para os cards. Já divergiu (132 x 131). Ver
seção 4.

### 3.3 Menores / dívida

- `metricas.jsonl` carrega campos mortos (`email_*`, do coletor de e-mail
  removido) e duplicados (`fila_site`/`fila_siscam9`/`fila_siscam8` ao lado de
  `fila_por_sistema`; `abertos` ao lado de `total_no_nome`).
- `fila_total_base_anterior` depende de uma variável de ambiente
  (`HELPDESK_STATUS_ABERTOS`) que também é fallback: dois papéis, um nome.
- `_debug_campos_do_primeiro_ticket` vai no JSON de produção.
- `slasexpirationdate`, `worked_hour`, `charge_hour`, `satisfaction`,
  `reopening`, `end`/`endtime` vêm da API e são descartados. `end` e `reopening`
  são exatamente o que falta para medir **tempo de resolução** e **reabertura**.
- Licenças: identidade = (cliente, sistema, vencimento) em texto. Renovação é
  inferida por diferença de fotos; mudou a grafia do cliente, vira "saiu + entrou".
- Licenças por scraping: quebra se o HTML mudar (`<caption>`, ordem das colunas).
- `helpdesk.json` leva nome de solicitante (dado pessoal de cliente) para o
  Claude, numa conta pessoal. Decisão de governança, não bug.
- `_tmp_extrai.py` na raiz: resto de sessão anterior.

### 3.4 O que está bem resolvido (preservar)

- Degradação: fonte fora do ar não derruba o resto.
- "Em aberto = tudo menos Fechado", lido da própria API a cada coleta.
- Trilha de auditoria no JSON (`status_consultados`, `por_modo`, `periodo`...).
- Detecção de degrau falso (`fila_status_qtd`, `lic_janela_dias`, régua das
  atribuições): a ideia é certa e deve virar conceito de primeira classe.
- Explicação de fonte e de métrica em todo card (`FONTES_INFO`, `METRICAS`).
- Amostra x contagem: contagens sempre sobre a fila inteira.
- Coletores somente-leitura.

## 4. Quanto o Claude realmente faz

**Não influencia nenhum card nem gráfico.** Os quatro geradores leem `dados/*.json`
e `historico/*.jsonl` e calculam tudo em Python. O `relatorio*.md` entra em **uma**
seção por página: a de id `briefing` (os slides de texto). Confirmado por busca:
`relatorio` só aparece em `ler_relatorio()` e na montagem de `secao_briefing`.

O que o Claude produz hoje, de fato:
1. **Transcrição de número para frase** (a maior parte do prompt): "diga quantos",
   "liste por_dev", "compare com o dia anterior". Isso é template, não precisa de IA.
2. **Aritmética**: média de 10 dias, somas da semana, ranking. Pior uso possível
   de um LLM, e duplicado com o Python.
3. **Julgamento** (pequena parte): "Atenção hoje", "o que a proporção sugere",
   "Sugestão de prioridade". Aqui a IA agrega algo.

Custo disso: 3 chamadas por dia, cada uma lendo o `helpdesk.json` de 79 KB; os
prompts são um segundo contrato de schema (quebra silenciosa); cada regra de
negócio existe duas vezes; e amarra o pipeline ao PC e à conta pessoal do Guilherme.

**Conclusão: o Claude não é estrutural. Tirá-lo não muda nenhum número do painel.**
No sistema novo: itens 1 e 2 viram código determinístico; o item 3 é opcional,
por API de servidor, recebendo **só agregados já calculados** (sem nome de
solicitante), com cache (1 vez por dia ou sob demanda), e a página funciona
inteira sem ele. Provedor trocável, como já previsto em `PLANO_CHATBOT.md`
(atenção: o Gemini gratuito treina com o que recebe).

## 5. Tempo real: o que a análise indica

**O que impede o tempo real hoje não é a coleta, é o que vem depois dela**: os
três `claude -p` (minutos) e a geração de quatro HTMLs monolíticos. A coleta do
Milldesk são ~20 GETs.

**"Como ter um Python sempre rodando?" Não precisa.** Opções, da mais simples
para a mais complexa:

1. **Tarefa agendada a cada N minutos** (Agendador de Tarefas do Windows, no
   **servidor**, não no PC do Guilherme). Roda o coletor e sai. Sem processo
   residente, sem vazamento de memória, reinicia sozinha. Recomendada.
2. Serviço residente com laço e `sleep` (precisa de supervisão/reinício).
3. Coletar sob demanda a cada abertura da página: **não**. Multiplica a carga no
   Milldesk pelo número de pessoas olhando e deixa a página lenta.
4. Webhook do Milldesk (tempo real de verdade): só se existir. **A verificar.**

**Forma recomendada do sistema novo:**

```
Agendador (servidor) a cada 5-10 min
  -> coletor -> banco SQLite: tabela de chamados (upsert por id, campos crus)
                              + tabela de eventos (mudou status / dono / fechou)
                              + fotos diárias (o metricas.jsonl de hoje)
  -> API de leitura pequena (ou JSONs prontos publicados no IIS)
  -> site: casca estática + fetch dos dados + "atualizado há X min"
```

Por que guardar o chamado cru e os eventos, e não só agregados:
- resolve B, C, D e E de uma vez: fluxo passa a ser **consulta sobre o banco**
  ("fechados em tal dia"), recalculável, sem buraco e sem depender da hora da coleta;
- cria o histórico que o Milldesk não tem (atribuição, mudança de status, tempo em
  cada estágio), hoje aproximado por diferença de fotos;
- filtros, drill-down e o chatbot do `PLANO_CHATBOT.md` passam a ser possíveis;
- escalar para outro setor = outro recorte da mesma base, não outro gerador.

Licenças mudam uma vez por dia: coleta de hora em hora já é excesso. Agenda:
a cada 15-30 min. **Cada fonte com a sua cadência e o seu carimbo de frescor na tela.**

### Respostas do Guilherme (01/10/2026)
- **Servidor:** Windows com IIS, já hospeda outras aplicações; ele acessa e
  administra, e dá para agendar tarefas. **Não se sabe se tem Python instalado**
  (verificar: versão, e se o IIS ali já roda algo além de estático/.NET).
- **`Sino - Compilação` é interno** (um solicitante da própria empresa). Internos
  têm de casar por regra mais larga que o `Sino` exato: decidir com ele se é
  "começa com Sino" ou lista explícita (cuidado: existe o solicitante
  `Sino CM Softlândia`, que parece cliente de teste).
- **Públicos e acesso:**
  - Suporte: vê tudo.
  - Desenvolvimento: vê tudo, com possível restrição ainda não definida.
  - Diretor: indicadores de rendimento de suporte e desenvolvimento.
  - Licenças: só licenças, com todos os detalhes delas.
- **IA fica de fora por enquanto.** Primeiro uma base sólida; a IA entra depois.
  Lembrar o Guilherme disso quando a base estiver pronta.
- **Briefing matinal:** ele não tem apego ao ritual (toast, abrir páginas); quer
  só visualizar as informações. O site substitui, não convive.
- **Data do `showTicketsPerPeriod`:** ele não sabe. Tem de ser medido (comparar
  `start`/`end` dos chamados devolvidos com o período pedido), com autorização
  para consultar a produção.
- O item A (vazamento da chave) foi corrigido no coletor em 01/10/2026
  (`sem_chave()` em `check_helpdesk.py`).

### Sondagem do `showTicketsPerPeriod` (01/10/2026, autorizada, 2 GETs de leitura)
Períodos 15/09 (62 chamados) e 14..18/09 (306 chamados):
- **Filtra pela data de ABERTURA (`start`).** 100% dos chamados tinham `start`
  dentro do período; o fechamento podia ser depois (15 de 306) ou não existir.
- Traz abertos e fechados, sem id repetido.
- **`end` não é confiável**: vem vazio em chamado já `Fechado` (98 sem `end` contra
  só 20 não fechados). `endtime` veio preenchido onde `end` era `None`. Para data
  de fechamento, usar `endtime` e conferir antes de confiar.
- `start` é só a data (`15/09/2026`); a hora está em `starttime` (`15/09/2026 08:28`).
- Consequências:
  - "atendimentos do dia D" hoje = abertos em D **e já fechados na manhã de D+1**.
    Rebuscar D mais tarde dá um número maior. Com o banco, reconsultar os últimos
    N dias a cada coleta corrige sozinho.
  - **Não existe consulta por data de fechamento.** "Fechados hoje" só sai de:
    (a) chamado que estava na fila aberta e sumiu (diferença de fotos por id), ou
    (b) rebusca de uma janela de aberturas olhando `status`/`endtime`.
    Chamado antigo que fecha só é pego por (a). O banco com upsert por id é o que
    viabiliza isso.
- Locais que contêm "sino" nesses 5 dias: só `Sino` (281) e `Sino - Compilação` (3).
  O Guilherme acredita que tudo que começa com "Sino" é interno.

### Primeira coleta real para o banco (01/10/2026 15:49, autorizada)
Fila aberta + rebusca de 60 dias (03/08 a 01/10), 19 GETs:
- Fila: 327 chamados, nenhum id repetido. Rebusca: 1731 chamados (1575 fechados).
- **A rebusca devolve os mesmos campos que a fila**: nos 156 chamados presentes nas
  duas, nenhum campo usado pelo banco divergiu. Só sobra `workflow_fields`.
- **`endtime` é confiável como data de fechamento**: presente em 1575 de 1575
  fechados, nunca anterior ao `starttime`. `end` só em 1356. (3 chamados ABERTOS
  têm `endtime`: provavelmente reabertos; por isso o banco só guarda a data de
  chamado fechado.)
- Nenhum chamado da rebusca veio sem status.
- **Atendimentos diários por dia de abertura batem com o histórico**: 30/09 = 43,
  29/09 = 57, 28/09 = 49, 25/09 = 34, iguais ao `atend_total` do `metricas.jsonl`.
- Fila às 08:12 = 315; às 15:49 = 327 (Site 32 -> 39, Siscam 9 90 -> 95). É a
  diferença que o painel da manhã não mostra.
- **O Milldesk TEM limite de requisições.** Duas coletas com 22 s de intervalo:
  depois de ~32 requisições em menos de um minuto, HTTP 429 (Too Many Requests).
  Uma coleta sozinha (19) passa. Consequências para o desenho: nunca duas coletas
  coladas; o pipeline antigo (20 GETs) e o novo não podem rodar no mesmo minuto;
  vale espaçar as chamadas dentro da coleta. Janela e teto exatos: desconhecidos.
- **Terceira coleta, 5 min 42 s depois (15:55): só 6 requisições passaram** antes
  do 429. O limite NÃO é "N por minuto que zera": parece um saldo que enche
  devagar (hipótese: ~1 requisição por minuto, com reserva de ~30). Dois pontos de
  medida só; não é fato. Se for isso, uma coleta completa (19) só cabe a cada
  ~20 min, e **a cadência de 10 min do plano não se sustenta**.
  Caminho provável: coleta RÁPIDA frequente (1 chamada: `showTicketsPerPeriod` dos
  últimos dias, que pega chamado novo, fechamento e mudança dos recentes) + coleta
  COMPLETA espaçada (fila inteira, que é a única que conta ausência). E parar a
  coleta no primeiro 429 em vez de insistir. Antes de desenhar: perguntar o limite
  ao suporte do Milldesk, e saber se mais alguém usa a mesma chave.
  Não consultar mais a API em 01/10 sem falar com o Guilherme.
- A coleta com 429 degradou como devia: gravada como incompleta, nenhuma ausência
  contada, nenhum evento, chave fora das mensagens.

### Servidor sem Python (resposta de 01/10/2026)
O servidor não tem Python. O desenho não exige servidor web em Python: o IIS
serve a casca estática e os JSONs; o Python é só a tarefa agendada que coleta.
Opções: instalar Python no servidor (leve), ou empacotar o coletor num `.exe`
(PyInstaller, dependência nova a aprovar), ou manter a coleta no PC do Guilherme
publicando no compartilhamento (depende do PC ligado). Decisão pendente.

### Perguntas abertas (bloqueiam o desenho)
1. Por qual data o `showTicketsPerPeriod` filtra? (abertura, fechamento, alteração)
2. O Milldesk tem limite de requisições? Webhook? Endpoint por id?
3. Onde o serviço roda: o servidor do IIS pode executar Python e tarefa agendada?
   Quem administra?
4. Quais setores entram e o que cada um precisa ver (define permissões por página;
   hoje é NTFS por arquivo).
5. `Sino - Compilação` é interno?
6. Dependências novas (framework web, banco) precisam de aprovação: `CLAUDE.md`
   pede stdlib por padrão. SQLite é stdlib.
7. O texto de IA continua existindo? Se sim, com qual provedor e orçamento.
8. O briefing matinal (toast, abrir páginas) continua existindo ao lado do site?

## 6. Para a visualização (prioridade do Guilherme)

Notas para o planejamento, a detalhar com ele:
- Separar na tela **agora** (estoque, com hora da coleta) de **período** (fluxo, com
  seletor de intervalo). Hoje as duas coisas dividem o mesmo card.
- Uma dimensão por gráfico (item G). Dono x estágio é uma matriz, não dois totais.
- Idade da fila é a medida de urgência (não há SLA): merece o gráfico principal,
  não um card.
- Com o banco de chamados dá para mostrar o que hoje não existe: tempo de
  resolução, reabertura, entrada x saída por dia, tempo parado em cada status.
- Degrau de régua vira anotação no próprio gráfico, não supressão de badge.
- Manter: explicação de métrica e de fonte ao lado de cada número.
