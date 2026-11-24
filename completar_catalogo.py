"""Inventario ampliado de nodos para revisar la seleccion de infraestructura critica.

Ejecutar desde la raiz del proyecto: python completar_catalogo.py
Genera un catalogo, sus asociaciones al grafo y archivos de evidencia.
La vista nodos_clave_candidatos permite revisar el conjunto propuesto.
"""

from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
from io import StringIO
import json
from math import isfinite
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import psycopg
from psycopg.rows import dict_row


BASE = Path(__file__).resolve().parent
CONEXION = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:ruteo_local@127.0.0.1:5433/ruteo"
)

DDL = """
CREATE TABLE IF NOT EXISTS public.catalogo_nodos (
    clave TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    localidad TEXT NOT NULL,
    categorias TEXT[] NOT NULL,
    roles TEXT[] NOT NULL,
    fuentes TEXT[] NOT NULL,
    criterio TEXT NOT NULL,
    precision_ubicacion TEXT NOT NULL,
    origen TEXT NOT NULL,
    geom geometry(Point, 4326),
    vertice_id BIGINT,
    enlace_id BIGINT,
    distancia_al_trazado_m DOUBLE PRECISION,
    distancia_al_vertice_m DOUBLE PRECISION,
    geom_acceso geometry(Point, 4326),
    grado_vecinos INTEGER,
    intercambios TEXT[] NOT NULL DEFAULT '{}',
    CHECK (vertice_id IS NULL OR vertice_id > 0),
    CHECK (distancia_al_trazado_m IS NULL OR distancia_al_trazado_m >= 0),
    CHECK (distancia_al_vertice_m IS NULL OR distancia_al_vertice_m >= 0)
);
ALTER TABLE public.catalogo_nodos ADD COLUMN IF NOT EXISTS evaluar BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.catalogo_nodos ADD COLUMN IF NOT EXISTS motivo_evaluacion TEXT NOT NULL DEFAULT 'Pendiente de revision';
CREATE TEMPORARY TABLE nuevo_catalogo
    (LIKE public.catalogo_nodos INCLUDING DEFAULTS INCLUDING CONSTRAINTS)
    ON COMMIT DROP;
"""

SQL_INSTALACIONES = """
INSERT INTO nuevo_catalogo (
    clave, nombre, localidad, categorias, roles, fuentes, criterio,
    precision_ubicacion, origen, geom, intercambios
)
SELECT
    'pdb_fac:' || f.id, f.nombre, COALESCE(f.ciudad, 'Sin ciudad informada'),
    ARRAY['instalacion_alojamiento_interconexion']::TEXT[] ||
        CASE WHEN CARDINALITY(ix.nombres) > 0 THEN ARRAY['ixp_pit']::TEXT[]
             ELSE '{}'::TEXT[] END,
    ARRAY['genera', 'recibe']::TEXT[] ||
        CASE WHEN CARDINALITY(ix.nombres) > 0 THEN ARRAY['intercambia']::TEXT[]
             ELSE '{}'::TEXT[] END,
    ARRAY[f.fuente]::TEXT[] || ix.fuentes,
    'Instalacion de alojamiento o interconexion de PeeringDB. Generacion y recepcion '
    'son funciones modeladas. Intercambio se asigna solo si existe presencia de IX registrada.',
    CASE WHEN f.geom IS NULL THEN 'Sin coordenadas en la fuente'
         ELSE 'Coordenadas declaradas en PeeringDB; asociacion al catastro aproximada' END,
    'peeringdb', f.geom, ix.nombres
FROM public.referencias_instalaciones AS f
CROSS JOIN LATERAL (
    SELECT
        ARRAY(
            SELECT DISTINCT i.nombre
            FROM public.referencias_presencias AS p
            JOIN public.referencias_intercambios AS i ON i.id = p.intercambio_id
            WHERE p.instalacion_id = f.id ORDER BY i.nombre
        ) AS nombres,
        ARRAY(
            SELECT DISTINCT i.fuente
            FROM public.referencias_presencias AS p
            JOIN public.referencias_intercambios AS i ON i.id = p.intercambio_id
            WHERE p.instalacion_id = f.id ORDER BY i.fuente
        ) AS fuentes
) AS ix;

INSERT INTO nuevo_catalogo (
    clave, nombre, localidad, categorias, roles, fuentes, criterio,
    precision_ubicacion, origen
)
SELECT
    'pdb_ix_pendiente:' || i.id, i.nombre, COALESCE(i.ciudad, 'Sin ciudad informada'),
    ARRAY['ixp_pit']::TEXT[], ARRAY['intercambia']::TEXT[], ARRAY[i.fuente]::TEXT[],
    'IX registrado en Chile, sin presencia fisica georreferenciada en los archivos '
    'de este trabajo. Se conserva pendiente para verificar su ubicacion.',
    'Sin sede fisica georreferenciada en las fuentes conservadas', 'ix_sin_sede'
FROM public.referencias_intercambios AS i
WHERE i.pais = 'CL' AND NOT EXISTS (
    SELECT 1 FROM public.referencias_presencias AS p
    JOIN public.referencias_instalaciones AS f ON f.id = p.instalacion_id
    WHERE p.intercambio_id = i.id AND f.geom IS NOT NULL
);
"""

SQL_ATERRIZAJE = """
INSERT INTO nuevo_catalogo (
    clave, nombre, localidad, categorias, roles, fuentes, criterio,
    precision_ubicacion, origen, geom
)
VALUES (
    %s, %s, %s,
    ARRAY['aterrizaje_submarino']::TEXT[],
    ARRAY['recibe', 'intercambia']::TEXT[], ARRAY[%s]::TEXT[],
    'Localidad de aterrizaje del catalogo TeleGeography. Se modela la entrada, '
    'salida y transferencia de informacion entre sistemas submarinos y terrestres.',
    'Punto de referencia de localidad; no ubicacion verificada de una estacion fisica',
    'telegeography', ST_SetSRID(ST_MakePoint(%s, %s), 4326)
);
"""

SQL_TRONCALES = """
WITH vecinos AS (
    SELECT source AS vertice, target AS vecino FROM public.enlaces
    WHERE source IS NOT NULL AND target IS NOT NULL AND source <> target
    UNION
    SELECT target, source FROM public.enlaces
    WHERE source IS NOT NULL AND target IS NOT NULL AND source <> target
), grados AS (
    SELECT vertice, COUNT(*)::INTEGER AS grado FROM vecinos GROUP BY vertice
)
INSERT INTO nuevo_catalogo (
    clave, nombre, localidad, categorias, roles, fuentes, criterio,
    precision_ubicacion, origen, geom, vertice_id,
    distancia_al_trazado_m, distancia_al_vertice_m, geom_acceso, grado_vecinos
)
SELECT
    'troncal:' || v.id, 'Ramificacion del catastro ' || v.id,
    'Ubicacion definida por el vertice del catastro',
    ARRAY['ramificacion_troncal_modelada']::TEXT[], ARRAY['intercambia']::TEXT[],
    ARRAY[
        'datos/yafun/Respuesta_T5610/fibra_optica_detectada.shp',
        'https://yafun.nic.cl/mapas/recorrido-yafun/'
    ]::TEXT[],
    'Vertice con al menos tres vecinos distintos en el grafo de fibra. Se modela '
    'como ramificacion de transporte. El catastro no acredita un router o PoP del operador.',
    'Coordenadas del vertice de la topologia derivada del catastro',
    'topologia', v.the_geom, v.id, 0, 0, v.the_geom, g.grado
FROM grados AS g
JOIN public.enlaces_vertices_pgr AS v ON v.id = g.vertice
WHERE g.grado >= 3;
"""

SQL_ASOCIACIONES = """
WITH cercanos AS MATERIALIZED (
    SELECT n.clave, n.geom AS geom_referencia,
           e.id AS enlace_id, e.source, e.target,
           e.distancia_m AS distancia_al_trazado_m,
           ST_Distance(n.geom::geography, ST_StartPoint(e.geom)::geography)
             <= ST_Distance(n.geom::geography, ST_EndPoint(e.geom)::geography)
             AS usar_origen
    FROM nuevo_catalogo AS n
    CROSS JOIN LATERAL (
        SELECT e.id, e.source, e.target, e.geom,
               ST_Distance(n.geom::geography, e.geom::geography) AS distancia_m
        FROM public.enlaces AS e
        ORDER BY distancia_m, e.id LIMIT 1
    ) AS e
    WHERE n.geom IS NOT NULL AND n.origen <> 'topologia'
), extremos AS (
    SELECT clave, enlace_id, distancia_al_trazado_m,
           CASE WHEN usar_origen THEN source ELSE target END AS vertice_id
    FROM cercanos
)
UPDATE nuevo_catalogo AS n SET
    vertice_id = e.vertice_id, enlace_id = e.enlace_id,
    distancia_al_trazado_m = e.distancia_al_trazado_m,
    distancia_al_vertice_m = ST_Distance(n.geom::geography, v.the_geom::geography),
    geom_acceso = v.the_geom
FROM extremos AS e
JOIN public.enlaces_vertices_pgr AS v ON v.id = e.vertice_id
WHERE n.clave = e.clave;
"""

SQL_VISTA = """
CREATE OR REPLACE VIEW public.nodos_clave_candidatos AS
WITH grupos AS (
    SELECT vertice_id AS id,
           (ARRAY_AGG(geom_acceso ORDER BY clave))[1] AS geom,
           ARRAY_AGG(clave ORDER BY clave) AS claves_catalogo,
           ARRAY_AGG(nombre ORDER BY clave) AS nombres,
           COUNT(*) AS registros_asociados
    FROM public.catalogo_nodos WHERE evaluar AND vertice_id IS NOT NULL
    GROUP BY vertice_id
)
SELECT g.*,
    ARRAY(
        SELECT DISTINCT r.rol FROM public.catalogo_nodos AS n
        CROSS JOIN LATERAL UNNEST(n.roles) AS r(rol)
        WHERE n.evaluar AND n.vertice_id = g.id ORDER BY r.rol
    ) AS roles,
    ARRAY(
        SELECT DISTINCT c.categoria FROM public.catalogo_nodos AS n
        CROSS JOIN LATERAL UNNEST(n.categorias) AS c(categoria)
        WHERE n.evaluar AND n.vertice_id = g.id ORDER BY c.categoria
    ) AS categorias
FROM grupos AS g;

COMMENT ON TABLE public.catalogo_nodos IS
'Inventario de fuentes y roles modelados. Las asociaciones por proximidad no prueban una conexion fisica. Las ramificaciones son inferencias del grafo.';
COMMENT ON VIEW public.nodos_clave_candidatos IS
'Accesos candidatos para revisar el conjunto critico; conserva por separado todos los registros del inventario.';
"""

SQL_RESULTADOS = """
WITH componentes AS MATERIALIZED (
    SELECT node, component FROM pgr_connectedComponents(
        'SELECT id, source, target, cost, cost AS reverse_cost FROM public.enlaces'
    )
)
SELECT n.clave, n.nombre, n.localidad, n.categorias, n.roles, n.fuentes,
       n.criterio, n.precision_ubicacion, n.origen, n.vertice_id, n.enlace_id,
       n.distancia_al_trazado_m, n.distancia_al_vertice_m, n.grado_vecinos,
       n.evaluar, n.motivo_evaluacion,
       n.intercambios, ST_X(n.geom) AS longitud, ST_Y(n.geom) AS latitud,
       ST_AsGeoJSON(n.geom, 9, 0)::jsonb AS geom,
       CASE WHEN n.vertice_id IS NOT NULL
            THEN COALESCE(c.component, -n.vertice_id) END AS componente
FROM public.catalogo_nodos AS n
LEFT JOIN componentes AS c ON c.node = n.vertice_id
ORDER BY n.origen, n.clave;
"""


def escribir(ruta, contenido):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=ruta.parent, delete=False) as f:
            temporal = Path(f.name)
            f.write(contenido)
        temporal.replace(ruta)
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)


def leer_aterrizajes(directorio=BASE / "datos"):
    ruta = directorio / "aterrizajes_chile.geojson"
    metadatos = json.loads((directorio / "fuentes_catalogo.json").read_text(encoding="utf-8"))
    if hashlib.sha256(ruta.read_bytes()).hexdigest() != metadatos["sha256"]:
        raise ValueError("El GeoJSON de aterrizajes no coincide con la copia documentada.")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if datos.get("type") != "FeatureCollection":
        raise ValueError("Se esperaba un GeoJSON FeatureCollection.")
    filas, identificadores = [], set()
    for feature in datos["features"]:
        p, g = feature["properties"], feature["geometry"]
        if not p["name"].endswith(", Chile") or p.get("is_tbd") is not False:
            raise ValueError("El inventario contiene un punto fuera del criterio documentado.")
        if g["type"] != "Point" or len(g["coordinates"]) != 2:
            raise ValueError("Los aterrizajes deben tener geometria Point en WGS84.")
        lon, lat = map(float, g["coordinates"])
        if not (isfinite(lon) and isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Coordenadas invalidas en el inventario.")
        if p["id"] in identificadores:
            raise ValueError("Identificador de aterrizaje duplicado.")
        identificadores.add(p["id"])
        localidad = p["name"].removesuffix(", Chile")
        filas.append((
            "landing:" + p["id"], "Aterrizaje de referencia - " + localidad,
            localidad, p["fuente"], lon, lat,
        ))
    if len(filas) != metadatos["registros"] or not filas:
        raise ValueError("La cantidad de referencias no coincide con los metadatos.")
    return filas, metadatos


def aplicar_localizaciones(conexion, directorio_datos):
    datos = json.loads((directorio_datos / "localizaciones_verificadas.json").read_text(encoding="utf-8"))
    for fila in datos["verificados"]:
        lon, lat = float(fila["longitud"]), float(fila["latitud"])
        if not (isfinite(lon) and isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Coordenadas invalidas en una localizacion complementaria.")
        cursor = conexion.execute("""
            UPDATE nuevo_catalogo SET
                geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                fuentes = ARRAY_APPEND(fuentes, %s),
                precision_ubicacion = %s,
                criterio = criterio || ' Ubicacion complementada con el localizador del operador.'
            WHERE clave = %s AND nombre = %s
        """, (lon, lat, fila["fuente"], fila["precision"], fila["clave"], fila["nombre_esperado"]))
        if cursor.rowcount != 1:
            raise ValueError(f"La localizacion complementaria no coincide con el registro {fila['clave']}.")


SQL_SELECCION = """
UPDATE nuevo_catalogo SET
    evaluar = geom IS NOT NULL AND vertice_id IS NOT NULL,
    motivo_evaluacion = CASE
        WHEN geom IS NULL THEN 'Sin ubicacion fisica georreferenciada verificable en las fuentes'
        WHEN vertice_id IS NULL THEN 'Sin asociacion a la topologia disponible'
        ELSE 'Incluido: acceso del catalogo georreferenciado, con asociacion explicita al grafo'
    END;

UPDATE nuevo_catalogo SET evaluar = FALSE,
    motivo_evaluacion = 'Referencia externa de Tortel: la asociacion de aproximadamente 94 km carece de un tramo documentado en este catastro'
WHERE clave = 'landing:tortel-chile';
"""


def guardar_evidencia(filas, metadatos, salida=BASE / "resultados"):
    campos = [
        "clave", "nombre", "localidad", "categorias", "roles", "fuentes",
        "longitud", "latitud", "precision_ubicacion", "criterio", "origen",
        "vertice_id", "enlace_id", "componente", "distancia_al_trazado_m",
        "distancia_al_vertice_m", "grado_vecinos", "intercambios", "evaluar", "motivo_evaluacion",
    ]
    csv_texto = StringIO(newline="")
    writer = csv.DictWriter(csv_texto, fieldnames=campos)
    writer.writeheader()
    for fila in filas:
        writer.writerow({k: " | ".join(fila[k]) if isinstance(fila[k], list) else fila[k] for k in campos})
    escribir(salida / "catalogo_nodos.csv", csv_texto.getvalue())

    features = [{
        "type": "Feature", "id": fila["clave"], "geometry": fila["geom"],
        "properties": {k: v for k, v in fila.items() if k != "geom"},
    } for fila in filas if fila["geom"] is not None]
    escribir(salida / "catalogo_nodos.geojson", json.dumps({
        "type": "FeatureCollection", "features": features,
    }, ensure_ascii=False, indent=2) + "\n")

    pendientes = [fila for fila in filas if fila["vertice_id"] is None]
    resumen = {
        "generado_en_utc": datetime.now(timezone.utc).isoformat(),
        "registros_catalogo": len(filas),
        "por_origen": dict(sorted(Counter(f["origen"] for f in filas).items())),
        "por_categoria": dict(sorted(Counter(c for f in filas for c in f["categorias"]).items())),
        "coordenadas_disponibles": len(features),
        "pendientes_asociacion": len(pendientes),
        "registros_evaluados": sum(f["evaluar"] for f in filas),
        "accesos_distintos_candidatos": len({f["vertice_id"] for f in filas if f["evaluar"]}),
        "excluidos_georreferenciados": [
            {k: f[k] for k in ["clave", "nombre", "motivo_evaluacion"]}
            for f in filas if f["geom"] is not None and not f["evaluar"]
        ],
        "pendientes": [{k: f[k] for k in ["clave", "nombre", "localidad", "fuentes"]} for f in pendientes],
        "fuente_aterrizajes": metadatos,
        "alcance": "Inventario incorporado al conjunto final de accesos clave mediante sql/04_nodos_finales.sql. Se documentan asociaciones geograficas aproximadas y exclusiones justificadas.",
    }
    escribir(salida / "diagnostico_catalogo.json", json.dumps(resumen, ensure_ascii=False, indent=2) + "\n")
    return resumen


def completar_catalogo(conexion_bd=CONEXION, directorio_datos=BASE / "datos", salida=BASE / "resultados"):
    aterrizajes, metadatos = leer_aterrizajes(directorio_datos)
    with psycopg.connect(conexion_bd, row_factory=dict_row) as conexion:
        conexion.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        conexion.execute("SELECT pg_advisory_xact_lock(20100228)")
        for tabla in ["enlaces", "enlaces_vertices_pgr", "referencias_instalaciones", "referencias_intercambios", "referencias_presencias"]:
            existe = conexion.execute("SELECT to_regclass(%s) AS tabla", ("public." + tabla,)).fetchone()["tabla"]
            if existe is None:
                raise ValueError(f"Falta la tabla public.{tabla}. Completa la carga anterior.")
        control = conexion.execute("""
            SELECT COUNT(*) AS enlaces,
                   COUNT(*) FILTER (WHERE source IS NULL OR target IS NULL) AS sin_topologia
            FROM public.enlaces
        """).fetchone()
        if control["enlaces"] == 0 or control["sin_topologia"]:
            raise ValueError("Se requiere una red no vacia con source y target completos.")
        conexion.execute(DDL, prepare=False)
        conexion.execute(SQL_INSTALACIONES, prepare=False)
        with conexion.cursor() as cursor:
            cursor.executemany(SQL_ATERRIZAJE, aterrizajes)
        conexion.execute(SQL_TRONCALES)
        aplicar_localizaciones(conexion, directorio_datos)
        conexion.execute(SQL_ASOCIACIONES)
        sin_acceso = conexion.execute("""
            SELECT COUNT(*) AS cantidad FROM nuevo_catalogo
            WHERE geom IS NOT NULL AND vertice_id IS NULL
        """).fetchone()["cantidad"]
        if sin_acceso:
            raise RuntimeError("Hay coordenadas que no pudieron asociarse a vertices de la topologia.")
        conexion.execute(SQL_SELECCION, prepare=False)
        # Esta tabla es un producto regenerable de las fuentes conservadas.
        conexion.execute("DELETE FROM public.catalogo_nodos")
        conexion.execute("INSERT INTO public.catalogo_nodos SELECT * FROM nuevo_catalogo")
        conexion.execute(SQL_VISTA, prepare=False)
        filas = conexion.execute(SQL_RESULTADOS).fetchall()

    resumen = guardar_evidencia(filas, metadatos, salida)
    print("Catalogo ampliado guardado.")
    print(f"Instalaciones de PeeringDB: {resumen['por_origen'].get('peeringdb', 0)}")
    print(f"Referencias de aterrizaje: {resumen['por_origen'].get('telegeography', 0)}")
    print(f"Instalaciones con presencia de IX: {sum(f['origen']=='peeringdb' and 'ixp_pit' in f['categorias'] for f in filas)}")
    print(f"Ramificaciones con tres o mas vecinos: {resumen['por_origen'].get('topologia', 0)}")
    print(f"Registros del catalogo: {len(filas)}")
    print(f"Registros con coordenadas: {resumen['coordenadas_disponibles']}")
    print(f"Accesos distintos candidatos: {resumen['accesos_distintos_candidatos']}")
    print(f"Registros incluidos en esos accesos: {resumen['registros_evaluados']}")
    print(f"Pendientes de ubicacion o asociacion: {len(resumen['pendientes'])}")
    for p in resumen["pendientes"]:
        print(f"  {p['nombre']} | {p['localidad']}")
    for p in resumen["excluidos_georreferenciados"]:
        print(f"  Fuera de alcance: {p['nombre']} | {p['motivo_evaluacion']}")
    print("\nCinco asociaciones mas lejanas al vertice del grafo:")
    ubicadas = sorted((f for f in filas if f['distancia_al_vertice_m'] is not None),
                      key=lambda f: (-f['distancia_al_vertice_m'], f['clave']))
    for f in ubicadas[:5]:
        print(f"  {f['nombre']} | acceso {f['vertice_id']} | {f['distancia_al_vertice_m']/1000:.3f} km")
    print(f"\nInventario: {salida / 'catalogo_nodos.csv'}")
    print(f"Resumen: {salida / 'diagnostico_catalogo.json'}")
    return resumen


if __name__ == "__main__":
    try:
        completar_catalogo()
    except (OSError, ValueError, RuntimeError, psycopg.Error) as error:
        raise SystemExit(f"Error: {error}")
