import os
from datetime import datetime, timedelta, timezone
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


@app.get("/api/sensores/{codigo}/comparativa")
def comparativa_energia(codigo: str):
    """
    Compara el acumulado de HOY (desde medianoche hasta la hora actual) de un
    sensor de energía contra:
      - el acumulado de AYER hasta la misma hora
      - la media de los últimos 5 días laborables hasta la misma hora
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

        ahora_local = datetime.now(ZONA_HORARIA)
        hora_actual = ahora_local.time()

        # margen de 15 días naturales para asegurar 5 días laborables completos
        desde_local = (ahora_local - timedelta(days=15)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        desde_utc = desde_local.astimezone(timezone.utc).isoformat()

        lecturas_resp = (
            supabase.table("lecturas")
            .select("valor, timestamp")
            .eq("sensor_id", sensor_numeric_id)
            .gte("timestamp", desde_utc)
            .order("timestamp", desc=False)
            .execute()
        )

        acumulados_por_dia = {}
        for fila in lecturas_resp.data:
            ts_local = datetime.fromisoformat(fila["timestamp"]).astimezone(ZONA_HORARIA)
            if ts_local.time() <= hora_actual:
                dia = ts_local.date()
                acumulados_por_dia[dia] = acumulados_por_dia.get(dia, 0) + (fila["valor"] or 0)

        hoy = ahora_local.date()
        hoy_acumulado = acumulados_por_dia.get(hoy, 0)

        ayer = hoy - timedelta(days=1)
        ayer_acumulado = acumulados_por_dia.get(ayer)

        dias_laborables = []
        cursor = hoy - timedelta(days=1)
        limite = hoy - timedelta(days=20)
        while len(dias_laborables) < 5 and cursor > limite:
            if cursor.weekday() < 5 and cursor in acumulados_por_dia:
                dias_laborables.append(acumulados_por_dia[cursor])
            cursor -= timedelta(days=1)

        media_5_laborables = (
            sum(dias_laborables) / len(dias_laborables) if dias_laborables else None
        )

        def pct_diff(actual, referencia):
            if referencia in (None, 0):
                return None
            return round((actual - referencia) / referencia * 100, 1)

        return {
            "hoy_acumulado": round(hoy_acumulado, 2),
            "ayer_misma_hora": round(ayer_acumulado, 2) if ayer_acumulado is not None else None,
            "media_5_laborables_misma_hora": round(media_5_laborables, 2) if media_5_laborables is not None else None,
            "vs_ayer_pct": pct_diff(hoy_acumulado, ayer_acumulado),
            "vs_media_pct": pct_diff(hoy_acumulado, media_5_laborables),
            "dias_laborables_usados": len(dias_laborables),
        }

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
