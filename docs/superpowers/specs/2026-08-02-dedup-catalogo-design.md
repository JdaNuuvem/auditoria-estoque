# Deduplicação de Catálogo — Design

## Contexto

O usuário pediu um sistema de "saneamento e organização de estoque" em 3 etapas:
auditoria inicial (classificar físico vs sistema), deduplicação de cadastros e
contagem oficial. Análise crítica do plano original, feita e aprovada com o
usuário:

- O app **nunca escreve na API i9logic** (só `GET`, confirmado em todo o
  `server.py`). "Unificar" cadastros e "atualizar estoque das lojas" não podem
  significar escrita automática no ERP — viram um mapeamento local mais um
  relatório para atualização manual no i9logic depois.
- Fazer a auditoria inicial (classificação) e a contagem oficial como duas
  passadas físicas separadas dobra o trabalho de chão à toa: o sistema de
  bipagem já em produção junta as duas coisas numa única tocada (1º bipe
  confirma existência, bipes seguintes contam). Etapas 1 e 3 do pedido
  original foram fundidas nessa mesma lógica — mas só fazem sentido fundidas
  **depois** do cadastro limpo, senão a mesma coisa física bipada por duas
  etiquetas diferentes conta pra dois lugares.
- Conclusão: **Deduplicação primeiro** (trabalho 100% de dados, sem gente no
  chão) — é o assunto deste spec. A contagem física unificada (antiga Etapa
  1+3) é um spec separado, que consome os grupos canônicos daqui.

Esta primeira parte do sistema entrega valor sozinha: mesmo sem nenhuma
contagem física acontecer ainda, cada loja já pode limpar os grupos de
duplicatas dos produtos que ela vende.

## Objetivo

Detectar automaticamente cadastros que representam o mesmo produto real
(critérios: EAN, NCM, marca, descrição normalizada, SKU, outras heurísticas),
apresentar sugestões de unificação para o admin de cada loja validar, e
persistir localmente os grupos confirmados — sem tocar no cadastro real da
i9logic.

## Escopo por loja

O catálogo de produtos (`CACHE["produtos"]`) é compartilhado entre todas as
filiais — o mesmo `id` de produto aparece em qualquer loja, só o estoque
(`estoques`) é próprio de cada uma. Ainda assim, por decisão explícita do
usuário:

- A fila de candidatos a duplicata, para uma loja, é filtrada aos produtos que
  **aquela loja** tem em `estoques` (fila pequena e relevante, não os ~19 mil
  produtos do catálogo inteiro).
- A decisão de unificar (ou rejeitar) é **independente por loja**. A mesma
  dupla de cadastros pode ficar unificada numa loja e separada em outra — não
  existe fusão global. Cada filial mantém seu próprio conjunto de grupos
  confirmados.

## Modelo de dados

Novo arquivo `dedup_groups.json` (mesmo padrão de `users.json`/
`audit_sessions.json`: dict serializado em disco, protegido por
`threading.Lock` dedicado — `_dedup_lock`), formato:

```json
{
  "<filialId>": {
    "<signature>": {
      "status": "approved" | "rejected",
      "memberProductIds": [123, 456],
      "canonicalProductId": 123,
      "confidence": "alta" | "media" | "baixa",
      "signals": ["ean_igual"],
      "decidedBy": "admin",
      "decidedAt": "2026-08-02T14:30:00"
    }
  }
}
```

`signature` é uma chave estável e determinística para o grupo candidato —
os `memberProductIds` ordenados e unidos por `-` (ex: `"123-456"`). Serve
para: (a) uma vez decidido (aprovado OU rejeitado), reanalisar não faz esse
par resurgir; (b) idempotência ao gerar candidatos de novo.

## Algoritmo de detecção

Sem dependências de IA/embeddings (YAGNI — o catálogo é texto curto
estruturado, heurísticas resolvem bem). Nova dependência leve:
`rapidfuzz` (extensão C, comparação de strings robusta a reordenação de
palavras/abreviações — superior ao `difflib` da stdlib para descrições de
produto, que variam bastante em ordem).

Para os produtos em estoque de uma filial:

1. **Bucketing por sinais fortes** (barato, evita O(n²) no catálogo inteiro):
   - Mesmo EAN não-vazio cadastrado em 2+ produtos diferentes → bucket próprio,
     confiança **alta** direto (sinal `ean_igual`).
   - Mesmo NCM + mesma marca (ambos não-vazios) → bucket candidato.
2. **Dentro de cada bucket** (exceto o de EAN igual, que já é decidido): pontua
   similaridade da descrição normalizada (minúsculas, sem acento, espaços
   colapsados) via `rapidfuzz.fuzz.token_sort_ratio` (tolera ordem de palavras
   trocada).
   - Score ≥ 90 → confiança **alta** (sinal `descricao_muito_similar` +
     `ncm_marca_iguais`).
   - Score ≥ 75 → confiança **média**.
   - Score ≥ 60 → confiança **baixa**.
   - Abaixo de 60 → descartado, não vira candidato.
3. Produtos com descrição muito similar mas **tamanho/variação diferente**
   detectável no texto (ex: "500ml" vs "1L", "P" vs "G") não são penalizados
   automaticamente pelo algoritmo — o texto da descrição já reduz o score de
   similaridade nesses casos na prática (unidades/tamanhos diferentes tornam
   as strings menos parecidas). Casos limítrofes caem em confiança
   média/baixa, indo para revisão individual, nunca aprovação em lote.

## Fluxo de revisão

Tela nova, só admin (reaproveita `S.isAdmin`/`S.adminPassword` e o padrão de
autenticação por senha em cada chamada, igual às rotas de admin já
existentes):

1. Admin escolhe uma loja (mesmo seletor de filiais já usado no cadastro de
   bipador).
2. Botão "Analisar Duplicatas" — roda o algoritmo, exclui candidatos cuja
   `signature` já tem decisão (approved ou rejected) persistida para aquela
   filial.
3. Candidatos de **confiança alta** aparecem destacados com botão
   "Aprovar todos os óbvios" (aprova em lote; canônico de cada grupo = o
   membro com maior estoque atual na filial, sem intervenção manual).
4. Candidatos de **média/baixa confiança** ficam numa fila individual,
   ordenada da mais pra menos confiável. Cada card mostra os produtos
   membros lado a lado (descrição, EAN, NCM, marca, estoque atual na filial),
   com um seletor pré-marcado no de maior estoque (admin pode trocar) e
   botões Aprovar / Rejeitar.
5. Aprovar grava `status: "approved"` com o `canonicalProductId` escolhido.
   Rejeitar grava `status: "rejected"` (sem canônico).

## Relatório

Tela + exportação CSV, por loja, dos grupos `approved`: lista os cadastros
membros (id, descrição, EAN, SKU) e qual foi marcado canônico — material
pronto para alguém consolidar manualmente o cadastro real no i9logic depois.

## Endpoints (todos exigem `adminPassword`, mesmo padrão de
`_admin_password_ok` + rate limit compartilhado com as outras rotas de admin)

- `POST /api/admin/dedup/analyze` `{adminPassword, filialId}` → lista de
  candidatos novos (não decididos ainda) com membros, confiança e sinais.
- `POST /api/admin/dedup/confirm` `{adminPassword, filialId, signature,
  canonicalProductId}` → aprova um grupo.
- `POST /api/admin/dedup/bulk-confirm` `{adminPassword, filialId,
  signatures: [...]}` → aprova vários de uma vez (canônico = maior estoque
  em cada, automático).
- `POST /api/admin/dedup/reject` `{adminPassword, filialId, signature}` →
  rejeita um grupo.
- `GET /api/dedup/groups?filialId=` → grupos `approved` daquela filial (será
  consumido pelo spec de Contagem Oficial depois; público como
  `GET /api/auth/users` já é, sem dado sensível).

## Testes

Suite pytest (Flask test client, catálogo de teste pequeno e controlado via
monkeypatch do `CACHE`, seguindo o padrão de `tests/test_local_persistence.py`):

- Bucketing por EAN igual gera candidato de confiança alta.
- Bucketing por NCM+marca+descrição similar gera candidato de confiança
  média/baixa conforme o score.
- Produtos genuinamente diferentes (descrição, NCM e marca distintos) não
  geram candidato.
- Candidato só é sugerido para uma filial se ela tiver estoque do produto.
- Aprovar um grupo numa filial não afeta a mesma dupla em outra filial
  (isolamento confirmado pelo usuário).
- Aprovação em lote grava todos com canônico = maior estoque.
- Rejeitar persiste e não resurge numa nova análise.
- `GET /api/dedup/groups` devolve só os grupos `approved` da filial pedida.
- Todas as rotas de admin exigem `adminPassword` correta (403 caso
  contrário), seguindo o padrão já testado em `admin_create_bipador`.

## Fora de escopo (YAGNI)

- Escrita automática no i9logic — impossível hoje (API é somente leitura) e
  não pedido; fica pro relatório manual.
- Fusão global entre lojas — decisão explícita do usuário: cada loja decide
  sozinha.
- Matching semântico via IA/embeddings — heurísticas de texto resolvem bem
  o problema, sem custo/latência de um serviço externo.
- Qualquer coisa do fluxo de contagem física (isso é o próximo spec,
  "Contagem Oficial Unificada" — Etapa 1+3 fundidas do pedido original).
- Editar/desfazer uma decisão já confirmada — não pedido; se necessário,
  fica para uma iteração futura.
