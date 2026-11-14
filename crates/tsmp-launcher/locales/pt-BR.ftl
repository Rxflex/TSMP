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
host-start = Começar a hospedar
host-reading-title = Lendo sua pasta Mods
host-reading-body = Na primeira vez, cada arquivo é verificado. Em uma pasta grande isso pode levar alguns minutos.
host-failed-title = A hospedagem não começou
host-title = Sessão aberta
host-lead = Deixe esta janela aberta enquanto vocês jogam.
host-address = Endereço
host-port = Porta
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
ready-title = Sincronizado com { $host }
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
