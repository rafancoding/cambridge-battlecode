
"""Restored best-known version of the Battlecode bot."""

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
FLEE_DIST = 5

def distance(a, b):
    return a.chebyshev_distance(b)


def tiles_around(position, radius):
    tiles = cambc.Bitboard()
    tiles.draw_chebyshev(position, radius)
    return tiles


def blocked_tiles():
    blocked = (
        game_map.walls()
        | game_map.holes()
        | game_map.altars()
        | game_map.spawn_region(Team.ENEMY)
        | cambc.vision.champion_tiles()
        | cambc.vision.camp_tiles()
        | cambc.vision.teleport_tiles()
    )
    blocked.set(cambc.me().position(), False)
    return blocked

def move_towards_closest(goals):
    me = cambc.me()
    origin = me.position()

    if not me.can_move() or goals.is_empty() or goals.is_set(origin):
        return False

    blocked = blocked_tiles()
    original_goals = cambc.Bitboard()
    original_goals |= goals

    goals &= (~blocked | cambc.vision.camp_tiles())

    if goals.is_empty():
        if LOG_EVENTS:
            print("PATH: filter removed all goals; using original goals")
        goals = original_goals

    if LOG_EVENTS:
        print(
            f"PATH after_filter={len(list(goals.positions()))}"
        )
        if goals.is_empty():
            return False

    if LOG_EVENTS:
        print(
            f"GOALS round={cambc.round_number()} "
            f"origin=({origin.x},{origin.y}) "
            f"goal_count={len(list(goals.positions()))}"
        )

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
    reachable_positions = list(reachable.positions())
    goal_positions = list(goals.positions())

    if LOG_EVENTS:
        print(
            f"PATH_DEBUG reachable={len(reachable_positions)} "
            f"goals={[(p.x, p.y) for p in goal_positions]} "
            f"speed={me.base_stats().base_move_speed}"
        )

    finite_candidates = []
    if LOG_EVENTS:
        print(f"PATH_CHECK origin_dist={field.get(origin)} "
            f"reachable={[(p.x, p.y, field.get(p)) for p in reachable_positions]}")
    for pos in reachable_positions:
        dist = field.get(pos)
        if dist != 65535:
            finite_candidates.append((dist, pos))

    if finite_candidates:
        finite_candidates.sort(key=lambda item: item[0])
        best_dist, best_pos = finite_candidates[0]

        if LOG_EVENTS:
            print(
                f"PATH round={cambc.round_number()} "
                f"from=({origin.x},{origin.y}) "
                f"to=({best_pos.x},{best_pos.y}) "
                f"dist={best_dist} "
                f"candidates={len(finite_candidates)}"
            )

        result = me.move_absolute(best_pos)

        if LOG_EVENTS:
            print(f"PATH_MOVE result={result}")

        return result

    # Fallback if the distance field cannot find a reachable route.
    if reachable_positions:
        reachable_positions.sort(
            key=lambda pos: min(distance(pos, goal) for goal in goal_positions)
        )

        best_pos = reachable_positions[0]

        if best_pos != origin:
            if LOG_EVENTS:
                print(
                    f"PATH_FALLBACK from=({origin.x},{origin.y}) "
                    f"to=({best_pos.x},{best_pos.y})"
                )
            return me.move_absolute(best_pos)

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

            if ability.use(camp):
                return True
        except cambc.Error:
            continue

    return False


def use_damage_ability_on_champion(enemy):
    me = cambc.me()

    for ability in me.abilities():
        if not ability.is_ready():
            continue

        if not (ability.is_damage() or ability.is_aoe_damage()):
            continue

        if not ability.can_target_enemies():
            continue

        try:
            if not ability.valid_tiles().is_set(enemy.position()):
                continue

            if ability.use(enemy):
                return True
        except cambc.Error:
            continue

    return False

def farm_camps(bot, avoid):
    # Use damage abilities and basic attacks against visible camps.
    for camp in cambc.vision.camps():
        if use_damage_ability_on_camp(camp):
            return

        if attack_if_in_range(camp):
            return

    goals = cambc.Bitboard()
    goal_indices = []
    visible = cambc.vision.visible_tiles()
    now = cambc.round_number()

    for index, group in enumerate(game_map.camp_groups()):
        if avoid.get(index, 0) > now:
            continue

        if (group.spawn_tiles() & visible).is_empty():
            continue

        if isinstance(group.state(), game_map.CampGroupRespawning):
            continue

        goals |= group.spawn_tiles()
        goal_indices.append(index)

    goals.set(cambc.me().position(), False)

    if LOG_EVENTS:
        print(
            f"FARM round={now} "
            f"visible_camps={len(list(cambc.vision.camps()))} "
            f"goals={len(list(goals.positions()))}"
        )

    if not goals.is_empty():
        moved = move_towards_closest(goals)

        if LOG_EVENTS:
            print(f"FARM_MOVE moved={moved}")

        if moved:
            for camp in cambc.vision.camps():
                if attack_if_in_range(camp):
                    return

        elif cambc.me().can_move():
            for index in goal_indices:
                bot.avoid[index] = now + 50

            bot.explore(cambc.me())
    else:
        bot.explore(cambc.me())


def threatened(me):
    here = me.position()

    for enemy in cambc.vision.champions(Team.ENEMY):
        if (
            enemy.health() > 0
            and distance(here, enemy.position()) <= FLEE_DIST
        ):
            return True

    for camp in cambc.vision.camps():
        if (
            camp.health() > 0
            and distance(here, camp.position()) <= FLEE_DIST
        ):
            return True

    return False


def flee_to_spawn():
    goals = cambc.Bitboard()
    goals |= game_map.spawn_region(Team.ALLY)
    return move_towards_closest(goals)

def escape_then_recall(me):
    # First move away from nearby threats.
    if threatened(me):
        if flee_to_spawn():
            return

    # Recall only after creating some distance.
    me.recall()

def submit_if_in_range(me):
    if me.gem_count() <= 0:
        return False

    for altar in game_map.altars().positions():
        if cambc.vision.has_altar_lock(altar):
            continue

        if (
            distance(me.position(), altar)
            <= cambc.constants.SUBMIT_GEMS_RANGE
        ):
            me.submit_gems(altar)
            return True

    return False


class Bot:
    def __init__(self):
        self.chosen_stat = None
        self.avoid = {}
        self.visited = set()
        self.explore_target = None

    def buy_items(self):
        me = cambc.me()

        item = (
            Item.POWER_SHARD_OF_STRENGTH,
            Item.POWER_SHARD_OF_FOCUS,
            Item.POWER_SHARD_OF_HEALTH,
        )[self.chosen_stat]

        count = me.gold_count() // item.cost()

        if count and me.can_buy(item, count):
            me.buy(item, count)

    def explore(self, me):
        pos = me.position()

        # Keep pursuing the current target until reached or invalid.
        if self.explore_target is not None:
            if distance(pos, self.explore_target) > 2:
                goals = cambc.Bitboard()
                goals.set(self.explore_target)

                if move_towards_closest(goals):
                    return True

            self.explore_target = None

        # Choose a new camp spawn tile only when needed.
        visible = cambc.vision.visible_tiles()
        candidates = []

        for group in game_map.camp_groups():
            if (
                not (group.spawn_tiles() & visible).is_empty()
                and isinstance(group.state(), game_map.CampGroupRespawning)
            ):
                continue

            for tile in group.spawn_tiles().positions():
                if distance(pos, tile) > 2:
                    candidates.append(tile)

        if not candidates:
            centre = cambc.Vec2(
                game_map.width() // 2,
                game_map.height() // 2,
            )
            self.explore_target = centre
        else:
            candidates.sort(key=lambda tile: distance(pos, tile))
            self.explore_target = candidates[0]

        goals = cambc.Bitboard()
        goals.set(self.explore_target)
        return move_towards_closest(goals)

    def play_turn(self):
        if LOG_EVENTS:
            me = cambc.me()
            print(
                f"TURN round={cambc.round_number()} "
                f"pos=({me.position().x},{me.position().y}) "
                f"alive={me.is_alive()} "
                f"can_move={me.can_move()} "
                f"can_attack={me.can_attack()}"
            )

        if self.chosen_stat is None:
            self.chosen_stat = 2

        me = cambc.me()

        if (
            not me.is_alive()
            or me.is_recalling()
            or me.is_submitting_gems()
            or me.is_teleporting()
            or (not me.can_move() and not me.can_attack())
        ):
            return

        # Recall to spawn when critically wounded.
        if me.health() < me.base_stats().base_health * 0.35:
            if threatened(me):
                if flee_to_spawn():
                    return
            else:
                me.recall()
                return

        # Protect carried gems from nearby threats.
        if me.gem_count() > 0 and threatened(me):
            if flee_to_spawn():
                return

        # Buy items while at the allied spawn.
        at_spawn = game_map.spawn_region(Team.ALLY).is_set(me.position())

        if at_spawn:
            self.buy_items()

        # Banking takes priority whenever the champion carries gems.
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

        for enemy in cambc.vision.champions(Team.ENEMY):
            if not enemy.is_visible() or enemy.health() <= 0:
                continue

            if use_damage_ability_on_champion(enemy):
                return

            if attack_if_in_range(enemy):
                return

        if LOG_EVENTS:
            print("BEFORE FARM")

        farm_camps(self, self.avoid)

        if LOG_EVENTS:
            print("AFTER FARM")

        me.set_priority(1 + below(100))
