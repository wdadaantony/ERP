# ERP modular

Implementación del ERP descrito en `erp-arquitectura-detallada.html`: núcleo de
módulos sobre una sola base de datos, con la capa de conectores construida desde
el primer día.

Estado actual: interfaz renovada y módulos operativos de ventas, proveedores,
inventario, contabilidad y preparación por empresa. Los servicios externos
siguen en modo simulado hasta implementar y validar el adaptador elegido por
cada cliente.

Consulta [Preparación comercial](PREPARACION_COMERCIAL.md) para el alta sin datos
de demostración, permisos por empresa, secretos, respaldos y límites actuales.
Esa guía sustituye las afirmaciones históricas de preparación para producción:
faltan verificaciones y capacidades adicionales según el alcance comercial.

## Puesta en marcha

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py sembrar_demo
.venv/Scripts/python manage.py createsuperuser
.venv/Scripts/python manage.py runserver
```

El ERP queda en <http://127.0.0.1:8000/> y el admin, para datos maestros y
configuración, en <http://127.0.0.1:8000/admin/>.

En otra terminal, el trabajador que envía a los servicios externos:

```bash
.venv/Scripts/python manage.py procesar_cola --continuo
```

Sin `DATABASE_URL` el proyecto usa SQLite, para poder trabajar sin Postgres
instalado. Para producción, copia `.env.example` a `.env` y define:

```
DATABASE_URL=postgres://usuario:clave@localhost:5432/erp
```

## Hosting

El proyecto incluye configuración de despliegue para servidores Python. Antes
de publicar, completar los controles de PREPARACION_COMERCIAL.md:

- **Base de datos**: `DATABASE_URL` (formato `postgres://usuario:clave@host:puerto/bd`)
  la resuelve `dj-database-url`; con Postgres o MySQL basta con definirla.
- **Servidor WSGI**: `gunicorn config.wsgi` (ya está en `requirements.txt` y en
  el `Procfile`, que además migra en cada release y corre el worker de la cola).
- **Estáticos**: `whitenoise` sirve `staticfiles/` directamente desde
  Gunicorn, sin necesitar Nginx ni un bucket aparte. Antes de arrancar en
  producción corre `manage.py collectstatic --noinput`.
- **Variables obligatorias en producción** (`DEBUG=0`): `SECRET_KEY` (larga y
  aleatoria), `ALLOWED_HOSTS` con el dominio real, `CSRF_TRUSTED_ORIGINS` con
  `https://` + ese dominio, y `DATABASE_URL`. Ver `.env.example`.
- **Media** (adjuntos de leads, logos, etc.): hoy se guardan en disco local
  (`MEDIA_ROOT`); en un hosting con filesystem efímero (Render, Railway free
  tier) hace falta moverlos a un storage externo (S3 o similar) antes de subir
  datos reales — no está hecho todavía.

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn config.wsgi --log-file -
```

## Cómo está organizado

| App | Qué guarda |
|---|---|
| `core` | Empresa, series y correlativos, usuarios multiempresa, límites de aprobación, auditoría |
| `terceros` | Clientes y proveedores en un solo registro, contactos, direcciones |
| `catalogo` | Productos y servicios, unidades SUNAT, listas de precios |
| `inventario` | Almacenes, ubicaciones, movimientos de stock, reservas |
| `ventas` | Pedido, líneas y la máquina de estados |
| `compras` | Órdenes de compra y recepciones de mercadería |
| `facturacion` | Comprobantes electrónicos con XML, hash y CDR |
| `tesoreria` | Cobros, pagos, cuentas bancarias y extractos |
| `contabilidad` | Plan de cuentas, asientos, periodos y reglas automáticas |
| `integraciones` | Servicios externos, mapeos, bitácora, cola de trabajos y webhooks |
| `crm` | Leads, embudo, actividades y conversión a cliente y pedido |

## Las pantallas

| Ruta | Para qué |
|---|---|
| `/` | Tablero del día: facturado del mes, por cobrar, vencido, y lo que requiere atención |
| `/crm/` | Embudo por etapas; `/crm/lista/` para buscar y filtrar |
| `/ventas/` | Pedidos, con su ficha, la línea de tiempo de estados y los botones del flujo |
| `/terceros/` | Clientes y proveedores, con contactos y direcciones |
| `/productos/` | Catálogo con stock y disponible a la vista |
| `/inventario/` | Existencias, kardex por producto y guías de remisión |
| `/compras/` | Órdenes, aprobación y recepción de mercadería |
| `/comprobantes/` | Comprobantes con la respuesta de SUNAT y notas de crédito |
| `/cobranza/` | Lo que se debe, con el cobro en la misma fila; conciliación bancaria |
| `/contabilidad/` | Asientos y saldos por cuenta |
| `/integraciones/` | Estado de cada servicio, cola de errores y bitácora |

Cada usuario ve solo lo suyo salvo que tenga el permiso `ver_todo` del módulo
(`ventas.ver_todo`, `crm.ver_todo`, `terceros.ver_todo`): dos vendedores en la
misma pantalla ven listas distintas. La empresa activa vive en la sesión, y quien
pertenece a varias la cambia desde el pie del menú.

## El flujo, paso a paso

Los modelos guardan; los módulos `servicios.py` de cada app deciden. Nunca se
manipulan los modelos a mano para operar.

```python
from apps.ventas.servicios import confirmar_pedido, crear_pedido
from apps.inventario.servicios import despachar_pedido
from apps.facturacion.servicios import emitir_comprobante
from apps.integraciones.trabajador import procesar_cola
from apps.tesoreria.servicios import registrar_cobro

pedido = crear_pedido(empresa, cliente, almacen, [{"producto": p, "cantidad": 3}])
confirmar_pedido(pedido)          # reserva stock, o deja el pedido «en espera»
despachar_pedido(pedido)          # movimientos de salida, consume la reserva
pedido.transicionar("entregado")
comprobante, trabajo = emitir_comprobante(pedido)   # encola, no llama al OSE
procesar_cola(empresa)            # el trabajador envía y aplica el CDR
registrar_cobro(comprobante, comprobante.total, referencia_externa="OP-4451")
# → el pedido queda cerrado y quedan dos asientos cuadrados
```

**Cuando falta stock** el pedido va a `en_espera` sin dejar reservas parciales
bloqueando a nadie. `generar_orden_compra(pedido, proveedor)` arma la compra del
faltante; al ingresar la mercadería con `ingresar_recepcion()` se recalcula el
costo promedio ponderado y `reintentar_reserva(pedido)` lo destraba.

### Lo que entra de afuera

Cada servicio externo tiene su propia URL de webhook y su propio secreto, de modo
que filtrar una firma compromete solo a ese conector:

```
POST /integraciones/webhook/<servicio_id>/
X-Firma: <hmac-sha256 del cuerpo con el secreto del servicio>
```

La vista valida la firma, guarda el `EventoWebhook` y responde de inmediato. El
procesamiento vive aparte, en `apps/integraciones/webhooks.py`, con un manejador
por tipo de servicio (`@webhook_de(ServicioExterno.Codigo.PASARELA)`).

Las respuestas dicen exactamente qué pasó: `200 procesado`, `200 duplicado`
(reenvío del mismo aviso), `202 recibido` (guardado pero no aplicable todavía —
se reintenta solo), `401 firma inválida`, `400 cuerpo mal formado`.

Todo lo que llega es dato de un tercero, nunca una orden. Un evento sin firma
válida se guarda para poder investigarlo, pero no se procesa ni cuenta para
deduplicar: si contara, bastaría mandar un evento sin firmar con un id adivinado
para que el aviso legítimo posterior se descartara como duplicado.

### Leads que entran solos

Los leads no se digitan: entran por webhook desde donde estén las campañas.

| Servicio | Qué manda | Traductor |
|---|---|---|
| Meta Ads | Formularios instantáneos (`leadgen`) con campaña y anuncio | `desde_meta` |
| WhatsApp Business | Mensajes entrantes de la nube de Meta | `desde_whatsapp` |
| n8n, Make, Zapier | Cualquier carga con nombre, correo o teléfono | `desde_generico` |
| CRM externo | HubSpot, Salesforce (aplana `properties`) | `desde_generico` |

Cada uno tiene su URL y su secreto. Mira las tuyas en **Integraciones**, o así:

```python
ServicioExterno.objects.filter(activo=True).values_list("nombre", "pk")
# /integraciones/webhook/<pk>/
```

Meta y WhatsApp verifican la URL al darla de alta con un GET (`hub.challenge`).
El receptor lo responde si el `hub.verify_token` coincide con el secreto del
servicio, o con `credenciales["verify_token"]` si prefieres separarlos.

**Un lead por persona, no por canal.** Antes de crear se busca por id externo,
documento, correo y teléfono. Quien ve el anuncio, llena el formulario y además
escribe por WhatsApp es **un solo lead con las dos conversaciones en su hilo**.
Al enriquecer nunca se pisa lo que el vendedor ya escribió, y un lead dado por
perdido que vuelve a escribir se reabre como «contactado» en lugar de quedarse
escondido.

El reparto va a quien tenga el permiso `crm.recibir_leads`, al que tenga menos
leads abiertos. Sin ese permiso asignado a nadie, reparte entre todos antes que
dejar leads huérfanos — pero conviene marcarlo, o le llegarán leads a
contabilidad y a almacén.

### La venta vuelve a la plataforma

Cuando el comprobante queda **cobrado**, se encola la conversión hacia Meta con
el valor real de la venta (`crm.servicios.enviar_conversion`). Se hace al cobrar
y no al facturar, para que el número que viaja sea plata realmente entrada.

Es el paso 17 del diagrama, del que el documento dice *«dónde se gana la plata»*:
mientras la plataforma solo sepa que hubo un lead, optimiza por leads baratos;
cuando sabe cuánto facturó, busca gente que compra.

Los datos personales viajan en SHA-256, como exige Meta, y `Lead.conversion_enviada`
impide mandarla dos veces.

### Cuando el aviso nunca llega

No se confía en el webhook: se pregunta.

```bash
python manage.py rescatar                    # las tres tareas
python manage.py rescatar --solo cobros      # solo la pasarela
```

- `rescatar_cobros` consulta a la pasarela por los comprobantes que siguen con
  saldo, y registra el cobro que ya estaba aprobado. Nunca cobra más que el
  saldo, aunque el otro lado diga otra cifra.
- `rescatar_comprobantes` reconsulta al OSE los que llevan mucho «enviado» sin CDR.
- `reprocesar_pendientes` reintenta los webhooks que quedaron sin aplicar.

Va en un cron cada 15 minutos.

### El trabajador de la cola

```bash
python manage.py procesar_cola              # una pasada, para cron
python manage.py procesar_cola --continuo   # se queda corriendo
```

Toma los trabajos vencidos, se los da al conector y aplica la respuesta al ERP.
Una falla de un trabajo nunca tumba el resto de la cola. Para agregar el
posproceso de una operación nueva, decora una función con
`@al_terminar("mi_operacion")` en `apps/integraciones/trabajador.py`.

## Las reglas que el código hace cumplir

Cada una tiene su prueba en `apps/core/tests/test_reglas_del_diseno.py`.

**El stock no es un campo.** No existe `Producto.stock`. La existencia es la suma
de los movimientos entre ubicaciones (`stock_en_mano`), y lo comprometible es esa
suma menos las reservas activas (`stock_disponible`). Las ubicaciones virtuales
—proveedor, cliente, ajuste— son la contrapartida de cada entrada y salida, así
todo movimiento cuadra.

**El pedido no se edita libremente.** `TRANSICIONES` en `apps/ventas/models.py`
declara qué saltos son legales. `Pedido.transicionar()` valida el salto y escribe
en la auditoría; intentar facturar un borrador levanta `TransicionInvalida`.

**El correlativo no se salta.** `Serie.siguiente_numero()` reserva el número con
un `UPDATE ... correlativo + 1` atómico, no leyendo y escribiendo por separado.

**Nada se envía dos veces.** `Conector.encolar()` usa `llave_idempotencia`, con
restricción única por servicio. Reencolar el mismo envío devuelve el trabajo
existente.

**El día malo está previsto.** `TrabajoIntegracion.programar_reintento()` espera
1, 5 y 15 minutos, y al tercer fallo manda el trabajo a la cola de errores.

**Todo envío queda registrado.** `Conector.ejecutar()` escribe un
`RegistroIntegracion` con lo enviado, lo recibido, el código HTTP y la duración,
tanto si sale bien como si falla.

## La capa de conectores

El núcleo nunca llama a un servicio externo mientras el usuario espera. Encola un
trabajo; un trabajador lo toma y se lo da al conector. Cambiar de pasarela o de
OSE es cambiar una clase.

Para agregar un conector:

1. Subclase de `Conector` en `apps/integraciones/conectores/`.
2. Define `codigo_servicio` y `operaciones`.
3. Implementa `_ejecutar(operacion, carga) -> Respuesta`.
4. Decórala con `@registrar` e impórtala en `conectores/__init__.py`.

Ante un timeout o un 5xx, levanta `ErrorConector(recuperable=True)` para que la
cola reintente.

`ConectorOSE` ya existe en modo simulado: acepta comprobantes y devuelve un CDR
falso con la forma del real. Cuando contrates el OSE, implementa
`_llamar_al_ose()` y desmarca `modo_simulado` en el servicio; el resto del ERP no
cambia.

## Pruebas

```bash
.venv/Scripts/python manage.py test apps
```

Las de concurrencia real (dos vendedores por la última unidad, doble cobro,
correlativos simultáneos) usan hilos y necesitan una base propia, así que se
omiten por defecto. Para correrlas:

```bash
ERP_TEST_DB=./pruebas.sqlite3 .venv/Scripts/python manage.py test apps.core.tests.test_concurrencia
```

## Notas de crédito

`emitir_nota_credito(comprobante, motivo="06")` extorna todo lo que queda vivo
del comprobante; con `monto` hace un extorno parcial. Valida el motivo contra el
catálogo 09 de SUNAT, no deja acreditar más de lo que el comprobante tiene sin
acreditar, y al ser aceptada genera el asiento de la venta al revés.

Como cualquier comprobante, se encola: tampoco llama al OSE en vivo.

## Lo que falta

**Depende de ti (contratar el servicio):**

- **Conector real del OSE**: implementar `_llamar_al_ose()` en
  `apps/integraciones/conectores/ose.py` y apagar `modo_simulado` en el servicio.
- **Conector real de la pasarela**: lo mismo en `pasarela.py`.

**Siguientes pasos naturales:**

- **Portal del cliente y del proveedor**: estado de cuenta, descarga de
  comprobantes, seguimiento del pedido.
- **Conectores de CRM externo, WhatsApp y Ads**: la fase 3 del documento. La capa
  ya está; falta una clase por servicio.
- **Devolver la conversión a Ads** con el valor real de la venta: el lead ya
  guarda origen, campaña y UTM, y `Lead.conversion_enviada` está reservado para
  marcarlo. Es el paso 17 del diagrama, donde se gana la plata.
- **Reportes y BI**: hoy hay indicadores en el inicio y saldos por cuenta; falta
  el tablero de márgenes y rotación.
- **Fase 2**: conector real del OSE y conciliación bancaria automática nocturna.
- **Fase 3**: CRM, WhatsApp, Ads y pasarela sobre la capa de conectores.

### Despliegue rapido en Render

El repositorio incluye `render.yaml`, preparado para crear un servicio web Django y una base PostgreSQL gratuita desde GitHub. En Render:

1. **New > Blueprint**.
2. Conecta `https://github.com/wdadaantony/ERP`.
3. Selecciona la rama `main`.
4. Revisa el plan gratuito y confirma **Apply**.

Render ejecutara `build.sh`, aplicara migraciones y arrancara Gunicorn. La URL temporal sera `https://erp.onrender.com` o una variante disponible si ese nombre ya esta tomado. La base gratuita sirve para probar y vence segun las condiciones vigentes de Render; para clientes reales usa un plan persistente y configura el worker de integraciones como servicio separado con:

```bash
python manage.py procesar_cola --continuo
```
