# TSMP launcher: Polish. See en.ftl for comments on each message.

language-name = Polski

## Side rail

step-role = Załóż sesję lub dołącz
step-sync = Wyrównaj mody i zapis
step-launch = Uruchom grę
step-load = Wczytaj wspólny zapis
step-play = Grajcie razem

rail-game = The Sims 4 { $version }
rail-game-unknown = The Sims 4, wersja nieznana
language = Język

## Before the game is found

loading-title = Szukamy The Sims 4
loading-body = To potrwa chwilę.
fatal-title = Nie znaleziono The Sims 4
fatal-hint = Zainstaluj grę, uruchom ją raz, a potem ponownie otwórz TSMP.

## Home

home-title = The Sims 4 we dwoje
home-lead = Obie osoby otwierają TSMP w tej samej sieci. Jedna zakłada sesję, druga dołącza.
home-host = Załóż sesję
home-host-detail = Udostępnij swoje mody i zapis drugiej osobie.
home-join = Dołącz do sesji
home-join-detail = Znajdź hosta w tej sieci i skopiuj jego mody.
home-computer = Ten komputer
fact-game = Gra
fact-mods = Mody
fact-packs = Pakiety
packs-none = Tylko gra podstawowa
packs-count =
    { $n ->
        [one] { $n } pakiet
        [few] { $n } pakiety
       *[other] { $n } pakietów
    }
script-mods-off = Mody skryptowe są wyłączone. W grze otwórz „Opcje gry” → „Inne”, zaznacz „Włącz zawartość niestandardową i mody” oraz „Zezwalaj na mody skryptowe”, a potem uruchom grę ponownie.

## Hosting

host-setup-title = Wybierz zapis do udostępnienia
host-setup-lead = Druga osoba dostanie kopię. Jej własne zapisy pozostaną nietknięte.
save-none = Bez wspólnego zapisu
save-none-detail = Zacznijcie razem nową grę
host-start = Załóż sesję
host-reading-title = Czytamy folder Mods
host-reading-body = Za pierwszym razem sprawdzany jest każdy plik. Przy dużym folderze może to potrwać kilka minut.
host-failed-title = Nie udało się założyć sesji
host-title = Sesja otwarta
host-lead = Zostaw to okno otwarte na czas gry.
host-address = Adres
host-port = Port
host-mods = Mody
host-mods-value =
    { $files ->
        [one] { $files } plik, { $size }
        [few] { $files } pliki, { $size }
       *[other] { $files } plików, { $size }
    }
host-save = Zapis
host-save-none = Brak, nowa gra
host-mod-missing = W folderze Mods nie ma tsmp.ts4script, więc druga osoba nie dostanie moda TSMP.
host-rivals-moved = Inny mod wieloosobowy nie może działać razem z TSMP, więc został przeniesiony z Mods do Mods.tsmp-backup: { $names }
players = Gracze
players-empty = Czekamy na drugą osobę. Powinna otworzyć TSMP, wybrać „Dołącz do sesji” i kliknąć ten komputer.
guest-connected = Połączono
guest-syncing = Kopiuje { $done } z { $total }
guest-ready = Gotowe
guest-failed = Błąd: { $message }
host-stop = Zamknij sesję

## Joining

join-title = Wybierz hosta
join-searching = Szukamy hosta w tej sieci. Druga osoba musi najpierw wybrać „Załóż sesję”. Jeśli nikt się nie pojawia, zezwól TSMP w zaporze na obu komputerach.
join-manual = Albo wpisz adres hosta
join-manual-detail = Host widzi go na ekranie sesji.
join-manual-connect = Połącz
join-manual-bad = To nie jest adres. Wygląda tak: 192.168.0.10.
join-failed-title = Nie udało się dołączyć
join-working-title = Dołączanie
join-connecting = Łączenie…
join-comparing = Porównujemy mody. Za pierwszym razem czytany jest cały folder Mods.
host-unnamed = host
review-title = Dopasuj grę do gry: { $host }
review-download =
    { $n ->
        [one] Pobierz { $n } plik ({ $size })
        [few] Pobierz { $n } pliki ({ $size })
       *[other] Pobierz { $n } plików ({ $size })
    }
review-move =
    { $n ->
        [one] Przenieś do Mods.tsmp-backup { $n } plik, którego host nie ma
        [few] Przenieś do Mods.tsmp-backup { $n } pliki, których host nie ma
       *[other] Przenieś do Mods.tsmp-backup { $n } plików, których host nie ma
    }
review-save = Pobierz wspólny zapis { $name } ({ $size })
review-more = i jeszcze { $n }
review-keep = Nic nie jest usuwane. Zastąpione i przeniesione pliki zostają w folderach kopii obok Mods i saves.
review-trust = Mody skryptowe hosta będą działać na tym komputerze z uprawnieniami gry. Dołączaj tylko do osób, którym ufasz.
review-versions = Wersje gry się różnią: twoja to { $ours }, hosta { $theirs }. Zaktualizujcie obie gry przed rozgrywką.
version-unknown = nieznana
sync = Synchronizuj
syncing-title = Kopiujemy od hosta
syncing-progress = { $done } z { $total }
ready-title = Zsynchronizowano: { $host }
ready-matched = Mody już się zgadzały.
ready-changed = Pobrane pliki: { $fetched }. Przeniesione pliki: { $moved }.
ready-backup = Twoje poprzednie pliki są w { $path }
ready-save-downloaded = Pobrano wspólny zapis { $name }.
ready-save-matched = Wspólny zapis { $name } już się zgadzał.
leave = Wyjdź
session-packs = Pakiety w tej sesji
session-packs-off = Wyłączone u ciebie

## Buttons used on several screens

back = Wstecz
cancel = Anuluj

## The game

game-ready = Uruchomcie grę, gdy wszyscy będą gotowi.
game-starting-steam = Uruchamianie przez Steam. Jeśli Steam zapyta o zgodę na argumenty uruchamiania, potwierdź.
game-starting = Uruchamianie gry…
game-running = Gra działa z pakietami sesji.
game-wrong-packs = Gra wczytała: { $loaded }. Sesja wymaga: { $expected }. Zamknij grę i uruchom ją ponownie stąd.
game-failed = Gra się nie uruchomiła: { $message }
game-start = Uruchom grę

## Multiplayer

live-no-mod = Czekamy, aż mod TSMP w grze się połączy.
live-no-remote = Launcher drugiej osoby nie jest połączony.
live-load-save = Wczytaj wspólny zapis, aby wejść do sesji.
live-wait-remote = Czekamy, aż druga osoba wczyta wspólny zapis.
live-on = Na żywo. Obie gry są połączone.

## Sizes and times

size-b = { $n } B
size-kb = { $n } KB
size-mb = { $n } MB
size-gb = { $n } GB
size-tb = { $n } TB
ago-now = przed chwilą
ago-minutes = { $n } min temu
ago-hours = { $n } godz. temu
ago-days =
    { $n ->
        [one] { $n } dzień temu
       *[other] { $n } dni temu
    }

fact-building = Wspólne budowanie
building-checking = Sprawdzanie…
building-ready = Gotowe dla tej wersji gry
building-missing = Gry { $version } nikt jeszcze nie opisał, więc wspólne budowanie jest wyłączone. Reszta działa.
building-unknown = Wersja gry jest nieznana, więc wspólne budowanie jest wyłączone. Reszta działa.
update-ready = Wersja { $version } jest zainstalowana i zadziała po ponownym uruchomieniu
update-restart = Uruchom ponownie w wersji { $version }
