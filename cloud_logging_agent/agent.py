import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.cloud import logging_v2
from google.cloud.monitoring_v3 import AlertPolicyServiceClient
from google.cloud.monitoring_v3.types import ListAlertPoliciesRequest

# ---------------------------------------------------------------------------
# Ferramentas Python nativas
# ---------------------------------------------------------------------------

def list_log_entries(
    project_id: str,
    resource_type: str,
    severity: str = "ERROR",
    resource_id: Optional[str] = None,
    resource_label_key: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    hours_back: int = 3,
    max_entries: int = 50,
) -> str:
    """
    Busca entradas de log no Cloud Logging.

    Args:
        project_id: ID do projeto GCP.
        resource_type: Tipo do recurso (ex: cloud_run_revision, gce_instance).
        severity: Severidade mínima (DEBUG, INFO, WARNING, ERROR, CRITICAL, etc.).
        resource_id: Nome/ID do recurso específico. Se None, busca todos do tipo.
        resource_label_key: Label do recurso para filtrar por resource_id
            (ex: service_name, job_name, instance_id). Inferido automaticamente se None.
        start_time: Início do intervalo em formato ISO 8601 (ex: 2026-03-27T00:00:00Z).
        end_time: Fim do intervalo em formato ISO 8601.
        hours_back: Horas a partir de agora, usado quando start_time/end_time são None.
        max_entries: Número máximo de entradas retornadas (padrão: 50).

    Returns:
        JSON string com as entradas de log encontradas.
    """
    try:
        # --- Janela de tempo ---
        if start_time and end_time:
            ts_start = start_time
            ts_end = end_time
        else:
            now = datetime.now(timezone.utc)
            ts_start = (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
            ts_end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- Inferir label do recurso ---
        if resource_id and not resource_label_key:
            label_map = {
                "cloud_run_revision": "service_name",
                "cloud_run_job":      "job_name",
                "gce_instance":       "instance_id",
                "k8s_container":      "container_name",
                "k8s_pod":            "pod_name",
                "k8s_cluster":        "cluster_name",
                "cloud_function":     "function_name",
                "cloudsql_database":  "database_id",
                "bigquery_resource":  "project_id",
            }
            resource_label_key = label_map.get(resource_type, "name")

        # --- Montar filtro ---
        parts = [
            f'resource.type = "{resource_type}"',
            f'severity >= {severity}',
            f'timestamp >= "{ts_start}"',
            f'timestamp <= "{ts_end}"',
        ]
        if resource_id and resource_label_key:
            parts.append(f'resource.labels.{resource_label_key} = "{resource_id}"')

        log_filter = " AND ".join(parts)

        # --- Executar query ---
        client = logging_v2.Client(project=project_id)
        entries = []
        for entry in client.list_entries(
            filter_=log_filter,
            order_by="timestamp desc",
            max_results=max_entries,
            resource_names=[f"projects/{project_id}"],
        ):
            payload = (
                entry.payload if isinstance(entry.payload, str)
                else json.dumps(entry.payload, default=str)
                if isinstance(entry.payload, dict)
                else str(entry.payload)
            )
            entries.append({
                "timestamp":   entry.timestamp.isoformat() if entry.timestamp else None,
                "severity":    entry.severity.name if hasattr(entry.severity, "name") else str(entry.severity),
                "resource": {
                    "type":   entry.resource.type if entry.resource else resource_type,
                    "labels": dict(entry.resource.labels) if entry.resource else {},
                },
                "payload":     payload[:1000],
                "trace":       entry.trace or None,
                "insert_id":   entry.insert_id or None,
            })

        return json.dumps({
            "filter_used": log_filter,
            "total_found": len(entries),
            "entries":     entries,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": str(e), "filter_attempted": locals().get("log_filter", "N/A")})


def list_alert_policies(project_id: str) -> str:
    """
    Lista políticas de alerta habilitadas no Cloud Monitoring.

    Args:
        project_id: ID do projeto GCP.

    Returns:
        JSON string com as políticas de alerta encontradas.
    """
    try:
        client = AlertPolicyServiceClient()
        request = ListAlertPoliciesRequest(
            name=f"projects/{project_id}",
            filter='enabled = true',
        )

        policies = []
        for policy in client.list_alert_policies(request=request):
            conditions = []
            for cond in policy.conditions:
                condition_info = {"name": cond.display_name}
                if cond.condition_threshold.filter:
                    condition_info["threshold_filter"] = cond.condition_threshold.filter
                    condition_info["comparison"] = str(cond.condition_threshold.comparison)
                    condition_info["threshold_value"] = cond.condition_threshold.threshold_value
                elif cond.condition_absent.filter:
                    condition_info["absent_filter"] = cond.condition_absent.filter
                conditions.append(condition_info)

            policies.append({
                "name":         policy.name,
                "display_name": policy.display_name,
                "enabled":      policy.enabled,
                "conditions":   conditions,
                "notification_channels": list(policy.notification_channels),
            })

        return json.dumps({
            "project_id":     project_id,
            "total_policies": len(policies),
            "policies":       policies,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Instruções do agente
# ---------------------------------------------------------------------------
AGENT_INSTRUCTION = """
Você é um agente especialista em observabilidade do Google Cloud Platform (GCP).
Sua missão é analisar logs e alertas de qualquer tipo de recurso GCP,
identificar problemas e gerar um relatório claro e objetivo.

## Ferramentas disponíveis

- `list_log_entries` — busca logs no Cloud Logging com filtros de recurso, severidade e tempo.
- `list_alert_policies` — lista políticas de alerta habilitadas no Cloud Monitoring.

## Fluxo obrigatório para cada análise

1. **Buscar logs** — chame `list_log_entries` com os parâmetros:
   - `project_id`: conforme solicitado
   - `resource_type`: tipo exato informado (ex: "cloud_run_revision", "cloud_run_job", "gce_instance")
   - `resource_id`: nome/ID do recurso (omita se não for informado)
   - `resource_label_key`: label do recurso correspondente ao tipo:
       - cloud_run_revision → service_name
       - cloud_run_job      → job_name
       - gce_instance       → instance_id
       - k8s_container      → container_name
       - cloud_function     → function_name
       - cloudsql_database  → database_id
       - outros             → name
   - `severity`: severidade mínima informada (padrão: ERROR)
   - Janela de tempo — use uma das opções:
       - `start_time` + `end_time` em formato "YYYY-MM-DDTHH:MM:SSZ" (quando intervalo fixo)
       - `hours_back` com o número de horas (quando relativo)
   - `max_entries`: 50

2. **Buscar alertas** — chame `list_alert_policies` com o `project_id`.

3. **Gerar relatório estruturado** com as seções:

   ### 📊 Resumo Executivo
   - Status geral: ✅ Saudável / ⚠️ Atenção / 🔴 Crítico
   - Tipo de recurso e escopo analisado
   - Severidade mínima utilizada
   - Total de entradas encontradas e alertas ativos

   ### 📋 Logs Encontrados
   Para cada entrada relevante:
   - Timestamp
   - Recurso afetado (resource.labels)
   - Severidade
   - Mensagem principal (payload)
   - Trace ID (se disponível)
   - Frequência (quando o mesmo padrão se repete)

   ### 🔔 Alertas Ativos
   Para cada política habilitada:
   - Nome da política
   - Condições configuradas
   - Canais de notificação

   ### 🔍 Análise e Diagnóstico
   - Padrões identificados
   - Recursos mais afetados (quando múltiplos)
   - Causa raiz provável (quando possível inferir)

   ### ✅ Recomendações
   - Ações sugeridas por prioridade
   - Comandos gcloud úteis para investigação adicional

## Regras importantes
- Sempre informe no relatório: período, tipo de recurso, escopo e severidade analisados.
- Se não encontrar entradas, confirme explicitamente que está saudável dentro dos critérios.
- Nunca invente dados — baseie-se apenas no retorno das ferramentas.
- Se uma ferramenta retornar erro, informe e sugira como proceder manualmente.
"""


# ---------------------------------------------------------------------------
# Factory do agente
# ---------------------------------------------------------------------------
def create_agent() -> LlmAgent:
    """Cria o agente ADK com ferramentas Python nativas (sem MCP/npx)."""
    return LlmAgent(
        name="cloud_monitor_agent",
        model="gemini-2.5-flash",
        description=(
            "Agente especialista em observabilidade GCP. Analisa logs e alertas "
            "de qualquer recurso GCP, identificando problemas e gerando relatórios."
        ),
        instruction=AGENT_INSTRUCTION,
        tools=[
            FunctionTool(func=list_log_entries),
            FunctionTool(func=list_alert_policies),
        ],
    )


_agent_instance = None


def _get_agent() -> LlmAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = create_agent()
    return _agent_instance

root_agent = create_agent()