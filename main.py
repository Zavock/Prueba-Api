from fastapi import FastAPI, status
from pydantic import BaseModel
from typing import Optional, Any
import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import json
import pandas as pd  # <-- agregado para leer Google Sheets

from servicios_api import obtener_workflows, activar_flujo

# --- Configuración ---
env_path = Path(__file__).parent / ".env"
print(f"Intentando cargar .env desde: {env_path}")
print(f"¿Existe el archivo?: {env_path.exists()}")

if 'OPENAI_API_KEY' in os.environ:
    old_key = os.environ.pop('OPENAI_API_KEY')
    print(f"Removida OPENAI_API_KEY del sistema: {old_key[:10]}...")

if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    print(".env cargado desde directorio del proyecto")
else:
    print("No se encontró .env en el directorio del proyecto")
    load_dotenv()

OPENAI_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_PROJECT_ID = os.getenv("OPENAI_PROJECT_ID")
MODO_SIMULACION = os.getenv("MODO_SIMULACION", "true").lower() == "true"
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Debug ---
logger.info(f"Archivo .env utilizado: {env_path}")
logger.info(f"OPENAI_KEY cargada: {'Sí' if OPENAI_KEY else 'No'}")
logger.info(f"MODO_SIMULACION: {MODO_SIMULACION}")
logger.info(f"GOOGLE_SHEET_ID: {GOOGLE_SHEET_ID}")

if not OPENAI_KEY:
    logger.error(
        "ERROR: OPENAI_API_KEY no está configurada en el archivo .env")
    raise ValueError("OPENAI_API_KEY es requerida")

# --- Cliente OpenAI ---
try:
    client_ai = OpenAI(api_key=OPENAI_KEY)
    logger.info(
        f"Cliente OpenAI inicializado {'(Project Key sin Project ID)' if OPENAI_KEY.startswith('sk-proj-') else '(Personal Key)'}")
except Exception as e:
    logger.error(f"Error inicializando cliente OpenAI: {e}")
    raise

# --- FastAPI ---
app = FastAPI(title="Orquestador de Workflows con IA", version="2.3.0")

# --- Modelos ---


class DatosEntrada(BaseModel):
    workflowId: str
    datos: dict[str, Any]


class DatosSalida(BaseModel):
    status: str
    mensaje: str
    siguienteFlujoId: Optional[str]
    siguienteFlujoNombre: Optional[str]
    parametros: Optional[list[Any]]
    resultado_api: Optional[Any]

# --- Leer reglas desde Google Sheets ---


def leer_reglas_google_sheet(sheet_id: str):
    if not sheet_id:
        logger.warning("No se configuró GOOGLE_SHEET_ID en el .env")
        return []
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
    try:
        df = pd.read_csv(url)
        reglas = df.to_dict(orient="records")
        logger.info(f"{len(reglas)} reglas cargadas desde Google Sheet")
        return reglas
    except Exception as e:
        logger.error(f"Error leyendo Google Sheet: {e}")
        return []

# --- Función IA ---


def consultar_ia(workflowId: str, datos: dict, workflows: dict):
    try:
        logger.info("Iniciando consulta a OpenAI...")

        reglas = leer_reglas_google_sheet(GOOGLE_SHEET_ID)

        prompt = f"""
        Tenemos un workflow actual con ID: {workflowId}.
        Estos son los datos asociados: {datos}.
        Reglas de negocio desde Google Sheet: {reglas}.
        
        Tarea: Decide explícitamente cuál es el siguiente workflow que debe activarse
        basándote en esas reglas. Si ninguna aplica, responde con 'Escalamiento manual'.

        Devuelve en JSON con el siguiente formato:
        {{
            "siguienteFlujoId": "id_del_workflow",
            "siguienteFlujoNombre": "nombre_del_workflow",
            "parametros": ["param1", "param2"]
        }}
        """

        resp = client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres un motor de decisiones de flujos. Siempre responde con JSON válido."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            max_tokens=500,
            temperature=0.1
        )

        logger.info("Consulta a OpenAI exitosa")
        return resp.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"Error consultando IA: {e}")
        return json.dumps({
            "siguienteFlujoId": None,
            "siguienteFlujoNombre": "Escalamiento manual",
            "parametros": []
        })

# --- Endpoint de salud ---


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "modo_simulacion": MODO_SIMULACION,
        "openai_configurado": bool(OPENAI_KEY),
        "google_sheet_id": GOOGLE_SHEET_ID,
        "env_path": str(env_path),
        "env_exists": env_path.exists()
    }

# --- Endpoint principal ---


@app.post("/procesarWorkflow", response_model=DatosSalida, status_code=status.HTTP_201_CREATED)
async def procesar_workflow(datos: DatosEntrada):
    logger.info(f"Procesando workflow: {datos.workflowId}")

    if MODO_SIMULACION:
        return DatosSalida(
            status="ok",
            mensaje="Simulación exitosa",
            siguienteFlujoId="2628de5f-1dec-4ebd-b0de-359a09b09210",
            siguienteFlujoNombre="JUAN_v2",
            parametros=["Plantilla_PDF_01", "Datos"],
            resultado_api={
                "status": "simulado",
                "mensaje": "Flujo 'JUAN_v2' procesado en simulación",
                "workflowId": datos.workflowId
            }
        )

    try:
        workflows = await obtener_workflows()
    except Exception as e:
        logger.error(f"No se pudieron obtener workflows: {e}")
        workflows = {}

    decision_raw = consultar_ia(datos.workflowId, datos.datos, workflows)
    logger.info(f"Respuesta IA cruda: {decision_raw}")

    try:
        decision = json.loads(decision_raw)
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando respuesta de IA: {e}")
        decision = {
            "siguienteFlujoId": None,
            "siguienteFlujoNombre": "Escalamiento manual",
            "parametros": []
        }

    try:
        if not decision.get("siguienteFlujoId"):
            resultado_api = {
                "status": "error",
                "mensaje": "No se pudo determinar el flujo siguiente",
                "workflowId": datos.workflowId
            }
        else:
            startedByUserId = datos.datos.get("startedByUserId")
            resultado_api = await activar_flujo(decision["siguienteFlujoId"], startedByUserId)
    except Exception as e:
        logger.error(f"Error enviando a Workflow Start: {e}")
        return DatosSalida(
            status="error",
            mensaje=f"No se pudo enviar la información al workflow externo: {str(e)}",
            siguienteFlujoId=decision.get("siguienteFlujoId"),
            siguienteFlujoNombre=decision.get("siguienteFlujoNombre"),
            parametros=decision.get("parametros"),
            resultado_api=None
        )

    return DatosSalida(
        status="ok",
        mensaje="Workflow procesado correctamente",
        siguienteFlujoId=decision.get("siguienteFlujoId"),
        siguienteFlujoNombre=decision.get("siguienteFlujoNombre"),
        parametros=decision.get("parametros"),
        resultado_api=resultado_api
    )
