@echo off
REM Briefing diario - roda coletores e sintetiza com Claude Code
cd /d "%~dp0"
call .venv\Scripts\activate.bat

echo === Briefing %date% %time% ===

for %%f in (coletores\check_*.py) do (
    echo --- rodando %%f
    python "%%f" || echo AVISO: %%f falhou, seguindo sem essa fonte
)

call claude -p "Leia todos os arquivos JSON na pasta dados/ e escreva o arquivo relatorio.md com meu briefing diario em portugues, nesta estrutura: 1) 'Atencao hoje' - apenas itens urgentes: chamados com SLA expirando ou expirado, prioridade/urgencia alta, status Rejeitado, ou qualquer coisa anomala. Se nao houver nada urgente, diga isso em uma linha. 2) 'Resumo por fonte' - 1 a 3 linhas por fonte com os numeros principais. Se uma fonte falhou ou nao esta disponivel, mencione em uma linha sem alarde. 3) 'Sugestao de prioridade' - em 2 linhas, o que voce atacaria primeiro. Seja direto. Nao invente dados que nao estao nos JSONs." --allowedTools "Read,Write"

echo === Relatorio gerado: relatorio.md ===