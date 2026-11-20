# Prueba desde un clon limpio

Se clonó el commit `3b4176e29d077227f53008b8c93fbb3c686724b7` en una carpeta vacía. El comando `python3 main.py --abrir` preparó su entorno Python, creó la base `ruteo_clon_02f3ecc15c81`, cargó `main.sql` y obtuvo `OK` de `pgr_createTopology`. El mapa respondió por HTTP con estado 200 y sus bytes coincidieron con el HTML recién generado.

| ATTR | Referencia | Clon |
| --- | ---: | ---: |
| Inicial | 0,065462119 | 0,065462119 |
| Del escenario | 0,040526849 | 0,040526849 |
| Posterior esperado | 0,047064171 | 0,047064171 |

[Controles de la ejecución](prueba_clon.json)
