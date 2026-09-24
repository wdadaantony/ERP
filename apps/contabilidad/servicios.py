"""Generación automática de asientos.

«La contabilidad nace del documento, no se digita aparte». Estas funciones son
las únicas que crean asientos: leen las `ReglaContable` de la empresa y arman las
líneas de debe y haber. Si falta una regla, fallan de forma explícita en vez de
inventarse una cuenta.
"""
from decimal import Decimal
from calendar import monthrange
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from apps.core.transacciones import operacion_serializada
from apps.core.monedas import a_moneda_base, redondear, decimal_finito

from apps.contabilidad.models import Asiento, LineaAsiento, PeriodoContable, ReglaContable


class FaltaReglaContable(ValidationError):
    """No hay regla configurada para ese tipo de documento."""


def _regla(empresa, tipo_documento):
    try:
        return ReglaContable.objects.select_related("cuenta_debe", "cuenta_haber").get(
            empresa=empresa, tipo_documento=tipo_documento
        )
    except ReglaContable.DoesNotExist:
        raise FaltaReglaContable(
            f"No hay regla contable para «{tipo_documento}» en {empresa}. "
            "Configúrala en Contabilidad › Reglas contables."
        )


def _periodo(empresa, fecha):
    periodo, _ = PeriodoContable.objects.get_or_create(
        empresa=empresa, anio=fecha.year, mes=fecha.month
    )
    return periodo


def _siguiente_numero_asiento(empresa, fecha):
    from apps.core.models import Serie

    prefijo = f"AS-{fecha.year}{fecha.month:02d}"
    ultimo = (
        Asiento.objects.filter(empresa=empresa, numero__startswith=prefijo)
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
    )
    consecutivo = int(ultimo.rsplit("-", 1)[1]) if ultimo else 0
    serie, _ = Serie.objects.get_or_create(empresa=empresa, tipo_documento="AS",
        serie=f"{fecha.year}{fecha.month:02d}", defaults={"correlativo_actual": consecutivo})
    if serie.correlativo_actual < consecutivo:
        serie.correlativo_actual = consecutivo
        serie.save(update_fields=["correlativo_actual"])
    return f"{prefijo}-{serie.siguiente_numero():05d}"


def _crear_asiento(empresa, fecha, glosa, lineas, **enlaces):
    """Crea el asiento y valida que cuadre antes de darlo por bueno."""
    lineas = list(lineas)
    for cuenta, tercero, _, debe, haber in lineas:
        if (cuenta.empresa_id != empresa.pk or not cuenta.activa or not cuenta.acepta_movimiento
                or (tercero and tercero.empresa_id != empresa.pk)):
            raise ValidationError('El asiento contiene cuentas o terceros no autorizados para esta empresa.')
        if decimal_finito(debe) < 0 or decimal_finito(haber) < 0 or (debe and haber):
            raise ValidationError('Cada línea debe tener un importe no negativo en debe o haber.')
    periodo = _periodo(empresa, fecha)
    if periodo.cerrado:
        raise ValidationError(f"El periodo {periodo} está cerrado: no admite asientos nuevos.")

    asiento = Asiento.objects.create(
        empresa=empresa,
        numero=_siguiente_numero_asiento(empresa, fecha),
        fecha=fecha,
        periodo=periodo,
        glosa=glosa,
        generado_automaticamente=True,
        **enlaces,
    )
    for cuenta, tercero, glosa_linea, debe, haber in lineas:
        if not debe and not haber:
            continue
        LineaAsiento.objects.create(
            empresa=empresa,
            asiento=asiento,
            cuenta=cuenta,
            tercero=tercero,
            glosa=glosa_linea,
            debe=redondear(debe),
            haber=redondear(haber),
        )
    asiento.validar_cuadre()
    return asiento


@operacion_serializada
def asiento_de_venta(comprobante):
    """Reconoce la venta: cobrar al cliente, contra ingreso e IGV por pagar.

    Es idempotente: si el comprobante ya tiene asiento, devuelve el existente.
    """
    existente = comprobante.asientos.first()
    if existente:
        return existente

    empresa = comprobante.empresa
    regla_venta = _regla(empresa, "venta_factura")
    regla_igv = _regla(empresa, "venta_igv")
    total_base = a_moneda_base(comprobante.total, comprobante)
    subtotal_base = a_moneda_base(comprobante.subtotal, comprobante)
    cero = Decimal("0.00")

    lineas = [
        (
            regla_venta.cuenta_debe,
            comprobante.tercero,
            f"Venta {comprobante.numero_completo}",
            total_base,
            cero,
        ),
        (regla_venta.cuenta_haber, None, "Ingreso por ventas", cero, subtotal_base),
        (regla_igv.cuenta_haber, None, "IGV por pagar", cero, total_base - subtotal_base),
    ]
    return _crear_asiento(
        empresa,
        comprobante.fecha_emision,
        f"Venta según {comprobante.numero_completo}",
        lineas,
        comprobante=comprobante,
    )


@operacion_serializada
def asiento_de_nota_credito(nota):
    """Extorna la venta: se revierte el ingreso y el IGV, y baja la cuenta por cobrar.

    Es el asiento de la venta al revés, por el monto de la nota.
    """
    existente = nota.asientos.first()
    if existente:
        return existente

    empresa = nota.empresa
    regla_venta = _regla(empresa, "venta_factura")
    regla_igv = _regla(empresa, "venta_igv")
    total_base = a_moneda_base(nota.total, nota)
    subtotal_base = a_moneda_base(nota.subtotal, nota)
    cero = Decimal("0.00")
    afectado = nota.comprobante_afectado

    lineas = [
        (regla_venta.cuenta_haber, None, "Extorno de venta", subtotal_base, cero),
        (regla_igv.cuenta_haber, None, "Extorno de IGV", total_base - subtotal_base, cero),
        (
            regla_venta.cuenta_debe,
            nota.tercero,
            f"Nota de crédito {nota.numero_completo}",
            cero,
            total_base,
        ),
    ]
    referencia = afectado.numero_completo if afectado else "sin referencia"
    return _crear_asiento(
        empresa,
        nota.fecha_emision,
        f"Nota de crédito {nota.numero_completo} sobre {referencia}",
        lineas,
        comprobante=nota,
    )


@operacion_serializada
def asiento_de_cobro(movimiento):
    """Reconoce el cobro: entra a banco, se cancela la cuenta por cobrar."""
    existente = movimiento.asientos.first()
    if existente:
        return existente

    empresa = movimiento.empresa
    regla = _regla(empresa, "cobro")
    recibido_base = a_moneda_base(movimiento.monto, movimiento)
    historico_base = recibido_base
    if movimiento.comprobante:
        # Diferencias acumuladas para que pagos parciales cierren al centavo.
        c = movimiento.comprobante
        historico_base = (a_moneda_base(c.total_cobrado, c)
                          - a_moneda_base(c.total_cobrado - movimiento.monto, c))
    comision_base = a_moneda_base(movimiento.comision, movimiento)
    cero = Decimal("0.00")
    referencia = movimiento.comprobante.numero_completo if movimiento.comprobante else "a cuenta"

    lineas = [
        (regla.cuenta_debe, None, f"Cobro {referencia}", recibido_base - comision_base, cero),
        (
            regla.cuenta_haber,
            movimiento.tercero,
            f"Cancelación {referencia}",
            cero,
            historico_base,
        ),
    ]
    diferencia = recibido_base - historico_base
    if diferencia > 0:
        cambio = _regla(empresa, "ganancia_cambio")
        lineas.append((cambio.cuenta_haber, None, "Ganancia por diferencia de cambio", cero, diferencia))
    elif diferencia < 0:
        cambio = _regla(empresa, "perdida_cambio")
        lineas.append((cambio.cuenta_debe, None, "Pérdida por diferencia de cambio", -diferencia, cero))
    if comision_base:
        comision = _regla(empresa, "comision_cobro")
        lineas.append((comision.cuenta_debe, None, "Comisión de cobro", comision_base, cero))
    return _crear_asiento(
        empresa,
        movimiento.fecha,
        f"Cobro de {movimiento.tercero}",
        lineas,
        movimiento_tesoreria=movimiento,
    )


@operacion_serializada
def asiento_de_extorno(movimiento):
    """Invierte exactamente el asiento del cobro, conservando ambas evidencias."""
    if movimiento.asientos.exists():
        return movimiento.asientos.first()
    if not movimiento.extorna_a_id:
        raise ValidationError("El movimiento no indica qué cobro extorna.")
    original = movimiento.extorna_a
    asiento_original = original.asientos.prefetch_related("lineas__cuenta", "lineas__tercero").first()
    if asiento_original is None:
        raise ValidationError("El cobro original no tiene asiento para extornar.")
    if hasattr(asiento_original, "extorno"):
        raise ValidationError("El asiento del cobro ya fue extornado.")
    lineas = [
        (l.cuenta, l.tercero, f"Extorno: {l.glosa}", l.haber, l.debe)
        for l in asiento_original.lineas.all()
    ]
    return _crear_asiento(
        movimiento.empresa, movimiento.fecha,
        f"Extorno de {asiento_original.numero}: {movimiento.notas}", lineas,
        movimiento_tesoreria=movimiento, extorna_a=asiento_original,
    )


def _saldo_documento_al_corte(comprobante, cierre):
    from apps.facturacion.models import EstadoComprobante, TipoComprobante
    from apps.tesoreria.models import Movimiento

    saldo = comprobante.total
    for nota in comprobante.notas.filter(
        fecha_emision__lte=cierre,
        estado__in=(EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO),
    ):
        saldo += nota.total if nota.tipo == TipoComprobante.NOTA_DEBITO else -nota.total
    for movimiento in comprobante.cobros.filter(
        fecha__lte=cierre, estado__in=(Movimiento.Estado.CONFIRMADO, Movimiento.Estado.CONCILIADO)
    ):
        saldo += movimiento.monto if movimiento.sentido == Movimiento.Sentido.PAGO else -movimiento.monto
    return redondear(saldo)


@operacion_serializada
def cerrar_periodo_con_revaluacion(periodo, tasas, usuario=None):
    """Revalúa cuentas por cobrar extranjeras, revierte al mes siguiente y cierra."""
    from apps.contabilidad.models import TipoCambioCierre
    from apps.facturacion.models import Comprobante, EstadoComprobante, TipoComprobante

    if periodo.cerrado:
        raise ValidationError(f"El periodo {periodo} ya está cerrado.")
    cierre = date(periodo.anio, periodo.mes, monthrange(periodo.anio, periodo.mes)[1])
    clave = f"revaluacion-cxc:{periodo.anio:04d}{periodo.mes:02d}"
    if Asiento.objects.filter(empresa=periodo.empresa, clave_automatica=clave).exists():
        raise ValidationError("Este periodo ya tiene una revaluación registrada.")
    documentos = Comprobante.objects.filter(
        empresa=periodo.empresa, tipo__in=(TipoComprobante.FACTURA, TipoComprobante.BOLETA),
        estado__in=(EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO),
        fecha_emision__lte=cierre,
    ).exclude(moneda=periodo.empresa.moneda_base).select_related("tercero", "empresa").prefetch_related("notas", "cobros")
    saldos = [(c, _saldo_documento_al_corte(c, cierre)) for c in documentos]
    saldos = [(c, saldo) for c, saldo in saldos if abs(saldo) >= Decimal("0.01")]
    faltantes = sorted({c.moneda for c, _ in saldos if c.moneda not in tasas})
    if faltantes:
        raise ValidationError("Falta tipo de cambio de cierre para: " + ", ".join(faltantes))

    cuenta_cliente = _regla(periodo.empresa, "venta_factura").cuenta_debe
    ganancia = _regla(periodo.empresa, "ganancia_cambio").cuenta_haber
    perdida = _regla(periodo.empresa, "perdida_cambio").cuenta_debe
    lineas = []
    for moneda in sorted({c.moneda for c, _ in saldos}):
        tasa = decimal_finito(tasas[moneda])
        if tasa <= 0:
            raise ValidationError(f"El tipo de cambio de {moneda} debe ser mayor que cero.")
        TipoCambioCierre.objects.create(empresa=periodo.empresa, periodo=periodo,
                                         moneda=moneda, tasa=tasa)
    for comprobante, saldo in saldos:
        diferencia = redondear(saldo * (decimal_finito(tasas[comprobante.moneda]) - comprobante.tipo_cambio))
        if diferencia > 0:
            lineas.extend(((cuenta_cliente, comprobante.tercero,
                f"Revaluación {comprobante.numero_completo}", diferencia, 0),
                (ganancia, None, "Ganancia no realizada por diferencia de cambio", 0, diferencia)))
        elif diferencia < 0:
            lineas.extend(((perdida, None, "Pérdida no realizada por diferencia de cambio", -diferencia, 0),
                (cuenta_cliente, comprobante.tercero,
                 f"Revaluación {comprobante.numero_completo}", 0, -diferencia)))
    asiento = None
    if lineas:
        asiento = _crear_asiento(periodo.empresa, cierre, f"Revaluación de CxC al cierre {periodo}",
                                  lineas, clave_automatica=clave, creado_por=usuario)
        fecha_reversion = cierre + timedelta(days=1)
        inversas = [(cuenta, tercero, f"Reversión: {glosa}", haber, debe)
                    for cuenta, tercero, glosa, debe, haber in lineas]
        _crear_asiento(periodo.empresa, fecha_reversion, f"Reversión de revaluación {periodo}",
                       inversas, clave_automatica=f"reversion-{clave}", creado_por=usuario,
                       extorna_a=asiento)
    periodo.cerrado = True
    periodo.save(update_fields=["cerrado", "actualizado_en"])
    return asiento
