# Portal Fiscal Nescon — Plataforma Fiscal (NF-e + NFS-e)

> **Documento-síntese para iniciar uma nova conversa.** Lê este primeiro; os detalhes profundos
> estão em `docs/HANDOFF.md` (motores), `docs/PROGRESSO_PLATAFORMA.md` (etapas da refatoração) e
> `docs/README_DEPLOY.md` (EasyPanel / VPS).
> **Local:** `start.bat` → http://localhost:5001 (admin/admin).
> **Produção:** GitHub `contabilidade-01/PortalFiscal_Nescon` → EasyPanel (Dockerfile, porta 8000).
> Env: copie `.env.example`. Python local: `C:\Users\parce\AppData\Local\Programs\Python\Python313\python.exe`.

---

## 1. O que é
Sistema **web próprio** da Nescon (escritório contábil) para **baixar automaticamente os XML de NF-e e NFS-e**
dos clientes, direto da SEFAZ/Portal Nacional, **sem depender de SIEG/Arquivei**. É uma **plataforma modular**:
o "puxador de NF" é **um módulo**; há espaço para **DAS** e outros. Substitui o processo manual de ir à SEFAZ
com a chave. Compartilha a base de clientes com o **GClick** (outro sistema do Jean).

## 2. Estado atual (o que funciona HOJE)
- **Base única: 85 empresas** (importadas do GClick). Cada uma com flags `puxa_nfe`/`puxa_nfse` (pode as duas) e `metodo_saida`.
- **Certificados vinculados: 29/85** (o resto: 19 cloud-only no OneDrive + senhas fora do nome — ver §7).
- **Motores 100% validados com dados reais:**
  - **NF-e entradas** (compras): 750 XML reais do Queijeiro.
  - **NF-e saídas** (vendas): 14 vendas reais da CH DA SILVA via `autXML` (cert da Nescon).
  - **NFS-e**: 25 NFS-e reais da ALINHAR (ADN).
  - **Ciência 210210**: aceita pela SEFAZ (via `xmlsec`).
- **Robô diário** (Tarefa Agendada Windows 06:00) + **jobs em background** (roda deslogado, com status ao vivo).
- **RBAC, menu modular, exclusão manual de XML, segurança base** — tudo testado.
- **Git** no GitHub (`contabilidade-01/PortalFiscal_Nescon`). **Deploy EasyPanel:** `docs/README_DEPLOY.md` + `.env.example`.

## 3. Fatos técnicos COMPROVADOS (não reinventar)
- **Serviço NF-e:** `NFeDistribuicaoDFe` (SOAP 1.2, `distDFeInt` v1.01, mTLS). É o mesmo que a SIEG usa.
- **Bloqueio 403 = chave CNG/RC2 do A1** → **usar OpenSSL/cryptography** (Python), **nunca .NET/Schannel**.
- **Janela de 3 meses** (NF-e): só entrega ~90 dias → **rodar diário**. (NFS-e NÃO tem essa janela — traz histórico.)
- **Anti-656:** 20 consultas/h por CNPJ (distNSU); parar no `cStat 137`; cooldown 1h; nunca zerar `ultNSU`.
- **Entradas × Saídas:** a distribuição só traz **compras** (você é destinatário). **Vendas o emitente NÃO pega**
  (cStat 641). Vendas via **autXML** (cliente autoriza o CNPJ da Nescon no emissor) → cert do escritório puxa.
- **NFS-e:** `GET https://adn.nfse.gov.br/contribuintes/DFe/{nsu}` mTLS; JSON `LoteDFe[].ArquivoXml` (base64+gzip);
  classifica **tomado (entrada)** × **prestado (saída)** por `emit`/`toma`.
- **Ciência 210210:** endpoint `www.nfe.fazenda.gov.br/NFeRecepcaoEvento4`, operação `nfeRecepcaoEventoNF`,
  corpo `<nfeDadosMsg>` direto; assinar com **`xmlsec`** (libxmlsec1) — `signxml` NÃO serve (bloqueia SHA1).
- **SIEG não tem acesso privilegiado** — usa os mesmos caminhos (autXML + captura na origem). Não há mágica.

## 4. Arquitetura / arquivos
```
app.py            Flask: rotas, login/RBAC, telas, /status, /download, /xml/excluir
models.py         SQLite: empresas, usuarios(+usuario_empresas), jobs, consultas_log, parametros. DATA_DIR/WAL.
worker.py         Motor de JOBS em background (fila + thread + claim atomico). Roda deslogado.
run_diario.py     Tarefa agendada 06:00 -> enfileira job 'completo'.
engines/
  certs.py        A1 compartilhado (PFX->PEM via cryptography). Base de tudo.
  nfe.py          NF-e SOAP/SEFAZ (entradas por cliente + saidas escritorio/autXML).
  nfse.py         NFS-e ADN REST (classifica tomado/prestado).
  ciencia.py      Evento 210210 assinado (xmlsec) + dar_ciencia_pendentes.
templates/        base(menu+barra status) login dashboard clientes certificados downloads
                  usuarios usuario_form trocar_senha das cliente_form
config.json       porta/limites (caminho XML local; env FISCAL_XML_DIR sobrepoe em prod)
.env.example      modelo das env vars do EasyPanel (SECRET_KEY, ADMIN_SENHA_INICIAL, cron…)
Dockerfile docker-compose.yml entrypoint.sh  -> deploy EasyPanel (waitress :8000 + volume /app/data)
DADOS (fora do Git): portal_fiscal.db · XML/<cnpj>/<AAAA-MM>/{NFe,NFSe}/... · Certificados/ · logs/
```

## 5. Módulos e telas
- **Fiscal (puxador):** Painel (KPIs + abas NF-e/NFS-e + botões puxar) · Clientes (base única, cadastro/editar/excluir,
  flags) · Certificados (abas Com/Sem + validade + vincular) · Downloads (por competência/tipo, ZIP + **excluir manual**).
- **DAS:** placeholder (cálculo/emissão — a construir).
- **Admin:** Usuários (papel + escopo por empresa).

## 6. Segurança / RBAC
- **admin** faz tudo; **operador** só vê/baixa as empresas designadas (`usuario_empresas`) — não gerencia nem puxa.
- `secret_key`/senha inicial via env; cookies HttpOnly/SameSite; banner + `/trocar-senha` se ainda for admin/admin.
- ⚠️ **PENDENTE P1 (Etapa 4b):** a **senha do certificado** ainda é gravada em **texto** no banco. Cifrar com Fernet
  (env `CERT_KEY`) antes de produção real — plano em `docs/PROGRESSO_PLATAFORMA.md`. Falta também CSRF nos forms.

## 7. Como continuar (próximos passos)
1. **Deploy EasyPanel** (app separado, mesmo VPS do GClick): seguir **`docs/README_DEPLOY.md`**. Env em `.env.example`. Re-vincular certificados no volume `/app/data/Certificados`.
2. **Etapa 4b:** cifrar senha do cert (Fernet/CERT_KEY). **P1 de segurança.**
3. **Cobertura de certificados (29→mais):** baixar a pasta cloud-only `...JEANDSON...\0002 - Certificado Digital`
   (senhas no nome) e rodar `vincular_com_senhatxt.py`. Os `Senha.txt` cobrem os avulsos.
4. **autXML dos clientes:** espalhar o CNPJ da Nescon `35.736.034/0001-23` no emissor de cada cliente (Bling:
   Config→Notas Fiscais→Config NF-e→autorizados). Marketplace configura no EMISSOR, não no painel. Planilha de
   triagem: `../Triagem_autXML_Clientes.xlsx`.

## 8. Projetos relacionados (contexto)
- **GClick** (`01_Jean/00_Claude/00_PROJETOS/GCLICK`): base dos 90 clientes; molde de deploy EasyPanel. NÃO misturar.
- **PORTAL NACIONAL NFSE** (`.../PORTAL NACIONAL NFSE/projeto_recuperado`): Flask recuperado; origem da lógica NFS-e.
- **robo integra v6** (`.../robo integra v6`): robô Playwright (Emissor Nacional + captcha). Nossa NFS-e via ADN é melhor.
- **PuxadorNFe_Web** (`TESTENFE/PuxadorNFe_Web`): versão anterior (só NF-e). O `PortalFiscal_Nescon` é o atual/oficial.

## 9. Regras de ouro (não quebrar)
- Nunca .NET/Schannel para o A1 — só OpenSSL/cryptography. Ciência = `xmlsec`.
- Nunca zerar `ultnsu_*`. Parar no 137/404. 1 varredura/dia por CNPJ.
- Saída de NF-e só via autXML (cert do escritório). Cert do proprio cliente dá 641.
- Dados (banco/XML/**Certificados**) NUNCA no Git. Separados do código (DATA_DIR/volume).
- Não misturar no repo do GClick. Mudanças no código → Git → deploy (dados ficam no servidor).
