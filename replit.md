# Treinamento FF

Bot de Discord para organizar filas de Free Fire, sortear times e criar partidas automaticamente.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `python main.py` — run the Discord bot
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required secret: `DISCORD_TOKEN` — token do bot, mantido no Secrets do Replit

### Iniciar o bot no Replit

1. Configure `DISCORD_TOKEN` em Secrets com o token da aplicação do bot no Discord. Não use o token de uma conta pessoal.
2. No Discord Developer Portal, na seção **Bot**, habilite **Server Members Intent** e **Message Content Intent**.
3. Adicione o bot ao servidor com os escopos `bot` e `applications.commands` e as permissões descritas abaixo.
4. Execute o workflow **Treinamento FF Bot** (`python main.py`), também associado ao botão Run.
5. Confira no console a confirmação de conexão ao Discord. Sem o token, o processo encerra com uma mensagem de erro.

As dependências Python estão em `requirements.txt`: discord.py, PyNaCl e qrcode com Pillow. O bot não possui página web; o uso ocorre no Discord e os logs aparecem no console. A API e o canvas não são necessários para iniciar o bot.

Teste local, sem acessar o Discord e usando bancos temporários: `python -m unittest discover -s tests -v`.

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- Python 3.11, discord.py 2.x
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `main.py` — entrada do bot, comandos slash e criação de partidas
- `config.py` — nomes da estrutura e configurações do sistema
- `database.py` — persistência SQLite
- `queue_manager.py` — regras das filas e sorteio dos times
- `server_setup.py` — criação idempotente de categorias, canais e painéis
- `views.py` — botões interativos do Discord

## Architecture decisions

- O token é lido exclusivamente de `DISCORD_TOKEN`; nunca é salvo em arquivo.
- SQLite local mantém filas, configurações e partidas após reinicializações.
- A estrutura do servidor é criada apenas quando categorias/canais equivalentes não existem.
- O sorteio usa `SystemRandom` e o código remove os jogadores da fila antes de criar a partida.

## Product

O bot cria a estrutura inicial do servidor, incluindo a categoria de comunidade, publica painéis para 1v1/2v2/3v3/4v4, mantém filas independentes de AP e Salinha, sorteia times, cria canais privados de partida e canais de voz por time.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- No portal do Discord, habilite **Server Members Intent** para remover usuários que saíram do servidor das filas e **Message Content Intent** para os comandos de texto.
- O bot precisa de View Channels, Send Messages, Embed Links, Read Message History, Use Application Commands e Manage Channels.
- Para mover jogadores automaticamente, o bot também precisa de Move Members, Connect e Speak.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
