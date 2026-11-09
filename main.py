"""Treinamento FF Discord bot entrypoint."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from health_server import start_health_server

from config import (
    ALL_QUEUE_NAMES,
    BOT_TOKEN,
    DATABASE_PATH,
    GAME_MODES,
    MATCH_CATEGORY,
    STAFF_ROLE_NAMES,
    TEMPORARY_MESSAGE_SECONDS,
    TOKEN_ENV_NAME,
)
from database import (
    Database,
    MatchRecord,
    PlayerStats,
    TournamentBracketMatch,
    TournamentPair,
    TournamentRecord,
)
from pix import format_brl, parse_brl_cents
from queue_manager import QueueManager
from server_setup import ServerSetup
from tournament_payments import TournamentPayments
from views import (
    MatchAdminView,
    MatchControlView,
    QueuePanelView,
    TournamentCancelConfirmView,
    TournamentManageView,
    TournamentMatchupsView,
    TournamentPairSelectionView,
    TournamentPanelView,
    TournamentPairsView,
    TournamentPrizeModal,
    TournamentSizeView,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("treinamento_ff")


def mention_list(guild: discord.Guild, user_ids: Iterable[int]) -> str:
    mentions: list[str] = []
    for user_id in user_ids:
        member = guild.get_member(user_id)
        mentions.append(member.mention if member else f"<@{user_id}>")
    return "\n".join(mentions) or "—"


def queue_players_text(
    guild: discord.Guild, user_ids: list[int], count_text: str
) -> str:
    players = []
    for user_id in user_ids:
        member = guild.get_member(user_id)
        mention = member.mention if member else f"<@{user_id}>"
        players.append(f"👤 {mention}")
    return f"{chr(10).join(players) if players else '—'}\n\n**{count_text}**"


class TreinamentoBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(
            command_prefix=("!", "."),
            intents=intents,
            help_command=None,
            case_insensitive=True,
        )
        self.database = Database(DATABASE_PATH)
        from central_gamer import CentralGamerSystem
        self.central_gamer = CentralGamerSystem(self)
        self.tournament_payments = TournamentPayments(self)
        self.queue_manager = QueueManager(self.database)
        self.server_setup = ServerSetup(self, self.database)
        self.match_lock = asyncio.Lock()
        self.command_sync_lock = asyncio.Lock()
        self.match_end_locks: dict[int, asyncio.Lock] = {}
        self.match_end_tasks: set[asyncio.Task[None]] = set()
        self.tournament_lock = asyncio.Lock()
        self.synced_guilds: set[int] = set()
        self.temporary_message_tasks: set[asyncio.Task[None]] = set()
        self.tournament_channel_cleanup_tasks: set[asyncio.Task[None]] = set()
        self.finished_tournament_cleanup_tasks: dict[int, asyncio.Task[None]] = {}
        self.tournament_payment_provisioning_tasks: dict[int, asyncio.Task[None]] = {}
        self.tournament_payment_cleanup_tasks: dict[int, asyncio.Task[None]] = {}
        self.registered_tournament_payment_views: set[tuple[str, int, int]] = set()
        self.backup_task: asyncio.Task[None] | None = None
        self.match_confirmation: dict[int, set[int]] = {}
        self.match_confirmation_tasks: dict[int, asyncio.Task[None]] = {}

    async def setup_hook(self) -> None:
        # Limpa entradas antigas de filas descontinuadas sem alterar os demais dados.
        with self.database.lock, self.database.connection:
            self.database.connection.execute("DELETE FROM queue_entries WHERE queue_name IN ('ap','salinha')")
        self.add_view(QueuePanelView(self))
        from central_gamer import CentralEntryView, SessionView
        self.add_view(CentralEntryView(self.central_gamer))
        self.add_view(SessionView(self.central_gamer))
        from recruitment import RecruitmentSystem, CandidateSearchModal
        self.recruitment = RecruitmentSystem(self)
        await self.recruitment.setup()
        # Registra os comandos de Guerra de Guilda antes da sincronização do Discord.
        from guild_war import GuildWarSystem
        self.guild_war_system = GuildWarSystem(self)
        await self.guild_war_system.setup()
        from apostados import ApostadosSystem
        self.apostados_system = ApostadosSystem(self)
        self.apostados_system.register_commands()
        from xtreino import XTreinoSystem
        self.xtreino_system = XTreinoSystem(self)
        await self.xtreino_system.setup()
        from missions import MissionSystem
        self.mission_system = MissionSystem(self)
        self.mission_system.register_commands()
        from missions import MissionManagerView, MissionPublicView
        # Register stable custom IDs so manager buttons keep working after restarts.
        self.add_view(MissionManagerView(self.mission_system))
        self.add_view(MissionPublicView(self.mission_system))
        self.add_view(TournamentPanelView(self))
        await self.apostados_system.restore_views()
        for match in self.database.list_all_active_matches():
            self.add_view(MatchAdminView(self, match.match_id))
        try:
            backup = await asyncio.to_thread(self.database.create_backup_snapshot)
            logger.info("Backup inicial do SQLite criado: %s", backup.name)
        except Exception:
            logger.exception("Não foi possível criar o backup inicial do SQLite.")
        self.backup_task = asyncio.create_task(
            self._periodic_database_backup(), name="sqlite-backup-loop"
        )

    async def _periodic_database_backup(self) -> None:
        while True:
            await asyncio.sleep(60 * 60)
            try:
                backup = await asyncio.to_thread(self.database.create_backup_snapshot)
                logger.info("Backup automático do SQLite criado: %s", backup.name)
            except Exception:
                logger.exception("Falha ao criar o backup automático do SQLite.")

    async def close(self) -> None:
        if self.backup_task:
            self.backup_task.cancel()
            await asyncio.gather(self.backup_task, return_exceptions=True)
            self.backup_task = None
        await self.tournament_payments.close()
        try:
            backup = await asyncio.to_thread(self.database.create_backup_snapshot)
            logger.info("Backup de encerramento do SQLite criado: %s", backup.name)
        except Exception:
            logger.exception("Não foi possível criar o backup de encerramento do SQLite.")
        await super().close()

    async def on_ready(self) -> None:
        if self.user:
            logger.info("Conectado como %s (%s)", self.user, self.user.id)
        logger.info("Servidores disponíveis para sincronização: %d", len(self.guilds))
        for guild in self.guilds:
            await self.server_setup.remove_retired_channels(guild)
            if hasattr(self, "recruitment"):
                await self.recruitment.ensure_panel(guild)
            await self.sync_guild_commands(guild)
            if hasattr(self, "central_gamer"):
                await self.central_gamer.setup_guild(guild)
            await self.refresh_panels(guild)
            await self.ensure_tournament_panel(guild)
            await self.restore_active_matches(guild)
            await self.restore_tournament_matches(guild)
            await self.restore_finished_tournament_channel_cleanups(guild)
            await self.tournament_payments.restore(guild)
            if hasattr(self, "xtreino_system"):
                await self.xtreino_system.restore_guild(guild)

    async def on_guild_available(self, guild: discord.Guild) -> None:
        await self.sync_guild_commands(guild)
        if hasattr(self, "central_gamer"):
            await self.central_gamer.setup_guild(guild)
        await self.restore_finished_tournament_channel_cleanups(guild)
        await self.tournament_payments.restore(guild)

    async def sync_guild_commands(self, guild: discord.Guild) -> None:
        async with self.command_sync_lock:
            if guild.id in self.synced_guilds:
                return

            if guild.me:
                required_permissions = (
                    "view_channel",
                    "send_messages",
                    "embed_links",
                    "read_message_history",
                    "use_application_commands",
                )
                missing_permissions = [
                    permission
                    for permission in required_permissions
                    if not getattr(guild.me.guild_permissions, permission, False)
                ]
                if missing_permissions:
                    logger.warning(
                        "Permissões ausentes para o bot no servidor %s: %s",
                        guild.id,
                        ", ".join(missing_permissions),
                    )

            try:
                self.tree.clear_commands(guild=guild)
                await self.tree.sync(guild=guild)
                synced_commands = await self.tree.sync()
                self.synced_guilds.add(guild.id)
                logger.info(
                    "Slash commands sincronizados no servidor %s: %s",
                    guild.id,
                    ", ".join(command.name for command in synced_commands) or "nenhum",
                )
            except discord.Forbidden:
                logger.exception(
                    "Sem permissão para sincronizar slash commands no servidor %s",
                    guild.id,
                )
            except discord.HTTPException:
                logger.exception(
                    "Discord recusou a sincronização dos slash commands no servidor %s",
                    guild.id,
                )
            except Exception:
                logger.exception(
                    "Falha inesperada ao sincronizar slash commands no servidor %s",
                    guild.id,
                )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.server_setup.ensure(guild)
        if hasattr(self, "recruitment"):
            await self.recruitment.ensure_panel(guild)
        self.tree.clear_commands(guild=guild)
        await self.tree.sync(guild=guild)
        await self.tree.sync()
        self.synced_guilds.add(guild.id)

    async def on_member_remove(self, member: discord.Member) -> None:
        removed = self.database.leave_queue(member.guild.id, member.id)
        if removed:
            await self.refresh_panels(member.guild)
            logger.info("Jogador %s removido da fila %s após sair do servidor", member.id, removed)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if hasattr(self, "central_gamer"):
            await self.central_gamer.handle_message(message)
        match = self.database.get_active_match_for_user(message.guild.id, message.author.id)
        if match and match.channel_id == message.channel.id and message.content.strip().casefold() == "on":
            confirmed = self.match_confirmation.setdefault(match.match_id, set())
            if message.author.id in match.players:
                confirmed.add(message.author.id)
                await message.add_reaction("✅")
                missing = set(match.players) - confirmed
                if not missing:
                    task = self.match_confirmation_tasks.pop(match.match_id, None)
                    if task:
                        task.cancel()
                    await message.channel.send("✅ **Todos confirmaram!** A partida continua normalmente.")
                    self.match_confirmation.pop(match.match_id, None)
        await self.process_commands(message)

    async def start_match_confirmation(self, guild: discord.Guild, match_id: int, channel: discord.TextChannel, players: list[int]) -> None:
        self.match_confirmation[match_id] = set()
        await channel.send(
            "🟢 **CONFIRMAÇÃO DE PRESENÇA**\nTodos os jogadores devem digitar `on` neste canal em até **3 minutos**.\n"
            + "\n".join(f"• <@{uid}>" for uid in players)
            + "\nSe alguém não confirmar, somente esta partida será encerrada."
        )
        async def deadline() -> None:
            try:
                await asyncio.sleep(180)
                match = self.database.get_match(match_id)
                confirmed = self.match_confirmation.get(match_id, set())
                if not match or match.status != "active" or set(players).issubset(confirmed):
                    return
                missing = set(players) - confirmed
                self.database.cancel_match(match_id)
                for cid in (match.channel_id, match.team_a_voice_channel_id, match.team_b_voice_channel_id):
                    obj = guild.get_channel(cid or 0)
                    if obj:
                        try:
                            await obj.delete(reason=f"Fila #{match_id:04d} encerrada por falta de confirmação")
                        except (discord.Forbidden, discord.HTTPException):
                            logger.exception("Falha ao remover canal da partida #%04d", match_id)
                logger.warning("Fila #%04d encerrada por falta de confirmação; ausentes=%s", match_id, sorted(missing))
                self.match_confirmation.pop(match_id, None)
            except asyncio.CancelledError:
                raise
            finally:
                self.match_confirmation_tasks.pop(match_id, None)
        task = asyncio.create_task(deadline(), name=f"match-confirm-{match_id}")
        self.match_confirmation_tasks[match_id] = task

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Move a player to their assigned team voice channel when they connect."""
        if after.channel is None or member.bot:
            return
        match = self.database.get_active_match_for_user(member.guild.id, member.id)
        if not match or match.mode == "1v1":
            return
        target_id = (
            match.team_a_voice_channel_id
            if member.id in match.team_a
            else match.team_b_voice_channel_id
        )
        target = member.guild.get_channel(target_id or 0)
        if not isinstance(target, discord.VoiceChannel) or after.channel.id == target.id:
            return
        try:
            await member.move_to(
                target,
                reason=f"Movimentação automática para o time da Partida #{match.match_id:04d}",
            )
            match_channel = member.guild.get_channel(match.channel_id or 0)
            if isinstance(match_channel, discord.TextChannel):
                await self.send_temporary_message(
                    match_channel,
                    f"🎮 {member.mention} foi direcionado para o canal do seu time.",
                )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Não foi possível mover %s para o canal do time na partida #%04d",
                member.id,
                match.match_id,
            )

    async def send_temporary_message(
        self,
        channel: discord.abc.Messageable,
        content: str,
    ) -> None:
        """Send a message that is deleted after the configured temporary period."""
        try:
            message = await channel.send(content)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Não foi possível enviar mensagem temporária.")
            return
        task = asyncio.create_task(self._delete_message_later(message))
        self.temporary_message_tasks.add(task)
        task.add_done_callback(self.temporary_message_tasks.discard)

    async def _delete_message_later(self, message: discord.Message) -> None:
        await asyncio.sleep(TEMPORARY_MESSAGE_SECONDS)
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.debug("Mensagem temporária já foi removida ou não pode ser removida.")

    async def build_queue_embed(self, guild_id: int) -> discord.Embed:
        status = self.queue_manager.status(guild_id)
        guild = self.get_guild(guild_id)
        embed = discord.Embed(
            title="🎮 TREINAMENTO FF",
            description=(
                "Encontre jogadores, entre nas filas e participe de partidas "
                "organizadas automaticamente pelo bot."
            ),
            color=discord.Color.blurple(),
        )
        if guild:
            queue_ids = {
                queue_name: self.database.list_queue(guild_id, queue_name)
                for queue_name in ("1v1", "2v2", "3v3", "4v4")
            }
            embed.add_field(
                name="🥊 1v1",
                value=queue_players_text(
                    guild, queue_ids["1v1"], f"{len(queue_ids['1v1'])}/2"
                ),
                inline=True,
            )
            embed.add_field(
                name="👥 2v2",
                value=queue_players_text(
                    guild, queue_ids["2v2"], f"{len(queue_ids['2v2'])}/4"
                ),
                inline=True,
            )
            embed.add_field(
                name="👥 3v3",
                value=queue_players_text(
                    guild, queue_ids["3v3"], f"{len(queue_ids['3v3'])}/6"
                ),
                inline=True,
            )
            embed.add_field(
                name="👥 4v4",
                value=queue_players_text(
                    guild, queue_ids["4v4"], f"{len(queue_ids['4v4'])}/8"
                ),
                inline=True,
            )
        else:
            embed.add_field(name="🥊 1v1", value=f"{status['1v1']}/2", inline=True)
            embed.add_field(name="👥 2v2", value=f"{status['2v2']}/4", inline=True)
            embed.add_field(name="👥 3v3", value=f"{status['3v3']}/6", inline=True)
            embed.add_field(name="👥 4v4", value=f"{status['4v4']}/8", inline=True)
        # O editor geral altera somente a camada visual; campos com jogadores/contagens
        # e a QueuePanelView original permanecem intactos.
        try:
            from message_editor import get_template
            visual = get_template(
                "queue_messages",
                "🎮 Painel de Filas",
                "Encontre jogadores, entre nas filas e participe de partidas organizadas automaticamente pelo bot.",
                0x5865F2,
                "Entre em uma fila por vez • Treinamento FF",
            )
            embed.title = visual["title"]
            embed.description = visual["description"]
            embed.color = discord.Color(max(0, min(0xFFFFFF, visual["color"])))
            embed.set_footer(text=visual["footer"] or "Entre em uma fila por vez • Treinamento FF")
        except Exception:
            embed.set_footer(text="Entre em uma fila por vez • Treinamento FF")
        return embed

    async def refresh_panels(self, guild: discord.Guild) -> None:
        config = self.database.get_guild_config(guild.id)
        channel = guild.get_channel(config.panel_channel_id or 0)
        if not isinstance(channel, discord.TextChannel) or not config.panel_message_id:
            return
        try:
            panel_message = await channel.fetch_message(config.panel_message_id)
            await panel_message.edit(
                embed=await self.build_queue_embed(guild.id),
                view=QueuePanelView(self),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.warning("Não foi possível atualizar o painel do servidor %s", guild.id)

    async def handle_join(self, interaction: discord.Interaction, queue_name: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "Este painel só funciona dentro de um servidor.", ephemeral=True
            )
            return
        async with self.match_lock:
            joined, existing_queue = self.queue_manager.join(
                interaction.guild.id, interaction.user.id, queue_name
            )
            if not joined:
                if existing_queue == queue_name:
                    message = "Você já está nessa fila."
                else:
                    message = (
                        f"Você já está na fila **{existing_queue}**. "
                        "Saia dela antes de entrar em outra."
                    )
                await interaction.response.send_message(message, ephemeral=True)
                return
            await interaction.response.send_message(
                f"Você entrou na fila **{self.queue_label(queue_name)}**.", ephemeral=True
            )
            await self.refresh_panels(interaction.guild)
            for ready_mode in self.queue_manager.ready_modes(interaction.guild.id):
                await self.create_match(interaction.guild, ready_mode)

    async def handle_leave(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "Este painel só funciona dentro de um servidor.", ephemeral=True
            )
            return
        queue_name = self.queue_manager.leave(interaction.guild.id, interaction.user.id)
        if queue_name:
            message = f"Você saiu da fila **{self.queue_label(queue_name)}**."
        else:
            message = "Você não está em nenhuma fila."
        await interaction.response.send_message(message, ephemeral=True)
        await self.refresh_panels(interaction.guild)

    async def send_status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "Use este painel em um servidor.", ephemeral=True
            )
            return
        embed = await self.build_queue_embed(interaction.guild.id)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    def tournament_panel_embed(self, tournament_id: int) -> discord.Embed:
        tournament = self.database.get_tournament(tournament_id)
        if not tournament:
            return discord.Embed(title="🏆 TORNEIO", description="Este torneio não está mais disponível.", color=discord.Color.red())
        teams = self.database.list_tournament_pairs(tournament_id)
        mode_label = tournament.mode
        if tournament.status == "registration":
            status = "🟢 Inscrições abertas"
            description = "Cadastre as equipes e encerre as inscrições quando atingir o limite."
        elif tournament.status == "ready":
            status = "🔒 Inscrições encerradas"
            description = "O torneio está pronto para ser iniciado pelo ADM."
        elif tournament.status == "active":
            status = "⚔️ Torneio em andamento"
            description = "O chaveamento está em andamento."
        elif tournament.status == "finished":
            status = "🏆 Finalizado"
            description = "O torneio foi finalizado."
        else:
            status = "❌ Cancelado"
            description = "Este torneio foi cancelado e não aceita mais ações."
        embed = discord.Embed(title=f"🏆 TORNEIO {mode_label}", description=description, color=discord.Color.gold())
        embed.add_field(name="⚔️ Formato", value=mode_label, inline=True)
        embed.add_field(name="👥 Equipes inscritas", value=f"{len(teams)}/{tournament.capacity}", inline=True)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="🏟️ Vagas", value=f"{tournament.capacity} equipes • {tournament.team_size} jogador(es) por equipe", inline=False)
        embed.add_field(
            name="💰 Valor da premiação",
            value=format_brl(tournament.prize_amount_cents),
            inline=False,
        )
        embed.set_footer(text=f"Torneio #{tournament.tournament_id:04d}")
        return embed

    async def ensure_tournament_panel(self, guild: discord.Guild) -> None:
        tournament = self.database.get_open_tournament(guild.id)
        if not tournament:
            return
        config = self.database.get_guild_config(guild.id)
        channel = guild.get_channel(tournament.panel_channel_id or 0)
        if not isinstance(channel, discord.TextChannel) or channel.id == config.panel_channel_id:
            try:
                category = guild.get_channel(config.match_category_id or 0)
                if not isinstance(category, discord.CategoryChannel):
                    category = discord.utils.get(guild.categories, name=MATCH_CATEGORY)
                overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False)
                }
                for role in guild.roles:
                    if role.name.casefold() in STAFF_ROLE_NAMES:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                if guild.me:
                    overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True)
                channel = await guild.create_text_channel(
                    f"🏆・torneio-{tournament.tournament_id:04d}",
                    category=category if isinstance(category, discord.CategoryChannel) else None,
                    overwrites=overwrites,
                    reason=f"Painel do Torneio #{tournament.tournament_id:04d}",
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Não foi possível criar o canal separado do torneio #%04d", tournament.tournament_id)
                return
        marker = f"treinamento-ff:tournament-panel:{tournament.tournament_id}"
        message: discord.Message | None = None
        if tournament.panel_message_id:
            try:
                message = await channel.fetch_message(tournament.panel_message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
        if message is None:
            try:
                async for candidate in channel.history(limit=100, oldest_first=True):
                    if marker in candidate.content:
                        message = candidate
                        break
            except (discord.Forbidden, discord.HTTPException):
                pass
        view = TournamentPanelView(
            self, tournament.status, tournament.capacity, tournament.mode
        )
        try:
            if message:
                await message.edit(content=f"<!-- {marker} -->", embed=self.tournament_panel_embed(tournament.tournament_id), view=view)
            else:
                message = await channel.send(content=f"<!-- {marker} -->", embed=self.tournament_panel_embed(tournament.tournament_id), view=view)
            self.database.update_tournament_panel(tournament.tournament_id, channel.id, message.id)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Não foi possível atualizar o painel do torneio #%04d", tournament.tournament_id)

    async def tournament_panel_after_change(self, guild: discord.Guild) -> None:
        await self.ensure_tournament_panel(guild)
        await self.refresh_panels(guild)

    async def refresh_tournament_panel_message(self, tournament_id: int) -> None:
        tournament = self.database.get_tournament(tournament_id)
        if not tournament or not tournament.panel_channel_id or not tournament.panel_message_id:
            return
        guild = self.get_guild(tournament.guild_id)
        if not guild:
            return
        channel = guild.get_channel(tournament.panel_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(tournament.panel_message_id)
            await message.edit(
                embed=self.tournament_panel_embed(tournament_id),
                view=TournamentPanelView(
                    self, tournament.status, tournament.capacity, tournament.mode
                ),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    def tournament_pairs_embed(self, tournament_id: int, page: int = 0) -> discord.Embed:
        tournament = self.database.get_tournament(tournament_id)
        if not tournament:
            return discord.Embed(title="🏆 EQUIPES INSCRITAS", description="Torneio indisponível.", color=discord.Color.red())
        teams = self.database.list_tournament_pairs(tournament_id)
        page_size = 8
        page_count = max(1, (len(teams) + page_size - 1) // page_size)
        page = max(0, min(page, page_count - 1))
        guild = self.get_guild(tournament.guild_id)
        lines = []
        for index, team in enumerate(teams[page * page_size:(page + 1) * page_size], start=page * page_size + 1):
            members = [f"<@{uid}>" for uid in team.players]
            lines.append(f"**{index}.** " + " • ".join(members))
        embed = discord.Embed(title=f"🏆 EQUIPES — {tournament.mode}", description="\n".join(lines) or "Nenhuma equipe inscrita.", color=discord.Color.blurple())
        embed.add_field(name="Inscrições", value=f"{len(teams)}/{tournament.capacity}", inline=True)
        embed.add_field(name="Status", value=tournament.status, inline=True)
        embed.add_field(
            name="💰 Valor da premiação",
            value=format_brl(tournament.prize_amount_cents),
            inline=False,
        )
        embed.set_footer(text=f"Página {page + 1}/{page_count}")
        return embed

    def tournament_matchups_embed(
        self,
        guild: discord.Guild,
        tournament: TournamentRecord,
        pair_ids: tuple[int, int, int, int],
    ) -> discord.Embed:
        embed = discord.Embed(
            title="🔄 GERENCIAR CONFRONTOS — 2x2",
            description=(
                "Escolha uma das combinações no menu. "
                "A seleção é salva e usada quando o torneio começar."
            ),
            color=discord.Color.blurple(),
        )
        for semifinal, index in ((1, 0), (2, 2)):
            team_a = self.database.get_tournament_pair(pair_ids[index])
            team_b = self.database.get_tournament_pair(pair_ids[index + 1])
            if not team_a or not team_b:
                value = "Dupla não encontrada."
            else:
                value = (
                    f"**{self.tournament_pair_name(guild, team_a)}** "
                    f"× **{self.tournament_pair_name(guild, team_b)}**"
                )
            embed.add_field(
                name=f"Semifinal {semifinal}",
                value=value,
                inline=False,
            )
        embed.set_footer(text=f"Torneio #{tournament.tournament_id:04d} • alterações bloqueadas após o início")
        return embed

    @staticmethod
    def tournament_phase_name(capacity: int, round_number: int) -> str:
        names = {4: {1: "SEMIFINAL", 2: "FINAL"}, 8: {1: "QUARTAS", 2: "SEMIFINAL", 3: "FINAL"}, 16: {1: "OITAVAS", 2: "QUARTAS", 3: "SEMIFINAL", 4: "FINAL"}}
        return names.get(capacity, {}).get(round_number, f"FASE {round_number}")

    @staticmethod
    def tournament_pair_name(guild: discord.Guild, pair: TournamentPair) -> str:
        names = []
        for user_id in pair.players:
            member = guild.get_member(user_id)
            names.append(member.display_name if member else f"<@{user_id}>")
        return " + ".join(names)

    async def handle_tournament_create(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode criar torneios.", ephemeral=True); return
        if self.database.get_open_tournament(interaction.guild.id):
            await interaction.response.send_message("Já existe um torneio aberto ou em andamento neste servidor.", ephemeral=True); return
        await interaction.response.send_message("Escolha o formato do torneio:", view=TournamentSizeView(self), ephemeral=True)

    async def handle_tournament_size(self, interaction: discord.Interaction, mode: str, capacity: int) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.edit_message(content="❌ Apenas ADM/staff pode criar torneios.", view=None); return
        async with self.tournament_lock:
            tournament_id = self.database.create_tournament(interaction.guild.id, capacity, interaction.user.id, mode)
        if tournament_id is None:
            await interaction.response.edit_message(content="Já existe um torneio aberto ou em andamento neste servidor.", view=None); return
        await interaction.response.edit_message(content=f"🏆 Torneio **{mode}** com **{capacity} equipes** criado.", view=None)
        await self.tournament_panel_after_change(interaction.guild)

    async def handle_tournament_add_pair(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode adicionar equipes.", ephemeral=True); return
        tournament = self.database.get_open_tournament(interaction.guild.id)
        if not tournament or tournament.status != "registration":
            await interaction.response.send_message("❌ As inscrições não estão abertas.", ephemeral=True); return
        await interaction.response.send_message(
            f"Selecione exatamente **{tournament.team_size} jogador(es)** para a equipe {tournament.mode}:",
            view=TournamentPairSelectionView(self, tournament.tournament_id, interaction.user.id, tournament.team_size), ephemeral=True)

    async def handle_tournament_pair_selection(self, interaction: discord.Interaction, tournament_id: int, selected_users: list[discord.User | discord.Member]) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode adicionar equipes.", ephemeral=True); return
        tournament = self.database.get_tournament(tournament_id)
        if not tournament or tournament.status != "registration":
            await interaction.response.send_message("❌ As inscrições já estão encerradas.", ephemeral=True); return
        if len(selected_users) != tournament.team_size or len({u.id for u in selected_users}) != tournament.team_size:
            await interaction.response.send_message(f"Selecione exatamente {tournament.team_size} jogadores diferentes.", ephemeral=True); return
        members = []
        for user in selected_users:
            member = interaction.guild.get_member(user.id)
            if not member:
                try: member = await interaction.guild.fetch_member(user.id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException): member = None
            if not member or member.bot:
                await interaction.response.send_message("❌ Todos os participantes precisam estar no servidor e não podem ser bots.", ephemeral=True); return
            members.append(member)
        async with self.tournament_lock:
            result, team = self.database.add_tournament_pair(tournament_id, [m.id for m in members])
        messages = {"invalid_team":"Quantidade inválida de jogadores.","not_found":"Torneio não encontrado.","closed":"Inscrições encerradas.","full":"Torneio cheio.","duplicate_pair":"Essa equipe já está inscrita.","player_registered":"Um dos jogadores já está em outra equipe."}
        if result != "added" or not team:
            await interaction.response.send_message(f"❌ {messages.get(result, 'Não foi possível adicionar a equipe.')}", ephemeral=True); return
        await interaction.response.edit_message(content="✅ Equipe adicionada: " + " • ".join(m.mention for m in members), view=None)
        await self.tournament_panel_after_change(interaction.guild)

    async def handle_tournament_list_pairs(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use este painel em um servidor.", ephemeral=True); return
        tournament = self.database.get_open_tournament(interaction.guild.id)
        if not tournament:
            await interaction.response.send_message("Não há torneio aberto.", ephemeral=True); return
        await interaction.response.send_message(embed=self.tournament_pairs_embed(tournament.tournament_id), view=TournamentPairsView(self, tournament.tournament_id, interaction.user.id), ephemeral=True)

    async def handle_tournament_manage(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode gerenciar.", ephemeral=True); return
        tournament = self.database.get_open_tournament(interaction.guild.id)
        if not tournament:
            await interaction.response.send_message("Não há torneio aberto.", ephemeral=True); return
        if tournament.status not in {"registration", "ready"}:
            await interaction.response.send_message(
                "O gerenciamento da premiação fica disponível antes do início do torneio.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=self.tournament_pairs_embed(tournament.tournament_id), view=TournamentManageView(self, tournament.tournament_id, interaction.user.id), ephemeral=True)

    async def open_tournament_prize_modal(
        self, interaction: discord.Interaction, tournament_id: int
    ) -> None:
        if (
            not interaction.guild
            or not isinstance(interaction.user, discord.Member)
            or not self.is_staff_member(interaction.user)
        ):
            await interaction.response.send_message(
                "❌ Apenas ADM/staff pode definir a premiação.", ephemeral=True
            )
            return
        tournament = self.database.get_tournament(tournament_id)
        if (
            not tournament
            or tournament.guild_id != interaction.guild.id
            or tournament.status not in {"registration", "ready"}
        ):
            await interaction.response.send_message(
                "A premiação só pode ser definida antes do início do torneio.",
                ephemeral=True,
            )
            return
        default_amount = (
            format_brl(tournament.prize_amount_cents)
            if tournament.prize_amount_cents is not None
            else ""
        )
        await interaction.response.send_modal(
            TournamentPrizeModal(self, tournament_id, default_amount)
        )

    async def handle_tournament_set_prize(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
        raw_amount: str,
    ) -> None:
        if (
            not interaction.guild
            or not isinstance(interaction.user, discord.Member)
            or not self.is_staff_member(interaction.user)
        ):
            await interaction.response.send_message(
                "❌ Apenas ADM/staff pode definir a premiação.", ephemeral=True
            )
            return
        try:
            amount_cents = parse_brl_cents(raw_amount)
        except ValueError as error:
            await interaction.response.send_message(
                f"❌ {error}", ephemeral=True
            )
            return
        result = self.database.set_tournament_prize(
            tournament_id, interaction.guild.id, amount_cents
        )
        if result == "not_found":
            await interaction.response.send_message(
                "❌ Torneio não encontrado.", ephemeral=True
            )
            return
        if result == "closed":
            await interaction.response.send_message(
                "❌ A premiação só pode ser alterada antes do início do torneio.",
                ephemeral=True,
            )
            return
        if result != "saved":
            await interaction.response.send_message(
                "❌ Não foi possível salvar a premiação.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self.tournament_panel_after_change(interaction.guild)
        await interaction.edit_original_response(
            f"✅ Premiação total definida em **{format_brl(amount_cents)}**.",
            embed=self.tournament_pairs_embed(tournament_id),
            view=TournamentManageView(self, tournament_id, interaction.user.id),
        )

    async def handle_tournament_capacity(
        self, interaction: discord.Interaction, tournament_id: int, capacity: int
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode alterar o limite.", ephemeral=True); return
        tournament = self.database.get_tournament(tournament_id)
        if not tournament or tournament.guild_id != interaction.guild.id:
            await interaction.response.send_message("❌ Torneio não encontrado.", ephemeral=True); return
        result, registered = self.database.update_tournament_capacity(tournament_id, capacity)
        if result == "too_small":
            await interaction.response.send_message(
                f"❌ O limite não pode ser menor que o número de equipes já inscritas ({registered}).",
                ephemeral=True,
            )
            return
        if result == "closed":
            await interaction.response.send_message("❌ O limite só pode ser alterado durante as inscrições.", ephemeral=True)
            return
        if result != "updated":
            await interaction.response.send_message("❌ Não foi possível alterar o limite do torneio.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content=f"✅ Limite atualizado para **{capacity} equipes**. O painel agora mostra **{registered}/{capacity}**.",
            embed=self.tournament_pairs_embed(tournament_id),
            view=TournamentManageView(self, tournament_id, interaction.user.id),
        )
        await self.tournament_panel_after_change(interaction.guild)

    async def handle_tournament_remove(self, interaction: discord.Interaction, tournament_id: int, team_id: int) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode gerenciar.", ephemeral=True); return
        tournament = self.database.get_tournament(tournament_id)
        if not tournament or tournament.status != "registration":
            await interaction.response.send_message("❌ Só é possível remover equipes antes do início.", ephemeral=True); return
        if self.database.remove_tournament_pair(tournament_id, team_id):
            await interaction.response.send_message("✅ Equipe removida.", ephemeral=True)
            await self.tournament_panel_after_change(interaction.guild)
        else:
            await interaction.response.send_message("❌ Equipe não encontrada.", ephemeral=True)

    async def handle_tournament_close(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode encerrar inscrições.", ephemeral=True); return
        tournament = self.database.get_open_tournament(interaction.guild.id)
        if not tournament or tournament.status != "registration":
            await interaction.response.send_message("Não há inscrições abertas.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        async with self.tournament_lock:
            result, _ = self.database.close_tournament(tournament.tournament_id)
        if result == "not_full":
            registered = len(self.database.list_tournament_pairs(tournament.tournament_id))
            await interaction.edit_original_response(content=f"❌ Cadastre exatamente **{tournament.capacity} equipes**. Atual: {registered}/{tournament.capacity}."); return
        if result != "ready":
            await interaction.edit_original_response(content="❌ Não foi possível encerrar as inscrições."); return
        await interaction.edit_original_response(content="🔒 Inscrições encerradas. O torneio está pronto. Use **⚔️ Iniciar Torneio** para gerar o chaveamento.")
        await self.tournament_panel_after_change(interaction.guild)

    async def handle_tournament_start(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode iniciar.", ephemeral=True); return
        tournament = self.database.get_open_tournament(interaction.guild.id)
        if not tournament or tournament.status != "ready":
            await interaction.response.send_message("❌ O torneio precisa estar com as inscrições encerradas.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        async with self.tournament_lock:
            result, _ = self.database.start_tournament(tournament.tournament_id)
        if result == "not_full":
            await interaction.edit_original_response(content=f"❌ O torneio precisa de {tournament.capacity} equipes."); return
        if result != "started":
            await interaction.edit_original_response(content="❌ Não foi possível iniciar o torneio."); return
        await self.create_pending_tournament_matches(interaction.guild, tournament.tournament_id)
        await interaction.edit_original_response(content="⚔️ Torneio iniciado! A primeira fase foi criada.")
        await self.tournament_panel_after_change(interaction.guild)

    async def handle_tournament_manage_matchups(
        self, interaction: discord.Interaction
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message(
                "❌ Apenas ADM/staff pode gerenciar os confrontos.", ephemeral=True
            )
            return
        tournament = self.database.get_open_tournament(interaction.guild.id)
        if (
            not tournament
            or tournament.status != "ready"
            or tournament.mode != "2v2"
            or tournament.capacity != 4
        ):
            await interaction.response.send_message(
                "❌ O gerenciamento das semifinais está disponível para torneios 2x2 com 4 equipes, após encerrar as inscrições e antes de iniciar.",
                ephemeral=True,
            )
            return
        teams = self.database.list_tournament_pairs(tournament.tournament_id)
        if len(teams) != 4:
            await interaction.response.send_message(
                "❌ São necessárias exatamente 4 duplas cadastradas para gerenciar as semifinais.",
                ephemeral=True,
            )
            return
        plan = self.database.get_tournament_matchup_plan(tournament.tournament_id)
        if plan is None:
            plan = (teams[0].pair_id, teams[1].pair_id, teams[2].pair_id, teams[3].pair_id)
        await interaction.response.send_message(
            embed=self.tournament_matchups_embed(interaction.guild, tournament, plan),
            view=TournamentMatchupsView(
                self,
                tournament.tournament_id,
                interaction.user.id,
                interaction.guild,
                teams,
                plan,
            ),
            ephemeral=True,
        )

    async def handle_tournament_set_matchups(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
        pair_ids: tuple[int, int, int, int],
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message(
                "❌ Apenas ADM/staff pode alterar os confrontos.", ephemeral=True
            )
            return
        tournament = self.database.get_tournament(tournament_id)
        if not tournament or tournament.guild_id != interaction.guild.id:
            await interaction.response.send_message("❌ Torneio não encontrado.", ephemeral=True)
            return
        async with self.tournament_lock:
            result = self.database.set_tournament_matchup_plan(tournament_id, pair_ids)
        if result != "saved":
            messages = {
                "not_found": "Torneio não encontrado.",
                "not_ready": "As alterações só podem ser feitas em um torneio 2x2 com 4 equipes antes de iniciar.",
                "already_started": "Os confrontos não podem ser alterados após o início.",
                "invalid_teams": "A combinação precisa usar cada uma das 4 duplas exatamente uma vez.",
            }
            await interaction.response.send_message(
                f"❌ {messages.get(result, 'Não foi possível salvar os confrontos.')}",
                ephemeral=True,
            )
            return
        saved_plan = self.database.get_tournament_matchup_plan(tournament_id)
        if saved_plan is None:
            await interaction.response.send_message(
                "❌ Não foi possível carregar os confrontos salvos.", ephemeral=True
            )
            return
        teams = self.database.list_tournament_pairs(tournament_id)
        await interaction.response.edit_message(
            content="✅ Confrontos salvos. Eles serão usados ao iniciar o torneio.",
            embed=self.tournament_matchups_embed(interaction.guild, tournament, saved_plan),
            view=TournamentMatchupsView(
                self,
                tournament_id,
                interaction.user.id,
                interaction.guild,
                teams,
                saved_plan,
            ),
        )

    async def handle_tournament_cancel(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode cancelar.", ephemeral=True); return
        tournament = self.database.get_open_tournament(interaction.guild.id)
        if not tournament:
            await interaction.response.send_message("Não há torneio aberto.", ephemeral=True); return
        await interaction.response.send_message(
            f"⚠️ Tem certeza que deseja cancelar o torneio **{tournament.mode} / {tournament.capacity} equipes**? Esta ação encerra o torneio.",
            view=TournamentCancelConfirmView(self, tournament.tournament_id, interaction.user.id), ephemeral=True)

    async def confirm_tournament_cancel(self, interaction: discord.Interaction, tournament_id: int, owner_id: int) -> None:
        if interaction.user.id != owner_id:
            await interaction.response.send_message("Este menu pertence ao ADM que iniciou a confirmação.", ephemeral=True); return
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Sem permissão.", ephemeral=True); return
        active_brackets = self.database.list_tournament_bracket_matches(tournament_id, ("active",))
        if not self.database.cancel_tournament(tournament_id):
            await interaction.response.edit_message(content="❌ O torneio já foi encerrado ou cancelado.", view=None); return
        # Cancel active tournament matches and remove their channels.
        for bracket in active_brackets:
            if bracket.regular_match_id:
                match = self.database.get_match(bracket.regular_match_id)
                if match:
                    self.database.cancel_match(match.match_id)
                    await self.close_match_channels_now(interaction.guild, match)
        await interaction.response.edit_message(content="❌ Torneio cancelado. Todos os controles foram bloqueados.", view=None)
        cancelled = self.database.get_tournament(tournament_id)
        if cancelled and cancelled.panel_channel_id:
            channel = interaction.guild.get_channel(cancelled.panel_channel_id)
            if isinstance(channel, discord.TextChannel):
                if cancelled.panel_message_id:
                    try:
                        message = await channel.fetch_message(cancelled.panel_message_id)
                        await message.edit(embed=self.tournament_panel_embed(tournament_id), view=TournamentPanelView(self, "cancelled"))
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                cleanup_task = asyncio.create_task(
                    self._delete_tournament_channel_later(channel, tournament_id)
                )
                self.tournament_channel_cleanup_tasks.add(cleanup_task)
                cleanup_task.add_done_callback(self.tournament_channel_cleanup_tasks.discard)
        await self.refresh_panels(interaction.guild)

    async def _delete_tournament_channel_later(
        self, channel: discord.TextChannel, tournament_id: int
    ) -> None:
        await asyncio.sleep(60)
        try:
            await channel.delete(
                reason=f"Exclusão do canal após cancelamento do Torneio #{tournament_id:04d}"
            )
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Não foi possível excluir o canal do Torneio #%04d após o cancelamento.",
                tournament_id,
            )

    async def restore_finished_tournament_channel_cleanups(
        self, guild: discord.Guild
    ) -> None:
        for tournament in self.database.list_pending_tournament_channel_cleanups(
            guild.id
        ):
            await self.refresh_tournament_panel_message(tournament.tournament_id)
            self.schedule_finished_tournament_channel_cleanup(guild, tournament)

    def schedule_finished_tournament_channel_cleanup(
        self, guild: discord.Guild, tournament: TournamentRecord
    ) -> None:
        cleanup_at = tournament.channel_cleanup_at
        if (
            tournament.status != "finished"
            or not cleanup_at
            or tournament.channel_cleanup_done_at is not None
        ):
            return
        existing = self.finished_tournament_cleanup_tasks.get(
            tournament.tournament_id
        )
        if existing and not existing.done():
            return

        task = asyncio.create_task(
            self._delete_finished_tournament_channel_after_deadline(
                guild, tournament.tournament_id, cleanup_at
            ),
            name=f"tournament-channel-cleanup-{tournament.tournament_id}",
        )
        self.finished_tournament_cleanup_tasks[tournament.tournament_id] = task

        def clear_finished_task(completed: asyncio.Task[None]) -> None:
            if (
                self.finished_tournament_cleanup_tasks.get(tournament.tournament_id)
                is completed
            ):
                self.finished_tournament_cleanup_tasks.pop(
                    tournament.tournament_id, None
                )
            if completed.cancelled():
                return
            error = completed.exception()
            if error:
                logger.error(
                    "O agendamento da exclusão do canal do Torneio #%04d terminou com erro.",
                    tournament.tournament_id,
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(clear_finished_task)

    async def _delete_finished_tournament_channel_after_deadline(
        self, guild: discord.Guild, tournament_id: int, cleanup_at: str
    ) -> None:
        while True:
            tournament = self.database.get_tournament(tournament_id)
            if (
                not tournament
                or tournament.guild_id != guild.id
                or tournament.status != "finished"
                or tournament.channel_cleanup_at != cleanup_at
                or tournament.channel_cleanup_done_at is not None
            ):
                return

            try:
                deadline = datetime.fromisoformat(cleanup_at)
            except ValueError:
                logger.exception(
                    "Prazo de exclusão inválido para o Torneio #%04d.",
                    tournament_id,
                )
                return
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)

        tournament = self.database.get_tournament(tournament_id)
        if (
            not tournament
            or tournament.guild_id != guild.id
            or tournament.status != "finished"
            or tournament.channel_cleanup_at != cleanup_at
            or tournament.channel_cleanup_done_at is not None
        ):
            return
        channel_id = tournament.panel_channel_id
        if not channel_id:
            self.database.mark_tournament_channel_cleanup_done(
                tournament_id, cleanup_at
            )
            return

        config = self.database.get_guild_config(guild.id)
        if channel_id == config.panel_channel_id:
            logger.error(
                "O canal do Torneio #%04d aponta para o painel geral; exclusão bloqueada.",
                tournament_id,
            )
            self.database.mark_tournament_channel_cleanup_done(
                tournament_id, cleanup_at
            )
            return

        while True:
            try:
                channel = guild.get_channel(channel_id)
                if channel is None:
                    channel = await guild.fetch_channel(channel_id)
            except discord.NotFound:
                self.database.mark_tournament_channel_cleanup_done(
                    tournament_id, cleanup_at
                )
                return
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Falha ao localizar o canal do Torneio #%04d; nova tentativa em 1 minuto.",
                    tournament_id,
                )
                await asyncio.sleep(60)
                continue

            if not isinstance(channel, discord.TextChannel):
                logger.error(
                    "O destino salvo do Torneio #%04d não é um canal de texto; "
                    "exclusão bloqueada.",
                    tournament_id,
                )
                self.database.mark_tournament_channel_cleanup_done(
                    tournament_id, cleanup_at
                )
                return
            try:
                await channel.delete(
                    reason=f"Encerramento do Torneio #{tournament_id:04d}"
                )
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Não foi possível excluir o canal do Torneio #%04d; "
                    "nova tentativa em 1 minuto.",
                    tournament_id,
                )
                await asyncio.sleep(60)
                continue

            self.database.mark_tournament_channel_cleanup_done(
                tournament_id, cleanup_at
            )
            logger.info(
                "Canal do Torneio #%04d excluído após o prazo salvo.",
                tournament_id,
            )
            return

    async def handle_tournament_announcement(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not self.is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas ADM/staff pode anunciar.", ephemeral=True); return
        tournament = self.database.get_open_tournament(interaction.guild.id)
        if not tournament:
            await interaction.response.send_message("Não há torneio aberto.", ephemeral=True); return
        await interaction.response.send_modal(TournamentAnnouncementModal(self, tournament.tournament_id))

    async def send_tournament_announcement(self, interaction: discord.Interaction, tournament_id: int, text: str) -> None:
        tournament = self.database.get_tournament(tournament_id)
        if not tournament or not interaction.guild:
            await interaction.response.send_message("❌ Torneio indisponível.", ephemeral=True); return
        channel = interaction.guild.get_channel(tournament.panel_channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Canal do torneio não encontrado.", ephemeral=True); return
        await channel.send(f"📢 **ANÚNCIO DO TORNEIO**\n{text}")
        await interaction.response.send_message("✅ Anúncio enviado no canal do torneio.", ephemeral=True)

    async def handle_tournament_bracket(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use no servidor.", ephemeral=True); return
        tournament = self.database.get_open_tournament(interaction.guild.id)
        if not tournament:
            await interaction.response.send_message("Nenhum torneio aberto.", ephemeral=True); return
        matches = self.database.list_tournament_bracket_matches(tournament.tournament_id)
        if not matches:
            await interaction.response.send_message("O chaveamento ainda não foi iniciado.", ephemeral=True); return
        embed = discord.Embed(title=f"🏆 CHAVEAMENTO — {tournament.mode}", color=discord.Color.gold())
        for round_number in sorted({m.round_number for m in matches}):
            lines=[]
            for m in [x for x in matches if x.round_number == round_number]:
                a=self.database.get_tournament_pair(m.pair_a_id); b=self.database.get_tournament_pair(m.pair_b_id)
                a_name=self.tournament_pair_name(interaction.guild,a) if a else "?"
                b_name=self.tournament_pair_name(interaction.guild,b) if b else "?"
                result = "⏳" if m.status in {"pending","active"} else (f"🏆 {self.tournament_pair_name(interaction.guild, self.database.get_tournament_pair(m.winner_pair_id))}" if m.winner_pair_id and self.database.get_tournament_pair(m.winner_pair_id) else "🏁")
                lines.append(f"**{m.match_number}.** {a_name} × {b_name} — {result}")
            embed.add_field(name=self.tournament_phase_name(tournament.capacity, round_number), value="\n".join(lines)[:1024] or "—", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def restore_tournament_matches(self, guild: discord.Guild) -> None:
        tournament = self.database.get_open_tournament(guild.id)
        if not tournament or tournament.status != "active": return
        await self.create_pending_tournament_matches(guild, tournament.tournament_id)

    async def restore_active_matches(self, guild: discord.Guild) -> None:
        """Reattach channels to active regular matches without creating new match rows."""
        async with self.match_lock:
            for match in self.database.list_active_matches(guild.id):
                if self.database.get_tournament_bracket_for_match(match.match_id):
                    continue
                await self.restore_match_channels(guild, match)

    async def _find_match_channel(
        self,
        guild: discord.Guild,
        channel_id: int | None,
        channel_name: str,
        channel_type: type,
    ):
        if channel_id:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, channel_type):
                return channel
        channel = next(
            (
                candidate
                for candidate in guild.channels
                if candidate.name == channel_name
                and isinstance(candidate, channel_type)
            ),
            None,
        )
        if channel:
            return channel
        if channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.NotFound:
                return None
            if isinstance(channel, channel_type):
                return channel
        return None

    async def _match_admin_message_exists(
        self, channel: discord.TextChannel, match_id: int
    ) -> bool:
        if not self.user:
            return True
        expected_custom_id = f"treinamento:match:{match_id}:admin"
        try:
            async for message in channel.history(limit=100):
                if message.author.id != self.user.id:
                    continue
                for row in message.components:
                    if any(
                        getattr(component, "custom_id", None) == expected_custom_id
                        for component in row.children
                    ):
                        return True
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Não foi possível verificar o histórico do canal da Partida #%04d; "
                "nenhuma mensagem duplicada será enviada.",
                match_id,
            )
            return True
        return False

    async def restore_match_channels(
        self,
        guild: discord.Guild,
        match: MatchRecord,
        tournament: TournamentRecord | None = None,
        bracket: TournamentBracketMatch | None = None,
    ) -> bool:
        """Resume an active match, adopting deterministic channel names when needed."""
        is_tournament = tournament is not None and bracket is not None
        text_name = (
            f"🎮・torneio-{match.match_id:04d}"
            if is_tournament
            else f"🎮・partida-{match.match_id:04d}"
        )
        config = self.database.get_guild_config(guild.id)
        category = guild.get_channel(config.match_category_id or 0)
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.get(guild.categories, name=MATCH_CATEGORY)
        changed = False
        try:
            text_channel = await self._find_match_channel(
                guild, match.channel_id, text_name, discord.TextChannel
            )
            if text_channel is None:
                text_channel = await guild.create_text_channel(
                    text_name,
                    category=category if isinstance(category, discord.CategoryChannel) else None,
                    overwrites=(
                        self.tournament_text_overwrites(guild, match.players)
                        if is_tournament
                        else self.match_text_overwrites(guild, match.players)
                    ),
                    reason=f"Recuperação da Partida #{match.match_id:04d}",
                )
                changed = True
            if match.channel_id != text_channel.id:
                self.database.set_match_channel(match.match_id, text_channel.id)
                changed = True

            team_a_channel = None
            team_b_channel = None
            if match.mode != "1v1":
                team_a_channel = await self._find_match_channel(
                    guild,
                    match.team_a_voice_channel_id,
                    f"🔵・time-a-{match.match_id:04d}",
                    discord.VoiceChannel,
                )
                if team_a_channel is None:
                    team_a_channel = await guild.create_voice_channel(
                        f"🔵・time-a-{match.match_id:04d}",
                        category=category if isinstance(category, discord.CategoryChannel) else None,
                        overwrites=self.team_voice_overwrites(guild, match.team_a),
                        reason=f"Recuperação do Time A da Partida #{match.match_id:04d}",
                    )
                    changed = True
                team_b_channel = await self._find_match_channel(
                    guild,
                    match.team_b_voice_channel_id,
                    f"🔴・time-b-{match.match_id:04d}",
                    discord.VoiceChannel,
                )
                if team_b_channel is None:
                    team_b_channel = await guild.create_voice_channel(
                        f"🔴・time-b-{match.match_id:04d}",
                        category=category if isinstance(category, discord.CategoryChannel) else None,
                        overwrites=self.team_voice_overwrites(guild, match.team_b),
                        reason=f"Recuperação do Time B da Partida #{match.match_id:04d}",
                    )
                    changed = True
                if (
                    match.team_a_voice_channel_id != team_a_channel.id
                    or match.team_b_voice_channel_id != team_b_channel.id
                ):
                    self.database.set_match_voice_channels(
                        match.match_id, team_a_channel.id, team_b_channel.id
                    )
                    changed = True

            if not await self._match_admin_message_exists(text_channel, match.match_id):
                if is_tournament:
                    team_a = self.database.get_tournament_pair(bracket.pair_a_id)
                    team_b = self.database.get_tournament_pair(bracket.pair_b_id)
                    if not team_a or not team_b:
                        return False
                    embed = self.tournament_match_embed(
                        guild,
                        tournament,
                        bracket,
                        team_a,
                        team_b,
                        team_a_channel.id if team_a_channel else None,
                        team_b_channel.id if team_b_channel else None,
                    )
                else:
                    embed = self.match_embed(
                        guild,
                        match.match_id,
                        match.mode,
                        match.team_a,
                        match.team_b,
                        team_a_channel.id if team_a_channel else None,
                        team_b_channel.id if team_b_channel else None,
                    )
                await text_channel.send(
                    embed=embed, view=MatchAdminView(self, match.match_id)
                )
                changed = True

            if team_a_channel and team_b_channel:
                await self.move_connected_players(
                    guild,
                    match.team_a,
                    match.team_b,
                    team_a_channel,
                    team_b_channel,
                    match.match_id,
                )
            if changed:
                logger.info("Partida #%04d recuperada no servidor %s", match.match_id, guild.id)
            return True
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Não foi possível recuperar os canais da Partida #%04d.",
                match.match_id,
            )
            return False

    async def create_pending_tournament_matches(self, guild: discord.Guild, tournament_id: int | None = None) -> None:
        tournament = self.database.get_tournament(tournament_id) if tournament_id is not None else self.database.get_open_tournament(guild.id)
        if not tournament or tournament.guild_id != guild.id or tournament.status != "active": return
        async with self.tournament_lock:
            brackets = self.database.list_tournament_bracket_matches(tournament.tournament_id, ("pending", "active"))
            for bracket in brackets:
                if bracket.status == "pending" and bracket.regular_match_id is None:
                    await self.create_tournament_bracket_match(guild, tournament, bracket)
                elif bracket.status == "active" and bracket.regular_match_id:
                    match = self.database.get_match(bracket.regular_match_id)
                    if match and match.status == "active":
                        await self.restore_match_channels(
                            guild, match, tournament, bracket
                        )

    async def create_tournament_bracket_match(self, guild: discord.Guild, tournament: TournamentRecord, bracket: TournamentBracketMatch) -> None:
        team_a = self.database.get_tournament_pair(bracket.pair_a_id); team_b = self.database.get_tournament_pair(bracket.pair_b_id)
        if not team_a or not team_b: return
        players = team_a.players + team_b.players
        regular_match: MatchRecord | None = None
        created_regular_match = False
        if bracket.regular_match_id:
            regular_match = self.database.get_match(bracket.regular_match_id)
            if not regular_match or regular_match.status != "active": return
        else:
            orphan_status, orphan_match_id = self.database.adopt_orphaned_tournament_match(
                guild.id,
                tournament.mode,
                players,
                team_a.players,
                team_b.players,
                bracket.bracket_match_id,
                tournament.started_at or "9999-12-31T23:59:59+00:00",
            )
            if orphan_status in {"adopted", "already_linked"} and orphan_match_id:
                adopted_match = self.database.get_match(orphan_match_id)
                current_bracket = self.database.get_tournament_bracket_match(
                    bracket.bracket_match_id
                )
                if (
                    adopted_match
                    and adopted_match.status == "active"
                    and current_bracket
                ):
                    await self.restore_match_channels(
                        guild, adopted_match, tournament, current_bracket
                    )
                return
            if orphan_status == "ambiguous":
                logger.error(
                    "Há mais de uma partida ativa sem vínculo para o confronto "
                    "do torneio #%04d; criação suspensa para evitar duplicação.",
                    bracket.bracket_match_id,
                )
                return
            regular_match_id = self.database.create_tournament_match(
                guild.id,
                tournament.mode,
                players,
                team_a.players,
                team_b.players,
                bracket.bracket_match_id,
            )
            if not regular_match_id:
                return
            regular_match = self.database.get_match(regular_match_id); created_regular_match = True
        if not regular_match: return
        channel=None; va=None; vb=None
        try:
            config=self.database.get_guild_config(guild.id); category=guild.get_channel(config.match_category_id or 0)
            if not isinstance(category, discord.CategoryChannel): category=discord.utils.get(guild.categories, name=MATCH_CATEGORY)
            channel=await guild.create_text_channel(f"🎮・torneio-{regular_match.match_id:04d}", category=category if isinstance(category,discord.CategoryChannel) else None, overwrites=self.tournament_text_overwrites(guild,players), reason=f"Confronto de torneio #{regular_match.match_id:04d}")
            self.database.set_match_channel(regular_match.match_id, channel.id)
            if tournament.mode != "1v1":
                va=await guild.create_voice_channel(f"🔵・time-a-{regular_match.match_id:04d}", category=category if isinstance(category,discord.CategoryChannel) else None, overwrites=self.team_voice_overwrites(guild,team_a.players), reason="Canal de voz do Time A")
                vb=await guild.create_voice_channel(f"🔴・time-b-{regular_match.match_id:04d}", category=category if isinstance(category,discord.CategoryChannel) else None, overwrites=self.team_voice_overwrites(guild,team_b.players), reason="Canal de voz do Time B")
                self.database.set_match_voice_channels(regular_match.match_id,va.id,vb.id)
            await channel.send(embed=self.tournament_match_embed(guild,tournament,bracket,team_a,team_b,va.id if va else None,vb.id if vb else None),view=MatchAdminView(self,regular_match.match_id))
            if va and vb: await self.move_connected_players(guild,team_a.players,team_b.players,va,vb,regular_match.match_id)
        except (discord.Forbidden,discord.HTTPException):
            logger.exception("Falha ao criar confronto de torneio")
            if created_regular_match:
                self.database.clear_tournament_match_assignment(bracket.bracket_match_id); self.database.cancel_match(regular_match.match_id)
            for ch in (va,vb,channel):
                if ch:
                    try: await ch.delete(reason="Limpeza após falha")
                    except (discord.NotFound,discord.Forbidden,discord.HTTPException): pass

    @staticmethod
    def match_text_overwrites(
        guild: discord.Guild, players: list[int]
    ) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False)
        }
        for user_id in players:
            member = guild.get_member(user_id)
            if member:
                overwrites[member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
        for role in guild.roles:
            if role.name.casefold() in {"staff", "moderador", "moderadores", "admin"}:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                read_message_history=True,
                manage_messages=True,
                manage_channels=True,
                connect=True,
                speak=True,
                move_members=True,
            )
        return overwrites

    @staticmethod
    def tournament_text_overwrites(guild: discord.Guild, players: list[int]) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
        overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        for uid in players:
            member=guild.get_member(uid)
            if member: overwrites[member]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        for role in guild.roles:
            if role.name.casefold() in STAFF_ROLE_NAMES: overwrites[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        if guild.me: overwrites[guild.me]=discord.PermissionOverwrite(view_channel=True,send_messages=True,embed_links=True,read_message_history=True,manage_messages=True,manage_channels=True,connect=True,speak=True,move_members=True)
        return overwrites

    def tournament_match_embed(self, guild: discord.Guild, tournament: TournamentRecord, bracket: TournamentBracketMatch, team_a: TournamentPair, team_b: TournamentPair, team_a_voice_channel_id: int | None, team_b_voice_channel_id: int | None) -> discord.Embed:
        embed=discord.Embed(title=f"🏆 TORNEIO {tournament.mode} — {self.tournament_phase_name(tournament.capacity, bracket.round_number)}",description=f"**Confronto {bracket.match_number:02d}**\n\n⚔️ Boa sorte!",color=discord.Color.gold())
        embed.add_field(name="🔵 TIME A",value=mention_list(guild,team_a.players),inline=True); embed.add_field(name="🔴 TIME B",value=mention_list(guild,team_b.players),inline=True)
        embed.add_field(
            name="💰 Valor da premiação",
            value=format_brl(tournament.prize_amount_cents),
            inline=False,
        )
        if team_a_voice_channel_id and team_b_voice_channel_id: embed.add_field(name="🔊 Canais de voz",value=f"Time A: <#{team_a_voice_channel_id}>\nTime B: <#{team_b_voice_channel_id}>",inline=False)
        else: embed.add_field(name="🔇 Voz",value="1x1: esta partida é somente por texto.",inline=False)
        embed.set_footer(text="🔐 Use o Painel ADM para registrar o resultado.")
        return embed

    @staticmethod
    def player_stats_embed(
        member: discord.Member | discord.User, stats: PlayerStats
    ) -> discord.Embed:
        total_matches = stats.wins + stats.losses
        win_rate = (stats.wins / total_matches * 100) if total_matches else 0
        embed = discord.Embed(
            title="👤 PERFIL DE JOGADOR",
            description=f"**{member.display_name}**",
            color=discord.Color.gold(),
        )
        embed.add_field(name="🏆 Vitórias", value=str(stats.wins), inline=True)
        embed.add_field(name="❌ Derrotas", value=str(stats.losses), inline=True)
        embed.add_field(name="🎮 Partidas", value=str(total_matches), inline=True)
        embed.add_field(name="📊 Aproveitamento", value=f"{win_rate:.0f}%", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Discord ID: {member.id}")
        return embed

    @staticmethod
    def is_staff_member(member: discord.Member) -> bool:
        return (
            member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
            or any(role.name.casefold() in STAFF_ROLE_NAMES for role in member.roles)
        )

    def match_end_lock(self, match_id: int) -> asyncio.Lock:
        return self.match_end_locks.setdefault(match_id, asyncio.Lock())

    async def handle_match_admin_panel(
        self, interaction: discord.Interaction, match_id: int
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Você não tem permissão para usar o Painel ADM.",
                ephemeral=True,
            )
            return
        if not self.is_staff_member(interaction.user):
            await interaction.response.send_message(
                "❌ Você não tem permissão para usar o Painel ADM.",
                ephemeral=True,
            )
            return
        match = self.database.get_match(match_id)
        if not match or match.guild_id != interaction.guild.id:
            await interaction.response.send_message(
                "Esta partida não foi encontrada.", ephemeral=True
            )
            return
        if match.status != "active":
            await interaction.response.send_message(
                "Esta partida já foi finalizada.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Painel ADM da Partida #{match_id:04d}:",
            view=MatchControlView(self, match_id),
            ephemeral=True,
        )

    async def handle_match_outcome(
        self,
        interaction: discord.Interaction,
        match_id: int,
        winner_team: str | None,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Você não tem permissão para realizar esta ação.",
                ephemeral=True,
            )
            return
        if not self.is_staff_member(interaction.user):
            await interaction.response.send_message(
                "❌ Você não tem permissão para realizar esta ação.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with self.match_end_lock(match_id):
            match = self.database.get_match(match_id)
            if not match or match.guild_id != interaction.guild.id:
                await interaction.edit_original_response(
                    content="Esta partida não foi encontrada."
                )
                return
            if match.status != "active":
                await interaction.edit_original_response(
                    content="Esta partida já foi finalizada."
                )
                return
            tournament_bracket = self.database.get_tournament_bracket_for_match(match_id)
            tournament_result = None
            if tournament_bracket:
                tournament_result = self.database.finish_tournament_match(
                    match_id, winner_team
                )
                if tournament_result is None:
                    await interaction.edit_original_response(
                        content="Esta partida já foi finalizada."
                    )
                    return
            elif not self.database.finish_match(match_id, winner_team):
                await interaction.edit_original_response(
                    content="Esta partida já foi finalizada."
                )
                return

            if tournament_result and tournament_result.champion_pair_id is not None:
                finished_tournament = self.database.get_tournament(
                    tournament_result.tournament_id
                )
                if (
                    finished_tournament
                    and finished_tournament.status == "finished"
                    and finished_tournament.channel_cleanup_at
                    and finished_tournament.channel_cleanup_done_at is None
                ):
                    self.schedule_finished_tournament_channel_cleanup(
                        interaction.guild, finished_tournament
                    )
                if interaction.guild:
                    self.tournament_payments.schedule_provisioning(
                        interaction.guild, tournament_result.tournament_id
                    )

            if tournament_result and tournament_result.champion_pair_id:
                champion = self.database.get_tournament_pair(
                    tournament_result.champion_pair_id
                )
                champion_text = (
                    mention_list(
                        interaction.guild,
                        champion.players,
                    )
                    if champion
                    else "Dupla não encontrada"
                )
                champion_name = (
                    self.tournament_pair_name(interaction.guild, champion)
                    if champion
                    else "Dupla não encontrada"
                )
                announcement = (
                    "🏆 **TORNEIO FINALIZADO!**\n"
                    f"🥇 **CAMPEÕES:** {champion_name}\n{champion_text}\n"
                    "🏅 Cada integrante da equipe vencedora recebeu +1 título no Hall da Fama.\n"
                    "💸 O canal privado de pagamentos foi preparado para os campeões.\n"
                    "🏁 A partida foi encerrada.\n"
                    "🕐 Este canal será fechado em 1 minuto."
                )
            elif tournament_result and winner_team:
                next_text = (
                    "A dupla vencedora avançará para a próxima fase."
                    if tournament_result.next_bracket_match_ids
                    else "Aguardando os demais confrontos desta fase."
                )
                announcement = (
                    f"🏆 Time {winner_team} venceu o confronto do torneio!\n"
                    f"✅ {next_text}\n"
                    "🏁 A partida foi encerrada.\n"
                    "🕐 Este canal será fechado em 1 minuto."
                )
            elif winner_team == "A":
                announcement = (
                    "🏆 Time A venceu a partida!\n"
                    "🏁 A partida foi encerrada.\n"
                    "🕐 Este canal será fechado em 1 minuto."
                )
            elif winner_team == "B":
                announcement = (
                    "🏆 Time B venceu a partida!\n"
                    "🏁 A partida foi encerrada.\n"
                    "🕐 Este canal será fechado em 1 minuto."
                )
            else:
                announcement = (
                    "🏁 Partida finalizada sem vencedor.\n"
                    "🕐 Este canal será fechado em 1 minuto."
                )

            channel = interaction.guild.get_channel(match.channel_id or 0)
            if channel is None and isinstance(interaction.channel, discord.TextChannel):
                channel = interaction.channel
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(announcement)
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning(
                        "Não foi possível publicar o encerramento da partida #%04d.",
                        match_id,
                    )
            await interaction.edit_original_response(
                content="Ação registrada. O canal será fechado em 1 minuto.",
                view=None,
            )
            if tournament_result and tournament_result.next_bracket_match_ids:
                await self.create_pending_tournament_matches(interaction.guild)
            if tournament_result:
                await self.refresh_tournament_panel_message(tournament_result.tournament_id)
            task = asyncio.create_task(
                self.close_match_after_delay(interaction.guild, match)
            )
            self.match_end_tasks.add(task)
            task.add_done_callback(self.match_end_tasks.discard)

    async def close_match_channels_now(self, guild: discord.Guild, match: MatchRecord) -> None:
        channel_ids = []
        if match.channel_id:
            channel_ids.append(match.channel_id)
        if match.mode != "1v1":
            if match.team_a_voice_channel_id:
                channel_ids.append(match.team_a_voice_channel_id)
            if match.team_b_voice_channel_id:
                channel_ids.append(match.team_b_voice_channel_id)
        for channel_id in channel_ids:
            channel = guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            try:
                await channel.delete(reason=f"Cancelamento do Torneio - Partida #{match.match_id:04d}")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    async def close_match_after_delay(
        self, guild: discord.Guild, match: MatchRecord
    ) -> None:
        await asyncio.sleep(60)
        channel_ids: list[int] = []
        if match.channel_id:
            channel_ids.append(match.channel_id)
        if match.mode != "1v1":
            if match.team_a_voice_channel_id:
                channel_ids.append(match.team_a_voice_channel_id)
            if match.team_b_voice_channel_id:
                channel_ids.append(match.team_b_voice_channel_id)

        for channel_id in channel_ids:
            channel = guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            try:
                await channel.delete(
                    reason=f"Encerramento da Partida #{match.match_id:04d}"
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.debug(
                    "Canal %s já não existe ou não pode ser excluído.", channel_id
                )

    async def create_match(self, guild: discord.Guild, mode: str) -> None:
        required = GAME_MODES[mode]
        players = self.queue_manager.take_ready_players(guild.id, mode)
        if len(players) != required:
            return
        team_a, team_b = self.queue_manager.split_teams(players)
        match_id = self.database.create_match(guild.id, mode, players, team_a, team_b)
        channel: discord.TextChannel | None = None
        team_a_channel: discord.VoiceChannel | None = None
        team_b_channel: discord.VoiceChannel | None = None
        try:
            config = self.database.get_guild_config(guild.id)
            category = guild.get_channel(config.match_category_id or 0)
            if not isinstance(category, discord.CategoryChannel):
                category = discord.utils.get(guild.categories, name=MATCH_CATEGORY)
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            for user_id in players:
                member = guild.get_member(user_id)
                if member:
                    overwrites[member] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                    )
            for role in guild.roles:
                if role.name.casefold() in {"staff", "moderador", "moderadores", "admin"}:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True
                    )
            if guild.me:
                overwrites[guild.me] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    embed_links=True,
                    read_message_history=True,
                    manage_messages=True,
                    manage_channels=True,
                    connect=True,
                    speak=True,
                    move_members=True,
                )
            channel = await guild.create_text_channel(
                f"🎮・partida-{match_id:04d}",
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                reason=f"Partida Treinamento FF #{match_id:04d}",
            )
            self.database.set_match_channel(match_id, channel.id)
            if mode != "1v1":
                team_a_channel = await guild.create_voice_channel(
                    f"🔵・time-a-{match_id:04d}",
                    category=category if isinstance(category, discord.CategoryChannel) else None,
                    overwrites=self.team_voice_overwrites(guild, team_a),
                    reason=f"Canal de voz do Time A da Partida #{match_id:04d}",
                )
                team_b_channel = await guild.create_voice_channel(
                    f"🔴・time-b-{match_id:04d}",
                    category=category if isinstance(category, discord.CategoryChannel) else None,
                    overwrites=self.team_voice_overwrites(guild, team_b),
                    reason=f"Canal de voz do Time B da Partida #{match_id:04d}",
                )
                self.database.set_match_voice_channels(
                    match_id, team_a_channel.id, team_b_channel.id
                )
            await channel.send(
                embed=self.match_embed(
                    guild,
                    match_id,
                    mode,
                    team_a,
                    team_b,
                    team_a_channel.id if team_a_channel else None,
                    team_b_channel.id if team_b_channel else None,
                ),
                view=MatchAdminView(self, match_id),
            )
            await self.start_match_confirmation(guild, match_id, channel, players)
            if mode != "1v1" and team_a_channel and team_b_channel:
                await self.send_temporary_message(
                    channel,
                    "⏳ Aviso temporário: entre no canal de voz do seu time. "
                    "Este aviso será apagado após 6 minutos.",
                )
                await self.move_connected_players(
                    guild,
                    team_a,
                    team_b,
                    team_a_channel,
                    team_b_channel,
                    match_id,
                )
            await self.refresh_panels(guild)
            logger.info("Partida #%04d criada no servidor %s", match_id, guild.id)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Não foi possível criar o canal da partida #%04d", match_id)
            self.database.cancel_match(match_id)
            for created_channel in (team_a_channel, team_b_channel, channel):
                if created_channel is None:
                    continue
                try:
                    await created_channel.delete(
                        reason=f"Limpeza da Partida #{match_id:04d} após falha"
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logger.debug(
                        "Canal parcial da Partida #%04d já não existe.",
                        match_id,
                    )

    @staticmethod
    def team_voice_overwrites(
        guild: discord.Guild, team: list[int]
    ) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False, connect=False, speak=False
            )
        }
        for user_id in team:
            member = guild.get_member(user_id)
            if member:
                overwrites[member] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    speak=True,
                    use_voice_activation=True,
                )
        for role in guild.roles:
            if role.name.casefold() in {"staff", "moderador", "moderadores", "admin"}:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    speak=True,
                    move_members=True,
                    mute_members=True,
                    deafen_members=True,
                )
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                move_members=True,
                manage_channels=True,
            )
        return overwrites

    async def move_connected_players(
        self,
        guild: discord.Guild,
        team_a: list[int],
        team_b: list[int],
        team_a_channel: discord.VoiceChannel,
        team_b_channel: discord.VoiceChannel,
        match_id: int,
    ) -> None:
        for user_id, target in [
            *((user_id, team_a_channel) for user_id in team_a),
            *((user_id, team_b_channel) for user_id in team_b),
        ]:
            member = guild.get_member(user_id)
            if member and member.voice and member.voice.channel != target:
                try:
                    await member.move_to(
                        target,
                        reason=f"Movimentação automática para a Partida #{match_id:04d}",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning(
                        "Não foi possível mover %s para o Time na partida #%04d",
                        user_id,
                        match_id,
                    )

    def match_embed(
        self,
        guild: discord.Guild,
        match_id: int,
        mode: str,
        team_a: list[int],
        team_b: list[int],
        team_a_voice_channel_id: int | None = None,
        team_b_voice_channel_id: int | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎮 PARTIDA #{match_id:04d}",
            description=f"**Modo:** {mode}\n\n🎲 Times sorteados automaticamente.",
            color=discord.Color.green(),
        )
        embed.add_field(name="🔵 TIME A", value=mention_list(guild, team_a), inline=True)
        embed.add_field(name="🔴 TIME B", value=mention_list(guild, team_b), inline=True)
        if team_a_voice_channel_id and team_b_voice_channel_id:
            embed.add_field(
                name="🔊 Canais de voz",
                value=(
                    f"Time A: <#{team_a_voice_channel_id}>\n"
                    f"Time B: <#{team_b_voice_channel_id}>"
                ),
                inline=False,
            )
        embed.set_footer(text="Use /partida para consultar sua partida atual.")
        return embed

    @staticmethod
    def queue_label(queue_name: str) -> str:
        return {
            "1v1": "🥊 1v1",
            "2v2": "👥 2v2",
            "3v3": "👥 3v3",
            "4v4": "👥 4v4",
        }[queue_name]

    async def send_match_info(
        self, interaction: discord.Interaction, match: MatchRecord | None
    ) -> None:
        if not match:
            content = "Você não está em uma partida ativa."
            if interaction.response.is_done():
                await interaction.edit_original_response(content=content, embed=None)
            else:
                await interaction.response.send_message(content, ephemeral=True)
            return
        channel_text = f"<#{match.channel_id}>" if match.channel_id else "canal ainda sendo criado"
        content = (
            f"Você está na **Partida #{match.match_id:04d}** ({match.mode}).\n"
            f"Canal: {channel_text}"
        )
        embed = (
            self.match_embed(
                interaction.guild,
                match.match_id,
                match.mode,
                match.team_a,
                match.team_b,
                match.team_a_voice_channel_id,
                match.team_b_voice_channel_id,
            )
            if interaction.guild
            else None
        )
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, embed=embed)
        else:
            await interaction.response.send_message(
                content,
                embed=embed,
                ephemeral=True,
            )


bot = TreinamentoBot()

from owner_panel import register_owner_panel
register_owner_panel(bot)


@bot.command(name="ip")
async def player_profile(
    context: commands.Context, member: discord.Member | None = None
) -> None:
    target = member or context.author
    stats = bot.database.get_player_stats(target.id)
    await context.send(embed=bot.player_stats_embed(target, stats))


@bot.tree.command(name="anunciar", description="ADM: publica um anúncio personalizado em um canal.")
@app_commands.describe(
    canal="Canal onde o anúncio será publicado",
    titulo="Título do anúncio",
    mensagem="Texto do anúncio",
    cor="Cor da mensagem",
    everyone="Mencionar @everyone no anúncio",
)
@app_commands.choices(cor=[
    app_commands.Choice(name="Vermelho", value="vermelho"),
    app_commands.Choice(name="Azul", value="azul"),
    app_commands.Choice(name="Verde", value="verde"),
    app_commands.Choice(name="Roxo", value="roxo"),
    app_commands.Choice(name="Dourado", value="dourado"),
    app_commands.Choice(name="Preto", value="preto"),
    app_commands.Choice(name="Branco", value="branco"),
])
@app_commands.guild_only()
async def anunciar(
    interaction: discord.Interaction,
    canal: discord.TextChannel,
    titulo: str,
    mensagem: str,
    cor: app_commands.Choice[str],
    everyone: bool = False,
) -> None:
    """Publica um embed; só administradores/staff podem usar."""
    member = interaction.user
    role_names = {role.name.casefold() for role in getattr(member, "roles", [])}
    is_staff = bool(role_names & {name.casefold() for name in STAFF_ROLE_NAMES})
    has_admin = isinstance(member, discord.Member) and (
        member.guild_permissions.administrator or member.guild_permissions.manage_guild
    )
    if not (is_staff or has_admin):
        await interaction.response.send_message(
            "❌ Somente a ADM autorizada pode usar este comando.", ephemeral=True
        )
        return
    if len(titulo) > 256 or len(mensagem) > 3500:
        await interaction.response.send_message(
            "❌ O título deve ter até 256 caracteres e a mensagem até 3500.", ephemeral=True
        )
        return
    colors = {
        "vermelho": 0xE74C3C, "azul": 0x3498DB, "verde": 0x2ECC71,
        "roxo": 0x9B59B6, "dourado": 0xF1C40F, "preto": 0x2B2D31,
        "branco": 0xFFFFFF,
    }
    embed = discord.Embed(title=titulo, description=mensagem, color=colors[cor.value])
    embed.set_footer(text=f"{interaction.guild.name} • ALL EVOM")
    allowed = discord.AllowedMentions(everyone=everyone, users=False, roles=False, replied_user=False)
    try:
        await canal.send(content="@everyone" if everyone else None, embed=embed, allowed_mentions=allowed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Não consegui publicar. Confira se o Galinha V8 pode ver o canal, enviar mensagens e incorporar links.",
            ephemeral=True,
        )
        return
    except discord.HTTPException:
        await interaction.response.send_message(
            "❌ O Discord recusou o anúncio. Confira o texto e tente novamente.", ephemeral=True
        )
        return
    await interaction.response.send_message(f"✅ Anúncio publicado em {canal.mention}.", ephemeral=True)


@bot.tree.command(name="painel-adm", description="ADM: mostra os comandos principais da ALL EVOM.")
@app_commands.guild_only()
async def painel_adm(interaction: discord.Interaction) -> None:
    """Painel privado com atalhos e instruções dos comandos administrativos."""
    member = interaction.user
    role_names = {role.name.casefold() for role in getattr(member, "roles", [])}
    is_staff = bool(role_names & {name.casefold() for name in STAFF_ROLE_NAMES})
    has_admin = isinstance(member, discord.Member) and (
        member.guild_permissions.administrator or member.guild_permissions.manage_guild
    )
    if not (is_staff or has_admin):
        await interaction.response.send_message(
            "❌ Somente a ADM autorizada pode abrir este painel.", ephemeral=True
        )
        return
    embed = discord.Embed(
        title="🐔 PAINEL ADM • ALL EVOM",
        description="Use os comandos abaixo neste canal privado. Este painel é visível somente para você.",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="📢 Anúncios",
        value="`/anunciar` — escolha o canal, título, mensagem, cor e se quer mencionar @everyone.",
        inline=False,
    )
    embed.add_field(
        name="📊 Jogadores",
        value="`.ip` — consulte as estatísticas normais.\n`.xt` — consulte as estatísticas de X-TREINO.",
        inline=False,
    )
    embed.add_field(
        name="🎮 Filas e partidas",
        value="`/fila` — painel de filas.\n`/status` — situação das filas.\n`/partida` — sua partida ativa.",
        inline=False,
    )
    embed.add_field(
        name="🛡️ Administração",
        value="`/limparfila`, `/reiniciarfila` e `/infosistema` — use somente quando necessário.",
        inline=False,
    )
    embed.set_footer(text="ALL EVOM • comandos sujeitos às permissões configuradas")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="fila", description="Publica ou atualiza o painel principal de filas.")
async def fila(interaction: discord.Interaction) -> None:
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "Use este comando em um canal de texto do servidor.", ephemeral=True
        )
        return
    # O painel precisa ser uma mensagem pública persistente para que os eventos
    # de entrada/saída possam atualizá-la, inclusive depois de reiniciar o bot.
    await interaction.response.defer(ephemeral=True, thinking=True)
    config = bot.database.get_guild_config(interaction.guild.id)
    channel = interaction.channel
    message = None
    if config.panel_channel_id and config.panel_message_id:
        old_channel = interaction.guild.get_channel(config.panel_channel_id)
        if isinstance(old_channel, discord.TextChannel):
            try:
                message = await old_channel.fetch_message(config.panel_message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
    if message is None:
        message = await channel.send(
            embed=await bot.build_queue_embed(interaction.guild.id),
            view=QueuePanelView(bot),
        )
    else:
        await message.edit(
            embed=await bot.build_queue_embed(interaction.guild.id),
            view=QueuePanelView(bot),
        )
    bot.database.update_guild_config(
        interaction.guild.id,
        panel_channel_id=message.channel.id,
        panel_message_id=message.id,
    )
    await interaction.edit_original_response(
        content=f"Painel de filas publicado/atualizado em {message.channel.mention}."
    )


@bot.tree.command(name="status", description="Mostra o estado de todas as filas.")
async def status(interaction: discord.Interaction) -> None:
    if interaction.guild:
        await interaction.response.defer(ephemeral=True)
    await bot.send_status(interaction)


@bot.tree.command(name="sairfila", description="Remove você da fila atual.")
@app_commands.describe(modo="Opcionalmente, informe uma fila específica.")
@app_commands.choices(
    modo=[
        app_commands.Choice(name="1v1", value="1v1"),
        app_commands.Choice(name="2v2", value="2v2"),
        app_commands.Choice(name="3v3", value="3v3"),
        app_commands.Choice(name="4v4", value="4v4"),
    ]
)
async def sairfila(
    interaction: discord.Interaction, modo: app_commands.Choice[str] | None = None
) -> None:
    if not interaction.guild:
        await interaction.response.send_message(
            "Use este comando em um servidor.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    queue_name = bot.queue_manager.leave(
        interaction.guild.id, interaction.user.id, modo.value if modo else None
    )
    await interaction.edit_original_response(
        content=(
            f"Você saiu da fila **{bot.queue_label(queue_name)}**."
            if queue_name
            else "Você não está nessa fila."
        )
    )
    await bot.refresh_panels(interaction.guild)


@bot.tree.command(name="partida", description="Mostra sua partida ativa.")
async def partida(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message(
            "Use este comando em um servidor.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    await bot.send_match_info(
        interaction,
        bot.database.get_active_match_for_user(interaction.guild.id, interaction.user.id),
    )


async def require_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Comando disponível apenas para staff.", ephemeral=True
        )
        return False
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "Você precisa da permissão **Gerenciar servidor** para usar este comando.",
            ephemeral=True,
        )
        return False
    return True


@bot.tree.command(name="limparfila", description="Admin: limpa uma fila.")
@app_commands.describe(modo="Fila que será limpa.")
@app_commands.choices(
    modo=[app_commands.Choice(name=mode, value=mode) for mode in ALL_QUEUE_NAMES]
)
async def limparfila(interaction: discord.Interaction, modo: app_commands.Choice[str]) -> None:
    if not await require_staff(interaction):
        return
    removed = bot.database.clear_queue(interaction.guild.id, modo.value)
    await interaction.response.send_message(
        f"Fila **{bot.queue_label(modo.value)}** limpa. {removed} jogador(es) removido(s)."
    )
    await bot.refresh_panels(interaction.guild)


@bot.tree.command(name="reiniciarfila", description="Admin: reinicia todas as filas.")
async def reiniciarfila(interaction: discord.Interaction) -> None:
    if not await require_staff(interaction):
        return
    total = sum(
        bot.database.clear_queue(interaction.guild.id, queue_name)
        for queue_name in ALL_QUEUE_NAMES
    )
    await interaction.response.send_message(
        f"Todas as filas foram reiniciadas. {total} jogador(es) removido(s)."
    )
    await bot.refresh_panels(interaction.guild)


@bot.tree.command(name="infosistema", description="Admin: mostra informações do sistema.")
async def infosistema(interaction: discord.Interaction) -> None:
    if not await require_staff(interaction):
        return
    active = bot.database.list_active_matches(interaction.guild.id)
    status = bot.queue_manager.status(interaction.guild.id)
    latest_backup = bot.database.latest_backup_path()
    backup_status = latest_backup.name if latest_backup else "ainda não criado"
    await interaction.response.send_message(
        f"**Treinamento FF**\n"
        f"Filas: {sum(status.values())} jogador(es) aguardando\n"
        f"Partidas ativas: {len(active)}\n"
        f"Persistência: SQLite ativa\n"
        f"Último backup local: {backup_status}",
        ephemeral=True,
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    original_error = getattr(error, "original", error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                "Ocorreu um erro ao processar o comando. Tente novamente.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Ocorreu um erro ao processar o comando. Tente novamente.", ephemeral=True
            )
    except discord.NotFound:
        logger.warning("A interação expirou antes da mensagem de erro ser enviada.")
    except discord.HTTPException:
        logger.exception("Falha ao enviar a mensagem de erro do comando.")
    logger.error("Erro em comando Discord: %s", original_error, exc_info=original_error)


@bot.event
async def on_command_error(
    context: commands.Context, error: commands.CommandError
) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.BadArgument):
        await context.send("Use `.IP @jogador` ou `.IP` para consultar as estatísticas.")
        return
    logger.error("Erro em comando de texto: %s", error, exc_info=error)





class BanConfirmView(discord.ui.View):
    def __init__(self, bot_instance, target_id: int, reason: str, requester_id: int):
        super().__init__(timeout=120)
        self.bot_instance = bot_instance
        self.target_id = int(target_id)
        self.reason = reason
        self.requester_id = int(requester_id)

    @discord.ui.button(label="🚫 Confirmar banimento", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message("Somente o administrador que solicitou pode confirmar.", ephemeral=True)
        member = interaction.guild.get_member(self.target_id)
        if member is None:
            try: member = await interaction.guild.fetch_member(self.target_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return await interaction.response.send_message("A conta não está mais no servidor; nenhum banimento foi executado.", ephemeral=True)
        bot_member = interaction.guild.me
        if member.id == interaction.guild.owner_id or member.id == self.bot_instance.user.id:
            return await interaction.response.send_message("❌ Não é permitido banir o dono do servidor ou o próprio bot.", ephemeral=True)
        if not interaction.user.guild_permissions.ban_members or not bot_member.guild_permissions.ban_members:
            return await interaction.response.send_message("❌ O administrador e o bot precisam da permissão Banir membros.", ephemeral=True)
        if member.top_role >= bot_member.top_role or (interaction.user.id != interaction.guild.owner_id and member.top_role >= interaction.user.top_role):
            return await interaction.response.send_message("❌ A hierarquia de cargos do Discord impede este banimento.", ephemeral=True)
        try:
            await interaction.guild.ban(member, reason=f"Solicitado por {interaction.user} ({interaction.user.id}): {self.reason}", delete_message_days=0)
            outcome = "success"
            await interaction.response.edit_message(content=f"✅ **{member}** (`{member.id}`) foi banido. Motivo: {self.reason}", view=None)
        except (discord.Forbidden, discord.HTTPException) as exc:
            outcome = f"failed:{type(exc).__name__}"
            await interaction.response.edit_message(content=f"❌ O Discord não permitiu o banimento de `{member.id}` ({type(exc).__name__}).", view=None)
        try:
            self.bot_instance.recruitment.log_event(member.id, interaction.guild.id, None, interaction.user.id, "ban_" + outcome, self.reason)
        except Exception:
            logger.exception("Falha registrando auditoria de banimento")


class BanCandidateSelectView(discord.ui.View):
    def __init__(self, bot_instance, rows, reason, requester_id):
        super().__init__(timeout=120)
        self.bot_instance = bot_instance; self.reason = reason; self.requester_id = requester_id
        options = [discord.SelectOption(label=f"{r['player_name'][:70]}", description=f"Discord {r['user_id']} • UID {r['ff_id']}"[:100], value=str(r["user_id"])) for r in rows[:25]]
        select = discord.ui.Select(placeholder="Selecione a conta Discord correta", min_values=1, max_values=1, options=options)
        async def callback(interaction):
            if interaction.user.id != self.requester_id:
                return await interaction.response.send_message("Somente quem solicitou pode selecionar.", ephemeral=True)
            uid = int(select.values[0])
            await interaction.response.send_message(f"Confirme o banimento da conta <@{uid}> (`{uid}`). Motivo: {self.reason}", view=BanConfirmView(self.bot_instance, uid, self.reason, self.requester_id), ephemeral=True)
        select.callback = callback; self.add_item(select)


@bot.tree.command(name="ban", description="ADM: localiza e bane uma conta por Discord, UID Free Fire ou WhatsApp.")
@app_commands.guild_only()
async def ban_command(interaction: discord.Interaction, identificador: str, motivo: str = "Não informado"):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando dentro do servidor.", ephemeral=True)
    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.ban_members):
        return await interaction.response.send_message("❌ Apenas ADM autorizada com permissão Banir membros pode usar este comando.", ephemeral=True)
    raw = identificador.strip()
    match = re.fullmatch(r"<@!?(\\d{15,22})>|(\\d{15,22})", raw)
    if match:
        uid = int(match.group(1) or match.group(2))
        try: target = await bot.fetch_user(uid)
        except discord.HTTPException:
            return await interaction.response.send_message("❌ Não consegui localizar essa conta Discord.", ephemeral=True)
        member = interaction.guild.get_member(uid)
        if member is None:
            return await interaction.response.send_message("❌ A conta não está no servidor; este comando não bane IDs externos.", ephemeral=True)
        return await interaction.response.send_message(f"Confirme o banimento de {member.mention} (`{uid}`). Motivo: {motivo}", view=BanConfirmView(bot, uid, motivo, interaction.user.id), ephemeral=True)
    normalized = re.sub(r"\\D", "", raw)
    rows = bot.recruitment.db.execute(
        "SELECT user_id,player_name,ff_id,whatsapp FROM recruitment_applications WHERE guild_id=?",
        (interaction.guild.id,),
    ).fetchall()
    # Busca estritamente na base existente e normaliza apenas os dígitos do telefone.
    rows = [r for r in rows if str(r["ff_id"]).strip() == raw or (normalized and re.sub(r"\\D", "", str(r["whatsapp"] or "")) == normalized)]
    unique = {int(r["user_id"]): r for r in rows}
    if not unique:
        return await interaction.response.send_message("❌ Nenhuma conta Discord associada a esse UID/WhatsApp foi encontrada na base de candidaturas.", ephemeral=True)
    candidates = list(unique.values())
    if len(candidates) > 1:
        return await interaction.response.send_message("Encontrei mais de uma correspondência. Selecione a conta correta:", view=BanCandidateSelectView(bot, candidates, motivo, interaction.user.id), ephemeral=True)
    uid = int(candidates[0]["user_id"])
    member = interaction.guild.get_member(uid)
    if member is None:
        return await interaction.response.send_message(f"O cadastro está associado ao Discord `{uid}`, mas essa conta não está no servidor. Nenhum banimento foi executado.", ephemeral=True)
    await interaction.response.send_message(f"Correspondência encontrada: {member.mention} (`{uid}`), nick **{candidates[0]['player_name']}**. Confirme o banimento. Motivo: {motivo}", view=BanConfirmView(bot, uid, motivo, interaction.user.id), ephemeral=True)


@bot.tree.command(name="lista_candidatos", description="Abre o painel de pesquisa e gerenciamento de candidatos.")
async def lista_candidatos(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild_id is None:
        return await interaction.response.send_message("Use este comando dentro do servidor.", ephemeral=True)
    member = interaction.user
    if not (member.guild_permissions.administrator or any(r.name.casefold() in STAFF_ROLE_NAMES for r in getattr(member, "roles", []))):
        return await interaction.response.send_message("Somente ADM/equipe pode consultar a lista de candidaturas.", ephemeral=True)
    total = bot.recruitment.db.execute("SELECT COUNT(*) FROM recruitment_applications WHERE guild_id=?", (interaction.guild_id,)).fetchone()[0]
    embed = discord.Embed(title="💎 Painel de candidatos EVOM", description=(
        f"**Cadastros salvos:** `{total}`\n\n"
        "Use **Pesquisar candidato** para buscar por ID do Free Fire, WhatsApp ou nick. "
        "Nos resultados, você pode remover um cadastro sem expulsar o usuário do Discord.\n\n"
        "🔒 Os dados de contato são exibidos apenas para a equipe autorizada."), color=discord.Color.from_rgb(130, 70, 220))
    view = discord.ui.View(timeout=180)
    async def search_callback(btn_interaction):
        from recruitment import CandidateSearchModal
        if not (btn_interaction.user.guild_permissions.administrator or any(r.name.casefold() in STAFF_ROLE_NAMES for r in getattr(btn_interaction.user, "roles", []))):
            return await btn_interaction.response.send_message("Somente ADM/equipe pode pesquisar candidatos.", ephemeral=True)
        await btn_interaction.response.send_modal(CandidateSearchModal(bot.recruitment))
    search_button = discord.ui.Button(label="🔎 Pesquisar candidato", style=discord.ButtonStyle.primary)
    search_button.callback = search_callback
    view.add_item(search_button)
    async def all_callback(btn_interaction):
        if not (btn_interaction.user.guild_permissions.administrator or any(r.name.casefold() in STAFF_ROLE_NAMES for r in getattr(btn_interaction.user, "roles", []))):
            return await btn_interaction.response.send_message("Somente ADM/equipe pode consultar candidatos.", ephemeral=True)
        from recruitment import CandidateAllView
        rows=bot.recruitment.db.execute("SELECT user_id,player_name,ff_id,whatsapp,status,created_at FROM recruitment_applications WHERE guild_id=? ORDER BY created_at DESC",(btn_interaction.guild_id,)).fetchall()
        if not rows: return await btn_interaction.response.send_message("Ainda não há candidatos cadastrados.",ephemeral=True)
        all_view=CandidateAllView(bot.recruitment,rows)
        await btn_interaction.response.send_message(embed=all_view.build_embed(),view=all_view,ephemeral=True)
    all_button=discord.ui.Button(label="📋 Ver todos",style=discord.ButtonStyle.secondary)
    all_button.callback=all_callback
    view.add_item(all_button)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


def main() -> None:
    # FadeHost espera uma aplicação HTTP ouvindo na porta definida em PORT.
    # Este endpoint simples de saúde não interfere no funcionamento do bot Discord.
    health_server = start_health_server()
    if not BOT_TOKEN:
        logger.error("O Secret %s não está configurado.", TOKEN_ENV_NAME)
        raise SystemExit(1)
    try:
        bot.run(BOT_TOKEN, log_handler=None)
    except KeyboardInterrupt:
        logger.info("Bot encerrado.")
    except discord.LoginFailure:
        logger.exception(
            "Falha de login no Discord. Verifique se DISCORD_TOKEN ou BOT_TOKEN contém um token válido."
        )
        raise SystemExit(1) from None
    except discord.PrivilegedIntentsRequired:
        logger.exception(
            "O Discord exigiu intents privilegiadas. Verifique Server Members Intent."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
