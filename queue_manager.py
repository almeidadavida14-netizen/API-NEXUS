"""Queue and random team formation rules."""

from __future__ import annotations

import random

from config import GAME_MODES
from database import Database


class QueueManager:
    def __init__(self, database: Database):
        self.database = database
        self.random = random.SystemRandom()

    def join(self, guild_id: int, user_id: int, queue_name: str) -> tuple[bool, str | None]:
        if queue_name not in tuple(GAME_MODES.keys()):
            raise ValueError("Fila desconhecida")
        return self.database.join_queue(guild_id, user_id, queue_name)

    def leave(self, guild_id: int, user_id: int, queue_name: str | None = None) -> str | None:
        return self.database.leave_queue(guild_id, user_id, queue_name)

    def status(self, guild_id: int) -> dict[str, int]:
        return {
            queue_name: len(self.database.list_queue(guild_id, queue_name))
            for queue_name in tuple(GAME_MODES.keys())
        }

    def ready_modes(self, guild_id: int) -> list[str]:
        return [
            mode
            for mode, required in GAME_MODES.items()
            if len(self.database.list_queue(guild_id, mode)) >= required
        ]

    def take_ready_players(self, guild_id: int, mode: str) -> list[int]:
        return self.database.pop_queue(guild_id, mode, GAME_MODES[mode])

    def split_teams(self, players: list[int]) -> tuple[list[int], list[int]]:
        shuffled = list(players)
        self.random.shuffle(shuffled)
        midpoint = len(shuffled) // 2
        return shuffled[:midpoint], shuffled[midpoint:]