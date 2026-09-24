"""Orquestación del pedido de venta.

Aquí vive el paso 6 al 8 del diagrama de carriles: confirmar el pedido, decidir
si hay stock, reservar o mandar a comprar. Los modelos guardan; estos servicios
deciden.
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from apps.core.transacciones import operacion_serializada
from apps.core.monedas import a_moneda_base, factor_cambio

from apps.core.models import LimiteAprobacion, Serie
from apps.inventario.servicios import StockInsuficiente, liberar_reservas, reservar_linea
from apps.ventas.models import EstadoPedido, Pedido


class LimiteExcedido(ValidationError):
    """El usuario no tiene atribución para aprobar este documento."""


@operacion_serializada
def siguiente_numero_pedido(empresa):
    """Correlativo propio de los pedidos, con la misma garantía que las series fiscales."""
    serie, _ = Serie.objects.get_or_create(
        empresa=empresa, tipo_documento="PED", serie="PED", defaults={"correlativo_actual": 0}
    )
    return f"PED-{serie.siguiente_numero():06d}"


def validar_limites(pedido, usuario):
    """Comprueba descuento máximo y monto máximo del vendedor.

    Pasado el límite el documento no se confirma solo: necesita visto bueno.
    """
    if usuario is None or usuario.is_superuser:
        return
    limite = LimiteAprobacion.objects.filter(usuario=usuario, empresa=pedido.empresa).first()
    if limite is None:
        return

    if limite.monto_maximo_venta and a_moneda_base(pedido.total, pedido) > limite.monto_maximo_venta:
        raise LimiteExcedido(
            f"El pedido suma {pedido.total} y tu límite es {limite.monto_maximo_venta}. "
            "Necesita aprobación."
        )
    descuento_mayor = max(
        (linea.descuento_pct for linea in pedido.lineas.all()), default=Decimal("0")
    )
    if descuento_mayor > limite.descuento_maximo_pct:
        raise LimiteExcedido(
            f"El descuento de {descuento_mayor}% supera tu máximo de "
            f"{limite.descuento_maximo_pct}%. Necesita aprobación."
        )


@operacion_serializada
def confirmar_pedido(pedido, usuario=None):
    """Confirma el pedido e intenta reservar el stock.

    Devuelve el pedido en `reservado` si alcanzó todo, o en `en_espera` si faltó
    algo. El faltante queda en `pedido.faltantes` para que compras lo resuelva.
    """
    if not pedido.lineas.exists():
        raise ValidationError("Un pedido sin líneas no se puede confirmar.")

    pedido.recalcular_totales()
    factor_cambio(pedido.moneda, pedido.empresa, pedido.tipo_cambio)
    validar_limites(pedido, usuario)
    from apps.ventas.credito import validar_credito
    validar_credito(pedido, usuario)

    if pedido.estado in (EstadoPedido.BORRADOR, EstadoPedido.ENVIADA):
        pedido.transicionar(EstadoPedido.CONFIRMADO, usuario)
    elif pedido.estado != EstadoPedido.CONFIRMADO:
        raise ValidationError(
            f"Un pedido en «{pedido.get_estado_display()}» ya no se confirma."
        )

    faltantes = []
    for linea in pedido.lineas.select_related("producto"):
        try:
            reservar_linea(linea)
        except StockInsuficiente as falta:
            faltantes.append(
                {
                    "linea": linea,
                    "producto": falta.producto,
                    "solicitado": falta.solicitado,
                    "disponible": falta.disponible,
                }
            )

    pedido.faltantes = faltantes
    if faltantes:
        # No se puede servir todavía: se libera lo parcial y se espera la compra.
        liberar_reservas(pedido)
        pedido.transicionar(EstadoPedido.EN_ESPERA, usuario)
    else:
        pedido.transicionar(EstadoPedido.RESERVADO, usuario)
    return pedido


@operacion_serializada
def reintentar_reserva(pedido, usuario=None):
    """Vuelve a intentar apartar el stock de un pedido en espera.

    Es lo que se corre cuando llega la mercadería comprada.
    """
    if pedido.estado != EstadoPedido.EN_ESPERA:
        raise ValidationError("Solo un pedido «en espera» puede reintentar la reserva.")

    faltantes = []
    for linea in pedido.lineas.select_related("producto"):
        try:
            reservar_linea(linea)
        except StockInsuficiente as falta:
            faltantes.append({"linea": linea, "producto": falta.producto})

    pedido.faltantes = faltantes
    if faltantes:
        liberar_reservas(pedido)
        return pedido
    pedido.transicionar(EstadoPedido.RESERVADO, usuario)
    return pedido


@operacion_serializada
def cancelar_pedido(pedido, usuario=None, motivo=""):
    """Anula el pedido y devuelve al disponible todo lo que tenía apartado."""
    if pedido.lineas.filter(cantidad_entregada__gt=0).exists():
        raise ValidationError('El pedido ya tiene entregas. Registra una devolución antes de resolver su saldo; no puede cancelarse directamente.')
    liberadas = liberar_reservas(pedido)
    pedido.transicionar(EstadoPedido.CANCELADO, usuario)
    if motivo:
        pedido.notas = f"{pedido.notas}\nCancelado: {motivo}".strip()
        pedido.save(update_fields=["notas", "actualizado_en"])
    return liberadas


@operacion_serializada
def generar_orden_compra(pedido, proveedor, usuario=None):
    """Arma la orden de compra del faltante de un pedido en espera.

    Es el desvío del diagrama: «¿hay stock? → no → orden de compra».
    """
    from apps.compras.models import EstadoOrdenCompra, LineaOrdenCompra, OrdenCompra
    from apps.inventario.models import stock_disponible
    from apps.inventario.servicios import ubicacion_de_stock

    if pedido.estado != EstadoPedido.EN_ESPERA:
        raise ValidationError("Solo se compra el faltante de un pedido «en espera».")

    serie, _ = Serie.objects.get_or_create(
        empresa=pedido.empresa, tipo_documento="OC", serie="OC", defaults={"correlativo_actual": 0}
    )
    orden = OrdenCompra.objects.create(
        empresa=pedido.empresa,
        numero=f"OC-{serie.siguiente_numero():06d}",
        proveedor=proveedor,
        almacen_destino=pedido.almacen,
        solicitante=usuario,
        estado=EstadoOrdenCompra.BORRADOR,
        fecha=date.today(),
        moneda=pedido.empresa.moneda_base,
        pedido_origen=pedido,
        notas=f"Faltante del pedido {pedido.numero}",
    )

    ubicacion = ubicacion_de_stock(pedido.almacen)
    creadas = 0
    for linea in pedido.lineas.select_related("producto"):
        producto = linea.producto
        if not producto.controla_stock:
            continue
        disponible = stock_disponible(producto, ubicacion, pedido.empresa)
        faltante = linea.cantidad - max(disponible, Decimal("0"))
        if faltante <= 0:
            continue
        LineaOrdenCompra.objects.create(
            empresa=pedido.empresa,
            orden=orden,
            producto=producto,
            cantidad=faltante,
            precio_unitario=producto.costo_promedio or producto.precio_lista,
        )
        creadas += 1

    if not creadas:
        orden.delete()
        raise ValidationError("Ya no hay faltante que comprar para este pedido.")

    orden.recalcular_totales()
    return orden


@operacion_serializada
def crear_pedido(empresa, tercero, almacen, lineas, usuario=None, **extra):
    """Atajo para armar un pedido con sus líneas en una sola llamada.

    `lineas` es una lista de diccionarios con producto, cantidad y, opcionalmente,
    precio_unitario y descuento_pct.
    """
    from apps.ventas.models import LineaPedido

    with transaction.atomic():
        pedido = Pedido.objects.create(
            empresa=empresa,
            numero=extra.pop("numero", None) or siguiente_numero_pedido(empresa),
            tercero=tercero,
            almacen=almacen,
            vendedor=usuario,
            fecha=extra.pop("fecha", None) or date.today(),
            **extra,
        )
        for datos in lineas:
            producto = datos["producto"]
            LineaPedido.objects.create(
                empresa=empresa,
                pedido=pedido,
                producto=producto,
                cantidad=Decimal(str(datos["cantidad"])),
                precio_unitario=Decimal(
                    str(datos.get("precio_unitario", producto.precio_lista))
                ),
                descuento_pct=Decimal(str(datos.get("descuento_pct", 0))),
            )
        pedido.recalcular_totales()
    return pedido
