"""Sistema de partidas/desafios EVOM NEXUS.

As partidas usam pontos internos de torneio. Este módulo não movimenta dinheiro
nem confirma transferências bancárias.
"""
from __future__ import annotations
import asyncio, logging, io, re
from datetime import datetime, timezone
import discord
from discord.ext import commands
from config import STAFF_ROLE_NAMES
import qrcode

log = logging.getLogger(__name__)
CATEGORY_NAME = "💎・PARTIDAS"

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def fmt_points(value):
    return f"{value:,}".replace(",", ".")

class ValueModal(discord.ui.Modal, title="Definir valor da partida"):
    points = discord.ui.TextInput(label="Pontos internos por jogador", placeholder="10", max_length=8)
    def __init__(self, parent): super().__init__(); self.parent_view=parent
    async def on_submit(self, interaction):
        try:
            value=int(str(self.points.value).strip())
            if not 1 <= value <= 1_000_000: raise ValueError
        except ValueError:
            return await interaction.response.send_message("Informe pontos internos entre 1 e 1.000.000.", ephemeral=True)
        self.parent_view.points=value
        await interaction.response.send_message(f"Valor interno definido: **{fmt_points(value)} pontos por jogador**.", ephemeral=True)

class ModeSelect(discord.ui.Select):
    def __init__(self,parent):
        self.parent_view=parent
        super().__init__(placeholder="Escolha o modo",min_values=1,max_values=1,options=[
            discord.SelectOption(label="1v1",value="1v1"),discord.SelectOption(label="2x2",value="2x2"),
            discord.SelectOption(label="3x3",value="3x3"),discord.SelectOption(label="4x4",value="4x4")])
    async def callback(self,interaction):
        self.parent_view.mode=self.values[0]
        await interaction.response.send_message(f"Modo selecionado: **{self.values[0]}**",ephemeral=True)

class ChallengeView(discord.ui.View):
    def __init__(self,system,creator_id):
        super().__init__(timeout=600); self.system=system; self.creator_id=creator_id; self.mode="1v1"; self.points=None; self.players=[]
        self.add_item(ModeSelect(self))
        select=discord.ui.UserSelect(placeholder="Selecione os jogadores",min_values=2,max_values=8)
        async def chosen(interaction):
            if interaction.user.id != self.creator_id and not self.system.is_admin(interaction.user):
                return await interaction.response.send_message("Somente quem abriu o painel ou a ADM pode configurar a partida.",ephemeral=True)
            self.players=[u.id for u in select.values]
            await interaction.response.send_message("Jogadores: "+", ".join(f"<@{uid}>" for uid in self.players),ephemeral=True)
        select.callback=chosen; self.add_item(select)
    def allowed(self,user): return user.id==self.creator_id or self.system.is_admin(user)
    @discord.ui.button(label="Definir valor",style=discord.ButtonStyle.secondary,emoji="🏆",row=2)
    async def set_value(self,interaction,button):
        if not self.allowed(interaction.user): return await interaction.response.send_message("Somente quem abriu o painel ou a ADM pode configurar.",ephemeral=True)
        await interaction.response.send_modal(ValueModal(self))
    @discord.ui.button(label="Criar partida",style=discord.ButtonStyle.success,emoji="🎮",row=2)
    async def create(self,interaction,button):
        if not self.allowed(interaction.user): return await interaction.response.send_message("Somente quem abriu o painel ou a ADM pode criar.",ephemeral=True)
        expected={"1v1":2,"2x2":4,"3x3":6,"4x4":8}[self.mode]
        if len(self.players)!=expected or len(set(self.players))!=len(self.players): return await interaction.response.send_message(f"O modo {self.mode} precisa de {expected} jogadores diferentes.",ephemeral=True)
        if not self.points: return await interaction.response.send_message("Defina o valor interno primeiro.",ephemeral=True)
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            mid,channel=await self.system.create_match(interaction,self.mode,self.points,self.players)
            await interaction.followup.send(f"✅ Partida **#{mid:03d}** criada: {channel.mention}",ephemeral=True); self.stop()
        except (discord.Forbidden,discord.HTTPException):
            log.exception("Falha ao criar canal de partida")
            await interaction.followup.send("Não consegui criar o canal. Confira as permissões do bot.",ephemeral=True)

class WinnerSelect(discord.ui.Select):
    def __init__(self,system,match_id,players,custom_id_suffix="winner"):
        self.system=system; self.match_id=match_id
        options=[discord.SelectOption(label=f"Jogador {i+1}",value=str(uid),description=f"Selecionar <@{uid}>") for i,uid in enumerate(players[:25])]
        super().__init__(placeholder="Selecionar jogador vencedor",min_values=1,max_values=1,options=options,custom_id=f"evom_ap_{custom_id_suffix}:{match_id}")
    async def callback(self,interaction):
        if not self.system.is_admin(interaction.user): return await interaction.response.send_message("Somente a ADM pode selecionar o vencedor.",ephemeral=True)
        uid=int(self.values[0]); self.system.selected_winner[self.match_id]=uid
        await interaction.response.send_message(f"Vencedor selecionado: <@{uid}>. Agora use **Confirmar vencedor**.",ephemeral=True)

class PixKeyModal(discord.ui.Modal, title="Configurar chave Pix da ADM"):
    pix_key = discord.ui.TextInput(label="Chave Pix", placeholder="CPF, e-mail, telefone ou chave aleatória", max_length=120)
    def __init__(self, system, match_id):
        super().__init__(); self.system=system; self.match_id=match_id
    async def on_submit(self, interaction):
        key=str(self.pix_key.value).strip()
        if len(key) < 3 or any(ord(c) < 32 for c in key):
            return await interaction.response.send_message("Informe uma chave Pix válida.", ephemeral=True)
        self.system.save_pix_key(interaction.guild_id, key)
        await interaction.response.send_message("Chave Pix salva. Vou publicar a chave e o QR Code neste canal.", ephemeral=True)
        await self.system.publish_pix(interaction.channel, interaction.guild_id)

class MatchView(discord.ui.View):
    def __init__(self,system,match_id,players=None):
        super().__init__(timeout=None); self.system=system; self.match_id=match_id
        row=players or system.get_players(match_id)
        # Cada mensagem precisa de custom_id próprio para que várias partidas coexistam.
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id:
                base=child.custom_id.split(":")[0]
                child.custom_id=f"{base}:{match_id}"
        if row: self.add_item(WinnerSelect(system,match_id,row))
    async def admin_only(self,interaction):
        if not self.system.is_admin(interaction.user):
            await interaction.response.send_message("Somente a ADM pode usar este controle.",ephemeral=True); return False
        return True
    @discord.ui.button(label="Confirmar vencedor",style=discord.ButtonStyle.success,emoji="🏆",row=2,custom_id="evom_ap_confirm_winner")
    async def confirm_winner(self,interaction,button):
        if not await self.admin_only(interaction): return
        uid=self.system.selected_winner.get(self.match_id)
        if not uid: return await interaction.response.send_message("Selecione primeiro o jogador vencedor no menu acima.",ephemeral=True)
        await self.system.finish_match(interaction,self.match_id,uid,"vitória")
    @discord.ui.button(label="Vitória por W.O.",style=discord.ButtonStyle.secondary,emoji="🏳️",row=2,custom_id="evom_ap_wo")
    async def wo(self,interaction,button):
        if not await self.admin_only(interaction): return
        uid=self.system.selected_winner.get(self.match_id)
        if not uid: return await interaction.response.send_message("Selecione no menu o jogador que venceu por W.O. e clique novamente.",ephemeral=True)
        await self.system.finish_match(interaction,self.match_id,uid,"W.O.")
    @discord.ui.button(label="Revanche",style=discord.ButtonStyle.primary,emoji="🔥",row=3,custom_id="evom_ap_rematch")
    async def rematch(self,interaction,button):
        if not await self.admin_only(interaction): return
        record=self.system.get_match(self.match_id)
        if not record: return await interaction.response.send_message("Partida não encontrada.",ephemeral=True)
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            mid,ch=await self.system.create_match(interaction,record["mode"],record["points"],self.system.get_players(self.match_id))
            await interaction.followup.send(f"🔥 Revanche criada: {ch.mention} (#{mid:03d}).",ephemeral=True)
        except Exception:
            log.exception("Falha criando revanche"); await interaction.followup.send("Não consegui criar a revanche; confira as permissões do bot.",ephemeral=True)
    @discord.ui.button(label="Alterar valor",style=discord.ButtonStyle.secondary,emoji="✏️",row=3,custom_id="evom_ap_change_value")
    async def change_value(self,interaction,button):
        if not await self.admin_only(interaction): return
        await interaction.response.send_modal(MatchValueModal(self.system,self.match_id))
    @discord.ui.button(label="Chave Pix / QR Code",style=discord.ButtonStyle.secondary,emoji="📲",row=4,custom_id="evom_ap_pix")
    async def pix_button(self,interaction,button):
        if not await self.admin_only(interaction): return
        await interaction.response.send_modal(PixKeyModal(self.system,self.match_id))
    @discord.ui.button(label="Finalizar partida",style=discord.ButtonStyle.danger,emoji="🏁",row=4,custom_id="evom_ap_finish_match")
    async def finish_match_button(self,interaction,button):
        if not await self.admin_only(interaction): return
        await self.system.finish_match(interaction,self.match_id,None,"finalizada sem vencedor")

class MatchValueModal(discord.ui.Modal,title="Alterar valor interno"):
    points=discord.ui.TextInput(label="Pontos internos por jogador",placeholder="10",max_length=8)
    def __init__(self,system,match_id): super().__init__(); self.system=system; self.match_id=match_id
    async def on_submit(self,interaction):
        try:
            value=int(str(self.points.value).strip())
            if not 1<=value<=1_000_000: raise ValueError
        except ValueError: return await interaction.response.send_message("Informe um número entre 1 e 1.000.000.",ephemeral=True)
        with self.system.lock,self.system.db: self.system.db.execute("UPDATE evom_ap_challenges SET points=?,updated_at=? WHERE id=? AND status='waiting'",(value,now_iso(),self.match_id))
        await interaction.response.send_message(f"Valor interno alterado para **{fmt_points(value)} pontos por jogador**.",ephemeral=True)
        await self.system.refresh_match_message(interaction.channel,self.match_id)

class ApostadosSystem:
    def __init__(self,bot):
        self.bot=bot; self.db=bot.database.connection; self.lock=bot.database.lock; self.selected_winner={}
        with self.lock,self.db:
            self.db.executescript("""CREATE TABLE IF NOT EXISTS evom_ap_challenges(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,creator_id INTEGER NOT NULL,mode TEXT NOT NULL,points INTEGER NOT NULL,player_ids TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'waiting',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,winner_id INTEGER,result_type TEXT);""")
            self.db.execute("CREATE TABLE IF NOT EXISTS evom_ap_pix_keys(guild_id INTEGER PRIMARY KEY, pix_key TEXT NOT NULL, updated_at TEXT NOT NULL)")
            cols={r[1] for r in self.db.execute("PRAGMA table_info(evom_ap_challenges)").fetchall()}
            if "points" not in cols: self.db.execute("ALTER TABLE evom_ap_challenges ADD COLUMN points INTEGER NOT NULL DEFAULT 1")
            if "winner_id" not in cols: self.db.execute("ALTER TABLE evom_ap_challenges ADD COLUMN winner_id INTEGER")
            if "result_type" not in cols: self.db.execute("ALTER TABLE evom_ap_challenges ADD COLUMN result_type TEXT")
    @staticmethod
    def is_admin(member):
        perms=getattr(member,"guild_permissions",None); roles={str(r.name).strip().casefold() for r in getattr(member,"roles",[])}; staff={str(x).strip().casefold() for x in STAFF_ROLE_NAMES}
        return bool(perms and (perms.administrator or perms.manage_guild or perms.manage_channels)) or bool(roles & staff)
    def get_match(self,mid):
        with self.lock: row=self.db.execute("SELECT * FROM evom_ap_challenges WHERE id=?",(mid,)).fetchone()
        return dict(row) if row else None
    def get_players(self,mid):
        row=self.get_match(mid); return [int(x) for x in row["player_ids"].split(",") if x] if row else []
    def save_pix_key(self, guild_id, key):
        with self.lock, self.db:
            self.db.execute("INSERT INTO evom_ap_pix_keys(guild_id,pix_key,updated_at) VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET pix_key=excluded.pix_key,updated_at=excluded.updated_at", (guild_id,key,now_iso()))
    def get_pix_key(self, guild_id):
        with self.lock:
            row=self.db.execute("SELECT pix_key FROM evom_ap_pix_keys WHERE guild_id=?",(guild_id,)).fetchone()
        return row[0] if row else None
    @staticmethod
    def _emv_field(tag, value):
        value=str(value)
        return f"{tag}{len(value):02d}{value}"
    def make_pix_payload(self, key):
        # BR Code Pix estático (sem valor fixo); o pagador informa o valor no app bancário.
        gui=self._emv_field("00","br.gov.bcb.pix")
        mai=self._emv_field("26",gui+self._emv_field("01",key))
        payload=(self._emv_field("00","01")+self._emv_field("01","11")+mai+
                 self._emv_field("52","0000")+self._emv_field("53","986")+
                 self._emv_field("58","BR")+self._emv_field("59","EVOM NEXUS")+
                 self._emv_field("60","SAO PAULO")+self._emv_field("62",self._emv_field("05","***"))+"6304")
        crc=0xFFFF
        for b in payload.encode("utf-8"):
            crc ^= b << 8
            for _ in range(8): crc=((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        return payload+f"{crc:04X}"
    async def publish_pix(self, channel, guild_id):
        key=self.get_pix_key(guild_id)
        if not key: return await channel.send("A ADM ainda não configurou a chave Pix.")
        try:
            qr=qrcode.QRCode(box_size=8,border=3); qr.add_data(self.make_pix_payload(key)); qr.make(fit=True)
            image=qr.make_image(fill_color="black",back_color="white")
            buf=io.BytesIO(); image.save(buf,format="PNG"); buf.seek(0)
            file=discord.File(buf,filename="evom_pix_qrcode.png")
            embed=discord.Embed(title="📲 PIX • EVOM NEXUS",description="Escaneie o QR Code no aplicativo do seu banco. O valor deve ser informado e conferido manualmente antes de confirmar qualquer pagamento.\n\n**Chave Pix (copiar):**\n```"+key+"```",color=discord.Color.blurple())
            embed.set_image(url="attachment://evom_pix_qrcode.png")
            await channel.send(embed=embed,file=file)
        except Exception:
            log.exception("Falha gerando QR Pix")
            await channel.send("Não consegui gerar o QR Code. Chave Pix configurada: ```"+key+"```")
    def register_commands(self):
        @self.bot.command(name="partida",help="Abre o painel de criação de partidas EVOM NEXUS.")
        async def partida(ctx):
            if not ctx.guild or not isinstance(ctx.author,discord.Member): return await ctx.reply("Use este comando dentro do servidor.",mention_author=False)
            if not self.is_admin(ctx.author) and not any(r.name.strip().casefold()=="membro oficial" for r in ctx.author.roles): return await ctx.reply("É necessário ser ADM ou ter o cargo **💎 Membro Oficial** para usar partidas.",mention_author=False)
            await ctx.send("💎 **EVOM NEXUS • CRIAR PARTIDA**\nEscolha o modo, selecione os jogadores e defina o valor interno. Cada partida terá seu próprio canal e você poderá abrir várias simultaneamente.",view=ChallengeView(self,ctx.author.id))
    async def create_match(self,interaction,mode,points,players):
        guild=interaction.guild
        if not guild: raise RuntimeError("Servidor não encontrado")
        category=discord.utils.get(guild.categories,name=CATEGORY_NAME)
        staff_names={str(x).strip().casefold() for x in STAFF_ROLE_NAMES}
        def overwrites():
            ow={guild.default_role:discord.PermissionOverwrite(view_channel=False)}
            if guild.me: ow[guild.me]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True,attach_files=True,embed_links=True)
            for role in guild.roles:
                nm=role.name.strip().casefold()
                if nm=="membro oficial": ow[role]=discord.PermissionOverwrite(view_channel=True,send_messages=False,read_message_history=True)
                elif nm in staff_names: ow[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True)
            return ow
        if category is None: category=await guild.create_category(CATEGORY_NAME,overwrites=overwrites(),reason="Categoria de partidas EVOM NEXUS")
        with self.lock: next_id=self.db.execute("SELECT COALESCE(MAX(id),0)+1 FROM evom_ap_challenges WHERE guild_id=?",(guild.id,)).fetchone()[0]
        ow=overwrites()
        for uid in players:
            member=guild.get_member(uid)
            if member: ow[member]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True,embed_links=True)
        channel=await guild.create_text_channel(f"partida-{next_id:03d}",category=category,overwrites=ow,reason="Nova partida EVOM NEXUS")
        with self.lock,self.db:
            cur=self.db.execute("INSERT INTO evom_ap_challenges(guild_id,channel_id,creator_id,mode,points,player_ids,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'waiting',?,?)",(guild.id,channel.id,interaction.user.id,mode,points,",".join(map(str,players)),now_iso(),now_iso())); mid=cur.lastrowid
        await channel.send(content=" ".join(f"<@{uid}>" for uid in players),embed=self.make_embed(mid),view=MatchView(self,mid,players))
        return mid,channel
    def make_embed(self,mid):
        r=self.get_match(mid); players=self.get_players(mid); winner=r.get("winner_id")
        desc=f"**Modo:** {r['mode']}\n**Jogadores:**\n"+"\n".join(f"• <@{uid}>" for uid in players)+f"\n\n**Valor interno:** {fmt_points(r['points'])} pontos por jogador\n**Status:** {r['status']}"
        if winner: desc+=f"\n**Vencedor:** <@{winner}> ({r.get('result_type') or 'resultado'})"
        desc+="\n\nEnvie aqui os prints do resultado. A ADM revisará antes de confirmar."
        return discord.Embed(title=f"💎 EVOM NEXUS • PARTIDA #{mid:03d}",description=desc,color=discord.Color.blurple())
    async def refresh_match_message(self,channel,mid):
        try:
            async for msg in channel.history(limit=20):
                if msg.author.id==self.bot.user.id and msg.embeds and msg.embeds[0].title and f"#{mid:03d}" in msg.embeds[0].title:
                    await msg.edit(embed=self.make_embed(mid),view=MatchView(self,mid,self.get_players(mid))); return
        except Exception: log.exception("Falha atualizando painel da partida")
    async def finish_match(self,interaction,mid,winner_id,result_type):
        with self.lock,self.db: self.db.execute("UPDATE evom_ap_challenges SET status='finished',winner_id=?,result_type=?,updated_at=? WHERE id=?",(winner_id,result_type,now_iso(),mid))
        await interaction.response.send_message("Resultado registrado. O canal será fechado em 40 segundos.",ephemeral=True)
        await interaction.channel.send(embed=discord.Embed(title="🏁 PARTIDA FINALIZADA",description=(f"**Resultado:** {result_type}\n"+(f"**Vencedor:** <@{winner_id}>\n" if winner_id else "")+"Este canal será removido em **40 segundos**."),color=discord.Color.green()))
        async def cleanup(channel):
            await asyncio.sleep(40); category=channel.category
            try: await channel.delete(reason=f"Partida EVOM #{mid:03d} finalizada")
            except (discord.NotFound,discord.Forbidden,discord.HTTPException): log.exception("Falha removendo canal da partida %s",mid); return
            if category and category.name==CATEGORY_NAME:
                try:
                    if not category.channels: await category.delete(reason="Categoria de partidas vazia")
                except (discord.NotFound,discord.Forbidden,discord.HTTPException): log.exception("Falha removendo categoria vazia")
        asyncio.create_task(cleanup(interaction.channel))
    async def restore_views(self):
        with self.lock: rows=self.db.execute("SELECT id FROM evom_ap_challenges WHERE status='waiting'").fetchall()
        for row in rows: self.bot.add_view(MatchView(self,row["id"],self.get_players(row["id"])))
