# Tarea 2 Algoritmos de ruteo

Este repositorio evalúa 189 accesos clave sobre 22.574 segmentos del catastro Yafún. La tabla de probabilidades y el grafo completo se distribuyen en `main.sql`.

## Ejecutar desde un clon limpio

Se necesita Linux con Python 3.10 o posterior y su módulo `venv`, Docker en funcionamiento y acceso a Docker directo o mediante `sudo`. La primera ejecución puede descargar la imagen y las dependencias. El fondo cartográfico de OpenStreetMap y las bibliotecas web de la visualización necesitan conexión.

Desde la raíz del clon, ejecuta:

```bash
python3 main.py --abrir
```

El comando prepara el entorno Python local a partir de `requirements.txt`, crea o inicia PostgreSQL con la imagen declarada en `compose.yaml`, crea la BD si hace falta y restaura `main.sql` al comenzar con una base vacía. Regenera la topología y el catálogo, aplica las probabilidades, calcula los tres ATTR y abre el mapa por HTTP local. La terminal muestra la dirección del mapa. Ctrl+C detiene el servidor. Si se omite `--abrir`, se generan los archivos y el programa termina.

La configuración local usa el contenedor `ruteo-db`, el puerto `5433` y la base `ruteo`. `DATABASE_URL` permite elegir otra base de la misma instancia. En una BD que ya contiene las fuentes, el programa las reutiliza. En cada corrida vuelve a exportar `main.sql` y los archivos de `resultados/` de esa copia del proyecto.

## Resultados de referencia

| Condición | ATTR | Pares con ruta |
| --- | ---: | ---: |
| Operación | 0,065462119 | 1.163 |
| Escenario de 125,00 km | 0,040526849 | 720 |
| Posterior esperado | 0,047064171 | Esperanza sobre radios |

El denominador se mantiene en 17.766 pares. El modelo, las fuentes, los supuestos y el contraste histórico están en el [informe](INFORME.md).

## Archivos y dependencias

- `main.py` coordina el flujo. `infra_bd.py` prepara PostgreSQL y exporta o restaura el SQL. PostGIS calcula distancias sobre el elipsoide y pgRouting genera la topología y los componentes.
- `completar_catalogo.py` y `cargar_referencias.py` reúnen las referencias de nodos. Las copias de las fuentes están en `datos/`.
- `modelo_sismo.py` asigna las probabilidades e integra el ATTR bajo fallas correlacionadas. `sql/03_probabilidades_falla.sql` conserva las actualizaciones por enlace.
- Psycopg conecta Python con PostgreSQL. Folium construye el mapa de Leaflet. Las versiones están en `requirements.txt`. El entorno y el servidor HTTP usan la biblioteca estándar de Python.
- `verificar_restauracion.py` comprueba una restauración independiente. [Prueba del clon](docs/prueba_clon.md).
