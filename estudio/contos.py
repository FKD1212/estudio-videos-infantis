"""Contos tradicionais de domínio público usados como ponto de partida dos roteiros: histórias leves,
engraçadas e cheias de animais, que o canal reconta do seu jeito, com os seus personagens.

Só a ideia central e o mecanismo de humor entram no roteiro: falas, nomes e a sequência de cenas são
novos, e nada vem de versões modernas (filmes, livros ilustrados, músicas), que têm dono. Muitos
originais têm finais duros (a raposa come todo mundo); cada conto traz o ajuste que o deixa próprio
para crianças de 2 a 5 anos. Recontar com personagens, narração e imagens próprias é história
original; ler o texto de outra pessoa seria "conteúdo reutilizado", que o YouTube não monetiza."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Conto:
    id: str
    titulo: str
    origem: str  # de onde vem e por que é de domínio público
    ideia: str   # a ideia central e onde está a graça
    ajuste: str = ""  # o que muda para ficar próprio para crianças pequenas


CONTOS = [
    Conto("nabo-gigante", "The Enormous Turnip", "conto popular russo (coletânea de Afanássiev, 1863)",
          "um legume gigante não sai da terra; cada amigo que chega ajuda a puxar, um atrás do outro, e quem "
          "resolve é o menorzinho de todos — e todos caem sentados, rindo"),
    Conto("musicos-de-bremen", "The Bremen Town Musicians", "conto dos irmãos Grimm (1819)",
          "animais diferentes formam uma banda, cada um com o seu som, e o primeiro ensaio é uma confusão "
          "engraçada até acharem o ritmo juntos",
          "sem ladrões nem susto: o desafio é ensaiar para um show para os amigos"),
    Conto("henny-penny", "Henny Penny", "conto popular europeu (século XIX)",
          "uma galinha leva uma bolota na cabeça, acha que o céu está caindo e sai juntando amigos de nomes "
          "rimados para avisar alguém, até descobrirem o mal-entendido",
          "sem raposa: o mal-entendido termina com uma explicação gentil e risadas"),
    Conto("galinha-ruiva", "The Little Red Hen", "conto popular inglês/americano (século XIX)",
          '"quem me ajuda?" — e cada amigo tem uma desculpa engraçada, repetida a cada etapa do pão',
          "final gentil: os amigos percebem, ajudam na última etapa e todos dividem o pão"),
    Conto("bolinho-fujao", "The Gingerbread Man", "conto popular americano (1875)",
          "algo redondo sai rolando e foge, e cada animal que tenta pegar entra na correria, com uma rima que "
          "se repete",
          "ninguém é comido: a perseguição vira brincadeira e termina num piquenique"),
    Conto("luva", "The Mitten", "conto popular ucraniano",
          "uma luva perdida na neve vira casa para um animal, depois outro, e outro, cada vez mais apertado, "
          "até o menorzinho fazer todo mundo pular para fora",
          "use só o conto popular, nada de versões ilustradas modernas"),
    Conto("tartaruga-e-lebre", "The Tortoise and the Hare", "fábula de Esopo",
          "uma corrida entre o rápido e o devagar; o rápido se distrai e o devagar chega sem parar",
          "Lulu não é convencida: se distrai com flores, borboletas e brincadeiras, e a corrida termina com "
          "os dois rindo e cruzando a linha juntos"),
    Conto("leao-e-ratinho", "The Lion and the Mouse", "fábula de Esopo",
          "o grandão duvida que o pequenininho possa ajudar, e é o pequenininho que salva o dia",
          "sem armadilha de caçador nem rugido assustador: o grandão fica preso numa coisa boba (um "
          "novelo, um galho)"),
    Conto("sopa-de-pedra", "Stone Soup", "conto popular europeu",
          'alguém anuncia uma "sopa de pedra" e, de curiosidade, cada amigo traz um ingrediente, até a sopa '
          "ficar deliciosa — a graça é a pedra que não faz nada"),
    Conto("cachinhos", "Goldilocks and the Three Bears", "conto popular inglês (Southey, 1837)",
          "três de tudo: um quente demais, um frio demais e um na medida certa — a graça está na repetição "
          "e nas caras de cada tentativa",
          "nada de entrar na casa dos outros sem convite: os ursos convidam para o lanche"),
    Conto("vento-e-sol", "The North Wind and the Sun", "fábula de Esopo",
          "o vento e o sol apostam quem consegue tirar o casaco de alguém; o vento sopra cada vez mais forte "
          "e só piora, o sol só brilha e consegue"),
    Conto("raposa-e-cegonha", "The Fox and the Stork", "fábula de Esopo",
          "dois amigos se convidam para jantar, mas um serve num prato raso e o outro num pote fundo, e "
          "ninguém consegue comer direito — até trocarem",
          "sem vingança: os dois acham graça e aprendem a pensar no outro"),
    Conto("rato-do-campo", "The Town Mouse and the Country Mouse", "fábula de Esopo",
          "dois primos se visitam e estranham tudo da casa um do outro, com mal-entendidos engraçados",
          "sem gato perseguindo: os primos descobrem que gostam de coisas diferentes, e tudo bem"),
    Conto("corvo-e-jarro", "The Crow and the Pitcher", "fábula de Esopo",
          "um passarinho com sede não alcança a água no fundo do jarro e tem a ideia de jogar pedrinhas "
          "até a água subir"),
    Conto("gato-e-guizo", "Belling the Cat", "fábula de Esopo",
          'todo mundo adora a ideia de "pôr um guizo no gato", mas ninguém quer ser quem coloca',
          "o gato é amigo e acha graça: o guizo vira o sininho de uma brincadeira de esconde-esconde"),
    Conto("coruja-e-gatinha", "The Owl and the Pussy-cat", "poema de Edward Lear (1871)",
          "uma viagem de barquinho cheia de nonsense e coisas trocadas de lugar, que termina numa dança ao "
          "luar",
          "sem casamento: é uma viagem de amigos"),
    Conto("filhote-de-elefante", "The Elephant's Child", "Histórias Assim, de Rudyard Kipling (1902)",
          'um filhote curioso pergunta "por quê?" sobre tudo e acaba descobrindo por que o elefante tem '
          "tromba",
          "sem crocodilo puxando nem bronca: a descoberta é uma brincadeira de esticar"),
]


def proximo(usados: list[str], semente: str, contos: list[Conto] | None = None) -> Conto:
    """O próximo conto ainda não usado (ou o menos usado), sorteado de forma estável pela semente."""
    contos = contos or CONTOS
    vezes = {c.id: usados.count(c.id) for c in contos}
    menos = min(vezes.values())
    livres = [c for c in contos if vezes[c.id] == menos]
    return livres[int(hashlib.sha1(semente.encode()).hexdigest(), 16) % len(livres)]


def trecho_do_prompt(conto: Conto) -> str:
    return (
        f'Ponto de partida desta vez: o conto tradicional "{conto.titulo}" ({conto.origem}, domínio público). '
        f"A ideia: {conto.ideia}. Use só essa ideia e o jeito de fazer rir, recontados de um jeito novo com os "
        "personagens do canal e os animais do nicho: falas, nomes e cenas são seus, e nada de versões modernas "
        "(filmes, livros ilustrados, músicas)."
        + (f" Ajuste obrigatório: {conto.ajuste}." if conto.ajuste else "")
    )
