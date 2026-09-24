"""Contadores del menú lateral: lo que pide atención en cada módulo."""
from django.db.models import F
from apps.core.alcance import acotar


def pendientes(request):
    empresa = getattr(request, "empresa", None)
    if empresa is None or not request.user.is_authenticated:
        return {}

    from apps.catalogo.models import Producto
    from apps.crm.models import EstadoLead, Lead
    from apps.facturacion.models import Comprobante, EstadoComprobante
    from apps.integraciones.models import TrabajoIntegracion
    from apps.inventario.models import stock_en_mano
    from apps.ventas.models import EstadoPedido, Pedido

    bajo_minimo = 0
    productos = Producto.objects.filter(
        empresa=empresa, activo=True, controla_stock=True, stock_minimo__gt=0
    ).only("id", "stock_minimo") if request.user.has_perm("inventario.view_movimientostock") else []
    for producto in productos:
        if stock_en_mano(producto, empresa=empresa) < producto.stock_minimo:
            bajo_minimo += 1

    return {
        "pendientes": {
            "leads": acotar(Lead.objects.all(), request, "crm", "vendedor").filter(
                estado=EstadoLead.NUEVO
            ).count() if request.user.has_perm("crm.view_lead") else 0,
            "en_espera": acotar(Pedido.objects.all(), request, "ventas", "vendedor").filter(
                estado=EstadoPedido.EN_ESPERA
            ).count() if request.user.has_perm("ventas.view_pedido") else 0,
            "rechazados": acotar(Comprobante.objects.all(), request, "ventas", "pedido__vendedor").filter(
                estado=EstadoComprobante.RECHAZADO
            ).count() if request.user.has_perm("facturacion.view_comprobante") else 0,
            "fallidos": TrabajoIntegracion.objects.filter(
                empresa=empresa, estado=TrabajoIntegracion.Estado.FALLIDO
            ).count() if request.user.has_perm("integraciones.view_registrointegracion") else 0,
            "bajo_minimo": bajo_minimo,
        }
    }
