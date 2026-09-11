import os

from fastapi import FastAPI, HTTPException
from supabase import create_client, Client

app = FastAPI(title="Digital Twin API")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Faltan las variables SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY")

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "digital-twin-api"
    }


@app.get("/api/edificios")
def obtener_edificios():
    respuesta = (
        supabase
        .table("edificios")
        .select("*")
        .order("id")
        .execute()
    )

    return respuesta.data


@app.get("/api/edificios/{edificio_id}/sensores")
def obtener_sensores(edificio_id: int):
    respuesta = (
        supabase
        .table("sensores")
        .select("id, edificio_id, sensor_id, activo, conexion_id, created_at")
        .eq("edificio_id", edificio_id)
        .order("id")
        .execute()
    )

    return respuesta.data
