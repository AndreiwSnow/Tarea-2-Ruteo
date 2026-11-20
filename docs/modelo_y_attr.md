# Modelo de falla y definicion de ATTR

## Modelo regional de referencia

Vass et al. (2021), *Probabilistic Shared Risk Link Groups Modeling
Correlated Resource Failures Caused by Disasters*, IEEE JSAC 39(9),
2672-2687, DOI https://doi.org/10.1109/JSAC.2021.3064652.
Manuscrito: https://real.mtak.hu/130386/1/PSRLG______JSAC2020.pdf

Su figura 3 presenta un ejemplo de region circular con radio proporcional
a una variable relativa uniforme entre 0 y 1. El modelo elimina enlaces
que tienen algun punto dentro de la region afectada y representa fallas
geograficamente correlacionadas en redes opticas.

Esta actividad adapta ese modelo de la siguiente manera:

1. Se condiciona al evento del Maule de USGS, con epicentro fijo.
2. Se usa una region circular de radio `R = S * R_max`, con
   `S ~ Uniforme(0,1)` comun a todos los enlaces.
3. El enlace e falla si `d_e <= R`, donde `d_e` es su distancia geodesica
   minima al epicentro. Basta con que una parte del segmento interseque
   el disco. El costo del enlace no se transforma en probabilidad.

Por tanto, la probabilidad marginal es:

`p_e = P(R >= d_e) = max(0, 1 - d_e/R_max)`.

La uniformidad es del radio, no del area del disco. Se conserva la dependencia
entre enlaces por el radio comun. Las probabilidades marginales por si solas
no determinan una confiabilidad de red unica: tambien es necesario declarar
la estructura de dependencia. En este caso es la indicada arriba.

## Parametros y procedencia

| Parametro | Valor predeterminado y origen |
| --- | --- |
| Evento, magnitud, epicentro y profundidad | GeoJSON USGS `official20100227063411530_30`, magnitud 8,8; se cargan desde el archivo y se consultan en la BD |
| Distribucion de S | Uniforme entre 0 y 1, segun el ejemplo de disco del modelo regional de Vass et al. |
| Longitud de ruptura usada como referencia | Aproximadamente 500 km, segun la sintesis tectonica de USGS para el evento del Maule |
| Radio maximo R_max | 250 km = 500/2: decision explicita de esta adaptacion, que usa la semilongitud de ruptura como escala geometrica isotropica |
| Radio del escenario ilustrado | 125 km = E[R] = R_max/2; es una realizacion representativa, distinta del promedio de ATTR |
| Distancia al enlace | PostGIS `ST_Distance` sobre `geography`, en metros |

Fuente de la escala de ruptura:
https://earthquake.usgs.gov/earthquakes/eventpage/official20100227063411530_30/executive
El informe USGS Open-File Report 2011-1053 tambien describe una ruptura de
aproximadamente 450-500 km:
https://pubs.usgs.gov/of/2011/1053/of2011-1053_ebook.pdf

La conversion de longitud de ruptura a radio circular es una simplificacion
de esta actividad. Una longitud de falla no es una medida del radio de dano
superficial. El modelo no es una curva de fragilidad calibrada para fibra
chilena, ni una prediccion de PGA o intensidad. Su resultado es una
probabilidad condicional al escenario geometrico. Se declaran esas
limitaciones para evitar atribuir a USGS o al paper una calibracion que
no realizaron.

Los parametros pueden cambiarse en una corrida de sensibilidad. Por ejemplo,
225 km corresponde a la mitad del extremo de 450 km citado en el informe;
300 km puede conservarse como escenario exploratorio previo. Para reproducir
la entrega se usan siempre los parametros guardados en resumen.json.

## Definicion de ATTR

Sea K el conjunto fijo de accesos clave distintos seleccionados del catalogo.
Para una realizacion de la red:

`C(G,K) = sum_{i<j, i,j en K} 1{i tiene ruta a j} / binom(|K|,2)`.

El ATTR probabilistico es su esperanza:

`ATTR = E[C(G,K)] = sum_{i<j, i,j en K} P(i tiene ruta a j) / binom(|K|,2)`.

En una realizacion, cada componente con k accesos clave aporta `k(k-1)/2`
pares. Los accesos aislados siguen perteneciendo al denominador.
Con todas las probabilidades de falla en cero, el ATTR inicial coincide
con el conteo de pares conectados del grafo observado.

Referencia: Neumayer y Modiano (2016), *Network reliability under
geographically correlated line and disk failure models*, Computer Networks
94, 14-28, seccion 4.2, definicion 9:
https://www.mit.edu/~modiano/papers/CV_J_83.pdf

## Metodo exacto desde las probabilidades de la BD

`modelo_sismo.py` guarda `prob_falla` para todos los enlaces de la BD,
lee los valores guardados y obtiene la esperanza con el radio comun.
Para cada enlace se define el umbral normalizado `t_e = 1 - p_e`.
La conectividad cambia solamente al cruzar esos umbrales. El algoritmo
recorre los intervalos en orden inverso, incorpora enlaces con conjuntos
disjuntos y suma `ancho_intervalo * pares_conectados`.

Dividir esa integral por `binom(|K|,2)` da el ATTR esperado exacto bajo
este modelo. Su costo es `O(E log E)` por la ordenacion y operaciones
amortizadas casi constantes de union y busqueda. No utiliza Monte Carlo
ni requiere elegir un numero de iteraciones.

Se verifica que el resultado coincide con la integracion geometrica de los
radios. Los conteos inicial y del escenario ilustrado se contrastan con
`calcular_attr()` y `pgr_connectedComponents` en SQL.

## Alcance de nodos y conexiones

La unidad de evaluacion es el acceso distinto del grafo, no cada nombre
de instalacion que se proyecta al mismo acceso. El catalogo completo
conserva todas las identidades y sus fuentes. Se incluyen las instalaciones
georreferenciadas, las referencias de aterrizaje aceptadas y las
ramificaciones de tres o mas vecinos distintos.

Los registros sin ubicacion fisica verificable quedan listados fuera del
conjunto evaluado. La asociacion de Tortel de aproximadamente 94 km se
descarta por falta de un tramo documentado. Las otras asociaciones son
aproximaciones declaradas y los accesos locales se suponen operativos.
La sensibilidad a esa representacion limita la interpretacion nacional.

Los enlaces del grafo provienen del archivo de fibra; `es_sintetico` es
falso en todos ellos. Las lineas discontinuas de asociacion en el mapa
representan la aproximacion geografica y no son nuevos enlaces de ruteo.
