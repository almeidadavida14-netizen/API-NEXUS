# NEXUS — Central Gamer IA e Backup de Estruturas

## Recursos preservados e atualizados

- Mantém a Central Gamer, seus formulários específicos, canais privados, histórico e perfis.
- Mantém o menu `/paineldono`, o backup/restauração de estruturas e os painéis existentes.
- A integração de conversa agora aceita provedores compatíveis com OpenAI, incluindo Groq e Gemini, por variáveis de ambiente.
- As chamadas de IA têm timeout configurável e novas tentativas com espera progressiva para falhas temporárias (408, 425, 429, 500, 502, 503, 504 e falhas de rede).
- Erros de autenticação e de cota recebem mensagens mais claras.
- O atendimento humano agora transforma a mensagem original da equipe em um painel após o administrador assumir. O painel oferece **Visualizar atendimento** e **Concluir atendimento**.
- Somente o administrador que assumiu pode visualizar pelo painel e concluir.
- Ao concluir, o histórico é arquivado, o acesso da equipe ao canal privado do jogador é retirado e o canal do jogador com o NEXUS permanece ativo. A mensagem da equipe é atualizada para indicar conclusão.
- O botão de finalizar do próprio jogador continua encerrando a sessão e arquivando o canal conforme o comportamento anterior.

## Configuração de conversa com Groq

O código aceita os nomes novos e mantém compatibilidade com os nomes antigos:

- `AI_PROVIDER=groq` (opcional, recomendado para deixar explícito)
- `GROQ_API_KEY` (recomendado) ou `OPENAI_API_KEY` (compatibilidade com a instalação antiga)
- `GROQ_BASE_URL=https://api.groq.com/openai/v1` (opcional; esse é o padrão do Groq)
- `GROQ_MODEL` (ou `AI_MODEL` / `OPENAI_MODEL`) — ID exato do modelo habilitado para sua conta.

Se você já cadastrou a chave Groq como `OPENAI_API_KEY`, a URL do Groq como `OPENAI_BASE_URL` e o ID do modelo como `OPENAI_MODEL`, a versão atualizada continua aceitando essa configuração. Não é obrigatório renomear tudo.

**Importante sobre gratuidade:** o código não consegue transformar um modelo pago em gratuito. Selecione no painel/documentação do Groq um modelo que esteja explicitamente disponível no seu plano gratuito e confira seus limites e preço atuais antes de usar. Na lista fornecida durante esta conversa, `qwen/qwen3.8-27b` e `openai/gpt-oss-120b` aparecem com preço por tokens, portanto não devem ser tratados como gratuitos. Modelos marcados como Enterprise também não devem ser presumidos gratuitos. Não há um modelo pago definido como padrão pelo código; o ID é configurado pelo usuário.

Configurações opcionais para estabilidade:
- `AI_TIMEOUT_SECONDS=90` (aceita valores entre 20 e 180)
- `AI_MAX_RETRIES=3` (aceita valores entre 0 e 4)

## Configuração de conversa com Gemini (alternativa)

- `AI_PROVIDER=gemini`
- `GEMINI_API_KEY` (ou `OPENAI_API_KEY` para compatibilidade)
- `GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai` (opcional)
- `GEMINI_MODEL` (ou `AI_MODEL` / `OPENAI_MODEL`) — ID de modelo válido para sua chave.

## Geração de imagens

A geração de imagens é separada da API de conversa. A chave Groq para chat não habilita geração de imagens.

Para um provedor compatível com o endpoint `/images/generations`, configure:
- `IMAGE_API_KEY`
- `IMAGE_BASE_URL`
- `IMAGE_MODEL`

Como compatibilidade antiga, se o `OPENAI_BASE_URL` não for Groq nem Gemini, o código ainda aceita `OPENAI_API_KEY`, `OPENAI_BASE_URL` e `OPENAI_IMAGE_MODEL`.

## Permissões e operação

O bot precisa de **Gerenciar Canais**, **Ver Canal**, **Enviar Mensagens**, **Ler Histórico de Mensagens**, **Incorporar Links** e **Anexar Arquivos**. Para concluir atendimento humano, também precisa conseguir editar a mensagem no canal de equipe e alterar permissões do canal privado do jogador.

Administradores com permissão nativa **Administrador** no Discord podem continuar vendo canais mesmo quando o bot remove as permissões explícitas da equipe; isso é uma regra do Discord que não pode ser contornada pelo bot.

## Limitações e validação

- Não existe uma base oficial universal de sensibilidades perfeitas para todos os celulares. Sugestões de Free Fire são pontos de partida ajustáveis.
- A API de conversa não implica geração gratuita de imagens.
- A implementação foi validada por compilação e testes locais disponíveis. Ela não foi testada em um servidor Discord real, nem com credenciais reais do Groq/Gemini; permissões, cota e disponibilidade dependem da hospedagem e da conta do usuário.
- Nenhuma chave de API é incluída no ZIP.
