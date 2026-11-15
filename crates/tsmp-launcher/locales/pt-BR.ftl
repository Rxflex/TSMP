# TSMP launcher: Brazilian Portuguese. See en.ftl for comments on each message.

language-name = Português (Brasil)

## Side rail

step-role = Hospedar ou entrar
step-sync = Igualar mods e jogo salvo
step-launch = Iniciar o jogo
step-load = Carregar o jogo salvo compartilhado
step-play = Jogar juntos

rail-game = The Sims 4 { $version }
rail-game-unknown = The Sims 4, versão desconhecida
language = Idioma

## Before the game is found

loading-title = Procurando o The Sims 4
loading-body = Isso leva um instante.
fatal-title = O The Sims 4 não foi encontrado
fatal-hint = Instale o jogo, abra-o uma vez e depois abra o TSMP de novo.

## Home

home-title = Jogue The Sims 4 em dupla
home-lead = Os dois jogadores abrem o TSMP na mesma rede. Um hospeda, o outro entra.
home-host = Hospedar uma sessão
home-host-detail = Compartilhe seus mods e seu jogo salvo com o outro jogador.
home-join = Entrar em uma sessão
home-join-detail = Encontre o anfitrião nesta rede e copie os mods dele.
home-computer = Este computador
fact-game = Jogo
fact-mods = Mods
fact-packs = Pacotes
packs-none = Somente o jogo base
packs-count =
    { $n ->
        [one] { $n } pacote
       *[other] { $n } pacotes
    }
script-mods-off = Os mods de script estão desativados. No jogo, abra “Opções de Jogo” → “Outros”, marque “Habilitar Conteúdo Personalizado e Mods” e “Mods de Script Permitidos” e reinicie o jogo.

## Hosting

host-setup-title = Escolha o jogo salvo para compartilhar
host-setup-lead = O outro jogador recebe uma cópia. Os jogos salvos dele não são alterados.
save-none = Sem jogo salvo compartilhado
save-none-detail = Começar um novo jogo juntos
save-variants = Jogos salvos desta família: { $n }
host-start = Começar a hospedar
host-reading-title = Lendo sua pasta Mods
host-reading-body = Na primeira vez, cada arquivo é verificado. Em uma pasta grande isso pode levar alguns minutos.
host-failed-title = A hospedagem não começou
host-title = Sessão aberta
host-lead = Deixe esta janela aberta enquanto vocês jogam.
host-address = Endereço
host-mods = Mods
host-mods-value =
    { $files ->
        [one] { $files } arquivo, { $size }
       *[other] { $files } arquivos, { $size }
    }
host-save = Jogo salvo
host-save-none = Nenhum, novo jogo
host-mod-missing = O tsmp.ts4script não está na sua pasta Mods, então o outro jogador não receberá o mod TSMP.
host-rivals-moved = Outro mod multijogador não funciona junto com o TSMP, então foi movido de Mods para Mods.tsmp-backup: { $names }
players = Jogadores
players-empty = Aguardando o outro jogador. Ele abre o TSMP, escolhe “Entrar em uma sessão” e seleciona este computador.
guest-connected = Conectado
guest-syncing = Copiando { $done } de { $total }
guest-ready = Pronto
guest-failed = Falhou: { $message }
host-stop = Parar de hospedar

## Joining

join-title = Escolha um anfitrião
join-searching = Procurando um anfitrião nesta rede. O outro jogador precisa escolher “Hospedar uma sessão” primeiro. Se nada aparecer, libere o TSMP no firewall dos dois computadores.
join-manual = Ou digite o endereço do anfitrião
join-manual-detail = O anfitrião o vê na tela da sessão.
join-manual-connect = Conectar
join-manual-bad = Isso não é um endereço. Ele se parece com 192.168.0.10.
join-failed-title = Não foi possível entrar
join-working-title = Entrando na sessão
join-connecting = Conectando…
join-comparing = Comparando mods. Na primeira vez, toda a sua pasta Mods é lida.
host-unnamed = o anfitrião
review-title = Igualar seu jogo ao de { $host }
review-download =
    { $n ->
        [one] Baixar { $n } arquivo ({ $size })
       *[other] Baixar { $n } arquivos ({ $size })
    }
review-move =
    { $n ->
        [one] Mover para Mods.tsmp-backup { $n } arquivo que o anfitrião não tem
       *[other] Mover para Mods.tsmp-backup { $n } arquivos que o anfitrião não tem
    }
review-save = Baixar o jogo salvo compartilhado { $name } ({ $size })
review-more = e mais { $n }
review-keep = Nada é excluído. Arquivos substituídos ou movidos ficam em pastas de backup ao lado de Mods e saves.
review-trust = Os mods de script do anfitrião serão executados neste computador com as permissões do jogo. Entre apenas na sessão de quem você confia.
review-versions = As versões do jogo são diferentes: a sua é { $ours } e a do anfitrião é { $theirs }. Atualizem os dois jogos antes de jogar.
version-unknown = desconhecida
sync = Sincronizar
syncing-title = Copiando do anfitrião
syncing-progress = { $done } de { $total }
ready-matched = Os mods já eram iguais.
ready-changed = Arquivos baixados: { $fetched }. Arquivos movidos: { $moved }.
ready-backup = Seus arquivos anteriores estão em { $path }
ready-save-downloaded = Jogo salvo compartilhado { $name } baixado.
ready-save-matched = O jogo salvo compartilhado { $name } já era igual.
leave = Sair
session-packs = Pacotes desta sessão
session-packs-off = Desativados para você

## Buttons used on several screens

back = Voltar
cancel = Cancelar

## The game

game-ready = Iniciem o jogo quando todos estiverem prontos.
game-starting-steam = Iniciando pela Steam. Se a Steam perguntar se permite os argumentos de inicialização, confirme.
game-starting = Iniciando o jogo…
game-running = O jogo está aberto com os pacotes da sessão.
game-wrong-packs = O jogo carregou: { $loaded }. A sessão precisa de: { $expected }. Feche o jogo e inicie-o por aqui de novo.
game-failed = O jogo não iniciou: { $message }
game-start = Iniciar jogo

## Multiplayer

live-no-mod = Aguardando o mod TSMP do jogo se conectar.
live-no-remote = O launcher do outro jogador não está conectado.
live-load-save = Carregue o jogo salvo compartilhado para entrar na sessão.
live-wait-remote = Aguardando o outro jogador carregar o jogo salvo compartilhado.
live-on = Ao vivo. Os dois jogos estão conectados.

## Sizes and times

size-b = { $n } B
size-kb = { $n } KB
size-mb = { $n } MB
size-gb = { $n } GB
size-tb = { $n } TB
ago-now = agora mesmo
ago-minutes = há { $n } min
ago-hours = há { $n } h
ago-days =
    { $n ->
        [one] há { $n } dia
       *[other] há { $n } dias
    }

fact-building = Construir juntos
building-checking = Verificando…
building-ready = Pronto para esta versão do jogo
building-missing = Ninguém descreveu o jogo { $version } ainda, então construir juntos está desligado. O resto funciona.
building-unknown = A versão do jogo é desconhecida, então construir juntos está desligado. O resto funciona.
update-ready = A versão { $version } está instalada e vale depois de reiniciar
update-restart = Reiniciar na versão { $version }

## The lobby, the journal and copying errors

copy = Copiar
copied = Copiado
journal = Registro
journal-empty = Nada aconteceu ainda.
join-failed-hint = Verifique se o anfitrião não fechou a sessão e se os dois computadores estão na mesma rede.
guest-title = Você está na sessão
player-you = Você
player-host = Anfitrião
player-you-host = Você, o anfitrião
player-starting = Iniciando o jogo
player-in-game = No jogo
player-failed = Erro

## Two ways to play: one shared save, or everyone brings a family of their own

step-load-own = Carregar seu próprio jogo salvo
host-mode-title = Como vocês querem jogar?
mode-shared = Uma família juntos
mode-shared-detail = Você escolhe um jogo salvo. O outro jogador recebe uma cópia e vocês jogam nele juntos.
mode-own = Cada um com sua família
mode-own-detail = Cada um cria uma família no seu jogo. Depois todas vivem no seu mundo.
own-step-1 = Cada um cria uma família no próprio jogo, como sempre, e salva.
own-step-2 = Clique em “Começar a hospedar”. Depois os dois iniciam o jogo pelo TSMP e carregam seus próprios jogos salvos.
own-step-3 = A família do outro jogador se muda para o seu mundo, com a casa dela se o lote estiver livre. A partir daí vocês jogam juntos.
own-world = Seu jogo salvo passa a ser o mundo compartilhado. Continue salvando como sempre.
own-new = Este jeito de jogar é novo. Salve seu jogo antes de experimentar.
mode-label = Como vocês jogam
lobby-own-host = Carregue seu jogo salvo no jogo. O outro jogador carrega o dele, e a família dele se muda para o seu mundo.
lobby-own-guest = Carregue seu próprio jogo salvo no jogo. Sua família se muda para o mundo do anfitrião e você continua jogando com ela lá.
lobby-own-keep = Depois disso, seu jogo contém o mundo do anfitrião. Para manter seu jogo salvo como estava, escolha “Salvar como” no jogo, e não “Salvar”.
review-own = Nenhum jogo salvo é copiado. Você carrega o seu, e sua família se muda para o mundo do anfitrião.
live-load-own = Carregue seu próprio jogo salvo para entrar na sessão.
live-wait-remote-own = Aguardando o outro jogador carregar o jogo salvo dele.
