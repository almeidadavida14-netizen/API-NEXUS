"""Persistent recruitment applications and approval announcements for EVOM NEXUS."""
from __future__ import annotations
import re, sqlite3
import discord
from message_editor import customized_embed
from discord import app_commands
from config import DATABASE_PATH, STAFF_ROLE_NAMES

CATEGORY_NAME = "🎯・RECRUTAMENTO"
PANEL_CHANNEL = "📋・recrutamento"
INFO_CHANNEL = "🎮・fazer-teste"
ADMIN_NOTICE_CHANNEL_ID = 1564959665262039220

class ApplicationModal(discord.ui.Modal, title="Candidatura ALL EVOM"):
    player_name = discord.ui.TextInput(label="Seu nome / nick", max_length=80)
    ff_id = discord.ui.TextInput(label="ID do Free Fire", max_length=32)
    whatsapp = discord.ui.TextInput(label="WhatsApp com DDD", placeholder="Ex.: 11987654321", max_length=20)
    availability = discord.ui.TextInput(label="Disponibilidade", max_length=100)
    def __init__(self, system):
        super().__init__(timeout=300); self.system=system
    async def on_submit(self, interaction: discord.Interaction):
        await self.system.create_application(interaction, str(self.player_name), str(self.ff_id), str(self.whatsapp), str(self.availability))

class ApplyView(discord.ui.View):
    def __init__(self, system):
        super().__init__(timeout=None); self.system=system
    @discord.ui.button(label="📝 Candidatar-se", style=discord.ButtonStyle.success, custom_id="evom:recruit:apply")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplicationModal(self.system))

class CandidateView(discord.ui.View):
    def __init__(self, system):
        super().__init__(timeout=None)
        self.system = system

    def staff(self, member):
        return member.guild_permissions.administrator or any(
            r.name.casefold() in STAFF_ROLE_NAMES for r in getattr(member, "roles", [])
        )

    async def get_application(self, interaction):
        row = self.system.get_by_channel(interaction.channel_id)
        if not row:
            await interaction.response.send_message("Candidatura não encontrada.", ephemeral=True)
            return None
        return row

    @discord.ui.button(label="👥 Selecionar ADM para jogar", style=discord.ButtonStyle.secondary, custom_id="evom:recruit:select_partner")
    async def select_partner(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await self.get_application(interaction)
        if not row: return
        if row["status"] != "testing" or int(row["staff_id"] or 0) != interaction.user.id:
            return await interaction.response.send_message("Somente o ADM responsável pode selecionar outro ADM durante um teste ativo.", ephemeral=True)
        await interaction.response.send_message("Selecione o ADM que vai participar do teste. Ele não poderá aprovar nem reprovar.", view=TestPartnerSelectView(self.system, int(row["user_id"]), interaction.user.id), ephemeral=True)

    @discord.ui.button(label="🔁 Transferir responsável", style=discord.ButtonStyle.secondary, custom_id="evom:recruit:transfer_owner")
    async def transfer_owner(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await self.get_application(interaction)
        if not row: return
        if row["status"] != "testing":
            return await interaction.response.send_message("Só é possível transferir a responsabilidade de um teste ativo.", ephemeral=True)
        if not (interaction.guild.owner_id == interaction.user.id or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Somente um ADM superior pode transferir a responsabilidade.", ephemeral=True)
        await interaction.response.send_message("Selecione o novo ADM responsável pelo teste.", view=TestTransferSelectView(self.system, int(row["user_id"]), int(row["staff_id"] or 0)), ephemeral=True)

    @discord.ui.button(label="❌ Reprovado", style=discord.ButtonStyle.danger, custom_id="evom:recruit:decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.staff(interaction.user):
            return await interaction.response.send_message("Somente ADM/equipe pode usar isso.", ephemeral=True)
        row = await self.get_application(interaction)
        if not row:
            return
        if row["status"] != "testing" or int(row["staff_id"] or 0) != interaction.user.id:
            return await interaction.response.send_message("Somente o ADM responsável por este teste pode reprovar.", ephemeral=True)
        self.system.update(row["user_id"], "rejected", interaction.user.id)
        self.system.log_event(row["user_id"], interaction.guild_id, interaction.channel_id, interaction.user.id, "rejected", "Candidatura reprovada")
        await interaction.response.send_message(
            f"❌ Resultado anunciado: candidatura reprovada por {interaction.user.mention}.\n"
            "Este canal será excluído em **40 segundos**."
        )
        await self.system.close_later(interaction.channel, 40)

    @discord.ui.button(label="✅ Aprovado", style=discord.ButtonStyle.success, custom_id="evom:recruit:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.staff(interaction.user):
            return await interaction.response.send_message("Somente ADM/equipe pode usar isso.", ephemeral=True)
        row = await self.get_application(interaction)
        if not row:
            return
        if row["status"] != "testing" or int(row["staff_id"] or 0) != interaction.user.id:
            return await interaction.response.send_message("Somente o ADM responsável por este teste pode aprovar.", ephemeral=True)
        self.system.update(row["user_id"], "approved", interaction.user.id)
        role_ok, role_message = await self.system.grant_member_role(interaction.guild, row["user_id"])
        self.system.log_event(row["user_id"], interaction.guild_id, interaction.channel_id, interaction.user.id, "approved", "Candidato aprovado; " + role_message)
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} marcou a candidatura como aprovada.\n{role_message}\n"
            "Este canal será excluído em **40 segundos**."
        )
        await self.system.close_later(interaction.channel, 40)

    @discord.ui.button(label="📣 Anunciar", style=discord.ButtonStyle.primary, custom_id="evom:recruit:announce")
    async def announce(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.staff(interaction.user):
            return await interaction.response.send_message("Somente ADM/equipe pode usar isso.", ephemeral=True)
        row = await self.get_application(interaction)
        if not row:
            return
        if int(row["staff_id"] or 0) != interaction.user.id:
            return await interaction.response.send_message("Somente o ADM responsável pode anunciar esta candidatura.", ephemeral=True)
        if row["status"] != "approved":
            return await interaction.response.send_message("Primeiro marque a candidatura como **✅ Aprovado**.", ephemeral=True)
        if self.system.was_announced(row["user_id"]):
            return await interaction.response.send_message("O anúncio desta candidatura já foi enviado.", ephemeral=True)
        await interaction.response.send_message(
            "Escolha o canal de destino e clique em **Enviar anúncio** para publicar:",
            view=AnnouncementView(self.system, row["user_id"], interaction.user.id), ephemeral=True
        )


class TestPartnerSelectView(discord.ui.View):
    def __init__(self, system, user_id, responsible_id):
        super().__init__(timeout=120); self.system=system; self.user_id=user_id; self.responsible_id=responsible_id
        self.add_item(TestPartnerSelect(system, user_id, responsible_id))

class TestPartnerSelect(discord.ui.UserSelect):
    def __init__(self, system, user_id, responsible_id):
        super().__init__(placeholder="Escolha o ADM participante", min_values=1, max_values=1)
        self.system=system; self.user_id=user_id; self.responsible_id=responsible_id
    async def callback(self, interaction):
        if interaction.user.id != self.responsible_id:
            return await interaction.response.send_message("Somente o responsável pode selecionar.", ephemeral=True)
        member=self.values[0]
        if not isinstance(member, discord.Member) or not (member.guild_permissions.administrator or any(r.name.casefold() in STAFF_ROLE_NAMES for r in member.roles)):
            return await interaction.response.send_message("Selecione um ADM/staff do servidor.", ephemeral=True)
        row=self.system.get(self.user_id); channel=interaction.guild.get_channel(int(row["channel_id"] or 0)) if row else None
        if not channel: return await interaction.response.send_message("Canal de teste não encontrado.", ephemeral=True)
        await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
        self.system.set_partner(self.user_id, member.id)
        self.system.log_event(self.user_id, interaction.guild_id, channel.id, interaction.user.id, "test_partner_selected", f"ADM participante: {member.id}")
        await interaction.response.send_message(f"✅ {member.mention} foi adicionado ao teste. Ele não pode aprovar ou reprovar.", ephemeral=True)

class TestTransferSelectView(discord.ui.View):
    def __init__(self, system, user_id, old_id):
        super().__init__(timeout=120); self.system=system; self.user_id=user_id; self.old_id=old_id
        self.add_item(TestTransferSelect(system,user_id,old_id))

class TestTransferSelect(discord.ui.UserSelect):
    def __init__(self, system, user_id, old_id):
        super().__init__(placeholder="Escolha o novo responsável", min_values=1, max_values=1)
        self.system=system; self.user_id=user_id; self.old_id=old_id
    async def callback(self, interaction):
        if not (interaction.guild.owner_id == interaction.user.id or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Somente ADM superior pode transferir.", ephemeral=True)
        member=self.values[0]
        if not isinstance(member, discord.Member) or not (member.guild_permissions.administrator or any(r.name.casefold() in STAFF_ROLE_NAMES for r in member.roles)):
            return await interaction.response.send_message("Selecione um ADM/staff.", ephemeral=True)
        if not self.system.transfer_responsible(self.user_id,self.old_id,member.id):
            return await interaction.response.send_message("Não foi possível transferir. Confira se o teste ainda está ativo.", ephemeral=True)
        row=self.system.get(self.user_id); channel=interaction.guild.get_channel(int(row["channel_id"] or 0)) if row else None
        if channel:
            await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
            old=interaction.guild.get_member(self.old_id)
            if old: await channel.set_permissions(old, overwrite=None)
            async for msg in channel.history(limit=30):
                if msg.author.id == self.system.bot.user.id and msg.embeds:
                    try: await msg.edit(view=CandidateView(self.system))
                    except discord.HTTPException: pass
                    break
        await interaction.response.send_message(f"✅ Responsabilidade transferida para {member.mention}.",ephemeral=True)


class AnnouncementView(discord.ui.View):
    def __init__(self, system, user_id, staff_id):
        super().__init__(timeout=300)
        self.system = system
        self.user_id = user_id
        self.staff_id = staff_id
        self.channel_id = None

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="Selecione o canal do anúncio", min_values=1, max_values=1)
    async def channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        if interaction.user.id != self.staff_id:
            return await interaction.response.send_message("Somente o ADM responsável pode confirmar.", ephemeral=True)
        self.channel_id = select.values[0].id
        await interaction.response.send_message(f"Canal selecionado: {select.values[0].mention}. Clique em **Enviar anúncio** para publicar.", ephemeral=True)

    @discord.ui.button(label="📣 Enviar anúncio", style=discord.ButtonStyle.success)
    async def send_announcement(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.staff_id:
            return await interaction.response.send_message("Somente o ADM responsável pode confirmar.", ephemeral=True)
        if not self.channel_id:
            return await interaction.response.send_message("Selecione um canal primeiro.", ephemeral=True)
        row = self.system.get(self.user_id)
        if not row or row["status"] != "approved":
            return await interaction.response.send_message("A candidatura não está marcada como aprovada.", ephemeral=True)
        if self.system.was_announced(self.user_id):
            return await interaction.response.send_message("Este anúncio já foi enviado.", ephemeral=True)
        if not self.system.reserve_announcement(self.user_id):
            return await interaction.response.send_message("O anúncio já está sendo enviado ou foi concluído.", ephemeral=True)
        channel = interaction.guild.get_channel(self.channel_id)
        if channel is None:
            self.system.release_announcement(self.user_id)
            return await interaction.response.send_message("Não encontrei o canal selecionado. Escolha outro canal.", ephemeral=True)
        member = interaction.guild.get_member(self.user_id)
        staff_id = row["staff_id"] or self.staff_id
        staff = interaction.guild.get_member(staff_id)
        name = discord.utils.escape_markdown(row["player_name"])
        staff_text = staff.mention if staff else f"<@{staff_id}>"
        description = (
            f"Deem as boas-vindas a {member.mention if member else name}!\n\n"
            "O jogador foi **aprovado no teste** e agora faz parte da nossa família.\n\n"
            f"**Nome/Nick:** {name}\n**ID do Free Fire:** `{row['ff_id']}`\n"
            f"**ID do Discord:** `{row['user_id']}`\n**Aprovado por:** {staff_text}"
        )
        embed = customized_embed("new_member_announcement", "🏆 NOVO MEMBRO NA ALL EVOM! 🔥", description, 0xD2192D, member=(member.mention if member else name), name=name, ff_id=row["ff_id"], user_id=row["user_id"], staff=staff_text)
        try:
            message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        except (discord.Forbidden, discord.HTTPException) as exc:
            self.system.release_announcement(self.user_id)
            return await interaction.response.send_message(f"Não consegui enviar o anúncio: {exc}", ephemeral=True)
        self.system.finish_announcement(self.user_id)
        self.system.log_event(self.user_id, interaction.guild_id, row["channel_id"], interaction.user.id, "announcement_sent", f"Anúncio publicado no canal {channel.id}; mensagem {message.id}")
        await interaction.response.send_message(f"Anúncio enviado em {channel.mention}.", ephemeral=True)

class AnalysisNoticeView(discord.ui.View):
    """Controles do aviso de candidatura no canal privado da ADM."""
    def __init__(self, system, user_id):
        super().__init__(timeout=None)
        self.system = system
        self.user_id = int(user_id)
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id:
                item.custom_id = f"{item.custom_id}:{self.user_id}"

    def staff(self, member):
        return bool(getattr(member.guild_permissions, "administrator", False)) or any(
            r.name.casefold() in STAFF_ROLE_NAMES or r.permissions.administrator
            for r in getattr(member, "roles", [])
        )

    async def _remove_notice(self, interaction):
        """Remove imediatamente a mensagem original da candidatura após o aceite."""
        try:
            await interaction.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            # Se o bot não tiver Manage Messages, mantém o fluxo e registra no log.
            self.system.log_event(
                self.user_id,
                interaction.guild_id,
                interaction.channel_id,
                interaction.user.id,
                "notice_delete_failed",
                "Não foi possível apagar a mensagem original da candidatura; confira a permissão Gerenciar Mensagens.",
            )

    @discord.ui.button(label="🎮 Aceitar para teste", style=discord.ButtonStyle.primary, custom_id="evom:recruit:claim_analysis")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.staff(interaction.user):
            return await interaction.response.send_message("Somente ADM/equipe autorizada pode aceitar candidaturas.", ephemeral=True)
        row = self.system.get(self.user_id)
        if not row or row["guild_id"] != interaction.guild_id:
            return await interaction.response.send_message("Candidatura não encontrada.", ephemeral=True)
        # A reserva no SQLite acontece antes da criação: cliques concorrentes não
        # conseguem abrir dois canais para a mesma candidatura.
        if not self.system.reserve_test(self.user_id, interaction.user.id):
            return await interaction.response.send_message("Esta candidatura já foi processada ou está sendo aceita por outro administrador.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = None
        created_channel = False
        try:
            guild = interaction.guild
            category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
            if category is None:
                category = await guild.create_category(CATEGORY_NAME, reason="Canal privado de teste de candidatura")
            safe = re.sub(r"[^a-z0-9-]+", "-", str(row["player_name"]).lower()).strip("-")[:55] or str(self.user_id)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            candidate = guild.get_member(self.user_id)
            if candidate is None:
                candidate = await guild.fetch_member(self.user_id)
            overwrites[candidate] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
            responsible = guild.get_member(interaction.user.id)
            if responsible:
                overwrites[responsible] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
            if guild.me:
                overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
            channel = guild.get_channel(int(row["channel_id"] or 0)) if row["channel_id"] else None
            if channel is None and row["channel_id"]:
                try:
                    channel = await guild.fetch_channel(int(row["channel_id"]))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    channel = None
            if channel is None:
                channel = await guild.create_text_channel(f"teste-{safe}", category=category, overwrites=overwrites, reason=f"Teste da candidatura {self.user_id}")
                created_channel = True
            else:
                await channel.edit(overwrites=overwrites, reason="Atualizar permissões do canal de teste existente")
            embed = discord.Embed(title="📝 Candidatura para avaliação", color=discord.Color.orange())
            embed.add_field(name="Candidato", value=f"<@{self.user_id}>", inline=False)
            embed.add_field(name="Nome/Nick", value=str(row["player_name"]), inline=True)
            embed.add_field(name="UID Free Fire", value=str(row["ff_id"]), inline=True)
            embed.add_field(name="WhatsApp", value=str(row["whatsapp"] or "Não informado"), inline=True)
            embed.add_field(name="Disponibilidade", value=str(row["availability"]), inline=False)
            embed.add_field(name="Responsável", value=interaction.user.mention, inline=False)
            updated_panel = False
            try:
                async for old_message in channel.history(limit=30):
                    if old_message.author.id == self.system.bot.user.id and old_message.embeds:
                        await old_message.edit(embed=embed, view=CandidateView(self.system))
                        updated_panel = True
                        break
            except (discord.Forbidden, discord.HTTPException):
                pass
            if not updated_panel:
                await channel.send(embed=embed, view=CandidateView(self.system))
            self.system.finish_test_reservation(self.user_id, channel.id, interaction.user.id)
            # Apaga o aviso original do canal candidaturas-adm somente depois
            # que o canal privado foi criado e a candidatura foi reservada com sucesso.
            await self._remove_notice(interaction)
            self.system.log_event(self.user_id, guild.id, channel.id, interaction.user.id, "test_accepted", "Canal privado criado após aceite da ADM")
            await interaction.followup.send(f"✅ Teste aceito. Canal privado criado: {channel.mention}", ephemeral=True)
        except Exception as exc:
            if channel is not None and created_channel:
                try:
                    await channel.delete(reason="Falha ao concluir a criação do canal de teste")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            self.system.release_test_reservation(self.user_id)
            self.system.log_event(self.user_id, interaction.guild_id, None, interaction.user.id, "test_accept_failed", type(exc).__name__)
            await interaction.followup.send("❌ Não foi possível criar o canal de teste. A candidatura voltou a aguardar análise.", ephemeral=True)
            raise

    @discord.ui.button(label="❌ Recusar para teste", style=discord.ButtonStyle.danger, custom_id="evom:recruit:decline_notice")
    async def decline_notice(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.staff(interaction.user):
            return await interaction.response.send_message("Somente ADM/equipe autorizada pode recusar candidaturas.", ephemeral=True)
        row = self.system.get(self.user_id)
        if not row or row["guild_id"] != interaction.guild_id:
            return await interaction.response.send_message("Candidatura não encontrada.", ephemeral=True)
        if not self.system.reject_pending(self.user_id, interaction.user.id):
            return await interaction.response.send_message("Esta candidatura já foi processada.", ephemeral=True)
        self.system.log_event(self.user_id, interaction.guild_id, None, interaction.user.id, "rejected_for_test", "Candidatura recusada no aviso da ADM")
        try:
            await interaction.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message("❌ Candidatura recusada. Nenhum canal privado foi criado.", ephemeral=True)


class RecruitmentSystem:
    def __init__(self, bot):
        self.bot=bot
        self.db=sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS recruitment_applications (user_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER, player_name TEXT NOT NULL, ff_id TEXT NOT NULL, test_mode TEXT NOT NULL, availability TEXT NOT NULL, status TEXT NOT NULL, staff_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS recruitment_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, channel_id INTEGER, staff_id INTEGER, event_type TEXT NOT NULL, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        self.db.commit()
        self.ensure_assignment_column()
        self.ensure_whatsapp_column()
        self.ensure_partner_column()
        self.ensure_announcement_columns()
    def ensure_assignment_column(self):
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(recruitment_applications)").fetchall()}
        if "assigned_staff_id" not in columns:
            self.db.execute("ALTER TABLE recruitment_applications ADD COLUMN assigned_staff_id INTEGER")
            self.db.commit()
    def ensure_whatsapp_column(self):
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(recruitment_applications)").fetchall()}
        if "whatsapp" not in columns:
            self.db.execute("ALTER TABLE recruitment_applications ADD COLUMN whatsapp TEXT NOT NULL DEFAULT ''")
            self.db.commit()
    def ensure_partner_column(self):
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(recruitment_applications)").fetchall()}
        if "test_partner_id" not in columns:
            self.db.execute("ALTER TABLE recruitment_applications ADD COLUMN test_partner_id INTEGER")
            self.db.commit()

    def set_partner(self, user_id, partner_id):
        with self.db:
            self.db.execute("UPDATE recruitment_applications SET test_partner_id=? WHERE user_id=? AND status='testing'", (int(partner_id), int(user_id)))

    def transfer_responsible(self, user_id, old_id, new_id):
        with self.db:
            cur = self.db.execute("UPDATE recruitment_applications SET staff_id=?, assigned_staff_id=? WHERE user_id=? AND staff_id=? AND status='testing'", (int(new_id), int(new_id), int(user_id), int(old_id)))
            return cur.rowcount == 1

    def ensure_announcement_columns(self):
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(recruitment_applications)").fetchall()}
        if "announcement_pending" not in columns:
            self.db.execute("ALTER TABLE recruitment_applications ADD COLUMN announcement_pending INTEGER NOT NULL DEFAULT 0")
        if "announcement_sent" not in columns:
            self.db.execute("ALTER TABLE recruitment_applications ADD COLUMN announcement_sent INTEGER NOT NULL DEFAULT 0")
        self.db.execute("UPDATE recruitment_applications SET announcement_sent=1 WHERE user_id IN (SELECT user_id FROM recruitment_events WHERE event_type='announcement_sent')")
        self.db.commit()

    def reserve_announcement(self, user_id):
        with self.db:
            cur=self.db.execute("UPDATE recruitment_applications SET announcement_pending=1 WHERE user_id=? AND status='approved' AND announcement_sent=0 AND announcement_pending=0",(int(user_id),))
            return cur.rowcount == 1

    def finish_announcement(self,user_id):
        with self.db:
            self.db.execute("UPDATE recruitment_applications SET announcement_pending=0,announcement_sent=1 WHERE user_id=?",(int(user_id),))

    def release_announcement(self,user_id):
        with self.db:
            self.db.execute("UPDATE recruitment_applications SET announcement_pending=0 WHERE user_id=? AND announcement_sent=0",(int(user_id),))

    def reserve_test(self, user_id, staff_id):
        with self.db:
            cur = self.db.execute("UPDATE recruitment_applications SET status='creating_test', assigned_staff_id=? WHERE user_id=? AND status='pending'", (int(staff_id), int(user_id)))
            return cur.rowcount == 1

    def finish_test_reservation(self, user_id, channel_id, staff_id):
        with self.db:
            cur = self.db.execute("UPDATE recruitment_applications SET status='testing', channel_id=?, staff_id=? WHERE user_id=? AND status='creating_test' AND assigned_staff_id=?", (int(channel_id), int(staff_id), int(user_id), int(staff_id)))
            if cur.rowcount != 1:
                raise RuntimeError("A reserva da candidatura foi perdida")

    def release_test_reservation(self, user_id):
        with self.db:
            self.db.execute("UPDATE recruitment_applications SET status='pending', assigned_staff_id=NULL WHERE user_id=? AND status='creating_test'", (int(user_id),))

    def reject_pending(self, user_id, staff_id):
        with self.db:
            cur = self.db.execute("UPDATE recruitment_applications SET status='rejected', staff_id=? WHERE user_id=? AND status='pending'", (int(staff_id), int(user_id)))
            return cur.rowcount == 1

    def search_applications(self, guild_id, query):
        q = str(query).strip()
        if not q:
            return []
        like = f"%{q}%"
        return self.db.execute(
            "SELECT user_id, player_name, ff_id, whatsapp, status, created_at, channel_id FROM recruitment_applications WHERE guild_id=? AND (ff_id LIKE ? OR whatsapp LIKE ? OR player_name LIKE ?) ORDER BY created_at DESC LIMIT 10",
            (int(guild_id), like, like, like),
        ).fetchall()
    def remove_application(self, guild_id, user_id, staff_id):
        row = self.db.execute("SELECT channel_id, player_name, status FROM recruitment_applications WHERE guild_id=? AND user_id=?", (int(guild_id), int(user_id))).fetchone()
        if not row or row["status"] != "rejected":
            return None
        self.log_event(user_id, guild_id, row["channel_id"], staff_id, "application_removed", "Candidatura removida pelo painel de candidatos")
        self.db.execute("DELETE FROM recruitment_applications WHERE guild_id=? AND user_id=?", (int(guild_id), int(user_id)))
        self.db.commit()
        return row
    def get_assigned(self, user_id):
        row = self.db.execute("SELECT assigned_staff_id FROM recruitment_applications WHERE user_id=?", (user_id,)).fetchone()
        return row[0] if row else None
    def assign(self, user_id, staff_id):
        self.db.execute("UPDATE recruitment_applications SET assigned_staff_id=? WHERE user_id=? AND assigned_staff_id IS NULL", (staff_id, user_id))
        self.db.commit()
    def claim_assignment(self, user_id, staff_id):
        self.db.execute("UPDATE recruitment_applications SET assigned_staff_id=? WHERE user_id=? AND assigned_staff_id IS NULL", (staff_id, user_id))
        self.db.commit()
        return self.get_assigned(user_id)
    def clear_assignment(self, user_id, staff_id):
        self.db.execute("UPDATE recruitment_applications SET assigned_staff_id=NULL WHERE user_id=? AND assigned_staff_id=?", (user_id, staff_id))
        self.db.commit()
    async def setup(self):
        self.bot.add_view(ApplyView(self)); self.bot.add_view(CandidateView(self))
        pending = self.db.execute("SELECT user_id FROM recruitment_applications WHERE status='pending'").fetchall()
        for row in pending:
            self.bot.add_view(AnalysisNoticeView(self, int(row["user_id"])))
        for guild in self.bot.guilds: await self.ensure_panel(guild)
    async def ensure_panel(self, guild):
        category=discord.utils.get(guild.categories,name=CATEGORY_NAME)
        if not category: category=await guild.create_category(CATEGORY_NAME, reason="Configurar recrutamento ALL EVOM")
        panel=discord.utils.get(category.text_channels,name=PANEL_CHANNEL)
        if not panel:
            panel=await guild.create_text_channel(PANEL_CHANNEL,category=category)
            await panel.send(embed=customized_embed("recruitment_panel", "🎯 Recrutamento ALL EVOM", "Quer entrar para a guilda? Clique abaixo e preencha sua candidatura. A equipe falará com você por um canal privado.", 0xFF0000),view=ApplyView(self))
        info=discord.utils.get(category.text_channels,name=INFO_CHANNEL)
        if not info:
            info=await guild.create_text_channel(INFO_CHANNEL,category=category)
            await info.send(embed=customized_embed("recruitment_info", "🎮 Como funciona o teste", "Aguarde a equipe orientar seu teste. Confira seu ID do Free Fire e mantenha a disponibilidade informada na candidatura.", 0x5865F2))
        # Restore persistent candidate controls on known channels.
        rows=self.db.execute('SELECT channel_id FROM recruitment_applications WHERE guild_id=? AND status IN (\'pending\',\'testing\',\'approved\') AND channel_id IS NOT NULL',(guild.id,)).fetchall()
        for row in rows: self.bot.add_view(CandidateView(self), message_id=None)
    async def create_application(self, interaction, name, ff_id, whatsapp, availability):
        existing = self.get(interaction.user.id)
        if existing and existing["status"] in ("pending", "creating_test", "testing"):
            return await interaction.response.send_message("Você já tem uma candidatura em andamento aguardando a equipe.", ephemeral=True)
        self.db.execute("DELETE FROM recruitment_events WHERE user_id=? AND event_type='announcement_sent'", (interaction.user.id,))
        self.db.execute(
            """INSERT INTO recruitment_applications(user_id,guild_id,channel_id,player_name,ff_id,test_mode,availability,status,staff_id,whatsapp)
            VALUES(?,?,NULL,?,?,'Não informado',?,'pending',NULL,?)
            ON CONFLICT(user_id) DO UPDATE SET guild_id=excluded.guild_id,channel_id=NULL,player_name=excluded.player_name,
            ff_id=excluded.ff_id,test_mode='Não informado',availability=excluded.availability,status='pending',staff_id=NULL,
            assigned_staff_id=NULL,test_partner_id=NULL,whatsapp=excluded.whatsapp,announcement_pending=0,announcement_sent=0""",
            (interaction.user.id, interaction.guild.id, name, ff_id, availability, whatsapp),
        )
        self.db.commit()
        self.log_event(interaction.user.id, interaction.guild.id, None, None, "application_created", "Candidatura criada; aguardando decisão da ADM")
        notice_channel = interaction.guild.get_channel(ADMIN_NOTICE_CHANNEL_ID)
        if notice_channel is None:
            try:
                notice_channel = await self.bot.fetch_channel(ADMIN_NOTICE_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                notice_channel = None
        if notice_channel is not None:
            notice_description=(f"**Candidato:** {interaction.user.mention}\n**Nome/Nick:** {discord.utils.escape_markdown(name)}\n"
                             f"**ID Free Fire:** `{ff_id}`\n**WhatsApp:** `{discord.utils.escape_markdown(whatsapp)}`\n"
                             f"**Disponibilidade:** {discord.utils.escape_markdown(availability)}\n"
                             "**Nenhum canal de teste foi criado ainda.**")
            notice = customized_embed("application_admin_notice", "📥 Nova candidatura para analisar", notice_description, 0xFFA500,
                candidate=interaction.user.mention, name=discord.utils.escape_markdown(name), ff_id=ff_id,
                whatsapp=discord.utils.escape_markdown(whatsapp), availability=discord.utils.escape_markdown(availability))
            try:
                await notice_channel.send(embed=notice, view=AnalysisNoticeView(self, interaction.user.id))
                self.log_event(interaction.user.id, interaction.guild.id, None, None, "admin_notice_sent", f"Aviso enviado ao canal {ADMIN_NOTICE_CHANNEL_ID}")
            except (discord.Forbidden, discord.HTTPException):
                self.log_event(interaction.user.id, interaction.guild.id, None, None, "admin_notice_failed", f"Falha ao enviar aviso ao canal {ADMIN_NOTICE_CHANNEL_ID}")
        else:
            self.log_event(interaction.user.id, interaction.guild.id, None, None, "admin_notice_failed", f"Canal {ADMIN_NOTICE_CHANNEL_ID} não encontrado/acessível")
        await interaction.response.send_message("✅ Candidatura enviada para análise da ADM. O canal privado de teste será criado somente se ela aceitar.", ephemeral=True)

    async def grant_member_role(self, guild, user_id):
        """Grant the diamond Membro Oficial role after an ADM approves an application."""
        if guild is None:
            return False, "⚠️ Não foi possível identificar o servidor para atribuir o cargo."
        role = discord.utils.get(guild.roles, name="💎 Membro Oficial") or discord.utils.get(guild.roles, name="Membro Oficial")
        try:
            if role is None:
                role = await guild.create_role(name="💎 Membro Oficial", reason="Cargo para candidaturas aprovadas da EVOM NEXUS")
            elif role.name == "Membro Oficial":
                await role.edit(name="💎 Membro Oficial", reason="Padronizar nome do cargo Membro Oficial")
            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None
            if member is None:
                return False, "⚠️ Candidatura aprovada, mas a pessoa não foi encontrada no servidor; o cargo precisa ser atribuído quando ela estiver no servidor."
            if role not in member.roles:
                await member.add_roles(role, reason="Candidatura Membro Oficial aprovada pela equipe")
            return True, f"💎 Cargo **{role.name}** atribuído ao candidato."
        except (discord.Forbidden, discord.HTTPException) as exc:
            return False, f"⚠️ A candidatura foi salva, mas não consegui atribuir o cargo automaticamente ({type(exc).__name__}). Confira a permissão Gerenciar Cargos e a posição do cargo do bot."

    def list_applications(self, guild_id, limit=20):
        return self.db.execute(
            "SELECT user_id, player_name, ff_id, status, created_at FROM recruitment_applications WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
            (int(guild_id), int(limit)),
        ).fetchall()

    def get(self,user_id):
        return self.db.execute('SELECT * FROM recruitment_applications WHERE user_id=?',(user_id,)).fetchone()
    def get_by_channel(self,channel_id):
        return self.db.execute('SELECT * FROM recruitment_applications WHERE channel_id=?',(channel_id,)).fetchone()
    def update(self,user_id,status,staff_id):
        self.db.execute('UPDATE recruitment_applications SET status=?,staff_id=? WHERE user_id=?',(status,staff_id,user_id)); self.db.commit()
    def log_event(self, user_id, guild_id, channel_id, staff_id, event_type, details=""):
        self.db.execute(
            "INSERT INTO recruitment_events(user_id,guild_id,channel_id,staff_id,event_type,details) VALUES(?,?,?,?,?,?)",
            (user_id, guild_id, channel_id, staff_id, event_type, details),
        )
        self.db.commit()

    def was_announced(self, user_id):
        row=self.db.execute("SELECT announcement_sent FROM recruitment_applications WHERE user_id=?",(int(user_id),)).fetchone()
        return bool(row and row[0])

    async def close_later(self, channel, seconds=40):
        import asyncio
        await asyncio.sleep(seconds)
        try:
            await channel.delete(reason=f"Resultado da candidatura; limpeza automática após {seconds} segundos")
            self.log_event(0, channel.guild.id, channel.id, None, "channel_deleted", f"Canal de teste excluído após {seconds} segundos")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            # Keep the failure in the audit trail if the bot lacks permission.
            try:
                self.log_event(0, channel.guild.id, channel.id, None, "channel_delete_failed", "Falha ao excluir canal; confira permissões do bot")
            except sqlite3.Error:
                pass


class CandidateAllView(discord.ui.View):
    def __init__(self, system, rows):
        super().__init__(timeout=180); self.system=system; self.rows=list(rows); self.page=0
        self.previous.disabled=True; self.next.disabled=len(self.rows)<=10
    def build_embed(self):
        labels={"pending":"⏳ Pendente","creating_test":"🛠️ Preparando teste","testing":"🎮 Em teste","approved":"💎 Aprovado","rejected":"❌ Reprovado"}
        page_rows=self.rows[self.page*10:(self.page+1)*10]
        embed=discord.Embed(title="📋 Todos os candidatos",description=f"Página {self.page+1} de {max(1,(len(self.rows)+9)//10)} · {len(self.rows)} cadastro(s)",color=discord.Color.blurple())
        for row in page_rows:
            embed.add_field(name=f"{discord.utils.escape_markdown(row['player_name'])} · {labels.get(row['status'],row['status'])}",value=f"Discord: <@{row['user_id']}> · UID: `{row['ff_id']}`\nWhatsApp: `{row['whatsapp'] or 'Não informado'}` · Cadastro: `{row['created_at']}`",inline=False)
        return embed
    async def interaction_check(self, interaction):
        if not CandidateView(self.system).staff(interaction.user):
            await interaction.response.send_message("Somente ADM/equipe pode consultar candidatos.",ephemeral=True); return False
        return True
    @discord.ui.button(label="⬅️ Anterior",style=discord.ButtonStyle.secondary)
    async def previous(self,interaction,button):
        self.page=max(0,self.page-1); self.previous.disabled=self.page==0; self.next.disabled=(self.page+1)*10>=len(self.rows)
        await interaction.response.edit_message(embed=self.build_embed(),view=self)
    @discord.ui.button(label="Próxima ➡️",style=discord.ButtonStyle.secondary)
    async def next(self,interaction,button):
        self.page=min((len(self.rows)-1)//10,self.page+1); self.previous.disabled=self.page==0; self.next.disabled=(self.page+1)*10>=len(self.rows)
        await interaction.response.edit_message(embed=self.build_embed(),view=self)


class CandidateSearchModal(discord.ui.Modal, title="🔎 Pesquisar candidato"):
    query = discord.ui.TextInput(label="ID Free Fire, WhatsApp ou nick", placeholder="Digite o ID, número ou nome", max_length=80)
    def __init__(self, system):
        super().__init__(timeout=180); self.system = system
    async def on_submit(self, interaction: discord.Interaction):
        rows = self.system.search_applications(interaction.guild_id, str(self.query))
        if not rows:
            return await interaction.response.send_message("Nenhum candidato encontrado. Confira o ID, WhatsApp ou nick.", ephemeral=True)
        await interaction.response.send_message(embed=candidate_results_embed(rows), view=CandidateSearchResultsView(self.system, [int(r["user_id"]) for r in rows]), ephemeral=True)


def candidate_results_embed(rows):
    labels = {"pending":"⏳ Pendente", "testing":"🎮 Em teste", "approved":"💎 Aprovado", "rejected":"❌ Reprovado"}
    embed = discord.Embed(title="🔎 Resultados da pesquisa", description="Candidatos encontrados na base do servidor", color=discord.Color.from_rgb(130, 70, 220))
    for row in rows[:10]:
        embed.add_field(name=f"{labels.get(row['status'], row['status'])} · {discord.utils.escape_markdown(row['player_name'])}", value=f"**ID Free Fire:** `{row['ff_id']}`\n**WhatsApp:** `{row['whatsapp'] or 'Não informado'}`\n**Discord:** <@{row['user_id']}> (`{row['user_id']}`)", inline=False)
    embed.set_footer(text="Os dados são privados e visíveis apenas para a equipe autorizada.")
    return embed


class CandidateSearchResultsView(discord.ui.View):
    def __init__(self, system, user_ids):
        super().__init__(timeout=180); self.system = system
        rejected_ids=[]
        for uid in user_ids:
            row=system.get(uid)
            if row and row["status"] == "rejected": rejected_ids.append(uid)
        options = [discord.SelectOption(label=str(uid), value=str(uid), description="Remover candidatura reprovada") for uid in rejected_ids[:25]]
        if options:
            self.add_item(CandidateRemoveSelect(system, options))


class CandidateRemoveSelect(discord.ui.Select):
    def __init__(self, system, options):
        super().__init__(placeholder="Selecionar candidato para remover do cadastro", min_values=1, max_values=1, options=options)
        self.system = system
    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or not CandidateView(self.system).staff(interaction.user):
            return await interaction.response.send_message("Somente ADM/equipe pode remover candidatos.", ephemeral=True)
        user_id = int(self.values[0])
        row = self.system.remove_application(interaction.guild_id, user_id, interaction.user.id)
        if not row:
            return await interaction.response.send_message("Somente candidaturas reprovadas podem ser removidas por este botão, ou o cadastro já foi removido.", ephemeral=True)
        await interaction.response.send_message(f"🗑️ Cadastro de **{discord.utils.escape_markdown(row['player_name'])}** removido da lista. Isso não expulsa a pessoa do Discord nem apaga o canal automaticamente.", ephemeral=True)
