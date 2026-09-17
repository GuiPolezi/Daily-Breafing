@echo off
REM Briefing diario - roda coletores, arquiva metricas e sintetiza com Claude Code
REM Ao final dispara tambem os briefings da diretoria e do financeiro (ambos diarios),
REM que NAO rodam coletores: reaproveitam os JSONs ja coletados aqui.
cd /d "%~dp0"
call .venv\Scripts\activate.bat

echo === Briefing %date% %time% ===

for %%f in (coletores\check_*.py) do (
    echo --- rodando %%f
    python "%%f" || echo AVISO: %%f falhou, seguindo sem essa fonte
)

echo --- arquivando metricas do dia
python arquivar.py || echo AVISO: arquivamento falhou, briefing segue sem memoria atualizada

call claude -p "Leia todos os arquivos JSON na pasta dados/ e tambem o historico em historico/metricas.jsonl (uma linha por dia, dias anteriores). Escreva o arquivo relatorio.md com meu briefing diario em portugues, nesta estrutura. Em TODA secao, diga explicitamente de qual fonte vem o numero: o help desk e o Milldesk, o e-mail e a caixa IMAP, as licencas sao o sistema interno de licencas. 1) 'Atencao hoje' - apenas itens urgentes: chamados abertos ha muito tempo (campo fila.idade do helpdesk.json: mais_de_90_dias e de_31_a_90_dias), prioridade/urgencia alta, status Rejeitado, licencas de producao vencidas ou vencendo em menos de 7 dias, ou qualquer coisa anomala. NAO existe informacao de SLA neste sistema: o prazo nao estava definido corretamente na origem e foi removido. Nunca cite SLA, prazo de atendimento ou chamado atrasado em relacao a prazo - a medida de urgencia aqui e ha quantos dias o chamado esta aberto. Se nao houver nada urgente, diga isso em uma linha. Alem disso, a data/hora atual e %date% %time%: compare o campo coletado_em de cada JSON de dados/ com ela e, se algum tiver mais de 24 horas ou o campo estiver ausente, liste nesta mesma secao como 'FONTE DESATUALIZADA: <nome> (ultima coleta: <data>)'. 2) 'Chamados por sistema (Milldesk)' - use fila.por_sistema do helpdesk.json. Diga quantos chamados em aberto existem no Site e no Siscam 9, nessa ordem, e compare com fila_site e fila_siscam9 da linha do dia anterior do historico. Para cada um desses dois sistemas, use tickets_por_sistema para citar ate 3 chamados mais antigos por id e assunto (maior dias_aberto primeiro). ATENCAO: tickets_por_sistema e uma AMOSTRA dos abertos ha mais tempo, nao a lista completa - o total de cada sistema esta em fila.por_sistema e os agregados em fila.por_sistema_resumo (abertos, acima_de_90_dias, mais_antigo_dias). Nunca conte chamados percorrendo a amostra: use os agregados. Pode ignorar o Siscam 8 nesta secao. Se fila.por_sistema nao existir no JSON, diga em uma linha que o recorte por sistema ainda nao foi coletado e pule. 3) 'Produtividade do suporte (Milldesk)' - use atendimentos_ultimo_dia_util do helpdesk.json: dia, total fechados e quebra por tecnico em uma linha. COMPARE com o historico: informe a media de atend_total dos ultimos 10 dias uteis registrados e diga se o dia ficou acima, abaixo ou na media. Se houver tickets_sem_tecnico_na_descricao ou do_solicitante_mas_nao_fechados maior que zero, avise em uma linha citando os IDs. 4) 'Desenvolvimento (Milldesk)' - use o bloco desenvolvimento do helpdesk.json. Diga total_atribuidos_a_devs e total_em_status_dev, e o que isso significa: o primeiro sao chamados que tem um desenvolvedor como responsavel, o segundo sao chamados parados em status de desenvolvimento, com dono ou sem. Diga tambem que fracao da fila total isso representa. Liste por_dev em uma linha (nome e quantidade de abertos, do maior para o menor) e cite em qual sistema esta a maior carga usando em_status_dev_por_sistema. Use em_status_dev_por_natureza para dizer quanto do que esta com o desenvolvimento e Corretivo (bug, falha, lentidao) e quanto e Evolutivo (melhoria, nova funcao). Se o bloco nao existir ou se configurado for false, diga em uma linha que HELPDESK_DEV_NAMES nao esta configurado e pule. 4b) 'Natureza da fila (Milldesk)' - use fila.por_natureza: quantos chamados sao Corretivo e quantos sao Evolutivo, e compare com fila_corretivo e fila_evolutivo da linha do dia anterior do historico. Em uma linha, diga o que a proporcao sugere: fila muito corretiva significa tempo gasto apagando incendio em vez de construir. Se o campo nao existir, pule sem comentar. 5) 'Tendencias (Milldesk e licencas)' - compare os numeros de hoje com a linha do dia anterior no historico e diga sempre de onde vem cada um: fila_abertos (fila total do Milldesk) subiu ou desceu, meus_abertos (que e a soma dos chamados dos tecnicos monitorados, nao apenas os meus) mudou, sla_expirado (chamados do Milldesk com SLA vencido) mudou, licencas vencidas persistem ha quantos dias. Maximo 4 linhas, so o que for relevante; se o historico tiver menos de 2 dias, pule esta secao sem comentar. 6) 'Resumo por fonte' - 1 a 3 linhas por fonte, nomeando a fonte (Milldesk, caixa IMAP, sistema de licencas) com os numeros principais. Se uma fonte falhou, mencione em uma linha sem alarde. 7) 'Sugestao de prioridade' - em 2 linhas, o que voce atacaria primeiro. Seja direto. Nao invente dados que nao estao nos arquivos." --allowedTools "Read,Write,Edit"

echo === Relatorio gerado: relatorio.md ===

echo --- gerando dashboard
python gerar_dashboard.py || echo AVISO: dashboard falhou
python notificar.py || echo AVISO: notificacao falhou

echo --- briefing da diretoria
call briefing_diretor.bat sem-abrir || echo AVISO: briefing da diretoria falhou

echo --- briefing do financeiro
call briefing_financeiro.bat sem-abrir || echo AVISO: briefing do financeiro falhou

REM Abre as tres no fim, para nao interromper o pipeline no meio.
REM As chamadas acima usam "sem-abrir" so para nao abrir a mesma pagina duas vezes.
echo --- abrindo as tres paginas
if exist dashboard.html start "" dashboard.html
if exist dashboard_diretor.html start "" dashboard_diretor.html
if exist dashboard_financeiro.html start "" dashboard_financeiro.html

echo === Fim: dashboard.html, dashboard_diretor.html, dashboard_financeiro.html ===
