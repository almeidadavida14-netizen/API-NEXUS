"""Interactive Discord views for queues."""

from __future__ import annotations

import discord


class QueuePanelView(discord.ui.View):
    def __init__(self, bot: "TreinamentoBot"):
        super().__init__(timeout=None)
        self.bot = bot
        for mode, label, emoji in (
            ("1v1", "Entrar no 1v1", "🥊"),
            ("2v2", "Entrar no 2v2", "👥"),
            ("3v3", "Entrar no 3v3", "👥"),
            ("4v4", "Entrar no 4v4", "👥"),
        ):
            self.add_item(JoinQueueButton(bot, mode, label, emoji))
        self.add_item(LeaveQueueButton(bot))
        self.add_item(StatusButton(bot))
        self.add_item(TournamentCreateButton(bot))
        from xtreino import XTreinoCreateButton
        self.add_item(XTreinoCreateButton(bot))


class JoinQueueButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", queue_name: str, label: str, emoji: str):
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.primary,
            custom_id=f"treinamento:join:{queue_name}",
        )
        self.bot = bot
        self.queue_name = queue_name

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_join(interaction, self.queue_name)


class LeaveQueueButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot"):
        super().__init__(
            label="Sair da fila",
            emoji="🚪",
            style=discord.ButtonStyle.secondary,
            custom_id="treinamento:leave",
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_leave(interaction)


class StatusButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot"):
        super().__init__(
            label="Status das filas",
            emoji="📊",
            style=discord.ButtonStyle.success,
            custom_id="treinamento:status",
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.send_status(interaction)


class TournamentCreateButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot"):
        super().__init__(
            label="Criar Torneio",
            emoji="🏆",
            style=discord.ButtonStyle.primary,
            custom_id="treinamento:tournament:create",
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_create(interaction)


class TournamentPanelView(discord.ui.View):
    def __init__(
        self,
        bot: "TreinamentoBot",
        status: str = "registration",
        capacity: int = 4,
        mode: str = "2v2",
    ):
        super().__init__(timeout=None)
        self.bot = bot
        registration = status == "registration"
        ready = status == "ready"
        active = status == "active"
        open_tournament = status in {"registration", "ready", "active"}
        can_manage_tournament = registration or ready
        can_manage_matchups = ready and capacity == 4 and mode == "2v2"
        self.add_item(TournamentAddPairButton(bot, disabled=not registration))
        self.add_item(TournamentListPairsButton(bot))
        self.add_item(TournamentManageButton(bot, disabled=not can_manage_tournament))
        self.add_item(TournamentManageMatchupsButton(bot, disabled=not can_manage_matchups))
        self.add_item(TournamentAnnouncementButton(bot, disabled=not open_tournament))
        self.add_item(TournamentCloseButton(bot, disabled=not registration))
        self.add_item(TournamentStartButton(bot, disabled=not (registration or ready)))
        self.add_item(TournamentBracketButton(bot, disabled=not open_tournament))
        self.add_item(TournamentCancelButton(bot, disabled=not (registration or ready or active)))


class TournamentAddPairButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", *, disabled: bool = False):
        super().__init__(label="Adicionar Equipe", emoji="➕", style=discord.ButtonStyle.success, custom_id="treinamento:tournament:add-team", disabled=disabled)
        self.bot = bot
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_add_pair(interaction)


class TournamentListPairsButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot"):
        super().__init__(label="Ver Equipes", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="treinamento:tournament:list-teams")
        self.bot = bot
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_list_pairs(interaction)


class TournamentManageButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", *, disabled: bool = False):
        super().__init__(label="Gerenciar Torneio", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="treinamento:tournament:manage", disabled=disabled)
        self.bot = bot
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_manage(interaction)


class TournamentSetPrizeButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", tournament_id: int, owner_id: int):
        super().__init__(
            label="Definir premiação",
            emoji="💰",
            style=discord.ButtonStyle.primary,
            custom_id=f"treinamento:tournament:prize:{tournament_id}",
        )
        self.bot = bot
        self.tournament_id = tournament_id
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Este menu pertence ao ADM que o abriu.", ephemeral=True
            )
            return
        await self.bot.open_tournament_prize_modal(interaction, self.tournament_id)


class TournamentAnnouncementButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", *, disabled: bool = False):
        super().__init__(label="Anúncios", emoji="📢", style=discord.ButtonStyle.primary, custom_id="treinamento:tournament:announcement", disabled=disabled)
        self.bot = bot
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_announcement(interaction)


class TournamentCloseButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", *, disabled: bool = False):
        super().__init__(label="Encerrar Inscrições", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="treinamento:tournament:close", disabled=disabled)
        self.bot = bot
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_close(interaction)


class TournamentStartButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", *, disabled: bool = False):
        super().__init__(label="Iniciar Torneio", emoji="⚔️", style=discord.ButtonStyle.success, custom_id="treinamento:tournament:start", disabled=disabled)
        self.bot = bot
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_start(interaction)


class TournamentBracketButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", *, disabled: bool = False):
        super().__init__(label="Ver Chaveamento", emoji="🏆", style=discord.ButtonStyle.primary, custom_id="treinamento:tournament:bracket", disabled=disabled)
        self.bot = bot
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_bracket(interaction)


class TournamentManageMatchupsButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", *, disabled: bool = False):
        super().__init__(
            label="Gerenciar Confrontos",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id="treinamento:tournament:manage-matchups",
            disabled=disabled,
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_manage_matchups(interaction)


class TournamentCancelButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", *, disabled: bool = False):
        super().__init__(label="Cancelar Torneio", emoji="❌", style=discord.ButtonStyle.danger, custom_id="treinamento:tournament:cancel", disabled=disabled)
        self.bot = bot
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_cancel(interaction)


class TournamentSizeView(discord.ui.View):
    def __init__(self, bot: "TreinamentoBot"):
        super().__init__(timeout=120)
        for mode, team_size in (("1v1",1),("2v2",2),("3v3",3),("4v4",4)):
            for capacity in (4,8,16):
                self.add_item(TournamentSizeButton(bot, mode, capacity, team_size))


class TournamentSizeButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", mode: str, capacity: int, team_size: int):
        super().__init__(label=f"{mode} • {capacity} equipes", style=discord.ButtonStyle.primary, custom_id=f"treinamento:tournament:size:{mode}:{capacity}")
        self.bot=bot; self.mode=mode; self.capacity=capacity; self.team_size=team_size
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_size(interaction, self.mode, self.capacity)


class TournamentPairSelectionView(discord.ui.View):
    def __init__(self, bot: "TreinamentoBot", tournament_id: int, owner_id: int, team_size: int):
        super().__init__(timeout=180)
        self.bot=bot; self.tournament_id=tournament_id; self.owner_id=owner_id
        self.user_select=discord.ui.UserSelect(placeholder=f"Selecione {team_size} jogador(es)", min_values=team_size, max_values=team_size, custom_id=f"treinamento:tournament:team-users:{tournament_id}:{owner_id}")
        self.add_item(self.user_select)
        self.add_item(TournamentPairConfirmButton(bot,tournament_id,owner_id))


class TournamentPairConfirmButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", tournament_id: int, owner_id: int):
        super().__init__(label="Confirmar Equipe", emoji="✅", style=discord.ButtonStyle.success, custom_id=f"treinamento:tournament:team-confirm:{tournament_id}:{owner_id}")
        self.bot=bot; self.tournament_id=tournament_id; self.owner_id=owner_id
    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esta seleção pertence ao ADM que a abriu.",ephemeral=True); return
        if not isinstance(self.view,TournamentPairSelectionView):
            await interaction.response.send_message("Não foi possível ler a seleção.",ephemeral=True); return
        await self.bot.handle_tournament_pair_selection(interaction,self.tournament_id,list(self.view.user_select.values))


class TournamentPairsView(discord.ui.View):
    def __init__(self, bot: "TreinamentoBot", tournament_id: int, owner_id: int, page: int = 0):
        super().__init__(timeout=120); self.bot=bot; self.tournament_id=tournament_id; self.owner_id=owner_id; self.page=page
        prev=discord.ui.Button(label="Anterior",emoji="◀️",style=discord.ButtonStyle.secondary,custom_id=f"treinamento:tournament:pairs:prev:{tournament_id}"); prev.callback=self.previous_page
        nxt=discord.ui.Button(label="Próxima",emoji="▶️",style=discord.ButtonStyle.secondary,custom_id=f"treinamento:tournament:pairs:next:{tournament_id}"); nxt.callback=self.next_page
        self.add_item(prev); self.add_item(nxt)
    async def _change_page(self, interaction: discord.Interaction, delta: int) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Este menu pertence a quem o abriu.",ephemeral=True); return
        teams=self.bot.database.list_tournament_pairs(self.tournament_id); pages=max(1,(len(teams)+7)//8); self.page=max(0,min(self.page+delta,pages-1))
        await interaction.response.edit_message(embed=self.bot.tournament_pairs_embed(self.tournament_id,self.page),view=self)
    async def previous_page(self, interaction: discord.Interaction): await self._change_page(interaction,-1)
    async def next_page(self, interaction: discord.Interaction): await self._change_page(interaction,1)


class TournamentMatchupsView(discord.ui.View):
    def __init__(
        self,
        bot: "TreinamentoBot",
        tournament_id: int,
        owner_id: int,
        guild: discord.Guild,
        teams: list,
        current_plan: tuple[int, int, int, int],
    ):
        super().__init__(timeout=180)
        self.bot = bot
        self.tournament_id = tournament_id
        self.owner_id = owner_id
        team_ids = [team.pair_id for team in teams]
        possible_plans = (
            (team_ids[0], team_ids[1], team_ids[2], team_ids[3]),
            (team_ids[0], team_ids[2], team_ids[1], team_ids[3]),
            (team_ids[0], team_ids[3], team_ids[1], team_ids[2]),
        )
        options = []
        for plan in possible_plans:
            a, b, c, d = (bot.database.get_tournament_pair(pair_id) for pair_id in plan)
            if not all((a, b, c, d)):
                continue
            name_a = bot.tournament_pair_name(guild, a)
            name_b = bot.tournament_pair_name(guild, b)
            name_c = bot.tournament_pair_name(guild, c)
            name_d = bot.tournament_pair_name(guild, d)
            options.append(
                discord.SelectOption(
                    label=f"Semifinal 1: Equipe {a.slot} × {b.slot} | Semi 2: {c.slot} × {d.slot}",
                    value=",".join(str(pair_id) for pair_id in plan),
                    description=f"{name_a} × {name_b} | {name_c} × {name_d}"[:100],
                    default=plan == current_plan,
                )
            )
        select = discord.ui.Select(
            placeholder="Escolha os confrontos das semifinais",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"treinamento:tournament:matchups:{tournament_id}",
        )

        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "Este menu pertence ao ADM que o abriu.", ephemeral=True
                )
                return
            pair_ids = tuple(int(pair_id) for pair_id in select.values[0].split(","))
            await self.bot.handle_tournament_set_matchups(
                interaction, self.tournament_id, pair_ids
            )

        select.callback = callback
        self.add_item(select)


class TournamentManageView(discord.ui.View):
    def __init__(self, bot: "TreinamentoBot", tournament_id: int, owner_id: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.tournament_id = tournament_id
        self.owner_id = owner_id
        tournament = bot.database.get_tournament(tournament_id)
        if tournament and tournament.status == "registration":
            capacity_select = discord.ui.Select(
                placeholder="Alterar limite de equipes",
                options=[
                    discord.SelectOption(
                        label=f"{capacity} equipes",
                        value=str(capacity),
                        description=f"Definir limite para {capacity} equipes",
                        default=capacity == tournament.capacity,
                    )
                    for capacity in (4, 8, 16)
                ],
                custom_id=f"treinamento:tournament:manage-capacity:{tournament_id}",
            )

            async def capacity_callback(interaction: discord.Interaction):
                if interaction.user.id != self.owner_id:
                    await interaction.response.send_message(
                        "Este menu pertence ao ADM que o abriu.", ephemeral=True
                    )
                    return
                await self.bot.handle_tournament_capacity(
                    interaction,
                    self.tournament_id,
                    int(capacity_select.values[0]),
                )

            capacity_select.callback = capacity_callback
            self.add_item(capacity_select)

        if tournament and tournament.status in {"registration", "ready"}:
            self.add_item(
                TournamentSetPrizeButton(bot, tournament_id, owner_id)
            )

        if tournament and tournament.status == "registration":
            teams = bot.database.list_tournament_pairs(tournament_id)
            options = []
            guild = bot.get_guild(tournament.guild_id)
            for team in teams[:25]:
                label = (
                    bot.tournament_pair_name(guild, team)[:100]
                    if guild
                    else f"Equipe {team.slot}"
                )
                options.append(
                    discord.SelectOption(
                        label=label,
                        value=str(team.pair_id),
                        description=f"Equipe {team.slot}",
                    )
                )
        else:
            options = []
        if options:
            select = discord.ui.Select(
                placeholder="Selecione uma equipe para remover",
                options=options,
                custom_id=f"treinamento:tournament:manage-remove:{tournament_id}",
            )

            async def callback(interaction: discord.Interaction):
                if interaction.user.id != self.owner_id:
                    await interaction.response.send_message(
                        "Este menu pertence ao ADM que o abriu.", ephemeral=True
                    )
                    return
                await self.bot.handle_tournament_remove(
                    interaction, self.tournament_id, int(select.values[0])
                )

            select.callback = callback
            self.add_item(select)


class TournamentCancelConfirmView(discord.ui.View):
    def __init__(self, bot: "TreinamentoBot", tournament_id: int, owner_id: int):
        super().__init__(timeout=60); self.bot=bot; self.tournament_id=tournament_id; self.owner_id=owner_id
        yes=discord.ui.Button(label="Sim, cancelar",emoji="❌",style=discord.ButtonStyle.danger,custom_id=f"treinamento:tournament:cancel:yes:{tournament_id}")
        no=discord.ui.Button(label="Não",emoji="↩️",style=discord.ButtonStyle.secondary,custom_id=f"treinamento:tournament:cancel:no:{tournament_id}")
        yes.callback=self.confirm; no.callback=self.deny; self.add_item(yes); self.add_item(no)
    async def confirm(self, interaction: discord.Interaction):
        await self.bot.confirm_tournament_cancel(interaction,self.tournament_id,self.owner_id)
    async def deny(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Este menu pertence ao ADM que o abriu.",ephemeral=True); return
        await interaction.response.edit_message(content="✅ Cancelamento abortado.",view=None)


class TournamentAnnouncementModal(discord.ui.Modal, title="📢 Anúncio do Torneio"):
    def __init__(self, bot: "TreinamentoBot", tournament_id: int):
        super().__init__(timeout=180); self.bot=bot; self.tournament_id=tournament_id
        self.message=discord.ui.TextInput(label="Mensagem",style=discord.TextStyle.paragraph,max_length=1800,placeholder="Digite o anúncio...")
        self.add_item(self.message)
    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.send_tournament_announcement(interaction,self.tournament_id,str(self.message.value))


class TournamentPrizeModal(discord.ui.Modal, title="Definir premiação"):
    def __init__(
        self, bot: "TreinamentoBot", tournament_id: int, default_amount: str = ""
    ):
        super().__init__(timeout=180)
        self.bot = bot
        self.tournament_id = tournament_id
        self.amount = discord.ui.TextInput(
            label="Valor total em reais",
            placeholder="Ex.: 100,00",
            default=default_amount or None,
            max_length=30,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_tournament_set_prize(
            interaction, self.tournament_id, str(self.amount.value)
        )


class TournamentPixModal(discord.ui.Modal, title="Dados para receber o Pix"):
    def __init__(
        self,
        bot: "TreinamentoBot",
        tournament_id: int,
        user_id: int,
        *,
        pix_key: str = "",
        recipient_name: str = "",
        recipient_city: str = "",
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.tournament_id = tournament_id
        self.user_id = user_id
        self.pix_key = discord.ui.TextInput(
            label="Chave Pix",
            placeholder="CPF, e-mail, telefone ou chave aleatória",
            default=pix_key or None,
            max_length=77,
        )
        self.recipient_name = discord.ui.TextInput(
            label="Nome do titular no Pix",
            placeholder="Nome que aparece no banco (até 25 caracteres)",
            default=recipient_name or None,
            max_length=25,
        )
        self.recipient_city = discord.ui.TextInput(
            label="Cidade do titular",
            placeholder="Cidade (até 15 caracteres)",
            default=recipient_city or None,
            max_length=15,
        )
        self.add_item(self.pix_key)
        self.add_item(self.recipient_name)
        self.add_item(self.recipient_city)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.tournament_payments.submit_pix(
            interaction,
            self.tournament_id,
            self.user_id,
            str(self.pix_key.value),
            str(self.recipient_name.value),
            str(self.recipient_city.value),
        )


class TournamentPayoutView(discord.ui.View):
    def __init__(
        self,
        bot: "TreinamentoBot",
        tournament_id: int,
        user_id: int,
        *,
        enabled: bool = True,
        has_pix: bool = False,
        has_amount: bool = False,
        paid: bool = False,
    ):
        super().__init__(timeout=None)
        self.add_item(
            TournamentPixButton(
                bot,
                tournament_id,
                user_id,
                disabled=not enabled or paid,
            )
        )
        self.add_item(
            TournamentPixQRButton(
                bot,
                tournament_id,
                user_id,
                disabled=not enabled or not has_pix or not has_amount,
            )
        )
        self.add_item(
            TournamentMarkPaidButton(
                bot,
                tournament_id,
                user_id,
                disabled=not enabled or paid or not has_pix or not has_amount,
            )
        )


class TournamentPixButton(discord.ui.Button):
    def __init__(
        self,
        bot: "TreinamentoBot",
        tournament_id: int,
        user_id: int,
        *,
        disabled: bool = False,
    ):
        super().__init__(
            label="Informar chave Pix",
            emoji="📲",
            style=discord.ButtonStyle.primary,
            custom_id=f"galinha:pay:{tournament_id}:{user_id}:pix",
            disabled=disabled,
        )
        self.bot = bot
        self.tournament_id = tournament_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.tournament_payments.open_pix_modal(
            interaction, self.tournament_id, self.user_id
        )


class TournamentPixQRButton(discord.ui.Button):
    def __init__(
        self,
        bot: "TreinamentoBot",
        tournament_id: int,
        user_id: int,
        *,
        disabled: bool = False,
    ):
        super().__init__(
            label="Ver QR Pix",
            emoji="🔳",
            style=discord.ButtonStyle.secondary,
            custom_id=f"galinha:pay:{tournament_id}:{user_id}:qr",
            disabled=disabled,
        )
        self.bot = bot
        self.tournament_id = tournament_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.tournament_payments.handle_pix_qr(
            interaction, self.tournament_id, self.user_id
        )


class TournamentMarkPaidButton(discord.ui.Button):
    def __init__(
        self,
        bot: "TreinamentoBot",
        tournament_id: int,
        user_id: int,
        *,
        disabled: bool = False,
    ):
        super().__init__(
            label="Marcar como pago",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"galinha:pay:{tournament_id}:{user_id}:paid",
            disabled=disabled,
        )
        self.bot = bot
        self.tournament_id = tournament_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.tournament_payments.handle_payout_paid(
            interaction, self.tournament_id, self.user_id
        )


class TournamentPaymentsView(discord.ui.View):
    def __init__(
        self, bot: "TreinamentoBot", tournament_id: int, *, enabled: bool = True
    ):
        super().__init__(timeout=None)
        self.add_item(
            TournamentFinalizePaymentsButton(
                bot, tournament_id, disabled=not enabled
            )
        )


class TournamentFinalizePaymentsButton(discord.ui.Button):
    def __init__(
        self, bot: "TreinamentoBot", tournament_id: int, *, disabled: bool = False
    ):
        super().__init__(
            label="Finalizar Pagamentos",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id=f"galinha:pay:{tournament_id}:finalize",
            disabled=disabled,
        )
        self.bot = bot
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.tournament_payments.handle_finalize(
            interaction, self.tournament_id
        )


class ConfirmTournamentFinalizeView(discord.ui.View):
    def __init__(
        self,
        bot: "TreinamentoBot",
        tournament_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=60)
        self.bot = bot
        self.tournament_id = tournament_id
        self.owner_id = owner_id
        confirm = discord.ui.Button(
            label="Confirmar finalização",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id=f"galinha:pay:{tournament_id}:confirm-finalize",
        )
        cancel = discord.ui.Button(
            label="Voltar",
            style=discord.ButtonStyle.secondary,
            custom_id=f"galinha:pay:{tournament_id}:cancel-finalize",
        )
        confirm.callback = self.confirm
        cancel.callback = self.cancel
        self.add_item(confirm)
        self.add_item(cancel)

    async def confirm(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Esta confirmação pertence ao ADM que a abriu.", ephemeral=True
            )
            return
        await self.bot.tournament_payments.confirm_finalize(
            interaction, self.tournament_id, self.owner_id
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Esta confirmação pertence ao ADM que a abriu.", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            content="Finalização cancelada; o canal de pagamentos continuará aberto.",
            view=None,
        )


class ConfirmTournamentPayoutView(discord.ui.View):
    def __init__(
        self,
        bot: "TreinamentoBot",
        tournament_id: int,
        user_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=60)
        self.bot = bot
        self.tournament_id = tournament_id
        self.user_id = user_id
        self.owner_id = owner_id
        confirm = discord.ui.Button(
            label="Confirmar pagamento",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"galinha:pay:{tournament_id}:{user_id}:confirm-paid",
        )
        cancel = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            custom_id=f"galinha:pay:{tournament_id}:{user_id}:cancel-paid",
        )
        confirm.callback = self.confirm
        cancel.callback = self.cancel
        self.add_item(confirm)
        self.add_item(cancel)

    async def confirm(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Esta confirmação pertence ao ADM que a abriu.", ephemeral=True
            )
            return
        await self.bot.tournament_payments.confirm_payout_paid(
            interaction, self.tournament_id, self.user_id, self.owner_id
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Esta confirmação pertence ao ADM que a abriu.", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            content="Pagamento não marcado.", view=None
        )


class MatchAdminView(discord.ui.View):
    """Persistent button embedded in each match channel."""

    def __init__(self, bot: "TreinamentoBot", match_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.match_id = match_id
        self.add_item(MatchAdminButton(bot, match_id))


class MatchAdminButton(discord.ui.Button):
    def __init__(self, bot: "TreinamentoBot", match_id: int):
        super().__init__(
            label="Painel ADM",
            emoji="🔐",
            style=discord.ButtonStyle.secondary,
            custom_id=f"treinamento:match:{match_id}:admin",
        )
        self.bot = bot
        self.match_id = match_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_match_admin_panel(interaction, self.match_id)


class MatchControlView(discord.ui.View):
    """Ephemeral controls shown only after staff authorization."""

    def __init__(self, bot: "TreinamentoBot", match_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.match_id = match_id
        self.add_item(MatchOutcomeButton(bot, match_id, "A"))
        self.add_item(MatchOutcomeButton(bot, match_id, "B"))
        self.add_item(MatchOutcomeButton(bot, match_id, None))


class MatchOutcomeButton(discord.ui.Button):
    def __init__(
        self,
        bot: "TreinamentoBot",
        match_id: int,
        winner_team: str | None,
    ):
        if winner_team == "A":
            label, emoji, style = "Time A venceu", "🔵", discord.ButtonStyle.primary
            action = "winner-a"
        elif winner_team == "B":
            label, emoji, style = "Time B venceu", "🔴", discord.ButtonStyle.danger
            action = "winner-b"
        else:
            label, emoji, style = "Finalizar Partida", "🏁", discord.ButtonStyle.secondary
            action = "finish"
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"treinamento:match:{match_id}:{action}",
        )
        self.bot = bot
        self.match_id = match_id
        self.winner_team = winner_team

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_match_outcome(
            interaction, self.match_id, self.winner_team
        )


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import TreinamentoBot