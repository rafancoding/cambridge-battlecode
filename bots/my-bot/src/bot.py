

"""Starter bot + four switchable experiments.

Every switch below defaults to the starter's behaviour, so an unmodified copy of
this file should play like starter-python. Turn ONE experiment on at a time and
test it against the starter over many maps (sides swapped).
"""
import cambc
from cambc import game_map
from randomness import below

Team, Item = cambc.Team, cambc.ItemType

# ---------------------------------------------------------------------------
# EXPERIMENT SWITCHES   (starter value -> suggested value to try)
# ---------------------------------------------------------------------------
# 1. Banking rule. Starter banks as soon as it holds any gem.
#    BANK_MIN_GEMS: bank once carrying at least this many gems (1 -> try 10..20).
#    BANK_IF_HURT_FRAC: below this health fraction, bank even a small amount
#    (0.0 -> try 0.6). Together: farm longer when healthy, cash in when in danger.
BANK_MIN_GEMS = 1
BANK_IF_HURT_FRAC = 0.0

# 2. Recall threshold (fraction of max health). Starter recalls below 0.35.
#    RECALL_FRAC_CARRYING applies only while holding gems (0.35 -> try 0.5).
RECALL_FRAC = 0.35
RECALL_FRAC_CARRYING = 0.35

# 3. After recalling, avoid the camp group you were nearest to for this many
#    rounds (0 = off -> try 150..300).
AVOID_ROUNDS = 0

# 4. Print DEATH / BANK / RECALL lines to the bot's stdout log.
LOG_EVENTS = True

# 5. Flee before recalling. If hurt and an enemy champion or camp monster is
#    within FLEE_DIST tiles, walk toward your spawn instead of calling recall
#    (0 = off = starter behaviour -> try 4). Recall only once nothing is close.
FLEE_DIST = 5
# ---------------------------------------------------------------------------


def distance(a, b):
    return a.chebyshev_distance(b)


def tiles_around(position, radius):
    tiles = cambc.Bitboard()
    tiles.draw_chebyshev(position, radius)
    return tiles


def blocked_tiles():
    # Combine terrain and visible occupants using native bitboard operations.
    blocked = (game_map.walls() | game_map.holes() | game_map.altars()
               | game_map.spawn_region(Team.ENEMY) | cambc.vision.champion_tiles()
               | cambc.vision.camp_tiles() | cambc.vision.teleport_tiles())
    blocked.set(cambc.me().position(), False)
    return blocked


def move_towards_closest(goals):
    me = cambc.me()
    origin = me.position()
    if not me.can_move() or goals.is_set(origin):
        return False

    # Only free goals or occupied camp tiles can seed the field.
    blocked = blocked_tiles()
    goals &= ~blocked | cambc.vision.camp_tiles()
    if goals.is_empty():
        return False
    if goals.count() == 1 and not blocked.is_set(goals.positions()[0]):
        return me.move_towards(goals.positions()[0])

    # Expand from the goals until our current tile is reached.
    field = cambc.DistanceField()
    field.compute_to(blocked & ~goals, goals, tiles_around(origin, 0), cambc.Bitboard())

    # Choose a reachable step with a smaller distance to the goal.
    reachable = blocked.reachable_tiles(origin, me.base_stats().base_move_speed)
    step = min([origin, *reachable.positions()], key=field.get)
    return step != origin and me.move_absolute(step)


def attack_if_in_range(target):
    # Make one basic attack if the target is already in attack range.
    me = cambc.me()
    if (not me.can_attack() or not target.is_visible() or target.health() <= 0
            or not me.attack_tiles().is_set(target.position())):
        return False
    me.attack(target)
    return True


def nearest_group_index(position):
    # Index of the camp group whose spawn tiles are closest to `position`.
    best, best_index = None, None
    for index, group in enumerate(game_map.camp_groups()):
        for tile in group.spawn_tiles().positions():
            d = distance(position, tile)
            if best is None or d < best:
                best, best_index = d, index
    return best_index


def farm_camps(avoid):
    # Attack a camp already in range, otherwise move toward the nearest camp tiles.
    for camp in cambc.vision.camps():
        if attack_if_in_range(camp):
            return

    # Union every group's spawn tiles except respawning or recently-avoided groups.
    goals = cambc.Bitboard()
    visible = cambc.vision.visible_tiles()
    now = cambc.round_number()
    for index, group in enumerate(game_map.camp_groups()):
        if avoid.get(index, 0) > now:
            continue
        if (not (group.spawn_tiles() & visible).is_empty()
                and isinstance(group.state(), game_map.CampGroupRespawning)):
            continue
        goals |= group.spawn_tiles()

    # Fresh vision after movement may reveal a camp already in range.
    if move_towards_closest(goals):
        for camp in cambc.vision.camps():
            if attack_if_in_range(camp):
                return


def threatened(me):
    # True if an enemy champion or a living camp monster is within FLEE_DIST.
    here = me.position()
    for enemy in cambc.vision.champions(Team.ENEMY):
        if enemy.health() > 0 and distance(here, enemy.position()) <= FLEE_DIST:
            return True
    for camp in cambc.vision.camps():
        if camp.health() > 0 and distance(here, camp.position()) <= FLEE_DIST:
            return True
    return False


def flee_to_spawn():
    goals = cambc.Bitboard()
    goals |= game_map.spawn_region(Team.ALLY)
    return move_towards_closest(goals)


def wants_to_bank(me):
    gems = me.gem_count()
    if gems <= 0:
        return False
    if gems >= BANK_MIN_GEMS:
        return True
    return me.health() < me.max_health() * BANK_IF_HURT_FRAC


class Bot:
    def __init__(self):
        # Per-champion state survives between turns.
        self.chosen_stat = None
        self.avoid = {}          # camp-group index -> round until which to skip it
        self.was_alive = True    # for death logging
        self.last_gems = 0
        self.last_health = 0

    def buy_items(self):
        # Spend only on power shards of the randomly chosen stat.
        me = cambc.me()
        item = (Item.POWER_SHARD_OF_STRENGTH, Item.POWER_SHARD_OF_FOCUS,
                Item.POWER_SHARD_OF_HEALTH)[self.chosen_stat]
        count = me.gold_count() // item.cost()
        if count and me.can_buy(item, count):
            me.buy(item, count)

    def log_death(self, me):
        # A champion that was alive last turn and is not now has died in between.
        alive = me.is_alive()
        if self.was_alive and not alive:
            print(f"DEATH r={cambc.round_number()} gems_carried={self.last_gems} "
                  f"hp_last_seen={self.last_health}", flush=True)
        self.was_alive = alive
        if alive:
            self.last_gems = me.gem_count()
            self.last_health = me.health()

    def play_turn(self):
        if self.chosen_stat is None:
            self.chosen_stat = below(3)

        me = cambc.me()
        if LOG_EVENTS:
            self.log_death(me)

        # Leave channels running and skip turns where we cannot act.
        if (not me.is_alive() or me.is_recalling() or me.is_submitting_gems()
                or me.is_teleporting() or (not me.can_move() and not me.can_attack())):
            return

        # Shop before leaving spawn.
        at_spawn = game_map.spawn_region(Team.ALLY).is_set(me.position())
        if at_spawn:
            self.buy_items()

        # Recall when badly hurt away from spawn (experiments 2 and 3).
        frac = RECALL_FRAC_CARRYING if me.gem_count() > 0 else RECALL_FRAC
        if not at_spawn and me.health() < me.max_health() * frac:
            # Experiment 5: recall seems to get interrupted while under attack,
            # so get away from the threat first.
            if FLEE_DIST > 0 and threatened(me) and flee_to_spawn():
                return
            if AVOID_ROUNDS > 0:
                group = nearest_group_index(me.position())
                if group is not None:
                    self.avoid[group] = cambc.round_number() + AVOID_ROUNDS
            result = me.recall()
            if LOG_EVENTS:
                print(f"RECALL r={cambc.round_number()} gems={me.gem_count()} "
                      f"hp={me.health()} result={result}", flush=True)
            return

        # Attack the first enemy champion already in range.
        for enemy in cambc.vision.champions(Team.ENEMY):
            if attack_if_in_range(enemy):
                return

        # Bank carried gems (experiment 1 decides when).
        if wants_to_bank(me):
            goals = cambc.Bitboard()
            for altar in game_map.altars().positions():
                if cambc.vision.has_altar_lock(altar):
                    continue
                if distance(me.position(), altar) <= cambc.constants.SUBMIT_GEMS_RANGE:
                    if LOG_EVENTS:
                        print(f"BANK r={cambc.round_number()} gems={me.gem_count()}",
                              flush=True)
                    me.submit_gems(altar)
                    return
                goals |= tiles_around(altar, cambc.constants.SUBMIT_GEMS_RANGE)
            move_towards_closest(goals)
            return

        farm_camps(self.avoid)

        me.set_priority(1 + below(100))


