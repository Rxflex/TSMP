# TSMP launcher: French. See en.ftl for comments on each message.

language-name = Français

## Side rail

step-role = Héberger ou rejoindre
step-sync = Aligner les mods et la sauvegarde
step-launch = Lancer le jeu
step-load = Charger la sauvegarde partagée
step-play = Jouer ensemble

rail-game = Les Sims 4 { $version }
rail-game-unknown = Les Sims 4, version inconnue
language = Langue

## Before the game is found

loading-title = Recherche des Sims 4
loading-body = Cela prend un instant.
fatal-title = Les Sims 4 sont introuvables
fatal-hint = Installez le jeu, lancez-le une fois, puis rouvrez TSMP.

## Home

home-title = Jouer aux Sims 4 ensemble
home-lead = Les deux joueurs ouvrent TSMP sur le même réseau. L’un héberge, l’autre rejoint.
home-host = Héberger une session
home-host-detail = Partagez vos mods et votre sauvegarde avec l’autre joueur.
home-join = Rejoindre une session
home-join-detail = Trouvez l’hôte sur ce réseau et copiez ses mods.
home-computer = Cet ordinateur
fact-game = Jeu
fact-mods = Mods
fact-packs = Packs
packs-none = Jeu de base uniquement
packs-count =
    { $n ->
        [one] { $n } pack
       *[other] { $n } packs
    }
script-mods-off = Les mods de script sont désactivés. Dans le jeu, ouvrez « Options de jeu » → « Autre », cochez « Activer le contenu personnalisé et les mods » et « Mods de script autorisés », puis relancez le jeu.

## Hosting

host-setup-title = Choisissez la sauvegarde à partager
host-setup-lead = L’autre joueur en reçoit une copie. Ses propres sauvegardes restent intactes.
save-none = Aucune sauvegarde partagée
save-none-detail = Commencer une nouvelle partie ensemble
host-start = Démarrer l’hébergement
host-reading-title = Lecture de votre dossier Mods
host-reading-body = La première fois, chaque fichier est vérifié. Pour un gros dossier, cela peut prendre quelques minutes.
host-failed-title = L’hébergement n’a pas démarré
host-title = Session ouverte
host-lead = Laissez cette fenêtre ouverte pendant que vous jouez.
host-address = Adresse
host-port = Port
host-mods = Mods
host-mods-value =
    { $files ->
        [one] { $files } fichier, { $size }
       *[other] { $files } fichiers, { $size }
    }
host-save = Sauvegarde
host-save-none = Aucune, nouvelle partie
host-mod-missing = tsmp.ts4script n’est pas dans votre dossier Mods : l’autre joueur ne recevra pas le mod TSMP.
host-rivals-moved = Un autre mod multijoueur ne peut pas fonctionner avec TSMP : il a été déplacé de Mods vers Mods.tsmp-backup : { $names }
players = Joueurs
players-empty = En attente de l’autre joueur. Il ouvre TSMP, choisit « Rejoindre une session » et sélectionne cet ordinateur.
guest-connected = Connecté
guest-syncing = Copie de { $done } sur { $total }
guest-ready = Prêt
guest-failed = Échec : { $message }
host-stop = Arrêter l’hébergement

## Joining

join-title = Choisissez un hôte
join-searching = Recherche d’un hôte sur ce réseau. L’autre joueur doit d’abord choisir « Héberger une session ». Si rien n’apparaît, autorisez TSMP dans le pare-feu des deux ordinateurs.
join-manual = Ou saisissez l'adresse de l'hôte
join-manual-detail = L'hôte la voit sur l'écran de sa session.
join-manual-connect = Se connecter
join-manual-bad = Ce n'est pas une adresse. Elle ressemble à 192.168.0.10.
join-failed-title = Impossible de rejoindre la session
join-working-title = Connexion à la session
join-connecting = Connexion…
join-comparing = Comparaison des mods. La première fois, tout votre dossier Mods est lu.
host-unnamed = l’hôte
review-title = S’aligner sur le jeu de { $host }
review-download =
    { $n ->
        [one] Télécharger { $n } fichier ({ $size })
       *[other] Télécharger { $n } fichiers ({ $size })
    }
review-move =
    { $n ->
        [one] Déplacer vers Mods.tsmp-backup { $n } fichier que l’hôte n’a pas
       *[other] Déplacer vers Mods.tsmp-backup { $n } fichiers que l’hôte n’a pas
    }
review-save = Télécharger la sauvegarde partagée { $name } ({ $size })
review-more = et { $n } de plus
review-keep = Rien n’est supprimé. Les fichiers remplacés ou déplacés sont conservés dans des dossiers de sauvegarde à côté de Mods et saves.
review-trust = Les mods de script de l’hôte s’exécuteront sur cet ordinateur avec les droits du jeu. Ne rejoignez que des personnes de confiance.
review-versions = Les versions du jeu diffèrent : la vôtre est { $ours }, celle de l’hôte est { $theirs }. Mettez les deux jeux à jour avant de jouer.
version-unknown = inconnue
sync = Synchroniser
syncing-title = Copie depuis l’hôte
syncing-progress = { $done } sur { $total }
ready-title = Synchronisé avec { $host }
ready-matched = Les mods correspondaient déjà.
ready-changed = Fichiers téléchargés : { $fetched }. Fichiers mis de côté : { $moved }.
ready-backup = Vos anciens fichiers se trouvent dans { $path }
ready-save-downloaded = Sauvegarde partagée { $name } téléchargée.
ready-save-matched = La sauvegarde partagée { $name } correspondait déjà.
leave = Quitter
session-packs = Packs de cette session
session-packs-off = Désactivés pour vous

## Buttons used on several screens

back = Retour
cancel = Annuler

## The game

game-ready = Lancez le jeu quand tout le monde est prêt.
game-starting-steam = Lancement via Steam. Si Steam demande d’autoriser les arguments de lancement, confirmez.
game-starting = Lancement du jeu…
game-running = Le jeu tourne avec les packs de la session.
game-wrong-packs = Le jeu a chargé : { $loaded }. La session demande : { $expected }. Fermez le jeu et relancez-le depuis ici.
game-failed = Le jeu n’a pas démarré : { $message }
game-start = Lancer le jeu

## Multiplayer

live-no-mod = En attente de la connexion du mod TSMP dans le jeu.
live-no-remote = Le lanceur de l’autre joueur n’est pas connecté.
live-load-save = Chargez la sauvegarde partagée pour rejoindre la session.
live-wait-remote = En attente que l’autre joueur charge la sauvegarde partagée.
live-on = En direct. Les deux jeux sont reliés.

## Sizes and times

size-b = { $n } o
size-kb = { $n } Ko
size-mb = { $n } Mo
size-gb = { $n } Go
size-tb = { $n } To
ago-now = à l’instant
ago-minutes = il y a { $n } min
ago-hours = il y a { $n } h
ago-days =
    { $n ->
        [one] il y a { $n } jour
       *[other] il y a { $n } jours
    }
