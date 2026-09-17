@echo off
REM Briefing do financeiro - NAO roda coletores: usa os JSONs ja coletados por briefing.bat.
REM Fonte unica de negocio: o sistema interno de licencas. Sem dado de help desk.
REM Argumento "sem-abrir": nao abre o navegador (usado quando chamado por briefing.bat).
cd /d "%~dp0"
call .venv\Scripts\activate.bat

echo === Briefing financeiro %date% %time% ===

call claude -p "Leia APENAS dados/licencas.json, historico/licencas.jsonl e historico/metricas.jsonl. NAO leia dados/helpdesk.json nem dados/email.json e nao cite nenhum dado de chamados. NUNCA cite nome de tecnico, de desenvolvedor ou de qualquer integrante da equipe - esta e uma regra de privacidade desta pagina. Escreva o arquivo relatorio_financeiro.md em portugues, para o financeiro, que cuida da renovacao das licencas. A fonte e o sistema interno de licencas e ela informa apenas cliente, sistema e data de vencimento: nao existe valor, contrato nem responsavel, entao nao fale de dinheiro. Estrutura: 1) 'Acao imediata' - licencas de producao ja vencidas e as que vencem em ate 7 dias, por cliente e sistema, da mais atrasada para a menos. Se nao houver nenhuma, diga isso em uma linha. 2) 'Vencendo no mes' - quantas licencas vencem nos proximos 30 dias e em quais sistemas se concentram, usando vencendo_em_breve e o campo dias de cada item. 3) 'O que mudou' - compare as DUAS ultimas linhas de historico/licencas.jsonl, que sao retratos diarios com a lista itens (cada item tem cliente, sistema, vencimento, dias e estado). Diga: quantas renovaram (mesma dupla cliente+sistema com vencimento mais para a frente), quantas venceram no periodo (estado passou de vencendo para vencida), quantas entraram na lista e quantas sairam. Cite ate 5 clientes por categoria. Ao falar das que sairam, deixe claro que a fonte nao diz se foi renovacao longa ou remocao no sistema. Se houver menos de duas linhas no arquivo, diga que a comparacao comeca quando existirem dois retratos e pule a secao. 4) 'Panorama' - totais: vencidas recentes, vencendo em breve, vencidas ha mais tempo (vencidas_antigas_total) e quantas foram ignoradas por serem homologacao ou teste (ignoradas_homolog_teste). Explique em uma linha que homologacao e teste nao entram em nenhuma conta desta pagina. 5) 'Tendencia' - usando lic_vencendo e lic_vencidas_recentes dos ultimos 10 dias de historico/metricas.jsonl, diga se o numero de vencidas esta subindo, caindo ou estavel. Duas linhas no maximo. Se houver menos de 3 dias, pule sem comentar. Nao invente dados que nao estao nos arquivos." --allowedTools "Read,Write,Edit"

echo === Relatorio gerado: relatorio_financeiro.md ===

python gerar_dashboard_financeiro.py || echo AVISO: dashboard do financeiro falhou
if "%1"=="" if exist dashboard_financeiro.html start "" dashboard_financeiro.html
