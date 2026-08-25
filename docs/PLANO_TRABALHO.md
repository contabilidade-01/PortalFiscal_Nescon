# PLANO DE TRABALHO — próximas etapas (handoff para a próxima IA)

> Escrito em 2026-07-16, ao fim de uma sessão longa (Etapas 11→16).
> **Leia antes:** `HANDOFF.md` (fatos comprovados) e `PROGRESSO_PLATAFORMA.md` (log por etapa).
> App dev: `start.bat` → localhost:5001 (admin/admin). Commit atual: `40e3c82`.

---

## 0. VEREDITO — parar de simular, começar a medir

Diagnóstico honesto do estado atual (não é crítica ao que foi feito, é priorização):

- Existem **4 superfícies de "economia"** (venda real, estimativa por compras, mensuração 3-fontes,
  RBT12 automático) para um benefício real medido de **R$ 200–1.100/mês por cliente**.
- **A modelagem cresceu sobre dado faltante:** apenas **1 recibo PGDAS** importado, **1 cliente** com
  saídas reais (CH DA SILVA), **81 de 85** empresas sem RBT12.
- **`markup=1,5` é um palpite.** Receita estimada = compras × 1,5, e sobre ela calcula-se imposto:
  é palpite sobre palpite. **Não pode ser entregue ao cliente como apuração.**
- Enquanto isso: **o sistema nunca foi para o servidor**, a **senha do certificado está em texto puro**
  no banco, e a cobertura de certificados é 29/85.

**Ativos reais construídos (preservar):** classificador de **NCM monofásico**, classificador de **CFOP**,
e o **% monofásico das compras** — este último vale como **TRIAGEM** (quem investigar), não como número final.

### ⛔ CONGELAR (não construir agora)
- Novas telas de simulação/cenário.
- Refinar `markup` por categoria; projeção retroativa 5 anos; ICMS-ST; novas fontes no semáforo.
- Qualquer feature nova de análise antes de resolver as Prioridades 1 e 2.

---

## 1. PRIORIDADE 1 — DADO (é o que destrava tudo que já existe)

### 1.1 Importação em LOTE de recibos PGDAS-D
Hoje `/importar/pgdas` importa 1 PDF por vez; só há **1 recibo** no banco.
- Aceitar upload múltiplo (ou varrer uma pasta) de PDFs; processar em lote com relatório
  (importados / ignorados / erro), reusando `engines/pgdas.py::parse_recibo`.
- **Aceite:** ≥ 20 clientes com ≥ 12 meses importados; na tela `/fiscal/economia?modo=estimativa`
  a coluna RBT12 passa a exibir **`PGDAS ✓`** (hoje mostra `cadastro`/`não informado`).
- Isso ativa sozinho o RBT12 real (ver `conferencia.rbt12_de_pgdas`) e a economia dos ~81 zerados.

### 1.2 Expandir autXML (traz as saídas reais)
Saída de NF-e **só** vem por autXML com o cert da Nescon (cert do emitente dá `cStat 641`).
Hoje só a CH DA SILVA autorizou.
- Processo (não é código): orientar clientes a cadastrar o CNPJ **35736034000123** como autXML no emissor.
- **Aceite:** +5 clientes com XMLs em `NFe/04_saida`. Cada novo cliente melhora a precisão automaticamente.

### 1.3 Cobertura de certificados (29/85)
- Baixar a pasta cloud-only `0002 - Certificado Digital` e re-vincular (`ferramentas/vincular_certificados.py`).
- **Aceite:** +20 empresas com `senha_ok=1`.

---

## 2. PRIORIDADE 2 — PRODUÇÃO (o sistema nunca subiu)

### 2.1 Deploy no EasyPanel
Seguir `docs/README_DEPLOY.md`. Volume em `/app/data`; env `SECRET_KEY`, `ADMIN_SENHA_INICIAL`,
`FISCAL_DATA_DIR`, `FISCAL_XML_DIR`. ⚠️ Re-vincular os certificados (caminhos mudam).

### 2.2 Git remote + push
Não há remote configurado. Criar repo no GitHub → `git remote add origin <url>` → `git push -u origin master`.
⚠️ Conferir que `.gitignore` bloqueia `*.db`, `*.pfx`, `XML/`, `Certificados/`, `.env` (já validado nos commits).

### 2.3 Agendamento
`cron`/Task Scheduler chamando `run_diario.py` (enfileira 'completo' e processa a fila).

### 2.4 🔒 Etapa 4b — cifrar a senha do certificado (RISCO LGPD)
`empresas.senha` está em **texto puro**. Migrar para Fernet com chave em env `CERT_KEY`.
Migração suave: se decifrar falhar, tratar como texto (não quebrar os 29 certs já vinculados).
**É o item de maior risco do sistema hoje.**

---

## 3. PRIORIDADE 3 — CONSOLIDAR (reduzir superfície, não aumentar)

### 3.1 Unificar as telas de economia
Colapsar `/fiscal/economia` (2 modos) + `/fiscal/economia/mensuracao` em **uma** tela com seletor de
**confiança da fonte**: `venda real` > `PGDAS` > `estimativa por compras`. Menos superfície, mesma informação.

### 3.2 Rotular ESTIMADO × REAL (obrigatório antes de usar com cliente)
Qualquer número derivado de `markup` deve aparecer como **estimativa/triagem**, com aviso explícito.
Nunca apresentar como apuração. Sugestão: badge "TRIAGEM" vs "APURADO".

### 3.3 Revisão crítica do cálculo (Etapas 14/15) antes de uso real
Revisar `economia_mono_estimada_compras` e `mensuracao_beneficio`: tratamento de estoque/defasagem
compra→venda, efeito do markup na faixa, e o critério do semáforo (`tolerancia_pp=5.0`).

---

## 4. CONHECIMENTO QUE NÃO PODE SE PERDER
(evita a próxima IA re-derivar — tudo isto foi comprovado com dado real)

- **Saídas NF-e = BUSCA por autXML** com o cert da Nescon (`nfe.puxar_saidas_escritorio`).
  **NÃO** é `consChNFe` por chave: o cert do próprio emitente retorna **`cStat 641`**. Já se tentou; foi revertido.
- **Monofásico = por NCM** (Tabela SPED 4.3.10 + Leis 9.718/10.147/10.485/13.097).
  **CST de PIS/COFINS não serve no Simples** (usam 49/99).
- **Economia:** `faturamento_monofásico × alíquota_efetiva × 15,5%` (PIS 2,76 + COFINS 12,74 — Anexo I).
- **RBT12 (política implementada):** 12+ meses → soma real (`pgdas`); 6–11 → proporcionaliza **só se o
  cadastro estiver vazio**; **<6 meses não extrapola** (1 mês × 12 mudaria a faixa — testado).
- **CFOP:** base tributável é só **venda**; remessa/retorno/brinde/devolução/transferência ficam fora.
- **Anti-bloqueio:** `cStat 656` = consumo indevido → cooldown 1h; nunca repetir consulta do NSU 0.
- **Números de referência (usar como teste de regressão):**
  | Verificação | Valor esperado |
  |---|---|
  | CH DA SILVA jun/2026 — saída bruta | R$ 257.299,21 |
  | CH DA SILVA jun/2026 — base tributável (CFOP) | **R$ 115.050,00** (resto = retorno 6902) |
  | ALINHAR — % compras monofásicas | **96,6%** (autopeças) |
  | QUEIJEIRO — % compras monofásicas | **18,5%** (bebidas frias) |
  | Economia teste sintético (R$1.000 mono, Anexo I faixa 1) | **R$ 6,20** |

---

## 5. COMO TESTAR (comandos usados nesta sessão)

```bash
# sintaxe
python -c "import ast; [ast.parse(open(f,encoding='utf-8').read(),f) for f in ['app.py','models.py','engines/conferencia.py','engines/monofasico.py','engines/cfop.py']]"

# classificadores
python engines/monofasico.py          # NCM: medicamento/pneu/autopeça = MONO; têxtil = não
python engines/cfop.py                # 5124 base=True; 6902 retorno base=False

# telas (test client, banco real)
python - <<'PY'
import sys; sys.path.insert(0,'.')
import app as A
cl=A.app.test_client(); cl.post('/login',data={'login':'admin','senha':'admin'},follow_redirects=True)
for u in ['/','/fiscal/economia','/fiscal/economia?modo=estimativa&aplicado=1',
          '/fiscal/economia/mensuracao','/fiscal/faturamento','/fiscal/conferencia',
          '/fiscal/auditoria','/importar/pgdas','/downloads','/ajuda']:
    print(u, cl.get(u).status_code)
PY
```

⚠️ **Reiniciar o servidor** após mudar rotas/templates (`debug=False`, sem auto-reload).

---

## 6. RESUMO DA ORDEM RECOMENDADA
1. **1.1** Importação em lote do PGDAS ← maior destravamento por esforço
2. **2.4** Cifrar senha do certificado ← maior risco aberto
3. **2.1 + 2.2** Deploy + push ← o sistema precisa sair da máquina local
4. **1.2 / 1.3** autXML e certificados ← ampliam a base de dado real
5. **3.1 / 3.2** Consolidar telas e rotular estimativa ← antes de usar com cliente
6. Só então reabrir discussão de novas análises (ICMS-ST etc.)
