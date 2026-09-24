"""Cobros, conciliación y cierre del círculo.

Un cobro confirmado genera su asiento y, cuando el comprobante queda saldado,
empuja el pedido a «pagado» y luego a «cerrado». Nadie tiene que acordarse de
hacerlo a mano.
"""
import logging
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from apps.core.transacciones import operacion_serializada
from apps.core.monedas import decimal_finito, factor_cambio, redondear

from apps.tesoreria.models import LineaExtractoBancario, MetodoPago, Movimiento
from apps.ventas.models import EstadoPedido

logger = logging.getLogger(__name__)


class CobroExcedeSaldo(ValidationError):
    """Se intentó cobrar más de lo que el comprobante debe."""


@operacion_serializada
def registrar_cobro(
    comprobante,
    monto,
    metodo=MetodoPago.TRANSFERENCIA,
    cuenta=None,
    referencia_externa="",
    fecha=None,
    comision=Decimal("0"),
    confirmar=True,
    tipo_cambio=None,
    usuario=None,
):
    """Registra un cobro contra un comprobante. Admite pagos parciales.

    Si `referencia_externa` ya fue registrada para esta empresa, devuelve el
    movimiento existente: es lo que evita duplicar cuando el webhook de la
    pasarela llega dos veces.
    """
    from apps.facturacion.models import EstadoComprobante, TipoComprobante

    monto = decimal_finito(monto)
    if monto != redondear(monto):
        raise ValidationError("El cobro admite como máximo dos decimales.")
    if monto <= 0:
        raise ValidationError("El monto del cobro debe ser mayor que cero.")
    if comprobante.tipo not in (TipoComprobante.FACTURA, TipoComprobante.BOLETA):
        raise ValidationError("El cobro debe aplicarse a una factura o boleta.")
    if comprobante.estado not in (EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO):
        raise ValidationError("Solo se puede cobrar un comprobante aceptado.")
    if cuenta and (cuenta.empresa_id != comprobante.empresa_id or
                   cuenta.moneda != comprobante.moneda or not cuenta.activa):
        raise ValidationError("La cuenta debe estar activa y tener la misma empresa y moneda del cobro.")
    referencia_externa = referencia_externa.strip()

    if referencia_externa:
        previo = Movimiento.objects.filter(
            empresa=comprobante.empresa,
            referencia_externa=referencia_externa,
            sentido=Movimiento.Sentido.COBRO,
        ).first()
        if previo:
            if previo.comprobante_id != comprobante.pk or previo.monto != monto:
                raise ValidationError("La referencia ya pertenece a otro cobro o importe.")
            if previo.estado == Movimiento.Estado.ANULADO:
                raise ValidationError("La referencia corresponde a un cobro anulado.")
            if confirmar and previo.estado == Movimiento.Estado.PENDIENTE:
                confirmar_cobro(previo, usuario)
            return previo

    if comprobante.moneda != comprobante.empresa.moneda_base and tipo_cambio is None:
        raise ValidationError("Indica el tipo de cambio del día del cobro.")
    tasa = factor_cambio(comprobante.moneda, comprobante.empresa, tipo_cambio if tipo_cambio is not None else 1)
    comision = redondear(comision)
    if comision < 0 or comision > monto:
        raise ValidationError("La comisión debe estar entre cero y el monto cobrado.")

    if monto > comprobante.saldo + Decimal("0.005"):
        raise CobroExcedeSaldo(
            f"El comprobante {comprobante.numero_completo} debe {comprobante.saldo} "
            f"y se intenta cobrar {monto}."
        )

    movimiento = Movimiento.objects.create(
        empresa=comprobante.empresa,
        sentido=Movimiento.Sentido.COBRO,
        tercero=comprobante.tercero,
        comprobante=comprobante,
        metodo=metodo,
        cuenta=cuenta,
        monto=monto,
        comision=comision,
        moneda=comprobante.moneda,
        tipo_cambio=tasa,
        fecha=fecha or date.today(),
        estado=Movimiento.Estado.PENDIENTE,
        referencia_externa=referencia_externa,
    )
    if confirmar:
        confirmar_cobro(movimiento, usuario)
    return movimiento


@operacion_serializada
def confirmar_cobro(movimiento, usuario=None):
    """Da por bueno el cobro: imputa al comprobante, contabiliza y avanza el pedido."""
    from apps.contabilidad.servicios import asiento_de_cobro

    if movimiento.estado in (Movimiento.Estado.CONFIRMADO, Movimiento.Estado.CONCILIADO):
        return movimiento
    if movimiento.estado == Movimiento.Estado.ANULADO:
        raise ValidationError("Un cobro anulado no se puede confirmar.")

    comprobante = movimiento.comprobante
    if comprobante:
        comprobante.refresh_from_db()
        if movimiento.monto > comprobante.saldo:
            raise CobroExcedeSaldo("El saldo cambió: el cobro pendiente supera la deuda actual.")

    movimiento.estado = Movimiento.Estado.CONFIRMADO
    movimiento.save(update_fields=["estado", "actualizado_en"])

    if comprobante:
        comprobante.total_cobrado += movimiento.monto
        comprobante.save(update_fields=["total_cobrado", "actualizado_en"])

    asiento_de_cobro(movimiento)

    if comprobante and comprobante.esta_pagado:
        _cerrar_pedido(comprobante, usuario)
        _avisar_conversion(comprobante)
    return movimiento


def _avisar_conversion(comprobante):
    """La venta está cobrada: se devuelve a la plataforma de anuncios.

    Se hace al cobrar, no al facturar, para que el valor que viaja sea plata
    realmente entrada. Una falla aquí no puede tumbar el cobro.
    """
    from apps.crm.servicios import enviar_conversion

    pedido = comprobante.pedido
    if pedido is None:
        return
    for lead in pedido.leads.all():
        try:
            enviar_conversion(lead, comprobante.total, comprobante.moneda)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo encolar la conversión del lead %s", lead.pk)


def _cerrar_pedido(comprobante, usuario=None):
    """Pagado y sin pendientes de entrega: el pedido se cierra solo."""
    pedido = comprobante.pedido
    if pedido is None or pedido.estado != EstadoPedido.FACTURADO:
        return
    pedido.transicionar(EstadoPedido.PAGADO, usuario)
    if all(
        linea.pendiente_entrega <= 0
        or not linea.producto.controla_stock
        for linea in pedido.lineas.select_related("producto")
    ):
        pedido.transicionar(EstadoPedido.CERRADO, usuario)


@operacion_serializada
def anular_cobro(movimiento, motivo=""):
    """Revierte un cobro confirmado, devolviendo el saldo al comprobante."""
    if movimiento.estado == Movimiento.Estado.ANULADO:
        return movimiento
    if movimiento.asientos.exists():
        raise ValidationError(
            "Este cobro ya tiene asiento contable. Regístralo con un extorno, "
            "no anulando el movimiento."
        )
    if movimiento.comprobante and movimiento.estado in (
        Movimiento.Estado.CONFIRMADO, Movimiento.Estado.CONCILIADO
    ):
        movimiento.comprobante.total_cobrado -= movimiento.monto
        movimiento.comprobante.save(update_fields=["total_cobrado", "actualizado_en"])
    movimiento.estado = Movimiento.Estado.ANULADO
    movimiento.notas = (f"{movimiento.notas} Anulado: {motivo}").strip()[:255]
    movimiento.save(update_fields=["estado", "notas", "actualizado_en"])
    return movimiento


@operacion_serializada
def extornar_cobro(movimiento, motivo, usuario=None, fecha=None):
    """Revierte un cobro contabilizado mediante documentos inversos, sin borrarlo."""
    from apps.contabilidad.servicios import asiento_de_extorno
    from apps.core.models import RegistroAuditoria

    if movimiento.sentido != Movimiento.Sentido.COBRO:
        raise ValidationError("Solo se puede extornar un cobro.")
    if movimiento.estado not in (Movimiento.Estado.CONFIRMADO, Movimiento.Estado.CONCILIADO):
        raise ValidationError("Solo se extorna un cobro confirmado o conciliado.")
    if not motivo or len(motivo.strip()) < 5:
        raise ValidationError("Indica un motivo de al menos cinco caracteres.")
    if hasattr(movimiento, "extorno"):
        return movimiento.extorno
    if not movimiento.asientos.exists():
        raise ValidationError("El cobro no está contabilizado; debe anularse, no extornarse.")

    referencia = f"EXT-{movimiento.pk}-{movimiento.referencia_externa}"[:120]
    extorno = Movimiento.objects.create(
        empresa=movimiento.empresa, sentido=Movimiento.Sentido.PAGO,
        tercero=movimiento.tercero, comprobante=movimiento.comprobante,
        metodo=movimiento.metodo, cuenta=movimiento.cuenta, monto=movimiento.monto,
        comision=movimiento.comision, moneda=movimiento.moneda,
        tipo_cambio=movimiento.tipo_cambio, fecha=fecha or date.today(),
        estado=Movimiento.Estado.CONFIRMADO, referencia_externa=referencia,
        notas=motivo.strip()[:255], extorna_a=movimiento,
    )
    asiento_de_extorno(extorno)
    comprobante = movimiento.comprobante
    if comprobante:
        comprobante.refresh_from_db()
        comprobante.total_cobrado = max(Decimal("0"), comprobante.total_cobrado - movimiento.monto)
        comprobante.save(update_fields=["total_cobrado", "actualizado_en"])
        pedido = comprobante.pedido
        if pedido and pedido.estado in (EstadoPedido.PAGADO, EstadoPedido.CERRADO):
            anterior = pedido.estado
            pedido.estado = EstadoPedido.FACTURADO
            pedido.save(update_fields=["estado", "actualizado_en"])
            RegistroAuditoria.objects.create(
                empresa=pedido.empresa, usuario=usuario, modelo="ventas.Pedido",
                objeto_id=str(pedido.pk), accion=RegistroAuditoria.Accion.TRANSICION,
                valores_antes={"estado": anterior},
                valores_despues={"estado": EstadoPedido.FACTURADO, "motivo": "extorno de cobro"},
            )
    return extorno


@operacion_serializada
def conciliar_extracto(cuenta, tolerancia_dias=3):
    """Empareja líneas del extracto con cobros que aún no se conciliaron.

    Exige cuenta, moneda, importe, sentido y fecha compatibles. Si existe
    referencia también debe coincidir; cualquier ambigüedad queda pendiente.
    """
    from datetime import timedelta

    emparejadas = 0
    pendientes = LineaExtractoBancario.objects.filter(cuenta=cuenta, conciliada=False)

    for linea in pendientes:
        if not linea.monto:
            continue
        candidatos = Movimiento.objects.filter(
            empresa=linea.empresa,
            cuenta=cuenta,
            moneda=cuenta.moneda,
            estado=Movimiento.Estado.CONFIRMADO,
            lineas_extracto__isnull=True,
            monto=abs(linea.monto),
            comision=0,
            sentido=Movimiento.Sentido.COBRO if linea.monto > 0 else Movimiento.Sentido.PAGO,
            fecha__gte=linea.fecha - timedelta(days=tolerancia_dias),
            fecha__lte=linea.fecha + timedelta(days=tolerancia_dias),
        )
        if linea.referencia:
            candidatos = candidatos.filter(referencia_externa=linea.referencia)
        opciones = list(candidatos[:2])
        if len(opciones) != 1:
            continue
        movimiento = opciones[0]
        # Una referencia repetida en el extracto también requiere revisión.
        similares = LineaExtractoBancario.objects.filter(cuenta=cuenta, conciliada=False,
            monto=linea.monto, fecha__gte=movimiento.fecha - timedelta(days=tolerancia_dias),
            fecha__lte=movimiento.fecha + timedelta(days=tolerancia_dias))
        if linea.referencia:
            similares = similares.filter(referencia=linea.referencia)
        if similares.count() != 1:
            continue

        linea.movimiento = movimiento
        linea.conciliada = True
        linea.save(update_fields=["movimiento", "conciliada", "actualizado_en"])
        movimiento.estado = Movimiento.Estado.CONCILIADO
        movimiento.save(update_fields=["estado", "actualizado_en"])
        emparejadas += 1

    return emparejadas
