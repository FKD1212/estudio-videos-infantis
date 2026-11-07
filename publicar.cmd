@echo off
rem Producao automatica: metricas, roteiro e producao dos episodios da fila, revisao, aprovacao,
rem envio e agendamento. Roda pelo Agendador de Tarefas do Windows todo dia (veja o README); o
rem resultado vai para dados\publicacao.log e pendencias aparecem como aviso do Windows.
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist dados mkdir dados
echo ===== %date% %time% >> "dados\publicacao.log"
".venv\Scripts\python.exe" -u -m estudio automatico >> "dados\publicacao.log" 2>&1
