@echo off
REM Remove a tarefa agendada do sistema novo. Nao apaga dados nem o site publicado.
schtasks /delete /tn "BriefingColeta" /f
