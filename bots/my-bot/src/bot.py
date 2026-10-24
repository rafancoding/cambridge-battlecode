
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

LOG_EVENTS = False

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
    now = cambc.round_number()

    if not me.can_move():
        print(
            f"MOVE r={now} BLOCKED can_move=False pos={origin}",
            flush=True,
        )
        return False

    if goals.is_empty():
        print(f"MOVE r={now} NO_GOALS pos={origin}", flush=True)
        return False

    if goals.is_set(origin):
        print(f"MOVE r={now} ALREADY_ON_GOAL pos={origin}", flush=True)
        return False

    blocked = blocked_tiles()
    goals &= (~blocked | cambc.vision.camp_tiles())

    if goals.is_empty():
        print(f"MOVE r={now} NO_GOALS_AFTER_FILTER pos={origin}", flush=True)
        return False

    # First choice: use the normal distance-field pathfinding.
    field = cambc.DistanceField()
    field.compute_to(
        blocked & ~goals,
        goals,
        tiles_around(origin, 0),
        cambc.Bitboard(),
    )

    # Keep the SDK's existing reachable-tiles calculation.
    reachable = blocked.reachable_tiles(
        origin, me.base_stats().base_move_speed
    )
    reachable_positions = list(reachable.positions())
    print(
    f"PATH_TEST r={now} origin={origin} "
    f"origin_dist={field.get(origin)} "
    f"reachable={[(p, field.get(p)) for p in reachable_positions]}",
    flush=True,
)
    goal_positions = list(goals.positions())

    print(
        f"MOVE r={now} pos={origin} "
        f"reachable={len(reachable_positions)} "
        f"goals={len(goal_positions)}",
        flush=True,
    )

    # Ignore unreachable candidates when choosing the best path.
    finite_candidates = []
    for pos in reachable_positions:
        dist = field.get(pos)
        if dist != 65535:
            finite_candidates.append((dist, pos))

    if finite_candidates:
        finite_candidates.sort(key=lambda item: item[0])
        best_dist, best_pos = finite_candidates[0]

        print(
            f"MOVE r={now} PATH best={best_pos} dist={best_dist}",
            flush=True,
        )

        moved = me.move_absolute(best_pos)
        print(
            f"MOVE r={now} PATH_MOVE result={moved}",
            flush=True,
        )
        return moved

    # Fallback: the distance field found no reachable path.
    # Try a legal reachable tile that reduces distance to a goal.
    current_distance = min(
        distance(origin, goal) for goal in goal_positions
    )

    improving = []
    for pos in reachable_positions:
        heuristic = min(
            distance(pos, goal) for goal in goal_positions
        )
        if heuristic < current_distance:
            improving.append((heuristic, pos))

    if improving:
        improving.sort(key=lambda item: item[0])
        best_heuristic, best_pos = improving[0]

        print(
            f"MOVE r={now} FALLBACK "
            f"best={best_pos} heuristic={best_heuristic} "
            f"was={current_distance}",
            flush=True,
        )

        moved = me.move_absolute(best_pos)
        print(
            f"MOVE r={now} FALLBACK_MOVE result={moved}",
            flush=True,
        )
        return moved

    print(
        f"MOVE r={now} NO_PROGRESS "
        f"pos={origin} reachable={len(reachable_positions)} "
        f"goal_distance={current_distance}",
        flush=True,
    )
    return False


def attack_if_in_range(target):
    me = cambc.me()

    if (
        not me.can_attack()
        or not target.is_visible()
        or target.health() <= 0
        or not me.attack_tiles().is_set(target.position())
    ):
        return False

    if LOG_EVENTS:
        print(
            f"ATTACK r={cambc.round_number()} "
            f"pos={me.position()} target={target} "
            f"hp={target.health()} gems={me.gem_count()}",
            flush=True,
        )

    me.attack(target)
    return True


def use_damage_ability_on_camp(camp):
    me = cambc.me()

    for ability in me.abilities():
        if not ability.is_ready():
            continue
        if not (ability.is_damage() or ability.is_aoe_damage()):
            continue
        if not ability.can_target_camps():
            continue

        try:
            if not ability.valid_tiles().is_set(camp.position()):
                continue

            used = ability.use(camp)
            if used:
                print(
                    f"ABILITY_CAMP r={cambc.round_number()} "
                    f"slot={ability.slot()} camp={camp}",
                    flush=True,
                )
                return True
        except cambc.Error as exc:
            print(
                f"ABILITY_CAMP_ERROR r={cambc.round_number()} "
                f"slot={ability.slot()} error={exc}",
                flush=True,
            )

    return False

def nearest_group_index(position):
    # Index of the camp group whose spawn tiles are closest to `position`.
    best, best_index = None, None
    for index, group in enumerate(game_map.camp_groups()):
        for tile in group.spawn_tiles().positions():
            d = distance(position, tile)
            if best is None or d < best:
                best, best_index = d, index
    return best_index

def farm_camps(bot, avoid):
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

        if use_damage_ability_on_camp(camp):
            return

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
    goal_indices = []
    visible = cambc.vision.visible_tiles()
    now = cambc.round_number()

    for index, group in enumerate(game_map.camp_groups()):
        if avoid.get(index, 0) > now:
            continue

        # Only consider groups whose spawn area is currently visible.
        if (group.spawn_tiles() & visible).is_empty():
            continue

        state = group.state()
        if isinstance(state, game_map.CampGroupRespawning):
            continue

        goals |= group.spawn_tiles()
        goal_indices.append(index)

    goals.set(cambc.me().position(), False)

    print(
        f"FARM r={now} pos={cambc.me().position()} "
        f"gems={cambc.me().gem_count()} goals={goals.count()}",
        flush=True,
    )

    if not goals.is_empty():
        moved = move_towards_closest(goals)

        if moved:
            for camp in cambc.vision.camps():
                if attack_if_in_range(camp):
                    return
        elif cambc.me().can_move():
            # These goals are currently unreachable. Retry them later.
            for index in goal_indices:
                bot.avoid[index] = now + 50

            bot.explore(cambc.me())
    else:
        bot.explore(cambc.me())

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
        self.visited = set()
        self.explore_target = None
        self.last_position = None
        self.stuck_turns = 0

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

    def explore(self, me):
        pos = me.position()
        self.visited.add((pos.x, pos.y))

        # First target the centre of the map.
        centre = cambc.Vec2(
            game_map.width() // 2,
            game_map.height() // 2,
        )

        if self.explore_target is None:
            self.explore_target = centre

        if distance(pos, self.explore_target) <= 2:
            self.explore_target = None

        if self.explore_target is None:
            candidates = []

            for group in game_map.camp_groups():
                for tile in group.spawn_tiles().positions():
                    key = (tile.x, tile.y)
                    if key not in self.visited:
                        candidates.append((distance(pos, tile), tile))

            if candidates:
                candidates.sort(key=lambda item: item[0])
                self.explore_target = candidates[0][1]
            else:
                self.visited.clear()
                self.visited.add((pos.x, pos.y))
                self.explore_target = centre

        goals = cambc.Bitboard()
        goals.set(self.explore_target)

        print(
            f"EXPLORE r={cambc.round_number()} pos={pos} "
            f"target={self.explore_target} visited={len(self.visited)}",
            flush=True,
        )

        moved = move_towards_closest(goals)

        if not moved and me.can_move():
            self.explore_target = None
 
    def play_turn(self):
        if self.chosen_stat is None:
            self.chosen_stat = 2

        me = cambc.me()

        if cambc.round_number() <= 3:
            for ability in me.abilities():
                info = ability.info()
                print(
                    f"ABILITY_DEBUG round={cambc.round_number()} "
                    f"slot={ability.slot()} "
                    f"type={type(info).__name__ if info is not None else 'EMPTY'} "
                    f"ready={ability.is_ready()} "
                    f"damage={ability.is_damage()} "
                    f"aoe={ability.is_aoe_damage()} "
                    f"enemy_target={ability.can_target_enemies()} "
                    f"camp_target={ability.can_target_camps()} "
                    f"ground_target={ability.can_target_ground()}"
                )

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

        if me.gem_count() > 0:
            if submit_if_in_range(me):
                return

            goals = cambc.Bitboard()
            for altar in game_map.altars().positions():
                if cambc.vision.has_altar_lock(altar):
                    continue
                goals |= tiles_around(
                    altar, cambc.constants.SUBMIT_GEMS_RANGE
                )

            if not goals.is_empty():
                move_towards_closest(goals)
            return

        frac = RECALL_FRAC
        if (
                    not at_spawn
                    and me.gem_count() > 0
                    and me.health() < me.max_health() * frac
            ):
            if FLEE_DIST > 0 and threatened(me):
                if flee_to_spawn():
                    return
                    
                result = me.recall()
                if LOG_EVENTS:
                    print(
                                f"RECALL r={cambc.round_number()} "
                                f"gems={me.gem_count()} hp={me.health()} "
                                f"result={result}",
                                flush=True,
                    )
                    return

        # Submit if already in range, even when carrying no gems
        # (this is harmless and keeps the action ordering clear).
        if submit_if_in_range(me):
            return

        # Attack the first enemy champion already in range.
        for enemy in cambc.vision.champions(Team.ENEMY):
            if attack_if_in_range(enemy):
                return

         # EXPERIMENT: cast a ready damage ability at a visible enemy.
            for ability in me.abilities():
                if not ability.is_ready():
                    continue
                if not (ability.is_damage() or ability.is_aoe_damage()):
                    continue
                if not ability.can_target_enemies():
                    continue
        
                for enemy in cambc.vision.champions(Team.ENEMY):
                    if enemy.health() <= 0:
                        continue
        
                    try:
                        if ability.use(enemy):
                            print(
                                f"ABILITY_USED slot={ability.slot()} "
                                f"round={cambc.round_number()}"
                            )
                            return
                    except Exception as exc:
                        print(
                            f"ABILITY_FAILED slot={ability.slot()} "
                            f"round={cambc.round_number()} error={exc}"
                        )

        if me.gem_count() > 0:
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
        
        farm_camps(self, self.avoid)

        me.set_priority(1 + below(100))