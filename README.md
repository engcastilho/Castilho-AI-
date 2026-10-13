# Castilho AI

Chatbot de IA no estilo ChatGPT/Claude: interface web de chat com respostas em tempo real
(streaming), histórico de conversas salvo e suporte a Markdown (código, tabelas, listas).
Usa o modelo **Claude** pela API oficial da Anthropic.

## Como funciona

```
Navegador (HTML/CSS/JS)  ──►  Servidor FastAPI (Python)  ──►  API do Claude (Anthropic)
        ▲                            │
        └──── texto em streaming ◄───┤
                                     ▼
                              SQLite (data/chat.db)
```

- **`app/main.py`**: servidor web e rotas da API (`/api/conversations`...).
- **`app/llm.py`**: chamada ao Claude com streaming, usando o SDK oficial `anthropic`.
- **`app/storage.py`**: salva conversas e mensagens em SQLite.
- **`app/config.py`**: configurações (modelo, prompt de sistema etc.).
- **`app/static/`**: a interface de chat (HTML, CSS e JavaScript puro).

## Como rodar

Pré-requisitos: Python 3.10+ e uma chave de API da Anthropic
(crie em <https://console.anthropic.com/settings/keys>).

```bash
# 1. Criar o ambiente e instalar as dependências
python -m venv .venv
source .venv/bin/activate        # no Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configurar a chave
cp .env.example .env              # depois edite o .env e cole sua ANTHROPIC_API_KEY

# 3. Iniciar o servidor
uvicorn app.main:app --reload
```

Abra <http://localhost:8000> no navegador.

**No Windows, depois da primeira instalação**, basta dar dois cliques no arquivo
`iniciar.bat`: ele liga o servidor e abre o navegador sozinho. Para desligar, feche a
janela preta.

## Personalização

Tudo é configurável pelo arquivo `.env`:

| Variável             | Padrão                | Para que serve                                                    |
|----------------------|-----------------------|-------------------------------------------------------------------|
| `CHAT_MODEL`         | `claude-opus-5-5`     | Modelo usado. `claude-sonnet-5-5` ou `claude-haiku-5-5` são mais baratos. |
| `CHAT_EFFORT`        | `medium`              | Quanto o modelo "pensa": `low` (rápido/barato) até `max`.         |
| `CHAT_MAX_TOKENS`    | `64000`               | Tamanho máximo de cada resposta.                                  |
| `CHAT_SYSTEM_PROMPT` | (ver `app/config.py`) | Personalidade e regras do assistente.                             |
| `CHAT_DB_PATH`       | `data/chat.db`        | Onde as conversas ficam salvas.                                   |

O **prompt de sistema** é o jeito mais fácil de transformar o chatbot em algo específico
(atendimento ao cliente, tutor, assistente jurídico...): descreva ali quem ele é, o que deve
fazer e o que não deve.

> O prompt de sistema fica gravado em cada conversa no momento em que ela é criada.
> Mudanças no `.env` valem para as **novas** conversas.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes usam um modelo falso, então não gastam créditos da API.

## Próximos passos possíveis

- Login de usuários (cada pessoa vê só as próprias conversas).
- Enviar imagens e PDFs para o modelo analisar.
- Ferramentas (busca na web, consulta a banco de dados, etc.).
- Base de conhecimento com seus próprios documentos.
- Publicar na internet (Render, Railway, Fly.io, uma VPS...).
