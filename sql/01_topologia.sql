BEGIN;

CREATE TABLE IF NOT EXISTS public.enlaces (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trazado_gid BIGINT NOT NULL,
    segmento INTEGER NOT NULL,
    source BIGINT,
    target BIGINT,
    cost DOUBLE PRECISION NOT NULL CHECK (cost > 0),
    geom geometry(LineString, 4326) NOT NULL,
    UNIQUE (trazado_gid, segmento)
);

ALTER TABLE public.enlaces ADD COLUMN IF NOT EXISTS
    prob_falla DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (prob_falla BETWEEN 0 AND 1);
ALTER TABLE public.enlaces ADD COLUMN IF NOT EXISTS
    es_sintetico BOOLEAN NOT NULL DEFAULT FALSE;

INSERT INTO public.enlaces (trazado_gid, segmento, cost, geom)
SELECT fibra.gid, segmento.path[1],
       ST_Length(segmento.geom::geography), segmento.geom
FROM public.fibra_original AS fibra
CROSS JOIN LATERAL ST_DumpSegments(fibra.geom) AS segmento
WHERE ST_Length(segmento.geom::geography) > 0
ORDER BY fibra.gid, segmento.path[1]
ON CONFLICT (trazado_gid, segmento) DO NOTHING;

SELECT pgr_createTopology(
    'public.enlaces', 0.00000001, 'geom', 'id', 'source', 'target', clean := true
);

UPDATE public.enlaces SET prob_falla = 0 WHERE prob_falla <> 0;

COMMIT;
