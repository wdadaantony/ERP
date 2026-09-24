"""Indicadores del dashboard con alcance de empresa y permisos del usuario."""
from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum
from apps.core.monedas import a_moneda_base


def indicadores(request, comprobantes, pedidos, hoy):
    datos = {'serie_facturacion': [], 'distribucion_pedidos': [], 'saldo_proveedores': None,
             'proveedores_vencidos': 0, 'pagos_proximos': [], 'cantidad_proveedores': 0}
    if request.user.has_perm('facturacion.view_comprobante'):
        meses = [hoy.replace(day=1)]
        for _ in range(5):
            meses.insert(0, (meses[0] - timedelta(days=1)).replace(day=1))
        totales = {mes: Decimal('0') for mes in meses}
        for c in comprobantes.filter(fecha_emision__gte=meses[0], fecha_emision__lte=hoy).select_related('empresa'):
            totales[c.fecha_emision.replace(day=1)] += a_moneda_base(c.total, c)
        maximo = max(totales.values()) or Decimal('1')
        datos['serie_facturacion'] = [dict(mes=mes, total=total, altura=int(total / maximo * 100)) for mes, total in totales.items()]
    if request.user.has_perm('ventas.view_pedido'):
        from apps.ventas.models import EstadoPedido
        from django.db.models import Count
        filas = list(pedidos.values('estado').annotate(n=Count('pk')).order_by('-n'))
        total = sum(f['n'] for f in filas)
        nombres = dict(EstadoPedido.choices)
        datos['distribucion_pedidos'] = [dict(nombre=nombres[f['estado']], cantidad=f['n'],
            ancho=round(f['n'] / total * 100)) for f in filas] if total else []
    if request.user.has_perm('gestion.view_facturaproveedor'):
        from apps.gestion.models import FacturaProveedor
        facturas = FacturaProveedor.objects.filter(empresa=request.empresa, estado='aprobada').select_related('empresa', 'proveedor').annotate(abonado=Sum('pagos__movimiento__monto'))
        saldo = Decimal('0')
        for f in facturas:
            pendiente = f.total - (f.abonado or Decimal('0'))
            if pendiente <= 0:
                continue
            saldo += a_moneda_base(pendiente, f)
            datos['cantidad_proveedores'] += 1
            datos['proveedores_vencidos'] += int(f.vencimiento < hoy)
            if len(datos['pagos_proximos']) < 5:
                datos['pagos_proximos'].append(dict(factura=f, saldo=pendiente, vencida=f.vencimiento < hoy))
        datos['saldo_proveedores'] = saldo
    return datos
