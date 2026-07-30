# Contagem de Estoque — Design

## Contexto

O app de Auditoria de Estoque hoje tem uma aba "Auditoria" que faz verificação binária:
bipa/busca um produto, marca como encontrado (ou duplicado se já bipado antes na sessão).
Não existe nenhuma forma de registrar *quantas unidades* de um produto existem fisicamente.

Este design adiciona um modo de **contagem real de unidades**, ao lado do modo de
verificação existente, dentro da mesma aba/sessão de auditoria.

## Objetivo

Permitir que o usuário:
1. Continue verificando presença de produtos (fluxo atual, sem mudanças de comportamento).
2. Também conte a quantidade física de cada produto — via bipagem repetida (+1 por bipe)
   ou via digitação manual de um número exato — e veja a diferença contra o que o sistema
   (API i9logic, já sincronizado) acha que deveria ter.

## Modelo de dados (unificado)

A sessão de auditoria (`audit_sessions.json`, já persistida no servidor) ganha um campo
novo por produto. Hoje cada entrada em `session.encontrados[productId]` é:

```json
{"ean": "...", "descricao": "...", "scannedAt": "HH:MM:SS"}
```

Passa a ser:

```json
{"ean": "...", "descricao": "...", "scannedAt": "HH:MM:SS", "qtd": 0}
```

- Presença no dict `encontrados` = "encontrado" (aba Verificação) — sem mudança.
- `qtd > 0` = já foi contado fisicamente (aba Contagem).
- Uma única sessão, um único histórico por loja/dia. Bipar na Contagem também marca
  o produto como encontrado na Verificação (é a mesma entrada).

## Resolução de identificador

- **Bipagem** (câmera ou leitor USB): sempre interpretado como **EAN exato** — é o que
  um código de barras físico contém. Sem mudança em relação ao fluxo atual.
- **Busca manual** (campo de texto, digitado): tenta, em ordem —
  1. EAN exato
  2. `codproduto` exato
  3. Filtro por NCM — NCM não é identificador único de produto (é código fiscal,
     dezenas/centenas de produtos podem compartilhar o mesmo NCM), então esse passo
     sempre retorna uma **lista** de candidatos para o usuário escolher, nunca resolve
     direto para um produto.

Essa resolução (`resolveProduct(code)`) é compartilhada pelas duas sub-abas.

## UI: sub-abas dentro de "Auditoria"

Seletor no topo da view Auditoria: **[Verificação] [Contagem]**. Ambas compartilham o
mesmo campo de bipagem/scan-input; o sub-modo ativo determina o que o scan/busca faz.

### Verificação (existente, sem mudança de comportamento)
Bipe/busca → resolve produto → marca encontrado / avisa duplicado. Passa a usar
`resolveProduct` (ean → codproduto → lista por NCM) em vez da lógica atual restrita a EAN
na busca manual.

### Contagem (novo)
- Lista de produtos já contados na sessão atual.
- Cada linha: `[-1]  [qtd — editável]  [+1]`  +  coluna "Sistema: X | Diferença: ±Y"
  (comparando com a soma de `produtos_estoques.qtd` do cache, para aquele produto na
  loja da sessão).
- Bipar um produto: soma +1 (cria a entrada com qtd=1 se ainda não existir).
- Digitar um número no campo e confirmar: **substitui** o valor daquele produto (não soma).
- Botão `-1`: decrementa 1, nunca desce abaixo de 0.

## Backend

### Endpoint novo: `POST /api/audit/count`

Body: `{sessionId, productId, ean, descricao}` + **exatamente um** de:
- `{delta: 1}` ou `{delta: -1}` — incrementa/decrementa a partir do valor atual (clamp em 0)
- `{qtd: N}` — define o valor exato (substitui)

Se `sessionId` ou `productId` ausente, ou nem `delta` nem `qtd` presentes, ou **ambos**
presentes ao mesmo tempo: 400 (contrato exige exatamente um dos dois, nunca os dois juntos).
Se sessão não existe: 404. Cria a entrada em `encontrados[productId]` se ainda não existir.

Resposta: `{ok: true, qtd: <novo valor>, qtdSistema: <soma do cache>, diferenca: qtd - qtdSistema}`.

`qtdSistema` é calculado somando `CACHE["estoques"]` filtrado por `idproduto == productId`
e `filial == session["filialId"]` (mesma lógica de agregação já usada em `debug_pull200`).

### Endpoint existente: `POST /api/audit/scan` (Verificação)

Sem mudança de contrato. Continua só marcando presença/duplicado.

## Tratamento de erros

- Código bipado/buscado não existe no catálogo: mensagem "produto não encontrado" (já
  existe hoje), nenhuma entrada é criada.
- Busca por NCM sem resultados: mensagem clara, nenhuma lista pra escolher.
- Decrementar abaixo de 0: trava em 0, sem erro.
- Falha de rede ao sincronizar contagem: mesmo padrão já usado no scan — atualiza local
  (otimista) + toast de aviso; não bloqueia o fluxo de bipagem.

## Testes

Pytest via Flask test client (`tests/test_local_persistence.py` ou arquivo novo dedicado),
sem tocar a API externa:
- `delta: 1` soma corretamente a partir de uma entrada nova.
- `delta: -1` nunca deixa `qtd` negativo.
- `qtd: N` substitui o valor exato (não soma com o existente).
- Cria a entrada em `encontrados` quando o produto ainda não tinha sido bipado.
- Calcula `qtdSistema`/`diferenca` corretamente contra um cache de estoques mockado
  (múltiplos registros de `tipoestoque` para o mesmo produto/loja, soma tudo).
- 400 quando faltam campos obrigatórios ou nem `delta` nem `qtd` são passados.
- 404 quando a sessão não existe.

A resolução de identificador (`resolveProduct`, front-end) fica coberta por teste manual
no navegador (Playwright), já que o projeto não tem harness de teste JS configurado —
a lógica de negócio de contagem (o que importa comparar/persistir) mora no backend e
tem cobertura pytest completa.

## Fora de escopo (YAGNI)

- Mostrar divergência de contagem nos "Relatórios" existentes — pode vir depois, não
  pedido agora.
- Histórico de contagens anteriores por produto (só a sessão atual importa).
- Múltiplas lojas na mesma sessão de contagem (sessão já é por loja, como hoje).
