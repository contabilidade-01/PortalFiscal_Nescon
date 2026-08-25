# Portal Fiscal Nescon — Sistema Unificado NFe + NFSe · HANDOFF

> Documento para a **próxima IA** (ou dev) assumir o turno. Explica o que foi feito,
> como chegamos aqui, o estado atual e o que falta — por fase.
> **Última atualização:** 2026-07-20 (Etapa 16 — ICMS-ST/segregação por CST/CSOSN nas saídas;
> Etapa 15 — Mensuração 3-fontes; Etapa 14 — Estimativa por Compras; Etapa 13 — Economia monofásicos).

---

## 1. O que é

Sistema **web único** (Flask) que unifica dois puxadores num só, com **uma base de clientes**:
- **NF-e** — Distribuição DF-e da SEFAZ (SOAP/mTLS). *Comprovado com dados reais.*
- **NFS-e** — Portal Nacional / ADN (REST/mTLS). *Motor portado do projeto recuperado; falta validar formato de resposta real.*

Abas NF-e/NFS-e, flags por cliente (`puxa_nfe`, `puxa_nfse`), método de saída, download por competência (ZIP), multiusuário (base).

**Roda local** em `http://localhost:5001`. Pronto para servidor depois.
**Pasta:** `TESTENFE/PortalFiscal_Nescon/`. Preserva intactos `PuxadorNFe_Web/` e `PORTAL NACIONAL NFSE/`.

---

## 2. Como chegamos aqui (contexto essencial — fatos COMPROVADOS)

1. **Bloqueio 403 não era SEFAZ** — era a chave **CNG/RC2 legado** do A1. Solução: **OpenSSL** (cryptography), **nunca .NET/Schannel**. → `engines/certs.py`.
2. **NF-e entradas provado:** 750 XML reais do Queijeiro (CNPJ 26786637000149).
3. **Janela de 3 meses** confirmada (consChNFe de fevereiro → `cStat 632`). Por isso **rodar diariamente**.
4. **Anti-656:** 20 consultas/h por CNPJ (distNSU); parar no `cStat 137`; cooldown 1h. Embutido nos motores.
5. **Saída (venda) NÃO vem pelo cert do emitente** (`cStat 641`). Só via **`autXML`** (terceiro autorizado) — **provado:** 14 vendas da CH DA SILVA (R$ 257 mil em junho) puxadas com o cert da **Nescon** (35736034000123).
6. **NFS-e = irmã da NFe:** mesmo A1, mesmo modelo NSU, mesmo base64+gzip. Muda só o protocolo (REST `adn.nfse.gov.br/contribuintes/DFe/{nsu}`, 404=fim, 429=rate limit).

Detalhes completos em `../DOCUMENTACAO_PUXADOR_NFE.md`.

---

## 3. Arquitetura

```
PortalFiscal_Nescon/
├── app.py                 # Flask: login, painel(abas), clientes(flags), execução, download
├── models.py              # SQLite ÚNICO: usuarios, empresas(+flags), parametros, execucoes
├── config.json            # portas, limites NFe/NFSe, base_url ADN
├── import_clientes.py     # Fase 1: importa 90→85 clientes do GClick (só leitura)
├── engines/
│   ├── certs.py           # A1 compartilhado (PFX→PEM via cryptography). Base de tudo.
│   ├── nfe.py             # motor NFe (SOAP/SEFAZ) — entradas + saídas(escritório autXML)
│   └── nfse.py            # motor NFSe (ADN REST) — VER §6 (validar resposta real)
├── templates/             # base.html (design system), login, dashboard(abas), clientes
├── Certificados/          # .pfx enviados pelo app
├── XML/<cnpj>/<AAAA-MM>/{NFe/{01_entrada,02_resumo,03_eventos,04_saida}, NFse}/
├── portal_fiscal.db       # banco (gerado)
└── HANDOFF.md             # este arquivo
```

**Base de clientes ÚNICA** = tabela `empresas` (serve NFe e NFSe com o mesmo cert):
`cnpj, nome, uf, cuf, arquivo, senha, senha_ok, validade, ativo,` **`puxa_nfe, puxa_nfse, metodo_saida`** `, emissor, marketplace, ultnsu_nfe, ultnsu_nfse, bloqueado_nfe_ate, bloqueado_nfse_ate, total_nfe, total_nfse, whatsapp, responsavel, email, obs`.

---

## 4. Fases — estado

| Fase | Descrição | Estado |
|---|---|---|
| 0 | Descoberta (NFe + NFSe entendidos) | ✅ |
| 1 | Sistema novo + base única + import 85 clientes | ✅ |
| 2 | Motor NFe (portado, comprovado) | ✅ |
| 3 | Motor NFSe (ADN REST) | ✅ VALIDADO — 25 NFSe reais puxadas (ALINHAR), formato confirmado |
| 4 | Web com abas NFe/NFSe + base c/ flags | ✅ (verificado via HTTP) |
| 5 | Vínculo de certificados em massa | 🟡 parcial — 22/85 (26%). Bloqueio: 59 dos 114 .pfx são **cloud-only** (OneDrive) |
| 6 | Download por competência ✅ + agendamento diário ✅; Ciência 210210 + DANFSE | 🟡 parcial |
| 7 | Segurança/LGPD + deploy servidor + Domínio API | ⬜ pendente |
| 8 | **NFC-e (SP)** — SOAP SEFAZ-SP + UI Configurações (data ANO-MÊS-DIA + limite) + aba Painel | ✅ FEITO |
| 9 | **Conferência Fiscal + Auditoria de Numeração** — /fiscal/conferencia (qtd+valor) + /fiscal/auditoria (quebras nNF, só NFes emitidas, canceladas contam) + "Forçar NSU inicial" | ✅ FEITO |
| 10 | **Experiência do Usuário (estagiário)** — Ajuda/glossário, Painel orientador, filtros em Downloads, período em Execuções, Conferência com filtros+resumo+CSV, Auditoria como dashboard c/ drill-down | ✅ FEITO |
| 11 | **Saídas NF-e por autXML** — corrigido: saída = BUSCA (cert Nescon), não consChNFe. 14 vendas CH DA SILVA baixadas | ✅ FEITO |
| 12 | **Faturamento por CFOP** — /fiscal/faturamento (isola base tributável de remessa/retorno/brinde) | ✅ FEITO |
| 13 | **Economia Fiscal (monofásicos)** — /fiscal/economia (projeção PIS/COFINS por NCM) + Anexo/RBT12 no cadastro + menu agrupado | ✅ FEITO |
| 14 | **Estimativa por Compras (rolling 12m)** — toggle venda real × estimativa; fix motor (separa entrada/saída pelo emit/dest); pasta 05_propria p/ escritório | ✅ FEITO |
| 15 | **Mensuração 3-fontes (Extrato do Simples)** — /fiscal/economia/mensuracao cruza vendas + compras + PGDAS-D (PDF importado); semáforo verde/amarelo/vermelho | ✅ FEITO |
| 16 | **ICMS-ST (segregação)** — /fiscal/economia/st detecta receita com ST por CST/CSOSN (102/500/201) nas saídas reais; projeção ICMS-ST a recolher fora do DAS + economia na parcela ICMS do DAS | ✅ FEITO |

### Detalhes rápidos das Etapas 8, 9 e 10

- **Etapa 8 (NFC-e SP):** motor `engines/nfce_sp.py` validado com a Sandra (CNPJ 08108132000143, 478 chaves listadas + XML real baixado, modelo 65). Configurações editáveis em `/configuracoes` (ANO/MÊS/DIA + limite 500). Disparo por "Listar e baixar NFC-e" no Painel (aba NFC-e) ou `/run/nfce`. Subpasta `XML/<cnpj>/<comp>/NFCe/01_venda/`.
- **Etapa 9 (Conferência + Auditoria):** `engines/conferencia.py` (novo) parseia os XMLs em disco. Rotas:
  - `/fiscal/conferencia` — tabela por Empresa × Competência × Tipo (qtd e valor de não-canceladas para tributação).
  - `/fiscal/auditoria` — tabela Empresa × Série × Mês com cálculo de quebras de nNF. **Escopo: apenas NFes EMITIDAS pela própria empresa** (NFe/04_saida, NFCe/01_venda, NFSe/02_prestado). Entradas (compras) e tomadas (serviços contratados) **não entram**. **Canceladas contam na sequência** (nNF reservado pela SEFAZ mesmo após cancelamento).
  - `POST /clientes/<id>/forcar-nsu` — salva NSU inicial 15 dígitos + flag; motor `nfe.puxar_entradas` usa uma vez e zera a flag.
- Sandra 2026-07 (conferência): NFCe 478 / R$ 45.925,97; NFSe 5 / R$ 259,64; NFe 14 / R$ 18.975,64.
- Sandra NFCe série 2 (auditoria): 478 no range (476 válidas + 2 canceladas), min 2841, max 3320, esperados 480, faltam 0.
- Smoke HTTP: 15/15 verde. Tempos: conferência 4s, auditoria 2.7–3.5s (82 CNPJs).
- Commits recentes: `4e02021` (Etapa 8), `5a500b4` (Etapa 9 base — próxima commit vai cobrir a correção do escopo da auditoria).
- **Etapa 10 (UX p/ estagiário):** página `/ajuda` (glossário + 4 passos + "o que cada tela faz"); Painel com faixa "3 passos" e botões em linguagem de negócio ("Puxar entradas (compras)"/"Puxar saídas (vendas)"); **Downloads** com filtros globais empresa/ano/mês/documento (`/download` e `/xml/excluir` respeitam ano/mês quando comp=TODAS); **Execuções** com bloco dinâmico "O que será baixado" (período por tipo); **Conferência** com filtros Empresa/Ano/Mês + cards-resumo + coluna "Movimento" + Exportar CSV (`?formato=csv`); **Auditoria** virou dashboard: cards-resumo + chips das empresas com divergência (âncora `#emp-<cnpj>`) + cards expansíveis (mostrar "só com divergência" por padrão). Menu ganhou Ajuda/Conferência/Auditoria no bloco geral (são `login_req`). `.claude/launch.json` criado. Validado no navegador com dados reais (Auditoria: 4 empresas com divergência; Conferência Sandra 2026-07: 497 notas / R$ 65.161,25). **Pendente:** commit da Etapa 10.

---

## 5. Como rodar

```bat
cd PortalFiscal_Nescon
python import_clientes.py         REM (1ª vez) importa os clientes do GClick
python app.py                     REM sobe em http://localhost:5001
```
Login inicial: **admin / admin**. Deps: flask, requests, cryptography, werkzeug (já instalados).

**Fluxo de uso:**
1. Aba **Clientes** → *Vincular em lote* apontando a pasta de certificados da Nescon (`...\0007_CERTIFICADO DIGITAL\Clientes`) → casa cada .pfx com a empresa pelo CNPJ.
2. Marcar em cada cliente **NF-e / NFS-e** e o **método de saída**.
3. Painel → aba **NF-e**: *Puxar entradas* (todas com cert) e *Puxar saídas* (configurar cert do escritório).
4. Painel → aba **NFS-e**: *Puxar NFS-e*.
5. Download por competência: endpoint `/download?cnpj=&comp=AAAA-MM&tipo=NFe|NFse` (falta botão na UI — Fase 6).

---

## 6. ✅ NFSe — VALIDADO (Fase 3)

`engines/nfse.py` chama `GET https://adn.nfse.gov.br/contribuintes/DFe/{nsu}` com mTLS (mesmo A1). **Confirmado com dados reais (2026-07-10):**
- Resposta JSON: `{"StatusProcessamento":"DOCUMENTOS_LOCALIZADOS","LoteDFe":[{"NSU":1,"ChaveAcesso":"...","TipoDocumento":"...","ArquivoXml":"<base64+gzip>","DataHoraGeracao":"..."}]}`. O parser `_extrair_docs` já lê `LoteDFe`/`NSU`/`ArquivoXml` corretamente.
- Teste: **ALINHAR → 25 NFSe reais** salvas por competência (`<?xml...><NFSe xmlns="http://www.sped.fazenda.gov.br...">`); 7 de 8 empresas testadas retornaram 200 (a maioria tem NFSe).
- **HTTP 429** = rate limit (o motor já respeita, backoff/Retry-After).
- **DESCOBERTA:** a NFSe nacional **NÃO tem a janela de 3 meses** da NF-e — a ALINHAR trouxe docs de **2023**. Ou seja, dá para puxar **histórico completo** de NFSe (diferente da NF-e). Menos urgência de rodar diário para NFSe.

---

## 7. TODO para a próxima IA (Fases 5–7)

**Fase 5 — Cadastro/certificados** (parcial: 22/85 já vinculados via `vincular_certificados.py`)
- [ ] **BLOQUEIO PRINCIPAL:** 59 dos 114 .pfx estão **cloud-only no OneDrive** (placeholders) e não abrem. Fix: no Explorer, botão direito na pasta `0007_CERTIFICADO DIGITAL` → **"Sempre manter neste dispositivo"** → aguardar baixar → rodar `vincular_certificados.py` de novo. A cobertura deve saltar.
- [ ] 11 .pfx abriram mas a senha não estava no nome — precisam senha manual (tela Clientes → Vincular).
- [ ] Empresas da base sem .pfx nas pastas → upload manual ou pedir o certificado ao cliente.
- [ ] Cliente **"só-venda"**: empresa sem cert (recebe vendas via autXML do escritório) aparecer no download.
- [ ] Editar contato/observação; importar coluna `emissor`/`metodo_saida` da planilha `Triagem_autXML_Clientes.xlsx`.

**Fase 6 — Operação**
- [x] **Download por competência** — página `/downloads` + endpoint `/download?cnpj=&comp=&tipo=NFe|NFse` (ZIP). Verificado.
- [x] **Agendamento diário** — `run_diario.py` + Tarefa Agendada do Windows `PortalFiscalNescon` (diária 06:00, ATIVA). Recriar/ajustar: `Agendar.bat`. Roda entradas NFe + saídas escritório + NFSe de todas as empresas prontas. Log em `logs/run_diario.log`. Roda quando o usuário está logado (para rodar deslogado, adicionar `/ru`+credenciais).
- [x] **Ciência automática (210210)** — `engines/ciencia.py` FUNCIONA. Assina o evento com **`xmlsec`** (libxmlsec1, RSA-SHA1 + C14N inclusiva, byte-compatível com o `.NET SignedXml`), envia ao **NFeRecepcaoEvento4 (AN, `www.nfe`)**. SEFAZ ACEITA: testes reais no Queijeiro deram `cStat 128` (lote) + `573` (duplicidade — as notas já tinham ciência) → prova que a assinatura passa (inválida daria 297). Caminho resolvido: operação `nfeRecepcaoEventoNF`; corpo `<nfeDadosMsg>` direto (document/literal); `verificar_servidor=False` (cadeia incompleta do www.nfe). **Descoberta:** `signxml` bloqueia SHA1 (que a NF-e exige) — usar `xmlsec`. **Wiring no robô diário: FEITO** — `ciencia.dar_ciencia_pendentes(emp)` varre os resumos sem procNFe completo, manifesta (dedup na tabela `ciencia_dada`), e o `run_diario.py` chama para cada empresa após as entradas. Testado: 1ª rodada manifesta, 2ª rodada 0 (dedup). A varredura do dia seguinte traz o XML completo das notas manifestadas.
- [ ] **DANFSE PDF**: portar `danfse_service.py` do projeto recuperado (reportlab).

**Fase 7 — Produção**
- [ ] **Segurança/LGPD:** hoje as senhas dos certs ficam em texto (no nome do arquivo e no banco). Redesenhar custódia (cofre criptografado, senha fora do nome).
- [ ] **Deploy servidor:** trocar `app.run` por **waitress** (`waitress-serve --port=5001 app:app`); secret_key por env; HTTPS.
- [ ] **Multiusuário com permissão por empresa** (hoje todos veem tudo).
- [ ] **Integração Domínio (API)** e cópia para pasta local do PC (config `pasta_local_extra`).

---

## 8. Regras de ouro (não quebrar)
- **Nunca** usar .NET/Schannel para o A1 — só OpenSSL/cryptography.
- **Nunca** zerar `ultnsu_*` sem motivo — sempre continuar de onde parou.
- **Parar no 137/404** e respeitar cooldown — não martelar a SEFAZ/ADN.
- **Saída de NF-e** só via `autXML` (cert do escritório). Cert do próprio cliente dá 641.
- Preservar `PuxadorNFe_Web/` e `PORTAL NACIONAL NFSE/` — este projeto é **novo e separado**.
