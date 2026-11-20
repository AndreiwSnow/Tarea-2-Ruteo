"""Comprueba main.sql del proyecto en una base vacía de la misma VM.

Guarda este archivo junto a main.py y ejecuta:
    .venv/bin/python verificar_restauracion.py
"""

import json
from math import isclose, isfinite
from pathlib import Path
import subprocess
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from completar_catalogo import escribir
from infra_bd import BaseDocker, MARCA_SQL
from modelo_sismo import CONEXION_BD, attr_desde_probabilidades


RAIZ = Path(__file__).resolve().parent
METRICAS = {
    "attr_antes": "Inicial",
    "attr_escenario": "Del escenario",
    "attr_despues_esperado": "Posterior esperado",
}
TOLERANCIA = 1e-12


def medir(conexion, radio_escenario_km):
    conexion.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conteos = conexion.execute("""
        SELECT (SELECT COUNT(*) FROM public.fibra_original) AS trazados,
               (SELECT COUNT(*) FROM public.enlaces_vertices_pgr) AS vertices,
               (SELECT COUNT(*) FROM public.probabilidades_falla) AS probabilidades,
               (SELECT COUNT(DISTINCT component) FROM pgr_connectedComponents(
                   'SELECT id, source, target, cost, cost AS reverse_cost FROM public.enlaces'
               )) AS componentes_iniciales
    """).fetchone()
    modelo = conexion.execute("""
        SELECT s.id AS evento, s.magnitud, m.radio_maximo_m
        FROM public.modelo_falla AS m JOIN public.sismos AS s ON s.id = m.sismo_id
        WHERE m.id = 1
    """).fetchone()
    if modelo is None or not isfinite(modelo["radio_maximo_m"]) or modelo["radio_maximo_m"] <= 0:
        raise ValueError("Falta el evento o el radio máximo del modelo en la base restaurada.")

    claves = {fila["id"] for fila in conexion.execute("SELECT id FROM public.nodos_clave")}
    enlaces = conexion.execute("""
        SELECT e.id, e.source, e.target, e.prob_falla, p.prob_falla AS prob_tabla, p.distancia_m
        FROM public.enlaces AS e
        LEFT JOIN public.probabilidades_falla AS p ON p.enlace_id = e.id ORDER BY e.id
    """).fetchall()
    if not enlaces or len(enlaces) != conteos["probabilidades"]:
        raise ValueError("La cantidad de probabilidades guardadas difiere de la cantidad de enlaces.")
    fallados = []
    for e in enlaces:
        distancia, p = e["distancia_m"], e["prob_falla"]
        if distancia is None or not isfinite(distancia) or distancia < 0:
            raise ValueError(f"Falta una distancia válida para el enlace {e['id']}.")
        esperada = max(0.0, 1.0 - distancia / modelo["radio_maximo_m"])
        if (p is None or not 0 <= p <= 1 or p != e["prob_tabla"]
                or not isclose(p, esperada, rel_tol=0, abs_tol=TOLERANCIA)):
            raise ValueError(f"La probabilidad guardada del enlace {e['id']} difiere del modelo.")
        if e["source"] is None or e["target"] is None:
            raise ValueError(f"Falta un extremo topológico en el enlace {e['id']}.")
        if distancia <= radio_escenario_km * 1000:
            fallados.append(e["id"])

    inicial = conexion.execute("SELECT * FROM public.calcular_attr()").fetchone()
    escenario = conexion.execute(
        "SELECT * FROM public.calcular_attr(%s::bigint[])", (fallados,)
    ).fetchone()
    if (len(claves) < 2 or inicial["cantidad_nodos"] != len(claves)
            or escenario["pares_posibles"] != inicial["pares_posibles"]):
        raise ValueError("Los conjuntos de nodos de la comparación de ATTR son incompatibles.")
    return {
        **conteos,
        "evento": modelo["evento"], "magnitud": modelo["magnitud"],
        "radio_maximo_km": modelo["radio_maximo_m"] / 1000,
        "enlaces_total": len(enlaces), "nodos_clave": len(claves),
        "enlaces_con_probabilidad_positiva": sum(e["prob_falla"] > 0 for e in enlaces),
        "enlaces_fallados_escenario": len(fallados),
        "pares_posibles": inicial["pares_posibles"],
        "pares_conectados_antes": inicial["pares_conectados"],
        "pares_conectados_escenario": escenario["pares_conectados"],
        "attr_antes": float(inicial["attr"]), "attr_escenario": float(escenario["attr"]),
        "attr_despues_esperado": attr_desde_probabilidades(enlaces, claves),
    }


def verificar():
    archivo_sql = RAIZ / "main.sql"
    with archivo_sql.open("rb") as f:
        if f.readline().rstrip() != MARCA_SQL.encode():
            raise ValueError("Se requiere el main.sql completo que generó el paso 12.")
    ref = json.loads((RAIZ / "resultados/resumen.json").read_text(encoding="utf-8"))
    if "topologia" not in ref or "registros_evaluados" not in ref:
        raise ValueError("Espera al final del paso 12 y comprueba su resultados/resumen.json.")
    radio = ref["radio_escenario_km"]
    esperados = {
        "trazados": 1915,  # Recuento del archivo de fibra original de esta actividad.
        "vertices": ref["topologia"]["vertices"], "probabilidades": ref["enlaces_total"],
        "evento": ref["sismo"]["id"], "magnitud": ref["sismo"]["magnitud"],
        **{k: ref[k] for k in ("radio_maximo_km", "enlaces_total", "componentes_iniciales",
                               "nodos_clave", "enlaces_con_probabilidad_positiva", "enlaces_fallados_escenario",
                               "pares_posibles", "pares_conectados_antes", "pares_conectados_escenario", *METRICAS)},
    }
    if not isfinite(radio) or not 0 <= radio <= esperados["radio_maximo_km"]:
        raise ValueError("El radio ilustrado en resumen.json queda fuera del intervalo del modelo.")
    print(f"Corrida de referencia • {ref['nodos_clave']} accesos • escenario de {radio:g} km", flush=True)
    for clave, etiqueta in METRICAS.items():
        print(f"{etiqueta:20s} {ref[clave]:.9f}", flush=True)

    nombre = f"ruteo_verificacion_{uuid4().hex[:16]}"
    prueba = make_conninfo(CONEXION_BD, dbname=nombre)
    motor = BaseDocker(prueba)
    # Si el nombre ya existe, CREATE falla y la restauración nunca se inicia.
    with psycopg.connect(make_conninfo(CONEXION_BD, dbname="postgres", connect_timeout=5),
                         autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(nombre)))
    print(f"Base de prueba creada • {nombre}", flush=True)
    motor.preparar()
    with psycopg.connect(prueba) as conexion:
        tablas, = conexion.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
        ).fetchone()
    if tablas != 0:
        raise ValueError("La base de prueba ya contiene tablas en public.")
    print("Tablas en el esquema public antes de la carga • 0", flush=True)
    if not motor.cargar_si_falta(archivo_sql):
        raise RuntimeError("El cargador omitió la restauración del archivo.")
    with psycopg.connect(prueba, row_factory=dict_row) as conexion:
        valores = medir(conexion, radio)
    diferencias = []
    for k, esperado in esperados.items():
        obtenido = valores[k]
        coincide = (isclose(obtenido, esperado, rel_tol=0, abs_tol=TOLERANCIA)
                    if k in METRICAS else obtenido == esperado)
        if not coincide:
            diferencias.append(k)
    print(f"\nTrazados {valores['trazados']} | enlaces {valores['enlaces_total']} | "
          f"probabilidades {valores['probabilidades']} | vértices {valores['vertices']}")
    print(f"Accesos {valores['nodos_clave']} | pares posibles {valores['pares_posibles']} | "
          f"conectados antes {valores['pares_conectados_antes']} y en el escenario {valores['pares_conectados_escenario']}")
    print(f"{'ATTR':20s} {'Original':>13s} {'Restaurado':>13s}")
    for clave, etiqueta in METRICAS.items():
        print(f"{etiqueta:20s} {esperados[clave]:13.9f} {valores[clave]:13.9f}")
    for clave in diferencias:
        print(f"Diferencia en {clave} • original {esperados[clave]} • restaurado {valores[clave]}")
    evidencia = RAIZ / "resultados" / f"{nombre}.json"
    escribir(evidencia, json.dumps({
        "verificada": not diferencias, "base_prueba": nombre, "tablas_public_antes": tablas,
        "radio_escenario_km": radio, "tolerancia_absoluta_attr": TOLERANCIA,
        "referencia": esperados, "restaurado": valores, "diferencias": diferencias,
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"Evidencia • {evidencia}")
    if diferencias:
        raise ValueError("Los resultados restaurados difieren de la referencia.")
    print("RESTAURACIÓN VERIFICADA")


if __name__ == "__main__":
    try:
        verificar()
    except (OSError, KeyError, ValueError, RuntimeError, psycopg.Error, subprocess.SubprocessError) as error:
        raise SystemExit(f"No se completó la verificación. {error}")
