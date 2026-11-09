# Galinha V8 — preparação para hospedagem contínua

Este pacote preserva o código-fonte e a configuração do projeto fornecido, acrescentando um Procfile e este guia. Ele não publica nem executa o bot por conta própria.

## Inicialização

O projeto usa Python e `discord.py`. O ponto de entrada é `main.py`; o token é lido da variável de ambiente `DISCORD_TOKEN`. O comando de inicialização é `python main.py`.

## Hospedagem

Escolha um serviço que suporte processos de trabalho contínuos (worker/background service) e que mantenha o processo ativo 24/7. Confira os preços e limites antes de confirmar. O `Procfile` pode ser usado por provedores compatíveis, mas não garante uptime por si só.

## Segredos

Configure `DISCORD_TOKEN` na área de variáveis de ambiente/Secrets do provedor. Não coloque o token no código nem em arquivos públicos. Se o token tiver sido exposto, regenere-o no Discord Developer Portal.

## Persistência

O bot usa SQLite em `data/treinamento_ff.sqlite3` e cria backups locais. Em uma hospedagem com sistema de arquivos efêmero, configure armazenamento persistente ou um mecanismo de backup externo antes de confiar no banco em produção. Não execute duas instâncias do bot apontando para o mesmo SQLite ao mesmo tempo.

## Intents e permissões

O código usa intents de membros e conteúdo de mensagens. Verifique se os intents privilegiados necessários estão habilitados no Discord Developer Portal e se o bot tem as permissões adequadas no servidor.

## Antes de ativar

1. Faça uma cópia de segurança do projeto original.
2. Configure `DISCORD_TOKEN` como variável de ambiente.
3. Instale as dependências com `pip install -r requirements.txt` (ou o comando recomendado pelo provedor).
4. Execute `python main.py` e confira os logs.
5. Só depois migre para o serviço contínuo.

O projeto original tem dependências e componentes adicionais; este pacote não remove funcionalidades de propósito. A implantação precisa ser testada no provedor escolhido.


## IA do NEXUS (Groq/Gemini)

A integração de conversa aceita provedores compatíveis com OpenAI. Para Groq, configure:
- `AI_PROVIDER=groq` (opcional, mas recomendado)
- `GROQ_API_KEY` (ou mantenha `OPENAI_API_KEY` para compatibilidade com configurações antigas)
- `GROQ_BASE_URL=https://api.groq.com/openai/v1` (opcional)
- `GROQ_MODEL` com o ID exato de um modelo habilitado na sua conta (ou `OPENAI_MODEL`).

Para Gemini, use `AI_PROVIDER=gemini`, `GEMINI_API_KEY`, `GEMINI_BASE_URL` e `GEMINI_MODEL`.

A estabilidade pode ser ajustada com `AI_TIMEOUT_SECONDS` (20 a 180 segundos; padrão 90) e `AI_MAX_RETRIES` (0 a 4; padrão 3). Erros temporários e limites de taxa recebem novas tentativas automáticas.

**Confira o preço e a cota do modelo antes de utilizá-lo.** A compatibilidade da API não significa que o modelo seja gratuito. Não coloque chaves no código, em arquivos do projeto ou em mensagens do Discord.

A geração de imagens usa configuração separada: `IMAGE_API_KEY`, `IMAGE_BASE_URL` e `IMAGE_MODEL`. A chave de chat do Groq não habilita automaticamente geração de imagens.
