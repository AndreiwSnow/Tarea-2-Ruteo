-- Referencias actuales de PeeringDB sobre el catastro fisico Yafun.
-- Cada instalacion con coordenadas se representa mediante un punto de acceso:
-- el extremo mas cercano del enlace de fibra mas cercano a su ubicacion.
-- Los accesos locales se suponen operativos en esta aproximacion geografica.
-- El ATTR evalua los puntos de acceso distintos. Una instalacion sin
-- coordenadas conserva su referencia y queda pendiente de incorporacion.

BEGIN;

CREATE TABLE IF NOT EXISTS public.accesos_instalaciones (
    instalacion_id BIGINT PRIMARY KEY
        REFERENCES public.referencias_instalaciones(id),
    vertice_id BIGINT NOT NULL CHECK (vertice_id > 0),
    enlace_id BIGINT NOT NULL,
    distancia_al_trazado_m DOUBLE PRECISION NOT NULL,
    intercambios TEXT[] NOT NULL,
    geom_acceso geometry(Point, 4326) NOT NULL,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DELETE FROM public.accesos_instalaciones AS acceso
WHERE NOT EXISTS (
    SELECT 1
    FROM public.referencias_instalaciones AS instalacion
    WHERE instalacion.id = acceso.instalacion_id
      AND instalacion.geom IS NOT NULL
);

-- Se utiliza la proximidad ya definida en cargar_referencias.py.
WITH asociaciones AS MATERIALIZED (
    SELECT
        referencia.id AS instalacion_id,
        referencia.distancia_m AS distancia_al_trazado_m,
        referencia.intercambios,
        enlace.id AS enlace_id,
        enlace.source,
        enlace.target,
        ST_Distance(
            referencia.geom::geography,
            ST_StartPoint(enlace.geom)::geography
        ) <= ST_Distance(
            referencia.geom::geography,
            ST_EndPoint(enlace.geom)::geography
        ) AS usar_origen
    FROM public.referencias_proximidad AS referencia
    JOIN public.enlaces AS enlace ON enlace.id = referencia.enlace_cercano
    WHERE referencia.geom IS NOT NULL
), seleccion AS (
    SELECT
        instalacion_id,
        CASE WHEN usar_origen THEN source ELSE target END AS vertice_id,
        enlace_id,
        distancia_al_trazado_m,
        intercambios
    FROM asociaciones
)
INSERT INTO public.accesos_instalaciones (
    instalacion_id, vertice_id, enlace_id, distancia_al_trazado_m,
    intercambios, geom_acceso
)
SELECT
    seleccion.instalacion_id,
    seleccion.vertice_id,
    seleccion.enlace_id,
    seleccion.distancia_al_trazado_m,
    seleccion.intercambios,
    vertice.the_geom
FROM seleccion
JOIN public.enlaces_vertices_pgr AS vertice ON vertice.id = seleccion.vertice_id
ON CONFLICT (instalacion_id) DO UPDATE SET
    vertice_id = EXCLUDED.vertice_id,
    enlace_id = EXCLUDED.enlace_id,
    distancia_al_trazado_m = EXCLUDED.distancia_al_trazado_m,
    intercambios = EXCLUDED.intercambios,
    geom_acceso = EXCLUDED.geom_acceso,
    actualizado_en = CURRENT_TIMESTAMP;

COMMENT ON TABLE public.accesos_instalaciones IS
'Asociacion geografica aproximada de instalaciones de PeeringDB a vertices de Yafun. Los accesos locales se suponen operativos.';

-- Se agrupan las instalaciones representadas por el mismo vertice.
CREATE OR REPLACE VIEW public.nodos_clave AS
SELECT
    acceso.vertice_id AS id,
    (ARRAY_AGG(acceso.geom_acceso ORDER BY acceso.instalacion_id))[1] AS geom,
    ARRAY_AGG(instalacion.id ORDER BY instalacion.id) AS instalaciones_ids,
    ARRAY_AGG(instalacion.nombre ORDER BY instalacion.id) AS instalaciones,
    ARRAY_AGG(instalacion.ciudad ORDER BY instalacion.id) AS ciudades,
    ARRAY['genera', 'recibe']::TEXT[] ||
        CASE WHEN BOOL_OR(CARDINALITY(acceso.intercambios) > 0)
             THEN ARRAY['intercambia']::TEXT[]
             ELSE ARRAY[]::TEXT[] END AS roles
FROM public.accesos_instalaciones AS acceso
JOIN public.referencias_instalaciones AS instalacion
    ON instalacion.id = acceso.instalacion_id
GROUP BY acceso.vertice_id;

COMMENT ON VIEW public.nodos_clave IS
'Puntos de acceso distintos usados para el ATTR. Generacion y recepcion son funciones modeladas de las instalaciones. Intercambio corresponde a presencia de IX registrada en PeeringDB.';

CREATE OR REPLACE FUNCTION public.calcular_attr(
    enlaces_fallados BIGINT[] DEFAULT '{}'::BIGINT[]
)
RETURNS TABLE (
    cantidad_nodos BIGINT,
    pares_posibles BIGINT,
    pares_conectados BIGINT,
    attr NUMERIC
)
LANGUAGE SQL STABLE
AS $funcion$
WITH componentes AS MATERIALIZED (
    SELECT node, component
    FROM pgr_connectedComponents(
        FORMAT(
            'SELECT id, source, target, cost, cost AS reverse_cost
             FROM public.enlaces
             WHERE NOT (id = ANY(%L::BIGINT[]))',
            ARRAY_REMOVE(COALESCE(enlaces_fallados, '{}'::BIGINT[]), NULL::BIGINT)
        )
    )
), grupos AS (
    -- Un vertice aislado sigue perteneciendo al conjunto de nodos evaluados.
    -- Los componentes de pgRouting tienen identificadores positivos.
    SELECT
        COALESCE(componente.component, -nodo.id) AS componente_id,
        COUNT(*)::NUMERIC AS cantidad
    FROM public.nodos_clave AS nodo
    LEFT JOIN componentes AS componente ON componente.node = nodo.id
    GROUP BY COALESCE(componente.component, -nodo.id)
), totales AS (
    SELECT
        COALESCE(SUM(cantidad), 0) AS nodos,
        COALESCE(SUM(cantidad * (cantidad - 1) / 2), 0) AS pares
    FROM grupos
)
SELECT
    nodos::BIGINT,
    (nodos * (nodos - 1) / 2)::BIGINT,
    pares::BIGINT,
    CASE WHEN nodos > 1
         THEN pares / (nodos * (nodos - 1) / 2)
         ELSE NULL END
FROM totales;
$funcion$;

COMMENT ON FUNCTION public.calcular_attr(BIGINT[]) IS
'Fraccion de pares de nodos clave distintos que tienen ruta en el escenario. Conserva los nodos aislados y el denominador original. El parametro contiene los identificadores de enlaces retirados.';

COMMIT;

SELECT
    COUNT(*) AS instalaciones_representadas,
    COUNT(DISTINCT vertice_id) AS puntos_de_acceso_distintos,
    ROUND(MAX(distancia_al_trazado_m)::NUMERIC, 1) AS distancia_maxima_m
FROM public.accesos_instalaciones;

SELECT
    COUNT(*) AS nodos_clave,
    COUNT(*) FILTER (WHERE roles @> ARRAY['genera']::TEXT[]) AS generan,
    COUNT(*) FILTER (WHERE roles @> ARRAY['recibe']::TEXT[]) AS reciben,
    COUNT(*) FILTER (WHERE roles @> ARRAY['intercambia']::TEXT[]) AS intercambian
FROM public.nodos_clave;

SELECT
    cantidad_nodos,
    pares_posibles,
    pares_conectados,
    ROUND(attr, 6) AS attr_inicial,
    ROUND(attr * 100, 2) AS porcentaje_pares_conectados
FROM public.calcular_attr();
