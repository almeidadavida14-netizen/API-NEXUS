"""Painel único de gerenciamento de missões EVOM NEXUS."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import discord
from discord import app_commands

log = logging.getLogger(__name__)

class MissionSystem:
    MODES = ("1v1", "2v2", "3v3", "4v4")

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.database.connection
        self.lock = bot.database.lock
        with self.lock, self.db:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS evom_missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                title TEXT NOT NULL, description TEXT NOT NULL, mode TEXT NOT NULL,
                reward INTEGER NOT NULL CHECK(reward >= 0), status TEXT NOT NULL DEFAULT 'open',
                channel_id INTEGER, created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
                expires_at TEXT
            );
            CREATE TABLE IF NOT EXISTS evom_mission_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL, submitter_id INTEGER NOT NULL, winner_id INTEGER NOT NULL,
                proof_url TEXT NOT NULL, note TEXT, status TEXT NOT NULL DEFAULT 'pending',
                reviewed_by INTEGER, reviewed_at TEXT, created_at TEXT NOT NULL,
                UNIQUE(mission_id, winner_id),
                FOREIGN KEY(mission_id) REFERENCES evom_missions(id)
            );
            CREATE INDEX IF NOT EXISTS evom_mission_reports_status
                ON evom_mission_reports(guild_id, status, mission_id);
            CREATE TABLE IF NOT EXISTS evom_mission_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                actor_id INTEGER NOT NULL, action TEXT NOT NULL, mission_id INTEGER,
                report_id INTEGER, details TEXT, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS evom_mission_audit_guild_time
                ON evom_mission_audit(guild_id, created_at);
            """)

    @staticmethod
    def is_admin(interaction):
        member = interaction.user
        perms = getattr(member, "guild_permissions", None)
        from config import STAFF_ROLE_NAMES
        staff_names = {str(x).casefold() for x in STAFF_ROLE_NAMES}
        has_staff_role = any(getattr(r, "name", "").casefold() in staff_names
                             for r in getattr(member, "roles", []))
        return bool(perms and (perms.administrator or perms.manage_guild or has_staff_role))

    async def send_result(self, interaction, content=None, *, embed=None, view=None, ephemeral=True):
        """Send a result whether the interaction was deferred or not."""
        if interaction.response.is_done():
            return await interaction.followup.send(
                content=content, embed=embed, view=view, ephemeral=ephemeral)
        return await interaction.response.send_message(
            content=content, embed=embed, view=view, ephemeral=ephemeral)

    def audit(self, guild_id, actor_id, action, mission_id=None, report_id=None, details=None):
        with self.lock, self.db:
            self.db.execute(
                """INSERT INTO evom_mission_audit
                (guild_id,actor_id,action,mission_id,report_id,details,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (guild_id, actor_id, action, mission_id, report_id, details,
                 datetime.now(timezone.utc).isoformat()))

    def register_commands(self):
        @self.bot.tree.command(name="gerenciador-missoes",
                               description="ADM: abre o painel de gerenciamento de missões.")
        @app_commands.guild_only()
        async def gerenciador(interaction: discord.Interaction):
            if not self.is_admin(interaction):
                return await self.send_result(interaction, 
                    "❌ Apenas ADMs da gerência ou ADMs autorizados podem abrir este painel.",
                    ephemeral=True)
            embed = discord.Embed(
                title="🎯 GERENCIADOR DE MISSÕES • EVOM NEXUS",
                description=("Use os botões abaixo para administrar tudo em um só lugar.\n\n"
                             "📝 **Criar missão** — publicar uma missão com modalidade, pontos e prazo padrão de 72 horas.\n"
                             "📨 **Enviar resultado** — jogador registra o vencedor e o link do print.\n"
                             "🔎 **Analisar resultado** — ADM aprova ou rejeita e libera os pontos.\n"
                             "📋 **Listar missões** — consultar missões abertas e resultados pendentes.\n"
                             "🔒 **Encerrar missão** — fechar uma missão aberta."),
                color=discord.Color.blurple())
            embed.set_footer(text="Os pontos só são creditados após aprovação do ADM.")
            await self.send_result(interaction, embed=embed, view=MissionManagerView(self), ephemeral=True)

    async def create_mission(self, interaction, title, description, mode, reward, channel_id, hours):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await self.send_result(interaction, "❌ ID de canal inválido.", ephemeral=True)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=hours)).isoformat()
        with self.lock, self.db:
            cur = self.db.execute("""INSERT INTO evom_missions
                (guild_id,title,description,mode,reward,channel_id,created_by,created_at,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (interaction.guild.id, title, description, mode, reward, channel.id,
                 interaction.user.id, now.isoformat(), expires))
            mission_id = cur.lastrowid
            self.db.execute("""INSERT INTO evom_mission_audit
                (guild_id,actor_id,action,mission_id,details,created_at)
                VALUES(?,?,?,?,?,?)""",
                (interaction.guild.id, interaction.user.id, "mission_created", mission_id,
                 f"{title} | {mode} | {reward} pontos", now.isoformat()))
        embed = discord.Embed(title=f"🎯 MISSÃO #{mission_id} • {title}",
                              description=description, color=discord.Color.blurple())
        embed.add_field(name="Modalidade", value=mode)
        embed.add_field(name="Recompensa", value=f"{reward} pontos por vencedor aprovado")
        embed.add_field(name="Prazo", value=f"{hours} horas")
        embed.set_footer(text=f"EVOM NEXUS • ID {mission_id}")
        try:
            await channel.send(embed=embed, view=MissionPublicView(self))
        except discord.Forbidden:
            with self.lock, self.db:
                self.db.execute("UPDATE evom_missions SET status='closed' WHERE id=?", (mission_id,))
            return await self.send_result(interaction, 
                "❌ Não tenho permissão para publicar nesse canal. Missão cancelada.", ephemeral=True)
        await self.send_result(interaction, f"✅ Missão **#{mission_id}** publicada em {channel.mention}.", ephemeral=True)

    async def submit_report(self, interaction, mission_id, winner_id, proof_url, note):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        parsed = urlparse(proof_url)
        if parsed.scheme != "https" or not parsed.netloc:
            return await self.send_result(interaction, "❌ Informe um link HTTPS válido para o print.", ephemeral=True)
        with self.lock:
            mission = self.db.execute("SELECT * FROM evom_missions WHERE id=? AND guild_id=?",
                                      (mission_id, interaction.guild.id)).fetchone()
        if not mission or mission["status"] != "open":
            return await self.send_result(interaction, "❌ Missão não encontrada ou encerrada.", ephemeral=True)
        if mission["expires_at"] and datetime.fromisoformat(mission["expires_at"]) <= datetime.now(timezone.utc):
            with self.lock, self.db:
                self.db.execute("UPDATE evom_missions SET status='closed' WHERE id=?", (mission_id,))
            return await self.send_result(interaction, "⌛ O prazo desta missão terminou.", ephemeral=True)
        winner = interaction.guild.get_member(winner_id)
        if winner is None:
            return await self.send_result(interaction, "❌ Não encontrei esse jogador neste servidor.", ephemeral=True)
        try:
            with self.lock, self.db:
                cur = self.db.execute("""INSERT INTO evom_mission_reports
                    (mission_id,guild_id,submitter_id,winner_id,proof_url,note,created_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (mission_id, interaction.guild.id, interaction.user.id, winner_id,
                     proof_url, note[:500], datetime.now(timezone.utc).isoformat()))
                rid = cur.lastrowid
                self.db.execute("""INSERT INTO evom_mission_audit
                    (guild_id,actor_id,action,mission_id,report_id,details,created_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (interaction.guild.id, interaction.user.id, "result_submitted", mission_id,
                     rid, f"Vencedor {winner_id}", datetime.now(timezone.utc).isoformat()))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                return await self.send_result(interaction, "⚠️ Esse vencedor já foi enviado nesta missão.", ephemeral=True)
            log.exception("Falha ao salvar resultado")
            return await self.send_result(interaction, "❌ Erro ao salvar resultado.", ephemeral=True)
        await self.send_result(interaction, f"📨 Resultado **#{rid}** enviado para análise.", ephemeral=True)

    async def review_report(self, interaction, report_id, decision, reason):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        with self.lock, self.db:
            report = self.db.execute("""SELECT r.*,m.reward,m.title,m.status AS mission_status
                FROM evom_mission_reports r JOIN evom_missions m ON m.id=r.mission_id
                WHERE r.id=? AND r.guild_id=?""", (report_id, interaction.guild.id)).fetchone()
            if not report:
                return await self.send_result(interaction, "❌ Resultado não encontrado.", ephemeral=True)
            if report["status"] != "pending":
                return await self.send_result(interaction, "⚠️ Esse resultado já foi analisado.", ephemeral=True)
            if decision == "approved" and report["mission_status"] != "open":
                return await self.send_result(interaction, 
                    "⚠️ A missão está encerrada; o resultado não pode liberar pontos.", ephemeral=True)
            if decision == "approved" and report["mission_status"] == "open":
                stamp = datetime.now(timezone.utc).isoformat()
                self.db.execute("""INSERT INTO evom_ranking_events
                    (guild_id,user_id,category,points,source_key,details,staff_id,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (interaction.guild.id, report["winner_id"], "partida", int(report["reward"]),
                     f"mission:{report_id}:{report['winner_id']}",
                     f"Vitória • missão #{report['mission_id']} • {report['title']}",
                     interaction.user.id, stamp))
            self.db.execute("""UPDATE evom_mission_reports SET status=?,reviewed_by=?,reviewed_at=?
                WHERE id=? AND status='pending'""",
                (decision, interaction.user.id, datetime.now(timezone.utc).isoformat(), report_id))
            self.db.execute("""INSERT INTO evom_mission_audit
                (guild_id,actor_id,action,mission_id,report_id,details,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (interaction.guild.id, interaction.user.id, f"result_{decision}", report["mission_id"],
                 report_id, str(reason)[:300], datetime.now(timezone.utc).isoformat()))
        if decision == "approved":
            msg = f"✅ Resultado **#{report_id}** aprovado. <@{report['winner_id']}> recebeu **{report['reward']} pontos**."
        else:
            msg = f"❌ Resultado **#{report_id}** rejeitado. Motivo: {reason[:300] or 'não informado'}."
        await self.send_result(interaction, msg, ephemeral=True)

    async def list_missions(self, interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        with self.lock:
            missions = self.db.execute("""SELECT id,title,mode,reward,expires_at,status FROM evom_missions
                WHERE guild_id=? ORDER BY id DESC LIMIT 15""",
                (interaction.guild.id,)).fetchall()
            pending = self.db.execute("""SELECT COUNT(*) FROM evom_mission_reports
                WHERE guild_id=? AND status='pending'""", (interaction.guild.id,)).fetchone()[0]
            reports = self.db.execute("""SELECT id,mission_id,winner_id FROM evom_mission_reports
                WHERE guild_id=? AND status='pending' ORDER BY id LIMIT 10""",
                (interaction.guild.id,)).fetchall()
        embed = discord.Embed(title="🎯 Missões EVOM NEXUS", color=discord.Color.blurple())
        embed.description = "\n".join(
            f"**#{m['id']} — {m['title']}** · {m['mode']} · {m['reward']} pts · "
            f"{'aberta' if m['status'] == 'open' else 'encerrada'}"
            for m in missions) or "Nenhuma missão cadastrada."
        embed.add_field(name="Resultados aguardando ADM", value=str(pending), inline=False)
        if reports:
            embed.add_field(name="Pendências", value="\n".join(
                f"Resultado #{r['id']} · missão #{r['mission_id']} · <@{r['winner_id']}>"
                for r in reports), inline=False)
        await self.send_result(interaction, embed=embed, ephemeral=True)

    async def close_mission(self, interaction, mission_id):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        with self.lock, self.db:
            cur = self.db.execute("""UPDATE evom_missions SET status='closed'
                WHERE id=? AND guild_id=? AND status='open'""", (mission_id, interaction.guild.id))
            if cur.rowcount:
                self.db.execute("""INSERT INTO evom_mission_audit
                    (guild_id,actor_id,action,mission_id,created_at)
                    VALUES(?,?,?,?,?)""",
                    (interaction.guild.id, interaction.user.id, "mission_closed", mission_id,
                     datetime.now(timezone.utc).isoformat()))
        await self.send_result(interaction, 
            "✅ Missão encerrada." if cur.rowcount else "❌ Missão não encontrada ou já encerrada.",
            ephemeral=True)


class MissionBaseModal(discord.ui.Modal):
    """Evita interações expiradas sem explicação quando o envio do modal falha."""
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Erro ao processar modal de missão", exc_info=error)
        message = "❌ Não consegui processar esse formulário. Confira os dados e tente novamente."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            log.exception("Também não foi possível responder à interação do modal")


class MissionCreateModal(MissionBaseModal, title="Criar missão"):
    titulo = discord.ui.TextInput(label="Título", max_length=80)
    descricao = discord.ui.TextInput(label="Descrição", style=discord.TextStyle.paragraph, max_length=900)
    modo = discord.ui.TextInput(label="Modalidade: 1v1, 2v2, 3v3 ou 4v4", max_length=3)
    pontos = discord.ui.TextInput(label="Pontos por vencedor", max_length=5)
    canal_id = discord.ui.TextInput(label="ID do canal de divulgação", max_length=25)

    def __init__(self, system):
        super().__init__()
        self.system = system

    async def on_submit(self, interaction):
        if not self.system.is_admin(interaction):
            return await interaction.response.send_message("❌ Acesso restrito aos ADMs.", ephemeral=True)
        mode = str(self.modo).strip().lower()
        try:
            reward = int(str(self.pontos).strip())
            channel_id = int(str(self.canal_id).strip())
            if mode not in self.system.MODES or reward < 0 or channel_id <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "❌ Confira a modalidade, os pontos e o ID do canal. A validade padrão é de 72 horas.",
                ephemeral=True,
            )
        await self.system.create_mission(
            interaction, str(self.titulo).strip(), str(self.descricao).strip(),
            mode, reward, channel_id, 72,
        )


class MissionSubmitModal(MissionBaseModal, title="Enviar resultado"):
    mission_id = discord.ui.TextInput(label="ID da missão", max_length=12)
    winner_id = discord.ui.TextInput(label="ID Discord do vencedor", max_length=25)
    proof_url = discord.ui.TextInput(label="Link HTTPS do print", max_length=400)
    note = discord.ui.TextInput(label="Observação (opcional)", required=False, max_length=500)
    def __init__(self, system):
        super().__init__(); self.system = system
    async def on_submit(self, interaction):
        try: mid, wid = int(str(self.mission_id)), int(str(self.winner_id))
        except ValueError:
            return await interaction.response.send_message("❌ IDs inválidos.", ephemeral=True)
        await self.system.submit_report(interaction, mid, wid, str(self.proof_url), str(self.note))


class MissionReviewModal(MissionBaseModal, title="Analisar resultado"):
    report_id = discord.ui.TextInput(label="ID do resultado", max_length=12)
    decision = discord.ui.TextInput(label="Decisão: aprovar ou rejeitar", max_length=10)
    reason = discord.ui.TextInput(label="Motivo (obrigatório ao rejeitar)", required=False, max_length=300)
    def __init__(self, system):
        super().__init__(); self.system = system
    async def on_submit(self, interaction):
        if not self.system.is_admin(interaction):
            return await interaction.response.send_message("❌ Acesso restrito aos ADMs.", ephemeral=True)
        try: rid = int(str(self.report_id))
        except ValueError:
            return await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
        decision = str(self.decision).strip().casefold()
        if decision not in ("aprovar", "rejeitar"):
            return await interaction.response.send_message("❌ Digite aprovar ou rejeitar.", ephemeral=True)
        if decision == "rejeitar" and not str(self.reason).strip():
            return await interaction.response.send_message("❌ Informe o motivo da rejeição.", ephemeral=True)
        await self.system.review_report(interaction, rid, "approved" if decision == "aprovar" else "rejected", str(self.reason))


class MissionCloseModal(MissionBaseModal, title="Encerrar missão"):
    mission_id = discord.ui.TextInput(label="ID da missão", max_length=12)
    def __init__(self, system):
        super().__init__(); self.system = system
    async def on_submit(self, interaction):
        if not self.system.is_admin(interaction):
            return await interaction.response.send_message("❌ Acesso restrito aos ADMs.", ephemeral=True)
        try: mid = int(str(self.mission_id))
        except ValueError:
            return await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
        await self.system.close_mission(interaction, mid)


class MissionManagerView(discord.ui.View):
    """Persistent manager controls; avoids buttons silently expiring after 15 minutes."""
    def __init__(self, system):
        super().__init__(timeout=None)
        self.system = system
    async def interaction_check(self, interaction):
        # The panel is normally delivered ephemerally to an administrator, but
        # if its view is reused/published, players may submit results only.
        item = self.get_item(interaction.data.get("custom_id", "")) if interaction.data else None
        if item is not None and getattr(item, "custom_id", "") in {"evom:missions:submit", "evom:missions:submit_result"}:
            return True
        if not self.system.is_admin(interaction):
            await interaction.response.send_message("❌ Ação exclusiva dos ADMs autorizados.", ephemeral=True)
            return False
        return True
    async def _open_modal(self, interaction: discord.Interaction, modal: discord.ui.Modal):
        try:
            await interaction.response.send_modal(modal)
        except Exception as exc:
            log.exception("Não foi possível abrir o formulário do gerenciador de missões")
            message = "❌ Não consegui abrir o formulário. Tente executar `/gerenciador-missoes` novamente."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    await interaction.response.send_message(message, ephemeral=True)
            except discord.HTTPException:
                log.exception("Não foi possível responder após falha ao abrir o modal")

    @discord.ui.button(label="Criar missão", style=discord.ButtonStyle.success, emoji="📝", row=0, custom_id="evom:missions:create")
    async def create(self, interaction, button):
        await self._open_modal(interaction, MissionCreateModal(self.system))
    @discord.ui.button(label="Enviar resultado", style=discord.ButtonStyle.primary, emoji="📨", row=0, custom_id="evom:missions:submit")
    async def submit(self, interaction, button):
        await self._open_modal(interaction, MissionSubmitModal(self.system))
    @discord.ui.button(label="Analisar resultado", style=discord.ButtonStyle.primary, emoji="🔎", row=1, custom_id="evom:missions:review")
    async def review(self, interaction, button):
        await self._open_modal(interaction, MissionReviewModal(self.system))
    @discord.ui.button(label="Listar missões", style=discord.ButtonStyle.secondary, emoji="📋", row=1, custom_id="evom:missions:list")
    async def listing(self, interaction, button):
        await self.system.list_missions(interaction)
    @discord.ui.button(label="Encerrar missão", style=discord.ButtonStyle.danger, emoji="🔒", row=2, custom_id="evom:missions:close")
    async def close(self, interaction, button):
        await self._open_modal(interaction, MissionCloseModal(self.system))


class MissionPublicView(discord.ui.View):
    """Public entry point for players to submit results from a mission post."""
    def __init__(self, system):
        super().__init__(timeout=None)
        self.system = system

    @discord.ui.button(label="Enviar resultado", style=discord.ButtonStyle.primary,
                       emoji="📨", custom_id="evom:missions:submit_result")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            return await interaction.response.send_message("Use este botão dentro do servidor.", ephemeral=True)
        await interaction.response.send_modal(MissionSubmitModal(self.system))
