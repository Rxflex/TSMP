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
save-variants = Sauvegardes de ce foyer : { $n }
host-start = Démarrer l’hébergement
host-reading-title = Lecture de votre dossier Mods
host-reading-body = La première fois, chaque fichier est vérifié. Pour un gros dossier, cela peut prendre quelques minutes.
host-failed-title = L’hébergement n’a pas démarré
host-title = Session ouverte
host-lead = Laissez cette fenêtre ouverte pendant que vous jouez.
host-address = Adresse
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
online = Par internet
online-detail = Pour un joueur hors de votre réseau. Saisissez l'adresse d'un serveur de sessions TSMP ; il vous donne une invitation à lui envoyer.
online-open = Ouvrir
online-bad-server = Ce n'est pas une adresse de serveur. Elle ressemble à play.example.org ou 203.0.113.5:47810.
# { $server } is the session server, such as play.example.org
online-connecting = Connexion à { $server }…
online-ready = Ouverte sur { $server }. L'autre joueur rejoint avec cette invitation :
# { $reason } is the error, in English
online-retrying = Le serveur est injoignable pour le moment ({ $reason }). Nouvel essai en cours ; l'invitation ne change pas.
online-invite-detail = Elle se colle sur l'écran « Rejoindre une session », à la place de l'adresse.
online-stop = Fermer à internet

## Joining

join-title = Choisissez un hôte
join-searching = Recherche d’un hôte sur ce réseau. L’autre joueur doit d’abord choisir « Héberger une session ». Si rien n’apparaît, autorisez TSMP dans le pare-feu des deux ordinateurs.
join-manual = Ou saisissez l'adresse ou l'invitation de l'hôte
join-manual-detail = Sur ce réseau, l'adresse que l'hôte voit sur l'écran de sa session. Depuis ailleurs, l'invitation que l'hôte vous envoie.
join-manual-connect = Se connecter
join-manual-bad = Ce n'est ni une adresse ni une invitation. Elles ressemblent à 192.168.0.10 et ABCD-EFGH@play.example.org.
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
live-syncing = Mise en phase des deux jeux. Gardez-les ouverts tous les deux ; cela peut prendre une ou deux minutes.
live-apart = Les deux jeux tournent, chacun sur son propre terrain ou dans sa propre sauvegarde.

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

fact-building = Construire ensemble
building-checking = Vérification…
building-ready = Prêt pour cette version du jeu
building-missing = Personne n’a encore décrit le jeu { $version }, la construction à deux est donc désactivée. Tout le reste fonctionne.
building-unknown = La version du jeu est inconnue, la construction à deux est donc désactivée. Tout le reste fonctionne.
update-ready = La version { $version } est installée et sera utilisée après un redémarrage
update-restart = Redémarrer avec la version { $version }

## The lobby, the journal and copying errors

copy = Copier
copied = Copié
journal = Journal
journal-empty = Rien ne s'est encore passé.
join-failed-hint = Vérifiez que l'hôte n'a pas fermé la session et que les deux ordinateurs sont sur le même réseau.
guest-title = Vous êtes dans la session
player-you = Vous
player-host = Hôte
player-you-host = Vous, l'hôte
player-starting = Lance le jeu
player-in-game = En jeu
player-failed = Erreur

## Two ways to play: one shared save, or everyone brings a family of their own

step-load-own = Charger votre propre sauvegarde
host-mode-title = Comment voulez-vous jouer ?
mode-shared = Une famille à plusieurs
mode-shared-detail = Vous choisissez une sauvegarde. L’autre joueur en reçoit une copie et vous y jouez ensemble.
mode-own = Chacun sa famille
mode-own-detail = Chacun crée une famille chez soi. Ensuite, toutes vivent dans votre monde.
own-step-1 = Chacun crée une famille dans son propre jeu, comme d’habitude, et sauvegarde.
own-step-2 = Cliquez sur « Démarrer l’hébergement ». Ensuite, chacun lance le jeu depuis TSMP et charge sa propre sauvegarde.
own-step-3 = La famille de l’autre joueur emménage dans votre monde, avec sa maison si ce terrain est libre. Vous jouez alors ensemble.
own-world = Votre sauvegarde devient le monde commun. Continuez à sauvegarder comme d’habitude.
own-new = Cette façon de jouer est nouvelle. Sauvegardez votre partie avant de l’essayer.
mode-label = Comment vous jouez
lobby-own-host = Chargez votre sauvegarde dans le jeu. L’autre joueur charge la sienne et sa famille emménage dans votre monde.
lobby-own-guest = Chargez votre propre sauvegarde dans le jeu. Votre famille emménage dans le monde de l’hôte et vous continuez à la jouer là-bas.
lobby-own-keep = Ensuite, votre jeu contient le monde de l’hôte. Pour garder votre sauvegarde telle qu’elle était, choisissez « Enregistrer sous » dans le jeu, et non « Enregistrer ».
review-own = Aucune sauvegarde n’est copiée. Vous chargez la vôtre et votre famille emménage dans le monde de l’hôte.
live-load-own = Chargez votre propre sauvegarde pour rejoindre la session.
live-wait-remote-own = En attente que l’autre joueur charge sa sauvegarde.
