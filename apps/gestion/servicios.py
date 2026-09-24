from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.core.transacciones import operacion_serializada
from apps.core.monedas import a_moneda_base, decimal_finito, factor_cambio, redondear
from apps.core.auditoria import registrar
from apps.contabilidad.servicios import _crear_asiento, _regla
from apps.inventario.models import MovimientoStock, Ubicacion, stock_en_mano, stock_disponible
from apps.inventario.servicios import ubicacion_virtual
from apps.tesoreria.models import Movimiento
from .models import FacturaProveedor, PagoProveedor, OperacionInventario


@operacion_serializada
def aprobar_factura(factura, usuario):
    if factura.estado == 'aprobada':
        return factura
    if not usuario.has_perm('gestion.aprobar_factura'):
        raise ValidationError('No tienes permiso de aprobación.')
    if factura.proveedor.empresa_id != factura.empresa_id or not factura.proveedor.es_proveedor:
        raise ValidationError('El proveedor no pertenece a esta empresa.')
    factor_cambio(factura.moneda, factura.empresa, factura.tipo_cambio)
    if factura.total <= 0 or factura.vencimiento < factura.fecha:
        raise ValidationError('Comprueba el total y el vencimiento.')
    if factura.orden_id:
        orden = factura.orden
        if (orden.empresa_id != factura.empresa_id or orden.proveedor_id != factura.proveedor_id
                or orden.moneda != factura.moneda or orden.estado not in ('aprobada', 'enviada', 'recibida', 'recibida_parcial', 'cerrada')):
            raise ValidationError('La orden no corresponde al proveedor, moneda o estado permitido.')
        otras = FacturaProveedor.objects.filter(orden=orden, estado='aprobada').exclude(pk=factura.pk)
        if sum((f.total for f in otras), Decimal('0')) + factura.total > orden.total:
            raise ValidationError('Las facturas superan el total de la orden de compra.')
    regla = _regla(factura.empresa, 'compra')
    total = a_moneda_base(factura.total, factura)
    neto = a_moneda_base(factura.subtotal, factura)
    lineas = [(factura.cuenta_destino, factura.proveedor, factura.concepto, neto, 0),
              (regla.cuenta_haber, factura.proveedor, factura.numero, 0, total)]
    if total - neto:
        lineas.append((_regla(factura.empresa, 'compra_igv').cuenta_debe, None, 'Impuesto de compra', total - neto, 0))
    factura.asiento = _crear_asiento(factura.empresa, factura.fecha, f'Compra {factura.numero}', lineas,
        clave_automatica=f'factura-proveedor:{factura.pk}', creado_por=usuario)
    factura.estado, factura.aprobada_por = 'aprobada', usuario
    factura.save(update_fields=['estado', 'aprobada_por', 'asiento', 'actualizado_en'])
    registrar(factura, usuario, 'transicion', {'estado': 'borrador'}, {'estado': 'aprobada'})
    return factura


@operacion_serializada
def pagar_factura(factura, usuario, monto, cuenta, referencia, fecha=None, tipo_cambio=None):
    if not usuario.has_perm('gestion.pagar_factura'):
        raise ValidationError('No tienes permiso para registrar pagos.')
    if factura.estado != 'aprobada':
        raise ValidationError('Primero debe aprobarse la factura.')
    monto = decimal_finito(monto)
    fecha = fecha or date.today()
    referencia = referencia.strip()
    if not referencia or len(referencia) > 120 or monto <= 0 or monto != redondear(monto):
        raise ValidationError('Indica una referencia y un importe positivo con hasta dos decimales.')
    if cuenta.empresa_id != factura.empresa_id or cuenta.moneda != factura.moneda or not cuenta.activa:
        raise ValidationError('La cuenta debe estar activa y corresponder a empresa y moneda.')
    previo = Movimiento.objects.filter(empresa=factura.empresa, sentido='pago', referencia_externa=referencia).first()
    if previo:
        if (PagoProveedor.objects.filter(factura=factura, movimiento=previo).exists()
                and previo.monto == monto and previo.cuenta_id == cuenta.pk and previo.fecha == fecha):
            return previo
        raise ValidationError('La referencia ya está utilizada por otro pago.')
    if monto > factura.saldo or fecha < factura.fecha:
        raise ValidationError('El pago supera el saldo o es anterior a la factura.')
    if factura.moneda != factura.empresa.moneda_base and tipo_cambio is None:
        raise ValidationError('Indica el tipo de cambio del pago.')
    tasa = factor_cambio(factura.moneda, factura.empresa, tipo_cambio or 1)
    movimiento = Movimiento.objects.create(empresa=factura.empresa, sentido='pago', tercero=factura.proveedor,
        cuenta=cuenta, metodo='transferencia', monto=monto, moneda=factura.moneda, tipo_cambio=tasa,
        fecha=fecha, estado='confirmado', referencia_externa=referencia, notas=f'Factura proveedor {factura.numero}')
    pagado = factura.pagado
    historico = a_moneda_base(pagado + monto, factura) - a_moneda_base(pagado, factura)
    efectivo = a_moneda_base(monto, movimiento)
    from apps.contabilidad.models import CuentaContable
    banco = CuentaContable.objects.filter(empresa=factura.empresa, codigo=cuenta.cuenta_contable, activa=True, acepta_movimiento=True).first()
    if banco is None:
        raise ValidationError('La cuenta bancaria necesita una cuenta contable activa y de movimiento.')
    lineas = [(_regla(factura.empresa, 'compra').cuenta_haber, factura.proveedor, factura.numero, historico, 0),
              (banco, None, referencia, 0, efectivo)]
    diferencia = efectivo - historico
    if diferencia > 0:
        lineas.append((_regla(factura.empresa, 'perdida_cambio').cuenta_debe, None, 'Diferencia de cambio', diferencia, 0))
    elif diferencia < 0:
        lineas.append((_regla(factura.empresa, 'ganancia_cambio').cuenta_haber, None, 'Diferencia de cambio', 0, -diferencia))
    _crear_asiento(factura.empresa, fecha, f'Pago a proveedor {factura.numero}', lineas,
                   movimiento_tesoreria=movimiento, creado_por=usuario)
    pago = PagoProveedor.objects.create(empresa=factura.empresa, factura=factura, movimiento=movimiento, usuario=usuario)
    registrar(pago, usuario, 'crear', despues={'factura': factura.pk, 'monto': str(monto), 'referencia': referencia})
    return movimiento


@operacion_serializada
def aplicar_inventario(operacion, usuario):
    if operacion.estado == 'hecho':
        return operacion.movimiento
    if not usuario.has_perm('gestion.aprobar_inventario'):
        raise ValidationError('No tienes permiso para aprobar operaciones de inventario.')
    producto, origen, destino = operacion.producto, operacion.origen, operacion.destino
    cantidad = decimal_finito(operacion.cantidad)
    if producto.empresa_id != operacion.empresa_id or origen.empresa_id != operacion.empresa_id or not producto.controla_stock:
        raise ValidationError('Producto o ubicación ajenos a la empresa, o producto sin stock.')
    if cantidad < 0 or cantidad != cantidad.quantize(Decimal('.0001')) or len(operacion.motivo.strip()) < 5:
        raise ValidationError('Revisa la cantidad y el motivo de la operación.')
    costo = producto.costo_promedio
    if (producto.controla_lotes or producto.controla_series) and operacion.tipo != 'devolucion':
        raise ValidationError('Los ajustes de productos con lotes o series requieren un flujo de trazabilidad específico; esta operación no puede omitir esa identificación.')
    if operacion.tipo == 'conteo':
        if origen.tipo != Ubicacion.Tipo.INTERNA:
            raise ValidationError('El conteo requiere una ubicación interna.')
        actual = stock_en_mano(producto, origen, operacion.empresa)
        if actual != operacion.existencia_esperada:
            raise ValidationError('El stock cambió desde el conteo. Registra un nuevo conteo físico.')
        ajuste = ubicacion_virtual(operacion.empresa, Ubicacion.Tipo.AJUSTE)
        diferencia = cantidad - actual
        cantidad = abs(diferencia)
        origen, destino = (ajuste, origen) if diferencia >= 0 else (origen, ajuste)
    elif operacion.tipo == 'transferencia':
        if not destino or origen.tipo != 'interna' or destino.tipo != 'interna' or destino.pk == origen.pk:
            raise ValidationError('Elige dos ubicaciones internas distintas.')
    elif operacion.tipo == 'devolucion':
        original = operacion.movimiento_original
        if not original or original.empresa_id != operacion.empresa_id or original.producto_id != producto.pk:
            raise ValidationError('Selecciona un movimiento original de este producto y empresa.')
        if not ((original.origen.tipo == 'interna' and original.destino.tipo == 'cliente') or
                (original.origen.tipo == 'proveedor' and original.destino.tipo == 'interna')):
            raise ValidationError('Solo se devuelven despachos a clientes o recepciones de proveedores.')
        devuelto = sum((o.cantidad for o in OperacionInventario.objects.filter(movimiento_original=original, estado='hecho')), Decimal('0'))
        if cantidad > original.cantidad - devuelto:
            raise ValidationError('La devolución supera la cantidad pendiente del movimiento original.')
        origen, destino, costo = original.destino, original.origen, original.costo_unitario
    else:
        raise ValidationError('Operación desconocida.')
    if not destino or destino.empresa_id != operacion.empresa_id:
        raise ValidationError('Destino ajeno a esta empresa.')
    if not cantidad and operacion.tipo != 'conteo':
        raise ValidationError('La cantidad debe ser mayor que cero.')
    if cantidad and origen.tipo == 'interna' and stock_disponible(producto, origen, operacion.empresa) < cantidad:
        raise ValidationError('Stock disponible insuficiente; no se puede consumir stock reservado.')
    movimiento = None
    if cantidad:
        existencia = stock_en_mano(producto, empresa=operacion.empresa)
        if operacion.tipo == 'devolucion' and destino.tipo == 'interna':
            producto.costo_promedio = (existencia * producto.costo_promedio + cantidad * costo) / (existencia + cantidad)
            producto.save(update_fields=['costo_promedio', 'actualizado_en'])
        movimiento = MovimientoStock.objects.create(empresa=operacion.empresa, producto=producto,
            cantidad=cantidad, origen=origen, destino=destino, costo_unitario=costo,
            fecha=timezone.now(), usuario=usuario, documento_origen=f'INV-{operacion.pk}',
            documento_tipo='gestion.OperacionInventario', documento_id=str(operacion.pk))
        if operacion.tipo == 'devolucion':
            movimiento.lote = operacion.movimiento_original.lote
            movimiento.numero_serie = operacion.movimiento_original.numero_serie
            movimiento.save(update_fields=['lote', 'numero_serie'])
        contabilizar_stock(movimiento, usuario, operacion.tipo)
    operacion.movimiento, operacion.estado, operacion.aprobada_por = movimiento, 'hecho', usuario
    operacion.save(update_fields=['movimiento', 'estado', 'aprobada_por', 'actualizado_en'])
    registrar(operacion, usuario, 'transicion', {'estado': 'pendiente'}, {'estado': 'hecho', 'motivo': operacion.motivo})
    return movimiento


def contabilizar_stock(movimiento, usuario=None, tipo='venta'):
    from .models import PerfilEmpresa
    if not PerfilEmpresa.objects.filter(empresa=movimiento.empresa, contabilizar_inventario=True).exists():
        return
    entrada = movimiento.destino.tipo == 'interna'
    salida = movimiento.origen.tipo == 'interna'
    if entrada == salida:
        return
    valor = redondear(movimiento.cantidad * movimiento.costo_unitario)
    if not valor:
        return
    nombre = 'costo_venta' if tipo == 'venta' or (tipo == 'devolucion' and movimiento.origen.tipo == 'cliente') else 'ajuste_inventario'
    regla = _regla(movimiento.empresa, nombre)
    debe, haber = (regla.cuenta_haber, regla.cuenta_debe) if entrada else (regla.cuenta_debe, regla.cuenta_haber)
    return _crear_asiento(movimiento.empresa, timezone.localdate(movimiento.fecha),
        f'{tipo}: {movimiento.documento_origen}', [(debe, None, tipo, valor, 0), (haber, None, tipo, 0, valor)],
        clave_automatica=f'stock:{movimiento.pk}', creado_por=usuario)
