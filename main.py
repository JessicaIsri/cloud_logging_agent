"""
API REST — Cloud Monitor Agent
Expõe o agente ADK como endpoint HTTP via FastAPI.

Endpoints:
  POST /analyze   — analisa logs e alertas de um recurso GCP
  GET  /health    — healthcheck
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

# Importa a factory do agente
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from cloud_logging_agent.agent import create_agent

# ---------------------------------------------------------------------------
# Estado global (agent + runner inicializados no startup)
# ---------------------------------------------------------------------------
_runner: Optional[Runner] = None
_exit_stack = None
APP_NAME = "cloud-monitor-agent"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa o agente e o runner na subida da API."""
    global _runner

    print("🚀 Inicializando Cloud Monitor Agent...")

    # McpToolset com StdioConnectionParams NÃO é async — create_agent() retorna
    # diretamente, sem await. O processo npx sobe em background quando o Runner
    # executa o primeiro request.
    agent = create_agent()

    session_service = InMemorySessionService()
    _runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    # Aquecimento: dá tempo ao processo npx inicializar antes do 1º request real.
    # Sem isso, o TaskGroup do ADK recebe chamadas antes do MCP estar pronto.
    print("🔥 Aguardando MCP inicializar...")
    await asyncio.sleep(3)
    print("✅ Agente pronto!")
    yield


app = FastAPI(
    title="Cloud Monitor Agent API",
    description="Agente ADK que analisa logs e alertas de Cloud Run via MCP de Observabilidade GCP.",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Schemas de entrada e saída
# ---------------------------------------------------------------------------

# Severidades válidas do Cloud Logging (em ordem crescente)
SeverityLevel = Literal["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]


class AnalyzeRequest(BaseModel):
    project_id: str = Field(
        ...,
        description="ID do projeto GCP (ex: meu-projeto-123)",
        examples=["meu-projeto-prod"],
    )
    resource_type: str = Field(
        ...,
        description=(
            "Tipo do recurso GCP conforme o Cloud Logging "
            "(ex: cloud_run_revision, cloud_run_job, gce_instance, k8s_container, cloudsql_database)."
        ),
        examples=["cloud_run_revision"],
    )
    resource_id: Optional[str] = Field(
        default=None,
        description=(
            "ID/nome do recurso específico. "
            "Se omitido, analisa TODOS os recursos do resource_type informado no projeto."
        ),
        examples=["minha-api"],
    )
    severity: SeverityLevel = Field(
        default="ERROR",
        description=(
            "Severidade mínima dos logs a buscar. "
            "O filtro usará 'severity >= VALOR', então INFO retorna INFO, WARNING, ERROR, etc. "
            "Valores: DEBUG, INFO, NOTICE, WARNING, ERROR, CRITICAL, ALERT, EMERGENCY."
        ),
        examples=["ERROR"],
    )
    hours_back: Optional[int] = Field(
        default=3,
        ge=1,
        le=72,
        description=(
            "Janela de tempo em horas a partir de agora (padrão: 3h). "
            "Ignorado se start_time e end_time forem informados."
        ),
    )
    start_time: Optional[datetime] = Field(
        default=None,
        description=(
            "Início do intervalo de busca (ISO 8601). "
            "Ex: '2026-03-27T00:00:00Z'. Se informado, end_time também deve ser informado."
        ),
        examples=["2026-03-27T00:00:00Z"],
    )
    end_time: Optional[datetime] = Field(
        default=None,
        description=(
            "Fim do intervalo de busca (ISO 8601). "
            "Ex: '2026-03-27T23:59:59Z'. Se informado, start_time também deve ser informado."
        ),
        examples=["2026-03-27T23:59:59Z"],
    )
    region: Optional[str] = Field(
        default=None,
        description="Região GCP (ex: us-central1). Opcional — refina o filtro.",
        examples=["us-central1"],
    )

    @model_validator(mode="after")
    def validate_time_range(self) -> "AnalyzeRequest":
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time e end_time devem ser informados juntos.")
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValueError("start_time deve ser anterior a end_time.")
        return self


class AnalyzeResponse(BaseModel):
    request_id: str
    project_id: str
    resource_type: str
    resource_id: Optional[str]
    severity: str
    analyzed_at: str
    report: str


class HealthResponse(BaseModel):
    status: str
    agent_ready: bool
    timestamp: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_prompt(req: AnalyzeRequest) -> str:
    """Monta o prompt enviado ao agente com os parâmetros da requisição."""
    region_info = f" na região {req.region}" if req.region else ""


    if req.start_time and req.end_time:
        time_info = (
            f"no intervalo de '{req.start_time.strftime('%Y-%m-%dT%H:%M:%SZ')}' "
            f"até '{req.end_time.strftime('%Y-%m-%dT%H:%M:%SZ')}'"
        )
    else:
        time_info = f"nas últimas {req.hours_back} horas"


    if req.resource_id:
        scope = (
            f"o recurso '{req.resource_id}' do tipo '{req.resource_type}' "
            f"no projeto '{req.project_id}'{region_info}"
        )
    else:
        scope = (
            f"TODOS os recursos do tipo '{req.resource_type}' "
            f"no projeto '{req.project_id}'{region_info}. "
            f"No filtro de logs, omita o label de nome/ID do recurso — "
            f"use apenas resource.type para restringir o tipo"
        )

    return (
        f"Analise {scope}. "
        f"Busque logs com severity >= {req.severity} {time_info} "
        f"e verifique alertas ativos. "
        f"Gere o relatório completo conforme as instruções."
    )


async def _run_agent(prompt: str) -> str:
    """Executa o agente e coleta a resposta final."""
    if _runner is None:
        raise RuntimeError("Runner não inicializado.")

    session_id = str(uuid.uuid4())
    user_id = "api-user"

    await _runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )

    user_message = Content(role="user", parts=[Part(text=prompt)])

    final_response = ""
    async for event in _runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_message,
    ):

        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_response += part.text

    return final_response.strip() or "⚠️ O agente não retornou uma resposta."


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, tags=["Status"])
async def health():
    """Healthcheck da API e do agente."""
    return HealthResponse(
        status="ok",
        agent_ready=_runner is not None,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/analyze", response_model=AnalyzeResponse, tags=["Análise"])
async def analyze(req: AnalyzeRequest):
    """
    Analisa logs e alertas de um recurso Cloud Run ou Cloud Run Job.

    Retorna um relatório estruturado com:
    - Resumo executivo (status geral)
    - Erros encontrados nas últimas N horas
    - Alertas ativos do projeto
    - Diagnóstico e recomendações
    """
    if _runner is None:
        raise HTTPException(status_code=503, detail="Agente não está pronto. Tente novamente.")

    prompt = _build_prompt(req)

    try:
        report = await _run_agent(prompt)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao executar o agente: {str(e)}",
        )

    return AnalyzeResponse(
        request_id=str(uuid.uuid4()),
        project_id=req.project_id,
        resource_type=req.resource_type,
        resource_id=req.resource_id,
        severity=req.severity,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
        report=report,
    )