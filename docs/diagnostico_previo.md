# Diagnostico del conjunto evaluado antes de ampliar el inventario

Datos confirmados en la VM el 19-11-2026:

- Trazados originales: 1.915.
- Enlaces de longitud positiva: 22.574.
- Suma de longitudes geodesicas: 10.312,76 km.
- Vertices: 22.686.
- Componentes del grafo: 134.
- Instalaciones de PeeringDB con coordenadas representadas: 44.
- Accesos distintos usados en el primer ATTR: 15.

De los 15 accesos, dos grupos contienen tres accesos cada uno. Los nueve
restantes estan en componentes que no contienen otro acceso del conjunto
critico. Esto no significa que sean vertices aislados del grafo completo.

| Componente | Accesos | Pares conectados |
| --- | --- | ---: |
| 485 | 715: Grupo ZGH - Colina; 716: EdgeConneX Santiago; 2934: acceso que agrupa 29 instalaciones, con Cirion SAN1 como primer nombre de la lista | 3 |
| 3309 | 3325: Scala SSCLCR01; 5073: ihosting; 5075: TECNOERA LONDRES ZONA 1 | 3 |
| Otros nueve componentes con un acceso cada uno | 5700, 8329, 5614, 1861, 1680, 5714, 3843, 21384, 21386 | 0 |

En ese conjunto, `pares_posibles = 15 * 14 / 2 = 105` y
`pares_conectados = 3 + 3 = 6`.
Por tanto, el ATTR inicial con todos los enlaces operativos es
`6 / 105 = 0,057142857...`, equivalente a 5,71 %.

El acceso 2934 representa 29 instalaciones que se asignaron al mismo extremo
de fibra por proximidad. El nombre Cirion SAN1 es el primer nombre de la
lista agrupada. No prueba que las 29 instalaciones sean Cirion ni que
compartan un acceso fisico real.

El componente 485 representa 32 instalaciones mediante tres accesos.
Si la unidad de conteo fueran las 44 instalaciones individuales y se
mantuvieran las mismas asociaciones y accesos locales supuestos operativos,
se obtendrian `C(32,2) + C(3,2) = 499` pares conectados de `C(44,2) = 946`,
es decir, 52,75 %. Es una comparacion de unidades de conteo bajo los mismos
supuestos, no una medicion independiente de la conectividad real.

El sitio del proyecto Yafun informa aproximadamente 28.000 km de ruta
documentada. Esa magnitud corresponde al recorrido del proyecto y no debe
interpretarse automaticamente como la longitud de fibra entregada en este
archivo. Se debe declarar la cobertura parcial del conjunto utilizado:
https://yafun.nic.cl/mapas/recorrido-yafun/

## Relación con los resultados finales y aclaración 

Este documento conserva los resultados de una etapa preliminar del proyecto, en la que se evaluaron únicamente 15 accesos clave asociados a instalaciones de PeeringDB.

En esa etapa, el ATTR inicial fue de 0,057142857 (5,71 %), correspondiente a 6 pares conectados de 105 posibles.

Posteriormente, el inventario se amplió incorporando referencias de aterrizajes submarinos, IXP/PIT y ramificaciones troncales. El conjunto definitivo comprende 189 accesos distintos, con 1.163 pares conectados de 17.766 posibles, obteniéndose un ATTR inicial de 0,065462119 (6,55 %).

**El valor de 5,71 % corresponde exclusivamente al diagnóstico preliminar y no debe utilizarse como resultado final de la entrega.**

Los resultados oficiales del proyecto se encuentran en `INFORME.md` y `resultados/resumen.json`. El presente documento se conserva como evidencia de la evolución metodológica del trabajo.
