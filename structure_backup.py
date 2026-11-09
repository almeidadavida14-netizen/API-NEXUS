"""Backup and restoration of Discord category/channel structures for the owner panel.

Only structures explicitly selected by the owner are removed. A snapshot is committed
and read back before Discord deletion starts. Existing bot messages are captured as
content/embeds; registered editor publications are recreated through their original
message factories when possible.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

import discord
from config import DATABASE_PATH, OWNER_USER_ID, STAFF_ROLE_NAMES

log = logging.getLogger(__name__)


def _db():
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS structure_backups (
        backup_id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        category_name TEXT NOT NULL,
        category_position INTEGER NOT NULL DEFAULT 0,
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    db.commit()
    return db


def _safe_name(value: str, fallback: str = "restaurado") -> str:
    value = re.sub(r"[^a-zA-Z0-9\-_]", "-", value.lower()).strip("-")
    return (value[:90] or fallback)


def _overwrite_to_dict(overwrite: discord.PermissionOverwrite) -> dict[str, bool | None]:
    return {name: value for name, value in overwrite if value is not None}


def _serialize_embed(embed: discord.Embed) -> dict[str, Any]:
    return embed.to_dict()


async def capture_category(category: discord.CategoryChannel) -> dict[str, Any]:
    guild = category.guild
    snapshot: dict[str, Any] = {
        "schema": 1,
        "category": {"id": category.id, "name": category.name, "position": category.position,
                      "overwrites": _serialize_overwrites(guild, category.overwrites)},
        "channels": [],
    }
    for channel in sorted(category.channels, key=lambda c: (c.position, c.id)):
        session_meta = _central_session_metadata(channel.id)
        item: dict[str, Any] = {
            "id": channel.id, "name": channel.name, "type": str(channel.type), "position": channel.position,
            "overwrites": _serialize_overwrites(guild, channel.overwrites),
            "central_session_id": session_meta["session_id"] if session_meta else None,
            "central_session_user_id": session_meta["user_id"] if session_meta else None,
            "recruitment_user_id": _recruitment_user_id(channel.id),
        }
        if isinstance(channel, discord.TextChannel):
            item.update({"topic": channel.topic, "nsfw": channel.nsfw,
                         "slowmode_delay": channel.slowmode_delay,
                         "default_auto_archive_duration": getattr(channel, "default_auto_archive_duration", None)})
            # For an active Central Gamer channel, preserve the whole transcript.
            # For ordinary channels, capture bot-authored panels/messages only.
            messages = []
            transcript = []
            try:
                async for message in channel.history(limit=None, oldest_first=True):
                    if session_meta:
                        transcript.append({"author_id": message.author.id, "author_name": str(message.author),
                            "is_bot": bool(message.author.bot), "content": message.content,
                            "embeds": [_serialize_embed(e) for e in message.embeds],
                            "attachments": [a.url for a in message.attachments],
                            "created_at": message.created_at.isoformat() if message.created_at else None})
                    if message.author.bot:
                        messages.append({
                            "message_id": message.id, "content": message.content,
                            "embeds": [_serialize_embed(e) for e in message.embeds],
                            "publication": _infer_publication_key(message),
                            "candidate_user_id": _candidate_user_id(message),
                        })
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Não foi possível ler o histórico do canal %s durante o backup", channel.id)
                raise RuntimeError(f"Não consegui ler o histórico de #{channel.name}. Dê ao bot a permissão Ler histórico de mensagens e tente novamente.") from exc
            item["bot_messages"] = messages
            if session_meta:
                item["session_transcript"] = transcript
        elif isinstance(channel, discord.VoiceChannel):
            item.update({"bitrate": channel.bitrate, "user_limit": channel.user_limit,
                         "rtc_region": str(channel.rtc_region) if channel.rtc_region else None})
        elif isinstance(channel, discord.ForumChannel):
            item.update({"topic": channel.topic, "nsfw": channel.nsfw,
                         "slowmode_delay": channel.slowmode_delay,
                         "default_auto_archive_duration": channel.default_auto_archive_duration})
        snapshot["channels"].append(item)
    return snapshot


def _serialize_overwrites(guild: discord.Guild, overwrites: dict[Any, discord.PermissionOverwrite]) -> list[dict[str, Any]]:
    result = []
    for target, overwrite in overwrites.items():
        if isinstance(target, discord.Role):
            result.append({"kind": "role", "id": target.id, "name": target.name,
                           "permissions": _overwrite_to_dict(overwrite)})
        elif isinstance(target, discord.Member):
            result.append({"kind": "member", "id": target.id,
                           "permissions": _overwrite_to_dict(overwrite)})
    return result


def _infer_publication_key(message: discord.Message) -> str | None:
    key = _publication_key(message.id)
    if key:
        return key
    if any(e.title == "🎮 CENTRAL GAMER — NEXUS" for e in message.embeds):
        return "central_gamer_panel"
    custom_ids = []
    for row in getattr(message, "components", []):
        for component in getattr(row, "children", []):
            custom_id = getattr(component, "custom_id", None)
            if custom_id:
                custom_ids.append(custom_id)
    if any("evom:recruit:apply" == value for value in custom_ids):
        return "recruitment_panel"
    if any(value.startswith("evom:recruit:claim_analysis:") for value in custom_ids):
        return "recruitment_analysis_notice"
    if any(value in {"evom:recruit:select_partner", "evom:recruit:transfer_owner", "evom:recruit:decline", "evom:recruit:approve", "evom:recruit:announce"} for value in custom_ids):
        return "recruitment_candidate_view"
    if any(value.startswith("nexus:central-gamer:") for value in custom_ids):
        return "central_gamer_session"
    if any(value.startswith("treinamento:join:") or value in {"treinamento:leave", "treinamento:status", "treinamento:tournament:create"} for value in custom_ids):
        return "queue_messages"
    return None


def _recruitment_user_id(channel_id: int) -> int | None:
    db = sqlite3.connect(DATABASE_PATH)
    try:
        row = db.execute("SELECT user_id FROM recruitment_applications WHERE channel_id=?", (channel_id,)).fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        db.close()


def _candidate_user_id(message: discord.Message) -> int | None:
    for row in getattr(message, "components", []):
        for component in getattr(row, "children", []):
            custom_id = getattr(component, "custom_id", "") or ""
            prefix = "evom:recruit:claim_analysis:"
            if custom_id.startswith(prefix):
                try: return int(custom_id.rsplit(":", 1)[1])
                except ValueError: return None
    return None


def _central_session_metadata(channel_id: int) -> dict[str, int] | None:
    db = sqlite3.connect(DATABASE_PATH)
    try:
        row = db.execute("SELECT session_id,user_id FROM central_gamer_sessions WHERE channel_id=? AND status='open'", (channel_id,)).fetchone()
        return {"session_id": int(row[0]), "user_id": int(row[1])} if row else None
    except sqlite3.Error:
        return None
    finally:
        db.close()


def _publication_key(message_id: int) -> str | None:
    db = _db()
    try:
        row = db.execute("SELECT message_key FROM message_publications WHERE message_id=?", (message_id,)).fetchone()
        return str(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        db.close()


def save_backup(guild_id: int, snapshot: dict[str, Any]) -> int:
    db = _db()
    try:
        cur = db.execute(
            "INSERT INTO structure_backups(guild_id,category_name,category_position,snapshot_json) VALUES(?,?,?,?)",
            (guild_id, snapshot["category"]["name"], snapshot["category"].get("position", 0),
             json.dumps(snapshot, ensure_ascii=False)),
        )
        backup_id = int(cur.lastrowid)
        # Verify persisted JSON before allowing deletion.
        row = db.execute("SELECT snapshot_json FROM structure_backups WHERE backup_id=?", (backup_id,)).fetchone()
        parsed = json.loads(row[0])
        if (parsed.get("schema") != 1 or "channels" not in parsed
                or len(parsed["channels"]) != len(snapshot.get("channels", []))
                or parsed.get("category", {}).get("name") != snapshot.get("category", {}).get("name")):
            raise RuntimeError("O backup salvo não passou na verificação de integridade.")
        db.commit()
        return backup_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_backups(guild_id: int):
    db = _db()
    try:
        return db.execute("SELECT backup_id,category_name,created_at FROM structure_backups WHERE guild_id=? ORDER BY backup_id DESC LIMIT 25", (guild_id,)).fetchall()
    finally:
        db.close()


def get_backup(backup_id: int, guild_id: int):
    db = _db()
    try:
        row = db.execute("SELECT * FROM structure_backups WHERE backup_id=? AND guild_id=?", (backup_id, guild_id)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["snapshot"] = json.loads(data.pop("snapshot_json"))
        return data
    finally:
        db.close()


def _resolve_overwrites(guild: discord.Guild, data: list[dict[str, Any]]) -> dict[Any, discord.PermissionOverwrite]:
    resolved = {}
    for entry in data:
        target = None
        if entry.get("kind") == "role":
            target = guild.get_role(int(entry.get("id", 0)))
            if target is None:
                target = discord.utils.get(guild.roles, name=entry.get("name", ""))
        elif entry.get("kind") == "member":
            target = guild.get_member(int(entry.get("id", 0)))
        if target is not None:
            resolved[target] = discord.PermissionOverwrite(**entry.get("permissions", {}))
    return resolved


async def restore_backup(guild: discord.Guild, backup: dict[str, Any], bot) -> discord.CategoryChannel:
    snap = backup["snapshot"]
    cat_data = snap["category"]
    category = await guild.create_category(
        name=cat_data["name"], overwrites=_resolve_overwrites(guild, cat_data.get("overwrites", [])),
        position=min(int(cat_data.get("position", 0)), len(guild.categories)),
        reason=f"Restaurar backup #{backup['backup_id']} pelo painel do dono",
    )
    restored_messages: list[tuple[discord.TextChannel, dict[str, Any]]] = []
    for item in snap.get("channels", []):
        name = str(item.get("name", "canal"))[:100] or "canal"
        overwrites = _resolve_overwrites(guild, item.get("overwrites", []))
        typ = item.get("type", "text")
        if typ in ("text", "news"):
            channel = await guild.create_text_channel(
                name, category=category, overwrites=overwrites, topic=item.get("topic"),
                nsfw=bool(item.get("nsfw", False)), slowmode_delay=int(item.get("slowmode_delay", 0) or 0),
                reason=f"Restaurar backup #{backup['backup_id']} pelo painel do dono",
            )
            if item.get("recruitment_user_id"):
                db = sqlite3.connect(DATABASE_PATH)
                try:
                    db.execute("UPDATE recruitment_applications SET channel_id=? WHERE user_id=?",
                               (channel.id, int(item["recruitment_user_id"])))
                    db.commit()
                except sqlite3.Error:
                    log.exception("Não foi possível atualizar o canal da candidatura restaurada")
                finally:
                    db.close()
            if item.get("central_session_id"):
                db = sqlite3.connect(DATABASE_PATH)
                try:
                    session_id = int(item["central_session_id"])
                    session_row = db.execute("SELECT user_id,status FROM central_gamer_sessions WHERE session_id=?", (session_id,)).fetchone()
                    if session_row:
                        another_open = db.execute("SELECT 1 FROM central_gamer_sessions WHERE guild_id=? AND user_id=? AND status='open' AND session_id<>? LIMIT 1",
                            (guild.id, int(session_row[0]), session_id)).fetchone()
                        if not another_open and session_row[1] in ("open", "orphaned"):
                            db.execute("UPDATE central_gamer_sessions SET channel_id=?,status='open',closed_at=NULL WHERE session_id=?",
                                       (channel.id, session_id))
                        else:
                            db.execute("UPDATE central_gamer_sessions SET channel_id=? WHERE session_id=?",
                                       (channel.id, session_id))
                    transcript = item.get("session_transcript", [])
                    if transcript:
                        db.execute("CREATE TABLE IF NOT EXISTS central_gamer_archives (session_id INTEGER PRIMARY KEY, transcript_json TEXT NOT NULL, archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                        db.execute("INSERT OR REPLACE INTO central_gamer_archives(session_id,transcript_json,archived_at) VALUES(?,?,CURRENT_TIMESTAMP)",
                                   (int(item["central_session_id"]), json.dumps(transcript, ensure_ascii=False)))
                    db.commit()
                except sqlite3.Error:
                    log.exception("Não foi possível atualizar o canal restaurado da sessão gamer")
                finally:
                    db.close()
                # Recreate a readable transcript and the original session controls.
                transcript = item.get("session_transcript", [])
                if transcript:
                    lines = []
                    for entry in transcript:
                        stamp = entry.get("created_at") or ""
                        who = entry.get("author_name") or "Participante"
                        body = entry.get("content") or "[mensagem sem texto]"
                        lines.append(f"[{stamp}] {who}: {body}")
                        for embed_data in entry.get("embeds", []):
                            if embed_data.get("title"): lines.append(f"  Embed: {embed_data['title']}")
                            if embed_data.get("description"): lines.append(f"  {embed_data['description']}")
                            for field in embed_data.get("fields", []):
                                lines.append(f"  {field.get('name', 'Campo')}: {field.get('value', '')}")
                        for attachment in entry.get("attachments", []):
                            lines.append(f"  Anexo: {attachment}")
                    transcript_bytes = ("HISTÓRICO RESTAURADO DO ATENDIMENTO NEXUS\n\n" + "\n".join(lines)).encode("utf-8")
                    from central_gamer import SessionView
                    await channel.send("📜 Histórico anterior restaurado. Os controles do atendimento estão ativos novamente.",
                        file=discord.File(__import__("io").BytesIO(transcript_bytes), filename="historico-atendimento.txt"),
                        view=SessionView(bot.central_gamer))
            else:
                for msg in item.get("bot_messages", []):
                    restored_messages.append((channel, msg))
        elif typ == "voice":
            channel = await guild.create_voice_channel(
                name, category=category, overwrites=overwrites,
                bitrate=min(int(item.get("bitrate", 64000) or 64000), guild.bitrate_limit),
                user_limit=int(item.get("user_limit", 0) or 0),
                reason=f"Restaurar backup #{backup['backup_id']} pelo painel do dono",
            )
        elif typ == "stage_voice":
            await guild.create_stage_channel(name, category=category, overwrites=overwrites,
                reason=f"Restaurar backup #{backup['backup_id']} pelo painel do dono")
        elif typ == "forum":
            await guild.create_forum(name, category=category, overwrites=overwrites,
                topic=item.get("topic"), nsfw=bool(item.get("nsfw", False)),
                slowmode_delay=int(item.get("slowmode_delay", 0) or 0),
                reason=f"Restaurar backup #{backup['backup_id']} pelo painel do dono")
        else:
            log.warning("Tipo de canal não restaurado no backup: %s", typ)

    # Recreate tracked messages using the original registered factories.
    for channel, msg in restored_messages:
        key = msg.get("publication")
        try:
            embed_objects = [discord.Embed.from_dict(e) for e in msg.get("embeds", [])]
            view = None
            if key == "recruitment_panel" and hasattr(bot, "recruitment"):
                from recruitment import ApplyView
                view = ApplyView(bot.recruitment)
            elif key == "recruitment_analysis_notice" and hasattr(bot, "recruitment"):
                from recruitment import AnalysisNoticeView
                candidate_id = msg.get("candidate_user_id")
                if candidate_id:
                    view = AnalysisNoticeView(bot.recruitment, int(candidate_id))
            elif key == "recruitment_candidate_view" and hasattr(bot, "recruitment"):
                from recruitment import CandidateView
                view = CandidateView(bot.recruitment)
            elif key == "queue_messages":
                from views import QueuePanelView
                view = QueuePanelView(bot)
            elif key == "recruitment_info":
                view = None
            elif key == "central_gamer_panel":
                from central_gamer import CentralEntryView
                view = CentralEntryView(bot.central_gamer)
            elif key == "central_gamer_session":
                from central_gamer import SessionView
                view = SessionView(bot.central_gamer)
            restored_message = await channel.send(content=msg.get("content") or None, embeds=embed_objects or None, view=view)
            if key:
                db = sqlite3.connect(DATABASE_PATH)
                try:
                    db.execute("CREATE TABLE IF NOT EXISTS message_publications (message_id INTEGER PRIMARY KEY, message_key TEXT NOT NULL, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                    db.execute("INSERT OR REPLACE INTO message_publications(message_id,message_key,guild_id,channel_id) VALUES(?,?,?,?)",
                               (restored_message.id, key, guild.id, channel.id))
                    db.commit()
                finally:
                    db.close()
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Falha ao reconstruir mensagem salva no backup %s", backup["backup_id"])
    return category


class StructureBackupHomeView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot

    async def interaction_check(self, interaction):
        if int(getattr(interaction.user, "id", 0)) != OWNER_USER_ID:
            await interaction.response.send_message("⛔ Apenas o dono do bot pode usar este menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Salvar / excluir categoria", emoji="🗂️", style=discord.ButtonStyle.primary)
    async def save_delete(self, interaction, button):
        if not interaction.guild:
            return await interaction.response.send_message("Use este painel dentro de um servidor.", ephemeral=True)
        await interaction.response.edit_message(embed=discord.Embed(
            title="🗂️ Salvar ou excluir categoria",
            description="Selecione a categoria. O NEXUS salva e verifica o backup antes de oferecer a exclusão."),
            view=CategoryBackupSelectView(self.bot, interaction.guild))

    @discord.ui.button(label="Ver backups salvos", emoji="📚", style=discord.ButtonStyle.secondary)
    async def backups(self, interaction, button):
        await interaction.response.edit_message(embed=discord.Embed(
            title="📚 Backups de categorias", description="Selecione um backup para restaurar."),
            view=BackupListView(self.bot, interaction.guild_id))

    @discord.ui.button(label="Voltar ao painel", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        from owner_panel import OwnerHomeView, owner_embed
        await interaction.response.edit_message(embed=owner_embed("👑 Painel do Dono", "Escolha uma categoria:"), view=OwnerHomeView(self.bot))


class CategoryBackupSelectView(discord.ui.View):
    def __init__(self, bot, guild):
        super().__init__(timeout=180)
        self.bot = bot
        self.add_item(CategoryBackupSelect(guild))
    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(embed=discord.Embed(title="📦 Backup e restauração", description="Salve categorias e canais antes de excluir. Depois, restaure a estrutura pelo backup."), view=StructureBackupHomeView(self.bot))


class CategoryBackupSelect(discord.ui.Select):
    def __init__(self, guild):
        categories = sorted(guild.categories, key=lambda c: c.position)[:25]
        options = [discord.SelectOption(label=c.name[:100], value=str(c.id),
                   description=f"{len(c.channels)} canal(is)") for c in categories]
        super().__init__(placeholder="Escolha uma categoria para salvar", min_values=1, max_values=1,
            options=options or [discord.SelectOption(label="Nenhuma categoria disponível", value="none")],
            disabled=not bool(options))

    async def callback(self, interaction):
        if int(interaction.user.id) != OWNER_USER_ID:
            return await interaction.response.send_message("Acesso negado.", ephemeral=True)
        if self.values[0] == "none":
            return await interaction.response.send_message("Nenhuma categoria disponível.", ephemeral=True)
        category = interaction.guild.get_channel(int(self.values[0]))
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("Categoria não encontrada.", ephemeral=True)
        await interaction.response.defer()
        try:
            snapshot = await capture_category(category)
            backup_id = save_backup(interaction.guild_id, snapshot)
        except Exception as exc:
            log.exception("Falha no backup da categoria")
            return await interaction.followup.send(f"❌ Backup não concluído; nada foi excluído. {str(exc)[:300]}", ephemeral=True)
        await interaction.edit_original_response(embed=discord.Embed(
            title="✅ Backup verificado",
            description=f"Categoria **{category.name}** salva no backup `#{backup_id}` com {len(snapshot['channels'])} canal(is).\n\nDeseja excluir a categoria e todos os canais juntos?"),
            view=DeleteCategoryConfirmView(self.bot, category.id, backup_id))


class DeleteCategoryConfirmView(discord.ui.View):
    def __init__(self, bot, category_id, backup_id):
        super().__init__(timeout=120)
        self.bot, self.category_id, self.backup_id = bot, category_id, backup_id

    @discord.ui.button(label="Excluir categoria e canais", style=discord.ButtonStyle.danger)
    async def delete(self, interaction, button):
        if int(interaction.user.id) != OWNER_USER_ID:
            return await interaction.response.send_message("Acesso negado.", ephemeral=True)
        category = interaction.guild.get_channel(self.category_id)
        backup = get_backup(self.backup_id, interaction.guild_id)
        if not isinstance(category, discord.CategoryChannel) or not backup:
            return await interaction.response.send_message("Não encontrei a categoria ou o backup verificado. Nada foi excluído.", ephemeral=True)
        # Ensure the snapshot still represents the selected category before deletion.
        if backup["category_name"] != category.name or int(backup["snapshot"]["category"].get("id", 0)) != category.id:
            return await interaction.response.send_message("A categoria mudou após o backup. Faça um novo backup antes de excluir.", ephemeral=True)
        saved_channels = {int(c.get("id", 0)): (c.get("name"), c.get("type")) for c in backup["snapshot"].get("channels", [])}
        current_channels = {c.id: (c.name, str(c.type)) for c in category.channels}
        if saved_channels != current_channels:
            return await interaction.response.send_message("Os canais foram adicionados, removidos, renomeados ou alterados depois do backup. Nada foi excluído; faça um novo backup.", ephemeral=True)
        if not interaction.guild.me or not interaction.guild.me.guild_permissions.manage_channels:
            return await interaction.response.send_message("Preciso da permissão Gerenciar Canais para excluir a categoria com segurança.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            for channel in list(category.channels):
                await channel.delete(reason=f"Exclusão após backup verificado #{self.backup_id}")
            await category.delete(reason=f"Exclusão após backup verificado #{self.backup_id}")
            await interaction.followup.send(f"✅ Categoria e canais excluídos. Backup `#{self.backup_id}` preservado para restauração.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Sem permissão para excluir todos os canais/categoria. O backup foi mantido.", ephemeral=True)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"⚠️ A exclusão parou por erro do Discord. O backup `#{self.backup_id}` continua salvo. {exc}", ephemeral=True)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Exclusão cancelada. O backup continua salvo.", embed=None, view=None)


class BackupListView(discord.ui.View):
    def __init__(self, bot, guild_id):
        super().__init__(timeout=180)
        self.bot = bot
        self.add_item(BackupSelect(guild_id))
    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(embed=discord.Embed(title="📦 Backup e restauração", description="Salve categorias e canais antes de excluir. Depois, restaure a estrutura pelo backup."), view=StructureBackupHomeView(self.bot))


class BackupSelect(discord.ui.Select):
    def __init__(self, guild_id):
        self.guild_id = guild_id
        rows = list_backups(guild_id) if guild_id else []
        options = [discord.SelectOption(label=f"#{r['backup_id']} · {r['category_name']}"[:100],
                    value=str(r['backup_id']), description=str(r['created_at'])[:100]) for r in rows]
        super().__init__(placeholder="Selecione uma categoria salva", min_values=1, max_values=1,
                         options=options or [discord.SelectOption(label="Nenhum backup salvo", value="none")],
                         disabled=not bool(options))

    async def callback(self, interaction):
        if int(interaction.user.id) != OWNER_USER_ID:
            return await interaction.response.send_message("Acesso negado.", ephemeral=True)
        backup = get_backup(int(self.values[0]), self.guild_id)
        if not backup:
            return await interaction.response.send_message("Backup não encontrado.", ephemeral=True)
        await interaction.response.edit_message(embed=discord.Embed(
            title=f"♻️ Restaurar {backup['category_name']}",
            description=f"Backup `#{backup['backup_id']}` · {len(backup['snapshot'].get('channels', []))} canal(is).\n\nA restauração criará uma nova categoria com os canais e permissões salvos."),
            view=RestoreConfirmView(self.view.bot, backup['backup_id'], self.guild_id))


class RestoreConfirmView(discord.ui.View):
    def __init__(self, bot, backup_id, guild_id):
        super().__init__(timeout=120)
        self.bot, self.backup_id, self.guild_id = bot, backup_id, guild_id

    @discord.ui.button(label="Restaurar categoria e canais", style=discord.ButtonStyle.success)
    async def restore(self, interaction, button):
        if int(interaction.user.id) != OWNER_USER_ID:
            return await interaction.response.send_message("Acesso negado.", ephemeral=True)
        backup = get_backup(self.backup_id, self.guild_id)
        if not backup:
            return await interaction.response.send_message("Backup não encontrado.", ephemeral=True)
        existing = discord.utils.get(interaction.guild.categories, name=backup["category_name"])
        if existing:
            return await interaction.response.send_message(
                f"Já existe uma categoria chamada **{backup['category_name']}**. Para evitar duplicação, renomeie ou remova a existente antes de restaurar.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            category = await restore_backup(interaction.guild, backup, self.bot)
            await interaction.followup.send(f"✅ Estrutura restaurada em {category.mention}. Confira permissões e painéis no servidor.", ephemeral=True)
        except Exception as exc:
            log.exception("Falha ao restaurar backup %s", self.backup_id)
            await interaction.followup.send(f"⚠️ Restauração parcial/interrompida. O backup permanece salvo. Detalhe: {str(exc)[:400]}", ephemeral=True)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Restauração cancelada.", embed=None, view=None)
