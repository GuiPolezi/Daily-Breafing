@echo off
REM Cria a tarefa agendada do sistema novo: rodar_coleta.bat a cada 5 minutos.
REM Rode UMA vez, no servidor, com o usuario que vai executar a tarefa.
REM O Windows pede a senha desse usuario: ela e necessaria para a tarefa rodar
REM mesmo sem ninguem logado. Rodar de novo substitui a tarefa existente.
REM O padrao do Agendador para tarefa criada assim ja e "nao iniciar nova
REM instancia se a anterior ainda estiver em execucao".
cd /d "%~dp0"
schtasks /create /tn "BriefingColeta" /tr "\"%~dp0rodar_coleta.bat\"" /sc minute /mo 5 /ru "%USERDOMAIN%\%USERNAME%" /rp * /f
if errorlevel 1 (
    echo ERRO: a tarefa nao foi criada. Abra o Prompt de Comando como administrador e tente de novo.
    exit /b 1
)
echo Tarefa "BriefingColeta" criada. Acompanhe em dados\coleta.log
