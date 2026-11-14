# TSMP launcher: Spanish. See en.ftl for comments on each message.

language-name = Español

## Side rail

step-role = Crear o unirse
step-sync = Igualar mods y partida
step-launch = Iniciar el juego
step-load = Cargar la partida compartida
step-play = Jugar juntos

rail-game = Los Sims 4 { $version }
rail-game-unknown = Los Sims 4, versión desconocida
language = Idioma

## Before the game is found

loading-title = Buscando Los Sims 4
loading-body = Tardará un momento.
fatal-title = No se encontró Los Sims 4
fatal-hint = Instala el juego, ábrelo una vez y vuelve a abrir TSMP.

## Home

home-title = Juega a Los Sims 4 en compañía
home-lead = Ambos jugadores abren TSMP en la misma red. Uno crea la sesión y el otro se une.
home-host = Crear una sesión
home-host-detail = Comparte tus mods y tu partida con el otro jugador.
home-join = Unirse a una sesión
home-join-detail = Busca al anfitrión en esta red y copia sus mods.
home-computer = Este equipo
fact-game = Juego
fact-mods = Mods
fact-packs = Packs
packs-none = Solo el juego base
packs-count =
    { $n ->
        [one] { $n } pack
       *[other] { $n } packs
    }
script-mods-off = Los mods de script están desactivados. En el juego abre «Opciones de juego» → «Otros», marca «Habilitar contenido personalizado y mods» y «Permitir mods de script», y reinicia el juego.

## Hosting

host-setup-title = Elige la partida que vas a compartir
host-setup-lead = El otro jugador recibe una copia. Sus propias partidas no se tocan.
save-none = Sin partida compartida
save-none-detail = Empezar una partida nueva juntos
save-variants = Partidas de esta unidad doméstica: { $n }
host-start = Crear la sesión
host-reading-title = Leyendo tu carpeta Mods
host-reading-body = La primera vez se comprueba cada archivo. Con una carpeta grande puede tardar unos minutos.
host-failed-title = No se pudo crear la sesión
host-title = Sesión abierta
host-lead = Deja esta ventana abierta mientras jugáis.
host-address = Dirección
host-port = Puerto
host-mods = Mods
host-mods-value =
    { $files ->
        [one] { $files } archivo, { $size }
       *[other] { $files } archivos, { $size }
    }
host-save = Partida
host-save-none = Ninguna, partida nueva
host-mod-missing = tsmp.ts4script no está en tu carpeta Mods, así que el otro jugador no recibirá el mod TSMP.
host-rivals-moved = Otro mod multijugador no puede funcionar junto a TSMP, así que se movió de Mods a Mods.tsmp-backup: { $names }
players = Jugadores
players-empty = Esperando al otro jugador. Tiene que abrir TSMP, elegir «Unirse a una sesión» y seleccionar este equipo.
guest-connected = Conectado
guest-syncing = Copiando { $done } de { $total }
guest-ready = Listo
guest-failed = Error: { $message }
host-stop = Cerrar la sesión

## Joining

join-title = Elige un anfitrión
join-searching = Buscando un anfitrión en esta red. El otro jugador tiene que elegir primero «Crear una sesión». Si no aparece nada, permite TSMP en el cortafuegos de ambos equipos.
join-manual = O escribe la dirección del anfitrión
join-manual-detail = El anfitrión la ve en la pantalla de su sesión.
join-manual-connect = Conectar
join-manual-bad = Eso no es una dirección. Tiene este aspecto: 192.168.0.10.
join-failed-title = No se pudo entrar en la sesión
join-working-title = Entrando en la sesión
join-connecting = Conectando…
join-comparing = Comparando mods. La primera vez se lee toda tu carpeta Mods.
host-unnamed = el anfitrión
review-title = Igualar tu juego al de { $host }
review-download =
    { $n ->
        [one] Descargar { $n } archivo ({ $size })
       *[other] Descargar { $n } archivos ({ $size })
    }
review-move =
    { $n ->
        [one] Mover a Mods.tsmp-backup { $n } archivo que el anfitrión no tiene
       *[other] Mover a Mods.tsmp-backup { $n } archivos que el anfitrión no tiene
    }
review-save = Descargar la partida compartida { $name } ({ $size })
review-more = y { $n } más
review-keep = No se borra nada. Los archivos sustituidos o movidos se guardan en carpetas de copia junto a Mods y saves.
review-trust = Los mods de script del anfitrión se ejecutarán en este equipo con los permisos del juego. Únete solo a alguien de confianza.
review-versions = Las versiones del juego no coinciden: la tuya es { $ours } y la del anfitrión es { $theirs }. Actualizad ambos juegos antes de jugar.
version-unknown = desconocida
sync = Sincronizar
syncing-title = Copiando del anfitrión
syncing-progress = { $done } de { $total }
ready-title = Sincronizado con { $host }
ready-matched = Los mods ya coincidían.
ready-changed = Archivos descargados: { $fetched }. Archivos apartados: { $moved }.
ready-backup = Tus archivos anteriores están en { $path }
ready-save-downloaded = Partida compartida { $name } descargada.
ready-save-matched = La partida compartida { $name } ya coincidía.
leave = Salir
session-packs = Packs de esta sesión
session-packs-off = Desactivados para ti

## Buttons used on several screens

back = Atrás
cancel = Cancelar

## The game

game-ready = Iniciad el juego cuando todos estéis listos.
game-starting-steam = Iniciando mediante Steam. Si Steam pregunta si permite los argumentos de inicio, confirma.
game-starting = Iniciando el juego…
game-running = El juego está abierto con los packs de la sesión.
game-wrong-packs = El juego cargó: { $loaded }. La sesión necesita: { $expected }. Cierra el juego y vuelve a iniciarlo desde aquí.
game-failed = El juego no se inició: { $message }
game-start = Iniciar juego

## Multiplayer

live-no-mod = Esperando a que el mod TSMP del juego se conecte.
live-no-remote = El lanzador del otro jugador no está conectado.
live-load-save = Carga la partida compartida para entrar en la sesión.
live-wait-remote = Esperando a que el otro jugador cargue la partida compartida.
live-on = En directo. Los dos juegos están unidos.

## Sizes and times

size-b = { $n } B
size-kb = { $n } KB
size-mb = { $n } MB
size-gb = { $n } GB
size-tb = { $n } TB
ago-now = ahora mismo
ago-minutes = hace { $n } min
ago-hours = hace { $n } h
ago-days =
    { $n ->
        [one] hace { $n } día
       *[other] hace { $n } días
    }

fact-building = Construir juntos
building-checking = Comprobando…
building-ready = Listo para esta versión del juego
building-missing = Nadie ha descrito todavía el juego { $version }, así que construir juntos está desactivado. Lo demás funciona.
building-unknown = No se conoce la versión del juego, así que construir juntos está desactivado. Lo demás funciona.
update-ready = La versión { $version } está instalada y se usará tras reiniciar
update-restart = Reiniciar con la versión { $version }
