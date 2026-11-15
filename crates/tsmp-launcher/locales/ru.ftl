# TSMP launcher: Russian. See en.ftl for comments on each message.

language-name = Русский

## Side rail

step-role = Создать или войти
step-sync = Сверить моды и сохранение
step-launch = Запустить игру
step-load = Загрузить общее сохранение
step-play = Играть вместе

rail-game = The Sims 4 { $version }
rail-game-unknown = The Sims 4, версия неизвестна
language = Язык

## Before the game is found

loading-title = Ищем The Sims 4
loading-body = Это займёт пару секунд.
fatal-title = The Sims 4 не найдена
fatal-hint = Установите игру, запустите её один раз и снова откройте TSMP.

## Home

home-title = The Sims 4 вдвоём
home-lead = Оба игрока открывают TSMP в одной сети. Один создаёт сессию, другой входит в неё.
home-host = Создать сессию
home-host-detail = Поделиться своими модами и сохранением с другим игроком.
home-join = Войти в сессию
home-join-detail = Найти хоста в этой сети и скопировать его моды.
home-computer = Этот компьютер
fact-game = Игра
fact-mods = Моды
fact-packs = Дополнения
packs-none = Только базовая игра
packs-count =
    { $n ->
        [one] { $n } дополнение
        [few] { $n } дополнения
       *[other] { $n } дополнений
    }
script-mods-off = Скриптовые моды выключены. В игре откройте «Настройки игры» → «Другое», отметьте «Включить пользовательские материалы и моды» и «Разрешить моды для сценариев», затем перезапустите игру.

## Hosting

host-setup-title = Какое сохранение сделать общим
host-setup-lead = Другой игрок получит копию. Его собственные сохранения останутся нетронутыми.
save-none = Без общего сохранения
save-none-detail = Начать новую игру вместе
save-variants = Сохранений этой семьи: { $n }
host-start = Создать сессию
host-reading-title = Читаем папку Mods
host-reading-body = В первый раз проверяется каждый файл. Для большой папки это может занять несколько минут.
host-failed-title = Сессия не создана
host-title = Сессия открыта
host-lead = Не закрывайте это окно, пока играете.
host-address = Адрес
host-mods = Моды
host-mods-value =
    { $files ->
        [one] { $files } файл, { $size }
        [few] { $files } файла, { $size }
       *[other] { $files } файлов, { $size }
    }
host-save = Сохранение
host-save-none = Нет, новая игра
host-mod-missing = В папке Mods нет tsmp.ts4script, поэтому другой игрок не получит мод TSMP.
host-rivals-moved = Другой мультиплеерный мод не может работать вместе с TSMP, поэтому он перенесён из Mods в Mods.tsmp-backup: { $names }
players = Игроки
players-empty = Ждём другого игрока. Ему нужно открыть TSMP, выбрать «Войти в сессию» и нажать на этот компьютер.
guest-connected = Подключён
guest-syncing = Копирует: { $done } из { $total }
guest-ready = Готов
guest-failed = Ошибка: { $message }
host-stop = Закрыть сессию

## Joining

join-title = Выберите хоста
join-searching = Ищем хоста в этой сети. Сначала другой игрок должен выбрать «Создать сессию». Если никто не появляется, разрешите TSMP в брандмауэре на обоих компьютерах.
join-manual = Или введите адрес хоста
join-manual-detail = Хост видит его на экране сессии.
join-manual-connect = Подключиться
join-manual-bad = Это не адрес. Он выглядит так: 192.168.0.10.
join-failed-title = Войти не получилось
join-working-title = Входим в сессию
join-connecting = Подключаемся…
join-comparing = Сравниваем моды. В первый раз читается вся папка Mods.
host-unnamed = хост
review-title = Привести игру к игре { $host }
review-download =
    { $n ->
        [one] Скачать { $n } файл ({ $size })
        [few] Скачать { $n } файла ({ $size })
       *[other] Скачать { $n } файлов ({ $size })
    }
review-move =
    { $n ->
        [one] Перенести в Mods.tsmp-backup { $n } файл, которого нет у хоста
        [few] Перенести в Mods.tsmp-backup { $n } файла, которых нет у хоста
       *[other] Перенести в Mods.tsmp-backup { $n } файлов, которых нет у хоста
    }
review-save = Скачать общее сохранение { $name } ({ $size })
review-more = и ещё { $n }
review-keep = Ничего не удаляется. Заменённые и перенесённые файлы остаются в папках с резервными копиями рядом с Mods и saves.
review-trust = Скриптовые моды хоста будут выполняться на этом компьютере с правами игры. Входите только к тем, кому доверяете.
review-versions = Версии игры отличаются: у вас { $ours }, у хоста { $theirs }. Обновите обе игры, прежде чем играть.
version-unknown = неизвестна
sync = Синхронизировать
syncing-title = Копируем с хоста
syncing-progress = { $done } из { $total }
ready-matched = Моды уже совпадали.
ready-changed = Скачано файлов: { $fetched }. Перенесено файлов: { $moved }.
ready-backup = Ваши прежние файлы лежат в { $path }
ready-save-downloaded = Общее сохранение { $name } скачано.
ready-save-matched = Общее сохранение { $name } уже совпадало.
leave = Выйти
session-packs = Дополнения в сессии
session-packs-off = Выключены у вас

## Buttons used on several screens

back = Назад
cancel = Отмена

## The game

game-ready = Запускайте игру, когда все готовы.
game-starting-steam = Запускаем через Steam. Если Steam спросит, разрешить ли параметры запуска, подтвердите.
game-starting = Запускаем игру…
game-running = Игра запущена с дополнениями сессии.
game-wrong-packs = Игра загрузила: { $loaded }, а сессии нужно: { $expected }. Закройте игру и запустите её отсюда ещё раз.
game-failed = Игра не запустилась: { $message }
game-start = Запустить игру

## Multiplayer

live-no-mod = Ждём, пока мод TSMP в игре подключится.
live-no-remote = Лаунчер другого игрока не подключён.
live-load-save = Загрузите общее сохранение, чтобы войти в сессию.
live-wait-remote = Ждём, пока другой игрок загрузит общее сохранение.
live-on = Сессия идёт. Обе игры соединены.

## Sizes and times

size-b = { $n } Б
size-kb = { $n } КБ
size-mb = { $n } МБ
size-gb = { $n } ГБ
size-tb = { $n } ТБ
ago-now = только что
ago-minutes = { $n } мин назад
ago-hours = { $n } ч назад
ago-days =
    { $n ->
        [one] { $n } день назад
        [few] { $n } дня назад
       *[other] { $n } дней назад
    }

fact-building = Совместная стройка
building-checking = Проверяем…
building-ready = Готова для этой версии игры
building-missing = Игру { $version } ещё никто не описал, поэтому совместная стройка выключена. Остальное работает.
building-unknown = Версия игры неизвестна, поэтому совместная стройка выключена. Остальное работает.
update-ready = Версия { $version } установлена и заработает после перезапуска
update-restart = Перезапустить в версии { $version }

## The lobby, the journal and copying errors

copy = Копировать
copied = Скопировано
journal = Журнал
journal-empty = Пока ничего не произошло.
join-failed-hint = Проверьте, что хост не закрыл сессию и что оба компьютера в одной сети.
guest-title = Вы в сессии
player-you = Вы
player-host = Хост
player-you-host = Вы, хост
player-starting = Запускает игру
player-in-game = В игре
player-failed = Ошибка

## Two ways to play: one shared save, or everyone brings a family of their own

step-load-own = Загрузить своё сохранение
host-mode-title = Как будете играть?
mode-shared = Одна семья на двоих
mode-shared-detail = Вы выбираете сохранение. Другой игрок получает копию, и вы играете в нём вместе.
mode-own = У каждого своя семья
mode-own-detail = Каждый создаёт семью у себя. Потом все живут в вашем мире.
own-step-1 = Каждый создаёт семью в своей игре, как обычно, и сохраняется.
own-step-2 = Нажмите «Создать сессию». Потом оба запускают игру из TSMP и загружают свои сохранения.
own-step-3 = Семья другого игрока переезжает в ваш мир, вместе с домом, если этот участок свободен. Дальше вы играете вместе.
own-world = Ваше сохранение становится общим миром. Сохраняйтесь как обычно.
own-new = Этот способ игры новый. Сохраните игру, прежде чем пробовать.
mode-label = Как играем
lobby-own-host = Загрузите своё сохранение в игре. Другой игрок загрузит своё, и его семья переедет в ваш мир.
lobby-own-guest = Загрузите своё сохранение в игре. Ваша семья переедет в мир хоста, и вы продолжите играть ею там.
lobby-own-keep = После этого в вашей игре будет мир хоста. Чтобы своё сохранение осталось прежним, выбирайте в игре «Сохранить как», а не «Сохранить».
review-own = Сохранение не копируется. Вы загружаете своё, и ваша семья переезжает в мир хоста.
live-load-own = Загрузите своё сохранение, чтобы войти в сессию.
live-wait-remote-own = Ждём, пока другой игрок загрузит своё сохранение.
