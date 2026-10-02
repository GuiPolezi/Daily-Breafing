# Retomada: onde paramos em 01/10/2026

> Arquivo de passagem de bastão. Leia este primeiro; ele aponta para os outros.
> Detalhe e justificativas: `PLANO_SISTEMA_NOVO.md` (plano e decisões),
> `ANALISE_SISTEMA.md` (análise do sistema antigo e medições da API),
> `CLAUDE.md` seção "Sistema novo" (regras).

## Atualização de 02/10/2026 (vale sobre o que está abaixo)

- **Python 3.8 provado no servidor:** `test_banco.py` 34 de 34 e
  `test_metricas.py` 19 de 19, rodados pelo Guilherme. O servidor abre
  `https://v1.milldesk.com` (falta confirmar o sistema de licenças).
- **Briefing das 8h rodou sem 429** (coleta às 08:14). Coleta completa do sistema
  novo às 09:07 (coleta 4, 18 requisições) também passou inteira: o saldo se
  recompõe de um dia para o outro e 53 min de distância bastaram. A rápida a cada
  5 min segue sem medição.
- **Primeiros eventos reais (25), coerentes:** 16 chamados novos (12 já fechados,
  4 em "Com o Desenvolvedor"), 3 fechamentos, 6 mudanças de status, nenhuma troca
  de dono. Fila 327 → 316; a conta fecha (327 + 4 − 3 − 12).
- **12 chamados antigos em "Aguardando Retorno do Solicitante" sumiram da fila**
  (ids 5641, 5778, 5782, 5785, 6137, 6140, 6717, 7221, 7412, 7413, 7503, 7522) e
  estão com 1 ausência. Devem virar "fechado (inferido)" na próxima coleta
  completa. Falta o Guilherme confirmar se houve fechamento em lote.
- **Decisões da Fase 3:** tema SINO por enquanto, mas trocável no futuro (tema
  isolado do resto); **uma página por público**.

- **Pendências pequenas feitas e aprovadas em revisão independente** (itens 1 a 5
  da lista abaixo, mais a parte "rápida barrada grava vazio" do item 6). Testes:
  `test_metricas.py` 25 de 25, `test_banco.py` 35 de 35 (no PC; falta rodar de
  novo no servidor). Ficaram: fila parcial descartada no 429 (item 6), `sem_chave()`
  com `+`/espaço (7), `.env` legível pelo `claude -p` (8), e dois achados baixos da
  revisão: `bloco_agenda()` não checa tipo dos campos; `status_considerados_*` e
  chaves de dicionário não são checados.
- **Protótipo da Fase 3 no ar para crítica:** pasta `site/` (4 páginas, `base.css`,
  `tema-sino.css`, `app.js`) e `prototipo.py` (`python prototipo.py` →
  http://127.0.0.1:8765). Tema inteiro em `tema-sino.css`. Aguardando a crítica do
  Guilherme antes de virar o site.
- **Sumidos da fila: contar e avisar** (aprovado pelo Guilherme). O site mostra 328
  em aberto e o Milldesk 316; os 12 a mais são os chamados sumidos acima, que o
  banco mantém abertos até a segunda ausência. `metricas.estoque()` exporta
  `aguardando_confirmacao` e a seção "Agora" mostra o aviso com a fila sem eles.
- **Rodada 2 do protótipo** (o Guilherme testou e achou interessante): cada seção
  do `app.js` é isolada (dado quebrado vira aviso só naquela seção), a agenda do
  diretor checa tipo de campo. Testes: `test_metricas.py` 27 de 27. Revisão
  independente desta rodada: **aprovada** (28 dados quebrados testados, nenhum
  derrubou a página). Achados baixos, não corrigidos: o aviso dos sumidos não se
  protege de número maior que o total nem de número em texto (só com arquivo
  corrompido).
- **Desenho adiado para o fim** (decisão do Guilherme): o protótipo fica como
  está; o que mostrar e como mostrar se revê com o sistema rodando no servidor.
- **Fase 4 escrita e aprovada em duas revisões independentes (correção e
  segurança), ainda NÃO instalada no servidor.** Arquivos: `agendada.py` (uma
  tarefa só, a cada 5 min, decide rápida/completa/nada), `publicar_site.py`
  (`SITE_DIR`), `rodar_coleta.bat`, `instalar_tarefas.bat`, `remover_tarefas.bat`,
  `site/web.config.exemplo`, `testes/test_agendada.py` (15 de 15 no PC).
  A primeira versão foi REPROVADA (completa podia repetir a cada 5 min); a
  corrigida anota a tentativa em `dados/agendada.json` antes de coletar, trata
  banco ilegível como "não consultar" e usa `dados/agendada.lock`.
  Decisões: site novo com tudo novo no IIS (pool, aplicação); expediente
  07:00-19:00 seg-sex; silêncio 08:00-08:30 (briefing antigo roda 08:10); tarefa
  com o usuário guilherme.polezi; agenda coletada no servidor (credenciais no .env).
- **Não verificado da Fase 4:** nada rodou no servidor (Windows 7, Python 3.8,
  `schtasks`, IIS); o `web.config.exemplo` nunca foi testado; a cadência real
  contra o limite do Milldesk.
- **Achados em aberto da Fase 4 (nenhum bloqueia):** banco permanentemente
  ilegível para a coleta e só aparece no log (médio: falta sinal no site);
  primeira publicação cria os JSON com a permissão da pasta (médio: resolvido no
  passo a passo, restringindo `dados\` do site ANTES); coletores de licenças e
  agenda não mascaram segredo em erro; atalho plantado em `site/` seria publicado;
  trava e anotação não atômicas; `test_layout.py` com 1 falha antiga em
  `dashboard_licencas.html` (`p.aviso-escopo` sem contrato de flex).
- **Próximo passo:** instalar no servidor (passo a passo na conversa de 02/10) e
  acompanhar `dados\coleta.log` no primeiro dia.

## Em uma frase

Estamos trocando o briefing matinal (`.bat` + HTML estático + `claude -p`) por um
site com dados quase em tempo real para Suporte, Desenvolvimento, Diretor e
Licenças. Fases 1 e 2 de 5 estão prontas e revisadas. **Nada foi commitado.**

## Estado no fim do dia

| Fase | Estado |
|---|---|
| 1. Banco e coleta (`banco.py`, `coletar.py`) | pronta, aprovada, 34 testes |
| 2. Métricas e arquivos por público (`metricas.py`, `exportar.py`) | pronta, aprovada, 19 testes |
| 3. Visualização (protótipo, depois o site) | **não começada — é a próxima** |
| 4. Agendamento e publicação no servidor | não começada |
| 5. Virada (desligar o sistema antigo) | não começada |

**Nenhuma coleta está rodando sozinha.** Ainda não existe tarefa agendada: o banco
tem a coleta feita à mão em 01/10 às 15:49 (1.902 chamados, 327 abertos) e só
muda quando alguém roda `python coletar.py`. O sistema antigo continua rodando
normalmente pelo `briefing.bat`.

## Arquivos mexidos hoje (todos sem commit)

Novos: `banco.py`, `coletar.py`, `metricas.py`, `exportar.py`,
`testes/test_banco.py`, `testes/test_metricas.py`, `requirements-servidor.txt`,
`ANALISE_SISTEMA.md`, `PLANO_SISTEMA_NOVO.md`, `RETOMADA.md`.

Alterados:
- `coletores/check_helpdesk.py`: a chave da API não vaza mais em mensagem de erro
  (`sem_chave()`); erro inesperado sai em uma linha, sem traceback;
  `status_para_consultar()` aceita um argumento opcional; linha de
  compatibilidade com Python 3.8.
- `coletores/check_licencas.py`, `coletores/check_agenda.py`, `arquivar.py`: só a
  linha de compatibilidade com Python 3.8.
- `publicar.py`: `Path.is_relative_to` trocado por equivalente que existe no 3.8.
- `.env.exemplo`: `COLETA_DIAS_REBUSCA`, `COLETA_PAUSA_SEGUNDOS`.
- `CLAUDE.md`: seção "Sistema novo".

Gerados, fora do git (`dados/` é ignorado): `dados/briefing.db`,
`dados/site/{suporte,desenvolvimento,diretor,licencas}.json`.

## Decisões do Guilherme já tomadas

- Site com dados quase em tempo real; prioridade é **ótima visualização**.
- Coleta roda no **servidor** em produção (Windows 7, IIS 7.5, Python 3.8.9 já
  instalado); desenvolvimento e teste no PC dele (Python 3.14).
- **Sem IA por enquanto.** Lembrá-lo quando a base estiver pronta.
- O ritual matinal (toast, abrir páginas) não precisa sobreviver.
- Públicos: Suporte vê tudo; Desenvolvimento vê o mesmo por enquanto; Diretor vê
  agregados + agenda; Licenças vê só licenças.
- **Agenda: só na página do diretor.**
- Local que começa com "Sino" é interno.
- Limite do Milldesk desconhecido e a chave talvez seja usada por outros:
  "faça o que for recomendado" → coleta rápida a cada 5 min, completa a cada 60.

## O que NÃO foi verificado

1. **Python 3.8 de verdade.** Compatibilidade conferida só por análise do código
   (três revisores). Ninguém executou no 3.8.
2. **Eventos com dado real.** O banco ainda não registrou nenhuma mudança real de
   status ou dono: a 2ª e a 3ª coletas de 01/10 foram barradas pelo limite.
3. **O limite do Milldesk.** Sabemos só que existe (HTTP 429) e que o saldo volta
   devagar. A cadência 5 min / 60 min é uma aposta conservadora, não uma medida.
4. **Se o saldo gasto em 01/10 se recompôs** até o briefing da manhã seguinte.
5. **Um chamado a mais em 30/09**: o banco tem 9 que não são atendimento, a coleta
   daquela manhã viu 8. Causa não investigada.

## Primeiras coisas a fazer amanhã

Nesta ordem:

1. **Perguntar ao Guilherme como foi o briefing das 8h.** Se apareceu erro 429
   nele, o limite é mais apertado do que a hipótese e a cadência precisa mudar.
2. **Uma coleta completa, uma só** (`python coletar.py`, ~19 requisições,
   **com autorização dele**, e longe do horário do `briefing.bat`). É a primeira
   coleta depois da linha de base: vai gerar os primeiros eventos reais. Conferir
   se fazem sentido (fechamentos, mudanças de status, donos). Depois
   `python exportar.py` (não consulta API).
   **Não rodar duas coletas em sequência.** Para repetir, usar `--rapida`
   (1 requisição) e esperar pelo menos alguns minutos.
3. **Corrigir as pendências pequenas** (lista abaixo) numa rodada só, com revisão
   independente no fim.
4. **Fase 3: protótipo da visualização.** Ver seção própria abaixo.

Em paralelo, do lado do Guilherme:
- rodar no servidor `python testes\test_banco.py` e
  `python testes\test_metricas.py` (offline; exigem o projeto e
  `pip install -r requirements-servidor.txt` lá). É a prova do Python 3.8;
- confirmar que o servidor alcança `v1.milldesk.com` e o sistema de licenças.

## Pendências pequenas (nenhuma bloqueia)

1. `DASHBOARD_MOSTRAR_RANKING` ainda MOSTRA nomes para valores não previstos
   (`hide`, `oculto`, vazio, `false # comentário`). Inverter em `exportar.py`: só
   uma lista de valores que significam "mostrar" libera nomes. **Fazer antes de
   alguém usar a opção.**
2. `recorte_diretor` e o bloco de licenças repassam alguns campos sem checar o tipo.
3. Testes que faltam: filtro de valor simples de `so()`, temporário fora da pasta
   de saída, ordenação, `BEGIN`/`rollback`, data futura.
4. Aviso "banco indisponível (KeyError)" quando o que falta é a chave da API.
5. `ORDER BY` na leitura do fluxo, para a grafia exibida do cliente ser estável.
6. Fase 1: fila parcial é descartada quando o 429 vem no meio; coleta rápida
   barrada grava `total_periodo = 0` em vez de vazio.
7. `sem_chave()` não cobre chave com mistura de símbolos (`+` e espaço). Só
   importa se a chave real tiver esses caracteres.
8. Risco antigo, fora do sistema novo: os `claude -p` dos `.bat` podem ler o
   `.env`. Mitigação seria uma regra de permissão; **só com pedido do Guilherme.**

## Fase 3: como começar

A fase que define o resultado. **Começa por desenho, não por código de produção.**

- Entrada: os quatro `dados/site/*.json` (formato em `exportar.py`; conteúdo em
  `metricas.py`). O protótipo lê esses arquivos, não o banco.
- Entregar um protótipo navegável com os dados reais para o Guilherme criticar.
  Só depois vira o site.
- Princípios já acordados (detalhe em `ANALISE_SISTEMA.md` seção 6):
  - "agora" (estoque, com hora da coleta) separado de "período" (fluxo, com seletor);
  - uma dimensão por gráfico: dono × estágio é matriz (`tecnico_por_status`);
  - idade da fila é o gráfico principal (não existe SLA);
  - novidades que o banco permite: entrada × saída por dia, tempo de resolução;
  - "atualizado há X min" visível, virando alerta quando o dado envelhece
    (`frescor`), e aviso de desde quando o histórico vale (`historico_desde`);
  - fonte e definição ao lado de cada número (reaproveitar `METRICAS` e
    `FONTES_INFO` de `dashboard_base.py`).
- Perguntar ao Guilherme antes de desenhar: o site novo segue o tema SINO das
  páginas atuais ou é desenho novo? Uma página por público ou uma só com abas?
- Cuidado de segurança: `suporte.json` traz assunto e solicitante digitados por
  cliente. A página tem de escrever esse texto como texto (`textContent`), nunca
  como HTML.
- Lembrete do `CLAUDE.md`: **nunca usar a extensão Claude in Chrome**; para ver a
  página, Chrome headless local ou o Guilherme abre.

## Fase 4: o que já se sabe

- Duas tarefas agendadas no servidor: `coletar.py --rapida` a cada 5 min e
  `coletar.py` (completa) a cada 60 min, seguidas de `exportar.py` e da
  publicação. Configurar "não iniciar nova instância se já estiver em execução".
- `dados/site/` é área de preparo. Publicar copiando o conteúdo por cima do
  arquivo existente (`shutil.copyfile`), para a permissão NTFS por público
  sobreviver. Nunca gravar direto na pasta do IIS.
- IIS 7.5: liberar `.json` no `web.config` e registrar o tipo MIME.
- Licenças e agenda ainda vêm dos coletores antigos (`dados/licencas.json`,
  `dados/agenda.json`); precisam de coleta própria agendada.
- Projeto, `.env` e banco ficam FORA da pasta do site.
- O sistema antigo e o novo não podem consultar o Milldesk no mesmo minuto.

## Comandos

```
python testes/test_banco.py        # 34 testes, offline
python testes/test_metricas.py     # 19 testes, offline
python coletar.py --rapida         # 1 requisição ao Milldesk
python coletar.py                  # ~19 requisições: pedir autorização, espaçar
python exportar.py                 # gera dados/site/*.json, sem consultar API
```
