# TSMP launcher: English. This file is the reference for every other language.
#
# Syntax: https://projectfluent.org/fluent/guide/
# { $name } is replaced by the launcher; keep every placeholder that the
# English text has. Names in quotes that refer to the game's own menus should
# match what the game shows in your language.

# The name of this language, in this language. Shown in the language list.
language-name = English

## Side rail: the steps of a session, in order

step-role = Host or join
step-sync = Match mods and save
step-launch = Start the game
step-load = Load the shared save
step-play = Play together

# Under the player's name. { $version } looks like 1.118.257.1020
rail-game = The Sims 4 { $version }
rail-game-unknown = The Sims 4, version unknown
language = Language

## Before the game is found

loading-title = Looking for The Sims 4
loading-body = This takes a moment.
fatal-title = The Sims 4 was not found
fatal-hint = Install the game and start it once, then open TSMP again.

## Home

home-title = Play The Sims 4 together
home-lead = Both players open TSMP on the same network. One hosts, the other joins.
home-host = Host a session
home-host-detail = Share your mods and save with the other player.
home-join = Join a session
home-join-detail = Find the host on this network and copy their mods.
home-computer = This computer
fact-game = Game
fact-mods = Mods
fact-packs = Packs
packs-none = Base game only
packs-count =
    { $n ->
        [one] { $n } pack
       *[other] { $n } packs
    }
script-mods-off = Script mods are turned off. In the game open Game Options → Other, tick “Enable Custom Content and Mods” and “Script Mods Allowed”, then restart the game.

## Hosting

host-setup-title = Choose the save to share
host-setup-lead = The other player gets a copy. Their own saves stay untouched.
save-none = No shared save
save-none-detail = Start a new game together
host-start = Start hosting
host-reading-title = Reading your Mods folder
host-reading-body = Every file is checked the first time, which can take a few minutes for a large folder.
host-failed-title = Hosting did not start
host-title = Hosting
host-lead = Leave this window open while you play.
host-address = Address
host-port = Port
host-mods = Mods
host-mods-value =
    { $files ->
        [one] { $files } file, { $size }
       *[other] { $files } files, { $size }
    }
host-save = Save
host-save-none = None, new game
host-mod-missing = tsmp.ts4script is not in your Mods folder, so the other player will not receive the TSMP mod.
host-rivals-moved = Another multiplayer mod cannot run beside TSMP, so it was moved from Mods to Mods.tsmp-backup: { $names }
players = Players
players-empty = Waiting for the other player. They open TSMP, choose “Join a session” and pick this computer.
guest-connected = Connected
# { $done } and { $total } are sizes such as 12.5 MB
guest-syncing = Copying { $done } of { $total }
guest-ready = Ready
guest-failed = Failed: { $message }
host-stop = Stop hosting

## Joining

join-title = Choose a host
join-searching = Looking for a host on this network. The other player has to choose “Host a session” first. If nothing shows up, allow TSMP through the firewall on both computers.
join-manual = Or enter the host's address
join-manual-detail = The host sees it on their session screen.
join-manual-connect = Connect
join-manual-bad = That is not an address. It looks like 192.168.0.10.
join-failed-title = Joining did not work
join-working-title = Joining
join-connecting = Connecting…
join-comparing = Comparing mods. The first time this reads your whole Mods folder.
# Used in place of the host's name when it is not known.
host-unnamed = the host
review-title = Match the game of { $host }
review-download =
    { $n ->
        [one] Download { $n } file ({ $size })
       *[other] Download { $n } files ({ $size })
    }
review-move =
    { $n ->
        [one] Move { $n } file the host does not have into Mods.tsmp-backup
       *[other] Move { $n } files the host does not have into Mods.tsmp-backup
    }
review-save = Download the shared save { $name } ({ $size })
# Ends a list of file names that was cut short.
review-more = and { $n } more
review-keep = Nothing is deleted. Replaced and moved files are kept in backup folders next to Mods and saves.
review-trust = Script mods from the host will run on this computer with the game’s permissions. Only join someone you trust.
review-versions = Game versions differ: yours is { $ours }, the host’s is { $theirs }. Update both games before playing.
version-unknown = unknown
sync = Sync
syncing-title = Copying from the host
syncing-progress = { $done } of { $total }
ready-title = In sync with { $host }
ready-matched = Mods already matched.
ready-changed = Files downloaded: { $fetched }. Files moved aside: { $moved }.
# { $path } is a folder such as Mods.tsmp-backup\2026-11-13
ready-backup = Your previous files are in { $path }
ready-save-downloaded = Shared save { $name } downloaded.
ready-save-matched = Shared save { $name } already matched.
leave = Leave
session-packs = Packs in this session
session-packs-off = Switched off for you

## Buttons used on several screens

back = Back
cancel = Cancel

## The game, in the bar at the bottom

game-ready = Start the game when everyone is ready.
game-starting-steam = Starting through Steam. If Steam asks whether to allow launch arguments, confirm it.
game-starting = Starting the game…
game-running = The game is running with the session’s packs.
# { $loaded } and { $expected } are lists of packs, or the text of packs-none.
game-wrong-packs = The game loaded { $loaded } but the session needs { $expected }. Close the game and start it from here again.
game-failed = The game did not start: { $message }
game-start = Start game

## Multiplayer, in the bar at the bottom while the game runs

live-no-mod = Waiting for the TSMP mod in the game to connect.
live-no-remote = The other player’s launcher is not connected.
live-load-save = Load the shared save to join the session.
live-wait-remote = Waiting for the other player to load the shared save.
live-on = Live. Both games are joined.

## Sizes and times. { $n } is a number such as 12.5

size-b = { $n } B
size-kb = { $n } KB
size-mb = { $n } MB
size-gb = { $n } GB
size-tb = { $n } TB
ago-now = just now
ago-minutes = { $n } min ago
ago-hours = { $n } h ago
ago-days =
    { $n ->
        [one] { $n } day ago
       *[other] { $n } days ago
    }
