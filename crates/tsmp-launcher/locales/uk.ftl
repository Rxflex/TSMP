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
online = Через інтернет
online-detail = Для гравця з іншої мережі. Введіть адресу сервера сесій TSMP — він видасть запрошення, яке треба надіслати гравцеві.
online-open = Відкрити
online-bad-server = Це не адреса сервера. Вона виглядає так: play.example.org або 203.0.113.5:47810.
# { $server } is the session server, such as play.example.org
online-connecting = Підключення до { $server }…
online-ready = Відкрито на { $server }. Інший гравець приєднується за цим запрошенням:
# { $reason } is the error, in English
online-retrying = Сервер зараз недоступний ({ $reason }). Пробуємо знову; запрошення не зміниться.
online-invite-detail = Його вставляють на екрані «Приєднатися до сесії», у поле адреси.
online-stop = Закрити для інтернету

## Joining

join-title = Виберіть хоста
join-searching = Шукаємо хоста в цій мережі. Спершу інший гравець має вибрати «Створити сесію». Якщо ніхто не з’являється, дозвольте TSMP у брандмауері на обох комп’ютерах.
join-manual = Або введіть адресу хоста чи запрошення
join-manual-detail = У цій мережі — адреса, яку хост бачить на екрані сесії. З іншої мережі — запрошення, яке надіслав хост.
join-manual-connect = Підключитися
join-manual-bad = Це не адреса і не запрошення. Вони виглядають так: 192.168.0.10 і ABCD-EFGH@play.example.org.
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
live-syncing = Ігри приводяться до одного стану. Не закривайте обидві; це може зайняти хвилину-дві.
live-apart = Обидві гри тривають, кожна на своїй ділянці або у своєму збереженні.
live-retrying = Поки кожна гра йде сама по собі; незабаром вони знову з’єднаються.

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

## Two ways to play: one shared save, or everyone brings a family of their own

step-load-own = Завантажити своє збереження
host-mode-title = Як гратимете?
mode-shared = Одна сім’я на двох
mode-shared-detail = Ви вибираєте збереження. Інший гравець отримує копію, і ви граєте в ньому разом.
mode-own = У кожного своя сім’я
mode-own-detail = Кожен створює сім’ю в себе. Потім усі живуть у вашому світі.
own-step-1 = Кожен створює сім’ю у своїй грі, як завжди, і зберігається.
own-step-2 = Натисніть «Створити сесію». Потім обоє запускають гру з TSMP і завантажують свої збереження.
own-step-3 = Сім’я іншого гравця переїжджає у ваш світ, разом із домом, якщо ця ділянка вільна. Далі ви граєте разом.
own-world = Ваше збереження стає спільним світом. Зберігайтеся як завжди.
own-new = Цей спосіб гри новий. Збережіть гру, перш ніж пробувати.
mode-label = Як граємо
lobby-own-host = Завантажте своє збереження в грі. Інший гравець завантажить своє, і його сім’я переїде у ваш світ.
lobby-own-guest = Завантажте своє збереження в грі. Ваша сім’я переїде у світ хоста, і ви далі гратимете нею там.
lobby-own-keep = Після цього у вашій грі буде світ хоста. Щоб ваше збереження лишилося таким, як було, вибирайте в грі «Зберегти як», а не «Зберегти».
review-own = Збереження не копіюється. Ви завантажуєте своє, і ваша сім’я переїжджає у світ хоста.
live-load-own = Завантажте своє збереження, щоб увійти в сесію.
live-wait-remote-own = Чекаємо, поки інший гравець завантажить своє збереження.
