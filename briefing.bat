@echo off
REM Briefing diario - roda coletores, arquiva metricas e sintetiza com Claude Code
cd /d "%~dp0"
call .venv\Scripts\activate.bat

echo === Briefing %date% %time% ===

for %%f in (coletores\check_*.py) do (
    echo --- rodando %%f
    python "%%f" || echo AVISO: %%f falhou, seguindo sem essa fonte
)

echo --- arquivando metricas do dia
python arquivar.py || echo AVISO: arquivamento falhou, briefing segue sem memoria atualizada

call claude -p "Leia todos os arquivos JSON na pasta dados/ e tambem o historico em historico/metricas.jsonl (uma linha por dia, dias anteriores). Escreva o arquivo relatorio.md com meu briefing diario em portugues, nesta estrutura: 1) 'Atencao hoje' - apenas itens urgentes: chamados com SLA expirando ou expirado, prioridade/urgencia alta, status Rejeitado, licencas de producao vencidas ou vencendo em menos de 7 dias, ou qualquer coisa anomala. Se nao houver nada urgente, diga isso em uma linha. 2) 'Produtividade do suporte' - use atendimentos_ultimo_dia_util do helpdesk.json: dia, total fechados e quebra por tecnico em uma linha. COMPARE com o historico: informe a media de atend_total dos ultimos 10 dias uteis registrados e diga se o dia ficou acima, abaixo ou na media. Se houver tickets_sem_tecnico_na_descricao ou do_solicitante_mas_nao_fechados maior que zero, avise em uma linha citando os IDs. 3) 'Tendencias' - compare os numeros de hoje com a linha do dia anterior no historico: fila de chamados subiu ou desceu, meus_abertos mudou, licencas vencidas persistem ha quantos dias. Maximo 3 linhas, so o que for relevante; se o historico tiver menos de 2 dias, pule esta secao sem comentar. 4) 'Resumo por fonte' - 1 a 3 linhas por fonte com os numeros principais. Se uma fonte falhou, mencione em uma linha sem alarde. 5) 'Sugestao de prioridade' - em 2 linhas, o que voce atacaria primeiro. Seja direto. Nao invente dados que nao estao nos arquivos." --allowedTools "Read,Write"

echo === Relatorio gerado: relatorio.md ===