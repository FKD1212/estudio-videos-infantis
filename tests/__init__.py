import os

# Os testes nunca chamam serviços de verdade: nem o modelo local (o Ollama levaria minutos), nem o
# Freesound, nem avisos do Windows. Definidas antes do .env ser lido, estas variáveis não são
# sobrescritas por ele. O teste que cuida da reserva local liga o modelo local de propósito.
os.environ.setdefault("SEM_MODELO_LOCAL", "1")
os.environ["FREESOUND_API_KEY"] = ""
os.environ["SEM_AVISOS"] = "1"
