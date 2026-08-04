# Dashboard com dados reais + relatório por loja — Design

## Contexto

O Dashboard do admin mostra "Cadastrados / Produtos Existentes / Estoque Contado /
Auditado" e "Progresso por Loja". Os cards do topo vêm de `getAuditStats()`, que só
lê `S.session` — a sessão de auditoria do **usuário logado no navegador atual**, não
um agregado real. Como o admin nunca faz login como bipador (não tem `filialId`
próprio), `S.session` é sempre `null` pra ele, então os cards ficam zerados
independente do que os bipadores já fizeram. "Progresso por Loja" já lê
`S.allSessions` (todas as sessões, de todas as lojas, carregadas via
`GET /api/audit/sessions`) e por isso já reflete a realidade — só não é clicável.

Na aba Auditoria, os painéis "Produtos Existentes" e "Estoque Contado" também usam
`S.session`. Pro bipador comum isso já é a loja dele (correto). Pro admin, que não
tem sessão própria, a aba fica vazia e sem forma de escolher de qual loja quer ver
os dados.

## Objetivo

1. Cards do topo do Dashboard passam a refletir dados reais agregados de todas as
   lojas, não a sessão do usuário logado.
2. Cada barra em "Progresso por Loja" fica clicável e leva para o relatório daquela
   loja (produtos vistos/bipados), com exportação em CSV.
3. Na aba Auditoria, quando o admin está logado, aparece um seletor de loja — só
   depois de escolher uma loja os painéis "Produtos Existentes" e "Estoque Contado"
   mostram dados (somente leitura).

## Métrica agregada dos cards do topo

`getAuditStats()` deixa de depender de `S.session` para os números usados no
Dashboard. Nova função `getGlobalAuditStats()`:

- Percorre `S.allSessions` (todas as lojas, todos os bipadores) e monta um `Set` de
  `productId` para "encontrados" (chave presente em `session.encontrados`) e outro
  `Set` para "contados" (`qtd != null`).
- Um mesmo produto bipado em 3 lojas diferentes conta **1 vez** em cada set — a
  métrica é "produtos únicos já vistos em algum lugar", não soma bruta por loja.
- `total` = `S.products.length` (catálogo, como já é hoje).
- `pct` = `found_únicos / total * 100`.

Sem filtro por data: sessões de dias antigos continuam contando (mesmo
comportamento que "Progresso por Loja" já tem hoje ao somar `s.encontrados` de
todas as sessões de uma loja, sem olhar `data`). Não faz parte do escopo mudar isso.

`renderDashboard()` passa a chamar `getGlobalAuditStats()` em vez de
`getAuditStats()`. A função antiga `getAuditStats()` continua existindo e sendo
usada só onde já é usada hoje (estatística pessoal do bipador dentro do painel de
Bipagem, `#stat-existentes`/`#stat-contados`).

## Progresso por Loja — clicável

Cada bloco de loja renderizado em `#progress-per-filial` (dentro de
`getProgressByFilial()`/`renderDashboard()`) ganha `cursor:pointer`, um estado de
hover simples e `onclick="goToFilialAudit(<filialId>)"`.

`goToFilialAudit(filialId)`:
1. Chama `switchView('audit')`.
2. Seleciona `filialId` no novo dropdown de loja da aba Auditoria (seção
   seguinte) e dispara o mesmo carregamento que a troca manual do dropdown
   dispara.
3. Muda para a sub-aba "Produtos Existentes" (`switchAuditPanel('existentes')`).

Essa função só é exposta a partir do Dashboard, que já é uma view exclusiva do
admin (`adminOnly`) — não precisa lidar com o caso "bipador comum clicando".

## Aba Auditoria — seletor de loja para o admin

Novo bloco no topo de `#audit-view`, visível só quando `S.isAdmin === true`:

```html
<div id="audit-admin-bar" style="display:none">
  <select id="audit-filial-select"><option value="">Selecione a loja</option></select>
  <button id="btn-audit-export-csv" class="btn-s btn-sm">Exportar CSV</button>
</div>
```

- Populado com `popAdminFilialSelect('#audit-filial-select')` (função já existente,
  reaproveitada).
- `switchView('audit')` mostra/esconde `#audit-admin-bar` de acordo com
  `S.isAdmin`, e quando `S.isAdmin` é `true` também esconde o painel de Bipagem
  (`#bipagem-panel` some; ou mostra um aviso fixo "Visualização apenas — selecione
  a loja acima" no lugar do input de bipar) e desabilita a sub-aba "Bipagem".
- Evento `change` no `#audit-filial-select` chama `onAdminFilialSelect(filialId)`:
  - `S.adminAuditFilialId = filialId`.
  - `S.adminAuditSession = getLatestSessionForFilial(filialId)` (nova função:
    filtra `S.allSessions` por `filialId`, ordena por `data` decrescente — string
    ISO ordena léxico corretamente — e retorna a primeira, ou `null` se a loja
    nunca teve sessão).
  - Re-renderiza os painéis "Produtos Existentes" e "Estoque Contado".
  - Se `S.adminAuditSession` for `null`, os painéis mostram
    "Nenhuma auditoria iniciada nessa loja ainda." em vez de listas vazias.

### Sessão ativa (leitura) nos painéis

Introduz `activeAuditSession()`:

```js
function activeAuditSession() {
  return S.isAdmin ? S.adminAuditSession : S.session;
}
```

`updateAuditUI()` (lista "Produtos Existentes") e `renderCountList()` (lista
"Estoque Contado") passam a ler de `activeAuditSession()` em vez de `S.session`
diretamente. Como as funções de escrita (`markFound`, `registerHit`,
`applyCountDelta`, `setCountForProduct`, `syncScanToServer`, `syncCountToServer`)
só são acionadas pelos controles do painel de Bipagem e pelos botões
`-1`/`+1`/input de "Estoque Contado" — ambos ocultos/sem função quando
`S.isAdmin` — elas não precisam ser tocadas.

Em `renderCountList()`, quando `S.isAdmin` é `true`, os botões `-1`/`+1` e o
`<input>` editável de quantidade não são renderizados — só o texto
"Qtd: N | Sistema: X | Diferença: ±Y" (mesmos dados, sem controle de edição).

## Exportar CSV do relatório por loja

Botão `#btn-audit-export-csv`, visível tanto pro bipador comum (exporta a própria
loja, sem precisar de seletor) quanto pro admin (exporta a loja escolhida no
dropdown). Chama `exportAuditReportCSV()`:

- Usa `activeAuditSession()`. Se `null`, mostra toast de aviso e não gera nada.
- Uma linha por produto em `session.encontrados`.
- Colunas: SKU (`codproduto` via `S.productsMap`), Descrição, EAN, Horário 1º bipe
  (`scannedAt`), Qtd Contada (`qtd`, vazio se nunca contado — só verificado),
  Estoque Sistema, Diferença.
- Estoque Sistema/Diferença **não estão persistidos no servidor** (o backend só os
  calcula e devolve na resposta de `/api/audit/count`, sem salvar na sessão) — o
  export recalcula no cliente: `estoqueSistema = S.stockMap[filialId + '_' + productId] || 0`,
  `diferenca = qtd != null ? qtd - estoqueSistema : null`. `S.stockMap` já está
  carregado globalmente (todas as filiais) desde o login.
- Nome do arquivo: `auditoria_<nome da loja>_<data ISO>.csv`, mesmo padrão do
  export de Relatórios existente.

## Fora de escopo (YAGNI)

- Nenhum endpoint novo no backend — tudo lido a partir de dados já carregados
  (`S.allSessions`, `S.products`, `S.stockMap`).
- Admin não edita contagens de nenhuma loja por essa tela — somente leitura.
- Sem histórico multi-dia: sempre a sessão mais recente da loja. Ver dias
  anteriores não foi pedido.
- Sem atualização em tempo real/polling — os dados só atualizam ao trocar de
  loja/view ou recarregar, mesmo padrão já usado no resto do app.
- Bipador comum não ganha seletor de loja — continua vendo só a própria, como
  hoje.
