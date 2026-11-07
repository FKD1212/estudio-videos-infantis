@echo off
rem Producao continua: roteiro, imagens, narracao, sons, trilha, montagem, revisao e aprovacao, um
rem episodio atras do outro. Roda pelo Agendador de Tarefas ao entrar no Windows (veja o README) e
rem volta sozinho se cair. Os envios NAO sao daqui: ficam com a tarefa diaria (publicar.cmd).
rem Pausar: .venv\Scripts\python.exe -m estudio pausar   (retomar: ... -m estudio retomar)
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist dados mkdir dados
:inicio
rem log maior que 20 MB vira produtor.log.1 (so o anterior fica guardado)
for %%A in ("dados\produtor.log") do if %%~zA GTR 20000000 move /y "dados\produtor.log" "dados\produtor.log.1" >nul
echo ===== %date% %time% >> "dados\produtor.log"
".venv\Scripts\python.exe" -u -m estudio produtor >> "dados\produtor.log" 2>&1
rem saiu (erro, falta de memoria ou outro produtor ja rodando): espera 1 minuto e volta
timeout /t 60 /nobreak >nul
goto inicio
