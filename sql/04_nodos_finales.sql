BEGIN;

-- El conjunto de evaluacion son accesos distintos del catalogo aceptado.
-- Cada fuente conserva tambien su ubicacion original y su asociacion.
CREATE OR REPLACE VIEW public.nodos_clave AS
WITH grupos AS (
    SELECT vertice_id AS id,
           (ARRAY_AGG(geom_acceso ORDER BY clave))[1] AS geom,
           ARRAY_AGG(nombre ORDER BY clave) AS instalaciones,
           ARRAY_AGG(localidad ORDER BY clave) AS ciudades,
           ARRAY_AGG(clave ORDER BY clave) AS claves_catalogo
    FROM public.catalogo_nodos
    WHERE evaluar AND vertice_id IS NOT NULL
    GROUP BY vertice_id
)
SELECT g.id, g.geom,
       ARRAY(
           SELECT SUBSTRING(n.clave FROM 9)::BIGINT
           FROM public.catalogo_nodos AS n
           WHERE n.evaluar AND n.vertice_id = g.id AND n.origen = 'peeringdb'
           ORDER BY n.clave
       ) AS instalaciones_ids,
       g.instalaciones, g.ciudades,
       ARRAY(
           SELECT DISTINCT r.rol FROM public.catalogo_nodos AS n
           CROSS JOIN LATERAL UNNEST(n.roles) AS r(rol)
           WHERE n.evaluar AND n.vertice_id = g.id ORDER BY r.rol
       ) AS roles,
       g.claves_catalogo,
       ARRAY(
           SELECT DISTINCT c.categoria FROM public.catalogo_nodos AS n
           CROSS JOIN LATERAL UNNEST(n.categorias) AS c(categoria)
           WHERE n.evaluar AND n.vertice_id = g.id ORDER BY c.categoria
       ) AS categorias,
       ARRAY(
           SELECT DISTINCT f.fuente FROM public.catalogo_nodos AS n
           CROSS JOIN LATERAL UNNEST(n.fuentes) AS f(fuente)
           WHERE n.evaluar AND n.vertice_id = g.id ORDER BY f.fuente
       ) AS fuentes
FROM grupos AS g;

COMMENT ON VIEW public.nodos_clave IS
'Accesos distintos seleccionados del catalogo. Las ubicaciones de referencia y asociaciones aproximadas se conservan en catalogo_nodos. El conjunto y el denominador son fijos durante cada comparacion.';

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
    FROM pgr_connectedComponents(FORMAT(
        'SELECT id, source, target, cost, cost AS reverse_cost
         FROM public.enlaces
         WHERE NOT (id = ANY(%L::BIGINT[]))',
        ARRAY_REMOVE(COALESCE(enlaces_fallados, '{}'::BIGINT[]), NULL::BIGINT)
    ))
), grupos AS (
    SELECT COALESCE(c.component, -n.id) AS componente,
           COUNT(*)::NUMERIC AS cantidad
    FROM public.nodos_clave AS n
    LEFT JOIN componentes AS c ON c.node = n.id
    GROUP BY COALESCE(c.component, -n.id)
), totales AS (
    SELECT COALESCE(SUM(cantidad), 0) AS nodos,
           COALESCE(SUM(cantidad * (cantidad - 1) / 2), 0) AS pares
    FROM grupos
)
SELECT nodos::BIGINT, (nodos * (nodos - 1) / 2)::BIGINT, pares::BIGINT,
       CASE WHEN nodos > 1 THEN pares / (nodos * (nodos - 1) / 2) END
FROM totales;
$funcion$;

COMMENT ON FUNCTION public.calcular_attr(BIGINT[]) IS
'Conectividad de una realizacion. La esperanza probabilistica se obtiene en Python usando las probabilidades de enlace y el modelo correlacionado de radio comun.';

COMMIT;
