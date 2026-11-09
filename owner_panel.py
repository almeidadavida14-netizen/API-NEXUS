"""Private owner-only control panel."""
from __future__ import annotations

import logging
import asyncio
from typing import Iterable

import aiohttp
import discord
from discord import app_commands

from config import OWNER_USER_ID

logger = logging.getLogger(__name__)


def is_owner(interaction: discord.Interaction) -> bool:
    return int(getattr(interaction.user, "id", 0)) == OWNER_USER_ID


async def reject_non_owner(interaction: discord.Interaction) -> bool:
    if is_owner(interaction):
        return False
    message = "⛔ Este painel é exclusivo do dono do bot."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return True


def owner_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.gold())


class OwnerHomeView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await reject_non_owner(interaction)

    @discord.ui.button(label="Candidatos", emoji="🧹", style=discord.ButtonStyle.secondary, row=0)
    async def candidates(self, interaction, button):
        count = self.bot.recruitment.db.execute(
            "SELECT COUNT(*) FROM recruitment_applications WHERE guild_id=?",
            (interaction.guild_id,),
        ).fetchone()[0] if interaction.guild_id else 0
        embed = owner_embed("🧹 Gerenciar candidatos",
            f"Cadastros neste servidor: **{count}**\n\nVocê pode consultar a quantidade ou apagar os cadastros deste servidor. O histórico de auditoria será preservado.")
        await interaction.response.edit_message(embed=embed, view=CandidateToolsView(self.bot))

    @discord.ui.button(label="X-TREINO", emoji="🎮", style=discord.ButtonStyle.secondary, row=0)
    async def xtreino(self, interaction, button):
        system = self.bot.xtreino_system
        total = system.db.execute("SELECT COUNT(*) FROM xtreino_events WHERE guild_id=?", (interaction.guild_id,)).fetchone()[0]
        embed = owner_embed("🎮 Gerenciar X-TREINO",
            f"Eventos registrados neste servidor: **{total}**\n\nA reinicialização remove os registros de eventos e inscrições do X-TREINO deste servidor para reiniciar a numeração. Estatísticas gerais dos jogadores e canais/categorias não serão apagados.")
        await interaction.response.edit_message(embed=embed, view=XTreinoToolsView(self.bot))

    @discord.ui.button(label="Banir", emoji="🔨", style=discord.ButtonStyle.danger, row=1)
    async def ban(self, interaction, button):
        await interaction.response.edit_message(embed=owner_embed("🔨 Banir membros", "Escolha se deseja selecionar um cargo ou membros específicos."), view=MemberActionStartView(self.bot, "ban"))

    @discord.ui.button(label="Expulsar", emoji="👢", style=discord.ButtonStyle.danger, row=1)
    async def kick(self, interaction, button):
        await interaction.response.edit_message(embed=owner_embed("👢 Expulsar membros", "Escolha se deseja selecionar um cargo ou membros específicos."), view=MemberActionStartView(self.bot, "kick"))

    @discord.ui.button(label="Personalizar bot", emoji="🤖", style=discord.ButtonStyle.primary, row=2)
    async def customize(self, interaction, button):
        embed = owner_embed("🤖 Personalizar bot",
            "Escolha o que deseja alterar.\n\n• Foto: cole um link de imagem hospedado no Discord (CDN).\n• Nome: altera o nome de usuário do bot.\n• Status: altera a atividade exibida.")
        await interaction.response.edit_message(embed=embed, view=BotCustomizeView(self.bot))

    @discord.ui.button(label="Personalizar mensagens", emoji="✏️", style=discord.ButtonStyle.primary, row=4)
    async def customize_messages(self, interaction, button):
        from message_editor import MessageEditorHomeView
        await interaction.response.edit_message(
            embed=owner_embed("✏️ PERSONALIZAR MENSAGENS", "Escolha uma categoria para editar as mensagens do bot. As alterações ficam salvas no banco de dados."),
            view=MessageEditorHomeView(self.bot),
        )

    @discord.ui.button(label="Backup de categorias", emoji="📦", style=discord.ButtonStyle.secondary, row=3)
    async def structure_backups(self, interaction, button):
        from structure_backup import StructureBackupHomeView
        await interaction.response.edit_message(
            embed=owner_embed("📦 Backup e restauração", "Salve categorias e canais antes de excluir. Depois, restaure a estrutura pelo backup."),
            view=StructureBackupHomeView(self.bot),
        )

    @discord.ui.button(label="Central Gamer IA", emoji="🎮", style=discord.ButtonStyle.primary, row=4)
    async def central_gamer(self, interaction, button):
        from central_gamer import CentralGamerAdminView
        await interaction.response.edit_message(
            embed=owner_embed("🎮 Central Gamer IA", "Crie ou gerencie a Central Gamer. Os atendimentos dos jogadores acontecem em canais privados."),
            view=CentralGamerAdminView(self.bot),
        )

    @discord.ui.button(label="Limpar mensagens", emoji="🧽", style=discord.ButtonStyle.danger, row=3)
    async def clear_messages(self, interaction, button):
        await interaction.response.edit_message(
            embed=owner_embed("🧽 Limpar mensagens", "Escolha o canal que deseja limpar. O bot vai mostrar a quantidade encontrada antes de pedir sua confirmação."),
            view=MessageCleanupChannelView(self.bot),
        )

    @discord.ui.button(label="Informações", emoji="📊", style=discord.ButtonStyle.secondary, row=2)
    async def info(self, interaction, button):
        guild = interaction.guild
        queues = self.bot.queue_manager.status(guild.id) if guild else {}
        active = self.bot.database.list_active_matches(guild.id) if guild else []
        xt_count = self.bot.xtreino_system.db.execute(
            "SELECT COUNT(*) FROM xtreino_events WHERE guild_id=?", (guild.id,)
        ).fetchone()[0] if guild else 0
        candidates = self.bot.recruitment.db.execute(
            "SELECT COUNT(*) FROM recruitment_applications WHERE guild_id=?", (guild.id,)
        ).fetchone()[0] if guild else 0
        embed = owner_embed("📊 Informações do servidor",
            f"**Bot:** {'Online' if self.bot.is_ready() else 'Inicializando'}\n"
            f"**Servidor:** {guild.name if guild else '—'}\n"
            f"**Membros:** {guild.member_count if guild else '—'}\n"
            f"**Candidatos:** {candidates}\n**Eventos X-TREINO:** {xt_count}\n"
            f"**Partidas ativas:** {len(active)}\n**Jogadores nas filas:** {sum(queues.values())}")
        await interaction.response.edit_message(embed=embed, view=BackHomeView(self.bot))

    @discord.ui.button(label="Fechar", emoji="✖️", style=discord.ButtonStyle.secondary, row=2)
    async def close(self, interaction, button):
        await interaction.response.edit_message(content="Painel fechado.", embed=None, view=None)


class BackHomeView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot

    @discord.ui.button(label="↩️ Voltar ao painel", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if await reject_non_owner(interaction):
            return
        await interaction.response.edit_message(embed=owner_embed("👑 Painel do Dono", "Escolha uma categoria:"), view=OwnerHomeView(self.bot))


class MessageCleanupChannelView(discord.ui.View):
    """Choose one text channel for a message cleanup operation."""
    def __init__(self, bot):
        super().__init__(timeout=180)
        self.bot = bot
        self.add_item(MessageCleanupChannelSelect())

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        if await reject_non_owner(interaction):
            return
        await interaction.response.edit_message(
            embed=owner_embed("👑 Painel do Dono", "Escolha uma categoria:"),
            view=OwnerHomeView(self.bot),
        )


class MessageCleanupChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Selecione um canal de texto",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if await reject_non_owner(interaction):
            return
        # ChannelSelect may provide a resolved channel wrapper depending on the
        # discord.py/API version. Resolve it again from the guild cache by ID.
        if interaction.guild is None:
            return await interaction.response.send_message(
                "❌ Essa opção só pode ser usada dentro de um servidor.", ephemeral=True
            )
        selected = self.values[0]
        selected_id = getattr(selected, "id", None)
        channel = None
        if selected_id is not None:
            getter = getattr(interaction.guild, "get_channel_or_thread", None)
            if getter is not None:
                channel = getter(selected_id)
            if channel is None:
                channel = interaction.guild.get_channel(selected_id)
            if channel is None:
                channel = interaction.guild.get_thread(selected_id)
        if channel is None or getattr(channel, "type", None) not in (
            discord.ChannelType.text,
            discord.ChannelType.news,
        ):
            return await interaction.response.send_message(
                "❌ Não consegui localizar esse canal de texto. Tente selecioná-lo novamente.",
                ephemeral=True
            )
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.view_channel or not perms.read_message_history or not perms.manage_messages:
            return await interaction.response.send_message(
                "❌ Preciso das permissões **Ver canal**, **Ler histórico de mensagens** e **Gerenciar mensagens** nesse canal.",
                ephemeral=True,
            )
        await interaction.response.edit_message(
            embed=owner_embed(
                "🧽 O que deseja apagar?",
                f"Canal escolhido: {channel.mention}\n\n"
                "• **Todas:** todas as mensagens que o bot conseguir excluir nesse canal.\n"
                "• **Últimas X:** você informa a quantidade de mensagens mais recentes.\n\n"
                "O Discord não permite exclusão em massa de mensagens com mais de 14 dias; essas serão removidas individualmente quando possível."
            ),
            view=MessageCleanupModeView(channel),
        )


class MessageCleanupModeView(discord.ui.View):
    def __init__(self, channel):
        super().__init__(timeout=180)
        self.channel_id = channel.id
        self.guild_id = channel.guild.id

    async def _preview(self, interaction, limit):
        if await reject_non_owner(interaction):
            return
        channel = interaction.guild.get_channel(self.channel_id)
        if channel is None:
            return await interaction.response.send_message("❌ Não encontrei mais esse canal.", ephemeral=True)
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.view_channel or not perms.read_message_history or not perms.manage_messages:
            return await interaction.response.send_message("❌ Faltam permissões para limpar esse canal.", ephemeral=True)
        await interaction.response.defer()
        try:
            count = 0
            async for _message in channel.history(limit=limit, oldest_first=False):
                count += 1
            if count == 0:
                return await interaction.edit_original_response(
                    embed=owner_embed("Canal vazio", "Não encontrei mensagens para apagar."),
                    view=BackHomeView(interaction.client),
                )
            mode = "todas as mensagens" if limit is None else f"as últimas **{limit}** mensagens"
            await interaction.edit_original_response(
                embed=owner_embed(
                    "⚠️ Confirmar limpeza do canal",
                    f"**Canal:** {channel.mention}\n**Opção:** {mode}\n**Mensagens encontradas:** {count}\n\n"
                    "Essa ação não pode ser desfeita pelo painel. Confirma a exclusão?"
                ),
                view=MessageCleanupConfirmView(channel.id, limit, count),
            )
        except discord.Forbidden:
            await interaction.edit_original_response(
                embed=owner_embed("Sem permissão", "Não consegui ler o histórico desse canal."),
                view=BackHomeView(interaction.client),
            )
        except discord.HTTPException:
            logger.exception("Falha ao contar mensagens para limpeza.")
            await interaction.edit_original_response(
                embed=owner_embed("Falha ao consultar canal", "O Discord não permitiu consultar o histórico. Tente novamente."),
                view=BackHomeView(interaction.client),
            )

    @discord.ui.button(label="🗑️ Todas as mensagens", style=discord.ButtonStyle.danger, row=0)
    async def all_messages(self, interaction, button):
        await self._preview(interaction, None)

    @discord.ui.button(label="🔢 Últimas X mensagens", style=discord.ButtonStyle.primary, row=0)
    async def last_messages(self, interaction, button):
        if await reject_non_owner(interaction):
            return
        await interaction.response.send_modal(MessageCleanupAmountModal(self.channel_id))

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        if await reject_non_owner(interaction):
            return
        await interaction.response.edit_message(
            embed=owner_embed("🧽 Limpar mensagens", "Escolha o canal que deseja limpar."),
            view=MessageCleanupChannelView(interaction.client),
        )


class MessageCleanupAmountModal(discord.ui.Modal, title="Limpar últimas mensagens"):
    amount = discord.ui.TextInput(
        label="Quantidade de mensagens",
        placeholder="Ex.: 100",
        min_length=1,
        max_length=5,
    )

    def __init__(self, channel_id):
        super().__init__(timeout=180)
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        if await reject_non_owner(interaction):
            return
        raw = str(self.amount).strip()
        if not raw.isdigit() or not 1 <= int(raw) <= 10000:
            return await interaction.response.send_message(
                "Informe um número entre **1 e 10.000**.", ephemeral=True
            )
        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if channel is None:
            return await interaction.response.send_message("❌ Não encontrei mais esse canal.", ephemeral=True)
        view = MessageCleanupModeView(channel)
        await view._preview(interaction, int(raw))


class MessageCleanupConfirmView(discord.ui.View):
    def __init__(self, channel_id, limit, expected_count):
        super().__init__(timeout=120)
        self.channel_id = channel_id
        self.limit = limit
        self.expected_count = expected_count

    @discord.ui.button(label="✅ Confirmar exclusão", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if await reject_non_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(self.channel_id)
        if channel is None:
            return await interaction.followup.send("❌ Não encontrei mais esse canal.", ephemeral=True)
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.view_channel or not perms.read_message_history or not perms.manage_messages:
            return await interaction.followup.send("❌ Faltam permissões para limpar esse canal.", ephemeral=True)
        try:
            deleted = await channel.purge(
                limit=self.limit,
                bulk=True,
                reason=f"Limpeza solicitada pelo dono do bot (ID {interaction.user.id})",
            )
            await interaction.followup.send(
                f"✅ Limpeza concluída em {channel.mention}. Mensagens apagadas: **{len(deleted)}**.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ O Discord negou a exclusão. Confira a permissão Gerenciar mensagens.", ephemeral=True)
        except discord.HTTPException:
            logger.exception("Falha ao excluir mensagens do canal pelo painel do dono.")
            await interaction.followup.send("❌ O Discord não permitiu concluir a limpeza. Veja os logs do bot.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if await reject_non_owner(interaction):
            return
        await interaction.response.edit_message(
            embed=owner_embed("Limpeza cancelada", "Nenhuma mensagem foi apagada."),
            view=BackHomeView(interaction.client),
        )


class CandidateToolsView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(label="🗑️ Resetar lista de candidatos", style=discord.ButtonStyle.danger)
    async def reset(self, interaction, button):
        if await reject_non_owner(interaction):
            return
        await interaction.response.edit_message(
            embed=owner_embed("⚠️ Confirmar reset de candidatos",
                "Isso apagará os cadastros da tabela de candidaturas **somente deste servidor**. Os registros de auditoria serão mantidos. Deseja continuar?"),
            view=ConfirmResetView(self.bot, "candidates"))

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.edit_message(embed=owner_embed("👑 Painel do Dono", "Escolha uma categoria:"), view=OwnerHomeView(self.bot))


class XTreinoToolsView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(label="🔄 Zerar numeração do X-TREINO", style=discord.ButtonStyle.danger)
    async def reset(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.edit_message(
            embed=owner_embed("⚠️ Confirmar reset do X-TREINO",
                "Isso apagará os eventos, inscrições e sorteios registrados neste servidor para reiniciar a numeração. **As estatísticas gerais dos jogadores e os canais/categorias não serão apagados.** Essa ação não pode ser desfeita pelo painel."),
            view=ConfirmResetView(self.bot, "xtreino"))

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.edit_message(embed=owner_embed("👑 Painel do Dono", "Escolha uma categoria:"), view=OwnerHomeView(self.bot))


class ConfirmResetView(discord.ui.View):
    def __init__(self, bot, kind: str):
        super().__init__(timeout=90)
        self.bot = bot
        self.kind = kind

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await asyncio.to_thread(self.bot.database.create_backup_snapshot)
            guild_id = interaction.guild_id
            if not guild_id:
                return await interaction.followup.send("Use o painel dentro de um servidor.", ephemeral=True)
            if self.kind == "candidates":
                with self.bot.recruitment.db:
                    cur = self.bot.recruitment.db.execute(
                        "DELETE FROM recruitment_applications WHERE guild_id=?", (guild_id,))
                result = f"✅ Lista de candidatos resetada. {cur.rowcount} cadastro(s) removido(s). Os registros de auditoria foram preservados."
            else:
                db = self.bot.xtreino_system.db
                event_rows = db.execute(
                    "SELECT * FROM xtreino_events WHERE guild_id=?", (guild_id,)).fetchall()
                event_ids = [int(row["id"]) for row in event_rows]
                config_row = db.execute(
                    "SELECT channel_id,admin_channel_id FROM xtreino_config WHERE guild_id=?", (guild_id,)
                ).fetchone()
                public_channel_id = int(config_row["channel_id"]) if config_row and config_row["channel_id"] else None
                admin_channel_id = int(config_row["admin_channel_id"]) if config_row and config_row["admin_channel_id"] else None
                stored_messages = set()
                for event in event_rows:
                    if public_channel_id and event["public_message_id"]:
                        stored_messages.add((public_channel_id, int(event["public_message_id"])))
                    if event["announcement_channel_id"] and event["announcement_message_id"]:
                        stored_messages.add((int(event["announcement_channel_id"]), int(event["announcement_message_id"])))
                    if admin_channel_id and event["admin_message_id"]:
                        stored_messages.add((admin_channel_id, int(event["admin_message_id"])))
                if event_ids:
                    marks = ",".join("?" for _ in event_ids)
                    for row in db.execute(
                        f"SELECT admin_message_id FROM xtreino_event_signups WHERE event_id IN ({marks})",
                        event_ids
                    ).fetchall():
                        if admin_channel_id and row["admin_message_id"]:
                            stored_messages.add((admin_channel_id, int(row["admin_message_id"])))
                # Remove apenas mensagens que o próprio sistema registrou para esses eventos,
                # evitando que mensagens antigas com botões apontem para IDs reutilizados.
                for channel_id, message_id in stored_messages:
                    channel = interaction.guild.get_channel(channel_id)
                    if channel is None:
                        try:
                            channel = await interaction.guild.fetch_channel(channel_id)
                        except discord.HTTPException:
                            channel = None
                    if channel is None or not hasattr(channel, "fetch_message"):
                        raise RuntimeError("Não consegui acessar um canal com mensagem antiga do X-TREINO; o reset foi cancelado para evitar IDs duplicados.")
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.delete()
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as exc:
                        raise RuntimeError("Não foi possível remover uma mensagem antiga do X-TREINO; o reset foi cancelado para evitar IDs duplicados.") from exc
                with db:
                    if event_ids:
                        marks = ",".join("?" for _ in event_ids)
                        for table in ("xtreino_event_signups", "xtreino_event_team_draws", "xtreino_event_team_members"):
                            db.execute(f"DELETE FROM {table} WHERE event_id IN ({marks})", event_ids)
                        db.execute("DELETE FROM xtreino_events WHERE guild_id=?", (guild_id,))
                    remaining = db.execute("SELECT COALESCE(MAX(id),0) FROM xtreino_events").fetchone()[0]
                    db.execute("DELETE FROM sqlite_sequence WHERE name='xtreino_events'")
                    if remaining:
                        db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('xtreino_events',?)", (int(remaining),))
                result = f"✅ Reset do X-TREINO concluído. {len(event_ids)} evento(s), inscrições, sorteios e mensagens antigas registradas foram removidos. Estatísticas e canais/categorias foram preservados."
            await interaction.followup.send(result, ephemeral=True)
        except Exception:
            logger.exception("Falha no reset solicitado pelo painel do dono.")
            await interaction.followup.send("❌ Não consegui concluir o reset. Confira os logs; o backup preventivo foi tentado antes da alteração.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.edit_message(embed=owner_embed("Cancelado", "Nenhum dado foi alterado."), view=BackHomeView(self.bot))


class MemberActionStartView(discord.ui.View):
    def __init__(self, bot, action: str):
        super().__init__(timeout=180)
        self.bot = bot
        self.action = action

    @discord.ui.button(label="Selecionar cargo", emoji="🎭", style=discord.ButtonStyle.primary)
    async def by_role(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.edit_message(embed=owner_embed("Selecionar cargo", "Escolha um cargo. O bot vai ignorar contas de bot e respeitar a hierarquia do Discord."), view=RoleTargetView(self.bot, self.action))

    @discord.ui.button(label="Selecionar membros", emoji="👥", style=discord.ButtonStyle.secondary)
    async def by_members(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.edit_message(embed=owner_embed("Selecionar membros", "Selecione até 25 membros. Contas de bot serão ignoradas."), view=MemberTargetView(self.bot, self.action))

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.edit_message(embed=owner_embed("👑 Painel do Dono", "Escolha uma categoria:"), view=OwnerHomeView(self.bot))


class RoleTargetView(discord.ui.View):
    def __init__(self, bot, action):
        super().__init__(timeout=180)
        self.bot = bot
        self.action = action
        self.add_item(RoleTargetSelect(bot, action))

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        if await reject_non_owner(interaction): return
        label = "Banir" if self.action == "ban" else "Expulsar"
        await interaction.response.edit_message(embed=owner_embed(label, "Escolha o método de seleção."), view=MemberActionStartView(self.bot, self.action))


class RoleTargetSelect(discord.ui.RoleSelect):
    def __init__(self, bot, action):
        super().__init__(placeholder="Escolha um cargo", min_values=1, max_values=1, row=0)
        self.bot = bot
        self.action = action

    async def callback(self, interaction):
        if await reject_non_owner(interaction): return
        role = self.values[0]
        if role.is_default():
            return await interaction.response.send_message("Não é permitido selecionar @everyone para uma ação em massa.", ephemeral=True)
        await interaction.response.defer()
        guild = interaction.guild
        members = []
        try:
            async for member in guild.fetch_members(limit=None):
                if not member.bot and role in member.roles:
                    members.append(member)
        except discord.HTTPException:
            members = [m for m in guild.members if not m.bot and role in m.roles]
        members = [m for m in members if self._can_act(interaction, m)]
        await show_action_confirmation(interaction, self.bot, self.action, members, f"Cargo: {role.name}", deferred=True)

    @staticmethod
    def _can_act(interaction, member):
        guild = interaction.guild
        bot_member = guild.me
        if member.id in {guild.owner_id, interaction.client.user.id, OWNER_USER_ID}:
            return False
        if bot_member is None or member.top_role >= bot_member.top_role:
            return False
        return True


class MemberTargetView(discord.ui.View):
    def __init__(self, bot, action):
        super().__init__(timeout=180)
        self.bot = bot
        self.action = action
        self.add_item(MemberTargetSelect(bot, action))

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        if await reject_non_owner(interaction): return
        label = "Banir" if self.action == "ban" else "Expulsar"
        await interaction.response.edit_message(embed=owner_embed(label, "Escolha o método de seleção."), view=MemberActionStartView(self.bot, self.action))


class MemberTargetSelect(discord.ui.UserSelect):
    def __init__(self, bot, action):
        super().__init__(placeholder="Selecione até 25 membros", min_values=1, max_values=25, row=0)
        self.bot = bot
        self.action = action

    async def callback(self, interaction):
        if await reject_non_owner(interaction): return
        await interaction.response.defer()
        selected = []
        for user in self.values:
            if user.bot:
                continue
            member = interaction.guild.get_member(user.id)
            if member is None:
                try: member = await interaction.guild.fetch_member(user.id)
                except discord.HTTPException: continue
            if member.id in {interaction.guild.owner_id, OWNER_USER_ID} or member.top_role >= interaction.guild.me.top_role:
                continue
            selected.append(member)
        await show_action_confirmation(interaction, self.bot, self.action, selected, "Seleção individual", deferred=True)


async def show_action_confirmation(interaction, bot, action, members, source, deferred=False):
    label = "banidos" if action == "ban" else "expulsos"
    ids = [m.id for m in members]
    names = "\n".join(f"• {discord.utils.escape_markdown(m.display_name)} (`{m.id}`)" for m in members[:12])
    if len(members) > 12:
        names += f"\n… e mais {len(members)-12} membro(s)"
    if not members:
        embed = owner_embed("Nenhum membro elegível", "Não encontrei membros elegíveis. Bots, @everyone, dono do servidor e membros acima/iguais à hierarquia do bot foram ignorados.")
        if deferred:
            return await interaction.edit_original_response(embed=embed, view=BackHomeView(bot))
        return await interaction.response.edit_message(embed=embed, view=BackHomeView(bot))
    embed = owner_embed("⚠️ Confirmar ação em massa",
        f"**Ação:** {'Banir' if action == 'ban' else 'Expulsar'}\n**Origem:** {source}\n**Total:** {len(ids)}\n\n{names}\n\nConfirme para continuar.")
    view = BulkActionConfirmView(bot, action, ids)
    if deferred:
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


class BulkActionConfirmView(discord.ui.View):
    def __init__(self, bot, action, member_ids):
        super().__init__(timeout=120)
        self.bot = bot
        self.action = action
        self.member_ids = list(member_ids)

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        bot_member = guild.me
        permission = bot_member.guild_permissions.ban_members if self.action == "ban" else bot_member.guild_permissions.kick_members
        if not permission:
            return await interaction.followup.send(f"❌ O bot precisa da permissão {'Banir' if self.action == 'ban' else 'Expulsar'} membros.", ephemeral=True)
        succeeded = 0
        failed = 0
        for user_id in self.member_ids:
            member = guild.get_member(user_id)
            if member is None:
                try: member = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    failed += 1
                    continue
            if member.bot or member.id == guild.owner_id or member.top_role >= bot_member.top_role:
                failed += 1
                continue
            try:
                reason = f"Ação em massa pelo painel privado do dono (ID {interaction.user.id})"
                if self.action == "ban":
                    await guild.ban(member, reason=reason, delete_message_seconds=0)
                else:
                    await guild.kick(member, reason=reason)
                succeeded += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
        await interaction.followup.send(f"Concluído: **{succeeded}** {'banimento(s)' if self.action == 'ban' else 'expulsão(ões)'}. Falhas/ignorados: **{failed}**.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.edit_message(embed=owner_embed("Cancelado", "Nenhuma pessoa foi banida ou expulsa."), view=BackHomeView(self.bot))


class BotCustomizeView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(label="🖼️ Alterar foto (link CDN Discord)", style=discord.ButtonStyle.primary)
    async def avatar(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.send_modal(AvatarModal(self.bot))

    @discord.ui.button(label="✏️ Alterar nome", style=discord.ButtonStyle.secondary)
    async def name(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.send_modal(BotNameModal(self.bot))

    @discord.ui.button(label="🎮 Alterar status/atividade", style=discord.ButtonStyle.secondary)
    async def status(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.send_modal(BotStatusModal(self.bot))

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if await reject_non_owner(interaction): return
        await interaction.response.edit_message(embed=owner_embed("👑 Painel do Dono", "Escolha uma categoria:"), view=OwnerHomeView(self.bot))


class AvatarModal(discord.ui.Modal, title="Alterar foto do bot"):
    url = discord.ui.TextInput(label="Link da imagem no CDN do Discord", placeholder="https://cdn.discordapp.com/...", max_length=500)
    def __init__(self, bot):
        super().__init__(timeout=180)
        self.bot = bot
    async def on_submit(self, interaction):
        if await reject_non_owner(interaction): return
        link = str(self.url).strip()
        if not link.startswith(("https://cdn.discordapp.com/", "https://media.discordapp.net/")):
            return await interaction.response.send_message("Use um link de imagem hospedado no CDN do Discord.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(link, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status != 200:
                        return await interaction.followup.send("Não consegui baixar essa imagem.", ephemeral=True)
                    content_type = response.headers.get("Content-Type", "")
                    data = await response.read()
            if not content_type.startswith("image/") or len(data) > 8 * 1024 * 1024:
                return await interaction.followup.send("O link precisa ser uma imagem de até 8 MB.", ephemeral=True)
            await self.bot.user.edit(avatar=data)
            await interaction.followup.send("✅ Foto do bot atualizada.", ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("❌ O Discord recusou a alteração da foto. Tente outra imagem mais tarde.", ephemeral=True)
        except Exception:
            logger.exception("Falha ao alterar avatar do bot.")
            await interaction.followup.send("❌ Não consegui alterar a foto do bot.", ephemeral=True)


class BotNameModal(discord.ui.Modal, title="Alterar nome do bot"):
    name = discord.ui.TextInput(label="Novo nome", max_length=32)
    def __init__(self, bot):
        super().__init__(timeout=180)
        self.bot = bot
    async def on_submit(self, interaction):
        if await reject_non_owner(interaction): return
        try:
            await self.bot.user.edit(username=str(self.name).strip())
            await interaction.response.send_message("✅ Nome do bot atualizado.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("❌ O Discord recusou a alteração. Pode haver limite temporário de mudança de nome.", ephemeral=True)


class BotStatusModal(discord.ui.Modal, title="Alterar status/atividade"):
    activity = discord.ui.TextInput(label="Texto da atividade (vazio para limpar)", required=False, max_length=100)
    def __init__(self, bot):
        super().__init__(timeout=180)
        self.bot = bot
    async def on_submit(self, interaction):
        if await reject_non_owner(interaction): return
        value = str(self.activity).strip()
        try:
            await self.bot.change_presence(activity=discord.Game(name=value) if value else None)
            await interaction.response.send_message("✅ Status/atividade atualizado.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("❌ Não consegui atualizar o status.", ephemeral=True)


def register_owner_panel(bot) -> None:
    @bot.tree.command(name="paineldono", description="Abre o painel privado exclusivo do dono do bot.")
    @app_commands.guild_only()
    async def paineldono(interaction: discord.Interaction):
        if await reject_non_owner(interaction):
            return
        await interaction.response.send_message(
            embed=owner_embed("👑 Painel do Dono", "Escolha uma categoria:"),
            view=OwnerHomeView(bot),
            ephemeral=True,
        )
