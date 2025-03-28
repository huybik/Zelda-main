import pygame
from os import walk
from math import sin
from particles import AnimationPlayer
from magic import MagicPlayer
from tooltips import StatusBars
from support import get_distance_direction
import pygame
from entity import Entity
from settings import (
    monster_data,
    CHAT_INTERVAL,
    SUMMARY_INTERVAL,
    OBSERVATION_TO_SUMMARY,
    MEMORY_SIZE,
)
from debug import debug
import time
from tooltips import TextBubble, StatusBars
import json
import asyncio
from persona import Persona
from memstream import MemoryStream
from support import get_distance_direction, wave_value
import random
from priorityqueue import PriorityQueueWithUpdate
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
        visible_sprite,
        # api,
        global_queue: PriorityQueueWithUpdate,
    ):

        # general setup
        super().__init__(groups)
        self.sprite_type = "enemy"
        self.groups = groups
        self.memory = MemoryStream()
        self.persona = Persona()
        self.global_queue = global_queue

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
        self.visible_sprite = visible_sprite
        self.target_location = pygame.math.Vector2()
        self.current_speed = 0
        self.old_target_location = pygame.math.Vector2()
        self.tile_size = 64
        self.path = None

        # stats
        monster_info = monster_data[self.name]
        self.name = name
        self.full_name = full_name
        self.exp = monster_info["exp"]
        self.attack_type = monster_info["attack_type"]
        self.characteristic = monster_info["characteristic"]
        self.vigilant = 0

        # ChatGPT API params
        self.last_chat_time = 0
        self.last_summary_time = 0
        self.last_internal_move_update = 0

        self.chat_interval = CHAT_INTERVAL  # ticks
        self.summary_interval = SUMMARY_INTERVAL  # ticks
        self.internal_move_update_interval = 500  # ticks

        self.reason = None
        self.task_decision = None
        self.task_summary = None
        self.current_decision = None

        # cooldowns
        # self.observation_time = 0
        # self. OBSERVATION_TO_SUMMARY = OBSERVATION_COOLDOWN

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
        self.text_bubble = TextBubble(self.visible_sprite)
        self.status_bars = StatusBars(self.visible_sprite)

        # upgrade cost
        self.upgrade_cost = 100
        self.upgrade_percentage = 0.01  # this increase cost and stat

        # inventory
        self.timber = 0
        self.max_timber = 10

    def cooldown(self):
        # this cooldown to prevent attack animation spam
        current_time = pygame.time.get_ticks()

        if not self.can_act:
            if current_time - self.act_time >= self.act_cooldown:
                self.can_act = True
        # if current_time - self.observation_time >= self.observation_cooldown:
        #     self.can_save_observation = True
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

        # Tint the sprite red based on vigilant
        if self.vigilant > 0:
            tinted_image = self.image.copy()
            # Convert vigilant (0-100) to alpha value (0-255)
            alpha = min(255, int((self.vigilant / 100) * 255))
            # Increase red tint while decreasing green/blue as vigilant rises
            tinted_image.fill(
                (255, max(255 - alpha, 128), max(255 - alpha, 128)),
                special_flags=pygame.BLEND_RGBA_MULT,
            )
            self.image = tinted_image

    def attack(self, target: "Entity"):
        # set event name
        self.attack_sound.play()
        if target.sprite_type == "player" or target.sprite_type == "enemy":
            target.get_damage(attacker=self)

    def heal(self, target: "Entity"):
        if (
            self.energy > 0
            and target.health < target.max_health
            and self.energy > self.attack_damage
        ):
            self.heal_sound.play()

            # action
            self.energy -= self.attack_damage
            if self.energy <= 0:
                self.outside_event = f"out of energy to heal {target.full_name}"
                self.energy = 0

            target.get_heal(healer=self)

    def save_observation(
        self,
        player: "Player",
        entities: list["Enemy"],
        objects: list["Tile"],
    ):
        # self.can_save_observation = False
        self.observation_count += 1
        """Logs the enemy's memory including nearby entities and objects within notice radius."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        memory_entry = {
            "timestamp": timestamp,
            "self": self.observation_template(self),
            "nearby_entities": [],
            "nearby_objects": [],
        }

        # Get nearby entities within notice radius
        if entities:
            for other_entity in entities:
                if other_entity != self:
                    memory_entry["nearby_entities"].append(
                        self.observation_template(other_entity)
                    )
        distance, _ = get_distance_direction(self, player)

        # Get player
        if distance <= self.notice_radius:
            memory_entry["nearby_entities"].append(self.observation_template(player))

        # Get nearby objects within notice radius
        if objects:
            obj = min(
                objects,
                key=lambda obj: get_distance_direction(self, obj)[0],
                default=None,
            )

            memory_entry["nearby_objects"].append(
                {
                    "object_name": obj.full_name,
                    "location": {"x": obj.rect.centerx, "y": obj.rect.centery},
                }
            )

        filename = f"stream_{self.full_name}.json"
        self.memory.write_memory(memory_entry, filename, threshold=MEMORY_SIZE)

    def observation_template(self, entity: "Entity"):
        if entity.sprite_type == "player":
            health = f'{int(entity.health)}/{int(entity.stats["health"])}'
            energy = f'{int(entity.energy)}/{int(entity.stats["energy"])}'
        elif entity.sprite_type == "enemy":
            health = f"{int(entity.health)}/{int(entity.max_health)}"
            energy = f"{int(entity.energy)}/{int(entity.max_energy)}"

        observation = {
            "entity_name": entity.full_name,
            "action": entity.action,
            "target_name": entity.target_name,
            "observations": {
                "intention": entity.internal_event,
                "event": entity.outside_event,
                "observed": entity.observed_event,
            },
            "location": {"x": entity.rect.centerx, "y": entity.rect.centery},
            "stats": {
                "health": health,
                "energy": energy,
                "experience": int(entity.exp),
            },
        }

        if entity.target_location:
            observation["previous_moving_to"] = {
                "x": int(entity.target_location.x),
                "y": int(entity.target_location.y),
            }
        if entity.target_name:
            observation["previous_target_name"] = entity.target_name

        return observation

    def mine(self, target: "Tile"):

        # save status
        # action

        self.animation_player.create_particles(
            "sparkle", target.hitbox.center, [self.visible_sprite]
        )
        if self.timber >= self.max_timber:
            self.outside_event = (
                f"can't mine {target.full_name} because of full inventory"
            )
            self.timber = self.max_timber
        else:
            self.timber += 1

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
        self.outside_event = "respawn"

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
            self.vigilant = int(decision["vigilant"])
            self.action = decision["action"]
            self.reason = decision["reason"]

            if decision["target_name"] == "None":
                self.target_name = None

            # self.target_name = "None"
            # self.target_name = "player"
            # self.action = "runaway"

    def interaction(
        self, player: "Player", entities: list["Entity"], objects: list["Tile"]
    ):

        if self.persona.decision != self.current_decision:
            self.set_decision(self.persona.decision)
            self.current_decision = self.persona.decision

        if not self.target_name:
            current_time = pygame.time.get_ticks()
            if (
                current_time - self.last_internal_move_update
                >= self.internal_move_update_interval
            ):
                self.wander()
                self.last_internal_move_update = current_time
                self.internal_event = "wandering"
        else:
            target = self.target_select(player, entities, objects)
            if target:
                self.internal_event = f"{self.action} {target.full_name}"

                distance, _ = get_distance_direction(self, target)
                if distance <= self.notice_radius:
                    if distance > self.act_radius:

                        # internal action update
                        current_time = pygame.time.get_ticks()
                        if (
                            current_time - self.last_internal_move_update
                            >= self.internal_move_update_interval
                        ):
                            self.internal_move_update(target)
                            self.last_internal_move_update = current_time

                    elif distance <= self.act_radius and self.can_act:

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

                        for entity in entities:
                            if entity != self and entity != target:
                                entity.observed_event = (
                                    f"{self.full_name} {self.action} {target.full_name}"
                                )

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
        # walk aimlessly for a bit to find new target
        self.target_location = pygame.math.Vector2(
            random.randint(
                int(self.starting_point[0] - 100), int(self.starting_point[0] + 100)
            ),  # Random from starting location
            random.randint(
                int(self.starting_point[1] - 100), int(self.starting_point[1] + 100)
            ),  # Random from starting location
        )
        self.action = "move"

    def runaway(self, target):
        distance, direction = get_distance_direction(self, target)
        if distance != 0:
            self.direction = -direction.normalize()
        self.target_location = pygame.math.Vector2()

    def idle(self):
        self.action = "idle"
        self.direction = pygame.math.Vector2()
        self.target_location = pygame.math.Vector2()
        self.target_name = None
        self.reason = None
        # if self.status_bars:
        #     self.status_bars.kill()
        #     self.status_bars = None
        # if self.text_bubble:
        #     self.text_bubble.kill()
        #     self.text_bubble = None

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

    def decide(self, distance):
        if not self.task_decision:
            self.task_decision = self.persona.fetch_decision(self)
            self.global_queue.put(priority=distance, task=self.task_decision)

        elif not self.global_queue.has(self.task_decision):
            # update priority for old task
            self.task_decision = self.persona.fetch_decision(self)
            self.global_queue.put(distance, self.task_decision)

        elif self.global_queue.has(self.task_decision):
            # update priority for old task
            self.global_queue.put(distance, self.task_decision)

        # update priority

        print("Queue size:", self.global_queue.qsize())

    def summary(self, distance):
        if not self.task_summary:
            self.task_summary = self.persona.summary_context(self)
            self.global_queue.put(distance, self.task_summary)
        # if current_time - self.last_summary_time >= self.summary_interval or not self.task_summary:
        elif not self.global_queue.has(self.task_summary):
            # update priority for old task
            self.task_summary = self.persona.summary_context(self)
            self.global_queue.put(distance, self.task_summary)
        elif self.global_queue.has(self.task_summary):
            # update priority
            self.global_queue.put(distance, self.task_summary)

        print("Queue size:", self.global_queue.qsize())

    def update(self):
        # main update for sprite
        # if self.action == "move":

        self.animate()
        self.cooldown()
        self.flickering()
        self.upgrade()

    def check_death(self):
        if self.health <= 0:
            self.kill()
            self.status_bars.kill()
            self.text_bubble.kill()
            # self.respawn()

    def control_update(
        self,
        player: "Player",
        entities: list["Entity"],
        objects: list["Tile"],
        distance_player,
    ):

        # init

        if not self.first_observation:
            self.save_observation(player, entities, objects)
            self.first_observation = True

        if not self.task_decision:
            self.decide(distance_player)

        if not self.task_summary:
            self.summary(distance_player)

        # routine update
        if self.outside_event != self.old_outside_event:
            for entity in entities:
                if entity != self:
                    entity.observed_event = f"{self.full_name} {self.outside_event}"

        if (
            self.observed_event != self.old_observed_event
            or self.outside_event != self.old_outside_event
        ):
            self.old_observed_event = self.observed_event
            self.old_outside_event = self.outside_event

            self.save_observation(player, entities, objects)

            # set new action
            if self.observation_count >= MEMORY_SIZE:
                self.observation_count = 0
                self.summary(distance_player)

            self.decide(distance_player)

    def enemy_update(
        self, player: "Player", entities: list["Entity"], objects: list["Tile"]
    ):
        distance, _ = get_distance_direction(self, player)
        # knockback

        # shortlist entitiees and objects within notice radius
        # shortlist entities and objects within notice radius
        nearby_entities = [
            entity
            for entity in entities
            if get_distance_direction(self, entity)[0] <= self.notice_radius
            and entity != self
        ]
        nearby_objects = [
            obj
            for obj in objects
            if get_distance_direction(self, obj)[0] <= self.notice_radius
        ]

        if distance > self.notice_radius:
            # self.idle()
            pass
        else:
            pass
        self.interaction(player, nearby_entities, nearby_objects)
        # self.decide(distance)

        self.move(self.target_location, self.speed, objects)

        self.control_update(player, nearby_entities, nearby_objects, distance)

        self.check_death()

        # update tooltips
        self.status_bars.update_rect(
            self  # Max energy (if you want to add energy system later)
        )
        if self.reason:
            self.text_bubble.update_text(
                f"{self.action} {self.target_name}: {self.reason}", self.rect
            )


