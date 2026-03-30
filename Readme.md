# 🔍 Cloud Monitor Agent

Agente de observabilidade construído com **Google Agent Development Kit (ADK)** e **FastAPI**, que analisa logs e alertas de qualquer recurso GCP usando o MCP oficial do Google Cloud (`@google-cloud/observability-mcp`).

---

## 🏗️ Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                      Cliente (REST)                             │
│   POST /analyze  { project_id, resource_type, severity, ... }  │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    FastAPI  (api/main.py)                        │
│  • Valida request (Pydantic)                                     │
│  • Monta prompt para o agente                                    │
│  • Executa Runner ADK                                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│           ADK LlmAgent  (cloud_logging_agent/agent.py)          │
│  • Modelo: gemini-2.5-flash                                      │
│  • Raciocina sobre qual ferramenta chamar                        │
│  • Interpreta resultados e gera relatório                        │
└────────────────────────────┬────────────────────────────────────┘
                             │  McpToolset (stdio)
┌────────────────────────────▼────────────────────────────────────┐
│     @google-cloud/observability-mcp  (processo Node.js/npx)     │
│  Ferramentas expostas:                                           │
│  • list_log_entries    → Cloud Logging API                       │
│  • list_alert_policies → Cloud Monitoring API                    │
│  • list_time_series    → Cloud Monitoring Metrics                │
│  • list_log_names      → Cloud Logging API                       │
└────────────────────────────┬────────────────────────────────────┘
                             │  Application Default Credentials
┌────────────────────────────▼────────────────────────────────────┐
│              Google Cloud APIs                                   │
│         Cloud Logging  •  Cloud Monitoring                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📋 Pré-requisitos

| Requisito | Versão mínima |
|-----------|---------------|
| Python | 3.11+ |
| Node.js | 18 LTS+ |
| Google Cloud SDK (`gcloud`) | qualquer versão recente |

### APIs GCP necessárias

```bash
gcloud services enable \
  logging.googleapis.com \
  monitoring.googleapis.com \
  --project=SEU_PROJETO
```

---

## ⚙️ Configuração

### 1. Instale as dependências

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

### 2. Autentique no GCP

```bash
# Login do usuário
gcloud auth login

# Application Default Credentials (usado pelo MCP)
gcloud auth application-default login

# Define o projeto de quota para as APIs de observabilidade
gcloud auth application-default set-quota-project SEU_PROJETO
```

### 3. Configure o `.env`

Crie um arquivo `.env` na raiz do projeto:

```env
# Gemini API Key (opção para desenvolvimento)
GOOGLE_API_KEY=sua-gemini-api-key

# Projeto GCP padrão
GOOGLE_CLOUD_PROJECT=seu-projeto-gcp

# Vertex AI (opção para produção — substitui GOOGLE_API_KEY)
# GOOGLE_GENAI_USE_VERTEXAI=true
# GOOGLE_CLOUD_LOCATION=us-central1

# Servidor
HOST=0.0.0.0
PORT=8080
LOG_LEVEL=info
```

### 4. Pré-baixe o pacote MCP (recomendado)

Evita delay no primeiro request:

```bash
npx -y @google-cloud/observability-mcp --version
```

---

## 🚀 Executando

### API REST (FastAPI)

```bash
python run.py
# API disponível em http://localhost:8080
# Swagger UI em http://localhost:8080/docs
```

### Interface web do ADK (para testes)

```bash
# Execute a partir da pasta PAI do pacote
cd ~/Documentos/codes
adk web
# Acesse http://localhost:8000 e selecione "cloud_logging_agent" no dropdown
```

---

## 📡 Endpoints

### `POST /analyze`

Analisa logs e alertas de um recurso GCP.

**Campos da requisição:**

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|-------------|-----------|
| `project_id` | string | ✅ | ID do projeto GCP |
| `resource_type` | string | ✅ | Tipo do recurso conforme Cloud Logging (ver tabela abaixo) |
| `resource_id` | string | ❌ | Nome/ID do recurso. Se omitido, analisa todos do tipo |
| `severity` | string | ❌ | Severidade mínima (padrão: `ERROR`). Ver níveis abaixo |
| `hours_back` | int | ❌ | Horas a partir de agora (padrão: `3`, máx: `72`). Ignorado se usar intervalo |
| `start_time` | datetime | ❌ | Início do intervalo (ISO 8601). Requer `end_time` |
| `end_time` | datetime | ❌ | Fim do intervalo (ISO 8601). Requer `start_time` |
| `region` | string | ❌ | Região GCP para refinar o filtro |

**Níveis de severidade** (em ordem crescente):

`DEBUG` → `INFO` → `NOTICE` → `WARNING` → `ERROR` → `CRITICAL` → `ALERT` → `EMERGENCY`

O filtro usa `severity >= VALOR`, então `WARNING` retorna WARNING, ERROR, CRITICAL, etc.

**Tipos de recurso comuns:**

| resource_type | Recurso GCP |
|---------------|-------------|
| `cloud_run_revision` | Cloud Run Services |
| `cloud_run_job` | Cloud Run Jobs |
| `gce_instance` | Compute Engine (VMs) |
| `k8s_container` | GKE / Kubernetes |
| `cloudsql_database` | Cloud SQL |
| `cloud_function` | Cloud Functions |
| `bigquery_resource` | BigQuery |

**Exemplos de requisição:**

```bash
# Cloud Run Service específico — últimas 3h, erros
curl -X POST http://localhost:8080/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "meu-projeto",
    "resource_type": "cloud_run_revision",
    "resource_id": "minha-api",
    "severity": "ERROR"
  }'

# Todos os Cloud Run Jobs — últimas 6h, warnings+
curl -X POST http://localhost:8080/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "meu-projeto",
    "resource_type": "cloud_run_job",
    "severity": "WARNING",
    "hours_back": 6
  }'

# Intervalo de data específico
curl -X POST http://localhost:8080/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "meu-projeto",
    "resource_type": "gce_instance",
    "severity": "ERROR",
    "start_time": "2026-03-27T00:00:00Z",
    "end_time": "2026-03-27T23:59:59Z"
  }'

# Com região específica
curl -X POST http://localhost:8080/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "meu-projeto",
    "resource_type": "k8s_container",
    "resource_id": "meu-container",
    "severity": "WARNING",
    "region": "us-central1",
    "hours_back": 12
  }'
```

**Exemplo de resposta:**

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "project_id": "meu-projeto",
  "resource_type": "cloud_run_revision",
  "resource_id": "minha-api",
  "severity": "ERROR",
  "analyzed_at": "2026-03-27T14:00:00Z",
  "report": "### 📊 Resumo Executivo\n🔴 Crítico — 47 erros encontrados..."
}
```

---

### `GET /health`

Healthcheck da API e do agente.

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "agent_ready": true,
  "timestamp": "2026-03-27T14:00:00Z"
}
```

---

## 📁 Estrutura do Projeto

```
.
├── cloud_logging_agent/
│   ├── __init__.py
│   └── agent.py          # LlmAgent ADK + McpToolset
├── main.py           # FastAPI: endpoints, schemas, runner
├── run.py                # Entry point (uvicorn)
├── pyproject.toml        # Dependências Python
├── .env                  # Variáveis de ambiente (não commitar)
└── README.md
```

---

## 🔐 IAM — Permissões necessárias

```bash
# Criar service account dedicado
gcloud iam service-accounts create monitoring-agent-sa \
  --display-name="Cloud Monitor Agent SA" \
  --project=SEU_PROJETO

# Conceder permissões mínimas
gcloud projects add-iam-policy-binding SEU_PROJETO \
  --member="serviceAccount:monitoring-agent-sa@SEU_PROJETO.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"

gcloud projects add-iam-policy-binding SEU_PROJETO \
  --member="serviceAccount:monitoring-agent-sa@SEU_PROJETO.iam.gserviceaccount.com" \
  --role="roles/monitoring.viewer"
```

---

## 🛠️ Ferramentas MCP utilizadas

| Ferramenta | API GCP | Uso |
|------------|---------|-----|
| `list_log_entries` | Cloud Logging | Busca logs por recurso, severidade e janela de tempo |
| `list_alert_policies` | Cloud Monitoring | Lista alertas ativos do projeto |
| `list_time_series` | Cloud Monitoring | Métricas adicionais |
| `list_log_names` | Cloud Logging | Auxilia na descoberta de filtros |

---

## 💡 Exemplo de relatório gerado

```
### 📊 Resumo Executivo
🔴 Crítico
- Tipo de recurso: cloud_run_revision
- Escopo: serviço 'minha-api' no projeto 'meu-projeto'
- Severidade mínima: ERROR
- Total de entradas: 47 | Alertas ativos: 2
- Período: 2026-03-27T11:00:00Z → 2026-03-27T14:00:00Z

### 📋 Logs Encontrados
[2026-03-27T13:58:42Z] CRITICAL — minha-api (23 ocorrências)
Connection refused: Cloud SQL instance 'prod-db' não acessível
Trace ID: abc123def456

[2026-03-27T13:45:10Z] ERROR — minha-api (24 ocorrências)
Request timeout after 60s — endpoint /api/v1/orders

### 🔔 Alertas Ativos
- "High Error Rate - minha-api": threshold 5% de erros ativado
- "Database Connection Pool Exhausted": ativado

### 🔍 Análise e Diagnóstico
Padrão: falha de conectividade com Cloud SQL.
Causa provável: instância pausada ou IP autorizado removido.

### ✅ Recomendações
1. Verificar status da instância Cloud SQL:
   gcloud sql instances describe prod-db --project=meu-projeto
2. Confirmar IPs autorizados no Console → SQL → Conexões
3. Revisar política de alertas para notificação proativa
```