# Bipadores por Loja + Admin — Design

## Contexto

Hoje qualquer pessoa se autocadastra (nome+email+loja, sem senha) direto no modal de
login, e cada login cria uma sessão de auditoria nova e vazia — mesmo que outro
bipador da mesma loja já esteja bipando naquele dia. O usuário tem 5 lojas e vai
cadastrar bipadores para todas; ele quer controle central desse cadastro e que o
ambiente de bipagem/contagem seja compartilhado entre bipadores da mesma loja (sem
duplicar trabalho), mas nunca misture dados entre lojas diferentes.

Além disso, o fluxo de bipagem/contagem muda: hoje existem dois sub-modos que o
bipador escolhe manualmente (Verificação / Contagem). O usuário quer um fluxo único
onde a repetição do bipe decide automaticamente o que acontece.

## Objetivo

1. Sessão de auditoria passa a ser por **loja + dia**, não por login — todo bipador
   da mesma loja no mesmo dia cai na mesma sessão.
2. Cadastro de bipador deixa de ser aberto: só um **admin** (senha única, variável de
   ambiente) cria contas de bipador, numa aba nova dentro do app.
3. Fluxo de bipagem unificado: 1º bipe de um código = confirma existência (aparece em
   **Produtos Existentes**); 2º bipe em diante do mesmo código = soma +1 na contagem
   (aparece em **Estoque Contado**). Sem alternância manual de modo para bipar.
4. Três áreas dentro de "Auditoria": **Bipagem** (campo de bipar/buscar), **Produtos
   Existentes** (lista do que já foi confirmado) e **Estoque Contado** (lista do que
   tem quantidade contada, com os controles -1/editar/+1 já existentes).

## Modelo de sessão (loja + dia)

`POST /api/audit/session/start` deixa de criar sempre uma sessão nova: procura, entre
as sessões já salvas, uma com o mesmo `filialId` e a mesma `data` (hoje) e, se achar,
devolve ela (200) em vez de criar outra (201). Sem outros bipadores na loja hoje,
comportamento é idêntico ao atual (cria a primeira sessão do dia).

**Efeito colateral necessário**: hoje todo logout chama `finalizeSession()`
(`POST /api/audit/session/finish`, marca `fim`). Com sessão compartilhada, isso
encerraria o ambiente para os colegas ainda bipando na mesma loja. Essa chamada
(no logout e no `beforeunload`) é removida, e o endpoint `/api/audit/session/finish`
— que ficaria sem nenhum uso — é removido também, já que "fim" não tem mais função:
a virada de dia já garante sessão nova por conta da checagem de data.

Sem alteração nos endpoints `/api/audit/scan` e `/api/audit/count` — a regra de
"1º bipe existe, 2º bipe conta" é decidida inteiramente no front-end, escolhendo qual
dos dois endpoints chamar com base no que já está na sessão local.

## Bipagem unificada (sem alternar modo)

Bipar ou selecionar um produto (busca manual) sempre passa por uma função única:
- Produto **ainda não** está em `session.encontrados` → chama o fluxo de
  "confirmar existência" (o mesmo que já existe: `POST /api/audit/scan`) → aparece em
  Produtos Existentes.
- Produto **já está** em `session.encontrados` (bipado/selecionado de novo) → chama o
  fluxo de contagem (`POST /api/audit/count` com `delta: 1`, já existente) → soma +1,
  aparece/atualiza em Estoque Contado. Continua aparecendo em Produtos Existentes
  também — é a mesma entrada, só que agora tem `qtd > 0`.

Bipagem física (leitor USB, câmera, imagem) continua resolvendo **só por EAN exato**
— isso não muda. Busca manual continua podendo resolver por EAN → SKU → lista de NCM
(candidatos), como já implementado — só a ação final (o que fazer com o produto
resolvido) passa a seguir a regra acima em vez de depender de um modo escolhido à mão.

## Três áreas em "Auditoria"

Sub-abas dentro da view Auditoria (substituem as duas atuais "Verificação"/"Contagem"):

1. **Bipagem** — campo de bipar (`#scan-input`), busca manual, botão câmera, feedback
   imediato ("✅ Produto existente" ou "📦 Contando: N un."), e um resumo
   "Produtos Existentes: X | Itens Contados: Y".
2. **Produtos Existentes** — lista de tudo confirmado presente na sessão (era
   "Últimos bipados" da Verificação).
3. **Estoque Contado** — lista só do que tem quantidade contada (`qtd != null`), com
   os controles `-1` / campo editável / `+1` e "Sistema: X | Diferença: ±Y" já
   existentes — sem mudança nessa parte, só a reorganização de aba.

## Admin único + aba Bipadores

- Uma senha (`ADMIN_PASSWORD`, variável de ambiente, mesmo padrão do token da API já
  usado no projeto). `POST /api/admin/login` valida com comparação de tempo
  constante (`hmac.compare_digest`).
- Modal de login ganha um link discreto "Sou administrador" que troca o formulário de
  nome+email pelo de senha. Sem persistência de sessão admin no `localStorage`
  (precisa digitar a senha de novo a cada acesso — não guardamos senha em texto puro
  no navegador entre sessões).
- Login como admin não passa pelo fluxo de sessão de auditoria (sem `filialId`, sem
  `createSession()`) — vai direto para a nova view "Bipadores", e o menu de navegação
  mostra só essa aba + Sair (Dashboard/Auditoria/Relatórios/Diagnóstico ficam ocultos
  para o admin; o inverso para o bipador comum).
- Aba **Bipadores**: lista os bipadores agrupados por loja (usa `GET /api/auth/users`,
  que já existe e continua público — só expõe nome/email/loja, nada sensível) e um
  formulário "+ Novo Bipador" (nome, email, loja via dropdown das lojas reais).
- `POST /api/auth/register` (autocadastro aberto) é removido. Vira
  `POST /api/admin/bipadores`, com o mesmo corpo de antes mais `adminPassword`
  (rejeita com 403 se não bater com `ADMIN_PASSWORD`).
- `POST /api/auth/login` (bipador comum, nome+email sem senha) não muda.

## Fora de escopo (YAGNI)

- Editar/desativar bipador — não pedido, adiciona-se depois se precisar.
- Múltiplos admins / papéis diferenciados — só um admin global por enquanto.
- Atualização em tempo real (polling) da sessão compartilhada — os dados só refletem
  o que outro bipador fez quando a página é recarregada ou a aba é trocada.
- Validar que o `filialId` recebido corresponde a uma loja real sincronizada — o
  dropdown do admin já só oferece lojas reais, e quem cadastra agora é o admin (não
  mais um cadastro aberto), então o risco de um `filialId` inventado cai bastante.
