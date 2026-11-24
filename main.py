"""Reconstruye la infraestructura, calcula ATTR y genera un mapa Leaflet.

Ejecucion reproducible desde un clon limpio:
    python3 main.py --abrir

El programa prepara PostgreSQL mediante Docker, restaura main.sql
si faltan las fuentes y ejecuta el flujo completo.

Sin --abrir genera los resultados y termina.
La opcion --cargar se mantiene por compatibilidad y no es obligatoria.

Los parametros del modelo son supuestos del escenario academico.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from functools import partial
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from itertools import combinations
import json
from math import isfinite
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import NamedTemporaryFile
from threading import Thread
from urllib.parse import quote
from urllib.request import Request, urlopen
import webbrowser
from zipfile import ZipFile

if __name__ == "__main__":
    from cierre_ruteo.entorno_ruteo import preparar_entorno
    preparar_entorno(__file__)

import folium
from branca.element import Element
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from cargar_referencias import cargar_referencias
from completar_catalogo import completar_catalogo
from infra_bd import BaseDocker
from modelo_sismo import CONEXION_BD, SISMO_ID, RADIO_MAXIMO_PREDETERMINADO_KM, ejecutar_modelo


BASE = Path(__file__).resolve().parent
DATOS = BASE / "datos"
URLS_DATOS = {
    "maule_2010.geojson": (
        "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&eventid="
        + SISMO_ID
    ),
    "peeringdb_instalaciones.json": (
        "https://www.peeringdb.com/api/fac?country=CL&depth=0&fields="
        "id,name,city,country,latitude,longitude,status,updated"
    ),
    "peeringdb_intercambios.json": (
        "https://www.peeringdb.com/api/ix?country=CL&depth=0&fields="
        "id,name,city,country,status,updated"
    ),
    "peeringdb_relaciones.json": (
        "https://www.peeringdb.com/api/ixfac?country=CL&depth=0&fields="
        "id,ix_id,fac_id,name,city,country"
    ),
}


def escribir_atomico(ruta, contenido):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=ruta.parent, delete=False
        ) as archivo:
            temporal = Path(archivo.name)
            archivo.write(contenido)
        temporal.replace(ruta)
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)


def descargar_faltantes():
    """Conserva las copias existentes para mantener la reproducibilidad."""
    DATOS.mkdir(parents=True, exist_ok=True)
    for nombre, url in URLS_DATOS.items():
        ruta = DATOS / nombre
        if ruta.is_file():
            print(f"Se conserva el archivo existente: datos/{nombre}")
            continue
        solicitud = Request(url, headers={"User-Agent": "Yafun-academic-analysis/1.0"})
        with urlopen(solicitud, timeout=60) as respuesta:
            contenido = respuesta.read().decode("utf-8")
        json.loads(contenido)
        escribir_atomico(ruta, contenido)
        print(f"Descargado: datos/{nombre}")


def buscar_fibra():
    candidatos = sorted(DATOS.rglob("fibra_optica_detectada.shp"))
    if not candidatos:
        comprimido = DATOS / "PortalTransparencia_Yafun.zip"
        if not comprimido.is_file():
            raise FileNotFoundError(
                "Falta datos/PortalTransparencia_Yafun.zip o el shapefile de fibra extraido."
            )
        destino = (DATOS / "yafun").resolve()
        destino.mkdir(parents=True, exist_ok=True)
        with ZipFile(comprimido) as archivo:
            for entrada in archivo.infolist():
                objetivo = (destino / entrada.filename).resolve()
                if not objetivo.is_relative_to(destino):
                    raise ValueError("El ZIP contiene una ruta fuera de su carpeta de destino.")
            archivo.extractall(destino)
        candidatos = sorted(DATOS.rglob("fibra_optica_detectada.shp"))
    if len(candidatos) != 1:
        raise ValueError(f"Se esperaba un shapefile de fibra, se encontraron {len(candidatos)}.")
    ruta = candidatos[0]
    for extension in [".shp", ".shx", ".dbf", ".prj"]:
        if not ruta.with_suffix(extension).is_file():
            raise FileNotFoundError(f"Falta un archivo del shapefile: {ruta.with_suffix(extension)}")
    return ruta


def leer_sismo(ruta):
    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    if contenido.get("type") == "FeatureCollection":
        candidatos = [f for f in contenido["features"] if f.get("id") == SISMO_ID]
        if len(candidatos) != 1:
            raise ValueError("El GeoJSON debe contener el evento del Maule seleccionado.")
        contenido = candidatos[0]
    if contenido.get("type") != "Feature" or contenido.get("id") != SISMO_ID:
        raise ValueError("El GeoJSON no corresponde al evento del Maule esperado.")
    geometria = contenido["geometry"]
    if geometria.get("type") != "Point" or len(geometria["coordinates"]) < 3:
        raise ValueError("Se requiere un epicentro Point con longitud, latitud y profundidad.")
    longitud, latitud, profundidad = map(float, geometria["coordinates"][:3])
    magnitud = float(contenido["properties"]["mag"])
    if not all(map(isfinite, [longitud, latitud, profundidad, magnitud])):
        raise ValueError("El evento contiene valores no finitos.")
    if not (-180 <= longitud <= 180 and -90 <= latitud <= 90 and profundidad >= 0):
        raise ValueError("Las coordenadas o la profundidad del sismo son invalidas.")
    if abs(magnitud - 8.8) > 1e-6:
        raise ValueError("El evento seleccionado debe tener magnitud 8.8.")
    return magnitud, profundidad, longitud, latitud


def cargar_bd(conexion_bd):
    """Carga fuentes conservadas, regenera topologia y actualiza los accesos."""
    for nombre in URLS_DATOS:
        if not (DATOS / nombre).is_file():
            raise FileNotFoundError(
                f"Falta datos/{nombre}. Copia tu archivo o usa --descargar para obtenerlo."
            )
    scripts = [BASE / "sql/01_topologia.sql", BASE / "sql/02_nodos_attr.sql"]
    for ruta in scripts:
        if not ruta.is_file():
            raise FileNotFoundError(f"Falta {ruta}.")
    sismo = leer_sismo(DATOS / "maule_2010.geojson")

    with psycopg.connect(conexion_bd, autocommit=True) as conexion:
        conexion.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conexion.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
        topologia_disponible = conexion.execute("""
            SELECT EXISTS (
                SELECT 1 FROM pg_proc WHERE proname = 'pgr_createtopology'
            )
        """).fetchone()[0]
        if not topologia_disponible:
            raise RuntimeError("Esta entrega requiere pgRouting 3.7 y pgr_createTopology.")
        existe = conexion.execute("SELECT to_regclass('public.fibra_original')").fetchone()[0]
        filas = conexion.execute("SELECT COUNT(*) FROM public.fibra_original").fetchone()[0] if existe else 0

    if filas == 0:
        ejecutable = shutil.which("ogr2ogr")
        if ejecutable is None:
            raise RuntimeError("Falta ogr2ogr. Instala gdal-bin antes de cargar la fibra.")
        fibra = buscar_fibra()
        parametros = conninfo_to_dict(conexion_bd)
        entorno = os.environ.copy()
        if "password" in parametros:
            entorno["PGPASSWORD"] = parametros.pop("password")
        orden = [ejecutable, "-f", "PostgreSQL", "PG:" + make_conninfo(**parametros), str(fibra)]
        orden += ["-nln", "public.fibra_original"]
        if existe:
            orden += ["-append"]
        else:
            orden += ["-lco", "GEOMETRY_NAME=geom", "-lco", "FID=gid"]
        orden += ["-nlt", "LINESTRING", "-t_srs", "EPSG:4326"]
        print("Importando la fibra con ogr2ogr...")
        proceso = subprocess.run(orden, env=entorno, capture_output=True, text=True, check=False)
        if proceso.returncode:
            raise RuntimeError("Fallo la importacion de fibra: " + proceso.stderr.strip())
    else:
        print(f"Se conserva fibra_original: {filas} trazados.")

    with psycopg.connect(conexion_bd, autocommit=True) as conexion:
        with conexion.transaction():
            conexion.execute("""
                CREATE TABLE IF NOT EXISTS public.sismos (
                    id TEXT PRIMARY KEY,
                    magnitud DOUBLE PRECISION NOT NULL,
                    profundidad_km DOUBLE PRECISION NOT NULL,
                    geom geometry(Point, 4326) NOT NULL
                )
            """)
            conexion.execute("""
                INSERT INTO public.sismos (id, magnitud, profundidad_km, geom)
                VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                ON CONFLICT (id) DO UPDATE SET
                    magnitud = EXCLUDED.magnitud,
                    profundidad_km = EXCLUDED.profundidad_km,
                    geom = EXCLUDED.geom
            """, (SISMO_ID, *sismo))
        print("Creando la topologia...")
        conexion.execute(scripts[0].read_text(encoding="utf-8"), prepare=False)

    cargar_referencias(directorio_datos=DATOS, conexion_bd=conexion_bd)
    with psycopg.connect(conexion_bd, autocommit=True) as conexion:
        conexion.execute(scripts[1].read_text(encoding="utf-8"), prepare=False)
    print("Fuentes, topologia, nodos clave y funcion ATTR cargados.")


def leer_infraestructura(conexion_bd, resultado):
    radio_m = resultado["radio_escenario_km"] * 1000
    with psycopg.connect(conexion_bd, row_factory=dict_row) as conexion:
        conexion.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        enlaces = conexion.execute("""
            SELECT e.id, e.source, e.target, e.cost AS longitud_m, e.es_sintetico,
                   ST_AsGeoJSON(e.geom, 9, 0)::jsonb AS geom,
                   p.distancia_m, p.prob_falla
            FROM public.enlaces AS e
            JOIN public.probabilidades_falla AS p ON p.enlace_id = e.id
            ORDER BY e.id
        """).fetchall()
        fallados = [e["id"] for e in enlaces if e["distancia_m"] <= radio_m]
        nodos = conexion.execute("""
            WITH antes AS MATERIALIZED (
                SELECT node, component FROM pgr_connectedComponents(
                    'SELECT id, source, target, cost, cost AS reverse_cost FROM public.enlaces'
                )
            ), despues AS MATERIALIZED (
                SELECT node, component FROM pgr_connectedComponents(FORMAT(
                    'SELECT id, source, target, cost, cost AS reverse_cost FROM public.enlaces
                     WHERE NOT (id = ANY(%%L::BIGINT[]))', %s::BIGINT[]
                ))
            )
            SELECT n.id, n.roles, n.instalaciones_ids, n.instalaciones, n.ciudades,
                   n.categorias, n.fuentes, n.claves_catalogo,
                   ST_AsGeoJSON(n.geom, 9, 0)::jsonb AS geom,
                   COALESCE(a.component, -n.id) AS componente_antes,
                   COALESCE(d.component, -n.id) AS componente_despues,
                   (SELECT COUNT(DISTINCT component) FROM antes) AS componentes_iniciales
            FROM public.nodos_clave AS n
            LEFT JOIN antes AS a ON a.node = n.id
            LEFT JOIN despues AS d ON d.node = n.id
            ORDER BY n.id
        """, (fallados,)).fetchall()
        instalaciones = conexion.execute("""
            SELECT clave AS id, nombre, localidad AS ciudad, fuentes[1] AS fuente,
                   ST_AsGeoJSON(geom, 9, 0)::jsonb AS geom,
                   CASE WHEN evaluar THEN vertice_id END AS vertice_id,
                   distancia_al_trazado_m, intercambios,
                   ST_AsGeoJSON(geom_acceso, 9, 0)::jsonb AS geom_acceso,
                   origen, categorias, evaluar, motivo_evaluacion, precision_ubicacion
            FROM public.catalogo_nodos ORDER BY clave
        """).fetchall()
    if len(enlaces) != resultado["enlaces_total"] or len(nodos) != resultado["nodos_clave"]:
        raise RuntimeError("La red cambio durante el calculo. Vuelve a ejecutar main.py.")
    return enlaces, nodos, instalaciones


def listar_pares(nodos):
    return [
        {
            "nodo_a": a["id"], "nodo_b": b["id"],
            "conectados_antes": a["componente_antes"] == b["componente_antes"],
            "conectados_despues": a["componente_despues"] == b["componente_despues"],
        }
        for a, b in combinations(nodos, 2)
    ]


def exportar_resultados(directorio, resultado, enlaces, nodos, instalaciones):
    pares = listar_pares(nodos)
    if sum(p["conectados_antes"] for p in pares) != resultado["pares_conectados_antes"]:
        raise RuntimeError("El detalle de pares iniciales no coincide con el ATTR.")
    if sum(p["conectados_despues"] for p in pares) != resultado["pares_conectados_escenario"]:
        raise RuntimeError("El detalle de pares posteriores no coincide con el ATTR.")
    radio_m = resultado["radio_escenario_km"] * 1000
    for nombre, campos, filas in [
        (
            "enlaces_probabilidades.csv",
            ["enlace_id", "source", "target", "longitud_m", "distancia_epicentro_m", "prob_falla", "fallado_escenario", "es_sintetico"],
            ({
                "enlace_id": e["id"], "source": e["source"], "target": e["target"],
                "longitud_m": e["longitud_m"], "distancia_epicentro_m": e["distancia_m"],
                "prob_falla": e["prob_falla"], "fallado_escenario": int(e["distancia_m"] <= radio_m),
                "es_sintetico": int(e.get("es_sintetico", False)),
            } for e in enlaces),
        ),
        (
            "pares_nodos.csv",
            ["nodo_a", "nodo_b", "conectados_antes", "conectados_despues"],
            ({k: int(v) if isinstance(v, bool) else v for k, v in p.items()} for p in pares),
        ),
    ]:
        texto = StringIO(newline="")
        escritor = csv.DictWriter(texto, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(filas)
        escribir_atomico(directorio / nombre, texto.getvalue())
    resumen = {
        **resultado,
        "generado_en_utc": datetime.now(timezone.utc).isoformat(),
        "componentes_iniciales": nodos[0]["componentes_iniciales"],
        "instalaciones_total": sum(i.get("origen", "peeringdb") == "peeringdb" for i in instalaciones),
        "instalaciones_representadas": sum(i.get("origen", "peeringdb") == "peeringdb" and i["vertice_id"] is not None for i in instalaciones),
        "instalaciones_sin_coordenadas": sum(i.get("origen", "peeringdb") == "peeringdb" and i["geom"] is None for i in instalaciones),
        "registros_catalogo": len(instalaciones),
        "registros_evaluados": sum(i.get("evaluar", i["vertice_id"] is not None) for i in instalaciones),
        "referencias_sin_coordenadas": sum(i["geom"] is None for i in instalaciones),
        "referencias_fuera_alcance": [
            {"id": i["id"], "nombre": i["nombre"], "motivo": i["motivo_evaluacion"]}
            for i in instalaciones if not i.get("evaluar", True)
        ],
        "modelo": "Disco regional con R uniforme entre 0 y R_max; epicentro fijo; fallas correlacionadas",
        "fuente_modelo": "https://doi.org/10.1109/JSAC.2021.3064652",
        "alcance": "Conectividad entre accesos aproximados del catastro; danos simulados.",
    }
    escribir_atomico(directorio / "resumen.json", json.dumps(resumen, ensure_ascii=False, indent=2) + "\n")
    texto_nodos = StringIO(newline="")
    escritor = csv.DictWriter(texto_nodos, fieldnames=[
        "acceso", "categorias", "roles", "nombres_asociados", "fuentes", "longitud", "latitud",
        "componente_antes", "componente_despues",
    ])
    escritor.writeheader()
    for n in nodos:
        lon, lat = n["geom"]["coordinates"][:2]
        escritor.writerow({
            "acceso": n["id"], "categorias": " | ".join(n.get("categorias", [])),
            "roles": " | ".join(n["roles"]), "nombres_asociados": " | ".join(n["instalaciones"]),
            "fuentes": " | ".join(n.get("fuentes", [])), "longitud": lon, "latitud": lat,
            "componente_antes": n["componente_antes"], "componente_despues": n["componente_despues"],
        })
    escribir_atomico(directorio / "nodos_clave.csv", texto_nodos.getvalue())
    generar_analisis(directorio, resumen, enlaces, nodos)
    return resumen


def generar_analisis(directorio, resumen, enlaces, nodos):
    base, post = resumen["attr_antes"], resumen["attr_despues_esperado"]
    variacion = (post / base - 1) * 100 if base else None
    filas = []
    for n in nodos:
        antes = sum(o["id"] != n["id"] and o["componente_antes"] == n["componente_antes"] for o in nodos)
        despues = sum(o["id"] != n["id"] and o["componente_despues"] == n["componente_despues"] for o in nodos)
        filas.append({"acceso": n["id"], "otros_accesos_antes": antes, "otros_accesos_despues": despues,
                      "perdida": antes - despues, "nombres": " | ".join(n["instalaciones"])})
    texto = StringIO(newline="")
    escritor = csv.DictWriter(texto, fieldnames=["acceso", "otros_accesos_antes", "otros_accesos_despues", "perdida", "nombres"])
    escritor.writeheader()
    escritor.writerows(sorted(filas, key=lambda f: (-f["perdida"], f["acceso"])))
    escribir_atomico(directorio / "afectacion_nodos.csv", texto.getvalue())
    mas = sorted(enlaces, key=lambda e: (-e["prob_falla"], e["distancia_m"], e["id"]))[:5]
    menos = sorted(enlaces, key=lambda e: (e["prob_falla"], -e["distancia_m"], e["id"]))[:5]
    texto = StringIO(newline="")
    escritor = csv.DictWriter(texto, fieldnames=["grupo", "enlace", "prob_falla", "distancia_km"])
    escritor.writeheader()
    for grupo, elementos in [("mas_afectados", mas), ("menos_afectados", menos)]:
        escritor.writerows({"grupo": grupo, "enlace": e["id"], "prob_falla": e["prob_falla"], "distancia_km": e["distancia_m"] / 1000} for e in elementos)
    escribir_atomico(directorio / "extremos_probabilidad.csv", texto.getvalue())
    porcentual = f"{variacion:.4f} %" if variacion is not None else "No definida: ATTR inicial cero"
    analisis = f"""# Resultados de la evaluacion

| Medida | Valor |
| --- | ---: |
| Accesos clave distintos | {resumen['nodos_clave']} |
| Pares posibles, fijos durante la comparacion | {resumen['pares_posibles']} |
| Pares conectados antes | {resumen['pares_conectados_antes']} |
| Pares conectados en el escenario mostrado | {resumen['pares_conectados_escenario']} |
| ATTR inicial | {base:.9f} |
| ATTR posterior esperado | {post:.9f} |
| Diferencia esperada, despues menos antes | {post-base:.9f} |
| Diferencia en puntos porcentuales | {(post-base)*100:.6f} |
| Variacion porcentual respecto al inicial | {porcentual} |
| Radio maximo del modelo, km | {resumen['radio_maximo_km']:g} |
| Radio del escenario mostrado, km | {resumen['radio_escenario_km']:g} |
| Enlaces fallados en el escenario | {resumen['enlaces_fallados_escenario']} |

El ATTR posterior esperado se integra desde las probabilidades de todos los
enlaces en la BD bajo el modelo de radio comun. El valor del escenario
mostrado es una realizacion y se conserva por separado en resumen.json.
Los accesos sin ruta a otro acceso del conjunto se identifican en
afectacion_nodos.csv por otros_accesos_antes = 0. Esto no demuestra que
sean vertices aislados del grafo completo.

Los enlaces mas y menos afectados estan en extremos_probabilidad.csv.
Los cambios de conectividad de cada par estan en pares_nodos.csv.
Las identidades, roles y fuentes se conservan en nodos_clave.csv y
catalogo_nodos.csv. Las asociaciones geograficas no acreditan enlaces
locales reales. El alcance excluye las referencias sin georreferencia
fisica y la asociacion de Tortel de aproximadamente 94 km sin tramo observado.

## Contraste con el 27F

El reporte oficial de SUBTEL del 3 de marzo de 2010 informo, durante la
recuperacion posterior al terremoto, operatividad de redes fijas de 75 % en
Maule y 45 % en Biobio. Se trata de porcentajes de servicio reportados en
esa fecha, con un universo y una metrica distintos del ATTR de este grafo.
Fuente: https://www.subtel.gob.cl/reporte-telecomunicaciones-en-regiones-afectadas-emitido-por-el-ministerio-del-interior/

El informe Yafun, seccion 2.2, describe una desconexion amplia de Internet
tras el 27F y problemas de interconexion y respaldos en Santiago. El modelo
de esta actividad considera fallas regionales de enlaces, accesos locales
supuestos operativos y una infraestructura parcial de otra fecha. No
incluye agotamiento de energia, congestion, fallas de equipos ni tsunami.
Las diferencias deben interpretarse a la luz de ese alcance y del conjunto
de accesos seleccionado.
"""
    escribir_atomico(directorio / "analisis_resultados.md", analisis)


def color_probabilidad(probabilidad):
    if probabilidad <= 0:
        return "#94a3b8"
    if probabilidad <= 0.25:
        return "#fde047"
    if probabilidad <= 0.5:
        return "#f59e0b"
    if probabilidad <= 0.75:
        return "#c2410c"
    return "#7c2d12"


def ubicacion(geometria):
    longitud, latitud = geometria["coordinates"][:2]
    return [latitud, longitud]


def agregar_panel(mapa, resumen):
    def porcentaje(valor):
        return f"{valor:.2%}".replace(".", ",")

    def entero(valor):
        return f"{valor:,}".replace(",", ".")

    estilos = """
    <style>
      #resumen-ruteo {position:fixed;top:16px;left:16px;width:310px;
        max-height:calc(100vh - 32px);overflow:auto;z-index:1000;padding:22px;
        background:#102235;color:#edf4fa;border-radius:14px;box-shadow:0 8px 28px #0004;
        font:14px/1.5 system-ui,sans-serif;box-sizing:border-box;}
      #resumen-ruteo h1 {font-size:21px;line-height:1.2;margin:8px 0 12px;color:white;}
      #resumen-ruteo h2 {font-size:13px;margin:17px 0 7px;color:#b9cfdf;}
      #resumen-ruteo .etiqueta {font-size:10px;letter-spacing:2px;color:#80d4c4;}
      #resumen-ruteo .metrica {display:flex;justify-content:space-between;gap:8px;
        border-bottom:1px solid #ffffff20;padding:8px 0;align-items:center;}
      #resumen-ruteo .metrica strong {font-size:24px;color:#80d4c4;white-space:nowrap;}
      #resumen-ruteo .pequeno {font-size:12px;color:#c2d2df;}
      #resumen-ruteo a {color:#91dccc;text-decoration:underline;}
      #resumen-ruteo .muestra {display:inline-block;width:16px;height:4px;
        vertical-align:middle;margin-right:7px;}
      #resumen-ruteo summary {cursor:pointer;color:#80d4c4;}
      @media (max-width:700px) {
        #resumen-ruteo {top:auto;bottom:12px;left:12px;width:calc(100% - 24px);
          max-height:34vh;padding:14px;}
        #resumen-ruteo h1 {font-size:18px;}
      }
    </style>
    """
    texto = f"""
    <aside id="resumen-ruteo" aria-label="Resultados del modelo">
      <div class="etiqueta">YAFÜN / MAULE 2010</div>
      <h1>Conectividad ante<br>un sismo de magnitud 8,8</h1>
      <p class="pequeno">Escenario académico sobre el catastro de fibra observado.</p>
      <div class="metrica"><span>ATTR inicial</span><strong>{porcentaje(resumen['attr_antes'])}</strong></div>
      <div class="metrica"><span>ATTR con radio de<br>{resumen['radio_escenario_km']:g} km</span>
        <strong>{porcentaje(resumen['attr_escenario'])}</strong></div>
      <div class="metrica"><span>ATTR posterior<br>esperado</span>
        <strong>{porcentaje(resumen['attr_despues_esperado'])}</strong></div>
      <p class="pequeno">Pares conectados: {resumen['pares_conectados_antes']} antes y
        {resumen['pares_conectados_escenario']} después, de {resumen['pares_posibles']} posibles.</p>
      <h2>Infraestructura evaluada</h2>
      <div>{entero(resumen['enlaces_total'])} enlaces · {resumen['nodos_clave']} accesos clave</div>
      <div>{entero(resumen['enlaces_fallados_escenario'])} enlaces fallados en el escenario</div>
      <div class="pequeno">{resumen['registros_evaluados']} registros del catálogo representados ·
        {resumen['referencias_sin_coordenadas']} sin coordenadas ·
        {resumen['componentes_iniciales']} componentes iniciales</div>
      <h2>Probabilidad de falla por enlace</h2>
      <div class="pequeno"><span class="muestra" style="background:#94a3b8"></span>0</div>
      <div class="pequeno"><span class="muestra" style="background:#fde047"></span>Mayor que 0 y hasta 0,25</div>
      <div class="pequeno"><span class="muestra" style="background:#f59e0b"></span>Mayor que 0,25 y hasta 0,50</div>
      <div class="pequeno"><span class="muestra" style="background:#c2410c"></span>Mayor que 0,50 y hasta 0,75</div>
      <div class="pequeno"><span class="muestra" style="background:#7c2d12"></span>Mayor que 0,75</div>
      <div class="pequeno"><span class="muestra" style="background:#e11d48"></span>Falla en el escenario mostrado</div>
      <p class="pequeno">Activa las capas en la esquina superior derecha.
        Para ver la infraestructura inicial, desactiva probabilidades y fallas.</p>
      <details><summary>Cómo interpretar el resultado</summary>
        <p class="pequeno">Los nodos clave son accesos distintos de instalaciones de PeeringDB,
        referencias de aterrizaje y ramificaciones modeladas de la fibra.
        Los registros que comparten acceso cuentan una vez.
        El indicador mide sus pares con ruta y conserva el mismo denominador después del sismo.</p>
        <p class="pequeno">Modelo: R uniforme entre 0 y {resumen['radio_maximo_km']:g} km.
        Un enlace falla si su distancia mínima al epicentro es menor o igual que R.
        El radio común correlaciona las fallas. El ATTR esperado integra todos los radios.</p>
        <p class="pequeno">Los radios son supuestos. Se modela daño a enlaces y se suponen
        operativos los accesos locales. El evento de 2010 se aplica al catastro de Yafün y
        referencias actuales, por lo que el mapa representa daño simulado.</p>
        <a href="https://doi.org/10.1109/JSAC.2021.3064652" target="_blank" rel="noopener">Fuente del modelo regional</a>
      </details>
      <h2>Archivos del cálculo</h2>
      <a href="resumen.json" download>Resumen JSON</a> ·
      <a href="enlaces_probabilidades.csv" download>Enlaces CSV</a> ·
      <a href="pares_nodos.csv" download>Pares CSV</a>
    </aside>
    """
    mapa.get_root().header.add_child(Element(estilos))
    mapa.get_root().html.add_child(Element(texto))


def generar_mapa(directorio, resumen, enlaces, nodos, instalaciones):
    epicentro = [resumen["sismo"]["latitud"], resumen["sismo"]["longitud"]]
    radio_m = resumen["radio_escenario_km"] * 1000
    mapa = folium.Map(location=epicentro, zoom_start=6, tiles="OpenStreetMap",
                      prefer_canvas=True, control_scale=True)
    mapa.get_root().header.add_child(Element("<title>Yafün | Conectividad y sismo</title>"))
    mapa.get_root().header.add_child(Element(
        '<meta name="referrer" content="strict-origin-when-cross-origin">'
    ))

    # Una geometria colectiva reduce el costo de la capa de referencia.
    fibra = {"type": "MultiLineString", "coordinates": [e["geom"]["coordinates"] for e in enlaces]}
    folium.GeoJson(fibra, name="Infraestructura inicial", show=True,
                   style_function=lambda _: {"color": "#54718a", "weight": 1.7, "opacity": 0.65},
                   interactive=False).add_to(mapa)

    elementos = []
    for e in enlaces:
        elementos.append({
            "type": "Feature", "id": e["id"], "geometry": e["geom"],
            "properties": {
                "enlace": e["id"], "origen": e["source"], "destino": e["target"],
                "distancia_km": round(e["distancia_m"] / 1000, 3),
                "prob_falla": round(e["prob_falla"], 6),
                "estado_escenario": "Fallado" if e["distancia_m"] <= radio_m else "Operativo",
                "sintetico": "Si" if e.get("es_sintetico", False) else "No",
                "color": color_probabilidad(e["prob_falla"]),
            },
        })
    coleccion = {"type": "FeatureCollection", "features": elementos}
    capa_prob = folium.GeoJson(
        coleccion, name="Probabilidades de falla", show=True,
        style_function=lambda f: {"color": f["properties"]["color"], "weight": 2.6, "opacity": 0.9,
                                  "dashArray": "6,4" if f["properties"]["sintetico"] == "Si" else None},
        highlight_function=lambda _: {"weight": 6, "opacity": 1},
        tooltip=folium.GeoJsonTooltip(
            fields=["enlace", "distancia_km", "prob_falla", "estado_escenario", "sintetico"],
            aliases=["Enlace", "Distancia al epicentro (km)", "Probabilidad de falla", "Escenario", "Sintetico"],
        ),
        popup=folium.GeoJsonPopup(
            fields=["enlace", "origen", "destino", "distancia_km", "prob_falla", "estado_escenario"],
            aliases=["Enlace", "Nodo de origen", "Nodo de destino", "Distancia (km)", "Probabilidad", "Escenario"],
        ),
    ).add_to(mapa)
    fallados = [f for f, e in zip(elementos, enlaces) if e["distancia_m"] <= radio_m]
    if fallados:
        folium.GeoJson(
            {"type": "FeatureCollection", "features": fallados},
            name=f"Fallas simuladas ({resumen['radio_escenario_km']:g} km)", show=True,
            style_function=lambda _: {"color": "#e11d48", "weight": 3.8, "opacity": 0.95},
            tooltip=folium.GeoJsonTooltip(fields=["enlace", "distancia_km"], aliases=["Enlace fallado", "Distancia (km)"]),
        ).add_to(mapa)

    capa_nodos = folium.FeatureGroup(name="Nodos clave: puntos de acceso", show=True).add_to(mapa)
    por_id = {n["id"]: n for n in nodos}
    for n in nodos:
        pares_antes = sum(o["id"] != n["id"] and o["componente_antes"] == n["componente_antes"] for o in nodos)
        pares_despues = sum(o["id"] != n["id"] and o["componente_despues"] == n["componente_despues"] for o in nodos)
        color = "#e11d48" if pares_despues < pares_antes else "#047857" if pares_despues else "#475569"
        nombres = "<br>".join(escape(nombre) for nombre in n["instalaciones"])
        roles = ", ".join(escape(rol) for rol in n["roles"])
        categorias = ", ".join(escape(c) for c in n.get("categorias", []))
        popup = (f"<b>Acceso {n['id']}</b><br>Categorías: {categorias}<br>Roles modelados: {roles}<br>"
                 f"Con ruta a otros accesos: {pares_antes} antes / {pares_despues} después"
                 f"<hr>{nombres}")
        folium.CircleMarker(ubicacion(n["geom"]), radius=6, color="white", weight=1.5,
                            fill=True, fill_color=color, fill_opacity=1,
        tooltip=f"Acceso {n['id']} · {len(n['instalaciones'])} registros",
                            popup=folium.Popup(popup, max_width=360)).add_to(capa_nodos)

    capa_inst = folium.FeatureGroup(name="Ubicaciones originales del catálogo", show=False).add_to(mapa)
    capa_accesos = folium.FeatureGroup(name="Asociaciones geográficas aproximadas", show=False).add_to(mapa)
    for instalacion in instalaciones:
        if instalacion["geom"] is None:
            continue
        texto = f"<b>{escape(instalacion['nombre'])}</b><br>{escape(instalacion['ciudad'] or '')}"
        texto += "<br>" + escape(instalacion.get("precision_ubicacion", "Referencia geográfica"))
        if not instalacion.get("evaluar", True):
            texto += "<br>Fuera del cálculo: " + escape(instalacion["motivo_evaluacion"])
        if instalacion["vertice_id"] in por_id:
            texto += (f"<br>Acceso asignado: {instalacion['vertice_id']}"
                      f"<br>Distancia al trazado: {instalacion['distancia_al_trazado_m']:.1f} m")
            intercambios = ", ".join(escape(ix) for ix in (instalacion["intercambios"] or []))
            if intercambios:
                texto += "<br>Presencia de intercambio: " + intercambios
            folium.PolyLine(
                [ubicacion(instalacion["geom"]), ubicacion(instalacion["geom_acceso"])],
                color="#7c3aed", weight=1.5, dash_array="5, 7", opacity=0.7,
                tooltip="Asociación geográfica aproximada al acceso; conexión local supuesta",
            ).add_to(capa_accesos)
        color_ref = "#64748b" if not instalacion.get("evaluar", True) else "#d97706" if instalacion.get("origen") == "telegeography" else "#7c3aed"
        folium.CircleMarker(ubicacion(instalacion["geom"]), radius=4, color=color_ref,
                            fill=True, fill_opacity=0.8,
                            tooltip=escape(instalacion["nombre"]),
                            popup=folium.Popup(texto, max_width=360)).add_to(capa_inst)

    capa_sismo = folium.FeatureGroup(name="Epicentro y radio del escenario", show=True).add_to(mapa)
    folium.Circle(epicentro, radius=radio_m, color="#e11d48", weight=1.8,
                  fill=True, fill_color="#e11d48", fill_opacity=0.06,
                  tooltip=f"Radio supuesto del escenario: {resumen['radio_escenario_km']:g} km").add_to(capa_sismo)
    folium.CircleMarker(epicentro, radius=9, color="white", weight=2, fill=True,
                        fill_color="#e11d48", fill_opacity=1,
                        tooltip="Epicentro USGS · Maule 2010 · M 8,8",
                        popup=f"Evento {escape(resumen['sismo']['id'])}<br>Profundidad: {resumen['sismo']['profundidad_km']:g} km").add_to(capa_sismo)
    folium.LayerControl(position="topright", collapsed=False).add_to(mapa)
    mapa.fit_bounds(capa_prob.get_bounds(), padding=(30, 30))
    agregar_panel(mapa, resumen)
    ruta = directorio / "mapa.html"
    escribir_atomico(ruta, mapa.get_root().render())
    return ruta


def servir_mapa(ruta):
    """Abre el mapa por HTTP local para identificar su origen ante el proveedor."""
    ruta = Path(ruta).resolve()
    manejador = partial(SimpleHTTPRequestHandler, directory=str(ruta.parent))
    with ThreadingHTTPServer(("127.0.0.1", 0), manejador) as servidor:
        url = f"http://127.0.0.1:{servidor.server_port}/{quote(ruta.name)}"
        print(f"Mapa disponible en: {url}", flush=True)
        print("Mantén esta terminal abierta mientras usas el mapa. Ctrl+C detiene el servidor.", flush=True)

        def abrir_navegador():
            try:
                if not webbrowser.open(url):
                    print(f"Abre esta dirección en el navegador de la VM: {url}", flush=True)
            except (OSError, webbrowser.Error):
                print(f"Abre esta dirección en el navegador de la VM: {url}", flush=True)

        Thread(target=abrir_navegador, daemon=True).start()
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor local detenido.")


def preparar_topologia(conexion_bd):
    print("Ejecutando pgr_createTopology con tolerancia 1e-8 grados...", flush=True)
    with psycopg.connect(conexion_bd, autocommit=True, row_factory=dict_row) as conexion:
        cursor = conexion.execute((BASE / "sql/01_topologia.sql").read_text(encoding="utf-8"), prepare=False)
        resultado = None
        while True:
            if cursor.description and cursor.description[0].name == "pgr_createtopology":
                resultado = cursor.fetchone()["pgr_createtopology"]
            if not cursor.nextset():
                break
        if resultado != "OK":
            raise RuntimeError(f"pgr_createTopology no termino con OK: {resultado}")
        control = conexion.execute("""
            SELECT COUNT(*) AS enlaces,
                   COUNT(*) FILTER (WHERE source IS NULL OR target IS NULL) AS sin_topologia,
                   COUNT(*) FILTER (WHERE prob_falla = 0) AS probabilidad_inicial_cero,
                   COUNT(*) FILTER (WHERE es_sintetico) AS sinteticos,
                   SUM(cost) / 1000.0 AS longitud_total_km,
                   (SELECT COUNT(*) FROM public.enlaces_vertices_pgr) AS vertices,
                   (SELECT COUNT(DISTINCT component) FROM pgr_connectedComponents(
                     'SELECT id, source, target, cost, cost AS reverse_cost FROM public.enlaces'
                   )) AS componentes
            FROM public.enlaces
        """).fetchone()
        if not control["enlaces"] or control["sin_topologia"] or control["probabilidad_inicial_cero"] != control["enlaces"]:
            raise RuntimeError("La verificacion inicial de la topologia fallo.")
    print(f"Topologia: {resultado} | {control['enlaces']} enlaces | {control['vertices']} vertices | {control['componentes']} componentes")
    print(f"Longitud: {control['longitud_total_km']:.2f} km | probabilidad inicial cero: {control['probabilidad_inicial_cero']} enlaces")
    return {**control, "tolerancia_grados": 1e-8, "pgr_createtopology": resultado}


def actualizar_sismo(conexion_bd):
    valores = leer_sismo(DATOS / "maule_2010.geojson")
    with psycopg.connect(conexion_bd) as conexion:
        conexion.execute("""
            INSERT INTO public.sismos (id, magnitud, profundidad_km, geom)
            VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            ON CONFLICT (id) DO UPDATE SET magnitud = EXCLUDED.magnitud,
                profundidad_km = EXCLUDED.profundidad_km, geom = EXCLUDED.geom
        """, (SISMO_ID, *valores))
    print(f"Sismo cargado desde GeoJSON USGS: {SISMO_ID} | magnitud {valores[0]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cargar", action="store_true", help="Compatibilidad: la topologia y el catalogo siempre se regeneran.")
    parser.add_argument("--descargar", action="store_true", help="Descargar JSON faltantes de USGS y PeeringDB.")
    parser.add_argument("--radio-km", type=float, default=RADIO_MAXIMO_PREDETERMINADO_KM, help="Radio maximo del escenario (250 km por defecto).")
    parser.add_argument("--radio-escenario-km", type=float, default=None, help="Radio mostrado (por defecto la mitad del maximo).")
    parser.add_argument("--salida", type=Path, default=BASE / "resultados", help="Carpeta para HTML, CSV y resumen.")
    parser.add_argument("--abrir", action="store_true", help="Servir el mapa por HTTP local y abrirlo en el navegador.")
    args = parser.parse_args()
    if not (isfinite(args.radio_km) and args.radio_km > 0):
        parser.error("--radio-km debe ser positivo y finito.")
    if args.radio_escenario_km is not None and not (isfinite(args.radio_escenario_km) and 0 <= args.radio_escenario_km <= args.radio_km):
        parser.error("--radio-escenario-km debe estar entre 0 y --radio-km.")
    try:
        if args.descargar:
            descargar_faltantes()
        motor = BaseDocker(CONEXION_BD)
        motor.preparar()
        motor.cargar_si_falta(BASE / "main.sql")
        actualizar_sismo(CONEXION_BD)
        topologia = preparar_topologia(CONEXION_BD)
        directorio = args.salida.expanduser().resolve()
        completar_catalogo(CONEXION_BD, DATOS, directorio)
        with psycopg.connect(CONEXION_BD, autocommit=True) as conexion:
            conexion.execute((BASE / "sql/04_nodos_finales.sql").read_text(encoding="utf-8"), prepare=False)
        print("Calculando probabilidades y ATTR...")
        resultado = ejecutar_modelo(args.radio_km, args.radio_escenario_km, CONEXION_BD, BASE)
        resultado["topologia"] = topologia
        enlaces, nodos, instalaciones = leer_infraestructura(CONEXION_BD, resultado)
        resultado["main_sql"] = str(motor.exportar(BASE / "main.sql"))
        resumen = exportar_resultados(directorio, resultado, enlaces, nodos, instalaciones)
        print("Generando el mapa Leaflet...")
        ruta = generar_mapa(directorio, resumen, enlaces, nodos, instalaciones)
    except (OSError, ValueError, RuntimeError, psycopg.Error, subprocess.SubprocessError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"ATTR inicial: {resultado['attr_antes']:.6f} ({resultado['attr_antes']:.2%})")
    print(f"ATTR del escenario: {resultado['attr_escenario']:.6f} ({resultado['attr_escenario']:.2%})")
    print(f"ATTR posterior esperado: {resultado['attr_despues_esperado']:.6f} ({resultado['attr_despues_esperado']:.2%})")
    print(f"Pares conectados: {resultado['pares_conectados_antes']} antes / {resultado['pares_conectados_escenario']} despues")
    print(f"Mapa: {ruta}")
    print(f"Resumen: {directorio / 'resumen.json'}")
    print(f"SQL autocontenido: {resultado['main_sql']}")
    print(f"SQL de probabilidades: {resultado['probabilidades_sql']}")
    if args.abrir:
        try:
            servir_mapa(ruta)
        except OSError as error:
            parser.exit(1, f"No se pudo iniciar el servidor local del mapa: {error}\n")


if __name__ == "__main__":
    main()
