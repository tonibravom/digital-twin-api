import os
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
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

    Nota: se consulta cada día por separado (en vez de traer 15 días de golpe)
    para evitar el límite por defecto de 1000 filas de Supabase, que cortaba
    las lecturas más recientes (las de hoy).
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
        hoy = ahora_local.date()
        hora_actual = ahora_local.time()

        def suma_dia(dia_local) -> Optional[float]:
            inicio_local = datetime.combine(dia_local, dtime.min, tzinfo=ZONA_HORARIA)
            fin_local = datetime.combine(dia_local, hora_actual, tzinfo=ZONA_HORARIA)

            resp = (
                supabase.table("lecturas")
                .select("valor")
                .eq("sensor_id", sensor_numeric_id)
                .gte("timestamp", inicio_local.astimezone(timezone.utc).isoformat())
                .lte("timestamp", fin_local.astimezone(timezone.utc).isoformat())
                .execute()
            )

            if not resp.data:
                return None
            return sum((fila["valor"] or 0) for fila in resp.data)

        hoy_acumulado = suma_dia(hoy) or 0

        ayer = hoy - timedelta(days=1)
        ayer_acumulado = suma_dia(ayer)

        dias_laborables_valores = []
        cursor = hoy - timedelta(days=1)
        intentos = 0
        while len(dias_laborables_valores) < 5 and intentos < 20:
            if cursor.weekday() < 5:
                valor_dia = suma_dia(cursor)
                if valor_dia is not None:
                    dias_laborables_valores.append(valor_dia)
            cursor -= timedelta(days=1)
            intentos += 1

        media_5_laborables = (
            sum(dias_laborables_valores) / len(dias_laborables_valores)
            if dias_laborables_valores else None
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
            "dias_laborables_usados": len(dias_laborables_valores),
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

from datetime import date
from pydantic import BaseModel


class HorarioActividad(BaseModel):
    hora_inicio_actividad: str
    hora_fin_actividad: str


class DiaCalendario(BaseModel):
    fecha: date
    activo: bool


@app.get("/api/edificios/{edificio_id}/configuracion")
def obtener_configuracion_edificio(edificio_id: int):
    respuesta = (
        supabase
        .table("edificios")
        .select(
            "id, nombre, hora_inicio_actividad, hora_fin_actividad"
        )
        .eq("id", edificio_id)
        .single()
        .execute()
    )

    calendario = (
        supabase
        .table("edificios_calendario")
        .select("id, fecha, activo")
        .eq("edificio_id", edificio_id)
        .order("fecha")
        .execute()
    )

    return {
        "edificio": respuesta.data,
        "calendario": calendario.data
    }


@app.put("/api/edificios/{edificio_id}/horario")
def actualizar_horario(
    edificio_id: int,
    horario: HorarioActividad
):
    respuesta = (
        supabase
        .table("edificios")
        .update({
            "hora_inicio_actividad": horario.hora_inicio_actividad,
            "hora_fin_actividad": horario.hora_fin_actividad
        })
        .eq("id", edificio_id)
        .execute()
    )

    return respuesta.data


@app.put("/api/edificios/{edificio_id}/calendario")
def actualizar_dia_calendario(
    edificio_id: int,
    dia: DiaCalendario
):
    respuesta = (
        supabase
        .table("edificios_calendario")
        .upsert(
            {
                "edificio_id": edificio_id,
                "fecha": dia.fecha.isoformat(),
                "activo": dia.activo
            },
            on_conflict="edificio_id,fecha"
        )
        .execute()
    )

    return respuesta.data
