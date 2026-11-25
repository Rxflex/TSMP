# TSMP launcher: Italian. See en.ftl for comments on each message.

language-name = Italiano

## Side rail

step-role = Ospita o partecipa
step-sync = Allinea mod e salvataggio
step-launch = Avvia il gioco
step-load = Carica il salvataggio condiviso
step-play = Giocate insieme

rail-game = The Sims 4 { $version }
rail-game-unknown = The Sims 4, versione sconosciuta
language = Lingua

## Before the game is found

loading-title = Ricerca di The Sims 4
loading-body = Ci vuole un attimo.
fatal-title = The Sims 4 non è stato trovato
fatal-hint = Installa il gioco, avvialo una volta e poi riapri TSMP.

## Home

home-title = Gioca a The Sims 4 in compagnia
home-lead = Entrambi i giocatori aprono TSMP sulla stessa rete. Uno ospita, l’altro partecipa.
home-host = Ospita una sessione
home-host-detail = Condividi le tue mod e il tuo salvataggio con l’altro giocatore.
home-join = Partecipa a una sessione
home-join-detail = Trova l’host su questa rete e copia le sue mod.
home-computer = Questo computer
fact-game = Gioco
fact-mods = Mod
fact-packs = Pacchetti
packs-none = Solo gioco base
packs-count =
    { $n ->
        [one] { $n } pacchetto
       *[other] { $n } pacchetti
    }
script-mods-off = Le mod script sono disattivate. Nel gioco apri “Opzioni di gioco” → “Altro”, spunta “Abilita contenuti personalizzati e mod” e “Mod script consentite”, poi riavvia il gioco.

## Hosting

host-setup-title = Scegli il salvataggio da condividere
host-setup-lead = L’altro giocatore ne riceve una copia. I suoi salvataggi non vengono toccati.
save-none = Nessun salvataggio condiviso
save-none-detail = Iniziate insieme una nuova partita
save-variants = Salvataggi di questa unità familiare: { $n }
host-start = Avvia la sessione
host-reading-title = Lettura della cartella Mods
host-reading-body = La prima volta viene controllato ogni file. Con una cartella grande possono volerci alcuni minuti.
host-failed-title = La sessione non è partita
host-title = Sessione aperta
host-lead = Lascia aperta questa finestra mentre giocate.
host-address = Indirizzo
host-mods = Mod
host-mods-value =
    { $files ->
        [one] { $files } file, { $size }
       *[other] { $files } file, { $size }
    }
host-save = Salvataggio
host-save-none = Nessuno, nuova partita
host-mod-missing = tsmp.ts4script non è nella tua cartella Mods, quindi l’altro giocatore non riceverà la mod TSMP.
host-rivals-moved = Un’altra mod multigiocatore non può funzionare insieme a TSMP, quindi è stata spostata da Mods a Mods.tsmp-backup: { $names }
players = Giocatori
players-empty = In attesa dell’altro giocatore. Deve aprire TSMP, scegliere “Partecipa a una sessione” e selezionare questo computer.
guest-connected = Connesso
guest-syncing = Copia di { $done } su { $total }
guest-ready = Pronto
guest-failed = Errore: { $message }
host-stop = Chiudi la sessione
online = Via internet
online-detail = Per chi è fuori dalla tua rete. Inserisci l'indirizzo di un server di sessioni TSMP: ti darà un invito da mandargli.
online-open = Apri
online-bad-server = Non è l'indirizzo di un server. Ha questo aspetto: play.example.org o 203.0.113.5:47810.
# { $server } is the session server, such as play.example.org
online-connecting = Connessione a { $server }…
online-ready = Aperta su { $server }. L'altro giocatore entra con questo invito:
# { $reason } is the error, in English
online-retrying = Il server non è raggiungibile ora ({ $reason }). Nuovo tentativo in corso; l'invito resta lo stesso.
online-invite-detail = Va incollato nella schermata «Partecipa a una sessione», al posto dell'indirizzo.
online-stop = Chiudi a internet

## Joining

join-title = Scegli un host
join-searching = Ricerca di un host su questa rete. L’altro giocatore deve prima scegliere “Ospita una sessione”. Se non compare nulla, consenti TSMP nel firewall di entrambi i computer.
join-manual = Oppure inserisci l'indirizzo o l'invito dell'host
join-manual-detail = In questa rete, l'indirizzo che l'host vede nella schermata della sessione. Da fuori, l'invito che l'host ti manda.
join-manual-connect = Connetti
join-manual-bad = Non è né un indirizzo né un invito. Hanno questo aspetto: 192.168.0.10 e ABCD-EFGH@play.example.org.
join-failed-title = Non è stato possibile partecipare
join-working-title = Accesso alla sessione
join-connecting = Connessione…
join-comparing = Confronto delle mod. La prima volta viene letta tutta la cartella Mods.
host-unnamed = l’host
review-title = Allinea il tuo gioco a quello di { $host }
review-download =
    { $n ->
        [one] Scarica { $n } file ({ $size })
       *[other] Scarica { $n } file ({ $size })
    }
review-move =
    { $n ->
        [one] Sposta in Mods.tsmp-backup { $n } file che l’host non ha
       *[other] Sposta in Mods.tsmp-backup { $n } file che l’host non ha
    }
review-save = Scarica il salvataggio condiviso { $name } ({ $size })
review-more = e altri { $n }
review-keep = Non viene eliminato nulla. I file sostituiti o spostati restano in cartelle di backup accanto a Mods e saves.
review-trust = Le mod script dell’host verranno eseguite su questo computer con i permessi del gioco. Partecipa solo alle sessioni di chi ti fidi.
review-versions = Le versioni del gioco sono diverse: la tua è { $ours }, quella dell’host è { $theirs }. Aggiornate entrambi i giochi prima di giocare.
version-unknown = sconosciuta
sync = Sincronizza
syncing-title = Copia dall’host
syncing-progress = { $done } su { $total }
ready-matched = Le mod coincidevano già.
ready-changed = File scaricati: { $fetched }. File messi da parte: { $moved }.
ready-backup = I tuoi file precedenti sono in { $path }
ready-save-downloaded = Salvataggio condiviso { $name } scaricato.
ready-save-matched = Il salvataggio condiviso { $name } coincideva già.
leave = Esci
session-packs = Pacchetti di questa sessione
session-packs-off = Disattivati per te

## Buttons used on several screens

back = Indietro
cancel = Annulla

## The game

game-ready = Avviate il gioco quando siete tutti pronti.
game-starting-steam = Avvio tramite Steam. Se Steam chiede di consentire gli argomenti di avvio, conferma.
game-starting = Avvio del gioco…
game-running = Il gioco è in esecuzione con i pacchetti della sessione.
game-wrong-packs = Il gioco ha caricato: { $loaded }. La sessione richiede: { $expected }. Chiudi il gioco e riavvialo da qui.
game-failed = Il gioco non si è avviato: { $message }
game-start = Avvia il gioco

## Multiplayer

live-no-mod = In attesa che la mod TSMP nel gioco si connetta.
live-no-remote = Il launcher dell’altro giocatore non è connesso.
live-load-save = Carica il salvataggio condiviso per entrare nella sessione.
live-wait-remote = In attesa che l’altro giocatore carichi il salvataggio condiviso.
live-on = In diretta. I due giochi sono collegati.
live-syncing = Allineamento dei due giochi in corso. Tienili aperti entrambi; può richiedere un minuto o due.
live-apart = Entrambi i giochi sono in corso, ognuno sul proprio lotto o nel proprio salvataggio.
live-retrying = Per ora ogni gioco prosegue da solo; tra poco si ricollegano.
live-stopped = Non è stato possibile unire i due salvataggi. Ogni gioco prosegue nel proprio; ricarica un salvataggio per riprovare.

## Sizes and times

size-b = { $n } B
size-kb = { $n } KB
size-mb = { $n } MB
size-gb = { $n } GB
size-tb = { $n } TB
ago-now = proprio ora
ago-minutes = { $n } min fa
ago-hours = { $n } h fa
ago-days =
    { $n ->
        [one] { $n } giorno fa
       *[other] { $n } giorni fa
    }

fact-building = Costruire insieme
building-checking = Controllo in corso…
building-ready = Pronto per questa versione del gioco
building-missing = Nessuno ha ancora descritto il gioco { $version }, quindi costruire insieme è disattivato. Il resto funziona.
building-unknown = La versione del gioco è sconosciuta, quindi costruire insieme è disattivato. Il resto funziona.
update-ready = La versione { $version } è installata e partirà dopo il riavvio
update-restart = Riavvia con la versione { $version }

## The lobby, the journal and copying errors

copy = Copia
copied = Copiato
journal = Registro
journal-empty = Non è ancora successo niente.
join-failed-hint = Controlla che l'host non abbia chiuso la sessione e che i due computer siano sulla stessa rete.
guest-title = Sei nella sessione
player-you = Tu
player-host = Host
player-you-host = Tu, l'host
player-starting = Avvia il gioco
player-in-game = In gioco
player-failed = Errore

## Two ways to play: one shared save, or everyone brings a family of their own

step-load-own = Carica il tuo salvataggio
host-mode-title = Come volete giocare?
mode-shared = Una famiglia insieme
mode-shared-detail = Scegli un salvataggio. L’altro giocatore ne riceve una copia e ci giocate insieme.
mode-own = Ognuno con la sua famiglia
mode-own-detail = Ognuno crea una famiglia nel proprio gioco. Poi vivono tutte nel tuo mondo.
own-step-1 = Ognuno crea una famiglia nel proprio gioco, come sempre, e salva.
own-step-2 = Premi “Avvia la sessione”. Poi entrambi avviate il gioco da TSMP e caricate ciascuno il proprio salvataggio.
own-step-3 = La famiglia dell’altro giocatore si trasferisce nel tuo mondo, con la sua casa se quel lotto è libero. Da lì in poi giocate insieme.
own-world = Il tuo salvataggio diventa il mondo condiviso. Continua a salvarlo come sempre.
own-new = Questo modo di giocare è nuovo. Salva la partita prima di provarlo.
mode-label = Come giocate
lobby-own-host = Carica il tuo salvataggio nel gioco. L’altro giocatore carica il suo e la sua famiglia si trasferisce nel tuo mondo.
lobby-own-guest = Carica il tuo salvataggio nel gioco. La tua famiglia si trasferisce nel mondo dell’host e continui a giocarla lì.
lobby-own-keep = Dopo, il tuo gioco contiene il mondo dell’host. Per conservare il tuo salvataggio com’era, scegli “Salva con nome” nel gioco, non “Salva”.
review-own = Nessun salvataggio viene copiato. Carichi il tuo e la tua famiglia si trasferisce nel mondo dell’host.
live-load-own = Carica il tuo salvataggio per entrare nella sessione.
live-wait-remote-own = In attesa che l’altro giocatore carichi il suo salvataggio.
