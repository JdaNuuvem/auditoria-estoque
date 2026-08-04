# Dashboard com dados reais + relatório por loja Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dashboard mostra números reais agregados (não dependentes de sessão de
usuário nem de loja selecionada), e o relatório "Encontrados" por loja exporta um
CSV completo o bastante para servir de conferência de auditoria.

**Architecture:** Mudança 100% em `templates/index.html` (frontend vanilla JS já
existente, sem framework, sem build step). Sem endpoint novo no backend — os dados
já chegam via `GET /api/audit/sessions` (`S.allSessions`) e `S.stockMap`, ambos já
carregados no client. Reaproveita a arquitetura já presente no arquivo (seletor de
loja único no cabeçalho, `currentViewSession()`, `getLatestSessionForFilial()`,
`viewFilialReport()` levando para a aba Relatórios) em vez de recriar algo
paralelo.

**Tech Stack:** HTML/CSS/JS vanilla (sem framework), servido por Flask
(`server.py` / `templates/index.html`).

## Global Constraints

- Sem endpoint novo no backend — tudo calculado a partir de dados já carregados
  no cliente (`S.allSessions`, `S.products`, `S.stockMap`).
- Sem mudança na estrutura de dados persistida em `data/audit_sessions.json` (ou
  equivalente) — só leitura.
- Admin nunca edita contagem de loja nenhuma por essas telas — sempre somente
  leitura (regra já implementada em `renderCountList`, não deve ser tocada).
- Cards do topo do Dashboard (`#dash-stats`) devem refletir a soma de **produtos
  únicos** vistos/contados em qualquer loja — nunca soma bruta (um produto visto
  em 3 lojas conta 1 vez) — e nunca dependem de `S.viewFilialId`.
- Verificação manual via browser (Playwright), já que não há test runner de
  frontend configurado no projeto — `tests/` só cobre o backend Python.

---

### Task 1: Cards do Dashboard usam agregação global (produtos únicos)

**Files:**
- Modify: `templates/index.html:675-680` (nova função, ao lado de
  `getProgressByFilial`)
- Modify: `templates/index.html:1149-1164` (`renderDashboard`)

**Interfaces:**
- Consumes: `S.allSessions` (array de sessões, cada uma com `.encontrados` —
  dict `productId -> {ean, descricao, scannedAt, qtd?}`), `S.products` (array,
  usa só `.length`).
- Produces: `getGlobalAuditStats()` → `{ total, found, contados, pending, pct }`,
  usada só dentro de `renderDashboard()`.

- [ ] **Step 1: Adicionar `getGlobalAuditStats()`**

Inserir logo depois da função `getProgressByFilial` (que termina na linha 680 com
`}`), antes do comentário `// A sessao de auditoria mais recente...`:

```js
// Agregado real do Dashboard: produtos UNICOS vistos/contados em qualquer
// loja, entre todas as sessoes do servidor (S.allSessions) - nao depende de
// qual usuario esta logado nem de qual loja o admin selecionou no cabecalho.
// Um produto bipado em 3 lojas diferentes conta 1 vez aqui (cobertura do
// catalogo), nao 3 (isso seria volume de trabalho, nao cobertura).
function getGlobalAuditStats() {
  const foundIds = new Set();
  const countedIds = new Set();
  for (const s of S.allSessions) {
    for (const [pid, entry] of Object.entries(s.encontrados || {})) {
      foundIds.add(pid);
      if (entry.qtd != null) countedIds.add(pid);
    }
  }
  const total = S.products.length;
  const found = foundIds.size;
  const contados = countedIds.size;
  const pending = total - found;
  const pct = total > 0 ? Math.round((found / total) * 100) : 0;
  return { total, found, contados, pending, pct };
}
```

- [ ] **Step 2: `renderDashboard()` usa a nova função e remove o hint condicional**

Old:
```js
function renderDashboard() {
  const st = getAuditStats();
  const cls = getGlobalClassifications();
  const byFilial = getProgressByFilial();

  const hint = S.isAdmin && !S.viewFilialId
    ? '<div style="font-size:.75rem;color:var(--muted);margin-bottom:8px">Produtos Existentes, Estoque Contado, Pendentes e Auditado mostram os numeros da loja selecionada no seletor do cabecalho. Selecione uma loja para ve-los.</div>'
    : '';
  $('#dash-stats-hint').innerHTML = hint;
  $('#dash-stats').innerHTML = `
```

New:
```js
function renderDashboard() {
  const st = getGlobalAuditStats();
  const cls = getGlobalClassifications();
  const byFilial = getProgressByFilial();

  $('#dash-stats').innerHTML = `
```

`getGlobalClassifications()` não muda — já é global (percorre `S.products`
inteiro, sem depender de sessão).

- [ ] **Step 3: Verificar manualmente**

Com o servidor rodando (ver Task 4 para como levantar/verificar), logar como
admin, ir em Dashboard **sem** selecionar nenhuma loja no seletor do cabeçalho:
"Produtos Existentes", "Estoque Contado" e "Auditado %" devem mostrar números
maiores que zero se existir qualquer sessão de bipagem salva (não mais
zerados/esperando seleção de loja). Trocar a loja no seletor do cabeçalho não
deve alterar esses 5 números (só afeta Relatórios/Auditoria).

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "fix: cards do dashboard usam agregado real de todas as lojas"
```

---

### Task 2: CSV do relatório "Encontrados" com colunas de auditoria

**Files:**
- Modify: `templates/index.html` (`exportCSV`, função atual em torno da linha
  1300-1325 — buscar pelo texto exato abaixo, o número de linha já mudou por
  causa da Task 1)

**Interfaces:**
- Consumes: `currentViewSession()` (já existe), `currentReport` (variável
  global já existente, valores possíveis: `'encontrados' | 'sem-codigo' |
  'duplicados' | 'sem-fornecedor' | 'sem-categoria' | 'descontinuados' |
  'todos'`), `S.stockMap` (dict `filialId + '_' + productId -> qtd`).
- Produces: nenhuma função nova — `exportCSV()` mantém a mesma assinatura
  (sem parâmetros), só passa a incluir colunas extras condicionalmente.

- [ ] **Step 1: Adicionar colunas extras quando o relatório ativo é "Encontrados"**

Old:
```js
function exportCSV() {
  const viewSession = currentViewSession();
  const headers = ['SKU', 'Descrição', 'EAN', 'Categoria', 'Fornecedor', 'Tipo', 'Ativo', 'Encontrado'];
  const rows = reportData.map(p => {
    const found = viewSession && viewSession.encontrados[p.id] ? 'Sim' : 'Não';
    return [
      fmt(p.codproduto),
      fmt(p.descricao).replace(/,/g, ' '),
      fmt(p.ean),
      fmt(p.departamentonivel1) || fmt(p.departamentonivel2) || '',
      p.fornecedor || '',
      fmt(p.tipo),
      p.ativo === '0' ? 'Não' : 'Sim',
      found,
    ];
  });
```

New:
```js
function exportCSV() {
  const viewSession = currentViewSession();
  const isEncontrados = currentReport === 'encontrados' && viewSession;
  const headers = ['SKU', 'Descrição', 'EAN', 'Categoria', 'Fornecedor', 'Tipo', 'Ativo', 'Encontrado'];
  if (isEncontrados) headers.push('Horário 1º Bipe', 'Qtd Contada', 'Estoque Sistema', 'Diferença');
  const rows = reportData.map(p => {
    const entry = viewSession ? viewSession.encontrados[p.id] : null;
    const found = entry ? 'Sim' : 'Não';
    const row = [
      fmt(p.codproduto),
      fmt(p.descricao).replace(/,/g, ' '),
      fmt(p.ean),
      fmt(p.departamentonivel1) || fmt(p.departamentonivel2) || '',
      p.fornecedor || '',
      fmt(p.tipo),
      p.ativo === '0' ? 'Não' : 'Sim',
      found,
    ];
    if (isEncontrados) {
      const qtd = entry && entry.qtd != null ? entry.qtd : null;
      const estoqueSistema = S.stockMap[viewSession.filialId + '_' + p.id] || 0;
      row.push(
        entry ? entry.scannedAt : '',
        qtd != null ? qtd : '',
        estoqueSistema,
        qtd != null ? qtd - estoqueSistema : '',
      );
    }
    return row;
  });
```

O restante da função (`csv`, `blob`, `a.download`, etc.) não muda.

- [ ] **Step 2: Verificar manualmente**

Logar como admin, selecionar uma loja com sessão de bipagem no seletor do
cabeçalho, ir em Relatórios, clicar na aba "Encontrados", clicar "Exportar CSV".
Abrir o arquivo baixado e confirmar que tem as colunas extras (Horário 1º Bipe,
Qtd Contada, Estoque Sistema, Diferença) preenchidas. Trocar para a aba
"Duplicados" (ou qualquer outra) e exportar de novo: essas 4 colunas não devem
aparecer (mantém o formato antigo pros outros relatórios).

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: csv de encontrados por loja inclui qtd contada e estoque sistema"
```

---

### Task 3: Polish — esconder "Bipagem" pro admin e avisar quando nenhuma loja selecionada

**Files:**
- Modify: `templates/index.html` (HTML da view de Auditoria, `switchAuditPanel`,
  `updateAuditUI`, `renderCountList`)

**Interfaces:**
- Consumes: `S.isAdmin`, `S.viewFilialId`, `currentViewSession()` (todos já
  existentes).
- Produces: nenhuma função nova exportada para outras tasks.

- [ ] **Step 1: Esconder a sub-aba "Bipagem" quando `S.isAdmin`**

Buscar a função `switchAuditPanel` (contém `report-tab` toggling para
`btn-mode-bipagem`/`btn-mode-existentes`/`btn-mode-contado`) e, na parte de
`switchView` que já trata `view === 'audit'` com `S.isAdmin` (branch já
existente que chama `switchAuditPanel('existentes')`), adicionar antes dessa
chamada:

```js
$('#btn-mode-bipagem').style.display = S.isAdmin ? 'none' : '';
```

E no branch `else` (bipador comum, que hoje faz
`switchAuditPanel('bipagem'); setTimeout(...)`), adicionar antes:

```js
$('#btn-mode-bipagem').style.display = '';
```

- [ ] **Step 2: Mensagem clara quando admin não selecionou loja**

Em `updateAuditUI()`, no branch que já existe para `!session` (hoje faz
`$('#found-count').textContent = '0'; $('#recent-scans-list').innerHTML = '';`),
trocar o `innerHTML` vazio por uma mensagem quando for admin:

```js
function updateAuditUI() {
  const session = currentViewSession();
  if (!session) {
    $('#found-count').textContent = '0';
    $('#recent-scans-list').innerHTML = S.isAdmin
      ? '<div class="empty">Selecione uma loja no seletor do cabeçalho para ver os produtos existentes.</div>'
      : '';
    return;
  }
  ...
```

Mesma troca em `renderCountList()`, no branch `if (!session) { ... }`:

```js
function renderCountList() {
  const session = currentViewSession();
  if (!session) {
    $('#count-total').textContent = '0';
    $('#count-list').innerHTML = S.isAdmin
      ? '<div class="empty">Selecione uma loja no seletor do cabeçalho para ver o estoque contado.</div>'
      : '';
    return;
  }
  ...
```

- [ ] **Step 3: Verificar manualmente**

Logar como admin, ir em Auditoria sem selecionar loja: sub-aba "Bipagem" não
aparece nos botões de aba; "Produtos Existentes" e "Estoque Contado" mostram a
mensagem "Selecione uma loja..." em vez de listas vazias silenciosas. Selecionar
uma loja no seletor do cabeçalho: as mensagens somem e os dados da loja aparecem
(sem controles de edição). Logar como bipador comum: sub-aba "Bipagem" continua
aparecendo normalmente, tudo funciona como antes.

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "polish: esconde bipagem para admin e avisa quando nenhuma loja selecionada"
```

---

### Task 4: Verificação ponta a ponta no browser

**Files:** nenhum (só verificação manual)

**Interfaces:** N/A

- [ ] **Step 1: Levantar o servidor local**

```bash
python server.py
```

Confirmar que sobe sem erro e que `http://localhost:5000` (ou a porta configurada
em `server.py`) responde.

- [ ] **Step 2: Roteiro de verificação manual (Playwright ou browser normal)**

1. Login como admin.
2. Dashboard: cards do topo mostram números reais sem selecionar loja alguma
   (Task 1).
3. "Progresso por Loja": clicar numa barra de loja com bipagem existente → deve
   ir para Relatórios, aba "Encontrados", com essa loja já selecionada no
   seletor do cabeçalho.
4. Nessa tela, clicar "Exportar CSV" → arquivo baixado tem as colunas extras
   (Task 2).
5. Ir em Auditoria: sem loja selecionada, mostra o aviso da Task 3; selecionar
   uma loja → "Produtos Existentes" e "Estoque Contado" mostram os dados dessa
   loja, sem botões de edição.
6. Logout, login como bipador comum de uma loja: Dashboard/Relatórios/Bipadores/
   Dedup ficam ocultos no menu (comportamento já existente, não deve ter
   regredido); Auditoria mostra Bipagem normalmente, com os controles de
   edição de sempre.

- [ ] **Step 3: Reportar ao usuário**

Se algo do roteiro falhar, corrigir antes de considerar a task concluída — não
seguir para outra task com o roteiro quebrado.
