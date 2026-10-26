'''
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
LOG_EVENTS = True
FLEE_DIST = 6
DASH_HEALTH_THRESHOLD = 0.35

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

    if LOG_EVENTS:
        print(
            f"MOVE_CALL round={cambc.round_number()} "
            f"pos=({origin.x},{origin.y}) "
            f"goal_count={len(list(goals.positions()))}"
        )

    if not me.can_move() or goals.is_empty():
        return False
    
    blocked = blocked_tiles()
    
    original_goals = cambc.Bitboard()
    original_goals |= goals

    
    goals &= (~blocked | cambc.vision.camp_tiles())

    if goals.is_set(origin):
        goals.set(origin, False)

    if goals.is_empty():
        if LOG_EVENTS:
            print(
                f"PATH_NO_VALID_GOALS round={cambc.round_number()} "
                f"pos=({origin.x},{origin.y})"
            )
        return False

    
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

    origin_dist = field.get(origin)
    finite_candidates = []

    for pos in reachable_positions:
        dist = field.get(pos)
        if dist != 65535 and dist < origin_dist:
            finite_candidates.append((dist, pos))

    if finite_candidates:
        finite_candidates.sort(key=lambda item: item[0])

        for best_dist, best_pos in finite_candidates:
            try:
                result = me.move_absolute(best_pos)

                if LOG_EVENTS:
                    print(
                        f"PATH_MOVE round={cambc.round_number()} "
                        f"from=({origin.x},{origin.y}) "
                        f"to=({best_pos.x},{best_pos.y}) "
                        f"dist={best_dist} "
                        f"result={result}"
                    )

                if result:
                    return True

            except cambc.Error as exc:
                if LOG_EVENTS:
                    print(
                        f"MOVE_REJECTED round={cambc.round_number()} "
                        f"from=({origin.x},{origin.y}) "
                        f"to=({best_pos.x},{best_pos.y}) "
                        f"error={exc}"
                    )

    if LOG_EVENTS:
        print(
            f"PATH_NO_PROGRESS round={cambc.round_number()} "
            f"origin=({origin.x},{origin.y}) "
            f"origin_dist={origin_dist}"
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

def fight_visible_enemies(me):
    enemies = [
        enemy
        for enemy in cambc.vision.champions(Team.ENEMY)
        if enemy.is_visible() and enemy.health() > 0
    ]

    # Attackable enemies first, then lower health, then closer.
    enemies.sort(
        key=lambda enemy: (
            not me.attack_tiles().is_set(enemy.position()),
            enemy.health(),
            distance(me.position(), enemy.position()),
        )
    )

    for enemy in enemies:
        if use_damage_ability_on_champion(enemy):
            return True

        if attack_if_in_range(enemy):
            return True

    return False

def use_escape_ability(me):
    pos = me.position()
    threats = []

    # Enemy champions are dangerous at close range.
    for enemy in cambc.vision.champions(Team.ENEMY):
        if enemy.is_visible() and enemy.health() > 0:
            threats.append((enemy.position(), FLEE_DIST))

    # Monoliths are threats; ordinary camps are not.
    for camp in cambc.vision.camps():
        if camp.health() <= 0 or not camp.is_monolith():
            continue

        radius = max(camp.attack_range(), FLEE_DIST)
        threats.append((camp.position(), radius))

    if not threats:
        return False

    def danger_score(tile):
        # Higher is safer: maximize distance beyond each threat's range.
        return min(
            distance(tile, threat_pos) - threat_range
            for threat_pos, threat_range in threats
        )

    current_score = danger_score(pos)

    # Only spend a dash when in danger and health is low.
    health_fraction = me.health() / max(
        1, me.base_stats().base_health
    )

    if current_score > 0:
        return False

    if health_fraction > DASH_HEALTH_THRESHOLD:
        return False

    for ability in me.abilities():
        if not ability.is_ready() or not ability.is_dash():
            continue

        try:
            tiles = ability.valid_tiles()
            blocked = blocked_tiles()

            candidates = [
                tile for tile in tiles.positions()
                if tile != pos
                and not blocked.is_set(tile)
                and danger_score(tile) > current_score
            ]

            if not candidates:
                continue

            candidates.sort(key=danger_score, reverse=True)
            if ability.use(candidates[0]):
                if LOG_EVENTS:
                    tile = candidates[0]
                    print(
                        f"ESCAPE_DASH round={cambc.round_number()} "
                        f"from=({pos.x},{pos.y}) "
                        f"to=({tile.x},{tile.y})"
                    )
                return True
        except cambc.Error:
            continue

    # If no safe dash is available, try a self-targeted shield or heal.
    health_fraction = me.health() / max(1, me.base_stats().base_health)
    for ability in me.abilities():
        if not ability.is_ready():
            continue
        if not (ability.is_shield() or ability.is_heal()):
            continue
        if not ability.can_target_self():
            continue

        if ability.is_heal() and health_fraction > 0.65:
            continue

        try:
            if ability.use(me):
                if LOG_EVENTS:
                    print(
                        f"DEFENSIVE_ABILITY round={cambc.round_number()} "
                        f"health={me.health()}"
                    )
                return True
        except cambc.Error:
            continue

    return False

def farm_camps(bot, avoid):
    me = cambc.me()
    pos = me.position()

    # Living monolith spawn tiles must never become farming targets.
    monolith_tiles = {
        camp.spawn_position()
        for camp in cambc.vision.camps()
        if camp.is_monolith() and camp.health() > 0
    }

    # Attack visible golems before ordinary camps.
    # Monoliths must never be attacked.
    camps = [
        camp for camp in cambc.vision.camps()
        if not camp.is_monolith() and camp.health() > 0
    ]
    camps.sort(key=lambda camp: not camp.is_golem())

    for camp in camps:
        if use_damage_ability_on_camp(camp):
            return
        if attack_if_in_range(camp):
            return

    visible = cambc.vision.visible_tiles()
    now = cambc.round_number()

    # Forget failed targets after a short cooldown.
    bot.failed_farm_targets = {
        tile: until
        for tile, until in bot.failed_farm_targets.items()
        if until > now
    }

    # Map visible golem spawn tiles for movement prioritisation.
    golem_tiles = {
        camp.spawn_position()
        for camp in cambc.vision.camps()
        if camp.is_golem() and camp.health() > 0
    }

    # Keep the current target if it is still a valid camp spawn tile.
    target = bot.farm_target
    target_valid = False

    if target is not None:
        if target in monolith_tiles:
            bot.farm_target = None
        else:
            for index, group in enumerate(game_map.camp_groups()):
                if not group.spawn_tiles().is_set(target):
                    continue

                if avoid.get(index, 0) > now:
                    break

                # Only check the camp's state when its spawn tiles are visible.
                if (group.spawn_tiles() & visible).is_empty():
                    break

                if isinstance(group.state(), game_map.CampGroupRespawning):
                    break

                target_valid = True
                break

    if not target_valid:
        bot.farm_target = None
        candidates = []

        for index, group in enumerate(game_map.camp_groups()):
            if avoid.get(index, 0) > now:
                continue

            # Do not query group.state() unless the group is visible.
            if (group.spawn_tiles() & visible).is_empty():
                continue

            if isinstance(group.state(), game_map.CampGroupRespawning):
                if LOG_EVENTS:
                    print(
                        f"CAMP_CHECK round={now} "
                        f"group={index} "
                        f"visible={not (group.spawn_tiles() & visible).is_empty()} "
                        f"respawning={isinstance(group.state(), game_map.CampGroupRespawning)}"
                    )
                continue

            for tile in group.spawn_tiles().positions():
                if tile == pos:
                    continue

                if tile in monolith_tiles:
                    continue

                if tile in bot.failed_farm_targets:
                    continue

                # Prefer golem spawn tiles, then choose the nearest target.
                is_golem_tile = tile in golem_tiles
                candidates.append((not is_golem_tile, distance(pos, tile), tile))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            bot.farm_target = candidates[0][2]

            if LOG_EVENTS:
                print(f"NEW_FARM_TARGET round={now} target={bot.farm_target}")

    target = bot.farm_target

    # Final safeguard against pursuing a living monolith spawn tile.
    if target in monolith_tiles:
        bot.farm_target = None
        target = None

    if target is None:
        bot.farm_target = None
        bot.explore(me)
        return

    bot.explore_target = None

    bot.failed_farm_targets = {
        tile: until
        for tile, until in bot.failed_farm_targets.items()
        if until > now
    }

    blocked = blocked_tiles()
    goals = cambc.Bitboard()

    if blocked.is_set(target):
        nearby = tiles_around(target, 1)
        nearby.set(target, False)

        for tile in nearby.positions():
            if not blocked.is_set(tile):
                goals.set(tile)
    else:
        goals.set(target)

    if goals.is_set(pos):
        return
    
    moved = move_towards_closest(goals)

    if LOG_EVENTS:
        print(
            f"FARM_TARGET round={now} "
            f"target=({target.x},{target.y}) "
            f"pos=({pos.x},{pos.y}) "
            f"moved={moved}"
        )

    if moved:
        bot.farm_target_failures = 0
    elif me.can_move():
        bot.farm_target_failures += 1

        if LOG_EVENTS:
            print(
                f"FARM_TARGET_BLOCKED round={now} "
                f"failures={bot.farm_target_failures}"
            )

        if bot.farm_target_failures >= 3:
            bot.failed_farm_targets[bot.farm_target] = now + 20
            bot.farm_target = None
            bot.farm_target_failures = 0


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
            and camp.is_monolith()
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

def intercept_enemy_recall(me):
    enemies = [
        enemy
        for enemy in cambc.vision.champions(Team.ENEMY)
        if enemy.is_visible()
        and enemy.health() > 0
        and enemy.is_recalling()
    ]

    if not enemies:
        return False

    # Deal damage immediately if possible.
    enemies.sort(
        key=lambda enemy: distance(me.position(), enemy.position())
    )

    for enemy in enemies:
        if use_damage_ability_on_champion(enemy):
            return True
        if attack_if_in_range(enemy):
            return True

    # Don't abandon our own valuable gem load to chase.
    if me.gem_count() > 0 or not me.can_move():
        return False

    # Approach an adjacent, unblocked tile instead of the
    # champion's occupied tile.
    enemy = enemies[0]
    goals = tiles_around(enemy.position(), 1)
    goals.set(enemy.position(), False)

    return move_towards_closest(goals)

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
            if LOG_EVENTS:
                print(
                    f"SUBMIT round={cambc.round_number()} "
                    f"pos=({me.position().x},{me.position().y}) "
                    f"altar=({altar.x},{altar.y}) "
                    f"gems={me.gem_count()} "
                    f"distance={distance(me.position(), altar)}"
                )
            me.submit_gems(altar)
            return True

    return False

class Bot:
    def __init__(self):
        self.chosen_stat = None
        self.avoid = {}
        self.visited = set()
        self.explore_target = None
        self.farm_target = None
        self.bank_target = None
        self.farm_target_failures = 0
        self.failed_farm_targets = {}

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
        if LOG_EVENTS:
            print(
                f"EXPLORE pos={me.position()} "
                f"target={self.explore_target} "
                f"farm_target={self.farm_target}"
            )

        # Keep pursuing the current target until reached or invalid.
        if self.explore_target is not None:
            if distance(pos, self.explore_target) > 2:
                goals = cambc.Bitboard()
                goals.set(self.explore_target)

                if move_towards_closest(goals):
                    return True

            self.explore_target = None

        # Choose a new camp spawn tile only when needed.

        candidates = []

        if not candidates:
            centre = cambc.Vec2(
                game_map.width() // 2,
                game_map.height() // 2,
            )
            candidates = [(distance(pos, centre), centre)]

        candidates.sort(key=lambda item: item[0])

        # Try several nearby targets instead of getting stuck
        # on the closest target when it is unreachable.
        for _, target in candidates:
            goals = cambc.Bitboard()
            goals.set(target)

            if move_towards_closest(goals):
                self.explore_target = target
                return True

        # No camp target made progress: try the map centre.
        centre = cambc.Vec2(
            game_map.width() // 2,
            game_map.height() // 2,
        )
        goals = cambc.Bitboard()
        goals.set(centre)

        if move_towards_closest(goals):
            self.explore_target = centre
            return True

        self.explore_target = None
        return False


    def play_turn(self):
        if LOG_EVENTS:
            me = cambc.me()
            print(
                f"TURN round={cambc.round_number()} "
                f"pos=({me.position().x},{me.position().y}) "
                f"alive={me.is_alive()} "
                f"gems={me.gem_count()} "
                f"can_move={me.can_move()} "
                f"can_attack={me.can_attack()}"
                f"id={int(me)} name={me.champion_name()} "
                f"pos=({me.position().x},{me.position().y}) "
            )

        if self.chosen_stat is None:
            self.chosen_stat = 0

        me = cambc.me()

        if (
            not me.is_alive()
            or me.is_recalling()
            or me.is_submitting_gems()
            or me.is_teleporting()
            or (not me.can_move() and not me.can_attack())
        ):
            return

        if use_escape_ability(me):
            return

        if me.health() < me.base_stats().base_health * 0.35:
            if threatened(me):
                if flee_to_spawn():
                    return
            else:
                me.recall()
                return

        # Interrupt visible enemy recalls when practical.
        if intercept_enemy_recall(me):
            return

        # Buy items while at the allied spawn.
        at_spawn = game_map.spawn_region(Team.ALLY).is_set(me.position())

        if at_spawn:
            self.buy_items()

        if LOG_EVENTS and me.gem_count() > 0:
            print(
                f"BANK round={cambc.round_number()} "
                f"gems={me.gem_count()} "
                f"pos=({me.position().x},{me.position().y})"
            )

        if me.gem_count() > 0:
            # Always bank immediately if already in submission range.
            if submit_if_in_range(me):
                self.bank_target = None
                return
    
            # Existing banking behaviour.
            altars = [
                altar
                for altar in game_map.altars().positions()
                if not cambc.vision.has_altar_lock(altar)
            ]

            if not altars:
                self.bank_target = None
                return

            if self.bank_target not in altars:
                self.bank_target = min(
                    altars,
                    key=lambda altar: distance(me.position(), altar),
                )

            goals = tiles_around(
                self.bank_target,
                cambc.constants.SUBMIT_GEMS_RANGE,
            )

            if LOG_EVENTS:
                            print(
                                f"BANK_TARGET round={cambc.round_number()} "
                                f"altar=({self.bank_target.x},{self.bank_target.y}) "
                                f"pos=({me.position().x},{me.position().y}) "
                                f"gems={me.gem_count()}"
                            )

            moved = move_towards_closest(goals)

            if not moved:
                if LOG_EVENTS:
                    print(
                        f"BANK_MOVE_FAILED round={cambc.round_number()} "
                        f"target={self.bank_target}"
                    )
                self.bank_target = None

            return
        
        if fight_visible_enemies(me):
            return

        if LOG_EVENTS:
            print("BEFORE FARM")

        farm_camps(self, self.avoid)

        if LOG_EVENTS:
            print("AFTER FARM")

        me.set_priority(1 + below(100))
'''


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
LOG_EVENTS = True
FLEE_DIST = 5
DASH_HEALTH_THRESHOLD = 0.35

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

    if LOG_EVENTS:
        print(
            f"MOVE_CALL round={cambc.round_number()} "
            f"pos=({origin.x},{origin.y}) "
            f"goal_count={len(list(goals.positions()))}"
        )

    if not me.can_move() or goals.is_empty():
        return False

    blocked = blocked_tiles()

    # Keep camp tiles as pathfinding goals: they are obstacles to enter, but
    # the distance field can still guide movement to a neighbouring tile.
    goals &= (~blocked | cambc.vision.camp_tiles())
    if goals.is_set(origin):
        goals.set(origin, False)

    if goals.is_empty():
        if LOG_EVENTS:
            print(
                f"PATH_NO_VALID_GOALS round={cambc.round_number()} "
                f"pos=({origin.x},{origin.y})"
            )
        return False

    if LOG_EVENTS:
        print(
            f"PATH after_filter={len(list(goals.positions()))}"
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

    origin_dist = field.get(origin)
    finite_candidates = []

    for pos in reachable_positions:
        dist = field.get(pos)
        if dist != 65535 and dist < origin_dist:
            finite_candidates.append((dist, pos))

    if finite_candidates:
        finite_candidates.sort(key=lambda item: item[0])

        # A destination can become obstructed after the map is observed.
        # Try the next best reachable tile rather than abandoning movement.
        for best_dist, best_pos in finite_candidates:
            try:
                result = me.move_absolute(best_pos)
            except cambc.Error as exc:
                if LOG_EVENTS:
                    print(
                        f"MOVE_REJECTED round={cambc.round_number()} "
                        f"to=({best_pos.x},{best_pos.y}) error={exc}"
                    )
                continue

            if LOG_EVENTS:
                print(
                    f"PATH_MOVE round={cambc.round_number()} "
                    f"from=({origin.x},{origin.y}) "
                    f"to=({best_pos.x},{best_pos.y}) "
                    f"dist={best_dist} result={result}"
                )
            if result:
                return True

    if LOG_EVENTS:
        print(
            f"PATH_NO_PROGRESS round={cambc.round_number()} "
            f"origin=({origin.x},{origin.y}) "
            f"origin_dist={origin_dist}"
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


def fight_visible_enemies(me):
    enemies = [
        enemy
        for enemy in cambc.vision.champions(Team.ENEMY)
        if enemy.is_visible() and enemy.health() > 0
    ]
    enemies.sort(
        key=lambda enemy: (
            not me.attack_tiles().is_set(enemy.position()),
            enemy.health(),
            distance(me.position(), enemy.position()),
        )
    )

    for enemy in enemies:
        if use_damage_ability_on_champion(enemy):
            return True
        if attack_if_in_range(enemy):
            return True
    return False


def use_escape_ability(me):
    pos = me.position()
    threats = []

    # Enemy champions are dangerous at close range.
    for enemy in cambc.vision.champions(Team.ENEMY):
        if enemy.is_visible() and enemy.health() > 0:
            threats.append((enemy.position(), FLEE_DIST))

    # Monoliths are threats; ordinary camps are not.
    for camp in cambc.vision.camps():
        if camp.health() <= 0 or not camp.is_monolith():
            continue

        radius = max(camp.attack_range(), FLEE_DIST)
        threats.append((camp.position(), radius))

    if not threats:
        return False

    def danger_score(tile):
        # Higher is safer: maximize distance beyond each threat's range.
        return min(
            distance(tile, threat_pos) - threat_range
            for threat_pos, threat_range in threats
        )

    current_score = danger_score(pos)

    # Only spend a dash when in danger and health is low.
    health_fraction = me.health() / max(
        1, me.base_stats().base_health
    )

    if current_score > 0:
        return False

    if health_fraction > DASH_HEALTH_THRESHOLD:
        return False

    for ability in me.abilities():
        if not ability.is_ready() or not ability.is_dash():
            continue

        try:
            tiles = ability.valid_tiles()
            blocked = blocked_tiles()
            candidates = [
                tile for tile in tiles.positions()
                if tile != pos
                and not blocked.is_set(tile)
                and danger_score(tile) > current_score
            ]
            if not candidates:
                continue

            candidates.sort(key=danger_score, reverse=True)
            if ability.use(candidates[0]):
                if LOG_EVENTS:
                    tile = candidates[0]
                    print(
                        f"ESCAPE_DASH round={cambc.round_number()} "
                        f"from=({pos.x},{pos.y}) "
                        f"to=({tile.x},{tile.y})"
                    )
                return True
        except cambc.Error:
            continue

    # If no safe dash is available, try a self-targeted shield or heal.
    health_fraction = me.health() / max(1, me.base_stats().base_health)
    for ability in me.abilities():
        if not ability.is_ready():
            continue
        if not (ability.is_shield() or ability.is_heal()):
            continue
        if not ability.can_target_self():
            continue

        if ability.is_heal() and health_fraction > 0.65:
            continue

        try:
            if ability.use(me):
                if LOG_EVENTS:
                    print(
                        f"DEFENSIVE_ABILITY round={cambc.round_number()} "
                        f"health={me.health()}"
                    )
                return True
        except cambc.Error:
            continue

    return False


def farm_camps(bot, avoid):
    me = cambc.me()
    pos = me.position()

    # Living monolith spawn tiles must never become farming targets.
    monolith_tiles = {
        camp.spawn_position()
        for camp in cambc.vision.camps()
        if camp.is_monolith() and camp.health() > 0
    }

    # Attack living golems before other ordinary camps; never attack monoliths.
    camps = [
        camp for camp in cambc.vision.camps()
        if not camp.is_monolith() and camp.health() > 0
    ]
    camps.sort(key=lambda camp: not camp.is_golem())

    for camp in camps:
        if use_damage_ability_on_camp(camp):
            return
        if attack_if_in_range(camp):
            return

    visible = cambc.vision.visible_tiles()
    now = cambc.round_number()

    # Forget failed targets after a short cooldown.
    bot.failed_farm_targets = {
        tile: until
        for tile, until in bot.failed_farm_targets.items()
        if until > now
    }

    # Keep the current target if it is still a valid camp spawn tile.
    target = bot.farm_target
    target_valid = False

    if target is not None:
        # Do not retain a target that is a living monolith spawn tile.
        if target in monolith_tiles:
            bot.farm_target = None
        else:
            for index, group in enumerate(game_map.camp_groups()):
                if not group.spawn_tiles().is_set(target):
                    continue

                if avoid.get(index, 0) > now:
                    break

                # Only check the camp's state when its spawn tiles are visible.
                if (group.spawn_tiles() & visible).is_empty():
                    break

                if isinstance(group.state(), game_map.CampGroupRespawning):
                    break

                target_valid = True
                break

    if not target_valid:
        bot.farm_target = None
        candidates = []
        golem_tiles = {
            camp.spawn_position()
            for camp in cambc.vision.camps()
            if camp.is_golem() and camp.health() > 0
        }

        for index, group in enumerate(game_map.camp_groups()):
            if avoid.get(index, 0) > now:
                continue

            # Do not query group.state() unless the group is visible.
            if (group.spawn_tiles() & visible).is_empty():
                continue

            if isinstance(group.state(), game_map.CampGroupRespawning):
                if LOG_EVENTS:
                    print(
                        f"CAMP_CHECK round={now} "
                        f"group={index} "
                        f"visible={not (group.spawn_tiles() & visible).is_empty()} "
                        f"respawning={isinstance(group.state(), game_map.CampGroupRespawning)}"
                        f"gold={group.gold_value()} "
                        f"gems={group.gem_value()} "
                        f"max_respawn={group.max_respawn_timer()} "
                        f"state={group.state()}"
                    )
                continue

            for tile in group.spawn_tiles().positions():
                if tile == pos:
                    continue

                # Do not select a living monolith spawn tile.
                if tile in monolith_tiles:
                    continue

                if tile in bot.failed_farm_targets:
                    continue

                candidates.append((tile not in golem_tiles, distance(pos, tile), tile))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            bot.farm_target = candidates[0][2]
            if LOG_EVENTS:
                print(f"NEW_FARM_TARGET round={now} target={bot.farm_target}")

    target = bot.farm_target

    # Final safeguard against pursuing a living monolith spawn tile.
    if target in monolith_tiles:
        bot.farm_target = None
        target = None

    if target is None:
        # No available camp: keep exploring, but don't retain a stale
        # farm target that can pull the champion back next action.
        bot.farm_target = None
        bot.explore(me)
        return

    bot.explore_target = None

    bot.failed_farm_targets = {
        tile: until
        for tile, until in bot.failed_farm_targets.items()
        if until > now
    }

    blocked = blocked_tiles()
    goals = cambc.Bitboard()

    if blocked.is_set(target):
        nearby = tiles_around(target, 1)
        nearby.set(target, False)
        goals = cambc.Bitboard()

        for tile in nearby.positions():
            if not blocked.is_set(tile):
                goals.set(tile)
    else:
        goals.set(target)

    if goals.is_set(pos):
        return

    moved = move_towards_closest(goals)

    if LOG_EVENTS:
        print(
            f"FARM_TARGET round={now} "
            f"target=({target.x},{target.y}) "
            f"pos=({pos.x},{pos.y}) "
            f"moved={moved}"
        )

    if moved:
        bot.farm_target_failures = 0
    elif me.can_move():
        bot.farm_target_failures += 1

        if LOG_EVENTS:
            print(
                f"FARM_TARGET_BLOCKED round={now} "
                f"failures={bot.farm_target_failures}"
            )

        if bot.farm_target_failures >= 3:
            bot.failed_farm_targets[bot.farm_target] = now + 20
            bot.farm_target = None
            bot.farm_target_failures = 0


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
            if LOG_EVENTS:
                print(
                    f"SUBMIT round={cambc.round_number()} "
                    f"pos=({me.position().x},{me.position().y}) "
                    f"altar=({altar.x},{altar.y}) "
                    f"gems={me.gem_count()} "
                    f"distance={distance(me.position(), altar)}"
                )
            me.submit_gems(altar)
            return True

    return False


class Bot:
    def __init__(self):
        self.chosen_stat = None
        self.avoid = {}
        self.visited = set()
        self.explore_target = None
        self.farm_target = None
        self.bank_target = None
        self.failed_bank_targets = {}
        self.farm_target_failures = 0
        self.failed_farm_targets = {}

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
        visible = cambc.vision.visible_tiles()

        # Remember explored tiles for fallback exploration when camps are exhausted.
        self.visited.update((tile.x, tile.y) for tile in visible.positions())

        if LOG_EVENTS:
            print(
                f"EXPLORE pos=({pos.x},{pos.y}) "
                f"target={self.explore_target} "
                f"farm_target={self.farm_target}"
            )

        # Continue a target only while it remains worth pursuing.
        if self.explore_target is not None:
            key = (self.explore_target.x, self.explore_target.y)
            if key not in self.visited and distance(pos, self.explore_target) > 2:
                goals = cambc.Bitboard()
                goals.set(self.explore_target)
                if move_towards_closest(goals):
                    return True
            self.explore_target = None

        # Prefer the original working strategy: travel toward camp spawn tiles.
        monolith_tiles = {
            camp.spawn_position()
            for camp in cambc.vision.camps()
            if camp.is_monolith() and camp.health() > 0
        }
        camp_candidates = []

        for group in game_map.camp_groups():
            # Query group.state() only when some spawn tile is visible.
            group_visible = not (group.spawn_tiles() & visible).is_empty()
            if group_visible and isinstance(group.state(), game_map.CampGroupRespawning):
                continue

            for tile in group.spawn_tiles().positions():
                if tile in self.failed_farm_targets or tile in monolith_tiles:
                    continue
                if distance(pos, tile) > 1:
                    camp_candidates.append((distance(pos, tile), tile))

        camp_candidates.sort(key=lambda item: item[0])
        for _, target in camp_candidates[:8]:
            goals = cambc.Bitboard()
            goals.set(target)
            if move_towards_closest(goals):
                self.explore_target = target
                return True

        # If no camp target is usable, explore toward a nearby unrevealed,
        # walkable tile instead of defaulting to the map centre.
        blocked = blocked_tiles()
        width, height = game_map.width(), game_map.height()
        frontier_candidates = []

        for y in range(height):
            for x in range(width):
                if (x, y) in self.visited:
                    continue
                target = cambc.Vec2(x, y)
                if blocked.is_set(target):
                    continue
                frontier_candidates.append((distance(pos, target), target))

        frontier_candidates.sort(key=lambda item: item[0])
        for _, target in frontier_candidates[:8]:
            goals = cambc.Bitboard()
            goals.set(target)
            if move_towards_closest(goals):
                self.explore_target = target
                return True

        self.explore_target = None
        return False

    def play_turn(self):
        if LOG_EVENTS:
            me = cambc.me()
            print(
                f"TURN round={cambc.round_number()} "
                f"pos=({me.position().x},{me.position().y}) "
                f"alive={me.is_alive()} "
                f"gems={me.gem_count()} "
                f"can_move={me.can_move()} "
                f"can_attack={me.can_attack()}"
                f"id={int(me)} name={me.champion_name()} "
                f"pos=({me.position().x},{me.position().y}) "
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

        if use_escape_ability(me):
            return

        # At critical health, retreat if possible; otherwise recall.
        if me.health() < me.base_stats().base_health * 0.35:
            if threatened(me) and flee_to_spawn():
                return
            me.recall()
            return

        # Buy items while at the allied spawn.
        at_spawn = game_map.spawn_region(Team.ALLY).is_set(me.position())

        if at_spawn:
            self.buy_items()

        if LOG_EVENTS and me.gem_count() > 0:
            print(
                f"BANK round={cambc.round_number()} "
                f"gems={me.gem_count()} "
                f"pos=({me.position().x},{me.position().y})"
            )

        if me.gem_count() > 0:
            if submit_if_in_range(me):
                self.bank_target = None
                return

            now = cambc.round_number()
            self.failed_bank_targets = {
                altar: until
                for altar, until in self.failed_bank_targets.items()
                if until > now
            }

            unlocked_altars = [
                altar for altar in game_map.altars().positions()
                if not cambc.vision.has_altar_lock(altar)
            ]
            if not unlocked_altars:
                self.bank_target = None
                return

            altars = [
                altar for altar in unlocked_altars
                if self.failed_bank_targets.get(altar, 0) <= now
            ]
            if not altars:
                # All unlocked altars are on cooldown; wait rather than retry
                # the same unreachable altar every turn.
                self.bank_target = None
                return

            if self.bank_target not in altars:
                self.bank_target = min(
                    altars,
                    key=lambda altar: distance(me.position(), altar),
                )

            goals = tiles_around(
                self.bank_target,
                cambc.constants.SUBMIT_GEMS_RANGE,
            )

            if LOG_EVENTS:
                print(
                    f"BANK_TARGET round={now} "
                    f"altar=({self.bank_target.x},{self.bank_target.y}) "
                    f"pos=({me.position().x},{me.position().y}) "
                    f"gems={me.gem_count()}"
                )

            # A temporary lack of movement is not evidence that an altar is
            # unreachable; retain the target and retry next turn.
            if not me.can_move():
                return

            moved = move_towards_closest(goals)
            if not moved:
                if LOG_EVENTS:
                    print(
                        f"BANK_MOVE_FAILED round={now} "
                        f"target={self.bank_target}"
                    )
                self.failed_bank_targets[self.bank_target] = now + 5
                self.bank_target = None
            return

        if fight_visible_enemies(me):
            return

        if LOG_EVENTS:
            print("BEFORE FARM")

        farm_camps(self, self.avoid)

        if LOG_EVENTS:
            print("AFTER FARM")

        me.set_priority(1 + below(100))



