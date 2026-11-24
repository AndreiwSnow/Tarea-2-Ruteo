## Verificación final posterior a la corrección de Docker

Fecha de prueba: 24-11-2026.

Commit evaluado: `946aafe351600fbbe7a244ac4469f006a46da875`.

Se realizó una nueva clonación desde GitHub para verificar la versión corregida, utilizando un contenedor Docker y un volumen independientes de los del desarrollo original.

Se ejecutó `python3 main.py`, que restauró `main.sql`, reconstruyó la topología mediante `pgr_createTopology` y calculó nuevamente las probabilidades y los valores de ATTR. Se comprobó también la generación y apertura del mapa mediante `python3 main.py --abrir`.

| Indicador | Resultado reproducido |
| --- | ---: |
| Enlaces | 22.574 |
| Vértices | 22.686 |
| Componentes | 134 |
| Accesos clave | 189 |
| Pares posibles | 17.766 |
| Pares conectados inicialmente | 1.163 |
| Pares conectados en el escenario | 720 |
| ATTR inicial | 0,065462119 |
| ATTR escenario | 0,040526849 |
| ATTR posterior esperado | 0,047064171 |

Se ejecutó `verificar_restauracion.py`, obteniendo el mensaje `RESTAURACIÓN VERIFICADA`.

La prueba confirmó que la corrección de `infra_bd.py` permite crear el contenedor en el entorno probado y reproducir los resultados publicados. La evidencia anterior se conserva como registro histórico de la versión previa.
