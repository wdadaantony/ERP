# Preparación comercial y operación

Actualizado: 24 de septiembre de 2026. Esta versión prepara un ERP para varias
empresas con integraciones configurables. La validación local no equivale a una
certificación tributaria ni a una validación del servidor de producción.

## Funciones incorporadas

- Facturas de proveedores con aprobación, vencimientos, pagos parciales,
  referencia bancaria única y asientos. Los pagos registran una operación ya
  realizada: no transfieren dinero. Las órdenes vinculadas limitan el total
  facturado por proveedor, empresa y moneda.
- Transferencias, conteos físicos y devoluciones con solicitud y aprobación.
  Los conteos se rechazan si cambió la existencia desde la solicitud; las
  operaciones protegen el stock reservado. Las devoluciones no pueden superar
  la cantidad del movimiento original.
- Despachos parciales: la cantidad ingresada es el total acumulado entregado,
  de modo que repetir el envío no duplique movimientos.
- Crédito comercial: condición de pago, autorización, línea del cliente y
  exposición comprometida en pedidos y comprobantes.
- Agenda CRM, cotización imprimible, importación validada de nuevos productos,
  ventas netas y antigüedad de deuda, balance por fechas con CSV, ajustes
  contables y movimientos de caja registrados.
- Membresías con roles por empresa, recuperación de contraseña por correo,
  adjuntos privados y credenciales referenciadas a variables de entorno.
- Diagnóstico de preparación por empresa, respaldo SQLite con manifiesto,
  verificación y restauración en un directorio nuevo, y comprobación de salud.

## Alta de una empresa cliente

No ejecutar `sembrar_demo` en una base de clientes reales. Crear previamente un
usuario activo desde Administración y ejecutar:

```powershell
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py configurar_roles
.venv/Scripts/python.exe manage.py alta_empresa --ruc 20123456789 --razon-social "Empresa de ejemplo" --usuario gerente --moneda PEN --rubro Comercio
```

El RUC mostrado es ilustrativo. El alta crea una empresa vacía, almacén,
ubicaciones, condición contado y series iniciales que deben revisarse. No crea
stock, saldos ni un plan contable supuesto. Configurar datos fiscales, series
autorizadas, unidades, impuestos, bancos y cuentas antes de registrar operaciones.

En Administración, revisar las membresías y asignar los roles mínimos para cada
empresa. Para usuarios existentes sin membresías todavía se conserva el sistema
anterior de roles globales; migrarlos expresamente antes de habilitar acceso a
clientes. Cuando un usuario tiene membresías, sus permisos se toman de la
membresía activa de la empresa seleccionada. Los superusuarios conservan acceso
global y deben reservarse para administradores de la plataforma.

Abrir **Sistema → Preparación de empresa**. Las reglas de compras necesitan
`compra` y `compra_igv`; validar también ventas, cobros y diferencias de cambio.
Para generar asientos de movimientos de stock, configurar `costo_venta` y
`ajuste_inventario` y luego activar `contabilizar_inventario` en Perfil de empresa.
Esto no reconstruye asientos históricos ni carga saldos iniciales.

## Conectar un proveedor posteriormente

Cada Servicio externo pertenece a una empresa y define código, proveedor,
URL, credenciales y modo simulado. Mantener el modo simulado durante la
preparación. Un servicio desactivado no ejecuta operaciones.

1. Implementar una subclase de `Conector` en un módulo instalado. Debe resolver
   las operaciones del proveedor, tiempos de espera, errores recuperables,
   idempotencia y autenticidad de sus webhooks. Declarar
   `soporta_modo_real = True` solamente cuando el adaptador esté implementado.
2. Registrar su ruta mediante la variable de entorno del servidor:

   ```text
   ERP_CONNECTOR_BACKENDS={"ose:mi_proveedor":"mi_paquete.ose.MiConector"}
   ```

   La clave debe coincidir exactamente con el código y proveedor del servicio.
   Las clases se instalan en el servidor; no se cargan desde datos enviados por
   usuarios. Los conectores genéricos simulados rechazan el modo real.
3. Usar referencias en el JSON de credenciales, por ejemplo
   `{"token":"env:EMPRESA_123_OSE_TOKEN"}`, o
   `{"_env":"EMPRESA_123_OSE_CREDENCIALES"}` para un objeto JSON completo.
   El secreto webhook también admite `env:EMPRESA_123_WEBHOOK`.
   Definir las variables fuera de la base de datos, con permisos restringidos.
   En producción se rechazan secretos literales. Migrar los secretos antiguos
   que pudieran existir en la base y rotarlos según corresponda.
4. Reiniciar web y worker después de cambiar variables. Probar primero con el
   entorno de pruebas del proveedor y cuentas de prueba de esa empresa.
5. Validar aceptación y rechazo, reintentos, duplicados, firmas, conciliación y
   archivos antes de desactivar el modo simulado para operaciones reales.

El diagnóstico local distingue simulado, adaptador pendiente y configuración
pendiente de verificar. No prueba conectividad ni certifica al proveedor.

Un adaptador de facturación puede devolver `respuesta.datos['archivos']` con
claves `pdf`, `xml` y `cdr` codificadas en base64 (máximo 10 MB por archivo).
Se comprueba formato básico y se guardan en almacenamiento privado; no pueden
reemplazarse por contenido distinto una vez asociados. Esto no valida la firma
tributaria: corresponde al adaptador y al proceso de homologación. Una
cotización imprimible no sustituye a un comprobante fiscal.

## Despliegue y recuperación

Configurar `DEBUG=0`, clave de aplicación, dominios, HTTPS, base de datos,
almacenamiento persistente privado y SMTP. Nunca publicar directamente
`MEDIA_ROOT`: las descargas deben pasar por las vistas autenticadas. Los ZIP de
respaldo tampoco deben servirse por HTTP. Mantener secretos fuera del repositorio.
El proxy de confianza debe controlar la cabecera de protocolo reenviado.

```powershell
.venv/Scripts/python.exe manage.py check --deploy
.venv/Scripts/python.exe manage.py collectstatic --noinput
.venv/Scripts/python.exe manage.py procesar_cola --continuo
.venv/Scripts/python.exe manage.py diagnosticar_operacion
.venv/Scripts/python.exe manage.py respaldar --destino D:/respaldos-privados/erp
.venv/Scripts/python.exe manage.py verificar_respaldo D:/respaldos-privados/erp/ARCHIVO.zip --restaurar-en D:/restauraciones/ensayo-nuevo
```

Los dos últimos comandos soportan SQLite. Para PostgreSQL usar `pg_dump` y
respaldo privado de documentos, y probar la restauración de ambos. Para una
copia coherente entre base y archivos, detener temporalmente las escrituras
durante el respaldo. El ZIP no está cifrado y no incluye variables de entorno:
protegerlo y custodiar la configuración por separado. Restaurar primero en un
entorno aislado; el comando no sustituye la base en uso. Definir frecuencia,
retención, copia externa y alertas en el servidor. No se instaló un programador
de tareas ni un servicio externo de monitoreo.

`/salud/` comprueba la conexión a la base, sin exponer datos. El diagnóstico
detecta trabajos fallidos y retrasos de la cola; no reemplaza la supervisión del
worker, los trabajos bloqueados en proceso ni las alertas de infraestructura.

## Límites que deben resolverse según el producto vendido

- Integraciones reales, validación tributaria y homologación por proveedor.
- MFA, limitación de intentos de acceso y revisión de seguridad del despliegue.
- Notas de crédito de proveedor, reversión de pagos y revaluación de cuentas por
  pagar/bancos. El cierre actual revalúa cuentas por cobrar; revisar las demás
  partidas con el responsable contable antes de cerrar.
- Flujos completos de lotes y series: las nuevas transferencias y conteos
  rechazan productos con seguimiento; las devoluciones conservan el seguimiento
  del movimiento original. No existe reconstrucción contable retroactiva.
- Estados financieros legales, rentabilidad y presupuesto. Ventas netas incluye
  impuestos y no es utilidad; el flujo mostrado usa movimientos confirmados y
  no representa el saldo bancario ni un estado financiero normativo.
- Importaciones amplias: la disponible crea productos nuevos y no importa
  existencias, saldos iniciales o históricos. Los ajustes manuales se limitan a
  usuarios con permiso y quedan auditados, sin doble aprobación independiente.
- Protección adicional de archivos (antimalware), limpieza de archivos huérfanos
  tras errores, escalabilidad, carga y concurrencia en PostgreSQL, y validación
  del aislamiento de empresas en el entorno final. La separación actual usa una
  base compartida y permisos de aplicación, no bases independientes.

## Evidencia local

Se ejecutaron 133 pruebas con base SQLite en archivo, incluyendo concurrencia,
sin fallos. Se verificaron migraciones, páginas y un respaldo restaurado en un
directorio independiente con comprobación de integridad. Estas comprobaciones
no acreditan conectividad con servicios externos ni rendimiento en producción.
