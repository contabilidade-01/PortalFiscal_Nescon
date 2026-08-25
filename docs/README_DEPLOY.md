# Deploy — Portal Fiscal Nescon (EasyPanel)

App **separado** no EasyPanel (não misturar com GClick / nescon-clientes).
Repositório: `https://github.com/contabilidade-01/PortalFiscal_Nescon` · branch `main`.

## Arquitetura

```
GitHub (código)  →  EasyPanel (build Dockerfile)  →  Container (waitress :8000)
                                                          │
                     Volume persistente ──────────────────┘
                     /app/data  →  banco + XML + Certificados + logs
```

- Imagem = só código. XML, `.pfx` e SQLite **nunca** entram no Git nem na imagem.
- Segredos = env vars do EasyPanel (modelo: `.env.example`).
- Relógio do container = `America/Sao_Paulo` (jobs e `/status` no horário de Brasília).

| Caminho no volume | Conteúdo |
|---|---|
| `/app/data/portal_fiscal.db` | Banco (empresas, usuários, fila de jobs) |
| `/app/data/XML/` | XML baixados da SEFAZ |
| `/app/data/Certificados/` | A1 (`.pfx`) enviados pelo painel |
| `/app/data/logs/` | `run_diario.log` (se usar o script) |

## 1. Variáveis de ambiente (EasyPanel → Environment)

Cole no painel (ou copie `.env.example` → `.env` local). **Não commitar** `SECRET_KEY` / senhas.

| Variável | Obrigatória | Valor no EasyPanel | O que faz |
|---|---|---|---|
| `SECRET_KEY` | sim | string aleatória ≥ 32 | Assina o cookie de sessão |
| `ADMIN_SENHA_INICIAL` | sim (1º boot) | senha forte | Senha do `admin` só se o banco estiver vazio |
| `FLASK_HTTPS` | sim | `1` | Cookie `Secure` (domínio HTTPS) |
| `TRUST_PROXY` | sim | `1` | Confia em `X-Forwarded-*` do proxy EasyPanel |
| `FISCAL_DATA_DIR` | já na imagem | `/app/data` | Onde mora o banco |
| `FISCAL_XML_DIR` | já na imagem | `/app/data/XML` | Onde grava XML |
| `TZ` | já na imagem | `America/Sao_Paulo` | Horário do robô e da UI |
| `FISCAL_CRON` | já na imagem | `1` | Robô diário dentro do container |
| `FISCAL_CRON_HORA` | já na imagem | `06:00` | Hora de enfileirar o job completo |
| `CERT_KEY` | não | — | Reserva da Etapa 4b (senha do PFX ainda é texto no banco) |

Gerar as chaves **na sua máquina**:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Rode duas vezes: uma para `SECRET_KEY`, outra (opcional) para senha admin.

## 2. EasyPanel → Create → App (recomendado)

1. **Source:** GitHub `contabilidade-01/PortalFiscal_Nescon`, branch `main`.
2. **Build:** Dockerfile (raiz). Porta do container: **8000**.
3. **Environment:** as variáveis da tabela acima (`SECRET_KEY`, `ADMIN_SENHA_INICIAL`, `FLASK_HTTPS=1`, `TRUST_PROXY=1`). O resto já vem na imagem.
4. **Mounts → Add Volume:** nome `portal-fiscal-data` → destino **`/app/data`**. Sem isso, cada redeploy apaga banco, XML e certificados.

> **Sintoma clássico:** o site abre, Waitress diz `Serving on 0.0.0.0:8000`, mas depois de cada push as empresas, execuções e XML somem. O front não “resetou”: o container nasceu sem disco persistente e o `init_db()` criou um banco vazio (só `admin`). Confira `GET /healthz` → `volume_montado` deve ser `true` e `risco_apagar_no_deploy` `false`.

5. **Domains:** ex. `fiscal.gestaoempresa.com` → serviço `web` / porta 8000 → HTTPS Let's Encrypt.
6. **Auto Deploy:** ligado, para rebuild a cada push em `main`.

Healthcheck interno: `GET /healthz` (sem login). O EasyPanel pode usar a mesma URL.

## 3. Alternativa: Docker Compose

Se o projeto no EasyPanel for tipo **Compose**, aponte para `docker-compose.yml` na raiz.
Segredos: cole no Environment do serviço `web` (não use `env_file` no painel se o `.env` não existir no Git).

Local:

```bash
cp .env.example .env   # preencha SECRET_KEY e ADMIN_SENHA_INICIAL
docker compose up -d --build
# http://localhost:8000
```

## 4. Primeiro acesso

1. Abra `https://fiscal.<seu-dominio>/login`.
2. Login `admin` + a senha de `ADMIN_SENHA_INICIAL`.
3. Troque a senha em **/trocar-senha**.
4. **Clientes** → cadastre/importe. **Certificados → Vincular** (upload dos `.pfx`) — os arquivos caem em `/app/data/Certificados`.

Não copie o `portal_fiscal.db` do Windows se os caminhos dos PFX forem `C:\Users\...`. No servidor o caminho tem que ser o do volume. Opção limpa = recomeçar o cadastro e reenviar os certificados. Opção B (avançada): copiar o `.db` + os `.pfx` para o volume e atualizar `empresas.arquivo` para `/app/data/Certificados/<ficheiro>.pfx`.

## 5. Robô diário

Com `FISCAL_CRON=1` o próprio processo web enfileira um job `completo` todo dia às `FISCAL_CRON_HORA` (padrão 06:00 BRT): entradas NF-e, ciência 210210, saídas autXML, NFS-e e NFC-e SP.

Não precisa de Tarefa Agendada do Windows no VPS. Reserva: Cron do EasyPanel com

```text
python /app/run_diario.py
```

Com `FISCAL_CRON=1` o `run_diario` assume `FISCAL_ROLE=web`: **só enfileira** e deixa o worker do app processar. Assim não há dois processos NFC-e no mesmo IP (limite SEFAZ-SP). No Windows, com o app desligado, `FISCAL_ROLE=cron` (ou omitir `FISCAL_CRON`) processa a fila.

Painel **Admin → Saúde SEFAZ** (`/sefaz/saude`): quem está em cooldown/circuito, histórico de 656/429 e botão de rearme.

(só use um dos dois — cron EasyPanel *e* `FISCAL_CRON=1` — para não duplicar a varredura.)

## 6. Backup (LGPD)

Agende backup **diário do volume** `portal-fiscal-data`. Esse volume tem certificados A1 e XML de clientes — o mesmo cuidado de pasta de certificado no PC.

## 7. O que NÃO sobe no Git / na imagem

Confirmado pelo `.gitignore` e `.dockerignore`:

- `Certificados/`, `*.pfx`, `*.p12`, `*.pem`
- `XML/`, `*.db`, `logs/`, `.env`

## 8. Windows local (sem Docker)

`start.bat` → http://localhost:5001 (Flask). Não use `FLASK_HTTPS` nem `FISCAL_CRON` no PC se já existir a Tarefa Agendada `PortalFiscalNescon`.

`start_servidor.bat` = waitress na 5001 (precisa `pip install waitress`).

## Pendência de segurança

A senha do certificado A1 ainda é gravada em texto no SQLite. Antes de expor o painel a mais gente, cifrar com Fernet (`CERT_KEY`) — ver `docs/PROGRESSO_PLATAFORMA.md` Etapa 4b.
