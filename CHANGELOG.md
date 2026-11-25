# Changelog

Each release is described here before its tag is pushed; the release page is
made from this file. [Русская версия](CHANGELOG.ru.md).

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
