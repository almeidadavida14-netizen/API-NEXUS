"""X-TREINO event, signup and admin workflow for EVOM NEXUS."""
from __future__ import annotations

import asyncio
import logging
import math
import random
import sqlite3
from datetime import datetime, timezone

import discord
from config import DATABASE_PATH, STAFF_ROLE_NAMES

CHANNEL_NAME = "⚔️・x-treino"
CATEGORY_NAME = "⚔️ X-TREINO"
ADMIN_CHANNEL_NAME = "📋・xtreino-adm"
logger = logging.getLogger(__name__)


def is_staff(member):
    return bool(getattr(member.guild_permissions, "administrator", False)) or any(
        r.name.strip().casefold() in STAFF_ROLE_NAMES for r in getattr(member, "roles", [])
    )


def is_official(member):
    return any(r.name.replace("💎", "").strip().casefold() == "membro oficial" for r in getattr(member, "roles", []))


class XTreinoCreateButton(discord.ui.Button):
    def __init__(self, bot):
        super().__init__(label="Criar X-TREINO", emoji="⚔️", style=discord.ButtonStyle.success,
                         custom_id="evom:xt:create_channel")
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Somente ADM pode criar o X-TREINO.", ephemeral=True)
        if interaction.guild is None:
            return await interaction.response.send_message("Use este botão dentro do servidor.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            system = self.bot.xtreino_system
            async with system._channel_creation_lock:
                channel = await system.ensure_channel(interaction.guild)
            await interaction.followup.send(f"✅ Painel X-TREINO disponível em {channel.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Não consegui criar/acessar os canais. Confira as permissões **Gerenciar Canais**, "
                "**Ver Canais**, **Enviar Mensagens** e **Ler Histórico de Mensagens**.", ephemeral=True)
        except Exception:
            logger.exception("Falha ao criar os canais do X-TREINO")
            await interaction.followup.send("❌ Erro ao criar o X-TREINO. Confira os logs da hospedagem.", ephemeral=True)


class XTreinoSystem:
    def __init__(self, bot):
        self.bot = bot
        self._channel_creation_lock = asyncio.Lock()
        self.db = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS xtreino_config(
            guild_id INTEGER PRIMARY KEY, channel_id INTEGER, panel_message_id INTEGER,
            category_id INTEGER, admin_channel_id INTEGER,
            signup_policy TEXT NOT NULL DEFAULT 'official'
        );
        CREATE TABLE IF NOT EXISTS xtreino_matches(
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,creator_id INTEGER NOT NULL,
            mode TEXT NOT NULL,teams_count INTEGER NOT NULL,map_name TEXT NOT NULL,match_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',announce_channel_id INTEGER,announcement_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xtreino_players(
            match_id INTEGER NOT NULL,user_id INTEGER NOT NULL,team_no INTEGER,PRIMARY KEY(match_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS xtreino_stats(
            guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,wins INTEGER DEFAULT 0,losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,played INTEGER DEFAULT 0,PRIMARY KEY(guild_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS xtreino_results(
            match_id INTEGER NOT NULL,user_id INTEGER NOT NULL,result TEXT NOT NULL,PRIMARY KEY(match_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS xtreino_bracket(
            id INTEGER PRIMARY KEY AUTOINCREMENT,match_id INTEGER NOT NULL,round_no INTEGER NOT NULL,
            match_no INTEGER NOT NULL,team_a INTEGER NOT NULL,team_b INTEGER NOT NULL,winner_team INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',UNIQUE(match_id,round_no,match_no)
        );
        CREATE TABLE IF NOT EXISTS xtreino_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,creator_id INTEGER NOT NULL,
            capacity INTEGER NOT NULL,game_mode TEXT NOT NULL,map_name TEXT NOT NULL,event_day TEXT NOT NULL,
            event_time TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',public_message_id INTEGER,
            announcement_channel_id INTEGER,announcement_message_id INTEGER,admin_message_id INTEGER,
            room_id TEXT,room_password TEXT,signup_policy TEXT NOT NULL DEFAULT 'official',created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xtreino_event_signups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,event_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,ff_id TEXT NOT NULL,whatsapp TEXT NOT NULL,game_mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',admin_message_id INTEGER,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(event_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS xtreino_event_team_draws(
            event_id INTEGER PRIMARY KEY,team_size INTEGER NOT NULL,draw_number INTEGER NOT NULL DEFAULT 1,
            drawn_by INTEGER NOT NULL,public_message_id INTEGER,announcement_message_id INTEGER,
            confirmed_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xtreino_event_team_members(
            event_id INTEGER NOT NULL,user_id INTEGER NOT NULL,team_no INTEGER NOT NULL,
            PRIMARY KEY(event_id,user_id)
        );
        ''')
        self._ensure_column("xtreino_config", "category_id", "INTEGER")
        self._ensure_column("xtreino_config", "admin_channel_id", "INTEGER")
        self._ensure_column("xtreino_config", "signup_policy", "TEXT NOT NULL DEFAULT 'official'")
        self._ensure_column("xtreino_events", "signup_policy", "TEXT NOT NULL DEFAULT 'official'")
        self.db.commit()

    def _ensure_column(self, table, name, definition):
        cols = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})").fetchall()}
        if name not in cols:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    async def setup(self):
        self.bot.add_view(XTreinoAdminPanel(self))
        @self.bot.command(name='xt', help='Mostra suas estatísticas de X-TREINO.')
        async def xt(ctx, membro: discord.Member | None = None):
            member = membro or ctx.author
            row = self.db.execute(
                'SELECT wins,losses,draws,played FROM xtreino_stats WHERE guild_id=? AND user_id=?',
                (ctx.guild.id, member.id)
            ).fetchone() if ctx.guild else None
            wins, losses, draws, played = tuple(row) if row else (0, 0, 0, 0)
            pct = wins / played * 100 if played else 0
            await ctx.reply(
                f'⚔️ **ESTATÍSTICAS X-TREINO — {member.display_name}**\n'
                f'🏆 Vitórias: **{wins}**\n❌ Derrotas: **{losses}**\n'
                f'🤝 Empates: **{draws}**\n🎮 Participações: **{played}**\n'
                f'📊 Aproveitamento: **{pct:.1f}%**', mention_author=False)

    async def ensure_channel(self, guild: discord.Guild):
        row = self.db.execute('SELECT * FROM xtreino_config WHERE guild_id=?', (guild.id,)).fetchone()
        category = guild.get_channel(int(row['category_id'])) if row and row['category_id'] else None
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if category is None:
            category = await guild.create_category(CATEGORY_NAME, reason='Criar estrutura do X-TREINO')

        everyone = guild.default_role
        bot_member = guild.me
        staff_roles = [r for r in guild.roles if r.permissions.administrator or r.name.strip().casefold() in STAFF_ROLE_NAMES]
        public_overwrites = {everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)}
        private_overwrites = {everyone: discord.PermissionOverwrite(view_channel=False)}
        if bot_member:
            public_overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
            private_overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
        for role in staff_roles:
            public_overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
            private_overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)

        public = guild.get_channel(int(row['channel_id'])) if row and row['channel_id'] else None
        if not isinstance(public, discord.TextChannel):
            public = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if public is None:
            public = await guild.create_text_channel(CHANNEL_NAME, category=category, overwrites=public_overwrites,
                                                     reason='Canal público e somente leitura do X-TREINO')
        else:
            await public.edit(category=category, overwrites=public_overwrites, reason='Atualizar canal do X-TREINO')

        admin = guild.get_channel(int(row['admin_channel_id'])) if row and row['admin_channel_id'] else None
        if not isinstance(admin, discord.TextChannel):
            admin = discord.utils.get(guild.text_channels, name=ADMIN_CHANNEL_NAME)
        if admin is None:
            admin = await guild.create_text_channel(ADMIN_CHANNEL_NAME, category=category, overwrites=private_overwrites,
                                                    reason='Canal privado de inscrições para ADMs')
        else:
            await admin.edit(category=category, overwrites=private_overwrites, reason='Atualizar canal privado do X-TREINO')

        panel_id = int(row['panel_message_id']) if row and row['panel_message_id'] else None
        # Migra o painel antigo que ficava no canal público para o canal privado dos ADMs,
        # removendo somente mensagens antigas do próprio bot com o título conhecido.
        if panel_id:
            try:
                old_public_panel = await public.fetch_message(panel_id)
                if old_public_panel.author.id == self.bot.user.id:
                    try:
                        await old_public_panel.delete()
                    except (discord.Forbidden, discord.HTTPException):
                        await old_public_panel.edit(view=None, content='Painel administrativo movido para o canal privado dos ADMs.')
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        async for old_public in public.history(limit=50):
            if old_public.author.id == self.bot.user.id and old_public.embeds and old_public.embeds[0].title in (
                '⚔️ PAINEL X-TREINO • EVOM NEXUS', '⚔️ PAINEL ADMINISTRATIVO X-TREINO'
            ):
                try:
                    await old_public.delete()
                except (discord.Forbidden, discord.HTTPException):
                    try:
                        await old_public.edit(view=None, content='Painel administrativo movido para o canal privado dos ADMs.')
                    except discord.HTTPException:
                        pass
        panel = None
        if panel_id:
            try:
                panel = await admin.fetch_message(panel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                panel = None
        if panel is None:
            async for old in admin.history(limit=50):
                if old.author.id == self.bot.user.id and old.embeds and old.embeds[0].title == '⚔️ PAINEL ADMINISTRATIVO X-TREINO':
                    panel = old
                    break
        embed = discord.Embed(
            title='⚔️ PAINEL ADMINISTRATIVO X-TREINO',
            description='Somente ADMs podem usar os controles abaixo. Crie o evento, anuncie as inscrições e envie os dados da sala.',
            color=discord.Color.blurple())
        if panel:
            await panel.edit(embed=embed, view=XTreinoAdminPanel(self))
        else:
            panel = await admin.send(embed=embed, view=XTreinoAdminPanel(self))
        self.db.execute('''INSERT INTO xtreino_config(guild_id,channel_id,panel_message_id,category_id,admin_channel_id)
            VALUES(?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,
            panel_message_id=excluded.panel_message_id,category_id=excluded.category_id,admin_channel_id=excluded.admin_channel_id''',
            (guild.id, public.id, panel.id, category.id, admin.id))
        self.db.commit()
        return public

    async def restore_guild(self, guild: discord.Guild):
        config = self.db.execute('SELECT * FROM xtreino_config WHERE guild_id=?', (guild.id,)).fetchone()
        if not config:
            return
        for event in self.db.execute("SELECT * FROM xtreino_events WHERE guild_id=? AND status IN ('open','full')", (guild.id,)).fetchall():
            event_id = int(event['id'])
            if event['public_message_id']:
                self.bot.add_view(XTreinoSignupView(self, event_id), message_id=int(event['public_message_id']))
            if event['announcement_message_id']:
                self.bot.add_view(XTreinoSignupView(self, event_id), message_id=int(event['announcement_message_id']))
        for signup in self.db.execute('''SELECT s.* FROM xtreino_event_signups s JOIN xtreino_events e ON e.id=s.event_id
            WHERE e.guild_id=? AND s.status='pending' ''', (guild.id,)).fetchall():
            if signup['admin_message_id']:
                self.bot.add_view(XTreinoReviewView(self, int(signup['event_id']), int(signup['user_id'])),
                                  message_id=int(signup['admin_message_id']))

    async def create_event(self, interaction: discord.Interaction, capacity: int, map_name: str, day: str, time: str):
        if not 2 <= capacity <= 16:
            return await interaction.followup.send('A quantidade deve ser entre 2 e 16 participantes.', ephemeral=True)
        config = self.db.execute('SELECT channel_id,admin_channel_id FROM xtreino_config WHERE guild_id=?', (interaction.guild_id,)).fetchone()
        if not config:
            await self.ensure_channel(interaction.guild)
            config = self.db.execute('SELECT channel_id,admin_channel_id FROM xtreino_config WHERE guild_id=?', (interaction.guild_id,)).fetchone()
        policy_row = self.db.execute('SELECT signup_policy FROM xtreino_config WHERE guild_id=?', (interaction.guild_id,)).fetchone()
        signup_policy = policy_row['signup_policy'] if policy_row else 'official'
        cur = self.db.execute('''INSERT INTO xtreino_events(guild_id,creator_id,capacity,game_mode,map_name,event_day,event_time,signup_policy)
            VALUES(?,?,?,?,?,?,?,?)''', (interaction.guild_id, interaction.user.id, capacity, 'Informado na inscrição', map_name, day, time, signup_policy))
        event_id = int(cur.lastrowid)
        self.db.commit()
        public = interaction.guild.get_channel(int(config['channel_id']))
        admin = interaction.guild.get_channel(int(config['admin_channel_id']))
        if not isinstance(public, discord.TextChannel) or not isinstance(admin, discord.TextChannel):
            return await interaction.followup.send('❌ Os canais do X-TREINO não foram encontrados. Clique em Criar X-TREINO novamente.', ephemeral=True)
        embed = self.event_embed(event_id)
        msg = await public.send(embed=embed, view=XTreinoSignupView(self, event_id))
        self.db.execute('UPDATE xtreino_events SET public_message_id=? WHERE id=?', (msg.id, event_id))
        staff_embed = discord.Embed(title=f'⚔️ X-TREINO #{event_id} criado',
            description=f'Evento criado por {interaction.user.mention}. As inscrições aparecerão aqui para análise.', color=discord.Color.blurple())
        await admin.send(embed=staff_embed)
        self.db.commit()
        await interaction.followup.send(
            f'✅ X-TREINO #{event_id} criado em {public.mention}. Agora você pode anunciar em outro canal pelo painel.',
            ephemeral=True)

    def event_embed(self, event_id: int):
        event = self.db.execute('SELECT * FROM xtreino_events WHERE id=?', (event_id,)).fetchone()
        count = self.db.execute("SELECT COUNT(*) FROM xtreino_event_signups WHERE event_id=? AND status IN ('pending','confirmed')", (event_id,)).fetchone()[0]
        players = self.db.execute("SELECT user_id FROM xtreino_event_signups WHERE event_id=? AND status IN ('pending','confirmed') ORDER BY id", (event_id,)).fetchall()
        people = ', '.join(f'<@{int(x[0])}>' for x in players) or 'Ninguém inscrito ainda'
        status = {'open': 'INSCRIÇÕES ABERTAS', 'full': 'INSCRIÇÕES ENCERRADAS', 'cancelled': 'EVENTO CANCELADO'}.get(event['status'], 'EVENTO')
        embed = discord.Embed(title=f"⚔️ X-TREINO #{event_id} • {status}",
            description=(f"**Vagas:** {count}/{event['capacity']}\n**Modo de jogo:** cada jogador informa na inscrição\n"
                         f"**Mapa:** {event['map_name']}\n**Dia:** {event['event_day']}\n**Horário:** {event['event_time']}\n\n"
                         f"**Inscritos:**\n{people}\n\nClique em **Inscrever-se** para participar ou em **Cancelar inscrição** para sair."),
            color=discord.Color.green() if event['status'] == 'open' else (discord.Color.red() if event['status'] == 'cancelled' else discord.Color.orange()))
        return embed

    async def refresh_event_messages(self, guild: discord.Guild, event_id: int):
        event = self.db.execute('SELECT * FROM xtreino_events WHERE id=?', (event_id,)).fetchone()
        if not event:
            return
        embed = self.event_embed(event_id)
        for channel_id, message_id in ((event['guild_id'] and self._channel_id(event['guild_id']), event['public_message_id']),
                                       (event['announcement_channel_id'], event['announcement_message_id'])):
            if not channel_id or not message_id:
                continue
            channel = guild.get_channel(int(channel_id))
            if channel:
                try:
                    msg = await channel.fetch_message(int(message_id))
                    await msg.edit(embed=embed, view=XTreinoSignupView(self, event_id) if event['status'] == 'open' else None)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

    def _channel_id(self, guild_id):
        row = self.db.execute('SELECT channel_id FROM xtreino_config WHERE guild_id=?', (guild_id,)).fetchone()
        return int(row['channel_id']) if row and row['channel_id'] else None

    def save_candidate_data(self, guild_id: int, user: discord.Member, ff_id: str, whatsapp: str):
        # A lista de candidatos usa esta mesma tabela; atualizamos apenas os dados de contato,
        # preservando status, ADM responsável e demais campos de uma candidatura existente.
        table = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recruitment_applications'").fetchone()
        if not table:
            logger.warning('Tabela recruitment_applications ainda não existe; dados do X-TREINO ficaram apenas no registro do evento.')
            return
        row = self.db.execute('SELECT guild_id FROM recruitment_applications WHERE user_id=?', (user.id,)).fetchone()
        if row and int(row['guild_id']) == int(guild_id):
            self.db.execute('UPDATE recruitment_applications SET player_name=?,ff_id=?,whatsapp=? WHERE user_id=?',
                            (user.display_name, ff_id, whatsapp, user.id))
        elif not row:
            self.db.execute('''INSERT INTO recruitment_applications
                (user_id,guild_id,channel_id,player_name,ff_id,test_mode,availability,status,staff_id,whatsapp)
                VALUES(?,?,NULL,?,?, 'X-TREINO','Não informado','pending',NULL,?)''',
                (user.id, guild_id, user.display_name, ff_id, whatsapp))
        else:
            logger.warning('Usuário %s já possui cadastro em outro servidor; cadastro de candidatura preservado.', user.id)

    async def register_signup(self, interaction: discord.Interaction, event_id: int, ff_id: str, whatsapp: str, mode: str):
        event = self.db.execute('SELECT * FROM xtreino_events WHERE id=? AND guild_id=?', (event_id, interaction.guild_id)).fetchone()
        if not event or event['status'] != 'open':
            return await interaction.response.send_message('As inscrições deste X-TREINO estão encerradas.', ephemeral=True)
        if event['signup_policy'] == 'official' and not is_official(interaction.user):
            return await interaction.response.send_message('Somente membros com o cargo **💎 Membro Oficial** podem se inscrever neste X-TREINO.', ephemeral=True)
        existing = self.db.execute("SELECT status FROM xtreino_event_signups WHERE event_id=? AND user_id=?", (event_id, interaction.user.id)).fetchone()
        if existing and existing['status'] in ('pending', 'confirmed'):
            return await interaction.response.send_message('Você já está inscrito neste X-TREINO. Use **Cancelar inscrição** se quiser sair.', ephemeral=True)
        count = self.db.execute("SELECT COUNT(*) FROM xtreino_event_signups WHERE event_id=? AND status IN ('pending','confirmed')", (event_id,)).fetchone()[0]
        if count >= int(event['capacity']):
            self.db.execute("UPDATE xtreino_events SET status='full' WHERE id=?", (event_id,)); self.db.commit()
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.refresh_event_messages(interaction.guild, event_id)
            return await interaction.followup.send('As vagas já foram preenchidas.', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        with self.db:
            self.db.execute('''INSERT INTO xtreino_event_signups(event_id,user_id,player_name,ff_id,whatsapp,game_mode,status)
                VALUES(?,?,?,?,?,?, 'pending') ON CONFLICT(event_id,user_id) DO UPDATE SET
                player_name=excluded.player_name,ff_id=excluded.ff_id,whatsapp=excluded.whatsapp,
                game_mode=excluded.game_mode,status='pending',admin_message_id=NULL''',
                (event_id, interaction.user.id, interaction.user.display_name, ff_id, whatsapp, mode))
            self.save_candidate_data(interaction.guild_id, interaction.user, ff_id, whatsapp)
            new_count = int(self.db.execute("SELECT COUNT(*) FROM xtreino_event_signups WHERE event_id=? AND status IN ('pending','confirmed')", (event_id,)).fetchone()[0])
            if new_count >= int(event['capacity']):
                self.db.execute("UPDATE xtreino_events SET status='full' WHERE id=?", (event_id,))
        config = self.db.execute('SELECT admin_channel_id FROM xtreino_config WHERE guild_id=?', (interaction.guild_id,)).fetchone()
        admin_channel = interaction.guild.get_channel(int(config['admin_channel_id'])) if config and config['admin_channel_id'] else None
        if isinstance(admin_channel, discord.TextChannel):
            embed = discord.Embed(title=f'📝 Inscrição para X-TREINO #{event_id}', color=discord.Color.orange())
            embed.add_field(name='Jogador', value=f'{interaction.user.mention} · {discord.utils.escape_markdown(interaction.user.display_name)}', inline=False)
            embed.add_field(name='ID Free Fire', value=f'`{ff_id}`', inline=True)
            embed.add_field(name='WhatsApp', value=f'`{whatsapp}`', inline=True)
            embed.add_field(name='Modo escolhido', value=mode, inline=False)
            admin_msg = await admin_channel.send(embed=embed, view=XTreinoReviewView(self, event_id, interaction.user.id))
            self.db.execute('UPDATE xtreino_event_signups SET admin_message_id=? WHERE event_id=? AND user_id=?',
                            (admin_msg.id, event_id, interaction.user.id)); self.db.commit()
        await self.refresh_event_messages(interaction.guild, event_id)
        await interaction.followup.send('✅ Sua inscrição foi enviada para análise dos ADMs.', ephemeral=True)

    async def cancel_signup(self, interaction: discord.Interaction, event_id: int):
        row = self.db.execute("SELECT * FROM xtreino_event_signups WHERE event_id=? AND user_id=? AND status IN ('pending','confirmed')",
                              (event_id, interaction.user.id)).fetchone()
        if not row:
            return await interaction.response.send_message('Você não possui inscrição ativa neste X-TREINO.', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.db.execute("UPDATE xtreino_event_signups SET status='cancelled' WHERE event_id=? AND user_id=?", (event_id, interaction.user.id))
        event = self.db.execute('SELECT capacity,status FROM xtreino_events WHERE id=?', (event_id,)).fetchone()
        count = self.db.execute("SELECT COUNT(*) FROM xtreino_event_signups WHERE event_id=? AND status IN ('pending','confirmed')", (event_id,)).fetchone()[0]
        if event and event['status'] == 'full' and count < int(event['capacity']):
            self.db.execute("UPDATE xtreino_events SET status='open' WHERE id=?", (event_id,))
        self.db.commit()
        if row['admin_message_id']:
            await self._delete_admin_signup_message(interaction.guild, row['admin_message_id'])
        await self.refresh_event_messages(interaction.guild, event_id)
        await interaction.followup.send('Sua inscrição foi cancelada.', ephemeral=True)

    async def review_signup(self, interaction: discord.Interaction, event_id: int, user_id: int, confirm: bool):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM pode analisar inscrições.', ephemeral=True)
        row = self.db.execute('SELECT * FROM xtreino_event_signups WHERE event_id=? AND user_id=?', (event_id, user_id)).fetchone()
        if not row or row['status'] not in ('pending', 'confirmed'):
            return await interaction.response.send_message('Esta inscrição já foi processada ou cancelada.', ephemeral=True)
        if row['admin_message_id'] and interaction.message and int(row['admin_message_id']) != interaction.message.id:
            return await interaction.response.send_message('Este aviso é antigo. Use o aviso mais recente no canal privado dos ADMs.', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        status = 'confirmed' if confirm else 'cancelled'
        self.db.execute('UPDATE xtreino_event_signups SET status=? WHERE event_id=? AND user_id=?', (status, event_id, user_id))
        event = self.db.execute('SELECT capacity,status FROM xtreino_events WHERE id=?', (event_id,)).fetchone()
        count = self.db.execute("SELECT COUNT(*) FROM xtreino_event_signups WHERE event_id=? AND status IN ('pending','confirmed')", (event_id,)).fetchone()[0]
        if event and event['status'] == 'full' and count < int(event['capacity']):
            self.db.execute("UPDATE xtreino_events SET status='open' WHERE id=?", (event_id,))
        self.db.commit()
        action = '✅ Inscrição confirmada pelo ADM.' if confirm else '❌ Inscrição cancelada pelo ADM.'
        await self._delete_admin_signup_message(interaction.guild, row['admin_message_id'])
        member = interaction.guild.get_member(user_id)
        if member:
            try:
                await member.send(f'{action}\nX-TREINO #{event_id} — {interaction.guild.name}')
            except (discord.Forbidden, discord.HTTPException):
                pass
        await self.refresh_event_messages(interaction.guild, event_id)
        await interaction.followup.send(action, ephemeral=True)

    async def _delete_admin_signup_message(self, guild, message_id):
        """Apaga o aviso temporário do ADM sem remover o registro do participante."""
        if not message_id:
            return
        config = self.db.execute('SELECT admin_channel_id FROM xtreino_config WHERE guild_id=?', (guild.id,)).fetchone()
        channel = guild.get_channel(int(config['admin_channel_id'])) if config and config['admin_channel_id'] else None
        if channel:
            try:
                msg = await channel.fetch_message(int(message_id))
                await msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning('Não foi possível apagar a mensagem administrativa %s', message_id)

    async def publish_announcement(self, interaction: discord.Interaction, event_id: int, channel_id: int):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM pode anunciar.', ephemeral=True)
        event = self.db.execute('SELECT * FROM xtreino_events WHERE id=? AND guild_id=?', (event_id, interaction.guild_id)).fetchone()
        if not event or event['status'] not in ('open', 'full'):
            return await interaction.response.send_message('Este X-TREINO não está disponível para anúncio.', ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message('Canal inválido.', ephemeral=True)
        if event['announcement_message_id']:
            return await interaction.response.send_message('Este X-TREINO já foi anunciado.', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = self.event_embed(event_id)
        msg = await channel.send(embed=embed, view=XTreinoSignupView(self, event_id) if event['status'] == 'open' else None)
        self.db.execute('UPDATE xtreino_events SET announcement_channel_id=?,announcement_message_id=? WHERE id=?',
                        (channel.id, msg.id, event_id)); self.db.commit()
        await interaction.followup.send(f'✅ Anúncio publicado em {channel.mention}.', ephemeral=True)

    async def send_room_details(self, interaction: discord.Interaction, event_id: int, room_id: str, password: str):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM pode enviar ID e senha.', ephemeral=True)
        event = self.db.execute('SELECT * FROM xtreino_events WHERE id=? AND guild_id=?', (event_id, interaction.guild_id)).fetchone()
        if not event:
            return await interaction.response.send_message('X-TREINO não encontrado.', ephemeral=True)
        public_id = self._channel_id(interaction.guild_id)
        channel = interaction.guild.get_channel(public_id) if public_id else None
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message('Canal público do X-TREINO não encontrado.', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = discord.Embed(title=f'🔐 SALA DO X-TREINO #{event_id}',
            description=f'**ID da sala:** `{discord.utils.escape_markdown(room_id)}`\n**Senha:** `{discord.utils.escape_markdown(password)}`\n\nBoa partida a todos! ⚔️',
            color=discord.Color.green())
        await channel.send(embed=embed)
        self.db.execute('UPDATE xtreino_events SET room_id=?,room_password=? WHERE id=?', (room_id, password, event_id)); self.db.commit()
        await interaction.followup.send(f'✅ ID e senha enviados em {channel.mention}.', ephemeral=True)

    def get_draw_players(self, event_id: int):
        return self.db.execute("""SELECT user_id,player_name,ff_id FROM xtreino_event_signups
            WHERE event_id=? AND status='confirmed' ORDER BY id""", (event_id,)).fetchall()

    def make_teams(self, players, team_size: int):
        shuffled = list(players)
        random.SystemRandom().shuffle(shuffled)
        team_count = math.ceil(len(shuffled) / team_size)
        base, extra = divmod(len(shuffled), team_count)
        teams, offset = [], 0
        for index in range(team_count):
            size = base + (1 if index < extra else 0)
            teams.append(shuffled[offset:offset + size])
            offset += size
        return teams

    def draw_embed(self, event_id: int, teams, team_size: int, draw_number: int):
        embed = discord.Embed(title=f'🎲 TIMES SORTEADOS — X-TREINO #{event_id}',
                              description=f'Formato: **até {team_size} por equipe** · Sorteio **#{draw_number}**',
                              color=discord.Color.gold())
        for number, team in enumerate(teams, 1):
            members = '\n'.join(f'• <@{int(p["user_id"])}> — ID FF: `{discord.utils.escape_markdown(str(p["ff_id"]))}`'
                                  for p in team)
            embed.add_field(name=f'⚔️ EQUIPE {number} ({len(team)} jogadores)', value=members or '—', inline=False)
        embed.set_footer(text='Sorteio confirmado pela administração • EVOM NEXUS')
        return embed

    async def publish_draw(self, interaction: discord.Interaction, event_id: int, team_size: int, teams):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM pode confirmar o sorteio.', ephemeral=True)
        event = self.db.execute('SELECT * FROM xtreino_events WHERE id=? AND guild_id=?',
                                (event_id, interaction.guild_id)).fetchone()
        if not event or event['status'] != 'full':
            return await interaction.response.send_message('Este X-TREINO não está com as inscrições completas.', ephemeral=True)
        pending = self.db.execute("SELECT COUNT(*) FROM xtreino_event_signups WHERE event_id=? AND status='pending'",
                                  (event_id,)).fetchone()[0]
        players = self.get_draw_players(event_id)
        if pending:
            return await interaction.response.send_message(
                f'⚠️ Ainda há **{pending} inscrição(ões)** aguardando análise. Confirme ou cancele todas antes do sorteio.', ephemeral=True)
        if len(players) != int(event['capacity']):
            return await interaction.response.send_message(
                f'⚠️ O limite é de {event["capacity"]}, mas existem {len(players)} inscrições confirmadas. '
                'Complete as confirmações antes de sortear.', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        old = self.db.execute('SELECT * FROM xtreino_event_team_draws WHERE event_id=?', (event_id,)).fetchone()
        draw_number = int(old['draw_number']) + 1 if old else 1
        embed = self.draw_embed(event_id, teams, team_size, draw_number)
        public_id = self._channel_id(interaction.guild_id)
        public = interaction.guild.get_channel(public_id) if public_id else None
        announce = interaction.guild.get_channel(int(event['announcement_channel_id'])) if event['announcement_channel_id'] else None
        if not isinstance(public, discord.TextChannel):
            return await interaction.followup.send('❌ Não encontrei o canal público do X-TREINO.', ephemeral=True)
        if old:
            self.db.execute('DELETE FROM xtreino_event_team_members WHERE event_id=?', (event_id,))
        for team_no, team in enumerate(teams, 1):
            for player in team:
                self.db.execute('INSERT INTO xtreino_event_team_members(event_id,user_id,team_no) VALUES(?,?,?)',
                                (event_id, int(player['user_id']), team_no))
        async def upsert_result(channel, message_id):
            if not isinstance(channel, discord.TextChannel):
                return None
            if message_id:
                try:
                    msg = await channel.fetch_message(int(message_id))
                    await msg.edit(embed=embed, content=None)
                    return msg.id
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            try:
                msg = await channel.send(embed=embed)
                return msg.id
            except (discord.Forbidden, discord.HTTPException):
                return None
        public_message_id = await upsert_result(public, old['public_message_id'] if old else None)
        if isinstance(announce, discord.TextChannel) and announce.id == public.id:
            announcement_message_id = public_message_id
        else:
            announcement_message_id = await upsert_result(announce, old['announcement_message_id'] if old else None)
        self.db.execute("""INSERT INTO xtreino_event_team_draws
            (event_id,team_size,draw_number,drawn_by,public_message_id,announcement_message_id,confirmed_at)
            VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(event_id) DO UPDATE SET team_size=excluded.team_size,draw_number=excluded.draw_number,
            drawn_by=excluded.drawn_by,public_message_id=excluded.public_message_id,
            announcement_message_id=excluded.announcement_message_id,confirmed_at=CURRENT_TIMESTAMP""",
            (event_id, team_size, draw_number, interaction.user.id, public_message_id, announcement_message_id))
        self.db.commit()
        missing = ' (não foi possível publicar no canal de anúncios; confira se ele foi configurado e se o bot tem permissão)' if not isinstance(announce, discord.TextChannel) or not announcement_message_id else ''
        await interaction.followup.send(f'✅ Sorteio #{draw_number} confirmado e publicado nos canais configurados.{missing}', ephemeral=True)

    async def cancel_event(self, interaction: discord.Interaction, event_id: int):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM pode encerrar o X-TREINO.', ephemeral=True)
        row = self.db.execute('SELECT * FROM xtreino_events WHERE id=? AND guild_id=?', (event_id, interaction.guild_id)).fetchone()
        if not row or row['status'] == 'cancelled':
            return await interaction.response.send_message('Este X-TREINO já foi encerrado.', ephemeral=True)
        await interaction.response.send_message(
            f'⚠️ Tem certeza que deseja encerrar o X-TREINO **#{event_id}**?\n'
            'Se confirmar, a categoria e os canais do X-TREINO serão excluídos. '
            'O histórico e os dados dos participantes continuarão salvos.',
            view=XTreinoEndConfirmView(self, event_id), ephemeral=True)

    async def confirm_end_event(self, interaction, event_id):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM pode encerrar o X-TREINO.', ephemeral=True)
        event = self.db.execute('SELECT * FROM xtreino_events WHERE id=? AND guild_id=?', (event_id, interaction.guild_id)).fetchone()
        if not event or event['status'] == 'cancelled':
            return await interaction.response.send_message('Este X-TREINO já foi encerrado.', ephemeral=True)
        other = self.db.execute("SELECT COUNT(*) FROM xtreino_events WHERE guild_id=? AND id<>? AND status IN ('open','full')", (interaction.guild_id, event_id)).fetchone()[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.db.execute("UPDATE xtreino_events SET status='cancelled' WHERE id=?", (event_id,))
        self.db.commit()
        await self.refresh_event_messages(interaction.guild, event_id)
        if other:
            return await interaction.followup.send('✅ Evento encerrado. A categoria e os canais foram mantidos porque existem outros X-TREINOS ativos.', ephemeral=True)
        cfg = self.db.execute('SELECT * FROM xtreino_config WHERE guild_id=?', (interaction.guild_id,)).fetchone()
        category = interaction.guild.get_channel(int(cfg['category_id'])) if cfg and cfg['category_id'] else None
        if isinstance(category, discord.CategoryChannel):
            try:
                # Exclui canais filhos antes da categoria: apagar só a categoria não apaga os canais no Discord.
                for channel in list(category.channels):
                    await channel.delete(reason=f'Encerramento confirmado do X-TREINO #{event_id}')
                await category.delete(reason=f'Encerramento confirmado do X-TREINO #{event_id}')
                self.db.execute('UPDATE xtreino_config SET channel_id=NULL,panel_message_id=NULL,category_id=NULL,admin_channel_id=NULL WHERE guild_id=?', (interaction.guild_id,))
                self.db.commit()
                return await interaction.followup.send(f'✅ X-TREINO #{event_id} encerrado. Categoria e canais excluídos; histórico e inscrições preservados.', ephemeral=True)
            except (discord.Forbidden, discord.HTTPException):
                logger.exception('Falha ao excluir categoria/canais do X-TREINO')
                return await interaction.followup.send('⚠️ Evento encerrado, mas não consegui excluir todos os canais/categoria. Confira a permissão Gerenciar Canais do bot.', ephemeral=True)
        await interaction.followup.send(f'✅ X-TREINO #{event_id} encerrado. Não havia categoria ativa para excluir.', ephemeral=True)


class XTreinoEndConfirmView(discord.ui.View):
    def __init__(self, system, event_id):
        super().__init__(timeout=60)
        self.system = system
        self.event_id = int(event_id)

    @discord.ui.button(label='Sim, encerrar e excluir canais', style=discord.ButtonStyle.danger)
    async def yes(self, interaction, button):
        await self.system.confirm_end_event(interaction, self.event_id)

    @discord.ui.button(label='Não, cancelar', style=discord.ButtonStyle.secondary)
    async def no(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        await interaction.response.edit_message(content='✅ Encerramento cancelado. Nada foi excluído.', view=None)


class XTreinoSignupPolicySelect(discord.ui.Select):
    def __init__(self, system):
        self.system = system
        super().__init__(placeholder='🔒 Quem pode se inscrever? (regra padrão dos próximos eventos)',
            min_values=1, max_values=1, custom_id='evom:xt:signup_policy', options=[
                discord.SelectOption(label='Todos', value='all', description='Qualquer membro do servidor pode participar'),
                discord.SelectOption(label='Membro Oficial', value='official', description='Somente quem tem o cargo Membro Oficial')])

    async def callback(self, interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        policy = self.values[0]
        self.system.db.execute("INSERT INTO xtreino_config(guild_id,signup_policy) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET signup_policy=excluded.signup_policy", (interaction.guild_id, policy))
        self.system.db.commit()
        label = 'Todos' if policy == 'all' else 'Membro Oficial'
        await interaction.response.send_message(f'✅ Regra padrão salva: **{label}**. Ela será aplicada aos próximos X-TREINOS.', ephemeral=True)


class XTreinoAdminPanel(discord.ui.View):
    def __init__(self, system):
        super().__init__(timeout=None)
        self.system = system
        self.add_item(XTreinoSignupPolicySelect(system))

    @discord.ui.button(label='⚔️ Criar X-TREINO', style=discord.ButtonStyle.success, custom_id='evom:xt:create_event')
    async def create(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        await interaction.response.send_message('Configure o evento abaixo:', view=XTreinoCreateView(self.system), ephemeral=True)

    @discord.ui.button(label='📣 Anunciar X-TREINO', style=discord.ButtonStyle.primary, custom_id='evom:xt:announce_event')
    async def announce(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        rows = self.system.db.execute("SELECT id FROM xtreino_events WHERE guild_id=? AND status IN ('open','full') AND announcement_message_id IS NULL ORDER BY id DESC LIMIT 25", (interaction.guild_id,)).fetchall()
        if not rows:
            return await interaction.response.send_message('Não há X-TREINO aguardando anúncio.', ephemeral=True)
        await interaction.response.send_message('Escolha o evento:', view=XTreinoEventActionView(self.system, 'announce', [int(r['id']) for r in rows]), ephemeral=True)

    @discord.ui.button(label='🔐 Enviar ID e senha da sala', style=discord.ButtonStyle.primary, custom_id='evom:xt:room_details')
    async def room(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        rows = self.system.db.execute("SELECT id FROM xtreino_events WHERE guild_id=? AND status IN ('open','full') ORDER BY id DESC LIMIT 25", (interaction.guild_id,)).fetchall()
        if not rows:
            return await interaction.response.send_message('Não há X-TREINO ativo.', ephemeral=True)
        await interaction.response.send_message('Escolha o evento para enviar os dados da sala:', view=XTreinoEventActionView(self.system, 'room', [int(r['id']) for r in rows]), ephemeral=True)

    @discord.ui.button(label='📋 X-TREINOS ativos', style=discord.ButtonStyle.secondary, custom_id='evom:xt:active_events')
    async def active(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        rows = self.system.db.execute("SELECT id,capacity,game_mode,map_name,event_day,event_time,status FROM xtreino_events WHERE guild_id=? AND status IN ('open','full') ORDER BY id DESC LIMIT 15", (interaction.guild_id,)).fetchall()
        if not rows:
            return await interaction.response.send_message('Nenhum X-TREINO ativo.', ephemeral=True)
        lines = []
        for r in rows:
            n = self.system.db.execute("SELECT COUNT(*) FROM xtreino_event_signups WHERE event_id=? AND status IN ('pending','confirmed')", (r['id'],)).fetchone()[0]
            lines.append(f"**#{r['id']}** · {n}/{r['capacity']} · {r['game_mode']} · {r['map_name']} · {r['event_day']} {r['event_time']} · {r['status']}")
        await interaction.response.send_message('\n'.join(lines), ephemeral=True)

    @discord.ui.button(label='🎲 Sortear / refazer times', style=discord.ButtonStyle.success, custom_id='evom:xt:draw_teams')
    async def draw(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM pode sortear os times.', ephemeral=True)
        rows = self.system.db.execute("SELECT id,capacity,map_name,event_day,event_time FROM xtreino_events WHERE guild_id=? AND status='full' ORDER BY id DESC LIMIT 25", (interaction.guild_id,)).fetchall()
        if not rows:
            return await interaction.response.send_message('Não há X-TREINO com limite de inscrições atingido para sortear.', ephemeral=True)
        await interaction.response.send_message('Escolha o X-TREINO para configurar o sorteio:',
            view=XTreinoDrawEventSelectView(self.system, [int(r['id']) for r in rows]), ephemeral=True)

    @discord.ui.button(label='❌ Encerrar X-TREINO', style=discord.ButtonStyle.danger, custom_id='evom:xt:cancel_event')
    async def cancel(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        rows = self.system.db.execute("SELECT id FROM xtreino_events WHERE guild_id=? AND status IN ('open','full') ORDER BY id DESC LIMIT 25", (interaction.guild_id,)).fetchall()
        if not rows:
            return await interaction.response.send_message('Não há X-TREINO ativo.', ephemeral=True)
        await interaction.response.send_message('Escolha o evento para encerrar:', view=XTreinoEventActionView(self.system, 'cancel', [int(r['id']) for r in rows]), ephemeral=True)


class XTreinoDrawEventSelectView(discord.ui.View):
    def __init__(self, system, event_ids):
        super().__init__(timeout=180)
        self.add_item(XTreinoDrawEventSelect(system, event_ids))


class XTreinoDrawEventSelect(discord.ui.Select):
    def __init__(self, system, event_ids):
        self.system = system
        options = []
        for event_id in event_ids[:25]:
            row = system.db.execute('SELECT capacity,map_name,event_day,event_time FROM xtreino_events WHERE id=?', (event_id,)).fetchone()
            if row:
                options.append(discord.SelectOption(label=f'X-TREINO #{event_id} · {row["capacity"]} vagas'[:100],
                    description=f'{row["map_name"]} · {row["event_day"]} {row["event_time"]}'[:100], value=str(event_id)))
        super().__init__(placeholder='Selecione o X-TREINO completo', min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        event_id = int(self.values[0])
        players = self.system.get_draw_players(event_id)
        pending = self.system.db.execute("SELECT COUNT(*) FROM xtreino_event_signups WHERE event_id=? AND status='pending'", (event_id,)).fetchone()[0]
        event = self.system.db.execute('SELECT capacity FROM xtreino_events WHERE id=?', (event_id,)).fetchone()
        if pending or len(players) != int(event['capacity']):
            return await interaction.response.send_message(
                f'⚠️ Antes de sortear, confirme/cancele as inscrições pendentes. Confirmados: {len(players)}/{event["capacity"]}. Pendentes: {pending}.', ephemeral=True)
        await interaction.response.send_message(
            f'🎲 **X-TREINO #{event_id}**\nLimite atingido: **{len(players)}/{event["capacity"]}** jogadores confirmados.\nEscolha quantos jogadores ficarão em cada equipe:',
            view=XTreinoDrawSetupView(self.system, event_id, len(players)), ephemeral=True)


class XTreinoDrawSetupView(discord.ui.View):
    def __init__(self, system, event_id, player_count):
        super().__init__(timeout=300)
        self.system = system
        self.event_id = int(event_id)
        self.player_count = int(player_count)
        # Opções fixas solicitadas: duplas, trios ou squads. O tamanho da equipe
        # nunca ultrapassa a quantidade de jogadores confirmados.
        available_sizes = [size for size in (2, 3, 4) if size <= self.player_count]
        self.team_size = 4 if 4 in available_sizes else (3 if 3 in available_sizes else 2)
        options = []
        labels = {2: 'Duplas (2 jogadores)', 3: 'Trios (3 jogadores)', 4: 'Squads (4 jogadores)'}
        for size in available_sizes:
            team_count = math.ceil(self.player_count / size)
            options.append(discord.SelectOption(label=labels[size],
                description=f'{team_count} equipes · distribuição equilibrada', value=str(size), default=size == self.team_size))
        self.add_item(XTreinoDrawTeamSizeSelect(options))

    @discord.ui.button(label='🎲 Sortear times', style=discord.ButtonStyle.success)
    async def generate(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        players = self.system.get_draw_players(self.event_id)
        if len(players) != self.player_count:
            return await interaction.response.send_message('As inscrições mudaram. Abra o sorteio novamente pelo painel.', ephemeral=True)
        teams = self.system.make_teams(players, self.team_size)
        old = self.system.db.execute('SELECT draw_number FROM xtreino_event_team_draws WHERE event_id=?', (self.event_id,)).fetchone()
        next_no = int(old['draw_number']) + 1 if old else 1
        embed = self.system.draw_embed(self.event_id, teams, self.team_size, next_no)
        await interaction.response.edit_message(content='Confira a prévia. **Refazer** gera novas equipes; **Confirmar** publica nos canais.',
                                                embed=embed, view=XTreinoDrawPreviewView(self.system, self.event_id, self.team_size, teams))


class XTreinoDrawTeamSizeSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder='Escolha o tamanho das equipes', min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        self.view.team_size = int(self.values[0])
        format_name = {2: 'Duplas', 3: 'Trios', 4: 'Squads'}[self.view.team_size]
        await interaction.response.edit_message(content=(f'🎲 **X-TREINO #{self.view.event_id}**\n'
            f'Jogadores confirmados: **{self.view.player_count}**\n'
            f'Formato escolhido: **{format_name} ({self.view.team_size} por equipe)**.\nClique em **Sortear times** para ver a prévia.'), view=self.view)


class XTreinoDrawPreviewView(discord.ui.View):
    def __init__(self, system, event_id, team_size, teams):
        super().__init__(timeout=300)
        self.system = system
        self.event_id = int(event_id)
        self.team_size = int(team_size)
        self.teams = teams

    @discord.ui.button(label='🔄 Refazer sorteio', style=discord.ButtonStyle.secondary)
    async def redraw(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        players = self.system.get_draw_players(self.event_id)
        if len(players) != sum(len(team) for team in self.teams):
            return await interaction.response.send_message('As inscrições mudaram. Abra o sorteio novamente.', ephemeral=True)
        # Refazer volta à escolha do formato antes de gerar outra prévia.
        await interaction.response.edit_message(
            content=(f'🔄 **Refazer sorteio — X-TREINO #{self.event_id}**\n'
                     f'Jogadores confirmados: **{len(players)}**\n'
                     'Escolha novamente o formato das equipes:'),
            embed=None,
            view=XTreinoDrawSetupView(self.system, self.event_id, len(players)))

    @discord.ui.button(label='✅ Confirmar e publicar', style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        await self.system.publish_draw(interaction, self.event_id, self.team_size, self.teams)
        if interaction.message:
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                pass


class XTreinoCreateView(discord.ui.View):
    def __init__(self, system):
        super().__init__(timeout=180)
        self.system = system

    @discord.ui.button(label='Preencher informações do X-TREINO', style=discord.ButtonStyle.success)
    async def fill(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        await interaction.response.send_modal(XTreinoCreateModal(self.system))


class XTreinoCreateModal(discord.ui.Modal, title='Configurar X-TREINO'):
    def __init__(self, system):
        super().__init__()
        self.system = system
        self.capacity = discord.ui.TextInput(label='Quantidade de participantes (2 a 16)', placeholder='Ex.: 8', max_length=2)
        self.map_name = discord.ui.TextInput(label='Mapa', placeholder='Ex.: Bermuda', max_length=60)
        self.day = discord.ui.TextInput(label='Dia do evento', placeholder='Ex.: 06/11/2026', max_length=30)
        self.time = discord.ui.TextInput(label='Horário', placeholder='Ex.: 20:00', max_length=20)
        for item in (self.capacity, self.map_name, self.day, self.time):
            self.add_item(item)

    async def on_submit(self, interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        try:
            capacity = int(str(self.capacity.value).strip())
        except ValueError:
            return await interaction.response.send_message('Informe uma quantidade numérica de 2 a 16.', ephemeral=True)
        if not 2 <= capacity <= 16:
            return await interaction.response.send_message('A quantidade deve ser de 2 até 16 participantes.', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.system.create_event(interaction, capacity, str(self.map_name.value).strip(),
                                       str(self.day.value).strip(), str(self.time.value).strip())


class XTreinoSignupView(discord.ui.View):
    def __init__(self, system, event_id):
        super().__init__(timeout=None)
        self.system = system
        self.event_id = int(event_id)
        self.add_item(XTreinoSignupButton(system, event_id, True))
        self.add_item(XTreinoSignupButton(system, event_id, False))


class XTreinoSignupButton(discord.ui.Button):
    def __init__(self, system, event_id, joining):
        self.system = system
        self.event_id = int(event_id)
        self.joining = joining
        super().__init__(label='Inscrever-se' if joining else 'Cancelar inscrição',
                         emoji='📝' if joining else '❌',
                         style=discord.ButtonStyle.success if joining else discord.ButtonStyle.secondary,
                         custom_id=f"evom:xt:{'join' if joining else 'leave'}:{event_id}")

    async def callback(self, interaction):
        if self.joining:
            event = self.system.db.execute('SELECT signup_policy,status FROM xtreino_events WHERE id=?', (self.event_id,)).fetchone()
            if not event or event['status'] != 'open':
                return await interaction.response.send_message('As inscrições deste X-TREINO estão encerradas.', ephemeral=True)
            if event['signup_policy'] == 'official' and not is_official(interaction.user):
                return await interaction.response.send_message('Somente membros com o cargo **💎 Membro Oficial** podem se inscrever neste X-TREINO.', ephemeral=True)
            await interaction.response.send_modal(XTreinoSignupModal(self.system, self.event_id, interaction.user))
        else:
            await self.system.cancel_signup(interaction, self.event_id)


class XTreinoSignupModal(discord.ui.Modal, title='Inscrição no X-TREINO'):
    def __init__(self, system, event_id, member):
        super().__init__()
        self.system = system
        self.event_id = int(event_id)
        self.ff_id = discord.ui.TextInput(label='ID do Free Fire', placeholder='Informe seu ID do jogo', max_length=30)
        self.whatsapp = discord.ui.TextInput(label='WhatsApp', placeholder='Informe seu WhatsApp com DDD', max_length=30)
        self.mode = discord.ui.TextInput(label='Modo de jogo', placeholder='Ex.: Rush ou Sniper', max_length=40)
        row = system.db.execute('SELECT ff_id,whatsapp FROM recruitment_applications WHERE user_id=? AND guild_id=?', (member.id, member.guild.id)).fetchone()
        if row:
            self.ff_id.default = str(row['ff_id'] or '')[:30] or None
            self.whatsapp.default = str(row['whatsapp'] or '')[:30] or None
        for item in (self.ff_id, self.whatsapp, self.mode):
            self.add_item(item)

    async def on_submit(self, interaction):
        ff_id = str(self.ff_id.value).strip()
        whatsapp = str(self.whatsapp.value).strip()
        mode = str(self.mode.value).strip()
        if not ff_id or not whatsapp or not mode:
            return await interaction.response.send_message('Preencha o ID, WhatsApp e modo de jogo.', ephemeral=True)
        await self.system.register_signup(interaction, self.event_id, ff_id, whatsapp, mode)


class XTreinoReviewView(discord.ui.View):
    def __init__(self, system, event_id, user_id):
        super().__init__(timeout=None)
        self.system = system
        self.event_id = int(event_id)
        self.user_id = int(user_id)
        self.add_item(XTreinoReviewButton(system, event_id, user_id, True))
        self.add_item(XTreinoReviewButton(system, event_id, user_id, False))


class XTreinoReviewButton(discord.ui.Button):
    def __init__(self, system, event_id, user_id, confirm):
        self.system = system
        self.event_id = int(event_id)
        self.user_id = int(user_id)
        self.confirm = confirm
        super().__init__(label='Confirmar inscrição' if confirm else 'Cancelar inscrição',
                         style=discord.ButtonStyle.success if confirm else discord.ButtonStyle.danger,
                         custom_id=f"evom:xt:review:{'ok' if confirm else 'no'}:{event_id}:{user_id}")

    async def callback(self, interaction):
        await self.system.review_signup(interaction, self.event_id, self.user_id, self.confirm)


class XTreinoEventActionView(discord.ui.View):
    def __init__(self, system, action, event_ids):
        super().__init__(timeout=120)
        self.system = system
        self.action = action
        self.add_item(XTreinoEventSelect(system, action, event_ids))


class XTreinoEventSelect(discord.ui.Select):
    def __init__(self, system, action, event_ids):
        self.system = system
        self.action = action
        options = []
        for event_id in event_ids[:25]:
            row = system.db.execute('SELECT capacity,game_mode,map_name,event_day,event_time FROM xtreino_events WHERE id=?', (event_id,)).fetchone()
            if row:
                options.append(discord.SelectOption(label=f"X-TREINO #{event_id} · {row['game_mode']}"[:100],
                    description=f"{row['map_name']} · {row['event_day']} {row['event_time']}"[:100], value=str(event_id)))
        super().__init__(placeholder='Selecione o X-TREINO', min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        event_id = int(self.values[0])
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        if self.action == 'announce':
            await interaction.response.send_message('Escolha o canal e depois confirme em **Enviar anúncio**:',
                view=XTreinoAnnouncementView(self.system, event_id), ephemeral=True)
        elif self.action == 'room':
            await interaction.response.send_modal(XTreinoRoomModal(self.system, event_id))
        else:
            await self.system.cancel_event(interaction, event_id)


class XTreinoAnnouncementView(discord.ui.View):
    def __init__(self, system, event_id):
        super().__init__(timeout=180)
        self.system = system
        self.event_id = int(event_id)
        self.channel_id = None
        self.add_item(XTreinoAnnouncementChannelSelect())

    @discord.ui.button(label='📣 Enviar anúncio', style=discord.ButtonStyle.success)
    async def send(self, interaction, button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        if not self.channel_id:
            return await interaction.response.send_message('Selecione um canal primeiro.', ephemeral=True)
        await self.system.publish_announcement(interaction, self.event_id, self.channel_id)


class XTreinoAnnouncementChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder='Selecione o canal do anúncio', channel_types=[discord.ChannelType.text], min_values=1, max_values=1)

    async def callback(self, interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message('Somente ADM.', ephemeral=True)
        self.view.channel_id = self.values[0].id
        await interaction.response.send_message(f'Canal selecionado: {self.values[0].mention}. Agora clique em **Enviar anúncio**.', ephemeral=True)


class XTreinoRoomModal(discord.ui.Modal, title='Enviar dados da sala'):
    def __init__(self, system, event_id):
        super().__init__()
        self.system = system
        self.event_id = int(event_id)
        self.room_id = discord.ui.TextInput(label='ID da sala', placeholder='Informe o ID da sala', max_length=40)
        self.password = discord.ui.TextInput(label='Senha da sala', placeholder='Informe a senha', max_length=40)
        self.add_item(self.room_id)
        self.add_item(self.password)

    async def on_submit(self, interaction):
        await self.system.send_room_details(interaction, self.event_id, str(self.room_id.value).strip(), str(self.password.value).strip())
