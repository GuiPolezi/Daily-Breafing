# Plano: sistema novo (site com dados quase em tempo real)

> Criado em 2026-10-01. **Status: AGUARDANDO APROVAÇÃO. Nada implementado.**
> Base: `ANALISE_SISTEMA.md` (ler antes). Regras do projeto: `CLAUDE.md`.

## Decisões já tomadas pelo Guilherme (01/10/2026)

- Site com dados em tempo quase real, para vários setores. Prioridade: **ótima
  visualização de dados**.
- **A coleta roda no SERVIDOR em produção** (decisão final de 01/10/2026: o
  Guilherme vai instalar o Python lá). Fases 1 a 3 são desenvolvidas e testadas
  no PC dele; a tarefa agendada vai para o servidor na Fase 4. Onde este plano
  diz "PC do Guilherme" no desenho, leia "servidor" a partir da Fase 4.
  No servidor: instalar por usuário ou em pasta própria, **sem** marcar "Add to
  PATH" e sem tocar no IIS; projeto e banco FORA da pasta do site.
- **Fase 1 concluída em 01/10/2026** (`banco.py`, `coletar.py`,
  `testes/test_banco.py`, 24 testes): aprovada por dois revisores independentes
  (correção e segurança) e validada com uma coleta real (ver `ANALISE_SISTEMA.md`).
  Pendências antes de agendar: (1) dividir em coleta rápida e coleta completa por
  causa do limite de requisições do Milldesk, e parar no primeiro 429; (2) status
  que responde lista vazia duas vezes seguidas ainda gera "fechou" falso (médio,
  some na coleta seguinte como "reabriu"); (3) rodar os testes no servidor (3.8).
  **A cadência de 10 min descrita abaixo foi substituída** (decisão de 01/10/2026,
  "faça o que for recomendado"; o limite oficial é desconhecido e a chave talvez
  seja usada, pouco, por outros):
  - coleta **rápida** (`coletar.py --rapida`, 1 requisição) a cada 5 min;
  - coleta **completa** (~19 requisições) a cada 60 min;
  - total ~30 requisições/hora, metade do que a hipótese do limite permite
    (~1/min), deixando folga para o briefing da manhã e para outros usos da chave;
  - cada coleta grava `modo` e `requisicoes` na tabela `coletas`, e todo 429 vira
    aviso: depois de uma semana rodando dá para apertar ou afrouxar com dado real.
  Itens (1) e (2) acima foram implementados nessa rodada.
- **Sem IA** por enquanto (lembrá-lo quando a base estiver pronta).
- O ritual matinal (toast, abrir páginas) não precisa sobreviver.
- Públicos: Suporte (tudo), Desenvolvimento (tudo, restrição a definir), Diretor
  (rendimento de suporte e dev), Licenças (só licenças).
- Internos: local que começa com "Sino".

- **Agenda: fica só na página do diretor** (decisão de 01/10/2026). É a agenda da
  empresa (os calendários da equipe), não só a do Guilherme.
- **Fase 2 concluída em 01/10/2026** (19 testes; aprovada por dois revisores
  independentes, correção e controle de acesso). Regras fixadas nela:
  `dados/site/` é área de PREPARO privada, nunca a pasta do IIS (a troca atômica
  recria o arquivo e apagaria a permissão NTFS por público; a Fase 4 publica com
  `shutil.copyfile` por cima do arquivo existente); o recorte do diretor é lista
  de permissão em todos os níveis; com a flag de esconder nomes, a agenda inteira
  fica de fora.
  Pendências para a próxima rodada (nenhuma bloqueia): (1) a flag
  `DASHBOARD_MOSTRAR_RANKING` ainda mostra nomes para valores não previstos
  (`hide`, `oculto`, vazio, `false # comentario`): inverter para lista de valores
  que MOSTRAM; (2) campos repassados sem checagem de tipo no recorte do diretor e
  nas licenças; (3) testes que prendam o filtro de valor simples de `so()`, o
  temporário fora da pasta, a ordenação e o BEGIN/rollback; (4) mensagem
  "banco indisponível (KeyError)" quando falta a chave; (5) `ORDER BY` na leitura
  do fluxo para a grafia do cliente ser estável; (6) pendências baixas da Fase 1
  (fila parcial descartada no 429, coleta rápida barrada grava 0).
  Arquivos: `metricas.py`, `exportar.py`, `testes/test_metricas.py`. Gera `dados/site/{suporte,desenvolvimento,diretor,
  licencas}.json`. O recorte de cada público é lista de permissão em `exportar.py`.
  Conferido contra o histórico do sistema antigo com o banco real: criados por
  clientes em 29/09 = 12 e 30/09 = 8, atendimentos 25/09 a 30/09 = 34/49/57/43,
  licenças 14 vencendo e 6 vencidas. Licenças e agenda ainda vêm dos JSONs dos
  coletores antigos (`dados/licencas.json`, `dados/agenda.json`).
  A observar: em 30/09 o banco tem 9 chamados que não são atendimento (8 de cliente
  + 1 interno), e a coleta daquela manhã viu 8. Um chamado com data de 30/09
  apareceu depois; causa não investigada.

## Servidor: Windows 7 + Python 3.8.9 (verificado em 01/10/2026)

O servidor é Windows 7 (IIS 7.5); 3.8 é a última linha do Python que roda nele.
O PC do Guilherme usa 3.14. **Todo código que roda no servidor tem de valer no 3.8.**
Verificado por varredura estática (não há 3.8 no PC para executar) e por consulta
ao PyPI:
- `banco.py`, `coletar.py`, `publicar.py`, `notificar.py`, `dashboard_base.py` e
  os geradores: compatíveis (têm `from __future__ import annotations`).
- **Quebram no 3.8** por anotações `X | None` / `list[...]` avaliadas:
  `coletores/check_helpdesk.py`, `check_licencas.py`, `check_agenda.py`,
  `arquivar.py`. Correção: uma linha em cada (`from __future__ import annotations`).
- **`requirements.txt` não instala no 3.8** (`requests>=2.34.2` não existe para ele).
  Sem os pisos, o pip resolve: requests 2.32.4, python-dotenv 1.0.1,
  beautifulsoup4 4.15.0, urllib3 2.2.3. Fazer um `requirements-servidor.txt`.
- Regras para código novo: `from __future__ import annotations` em todo arquivo;
  nada de `match`, `str.removeprefix`, `dict | dict`, `zip(strict=)`,
  `isinstance(x, A | B)`, `zoneinfo`; f-string sem aspas iguais aninhadas.
- Antes da Fase 4: rodar `testes/test_banco.py` NO servidor (é offline).
- IIS 7.5 não conhece `.json` por padrão: além do `web.config`, registrar o tipo MIME.
- Python 3.8 e Windows 7 não recebem mais correção de segurança. A tarefa só faz
  requisições de saída (não abre porta), o que limita a exposição, mas é um risco
  aceito e não eliminado.

## Desenho

```
PC do Guilherme — Agendador de Tarefas, a cada N min em horário comercial
  coletar.py
    Milldesk  -> banco local SQLite (briefing.db)  [nunca publicado]
    Licenças  -> idem (cadência própria, mais lenta)
  exportar.py
    banco -> um JSON por público, só com o que aquele público pode ver
  publicar.py (o que já existe, estendido)
    -> \\SERVIDOR\site\  { index.html, assets/, dados/<publico>.json }

Servidor IIS (sem mudança de software)
  entrega arquivos estáticos com Autenticação do Windows
  permissão NTFS por arquivo de dados = quem vê o quê

Navegador
  página estática + busca o JSON do seu público a cada 60 s
  mostra "atualizado há X min"; fica em alerta quando o dado envelhece
```

**Consequência assumida da escolha:** PC desligado, hibernando ou fora da rede =
dados parados. O site não esconde isso: o carimbo de frescor vira alerta visível.
Migrar a coleta para o servidor depois é trocar onde a tarefa agendada roda; o
resto não muda.

## O banco (SQLite, biblioteca padrão)

| Tabela | Conteúdo | Resolve |
|---|---|---|
| `chamados` | um registro por id, campos crus úteis, `visto_em` | fila sem duplicata (item E da análise) |
| `eventos` | abriu, mudou status, mudou dono, fechou, reabriu — com hora da coleta que detectou | histórico que o Milldesk não tem; "fechados hoje" |
| `coletas` | cada execução: hora, fonte, sucesso/erro, régua de status | frescor por fonte; degrau de régua |
| `licencas` + `licencas_eventos` | estado atual e mudanças | o que renovou/venceu sem comparar fotos à mão |
| `fotos_diarias` | o `metricas.jsonl` de hoje, importado | continuidade dos gráficos de evolução |

Regras de coleta:
- Fila aberta: `showTicketsByStatus` de cada status não fechado, **deduplicada por id**.
- Fluxo: `showTicketsPerPeriod` dos **últimos N dias de abertura** a cada coleta
  (não só ontem). Corrige atendimento que fecha depois, fim de semana, feriado e
  dia sem execução (itens B, C, D).
- Fechamento: chamado que estava aberto e sumiu da fila vira evento "fechou",
  confirmado na rebusca por período quando a abertura estiver na janela.
- Toda mensagem de erro passa por `sem_chave()` antes de ir ao banco.
- Coletores continuam somente-leitura.

## Métricas

Uma camada só (`metricas.py`) calcula tudo a partir do banco. Cada métrica declara:
**estoque** (foto, com hora) ou **fluxo** (com intervalo, datado pelo dia do fato).
As explicações de `METRICAS`/`FONTES_INFO` são reaproveitadas.

Critério de aceite desta camada: no mesmo instante, os números de estoque batem
com os do `helpdesk.json` do sistema atual (fila total, por sistema, por dev, por
status, idade). Diferença só onde a análise apontou erro, e listada.

## Fases

Cada fase: implementação → validação com dado real → revisão independente → próxima.

**Fase 1 — Banco e coleta.** `banco.py`, `coletar.py`, importação do histórico
existente. Roda ao lado do sistema atual, sem tocar nele. Aceite: paridade de
estoque com o `helpdesk.json`; segunda coleta gera eventos coerentes.
*Carga inicial: uma rebusca de ~60 dias no Milldesk (leitura), a autorizar.*

**Fase 2 — Métricas e exportação.** `metricas.py`, `exportar.py`, um JSON por
público. Aceite: teste automatizado de paridade; JSON do público Licenças sem
nenhum nome de pessoa (imposto no código, como hoje).

**Fase 3 — Visualização.** É a fase que define o resultado, e começa por
**desenho, não por código**: eu monto um protótipo navegável com os seus dados
reais, você critica, e só então vira o site. Princípios vindos da análise:
- "agora" (estoque) separado de "período" (fluxo), com seletor de intervalo;
- uma dimensão por gráfico: dono × estágio é matriz, não dois totais;
- idade da fila como gráfico principal (não há SLA);
- novidades que o banco permite: entrada × saída por dia, tempo de resolução,
  tempo parado em cada status, reabertura;
- mudança de régua como anotação no gráfico;
- fonte e definição ao lado de cada número.

**Fase 4 — Agendamento e publicação.** Tarefa agendada (`.bat` de instalar e de
remover), `publicar.py` estendido, alerta de frescor. No servidor, uma mudança
sua: o `web.config` hoje só entrega `.html`; precisa entregar também `.json`
(e `.js`/`.css` se os assets saírem do HTML).

**Fase 5 — Virada.** Os `.bat` antigos e o `claude -p` saem de cena depois de uma
semana com os dois sistemas lado a lado e números conferidos.

## Segurança e acesso

- O banco, o `.env` e o histórico **nunca** vão para o servidor (a guarda de
  `publicar.py` contra destino dentro do projeto continua).
- Separação por público é feita **no dado**, não na tela: cada público tem o seu
  JSON e a permissão NTFS é no arquivo. Esconder por JavaScript não é controle.
- Nome de solicitante (dado de cliente) só nos JSONs de Suporte e Desenvolvimento.

## Riscos

| Risco | Tratamento |
|---|---|
| PC desligado = site parado | alerta de frescor; migração futura para o servidor é barata |
| Limite de requisições do Milldesk desconhecido | começar em 10 min; medir; recuar se houver erro |
| Evento só é visto na coleta seguinte | hora do evento = hora da coleta; duas mudanças entre coletas viram uma |
| `endtime` não validado como data de fechamento | conferir na Fase 1 antes de usar |
| Escrita do JSON durante a leitura do navegador | gravar em arquivo temporário e renomear |

## Para você decidir

1. **Cadência:** proponho Milldesk a cada 10 min, seg–sex 07h–19h; licenças a cada
   hora. Serve?
2. **Agenda (Google):** é a sua agenda pessoal. Sai do site multi-setor, ou fica
   numa página só sua?
3. **Restrição do Desenvolvimento:** começo com Dev vendo o mesmo que Suporte e
   separamos quando você souber o quê. Serve?
4. **Carga inicial:** autoriza a rebusca de ~60 dias no Milldesk na Fase 1?
5. **Dependência nova:** nenhuma prevista (SQLite é da biblioteca padrão; gráficos
   seguem com o Chart.js já embutido). Se o protótipo pedir outra biblioteca de
   gráfico, eu pergunto antes.
