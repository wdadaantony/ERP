"""El crédito disponible incluye deuda y pedidos ya comprometidos, en moneda base."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from apps.core.models import LimiteAprobacion
from apps.core.monedas import a_moneda_base
from apps.facturacion.models import Comprobante, EstadoComprobante, TipoComprobante
from apps.ventas.models import Pedido, EstadoPedido


def validar_credito(pedido, usuario):
    tercero = pedido.tercero
    if not tercero.condicion_pago_id or not tercero.condicion_pago.dias:
        return
    if not usuario:
        raise ValidationError('La venta a crédito requiere un usuario responsable.')
    limite = LimiteAprobacion.objects.filter(usuario=usuario, empresa=pedido.empresa).first()
    if not usuario.is_superuser and (not limite or not limite.puede_vender_al_credito):
        raise ValidationError('No tienes autorización para vender al crédito.')
    if tercero.linea_credito <= 0:
        raise ValidationError('El cliente no tiene una línea de crédito aprobada.')
    comprobantes = Comprobante.objects.filter(
        empresa=pedido.empresa, tercero=tercero,
        tipo__in=(TipoComprobante.FACTURA, TipoComprobante.BOLETA),
    ).exclude(estado=EstadoComprobante.ANULADO).select_related('empresa').prefetch_related('notas')
    deuda = sum((max(Decimal('0'), c.saldo_base) for c in comprobantes), Decimal('0'))
    comprometidos = Pedido.objects.filter(
        empresa=pedido.empresa, tercero=tercero,
        estado__in=(EstadoPedido.CONFIRMADO, EstadoPedido.RESERVADO,
                    EstadoPedido.EN_ESPERA, EstadoPedido.ENTREGADO),
    ).exclude(pk=pedido.pk).exclude(
        pk__in=comprobantes.filter(pedido__isnull=False).values('pedido_id')
    ).select_related('empresa')
    deuda += sum((a_moneda_base(p.total, p) for p in comprometidos), Decimal('0'))
    requerido = a_moneda_base(pedido.total, pedido)
    if deuda + requerido > tercero.linea_credito:
        disponible = max(Decimal('0'), tercero.linea_credito - deuda)
        raise ValidationError(f'Crédito insuficiente. Disponible: {disponible:.2f} {pedido.empresa.moneda_base}.')
