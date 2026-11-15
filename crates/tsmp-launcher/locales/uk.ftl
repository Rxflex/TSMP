# TSMP launcher: Ukrainian. See en.ftl for comments on each message.
# The game has no Ukrainian version, so its menu names are given in English.

language-name = Українська

## Side rail

step-role = Створити або приєднатися
step-sync = Звірити моди та збереження
step-launch = Запустити гру
step-load = Завантажити спільне збереження
step-play = Грати разом

rail-game = The Sims 4 { $version }
rail-game-unknown = The Sims 4, версія невідома
language = Мова

## Before the game is found

loading-title = Шукаємо The Sims 4
loading-body = Це займе кілька секунд.
fatal-title = The Sims 4 не знайдено
fatal-hint = Встановіть гру, запустіть її один раз і знову відкрийте TSMP.

## Home

home-title = The Sims 4 удвох
home-lead = Обидва гравці відкривають TSMP в одній мережі. Один створює сесію, інший приєднується.
home-host = Створити сесію
home-host-detail = Поділитися своїми модами та збереженням з іншим гравцем.
home-join = Приєднатися до сесії
home-join-detail = Знайти хоста в цій мережі та скопіювати його моди.
home-computer = Цей комп’ютер
fact-game = Гра
fact-mods = Моди
fact-packs = Доповнення
packs-none = Лише базова гра
packs-count =
    { $n ->
        [one] { $n } доповнення
        [few] { $n } доповнення
       *[other] { $n } доповнень
    }
script-mods-off = Скриптові моди вимкнено. У грі відкрийте Game Options → Other, позначте “Enable Custom Content and Mods” і “Script Mods Allowed”, потім перезапустіть гру.

## Hosting

host-setup-title = Яке збереження зробити спільним
host-setup-lead = Інший гравець отримає копію. Його власні збереження залишаться недоторканими.
save-none = Без спільного збереження
save-none-detail = Почати нову гру разом
save-variants = Збережень цієї родини: { $n }
host-start = Створити сесію
host-reading-title = Читаємо теку Mods
host-reading-body = Першого разу перевіряється кожен файл. Для великої теки це може тривати кілька хвилин.
host-failed-title = Сесію не створено
host-title = Сесію відкрито
host-lead = Не закривайте це вікно, поки граєте.
host-address = Адреса
host-mods = Моди
host-mods-value =
    { $files ->
        [one] { $files } файл, { $size }
        [few] { $files } файли, { $size }
       *[other] { $files } файлів, { $size }
    }
host-save = Збереження
host-save-none = Немає, нова гра
host-mod-missing = У теці Mods немає tsmp.ts4script, тому інший гравець не отримає мод TSMP.
host-rivals-moved = Інший мультиплеєрний мод не може працювати разом із TSMP, тому його перенесено з Mods до Mods.tsmp-backup: { $names }
players = Гравці
players-empty = Чекаємо на іншого гравця. Йому треба відкрити TSMP, вибрати «Приєднатися до сесії» та натиснути на цей комп’ютер.
guest-connected = Підключено
guest-syncing = Копіює: { $done } із { $total }
guest-ready = Готовий
guest-failed = Помилка: { $message }
host-stop = Закрити сесію

## Joining

join-title = Виберіть хоста
join-searching = Шукаємо хоста в цій мережі. Спершу інший гравець має вибрати «Створити сесію». Якщо ніхто не з’являється, дозвольте TSMP у брандмауері на обох комп’ютерах.
join-manual = Або введіть адресу хоста
join-manual-detail = Хост бачить її на екрані сесії.
join-manual-connect = Підключитися
join-manual-bad = Це не адреса. Вона виглядає так: 192.168.0.10.
join-failed-title = Приєднатися не вдалося
join-working-title = Приєднуємося
join-connecting = Підключаємося…
join-comparing = Порівнюємо моди. Першого разу читається вся тека Mods.
host-unnamed = хост
review-title = Зробити гру такою, як у { $host }
review-download =
    { $n ->
        [one] Завантажити { $n } файл ({ $size })
        [few] Завантажити { $n } файли ({ $size })
       *[other] Завантажити { $n } файлів ({ $size })
    }
review-move =
    { $n ->
        [one] Перенести до Mods.tsmp-backup { $n } файл, якого немає в хоста
        [few] Перенести до Mods.tsmp-backup { $n } файли, яких немає в хоста
       *[other] Перенести до Mods.tsmp-backup { $n } файлів, яких немає в хоста
    }
review-save = Завантажити спільне збереження { $name } ({ $size })
review-more = і ще { $n }
review-keep = Нічого не видаляється. Замінені та перенесені файли залишаються в теках із резервними копіями поруч із Mods і saves.
review-trust = Скриптові моди хоста виконуватимуться на цьому комп’ютері з правами гри. Приєднуйтеся лише до тих, кому довіряєте.
review-versions = Версії гри відрізняються: у вас { $ours }, у хоста { $theirs }. Оновіть обидві гри, перш ніж грати.
version-unknown = невідома
sync = Синхронізувати
syncing-title = Копіюємо з хоста
syncing-progress = { $done } із { $total }
ready-matched = Моди вже збігалися.
ready-changed = Завантажено файлів: { $fetched }. Перенесено файлів: { $moved }.
ready-backup = Ваші попередні файли лежать у { $path }
ready-save-downloaded = Спільне збереження { $name } завантажено.
ready-save-matched = Спільне збереження { $name } уже збігалося.
leave = Вийти
session-packs = Доповнення в сесії
session-packs-off = Вимкнено у вас

## Buttons used on several screens

back = Назад
cancel = Скасувати

## The game

game-ready = Запускайте гру, коли всі готові.
game-starting-steam = Запускаємо через Steam. Якщо Steam запитає, чи дозволити параметри запуску, підтвердьте.
game-starting = Запускаємо гру…
game-running = Гру запущено з доповненнями сесії.
game-wrong-packs = Гра завантажила: { $loaded }, а сесії потрібно: { $expected }. Закрийте гру та запустіть її звідси ще раз.
game-failed = Гра не запустилася: { $message }
game-start = Запустити гру

## Multiplayer

live-no-mod = Чекаємо, поки мод TSMP у грі підключиться.
live-no-remote = Лаунчер іншого гравця не підключено.
live-load-save = Завантажте спільне збереження, щоб увійти в сесію.
live-wait-remote = Чекаємо, поки інший гравець завантажить спільне збереження.
live-on = Сесія триває. Обидві гри з’єднано.

## Sizes and times

size-b = { $n } Б
size-kb = { $n } КБ
size-mb = { $n } МБ
size-gb = { $n } ГБ
size-tb = { $n } ТБ
ago-now = щойно
ago-minutes = { $n } хв тому
ago-hours = { $n } год тому
ago-days =
    { $n ->
        [one] { $n } день тому
        [few] { $n } дні тому
       *[other] { $n } днів тому
    }

fact-building = Спільне будівництво
building-checking = Перевіряємо…
building-ready = Готове для цієї версії гри
building-missing = Гру { $version } ще ніхто не описав, тому спільне будівництво вимкнено. Решта працює.
building-unknown = Версія гри невідома, тому спільне будівництво вимкнено. Решта працює.
update-ready = Версію { $version } встановлено, вона запрацює після перезапуску
update-restart = Перезапустити у версії { $version }

## The lobby, the journal and copying errors

copy = Копіювати
copied = Скопійовано
journal = Журнал
journal-empty = Поки нічого не сталося.
join-failed-hint = Перевірте, що хост не закрив сесію і що обидва комп'ютери в одній мережі.
guest-title = Ви в сесії
player-you = Ви
player-host = Хост
player-you-host = Ви, хост
player-starting = Запускає гру
player-in-game = У грі
player-failed = Помилка
