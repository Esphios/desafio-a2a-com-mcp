# A Ponte — agente A2A com MCP por dentro

Implementação determinística do desafio de reserva de salas. O servidor MCP e o agente A2A são processos HTTP separados; regras de sala ficam exclusivamente no MCP, e o agente traduz o ciclo multi-round para estados de uma Task A2A.

## Como rodar

Requisitos: Python 3.10 ou superior e [uv](https://docs.astral.sh/uv/). A partir de um clone limpo:

```bash
uv sync --frozen
python -c "import secrets; print(secrets.token_hex(32))"
```

Copie o valor gerado para `REQUEST_STATE_SECRET`. No PowerShell, abra dois terminais na raiz do projeto.

Terminal 1 — servidor MCP:

```powershell
$env:REQUEST_STATE_SECRET='<segredo-gerado>'
uv run python servidor-mcp/main.py
```

Terminal 2 — agente A2A:

```powershell
$env:MCP_URL='http://127.0.0.1:7301/mcp'
uv run python agente/main.py
```

Em um terceiro terminal:

```powershell
uv run python validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
uv run pytest -q
```

Os valores padrão são porta `7301`/`POST /mcp` para o MCP e porta `7300`, `POST /a2a` e `GET /.well-known/agent-card.json` para A2A. `MCP_PORT` e `A2A_PORT` permitem mudar apenas as portas.

## Onde a ponte acontece

O cliente em `agente/mcp_client.py` faz `tools/list` e lê `politica://uso` antes da primeira chamada. Em `agente/bridge.py`, `Bridge._pause` converte o `input_required` MCP em `TASK_STATE_INPUT_REQUIRED`, publica somente a lista ordenada de alternativas e guarda o token opaco em `PausedCall`. Na continuação, `Bridge._continue` valida a escolha e chama novamente `reservar_sala` com um id JSON-RPC novo, a mesma chave de `inputResponses` e o `requestState`. O token nunca entra na Task nem em respostas A2A.

## Decisões técnicas

- Python 3.10+, MCP Python SDK v2 e A2A Python SDK v1, com versões diretas fixadas em `pyproject.toml` e resolução completa em `uv.lock`.
- O MCP usa Streamable HTTP stateless. `RequestStateSecurity` do SDK protege o estado com a chave externa `REQUEST_STATE_SECRET`, audiência estável e TTL de 600 segundos. O estado selado contém o pedido completo, por isso um retry continua válido após reiniciar o MCP com o mesmo segredo. Reiniciar com outro segredo invalida tokens pendentes.
- Reservas, Tasks e `PausedCall` ficam em memória. Reservas e Tasks não sobrevivem a reinício; isso é intencional. O `requestState` é a única continuidade que atravessa um reinício do MCP.
- O cliente MCP é HTTP fino para preservar `input_required` cru, `_meta`, headers espelhados, trace context e ids independentes. Não há LLM, banco, ORM, container ou Google ADK.
- `dados/`, `validador/` e `exemplos/` permanecem inalterados em relação ao commit-base `263f17e`.

## Saída do validador

Última execução com os dois processos recém-iniciados; código de saída `0`:

```text
trace-id desta execucao: 691ae811f6a51dd5903c330d293b5131
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
