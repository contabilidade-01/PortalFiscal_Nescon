# PROGRESSO — Refatoração para Plataforma (Portal Fiscal Nescon)

> **Documento vivo.** Atualizar ao FIM de cada etapa. Se o turno acabar, a próxima IA
> lê isto + `HANDOFF.md` e continua da próxima etapa pendente.
> **Regra:** não mexer no código de forma abrupta. Cada etapa: implementar → testar → registrar aqui.
> Pasta do sistema: `TESTENFE/PortalFiscal_Nescon`. Python: `C:\Users\parce\AppData\Local\Programs\Python\Python313\python.exe`.
> App (dev): `start.bat` → localhost:5001, login admin/admin.

> **📌 Avanços recentes (resumo)**
> - **Etapa 16** (2026-07-20) — **ICMS-ST (segregação)**: `/fiscal/economia/st` detecta receita com
>   **ICMS-ST** nas saídas reais via **CST** (10/30/60/70/90, regime normal) ou **CSOSN**
>   (201/202/500/900, Simples). Projeta **ICMS-ST a recolher fora do DAS** (GIA-ST estadual) +
>   **economia na parcela ICMS** do DAS. Alíquota interna extraída do `<ICMS00><pICMS>` ou
>   da UF do cadastro. Validação real: **Sandra** (lanchonete, SP) — 227 NFCe com
>   CSOSN 500 → R$ 6.391,67 receita ST (17,3%), R$ 1.150,50 ICMS-ST a recolher, R$ 368,16
>   economia/mês no DAS. Sandra é o caso clássico de ST mal segregada.
> - **Etapa 15** (2026-07-16) — **Mensuração 3-fontes (Extrato do Simples)**: `/fiscal/economia/mensuracao`
>   cruza **vendas reais** (NF/NFCe saída) × **compras reais** (rolling 12m) × **Extrato do Simples**
>   (PDF do Recibo PGDAS-D importado). Receita com hierarquia PGDAS > venda > markup. Semáforo
>   verde/amarelo/vermelho mostra confiança. Rota `/importar/pgdas` (admin) faz upload do PDF,
>   extrai CNPJ, período, receita, anexo, RBT12, DAS, e atualiza cadastro se faltar.
>   Validação real: **Sandra** (R$ 203.263 PGDAS + vendas 0% mono) → vermelho (vendas confirmam
>   que ela não vende monofásico). **CH DA SILVA** (R$ 115k vendas reais, têxtil) → verde
>   (cruzamento confirma 0% monofásico).
> - **Etapa 14** (2026-07-16) — **Estimativa de Economia por Compras (rolling 12m)**:
>   `/fiscal/economia?modo=estimativa` calcula % monofásico pelas **compras reais**
>   (NFs onde o cliente é destinatário), receita com fallback PGDAS → venda própria →
>   markup 1,5. Toggle "Pela venda real" vs "Pela estimativa de compras".
>   **Investigação estrutural**: a pasta `01_entrada` está correta (compra real,
>   destinatário == cnpj) — não havia bug; a separação pelo CFOP estava confundindo
>   direção do emitente com a do destinatário. **Fix no motor `nfe.py`**: vendas próprias
>   (cert do cliente) vão para `04_saida`, vendas do escritório vão para `04_saida/<emit>`,
>   emissões próprias do escritório vão para `05_propria`. Subpastas auditáveis.
> - **Etapa 13** (2026-07-16) — **Economia Fiscal (monofásicos)**: `/fiscal/economia` projeta a economia
>   de PIS/COFINS (Simples, segregação de receitas) identificando produtos por **NCM** sobre as saídas
>   reais; cadastro ganhou **Anexo + RBT12**; **menu reagrupado** em dropdowns (Fiscal/Análises/Admin).
> - **Etapa 12** (2026-07-16) — **Faturamento por CFOP** (`/fiscal/faturamento`): isola a base de
>   tributação (venda) de remessa/retorno/brinde/devolução/transferência.
> - **Etapa 11** (2026-07-16) — **Saídas NF-e = BUSCA por autXML** (não consChNFe); 14 vendas da
>   CH DA SILVA baixadas (cert Nescon); marcada `metodo_saida=autXML`.
> - **Etapa 10** (2026-07-15, ✅ CONCLUÍDA) — **Experiência do usuário (pensada para estagiário)**: página **Ajuda/glossário**, Painel orientador (3 passos + rótulos de negócio), Downloads com filtros globais (empresa/ano/mês/doc), Execuções mostrando o período/competência por tipo, Conferência com filtros Ano/Mês/Empresa + cards-resumo + CSV, Auditoria como **dashboard de divergências** com chips clicáveis e drill-down. Validado no navegador com dados reais. Plano e log no fim deste arquivo.
> - **Etapa 9** (2026-07-15) — `/fiscal/conferencia` (qtd+valor não canceladas) + `/fiscal/auditoria` (quebras de nNF, **somente NFes EMITIDAS** pela própria empresa, **canceladas contam na sequência**) + ação **Forçar NSU inicial** em cliente sem demarcação confiável.
> - **Etapa 8** (2026-07-15) — NFC-e (SP) integrada. Motor SOAP SEFAZ-SP validado com a Sandra (478 chaves, download real). UI Configurações (data ANO-MÊS-DIA + limite 500) + aba Painel + bloco Downloads.
> - **Etapa 7** — Gestão de Execuções + Protocolos (nProt da Ciência 210210).
> - Servidor atual: `http://localhost:5001`, login `admin/admin`. Commit mais recente: `5a500b4` (Etapa 9 base). Próximo commit cobrirá a correção do escopo da auditoria (apenas emitidas + canceladas contam).

## Objetivo
Transformar o "puxador de NF" numa **plataforma modular**: puxador vira UM módulo; abre espaço
para DAS e outros. Com: RBAC (admin cria usuários, escopo por empresa), jobs em background com
status visível (roda navegando/deslogado), anti-bloqueio profissional, segurança de produção,
e deploy no EasyPanel (como o GClick — app separado no mesmo VPS).

## Etapas
| # | Etapa | Status |
|---|---|---|
| 0 | `models.py` reescrito: RBAC + tabela `jobs` + `consultas_log` + DATA_DIR + WAL | ✅ FEITO |
| 1 | `worker.py` (fila de jobs) + integrar no `app.py` (jobs no lugar de threads) + `/status` + barra de status ao vivo | ✅ FEITO |
| 2 | RBAC no `app.py`: `admin_required`, escopo nas queries, tela **Usuários** (criar/editar/excluir, papel, empresas) | ✅ FEITO |
| 3 | **Menu modular** (Fiscal / DAS / Admin) + módulo **DAS** placeholder | ✅ FEITO |
| 4 | Segurança: `secret_key`/env, cookies, forçar troca do admin | ✅ FEITO |
| 4b | **Criptografar senha do cert** no banco (migração cuidadosa) | ⬜ (ver plano etapa 4, adiado com segurança) |
| 5 | Git prep: `.gitignore`, `Dockerfile` (Flask+waitress+libxmlsec1), `.dockerignore`, `requirements.txt`, `README_DEPLOY.md` | ✅ FEITO |
| 6 | Testes finais + `git init` + primeiro commit (pronto p/ push) | ✅ FEITO |
| 7 | **Gestão de Execuções** (tela `/execucoes`: jobs+resultados, nova execução, cancelar) + **Protocolos** (captura `nProt` da Ciência + tela `/protocolos`) | ✅ FEITO |
| 8 | **Integração NFC-e (SP)** — motor SOAP SEFAZ-SP + fluxo "todas marcadas" + UI Configurações (data ANO-MÊS-DIA + limite) + aba Painel + bloco Downloads | ✅ FEITO |
| 9 | **Conferência Fiscal + Auditoria de Numeração** — /fiscal/conferencia (qtd+valor) + /fiscal/auditoria (quebras nNF, **só NFes emitidas**, canceladas contam na sequência) + "Forçar NSU inicial" para empresas sem demarcação confiável | ✅ FEITO |
| 10 | **Experiência do Usuário (para estagiário)** — linguagem clara + Ajuda/glossário, Painel orientador, Downloads com filtros, Execuções com período visível, Conferência com filtros dinâmicos + resumo, Auditoria como dashboard com drill-down | ✅ FEITO |
| 11 | **Saídas NF-e por autXML** (BUSCA via cert Nescon) | ✅ FEITO |
| 12 | **Faturamento por CFOP** — /fiscal/faturamento isola base de tributação (venda) | ✅ FEITO |
| 13 | **Economia Fiscal (monofásicos)** — /fiscal/economia (projeção PIS/COFINS por NCM) + Anexo/RBT12 + menu dropdowns | ✅ FEITO |
| 14 | **Estimativa por Compras (rolling 12m)** — toggle venda × estimativa; fix motor (separa entrada/saída por emit/dest); pasta 05_propria para escritório | ✅ FEITO |
| 15 | **Mensuração 3-fontes (Extrato do Simples)** — `/fiscal/economia/mensuracao` cruza vendas + compras + PGDAS-D (PDF importado); semáforo verde/amarelo/vermelho | ✅ FEITO |
| 16 | **ICMS-ST (segregação)** — `/fiscal/economia/st` detecta receita com ST por CST/CSOSN (10/30/60/70/90 + 201/202/500/900); projeção ICMS-ST a recolher fora do DAS + economia na parcela ICMS | ✅ FEITO |

**Etapa 7 (feita):** `models.py` — coluna `ciencia_dada.nProt` (migração `_add_col`) + `ciencia_registrar(...,nProt)`.
`ciencia.py` — `dar_ciencia_pendentes` extrai `<nProt>` da resposta e grava. `app.py` — rotas `/execucoes`,
`/execucoes/nova` (enfileira por tipo), `/jobs/<id>/cancelar` (só job em fila), `/protocolos` (lista ciencia_dada+empresa).
Templates `execucoes.html` (nova execução + tabela de jobs c/ cancelar + resultados por empresa) e `protocolos.html`
(KPIs + tabela com nProt). Menu admin: Execuções + Protocolos. TESTADO: migração ok, telas 200, nova execução
enfileirou e rodou (job 'ok'), 3 registros de ciência exibidos. Obs: distribuição (puxar) NÃO gera protocolo;
só a Ciência (evento 210210) gera `nProt`.

**Etapa 8 (feita):** Motor `engines/nfce_sp.py` — SOAP SEFAZ-SP com mTLS (cert_path → PEM temporário),
listagem (`retNfceListagemChaves`/`cStat 100` → `<chNFCe>`) e download (`retNfceDownload` → XML modelo 65).
VALIDADO com a Sandra (82 chaves em 2 dias; download real de 6.452 bytes). XMLs gravados em
`XML/<cnpj>/<competência>/NFCe/01_venda/` casando com o padrão de download/exclusão existente.
Fluxo UI (cadastrar → listar → baixar de todas):
  • `models.py` coluna `empresas.puxa_nfce` (checkbox "Puxa NFC-e") + persistência nas 3 rotas de save.
  • `worker.py` job `nfce` itera empresas com `puxa_nfce=1`, lê `ultima_execucao.data_inicial_nfce`
    e `limite_nfce`, chama `puxar_nfce(...)` e grava `ultima_execucao` ao final.
  • `app.py` rota `POST /run/nfce` (admin) enfileira o job; `/execucoes/nova` aceita `tipo=nfce`.
  • Tela **Configurações** (`/configuracoes` GET/POST) edita `data_inicial_nfce` (ANO/MÊS/DIA) e
    `limite_nfce` (padrão 500) — link "Configurações" no menu admin.
  • Painel: KPI "Puxam NFC-e", aba NFC-e com execuções e botão "Listar e baixar NFC-e".
  • Downloads: bloco NFC-e por competência (ZIP + excluir) ao lado de NF-e/NFS-e.
  • `execucoes.html`: opção **"Só NFC-e (varejo, SP)"** no seletor de nova execução.
Sintaxe OK. Smoke test (DB scratch isolado, 12 verificações): todas as rotas 200; flag `puxa_nfce`
persistida em `empresas.puxa_nfce=1`; job enfileirado; telas com bloco NFC-e renderizam.
Obs.: para o worker realmente baixar é preciso ter cert PFX vinculado à empresa (não foi exercitado
no smoke — engine já validada com a Sandra em produção).

**Etapa 9 (feita):**
- `models.py`: colunas `empresas.forcar_nsu_nfe` e `empresas.nsu_inicial_forcado` (NSU 15 dígitos)
  via `_add_col` (migração automática).
- `engines/nfe.py:puxar_entradas`: se `emp['forcar_nsu_nfe']=1`, usa `nsu_inicial_forcado` como
  `ult` inicial e zera a flag após a execução (uso único, não fica preso). Anota `[forcado]`
  no detalhe da execução.
- `engines/conferencia.py` (NOVO): parser tolerante de NF-e/NFC-e (nfeProc) e NFS-e
  (CompNfse/DPS/vários layouts). Funções:
  - `conferencia(cnpjs, ano_mes, incluir_canceladas)` → lista `[cnpj, competencia, modelo,
    sub, qtd, valor, canceladas]`. Considera apenas não-canceladas para o total (cStat≠110).
  - `auditoria_numeracao(cnpjs, ano_mes, modelos, contar_canceladas)` → quebra por
    `(competência, modelo, série)` apenas das **NFes EMITIDAS pela própria empresa**.
    Filtra pelas subpastas: `NFe/04_saida`, `NFCe/01_venda`, `NFSe/02_prestado`.
    Entradas (NFe/01_entrada, 02_resumo, 03_eventos) e Tomadas (NFSe/01_tomado) **não entram**.
    **Canceladas contam na sequência** (default `contar_canceladas=True`): nNF autorizado
    pela SEFAZ é reservado mesmo se a NF for cancelada — sem isso o auditor acharia
    "quebra" em todo cancelamento. Buracos (cap 200 exibidos, defesa contra MemoryError
    em range>200k).
- `app.py`: rotas
  - `/fiscal/conferencia` (login) — tabela Empresa × Competência com qtd/valor por
    tipo (NFe entrada/saída, NFSe tomado/prestado, NFCe venda), filtro por mês e checkbox
    "incluir canceladas".
  - `/fiscal/auditoria` (login) — tabela Empresa × Série × Mês, **só NFes emitidas**.
    Colunas: No range, Válidas, Cancel., De, Até, Esperadas, Faltam, Buracos.
    Filtros: mês, modelos (NFe/NFCe/NFSe). Checkbox "não contar canceladas na sequência"
    para diagnóstico extra (descobre notas autorizadas não baixadas).
  - `POST /clientes/<id>/forcar-nsu` (admin) — salva NSU inicial + flag.
- Templates: `fiscal_conferencia.html`, `fiscal_auditoria.html`. Adicionados links no menu admin.
- `cliente_form.html`: card "Forçar NSU inicial (NF-e)" com input 15 dígitos + checkbox
  "Ativar na próxima execução".
- TESTADO (Sandra): NFCe 478 notas valor R$ 45.925,97; NFSe 5 notas R$ 259,64; NFe 14
  notas R$ 18.975,64. **Auditoria NFCe série 2 da Sandra**: 478 baixadas (476 válidas +
  2 canceladas), min=2841, max=3320, esperados=480, **faltam=0 com canceladas contando**
  (notas 3059 e 3064 estão entre as baixadas como canceladas). Smoke HTTP: 15/15 verde.
  Tempos: conferência 4s, auditoria geral 3.5s, auditoria 2026-07 2.7s para 82 CNPJs.

**Decisão de escopo da auditoria (importante, ver `templates/fiscal_auditoria.html` nota):**
- "Auditoria" = detectar **notas EMITIDAS pela empresa cliente que faltam ser baixadas**.
- Por isso nunca considera NFe entradas (notas de fornecedores) nem NFS-e tomadas.
- Canceladas **contam** na sequência (a SEFAZ mantém o número reservado), mas aparecem
  destacadas na coluna "Cancel." para conferência. Se quiser auditar só autorizadas,
  marque "não contar canceladas na sequência".

## Estado atual detalhado
- **`models.py`** (ETAPA 0 FEITA): adicionou tabelas `usuario_empresas`, `jobs`, `consultas_log`;
  colunas `usuarios.todas_empresas`/`ativo` (migração `_add_col`); `DATA_DIR` (env `FISCAL_DATA_DIR`,
  default = pasta do app); WAL. Helpers novos: `empresas_visiveis_ids`, `pode_ver_empresa`,
  `pode_consultar`, `registrar_consulta`. Admin inicial usa env `ADMIN_SENHA_INICIAL` (default admin).
  ⚠️ `init_db()` precisa rodar 1x (o app chama no boot) para criar as tabelas/colunas novas.
- **`worker.py`**: JÁ ESCRITO nesta etapa (fila de jobs, thread, claim atômico, `processar_fila_ate_vazia`,
  `iniciar_worker`, `status`). FALTA: ligar no `app.py`.
- **`app.py`** (ainda NÃO tocado nesta refatoração): as rotas `run_nfe_entradas/saidas/nfse` ainda usam
  `threading.Thread`. Trocar por `worker.enfileirar(...)`. Adicionar `worker.iniciar_worker()` no boot,
  rota `/status` (JSON), e no `base.html` uma barra que faz polling do `/status`.

## Como as engines funcionam (não quebrar)
- `engines/nfe.py` (SOAP/SEFAZ, entradas+saídas), `engines/nfse.py` (ADN REST), `engines/ciencia.py`
  (evento 210210 via **xmlsec**), `engines/certs.py` (PFX→PEM via cryptography). TODAS já validadas com
  dados reais (ver HANDOFF.md). **Anti-bloqueio já embutido** nas engines (cooldown por CNPJ, cap de lotes,
  parar no 137). NÃO zerar `ultnsu_*`.
- Cobertura de certificados hoje: **29/85** empresas. `run_diario.py` = tarefa agendada 06:00 (Windows).

## Decisões / constraints
- Deploy = **app separado no EasyPanel/VPS** do GClick (não misturar no repo dele). Molde: `GCLICK/Dockerfile` + `PLANO_DEPLOY_EASYPANEL.md`.
- Dados (banco/XML/**Certificados**) NUNCA no Git → `.gitignore`. LGPD.
- SQLite + WAL basta p/ o volume; migração p/ Postgres fica isolada em `models`.
- Certificados: decisão A (VPS) vs B (híbrido on-prem) ainda EM ABERTO com o Jean.

## Log de execução (append a cada etapa)
- (etapa 0) models.py reescrito e gravado. worker.py escrito.
- (etapa 1 FEITA) `worker.py` gravado; `app.py`: `import worker` + `worker.iniciar_worker()` no boot;
  rotas `run_nfe_entradas/saidas/nfse` agora chamam `worker.enfileirar(...)` (sem threading solto);
  removido `_jobs` dict; nova rota `/status` (JSON); dashboard usa `worker.status()`.
  `base.html`: barra fixa `#jobbar` no rodapé + polling `/status` a cada 3s (visivel em qualquer tela).
  `run_diario.py` reescrito p/ enfileirar 'completo' + `processar_fila_ate_vazia`.
  TESTADO: enfileirar ciencia -> web worker processou em ~2s -> status 'ok' (0 docs). App: 19 rotas, compila.
  Servidor de teste rodou em 5001 (login admin/admin).
  PROXIMO (etapa 2): RBAC. Ver secao "Etapa 2" abaixo.

## Etapa 2 — plano detalhado (RBAC)
1. `app.py` login: guardar em session tambem `papel` e `todas` (todas_empresas). Buscar user completo.
2. Decorators: `admin_required` (papel=='admin'). `login_req` ja existe.
3. Helper `usuario_atual()` -> row do usuario logado. Filtrar listas de empresas por
   `models.empresas_visiveis_ids(user)` (None=todas) em: clientes, certificados, downloads, dashboard KPIs.
   Bloquear acoes (editar/excluir/baixar/enfileirar por-cnpj) se `not models.pode_ver_empresa`.
4. Rotas admin `/usuarios` (lista), `/usuarios/novo`, `/usuarios/<id>/editar` (papel, ativo,
   todas_empresas, multiselect de empresas -> tabela usuario_empresas), `/usuarios/<id>/excluir`.
   Nao deixar excluir/desativar o proprio admin logado nem o ultimo admin.
5. Template `usuarios.html` + `usuario_form.html` (checkbox todas_empresas + lista de empresas).
6. Menu (base.html): item "Usuarios" so para admin.
TESTAR: criar operador escopo a 1 empresa -> logar -> ve so aquela; nao acessa /usuarios.

- (etapa 2 FEITA) `app.py`: `usuario_atual()`, `admin_required`, context_processor `papel_atual`,
  helpers `_visiveis_ids/_scope/_pode_ver_cnpj`. Login guarda `papel` e checa `ativo`. Listagens
  (dashboard, clientes, certificados, downloads) filtradas por `_scope`. `/download` com guarda
  `_pode_ver_cnpj`. Rotas de gestao (run_*, cert_*, cliente_novo/editar/excluir/salvar) => `@admin_required`.
  Rotas novas: `/usuarios`, `/usuarios/novo`, `/usuarios/<id>/editar`, `/usuarios/<id>/excluir`
  (protege ultimo admin e auto-exclusao). Templates `usuarios.html` + `usuario_form.html`
  (checkbox todas_empresas + multiselect empresas). Menu: "Usuarios" so p/ admin.
  TESTADO OK: operador escopo=1 ve so 1 empresa; /usuarios e /run/* dao 302 p/ operador.
  PROXIMO (etapa 3): menu modular + DAS.

## Etapa 3 — plano (menu modular + DAS)
1. `base.html`: reagrupar o topnav em MODULOS. Sugestao simples (sem quebrar): manter os links atuais,
   mas adicionar um item **DAS** e agrupar visualmente. OU dropdowns por modulo. Manter leve.
   Itens: Fiscal(Painel/Clientes/Certificados/Downloads) · **DAS**(novo) · Admin(Usuarios) [so admin].
2. Rota `/das` + template `das.html` = placeholder "Modulo DAS — calculo e emissao (em breve)".
   Colocar como card com "em construcao" e um resumo do que fara (apurar Simples/MEI, gerar DAS).
3. Link DAS no menu (visivel a todos, ou so admin — decidir; deixar visivel a todos com aviso).
TESTAR: /das abre; menu mostra os grupos.

## Etapas seguintes (lembrete p/ proxima IA)
- Etapa 4 SEGURANCA: `app.secret_key = os.environ.get('SECRET_KEY', <fallback dev>)`;
  cookie `SESSION_COOKIE_HTTPONLY/SAMESITE='Lax'`, `SESSION_COOKIE_SECURE` se HTTPS (env FLASK_HTTPS);
  forcar troca se admin ainda usa senha 'admin' (banner + rota trocar senha); **criptografar senha do cert**:
  usar Fernet com chave em env `CERT_KEY`; migrar coluna `empresas.senha` para cifrada (helper
  encrypt/decrypt em models ou engines/certs; ponto de uso: onde le `emp['senha']` p/ abrir o PFX).
  CUIDADO: nao quebrar os 29 certs ja vinculados -> migrar suave (se decifrar falhar, tratar como texto).
- Etapa 5 GIT/DEPLOY: `.gitignore` (usar molde do GCLICK + `Certificados/` + `XML/` + `*.db*` + `.env`),
  `requirements.txt` (flask, werkzeug, requests, cryptography, lxml, xmlsec, waitress),
  `Dockerfile` (python:3.12-slim + `apt-get install -y libxml2 libxmlsec1 libxmlsec1-openssl openssl` +
  pip -r + COPY do codigo + `CMD waitress-serve --listen=0.0.0.0:8000 app:app`), `.dockerignore`
  (exclui data/XML/Certificados/.db/.env/__pycache__), `README_DEPLOY.md` espelhando o do GCLICK
  (volume em /app/data via FISCAL_DATA_DIR; env SECRET_KEY/ADMIN_SENHA_INICIAL/CERT_KEY).
  Refatorar `config.json`/paths p/ usar `models.DATA_DIR` (XML e Certificados sob DATA_DIR) — CUIDADO:
  hoje `SAIDA` vem de config.json com caminho absoluto local; em prod deve ser `DATA_DIR/XML`.
- Etapa 6: `git init` + primeiro commit (conferir que .gitignore bloqueia dados/segredos).

- (etapa 3 FEITA) rota `/das` + `das.html` (placeholder). Menu modular em `base.html`
  (Painel/Clientes/Certificados/Downloads | DAS | Usuarios[admin]) com `.navsep`. TESTADO OK.
- (etapa 4 FEITA) `secret_key` via env `SECRET_KEY`; cookies HttpOnly/SameSite=Lax/Secure(env FLASK_HTTPS);
  MAX_CONTENT_LENGTH; login detecta admin/admin -> `session['senha_padrao']` -> banner no base.html;
  rota `/trocar-senha` + `trocar_senha.html`. TESTADO OK (admin/admin preservado no dev).
  4b (senha do cert cifrada) ADIADA de proposito (evitar quebrar os 29 certs) — plano acima.
- (etapa 5 FEITA) caminhos sensiveis a env: `models.DATA_DIR` (FISCAL_DATA_DIR), `SAIDA`/CERT_DIR
  honram `FISCAL_XML_DIR`/DATA_DIR (fallback = config local, nao muda nada em dev). Edicoes em
  app.py + engines/nfe/nfse/ciencia. Criados: `.gitignore`, `requirements.txt` (+waitress),
  `Dockerfile` (python:3.12-slim + build-essential/pkg-config/libxml2-dev/libxmlsec1-dev/openssl +
  waitress-serve app:app :8000), `.dockerignore`, `README_DEPLOY.md`, `start_servidor.bat`.
  Smoke test: todas as telas 200 OK.
- (etapa 6 FEITA) `git init` na pasta do sistema. `.gitignore` conferido: NENHUM .db/.pfx/XML/.env staged.
  Primeiro commit `5a0c40d` (36 arquivos). SEM push (sem remote — Jean cria o repo no GitHub).

- (extra) **Exclusao manual de XML**: rota `/xml/excluir` (admin, reusa filtros do /download) +
  botao "Excluir" na tela Downloads (formaction/formmethod override, so admin, com confirm).
  Remove arquivos + subpasta vazia; PRESERVA o contador ultNSU. TESTADO OK (seed->excluir->sumiu; anonimo bloqueado).
- (extra) **`README.md`** master criado = sintese geral para iniciar nova conversa (aponta p/ HANDOFF/PROGRESSO/README_DEPLOY).

## >>> PROXIMOS PASSOS (para Jean / proxima IA) <<<
1. **Push:** Jean cria repo no GitHub e roda `git remote add origin <url>` + `git push -u origin main`.
2. **Etapa 4b (seguranca P1):** cifrar `empresas.senha` (Fernet + env CERT_KEY) — plano no bloco "Etapas seguintes".
3. **Deploy EasyPanel:** seguir `README_DEPLOY.md` (app separado, volume /app/data, env vars).
   ⚠️ Reapontar caminhos dos certificados p/ /app/data/Certificados (re-vincular por upload).
4. **Agendamento no servidor:** cron chamando `run_diario.py`.
5. Cobertura de certificados: baixar a pasta cloud-only `0002 - Certificado Digital` (senhas no nome) e re-vincular (29->mais).

---

## Etapa 10 — Experiência do Usuário (pensada para um ESTAGIÁRIO)

> **Persona:** um estagiário do Departamento Fiscal, que **não** conhece a fundo os termos
> (entrada/saída/tomado/prestado, NSU, cStat, série, nNF) e precisa **se situar, usar e analisar**
> sem depender de alguém explicando. Meta: a tela ensina; o certo é o caminho fácil.

### Análise dos problemas apontados (2026-07-15)
1. **Painel** — não deixa claro o significado de "NFS-e Tomados" e "NF-e de Entrada (Compras)".
   Falta linguagem de negócio ("entrada = compra que recebi", "tomado = serviço que contratei").
2. **Download por competência** — sem filtros; lista tudo de todas as empresas. Difícil achar.
3. **Execuções** — não mostra **qual período/competência** está sendo baixado. Para os tipos
   que têm janela configurável (NFC-e = desde a data em Configurações; NF-e/NFS-e = incremental por
   NSU), a tela precisa exibir isso. Tipos sem filtro tudo bem, mas avisar.
4. **Conferência Fiscal** — escolher competência numa lista enorme é ruim; **faltam filtros por
   ano, mês e empresa**; pouco visual; sem resumo (KPIs) nem exportação.
5. **Auditoria de Numeração** — deveria ser um **dashboard**: mostrar **quais empresas têm
   divergência** e, ao clicar na empresa, ir direto às discrepâncias (drill-down). Hoje é uma
   tabela crua.

### Plano em sub-etapas (cada uma: implementar → testar → registrar aqui)
- **10.1 — Linguagem clara + Ajuda/Glossário.** Macro Jinja reutilizável de "legenda"
  (`_ui.html`) com os termos de negócio. Página `/ajuda` (glossário + passo-a-passo do estagiário
  + o que é cada tela). Link **Ajuda** no menu. Tooltips consistentes.
- **10.2 — Painel orientador.** Faixa "Como usar em 3 passos" (1 Cadastrar+certificado →
  2 Puxar → 3 Conferir). Rótulos de negócio nas abas/botões (Entradas = compras; Saídas = vendas;
  Tomados = serviços contratados; Prestados = serviços prestados). Descrições curtas.
- **10.3 — Downloads com filtros globais.** Barra de filtros no topo: busca por empresa,
  Ano, Mês, Documento (NF-e/NFS-e/NFC-e). Só mostra o que casa. Contadores.
- **10.4 — Execuções com período visível.** Bloco "O que será baixado" na tela de nova execução
  (NFC-e: desde `nfce_data_inicial` até hoje, limite N; NF-e/NFS-e: incremental por NSU; Ciência:
  eventos pendentes). Coluna/ną tabela mostrando janela/competência quando aplicável.
- **10.5 — Conferência Fiscal redesenhada.** Filtros dinâmicos **Ano / Mês / Empresa**
  (derivados das competências em disco). Cards-resumo (nº de notas, valor total, canceladas).
  Layout mais limpo (agrupado por empresa, chips por tipo). Botão **Exportar CSV**.
- **10.6 — Auditoria como dashboard.** Cards-resumo (empresas auditadas, empresas COM divergência,
  séries com quebra). Lista de **empresas com divergência primeiro** (destaque). Clique na empresa
  → drill-down das séries/buracos daquela empresa (âncora/expand). Filtro Ano/Mês/Modelo. Por
  padrão mostra só problemas, com toggle "mostrar tudo".
- **10.7 — Teste + registro + abrir a página.** Smoke test das telas novas; registrar aqui;
  subir o servidor e abrir no navegador.

### Log de execução da Etapa 10 (append a cada sub-etapa)
- (planejado) 2026-07-15 — análise feita e plano registrado. Início por 10.1.
- (10.1 FEITO) 2026-07-15 — Página **Ajuda** (`templates/ajuda.html` + rota `/ajuda` login_req):
  4 passos, "as 4 palavras que confundem" (entrada/saída/tomado/prestado com ícones), glossário
  (NF-e/NFC-e/NFS-e, competência, cert A1, NSU, cStat, série/nNF, cancelada, ciência, nProt, autXML)
  e "o que cada tela faz". Menu: link **Ajuda** (todos). Conferência/Auditoria movidas do bloco admin
  para o menu geral (são login_req; operador/estagiário precisa vê-las).
- (10.2 FEITO) 2026-07-15 — Painel: faixa **"Como usar em 3 passos"** (Cadastrar→Puxar→Conferir) com
  link p/ Ajuda. Botões renomeados p/ linguagem de negócio: "Puxar entradas (compras)" / "Puxar saídas
  (vendas)"; descrições NF-e e NFS-e explicando entrada/saída/tomado/prestado + tooltips.
- (10.3 FEITO) 2026-07-15 — Downloads com **filtros globais** (empresa/ano/mês/documento) + contadores.
  `app.py`: `/downloads` filtra e coleta anos; `/download` e `/xml/excluir` aceitam `ano`/`mes` e, quando
  comp=TODAS, respeitam o filtro; forms enviam ano/mês ocultos. Selo "Todas as listadas".
- (10.4 FEITO) 2026-07-15 — Execuções: bloco dinâmico **"O que será baixado"** que muda conforme o
  tipo selecionado. `app.py:execucoes` passa `periodos` (NFC-e: desde `nfce_data_inicial` até hoje +
  limite; NF-e/NFS-e: incremental por NSU; Ciência: pendentes). Rótulos do seletor mais claros.
- (10.5 FEITO) 2026-07-15 — **Conferência** redesenhada: filtros **Empresa / Ano / Mês** (derivados das
  competências em disco; caminho rápido ano+mês → motor, senão filtra por ano/mês na rota) + checkbox
  canceladas. **Cards-resumo** (notas, valor total, empresas, canceladas). Layout agrupado por empresa
  com **subtotais**; coluna "Movimento" traduz a subpasta (Entrada/Saída/Tomado/Prestado/Venda).
  **Exportar CSV** (`?formato=csv`, `;`, BOM p/ Excel-pt).
- (10.6 FEITO) 2026-07-15 — **Auditoria vira dashboard**: cards-resumo (empresas auditadas, com
  divergência, séries com quebra, números faltando). Faixa de **chips das empresas com divergência**
  (clique → âncora `#emp-<cnpj>` e abre o card). Detalhe por empresa em **cards expansíveis**
  (com quebra abrem por padrão). Filtros Empresa/Ano/Mês/Modelos + **Mostrar: só com divergência
  (padrão) / todas**. `app.py:fiscal_auditoria` agrupa por empresa, ordena com-quebra primeiro.
- (10.7 FEITO) 2026-07-15 — Testes: sintaxe OK (app/models/worker/nfce_sp/conferencia). Smoke UX
  (banco+XML scratch, NFC-e com buraco nNF=4 e 1 cancelada): 11 verificações verdes — /ajuda, /,
  /downloads (+filtros), /execucoes ("O que será baixado"), /fiscal/conferencia (valor 100,00),
  /fiscal/auditoria (drill-down + buraco), CSV. **Validado no navegador com dados REAIS** (85 empresas):
  Auditoria → 13 auditadas, 4 com divergência (Claudemir, Kalmaq, Sandra, Ueriton) como chips clicáveis,
  cards das problemáticas abertos. Conferência (Sandra 2026-07) → 497 notas / R$ 65.161,25; linhas
  NFC-e Venda 478 (R$ 45.925,97), NFS-e Tomado 4 / Prestado 1, NF-e Entrada 14. Servidor de pé em
  http://localhost:5001 (novo, com o código da Etapa 10). Criado `.claude/launch.json` (portal-fiscal).
- **ETAPA 10 CONCLUÍDA.** Pendente p/ próxima IA: commit da Etapa 10; se desejar, quebra da nav em
  dropdowns por módulo (o topnav ficou com muitos itens) e um "onboarding" dispensável (localStorage).
- (10.2b FEITO) 2026-07-15 — Painel agora mostra a **quebra por MOVIMENTO** em cada aba (faixa
  `.movbar`): NF-e → Entradas (compras) × Saídas (vendas) × Resumos/eventos; NFS-e → Tomados
  (contratados) × Prestados; NFC-e → Vendas (varejo). `app.py:_contagem_movimentos(cnpjs)` conta os
  XMLs por subpasta (rápido, sem parsear). Os **contadores das abas passaram a usar a contagem REAL em
  disco** (kpi.docs_* = soma do movimento) — antes vinham de `total_*` (contador acumulado) e não
  batiam. Conferido no navegador: NF-e 3746 (=1094+0+2652), NFS-e 1093 (=643+450), NFC-e 478.
  ⚠️ OBSERVAÇÃO p/ o Jean: **NF-e Saídas (04_saida) = 0 em disco** apesar de haver execução "saida
  Escritorio 656". Investigar se o puxador de saídas (autXML/escritório) está gravando em NFe/04_saida
  ou se os 656 caíram como resumo/entrada. (Fora do escopo desta etapa de UX — é dado/engine.)
- (10.9 FEITO) 2026-07-16 — Ajustes pedidos pelo Jean:
  • **Mês anterior por padrão** em todas as telas de competência (Conferência, Auditoria, Downloads):
    helper `_comp_anterior()`; só aplica na 1ª abertura (sentinela `aplicado=1` no form) — o usuário pode
    escolher qualquer mês, o mês vigente, ou "Todas competências". Badge "Apuração: MM/AAAA" no topo.
  • **Conferência em MATRIZ** (tipos em COLUNAS): Empresa × [Entradas·compras | Saídas·vendas |
    Serv.tomados | Serv.prestados | Vendas NFC-e] + Total, com linha TOTAL geral. Colunas emitidas
    (saída/prestado/venda) destacadas em verde (base da tributação). `_COLS_CONF` na `app.py`.
  • **Formato de valor BR** corrigido: filtro Jinja `brl` (65161.25 → 65.161,25). Aplicado nos KPIs e células.
  • **DELAY da Conferência resolvido**: causa medida = motor parseava TODOS os meses (85 empresas → 6,32s).
    Com filtro de 1 mês (padrão agora) → 0,93s. (Cache do motor pode vir depois, mas o default já resolve.)
  • **Limite 500 (NFC-e)**: nota na tela Configurações explicando que passar de 500 num mês é OK — cada
    rodada baixa até o limite e **pula o que já baixou**; se a parada for `limite`, rodar de novo continua
    de onde parou. É **exclusivo da NFC-e** (varejo). NF-e/NFS-e usam NSU incremental e **não precisam** de teto.
  Validado no navegador (dados reais): Conferência default = Apuração 06/2026 (matriz, R$ 1.179.358,55),
  "Todas competências" agrega tudo (Entradas 1094 batendo com o Painel). Auditoria/Downloads também em 06/2026.
  ⚠️ A matriz deixou VISÍVEL que a coluna **Saídas (vendas) = R$ 0,00 em todas as empresas** — confirma o
  problema já anotado (puxador de saídas NF-e não grava em NFe/04_saida). Investigar numa próxima etapa.
- (10.10 — investigacao SAIDAS = 0) 2026-07-16 — Diagnostico: as 2 execucoes `nfe_saida` deram
  **docs=0, parada=656** (cStat 656 = Consumo Indevido), com `ultnsu_saida=None` (recomeca do NSU 0
  a cada rodada). **Nenhum `04_saida` em disco.** Causa estrutural: `puxar_saidas_escritorio()` usa a
  NFeDistribuicaoDFe com o CNPJ do escritorio (Nescon Servicos 35736034000123) — só retorna notas em
  que o CLIENTE autorizou esse CNPJ como **autXML** na emissao. Sem autXML configurado, vem vazio; e
  repetir a consulta do NSU 0 dispara o 656. **NAO é correcao pequena** (depende de estrategia de autXML,
  já levantada antes). Fix pequeno aplicado (anti-bloqueio, seguro): `engines/nfe.py` — cooldown
  (`param bloqueado_saida_ate`, checado no inicio e setado em 137/656) para parar de bater na SEFAZ do
  zero. UI honesta: aviso no Painel (aba NF-e) quando saidas=0 explicando o autXML + 656. **PENDENTE
  (etapa futura, com o Jean):** definir estrategia de saidas (autXML por cliente vs conectores dos
  marketplaces/emissores) — sem isso a apuracao/auditoria de NF-e saida fica zerada.

## Etapa 11 — Saídas NF-e = BUSCA por autXML (NÃO por chave)
> **CORREÇÃO DE RUMO (2026-07-16):** cheguei a montar `consChNFe` por chave, mas o Jean corrigiu e a
> doc CONFIRMA (HANDOFF fato 5): a saída **NÃO vem pelo cert do emitente** (`cStat 641`) e **não temos
> chave** — a operação é uma **BUSCA**. O que funciona (COMPROVADO): distribuição por NSU com o
> certificado da **NESCON** (escritório, 35736034000123) → traz as vendas dos clientes que
> **autorizaram a Nescon como `autXML`**. Foi assim que vieram **14 vendas da CH DA SILVA
> (29133335000160), R$ 257 mil em junho**. O código disso JÁ EXISTE: `nfe.puxar_saidas_escritorio()`
> (botão "Puxar saídas" / `/run/nfe/saidas`). O `consChNFe`-por-chave foi **revertido** (era o caminho
> errado: exige chave que não temos e usa o cert do emitente).
- **O "único cliente" = CH DA SILVA ASSISTEN (29133335000160)** — é a única que configurou `autXML` →
  Nescon (a "liberação"). Marcada no cadastro como `metodo_saida='autXML'`.
- **Por que hoje dá 0 + cStat 656:** `ultnsu_saida` estava `None` → o motor recomeça do NSU 0 a cada
  rodada e a SEFAZ bloqueia por **consumo indevido (656)**. Fix anti-bloqueio já aplicado (cooldown em
  137/656 via `bloqueado_saida_ate`). Para reproduzir as vendas da CH DA SILVA: esperar o cooldown do
  656 (~1h) e rodar **Puxar saídas** UMA vez (é BUSCA, sem chave). Janela de retenção da SEFAZ ~3 meses.
- PENDENTE: (a) confirmar no navegador que a busca volta a trazer a CH DA SILVA após o cooldown;
  (b) escalar autXML para mais clientes (cada cliente precisa autorizar a Nescon no emissor) — é isso
  que amplia a cobertura de saídas.
- (11 VALIDADO) 2026-07-16 — Rodei `puxar_saidas_escritorio()` (busca autXML, cert Nescon):
  **total=14, parada=fim** em 0,8s. Salvou 14 saídas da CH DA SILVA em NFe/04_saida:
  abr/2 (R$ 12.900), mai/4 (R$ 97.220), **jun/8 (R$ 257.299,21)** — junho bate com a doc. `ultnsu_saida`
  avançou p/ 000000000000184. Conferência (06/2026, CH DA SILVA) agora mostra Saídas = 8 / R$ 257.299,21
  (antes 0). CONCLUSÃO: o mecanismo de saídas SEMPRE existiu (autXML+distNSU); o 656 era bloqueio
  transitório. Ampliar cobertura = mais clientes autorizarem a Nescon como autXML no emissor.

## Etapa 12 — Classificação por CFOP (isolar a base de tributação)
- (12.1 FEITO) 2026-07-16 — `engines/cfop.py`: `classificar(cfop)` → {direcao, grupo, rotulo, base}.
  Grupos: venda_compra, venda_compra_st, industrializacao (BASE) · devolucao, transferencia,
  bonificacao_brinde, retorno, remessa, outros (FORA da base). Cobre as naturezas conhecidas;
  desconhecido → 'outros' (conservador, não infla tributo). Validado com CFOPs reais.
- (12.2 FEITO) `engines/conferencia.py`: `faturamento_cfop(cnpjs, ano, mes)` — itera NF-e 55
  (01_entrada/04_saida), extrai CFOP+vProd por item, ignora canceladas, agrega por grupo
  (qtd de NFs distintas + valor + detalhe por CFOP), separando saída (faturamento) x entrada (compras).
- (12.3 FEITO) `app.py` rota `/fiscal/faturamento` + template `fiscal_faturamento.html`: KPIs
  (Faturamento tributável / Fora da base / Compras), tabela por empresa com drill-down dos CFOPs,
  mês anterior por padrão, filtros empresa/ano/mês. Menu: link **Faturamento**. Glossário: verbete CFOP.
- **PROVA (CH DA SILVA, junho):** a conferência "bruta" mostrava R$ 257.299,21 de saída, mas o
  **faturamento tributável real é R$ 115.050,00** (industrialização 5124/6124); **R$ 142.249,21 são
  RETORNO (CFOP 6902)** — passagem, fora da base. Entradas R$ 36.677,84 = remessa p/ industrialização
  (não é compra). Sem CFOP, tributaria-se o dobro. Testado via Flask (HTTP 200, valores conferem).

## Etapa 13 — Módulo Economia Fiscal (monofásicos) + menu agrupado
Base legal: Simples que REVENDE produto monofásico segrega a receita e não paga PIS/COFINS
(LC 123 art. 18 §4º-A + Res. CGSN 140/2018). Identificação por NCM (CST não confiável no Simples).
- (13.1 FEITO) `engines/monofasico.py`: `classificar(ncm)` (curada por prefixo, leis 9.718/10.147/
  10.485/13.097 + overrides do banco) + tabelas do Simples (`aliquota_efetiva`, `share_pis_cofins`
  Anexo I=15,5%, `economia_pis_cofins`). Validado (medicamento/pneu/autopeça/bebida/combustível=MONO;
  têxtil=não).
- (13.2 FEITO) `models.py`: tabela `ncm_monofasico` + colunas `empresas.simples_anexo/simples_rbt12`
  (migração `_add_col`). `ferramentas/importar_monofasico.py`: importa a Tabela SPED 4.3.10 (origem='sped').
- (13.3 FEITO) `engines/conferencia.py::economia_monofasico(cnpjs,ano,mes)`: sobre NFe 04_saida
  (não canceladas), item de VENDA (CFOP base) com NCM monofásico → fat_venda, fat_mono, %, por_categoria,
  por_ncm. (Base = SÓ SAÍDAS REAIS, decisão do Jean.)
- (13.4 FEITO) `app.py::/fiscal/economia` + `templates/fiscal_economia.html`: KPIs (economia R$/mês,
  fat. monofásico, nº empresas, sem RBT12), tabela por empresa com economia + Anexo/alíq. efetiva +
  drill-down por categoria/NCM, "informar RBT12" (link p/ editar), toggle "ocultar sem benefício".
  Cadastro (`cliente_form.html` + rotas novo/editar) ganhou Anexo + RBT12 (helper `_rbt12` aceita
  180000/180.000/180.000,00).
- (13.5 FEITO) Menu reagrupado em dropdowns (`base.html`): Fiscal / Análises (+ Economia) / Admin +
  Ajuda. Glossário: monofásico, segregação de receitas, RBT12/Anexo, NCM.
- **Testes:** isolado (XML sintético: venda medicamento 30049099 R$1000 + venda normal R$2000 + brinde
  R$500 → fat_mono=1000, economia=**R$6,20** [1000×4%×15,5%]); real (CH DA SILVA têxtil = R$0, sem falso
  positivo; migração ok; menu/telas 200); importador SPED ok.
- **COBERTURA:** hoje R$0 no real porque só a CH DA SILVA tem saídas (têxtil, não-monofásico). O módulo
  se enche conforme mais clientes liberarem autXML→Nescon e tiverem saídas com produtos monofásicos.
  Alternativa (fora de escopo, se o Jean quiser cobertura já): estimar pela PROPORÇÃO das entradas.
- Extensões futuras: ICMS-ST (CSOSN 500/CST 60), recuperação retroativa 5 anos.

## Etapa 14 — Estimativa de Economia por Compras (rolling 12m) + fix estrutural do motor

**Pergunta do Jean:** "se não temos vendas, dá pra estimar o benefício pelas compras?"
**Resposta:** sim. A proporção de NCM monofásicos nas COMPRAS REAIS (NFs onde o cliente é
destinatário) é um espelho da proporção nas vendas. Aplicada sobre a receita (PGDAS, vendas
próprias em disco, ou compras × markup 1,5), estima o benefício mensal.

**Pergunta 2 (do Jean):** "o sistema tem que funcionar no servidor, ele precisa se comportar
buscando e baixando no servidor." Investigação: revisei o motor antes de codar e descobri:

### A. Investigação estrutural
Por que a pasta `01_entrada` tinha CFOPs 5xxx (que parecem "saída")?
→ O CFOP fala do **emitente**; quando o **destinatário** é o cliente, isso é **compra**.
Não havia bug — a confusão era semântica. Confirmado: 50 NFs do ALINHAR (25278860), todas
`emit=fornecedor`, `dest=ALINHAR`. Script `ferramentas/migrar_vendas_proprias.py` rodou
idempotente: **0 NFs a mover**.

### B. Fix no motor (`engines/nfe.py`)
Trocada `_docs` por `_docs_classificar(body, cnpj, eh_escritorio)`:
- **Cliente** puxando: `<emit>==cnpj` → `04_saida` (venda própria); `<dest>==cnpj` → `01_entrada`;
  resumos em `02_resumo`; eventos em `03_eventos`.
- **Escritório** puxando: `<emit>==office` → `05_propria` (nova pasta p/ emissões próprias da Nescon);
  `<emit>!=office, office in autXML` → `04_saida/<emit>`; senão ignora (não grava).
- `SUBS_PROPRIAS` em `engines/conferencia.py` agora inclui `05_propria`.
- Resultado: a **separação entrada/saída fica correta daqui pra frente em dev e prod**.

### C. Estimador (`engines/conferencia.py::economia_mono_estimada_compras`)
- Janela = últimos **12 meses** (rolling; configurável 6/12/18/24m).
- Identifica compras reais = NFs em `01_entrada` com `<dest>==cnpj` E itens com `CFOP base`.
- % monofásico = `valor_mono / total_comprado`.
- Receita (fallback): **PGDAS importado → venda própria do cliente → compras × markup 1,5**.
- Economia = `receita × pct_mono × aliquota_efetiva(anexo, rbt12) × share_pis_cofins(anexo, faixa)`.

### D. UI (`templates/fiscal_economia.html`)
- **Toggle no topo**: "Pela venda real" (modo padrão Etapa 13) vs "Pela estimativa de compras"
  (Etapa 14). Modo persistido em `?modo=`.
- Modo estimativa: tabela mostra **Compras ({{janela}}m)**, **% monofásico**, **Receita usada**
  com tag colorida da fonte (PGDAS verde / venda azul / markup amarelo).
- Aviso amarelo: deixa claro que é **estimativa** rolling, com fallback explicado.
- Filtro janela (6/12/18/24 meses) só no modo estimativa.

### E. Resultado com dados reais (12 clientes com compras baixadas, RBT12 fictícia para teste)
- ALINHAR: 96,6% mono (autopeças) → R$ 807/mês.
- UERITON: 21,2% mono (autopeças+combustíveis) → R$ 645/mês.
- QUEIJEIRO: 18,5% mono (bebidas+combustíveis) → R$ 1.133/mês.
- SANDRA: 12,2% mono (bebidas) → R$ 217/mês.
- **TOTAL** (4 com RBT12 fictícia): R$ 2.803/mês.
- Sem RBT12 cadastrada: mostra "informar RBT12" (igual ao modo venda).

### F. Smoke test
- **12 clientes reais** (sem RBT12 cadastrada) — todos aparecem com % mono calculado, sem falso
  positivo. Compras reais: **R$ 1,85 mi total**, 22,5% mono médio.
- HTTP: `/fiscal/economia?modo=venda&aplicado=1` 200; `?modo=estimativa&aplicado=1` 200 (55 KB);
  fallback `?modo=invalido` → `venda`; janela 6/12/24m funciona.
- **Não-quebra do Etapa 13**: `?modo=venda` ainda mostra CH DA SILVA (1 empresa, sem monofásicos,
  link "informar RBT12").

### G. Implicações pro servidor (clean start)
- **85 .pfx** precisam estar em `/app/data/Certificados` e o `empresas.arquivo` apontando pra lá.
  Sem isso, **o servidor não baixa nada** (motor usa o cert do cadastro).
- `ultnsu_*` zera no servidor; primeira execução puxa últimos ~3 meses (janela da SEFAZ).
- Volume comporta **<100 MB** (85 clientes × ~30 NFs/mês × 3 meses × ~5 KB + 85 .pfx + SQLite).
- **mTLS não depende de IP** — funciona do EasyPanel/VPS com o .pfx.
- O **fix do motor garante separação correta** entrada/saída em dev e prod daqui pra frente.
- O `puxar_saidas_escritorio` precisa do cert da Nescon (parametros `office_*`) configurado no
  servidor; sem isso, coluna Saídas fica zerada (igual Etapa 11).

## Etapa 15 — Mensuração 3-fontes (Extrato do Simples Nacional)

**Pergunta do Jean:** "no caso da Sandra, poderíamos fazer uma mensuração? nosso sistema já
tem reconhecimento do extrato automaticamente?" **Resposta:** não, não tinha. O sistema só
lia XMLs da SEFAZ. Esta etapa implementa **importação do PDF do Recibo PGDAS-D** (o "Extrato
do Simples" oficial) e cruza com as outras 2 fontes.

### A. Parser `engines/pgdas.py`
Lê o PDF do Recibo PGDAS-D (gerado pelo portal do Simples ou pelo programa PGDAS-Download)
e extrai: CNPJ, período (MM/AAAA), receita bruta total, anexo, alíquota efetiva, DAS devido,
RBT12 (se constar). Usa `pdfplumber` (adicionado a `requirements.txt`).

### B. Tabela `pgdas_recibos` (models.py)
Campos: `cnpj, ano, mes, receita_total, anexo, arquivo, parsed_em, hash_linha`. UNIQUE por
`(cnpj, ano, mes, hash_linha)` para dedup automática.

### C. Função `engines/conferencia.py::mensuracao_beneficio(cnpjs, ano, mes)`
Cruza 3 fontes para cada CNPJ:
- **Hierarquia de receita**: PGDAS (oficial declarado) > VENDAS REAIS (NF/NFCe saída) > markup 1,5
- **% monofásica**: usa vendas reais se houver (sinal real); senão usa compras (proxy)
- **Semáforo**:
  - **verde**: vendas confirmam (diff compras × vendas ≤ 5 p.p.)
  - **amarelo**: extrapolação (só compras, ou só PGDAS, sem vendas p/ comparar)
  - **vermelho**: sem fonte ou vendas ≠ compras por >5 p.p. (viés)

### D. Rotas
- `POST /importar/pgdas` (admin) — upload PDF + seletor empresa. Valida CNPJ do PDF == CNPJ da
  empresa selecionada. Atualiza `empresas.simples_anexo` e `empresas.simples_rbt12` se vazios.
- `GET /fiscal/economia/mensuracao` — tela com KPIs (economia total, semáforo, com PGDAS),
  tabela por empresa com receita (tag colorida da fonte), % compras × % vendas × diff ×
  semáforo, link "informar RBT12" para quem ainda não tem.

### E. Resultado real (após 1 upload de teste da Sandra)
- **Sandra (08108132)**: receita PGDAS R$ 203.263; vendas reais R$ 36.861 (18%);
  compras 12,2% mono (bebidas); vendas 0% mono → **vermelho** (vendas confirmam que ela não
  vende monofásico). Insight: 82% da receita da Sandra é venda direta sem NF (típico de
  lanchonete), por isso o PGDAS é crucial p/ o cálculo do DAS real.
- **CH DA SILVA (29133335)**: R$ 115k vendas reais (têxtil, autXML escritório);
  compras 0% mono → vendas 0% mono → **verde** (cruzamento bate exatamente).
- **Outros 8** com compras: amarelo (esperado — sem vendas baixadas ainda).

### F. Insights da Etapa 14 (que motivou esta)
A Etapa 14 (estimativa por compras) é **franca** quando o cliente compra monofásico mas vende
outra coisa (como a Sandra, que compra bebida mas vende comida). O **PGDAS** dá a **receita
declarada oficial** que, junto com as vendas, mostra o **mix real do que entra no DAS**.

### G. Smoke test
- HTTP: `/importar/pgdas` 200 (GET), 302→200 (POST upload); `/fiscal/economia/mensuracao` 200.
- Parser: PDF sintético gerado via reportlab → todos os campos extraídos corretamente.
- Migração: tabela `pgdas_recibos` criada via `init_db()`.
- Dados reais: 8 amarelos (extrapolação) + 1 verde (CH DA SILVA) + 76 vermelhos (sem dados).

### H. Pendências / extensões
- **Auto-import mensal**: o PGDAS-Download gera PDFs em lote. Hoje o usuário precisa fazer
  upload manual de cada um. Próximo passo: watcher de pasta + auto-import.
- **Validação cruzada receita**: comparar PGDAS × soma de NFCe saída × venda própria.
  Quando os 3 números divergem muito (>10%), sinalizar.
- **DEFIS** (substituiu PGDAS-D em 2025): estrutura similar mas anual. Implementar depois.
- **Estimativa retroativa**: hoje só olha o mês corrente; com 12 meses de PGDAS dá p/ projetar
  economia acumulada.

## Etapa 16 — ICMS-ST (segregação no Simples)

**Pergunta do Jean:** "no caso da Sandra, poderíamos fazer uma mensuração?"
Decisão registrada na Etapa 15. Esta etapa entrega a parte de ICMS-ST.

### A. Base legal
- **LC 123/2006 art. 13 §1º XIII 'a'**: ST é **excluída** do regime unificado — recolhida fora
- **Res. CGSN 140/2018 art. 25 §8º II**: substituto optante segrega + recolhe ST por fora
- **Res. CGSN 94/2011 art. 5º**: reforça exclusão do ST do Simples
- **RICMS/2000-SP art. 274**: operacionaliza na GIA-ST estadual

### B. Classificador `engines/st.py`
Reconhece **CST** (regime normal, 2 dígitos) **e CSOSN** (Simples, 3 dígitos) com ST:
- **CST ST**: 10, 30, 60, 70, 90
- **CSOSN ST**: 201, 202, 203, 500, 900

Dois regexes separados (CST vs CSOSN) porque o backreference não funciona com o "SN" extra.

`classificar_st(texto_xml)` → `{tem_st, csts_encontrados, valor_total, valor_com_st, valor_nf, aliquota_interna}`.

Alíquota interna: extraída do `<ICMS00><pICMS>` (primeira ocorrência), fallback para tabela
interna por UF (SP=18, RJ=20, MG=18, PR=19,5, RS=18, BA=20,5, etc.).

### C. Motor `engines/conferencia.py::receita_com_st(cnpjs, ano, mes)`
Itera `NFe/04_saida`, `NFe/05_propria`, `NFCe/01_venda` (mesma regra da Etapa 13/14). Soma:
- `fat_total` = soma de vProd
- `fat_com_st` = soma de vProd dos itens onde CST/CSOSN ∈ {_ST_CODES}
- `pct_st` = fat_com_st / fat_total × 100
- `aliquota_interna` = XML ou UF (com flag de origem)

### D. Tela `/fiscal/economia/st` (login)
KPIs (R$ fat_com_st, nº empresas c/ ST, R$ economia DAS/mês, R$ fat sem ST).
Tabela por empresa: UF, faturamento, receita ST, % ST, alíquota interna, códigos ICMS
(tags coloridas: ST vs normal), **economia no DAS**.
Filtros: empresa, ano, mês, toggle "só com ST". Mês anterior padrão.

Cálculo (revenda / **substituído**):
- **Quem recolhe ICMS-ST:** a indústria (substituto). O revendedor **não recolhe** ST.
- **Quem paga o ICMS-ST:** o consumidor final, embutido no preço de compra.
- **O que a tela calcula:** a **economia no DAS** = receita com ST × alíquota interna × share_icms
  (~32% no Simples Anexo I faixa 1, simplificado). É o ICMS próprio que deixa de incidir
  sobre a parcela segregada (LC 123 art. 13 §1º XIII "a" + Res CGSN 140 art. 25 §8º II).
- **Substituto (indústria/atacadista):** recolhimento do ICMS-ST via GIA-ST estadual
  (alíquota interna × receita com ST) — caso **não coberto** pela tela atual.

### E. Resultado real (Sandra 06/2026)
- **505 NFCe** vendidas, R$ 36.861 faturamento
- **227 NFs com CSOSN 500** (ICMS-ST retida anteriormente) — **R$ 6.391,67** (17,3%)
- UF: SP → alíquota interna 18%
- **Economia no DAS** (parcela ICMS): **R$ 368,16/mês** ← valor correto
- Códigos CSOSN na base: 102 (tributada normal) + 500 (ST)

**Atenção conceitual (correção pós-commit):** empresa do Simples que **revende**
(substituída) **não recolhe ICMS-ST por fora** — quem recolhe é a **indústria
(substituta)** via GIA-ST estadual. O ICMS-ST da Sandra já foi pago pela
Coca-Cola/Nestlé/etc. e está embutido no preço que ela pagou pela compra.
A economia que ela ganha com a segregação é o **ICMS próprio que deixa de
incidir no DAS** sobre a receita com ST — não é "ICMS-ST a recolher".
A Etapa 16 foi corrigida (commit a seguir) para refletir isso: a coluna
"ICMS-ST a recolher" foi **removida** da tela; a nota explicativa agora
distingue substituto × substituído.

### F. Smoke test
- Sintético (CST 60 + CST 00): R$ 1.000 com ST, R$ 500 sem ST, alíquota 18% ✓
- Sintético (CSOSN 500 + 102): mesmo resultado, valida o parser do Simples ✓
- Dados reais: 20 empresas com saídas, **1 com ST (Sandra)**, 19 sem
- HTTP: `/fiscal/economia/st?aplicado=1&ano=2026&mes=06` 200, KPI bate cálculo

### G. Glossário + menu
- Verbetes na Ajuda: "ICMS-ST (Substituição Tributária)" + "CST vs CSOSN"
- Menu Análises → Benefícios fiscais → "ICMS-ST (segregação)"

### H. Pendências / extensões
- **Cálculo exato** depende de **RBT12/Anexo** do cadastro (hoje usa 32% fixo para Anexo I faixa 1)
- **MVA** depende de estado + produto + Convênio — não entra nesta versão
- **GIA-ST** estadual não é automatizável (cada estado tem sua obrigação)
- **Recuperação retroativa**: hoje só projeta o mês; 5 anos = ~R$ 22k de economia só na Sandra

## Etapa 16 — RBT12 automático a partir dos recibos PGDAS-D
Contexto: as Etapas 14/15 (feitas em paralelo por outra ferramenta) entregaram a Estimativa por
Compras + Mensuração 3-fontes, mas o cálculo morria em `rbt12=0`: **81 das 85 empresas sem RBT12**
(ex.: ALZIRÃO com 22,7% de compras monofásicas e economia R$ 0,00). O recibo PGDAS-D já traz receita
e anexo por mês → dá para derivar o RBT12 sem digitar 81 cadastros.
- `engines/conferencia.py`: `rbt12_de_pgdas(lista, rbt12_cadastro)` e `rbt12_efetivo(cnpj, cadastro)`.
  **Política prudente** (o RBT12 define a faixa; errar distorce tudo):
    * **12+ meses** → soma real dos 12 últimos → fonte `pgdas` (VENCE o cadastro).
    * **6–11 meses** → proporcionaliza (média×12) **só se o cadastro estiver vazio** → `pgdas_proporcional`.
    * **<6 meses** → NÃO extrapola; mantém o cadastro (evita 1 mês × 12 jogar a empresa de faixa).
- Integrado em `economia_mono_estimada_compras` (e a `mensuracao_beneficio` herda, pois reusa esse
  resultado) + na rota `/fiscal/economia` (modos venda e estimativa) via `rbt12_efetivo`.
- Saída passou a expor `rbt12_fonte` / `rbt12_meses` / `rbt12_proporcional`; a tela mostra
  "RBT12 PGDAS ✓ / PGDAS ≈ / cadastro / não informado" (transparência da origem do número).
- **Testado:** política conferida (12m→real; 8m sem cadastro→proporcional; 8m com cadastro→cadastro;
  1m→NÃO extrapola). Real: Sandra tem 1 recibo e **manteve** os R$360k do cadastro (correto).
  Motor 26 empresas OK; telas /fiscal/economia (venda+estimativa), /mensuracao e /importar/pgdas = 200.
- **Gargalo remanescente = DADO:** só 1 recibo PGDAS importado. Conforme importar 12 meses por cliente,
  o RBT12 vira real e a economia aparece sozinha para os 81 clientes hoje zerados.
