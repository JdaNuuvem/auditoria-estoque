# Histórico Permanente de Bipagem — Design

## Contexto

Pedido do usuário: "precisamos guardar em nosso banco de dados todos os
produtos que forem sendo bipados e registrados". Esclarecido: não é dúvida
sobre persistência (que já existe, agregada por sessão em
`audit_sessions.json`) — é pedido de um **log durável de cada bipagem/
contagem individual** (quem, qual produto, que horário, em qual loja), que
sobrevive à reutilização de sessão (mesma loja+dia reaproveita a sessão
existente) e à virada do dia (sessão antiga nunca é apagada, mas hoje não há
como filtrar "o que o fulano bipou ontem" sem varrer sessões inteiras).

Contexto técnico existente (`server.py`):

- `POST /api/audit/session/start` reaproveita a sessão do dia por
  `filialId`, ou cria uma nova — sessão tem `userEmail`/`userName` de quem a
  abriu, mas quem bipa depois pode ser qualquer bipador daquela loja no
  mesmo dia (sessão compartilhada).
- `POST /api/audit/scan` marca `session["encontrados"][productId]` na
  primeira vez que um produto é bipado (idempotente — `dup: true` se já
  existia, sem sobrescrever `scannedAt`).
- `POST /api/audit/count` ajusta `qtd` de duas formas: `delta` (+1/-1, dos
  botões de incremento/decremento após o primeiro bipe) ou `qtd` (valor
  absoluto, quando o operador digita um número exato no campo em vez de usar
  o leitor — correção manual). Ambas atualizam o mesmo
  `session["encontrados"][productId]`.
- Isso tudo é um **snapshot agregado** (estado atual por produto por
  sessão) — nunca foi um log de eventos, e não tem quem fez cada ajuste
  quando a sessão é compartilhada por múltiplos bipadores.

## Decisões já tomadas com o usuário

- Quer uma **tela nova** para visualizar o histórico (não é só dado
  guardado sem UI).
- **Toda ação conta como evento**: bipagem física (scan) e correção manual
  de quantidade (`qtd` absoluto) — não só o primeiro bipe.
- Filtros da tela: **loja + bipador + intervalo de data**.
- **Sem retenção/limpeza** — guarda para sempre.
- Formato de armazenamento: **arquivo append-only, uma linha JSON por
  evento** (mesmo diretório `DATA_DIR` dos outros arquivos locais).

## Modelo de dados

Novo arquivo `scan_log.jsonl` em `DATA_DIR`. Cada linha é um objeto JSON
independente (não é um array — nunca precisa reescrever o arquivo inteiro
para adicionar um evento, só um `open(..., "a")` + `write` protegido por um
lock de thread dedicado, `_scan_log_lock`, seguindo o mesmo padrão de
`_audit_lock`):

```json
{"ts": "2026-08-03T14:32:10", "sessionId": "audit_12_2026-08-03_...", "filialId": 12, "filialNome": "CHARME COSMETICOS", "userEmail": "fulano@x.com", "userName": "Fulano", "productId": 456, "ean": "789...", "descricao": "Batom Vermelho", "tipo": "scan", "novo": true, "qtd": null}
{"ts": "2026-08-03T14:33:02", "sessionId": "audit_12_2026-08-03_...", "filialId": 12, "filialNome": "CHARME COSMETICOS", "userEmail": "fulano@x.com", "userName": "Fulano", "productId": 456, "ean": "789...", "descricao": "Batom Vermelho", "tipo": "contagem_incremento", "novo": false, "qtd": 1}
{"ts": "2026-08-03T15:01:44", "sessionId": "audit_12_2026-08-03_...", "filialId": 12, "filialNome": "CHARME COSMETICOS", "userEmail": "ciclana@x.com", "userName": "Ciclana", "productId": 456, "ean": "789...", "descricao": "Batom Vermelho", "tipo": "contagem_manual", "novo": false, "qtd": 5}
```

Campos:

- `ts`: timestamp ISO local (`datetime.now().isoformat()`), mesmo padrão de
  precisão usado hoje em `scannedAt` no snapshot de sessão.
- `sessionId`, `filialId`, `filialNome`: copiados da sessão no momento do
  evento (a sessão pode, em teoria, ser reaberta em outro dia com outro
  `filialNome` cadastral — o log grava o valor daquele instante, não uma
  referência que muda depois).
- `userEmail`/`userName`: quem disparou **esta chamada específica**, não
  quem abriu a sessão — hoje `audit_scan`/`audit_count` não recebem
  identidade de usuário (só `sessionId`); isso muda (ver Endpoints abaixo).
- `productId`, `ean`, `descricao`: como já trafega hoje nesses dois
  endpoints.
- `tipo`: `"scan"` (chamada a `/api/audit/scan`), `"contagem_incremento"`
  (chamada a `/api/audit/count` com `delta`), `"contagem_manual"` (chamada
  a `/api/audit/count` com `qtd` absoluto — a correção manual pedida
  explicitamente pelo usuário).
- `novo`: só relevante para `tipo: "scan"` — `true` se foi a primeira vez
  que aquele produto foi confirmado na sessão, `false` se foi um re-scan de
  algo já encontrado (o endpoint já sabe disso via `is_dup`). Continua
  logando o evento mesmo quando `novo: false`, porque "alguém re-bipou algo
  já confirmado, nesse horário" é informação de auditoria válida.
- `qtd`: valor resultante após o evento (mesmo `entry["qtd"]` que o
  endpoint já calcula), `null` para eventos `tipo: "scan"`.

## Escrita do log (não muda o comportamento existente)

`audit_scan` e `audit_count` continuam fazendo exatamente o que já fazem
com `audit_sessions.json` — o log é **aditivo e paralelo**, nunca substitui
o snapshot. Cada uma das duas rotas, após atualizar a sessão (dentro do
mesmo bloco `with _audit_lock`, já que ambos usam dado da sessão), chama
uma função nova `_append_scan_log(evento_dict)` que abre `scan_log.jsonl`
em modo `"a"` e escreve uma linha.

**Falha ao escrever o log nunca derruba a operação principal**: bipar ou
contar continua funcionando normalmente mesmo que o disco do log falhe por
algum motivo — `_append_scan_log` captura qualquer exceção de I/O
internamente e loga no servidor (`print`/logging, mesmo padrão simples já
usado no projeto), sem propagar. O log é auditoria, não a fonte de verdade
operacional (essa continua sendo `audit_sessions.json`).

### Identidade do bipador nas chamadas

Hoje `POST /api/audit/scan` e `POST /api/audit/count` só recebem
`sessionId` — não sabem quem, especificamente, disparou aquela chamada
(a sessão é compartilhada). Os dois passam a aceitar um campo opcional
`userEmail` no corpo da requisição; o frontend já tem essa informação em
`S.currentUser`/localStorage (mesma fonte usada para abrir a sessão) e
passa a enviá-la em toda chamada de scan/count. Se `userEmail` vier vazio
(cliente antigo, ou falha ao carregar o usuário local), o evento é gravado
com `userEmail: null`/`userName: null` — nunca bloqueia a operação por
causa disso (log é best-effort, como já estabelecido acima).

## Endpoint de consulta

Diferente de `GET /api/dedup/groups` (público, sem dado sensível — só
mostra que dois cadastros são o mesmo produto), o histórico de bipagem
expõe **quem fez o quê e quando**, por bipador — informação sensível o
suficiente para exigir autenticação de admin, como todas as outras rotas
`/api/admin/*`. Como enviar uma senha via query string de um `GET` a
exporia em logs de acesso/histórico do navegador, o endpoint é `POST`
(mesmo padrão de todas as rotas admin existentes: `adminPassword` no corpo
JSON, `_admin_password_ok` + rate limit compartilhado):

`POST /api/admin/scan-log` `{adminPassword, filialId?, bipadorEmail?,
from?, to?}` → lê `scan_log.jsonl` linha a linha, filtra pelos campos
informados (todos opcionais — sem nenhum filtro, devolve tudo), ordena por
`ts` decrescente, devolve `{"ok": true, "events": [...]}`. `from`/`to` são
datas `YYYY-MM-DD` (comparadas contra a parte de data de `ts`), inclusivas.

## Tela nova (só admin)

Reaproveita o padrão já existente da view de Deduplicação: nav item
exclusivo de admin, seletor de filial (`popAdminFilialSelect`), mais dois
campos novos — seletor de bipador (lista de `users.json` daquela loja, ou
todos se "todas as lojas" não fizer sentido aqui — filial é sempre
obrigatória para manter a consulta pequena, como no dedup) e intervalo de
data (dois `<input type="date">`). Botão "Buscar" chama o endpoint e
renderiza uma tabela simples: data/hora, bipador, produto (descrição +
EAN), tipo de evento (rótulo amigável: "Bipagem", "Contagem (+/-)",
"Correção manual"), quantidade resultante.

Sem paginação nesta primeira versão (YAGNI — volume por loja por período é
pequeno; se crescer, é um ajuste isolado no endpoint depois).

## Testes

Suite pytest, mesmo padrão de `tests/test_local_persistence.py`:

- `audit_scan` grava um evento `tipo: "scan"` em `scan_log.jsonl` na
  primeira bipagem (`novo: true`).
- Re-bipar o mesmo produto na mesma sessão grava um segundo evento
  (`novo: false`), sem duplicar/sobrescrever o snapshot da sessão.
- `audit_count` com `delta` grava `tipo: "contagem_incremento"`.
- `audit_count` com `qtd` grava `tipo: "contagem_manual"`.
- Falha simulada de escrita do log (ex.: monkeypatch em `open` levantando
  exceção) não impede a resposta `200 OK` do `scan`/`count` em si.
- `POST /api/admin/scan-log` exige `adminPassword` correta (403 caso
  contrário), seguindo o padrão já testado em `admin_create_bipador`.
- Filtro por `filialId` isola eventos de outras lojas.
- Filtro por `bipadorEmail` isola eventos de outros bipadores.
- Filtro por `from`/`to` exclui eventos fora do intervalo.
- Sem filtro nenhum (além de `adminPassword`) devolve todos os eventos,
  ordenados por `ts` decrescente.

## Fora de escopo (YAGNI)

- Paginação da tela/endpoint — volume atual não justifica.
- Exportação CSV do histórico — não pedido; se vier depois, é o mesmo
  padrão já usado no relatório de Deduplicação.
- Editar/apagar um evento do log — log de auditoria não deveria ser
  editável; se um bipe for errado, a correção em si já vira um novo evento
  (`contagem_manual`) que mostra a correção lado a lado com o erro original.
- Arquivamento/rotação do arquivo — decisão explícita do usuário de guardar
  para sempre; revisitar só se o arquivo crescer a ponto de virar problema
  real de desempenho na leitura (não esperado tão cedo, 8 lojas).
