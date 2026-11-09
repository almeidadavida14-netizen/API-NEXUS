from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import TYPE_CHECKING

import discord

from config import MATCH_CATEGORY, STAFF_ROLE_NAMES
from database import TournamentPaymentBatch, TournamentPayout
from pix import build_pix_payload, format_brl, generate_pix_qr_png
from views import (
    ConfirmTournamentFinalizeView,
    ConfirmTournamentPayoutView,
    TournamentPaymentsView,
    TournamentPayoutView,
    TournamentPixModal,
)

if TYPE_CHECKING:
    from main import TreinamentoBot

logger = logging.getLogger(__name__)


class TournamentPayments:
    """Creates and recovers a private Pix-payment channel per finished tournament."""

    def __init__(self, bot: TreinamentoBot):
        self.bot = bot
        self.database = bot.database
        self.provisioning_tasks: dict[int, asyncio.Task[None]] = {}
        self.cleanup_tasks: dict[int, asyncio.Task[None]] = {}
        self.batch_locks: dict[int, asyncio.Lock] = {}
        self.registered_views: set[tuple[str, int, int]] = set()

    async def close(self) -> None:
        tasks = [*self.provisioning_tasks.values(), *self.cleanup_tasks.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.provisioning_tasks.clear()
        self.cleanup_tasks.clear()

    async def restore(self, guild: discord.Guild) -> None:
        batches = self.database.list_tournament_payment_batches(
            guild.id, ("provisioning", "open", "closing")
        )
        for batch in batches:
            self._register_persistent_views(batch)
            if batch.status in {"provisioning", "open"}:
                self.schedule_provisioning(guild, batch.tournament_id)
            elif batch.status == "closing":
                self.schedule_cleanup(guild, batch.tournament_id)

    def _register_persistent_views(self, batch: TournamentPaymentBatch) -> None:
        if batch.summary_message_id:
            key = ("summary", batch.tournament_id, 0)
            if key not in self.registered_views:
                self.bot.add_view(
                    TournamentPaymentsView(
                        self.bot,
                        batch.tournament_id,
                        enabled=batch.status == "open",
                    ),
                    message_id=batch.summary_message_id,
                )
                self.registered_views.add(key)
        for payout in self.database.list_tournament_payouts(batch.tournament_id):
            if not payout.message_id:
                continue
            key = ("payout", batch.tournament_id, payout.user_id)
            if key in self.registered_views:
                continue
            self.bot.add_view(
                self._payout_view(batch, payout),
                message_id=payout.message_id,
            )
            self.registered_views.add(key)

    def _payout_view(
        self, batch: TournamentPaymentBatch, payout: TournamentPayout
    ) -> TournamentPayoutView:
        return TournamentPayoutView(
            self.bot,
            batch.tournament_id,
            payout.user_id,
            enabled=batch.status == "open",
            has_pix=bool(
                payout.pix_key and payout.recipient_name and payout.recipient_city
            ),
            has_amount=bool(payout.amount_cents and payout.amount_cents > 0),
            paid=payout.status == "paid",
        )

    def schedule_provisioning(
        self, guild: discord.Guild, tournament_id: int
    ) -> None:
        current = self.provisioning_tasks.get(tournament_id)
        if current and not current.done():
            return
        task = asyncio.create_task(
            self._provisioning_worker(guild, tournament_id),
            name=f"tournament-payment-provision-{tournament_id}",
        )
        self.provisioning_tasks[tournament_id] = task
        task.add_done_callback(
            lambda finished, tid=tournament_id: self._forget_task(
                self.provisioning_tasks, tid, finished
            )
        )

    @staticmethod
    def _forget_task(
        task_map: dict[int, asyncio.Task[None]],
        tournament_id: int,
        task: asyncio.Task[None],
    ) -> None:
        if task_map.get(tournament_id) is task:
            task_map.pop(tournament_id, None)

    async def _provisioning_worker(
        self, guild: discord.Guild, tournament_id: int
    ) -> None:
        while not self.bot.is_closed():
            batch = self.database.get_tournament_payment_batch(tournament_id)
            if not batch or batch.guild_id != guild.id:
                return
            if batch.status not in {"provisioning", "open"}:
                return
            try:
                if await self.ensure_channel(guild, tournament_id):
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Falha ao preparar pagamentos do torneio #%04d.",
                    tournament_id,
                )
            await asyncio.sleep(60)

    def _topic_marker(self, tournament_id: int) -> str:
        return f"galinha-v8:pagamentos:{tournament_id}"

    def _message_marker(self, tournament_id: int, user_id: int | None) -> str:
        suffix = "resumo" if user_id is None else f"jogador:{user_id}"
        return f"ref interna {self._topic_marker(tournament_id)}:{suffix}"

    async def _payment_overwrites(
        self, guild: discord.Guild, payouts: list[TournamentPayout]
    ) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False)
        }
        for role in guild.roles:
            if role.name.casefold() in STAFF_ROLE_NAMES:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                )
        for member in guild.members:
            if self.bot.is_staff_member(member):
                overwrites[member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                )
        for payout in payouts:
            member = guild.get_member(payout.user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(payout.user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None
            if member is not None and not self.bot.is_staff_member(member):
                overwrites[member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True,
                )
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                embed_links=True,
                attach_files=True,
            )
        return overwrites

    async def _find_channel(
        self, guild: discord.Guild, batch: TournamentPaymentBatch
    ) -> discord.TextChannel | None:
        if batch.channel_id:
            channel = guild.get_channel(batch.channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
            try:
                fetched = await guild.fetch_channel(batch.channel_id)
            except discord.NotFound:
                fetched = None
            if isinstance(fetched, discord.TextChannel):
                return fetched
        marker = self._topic_marker(batch.tournament_id)
        return next(
            (
                channel
                for channel in guild.text_channels
                if marker in (channel.topic or "")
            ),
            None,
        )

    async def _create_channel(
        self,
        guild: discord.Guild,
        batch: TournamentPaymentBatch,
        payouts: list[TournamentPayout],
    ) -> discord.TextChannel:
        config = self.database.get_guild_config(guild.id)
        category = guild.get_channel(config.match_category_id or 0)
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.get(guild.categories, name=MATCH_CATEGORY)
        return await guild.create_text_channel(
            "💸・pagamentos",
            category=category if isinstance(category, discord.CategoryChannel) else None,
            overwrites=await self._payment_overwrites(guild, payouts),
            topic=self._topic_marker(batch.tournament_id),
            reason=f"Pagamentos dos campeões do torneio #{batch.tournament_id:04d}",
        )

    async def _find_message(
        self,
        channel: discord.TextChannel,
        stored_message_id: int | None,
        marker: str,
    ) -> discord.Message | None:
        if stored_message_id:
            try:
                stored_message = await channel.fetch_message(stored_message_id)
                if marker in stored_message.content or any(
                    embed.footer.text and marker in embed.footer.text
                    for embed in stored_message.embeds
                ):
                    return stored_message
            except discord.NotFound:
                pass
        async for message in channel.history(limit=100):
            if marker in message.content:
                return message
            if any(
                embed.footer.text and marker in embed.footer.text
                for embed in message.embeds
            ):
                return message
        return None

    def _payout_embed(
        self,
        batch: TournamentPaymentBatch,
        payout: TournamentPayout,
    ) -> discord.Embed:
        if payout.status == "paid":
            payment_status = "✅ Pago e registrado"
        elif payout.pix_key and payout.recipient_name and payout.recipient_city:
            payment_status = "🟡 Dados Pix recebidos; aguardando conferência do ADM"
        else:
            payment_status = "⏳ Aguardando os dados Pix do jogador"
        embed = discord.Embed(
            title=f"💸 Pagamento • Torneio #{batch.tournament_id:04d}",
            description=f"Jogador: <@{payout.user_id}>",
            color=discord.Color.green()
            if payout.status == "paid"
            else discord.Color.gold(),
        )
        embed.add_field(
            name="Valor individual",
            value=format_brl(payout.amount_cents),
            inline=True,
        )
        embed.add_field(name="Status", value=payment_status, inline=False)
        embed.add_field(
            name="Dados Pix",
            value=(
                "Recebidos em formulário privado."
                if payout.pix_key and payout.recipient_name and payout.recipient_city
                else "Ainda não informados."
            ),
            inline=False,
        )
        if payout.status == "paid" and payout.paid_by:
            embed.add_field(
                name="Conferido por",
                value=f"<@{payout.paid_by}>",
                inline=True,
            )
        embed.set_footer(
            text=self._message_marker(batch.tournament_id, payout.user_id)
        )
        return embed

    def _summary_embed(
        self,
        batch: TournamentPaymentBatch,
        payouts: list[TournamentPayout],
    ) -> discord.Embed:
        tournament = self.database.get_tournament(batch.tournament_id)
        players = [payout.user_id for payout in payouts]
        embed = discord.Embed(
            title=f"💸 Pagamentos • Torneio #{batch.tournament_id:04d}",
            description=(
                f"Equipe campeã: {', '.join(f'<@{user_id}>' for user_id in players) or '—'}"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Valor da premiação",
            value=format_brl(batch.total_amount_cents),
            inline=True,
        )
        embed.add_field(
            name="Formato",
            value=tournament.mode if tournament else "Torneio",
            inline=True,
        )
        split_lines = [
            f"<@{payout.user_id}> — {format_brl(payout.amount_cents)}"
            for payout in payouts
        ]
        embed.add_field(
            name="Divisão por campeão",
            value="\n".join(split_lines) or "Nenhum integrante da equipe foi encontrado.",
            inline=False,
        )
        paid_count = sum(payout.status == "paid" for payout in payouts)
        embed.add_field(
            name="Pagamentos confirmados",
            value=f"{paid_count}/{len(payouts)}",
            inline=True,
        )
        if batch.status == "closing" and batch.close_at:
            embed.add_field(
                name="Encerramento",
                value=f"Canal agendado para exclusão em {batch.close_at}.",
                inline=False,
            )
        elif batch.total_amount_cents is None:
            embed.add_field(
                name="Próximos passos",
                value="A premiação não foi definida; nenhum pagamento pode ser marcado.",
                inline=False,
            )
        else:
            embed.add_field(
                name="Próximos passos",
                value=(
                    "Cada campeão informa os dados Pix no formulário privado. "
                    "O QR Code é mostrado apenas ao ADM."
                ),
                inline=False,
            )
        embed.set_footer(text=self._message_marker(batch.tournament_id, None))
        return embed

    async def ensure_channel(
        self, guild: discord.Guild, tournament_id: int
    ) -> bool:
        lock = self.batch_locks.setdefault(tournament_id, asyncio.Lock())
        async with lock:
            return await self._ensure_channel_locked(guild, tournament_id)

    async def _ensure_channel_locked(
        self, guild: discord.Guild, tournament_id: int
    ) -> bool:
        batch = self.database.get_tournament_payment_batch(tournament_id)
        if (
            not batch
            or batch.guild_id != guild.id
            or batch.status not in {"provisioning", "open"}
        ):
            return False
        payouts = self.database.list_tournament_payouts(tournament_id)
        channel = await self._find_channel(guild, batch)
        if channel is None:
            channel = await self._create_channel(guild, batch, payouts)
        if not self.database.set_tournament_payment_channel(
            tournament_id, channel.id
        ):
            return False
        batch = self.database.get_tournament_payment_batch(tournament_id)
        if not batch:
            return False

        enabled = batch.status == "open"
        for payout in payouts:
            marker = self._message_marker(tournament_id, payout.user_id)
            message = await self._find_message(channel, payout.message_id, marker)
            embed = self._payout_embed(batch, payout)
            view = self._payout_view(batch, payout)
            content = f"<@{payout.user_id}>"
            allowed_mentions = discord.AllowedMentions(
                users=True, roles=False, everyone=False
            )
            if message:
                message = await message.edit(
                    content=content,
                    embed=embed,
                    view=view,
                    allowed_mentions=allowed_mentions,
                )
            else:
                message = await channel.send(
                    content=content,
                    embed=embed,
                    view=view,
                    allowed_mentions=allowed_mentions,
                )
            self.database.set_tournament_payout_message_id(
                tournament_id, payout.user_id, message.id
            )
            self.registered_views.add(("payout", tournament_id, payout.user_id))

        batch = self.database.get_tournament_payment_batch(tournament_id)
        if not batch:
            return False
        payouts = self.database.list_tournament_payouts(tournament_id)
        summary_marker = self._message_marker(tournament_id, None)
        summary = await self._find_message(
            channel, batch.summary_message_id, summary_marker
        )
        summary_embed = self._summary_embed(batch, payouts)
        summary_view = TournamentPaymentsView(
            self.bot, tournament_id, enabled=batch.status == "open"
        )
        champion_mentions = ", ".join(
            f"<@{payout.user_id}>" for payout in payouts
        ) or "—"
        summary_content = f"🏆 Campeões do torneio #{tournament_id:04d}: {champion_mentions}"
        allowed_mentions = discord.AllowedMentions(
            users=True, roles=False, everyone=False
        )
        if summary:
            summary = await summary.edit(
                content=summary_content,
                embed=summary_embed,
                view=summary_view,
                allowed_mentions=allowed_mentions,
            )
        else:
            summary = await channel.send(
                content=summary_content,
                embed=summary_embed,
                view=summary_view,
                allowed_mentions=allowed_mentions,
            )
        self.database.set_tournament_payment_summary_message_id(
            tournament_id, summary.id
        )
        self.registered_views.add(("summary", tournament_id, 0))

        if not self.database.mark_tournament_payment_batch_open(tournament_id):
            return False
        if not enabled:
            return await self._ensure_channel_locked(guild, tournament_id)
        return True

    async def open_pix_modal(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
        user_id: int,
    ) -> None:
        if interaction.user.id != user_id:
            await interaction.response.send_message(
                "Este formulário só pode ser enviado pelo jogador indicado.",
                ephemeral=True,
            )
            return
        batch = self.database.get_tournament_payment_batch(tournament_id)
        payout = self.database.get_tournament_payout(tournament_id, user_id)
        if (
            not interaction.guild
            or not batch
            or batch.guild_id != interaction.guild.id
            or batch.status not in {"provisioning", "open"}
            or not payout
            or payout.status == "paid"
        ):
            await interaction.response.send_message(
                "Este formulário não está mais disponível.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            TournamentPixModal(
                self.bot,
                tournament_id,
                user_id,
                pix_key=payout.pix_key or "",
                recipient_name=payout.recipient_name or "",
                recipient_city=payout.recipient_city or "",
            )
        )

    async def submit_pix(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
        user_id: int,
        pix_key: str,
        recipient_name: str,
        recipient_city: str,
    ) -> None:
        if interaction.user.id != user_id:
            await interaction.response.send_message(
                "Somente o jogador indicado pode informar esses dados.",
                ephemeral=True,
            )
            return
        batch = self.database.get_tournament_payment_batch(tournament_id)
        if (
            not interaction.guild
            or not batch
            or batch.guild_id != interaction.guild.id
            or batch.status not in {"provisioning", "open"}
        ):
            await interaction.response.send_message(
                "Este formulário não está mais disponível.", ephemeral=True
            )
            return
        payout = self.database.get_tournament_payout(tournament_id, user_id)
        if not payout:
            await interaction.response.send_message(
                "Pagamento não encontrado.", ephemeral=True
            )
            return
        try:
            build_pix_payload(
                pix_key,
                recipient_name,
                recipient_city,
                payout.amount_cents,
            )
        except (ValueError, UnicodeError) as error:
            await interaction.response.send_message(
                f"❌ {error}", ephemeral=True
            )
            return
        result = self.database.submit_tournament_pix(
            tournament_id,
            user_id,
            pix_key.strip(),
            recipient_name.strip(),
            recipient_city.strip(),
        )
        if result != "saved":
            await interaction.response.send_message(
                "Não foi possível salvar os dados Pix; o torneio pode já estar encerrado.",
                ephemeral=True,
            )
            return
        if interaction.guild:
            await interaction.response.defer(ephemeral=True)
            try:
                await self.ensure_channel(interaction.guild, tournament_id)
            except discord.HTTPException:
                logger.exception(
                    "Dados Pix salvos, mas a mensagem do torneio #%04d não foi atualizada.",
                    tournament_id,
                )
                self.schedule_provisioning(interaction.guild, tournament_id)
            await interaction.edit_original_response(
                content=(
                    "✅ Dados recebidos em privado. A chave não será exibida no canal; "
                    "o ADM poderá gerar o QR Code após a conferência."
                )
            )
        else:
            await interaction.response.send_message(
                "✅ Dados recebidos em privado. A chave não será exibida no canal; "
                "o ADM poderá gerar o QR Code após a conferência.",
                ephemeral=True,
            )

    async def handle_pix_qr(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
        user_id: int,
    ) -> None:
        if (
            not isinstance(interaction.user, discord.Member)
            or not self.bot.is_staff_member(interaction.user)
        ):
            await interaction.response.send_message(
                "❌ Apenas ADM/staff pode consultar o QR Code.",
                ephemeral=True,
            )
            return
        payout = self.database.get_tournament_payout(tournament_id, user_id)
        batch = self.database.get_tournament_payment_batch(tournament_id)
        if (
            not interaction.guild
            or not batch
            or batch.guild_id != interaction.guild.id
            or batch.status != "open"
            or not payout
            or payout.amount_cents is None
            or payout.amount_cents <= 0
            or not payout.pix_key
            or not payout.recipient_name
            or not payout.recipient_city
        ):
            await interaction.response.send_message(
                "O QR Code ainda não pode ser gerado: confira o valor e os dados Pix.",
                ephemeral=True,
            )
            return
        try:
            payload = build_pix_payload(
                payout.pix_key,
                payout.recipient_name,
                payout.recipient_city,
                payout.amount_cents,
            )
            image = generate_pix_qr_png(payload)
        except (ValueError, UnicodeError) as error:
            await interaction.response.send_message(
                f"Não foi possível gerar o QR Code Pix: {error}",
                ephemeral=True,
            )
            return
        filename = f"pix-t{tournament_id}-u{user_id}.png"
        await interaction.response.send_message(
            content=(
                f"QR Pix de **{format_brl(payout.amount_cents)}** para <@{user_id}>. "
                f"Pix copia e cola:\n```{payload}```"
            ),
            file=discord.File(BytesIO(image), filename=filename),
            ephemeral=True,
        )

    async def handle_payout_paid(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
        user_id: int,
    ) -> None:
        if (
            not isinstance(interaction.user, discord.Member)
            or not self.bot.is_staff_member(interaction.user)
        ):
            await interaction.response.send_message(
                "❌ Apenas ADM/staff pode registrar pagamentos.",
                ephemeral=True,
            )
            return
        payout = self.database.get_tournament_payout(tournament_id, user_id)
        batch = self.database.get_tournament_payment_batch(tournament_id)
        if (
            not interaction.guild
            or not payout
            or not batch
            or batch.guild_id != interaction.guild.id
            or batch.status != "open"
        ):
            await interaction.response.send_message(
                "Pagamento não encontrado ou já encerrado.", ephemeral=True
            )
            return
        if payout.status == "paid":
            await interaction.response.send_message(
                "Este pagamento já foi registrado.", ephemeral=True
            )
            return
        if (
            payout.amount_cents is None
            or payout.amount_cents <= 0
            or not payout.pix_key
            or not payout.recipient_name
            or not payout.recipient_city
        ):
            await interaction.response.send_message(
                "O jogador ainda não informou todos os dados Pix ou não há valor definido.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Confirme que conferiu a transferência de **{format_brl(payout.amount_cents)}** "
            f"para <@{user_id}> antes de registrá-la como paga.",
            view=ConfirmTournamentPayoutView(
                self.bot, tournament_id, user_id, interaction.user.id
            ),
            ephemeral=True,
        )

    async def confirm_payout_paid(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
        user_id: int,
        owner_id: int,
    ) -> None:
        if (
            interaction.user.id != owner_id
            or not isinstance(interaction.user, discord.Member)
            or not self.bot.is_staff_member(interaction.user)
            or not interaction.guild
        ):
            await interaction.response.send_message(
                "❌ Apenas o ADM/staff que abriu a confirmação pode concluí-la.",
                ephemeral=True,
            )
            return
        batch = self.database.get_tournament_payment_batch(tournament_id)
        if not batch or batch.guild_id != interaction.guild.id:
            await interaction.response.edit_message(
                content="Pagamento não encontrado neste servidor.",
                view=None,
            )
            return
        result = self.database.mark_tournament_payout_paid(
            tournament_id, user_id, owner_id
        )
        if result == "paid":
            guild = interaction.guild
            await interaction.response.edit_message(
                content=f"✅ Pagamento de <@{user_id}> registrado como pago.",
                view=None,
            )
            if batch and guild:
                try:
                    await self.ensure_channel(guild, tournament_id)
                except discord.HTTPException:
                    logger.exception(
                        "Pagamento salvo, mas a mensagem do torneio #%04d não foi atualizada.",
                        tournament_id,
                    )
                    self.schedule_provisioning(guild, tournament_id)
            return
        messages = {
            "already_paid": "Este pagamento já foi registrado anteriormente.",
            "not_ready": "Faltam dados Pix ou o valor da premiação.",
            "closed": "O canal de pagamentos já foi encerrado.",
            "not_found": "Pagamento não encontrado.",
        }
        await interaction.response.edit_message(
            content=messages.get(result, "Não foi possível registrar o pagamento."),
            view=None,
        )

    async def handle_finalize(
        self, interaction: discord.Interaction, tournament_id: int
    ) -> None:
        if (
            not isinstance(interaction.user, discord.Member)
            or not self.bot.is_staff_member(interaction.user)
        ):
            await interaction.response.send_message(
                "❌ Apenas ADM/staff pode finalizar os pagamentos.",
                ephemeral=True,
            )
            return
        batch = self.database.get_tournament_payment_batch(tournament_id)
        if (
            not interaction.guild
            or not batch
            or batch.guild_id != interaction.guild.id
            or batch.status != "open"
        ):
            await interaction.response.send_message(
                "Este canal de pagamentos não está aberto.", ephemeral=True
            )
            return
        payouts = self.database.list_tournament_payouts(tournament_id)
        unpaid = sum(payout.status != "paid" for payout in payouts)
        if batch.total_amount_cents and unpaid:
            await interaction.response.send_message(
                f"Há **{unpaid}** pagamento(s) ainda não confirmado(s). "
                "Registre todos após conferir as transferências antes de finalizar.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Confirme a finalização. O canal será excluído 1 minuto após a confirmação.",
            view=ConfirmTournamentFinalizeView(
                self.bot, tournament_id, interaction.user.id
            ),
            ephemeral=True,
        )

    async def confirm_finalize(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
        owner_id: int,
    ) -> None:
        if (
            interaction.user.id != owner_id
            or not isinstance(interaction.user, discord.Member)
            or not self.bot.is_staff_member(interaction.user)
            or not interaction.guild
        ):
            await interaction.response.send_message(
                "❌ Apenas o ADM/staff que abriu a confirmação pode concluí-la.",
                ephemeral=True,
            )
            return
        result, unpaid = self.database.begin_tournament_payment_finalization(
            tournament_id, interaction.guild.id, owner_id
        )
        if result == "unpaid":
            await interaction.response.edit_message(
                content=(
                    f"Ainda há **{unpaid}** pagamento(s) não confirmado(s). "
                    "O canal permanece aberto."
                ),
                view=None,
            )
            return
        if result != "scheduled":
            await interaction.response.edit_message(
                content="Este canal já foi finalizado ou não está aberto.",
                view=None,
            )
            return
        batch = self.database.get_tournament_payment_batch(tournament_id)
        if batch:
            self.schedule_cleanup(interaction.guild, tournament_id)
        await interaction.response.edit_message(
            content="✅ Finalização confirmada. O canal será excluído em 1 minuto.",
            view=None,
        )
        if batch:
            try:
                await self._disable_channel_controls(interaction.guild, batch)
            except discord.HTTPException:
                logger.exception(
                    "Finalização do torneio #%04d foi agendada, mas os controles não foram atualizados.",
                    tournament_id,
                )

    async def _disable_channel_controls(
        self, guild: discord.Guild, batch: TournamentPaymentBatch
    ) -> None:
        try:
            channel = await self._find_channel(guild, batch)
        except discord.HTTPException:
            logger.exception(
                "Não foi possível carregar o canal de pagamentos do torneio #%04d.",
                batch.tournament_id,
            )
            return
        if channel is None:
            return
        for payout in self.database.list_tournament_payouts(batch.tournament_id):
            if not payout.message_id:
                continue
            try:
                message = await channel.fetch_message(payout.message_id)
                await message.edit(view=self._payout_view(batch, payout))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
        if batch.summary_message_id:
            try:
                message = await channel.fetch_message(batch.summary_message_id)
                await message.edit(
                    embed=self._summary_embed(
                        batch,
                        self.database.list_tournament_payouts(batch.tournament_id),
                    ),
                    view=TournamentPaymentsView(
                        self.bot, batch.tournament_id, enabled=False
                    ),
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    def schedule_cleanup(
        self, guild: discord.Guild, tournament_id: int
    ) -> None:
        current = self.cleanup_tasks.get(tournament_id)
        if current and not current.done():
            return
        task = asyncio.create_task(
            self._cleanup_worker(guild, tournament_id),
            name=f"tournament-payment-cleanup-{tournament_id}",
        )
        self.cleanup_tasks[tournament_id] = task
        task.add_done_callback(
            lambda finished, tid=tournament_id: self._forget_task(
                self.cleanup_tasks, tid, finished
            )
        )

    async def _cleanup_worker(
        self, guild: discord.Guild, tournament_id: int
    ) -> None:
        while not self.bot.is_closed():
            batch = self.database.get_tournament_payment_batch(tournament_id)
            if (
                not batch
                or batch.guild_id != guild.id
                or batch.status != "closing"
                or not batch.close_at
            ):
                return
            expected_close_at = batch.close_at
            deadline = datetime.fromisoformat(expected_close_at)
            delay = max(
                0.0,
                (deadline - datetime.now(timezone.utc)).total_seconds(),
            )
            if delay:
                await asyncio.sleep(delay)
            latest = self.database.get_tournament_payment_batch(tournament_id)
            if (
                not latest
                or latest.status != "closing"
                or latest.close_at != expected_close_at
            ):
                return
            try:
                channel = await self._find_channel(guild, latest)
            except discord.HTTPException:
                logger.exception(
                    "Falha ao localizar o canal de pagamentos do torneio #%04d.",
                    tournament_id,
                )
                await asyncio.sleep(60)
                continue
            if channel is None:
                self.database.mark_tournament_payment_batch_closed(
                    tournament_id, expected_close_at
                )
                return
            if self._topic_marker(tournament_id) not in (channel.topic or ""):
                logger.error(
                    "Canal de pagamentos #%04d não corresponde ao identificador salvo; "
                    "a exclusão foi bloqueada por segurança.",
                    tournament_id,
                )
                await asyncio.sleep(60)
                continue
            try:
                await channel.delete(
                    reason=f"Pagamentos finalizados do torneio #{tournament_id:04d}"
                )
            except discord.NotFound:
                pass
            except asyncio.CancelledError:
                raise
            except discord.HTTPException:
                logger.exception(
                    "Não foi possível excluir o canal de pagamentos do torneio #%04d.",
                    tournament_id,
                )
                await asyncio.sleep(60)
                continue
            self.database.mark_tournament_payment_batch_closed(
                tournament_id, expected_close_at
            )
            return
