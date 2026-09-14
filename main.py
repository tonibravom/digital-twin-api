import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from typing import Optional

ZONA_HORARIA = ZoneInfo("Europe/Madrid")

app = FastAPI(title="Digital Twin API")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Faltan las variables SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY")

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)

# --- CORS ---
# Dominio desde el que se sirve tu dashboard (GitHub Pages)
origins = [
    "https://tonibravom.github.io",
    "http://localhost:3000",
    "http://127.0.0.1:5500",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "digital-twin-api"
    }


# --- Endpoints existentes: catálogo de edificios y sensores ---

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


# --- Endpoints nuevos: lecturas históricas ---

@app.get("/api/lecturas")
def obtener_lecturas(
    sensor_id: Optional[str] = Query(None, description="ID del sensor a filtrar"),
    desde: Optional[str] = Query(None, description="Fecha/hora inicio ISO8601, ej: 2026-09-01T00:00:00"),
    hasta: Optional[str] = Query(None, description="Fecha/hora fin ISO8601"),
    limit: int = Query(1000, le=10000, description="Número máximo de registros"),
):
    """
    Consulta lecturas históricas de sensores.
    Filtra opcionalmente por sensor_id y rango de fechas (columna 'timestamp').
    """
    try:
        query = supabase.table("lecturas").select("*")

        if sensor_id:
            query = query.eq("sensor_id", sensor_id)
        if desde:
            query = query.gte("timestamp", desde)
        if hasta:
            query = query.lte("timestamp", hasta)

        query = query.order("timestamp", desc=False).limit(limit)

        respuesta = query.execute()
        return {"count": len(respuesta.data), "data": respuesta.data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _inicio_dia_utc_iso() -> str:
    """Medianoche de hoy en hora de Madrid, convertida a UTC ISO8601."""
    ahora_local = datetime.now(ZONA_HORARIA)
    inicio_local = ahora_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return inicio_local.astimezone(timezone.utc).isoformat()


@app.get("/api/sensores/{codigo}/valores")
def obtener_valores_sensor(codigo: str):
    """
    Sustituto directo de los antiguos ficheros datos_sensores/<codigo>.json.
    Devuelve {"values": [...]} con las lecturas de HOY (hora de Madrid) para
    el sensor identificado por su código de texto (columna 'sensor_id' en
    la tabla 'sensores').
    """
    try:
        sensor_resp = (
            supabase.table("sensores")
            .select("id")
            .eq("sensor_id", codigo)
            .limit(1)
            .execute()
        )

        if not sensor_resp.data:
            raise HTTPException(status_code=404, detail=f"Sensor '{codigo}' no encontrado")

        sensor_numeric_id = sensor_resp.data[0]["id"]
        desde = _inicio_dia_utc_iso()

        lecturas_resp = (
            supabase.table("lecturas")
            .select("valor, timestamp")
            .eq("sensor_id", sensor_numeric_id)
            .gte("timestamp", desde)
            .order("timestamp", desc=False)
            .execute()
        )

        valores = [fila["valor"] for fila in lecturas_resp.data]
        return {"values": valores}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/lecturas/sensores-disponibles")
def obtener_sensores_con_lecturas():
    """
    Lista de sensor_id distintos presentes en la tabla 'lecturas'.
    Útil para poblar selectores en el dashboard.
    """
    try:
        respuesta = supabase.table("lecturas").select("sensor_id").execute()
        sensores = sorted({fila["sensor_id"] for fila in respuesta.data if fila.get("sensor_id")})
        return {"sensores": sensores}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
