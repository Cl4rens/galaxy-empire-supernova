# Game rules

The original server's numbers are lost. Galaxy Empire is closely modelled on OGame (same
buildings, ships, research, resources), so Supernova uses the classic OGame formulas as a
starting point. They live in [`server/supernova/economy.py`](../server/supernova/economy.py).
Corrections from people who remember the original values are very welcome.

All production and build times scale with the server's game speed (`SUPERNOVA_SPEED`,
default 1).

## Planets

Every planet has its own buildings, resources, shipyard and production, and keeps producing
while the player looks at another one. Research belongs to the whole empire; a research runs in
the lab of the planet where it was started.
Colonies can be given up as long as no fleet flies from or to them; the home planet stays.

## New account

- 500 metal, 500 crystal, 0 deuterium
- all buildings at level 0; storage for 10,000 of each resource
- base production without any mine: 60 metal and 30 crystal per hour
- the home planet has 178 building fields (wiki)

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
| 15 | Sensor Array (moon) | 20,000 | 40,000 | 20,000 | 2 | Moon Base 1 |
| 16 | Transmit Portal (moon) | 2,000,000 | 4,000,000 | 2,000,000 | 2 | Moon Base 1, Hyperspace Technology 7 |
| 17 | Moon Shipyard (moon) | 400 | 200 | 100 | 2 | Moon Robotics Factory 2 |
| 18 | Moon Robotics Factory (moon) | 400 | 120 | 200 | 2 | |
| 19 | Moon Base (moon) | 20,000 | 40,000 | 20,000 | 2 | |
| 20 | Krypton Lab\* | 10,000 | 20,000 | 10,000 | 2 | Research Lab 5 |
| 21 | Pioneer Lab\* (moon) | 50,000 | 50,000 | 20,000 | 2 | Moon Base 1 |

\* No OGame equivalent; values are made up. (moon): built on moons only, see [Moons](#moons).

- **Build time** (hours) = (metal + crystal) / (2500 × (1 + robotics level) × 2^nanite level) / speed,
  at least 1 second.
- **Tear down** costs half of what the current level cost to build.
- **One construction at a time** per planet. Cancelling refunds the full cost.
- **Lots:** the three mines have fixed lots 1–3; every other building takes one of the free
  lots 4–15 when it is first built and frees it when torn down to level 0.

## Production per hour

| | Formula |
|-|---------|
| Metal Mine | 120 × L × 1.1^L |
| Crystal Mine | 80 × L × 1.1^L |
| Deuterium Synthesizer | 60 × L × 1.1^L × (1.28 − 0.002 × maximum temperature) |
| Solar Plant (energy) | 20 × L × 1.1^L |
| Fusion Reactor (energy) | 30 × L × (1.05 + 0.01 × Energy Technology)^L, uses 10 × L × 1.1^L deuterium |

Mines use energy (metal and crystal 10 × L × 1.1^L, deuterium 20 × L × 1.1^L). If they need more
energy than the planet produces, all mines produce proportionally less. Every mine and power
plant can be throttled with its percentage setting.

The mine formulas are fitted to the wiki's tables (metal 350 / h at level 2 and 3,172 at level 10
including the base production, crystal 223 and 2,104, gas 175 at level 2): four times OGame's
metal and crystal mines, six times its synthesizer. Energy use and the solar plant match OGame.

**Planets.** The temperature depends on the position: maximum 150 − 12 × position (± 10 per
planet), minimum 40 below. Cold planets yield more gas; a solar satellite gives
⌊(maximum temperature + 140) / 6⌋ energy, 1 to 40. Home planets have 178 fields; a colony gets a
random size for its position, largest at 3-7 (150-285 fields), smallest at 1-2 and 13-15 (60-160).
A planet shifter changes the temperature but not the size.

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

What researches do besides unlocking buildings and ships (the game's texts and the wiki):

- **Energy Technology** raises the output of the fusion reactor.
- **Combustion / Impulse / Hyperspace Drive** add 10 / 20 / 30 % to the speed of the ships using them.
- **Hyperspace Technology** saves fuel: 26 % × (1 − 0.65^level), fitted to the wiki's measurements.
- **Computer Technology:** fleets in the air at once (expeditions not counted) = 1 + ⌊level / 2⌋;
  VIP 4 and up add one flight route.
- **Astrophysics:** one more planet every two levels (at most 8 colonies), and by level (wiki table)
  the daily safe explorations (0, 8, 16, 16, 48, 48, 64, 64, 80, then 120) and the expedition
  fleets a planet can have out at once (0, 1, 1, 1, 2 from level 4, 3 from 9, 4 from 16, 5 from 25).
- **Weapons / Shielding / Armour Technology:** +10 % per level in combat.
- **Ultra Temperature Polymerizer:** the Pioneer Lab costs 3 % less per level.
- **Anti-Gravity:** the Pioneer Lab is built 2 % faster per level.
- Ion Technology "increases construction efficiency" according to the game, but neither the game
  nor the wiki gives a number, so it only unlocks things here.

## Shipyard

Ships and defense are built in the shipyard, one unit after another. Each order of *n* units is
added to the shipyard queue; orders run back to back, and every finished unit is available
immediately.

- **Time per unit** (hours) = (metal + crystal) / (2500 × (1 + shipyard level) × 2^nanite level)
  / speed, at least 1 second.
- **Cancelling** an order refunds the units that are not built yet; later orders move up.
- The **shipyard and the nanite factory cannot be upgraded while the shipyard is working**, and
  nothing can be ordered while one of them is being upgraded.
- **Shield domes** exist only once per planet.
- Every **solar satellite** produces 1-40 energy depending on the planet's temperature (see Production).
- Interceptors (23) come from the krypton shop and Iron Behemoths (24) from the Pioneer Lab.
- The Proton Cannon (27, defense) is built in the shipyard (shipyard 15, nanite factory 2).
- Devourer Interceptors (25), Titanenergetic Frigates (26) and Annihilator construction
  permits (28) are shop items; Reapers (29) are built from 3,000 intricate components, which
  every battle gives for large enemy ships destroyed (hull / 5,000 each, from battlecruisers up).

| Type | Unit | Metal | Crystal | Deuterium | Requires |
|-----:|------|------:|--------:|----------:|----------|
| 1 | Light Fighter | 3,000 | 1,000 | 0 | Shipyard 1, Combustion Drive 1 |
| 2 | Heavy Fighter | 6,000 | 4,000 | 0 | Shipyard 3, Armour 2, Impulse Drive 2 |
| 3 | Cruiser | 20,000 | 7,000 | 2,000 | Shipyard 5, Impulse Drive 4, Ion 2 |
| 4 | Battleship | 45,000 | 15,000 | 0 | Shipyard 7, Hyperspace Drive 4 |
| 5 | Battlecruiser | 30,000 | 40,000 | 15,000 | Shipyard 8, Hyperspace Technology 5, Hyperspace Drive 5, Laser 12 |
| 6 | Bomber | 50,000 | 25,000 | 15,000 | Shipyard 8, Impulse Drive 6, Plasma 5 |
| 7 | Destroyer | 60,000 | 50,000 | 15,000 | Shipyard 9, Hyperspace Drive 6, Hyperspace Technology 5 |
| 8 | Deathstar | 5,000,000 | 4,000,000 | 1,000,000 | Shipyard 12, Hyperspace Drive 7, Hyperspace Technology 6, Anti-Gravity 1 |
| 9 | Small Cargo | 2,000 | 2,000 | 0 | Shipyard 2, Combustion Drive 2 |
| 10 | Large Cargo | 6,000 | 6,000 | 0 | Shipyard 4, Combustion Drive 6 |
| 11 | Colony Ship | 10,000 | 20,000 | 10,000 | Shipyard 4, Impulse Drive 3 |
| 12 | Recycler | 10,000 | 6,000 | 2,000 | Shipyard 4, Combustion Drive 6, Shielding 2 |
| 13 | Espionage Probe | 6,000 | 4,000 | 0 | Shipyard 3, Combustion Drive 3, Espionage 2 |
| 14 | Solar Satellite | 0 | 2,000 | 500 | Shipyard 1 |
| 15 | Rocket Launcher | 2,000 | 0 | 0 | Shipyard 1 |
| 16 | Light Laser | 1,500 | 500 | 0 | Shipyard 2, Energy 1, Laser 3 |
| 17 | Heavy Laser | 6,000 | 2,000 | 0 | Shipyard 4, Energy 3, Laser 6 |
| 18 | Gauss Cannon | 20,000 | 15,000 | 2,000 | Shipyard 6, Energy 6, Weapons 3, Shielding 1 |
| 19 | Ion Cannon | 2,000 | 6,000 | 0 | Shipyard 4, Ion 4 |
| 20 | Plasma Cannon | 50,000 | 50,000 | 30,000 | Shipyard 8, Plasma 7 |
| 21 | Small Shield Dome | 10,000 | 10,000 | 0 | Shipyard 1, Shielding 2 |
| 22 | Large Shield Dome | 50,000 | 50,000 | 0 | Shipyard 6, Shielding 6 |

## Universe

- 9 galaxies × 499 solar systems × 15 planet positions, as in OGame. Position 16 of every
  system is the expedition point, 17–21 are pirate bases (shown, not attackable yet).
- New players get a home planet in galaxy 1 at position 4–12 of a random system. The range of
  systems grows with the number of planets (10 + planets), so early players live close together
  instead of being spread over 4,491 systems.

## Fleets

Ships fly between planets; defense units and solar satellites cannot fly. Implemented missions:

- **Transport:** delivers the cargo to any player's planet, then returns.
- **Deploy:** moves ships and cargo to another planet of your own; they stay there.
- **Espionage:** needs at least one espionage probe; probes return after the report.
- **Attack:** needs armed ships; fights the planet's ships and defense (see Combat).
- **Colonize:** a colony ship settles a free position (1–15). The ship becomes the colony; other
  ships and the cargo stay there. How many planets a player may rule depends on Astrophysics:
  1 + ⌊level / 2⌋, at most 9, i.e. one more planet at levels 2, 4, 6, …
- **Recycle:** recyclers collect a debris field up to their cargo space (20,000 each), metal
  first; the rest stays for later.

Every arrival and return sends a report to the mailbox; the target of a transport or of
espionage is notified too.

### Espionage

How much a report shows depends on the espionage level =
probes + (own − target espionage technology) × |own − target espionage technology| (OGame):

| Level | Shown |
|------:|-------|
| any | resources, energy |
| ≥ 2 | ships |
| ≥ 3 | defense |
| ≥ 5 | buildings |

A fleet that is still on its way out can be **recalled**; it flies back as long as it has
already flown. Missions complete on the server even while the player is offline.

| | Formula (OGame) |
|-|-----------------|
| Distance, other galaxy | 20,000 × galaxies apart |
| Distance, same galaxy | 2,700 + 95 × systems apart |
| Distance, same system | 1,000 + 5 × positions apart |
| Flight time (seconds) | (10 + 3500 / (speed% / 10) × √(10 × distance / slowest ship speed)) / game speed |
| Fuel (deuterium) | 1 + Σ consumption × distance / 35,000 × (speed% / 100 + 1)² |

Ship speed grows with its drive research: combustion +10 %, impulse +20 %, hyperspace +30 % per
level. Small cargos switch to the impulse drive (speed 10,000) at Impulse Drive 5, bombers to the
hyperspace drive at Hyperspace Drive 8.

| Ship | Speed | Cargo | Fuel | Drive |
|------|------:|------:|-----:|-------|
| Light Fighter | 12,500 | 50 | 20 | Combustion |
| Heavy Fighter | 10,000 | 100 | 75 | Impulse |
| Cruiser | 15,000 | 800 | 300 | Impulse |
| Battleship | 10,000 | 1,500 | 500 | Hyperspace |
| Battlecruiser | 10,000 | 750 | 250 | Hyperspace |
| Bomber | 4,000 | 500 | 1,000 | Impulse |
| Destroyer | 5,000 | 2,000 | 1,000 | Hyperspace |
| Deathstar | 100 | 1,000,000 | 1 | Hyperspace |
| Small Cargo | 5,000 | 5,000 | 20 | Combustion |
| Large Cargo | 7,500 | 25,000 | 50 | Combustion |
| Colony Ship | 2,500 | 7,500 | 1,000 | Impulse |
| Recycler | 7,500 | 20,000 | 300 | Combustion |
| Espionage Probe | 100,000,000 | 5 | 1 | Combustion |
| Interceptor | 5,000 | 2,000 | 1,200 | Hyperspace |
| Iron Behemoth | 5,000 | 4,000 | 3,200 | Hyperspace |

## Combat

Battles follow OGame (`server/supernova/combat.py`):

- At most 6 rounds. Every round, every unit fires at a random enemy unit. Shields are restored
  at the start of each round; a shot weaker than 1 % of the target's shield is absorbed.
- Damage hits the shield first, then the hull. A unit whose hull drops below 70 % explodes with
  a probability of 1 − hull / initial hull.
- Rapid fire: some units fire again at certain targets (e.g. cruisers at light fighters 6,
  battlecruisers at battleships 7, deathstars at almost everything).
- Weapons, Shielding and Armour Technology each add 10 % per level.
- Hull = (metal + crystal) / 10 of the unit's cost, except where the Galaxy Empire wiki says
  otherwise (espionage probe, interceptor, Iron Behemoth).
- Rapid fire of the interceptor and the Iron Behemoth follows the wiki (both against
  destroyers 10, the Behemoth also against interceptors 10); ion cannons fire 5 times at
  Behemoths.
- If the attacker wins, it takes up to half of each resource, limited by the cargo space of
  its surviving ships.
- 70 % of destroyed defense units are repaired after the battle.
- 30 % of the metal and crystal of all destroyed ships (both sides) becomes a debris field at
  the target's position.
- Both sides get a battle report; the defender sees the attack coming in its flight list.

| Unit | Weapon | Shield | Hull |
|------|-------:|-------:|-----:|
| Light Fighter | 50 | 10 | 400 |
| Heavy Fighter | 150 | 25 | 1,000 |
| Cruiser | 400 | 50 | 2,700 |
| Battleship | 1,000 | 200 | 6,000 |
| Battlecruiser | 700 | 400 | 7,000 |
| Bomber | 1,000 | 500 | 7,500 |
| Destroyer | 2,000 | 500 | 11,000 |
| Deathstar | 200,000 | 50,000 | 900,000 |
| Small Cargo | 5 | 10 | 400 |
| Large Cargo | 5 | 25 | 1,200 |
| Colony Ship | 50 | 100 | 3,000 |
| Recycler | 1 | 10 | 1,600 |
| Espionage Probe | 0 | 0 | 100 |
| Solar Satellite | 1 | 1 | 200 |
| Rocket Launcher | 80 | 20 | 200 |
| Light Laser | 100 | 25 | 200 |
| Heavy Laser | 250 | 100 | 800 |
| Gauss Cannon | 1,100 | 200 | 3,500 |
| Ion Cannon | 150 | 500 | 800 |
| Plasma Cannon | 3,000 | 300 | 10,000 |
| Small Shield Dome | 1 | 2,000 | 2,000 |
| Large Shield Dome | 1 | 10,000 | 10,000 |
| Interceptor | 7,000 | 1,500 | 160,000 |
| Iron Behemoth | 5,500 | 4,000 | 350,000 |

## Pirates and expeditions

These are Supernova's own rules; the original values are unknown (`server/supernova/npc.py`).

**Pirate bases** sit at positions 17–21 of every system, level 1–5. A base of level *L* defends
with *L*² × (8 light fighters, 3 heavy fighters, 1 cruiser, 10 rocket launchers, 4 light lasers),
fights with weapons/shielding/armour technology *L*, and holds *L*² × (6,000 metal, 4,000 crystal,
1,500 deuterium). Beating it gives the loot (as far as the cargo space allows) and leaves the
base empty for 4 hours / game speed; the galaxy view shows the countdown. Pirate bases can be
spied on and leave debris like any battle.

**Expeditions** fly to position 16. They need Astrophysics, which sets how many a day are safe and how many fleets a planet can have out at once (see Research); VIP and gamma detectors add more. Outcomes:

| Chance | Outcome |
|-------:|---------|
| 35 % | resources: 10–50 % of the fleet's value, as far as the cargo space allows |
| 15 % | ships join the fleet |
| 8 % | 50–300 dark matter |
| 12 % | pirate ambush (a battle) |
| 29 % | nothing |
| 1 % | black hole: the fleet is lost |

## Rankings

- **Points:** one point per 1,000 resources spent on buildings, research, ships and defense
  (all planets), as in OGame.
- **Strength:** the same for ships and defense only.
- **Statistics:** battles won, lost and drawn against other players.

Rankings are computed on request from all players' states; fine for community servers with
up to a few hundred players.

## Alliances

- Founding needs an alliance depot on any planet and costs 999 metal, crystal and deuterium
  from the current planet. Names are unique (case-insensitive).
- Up to 50 members. The president and vice presidents accept or reject applications, change
  ranks and kick members; with automatic joining applications are accepted at once.
- If the president leaves, the longest-serving vice president (or member) takes over; the last
  member leaving dissolves the alliance.
- The alliance score is the sum of its members' points.

## Tutorial quests

A chain of 21 quests leads new players through the game: mines, energy, deuterium, robotics,
shipyard, storage, naming a planet, research, the first fleet, espionage, attacking pirates,
expeditions, registering, colonizing, transport, deploy, attacking a player, recycling and the
radar center. Each pays resources, some also ships the next quest needs (espionage probes,
light fighters, a colony ship, small cargos, a recycler). Rewards go to the current planet when
the player opens the quest window after reaching the goals. Fleet missions sent earlier also
count. Players can skip the tutorial; skipping pays nothing.

## Moons

- A battle at a player's planet can form a moon there: 1 % chance per 100,000 debris (metal and
  crystal), at most 20 %. A planet has at most one moon.
- Moons produce nothing. They have 1 building field plus 3 per moon base level; the moon base
  itself can always be built.
- Moon buildings: moon base, moon robotics factory (speeds up moon constructions), moon
  shipyard (needs moon robotics factory 2; builds ships like the planet shipyard), sensor array,
  transmit portal and pioneer lab (each needs moon base 1). Planet buildings cannot be built on
  moons and moon buildings not on planets.
- Fleets can fly to moons (transport, deploy, espionage, attack). A planet and its moon are 5
  distance units apart.
- **Sensor array:** scans the flights from and to a planet up to level² - 1 systems away in the
  moon's galaxy. Each scan costs 5,000 deuterium from the moon; saving the result as a mail is
  free.
- **Transmit portal:** ships jump at once and without fuel between two own moons that both have
  a portal. Afterwards both portals recharge for 60 min × 0.7^(level - 1) / speed (at least
  1 minute).
- Giving up a colony gives up its moon too.

## Daily check-in and gifts

- Every day (UTC) a player logs in unlocks the next of 21 check-in gifts: resources on most
  days, 10 light fighters on day 7, 10 small cargos on day 14 and 5 cruisers on day 21. After
  all 21 are claimed the calendar starts again. Gifts go to the current planet.
- New players get a welcome gift (2,000 metal, 1,000 crystal, 500 deuterium), opened from the
  first tutorial quest.
- Gifts the operator sends with the admin tool are credited to the home planet at once and
  announced by mail.

## Kryptons

The original rules are lost; Supernova's own:

- **Refining** costs 200 krypton stones × refinery stage (1-5) and puts a krypton into the
  tray (18 places). After each refining the stage goes up by one (50 %) or back to 1. A krypton
  gets 1 star plus one per stage with 30 % chance each (+3 % per krypton research level), at
  most 6 stars. With "auto break-down" kryptons of 4 stars or less turn into energy at once.
- From the tray kryptons are **collected** into the storage (18 places, up to 54 for 300 stones
  each) or **broken down** into 10 × 2^(stars-1) krypton energy.
- Kryptons are **set into planets** (1 slot, up to 5; the n-th extra slot costs 1,000 × n
  stones) and taken out for free. **Upgrading** to the next level costs 20 × stars × level
  energy (levels 1-10); breaking down an upgraded krypton returns half of that energy.
- **Effects**, per krypton: 2 % per star + 1 % per level above 1. Weapon, shield and armour
  kryptons strengthen the planet's defense and fleets starting from it; metal, crystal and gas
  kryptons raise the planet's mine output.
- **Krypton stones** come from beaten pirate bases (50 × level × (level + 1)) and the krypton
  merchant, who sells 1,000 stones for 10 dark matter (10 times a day) or 6,000 for 50
  (3 times). Dark matter is found on expeditions.
- **Krypton research** (levels 1-10) costs 20 × (level + 1) dark matter and takes
  (level + 1) hours / speed.

## Ship repair

As the client's help text says: when ship repair is switched on (the default), 50 % of the
ships a defender loses at a planet stay there as damaged ships. Repairing costs no resources:
each ship takes half its build time with the planet's robotics factory, all selected ships one
after another. Repaired ships wait in the dock until the player takes them over. Damaged ships
can also be dismantled for 30 % of their metal and crystal cost. Defense units are rebuilt as
before (70 %) and cannot be repaired.

## Newbie protection

As in OGame:

- Players below 5,000 points can only be attacked by other players below 5,000 points (and
  cannot attack stronger players).
- Nobody can attack a player with less than a fifth of their own points, or more than five
  times their points, unless the weaker player has at least 500,000 points.

Espionage, transport and pirate bases are not affected. Operators can change the limits with
`SUPERNOVA_NEWBIE_POINTS` and `SUPERNOVA_STRONG_POINTS`.

## Coordinated attacks

Every attack of an alliance member on another player can be joined by up to four other
members of the alliance (tab "coordinated attacks"). Joined fleets fly at their own speed but
arrive exactly with the attack; a fleet that cannot make it in time cannot join. The group
fights as one side, with the technologies and kryptons of the player who started the attack.
Surviving ships and the loot are shared in proportion to what each fleet sent. Newbie
protection applies to every participant. When the attacker recalls the attack, all joined
fleets turn around too.

## Pioneer Lab and Iron Behemoth

Following the client's help texts, with Supernova's numbers:

- The Pioneer Lab is built on a moon; an empire can have only one.
- An Iron Behemoth costs 250 alloy fragments and 50 × (number owned + 1) dark matter; it takes
  4 h × (1 + owned / 2) / speed. Weapons 5,500, shields 4,000, hull 350,000 (wiki values).
- Alloy fragments: 1 per 20,000 debris of every battle a player fights (+2 % per level of
  Alloy Detection Tech), or 250 in a box for dark matter (20, then +10 per box that day).
- Dark energy: only from battles fought with Behemoths, 1 per 10 points the enemy lost.
- Each Behemoth in a fleet adds 5 % attack to the whole fleet. A fleet may take
  1 + (Dynamic Power Tech level) Behemoths along.
- Pioneer techs cost 100 × (level + 1)² dark energy and 10 × (level + 1) dark matter and take
  (level + 1) h / speed; one at a time.

## ECM

- One ECM module per empire, levels 0–20. Each level adds 2 % weapons, shields and armour to all
  fleets and defense.
- A try costs 1 + level / 2 chips (rounded down). It succeeds with 50 % / (1 + level / 4) plus a
  share of the gathered fortune. A failure adds fortune; full fortune (5 + 5 × level) guarantees
  success. Fortune is lost at midnight UTC.
- Chips: 1 per pirate level from beaten pirate bases, or 5 dark matter each.

## System lords

- The owner with the most points in a solar system is its lord (from 1,000 points on).
- Rank = 1 + log2(points / 1,000), at most 21 (Grand Marshal).
- The lord's planets in that system get +rank % weapons, shields and armour and +2 × rank % mine
  output. Lords are worked out once a minute.

## Alliance treasury

- Members donate dark matter and credits; the president and vice presidents spend them on
  star fortress items. Every donation and payment is logged.

## Star fortresses

- Three fortresses (Orion, Draco, Hydra). Battles are every week / speed (default Sunday
  19:00 UTC, `SUPERNOVA_FORTRESS_BATTLE`); the last 2/7 of the cycle before a battle is the war
  phase.
- In the war phase the alliance leader declares war with a Gravity Field Beacon (100 dark
  matter from the treasury); the holder takes part without one. Members station fleets at the
  fortress (flight as over 5,000 distance units).
- Morale = stationed fleet points (metal + crystal) / 1,000 + Battle Songs (+50 for 20 dark
  matter, +200 for 50 credits) + 30 per won interspace battle.
- The two alliances with the most morale and stationed fleets fight; the holder defends. The
  one with more morale gets +10 % weapons, shields and armour. With only one alliance there,
  it takes the fortress without a fight. Afterwards all stationed fleets fly home.
- The holder's members get +10 % production of the fortress's resource (metal, crystal,
  deuterium), daily supplies (50,000 metal, 30,000 crystal, 10,000 deuterium, 5 ECM chips)
  and the fortress store (ECM chips, alloy boxes, krypton stones, resource crates).
- Interspace: guard fleets (pirates of level 3, 5, 7) that any alliance member can attack 3
  times a day, for supplies and, during a war, morale.

## Item shop and speed-ups

The catalog follows the wiki's Space Store: resource containers (S/M/L), speed-ups for dark
matter and the wiki's speed stones (1m to 24h) for credits, officers (tab "Officer"),
terraformers (S/M/L for 99/249/599 credits: 1-15/20/25 extra building fields, the best roll
counts), devices, ECM chips, alloy boxes (also for credits) and krypton stones. Credits cannot
be bought; the operator hands them out (`admin give <user> credits=500`).

Officers (wiki prices: level 1 for dark matter, 7 or 30 days; levels 2 and 3 for credits, 7 days):

| Officer | Effect (Lv.1 / 2 / 3) |
|---------|-----------------------|
| Commander | construction time -10 / -10 / -20 % |
| Engineer | fusion energy +10 / +10 / +20 %; saves 50 / 70 / 75 % of the defense that would be lost |
| Technocrat | research time -25 / -30 / -35 % |
| Geologist | mine output +10 / +20 / +30 % |
| Admiral | defense construction time -10 / -15 / -20 % |
| Guider | 35 / 45 / 55 % of the ships an expedition loses come back |

The wiki's extra building queues and fleet slots do not apply (Supernova has neither limit).

Gift packs (Supernova's own): Commander Pack (60 dark matter: 150,000 metal, 100,000 crystal,
50,000 deuterium, 10 ECM chips, 3 one-hour speed-ups) and Fleet Pack (199 credits: 2 Devourer
Interceptors, 1 Titanenergetic Frigate). They go into the capsule and are opened there.

Growth packages (client text: 1 growth point per 1,000 metal, crystal or deuterium spent, which is
the score): packages at 20, 100, 300, 1,000, 3,000, 10,000, 30,000 and 100,000 points with
resources, dark matter, ECM chips and items (Supernova's own). "Receive" collects all reached ones.

Chests (the client's Facebook chests, played with alliance members; all numbers Supernova's own):
the chest fills by itself, 1 point every 6 hours, and +1 for every alliance member who helps after
the player asked (each member once a day). It opens at 4 + level points and gives 30,000 / 20,000 /
8,000 metal / crystal / deuterium per level, ECM chips from level 3 and dark matter from level 5.
Every opening gives 1 chest experience; 2 x level experience raise the level (at most 10). The
extra bar fills with every help given (+1) and every player who joins with the player's
invitation code (+3); at 10 the next opening also gives 30 dark matter, 5 ECM chips, krypton
stones and a 1 h speed-up. The chests are in the menu at the bottom ("Chests").

Invitation codes: every player's code is `SN` + the player id (Settings > Invite friends). In
the first 7 days a new player can redeem one friend's code; both get 30 dark matter and the
new player 50,000 metal, 30,000 crystal and 10,000 deuterium.

- Everything in the shop costs dark matter; bought items go to the capsule (depot) and are
  used from there: resource packs (on the current planet), ECM chips, alloy fragment boxes,
  krypton stones.
- Speed-ups (5 min to 12 h) shorten a running construction, research or shipyard order. Dark
  matter finishes an order at once: 1 per 5 minutes left (at least 1).

## Dark matter

The original game sold dark matter for real money. Supernova hands it out instead:

- New players start with 200 (`SUPERNOVA_STARTING_DARK_MATTER`).
- The daily check-in gives dark matter on days 3, 5, 10, 12, 17 and 19 (20 to 80).
- A beaten pirate base gives 3 per pirate level; expeditions sometimes find 50 to 300.
- Optionally every player earns an income per day (`SUPERNOVA_DARK_MATTER_PER_DAY`, default 0).

## Ship modification

- Devices (wiki names: Ship Armor Plate, Energy Device, Power Adaptor, Engine System): Lv1
  for battlecruisers, bombers, destroyers and plasma cannons, Lv2 for the advanced ships and
  proton cannons. They come from expeditions (25 %: 1-5 devices), from battles a player wins
  against another player (3) and from the shop (2 / 6 dark matter).
- Level n of a device type takes 2^(n-1) devices and 100K, 150K, 200K, then +200K up to 1.4M
  (level 9), 1.8M, 2.2M, then +400K points (wiki up to level 11). Levels 1-9 add 10 % each,
  level 10 20 % and each further level 30 %, for that ship type only; at most level 20.
  Defense gets no engine systems.

## Advanced ships

Values from the Galaxy Empire wiki; the Reaper's are Supernova's own.

| Unit | Weapon | Shield | Hull | Speed | Cargo | Fuel | Special |
|------|-------:|-------:|-----:|------:|------:|-----:|---------|
| Devourer Interceptor (25) | 6,500 | 1,500 | 150,000 | 4,000 | 3,000 | 2,200 | shots pass through shields |
| Titanenergetic Frigate (26) | 5,000 | 3,000 | 450,000 | 4,000 | 4,000 | 2,500 | |
| Proton Cannon (27) | 5,000 | 5,000 | 120,000 | – | – | – | a fifth of the damage against units below 10,000 hull |
| Annihilator (28) | – | 1 | 1 | 9,000 | 1 | 1,000 | explodes before the battle |
| Reaper (29) | 8,000 | 3,000 | 400,000 | 5,000 | 3,000 | 2,000 | aims at the biggest ships, immune to Annihilators |

- **Annihilators** of the attacker explode one after the other before the battle, as long as the
  defender has at least two of: interceptors, Iron Behemoths, Devourer Interceptors,
  Titanenergetic Frigates. Each explosion does 500,000 damage (with weapons technology), shared
  equally by the defender's unit types; Titanenergetic Frigates take 5x, Devourers and
  interceptors 4x, Behemoths 3x. Each type loses damage / hull units, at least one. Defenders'
  Annihilators never explode, and a commander can have at most 200.
- Rapid fire (wiki): Devourer against light fighters 20, large cargo 10, interceptors and
  Behemoths 5; Frigate against large cargo 15, light fighters 10, destroyers, Devourers and
  Behemoths 5; Proton Cannon against interceptors, Devourers and Frigates 12.

## Missiles

Rules from the client (texts, order limits, battle report); costs and damage are Supernova's own.

- Both need Combined Guidance Tech 1 (research lab 10; 2,000 metal, 4,000 crystal, 600
  deuterium, doubling per level) and shipyard 4.
- **Intercept Missile** (30, defense; 8,000 metal, 2,000 deuterium) and **Interplanetary
  Missile** (31, flies with a fleet; 12,500 metal, 2,500 crystal, 10,000 deuterium). A planet
  holds, and a battle uses, at most 20 + 3 x level intercept missiles (up to 170) and 2 x level
  interplanetary missiles (up to 100).
- Before a battle, in this order:
  1. The defender's intercept missiles stop one attacking unit each: Annihilators first, then
     interplanetary missiles, Iron Behemoths, interceptors, Devourers, Frigates. Used ones are
     gone (not repaired like other defense).
  2. Interplanetary missiles fire: the attacker's always, the planet's with a 50 % chance. Each
     destroys one Reaper or, without Reapers, 1,000,000 hull points of the enemy's biggest ship
     type (at least one ship). They are used up.
  3. The attacker's Annihilators explode (see above).
- Missiles never take part in the battle rounds; unused ones stay or fly home.
- Battle reports now carry the client's full layout: loot (`booty`), intercepted units, missile
  hits, Annihilator explosions and intricate components for both sides.

## Daily tasks

Supernova's own; they start over every day (UTC) and each reward can be collected once a day.

| Task | Goal | Reward |
|------|------|--------|
| Upgrade buildings | start 3 building upgrades | 20,000 metal, 10,000 crystal |
| Research | start a research | 15,000 crystal, 5,000 deuterium |
| Build units | order 20 ships or defense units | 15,000 metal, 15,000 crystal |
| Fight pirates | attack 2 pirate bases | 3 ECM chips |
| Scout | send an espionage mission | 10,000 deuterium |
| Explore | send an expedition | 2 ECM chips, 10 dark matter |
| Salvage | send recyclers to a debris field | 15,000 metal, 15,000 crystal |
| Supply | transport resources | 8,000 deuterium |

## Radar, resource settings and fleets

- **Radar facility** (wiki): an attack shows up in the defender's flight list only while the
  attacked planet's radar is switched on and its level detects one of the attacking ship types:
  light fighter 1, probe 2, colony ship 4, small cargo 5, heavy fighter 7, recycler 8, large
  cargo 10, cruiser 11, battleship 12, battlecruiser 13, everything else 14. A switched-on radar
  uses 10 energy per level (Supernova's number).
- **Resource settings**: each mine and power plant runs at 0-100 % in steps of 10.
- **Quick finish**: the last minute of a construction can be skipped for free.
- **Returning fleets** can be brought home at once with a light speed engine (capsule, 10
  credits) or 1 credit per 5 minutes left.
- **Interceptors** are fused in the capsule from 150 galaxium each; from 1,000 owned on each
  costs 1 dark matter or 2 credits more (wiki). Galaxium comes from battles: 1 per 20,000 metal
  and crystal of enemy ships destroyed.

## Galaxy Challenge (Fight G.E.F.)

The operator starts it (`admin gc-start`); it follows the client's help texts and the wiki:

- 4 days, 96 battles of one hour. The president or a vice-president declares war; the 30
  members with the most power (wiki power per ship: light fighter 1, heavy fighter 3, cruiser
  10, battleship 24, battlecruiser 31, bomber 35, destroyer 46, interceptor 563, Iron Behemoth
  1,246, Devourer 650, Frigate 1,500) join. Up to 50 for 300 credits each. Others sign up
  themselves while there is room. Ships are not lost.
- Attack and defense are taken from all ships at sign-up (attack: weapons / 10; defense:
  (shields + hull / 10) / 10). Holding a star fortress gives +10 %, mission leaders +20 %.
- Energy: at most 1,000, 1 back per minute; 199 credits for 100, 1,990 for 1,000. A normal
  attack costs 25, a double strike 100 (200 % attack, 4x points and resources).
- Five G.E.F. fleets (50-140 % of the player's attributes) and an elite fleet (200 %); they get
  stronger with the player's win ratio. Wins give 10-120 points (+10 % per win in a row, up to
  +100 %), crystal ore, energy gel and titanenergetic cubes.
- Assistance: before attacking the elite fleet, a player can ask the other alliance members for
  help (50 credits); every accepted request gives +10 % attack against it (at most +100 %), the
  helper gets 20 crystal ore.
- Up to 3 mission leaders per battle (members apply, the president or vice-presidents choose).
  The G.E.F. attacks every 10 minutes: leaders with at least 10 courage points (bought for 100
  credits) defend, each using 10; otherwise a random member defends. A held attack gives 30
  (leader) or 20 points.
- After each battle: alliance points = members' points + 5 % per leader. More than the rival
  G.E.F. fleet (about 60 points per member) wins the battle and gives 20 % more.
- Exchange (wiki): Devourer Interceptor for 2,000 crystal ore and 500 energy gel, Titanenergetic
  Frigate for 3,500 crystal ore and 1,000 titanenergetic cubes; possible until 3 days after the
  end, then the resources decompose.
- Rewards when it ends (dark matter and ships on the home planet, with a mail): rank 1: 300 DM
  and 2 frigates, 2: 200 DM and a frigate, 3: 150 DM and 2 devourers, 4-10: 100 DM and a
  devourer, 11-50: 50 DM. Members of the best alliances: 200 DM and a devourer, 150, 100, 50 DM
  (ranks 1, 2, 3, 4-10).
- Event tasks (daily: 10 wins, 2 double strikes, 1 assistance, 500 points; alliance: 5 and 20
  won battles, 10,000 points) give event resources or dark matter. The "Recognition" shop sells
  resource crates for dark matter once a day.

All numbers the texts do not give are Supernova's own.

## Conquer The Universe (arena)

The operator starts it (`admin arena-start`); it follows the client's help texts. While it
runs, the star gate opens the arena instead of the star fortresses.

- Stages: garrison (default 24 h), challenge (6 days), prize (1 day).
- Ships: heavy fighters, cruisers, battleships, battlecruisers, bombers and destroyers of the
  current planet. Power = weapons + shields + a tenth of the hull of every ship.
- Garrison: puts these ships on guard; the first garrison gives the lowest rank. The garrison
  keeps its power when the ships leave. A new garrison must be stronger, else it costs 50
  credits.
- The rank list starts with 100 robot fleets (G.E.F. drones); players start below them.
- Challenge (challenge stage, needs a rank): attack one of 4 random higher ranks with the ships
  of the current planet. Basic attributes only (no research), no losses. A win swaps the places.
  3 challenges a day are free, 2 more cost 20 credits each; 10 minutes cool-down (1 credit per
  minute to skip); new opponents for 5 credits.
- Points: 20 per win, 5 per loss, plus prizes; at most 1,000,000, kept between events. They
  buy ECM chips, krypton stones, alloy boxes, 1 h speed-ups, Devourer Interceptors (1,500) and
  Titanenergetic Frigates (2,500) in the arena shop, each with a limit per event.
- Daily prize (every 24 h of the challenge stage, by rank at that time) and final prize (rank
  when the challenge stage ends), points / credits / dark matter:

| Rank | Daily | Final |
|------|-------|-------|
| 1 | 300 / - / 50 | 1,500 / 100 / 250 |
| 2 | 250 / - / 40 | 1,200 / 50 / 200 |
| 3 | 200 / - / 30 | 1,000 / 25 / 150 |
| 4-10 | 150 / - / 20 | 700 / - / 100 |
| 11-50 | 100 / - / 10 | 400 / - / 50 |
| 51-100 | 60 / - / 5 | 200 / - / 20 |
| below | 30 / - / 1 | 100 / - / 5 |

- Alliances (texts): every challenge gives the alliance 1 activist point (5 per member and
  day), a member's first win of a day 1 morale point. The next day 4 activist points become 1 %
  weapons and each morale point 1 % armour in arena battles (at most 80 and 20 points a day).
  The president and vice-presidents can buy 4 activist points or 1 morale point for 40 dark
  matter from the treasury. Members of an alliance with an activist bonus collect 20 points and
  5 dark matter a day.

All numbers the texts do not give are Supernova's own.
