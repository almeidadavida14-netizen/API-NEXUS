"""Application settings for the Treinamento FF Discord bot."""

from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "treinamento_ff.sqlite3"

# Accept either environment-variable name used by common hosting panels.
# Prefer DISCORD_TOKEN when both are present; fall back to BOT_TOKEN.
TOKEN_ENV_NAMES = ("DISCORD_TOKEN", "BOT_TOKEN")
TOKEN_ENV_NAME = " or ".join(TOKEN_ENV_NAMES)
BOT_TOKEN = (os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN") or "").strip() or None

# ID da conta autorizada a abrir e usar o painel privado do dono.
OWNER_USER_ID = 1090797688456482947

INFO_CATEGORY = "📁 INFORMAÇÕES"
QUEUE_CATEGORY = "📁 FILAS"
MATCH_CATEGORY = "🏆・PARTIDAS"
COMMUNITY_CATEGORY = "🌐・COMUNIDADE"

INFO_CHANNELS = {
    "boas_vindas": ("👋・boas-vindas", "welcome"),
    "anuncios": ("📢・anúncios", "announcements"),
    "regras": ("📜・regras", "rules"),
    "calendario": ("📅・calendário", "calendar"),
    "como_jogar": ("❓・como-jogar", "how_to_play"),
}

QUEUE_CHANNEL_NAME = "🎮・painel-de-filas"
COMMUNITY_CHANNELS = {
    "chat_geral": ("💬・chat-geral", "text"),
    "clipes_prints": ("📸・clipes-prints", "text"),
    "voz_geral": ("🔊・voz-geral", "voice"),
}

TEMPORARY_MESSAGE_SECONDS = 6 * 60

GAME_MODES = {
    "1v1": 2,
    "2v2": 4,
    "3v3": 6,
    "4v4": 8,
}

ALL_QUEUE_NAMES = tuple(GAME_MODES.keys())
STAFF_ROLE_NAMES = {"staff", "moderador", "moderadores", "admin", "administrador"}