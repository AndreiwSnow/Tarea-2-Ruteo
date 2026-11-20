# Criterio de seleccion de nodos y evidencia de fuentes

Este paso construye un inventario ampliado para revisar el conjunto de nodos
criticos requerido por la rubrica. Cada registro conserva nombre, localidad,
coordenadas disponibles, categorias, funciones modeladas, fuente y criterio.
Las asociaciones al grafo conservan la distancia al trazado y al vertice usado.

| Categoria | Fuente y criterio | Rol en el modelo |
| --- | --- | --- |
| Instalacion de alojamiento o interconexion | Todos los registros de instalaciones de las copias PeeringDB del trabajo | Genera y recibe, como funciones modeladas del alojamiento de servicios y la entrega de informacion |
| IXP/PIT | Presencia de intercambio registrada en una instalacion de PeeringDB | Intercambia; comparte el registro fisico de la instalacion que lo aloja |
| Aterrizaje submarino | Referencias de localidad en Chile del Submarine Cable Map de TeleGeography, sin coordenadas pendientes de definir | Recibe e intercambia entre los sistemas submarinos y terrestres |
| Ramificacion troncal modelada | Todos los vertices del catastro con al menos tres vecinos distintos | Intercambia, como funcion inferida de distribucion de transporte en el grafo |

## PeeringDB

Se reutilizan las copias ya cargadas en `referencias_instalaciones`,
`referencias_intercambios` y `referencias_presencias`.
El criterio identifica instalaciones de alojamiento e interconexion.
Esta categoria no acredita que cada instalacion sea un centro de datos con
computo propio ni mide el trafico que genera o recibe. Los roles se declaran
como parte del modelo. Las presencias de intercambio conservan el nombre
y la URL de cada IXP del registro.

La instalacion ODATA ST01 se complementa con las coordenadas del localizador
de campus que publica el operador en https://odatadc.com/es/localidades/.
El archivo `datos/localizaciones_verificadas.json` conserva titulo,
direccion, coordenadas originales y fuente. Se interpretan como coordenadas
geograficas WGS84 para el modelo; el operador no declara una exactitud de
levantamiento ni acredita el acceso fisico de fibra con ese localizador.

Un intercambio sin ninguna sede georreferenciada en las fuentes conservadas
se incluye como registro pendiente. Una instalacion sin coordenadas tambien
conserva su nombre y su fuente. Estos casos se deben resolver o delimitar
expresamente antes de cerrar el conjunto final de evaluacion.

La funcion de intercambio de los PIT se describe en el documento tecnico de
SUBTEL, *Requisitos tecnicos y administrativos de un punto de intercambio de
trafico de Internet*:
https://www.subtel.gob.cl/images/stories/articles/subtel/asocfile/req_tec_trafico.pdf

## Aterrizajes submarinos

La copia de `datos/aterrizajes_chile.geojson` contiene 19 puntos de referencia
seleccionados del catalogo de TeleGeography consultado el 19-11-2026. Se
seleccionaron nombres terminados en `, Chile` con `is_tbd = false`. Se
conservan los identificadores originales, las coordenadas y la URL de cada
registro. El archivo `datos/fuentes_catalogo.json` conserva su hash SHA-256.

Fuente de coordenadas:
https://www.submarinecablemap.com/api/v3/landing-point/landing-point-geo.json

Mapa y atribucion: TeleGeography, Submarine Cable Map,
https://www.submarinecablemap.com/

Las coordenadas son referencias de localidad del catalogo. No son un
levantamiento de la estacion fisica ni permiten identificar el trazado
local real desde el aterrizaje a la fibra observada. El archivo de puntos
no incluye una cronologia completa de operacion de cada cable.

Como verificacion adicional, el operador Telxius identifica Arica y
Valparaiso como aterrizajes del sistema Mistral:
https://telxius.com/en/mistral-2/

Este inventario incorpora referencias contemporaneas sobre un catastro
historico. Su aplicacion al sismo de 2010 es un escenario de simulacion;
no reconstruye todos los sistemas que existian en 2010.

## Ramificaciones troncales modeladas

Se consideran todos los vertices que tienen tres o mas vecinos distintos
en la topologia del catastro de fibra. Se utiliza `UNION` para que enlaces
paralelos o duplicados hacia el mismo vecino no aumenten artificialmente
el grado. Los lazos de un vertice a si mismo no cuentan como vecinos.

Es un criterio estructural y reproducible para identificar puntos de
ramificacion del grafo. El catastro geometrico no verifica que exista un
router, repetidor o PoP de un operador en cada uno de esos puntos. Por eso
se usa la categoria explicita `ramificacion_troncal_modelada`.

Fuente del grafo: `fibra_optica_detectada.shp` del archivo de transparencia
Yafun, transformado con `pgr_createTopology`. Referencia del proyecto:
https://yafun.nic.cl/mapas/recorrido-yafun/

## Asociacion de las ubicaciones al grafo

Para las referencias externas se busca el segmento de fibra mas cercano,
usando distancia geodesica en metros con PostGIS `geography`. Se selecciona
despues el extremo de ese segmento mas cercano al punto de referencia.
Se conservan ambas distancias: al segmento y al vertice del grafo.

La proximidad es una aproximacion de acceso y no prueba una conexion fisica.
Una asociacion de varios kilometros debe analizarse como incertidumbre
de representacion. Las ramificaciones usan directamente su propio vertice.
Este inventario no agrega tramos a la tabla de enlaces.

## Unidad de conteo y siguiente integracion

`catalogo_nodos` conserva cada registro de fuente por separado.
`nodos_clave_candidatos` agrupa los registros asociados al mismo vertice del
grafo para facilitar la revision. Contar instalaciones y contar accesos
distintos son dos unidades diferentes y producen distintos denominadores.
La evaluacion final usa los accesos distintos aceptados, como nodos de la
topologia, y mantiene sus identidades y funciones asociadas en el inventario.

La columna `evaluar` identifica los registros admitidos. Los registros sin
ubicacion fisica verificable quedan pendientes y fuera del conjunto
evaluado. Tortel conserva su punto y su distancia de asociacion, pero se
excluye la asignacion a casi 94 km porque falta un tramo documentado.
Las otras asociaciones se incluyen como aproximaciones, con sus distancias
declaradas y accesos locales supuestos operativos; requieren interpretar
los resultados como conectividad del grafo representado.

La vista `nodos_clave` se regenera desde los registros aceptados y se usa
de forma consistente en el calculo, el mapa y el SQL autocontenido.
`docs/diagnostico_previo.md` conserva la explicacion del conjunto de 15
accesos del prototipo como antecedente, no como resultado de esta version.

## Archivos generados

- `resultados/catalogo_nodos.csv`: inventario completo, incluidos pendientes.
- `resultados/catalogo_nodos.geojson`: registros con coordenadas de referencia.
- `resultados/diagnostico_catalogo.json`: cantidades, fuentes y pendientes.

Los archivos se regeneran al ejecutar `python completar_catalogo.py`.
