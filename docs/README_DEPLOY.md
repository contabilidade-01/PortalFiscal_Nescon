# Deploy — Portal Fiscal Nescon (EasyPanel, mesmo VPS do GClick)

App **separado** no EasyPanel (não misturar no repo do GClick). Molde: o `PLANO_DEPLOY_EASYPANEL.md` do GClick.

## Arquitetura
```
GitHub (codigo)  ->  EasyPanel (build Dockerfile)  ->  Container (waitress:8000)
                                                          |
                     Volume persistente  -----------------+
                     /app/data  (banco + XML + Certificados)
```
- Imagem = só código. Dados/segredos vêm do **volume** e das **env vars** (`.dockerignore` garante).
- **Separacao codigo x dados** via `FISCAL_DATA_DIR=/app/data` (banco), `FISCAL_XML_DIR=/app/data/XML`,
  certificados em `/app/data/Certificados`.

## 1. Antes de expor (segurança)
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # SECRET_KEY
```
- Definir `SECRET_KEY` (aleatória) e `ADMIN_SENHA_INICIAL` (senha forte) nas env vars.
- No 1º login, o sistema avisa se ainda estiver em `admin/admin` (banner) → trocar em **/trocar-senha**.
- ⚠️ **Pendência de segurança (Etapa 4b):** a senha do certificado ainda é gravada em texto no banco.
  Antes de produção real, implementar criptografia (Fernet, env `CERT_KEY`) — ver `PROGRESSO_PLATAFORMA.md`.

## 2. Subir no GitHub
```bash
cd PortalFiscal_Nescon
git init && git add . && git commit -m "Portal Fiscal: deploy inicial"
git branch -M main
git remote add origin https://github.com/<usuario>/portal-fiscal-nescon.git
git push -u origin main
```
> O `.gitignore` bloqueia `.env`, `*.db`, **`XML/`**, **`Certificados/`**, `*.pfx`. Conferir no GitHub que NÃO subiram.

## 3. EasyPanel → Create → App
- Source: GitHub (repo `portal-fiscal-nescon`, branch `main`); Build: **Dockerfile**; Port: **8000**.
- **Environment:**
  ```env
  SECRET_KEY=<aleatoria_32>
  ADMIN_SENHA_INICIAL=<senha_forte>
  FISCAL_DATA_DIR=/app/data
  FISCAL_XML_DIR=/app/data/XML
  FLASK_HTTPS=1
  # CERT_KEY=<chave_fernet>   # quando a Etapa 4b estiver pronta
  ```
- **Mounts → Add Volume:** `portal-fiscal-data` → `/app/data`  (CRÍTICO — sem isso, redeploy apaga tudo).
- **Domains:** ex. `fiscal.gestaoempresa.com` (HTTPS Let's Encrypt automático).

## 4. Levar dados + certificados (⚠️ ponto de atenção)
Os certificados hoje estão em caminhos do **OneDrive local** (coluna `empresas.arquivo`). No servidor eles precisam existir em `/app/data/Certificados` **e** os caminhos reapontados. Duas opções:
- **A) Recomeço limpo:** subir o app → **Clientes** importa/sincroniza do GClick → **Certificados → Vincular**
  (upload dos .pfx) reaponta cada um para `/app/data/Certificados`. (mais simples, recomendado)
- **B) Migrar o banco:** enviar `portal_fiscal.db` para `/app/data/` + copiar os `.pfx` para
  `/app/data/Certificados/` + rodar um UPDATE reapontando `empresas.arquivo`. (preserva histórico)

## 5. Agendamento diário (no servidor)
- Container: adicionar um **cron** no EasyPanel (ou um segundo serviço/scheduler) rodando
  `python run_diario.py` 1x/dia. Alternativa: o worker já roda no processo web; criar um cron
  que faça um POST em `/run/...` autenticado, OU um job agendado que chama `run_diario.py`.

## 6. Rodar em produção LOCAL (Windows, sem Docker)
`pip install waitress` → `start_servidor.bat` (waitress na 5001). O worker de jobs sobe junto.

## 7. Backup
Volume `/app/data` (banco + XML + certificados). Agendar backup diário no EasyPanel.
**Atenção LGPD:** esse volume contém certificados — tratar com o mesmo cuidado de dados sensíveis.
