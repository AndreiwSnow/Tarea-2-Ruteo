# Resultados de la evaluacion

| Medida | Valor |
| --- | ---: |
| Accesos clave distintos | 189 |
| Pares posibles, fijos durante la comparacion | 17766 |
| Pares conectados antes | 1163 |
| Pares conectados en el escenario mostrado | 720 |
| ATTR inicial | 0.065462119 |
| ATTR posterior esperado | 0.047064171 |
| Diferencia esperada, despues menos antes | -0.018397947 |
| Diferencia en puntos porcentuales | -1.839795 |
| Variacion porcentual respecto al inicial | -28.1047 % |
| Radio maximo del modelo, km | 250 |
| Radio del escenario mostrado, km | 125 |
| Enlaces fallados en el escenario | 2992 |

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
