# Changelog

Each release is described here before its tag is pushed; the release page is
made from this file. [Русская версия](CHANGELOG.ru.md).

## v0.0.2 — Fixes from the first real game

The first real game of **Each with their own family** on two computers went
badly. This release fixes what that game showed. The mode stays an alpha and
the fixes have not been tried in a real game yet: for a stable game play
**One family together**, which plays exactly as in 0.0.1: none of the changes
below touch it.

- **No more waiting on a black screen.** If the game does not start loading
  the other player's state within 15 seconds, the mod stops waiting and the
  guest plays on alone. If the screen is still black then, leave through
  Manage Worlds and load your save. Leaving through Manage Worlds or the main
  menu while loading no longer puts the host's world in the guest's game by
  mistake, and a game restarted from a save that holds the host's world
  plays its own home instead of loading the host's copy of it.
- **The guest keeps its own family and account.** After loading the host's
  world the guest's game still plays the guest's household, under the
  guest's own account. Before, every lot in it belonged to the host's
  account, which is the likely reason the guest could not build on its own
  home. A save that 0.0.1 already gave the host's account gets the guest's
  back on the next load of the host's world, from the household the guest
  made; if build mode is still refused on your own home after that, restore
  your save from `saves.tsmp-backup/` as it was before the 0.0.1 session. If
  the game ends up playing another household all the same, the guest is told
  to switch back.
- **"Go home" moves only the guest.** Sending the guest's sim home from the
  host's lot sent the host's whole game there too. Now only the guest's game
  travels, and the host stays where it is, at the speed it had. A host who
  travels to a guest's home is told that its walls are only in the guest's
  game.
- **Your home stays yours.** Back on your own home while the host is
  elsewhere, your game keeps its own copy of the lot and where your sims
  stood, not the host's copy of it.
- **No endless reloading.** If the host's game moves on while the guest
  loads, the guest loads once more at most, then joins, and both players
  are told. The host's game really stays paused meanwhile: pressing play is
  refused. Loads that keep failing are tried less and less often.
- **Only your own sims in the skewer.** The guest no longer sees the host's
  family among its sims, and the host's game no longer picks the guest's
  active sim.
- **Build mode is honest about walls.** Two saves have different walls, so
  build-mode edits are now shared only on a lot whose walls are exactly the
  same in both games, and each game keeps its own walls otherwise. Edits the
  other game turned down are counted as turned down, and a guest that
  restarts is not sent again what it already has.
- **Clearer logs** for the next playtest: whose lot each loaded lot is, every
  switch to build mode, and every travel the host's game is told to make.

What to expect from this mode for now is in the
[README](https://github.com/Rxflex/TSMP/blob/HEAD/README.md#each-with-their-own-family).

## v0.0.1 — Play together, anywhere

The first release: two people play one world of The Sims 4 together, on one
local network or over the internet, and the launcher does the setup.

- **Live mode.** Both players see the same lot, sims and clock, and each
  controls their own sims. The host's simulation decides what happens.
- **Build mode, both at once.** Walls, floors and wall coverings drawn by
  either player appear for the other, also those drawn while the two were
  apart for a moment.
- **Over the internet.** Players on different networks meet through a small
  session server (`tsmp-server`, one file for Linux) with an invite such as
  `K7QM-2XDA@play.example.org`. A dropped connection is noticed within seconds
  and the session picks up again by itself; an invite survives a host that is
  away for half an hour.
- **Each with their own family.** A guest brings a household from a save of
  their own, with its relationships, into the host's world and keeps playing
  it there. Both launchers back up their saves first.
  *Known to be broken in practice:* in a real game on two computers the
  session kept coming apart, the guest could not use build mode, loading went
  round in a loop and the guest fell into a black void. Until that is fixed
  this mode is an alpha; for a stable game play **One family together**. See
  [Known problems](https://github.com/Rxflex/TSMP/blob/HEAD/docs/internals.md#known-problems).
- **Always in step.** The guest loads the host's world when it arrives and
  again whenever the two could have drifted apart; meanwhile the host's game
  stays paused, and both players are told what is going on.
- **Different lots.** Players can each be on a lot of their own and meet
  again; a guest travelling to the host sees one loading screen.
- **Create-a-Sim.** Either player edits their own sims; the host's game takes
  the guest's new look.
- **Different packs.** Players with different DLC play with the packs they
  have in common. TSMP does not unlock content.
- **One-button setup.** The guest's Mods folder and the shared save are made
  the same as the host's, and nothing is deleted.
- **The launcher** speaks ten languages, shows one log for both games, and
  updates itself from this page.

Live mode, building together, different packs and the setup on a local network
were tried with two computers. Playing over the internet, families of their
own, Create-a-Sim for the guest and different lots are new and so far tried
against the game's protocol in tests only: reports from your games are very
welcome.
