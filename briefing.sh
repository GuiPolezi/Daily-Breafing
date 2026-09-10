#!/usr/bin/env bash
# Briefing diário: coleta dados e pede ao Claude Code para sintetizar.
# Agende no cron: 30 7 * * 1-5 cd /caminho/briefing-diario && ./briefing.sh
set -uo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate

echo "=== Briefing $(date '+%d/%m/%Y %H:%M') ==="

# 1) Coletores (determinísticos). Um falhar não derruba os outros.
for coletor in coletores/check_*.py; do
    echo "--- rodando $coletor"
    python "$coletor" || echo "AVISO: $coletor falhou (seguindo sem essa fonte)"
done

# 2) Síntese com Claude Code em modo headless (-p = print/não-interativo).
#    Aqui está a parte "agente": ele lê os JSONs, interpreta e prioriza.
claude -p "Leia todos os arquivos JSON na pasta dados/ e escreva o arquivo \
relatorio.md com meu briefing diário em português, nesta estrutura: \
1) '⚠️ Atenção hoje' — apenas itens urgentes: licenças vencidas ou vencendo \
em menos de 7 dias, tickets de prioridade alta, qualquer coisa anômala. \
Se não houver nada urgente, diga isso em uma linha. \
2) 'Resumo por fonte' — 1 a 3 linhas por fonte (e-mail, licenças, help desk, \
whatsapp), com os números principais. Se uma fonte falhou ou não está \
disponível, mencione em uma linha sem alarde. \
3) 'Sugestão de prioridade' — em 2 linhas, o que você atacaria primeiro. \
Seja direto, sem enrolação. Não invente dados que não estão nos JSONs." \
  --allowedTools "Read,Write"

echo "=== Relatório gerado: relatorio.md ==="

# 3) (Opcional) Entregar o relatório para você. Exemplos:
# cat relatorio.md | mail -s "Briefing diário" voce@empresa.com
# curl -s -X POST "https://api.telegram.org/bot$TG_TOKEN/sendMessage" \
#      -d chat_id="$TG_CHAT" --data-urlencode "text=$(cat relatorio.md)"
