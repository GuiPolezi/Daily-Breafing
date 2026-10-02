@echo off
REM Uma rodada do sistema novo (coleta + exportacao + publicacao do site).
REM Chamado pela tarefa agendada a cada 5 minutos. Quem decide o que fazer em
REM cada rodada e o agendada.py. Tudo que ele imprime vai para dados\coleta.log.
cd /d "%~dp0"
if not exist dados mkdir dados
set PY=python
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe
set PYTHONIOENCODING=utf-8
REM log nao cresce para sempre: passou de ~2 MB, vira coleta.log.1
if exist dados\coleta.log for %%A in (dados\coleta.log) do if %%~zA GTR 2000000 move /y dados\coleta.log dados\coleta.log.1 >nul
%PY% agendada.py %* >> dados\coleta.log 2>&1
