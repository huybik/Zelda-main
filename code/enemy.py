import pygame
from entity import Entity
from settings import monster_data, output_format
from debug import debug
import time
from tooltips import TextBubble, StatusBars
import json
import asyncio
from persona import Persona
from memstream import MemoryStream
from support import get_distance_direction, wave_value
import random
from queue import PriorityQueue

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from entity import Entity
    from tile import Tile
    from player import Player


# from ui import UI


class Enemy(Entity):

    def __init__(
        self,
        name,
        full_name,
        pos,
        groups,
        obstacle_sprites,
    ):

        # general setup
        super().__init__(groups)
        self.sprite_type = "enemy"
        self.groups = groups
        self.memory = MemoryStream()
        self.persona = Persona()

        # graphic setup
        path = "../graphics/monsters/"
        self.animations = {
            "idle": [],
            "move": [],
            "attack": [],
            "heal": [],
            "mine": [],
            "runaway": [],
        }
        self.import_graphics(path, name, self.animations)

        self.action = "idle"
        self.image = self.animations[self.action][self.frame_index]
        self.rect = self.image.get_rect(topleft=pos)

        self.hitbox = self.rect
        self.starting_point = pos

        # movement
        self.obstacle_sprites = obstacle_sprites
        self.target_location = pygame.math.Vector2()
        self.current_speed = 0
        self.old_target_location = pygame.math.Vector2()
        self.tile_size = 64
        self.path = None

        # stats
        self.name = name
        monster_info = monster_data[self.name]
        self.monster_info = monster_info

        self.health = monster_info["health"]
        self.max_health = monster_info["health"]
        self.exp = monster_info["exp"]
        self.speed = monster_info["speed"]
        self.attack_damage = monster_info["damage"]
        self.resistance = monster_info["resistance"]
        self.act_radius = monster_info["act_radius"]
        self.notice_radius = monster_info["notice_radius"]
        self.attack_type = monster_info["attack_type"]
        self.characteristic = monster_info["characteristic"]
        self.full_name = full_name

        self.energy = self.max_health
        self.max_energy = self.max_health
        self.aggression = 0

        # ChatGPT API params
        self.last_chat_time = 0
        self.last_summary_time = 0
        self.last_internal_move_update = 0

        self.chat_interval = 6  # seconds
        self.summary_interval = 20  # seconds
        self.internal_move_update_interval = 0.5  # seconds

        self.reason = None

        self.decision_task = None
        self.summary_task = None

        self.current_decision = None
        self.event_status = None

        # cooldowns
        self.observation_time = 0
        self.observation_cooldown = 1000
        self.can_save_observation = True

        self.vulnerable = True
        self.vulnerable_time = 0
        self.vulnerable_duration = 1000

        self.act_time = 0
        self.act_cooldown = 1000
        self.can_act = True

        # sound
        self.attack_sound = pygame.mixer.Sound(monster_info["attack_sound"])
        self.attack_sound.set_volume(0.6)
        self.heal_sound = pygame.mixer.Sound("../audio/heal.wav")
        self.heal_sound.set_volume(0.6)

        # Add tooltips
        self.text_bubble = TextBubble([self.groups[0]])
        self.status_bars = StatusBars(self.groups[0])

        # upgrade cost
        self.upgrade_cost = 100
        self.upgrade_percentage = 0.01  # this increase cost and stat

    def cooldown(self):
        # this cooldown to prevent attack animation spam
        current_time = pygame.time.get_ticks()

        if not self.can_act:
            if current_time - self.act_time >= self.act_cooldown:
                self.can_act = True
        if current_time - self.observation_time >= self.observation_cooldown:
            self.can_save_observation = True
        if current_time - self.vulnerable_time >= self.vulnerable_duration:
            self.vulnerable = True

    def animate(self):
        animation = self.animations[self.animate_sequence]

        self.frame_index += self.animation_speed
        if self.frame_index >= len(animation):
            self.frame_index = 0
            if self.animate_sequence == self.action:
                # self.can_act = False
                self.animate_sequence = "move"
        self.image = animation[int(self.frame_index)]

        # Tint the sprite red based on aggression
        if self.aggression > 0:
            tinted_image = self.image.copy()
            # Convert aggression (0-100) to alpha value (0-255)
            alpha = min(255, int((self.aggression / 100) * 255))
            # Increase red tint while decreasing green/blue as aggression rises
            tinted_image.fill(
                (255, max(255 - alpha, 128), max(255 - alpha, 128)),
                special_flags=pygame.BLEND_RGBA_MULT,
            )
            self.image = tinted_image

    import pygame

    def move(
        self,
        target: pygame.math.Vector2,
        speed: int,
        objects: list = None,
        tile_size=64,
    ):
        current = pygame.math.Vector2(self.hitbox.centerx, self.hitbox.centery)

        def to_grid(pos, tile_size):
            return (int(pos.x // tile_size), int(pos.y // tile_size))

        def to_world(grid, tile_size):
            return pygame.math.Vector2(
                grid[0] * tile_size + tile_size / 2,
                grid[1] * tile_size + tile_size / 2,
            )

        def get_occupied_grids(rect, tile_size):
            """Returns all grid positions occupied by a rectangular obstacle."""
            grids = set()
            start_x = int(rect.left // tile_size)
            start_y = int(rect.top // tile_size)
            end_x = int(rect.right // tile_size)
            end_y = int(rect.bottom // tile_size)

            for x in range(start_x, end_x + 1):
                for y in range(start_y, end_y + 1):
                    grids.add((x, y))
            return grids

        def find_nearest_walkable(grid, obstacle_grids):
            # Check all 4-connected neighbors
            neighbors = [
                (grid[0] + dx, grid[1] + dy)
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]
            ]
            # Return the first valid neighbor that is not an obstacle
            for neighbor in neighbors:
                if neighbor not in obstacle_grids:
                    return neighbor
            return None  # No valid adjacent grid

        def astar_pathfinding(start, goal, obstacles, tile_size):
            start_grid = to_grid(start, tile_size)
            goal_grid = to_grid(goal, tile_size)

            # Mark all occupied grids
            obstacle_grids = set()
            for obj in obstacles:
                obstacle_grids.update(get_occupied_grids(obj, tile_size))

            # Adjust goal if it's in the obstacle grid
            if goal_grid in obstacle_grids:
                goal_grid = find_nearest_walkable(goal_grid, obstacle_grids)
                if not goal_grid:
                    return []  # No valid path available

            # Heuristic function (Manhattan distance)
            def heuristic(a, b):
                return abs(a[0] - b[0]) + abs(a[1] - b[1])

            # A* setup
            open_set = PriorityQueue()
            open_set.put((0, start_grid))
            came_from = {}
            g_score = {start_grid: 0}
            f_score = {start_grid: heuristic(start_grid, goal_grid)}

            while not open_set.empty():
                _, current_grid = open_set.get()

                if current_grid == goal_grid:
                    # Reconstruct path
                    path = []
                    while current_grid in came_from:
                        path.append(to_world(current_grid, tile_size))
                        current_grid = came_from[current_grid]
                    path.reverse()
                    return path

                # Explore neighbors
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    neighbor = (current_grid[0] + dx, current_grid[1] + dy)
                    if neighbor in obstacle_grids:
                        continue

                    tentative_g_score = g_score[current_grid] + 1
                    if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                        came_from[neighbor] = current_grid
                        g_score[neighbor] = tentative_g_score
                        f_score[neighbor] = tentative_g_score + heuristic(
                            neighbor, goal_grid
                        )
                        open_set.put((f_score[neighbor], neighbor))

            return []  # No path found

        if target and objects:
            if (
                to_grid(target, tile_size)
                != to_grid(self.old_target_location, tile_size)
                or not self.path
            ):
                self.old_target_location = target
                # Pathfinding: Calculate path if necessary
                self.path = astar_pathfinding(
                    current, target, [obj.hitbox for obj in objects], tile_size
                )
            # Follow the calculated path
            elif self.path:
                next_waypoint = pygame.math.Vector2(self.path[0])
                desired_direction = next_waypoint - current
                distance = desired_direction.magnitude()

                if distance < 3:  # Reached waypoint
                    self.path.pop(0)
                    if not self.path:  # Final waypoint reached\
                        self.direction = pygame.math.Vector2(0, 0)
                        return
                else:
                    desired_direction = desired_direction.normalize()
                    self.direction = desired_direction

        move_delta = self.direction * speed
        # Smooth movement and apply speed
        self.hitbox.x += move_delta.x
        self.collision("horizontal")
        self.hitbox.y += move_delta.y
        self.collision("vertical")
        self.rect.center = self.hitbox.center

    def check_collision(self, test_rect):
        for sprite in self.obstacle_sprites:
            if test_rect.colliderect(sprite.hitbox):
                return True
        return False

    def collision(self, direction):
        if direction == "horizontal":
            for sprite in self.obstacle_sprites:
                if sprite.hitbox.colliderect(self.hitbox):
                    if self.direction.x > 0:  # moving right
                        self.hitbox.right = sprite.hitbox.left
                    if self.direction.x < 0:  # moving left
                        self.hitbox.left = sprite.hitbox.right

        if direction == "vertical":
            for sprite in self.obstacle_sprites:
                if sprite.hitbox.colliderect(self.hitbox):
                    if self.direction.y > 0:  # moving down
                        self.hitbox.bottom = sprite.hitbox.top
                    if self.direction.y < 0:  # moving up
                        self.hitbox.top = sprite.hitbox.bottom

    def attack(self, target: "Entity"):
        # set event name
        self.attack_sound.play()
        if target.sprite_type == "player" or target.sprite_type == "enemy":
            target.get_damage(attacker=self)

    def heal(self, target: "Entity"):
        if self.energy > 0 and target.health < target.max_health:
            self.heal_sound.play()

            # action
            self.energy -= self.attack_damage
            if self.energy < 0:
                self.energy = 0

            target.get_heal(healer=self)

    def mine(self, target: "Tile"):

        # save status
        # action
        if self.energy < self.max_energy:
            self.energy += int(0.05 * self.max_energy)
            if self.energy > self.max_energy:
                self.energy = self.max_energy
        if self.health < self.max_health:
            self.health += int(0.05 * self.max_health)
            if self.health > self.max_health:
                self.health = self.max_health

        self.animation_player.create_particles(
            "sparkle", target.hitbox.center, [self.groups[0]]
        )
        self.exp += 10

    def respawn(self):
        self.rect.topleft = self.starting_point
        self.hitbox = self.rect
        self.health = self.monster_info["health"]
        self.max_health = self.monster_info["health"]
        self.exp = self.monster_info["exp"]
        self.speed = self.monster_info["speed"]
        self.attack_damage = self.monster_info["damage"]

        self.energy = self.max_health
        self.max_energy = self.max_health
        self.vulnerable = False

    def upgrade(self):
        if self.exp >= self.upgrade_cost:
            self.lvl += 1
            self.upgrade_cost += int(self.upgrade_cost * (1 + self.upgrade_percentage))
            self.max_health = int(self.max_health * (1 + self.upgrade_percentage))
            self.max_energy = int(self.max_energy * (1 + self.upgrade_percentage))
            self.speed = int(self.speed * (1 + self.upgrade_percentage))
            self.attack_damage = int(self.attack_damage * (1 + self.upgrade_percentage))

    def flickering(self):
        if not self.vulnerable:
            self.image.set_alpha(wave_value())
        else:
            self.image.set_alpha(255)

    def set_decision(self, decision):
        if decision:
            self.target_name = decision["target_name"]
            self.aggression = int(decision["aggression"])
            self.action = decision["action"]
            self.reason = decision["reason"]

            # self.target_name = "player"
            # self.action = "runaway"

    def interaction(
        self, player: "Player", entities: list["Entity"], objects: list["Tile"]
    ):

        if self.persona.decision != self.current_decision:
            self.set_decision(self.persona.decision)
            self.current_decision = self.persona.decision
        # self.set_decision(self.persona.decision)

        if not self.text_bubble:
            self.text_bubble = TextBubble(self.groups[0])
        if not self.status_bars:
            self.status_bars = StatusBars(self.groups[0])

        self.status_bars.update_rect(
            self  # Max energy (if you want to add energy system later)
        )

        target = self.target_select(player, entities, objects)
        if target:
            # update bubble

            distance, _ = get_distance_direction(self, target)
            if distance <= self.act_radius and self.can_act:

                if self.action == "attack":
                    self.attack(target)
                elif self.action == "heal":
                    self.heal(target)
                elif self.action == "mine":
                    self.mine(target)
                elif self.action == "runaway":
                    self.runaway(target)

                self.can_act = False
                self.animate_sequence = self.action
                self.act_time = pygame.time.get_ticks()

                event_status = f"{self.action} {target.full_name}"

                if self.event_status != event_status:
                    self.event_status = event_status
                    self.can_save_observation = True

            # internal action update
            current_time = pygame.time.get_ticks()
            if (
                current_time - self.last_internal_move_update
                >= self.internal_move_update_interval
            ):
                self.internal_move_update(target)
                self.last_internal_move_update = current_time
                # self.wander()

            if self.reason:
                self.text_bubble.update_text(
                    f"{self.action} {target.full_name}: {self.reason}", self.rect
                )

        # save observation
        self.save_observation(player, entities, objects)

        # if self.can_save_observation and any_key_pressed:

    def target_select(
        self, player: "Player", entities: list["Entity"], objects: list["Tile"]
    ):
        # chose target
        target = None
        if self.target_name == "player":
            target = player

        for entity in entities:
            if entity != self and entity.full_name == self.target_name:
                target = entity
                break
        for object in objects:
            if object.full_name == self.target_name:
                target = object
                break

        return target

        # Update status bars position to follow enemy

    def wander(self):
        # wander arround if no target
        if not self.target_name or self.target_name == "None":
            # walk aimlessly for a bit to find new target
            self.target_location = pygame.math.Vector2(
                random.randint(
                    int(self.hitbox.centerx - 100), int(self.hitbox.centerx + 100)
                ),  # Random from current location
                random.randint(
                    int(self.hitbox.centery - 100), int(self.hitbox.centery + 100)
                ),  # Random from current location
            )
            self.action = "move"

    def runaway(self, target):
        distance, direction = get_distance_direction(self, target)
        if distance != 0:
            self.direction = -direction.normalize()
        self.target_location = pygame.math.Vector2()

    def save_observation(self, player, entities, objects):
        # save observation
        if self.can_save_observation:
            self.memory.save_observation(self, player, entities, objects)
            self.observation_time = pygame.time.get_ticks()
            self.can_save_observation = False

    def idle(self):
        self.action = "idle"
        self.direction = pygame.math.Vector2()
        self.target_location = pygame.math.Vector2()
        self.target_name = None
        self.reason = None
        if self.status_bars:
            self.status_bars.kill()
            self.status_bars = None
        if self.text_bubble:
            self.text_bubble.kill()
            self.text_bubble = None

    def internal_move_update(self, target: "Entity"):

        if self.vulnerable:  # avoid moving when knockback in effect
            if self.action == "runaway":
                self.runaway(target)
            elif (
                self.action == "attack"
                or self.action == "mine"
                or self.action == "heal"
            ):
                self.target_location = pygame.math.Vector2(target.hitbox.center)
                # print(self.target_location)
                # + pygame.math.Vector2(random.randint(-1, 1), random.randint(-1, 1))

    async def enemy_decision(self, player, entities, objects):
        try:
            distance, _ = get_distance_direction(self, player)
            current_time = time.time()

            if distance <= self.notice_radius:
                if current_time - self.last_chat_time >= self.chat_interval:
                    # Add timeout to prevent hanging
                    if self.decision_task is None or self.decision_task.done():
                        self.decision_task = asyncio.create_task(
                            asyncio.wait_for(
                                self.persona.fetch_decision(self),
                                timeout=5.0,
                            )
                        )
                        self.last_chat_time = current_time

                if current_time - self.last_summary_time >= self.summary_interval:
                    if self.summary_task is None or self.summary_task.done():
                        self.summary_task = asyncio.create_task(
                            asyncio.wait_for(
                                self.persona.summary_context(self),
                                timeout=10.0,
                            )
                        )

                        self.last_summary_time = current_time

        except Exception as e:
            print(f"Enemy decision error: {e}")

    def update(self):
        # main update for sprite
        # if self.action == "move":

        self.animate()
        self.cooldown()
        self.flickering()
        self.upgrade()

    def enemy_update(
        self, player: "Player", entities: list["Entity"], objects: list["Tile"]
    ):
        distance, _ = get_distance_direction(self, player)
        # knockback

        if distance > self.notice_radius:
            self.idle()
        else:
            self.interaction(player, entities, objects)

        self.move(self.target_location, self.speed, objects)
