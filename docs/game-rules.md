# Game rules

The original server's numbers are lost. Galaxy Empire is closely modelled on OGame (same
buildings, ships, research, resources), so Supernova uses the classic OGame formulas as a
starting point. They live in [`server/supernova/economy.py`](../server/supernova/economy.py).
Corrections from people who remember the original values are very welcome.

All production and build times scale with the server's game speed (`SUPERNOVA_SPEED`,
default 1).

## New account

- 500 metal, 500 crystal, 0 deuterium
- all buildings at level 0; storage for 10,000 of each resource
- base production without any mine: 30 metal and 15 crystal per hour

## Buildings

Cost to upgrade from level *L* to *L+1*: `base × factor^L`.

| Type | Building | Metal | Crystal | Deuterium | Factor | Requires |
|-----:|----------|------:|--------:|----------:|-------:|----------|
| 1 | Metal Mine | 60 | 15 | 0 | 1.5 | |
| 2 | Crystal Mine | 48 | 24 | 0 | 1.6 | |
| 3 | Deuterium Synthesizer | 225 | 75 | 0 | 1.5 | |
| 4 | Solar Plant | 75 | 30 | 0 | 1.5 | |
| 5 | Fusion Reactor | 900 | 360 | 180 | 1.8 | Deuterium Synthesizer 5, Energy Technology 3 |
| 6 | Metal Storage | 1,000 | 0 | 0 | 2 | |
| 7 | Crystal Storage | 1,000 | 500 | 0 | 2 | |
| 8 | Deuterium Tank | 1,000 | 1,000 | 0 | 2 | |
| 9 | Robotics Factory | 400 | 120 | 200 | 2 | |
| 10 | Shipyard | 400 | 200 | 100 | 2 | Robotics Factory 2 |
| 11 | Research Lab | 200 | 400 | 200 | 2 | |
| 12 | Alliance Depot | 20,000 | 40,000 | 0 | 2 | |
| 13 | Radar Center\* | 20,000 | 20,000 | 1,000 | 2 | Shipyard 1 |
| 14 | Nanite Factory | 1,000,000 | 500,000 | 100,000 | 2 | Robotics Factory 10, Computer Technology 10 |
| 15 | Sensor Array\* | 20,000 | 40,000 | 20,000 | 2 | Radar Center 1 |
| 16 | Transmit Portal\* | 2,000,000 | 4,000,000 | 2,000,000 | 2 | Hyperspace Technology 7 |
| 20 | Krypton Lab\* | 10,000 | 20,000 | 10,000 | 2 | Research Lab 5 |
| 21 | Pioneer Lab\* | 50,000 | 50,000 | 20,000 | 2 | Research Lab 8 |

\* No OGame equivalent; values are made up. Moon buildings (17–19) are not implemented yet.

- **Build time** (hours) = (metal + crystal) / (2500 × (1 + robotics level) × 2^nanite level) / speed,
  at least 1 second.
- **Tear down** costs half of what the current level cost to build.
- **One construction at a time** per planet. Cancelling refunds the full cost.
- **Lots:** the three mines have fixed lots 1–3; every other building takes one of the free
  lots 4–15 when it is first built and frees it when torn down to level 0.

## Production per hour

| | Formula |
|-|---------|
| Metal Mine | 30 × L × 1.1^L |
| Crystal Mine | 20 × L × 1.1^L |
| Deuterium Synthesizer | 10 × L × 1.1^L |
| Solar Plant (energy) | 20 × L × 1.1^L |
| Fusion Reactor (energy) | 30 × L × (1.05 + 0.01 × Energy Technology)^L, uses 10 × L × 1.1^L deuterium |

Mines use energy (metal and crystal 10 × L × 1.1^L, deuterium 20 × L × 1.1^L). If they need more
energy than the planet produces, all mines produce proportionally less. Every mine and power
plant can be throttled with its percentage setting.

**Storage capacity** = 5000 × ⌊2.5 × e^(20 × L / 33)⌋. Mines stop producing when the storage is
full.

## Research

Cost to research level *L+1*: `base × factor^L`. Research needs a research lab; time (hours) =
(metal + crystal) / (1000 × (1 + research lab level)) / speed, at least 1 second.

- **One research at a time** per empire. Cancelling refunds the full cost.
- The research lab **cannot be upgraded while research is running**, and research cannot start
  while the lab is being upgraded.

| Type | Research | Metal | Crystal | Deuterium | Factor | Requires |
|-----:|----------|------:|--------:|----------:|-------:|----------|
| 1 | Energy Technology | 0 | 800 | 400 | 2 | Lab 1 |
| 2 | Laser Technology | 200 | 100 | 0 | 2 | Lab 1, Energy 2 |
| 3 | Ion Technology | 1,000 | 300 | 100 | 2 | Lab 4, Laser 5, Energy 4 |
| 4 | Hyperspace Technology | 0 | 4,000 | 2,000 | 2 | Lab 7, Energy 5, Shielding 5 |
| 5 | Plasma Technology | 2,000 | 4,000 | 1,000 | 2 | Lab 4, Energy 8, Laser 10, Ion 5 |
| 6 | Combustion Drive | 400 | 0 | 600 | 2 | Lab 1, Energy 1 |
| 7 | Impulse Drive | 2,000 | 4,000 | 600 | 2 | Lab 2, Energy 1 |
| 8 | Hyperspace Drive | 10,000 | 20,000 | 6,000 | 2 | Lab 7, Hyperspace Technology 3 |
| 9 | Espionage Technology | 200 | 1,000 | 200 | 2 | Lab 3 |
| 10 | Computer Technology | 0 | 400 | 600 | 2 | Lab 1 |
| 11 | Astrophysics | 4,000 | 8,000 | 4,000 | 1.75 | Lab 3, Espionage 4, Impulse Drive 3 |
| 14 | Weapons Technology | 800 | 200 | 0 | 2 | Lab 4 |
| 15 | Shielding Technology | 200 | 600 | 0 | 2 | Lab 6, Energy 3 |
| 16 | Armour Technology | 1,000 | 0 | 0 | 2 | Lab 2 |
| 18 | Ultra Temperature Polymerizer\* | 5,000 | 10,000 | 5,000 | 2 | Lab 8, Plasma 3 |
| 19 | Anti-Gravity\* | 10,000 | 20,000 | 10,000 | 2 | Lab 12, Hyperspace Technology 8 |
| 22 | Missile Technology\* | 5,000 | 5,000 | 2,000 | 2 | Lab 5, Weapons 5 |

\* No OGame equivalent; values are made up. Types 17 (krypton technology), 20 and 21 (flagship
technologies) belong to other screens and are not part of the lab.

Energy Technology raises the output of the fusion reactor.
