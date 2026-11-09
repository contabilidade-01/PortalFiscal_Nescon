# Envio de XML para a Domínio (API Onvio)

Estado em 2026-11-09: **fila, tela e cliente da API prontos; não testados contra a API real.**
Faltam o `client_id`/`client_secret` do integrador (pedidos a `api.dominio@thomsonreuters.com`)
e as respostas às dúvidas abaixo.

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

## A confirmar com a Domínio (tudo ajustável por env, sem mudar código)
| Dúvida | Env | Padrão hoje |
|---|---|---|
| Grant `client_credentials` e `audience` | `DOMINIO_AUDIENCE` | sem audience |
| Versão do envio (v2 público × v3 do guia) | `DOMINIO_INVOICE_VERSAO` | `v3` |
| Nome do header da chave | `DOMINIO_HEADER_CHAVE` | `x-integration-key` |
| Trocar a chave em `activation/enable`? | `DOMINIO_ATIVAR_CHAVE` | `0` (ainda não ligado na fila) |
| `boxeFile` | `DOMINIO_BOXE_FILE` | `0` |
| Tabela de códigos de retorno | `DOMINIO_CODIGOS` (JSON código→estado) | só `EA10`=JA_EXISTENTE |
| NFS-e Padrão Nacional aceita sem conversão? | — | enviado como está |

Enquanto a tabela de códigos não vier, **nenhum lote vira CONFIRMADO**: fica ENVIADO e, depois de
40 consultas sem código conhecido, vira INCERTO.

## Liberação sugerida
1. Fase 0: `Testar conexão`, 1 NF-e e 1 NFS-e de um cliente; registrar as respostas e preencher
   `DOMINIO_CODIGOS`.
2. Ligar só o cliente piloto (data inicial = hoje) e conferir no Domínio por alguns dias.
3. Importar as chaves dos demais (CSV `CNPJ;chave`) e ligar por carteira.

`DOMINIO_SIMULADO=1` mostra a tela e a fila funcionando localmente sem enviar nada.
