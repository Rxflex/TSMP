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
save-variants = Zapisy tej rodziny: { $n }
host-start = Załóż sesję
host-reading-title = Czytamy folder Mods
host-reading-body = Za pierwszym razem sprawdzany jest każdy plik. Przy dużym folderze może to potrwać kilka minut.
host-failed-title = Nie udało się założyć sesji
host-title = Sesja otwarta
host-lead = Zostaw to okno otwarte na czas gry.
host-address = Adres
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
online = Przez internet
online-detail = Dla gracza spoza twojej sieci. Wpisz adres serwera sesji TSMP, a da ci zaproszenie do wysłania.
online-open = Otwórz
online-bad-server = To nie jest adres serwera. Wygląda tak: play.example.org albo 203.0.113.5:47810.
# { $server } is the session server, such as play.example.org
online-connecting = Łączenie z { $server }…
online-ready = Otwarte na { $server }. Drugi gracz dołącza z tym zaproszeniem:
# { $reason } is the error, in English
online-retrying = Serwer jest teraz nieosiągalny ({ $reason }). Kolejna próba; zaproszenie się nie zmieni.
online-invite-detail = Wkleja się je na ekranie „Dołącz do sesji”, w miejscu adresu.
online-stop = Zamknij dla internetu

## Joining

join-title = Wybierz hosta
join-searching = Szukamy hosta w tej sieci. Druga osoba musi najpierw wybrać „Załóż sesję”. Jeśli nikt się nie pojawia, zezwól TSMP w zaporze na obu komputerach.
join-manual = Albo wpisz adres lub zaproszenie hosta
join-manual-detail = W tej sieci adres, który host widzi na ekranie sesji. Z innej sieci zaproszenie, które przysłał host.
join-manual-connect = Połącz
join-manual-bad = To nie jest ani adres, ani zaproszenie. Wyglądają tak: 192.168.0.10 i ABCD-EFGH@play.example.org.
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
live-syncing = Gry są ze sobą zrównywane. Nie zamykaj żadnej; może to potrwać minutę lub dwie.
live-apart = Obie gry działają, każda na swojej działce lub we własnym zapisie.
live-retrying = Na razie każda gra toczy się osobno; wkrótce znów się połączą.
live-stopped = Nie udało się połączyć obu zapisów. Każda gra toczy się we własnym; wczytaj zapis ponownie, aby spróbować jeszcze raz.

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

## The lobby, the journal and copying errors

copy = Kopiuj
copied = Skopiowano
journal = Dziennik
journal-empty = Jeszcze nic się nie wydarzyło.
join-failed-hint = Sprawdź, czy host nie zamknął sesji i czy oba komputery są w tej samej sieci.
guest-title = Jesteś w sesji
player-you = Ty
player-host = Host
player-you-host = Ty, host
player-starting = Uruchamia grę
player-in-game = W grze
player-failed = Błąd

## Two ways to play: one shared save, or everyone brings a family of their own

step-load-own = Wczytaj własny zapis
host-mode-title = Jak chcecie grać?
mode-shared = Jedna rodzina razem
mode-shared-detail = Wybierasz zapis. Druga osoba dostaje kopię i gracie w niego razem.
mode-own = Każdy ze swoją rodziną
mode-own-detail = Każdy tworzy rodzinę u siebie. Potem wszystkie mieszkają w twoim świecie.
own-step-1 = Każdy tworzy rodzinę we własnej grze, jak zwykle, i zapisuje.
own-step-2 = Kliknij „Załóż sesję”. Potem oboje uruchamiacie grę z TSMP i wczytujecie własne zapisy.
own-step-3 = Rodzina drugiej osoby wprowadza się do twojego świata, razem z domem, jeśli ta parcela jest wolna. Od tej chwili gracie razem.
own-world = Twój zapis staje się wspólnym światem. Zapisuj go jak zawsze.
own-new = Ten sposób gry jest nowy. Zapisz grę, zanim go wypróbujesz.
mode-label = Jak gracie
lobby-own-host = Wczytaj swój zapis w grze. Druga osoba wczyta swój, a jej rodzina wprowadzi się do twojego świata.
lobby-own-guest = Wczytaj własny zapis w grze. Twoja rodzina wprowadzi się do świata hosta i dalej będziesz nią tam grać.
lobby-own-keep = Potem w twojej grze będzie świat hosta. Aby twój zapis został taki, jaki był, wybierz w grze „Zapisz jako”, a nie „Zapisz”.
review-own = Żaden zapis nie jest kopiowany. Wczytujesz własny, a twoja rodzina wprowadza się do świata hosta.
live-load-own = Wczytaj własny zapis, aby wejść do sesji.
live-wait-remote-own = Czekamy, aż druga osoba wczyta swój zapis.
