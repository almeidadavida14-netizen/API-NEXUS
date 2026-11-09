"""Persistent Guild War line registration for EVOM NEXUS.

Uses the bot's existing SQLite connection; channel cleanup never deletes saved records.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from config import STAFF_ROLE_NAMES

log = logging.getLogger(__name__)
CATEGORY_NAME = "╭・CENTRAL DE LINES・╮"
PANEL_NAME = "🎮・painel-de-lines"
APPLICATIONS_NAME = "📋・inscrições-de-lines"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GuildWarSystem:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.database.connection
        self.lock = bot.database.lock
        with self.lock, self.db:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS evom_guild_war_editions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                edition_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                category_id INTEGER,
                panel_channel_id INTEGER,
                panel_message_id INTEGER,
                applications_channel_id INTEGER,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                ended_by INTEGER,
                ended_at TEXT,
                war_started_at TEXT,
                UNIQUE(guild_id, edition_number)
            );
            CREATE INDEX IF NOT EXISTS evom_gw_editions_active
                ON evom_guild_war_editions(guild_id, status);
            CREATE TABLE IF NOT EXISTS evom_guild_war_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                edition_id INTEGER NOT NULL,
                line_number INTEGER NOT NULL,
                line_name TEXT NOT NULL,
                captain_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                reviewed_by INTEGER,
                reviewed_at TEXT,
                voice_channel_id INTEGER,
                UNIQUE(edition_id, line_number)
            );
            CREATE TABLE IF NOT EXISTS evom_guild_war_line_players (
                line_id INTEGER NOT NULL,
                slot INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                ff_uid TEXT NOT NULL,
                ff_nickname TEXT NOT NULL,
                PRIMARY KEY(line_id, slot),
                UNIQUE(line_id, user_id),
                UNIQUE(line_id, ff_uid)
            );
            CREATE TABLE IF NOT EXISTS evom_guild_war_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                edition_id INTEGER NOT NULL,
                line_id INTEGER,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            );
            """)
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(evom_guild_war_editions)").fetchall()}
            if "war_started_at" not in columns:
                self.db.execute("ALTER TABLE evom_guild_war_editions ADD COLUMN war_started_at TEXT")

    @staticmethod
    def is_staff(member) -> bool:
        perms = getattr(member, "guild_permissions", None)
        names = {str(x).strip().casefold() for x in STAFF_ROLE_NAMES}
        has_role = any(getattr(r, "name", "").strip().casefold() in names for r in getattr(member, "roles", []))
        return bool(perms and (perms.administrator or perms.manage_guild or has_role))

    @staticmethod
    def has_official_role(member) -> bool:
        return any(getattr(role, "name", "").strip().casefold() == "membro oficial" for role in getattr(member, "roles", []))

    def active_edition(self, guild_id: int):
        with self.lock:
            return self.db.execute(
                "SELECT * FROM evom_guild_war_editions WHERE guild_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                (guild_id,),
            ).fetchone()

    def get_line(self, line_id: int):
        with self.lock:
            return self.db.execute("SELECT * FROM evom_guild_war_lines WHERE id=?", (line_id,)).fetchone()

    def players(self, line_id: int):
        with self.lock:
            return self.db.execute(
                "SELECT * FROM evom_guild_war_line_players WHERE line_id=? ORDER BY slot", (line_id,)
            ).fetchall()

    def audit(self, guild_id, edition_id, line_id, actor_id, action, details=""):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO evom_guild_war_audit(guild_id,edition_id,line_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?,?,?)",
                (guild_id, edition_id, line_id, actor_id, action, details, utcnow()),
            )

    async def setup(self):
        self.register_commands()
        self.bot.add_view(LinePanelView(self))
        with self.lock:
            rows = self.db.execute("SELECT id FROM evom_guild_war_lines WHERE status IN ('pending','approved')").fetchall()
        for row in rows:
            self.bot.add_view(LineReviewView(self, row["id"]))

    def register_commands(self):
        @self.bot.tree.command(name="abrir_lines", description="ADM: abre uma edição da Guerra de Guilda e cria os canais de lines.")
        async def abrir_lines(interaction: discord.Interaction):
            if not interaction.guild or not self.is_staff(interaction.user):
                return await interaction.response.send_message("Somente ADM autorizado ou fundador pode abrir as lines.", ephemeral=True)
            active = self.active_edition(interaction.guild.id)
            if active:
                return await interaction.response.send_message(
                    f"Já existe uma Guerra de Guilda aberta (edição #{active['edition_number']}). Use o painel da ADM para gerenciá-la.",
                    ephemeral=True, view=AdminGuildWarView(self, active["id"]),
                )
            await interaction.response.defer(ephemeral=True, thinking=True)
            guild = interaction.guild
            try:
                category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                }
                for role in guild.roles:
                    if role.name.strip().casefold() == "membro oficial":
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
                    elif role.name.strip().casefold() in {str(x).casefold() for x in STAFF_ROLE_NAMES}:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
                if category is None:
                    category = await guild.create_category(CATEGORY_NAME, overwrites=overwrites, reason="Abertura da Guerra de Guilda EVOM NEXUS")
                else:
                    await category.edit(overwrites=overwrites, reason="Sincronizar permissões da Central de Lines")
                panel = discord.utils.get(category.text_channels, name=PANEL_NAME)
                if panel is None:
                    panel = await guild.create_text_channel(PANEL_NAME, category=category, reason="Painel de inscrição de lines")
                applications = discord.utils.get(category.text_channels, name=APPLICATIONS_NAME)
                if applications is None:
                    applications = await guild.create_text_channel(APPLICATIONS_NAME, category=category, reason="Revisão privada de inscrições de lines")
                # Explicit channel overwrites ensure the review channel stays private.
                await panel.edit(overwrites=overwrites)
                applications_overwrites = dict(overwrites)
                for role in guild.roles:
                    if role.name.strip().casefold() == "membro oficial":
                        applications_overwrites[role] = discord.PermissionOverwrite(view_channel=False, send_messages=False, read_message_history=False)
                applications_overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
                await applications.edit(overwrites=applications_overwrites)
                with self.lock, self.db:
                    edition_number = self.db.execute("SELECT COALESCE(MAX(edition_number),0)+1 FROM evom_guild_war_editions WHERE guild_id=?", (guild.id,)).fetchone()[0]
                    cur = self.db.execute(
                        "INSERT INTO evom_guild_war_editions(guild_id,edition_number,status,category_id,panel_channel_id,applications_channel_id,created_by,created_at) VALUES(?,?,'active',?,?,?,?,?)",
                        (guild.id, edition_number, category.id, panel.id, applications.id, interaction.user.id, utcnow()),
                    )
                    edition_id = cur.lastrowid
                await panel.send(embed=self.panel_embed(edition_number), view=LinePanelView(self))
                await interaction.followup.send(
                    f"✅ **Guerra de Guilda #{edition_number} aberta!**\nCategoria: {category.name}\nPainel: {panel.mention}\nInscrições privadas: {applications.mention}",
                    ephemeral=True, view=AdminGuildWarView(self, edition_id),
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.exception("Falha ao abrir Central de Lines")
                await interaction.followup.send(
                    "Não consegui criar/configurar a categoria e os canais. Confira as permissões **Gerenciar canais**, **Ver canais**, **Enviar mensagens** e **Inserir links** do bot.", ephemeral=True
                )

        @self.bot.tree.command(name="painel_guildwar", description="ADM: abre o painel de controle da Guerra de Guilda.")
        async def painel_guildwar(interaction: discord.Interaction):
            if not interaction.guild or not self.is_staff(interaction.user):
                return await interaction.response.send_message("Somente ADM autorizado ou fundador pode acessar este painel.", ephemeral=True)
            active = self.active_edition(interaction.guild.id)
            if not active:
                return await interaction.response.send_message("Não há Guerra de Guilda ativa. Use `/abrir_lines` para criar uma edição.", ephemeral=True)
            await interaction.response.send_message("🛡️ **Painel de controle da ADM**", view=AdminGuildWarView(self, active["id"]), ephemeral=True)

    @staticmethod
    def panel_embed(edition_number):
        embed = discord.Embed(
            title="🎮 EVOM NEXUS • INSCRIÇÃO DE LINES",
            description=(f"**Guerra de Guilda #{edition_number}**\n\n"
                         "Use o botão abaixo para inscrever sua equipe. A line deve ter **4 jogadores**, incluindo o capitão.\n"
                         "Os quatro UIDs precisam pertencer a jogadores aprovados no recrutamento deste servidor.\n\n"
                         "As inscrições serão enviadas para análise da ADM. Cadastros e histórico ficam salvos mesmo depois que os canais forem removidos."),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="EVOM NEXUS • Central de Lines")
        return embed

    async def submit_line(self, interaction: discord.Interaction, line_name: str, uids: list[str]):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Use isso dentro do servidor.", ephemeral=True)
        if not self.has_official_role(interaction.user) and not self.is_staff(interaction.user):
            return await interaction.response.send_message("É necessário ter o cargo **Membro Oficial** para inscrever uma line.", ephemeral=True)
        edition = self.active_edition(guild.id)
        if not edition:
            return await interaction.response.send_message("Não há inscrições abertas neste momento.", ephemeral=True)
        clean_uids = [str(uid).strip() for uid in uids]
        if any(not uid.isdigit() for uid in clean_uids) or len(set(clean_uids)) != 4:
            return await interaction.response.send_message("Informe quatro UIDs numéricos diferentes.", ephemeral=True)
        # Resolve every UID to the Discord identity already captured by the recruitment system.
        with self.lock:
            found = []
            for uid in clean_uids:
                row = self.db.execute(
                    "SELECT user_id, player_name, ff_id FROM recruitment_applications WHERE guild_id=? AND ff_id=? AND status='approved' ORDER BY created_at DESC LIMIT 1",
                    (guild.id, uid),
                ).fetchone()
                if not row:
                    found.append(None)
                else:
                    found.append(row)
        if any(row is None for row in found):
            missing = [uid for uid, row in zip(clean_uids, found) if row is None]
            return await interaction.response.send_message(
                "Não encontrei cadastro aprovado para estes UID(s): " + ", ".join(f"`{x}`" for x in missing) + ". A ADM precisa aprovar o cadastro no recrutamento primeiro.", ephemeral=True
            )
        user_ids = [int(row["user_id"]) for row in found]
        if len(set(user_ids)) != 4:
            return await interaction.response.send_message("Cada UID precisa corresponder a um jogador diferente.", ephemeral=True)
        if interaction.user.id not in user_ids:
            return await interaction.response.send_message("O capitão que envia a inscrição precisa estar entre os quatro jogadores da line.", ephemeral=True)
        with self.lock:
            placeholders = ",".join("?" for _ in user_ids)
            already = self.db.execute(
                f"SELECT p.user_id, l.line_number, l.status FROM evom_guild_war_line_players p JOIN evom_guild_war_lines l ON l.id=p.line_id WHERE l.edition_id=? AND l.status IN ('pending','approved') AND p.user_id IN ({placeholders})",
                (edition["id"], *user_ids),
            ).fetchall()
        if already:
            return await interaction.response.send_message("Um ou mais jogadores já estão inscritos em outra line nesta guerra. Cada jogador só pode pertencer a uma equipe.", ephemeral=True)
        with self.lock, self.db:
            line_number = self.db.execute("SELECT COALESCE(MAX(line_number),0)+1 FROM evom_guild_war_lines WHERE edition_id=?", (edition["id"],)).fetchone()[0]
            cur = self.db.execute(
                "INSERT INTO evom_guild_war_lines(guild_id,edition_id,line_number,line_name,captain_id,status,created_by,created_at) VALUES(?,?,?,?,?,'pending',?,?)",
                (guild.id, edition["id"], line_number, line_name.strip()[:80], interaction.user.id, interaction.user.id, utcnow()),
            )
            line_id = cur.lastrowid
            for slot, (uid, row) in enumerate(zip(clean_uids, found), start=1):
                self.db.execute(
                    "INSERT INTO evom_guild_war_line_players(line_id,slot,user_id,ff_uid,ff_nickname) VALUES(?,?,?,?,?)",
                    (line_id, slot, int(row["user_id"]), uid, str(row["player_name"])),
                )
            self.db.execute("INSERT INTO evom_guild_war_audit(guild_id,edition_id,line_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?,?,?)",
                            (guild.id, edition["id"], line_id, interaction.user.id, "submitted", line_name.strip(), utcnow()))
        applications = guild.get_channel(edition["applications_channel_id"])
        if applications:
            captain = guild.get_member(interaction.user.id)
            embed = self.line_embed(line_id, status="pending")
            try:
                await applications.send(embed=embed, view=LineReviewView(self, line_id))
            except discord.HTTPException:
                log.exception("Falha ao publicar inscrição %s", line_id)
        self.bot.add_view(LineReviewView(self, line_id))
        await interaction.response.send_message(f"✅ Inscrição enviada! Sua equipe é **LINE {line_number:03d} — {discord.utils.escape_markdown(line_name)}**. A ADM vai analisar.", ephemeral=True)

    def line_embed(self, line_id: int, status: str | None = None):
        line = self.get_line(line_id)
        if not line:
            return discord.Embed(title="Inscrição não encontrada")
        players = self.players(line_id)
        status = status or line["status"]
        label = {"pending": "⏳ Pendente", "approved": "✅ Aprovada", "rejected": "❌ Recusada"}.get(status, status)
        desc = f"**Line:** {line['line_number']:03d} — {discord.utils.escape_markdown(line['line_name'])}\n**Capitão:** <@{line['captain_id']}>\n**Status:** {label}\n\n"
        desc += "\n".join(f"**Jogador {p['slot']}:** <@{p['user_id']}> • `{p['ff_uid']}` • {discord.utils.escape_markdown(p['ff_nickname'])}" for p in players)
        embed = discord.Embed(title="📋 Inscrição de Line", description=desc, color=discord.Color.orange() if status == "pending" else discord.Color.green() if status == "approved" else discord.Color.red())
        return embed

    async def review(self, interaction, line_id, action):
        if not interaction.guild or not self.is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM autorizado ou fundador pode revisar lines.", ephemeral=True)
        line = self.get_line(line_id)
        if not line:
            return await interaction.response.send_message("Esta inscrição não foi encontrada.", ephemeral=True)
        if line["status"] != "pending":
            return await interaction.response.send_message(f"Esta line já está {line['status']}.", ephemeral=True)
        status = "approved" if action == "approve" else "rejected"
        with self.lock, self.db:
            self.db.execute("UPDATE evom_guild_war_lines SET status=?,reviewed_by=?,reviewed_at=? WHERE id=? AND status='pending'", (status, interaction.user.id, utcnow(), line_id))
            self.db.execute("INSERT INTO evom_guild_war_audit(guild_id,edition_id,line_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?,?,?)", (line["guild_id"], line["edition_id"], line_id, interaction.user.id, status, "", utcnow()))
        await interaction.response.edit_message(embed=self.line_embed(line_id, status=status), view=LineReviewView(self, line_id, disabled=True))
        await interaction.followup.send(f"Line {line['line_number']:03d} {'aprovada' if status == 'approved' else 'recusada'} por {interaction.user.mention}.", ephemeral=True)

    async def create_voice(self, interaction, line_id):
        line = self.get_line(line_id)
        if not interaction.guild or not line or line["guild_id"] != interaction.guild.id:
            return await interaction.response.send_message("Line não encontrada neste servidor.", ephemeral=True)
        if line["status"] != "approved":
            return await interaction.response.send_message("O canal de voz só pode ser criado para uma line aprovada.", ephemeral=True)
        if interaction.user.id != line["captain_id"] and not self.is_staff(interaction.user):
            return await interaction.response.send_message("Só o capitão da line ou a ADM pode criar o canal de voz.", ephemeral=True)
        edition = self.active_edition(interaction.guild.id)
        if not edition or edition["id"] != line["edition_id"]:
            return await interaction.response.send_message("Esta Guerra de Guilda não está ativa.", ephemeral=True)
        if not edition["war_started_at"]:
            return await interaction.response.send_message("A ADM precisa iniciar a Guerra de Guilda pelo painel antes de criar canais de voz.", ephemeral=True)
        if line["voice_channel_id"]:
            existing = interaction.guild.get_channel(line["voice_channel_id"])
            if existing:
                return await interaction.response.send_message(f"O canal da line já existe: {existing.mention}", ephemeral=True)
        category = interaction.guild.get_channel(edition["category_id"])
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("A categoria da guerra não foi encontrada.", ephemeral=True)
        overwrites = {interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False)}
        for role in interaction.guild.roles:
            if role.name.strip().casefold() == "membro oficial":
                overwrites[role] = discord.PermissionOverwrite(view_channel=False, connect=False)
        for player in self.players(line_id):
            member = interaction.guild.get_member(player["user_id"])
            if member:
                overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, send_messages=True, read_message_history=True)
        for role in interaction.guild.roles:
            if role.name.strip().casefold() in {str(x).casefold() for x in STAFF_ROLE_NAMES}:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, manage_channels=True)
        channel = await interaction.guild.create_voice_channel(f"🔊・line-{line['line_number']:03d}", category=category, overwrites=overwrites, reason=f"Canal privado da LINE {line['line_number']:03d}")
        with self.lock, self.db:
            self.db.execute("UPDATE evom_guild_war_lines SET voice_channel_id=? WHERE id=?", (channel.id, line_id))
            self.db.execute("INSERT INTO evom_guild_war_audit(guild_id,edition_id,line_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?,?,?)", (line["guild_id"], line["edition_id"], line_id, interaction.user.id, "voice_created", str(channel.id), utcnow()))
        await interaction.response.send_message(f"🔊 Canal privado criado: {channel.mention}. Só os quatro jogadores da line e a ADM têm acesso.", ephemeral=True)

    async def start_war(self, interaction, edition_id):
        if not interaction.guild or not self.is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM autorizado ou fundador pode iniciar a guerra.", ephemeral=True)
        with self.lock, self.db:
            edition = self.db.execute("SELECT * FROM evom_guild_war_editions WHERE id=? AND guild_id=? AND status='active'", (edition_id, interaction.guild.id)).fetchone()
            if not edition:
                return await interaction.response.send_message("Esta edição não está ativa.", ephemeral=True)
            if edition["war_started_at"]:
                return await interaction.response.send_message("A Guerra de Guilda já foi iniciada.", ephemeral=True)
            self.db.execute("UPDATE evom_guild_war_editions SET war_started_at=? WHERE id=?", (utcnow(), edition_id))
            self.db.execute("INSERT INTO evom_guild_war_audit(guild_id,edition_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?,?)", (interaction.guild.id, edition_id, interaction.user.id, "war_started", "", utcnow()))
        await interaction.response.send_message("⚔️ Guerra de Guilda iniciada! As lines aprovadas já podem criar seus canais de voz.", ephemeral=True)

    async def announce_lines(self, interaction, edition_id, channel_id):
        if not interaction.guild or not self.is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM autorizado ou fundador pode anunciar as lines.", ephemeral=True)
        with self.lock:
            edition = self.db.execute("SELECT * FROM evom_guild_war_editions WHERE id=? AND guild_id=? AND status='active'", (edition_id, interaction.guild.id)).fetchone()
            lines = self.db.execute("SELECT id,line_number,line_name,captain_id FROM evom_guild_war_lines WHERE edition_id=? AND status='approved' ORDER BY line_number", (edition_id,)).fetchall()
        if not edition:
            return await interaction.response.send_message("Esta edição não está ativa.", ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.ForumChannel)):
            return await interaction.response.send_message("Escolha um canal de texto válido.", ephemeral=True)
        if not lines:
            return await interaction.response.send_message("Ainda não há lines aprovadas para anunciar.", ephemeral=True)
        description = "\n".join(f"**LINE {row['line_number']:03d} — {discord.utils.escape_markdown(row['line_name'])}** • Capitão: <@{row['captain_id']}>" for row in lines)
        embed = discord.Embed(title=f"⚔️ EVOM NEXUS • GUERRA DE GUILDA #{edition['edition_number']}", description=description[:4000], color=discord.Color.red())
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        except discord.HTTPException:
            return await interaction.response.send_message("Não consegui publicar o anúncio nesse canal.", ephemeral=True)
        self.audit(interaction.guild.id, edition_id, None, interaction.user.id, "lines_announced", f"channel={channel.id}; count={len(lines)}")
        await interaction.response.send_message(f"Anúncio de {len(lines)} line(s) publicado em {channel.mention}.", ephemeral=True)

    async def replace_player(self, interaction, edition_id, line_number, old_uid, new_uid):
        if not interaction.guild:
            return await interaction.response.send_message("Use isso dentro do servidor.", ephemeral=True)
        edition = self.active_edition(interaction.guild.id)
        if not edition or edition["id"] != edition_id:
            return await interaction.response.send_message("Esta Guerra de Guilda não está ativa.", ephemeral=True)
        with self.lock:
            line = self.db.execute("SELECT * FROM evom_guild_war_lines WHERE edition_id=? AND line_number=? AND status='approved'", (edition_id, line_number)).fetchone()
        if not line:
            return await interaction.response.send_message("Não encontrei uma line aprovada com esse número.", ephemeral=True)
        if not self.is_staff(interaction.user) and interaction.user.id != line["captain_id"]:
            return await interaction.response.send_message("Só a ADM ou o capitão da própria line pode trocar um jogador.", ephemeral=True)
        with self.lock:
            old = self.db.execute("SELECT * FROM evom_guild_war_line_players WHERE line_id=? AND ff_uid=?", (line["id"], old_uid.strip())).fetchone() if line else None
            new = self.db.execute("SELECT user_id,player_name,ff_id FROM recruitment_applications WHERE guild_id=? AND ff_id=? AND status='approved' ORDER BY created_at DESC LIMIT 1", (interaction.guild.id, new_uid.strip())).fetchone()
            occupied = self.db.execute("SELECT 1 FROM evom_guild_war_line_players p JOIN evom_guild_war_lines l ON l.id=p.line_id WHERE l.edition_id=? AND l.status IN ('pending','approved') AND p.user_id=?", (edition_id, new["user_id"])).fetchone() if new else None
        if not line or not old or not new:
            return await interaction.response.send_message("Não encontrei a line aprovada, o UID antigo ou o UID novo aprovado no recrutamento.", ephemeral=True)
        if occupied:
            return await interaction.response.send_message("O jogador substituto já está em outra line desta guerra.", ephemeral=True)
        with self.lock, self.db:
            self.db.execute("UPDATE evom_guild_war_line_players SET user_id=?,ff_uid=?,ff_nickname=? WHERE line_id=? AND slot=?", (int(new["user_id"]), new_uid.strip(), str(new["player_name"]), line["id"], old["slot"]))
            details = f"saiu_user={old['user_id']};saiu_uid={old_uid};entrou_user={new['user_id']};entrou_uid={new_uid}"
            self.db.execute("INSERT INTO evom_guild_war_audit(guild_id,edition_id,line_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?,?,?)", (interaction.guild.id, edition_id, line["id"], interaction.user.id, "player_replaced", details, utcnow()))
        # If a voice channel already exists, update its access immediately.
        if line["voice_channel_id"]:
            voice = interaction.guild.get_channel(line["voice_channel_id"])
            old_member = interaction.guild.get_member(old["user_id"])
            new_member = interaction.guild.get_member(new["user_id"])
            if voice and old_member:
                try: await voice.set_permissions(old_member, overwrite=None, reason="Jogador removido da line")
                except discord.HTTPException: pass
            if voice and new_member:
                try: await voice.set_permissions(new_member, view_channel=True, connect=True, speak=True, send_messages=True, read_message_history=True, reason="Jogador substituto da line")
                except discord.HTTPException: pass
        await interaction.response.send_message(f"✅ LINE {line_number:03d} atualizada: saiu <@{old['user_id']}> e entrou <@{new['user_id']}>. Alteração registrada no histórico.", ephemeral=True)

    async def close_edition(self, interaction, edition_id):
        if not interaction.guild or not self.is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM autorizado ou fundador pode encerrar a guerra.", ephemeral=True)
        with self.lock:
            edition = self.db.execute("SELECT * FROM evom_guild_war_editions WHERE id=? AND guild_id=? AND status='active'", (edition_id, interaction.guild.id)).fetchone()
        if not edition:
            return await interaction.response.send_message("Esta edição já foi encerrada ou não existe.", ephemeral=True)
        await interaction.response.send_message(
            "⚠️ **Tem certeza de que deseja encerrar esta Guerra de Guilda?**\n\n"
            "Isso vai excluir a categoria **CENTRAL DE LINES**, os canais de painel/inscrições e os canais de voz temporários desta edição.\n\n"
            "**Os cadastros, UIDs, nicks, lines e históricos permanecerão salvos no banco de dados.**",
            view=ConfirmCloseGuildWarView(self, edition_id), ephemeral=True,
        )

    async def confirm_close(self, interaction, edition_id):
        if not interaction.guild or not self.is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM autorizado ou fundador pode confirmar.", ephemeral=True)
        with self.lock:
            edition = self.db.execute("SELECT * FROM evom_guild_war_editions WHERE id=? AND guild_id=? AND status='active'", (edition_id, interaction.guild.id)).fetchone()
            voices = self.db.execute("SELECT voice_channel_id FROM evom_guild_war_lines WHERE edition_id=? AND voice_channel_id IS NOT NULL", (edition_id,)).fetchall()
        if not edition:
            return await interaction.response.send_message("Esta edição já foi encerrada.", ephemeral=True)
        guild = interaction.guild
        errors = []
        for row in voices:
            channel = guild.get_channel(row["voice_channel_id"])
            if channel:
                try:
                    await channel.delete(reason=f"Encerramento da Guerra de Guilda por {interaction.user}")
                except discord.HTTPException:
                    errors.append(channel.name)
        category = guild.get_channel(edition["category_id"])
        if isinstance(category, discord.CategoryChannel):
            for channel in list(category.channels):
                try:
                    await channel.delete(reason=f"Encerramento da Guerra de Guilda por {interaction.user}")
                except discord.HTTPException:
                    errors.append(channel.name)
            try:
                await category.delete(reason=f"Encerramento da Guerra de Guilda por {interaction.user}")
            except discord.HTTPException:
                errors.append(category.name)
        with self.lock, self.db:
            self.db.execute("UPDATE evom_guild_war_editions SET status='ended',ended_by=?,ended_at=? WHERE id=?", (interaction.user.id, utcnow(), edition_id))
            self.db.execute("INSERT INTO evom_guild_war_audit(guild_id,edition_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?,?)", (guild.id, edition_id, interaction.user.id, "edition_ended", json.dumps({"cleanup_errors": errors}), utcnow()))
        suffix = "\n⚠️ Não consegui excluir estes canais: " + ", ".join(errors) if errors else ""
        await interaction.followup.send("✅ Guerra de Guilda encerrada. Categoria e canais temporários removidos; cadastros e histórico preservados." + suffix, ephemeral=True)


class LineSignupModal(discord.ui.Modal, title="Inscrição de Line — EVOM NEXUS"):
    line_name = discord.ui.TextInput(label="Nome da line", max_length=80, placeholder="Ex.: EVOM Alpha")
    uid1 = discord.ui.TextInput(label="UID Free Fire do jogador 1", max_length=20)
    uid2 = discord.ui.TextInput(label="UID Free Fire do jogador 2", max_length=20)
    uid3 = discord.ui.TextInput(label="UID Free Fire do jogador 3", max_length=20)
    uid4 = discord.ui.TextInput(label="UID Free Fire do jogador 4 (capitão incluso)", max_length=20)
    def __init__(self, system):
        super().__init__(); self.system = system
    async def on_submit(self, interaction):
        await self.system.submit_line(interaction, str(self.line_name), [str(self.uid1), str(self.uid2), str(self.uid3), str(self.uid4)])


class LinePanelView(discord.ui.View):
    def __init__(self, system):
        super().__init__(timeout=None); self.system = system
    @discord.ui.button(label="📝 Inscrever minha line", style=discord.ButtonStyle.success, custom_id="evom:gw:signup", row=0)
    async def signup(self, interaction, button):
        if not interaction.guild or (not self.system.has_official_role(interaction.user) and not self.system.is_staff(interaction.user)):
            return await interaction.response.send_message("É necessário ter o cargo **Membro Oficial** para usar este painel.", ephemeral=True)
        if not self.system.active_edition(interaction.guild.id):
            return await interaction.response.send_message("As inscrições estão fechadas.", ephemeral=True)
        await interaction.response.send_modal(LineSignupModal(self.system))
    @discord.ui.button(label="🔄 Pedir/trocar jogador da minha line", style=discord.ButtonStyle.secondary, custom_id="evom:gw:captain_replace", row=0)
    async def captain_replace(self, interaction, button):
        if not interaction.guild or (not self.system.has_official_role(interaction.user) and not self.system.is_staff(interaction.user)):
            return await interaction.response.send_message("É necessário ter o cargo **Membro Oficial** para usar este painel.", ephemeral=True)
        edition = self.system.active_edition(interaction.guild.id)
        if not edition:
            return await interaction.response.send_message("Não há Guerra de Guilda ativa.", ephemeral=True)
        await interaction.response.send_modal(ReplacePlayerModal(self.system, edition["id"]))


class LineReviewView(discord.ui.View):
    def __init__(self, system, line_id, disabled=False):
        super().__init__(timeout=None); self.system = system; self.line_id = int(line_id)
        approve = discord.ui.Button(label="✅ Aprovar", style=discord.ButtonStyle.success, custom_id=f"evom:gw:approve:{line_id}", disabled=disabled)
        reject = discord.ui.Button(label="❌ Recusar", style=discord.ButtonStyle.danger, custom_id=f"evom:gw:reject:{line_id}", disabled=disabled)
        approve.callback = self._approve
        reject.callback = self._reject
        self.add_item(approve); self.add_item(reject)
        line = system.get_line(line_id)
        if line and line["status"] == "approved":
            voice = discord.ui.Button(label="🔊 Criar voz da line", style=discord.ButtonStyle.primary, custom_id=f"evom:gw:voice:{line_id}")
            voice.callback = self._voice
            self.add_item(voice)
    async def _approve(self, interaction): await self.system.review(interaction, self.line_id, "approve")
    async def _reject(self, interaction): await self.system.review(interaction, self.line_id, "reject")
    async def _voice(self, interaction): await self.system.create_voice(interaction, self.line_id)


class AdminGuildWarView(discord.ui.View):
    def __init__(self, system, edition_id):
        super().__init__(timeout=300); self.system = system; self.edition_id = edition_id
    @discord.ui.button(label="⚔️ Iniciar Guerra", style=discord.ButtonStyle.success, row=0)
    async def start(self, interaction, button):
        await self.system.start_war(interaction, self.edition_id)
    @discord.ui.button(label="📣 Anunciar Lines", style=discord.ButtonStyle.primary, row=0)
    async def announce(self, interaction, button):
        if not interaction.guild or not self.system.is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM autorizado pode anunciar.", ephemeral=True)
        await interaction.response.send_message("Escolha o canal para anunciar as lines aprovadas:", view=AnnounceChannelView(self.system, self.edition_id), ephemeral=True)
    @discord.ui.button(label="🔄 Trocar jogador", style=discord.ButtonStyle.secondary, row=1)
    async def replace(self, interaction, button):
        if not interaction.guild or not self.system.is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM autorizado pode trocar jogadores.", ephemeral=True)
        await interaction.response.send_modal(ReplacePlayerModal(self.system, self.edition_id))
    @discord.ui.button(label="📋 Consultar lines", style=discord.ButtonStyle.secondary, row=1)
    async def list_lines(self, interaction, button):
        if not interaction.guild or not self.system.is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM autorizado pode consultar lines.", ephemeral=True)
        with self.system.lock:
            lines = self.system.db.execute("SELECT id,line_number,line_name,status,captain_id FROM evom_guild_war_lines WHERE edition_id=? ORDER BY line_number", (self.edition_id,)).fetchall()
        if not lines:
            return await interaction.response.send_message("Ainda não há lines inscritas nesta guerra.", ephemeral=True)
        body = "\n".join(f"**LINE {r['line_number']:03d} — {discord.utils.escape_markdown(r['line_name'])}** · {r['status']} · Capitão <@{r['captain_id']}>" for r in lines)
        await interaction.response.send_message(body[:1900], ephemeral=True)
    @discord.ui.button(label="🛑 Encerrar Guilda", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction, button):
        await self.system.close_edition(interaction, self.edition_id)


class AnnounceChannelView(discord.ui.View):
    def __init__(self, system, edition_id):
        super().__init__(timeout=180); self.system = system; self.edition_id = edition_id
    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="Selecione o canal de anúncio", min_values=1, max_values=1)
    async def channel(self, interaction, select):
        if not interaction.guild or not self.system.is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM autorizado pode anunciar.", ephemeral=True)
        await self.system.announce_lines(interaction, self.edition_id, select.values[0].id)


class ReplacePlayerModal(discord.ui.Modal, title="Trocar jogador da line"):
    line_number = discord.ui.TextInput(label="Número da line (ex.: 1 para LINE 001)", max_length=4)
    old_uid = discord.ui.TextInput(label="UID do jogador que vai sair", max_length=20)
    new_uid = discord.ui.TextInput(label="UID do substituto (cadastro aprovado)", max_length=20)
    def __init__(self, system, edition_id):
        super().__init__(); self.system = system; self.edition_id = edition_id
    async def on_submit(self, interaction):
        try: number = int(str(self.line_number).strip())
        except ValueError: return await interaction.response.send_message("Informe o número da line, por exemplo 1.", ephemeral=True)
        await self.system.replace_player(interaction, self.edition_id, number, str(self.old_uid), str(self.new_uid))


class ConfirmCloseGuildWarView(discord.ui.View):
    def __init__(self, system, edition_id):
        super().__init__(timeout=120); self.system = system; self.edition_id = edition_id
    @discord.ui.button(label="✅ Sim, encerrar guilda", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await self.system.confirm_close(interaction, self.edition_id)
    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="Cancelado. A Guerra de Guilda continua ativa e nada foi apagado.", view=self)
