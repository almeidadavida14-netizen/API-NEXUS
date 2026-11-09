"""Persistent, owner-managed message templates for safe bot messages."""
from __future__ import annotations
import json, sqlite3
from contextlib import contextmanager
import discord
from config import DATABASE_PATH

CATALOG = {
 'Recrutamento': [
  ('recruitment_panel','Painel de recrutamento',True,'🎯 Recrutamento ALL EVOM','Quer entrar para a guilda? Clique abaixo e preencha sua candidatura. A equipe falará com você por um canal privado.'),
  ('recruitment_info','Informações do teste',True,'🎮 Como funciona o teste','Aguarde a equipe orientar seu teste. Confira seu ID do Free Fire e mantenha a disponibilidade informada na candidatura.'),
  ('application_admin_notice','Aviso de nova candidatura',True,'📥 Nova candidatura para analisar','**Candidato:** {candidate}\n**Nome/Nick:** {name}\n**ID Free Fire:** `{ff_id}`\n**WhatsApp:** `{whatsapp}`\n**Disponibilidade:** {availability}\n**Nenhum canal de teste foi criado ainda.**'),
  ('application_received','Confirmação de candidatura',True,'Candidatura enviada','✅ Candidatura enviada para análise da ADM. O canal privado de teste será criado somente se ela aceitar.'),
  ('candidate_test_notice','Candidatura para avaliação',False,'📝 Candidatura para avaliação','Mensagem vinculada aos controles interativos de teste; não editável com segurança nesta versão.'),
 ],
 'Anúncios': [('new_member_announcement','NOVO MEMBRO',True,'🏆 NOVO MEMBRO NA ALL EVOM! 🔥','Deem as boas-vindas a {member}!\n\nO jogador foi **aprovado no teste** e agora faz parte da nossa família.\n\n**Nome/Nick:** {name}\n**ID do Free Fire:** `{ff_id}`\n**ID do Discord:** `{user_id}`\n**Aprovado por:** {staff}')],
 'Candidatura': [('application_form','Formulário de candidatura',False,'Candidatura ALL EVOM','Campos e validações do formulário são controlados pelo modal; texto não editável com segurança.')],
 'X-TREINO': [('xtreino_messages','Mensagens do X-TREINO',False,'X-TREINO','Mensagens conectadas a estados, botões e inscrições; não editável com segurança nesta versão.')],
 'Filas': [('queue_messages','Painel principal das filas',True,'Filas','Mensagens conectadas à lógica de fila e atualização; não editável com segurança nesta versão.')],
 'Sorteios': [('giveaway_messages','Mensagens de sorteios',False,'Sorteios','Mensagens ligadas aos resultados e controles do sorteio; não editável com segurança nesta versão.')],
 'Mensagens automáticas': [('automatic_messages','Mensagens automáticas',False,'Mensagens automáticas','Mensagens diversas ainda precisam de mapeamento individual; não editável com segurança nesta versão.')],
}

@contextmanager
def _db():
    db=sqlite3.connect(DATABASE_PATH)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS message_customizations (message_key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        db.execute("CREATE TABLE IF NOT EXISTS message_publications (message_id INTEGER PRIMARY KEY, message_key TEXT NOT NULL, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        yield db
        db.commit()
    finally:
        db.close()

def get_template(key, default_title, default_description, default_color=0xD2192D, default_footer=''):
    try:
        with _db() as db:
            row=db.execute('SELECT payload FROM message_customizations WHERE message_key=?',(key,)).fetchone()
        data=json.loads(row[0]) if row else {}
    except Exception:
        data={}
    return {'title':data.get('title',default_title),'description':data.get('description',default_description),'color':int(data.get('color',default_color)),'footer':data.get('footer',default_footer)}

def customized_embed(key, title, description, color=0xD2192D, footer='', **values):
    data=get_template(key,title,description,color,footer)
    try: rendered=data['description'].format_map({k: str(v) for k,v in values.items()})
    except (KeyError, ValueError): rendered=description
    embed=discord.Embed(title=data['title'],description=rendered,color=max(0,min(0xFFFFFF,data['color'])))
    if data['footer']: embed.set_footer(text=data['footer'])
    return embed

class MessageCategorySelect(discord.ui.Select):
    def __init__(self, bot):
        self.bot=bot
        super().__init__(placeholder='Escolha uma categoria de mensagens', options=[discord.SelectOption(label=k,description=f'{sum(1 for x in v if x[2])} editável(is) nesta versão') for k,v in CATALOG.items()])
    async def callback(self, interaction):
        if await _reject(interaction): return
        await interaction.response.edit_message(embed=_embed('Personalizar mensagens',f'Categoria: **{self.values[0]}**\nSelecione uma mensagem.'),view=MessageListView(self.bot,self.values[0]))

async def _reject(i):
    from owner_panel import reject_non_owner
    return await reject_non_owner(i)

def _embed(title,desc): return discord.Embed(title=f'✏️ {title}',description=desc,color=discord.Color.gold())

class MessageEditorHomeView(discord.ui.View):
    def __init__(self,bot):
        super().__init__(timeout=300); self.bot=bot; self.add_item(MessageCategorySelect(bot))
        self.add_item(BackButton(bot))
class BackButton(discord.ui.Button):
    def __init__(self,bot): super().__init__(label='↩️ Voltar ao painel',style=discord.ButtonStyle.secondary,row=1); self.bot=bot
    async def callback(self,i):
        if await _reject(i): return
        from owner_panel import OwnerHomeView,owner_embed
        await i.response.edit_message(embed=owner_embed('👑 Painel do Dono','Escolha uma categoria:'),view=OwnerHomeView(self.bot))

class MessageListView(discord.ui.View):
    def __init__(self,bot,category):
        super().__init__(timeout=300); self.bot=bot; self.category=category
        opts=[]
        for key,label,enabled,title,desc in CATALOG[category]:
            opts.append(discord.SelectOption(label=label[:100],value=key,description=('Editável' if enabled else 'Não editável com segurança') ))
        self.add_item(MessageSelect(bot,category,opts)); self.add_item(BackCategories(bot))
class BackCategories(discord.ui.Button):
    def __init__(self,bot): super().__init__(label='↩️ Categorias',style=discord.ButtonStyle.secondary,row=1); self.bot=bot
    async def callback(self,i):
        if await _reject(i): return
        await i.response.edit_message(embed=_embed('Personalizar mensagens','Escolha uma categoria.'),view=MessageEditorHomeView(self.bot))
class MessageSelect(discord.ui.Select):
    def __init__(self,bot,category,options): self.bot=bot; self.category=category; super().__init__(placeholder='Selecione uma mensagem',options=options)
    async def callback(self,i):
        if await _reject(i): return
        item=next(x for x in CATALOG[self.category] if x[0]==self.values[0])
        key,label,enabled,title,desc=item
        if not enabled:
            return await i.response.send_message(f'ℹ️ **{label}**\n{desc}',ephemeral=True)
        await i.response.edit_message(embed=_embed(label,'Edite título, texto, cor e rodapé. A prévia usa os valores salvos atualmente.'),view=MessageActionsView(self.bot,self.category,item))

class MessageActionsView(discord.ui.View):
    def __init__(self,bot,category,item):
        super().__init__(timeout=300); self.bot=bot; self.category=category; self.item=item
        self.add_item(EditMessageButton(bot,category,item)); self.add_item(PreviewMessageButton(bot,item))
        self.add_item(PublishMessageButton(bot,category,item))
        self.add_item(RestoreMessageButton(bot,category,item)); self.add_item(BackToList(bot,category))
class EditMessageButton(discord.ui.Button):
    def __init__(self,bot,category,item): super().__init__(label='Editar mensagem',style=discord.ButtonStyle.primary); self.bot=bot; self.category=category; self.item=item
    async def callback(self,i):
        if await _reject(i): return
        await i.response.send_modal(EditMessageModal(self.category,self.item))
class EditMessageModal(discord.ui.Modal):
    def __init__(self,category,item):
        super().__init__(title=f'Editar: {item[1]}'[:45]); self.category=category; self.item=item
        key,label,enabled,title,desc=item; current=get_template(key,title,desc)
        self.title_field=discord.ui.TextInput(label='Título',default=current['title'][:256],max_length=256)
        self.desc_field=discord.ui.TextInput(label='Texto / descrição',style=discord.TextStyle.paragraph,default=current['description'][:4000],max_length=4000)
        self.color_field=discord.ui.TextInput(label='Cor hexadecimal (ex.: D2192D)',default=f"{current['color']:06X}",max_length=6)
        self.footer_field=discord.ui.TextInput(label='Rodapé (opcional)',default=current['footer'][:2048],required=False,max_length=2048)
        for f in (self.title_field,self.desc_field,self.color_field,self.footer_field): self.add_item(f)
    async def on_submit(self,i):
        if await _reject(i): return
        try: color=int(str(self.color_field.value).strip().replace('#',''),16)
        except ValueError: return await i.response.send_message('Cor inválida. Use 6 caracteres hexadecimais, por exemplo D2192D.',ephemeral=True)
        payload={'title':str(self.title_field.value),'description':str(self.desc_field.value),'color':color,'footer':str(self.footer_field.value)}
        with _db() as db: db.execute('INSERT INTO message_customizations(message_key,payload,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(message_key) DO UPDATE SET payload=excluded.payload,updated_at=CURRENT_TIMESTAMP',(self.item[0],json.dumps(payload,ensure_ascii=False)))
        await i.response.send_message('✅ Personalização salva permanentemente. As próximas mensagens usarão o novo modelo.',ephemeral=True)
class PreviewMessageButton(discord.ui.Button):
    def __init__(self,bot,item): super().__init__(label='Prévia',style=discord.ButtonStyle.success); self.bot=bot; self.item=item
    async def callback(self,i):
        if await _reject(i): return
        key,label,en,title,desc=self.item
        samples={'member':'Novo membro','name':'Jogador','ff_id':'123456789','user_id':'123456789012345678','staff':'ADM responsável','candidate':'@candidato','whatsapp':'11999999999','availability':'Noite'}
        e=customized_embed(key,title,desc,**samples)
        await i.response.send_message('Prévia da mensagem (sem publicar no canal):',embed=e,ephemeral=True)
class RestoreMessageButton(discord.ui.Button):
    def __init__(self,bot,category,item): super().__init__(label='Restaurar padrão',style=discord.ButtonStyle.danger); self.bot=bot; self.category=category; self.item=item
    async def callback(self,i):
        if await _reject(i): return
        with _db() as db: db.execute('DELETE FROM message_customizations WHERE message_key=?',(self.item[0],))
        await i.response.send_message('↩️ Mensagem restaurada ao padrão original.',ephemeral=True)
class BackToList(discord.ui.Button):
    def __init__(self,bot,category): super().__init__(label='↩️ Mensagens',style=discord.ButtonStyle.secondary,row=1); self.bot=bot; self.category=category
    async def callback(self,i):
        if await _reject(i): return
        await i.response.edit_message(embed=_embed('Personalizar mensagens',f'Categoria: **{self.category}**\nSelecione uma mensagem.'),view=MessageListView(self.bot,self.category))


class PublishMessageButton(discord.ui.Button):
    def __init__(self, bot, category, item):
        super().__init__(label='📨 Publicar mensagem', style=discord.ButtonStyle.success, row=1)
        self.bot, self.category, self.item = bot, category, item

    async def callback(self, interaction):
        if await _reject(interaction): return
        await interaction.response.send_message(
            'Escolha o canal. Você poderá publicar uma nova mensagem ou atualizar uma publicação registrada.',
            view=PublishMessageView(self.bot, self.item), ephemeral=True
        )


class PublishChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder='Selecione o canal de destino',
                         channel_types=[discord.ChannelType.text], min_values=1, max_values=1)

    async def callback(self, interaction):
        if await _reject(interaction): return
        # ChannelSelect pode devolver um objeto parcial dependendo da versão
        # da biblioteca Discord. Guarde apenas o ID e resolva o canal no cache
        # da guild no momento da publicação.
        selected = self.values[0]
        guild = interaction.guild
        if guild is None or getattr(selected, 'guild', None) is None or selected.guild.id != guild.id:
            return await interaction.response.send_message(
                'O canal selecionado não pertence a este servidor. Selecione outro canal.',
                ephemeral=True
            )
        self.view.channel_id = int(selected.id)
        await interaction.response.edit_message(
            content=f'Canal selecionado: {selected.mention}\nEscolha publicar uma nova mensagem ou atualizar uma publicação anterior.',
            view=self.view
        )


class ExistingPublicationSelect(discord.ui.Select):
    def __init__(self, item):
        key = item[0]
        with _db() as db:
            rows = db.execute(
                'SELECT message_id, channel_id FROM message_publications WHERE message_key=? ORDER BY published_at DESC LIMIT 25',
                (key,)
            ).fetchall()
        self.publications = {str(r[0]): int(r[1]) for r in rows}
        options = [discord.SelectOption(label=f'Mensagem {mid}', value=str(mid),
                    description=f'Canal ID {cid}') for mid, cid in self.publications.items()]
        super().__init__(placeholder='Escolha uma mensagem para atualizar',
                         options=options or [discord.SelectOption(label='Nenhuma publicação registrada', value='none')],
                         disabled=not bool(options))

    async def callback(self, interaction):
        if await _reject(interaction): return
        self.view.message_id = int(self.values[0])
        await interaction.response.edit_message(
            content=f'Mensagem existente selecionada (ID {self.view.message_id}).',
            view=self.view
        )


class PublishMessageView(discord.ui.View):
    def __init__(self, bot, item):
        super().__init__(timeout=300)
        self.bot, self.item = bot, item
        self.channel_id = None
        self.message_id = None
        self.add_item(PublishChannelSelect())
        self.add_item(ExistingPublicationSelect(item))
        self.add_item(PublishNewButton())
        self.add_item(UpdateExistingButton())


async def _build_publication(bot, guild, item):
    key, label, enabled, title, description = item
    if key == 'recruitment_panel':
        from recruitment import ApplyView
        return customized_embed(key, title, description, 0xFF0000), ApplyView(bot.recruitment)
    if key == 'recruitment_info':
        return customized_embed(key, title, description, 0x5865F2), None
    if key == 'queue_messages':
        from views import QueuePanelView
        return await bot.build_queue_embed(guild.id), QueuePanelView(bot)
    raise RuntimeError('Este tipo de mensagem ainda não possui uma integração segura com o sistema original.')


async def _publish(interaction, view, update=False):
    if await _reject(interaction): return
    if view.channel_id is None:
        return await interaction.response.send_message('Selecione primeiro o canal de destino.', ephemeral=True)
    guild = interaction.guild
    if guild is None or guild.id != interaction.guild_id:
        return await interaction.response.send_message('Não foi possível identificar o servidor desta interação.', ephemeral=True)
    # Re-resolve o canal pelo ID usando o cache da guild. Isso evita rejeitar
    # um canal válido quando ChannelSelect fornece um objeto parcial/wrapper.
    channel = guild.get_channel(int(view.channel_id))
    if channel is None:
        return await interaction.response.send_message(
            'Não consegui localizar esse canal neste servidor. Selecione o canal novamente.',
            ephemeral=True
        )
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
        return await interaction.response.send_message(
            'O destino precisa ser um canal de texto deste servidor.',
            ephemeral=True
        )
    try:
        embed, components = await _build_publication(view.bot, interaction.guild, view.item)
        if update:
            if not view.message_id:
                return await interaction.response.send_message('Selecione uma mensagem anteriormente publicada pelo editor.', ephemeral=True)
            with _db() as db:
                row = db.execute('SELECT channel_id FROM message_publications WHERE message_id=? AND message_key=?',
                                 (view.message_id, view.item[0])).fetchone()
            if not row:
                return await interaction.response.send_message(
                    'Não posso atualizar essa mensagem com segurança: ela não está registrada como publicação deste painel. Use publicar nova mensagem.',
                    ephemeral=True
                )
            target_channel = interaction.guild.get_channel(int(row[0]))
            if not isinstance(target_channel, discord.TextChannel):
                return await interaction.response.send_message('O canal da publicação original não está acessível. Publique uma nova mensagem.', ephemeral=True)
            try:
                message = await target_channel.fetch_message(view.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return await interaction.response.send_message('Não foi possível confirmar a mensagem original. Publique uma nova mensagem.', ephemeral=True)
            if message.author.id != view.bot.user.id:
                return await interaction.response.send_message('A mensagem não pertence ao bot; atualização bloqueada.', ephemeral=True)
            await message.edit(embed=embed, view=components)
        else:
            message = await channel.send(embed=embed, view=components)
        with _db() as db:
            db.execute("INSERT INTO message_publications(message_id,message_key,guild_id,channel_id) VALUES(?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET message_key=excluded.message_key,guild_id=excluded.guild_id,channel_id=excluded.channel_id",
                       (message.id, view.item[0], interaction.guild_id, message.channel.id))
        action = 'atualizada' if update else 'publicada'
        await interaction.response.send_message(f'✅ Mensagem {action} em {message.channel.mention}. ID: `{message.id}`', ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message('❌ Sem permissão para enviar/editar mensagens nesse canal. Confira Enviar mensagens, Incorporar links e Ler histórico.', ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f'❌ Não foi possível publicar com segurança: {str(exc)[:700]}', ephemeral=True)


class PublishNewButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='Publicar nova mensagem', style=discord.ButtonStyle.success)
    async def callback(self, interaction):
        await _publish(interaction, self.view, update=False)


class UpdateExistingButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='Atualizar mensagem existente', style=discord.ButtonStyle.primary)
    async def callback(self, interaction):
        await _publish(interaction, self.view, update=True)
