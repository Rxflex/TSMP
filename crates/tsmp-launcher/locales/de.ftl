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
host-port = Port
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

## Joining

join-title = Host wählen
join-searching = Im Netzwerk wird nach einem Host gesucht. Die andere Person muss zuerst „Sitzung hosten“ wählen. Wenn niemand erscheint, erlaube TSMP auf beiden Computern in der Firewall.
join-manual = Oder Adresse des Hosts eingeben
join-manual-detail = Der Host sieht sie auf dem Sitzungsbildschirm.
join-manual-connect = Verbinden
join-manual-bad = Das ist keine Adresse. Sie sieht so aus: 192.168.0.10.
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
ready-title = Abgeglichen mit { $host }
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
