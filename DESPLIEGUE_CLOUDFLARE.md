# GitHub y Cloudflare

El repositorio contiene el código del ERP, no las bases de datos locales,
documentos privados, respaldos ni credenciales. Clonarlo no copia las empresas
que ya existen en otra instalación.

## Estado del despliegue

Subir a GitHub no publica automáticamente el ERP. Este proyecto usa Django,
transacciones de base de datos, archivos privados y un proceso independiente
para la cola de integraciones. No se despliega como un sitio estático de Pages.

Cloudflare admite Django en Python Workers. Sin embargo, su documentación
indica que los backends Django de D1 y Durable Objects no soportan transacciones:
`transaction.atomic` no proporciona rollback. Por ese motivo no se ha adaptado
este ERP a dichos backends; hacerlo sin rediseñar y validar las operaciones
comprometería la consistencia de stock, pagos y contabilidad.

Fuente consultada el 24/09/2026:
https://developers.cloudflare.com/workers/languages/python/packages/django/#database-backends

## Ruta de despliegue conservando el comportamiento actual

Ejecutar Django y su worker en un servidor Python con PostgreSQL y almacenamiento
privado persistente. Cloudflare puede gestionar el dominio y el proxy delante
de ese servidor. Configurar:

- Python compatible con las versiones fijadas en `requirements.txt`.
- Variables de `.env.example`, con `DEBUG=0`, `SECRET_KEY` aleatoria,
  `DATABASE_URL`, `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` del dominio final.
- Instalación: `pip install -r requirements.txt`.
- Migraciones: `python manage.py migrate`.
- Archivos estáticos: `python manage.py collectstatic --noinput`.
- Servidor web: `gunicorn config.wsgi --bind 0.0.0.0:8000`.
- Proceso independiente: `python manage.py procesar_cola --continuo`.
- HTTPS entre Cloudflare y el servidor; no almacenar en caché páginas privadas.
- Documentos privados persistentes, respaldos y supervisión de ambos procesos.

El `Procfile` contiene los comandos de web, worker y migración. No ejecutar
`sembrar_demo` en una base con clientes reales. Completar el alta y los controles
de `PREPARACION_COMERCIAL.md` antes de operar. No hay un despliegue Cloudflare
creado ni recursos de pago contratados por subir este repositorio.

## Opcion preparada: Render + Cloudflare

El repositorio ya trae `render.yaml` para crear un Blueprint en Render con:

- servicio web Python/Django;
- base PostgreSQL gratuita para pruebas;
- `DEBUG=0`, `SECRET_KEY` generada, `DATABASE_URL` interna;
- `collectstatic`, migraciones y Gunicorn.

Flujo recomendado:

1. En Render, crear **New > Blueprint** desde `wdadaantony/ERP`.
2. Esperar a que termine el primer deploy y abrir la URL `*.onrender.com`.
3. Crear el superusuario desde Render Shell:

   ```bash
   python manage.py createsuperuser
   ```

4. Si usaras un dominio propio en Cloudflare, agregarlo como custom domain en Render y luego crear el registro DNS que Render indique.

Para operar con clientes reales falta agregar un worker persistente para `python manage.py procesar_cola --continuo`, storage externo para adjuntos privados y un plan de base de datos que no expire.
