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
online = Por internet
online-detail = Para alguien fuera de tu red. Escribe la dirección de un servidor de sesiones TSMP y te dará una invitación para enviarle.
online-open = Abrir
online-bad-server = Eso no es la dirección de un servidor. Tiene este aspecto: play.example.org o 203.0.113.5:47810.
# { $server } is the session server, such as play.example.org
online-connecting = Conectando con { $server }…
online-ready = Abierta en { $server }. El otro jugador entra con esta invitación:
# { $reason } is the error, in English
online-retrying = No se puede llegar al servidor ahora ({ $reason }). Se vuelve a intentar; la invitación no cambia.
online-invite-detail = Se pega en la pantalla «Unirse a una sesión», donde va la dirección.
online-stop = Cerrar a internet

## Joining

join-title = Elige un anfitrión
join-searching = Buscando un anfitrión en esta red. El otro jugador tiene que elegir primero «Crear una sesión». Si no aparece nada, permite TSMP en el cortafuegos de ambos equipos.
join-manual = O escribe la dirección o la invitación del anfitrión
join-manual-detail = En esta red, la dirección que el anfitrión ve en la pantalla de su sesión. Desde otra red, la invitación que te envía el anfitrión.
join-manual-connect = Conectar
join-manual-bad = Eso no es ni una dirección ni una invitación. Tienen este aspecto: 192.168.0.10 y ABCD-EFGH@play.example.org.
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
live-syncing = Poniendo los dos juegos al día. Mantén ambos abiertos; puede tardar uno o dos minutos.
live-apart = Los dos juegos están en marcha, cada uno en su propio solar o en su propia partida.

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

## The lobby, the journal and copying errors

copy = Copiar
copied = Copiado
journal = Registro
journal-empty = Todavía no ha pasado nada.
join-failed-hint = Comprueba que el anfitrión no haya cerrado la sesión y que los dos ordenadores estén en la misma red.
guest-title = Estás en la sesión
player-you = Tú
player-host = Anfitrión
player-you-host = Tú, el anfitrión
player-starting = Iniciando el juego
player-in-game = En el juego
player-failed = Error

## Two ways to play: one shared save, or everyone brings a family of their own

step-load-own = Cargar tu propia partida
host-mode-title = ¿Cómo queréis jugar?
mode-shared = Una familia entre todos
mode-shared-detail = Eliges una partida. El otro jugador recibe una copia y la jugáis juntos.
mode-own = Cada uno con su familia
mode-own-detail = Cada uno crea una familia en su juego. Después todas viven en tu mundo.
own-step-1 = Cada uno crea una familia en su propio juego, como siempre, y guarda.
own-step-2 = Pulsa «Crear la sesión». Después los dos iniciáis el juego desde TSMP y cargáis vuestras propias partidas.
own-step-3 = La familia del otro jugador se muda a tu mundo, con su casa si ese solar está libre. A partir de ahí jugáis juntos.
own-world = Tu partida pasa a ser el mundo compartido. Sigue guardándola como siempre.
own-new = Esta forma de jugar es nueva. Guarda tu partida antes de probarla.
mode-label = Cómo jugáis
lobby-own-host = Carga tu partida en el juego. El otro jugador carga la suya y su familia se muda a tu mundo.
lobby-own-guest = Carga tu propia partida en el juego. Tu familia se muda al mundo del anfitrión y sigues jugando con ella allí.
lobby-own-keep = Después tu juego contiene el mundo del anfitrión. Para conservar tu partida como estaba, elige «Guardar como» en el juego, no «Guardar».
review-own = No se copia ninguna partida. Cargas la tuya y tu familia se muda al mundo del anfitrión.
live-load-own = Carga tu propia partida para entrar en la sesión.
live-wait-remote-own = Esperando a que el otro jugador cargue su partida.
