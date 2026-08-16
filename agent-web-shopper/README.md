# Shopping Agent

Agente de IA que pesquisa produtos em múltiplos sites e recomenda a melhor opção
segundo critérios informados pelo usuário via chat web.

---

## Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│                     Canal de entrada                     │
│                Interface Web (FastAPI)                   │
└───────────────────┬─────────────────────────────────────┘
                    │
             ┌──────▼──────┐
             │   agent.py  │  Loop agentico (tool use)
             └──────┬──────┘
          ┌─────────┴────────┐
   ┌──────▼──────┐   ┌───────▼──────────┐
   │ search_web  │   │ fetch_and_extract │
   │ DuckDuckGo  │   │ Playwright + LLM  │
   └─────────────┘   └──────────────────┘
                            │
                   ┌────────▼────────┐
                   │ selector_agent  │  Sub-agente CSS/XPath
                   │ (Claude)        │  por site novo
                   └─────────────────┘
                            │
              ┌─────────────▼──────────┐
              │       tracing.py       │  RunContext + spans
              │   db.py (SQLite WAL)   │  rastreabilidade completa
              └────────────────────────┘
```

### Por que essas escolhas?

| Componente | Escolha | Razão |
|---|---|---|
| LLM (loop principal) | Claude claude-sonnet-4-6 (Anthropic) | Excelente raciocínio multi-etapa; suporte nativo a tool use |
| LLM (selector-agent) | Claude claude-haiku-4-5 (Anthropic) | Tarefa mais estruturada (HTML → JSON de seletores); modelo menor e mais barato, com gate de confiança em `extract.py` como rede de segurança |
| Framework | Loop agentico próprio (sem LangGraph) | Código mais simples e transparente para o escopo do desafio |
| Browser | Playwright | Renderiza JS, simula usuário real, configurável headless/headed |
| Busca web | DuckDuckGo Search | Gratuito, sem API key, resultados reais |
| Extração | Selector Agent (sub-agente LLM) | Seletores gerados dinamicamente = escalável para qualquer site novo |
| Persistência | SQLite + WAL | Zero infra, leitura concorrente, fácil de migrar para Postgres |
| Web | FastAPI + uvicorn | Async nativo, tipagem Pydantic, docs automáticas em /docs |
| Observabilidade | Spans hierárquicos no DB | Cada etapa (LLM call, tool call) rastreada com tokens e custo |

> 💡 **Provider plugável**: o LLM não fica preso à Anthropic — setando
> `LLM_PROVIDER=gemini` no `.env` o agente passa a usar a API do Gemini
> (gratuita, sem gastar créditos do Claude), sem mudar nenhuma linha de
> código. Claude é o provider documentado e usado na avaliação do desafio;
> Gemini é uma opção para testar/desenvolver localmente de graça. Detalhes
> em [Testar de graça com Gemini](#testar-de-graça-com-gemini-opcional).

---

## Pré-requisitos

- Python 3.11+
- Conta Anthropic com API key

---

## Setup rápido

### 1. Clone e entre no diretório

```bash
git clone <url-do-repositorio>
cd agent-web-shopper
```

### 2. Crie e ative o ambiente virtual

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Instale as dependências

```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Configure as variáveis de ambiente

```bash
cp .env.example .env
# Edite .env e preencha ANTHROPIC_API_KEY
```

Todas as outras variáveis já têm um valor padrão razoável em `.env.example` —
veja a tabela completa na seção [Variáveis de ambiente](#variáveis-de-ambiente)
abaixo antes de mexer nelas.

### 5. Inicie o servidor

```bash
python main.py
```

Acesse **http://localhost:8000** para o chat e **http://localhost:8000/dashboard**
para o painel de observabilidade.

---

## Variáveis de ambiente

Referência completa de tudo que está em `.env.example` hoje (todas opcionais
exceto a chave do provider ativo — os defaults abaixo já cobrem um setup local
razoável):

| Variável | Padrão | Descrição |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` (documentado/avaliado no desafio) ou `gemini` (teste local grátis — ver seção abaixo) |
| `ANTHROPIC_API_KEY` | — | Obrigatória se `LLM_PROVIDER=anthropic` |
| `GEMINI_API_KEY` | — | Obrigatória se `LLM_PROVIDER=gemini` |
| `LLM_MODEL` | `claude-sonnet-4-6` | Modelo do loop principal (raciocínio, decide as tools) |
| `LLM_MODEL_SELECTOR` | `claude-haiku-4-5` | Modelo do selector-agent — tarefa mais estruturada, por isso um modelo mais barato por padrão |
| `LLM_MAX_TOKENS` | `2000` | Teto de tokens de saída por chamada do loop principal |
| `HISTORY_MAX_MESSAGES` | `20` | Mensagens mantidas em memória entre turnos da mesma conversa antes de truncar (ver [Otimizações de custo](#otimizações-de-custo)) |
| `DB_PATH` | `data/traces.sqlite3` | Caminho do SQLite de observabilidade/tracing |
| `LOG_LEVEL` | `INFO` | Nível de log padrão do Python (`logging`) |
| `HEADLESS` | `true` | Playwright roda o Chromium sem interface gráfica |
| `NAV_TIMEOUT_MS` | `20000` | Timeout de navegação (ms) do Playwright em `fetch_and_extract` |
| `MAX_SITES_PER_SEARCH` | `5` | Quantos resultados `search_web` retorna por busca |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Endereço/porta do servidor FastAPI |
| `PROMPT_FILE` | — | Caminho pro JSON de override de prompts, com hot-reload (ver [Alterar o prompt sem redeploy](#alterar-o-prompt-sem-redeploy)) |

---

## Testar de graça com Gemini (opcional)

O agente foi desenhado e avaliado em cima do Claude (ver justificativa na seção
de arquitetura acima) — esse é o provider padrão. Para testar localmente sem
gastar créditos do Claude, dá pra trocar o provider por variável de ambiente,
sem mudar nenhuma linha de código do agente:

```bash
# no .env
LLM_PROVIDER=gemini
GEMINI_API_KEY=...        # grátis em https://aistudio.google.com/apikey
LLM_MODEL=gemini-3.5-flash
LLM_MODEL_SELECTOR=gemini-3.5-flash-lite
```

`app/llm.py` traduz o formato de mensagens/tools para a API do Gemini
(`app/llm_gemini.py`) e devolve a resposta no mesmo formato que `agent.py` e
`selector_agent.py` já esperam — por isso nenhum outro arquivo precisa mudar.
Para voltar ao Claude, é só remover `LLM_PROVIDER` (ou setar `anthropic`).

---

## Interface Web

Abra `http://localhost:8000` no navegador. Digite sua busca e aguarde —
o agente visitará os sites e retornará a recomendação.

---

## Observabilidade

Tudo que o agente faz fica registrado no SQLite em `data/traces.sqlite3` —
cada chamada de LLM e cada tool call vira um **span** com entrada e saída
completas (incluindo o texto que o modelo produziu e as tools que decidiu
chamar em cada iteração, não só metadados como `stop_reason`), ligado ao
span pai via `parent_span_id` — reconstrói a árvore inteira de qualquer
execução, mesmo depois de reiniciar o servidor.

### Dashboard visual

`http://localhost:8000/dashboard` — métricas agregadas + lista de execuções
com árvore de spans clicável (cada span expande pra mostrar entrada/saída).

### API de observabilidade

| Endpoint | Descrição |
|---|---|
| `GET /api/metrics` | Métricas agregadas (sucesso, tokens médios, custo total) |
| `GET /api/runs` | Lista de execuções recentes |
| `GET /api/runs/{id}` | Detalhes de uma execução |
| `GET /api/runs/{id}/spans` | Árvore de spans (etapas) de uma execução |

### Custo por execução

Cada resposta do chat retorna:
```json
{
  "run_id": "...",
  "tokens_in": 1234,
  "tokens_out": 456,
  "cost_usd": 0.0102
}
```

### Otimizações de custo

- **Prompt caching (Anthropic)**: o system prompt + schema das ferramentas
  fica marcado com `cache_control`, e o histórico crescente da conversa
  ganha um breakpoint rolante a cada iteração do loop — reduz bastante o
  custo de runs com várias idas e voltas de tool use. Acompanhe
  `cache_creation_input_tokens`/`cache_read_input_tokens` no `usage` da
  Anthropic para ver o efeito.
- **Cache de seletores por domínio**: `fetch_and_extract` reaproveita
  seletores já gerados para um domínio/conjunto de campos já visto, evitando
  reenviar HTML pro selector-agent a cada visita — com auto-cura caso o site
  mude o HTML (seletor cacheado que não extrai nada é descartado e
  regenerado).
- **Modelo mais barato no selector-agent**: `LLM_MODEL_SELECTOR` usa
  `claude-haiku-4-5` por padrão (tarefa mais estruturada que o raciocínio
  principal), mantendo `LLM_MODEL` (Sonnet) só para o loop principal.
- **Histórico limitado entre turnos**: `HISTORY_MAX_MESSAGES` (padrão 20)
  evita que a memória de conversa cresça sem limite.

---

## Confiabilidade

- **Retry com backoff** nas chamadas à Anthropic (`app/llm.py`) — só em
  erros que fazem sentido tentar de novo (rate limit, timeout, falha de
  conexão, erro 5xx do servidor). Erro de request inválido ou autenticação
  falha na primeira tentativa, sem desperdiçar retries.
- **Falha de ferramenta não derruba a run**: se `search_web` ou
  `fetch_and_extract` falham, o erro vira `{"error": ...}` devolvido ao
  modelo (que decide tentar outra fonte ou avisar o usuário) em vez de
  quebrar a execução inteira — e o `tool_result` correspondente carrega
  `is_error: true`, pra o modelo não confundir uma mensagem de erro com um
  dado válido.
- **Erros internos não vazam detalhe pro cliente**: uma exceção não tratada
  em `/api/chat` é logada por completo no servidor (`exc_info=True`), mas o
  cliente recebe uma mensagem genérica em vez do stack trace/detalhe interno.

---

## Alterar o prompt sem redeploy

Crie um arquivo JSON com as chaves que deseja sobrescrever:

```json
{
  "MAIN_AGENT_SYSTEM_PROMPT": "Você é um agente especializado em eletrônicos..."
}
```

Defina `PROMPT_FILE=/caminho/para/prompts.json` no `.env` e reinicie o servidor
uma vez para o processo passar a apontar pro arquivo. A partir daí, **editar o
conteúdo do arquivo vale na próxima mensagem enviada ao agente — sem precisar
reiniciar de novo**: `app/prompts.py` confere o mtime do arquivo a cada
chamada e recarrega automaticamente quando ele muda.

---

## Segurança (prompt injection)

O agente recusa tentativas de:
- Revelar o system prompt
- Ignorar instruções ("DAN mode", "ignore as instruções anteriores")
- Executar código enviado pelo usuário
- Atender tarefas fora do escopo de pesquisa de produtos

Além disso, tanto o prompt principal quanto o do selector-agent instruem
explicitamente o modelo a tratar todo conteúdo trazido por ferramentas
(resultado de busca, HTML extraído de páginas de terceiros) como **dado a
ser analisado, nunca como instrução** — isso cobre o cenário de injection
indireto (uma página de produto com texto malicioso embutido), não só
tentativas vindas diretamente do usuário no chat. O HTML enviado ao
selector-agent é delimitado explicitamente como conteúdo não confiável.

---

## Avaliação

Execute os casos de teste:

```bash
python -m eval.run_eval
```

Isso roda os 5 casos de `eval/cases.jsonl`, imprime um resumo no terminal
e gera `eval/report.md` com métricas detalhadas (taxa de sucesso, tokens médios,
custo total, tempo médio por execução).

---

## Escalabilidade

Para múltiplos usuários simultâneos:

1. **FastAPI + uvicorn com workers**: `uvicorn app.web.app:app --workers 4`
   — cada request roda em thread separada (`asyncio.to_thread`), o event loop
   não fica bloqueado pelo Playwright ou pela API do Claude. Como a maior
   parte do trabalho do agente é espera de rede (LLM, navegação, busca), o
   GIL do Python não é um gargalo sério aqui — I/O libera o GIL, então
   múltiplas execuções em threads diferentes progridem de verdade em
   paralelo dentro de um processo.

   > ⚠️ **Ressalva real, não só teórica**: `_conversations` (memória de
   > conversa entre turnos, em `app/agent.py`) é um dict **em memória, de
   > um processo só**. Com `--workers 4`, cada worker é um processo Python
   > separado com sua própria cópia vazia desse dict — se a segunda
   > mensagem de uma conversa cair num worker diferente da primeira (o
   > load balancer do uvicorn distribui por round-robin), o agente perde o
   > histórico **silenciosamente**, sem erro, como se fosse a primeira
   > mensagem. É a única lacuna de escala que causa comportamento
   > *incorreto* (as outras abaixo só ficam mais lentas/caras sob carga).
   > Correção: mover `_conversations` pra um armazenamento compartilhado
   > entre processos (Redis, ou uma tabela no próprio SQLite/Postgres)
   > antes de rodar com mais de um worker — não muda a lógica do agente,
   > só onde o histórico é lido/escrito.

2. **Banco de dados**: trocar SQLite por Postgres é um `DB_PATH` + uma linha
   de código em `db.py` (a interface é a mesma). O WAL do SQLite já suporta
   leituras concorrentes com uma escrita — não é o primeiro gargalo a
   aparecer nesse volume de tracing.

3. **Filas**: para picos de carga, adicionar Celery/Redis entre a API e o
   `run_agent` desacopla recebimento de processamento — a API responde
   imediatamente com um `run_id` e o cliente faz polling. Isso também
   suaviza picos contra o rate limit do provider de LLM, que na prática é
   o teto de throughput mais apertado do sistema — mais apertado que
   qualquer limite do próprio código.

4. **Reuso de browser**: hoje `fetch_and_extract` sobe um Chromium novo do
   zero a cada chamada (`app/tools/browser.py`) — sob carga concorrente,
   provavelmente é o primeiro recurso (CPU/RAM) a esgotar, antes do SQLite
   ou do rate limit do LLM. Um pool de browsers/contextos reutilizáveis
   resolveria; ficou fora do escopo desta rodada por exigir gerenciar
   ciclo de vida compartilhado numa base hoje 100% síncrona.

---

## Estrutura do projeto

```
agent-web-shopper/
├── app/
│   ├── config.py          # Configurações via env vars
│   ├── prompts.py         # System prompts (carregáveis externamente)
│   ├── tracing.py         # RunContext + spans (observabilidade)
│   ├── db.py              # SQLite: persistência de runs e spans
│   ├── llm.py             # Wrapper Claude API + contagem de tokens
│   ├── schemas.py         # Modelos Pydantic (request/response)
│   ├── agent.py           # Loop agentico principal
│   ├── tools/
│   │   ├── browser.py     # Playwright: navega e retorna HTML
│   │   ├── search.py      # DuckDuckGo: descobre sites candidatos
│   │   ├── selector_agent.py  # Sub-agente: gera seletores CSS/XPath
│   │   └── extract.py     # Aplica seletores e extrai dados estruturados
│   └── web/
│       ├── app.py         # FastAPI: rotas + startup
│       └── static/
│           ├── index.html     # Interface de chat
│           └── dashboard.html # Painel de observabilidade
├── eval/
│   ├── cases.jsonl        # Casos de teste
│   ├── run_eval.py        # Runner de avaliação
│   └── report.md          # Relatório gerado automaticamente
├── data/                  # SQLite (criado automaticamente)
├── main.py                # Ponto de entrada
├── .env.example           # Exemplo de configuração
└── requirements.txt
```
