"""Idempotent setup of the server structure and informational content."""

from __future__ import annotations

import discord
import logging

from config import (
    COMMUNITY_CATEGORY,
    COMMUNITY_CHANNELS,
    INFO_CATEGORY,
    INFO_CHANNELS,
    MATCH_CATEGORY,
    QUEUE_CATEGORY,
    QUEUE_CHANNEL_NAME,
    STAFF_ROLE_NAMES,
)
from database import Database
from views import QueuePanelView

logger = logging.getLogger("treinamento_ff.server_setup")


WELCOME_TEXT = (
    "🔥 **BEM-VINDO AO TREINAMENTO FF!**\n\n"
    "Aqui você pode encontrar jogadores e participar de treinos e partidas organizadas.\n\n"
    "🥊 1v1  •  👥 2v2  •  👥 3v3  •  👥 4v4\n"
    "Entre nas filas e deixe o bot organizar tudo automaticamente! 🎮"
)

RULES_TEXT = (
    "📜 **REGRAS — TREINAMENTO FF**\n\n"
    "1. 🤝 **Respeito** — respeite jogadores e equipe.\n"
    "2. 🚫 **Sem spam** — não abuse de mensagens ou menções.\n"
    "3. 🎮 **Jogo limpo** — cheats e exploits não são permitidos.\n"
    "4. 🎲 **Respeite o sorteio** — os times são definidos pelo bot.\n"
    "5. 🕐 **Evite abandonar filas** — entre apenas se pretende jogar.\n"
    "6. 🎯 **AP e Salinha** — respeite a ordem das filas.\n"
    "7. 📢 **Staff** — siga as orientações da moderação.\n"
    "8. 🔐 **Segurança** — nunca compartilhe tokens ou senhas.\n"
    "9. ⚠️ **Punições** — violações podem resultar em punições.\n"
    "10. ❤️ **Bom senso** — ajude a manter o servidor divertido."
)

HOW_TO_PLAY_TEXT = (
    "🎮 **COMO JOGAR**\n\n"
    "1️⃣ Abra o painel de filas.\n"
    "2️⃣ Escolha o modo desejado.\n"
    "3️⃣ Entre na fila.\n"
    "4️⃣ Aguarde os jogadores necessários.\n"
    "5️⃣ Quando completar, o bot criará a partida.\n"
    "6️⃣ Nos modos de equipe, o bot sorteará os times.\n\n"
    "👥 **TAMANHO DAS FILAS**\n"
    "🥊 1v1 → 2 jogadores\n"
    "👥 2v2 → 4 jogadores\n"
    "👥 3v3 → 6 jogadores\n"
    "👥 4v4 → 8 jogadores\n\n"
    "🎯 AP e 🏠 Salinha possuem filas separadas."
)

ANNOUNCEMENTS_TEXT = (
    "📢 **TREINAMENTO FF ESTÁ ONLINE!**\n\n"
    "🔥 O servidor está pronto para receber suas partidas.\n\n"
    "🥊 1v1  •  👥 2v2  •  👥 3v3  •  👥 4v4\n"
    "🎲 Nos modos de equipe, o bot sorteia os jogadores automaticamente.\n"
    "Boa sorte nas partidas! 🏆"
)

CALENDAR_TEXT = (
    "📅 **CALENDÁRIO**\n\n"
    "Este canal está reservado para a staff publicar:\n"
    "🏆 Campeonatos\n"
    "🎯 Eventos de treino\n"
    "🔥 Treinos especiais\n"
    "🛠️ Manutenções\n"
    "📢 Eventos do servidor"
)


class ServerSetup:
    def __init__(self, bot: "TreinamentoBot", database: Database):
        self.bot = bot
        self.database = database

    async def ensure(self, guild: discord.Guild) -> None:
        self.database.ensure_guild(guild.id)
        info_category = await self._get_or_create_category(guild, INFO_CATEGORY)
        queue_category = await self._get_or_create_category(guild, QUEUE_CATEGORY)
        match_category = await self._get_or_create_category(guild, MATCH_CATEGORY)
        community_category: discord.CategoryChannel | None = None
        try:
            community_category = await self._get_or_create_category(
                guild, COMMUNITY_CATEGORY
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Não foi possível criar ou acessar a categoria %s no servidor %s. "
                "Verifique a permissão Manage Channels.",
                COMMUNITY_CATEGORY,
                guild.id,
            )

        info_channels: dict[str, discord.TextChannel] = {}
        for key, (name, _) in INFO_CHANNELS.items():
            info_channels[key] = await self._get_or_create_channel(
                guild, name, info_category, staff_only=True
            )

        queue_channel = await self._get_or_create_channel(
            guild, QUEUE_CHANNEL_NAME, queue_category, staff_only=True
        )
        if community_category:
            for key in ("chat_geral", "clipes_prints"):
                try:
                    await self._get_or_create_channel(
                        guild,
                        COMMUNITY_CHANNELS[key][0],
                        community_category,
                        community=True,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning(
                        "Não foi possível criar ou organizar o canal %s no servidor %s. "
                        "Verifique a permissão Manage Channels.",
                        COMMUNITY_CHANNELS[key][0],
                        guild.id,
                    )
            try:
                await self._get_or_create_voice_channel(
                    guild,
                    COMMUNITY_CHANNELS["voz_geral"][0],
                    community_category,
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Não foi possível criar ou organizar o canal %s no servidor %s. "
                    "Verifique a permissão Manage Channels.",
                    COMMUNITY_CHANNELS["voz_geral"][0],
                    guild.id,
                )

        self.database.update_guild_config(
            guild.id,
            panel_channel_id=queue_channel.id,
            match_category_id=match_category.id,
        )
        # AP e Salinha foram descontinuados: remover canais antigos sem recriá-los.
        for old_name in ("🎯・treino-ap", "🏠・salinha"):
            old_channel = discord.utils.get(guild.text_channels, name=old_name)
            if old_channel:
                try:
                    await old_channel.delete(reason="Remoção definitiva das filas AP e Salinha")
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning("Não foi possível remover o canal antigo %s", old_name)

        await self._ensure_content(info_channels["boas_vindas"], WELCOME_TEXT, "welcome")
        await self._ensure_content(info_channels["regras"], RULES_TEXT, "rules")
        await self._ensure_content(info_channels["como_jogar"], HOW_TO_PLAY_TEXT, "how_to_play")
        await self._ensure_content(info_channels["anuncios"], ANNOUNCEMENTS_TEXT, "announcements")
        await self._ensure_content(info_channels["calendario"], CALENDAR_TEXT, "calendar")
        await self._ensure_panel(queue_channel)

    async def remove_retired_channels(self, guild: discord.Guild) -> None:
        """Remove legacy AP/Salinha channels from already-configured servers."""
        for old_name in ("🎯・treino-ap", "🏠・salinha"):
            old_channel = discord.utils.get(guild.text_channels, name=old_name)
            if old_channel:
                try:
                    await old_channel.delete(reason="Remoção definitiva das filas AP e Salinha")
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning("Não foi possível remover o canal antigo %s", old_name)

    async def ensure_community_channels_only(self, guild: discord.Guild) -> None:
        """Create only missing community channels in the existing category.

        This deliberately does not create the category, move existing channels,
        edit existing channels, or touch any other server structure.
        """
        community_category = discord.utils.get(
            guild.categories, name=COMMUNITY_CATEGORY
        )
        if not community_category:
            logger.warning(
                "A categoria %s não existe no servidor %s; nenhuma alteração foi feita.",
                COMMUNITY_CATEGORY,
                guild.id,
            )
            return

        for key in ("chat_geral", "clipes_prints"):
            name = COMMUNITY_CHANNELS[key][0]
            if discord.utils.get(community_category.text_channels, name=name):
                continue
            try:
                await guild.create_text_channel(
                    name,
                    category=community_category,
                    overwrites=self._community_overwrites(guild),
                    reason="Criação dos canais da comunidade do Treinamento FF",
                )
                logger.info("Canal de comunidade criado: %s", name)
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Não foi possível criar o canal de comunidade %s no servidor %s.",
                    name,
                    guild.id,
                )

        voice_name = COMMUNITY_CHANNELS["voz_geral"][0]
        if discord.utils.get(community_category.voice_channels, name=voice_name):
            return
        try:
            await guild.create_voice_channel(
                voice_name,
                category=community_category,
                overwrites=self._community_overwrites(guild),
                reason="Criação dos canais da comunidade do Treinamento FF",
            )
            logger.info("Canal de comunidade criado: %s", voice_name)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Não foi possível criar o canal de comunidade %s no servidor %s.",
                voice_name,
                guild.id,
            )

    async def _get_or_create_category(
        self, guild: discord.Guild, name: str
    ) -> discord.CategoryChannel:
        existing = discord.utils.get(guild.categories, name=name)
        if existing:
            return existing
        return await guild.create_category(name, reason="Configuração do Treinamento FF")

    async def _get_or_create_channel(
        self,
        guild: discord.Guild,
        name: str,
        category: discord.CategoryChannel,
        *,
        staff_only: bool = False,
        community: bool = False,
    ) -> discord.TextChannel:
        existing = discord.utils.get(guild.text_channels, name=name)
        if existing:
            if existing.category_id != category.id:
                try:
                    await existing.edit(
                        category=category,
                        reason="Organização dos canais do Treinamento FF",
                    )
                except discord.Forbidden:
                    logger.warning(
                        "Não foi possível organizar o canal %s na categoria do sistema",
                        existing.name,
                    )
            if community:
                await self._ensure_community_access(existing, guild)
            else:
                await self._ensure_bot_access(existing, guild)
            return existing
        overwrites = None
        if staff_only:
            overwrites = self._staff_overwrites(guild)
        elif community:
            overwrites = self._community_overwrites(guild)
        return await guild.create_text_channel(
            name,
            category=category,
            overwrites=overwrites,
            reason="Configuração do Treinamento FF",
        )

    async def _get_or_create_voice_channel(
        self,
        guild: discord.Guild,
        name: str,
        category: discord.CategoryChannel,
    ) -> discord.VoiceChannel:
        existing = discord.utils.get(guild.voice_channels, name=name)
        if existing:
            if existing.category_id != category.id:
                try:
                    await existing.edit(
                        category=category,
                        reason="Organização dos canais do Treinamento FF",
                    )
                except discord.Forbidden:
                    logger.warning(
                        "Não foi possível organizar o canal de voz %s na categoria do sistema",
                        existing.name,
                    )
            await self._ensure_community_voice_access(existing, guild)
            return existing
        return await guild.create_voice_channel(
            name,
            category=category,
            overwrites=self._community_overwrites(guild),
            reason="Configuração do Treinamento FF",
        )

    def _staff_overwrites(self, guild: discord.Guild) -> dict[discord.Role, discord.PermissionOverwrite]:
        overwrites: dict[discord.Role, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(send_messages=False),
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                read_message_history=True,
                manage_messages=True,
            )
        for role in guild.roles:
            if role.name.casefold() in STAFF_ROLE_NAMES:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
        return overwrites

    def _community_overwrites(
        self, guild: discord.Guild
    ) -> dict[discord.Role, discord.PermissionOverwrite]:
        overwrites: dict[discord.Role, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                connect=True,
                speak=True,
            )
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                read_message_history=True,
                manage_messages=True,
                connect=True,
                speak=True,
                move_members=True,
                manage_channels=True,
            )
        for role in guild.roles:
            if role.name.casefold() in STAFF_ROLE_NAMES:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True,
                    read_message_history=True,
                    manage_messages=True,
                    manage_channels=True,
                    connect=True,
                    speak=True,
                    move_members=True,
                    mute_members=True,
                    deafen_members=True,
                )
        return overwrites

    async def _ensure_bot_access(
        self, channel: discord.TextChannel, guild: discord.Guild
    ) -> None:
        """Grant only the bot's own member overwrite on recognized system channels."""
        if not guild.me:
            return
        try:
            await channel.set_permissions(
                guild.me,
                view_channel=True,
                send_messages=True,
                embed_links=True,
                read_message_history=True,
                manage_messages=True,
                reason="Permissão do bot no canal do Treinamento FF",
            )
        except discord.Forbidden:
            logger.warning(
                "Não foi possível ajustar permissões do bot no canal %s (%s)",
                channel.name,
                channel.id,
            )

    async def _ensure_voice_bot_access(
        self, channel: discord.VoiceChannel, guild: discord.Guild
    ) -> None:
        if not guild.me:
            return
        try:
            await channel.set_permissions(
                guild.me,
                view_channel=True,
                connect=True,
                speak=True,
                move_members=True,
                manage_channels=True,
                reason="Permissão do bot no canal do Treinamento FF",
            )
        except discord.Forbidden:
            logger.warning(
                "Não foi possível ajustar permissões do bot no canal de voz %s (%s)",
                channel.name,
                channel.id,
            )

    async def _ensure_community_voice_access(
        self, channel: discord.VoiceChannel, guild: discord.Guild
    ) -> None:
        if not guild.me:
            return
        try:
            await channel.edit(
                overwrites=self._community_overwrites(guild),
                reason="Permissões da comunidade do Treinamento FF",
            )
        except discord.Forbidden:
            logger.warning(
                "Não foi possível ajustar permissões da comunidade no canal de voz %s (%s)",
                channel.name,
                channel.id,
            )

    async def _ensure_community_access(
        self, channel: discord.TextChannel, guild: discord.Guild
    ) -> None:
        if not guild.me:
            return
        try:
            await channel.edit(
                overwrites=self._community_overwrites(guild),
                reason="Permissões da comunidade do Treinamento FF",
            )
        except discord.Forbidden:
            logger.warning(
                "Não foi possível ajustar permissões da comunidade no canal %s (%s)",
                channel.name,
                channel.id,
            )

    async def _find_marked_message(
        self, channel: discord.TextChannel, marker: str
    ) -> discord.Message | None:
        async for message in channel.history(limit=50, oldest_first=True):
            if marker in message.content:
                return message
        return None

    async def _ensure_content(self, channel: discord.TextChannel, content: str, key: str) -> None:
        marker = f"treinamento-ff:{key}"
        if await self._find_marked_message(channel, marker):
            return
        try:
            await channel.send(f"<!-- {marker} -->\n{content}")
        except discord.Forbidden:
            logger.warning(
                "Sem permissão para publicar conteúdo no canal %s (%s)",
                channel.name,
                channel.id,
            )

    async def _ensure_panel(self, channel: discord.TextChannel) -> None:
        marker = "treinamento-ff:queue-panel"
        message = await self._find_marked_message(channel, marker)
        embed = await self.bot.build_queue_embed(channel.guild.id)
        view = QueuePanelView(self.bot)
        if message:
            await message.edit(content=f"<!-- {marker} -->", embed=embed, view=view)
            self.database.update_guild_config(
                channel.guild.id,
                panel_channel_id=channel.id,
                panel_message_id=message.id,
            )
            return
        message = await channel.send(content=f"<!-- {marker} -->", embed=embed, view=view)
        self.database.update_guild_config(
            channel.guild.id,
            panel_channel_id=channel.id,
            panel_message_id=message.id,
        )



from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import TreinamentoBot