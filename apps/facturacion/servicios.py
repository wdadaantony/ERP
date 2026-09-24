"""Emisión de comprobantes electrónicos.

El punto clave del diseño: emitir **no** llama al OSE. Arma el comprobante, toma
el correlativo y deja un trabajo en la cola. El vendedor guarda al instante y el
comprobante se resuelve en segundo plano.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from apps.core.transacciones import operacion_serializada
from apps.core.monedas import decimal_finito, factor_cambio, repartir_importe

from apps.core.models import Serie, TipoDocumentoIdentidad
from apps.facturacion.models import (
    Comprobante,
    EstadoComprobante,
    LineaComprobante,
    TipoComprobante,
)
from apps.integraciones.conectores import obtener_conector
from apps.integraciones.models import ServicioExterno
from apps.ventas.models import EstadoPedido

CENTIMO = Decimal("0.01")


def _redondear(valor):
    return Decimal(valor).quantize(CENTIMO, rounding=ROUND_HALF_UP)


def tipo_de_comprobante(tercero):
    """Con RUC se emite factura; sin RUC, boleta."""
    if tercero.tipo_documento == TipoDocumentoIdentidad.RUC:
        return TipoComprobante.FACTURA
    return TipoComprobante.BOLETA


def _serie_para(empresa, tipo):
    serie = Serie.objects.filter(
        empresa=empresa, tipo_documento=tipo, activa=True
    ).order_by("id").first()
    if serie is None:
        raise ValidationError(
            f"No hay serie activa para el tipo de documento «{tipo}» en {empresa}. "
            "Créala en Core › Series."
        )
    return serie


@operacion_serializada
def emitir_comprobante(pedido, usuario=None, tipo=None, fecha=None):
    """Genera el comprobante del pedido y lo deja encolado hacia el OSE.

    El pedido debe estar entregado: se factura lo que ya salió de almacén.
    Devuelve `(comprobante, trabajo_encolado)`.
    """
    if pedido.estado != EstadoPedido.ENTREGADO:
        raise ValidationError(
            f"Solo se factura un pedido entregado; este está en "
            f"«{pedido.get_estado_display()}»."
        )
    existente = pedido.comprobantes.exclude(estado=EstadoComprobante.ANULADO).first()
    if existente:
        raise ValidationError(f"El pedido {pedido.numero} ya tiene el comprobante {existente}.")

    empresa = pedido.empresa
    factor_cambio(pedido.moneda, empresa, pedido.tipo_cambio)
    tipo = tipo or tipo_de_comprobante(pedido.tercero)
    serie = _serie_para(empresa, tipo)
    fecha = fecha or date.today()

    comprobante = Comprobante.objects.create(
        empresa=empresa,
        tipo=tipo,
        serie=serie.serie,
        correlativo=serie.siguiente_numero(),
        pedido=pedido,
        tercero=pedido.tercero,
        fecha_emision=fecha,
        fecha_vencimiento=_vencimiento(pedido, fecha),
        moneda=pedido.moneda,
        tipo_cambio=pedido.tipo_cambio,
        estado=EstadoComprobante.BORRADOR,
    )

    subtotal = igv_total = Decimal("0.00")
    for linea in pedido.lineas.select_related("producto", "producto__unidad_medida"):
        pendiente = linea.pendiente_facturacion
        if pendiente <= 0:
            continue
        proporcion = pendiente / linea.cantidad
        base = _redondear(linea.subtotal * proporcion)
        impuesto = _redondear(linea.impuesto * proporcion)

        LineaComprobante.objects.create(
            empresa=empresa,
            comprobante=comprobante,
            producto=linea.producto,
            linea_pedido=linea,
            descripcion=linea.descripcion or linea.producto.nombre,
            unidad_medida=linea.producto.unidad_medida.codigo,
            cantidad=pendiente,
            precio_unitario=linea.precio_unitario,
            afectacion_igv=linea.producto.afectacion_igv,
            igv=impuesto,
            total=base + impuesto,
        )
        subtotal += base
        igv_total += impuesto

        linea.cantidad_facturada = linea.cantidad
        linea.save(update_fields=["cantidad_facturada", "actualizado_en"])

    if subtotal <= 0:
        raise ValidationError("No queda nada por facturar en este pedido.")

    comprobante.subtotal = subtotal
    comprobante.igv = igv_total
    comprobante.total = subtotal + igv_total
    comprobante.estado = EstadoComprobante.POR_ENVIAR
    comprobante.save(update_fields=["subtotal", "igv", "total", "estado", "actualizado_en"])

    pedido.transicionar(EstadoPedido.FACTURADO, usuario)
    trabajo = encolar_envio(comprobante)
    return comprobante, trabajo


def _vencimiento(pedido, fecha):
    condicion = pedido.tercero.condicion_pago
    if condicion and condicion.dias:
        return fecha + timedelta(days=condicion.dias)
    return fecha


def encolar_envio(comprobante):
    """Deja el envío en la cola con una llave que impide duplicarlo."""
    servicio = ServicioExterno.objects.filter(
        empresa=comprobante.empresa, codigo=ServicioExterno.Codigo.OSE, activo=True
    ).first()
    if servicio is None:
        raise ValidationError(
            f"No hay un servicio de OSE configurado para {comprobante.empresa}."
        )

    conector = obtener_conector(servicio)
    return conector.encolar(
        "emitir_comprobante",
        carga=construir_carga(comprobante),
        llave=f"cpe:{comprobante.empresa_id}:{comprobante.numero_completo}",
        objeto_id=comprobante.pk,
        entidad="facturacion.Comprobante",
    )


def construir_carga(comprobante):
    """Traduce el comprobante al diccionario que espera el conector.

    Deliberadamente neutro: el conector de cada proveedor lo adapta a su formato.
    """
    empresa = comprobante.empresa
    return {
        "numero_completo": comprobante.numero_completo,
        "tipo": comprobante.tipo,
        "tipo_nombre": comprobante.get_tipo_display(),
        "fecha_emision": comprobante.fecha_emision.isoformat(),
        "moneda": comprobante.moneda,
        "documento_afectado": (
            comprobante.comprobante_afectado.numero_completo
            if comprobante.comprobante_afectado_id else ""
        ),
        "codigo_motivo_nota": comprobante.codigo_motivo_nota,
        "motivo_nota": comprobante.motivo_nota,
        "emisor": {"ruc": empresa.ruc, "razon_social": empresa.razon_social},
        "receptor": {
            "tipo_documento": comprobante.tercero.tipo_documento,
            "numero_documento": comprobante.tercero.numero_documento,
            "razon_social": comprobante.tercero.razon_social,
            "direccion": comprobante.tercero.direccion_fiscal,
        },
        "totales": {
            "gravado": str(comprobante.subtotal),
            "igv": str(comprobante.igv),
            "total": str(comprobante.total),
        },
        "lineas": [
            {
                "descripcion": linea.descripcion,
                "unidad": linea.unidad_medida,
                "cantidad": str(linea.cantidad),
                "precio_unitario": str(linea.precio_unitario),
                "afectacion_igv": linea.afectacion_igv,
                "igv": str(linea.igv),
                "total": str(linea.total),
            }
            for linea in comprobante.lineas.all()
        ],
    }


#: Catálogo 09 de SUNAT, motivos de nota de crédito.
MOTIVOS_NOTA_CREDITO = {
    "01": "Anulación de la operación",
    "02": "Anulación por error en el RUC",
    "03": "Corrección por error en la descripción",
    "04": "Descuento global",
    "06": "Devolución total",
    "07": "Devolución por ítem",
    "13": "Ajuste de operaciones de exportación",
}
MOTIVOS_NOTA_DEBITO = {
    "01": "Intereses por mora",
    "02": "Aumento en el valor",
    "03": "Penalidades u otros conceptos",
}


@operacion_serializada
def emitir_nota_credito(comprobante, motivo="06", monto=None, usuario=None, fecha=None):
    """Emite la nota de crédito de un comprobante aceptado.

    Sin `monto` extorna todo lo que queda vivo del comprobante; con `monto` hace
    un extorno parcial (un descuento posterior, una devolución de parte).
    Como cualquier otro comprobante, se encola: no se llama al OSE en vivo.
    """
    if comprobante.tipo in (TipoComprobante.NOTA_CREDITO, TipoComprobante.NOTA_DEBITO):
        raise ValidationError("No se emite una nota de crédito sobre otra nota.")
    if comprobante.estado not in (EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO):
        raise ValidationError(
            f"Solo se acredita un comprobante aceptado; este está en "
            f"«{comprobante.get_estado_display()}»."
        )
    if motivo not in MOTIVOS_NOTA_CREDITO:
        raise ValidationError(f"Motivo «{motivo}» fuera del catálogo 09 de SUNAT.")
    if motivo == "03":
        raise ValidationError("La corrección de descripción no reduce la deuda; este flujo solo emite notas monetarias.")

    vivo = _monto_vivo(comprobante)
    monto = decimal_finito(monto) if monto is not None else vivo
    if monto != _redondear(monto):
        raise ValidationError("La nota admite como máximo dos decimales.")
    if monto <= 0:
        raise ValidationError("El monto de la nota de crédito debe ser mayor que cero.")
    if monto > vivo + Decimal("0.005"):
        raise ValidationError(
            f"El comprobante {comprobante.numero_completo} tiene {vivo} sin acreditar "
            f"y se intenta acreditar {monto}."
        )

    empresa = comprobante.empresa
    serie = _serie_para(empresa, TipoComprobante.NOTA_CREDITO)
    fecha = fecha or date.today()

    # La proporción reparte el monto entre base gravada e IGV manteniendo la tasa.
    proporcion = monto / comprobante.total
    subtotal = _redondear(comprobante.subtotal * proporcion)
    igv = _redondear(monto - subtotal)

    nota = Comprobante.objects.create(
        empresa=empresa,
        tipo=TipoComprobante.NOTA_CREDITO,
        serie=serie.serie,
        correlativo=serie.siguiente_numero(),
        pedido=comprobante.pedido,
        tercero=comprobante.tercero,
        fecha_emision=fecha,
        moneda=comprobante.moneda,
        tipo_cambio=comprobante.tipo_cambio,
        subtotal=subtotal,
        igv=igv,
        total=monto,
        estado=EstadoComprobante.POR_ENVIAR,
        comprobante_afectado=comprobante,
        motivo_nota=MOTIVOS_NOTA_CREDITO[motivo],
        codigo_motivo_nota=motivo,
    )

    lineas = list(comprobante.lineas.all())
    bases = repartir_importe(subtotal, [l.total - l.igv for l in lineas])
    impuestos = repartir_importe(igv, [l.igv for l in lineas])
    for linea, base_linea, impuesto_linea in zip(lineas, bases, impuestos):
        LineaComprobante.objects.create(
            empresa=empresa,
            comprobante=nota,
            producto=linea.producto,
            linea_pedido=linea.linea_pedido,
            descripcion=linea.descripcion,
            unidad_medida=linea.unidad_medida,
            cantidad=_redondear(linea.cantidad * proporcion),
            precio_unitario=linea.precio_unitario,
            afectacion_igv=linea.afectacion_igv,
            igv=impuesto_linea,
            total=base_linea + impuesto_linea,
        )

    trabajo = encolar_envio(nota)
    return nota, trabajo


@operacion_serializada
def emitir_nota_debito(comprobante, motivo, monto, usuario=None, fecha=None):
    """Incrementa una cuenta por cobrar mediante una nota de débito electrónica."""
    if comprobante.tipo not in (TipoComprobante.FACTURA, TipoComprobante.BOLETA):
        raise ValidationError("La nota de débito debe afectar una factura o boleta.")
    if comprobante.estado not in (EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO):
        raise ValidationError("Solo se modifica un comprobante aceptado.")
    if motivo not in MOTIVOS_NOTA_DEBITO:
        raise ValidationError("Motivo de nota de débito no válido.")
    monto = decimal_finito(monto)
    if monto <= 0 or monto != _redondear(monto):
        raise ValidationError("El importe debe ser positivo y tener máximo dos decimales.")
    empresa = comprobante.empresa
    serie = _serie_para(empresa, TipoComprobante.NOTA_DEBITO)
    fecha = fecha or date.today()
    proporcion = monto / comprobante.total
    subtotal = _redondear(comprobante.subtotal * proporcion)
    igv = monto - subtotal
    nota = Comprobante.objects.create(
        empresa=empresa, tipo=TipoComprobante.NOTA_DEBITO, serie=serie.serie,
        correlativo=serie.siguiente_numero(), pedido=comprobante.pedido,
        tercero=comprobante.tercero, fecha_emision=fecha, moneda=comprobante.moneda,
        tipo_cambio=comprobante.tipo_cambio, subtotal=subtotal, igv=igv, total=monto,
        estado=EstadoComprobante.POR_ENVIAR, comprobante_afectado=comprobante,
        motivo_nota=MOTIVOS_NOTA_DEBITO[motivo], codigo_motivo_nota=motivo,
    )
    lineas = list(comprobante.lineas.all())
    bases = repartir_importe(subtotal, [l.total - l.igv for l in lineas])
    impuestos = repartir_importe(igv, [l.igv for l in lineas])
    for linea, base, impuesto in zip(lineas, bases, impuestos):
        LineaComprobante.objects.create(
            empresa=empresa, comprobante=nota, producto=linea.producto,
            linea_pedido=linea.linea_pedido, descripcion=linea.descripcion,
            unidad_medida=linea.unidad_medida, cantidad=_redondear(linea.cantidad * proporcion),
            precio_unitario=linea.precio_unitario, afectacion_igv=linea.afectacion_igv,
            igv=impuesto, total=base + impuesto,
        )
    return nota, encolar_envio(nota)


@operacion_serializada
def emitir_correccion_descripcion(comprobante, linea_id, descripcion, usuario=None, fecha=None):
    """Emite NC motivo 03 sin efecto monetario para corregir una descripción."""
    if comprobante.tipo not in (TipoComprobante.FACTURA, TipoComprobante.BOLETA):
        raise ValidationError("La corrección debe afectar una factura o boleta.")
    if comprobante.estado not in (EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO):
        raise ValidationError("Solo se corrige un comprobante aceptado.")
    descripcion = (descripcion or "").strip()
    if len(descripcion) < 3:
        raise ValidationError("Indica la descripción corregida.")
    linea = comprobante.lineas.filter(pk=linea_id).first()
    if linea is None:
        raise ValidationError("La línea no pertenece al comprobante.")
    empresa = comprobante.empresa
    serie = _serie_para(empresa, TipoComprobante.NOTA_CREDITO)
    nota = Comprobante.objects.create(
        empresa=empresa, tipo=TipoComprobante.NOTA_CREDITO, serie=serie.serie,
        correlativo=serie.siguiente_numero(), pedido=comprobante.pedido,
        tercero=comprobante.tercero, fecha_emision=fecha or date.today(),
        moneda=comprobante.moneda, tipo_cambio=comprobante.tipo_cambio,
        subtotal=0, igv=0, total=0, estado=EstadoComprobante.POR_ENVIAR,
        comprobante_afectado=comprobante, motivo_nota=MOTIVOS_NOTA_CREDITO["03"],
        codigo_motivo_nota="03",
    )
    LineaComprobante.objects.create(
        empresa=empresa, comprobante=nota, producto=linea.producto,
        linea_pedido=linea.linea_pedido, descripcion=descripcion,
        unidad_medida=linea.unidad_medida, cantidad=linea.cantidad,
        precio_unitario=0, afectacion_igv=linea.afectacion_igv, igv=0, total=0,
    )
    return nota, encolar_envio(nota)


def _monto_vivo(comprobante):
    """Lo que queda del comprobante después de las notas de crédito ya emitidas."""
    acreditado = sum(
        (
            nota.total
            for nota in comprobante.notas.filter(tipo=TipoComprobante.NOTA_CREDITO).exclude(
                estado__in=(EstadoComprobante.RECHAZADO, EstadoComprobante.ANULADO)
            )
        ),
        Decimal("0.00"),
    )
    debitos = sum((n.total for n in comprobante.notas.filter(
        tipo=TipoComprobante.NOTA_DEBITO,
        estado__in=(EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO)
    )), Decimal("0"))
    return comprobante.total + debitos - acreditado


@operacion_serializada
def aplicar_respuesta(comprobante, datos, id_externo=""):
    """Aplica el CDR: acepta, observa o rechaza, y contabiliza si corresponde.

    El correlativo no se pierde nunca: un rechazo deja el comprobante corregible
    y reenviable, con el texto exacto del error a la vista.
    """
    estado_externo = (datos or {}).get("estado", "").lower()
    mapa = {
        "aceptado": EstadoComprobante.ACEPTADO,
        "observado": EstadoComprobante.OBSERVADO,
        "rechazado": EstadoComprobante.RECHAZADO,
        "anulado": EstadoComprobante.ANULADO,
    }
    nuevo_estado = mapa.get(estado_externo, EstadoComprobante.ENVIADO)
    if comprobante.estado in (EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO):
        if nuevo_estado not in (EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO):
            raise ValidationError("Un documento contabilizado no puede retroceder ni anularse por un aviso tardío. Requiere una corrección controlada.")
    if comprobante.estado == EstadoComprobante.ANULADO:
        raise ValidationError("Un documento anulado no puede recibir nuevas respuestas.")
    comprobante.estado = nuevo_estado
    comprobante.codigo_respuesta = str(datos.get("codigo_respuesta", ""))
    comprobante.mensaje_respuesta = datos.get("mensaje", "")
    comprobante.hash_cpe = datos.get("hash", comprobante.hash_cpe)
    if id_externo:
        comprobante.id_externo = id_externo
    comprobante.save(
        update_fields=[
            "estado",
            "codigo_respuesta",
            "mensaje_respuesta",
            "hash_cpe",
            "id_externo",
            "actualizado_en",
        ]
    )

    if comprobante.estado in (EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO):
        from apps.contabilidad.servicios import asiento_de_nota_credito, asiento_de_venta

        if comprobante.tipo == TipoComprobante.NOTA_CREDITO and comprobante.total:
            asiento_de_nota_credito(comprobante)
        else:
            asiento_de_venta(comprobante)
    return comprobante
