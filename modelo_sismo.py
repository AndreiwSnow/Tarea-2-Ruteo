"""Probabilidades y ATTR para un disco de dano con radio uniforme.

Adaptacion del modelo regional de Vass et al. (2021),
https://doi.org/10.1109/JSAC.2021.3064652

El epicentro se fija en el evento del Maule descargado de USGS.
R ~ Uniforme(0, R_max). Un enlace falla si su distancia minima al
epicentro es menor o igual que R. R_max es un parametro supuesto del
escenario academico. Su valor predeterminado es 250 km: la mitad de los
aproximadamente 500 km de longitud de ruptura descritos por USGS para el
27F, usados como escala geometrica simplificada, no como radio de dano medido.
"""

import argparse
from itertools import groupby
from math import isfinite
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


BASE = Path(__file__).resolve().parent
CONEXION_BD = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:ruteo_local@127.0.0.1:5433/ruteo"
)
SISMO_ID = "official20100227063411530_30"
REFERENCIA = "https://doi.org/10.1109/JSAC.2021.3064652"
FUENTE_ESCALA = "https://earthquake.usgs.gov/earthquakes/eventpage/official20100227063411530_30/executive"
LONGITUD_RUPTURA_KM = 500.0
RADIO_MAXIMO_PREDETERMINADO_KM = LONGITUD_RUPTURA_KM / 2


class ConjuntosDisjuntos:
    """Union-find con conteo de pares de nodos clave conectados."""

    def __init__(self, enlaces, claves):
        vertices = set(claves)
        for enlace in enlaces:
            vertices.update((enlace["source"], enlace["target"]))
        self.padre = {vertice: vertice for vertice in vertices}
        self.tamano = {vertice: 1 for vertice in vertices}
        self.claves = {vertice: int(vertice in claves) for vertice in vertices}
        self.pares = 0

    def raiz(self, vertice):
        while self.padre[vertice] != vertice:
            self.padre[vertice] = self.padre[self.padre[vertice]]
            vertice = self.padre[vertice]
        return vertice

    def unir(self, origen, destino):
        origen, destino = self.raiz(origen), self.raiz(destino)
        if origen == destino:
            return
        if self.tamano[origen] < self.tamano[destino]:
            origen, destino = destino, origen
        self.pares += self.claves[origen] * self.claves[destino]
        self.padre[destino] = origen
        self.tamano[origen] += self.tamano[destino]
        self.claves[origen] += self.claves[destino]


def pares_para_radio(enlaces, claves, radio_m=None):
    conjuntos = ConjuntosDisjuntos(enlaces, claves)
    for enlace in enlaces:
        if radio_m is None or enlace["distancia_m"] > radio_m:
            conjuntos.unir(enlace["source"], enlace["target"])
    return conjuntos.pares


def attr_esperado(enlaces, claves, radio_maximo_m):
    """Integra exactamente el ATTR sobre los radios entre 0 y R_max.

    La conectividad cambia en las distancias de los enlaces. Se incorporan
    enlaces de mayor a menor distancia y se integra cada intervalo de radio.
    """
    claves = set(claves)
    if len(claves) < 2:
        raise ValueError("Se necesitan al menos dos puntos de acceso distintos.")
    if not isfinite(radio_maximo_m) or radio_maximo_m <= 0:
        raise ValueError("El radio maximo debe ser un numero positivo finito.")

    conjuntos = ConjuntosDisjuntos(enlaces, claves)
    ordenados = sorted(enlaces, key=lambda enlace: enlace["distancia_m"], reverse=True)
    limite = radio_maximo_m
    integral_pares = 0.0
    for distancia, grupo in groupby(ordenados, key=lambda enlace: enlace["distancia_m"]):
        if distancia <= radio_maximo_m:
            integral_pares += (limite - distancia) * conjuntos.pares
            limite = distancia
        for enlace in grupo:
            conjuntos.unir(enlace["source"], enlace["target"])
    integral_pares += limite * conjuntos.pares
    pares_posibles = len(claves) * (len(claves) - 1) // 2
    return integral_pares / (radio_maximo_m * pares_posibles)


def construir_sql(enlaces, sismo_id, radio_maximo_m, conexion=None):
    """Genera el SQL reproducible con los valores de cada probabilidad."""
    evento_sql = sql.Literal(sismo_id).as_string(conexion)
    fuente_sql = sql.Literal(REFERENCIA).as_string(conexion)
    radio_sql = repr(float(radio_maximo_m))
    filas = []
    for enlace in enlaces:
        probabilidad = max(0.0, min(1.0, 1.0 - enlace["distancia_m"] / radio_maximo_m))
        filas.append(
            f"({int(enlace['id'])}, {float(enlace['distancia_m'])!r}, {probabilidad!r})"
        )
    if not filas:
        raise ValueError("La red debe contener enlaces para exportar probabilidades.")

    cabecera = f"""-- Modelo regional de disco con radio uniforme, Vass et al. (2021).
-- Referencia: {REFERENCIA}
-- R_max supuesto del escenario: {radio_maximo_m / 1000:g} km.
-- Distancia minima geodesica desde el epicentro a cada enlace, en metros.
CREATE TABLE IF NOT EXISTS public.modelo_falla (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    sismo_id TEXT NOT NULL REFERENCES public.sismos(id),
    radio_maximo_m DOUBLE PRECISION NOT NULL CHECK (radio_maximo_m > 0),
    modelo TEXT NOT NULL,
    fuente TEXT NOT NULL
);
INSERT INTO public.modelo_falla (id, sismo_id, radio_maximo_m, modelo, fuente)
VALUES (1, {evento_sql}, {radio_sql}, 'Disco de radio uniforme', {fuente_sql})
ON CONFLICT (id) DO UPDATE SET
    sismo_id = EXCLUDED.sismo_id,
    radio_maximo_m = EXCLUDED.radio_maximo_m,
    modelo = EXCLUDED.modelo,
    fuente = EXCLUDED.fuente;

CREATE TABLE IF NOT EXISTS public.probabilidades_falla (
    enlace_id BIGINT PRIMARY KEY REFERENCES public.enlaces(id) ON DELETE CASCADE,
    distancia_m DOUBLE PRECISION NOT NULL CHECK (distancia_m >= 0),
    prob_falla DOUBLE PRECISION NOT NULL CHECK (prob_falla BETWEEN 0 AND 1)
);
DELETE FROM public.probabilidades_falla AS probabilidad
WHERE NOT EXISTS (
    SELECT 1 FROM public.enlaces AS enlace WHERE enlace.id = probabilidad.enlace_id
);
INSERT INTO public.probabilidades_falla (enlace_id, distancia_m, prob_falla)
VALUES
"""
    cierre = """
ON CONFLICT (enlace_id) DO UPDATE SET
    distancia_m = EXCLUDED.distancia_m,
    prob_falla = EXCLUDED.prob_falla;
COMMENT ON TABLE public.probabilidades_falla IS
'Probabilidades marginales del modelo regional; los enlaces comparten el radio de dano.';
UPDATE public.enlaces AS e SET prob_falla = p.prob_falla
FROM public.probabilidades_falla AS p WHERE p.enlace_id = e.id;
"""
    return cabecera + ",\n".join(filas) + cierre


def attr_desde_probabilidades(enlaces, claves):
    """E[ATTR] exacto desde prob_falla bajo el modelo de radio comun.

    S es uniforme en [0,1]. Cada enlace falla cuando S >= 1 - p_e.
    Este acoplamiento representa las fallas regionales correlacionadas.
    """
    claves = set(claves)
    if len(claves) < 2:
        raise ValueError("Se necesitan al menos dos accesos clave.")
    for e in enlaces:
        p = e["prob_falla"]
        if not isfinite(p) or not 0 <= p <= 1:
            raise ValueError("Las probabilidades deben estar entre cero y uno.")
    conjuntos = ConjuntosDisjuntos(enlaces, claves)
    ordenados = sorted(enlaces, key=lambda e: 1 - e["prob_falla"], reverse=True)
    limite, integral = 1.0, 0.0
    for umbral, grupo in groupby(ordenados, key=lambda e: 1 - e["prob_falla"]):
        integral += (limite - umbral) * conjuntos.pares
        limite = umbral
        for e in grupo:
            conjuntos.unir(e["source"], e["target"])
    integral += limite * conjuntos.pares
    return integral / (len(claves) * (len(claves) - 1) / 2)


def ejecutar_modelo(
    radio_maximo_km=RADIO_MAXIMO_PREDETERMINADO_KM,
    radio_escenario_km=None,
    conexion_bd=CONEXION_BD,
    directorio_salida=BASE,
):
    radio_maximo_m = float(radio_maximo_km) * 1000
    radio_escenario_m = (
        radio_maximo_m / 2
        if radio_escenario_km is None
        else float(radio_escenario_km) * 1000
    )
    if not isfinite(radio_maximo_m) or radio_maximo_m <= 0:
        raise ValueError("El radio maximo debe ser positivo y finito.")
    if not isfinite(radio_escenario_m) or not 0 <= radio_escenario_m <= radio_maximo_m:
        raise ValueError("El radio del escenario debe estar entre 0 y el radio maximo.")

    ruta_sql = Path(directorio_salida) / "sql" / "03_probabilidades_falla.sql"
    ruta_sql.parent.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(conexion_bd, autocommit=True, row_factory=dict_row) as conexion:
        # Serializa las ejecuciones del generador hasta guardar el archivo SQL.
        conexion.execute("SELECT pg_advisory_lock(20100227)")
        with conexion.transaction():
            conexion.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conexion.execute("""
                ALTER TABLE public.enlaces ADD COLUMN IF NOT EXISTS
                prob_falla DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (prob_falla BETWEEN 0 AND 1)
            """)
            conexion.execute("UPDATE public.enlaces SET prob_falla = 0 WHERE prob_falla <> 0")
            sismo = conexion.execute("""
                SELECT id, magnitud, profundidad_km,
                       ST_X(geom) AS longitud, ST_Y(geom) AS latitud
                FROM public.sismos WHERE id = %s;
            """, (SISMO_ID,)).fetchone()
            if sismo is None:
                raise ValueError(f"Falta cargar el sismo {SISMO_ID}.")

            claves = {
                fila["id"] for fila in conexion.execute("SELECT id FROM public.nodos_clave")
            }
            if len(claves) < 2:
                raise ValueError("Se necesitan al menos dos nodos clave distintos.")

            enlaces = conexion.execute("""
                SELECT enlace.id, enlace.source, enlace.target,
                       ST_Distance(enlace.geom::geography, sismo.geom::geography)
                           AS distancia_m
                FROM public.enlaces AS enlace
                CROSS JOIN public.sismos AS sismo
                WHERE sismo.id = %s
                ORDER BY enlace.id;
            """, (SISMO_ID,)).fetchall()
            for enlace in enlaces:
                if enlace["source"] is None or enlace["target"] is None:
                    raise ValueError(f"El enlace {enlace['id']} necesita topologia.")
                distancia = enlace["distancia_m"]
                if distancia is None or not isfinite(distancia) or distancia < 0:
                    raise ValueError(f"Distancia invalida en enlace {enlace['id']}.")

            pares_posibles = len(claves) * (len(claves) - 1) // 2
            pares_antes = pares_para_radio(enlaces, claves)
            pares_escenario = pares_para_radio(enlaces, claves, radio_escenario_m)
            esperado_geometrico = attr_esperado(enlaces, claves, radio_maximo_m)
            fallados = [
                enlace["id"] for enlace in enlaces
                if enlace["distancia_m"] <= radio_escenario_m
            ]

            # Contrasta los conteos de Python con la funcion SQL ya verificada.
            base_sql = conexion.execute("SELECT * FROM public.calcular_attr() ").fetchone()
            escenario_sql = conexion.execute(
                "SELECT * FROM public.calcular_attr(%s::BIGINT[])", (fallados,)
            ).fetchone()
            if (
                base_sql["cantidad_nodos"] != len(claves)
                or base_sql["pares_posibles"] != pares_posibles
                or base_sql["pares_conectados"] != pares_antes
                or escenario_sql["cantidad_nodos"] != len(claves)
                or escenario_sql["pares_conectados"] != pares_escenario
            ):
                raise RuntimeError("Los conteos de Python y pgRouting difieren.")
            probabilidades_base = conexion.execute(
                "SELECT id, source, target, prob_falla FROM public.enlaces ORDER BY id"
            ).fetchall()
            if any(e["prob_falla"] != 0 for e in probabilidades_base):
                raise RuntimeError("La probabilidad inicial debe ser cero en todos los enlaces.")
            base_esperada = attr_desde_probabilidades(probabilidades_base, claves)
            if abs(base_esperada - pares_antes / pares_posibles) > 1e-12:
                raise RuntimeError("El ATTR inicial probabilistico y el conteo de componentes difieren.")
            cuerpo_sql = construir_sql(enlaces, SISMO_ID, radio_maximo_m, conexion)
            conexion.execute(cuerpo_sql, prepare=False)
            probabilidades_bd = conexion.execute(
                "SELECT id, source, target, prob_falla FROM public.enlaces ORDER BY id"
            ).fetchall()
            esperado = attr_desde_probabilidades(probabilidades_bd, claves)
            if abs(esperado - esperado_geometrico) > 1e-12:
                raise RuntimeError("Las probabilidades guardadas y el modelo geometrico no coinciden.")
            if esperado > base_esperada + 1e-12:
                raise RuntimeError("El ATTR esperado supera al inicial.")

        temporal = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=ruta_sql.parent, delete=False
            ) as archivo:
                temporal = Path(archivo.name)
                archivo.write("BEGIN;\n" + cuerpo_sql + "\nCOMMIT;\n")
            temporal.replace(ruta_sql)
        finally:
            if temporal is not None:
                temporal.unlink(missing_ok=True)

    return {
        "sismo": sismo,
        "radio_maximo_km": radio_maximo_m / 1000,
        "radio_escenario_km": radio_escenario_m / 1000,
        "nodos_clave": len(claves),
        "pares_posibles": pares_posibles,
        "pares_conectados_antes": pares_antes,
        "pares_conectados_escenario": pares_escenario,
        "attr_antes": pares_antes / pares_posibles,
        "attr_escenario": pares_escenario / pares_posibles,
        "attr_despues_esperado": esperado,
        "diferencia_attr_esperado": esperado - base_esperada,
        "variacion_porcentual_attr": (esperado / base_esperada - 1) * 100 if base_esperada else None,
        "enlaces_base_probabilidad_cero": len(probabilidades_base),
        "fuente_escala": FUENTE_ESCALA,
        "longitud_ruptura_referencia_km": LONGITUD_RUPTURA_KM,
        "origen_radio": "Mitad de una ruptura de referencia de 500 km; aproximacion geometrica" if radio_maximo_km == RADIO_MAXIMO_PREDETERMINADO_KM else "Parametro de escenario elegido por el usuario",
        "enlaces_total": len(enlaces),
        "enlaces_con_probabilidad_positiva": sum(
            enlace["distancia_m"] < radio_maximo_m for enlace in enlaces
        ),
        "enlaces_fallados_escenario": len(fallados),
        "probabilidades_sql": str(ruta_sql),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radio-km", type=float, default=RADIO_MAXIMO_PREDETERMINADO_KM)
    parser.add_argument("--radio-escenario-km", type=float)
    args = parser.parse_args()
    resultado = ejecutar_modelo(args.radio_km, args.radio_escenario_km)
    print(f"Sismo: {resultado['sismo']['id']} | magnitud {resultado['sismo']['magnitud']}")
    print(f"Radio maximo supuesto: {resultado['radio_maximo_km']:g} km")
    print(f"Radio del escenario mostrado: {resultado['radio_escenario_km']:g} km")
    print(f"Nodos clave: {resultado['nodos_clave']}")
    print(f"Pares posibles: {resultado['pares_posibles']}")
    print(f"Enlaces totales: {resultado['enlaces_total']}")
    print(f"Enlaces con probabilidad positiva: {resultado['enlaces_con_probabilidad_positiva']}")
    print(f"Enlaces fallados en el escenario: {resultado['enlaces_fallados_escenario']}")
    print(f"ATTR inicial: {resultado['attr_antes']:.6f} ({resultado['attr_antes']:.2%})")
    print(f"ATTR del escenario: {resultado['attr_escenario']:.6f} ({resultado['attr_escenario']:.2%})")
    print(f"ATTR posterior esperado: {resultado['attr_despues_esperado']:.6f} "
          f"({resultado['attr_despues_esperado']:.2%})")
    print(f"Archivo generado: {resultado['probabilidades_sql']}")


if __name__ == "__main__":
    main()
