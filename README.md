# Estúdio de vídeos infantis

Do nicho ao vídeo pronto para publicar, sem pagar nada por vídeo:

1. **Pesquisa:** compara nichos infantis em português e inglês e encontra os vídeos que superam
   muito o normal do próprio canal (outliers).
2. **Estrutura:** o Gemini assiste aos outliers e extrai o que prende a atenção: gancho, ritmo,
   repetição e interação.
3. **Roteiro:** episódio original com os personagens do seu canal, usando essas lições.
4. **Produção:** narração, imagens com personagens consistentes, trilha original composta por
   IA, vídeo com movimento de câmera, legendas e miniatura.
5. **Publicação:** revisão das regras de monetização, sua aprovação e envio automático, já
   agendado e marcado como conteúdo para crianças.
6. **Métricas:** retenção real dos seus vídeos, que decide o estilo das próximas trilhas.

## Quanto custa

| Etapa | Ferramenta | Custo | Limite | Quando a cota acaba |
|---|---|---|---|---|
| Pesquisa | YouTube Data API | grátis | 10.000 unidades/dia (~3.400 por pesquisa) | espera o dia seguinte |
| Estrutura e estilos | Gemini (assiste ao vídeo pelo link) | grátis | ver abaixo | espera o dia seguinte |
| Roteiro e revisão | Gemini 3.8 → 3.6 → 3.5 Flash-Lite | grátis | ver abaixo | Gemma 4 no Ollama, local |
| Narração | Gemini 3.8 Flash TTS | grátis | uma chamada por cena | Kokoro, voz local |
| Imagens | FLUX.2 [klein] 4B na sua placa | grátis | nenhum (~15-35 s por imagem) | — |
| Trilha | ACE-Step 1.5 na sua placa | grátis | nenhum | o CLAP local ouve no lugar do Gemini |
| Sons de animais | Freesound (só CC0) | grátis | chave gratuita | — |
| Montagem | ffmpeg | grátis | nenhum | — |
| Envio e métricas | YouTube Data e Analytics API | grátis | 100 vídeos/dia | — |

**A cota grátis do Gemini é pequena:** em out/2026, 20 pedidos por dia no gemini-3.8-flash.
Quando ela acaba, o programa passa sozinho para o 3.6 Flash e depois para o 3.5 Flash-Lite, que
têm cota própria, e por fim para os modelos locais da tabela. Nada é pago. Os limites da sua conta
aparecem em [ai.dev/rate-limit](https://ai.dev/rate-limit), e a cota reinicia à meia-noite do
Pacífico (4h ou 5h em Brasília). O que fica pronto é salvo, e cada etapa continua de onde parou.

**Quando começar a pagar:** o primeiro gargalo será a cota de narração, quando você quiser
publicar mais de um episódio por dia. Para passar ao plano pago, basta ativar o faturamento no
AI Studio; o código não muda. Pelos preços de outubro/2026, um episódio de 4 minutos sairia por
volta de US$ 0,10 no plano pago (roteiro + narração, estimativa). As imagens continuam de graça
na sua placa. Um detalhe: no plano gratuito, o Google pode usar o conteúdo enviado para
melhorar os produtos dele; no pago, não.

## Configuração (uma vez)

1. **Chave da YouTube API:** em [console.cloud.google.com](https://console.cloud.google.com),
   crie um projeto, ative a **YouTube Data API v3** e gere uma **Chave de API** em Credenciais.
2. **Chave do Gemini:** em [aistudio.google.com](https://aistudio.google.com), clique em
   *Get API key*.
3. Copie `.env.example` para `.env` e cole as duas chaves.
4. Ambiente Python (já criado em `.venv`; para recriar):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
5. **Imagens na placa de vídeo** (já instalado nesta máquina; para recriar):
   ```powershell
   .\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu130
   .\.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
   ```
   Na primeira geração o modelo é baixado do Hugging Face (~16 GB, em
   `C:\Users\<você>\.cache\huggingface`).
6. **Música por IA** (já instalado nesta máquina; para recriar):
   ```powershell
   git clone https://github.com/ace-step/ACE-Step-1.5 ferramentas\ACE-Step-1.5
   cd ferramentas\ACE-Step-1.5
   ..\..\.venv\Scripts\uv.exe sync --python 3.12
   ```
   O ACE-Step roda num ambiente próprio (Python 3.12) dentro de `ferramentas\`, e o modelo
   (~13 GB) fica em `ferramentas\ACE-Step-1.5\checkpoints`. Se preferir músicas prontas, mude
   `fonte = "biblioteca"` em `[musica]` no `canal.toml` e coloque faixas da
   [YouTube Audio Library](https://studio.youtube.com) em `musica/alegre`, `musica/calma` ou
   `musica/aventura`, registrando cada uma em `musica/licencas.toml`.
7. **Seu canal:** edite `canal.toml` com o nome, o público, o estilo visual, a voz e os
   personagens fixos. Esse é o seu universo, e é ele que diferencia o canal.
8. **Sons de animais:** crie uma conta gratuita em [freesound.org](https://freesound.org), peça
   uma chave em [freesound.org/apiv2/apply](https://freesound.org/apiv2/apply) e cole-a em
   `FREESOUND_API_KEY` no `.env`. O programa só usa sons com licença CC0, e o ouvido local
   confere se o som é mesmo do animal antes de usar. Sem a chave, as cenas com som saem só com
   a narração.
9. **Reserva local** (já instalada nesta máquina), para quando a cota grátis do Gemini acaba:
   - **Texto e revisão:** [Ollama](https://ollama.com) com `gemma4:26b` (`ollama pull gemma4:26b`).
   - **Voz:** Kokoro, num ambiente próprio em `ferramentas\kokoro`. Para recriar:
     ```powershell
     .\.venv\Scripts\uv.exe venv ferramentas\kokoro\.venv --python 3.12
     .\.venv\Scripts\uv.exe pip install --python ferramentas\kokoro\.venv\Scripts\python.exe torch==2.7.1 --index-url https://download.pytorch.org/whl/cu128
     .\.venv\Scripts\uv.exe pip install --python ferramentas\kokoro\.venv\Scripts\python.exe kokoro soundfile "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
     ```
   - **Ouvido (CLAP):** já vem com os pacotes de GPU; o modelo é baixado no primeiro uso.

## Como rodar

Todos os comandos rodam no **terminal do PowerShell, dentro da pasta do projeto**. No VS Code,
abra *Terminal > Novo Terminal* (ou Ctrl+'): ele já abre na pasta certa. Fora do VS Code, abra o
PowerShell e entre na pasta antes: `cd "D:\Felipe Pasta\Pasta projeto"`.

**Uma vez:** a configuração acima, a de "Upload automático" mais abaixo e a pesquisa:

```powershell
.\.venv\Scripts\python.exe -m estudio conectar     # autoriza o canal (abre o navegador)
.\.venv\Scripts\python.exe -m estudio pesquisar    # ranking de nichos
.\.venv\Scripts\python.exe -m estudio estrutura    # o que prende a atenção nos outliers
.\.venv\Scripts\python.exe -m estudio estilos      # estilos musicais dos outliers
```

**Depois disso, nenhum comando é necessário.** Duas tarefas do Agendador do Windows fazem tudo:

| Tarefa | Quando | O que faz | Registro |
|---|---|---|---|
| **Estudio - produtor** (`produtor.cmd`) | desde que você entra no Windows | produz sem pausa até cobrir os posts da semana, e para | `dados\produtor.log` |
| **Estudio - publicar** (`publicar.cmd`) | todo dia às 8h (ou assim que o computador ligar) | métricas e envios nos horários programados | `dados\publicacao.log` |

**Produção por semana** (`[automacao] continua = true`). O produtor trabalha sem pausa até ter
um episódio pronto (ou em produção) para cada horário de `[publicacao]` até o fim **desta semana e
da seguinte** (`semanas_adiantadas = 1`). Aí para, avisa no Windows ("semana pronta") e tira o
modelo de imagem da memória. Na segunda-feira seguinte, quando entra uma semana nova sem episódios,
ele volta sozinho e produz os posts dela. Com a grade atual, são 3 episódios por semana, feitos
em pouco mais de 1 hora, sempre com uma semana de folga para o caso de o computador ficar
desligado. Se a revisão bloquear um episódio ou ele falhar, um substituto é produzido. Se você
aumentar `max_por_semana` ou os `horarios`, a produção volta em até 5 minutos para cobrir as vagas
novas. O comando `agenda` mostra em que pé está.

Enquanto produz, são três linhas de trabalho ao mesmo tempo, para nenhuma parte do computador
ficar parada:

- **roteiro:** começa um episódio novo só enquanto faltar episódio para a semana, com até
  `roteiros_adiantados` (2) roteiros à frente (Gemini; quando a cota acaba, o Gemma 4 local);
- **placa de vídeo:** imagens, narração, sons e trilha, um episódio depois do outro. A placa
  (12 GB) atende uma etapa por vez, então uma trava (`.cache\gpu.lock`) dá a vez entre as linhas e
  entre processos;
- **montagem (processador):** monta o vídeo com vários encodes em paralelo, revisa, aprova e
  apaga os intermediários (clipes, quadros e versões descartadas da trilha), mantendo imagens,
  narração, trilha e vídeo.

Enquanto a montagem de um episódio usa o processador, a placa já trabalha no próximo. A tarefa
roda com prioridade "abaixo do normal": usa tudo o que estiver ocioso, mas cede a vez ao que você
estiver fazendo no computador. Se cair, volta sozinha em 1 minuto e continua do ponto em que o
episódio estava. Quando o código do estúdio muda, o produtor termina as etapas em andamento e
reinicia com a versão nova. Um episódio que falha 3 vezes seguidas sai da fila (com aviso) até o roteiro
mudar ou o `etapas.json` dele ser apagado. Com menos de `espaco_minimo_gb` (20 GB) livres no disco,
a produção espera e avisa.

```powershell
.\.venv\Scripts\python.exe -m estudio pausar     # termina a etapa atual e para (os envios continuam)
.\.venv\Scripts\python.exe -m estudio retomar
```

**Envio programado.** A tarefa diária busca as métricas e sobe os aprovados que vão ao ar dentro de
`antecedencia_dias` (14), agendados nos horários de `[publicacao]`. O que passar disso fica no
estoque (`agenda` mostra quantos), pronto e aprovado, esperando a vez. Para publicar mais, aumente
`max_por_semana` e os `horarios`: a produção acompanha a grade.

Com `continua = false`, a tarefa diária volta a produzir sozinha, um episódio por dia
(`novos_por_execucao`) enquanto a fila estiver abaixo de `manter_agendados`.

Cada etapa é independente: cota esgotada ou queda de rede numa não impede as outras.
**Quando algo só você resolve** (reconectar o canal a cada 7 dias, um episódio bloqueado pela
revisão, um erro que se repete, disco cheio), aparece um **aviso do Windows**.

Com `aprovacao = "automatica"` em `[publicacao]`, um episódio sobe se a revisão (regras + Gemini)
não tiver **bloqueio**; avisos ficam em `aprovacao.json`, e as métricas fazem o papel da revisão
depois de publicado.

Quando a revisão da IA aponta defeito numa imagem específica (como "imagem 4.1": deformação,
personagem em dobro, pista que entrega a resposta), o vídeo não fica esperando você. O produtor
troca a semente só daquela cena, refaz as imagens dela, monta e revisa de novo, até 2 vezes
(`correcoes_automaticas` no `roteiro.json`). Depois disso, vale a revisão: com só avisos, o vídeo é
aprovado; com bloqueio, ele espera você e aparece um aviso do Windows. Com `aprovacao = "humana"`, nada sobe sem `revisar` e `aprovar`.

Para fazer um episódio com uma ideia sua, fora da fila automática:

```powershell
.\.venv\Scripts\python.exe -m estudio roteiro --nicho animais --idioma en --tema "sua ideia"
.\.venv\Scripts\python.exe -m estudio produzir
```

Para acompanhar: `episodios`, `agenda` e `metricas`.

## Canal infantil, hashtags e playlists

A seção `[youtube]` do `canal.toml` define como o canal se apresenta; a tarefa diária aplica o que
mudar:

- **Audiência "conteúdo para crianças"** no canal e em cada vídeo. Segundo o YouTube, vídeos
  marcados assim tendem a ser recomendados junto de outros vídeos infantis. Em troca, ficam sem
  comentários, notificações e anúncios personalizados. Se a API não aceitar a marcação do canal,
  o log explica como fazer no Studio (*Configurações > Canal > Configurações avançadas > Público*).
- **Descrição do canal, palavras-chave e idioma**, em inglês.
- **Playlist da série** (`[youtube.playlists]`): cada vídeo entra sozinho na playlist do seu nicho,
  o que encadeia um episódio no outro.
- **Hashtags:** cada episódio ganha de 3 a 5 hashtags do roteiro mais as da série
  (`hashtags = ["#LuluAndBenny"]`). Elas vão na última linha da descrição, e as 3 primeiras aparecem
  acima do título. O máximo é 15 por vídeo, porque acima disso o YouTube ignora todas. Elas passam
  pelas mesmas checagens do título: marca de terceiros (até `#PawPatrol`) e promessa de saúde
  bloqueiam. Se você mudar título, descrição, tags ou hashtags de um episódio já enviado, a tarefa
  atualiza o vídeo no YouTube sem reenviá-lo.

## Histórias leves e engraçadas

Todo roteiro, de episódio ou de Short, segue o tom de `[roteiro]` no `canal.toml`: leve e
bem-humorado, com surpresas bobas, mal-entendidos gentis, sons engraçados, exageros, um bordão que
se repete três vezes com uma virada na terceira e a dupla cômica Lulu (curiosa) e Benny (calmo e
sabido). Todos riem juntos: ninguém ri de ninguém, e não há dor, susto ou zombaria.

Cada episódio novo parte de um **conto tradicional de domínio público**, um diferente por vez
(lista em `estudio/contos.py`). Muitos são histórias de animais que vão se juntando, e por isso
combinam com o formato de sons de animais. Alguns exemplos:

- O Nabo Gigante;
- Os Músicos de Bremen;
- Henny Penny;
- A Galinha Ruiva;
- fábulas de Esopo (a tartaruga e a lebre, o leão e o ratinho, o vento e o sol...);
- Edward Lear e Kipling.

Só entram a ideia e o jeito de fazer rir. Falas, nomes e cenas são novos, e nada vem de versões
modernas (filmes, livros ilustrados, músicas). Cada conto traz o ajuste obrigatório para crianças
pequenas: por exemplo, em Henny Penny não há raposa, e na corrida a Lulu se distrai em vez de ser
convencida. O conto usado fica em `inspiracao`, no `roteiro.json`, e a revisão confere o ajuste.
Um `--tema` seu vale mais que o conto.

**Por que não histórias do Reddit (r/stories) ou de outros sites:**

- **Termos do Reddit:** uso comercial dos dados exige aprovação escrita e contrato com o Reddit.
- **Monetização:** ler histórias escritas por outros é "conteúdo reutilizado", que o YouTube não
  monetiza.
- **Direitos autorais:** os textos são dos autores.
- **Público:** quase todas são relatos de adultos, impróprios para crianças de 2 a 5 anos.

## Shorts

Cada episódio rende até `por_episodio` (3) Shorts verticais de cerca de 40 s, um por dia às 17h
(seção `[shorts]` do `canal.toml`). Cada Short pega **um animal** do episódio e conta de um jeito
próprio, num destes formatos:

- **pergunta:** o som real do bicho toca logo no primeiro segundo, sobre uma pista (sombra ou
  rabinho); a narradora pergunta quem é, faz uma pausa para a criança responder e vem a revelação;
- **curiosidade:** abre com um fato verdadeiro e surpreendente sobre o bicho ("Did you know...?")
  e mostra mais dois fatos simples. A revisão confere se os fatos são verdadeiros;
- **musica:** uma canção original de uns 30 s para cantar junto, com o som do bicho no refrão. A
  letra é do Gemini e quem canta é o ACE-Step. Cada versão é ouvida pelo Gemini, que reprova voz
  embolada, letra diferente da pedida ou melodia parecida com uma música conhecida.

**O formato é escolhido pelo engajamento.** Para cada Short publicado há 7 dias ou mais, com 30
views ou mais, o comando `metricas` calcula um engajamento relativo à mediana dos Shorts do canal
(1,00 = típico):

- metade é retenção: a porcentagem média assistida, que no Short passa de 100% quando ele é revisto;
- metade é interação por view: curtida vale 1, compartilhamento 2 e inscrição 3.

Cada Short novo sorteia o formato pela chance de cada um ser o melhor, como nas trilhas. O que
engaja mais passa a sair na maioria das vezes, e os outros continuam sendo testados.

**Feitos na vertical:**

- **Imagens:** novas, em 9:16 (1080x1920), com os mesmos personagens do episódio. As imagens de
  referência deles são reaproveitadas, então não são geradas de novo.
- **Som:** o som real do animal.
- **Música:** a trilha do próprio episódio como fundo, ou a canção no formato música.
- **Mesma revisão e aprovação automática dos episódios:** a checagem de repetição compara um Short
  com os outros Shorts. O roteirista recebe as aberturas dos Shorts anteriores para começar de um
  jeito diferente.

**Chamada para o episódio completo.** A última cena de todo Short tem uma frase narrada, convidando
a ver a história inteira, sobre um cartão com a miniatura do episódio e um botão de play. A frase
varia de um Short para outro e não pede inscrição nem curtida. A descrição traz
"▶ Watch the full episode: <link>", e a hashtag `#Shorts` fica entre as 3 que aparecem acima do
título. Um Short só vai ao ar depois do episódio de onde saiu, para o link funcionar.

Conteúdo para crianças não tem cards nem tela final, e a API do YouTube não define o "vídeo
relacionado" do Short, que é o link clicável sobre o título. Se quiser, coloque esse link à mão no
Studio (*Detalhes > Vídeo relacionado*). Não descobri se ele aparece em Shorts para crianças.

**Produção.** O mesmo produtor da semana faz os Shorts depois dos episódios e para quando os
horários de Shorts até o fim da semana seguinte estiverem cobertos. Os Shorts saem dos episódios
prontos, do mais antigo para o mais novo. A tarefa diária envia os Shorts junto com os episódios.
Para fazer um Short agora:

```powershell
.\.venv\Scripts\python.exe -m estudio short --episodio "parte do nome" --formato pergunta --animal duck
```

Sem `--formato` e `--animal`, vale a escolha automática. Para 2 Shorts por dia, use 14 `horarios`,
`max_por_semana = 14` e `por_episodio = 5`.

**Dinheiro:** a partir de 1/2/2027, a receita de Shorts só é paga a canais com 10 milhões de
views em Shorts em 90 dias. Até lá, os Shorts servem para levar crianças e pais aos episódios
longos, que é onde está a receita.

## Uso

### Pesquisa (fases 1 e 2)

```powershell
.\.venv\Scripts\python.exe -m estudio pesquisar                        # ~3.500 unidades de cota
.\.venv\Scripts\python.exe -m estudio estrutura --top 3                # 3 maiores outliers por nicho/idioma
.\.venv\Scripts\python.exe -m estudio estrutura --nicho historias --top 8   # aprofundar num nicho
.\.venv\Scripts\python.exe -m estudio relatorio                        # regerar o relatório
.\.venv\Scripts\python.exe -m estudio estilos --top 2                  # estilo musical dos outliers
```

Resultados em `dados/`: `relatorio.md` (ranking de nichos, vídeos que se destacam e estruturas),
`nichos.csv` e `videos.csv` (abrem no Excel) e `estruturas/<id>.json`. Os nichos e termos de
busca ficam em `nichos.toml`; para testar outro nicho sem refazer tudo, use `pesquisar --somente <nome>`.

### Um episódio (fases 3 e 4)

```powershell
# 1. Roteiro: o tema é opcional, mas a ideia ser sua deixa o canal mais autêntico
.\.venv\Scripts\python.exe -m estudio roteiro --nicho historias --idioma pt --tema "Lulu aprende a esperar a vez"

# 2. Revise dados\episodios\<episódio>\roteiro.json e edite à vontade

# 3. Imagens + narração + trilha + montagem (refaz só o que mudou no roteiro)
.\.venv\Scripts\python.exe -m estudio produzir

# Etapas avulsas e acompanhamento
.\.venv\Scripts\python.exe -m estudio imagens      # depois confira revisao_imagens.jpg
.\.venv\Scripts\python.exe -m estudio narrar
.\.venv\Scripts\python.exe -m estudio trilha       # versões e notas em musica\trilha.json
.\.venv\Scripts\python.exe -m estudio montar
.\.venv\Scripts\python.exe -m estudio episodios    # o que falta em cada episódio
```

Sem nome, os comandos usam o último episódio criado. Para outro, passe o nome ou parte dele:
`produzir lulu-aprende`.

Cada episódio fica em `dados/episodios/<data>-<nicho>-<idioma>-<título>/`:

- `roteiro.json`: história, cenas, miniatura e título, descrição e tags para o YouTube. Cada
  cena tem a fala (`narracao`), de 1 a 3 imagens (`imagens`), que se revezam enquanto ela fala,
  porque os vídeos que se destacam trocam de imagem a cada 2-4 s, o texto na tela e, se for o
  caso, o animal cujo som real toca 2 vezes depois da fala (`som`).
- `revisao_imagens.jpg`: todas as imagens numeradas como cena.imagem (3.2 = cena 3, 2ª imagem).
- `video.mp4`, `miniatura.jpg`, `legendas.srt` e `publicar.txt`, com título, descrição, tags e
  checklist do YouTube Studio.

**Ajustes comuns no roteiro.json:**

- **Imagem ruim:** mude a `"semente"` da cena (0 → 1) e rode `produzir`; só as imagens dela são
  refeitas.
- **Fala errada:** corrija o texto da `"narracao"`; só aquela cena é narrada de novo.
- **Episódio narrado pela voz local e você quer o Gemini:** apague `narracao/motor.json` e rode
  `produzir` num dia com cota.
- **Som de animal errado:** apague a entrada dele em `dados/sons/catalogo.json` e o `.wav`; o
  próximo `produzir` busca outro. Para usar uma gravação sua, salve `dados/sons/<animal>.wav` e
  registre-a no catálogo com `"licenca": "própria"`.
- **Cena sem fala:** deixe `"narracao": ""` e defina `"duracao_s": 3`.
- **Outra trilha:** acrescente `"semente_musica": 1` (ou mude o `"clima_musica"`); só a trilha
  é recomposta.
- **Faixa específica da biblioteca:** acrescente `"musica": "musica/calma/minha-faixa.mp3"`.
- **Sua própria voz:** grave a cena e salve por cima de `narracao/cena_05.wav`.
- **Personagem fixo com outra cara:** mude a `semente` dele no `canal.toml`. Vale para todos os
  episódios; as referências ficam em `dados/personagens/`.

### Publicar (fase 5)

```powershell
.\.venv\Scripts\python.exe -m estudio revisar      # regras automáticas + revisão do Gemini
.\.venv\Scripts\python.exe -m estudio aprovar      # sua aprovação, depois de assistir ao vídeo
.\.venv\Scripts\python.exe -m estudio publicar     # envia os aprovados e agenda (--simular para só ver)
.\.venv\Scripts\python.exe -m estudio agenda       # o que está agendado e os próximos horários livres
```

Os vídeos sobem com "conteúdo para crianças" marcado, título, descrição (com o crédito da
música), tags, miniatura e legendas. Cada um entra no próximo horário livre de
`[publicacao]` no `canal.toml`, sem passar do máximo por semana, e o YouTube o publica sozinho
na hora marcada. O resultado de cada envio fica em `publicacao.json`, na pasta do episódio.

## Estrutura de conformidade

Para ganhar dinheiro com anúncios, o canal precisa entrar no Programa de Parcerias do YouTube
(YPP) e continuar dentro dele. Para conteúdo infantil feito com IA, as regras que mais pesam
são três:

- **Conteúdo inautêntico (julho de 2026):** o YouTube tira a monetização de canais com muito
  conteúdo genérico, repetitivo ou feito a partir de template, sem arco narrativo, ou feito
  para angustiar (por exemplo, um animal sofrendo que depois é salvo). Usar IA não é proibido.
- **Princípios de qualidade para crianças:** vídeos muito promocionais, falsamente educativos,
  difíceis de acompanhar, sensacionalistas, que incentivam mau comportamento ou que usam
  personagens conhecidos de forma estranha perdem anúncios. Um canal focado nisso sai do YPP.
- **Metadados enganosos, direitos autorais e "feito para crianças":** nada de marca ou
  personagem alheio para pegar busca, música só com licença, e a marcação obrigatória de
  conteúdo para crianças.

Cada regra tem um mecanismo no projeto:

| Regra | Como o projeto cumpre |
|---|---|
| Nada genérico | Personagens e estilo próprios e fixos (`canal.toml`); roteiro com começo, meio e fim |
| Nada repetitivo | O roteirista recebe a lista de episódios anteriores. A revisão **bloqueia** narração 30% igual à de outro episódio e avisa a partir de 15% ou com título quase igual |
| Nada de template | Dos outliers o roteiro aprende só o gancho e o ritmo, nunca o passo a passo. A revisão **avisa** quando o roteiro não teve nenhuma edição sua |
| Ritmo sustentável | No máximo `max_por_semana` publicações (padrão 3), em horários fixos, em vez de rajadas |
| Nada angustiante | O roteiro proíbe sofrimento e sustos; a revisão avisa sobre palavras de choro, sangue, morte etc.; o Gemini julga o contexto |
| Princípios de qualidade | O Gemini revisa roteiro, miniatura e todas as imagens contra a lista do YouTube e confere se o que é ensinado está certo |
| Metadados honestos | A revisão **bloqueia** marcas e personagens de terceiros no título, descrição, tags e miniatura, e avisa sobre título caça-clique |
| Música licenciada | A trilha é própria, feita por um modelo treinado com música licenciada, e a versão que lembrar uma música conhecida é reprovada. Faixa da biblioteca só passa se estiver em `musica/licencas.toml`, e o crédito exigido vai sozinho para a descrição |
| Feito para crianças | Marcado sempre no envio (`selfDeclaredMadeForKids`) |
| Conteúdo sintético | Marcação controlada por `conteudo_sintetico` no `canal.toml` (só é exigida para vídeo que parece real) |
| Aprovação | No modo `automatica`, sobe sozinho o que a revisão completa (regras + Gemini) não bloquear; qualquer bloqueio segura o episódio. No modo `humana`, nada sobe sem `aprovar`, que exige a revisão do Gemini e a confirmação dos avisos (`--ciente`). Bloqueios das regras automáticas não têm atalho; um bloqueio só da IA pode ser contestado com `--discordo-da-ia "motivo"`, que fica registrado. Se vídeo, miniatura, legenda ou roteiro mudarem depois da aprovação, o episódio não sobe até ser revisado e aprovado de novo |

Os registros (`revisao.json`, `aprovacao.json`, `publicacao.json`) ficam na pasta de cada
episódio. Se o YouTube pedir explicações sobre um vídeo, você tem o histórico de quem aprovou
o quê e quando.

Lembre ainda que conteúdo marcado como "para crianças" não recebe anúncios personalizados
(rende menos por view) e fica com os comentários desativados.

## Upload automático (configuração, uma vez)

1. **Credencial OAuth:** no mesmo projeto do Google Cloud da chave de API, abra *Google Auth
   Platform* (tela de consentimento OAuth):
   - tipo de usuário **Externo**; preencha nome do app e e-mail;
   - em *Público-alvo*, clique em **Publicar app** ("Em produção"). Em modo "Teste", o acesso
     expira a cada 7 dias e o envio automático para de funcionar;
   - em *Clientes*, crie um cliente do tipo **App para computador** e baixe o JSON. Salve-o na
     pasta do projeto como `client_secret.json`.
   - em *APIs e serviços → Biblioteca*, ative também a **YouTube Analytics API** (é ela que
     lê a retenção dos seus vídeos para o comando `metricas`).
   - se o botão **Publicar app** ficar bloqueado pedindo para completar a página de Branding (um
     problema conhecido, sem solução oficial), deixe o app "Em teste" e adicione a conta dona do
     canal em *Público-alvo → Usuários de teste*. Funciona, mas o acesso expira a cada 7 dias:
     `episodios` e `agenda` avisam quando for hora de rodar `conectar` de novo.
2. **Conectar o canal:**
   ```powershell
   .\.venv\Scripts\python.exe -m estudio conectar
   ```
   O navegador abre. Entre com a conta dona do canal e escolha o canal certo. Como o app é
   seu e não passou pela verificação do Google, aparece o aviso "O Google não verificou este
   app": clique em *Avançado* e depois em *Acessar*. Isso é esperado para uso pessoal, que tem
   limite de 100 usuários.
3. **Miniatura própria:** o YouTube só aceita miniatura enviada se o canal estiver verificado
   por telefone ([youtube.com/verify](https://www.youtube.com/verify)). Sem isso, o vídeo sobe
   normalmente e a miniatura é tentada de novo a cada execução.
4. **Envio todo dia, sozinho:** registre o `publicar.cmd` no Agendador de Tarefas do Windows
   (PowerShell):
   ```powershell
   $acao = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c "D:\Felipe Pasta\Pasta projeto\publicar.cmd"'
   $quando = New-ScheduledTaskTrigger -Daily -At 08:00
   $ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable
   Register-ScheduledTask -TaskName "Estudio - publicar" -Action $acao -Trigger $quando -Settings $ajustes
   ```
   A partir daí, todo episódio que você aprovar sobe na manhã seguinte e vai ao ar no próximo
   horário livre, e as métricas dos vídeos já publicados são atualizadas. O que aconteceu fica
   em `dados\publicacao.log`.
5. **Produção contínua:** registre o `produtor.cmd` para rodar sempre que você entrar no
   Windows, sem janela, sem limite de tempo e voltando sozinho se cair:
   ```powershell
   $acao = New-ScheduledTaskAction -Execute "conhost.exe" -Argument '--headless cmd.exe /c "D:\Felipe Pasta\Pasta projeto\produtor.cmd"'
   $quando = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
   $ajustes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
   Register-ScheduledTask -TaskName "Estudio - produtor" -Action $acao -Trigger $quando -Settings $ajustes
   Start-ScheduledTask -TaskName "Estudio - produtor"
   ```
   Para parar: `python -m estudio pausar` (termina a etapa atual e para). Para não iniciar mais
   ao entrar no Windows: `Disable-ScheduledTask -TaskName "Estudio - produtor"`. O
   `Stop-ScheduledTask` não basta: ele fecha só a janela invisível, e o produtor continua. Só roda
   um produtor por vez; um segundo fica de reserva.

## Música por IA e métricas

**O que se copia e o que não se copia.** Dos vídeos infantis que mais se destacam, o comando
`estilos` faz o Gemini ouvir a música e anotar o estilo: gênero, andamento, tom, instrumentos,
energia, forma (intro, tema que se repete, ponte) e os recursos que prendem a atenção. Isso não
tem dono, e é o que o ACE-Step recebe para compor. Melodia, letra e gravação de uma música
existente têm dono: copiá-las faz o Content ID mandar a receita do vídeo para o detentor dos
direitos e pode render advertência por direitos autorais, que tira o canal do Programa de
Parcerias. Por isso o projeto nunca reproduz uma música, e a versão que o Gemini disser que lembra
uma música conhecida é descartada.

**Como cada trilha é avaliada.** Para cada episódio o ACE-Step compõe 3 versões (cerca de 15 s
cada na RTX 5070). Cada uma passa por:

1. **Medições do ffmpeg:** duração certa, volume entre −35 e −5 LUFS e nenhum buraco de
   silêncio no meio.
2. **Escuta do Gemini:** nota para crianças, nota de qualidade de produção, se combina com o
   clima, se tem voz e se lembra uma música conhecida. Notas abaixo de 6 reprovam.

Fica a versão com a melhor nota. Se nenhuma passar, ele compõe mais 3 com outras sementes. Tudo
fica registrado em `musica/trilha.json`, na pasta do episódio.

**Como as métricas ajustam o estilo.** O comando `metricas`, que a tarefa diária também roda,
busca no YouTube Analytics as views, a porcentagem média assistida e a curva de retenção de
cada vídeo. Cada vídeo é comparado com a mediana do próprio canal: 1,00 é o típico, e 1,20 segurou
20% mais. Isso evita julgar a trilha pelo sucesso da história ou da miniatura. Só contam vídeos
com 7 dias ou mais no ar e 30 views ou mais.

Para cada clima (calma, alegre, aventura), o próximo episódio sorteia o estilo pela chance de
cada um ser o melhor (amostragem de Thompson). Assim, o estilo que segura mais a audiência passa
a ser usado na maioria das vezes, e os outros continuam sendo testados de vez em quando, caso a
diferença tenha sido sorte. Um episódio mantém o estilo que já usou, então a trilha não muda
depois de aprovada.

**Limite honesto:** a trilha pesa pouco na retenção perto da história e da miniatura, e um canal
novo publica poucos vídeos. As primeiras conclusões confiáveis devem levar alguns meses (dezenas
de vídeos por clima). Até lá, `metricas` mostra as chances e o número de vídeos de cada estilo
para você ver quanta evidência existe.

## Como as métricas da pesquisa são calculadas

- **Outlier:** views do vídeo ÷ mediana de views dos 50 uploads recentes do mesmo canal. Shorts
  são comparados só com Shorts e vídeos longos só com longos; vídeos com menos de 14 dias ficam
  fora da base. Um 10x num canal pequeno indica que o formato puxou o vídeo, não o tamanho do canal.
- **Potencial canal novo:** mediana de views por dia dos vídeos de canais com menos de 100 mil
  inscritos que aparecem entre os mais vistos do nicho. É o principal critério do ranking.
- A API não mostra a retenção de vídeos de outros canais. O outlier é a melhor aproximação
  disponível. A retenção real virá dos seus próprios vídeos, na fase de métricas.

## Testes

```powershell
.\.venv\Scripts\python.exe -m unittest
```

## Próximas fases

1. **Métricas para o resto do vídeo:** usar a mesma comparação com o típico do canal para
   ajustar o gancho do roteiro, o ritmo das cenas e o estilo da miniatura, e apontar na curva de
   retenção onde as crianças saem.
2. **Canções com letra (nicho "musicas"):** o ACE-Step também canta. A letra seria original, do
   Gemini, e a música no estilo aprendido.
3. **Versão em inglês do mesmo episódio:** reaproveita as imagens e troca só a narração, o que
   dobra a produção sem custo de GPU.
