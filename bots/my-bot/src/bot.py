
"""Starter bot + four switchable experiments.

Every switch below defaults to the starter's behaviour, so an unmodified copy of
this file should play like starter-python. Turn ONE experiment on at a time and
test it against the starter over many maps (sides swapped).
"""
import cambc
from cambc import game_map
from randomness import below

Team, Item = cambc.Team, cambc.ItemType

BANK_MIN_GEMS = 1
BANK_IF_HURT_FRAC = 0.6

RECALL_FRAC = 0.35
RECALL_FRAC_CARRYING = 0.35

AVOID_ROUNDS = 0

LOG_EVENTS = True

FLEE_DIST = 3

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

    if not me.can_move():
        print(
            f"MOVE r={cambc.round_number()} BLOCKED can_move=False pos={origin}",
            flush=True,
        )
        return False

    if goals.is_set(origin):
        print(
            f"MOVE r={cambc.round_number()} ALREADY_ON_GOAL pos={origin}",
            flush=True,
        )
        return False

    blocked = blocked_tiles()
    goals &= ~blocked | cambc.vision.camp_tiles()

    if goals.is_empty():
        print(f"MOVE r={cambc.round_number()} NO_GOALS pos={origin}", flush=True)
        return False

    field = cambc.DistanceField()
    field.compute_to(
        blocked & ~goals,
        goals,
        tiles_around(origin, 0),
        cambc.Bitboard(),
    )

    reachable = blocked.reachable_tiles(
        origin, me.base_stats().base_move_speed
    )
    
    candidates = [origin, *reachable.positions()]
    distances = [(field.get(p), p) for p in candidates]
    distances.sort(key=lambda item: item[0])

    best_dist, best_pos = distances[0]

    print(
        f"MOVE r={cambc.round_number()} pos={origin} "
        f"best={best_pos} dist={best_dist} "
        f"reachable={len(candidates)} goals={goals.count()}",
        flush=True,
    )

    if best_pos == origin or best_dist == 65535:
        print(f"MOVE r={cambc.round_number()} NO_VALID_STEP", flush=True)
        return False

    moved = me.move_absolute(best_pos)
    print(
        f"MOVE r={cambc.round_number()} move_absolute_result={moved}",
        flush=True,
    )
    return moved

def attack_if_in_range(target):
    # Make one basic attack if the target is already in attack range.
    me = cambc.me()
    if (not me.can_attack() or not target.is_visible() or target.health() <= 0
            or not me.attack_tiles().is_set(target.position())):
        return False
    print(
    f"ATTACK r={cambc.round_number()} "
    f"pos={me.position()} target={target} "
    f"camp_hp={target.health()} gems={me.gem_count()}",
    flush=True,
)
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
    print(
    f"CAMP r={cambc.round_number()} "
    f"pos={cambc.me().position()} "
    f"visible_camps={len(list(cambc.vision.camps()))} "
    f"gems={cambc.me().gem_count()}",
    flush=True,
)

    for camp in cambc.vision.camps():
        print(
            f"CHECK_ATTACK r={cambc.round_number()} camp={camp}",
            flush=True,
        )
        result = attack_if_in_range(camp)
        print(
            f"CHECK_RESULT r={cambc.round_number()} "
            f"camp={camp} result={result}",
            flush=True,
        )

        if result:
            print(
                f"AFTER_ATTACK_REACHED r={cambc.round_number()}",
                flush=True,
            )
            group = camp.camp_group()
            print(
            f"GROUP_CAMPS r={cambc.round_number()} "
            f"camps={[(str(c.kind()), c.spawn_position()) for c in group.camps()]}",
            flush=True,
            )
            print(
                f"AFTER_ATTACK r={cambc.round_number()} "
                f"gems={cambc.me().gem_count()} "
                f"group_state={group.state()} "
                f"group_gems={group.gem_value()}",
                flush=True,
            )
            return

    goals = cambc.Bitboard()
    visible = cambc.vision.visible_tiles()
    now = cambc.round_number()

    for index, group in enumerate(game_map.camp_groups()):
        try:
            state = group.state()
            print(
                f"GROUP r={now} id={index} state={state} "
                f"gems_reward={group.gem_value()} "
                f"camps={len(group.camps())}",
                flush=True,
            )
        except cambc.Error as exc:
            print(f"GROUP r={now} id={index} state_error={exc}", flush=True)

    for index, group in enumerate(game_map.camp_groups()):
        if avoid.get(index, 0) > now:
            continue

        if (
            not (group.spawn_tiles() & visible).is_empty()
            and isinstance(group.state(), game_map.CampGroupRespawning)
        ):
            continue

        goals |= group.spawn_tiles()

    # The current tile isn't useful as a destination if no camp is here.
    goals.set(cambc.me().position(), False)

    print(
    f"FARM r={cambc.round_number()} pos={cambc.me().position()} " 
    f"gems={cambc.me().gem_count()} goals={goals.count()}",
    flush=True,
    )

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

def submit_if_in_range(me):
    if me.gem_count() <= 0:
        return False

    for altar in game_map.altars().positions():
        if cambc.vision.has_altar_lock(altar):
            continue

        if distance(me.position(), altar) <= cambc.constants.SUBMIT_GEMS_RANGE:
            if LOG_EVENTS:
                print(
                    f"BANK r={cambc.round_number()} "
                    f"gems={me.gem_count()}",
                    flush=True
                )
            me.submit_gems(altar)
            return True

    return False

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
            if FLEE_DIST > 0 and threatened(me) and me.gem_count() == 0:
                if flee_to_spawn():
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

        if submit_if_in_range(me):
            return

        # Attack the first enemy champion already in range.
        for enemy in cambc.vision.champions(Team.ENEMY):
            if attack_if_in_range(enemy):
                return

        if me.gem_count() > 0 and wants_to_bank(me):
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

 
 

