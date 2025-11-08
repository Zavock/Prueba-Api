import httpx
import os
from dotenv import load_dotenv
import logging

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL")
WORKFLOW_URL = f"{API_BASE_URL}/Workflow/Start"
AUTH_URL = f"{API_BASE_URL}/Auth/ServiceToken"
WORKFLOW_GET_ALL_URL = f"{API_BASE_URL}/Workflow/GetAll"

TIMEOUT = int(os.getenv("API_TIMEOUT", 10))
SERVICE_NAME = os.getenv("SERVICE_NAME")
SERVICE_SECRET = os.getenv("SERVICE_SECRET")
SERVICE_SCOPES = ["read"]

service_token = None
logger = logging.getLogger(__name__)


async def obtener_token():
    """
    Solicita un ServiceToken a la API externa y lo cachea en memoria.
    """
    global service_token
    if service_token:
        return service_token

    payload = {
        "serviceName": SERVICE_NAME,
        "serviceSecret": SERVICE_SECRET,
        "requestedScopes": SERVICE_SCOPES
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(AUTH_URL, json=payload)
    resp.raise_for_status()
    data = resp.json()

    logger.info(f"Respuesta autenticación: {data}")

    token = data.get("token")
    if not token:
        raise ValueError("No se recibió token en la autenticación.")

    service_token = token
    return service_token


async def obtener_workflows():
    """
    Consulta todos los workflows disponibles en la API.
    """
    token = await obtener_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = WORKFLOW_GET_ALL_URL

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def activar_flujo(workflowId: str, startedByUserId: str):
    """
    Llama al endpoint de Workflow/Start para activar el siguiente flujo.
    """
    token = await obtener_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{WORKFLOW_URL}/{workflowId}"
    payload = {"startedByUserId": startedByUserId}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()
