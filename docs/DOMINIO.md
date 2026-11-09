# Envio de XML para a Domínio (API Onvio)

Estado em 2026-11-09: **fila, tela e cliente da API prontos e alinhados ao guia oficial; não testados contra a API real.**
Faltam o `client_id`/`client_secret` do integrador (pedidos a `api.dominio@thomsonreuters.com`)
e as respostas às dúvidas abaixo.

## Dois modos de conexão (Admin → Envio Domínio → Conexão com a Domínio)
| Modo | O que precisa | Quem fala com a Domínio |
|---|---|---|
| **Gateway do sistema** (padrão) | Código de ativação do fornecedor + chave Domínio de cada empresa | O gateway do fornecedor (`yzmp6lmumi.execute-api.sa-east-1.amazonaws.com`), igual ao EXE CentralNFSe |
| **API direta** | Client ID/Secret da Domínio + chave de cada empresa | O próprio Portal Fiscal (`api.onvio.com.br`) |

### Gateway (engines/dominio_gateway.py) — rotas, iguais às do EXE
| Rota | Uso |
|---|---|
| `POST /api/v1/ativacoes` | `{ticket, installation_id}` → `credential`, `expires_at` (credencial cifrada com `DOMINIO_KEY`) |
| `GET /health` | Testar conexão |
| `POST /api/v1/envios` | XML (`application/xml`) + `X-Company-Cnpj`, `X-Company-Key`, `X-Idempotency-Key` |
| `GET /api/v1/envios/{request_id}` | Operação já iniciada: consulta antes de reenviar |
| `POST /api/v1/envios/status` | Resultados em lote (a cada 30 s) |
| `POST /api/v1/envios/acompanhamento` | Gateway acompanha sozinho o que está AGUARDANDO_DOMINIO (2 por ciclo) |
| `POST /api/v1/envios/confirmacoes` | Confirma ao gateway os resultados finais já gravados |
| `POST /api/v1/envios/{request_id}/retentativas` | Libera o reenvio manual |

- `request_id = sha256(installation_id|cnpj|sha256(xml))`: a mesma operação nunca é duplicada.
- Estados: RECEBIDO/AGUARDANDO_DOMINIO → ENVIADO; CONFIRMADO; JA_EXISTENTE; REJEITADO → RECUSADO;
  INDETERMINADO → INCERTO; FALHA_TRANSITORIA → REPETIR (ou PAUSADO se o gateway não permitir).
- Ritmo do EXE: 1 XML a cada ~4 s; resultados a cada 30 s.
- **Atenção:** ativar o portal com o código cria uma instalação nova no gateway. Se o código for de
  uso único, a instalação do EXE pode deixar de valer — confirmar com o fornecedor.

## Como funciona
```
XML/<cnpj>/<AAAA-MM>/<doc>/<sub>/  →  varrer()  →  tabela dominio_envios  →  processar()  →  API
                                                                          →  consultar()  ←  lote
```
- **Arquivos:** `engines/dominio_api.py` (HTTP), `engines/dominio_fila.py` (fila e worker),
  tela `templates/dominio.html` (rota `/dominio`, menu Admin → Envio Domínio).
- **Entra na fila:** `NFe/01_entrada|04_saida|09_cancelada`, `NFSe/01_tomado|02_prestado`,
  `NFCe/01_venda|09_cancelada` e, em `03_eventos`, só cancelamentos (110111/110112, 101101/105102).
  `02_resumo` nunca entra.
- **Identidade:** CNPJ + doc + nome do arquivo. A nota que vai de `04_saida` para `09_cancelada`
  continua sendo a mesma linha.
- **Data inicial por empresa:** XML com emissão anterior vira `FORA_PERIODO` e não é enviado.
- **Segurança contra duplicidade:** linha reservada (`ENVIANDO`) antes do envio; queda de conexão
  durante o envio vira `INCERTO` e só volta com confirmação manual.
- **Thread própria:** não disputa a fila de jobs da SEFAZ. Liga com `DOMINIO_ENVIO_ATIVO=1` **e**
  o botão "Ligar envio" na tela.

## Estados
| Estado | Significado |
|---|---|
| NOVO / REPETIR | Na fila / nova tentativa agendada |
| ENVIANDO | Reservado por este servidor |
| ENVIADO | Lote criado; aguardando consulta |
| CONFIRMADO / JA_EXISTENTE | Encerrado com sucesso |
| RECUSADO | Recusa definitiva (ou XML alterado / arquivo sumiu) |
| PAUSADO | 401/403 ou chave ausente; volta ao trocar a chave |
| INCERTO | Pode ter chegado: conferir na Domínio antes de reenviar |
| FORA_PERIODO | Antes da data inicial da empresa |

## Guia oficial da Domínio (solução 8476, lido em 2026-11-09)
Fonte: https://suporte.dominioatendimento.com/central/faces/solucao.html?codigo=8476

1. **Cadastro do ERP:** feito no site da Domínio ("Integração online com qualquer ERP"). O
   Client ID/Secret é exclusivo do ERP (o Portal Fiscal) e não é a chave do cliente.
2. **Token:** `POST https://auth.thomsonreuters.com/oauth/token`, Basic `client_id:client_secret`,
   form `grant_type=client_credentials`, `client_id`, `client_secret`,
   `audience=409f91f6-dc17-44c8-a5d8-e0a1bafd8b67`. Vale 24 h. **Gerar 1x ao dia** e usar o mesmo
   token para todos os clientes. Consumo excessivo (acima de 40) bloqueia a integração.
   → O portal guarda o token cifrado no banco (sobrevive a deploy) e limita a 5 pedidos/dia.
3. **Conferir a chave do cliente:** `GET .../integration/v1/activation/info` com Bearer +
   `x-integration-key` = chave do contador → escritório, cliente e CNPJ.
   → O portal confere antes de ativar e **pausa** se o CNPJ for de outra empresa.
4. **Gerar a integrationKey:** `POST .../integration/v1/activation/enable` (mesmos headers).
   → Feito 1x por empresa; a integrationKey fica cifrada e é reusada.
5. **Envio:** `POST .../invoice/v3/batches`, Bearer + `x-integration-key` = **integrationKey**
   (nunca a chave do contador); form-data `file[]` (XML), `query` `{"boxeFile": false}`
   (`true` só se o escritório tem BOX-e contratado), `fileComplement[]` opcional.
   Resposta: "Aguardando processamento".
6. **Consulta:** `GET .../invoice/v3/batches/{id}` com os mesmos headers.
   "Arquivo armazenado na API" = sucesso → estado CONFIRMADO.
7. **TLS:** 1.3 (ou 1.2 com ECDHE-GCM/CHACHA20) — o Python/OpenSSL da imagem atende.
8. **Homologação:** pedir à Domínio (e-mail ou WhatsApp da API) uma chave de homologação,
   informando um CNPJ de exemplo.

O código extraído do EXE CentralNFSe **não** usa Client ID/Secret: ele dizia "no Domínio OAuth
credentials in the EXE" e mandava os XMLs a um gateway na AWS, onde ficava a credencial. O
Portal Fiscal faz o papel desse gateway, direto do servidor.

## Verificado na API real (2026-11-09, sem credencial)
- Rotas `invoice/v2|v3/batches`, `status/v2/integration/info` e `integration/v1/activation/*`
  existem; todas exigem token (só a chave do cliente não basta).

## Ainda sem documentação oficial
| Item | Env | Hoje |
|---|---|---|
| Códigos de erro do lote (só as mensagens de sucesso/aguardando estão no guia) | `DOMINIO_CODIGOS` (JSON código→estado) | `EA10`=JA_EXISTENTE |
| NFS-e Padrão Nacional aceita sem conversão? | — | enviado como está |
| O limite "acima de 40" é de tokens ou de todas as requisições? | `DOMINIO_LOTE_CICLO`, `DOMINIO_INTERVALO_SEG` | 5 XML a cada 15 s |

## Credenciais
Admin → Envio Domínio → "Credenciais do integrador": Client ID e Client Secret (secret cifrado
com `DOMINIO_KEY`). As variáveis `DOMINIO_CLIENT_ID`/`DOMINIO_CLIENT_SECRET` do EasyPanel, se
existirem, têm prioridade.

Enquanto a tabela de códigos não vier, **nenhum lote vira CONFIRMADO**: fica ENVIADO e, depois de
40 consultas sem código conhecido, vira INCERTO.

## Liberação sugerida
1. Fase 0: `Testar conexão`, 1 NF-e e 1 NFS-e de um cliente; registrar as respostas e preencher
   `DOMINIO_CODIGOS`.
2. Ligar só o cliente piloto (data inicial = hoje) e conferir no Domínio por alguns dias.
3. Importar as chaves dos demais (CSV `CNPJ;chave`) e ligar por carteira.

`DOMINIO_SIMULADO=1` mostra a tela e a fila funcionando localmente sem enviar nada.
