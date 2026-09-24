# Endurecimiento operativo — 21/09/2026

## Cambios aplicados

- Acceso por permiso en cada ruta operativa. Las rutas nuevas quedan denegadas hasta declarar su política. La empresa debe estar activa y autorizada; menús y acciones respetan los permisos. Los datos vinculados de otros módulos también se ocultan cuando no hay acceso.
- Grupos `ERP · Ventas`, `ERP · Almacén`, `ERP · Caja`, `ERP · Compras`, `ERP · Contabilidad`, `ERP · Gerencia` y `ERP · Consulta`. Compras no concede aprobación; Gerencia sí. Tener una sesión ya no concede acceso operativo.
- Conciliación automática exige empresa, cuenta bancaria, moneda, importe, sentido, estado confirmado y fecha dentro de tres días. Si hay referencia, también debe coincidir. No se selecciona la primera de varias coincidencias. Las comisiones quedan fuera de la conciliación automática para revisión.
- El saldo solo aplica notas aceptadas u observadas: crédito resta, débito suma. Las notas pendientes reservan capacidad de emisión, pero no reducen todavía la deuda. La corrección de descripción no puede emitirse por el flujo monetario. Una respuesta tardía no revierte un documento contabilizado.
- Reservas, despachos, cobros, emisiones, numeradores y asientos se serializan por empresa. Se recarga el documento después del bloqueo y se revalida el saldo al confirmar un cobro pendiente. Una referencia repetida no puede usarse para otro documento o importe.
- Despachar descuenta existencias, consume reservas, actualiza cantidades entregadas y cambia el estado del pedido en una sola transacción. Si falla cualquier paso, todo se revierte.
- Indicadores de facturación y cobranza convertidos a moneda base al tipo histórico del documento. Facturado es un indicador bruto; no equivale al resultado contable neto. Los importes individuales conservan su moneda. Los cobros extranjeros exigen su propio tipo de cambio; el asiento separa diferencia de cambio y comisión. Pagos parciales cierran al centavo. Notas parciales reparten centavos sin alterar su total.
- Compras muestran tipo de cambio; el costo de recepción se convierte a moneda base. Una compra de faltantes utiliza moneda base porque los costos del catálogo están expresados en ella.
- Pedidos, órdenes, recepciones, comprobantes, cobros, extractos, asientos, reservas, movimientos de stock y registros de integración son de solo lectura en administración, incluso para superusuarios. Sin borrado masivo ni acciones de recálculo. Los borradores se editan en las pantallas operativas. Series no permiten reiniciar correlativos; periodos cerrados no se reabren desde administración.

## Configuración que requiere un responsable

1. En administración, un superusuario debe asignar a cada usuario sus empresas y el grupo mínimo necesario. No se han asignado roles a usuarios automáticamente. Los grupos son globales: el rol aplica a todas las empresas autorizadas de ese usuario. Roles distintos por empresa requieren un modelo adicional de membresías con roles.
2. Configurar los límites monetarios por usuario/empresa; sus importes se comparan en moneda base. Se conserva la política existente: sin límite configurado, o monto máximo cero, no hay tope monetario. Esto no sustituye los permisos de aprobación.
3. Validar las reglas contables `ganancia_cambio` (cuenta haber), `perdida_cambio` (cuenta debe) y `comision_cobro` (cuenta debe). Si falta una regla necesaria, se rechaza el cobro entero, sin cambiar el saldo. Las cuentas sembradas en Demo son ejemplos, no una aprobación del plan contable de una empresa real.
4. Asociar cobros bancarios a la cuenta correcta. Los cobros sin cuenta no se concilian automáticamente. Revisar manualmente extractos ambiguos, comisiones y diferencias de importe: no se ajustan importes para forzar una coincidencia.

## Verificación

84 pruebas automatizadas, incluyendo seis con conexiones simultáneas reales: última unidad de stock, cobro duplicado, exceso de saldo, doble despacho, numeración y doble facturación. También se verifican permisos, separación de empresas, bloqueo de edición administrativa, notas, conversiones, redondeo y renderizado de plantillas.

Ejecutar en PowerShell desde la raíz del proyecto:

```powershell
.venv/Scripts/python.exe manage.py check
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run
$env:ERP_TEST_DB = Join-Path $env:TEMP ('erp-integridad-' + [guid]::NewGuid().ToString('N') + '.sqlite3')
.venv/Scripts/python.exe manage.py test apps.core.tests --noinput
Remove-Item Env:ERP_TEST_DB
```

Usar siempre un archivo de pruebas exclusivo: Django crea y elimina esa base. Sin `ERP_TEST_DB`, las seis pruebas simultáneas se omiten en SQLite para no confundir bloqueos de su base compartida en memoria con el comportamiento del archivo real.

SQLite usa transacciones IMMEDIATE y espera hasta 30 segundos por el escritor. En otros motores se usa bloqueo de fila sobre la empresa. El bloqueo por empresa prioriza integridad; no es una prueba de capacidad para miles de operaciones concurrentes. No se ha validado contra PostgreSQL en esta instalación.

Migraciones aplicadas: core 0002/0003 y tesorería 0002. Respaldo anterior: `respaldo-antes-integridad-20260921-130035.sqlite3`. Incluye datos sensibles; no debe publicarse ni servirse por HTTP. No se reescriben asientos históricos ni se eliminan documentos existentes.

## Límites de esta entrega

Esto no certifica un ERP «100 % sin fallas». Siguen requiriendo trabajo y validación separados: extornos de cobros ya contabilizados, flujo completo de emisión de notas de débito/correcciones descriptivas, revaluación al cierre, pruebas de carga, restauración de respaldos y conectores reales. Los cambios de diferencias de cambio aplican a nuevos cobros, no reparan automáticamente contabilidad anterior. Las protecciones administrativas no impiden que un operador con acceso SQL altere directamente la base.
