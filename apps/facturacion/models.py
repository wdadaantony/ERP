"""Comprobantes electrónicos.

Guarda la respuesta oficial, no solo el PDF: el XML firmado, el hash y el CDR
que devuelve SUNAT a través del OSE. Sin eso no hay cómo defender una emisión.
"""
from decimal import Decimal

from django.db import models

from apps.core.models import ModeloEmpresa, Moneda


class TipoComprobante(models.TextChoices):
    """Catálogo 01 de SUNAT."""

    FACTURA = "01", "Factura"
    BOLETA = "03", "Boleta de venta"
    NOTA_CREDITO = "07", "Nota de crédito"
    NOTA_DEBITO = "08", "Nota de débito"
    GUIA_REMISION = "09", "Guía de remisión"


class EstadoComprobante(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    POR_ENVIAR = "por_enviar", "En cola de envío"
    ENVIADO = "enviado", "Enviado, esperando CDR"
    ACEPTADO = "aceptado", "Aceptado por SUNAT"
    OBSERVADO = "observado", "Aceptado con observaciones"
    RECHAZADO = "rechazado", "Rechazado por SUNAT"
    ANULADO = "anulado", "Anulado por comunicación de baja"


class Comprobante(ModeloEmpresa):
    tipo = models.CharField(max_length=2, choices=TipoComprobante, db_index=True)
    serie = models.CharField(max_length=4)
    correlativo = models.PositiveIntegerField()

    pedido = models.ForeignKey(
        "ventas.Pedido",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="comprobantes",
    )
    tercero = models.ForeignKey(
        "terceros.Tercero", on_delete=models.PROTECT, related_name="comprobantes"
    )

    fecha_emision = models.DateField(db_index=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    moneda = models.CharField(max_length=3, choices=Moneda, default=Moneda.PEN)
    tipo_cambio = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("1"))

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igv = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_cobrado = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    estado = models.CharField(
        max_length=12, choices=EstadoComprobante, default=EstadoComprobante.BORRADOR, db_index=True
    )

    # Respuesta oficial del OSE / SUNAT
    hash_cpe = models.CharField(max_length=100, blank=True)
    xml_firmado = models.FileField(upload_to="cpe/xml/", blank=True, null=True)
    cdr = models.FileField(upload_to="cpe/cdr/", blank=True, null=True)
    pdf = models.FileField(upload_to="cpe/pdf/", blank=True, null=True)
    codigo_respuesta = models.CharField(max_length=10, blank=True)
    mensaje_respuesta = models.TextField(
        blank=True, help_text="El texto exacto del error que ve la persona en el pedido"
    )
    id_externo = models.CharField(max_length=100, blank=True, db_index=True)

    # Notas de crédito y débito apuntan al comprobante que modifican
    comprobante_afectado = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="notas"
    )
    motivo_nota = models.CharField(max_length=120, blank=True)
    codigo_motivo_nota = models.CharField(max_length=2, blank=True)

    class Meta:
        verbose_name = "comprobante"
        verbose_name_plural = "comprobantes"
        ordering = ("-fecha_emision", "-correlativo")
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "tipo", "serie", "correlativo"),
                name="comprobante_unico_por_correlativo",
            )
        ]
        indexes = [models.Index(fields=("empresa", "estado"))]

    def __str__(self):
        return self.numero_completo

    @property
    def numero_completo(self):
        return f"{self.serie}-{self.correlativo:08d}"

    @property
    def saldo(self):
        return self.importe_vigente - self.total_cobrado

    @property
    def importe_vigente(self):
        importe = self.total
        for nota in self.notas.all():
            if nota.estado not in (EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO):
                continue
            if nota.tipo == TipoComprobante.NOTA_CREDITO:
                importe -= nota.total
            elif nota.tipo == TipoComprobante.NOTA_DEBITO:
                importe += nota.total
        return importe

    @property
    def saldo_base(self):
        from apps.core.monedas import a_moneda_base
        return a_moneda_base(self.saldo, self)

    @property
    def esta_pagado(self):
        return self.saldo <= Decimal("0.005")


class LineaComprobante(ModeloEmpresa):
    comprobante = models.ForeignKey(Comprobante, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey(
        "catalogo.Producto", on_delete=models.PROTECT, related_name="lineas_comprobante"
    )
    linea_pedido = models.ForeignKey(
        "ventas.LineaPedido",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lineas_comprobante",
    )
    descripcion = models.CharField(max_length=255)
    unidad_medida = models.CharField(max_length=5, default="NIU")
    cantidad = models.DecimalField(max_digits=14, decimal_places=4)
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    descuento = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    afectacion_igv = models.CharField(max_length=2, default="10")
    igv = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = "línea de comprobante"
        verbose_name_plural = "líneas de comprobante"

    def __str__(self):
        return f"{self.cantidad} × {self.descripcion}"
