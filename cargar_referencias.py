"""Carga las referencias de PeeringDB y mide su proximidad al catastro Yafun."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

import psycopg


DIRECTORIO_DATOS = Path(__file__).resolve().parent / "datos"
CONEXION_BD = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:ruteo_local@127.0.0.1:5433/ruteo",
)


def leer_registros(ruta):
    with ruta.open(encoding="utf-8") as archivo:
        registros = json.load(archivo)["data"]
    if not isinstance(registros, list):
        raise ValueError(f"El campo data de {ruta.name} debe ser una lista.")
    return registros


def completar_intercambios(intercambios, relaciones, directorio_datos):
    """Resuelve los intercambios extranjeros presentes en instalaciones chilenas."""
    por_id = {registro["id"]: registro for registro in intercambios}
    ruta_extra = directorio_datos / "peeringdb_intercambios_complementarios.json"
    complementarios = {}
    if ruta_extra.exists():
        complementarios = {
            registro["id"]: registro for registro in leer_registros(ruta_extra)
        }

    requeridos = {registro["ix_id"] for registro in relaciones}
    for identificador in sorted(requeridos - por_id.keys()):
        if identificador not in complementarios:
            url = f"https://www.peeringdb.com/api/ix/{identificador}?depth=0"
            solicitud = Request(
                url,
                headers={
                    "User-Agent": "Yafun-academic-analysis/1.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(solicitud, timeout=30) as respuesta:
                registros = json.load(respuesta)["data"]
            if len(registros) != 1 or registros[0]["id"] != identificador:
                raise ValueError(f"Respuesta inesperada para intercambio {identificador}.")
            registro = registros[0]
            complementarios[identificador] = {
                campo: registro.get(campo)
                for campo in ["id", "name", "city", "country", "status", "updated"]
            }
        por_id[identificador] = complementarios[identificador]

    if complementarios:
        temporal = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directorio_datos, delete=False
            ) as archivo:
                temporal = Path(archivo.name)
                json.dump(
                    {"data": sorted(complementarios.values(), key=lambda fila: fila["id"])},
                    archivo,
                    ensure_ascii=False,
                    indent=2,
                )
            temporal.replace(ruta_extra)
        finally:
            if temporal is not None:
                temporal.unlink(missing_ok=True)

    return sorted(por_id.values(), key=lambda fila: fila["id"])


def cargar_referencias(directorio_datos=DIRECTORIO_DATOS, conexion_bd=CONEXION_BD):
    instalaciones = leer_registros(directorio_datos / "peeringdb_instalaciones.json")
    intercambios = leer_registros(directorio_datos / "peeringdb_intercambios.json")
    relaciones = leer_registros(directorio_datos / "peeringdb_relaciones.json")
    intercambios = completar_intercambios(intercambios, relaciones, directorio_datos)

    instalaciones_por_id = {fila["id"]: fila for fila in instalaciones}
    faltantes = {fila["fac_id"] for fila in relaciones} - instalaciones_por_id.keys()
    if faltantes:
        raise ValueError(f"Faltan instalaciones referidas por las relaciones: {sorted(faltantes)}")

    with psycopg.connect(conexion_bd) as conexion:
        with conexion.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS public.referencias_instalaciones (
                    id BIGINT PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    ciudad TEXT,
                    pais TEXT,
                    geom geometry(Point, 4326),
                    fuente TEXT NOT NULL,
                    actualizado_fuente TIMESTAMPTZ,
                    cargado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS public.referencias_intercambios (
                    id BIGINT PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    ciudad TEXT,
                    pais TEXT,
                    fuente TEXT NOT NULL,
                    actualizado_fuente TIMESTAMPTZ
                );

                CREATE TABLE IF NOT EXISTS public.referencias_presencias (
                    id BIGINT PRIMARY KEY,
                    instalacion_id BIGINT NOT NULL
                        REFERENCES public.referencias_instalaciones(id),
                    intercambio_id BIGINT NOT NULL
                        REFERENCES public.referencias_intercambios(id)
                );
            """)

            for fila in sorted(instalaciones, key=lambda registro: registro["id"]):
                longitud, latitud = fila.get("longitude"), fila.get("latitude")
                if longitud is None or latitud is None:
                    longitud, latitud = None, None
                else:
                    longitud, latitud = float(longitud), float(latitud)
                    if not (-180 <= longitud <= 180 and -90 <= latitud <= 90):
                        raise ValueError(f"Coordenadas invalidas en instalacion {fila['id']}.")

                cursor.execute("""
                    INSERT INTO public.referencias_instalaciones
                        (id, nombre, ciudad, pais, geom, fuente, actualizado_fuente)
                    VALUES (%s, %s, %s, %s,
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        nombre = EXCLUDED.nombre,
                        ciudad = EXCLUDED.ciudad,
                        pais = EXCLUDED.pais,
                        geom = EXCLUDED.geom,
                        fuente = EXCLUDED.fuente,
                        actualizado_fuente = EXCLUDED.actualizado_fuente,
                        cargado_en = CURRENT_TIMESTAMP;
                """, (
                    fila["id"], fila["name"], fila.get("city"), fila.get("country"),
                    longitud, latitud, f"https://www.peeringdb.com/fac/{fila['id']}",
                    fila.get("updated"),
                ))

            for fila in intercambios:
                cursor.execute("""
                    INSERT INTO public.referencias_intercambios
                        (id, nombre, ciudad, pais, fuente, actualizado_fuente)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        nombre = EXCLUDED.nombre,
                        ciudad = EXCLUDED.ciudad,
                        pais = EXCLUDED.pais,
                        fuente = EXCLUDED.fuente,
                        actualizado_fuente = EXCLUDED.actualizado_fuente;
                """, (
                    fila["id"], fila["name"], fila.get("city"), fila.get("country"),
                    f"https://www.peeringdb.com/ix/{fila['id']}", fila.get("updated"),
                ))

            cursor.executemany("""
                INSERT INTO public.referencias_presencias
                    (id, instalacion_id, intercambio_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    instalacion_id = EXCLUDED.instalacion_id,
                    intercambio_id = EXCLUDED.intercambio_id;
            """, [
                (fila["id"], fila["fac_id"], fila["ix_id"])
                for fila in sorted(relaciones, key=lambda registro: registro["id"])
            ])

            cursor.execute("""
                CREATE OR REPLACE VIEW public.referencias_proximidad AS
                SELECT
                    instalacion.id,
                    instalacion.nombre,
                    instalacion.ciudad,
                    instalacion.geom,
                    ARRAY(
                        SELECT intercambio.nombre
                        FROM public.referencias_presencias AS presencia
                        JOIN public.referencias_intercambios AS intercambio
                            ON intercambio.id = presencia.intercambio_id
                        WHERE presencia.instalacion_id = instalacion.id
                        ORDER BY intercambio.nombre, intercambio.id
                    ) AS intercambios,
                    cercano.id AS enlace_cercano,
                    cercano.distancia_m
                FROM public.referencias_instalaciones AS instalacion
                LEFT JOIN LATERAL (
                    SELECT
                        enlace.id,
                        ST_Distance(
                            instalacion.geom::geography,
                            enlace.geom::geography
                        ) AS distancia_m
                    FROM public.enlaces AS enlace
                    WHERE instalacion.geom IS NOT NULL
                    ORDER BY distancia_m, enlace.id
                    LIMIT 1
                ) AS cercano ON TRUE;
            """)

            cursor.execute("""
                SELECT nombre, ciudad, intercambios, enlace_cercano, distancia_m
                FROM public.referencias_proximidad
                ORDER BY distancia_m NULLS LAST, id;
            """)
            proximidades = cursor.fetchall()

    ubicadas = [fila for fila in proximidades if fila[4] is not None]
    pendientes = [fila for fila in proximidades if fila[4] is None]
    print("Carga completada.")
    print(f"Instalaciones leidas: {len(instalaciones)}")
    print(f"Intercambios leidos, incluidos complementarios: {len(intercambios)}")
    print(f"Relaciones leidas: {len(relaciones)}")
    print(f"Instalaciones con coordenadas y enlace cercano: {len(ubicadas)}")
    print(f"Instalaciones pendientes de ubicacion: {len(pendientes)}")
    print(f"Instalaciones con presencia de intercambio: {sum(bool(fila[2]) for fila in proximidades)}")
    for limite in [100, 500, 1000, 5000, 10000]:
        cantidad = sum(fila[4] <= limite for fila in ubicadas)
        print(f"A {limite} metros o menos de un trazado: {cantidad}")

    print("\nCinco instalaciones mas cercanas al trazado:")
    for nombre, ciudad, intercambios, enlace, distancia in ubicadas[:5]:
        print(f"{nombre} | {ciudad} | enlace {enlace} | {distancia:.1f} m")
    print("\nCinco instalaciones mas alejadas del trazado:")
    for nombre, ciudad, intercambios, enlace, distancia in ubicadas[-5:]:
        print(f"{nombre} | {ciudad} | enlace {enlace} | {distancia:.1f} m")
    print("\nPendientes de ubicacion:")
    for nombre, ciudad, intercambios, enlace, distancia in pendientes:
        print(f"{nombre} | {ciudad}")


if __name__ == "__main__":
    cargar_referencias()
