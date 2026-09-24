"""Operaciones de almacén.

Todo lo que toca el stock pasa por aquí. Los movimientos siempre nacen amarrados
al documento que los originó, y las reservas se toman al confirmar el pedido, no
al facturar.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from apps.core.transacciones import operacion_serializada
from apps.core.monedas import factor_cambio
from django.utils import timezone

from apps.inventario.models import (
    MovimientoStock,
    ReservaStock,
    Ubicacion,
    stock_disponible,
)


class StockInsuficiente(ValidationError):
    """No alcanza lo disponible para lo que se quiere comprometer o sacar."""

    def __init__(self, producto, solicitado, disponible):
        self.producto = producto
        self.solicitado = solicitado
        self.disponible = disponible
        super().__init__(
            f"Stock insuficiente de {producto}: se piden {solicitado} y hay {disponible}."
        )


def ubicacion_de_stock(almacen):
    """Ubicación física por defecto del almacén."""
    ubicacion = almacen.ubicaciones.filter(tipo=Ubicacion.Tipo.INTERNA).order_by("id").first()
    if ubicacion is None:
        raise ValidationError(f"El almacén {almacen} no tiene ninguna ubicación interna.")
    return ubicacion


def ubicacion_virtual(empresa, tipo):
    """Contrapartida virtual: proveedor, cliente o ajuste."""
    ubicacion = Ubicacion.objects.filter(empresa=empresa, tipo=tipo).order_by("id").first()
    if ubicacion is None:
        raise ValidationError(
            f"Falta la ubicación virtual de tipo «{tipo}» en {empresa}. "
            "Créala en Inventario › Ubicaciones."
        )
    return ubicacion


@operacion_serializada
def reservar_linea(linea, ubicacion=None):
    """Aparta stock para una línea de pedido.

    Devuelve la reserva creada, o `None` si el producto no controla stock.
    Levanta `StockInsuficiente` si no alcanza: quien llama decide si el pedido
    pasa a «en espera».
    """
    producto = linea.producto
    if not producto.controla_stock:
        return None

    ubicacion = ubicacion or ubicacion_de_stock(linea.pedido.almacen)
    ya_reservado = sum(
        (r.cantidad for r in linea.reservas.filter(estado=ReservaStock.Estado.ACTIVA)),
        Decimal("0"),
    )
    faltante = linea.cantidad - ya_reservado
    if faltante <= 0:
        return None

    disponible = stock_disponible(producto, ubicacion, linea.empresa)
    if disponible < faltante:
        raise StockInsuficiente(producto, faltante, disponible)

    return ReservaStock.objects.create(
        empresa=linea.empresa,
        producto=producto,
        ubicacion=ubicacion,
        cantidad=faltante,
        linea_pedido=linea,
        documento_origen=linea.pedido.numero,
    )


@operacion_serializada
def liberar_reservas(pedido):
    """Devuelve al disponible todo lo apartado por un pedido. Se usa al cancelar."""
    return ReservaStock.objects.filter(
        linea_pedido__pedido=pedido, estado=ReservaStock.Estado.ACTIVA
    ).update(estado=ReservaStock.Estado.LIBERADA)


@operacion_serializada
def despachar_pedido(pedido, usuario=None, fecha=None, cantidades_acumuladas=None):
    """Saca la mercadería del almacén y consume las reservas.

    Genera un movimiento por línea, de la ubicación física a la virtual de
    cliente, y actualiza `cantidad_entregada`. Devuelve los movimientos creados.
    """
    fecha = fecha or timezone.now()
    if pedido.estado != "reservado":
        raise ValidationError("Solo se puede despachar un pedido con stock reservado.")
    origen = ubicacion_de_stock(pedido.almacen)
    destino = ubicacion_virtual(pedido.empresa, Ubicacion.Tipo.CLIENTE)
    movimientos = []

    for linea in pedido.lineas.select_related("producto"):
        objetivo = linea.cantidad if cantidades_acumuladas is None else Decimal(str(cantidades_acumuladas.get(linea.pk, linea.cantidad_entregada)))
        if not objetivo.is_finite() or objetivo < linea.cantidad_entregada or objetivo > linea.cantidad or objetivo != objetivo.quantize(Decimal('.0001')):
            raise ValidationError('La cantidad acumulada debe estar entre lo ya entregado y lo solicitado, con hasta cuatro decimales.')
        if not linea.producto.controla_stock:
            linea.cantidad_entregada = objetivo
            linea.save(update_fields=["cantidad_entregada", "actualizado_en"])
            continue
        pendiente = objetivo - linea.cantidad_entregada
        if pendiente <= 0:
            continue

        disponible = stock_disponible(linea.producto, origen, pedido.empresa)
        reservado_propio = sum(
            (r.cantidad for r in linea.reservas.filter(estado=ReservaStock.Estado.ACTIVA)),
            Decimal("0"),
        )
        # Lo propio ya está apartado; solo lo que exceda compite con el resto.
        if pendiente > disponible + reservado_propio:
            raise StockInsuficiente(linea.producto, pendiente, disponible + reservado_propio)

        movimientos.append(
            MovimientoStock.objects.create(
                empresa=pedido.empresa,
                producto=linea.producto,
                cantidad=pendiente,
                origen=origen,
                destino=destino,
                costo_unitario=linea.producto.costo_promedio,
                fecha=fecha,
                documento_origen=pedido.numero,
                documento_tipo="ventas.Pedido",
                documento_id=str(pedido.pk),
                usuario=usuario,
            )
        )
        from apps.gestion.servicios import contabilizar_stock
        contabilizar_stock(movimientos[-1], usuario)
        linea.cantidad_entregada = objetivo
        linea.save(update_fields=["cantidad_entregada", "actualizado_en"])
        consumir = pendiente
        for reserva in linea.reservas.filter(estado=ReservaStock.Estado.ACTIVA).order_by('pk'):
            if consumir <= 0:
                break
            if reserva.cantidad <= consumir:
                consumir -= reserva.cantidad
                reserva.estado = ReservaStock.Estado.CONSUMIDA
                reserva.save(update_fields=['estado', 'actualizado_en'])
            else:
                reserva.cantidad -= consumir
                reserva.save(update_fields=['cantidad', 'actualizado_en'])
                ReservaStock.objects.create(empresa=linea.empresa, producto=linea.producto,
                    ubicacion=reserva.ubicacion, cantidad=consumir, linea_pedido=linea,
                    documento_origen=pedido.numero, estado=ReservaStock.Estado.CONSUMIDA)
                consumir = Decimal('0')

    if all(l.pendiente_entrega <= 0 for l in pedido.lineas.all()):
        pedido.transicionar("entregado", usuario)
    return movimientos


@operacion_serializada
def ingresar_recepcion(recepcion, usuario=None):
    """Registra la entrada de mercadería y recalcula el costo promedio.

    El costo promedio ponderado se mueve solo con lo que realmente entró; así el
    margen que reporta el ERP no depende de lo que alguien digite a mano.
    """
    from apps.inventario.models import stock_en_mano

    if MovimientoStock.objects.filter(empresa=recepcion.empresa,
            documento_tipo="compras.Recepcion", documento_id=str(recepcion.pk)).exists():
        raise ValidationError("Esta recepción ya fue ingresada al almacén.")

    origen = ubicacion_virtual(recepcion.empresa, Ubicacion.Tipo.PROVEEDOR)
    destino = ubicacion_de_stock(recepcion.almacen)
    movimientos = []

    for linea in recepcion.lineas.select_related("producto"):
        producto = linea.producto
        tasa = factor_cambio(recepcion.orden.moneda, recepcion.empresa,
                             recepcion.orden.tipo_cambio) if recepcion.orden_id else Decimal("1")
        costo_base = linea.costo_unitario * tasa
        if not producto.controla_stock:
            continue

        stock_previo = stock_en_mano(producto, empresa=recepcion.empresa)
        movimientos.append(
            MovimientoStock.objects.create(
                empresa=recepcion.empresa,
                producto=producto,
                cantidad=linea.cantidad,
                origen=origen,
                destino=destino,
                lote=linea.lote,
                costo_unitario=costo_base,
                fecha=timezone.now(),
                documento_origen=recepcion.numero,
                documento_tipo="compras.Recepcion",
                documento_id=str(recepcion.pk),
                usuario=usuario,
            )
        )

        if linea.costo_unitario:
            valor_previo = stock_previo * producto.costo_promedio
            valor_nuevo = linea.cantidad * costo_base
            cantidad_total = stock_previo + linea.cantidad
            if cantidad_total > 0:
                producto.costo_promedio = (valor_previo + valor_nuevo) / cantidad_total
                producto.save(update_fields=["costo_promedio", "actualizado_en"])

        if linea.linea_orden:
            linea.linea_orden.cantidad_recibida += linea.cantidad
            linea.linea_orden.save(update_fields=["cantidad_recibida", "actualizado_en"])

    _actualizar_estado_orden(recepcion.orden)
    return movimientos


def _actualizar_estado_orden(orden):
    """Marca la orden como recibida total o parcialmente, según lo que llegó."""
    from apps.compras.models import EstadoOrdenCompra

    if orden is None:
        return
    lineas = list(orden.lineas.all())
    if not lineas:
        return
    if all(l.pendiente_recepcion <= 0 for l in lineas):
        orden.estado = EstadoOrdenCompra.RECIBIDA
    elif any(l.cantidad_recibida > 0 for l in lineas):
        orden.estado = EstadoOrdenCompra.RECIBIDA_PARCIAL
    else:
        return
    orden.save(update_fields=["estado", "actualizado_en"])


@operacion_serializada
def emitir_guia(pedido, punto_llegada="", fecha_traslado=None, usuario=None, **datos):
    """Emite la guía de remisión del despacho y la encola hacia el OSE.

    Se llama después de `despachar_pedido`: la guía transporta lo que ya salió.
    """
    from datetime import date

    from apps.core.models import Serie
    from apps.integraciones.conectores import obtener_conector
    from apps.integraciones.models import ServicioExterno
    from apps.inventario.models import GuiaRemision

    if not pedido.lineas.filter(cantidad_entregada__gt=0).exists():
        raise ValidationError("No hay nada despachado en este pedido para trasladar.")
    existente = pedido.guias.exclude(estado=GuiaRemision.Estado.RECHAZADO).first()
    if existente:
        raise ValidationError(f"El pedido ya tiene la guía {existente}.")

    serie = Serie.objects.filter(empresa=pedido.empresa, tipo_documento="09", activa=True).first()
    if serie is None:
        raise ValidationError("No hay serie activa para guías de remisión (tipo 09).")

    if not punto_llegada:
        if pedido.direccion_entrega:
            punto_llegada = pedido.direccion_entrega.direccion
        else:
            punto_llegada = pedido.tercero.direccion_fiscal
    if not punto_llegada:
        raise ValidationError("Falta la dirección de llegada: el cliente no tiene dirección.")

    hoy = date.today()
    guia = GuiaRemision.objects.create(
        empresa=pedido.empresa,
        serie=serie.serie,
        correlativo=serie.siguiente_numero(),
        pedido=pedido,
        destinatario=pedido.tercero,
        fecha_emision=hoy,
        fecha_traslado=fecha_traslado or hoy,
        punto_partida=pedido.almacen.direccion or pedido.empresa.direccion_fiscal,
        punto_llegada=punto_llegada,
        estado=GuiaRemision.Estado.POR_ENVIAR,
        **datos,
    )

    servicio = ServicioExterno.objects.filter(
        empresa=pedido.empresa, codigo=ServicioExterno.Codigo.OSE, activo=True
    ).first()
    if servicio is None:
        return guia, None
    conector = obtener_conector(servicio)
    trabajo = conector.encolar(
        "emitir_guia",
        carga={
            "numero_completo": guia.numero_completo,
            "tipo": "09",
            "tipo_nombre": "La guía de remisión",
            "fecha_traslado": guia.fecha_traslado.isoformat(),
            "destinatario": {
                "tipo_documento": guia.destinatario.tipo_documento,
                "numero_documento": guia.destinatario.numero_documento,
                "razon_social": guia.destinatario.razon_social,
            },
            "partida": guia.punto_partida,
            "llegada": guia.punto_llegada,
            "bultos": guia.bultos,
            "peso_kg": str(guia.peso_kg),
            "bienes": [
                {"descripcion": m.producto.nombre, "cantidad": str(m.cantidad),
                 "unidad": m.producto.unidad_medida.codigo}
                for m in guia.movimientos
            ],
        },
        llave=f"guia:{pedido.empresa_id}:{guia.numero_completo}",
        objeto_id=guia.pk,
        entidad="inventario.GuiaRemision",
    )
    return guia, trabajo
