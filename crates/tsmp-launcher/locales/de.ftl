# TSMP launcher: German. See en.ftl for comments on each message.

language-name = Deutsch

## Side rail

step-role = Hosten oder beitreten
step-sync = Mods und Spielstand abgleichen
step-launch = Spiel starten
step-load = Gemeinsamen Spielstand laden
step-play = Zusammen spielen

rail-game = Die Sims 4 { $version }
rail-game-unknown = Die Sims 4, Version unbekannt
language = Sprache

## Before the game is found

loading-title = Die Sims 4 wird gesucht
loading-body = Das dauert einen Moment.
fatal-title = Die Sims 4 wurde nicht gefunden
fatal-hint = Installiere das Spiel, starte es einmal und öffne TSMP dann erneut.

## Home

home-title = Die Sims 4 zusammen spielen
home-lead = Beide öffnen TSMP im selben Netzwerk. Eine Person hostet, die andere tritt bei.
home-host = Sitzung hosten
home-host-detail = Teile deine Mods und deinen Spielstand mit der anderen Person.
home-join = Sitzung beitreten
home-join-detail = Finde den Host in diesem Netzwerk und kopiere seine Mods.
home-computer = Dieser Computer
fact-game = Spiel
fact-mods = Mods
fact-packs = Packs
packs-none = Nur Basisspiel
packs-count =
    { $n ->
        [one] { $n } Pack
       *[other] { $n } Packs
    }
script-mods-off = Script-Mods sind ausgeschaltet. Öffne im Spiel „Spieloptionen“ → „Sonstiges“, aktiviere „Benutzerdefinierte Inhalte und Mods aktivieren“ und „Script-Mods erlaubt“ und starte das Spiel neu.

## Hosting

host-setup-title = Spielstand zum Teilen wählen
host-setup-lead = Die andere Person erhält eine Kopie. Ihre eigenen Spielstände bleiben unberührt.
save-none = Kein gemeinsamer Spielstand
save-none-detail = Zusammen ein neues Spiel beginnen
save-variants = Spielstände dieses Haushalts: { $n }
host-start = Hosten starten
host-reading-title = Dein Mods-Ordner wird gelesen
host-reading-body = Beim ersten Mal wird jede Datei geprüft. Bei einem großen Ordner kann das einige Minuten dauern.
host-failed-title = Hosten wurde nicht gestartet
host-title = Sitzung läuft
host-lead = Lass dieses Fenster geöffnet, solange ihr spielt.
host-address = Adresse
host-mods = Mods
host-mods-value =
    { $files ->
        [one] { $files } Datei, { $size }
       *[other] { $files } Dateien, { $size }
    }
host-save = Spielstand
host-save-none = Keiner, neues Spiel
host-mod-missing = tsmp.ts4script liegt nicht in deinem Mods-Ordner, daher erhält die andere Person die TSMP-Mod nicht.
host-rivals-moved = Eine andere Multiplayer-Mod kann nicht neben TSMP laufen und wurde deshalb aus Mods nach Mods.tsmp-backup verschoben: { $names }
players = Spieler
players-empty = Warten auf die andere Person. Sie öffnet TSMP, wählt „Sitzung beitreten“ und klickt auf diesen Computer.
guest-connected = Verbunden
guest-syncing = Kopiert { $done } von { $total }
guest-ready = Bereit
guest-failed = Fehlgeschlagen: { $message }
host-stop = Hosten beenden
online = Über das Internet
online-detail = Für jemanden außerhalb deines Netzwerks. Gib die Adresse eines TSMP-Sitzungsservers ein; er gibt dir eine Einladung, die du weiterschickst.
online-open = Öffnen
online-bad-server = Das ist keine Serveradresse. Sie sieht so aus: play.example.org oder 203.0.113.5:47810.
# { $server } is the session server, such as play.example.org
online-connecting = Verbinde mit { $server }…
online-ready = Offen auf { $server }. Mit dieser Einladung tritt der andere bei:
# { $reason } is the error, in English
online-retrying = Der Server ist gerade nicht erreichbar ({ $reason }). Neuer Versuch läuft; die Einladung bleibt gleich.
online-invite-detail = Sie wird auf dem Bildschirm „Sitzung beitreten“ dort eingefügt, wo die Adresse hingehört.
online-stop = Für das Internet schließen

## Joining

join-title = Host wählen
join-searching = Im Netzwerk wird nach einem Host gesucht. Die andere Person muss zuerst „Sitzung hosten“ wählen. Wenn niemand erscheint, erlaube TSMP auf beiden Computern in der Firewall.
join-manual = Oder Adresse oder Einladung des Hosts eingeben
join-manual-detail = Im selben Netzwerk die Adresse, die der Host auf dem Sitzungsbildschirm sieht. Von woanders die Einladung, die der Host dir schickt.
join-manual-connect = Verbinden
join-manual-bad = Das ist weder eine Adresse noch eine Einladung. Sie sehen so aus: 192.168.0.10 und ABCD-EFGH@play.example.org.
join-failed-title = Beitreten hat nicht funktioniert
join-working-title = Beitritt läuft
join-connecting = Verbindung wird hergestellt…
join-comparing = Mods werden verglichen. Beim ersten Mal wird dein ganzer Mods-Ordner gelesen.
host-unnamed = dem Host
review-title = An das Spiel von { $host } angleichen
review-download =
    { $n ->
        [one] { $n } Datei herunterladen ({ $size })
       *[other] { $n } Dateien herunterladen ({ $size })
    }
review-move =
    { $n ->
        [one] { $n } Datei, die der Host nicht hat, nach Mods.tsmp-backup verschieben
       *[other] { $n } Dateien, die der Host nicht hat, nach Mods.tsmp-backup verschieben
    }
review-save = Gemeinsamen Spielstand { $name } herunterladen ({ $size })
review-more = und { $n } weitere
review-keep = Nichts wird gelöscht. Ersetzte und verschobene Dateien bleiben in Sicherungsordnern neben Mods und saves erhalten.
review-trust = Script-Mods des Hosts laufen auf diesem Computer mit den Rechten des Spiels. Tritt nur Personen bei, denen du vertraust.
review-versions = Die Spielversionen unterscheiden sich: deine ist { $ours }, die des Hosts { $theirs }. Aktualisiert beide Spiele, bevor ihr spielt.
version-unknown = unbekannt
sync = Abgleichen
syncing-title = Dateien werden vom Host kopiert
syncing-progress = { $done } von { $total }
ready-matched = Die Mods stimmten bereits überein.
ready-changed = Heruntergeladene Dateien: { $fetched }. Verschobene Dateien: { $moved }.
ready-backup = Deine bisherigen Dateien liegen in { $path }
ready-save-downloaded = Gemeinsamer Spielstand { $name } heruntergeladen.
ready-save-matched = Gemeinsamer Spielstand { $name } stimmte bereits überein.
leave = Verlassen
session-packs = Packs in dieser Sitzung
session-packs-off = Bei dir ausgeschaltet

## Buttons used on several screens

back = Zurück
cancel = Abbrechen

## The game

game-ready = Startet das Spiel, wenn alle bereit sind.
game-starting-steam = Start über Steam. Wenn Steam fragt, ob Startargumente erlaubt sind, bestätige das.
game-starting = Spiel wird gestartet…
game-running = Das Spiel läuft mit den Packs der Sitzung.
game-wrong-packs = Das Spiel hat geladen: { $loaded }. Die Sitzung braucht: { $expected }. Schließe das Spiel und starte es erneut von hier.
game-failed = Das Spiel wurde nicht gestartet: { $message }
game-start = Spiel starten

## Multiplayer

live-no-mod = Warten, bis sich die TSMP-Mod im Spiel verbindet.
live-no-remote = Der Launcher der anderen Person ist nicht verbunden.
live-load-save = Lade den gemeinsamen Spielstand, um der Sitzung beizutreten.
live-wait-remote = Warten, bis die andere Person den gemeinsamen Spielstand lädt.
live-on = Live. Beide Spiele sind verbunden.
live-syncing = Die beiden Spiele werden abgeglichen. Lass beide offen; das kann ein, zwei Minuten dauern.
live-apart = Beide Spiele laufen, jedes auf seinem eigenen Grundstück oder in seinem eigenen Spielstand.

## Sizes and times

size-b = { $n } B
size-kb = { $n } KB
size-mb = { $n } MB
size-gb = { $n } GB
size-tb = { $n } TB
ago-now = gerade eben
ago-minutes = vor { $n } Min.
ago-hours = vor { $n } Std.
ago-days =
    { $n ->
        [one] vor { $n } Tag
       *[other] vor { $n } Tagen
    }

fact-building = Gemeinsam bauen
building-checking = Wird geprüft…
building-ready = Bereit für diese Spielversion
building-missing = Spielversion { $version } hat noch niemand beschrieben, deshalb ist gemeinsames Bauen aus. Alles andere funktioniert.
building-unknown = Die Spielversion ist unbekannt, deshalb ist gemeinsames Bauen aus. Alles andere funktioniert.
update-ready = Version { $version } ist installiert und läuft nach einem Neustart
update-restart = Neu starten mit Version { $version }

## The lobby, the journal and copying errors

copy = Kopieren
copied = Kopiert
journal = Protokoll
journal-empty = Noch ist nichts passiert.
join-failed-hint = Prüfe, ob der Host die Sitzung nicht geschlossen hat und beide Computer im selben Netzwerk sind.
guest-title = Du bist in der Sitzung
player-you = Du
player-host = Host
player-you-host = Du, der Host
player-starting = Startet das Spiel
player-in-game = Im Spiel
player-failed = Fehler

## Two ways to play: one shared save, or everyone brings a family of their own

step-load-own = Eigenen Spielstand laden
host-mode-title = Wie wollt ihr spielen?
mode-shared = Eine Familie gemeinsam
mode-shared-detail = Du wählst einen Spielstand. Die andere Person bekommt eine Kopie, und ihr spielt ihn zusammen.
mode-own = Jede Person mit eigener Familie
mode-own-detail = Jede Person erstellt bei sich eine Familie. Danach leben alle in deiner Welt.
own-step-1 = Jede Person erstellt wie gewohnt im eigenen Spiel eine Familie und speichert.
own-step-2 = Klicke auf „Hosten starten“. Dann starten beide das Spiel aus TSMP und laden ihren eigenen Spielstand.
own-step-3 = Die Familie der anderen Person zieht in deine Welt, mit ihrem Zuhause, wenn das Grundstück frei ist. Von da an spielt ihr zusammen.
own-world = Dein Spielstand wird zur gemeinsamen Welt. Speichere ihn wie immer.
own-new = Diese Spielweise ist neu. Speichere dein Spiel, bevor du sie ausprobierst.
mode-label = So spielt ihr
lobby-own-host = Lade deinen Spielstand im Spiel. Die andere Person lädt ihren, und ihre Familie zieht in deine Welt.
lobby-own-guest = Lade deinen eigenen Spielstand im Spiel. Deine Familie zieht in die Welt des Hosts, und du spielst sie dort weiter.
lobby-own-keep = Danach enthält dein Spiel die Welt des Hosts. Damit dein eigener Spielstand bleibt, wie er war, wähle im Spiel „Speichern unter“ statt „Speichern“.
review-own = Es wird kein Spielstand kopiert. Du lädst deinen eigenen, und deine Familie zieht in die Welt des Hosts.
live-load-own = Lade deinen eigenen Spielstand, um der Sitzung beizutreten.
live-wait-remote-own = Warten, bis die andere Person ihren Spielstand lädt.
