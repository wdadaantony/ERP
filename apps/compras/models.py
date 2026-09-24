"""Órdenes de compra e ingreso de mercadería.

Cuando el pedido de venta queda «en espera» por falta de stock, es este módulo
el que lo destraba: se compra, ingresa la mercadería y el pedido vuelve a avanzar.
"""
from decimal import Decimal
from apps.core.monedas import redondear

from django.db import models

from apps.core.models import ModeloEmpresa, Moneda

IGV = Decimal("0.18")


class EstadoOrdenCompra(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    POR_APROBAR = "por_aprobar", "Esperando aprobación"
    APROBADA = "aprobada", "Aprobada"
    ENVIADA = "enviada", "Enviada al proveedor"
    RECIBIDA_PARCIAL = "recibida_parcial", "Recibida parcialmente"
    RECIBIDA = "recibida", "Recibida"
    CERRADA = "cerrada", "Cerrada"
    CANCELADA = "cancelada", "Cancelada"


class OrdenCompra(ModeloEmpresa):
    numero = models.CharField(max_length=30, db_index=True)
    proveedor = models.ForeignKey(
        "terceros.Tercero", on_delete=models.PROTECT, related_name="ordenes_compra"
    )
    almacen_destino = models.ForeignKey(
        "inventario.Almacen", on_delete=models.PROTECT, related_name="ordenes_compra"
    )
    solicitante = models.ForeignKey(
        "core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    aprobada_por = models.ForeignKey(
        "core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    estado = models.CharField(
        max_length=20, choices=EstadoOrdenCompra, default=EstadoOrdenCompra.BORRADOR, db_index=True
    )
    fecha = models.DateField()
    fecha_entrega_esperada = models.DateField(null=True, blank=True)

    moneda = models.CharField(max_length=3, choices=Moneda, default=Moneda.PEN)
    tipo_cambio = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("1"))

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impuestos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    pedido_origen = models.ForeignKey(
        "ventas.Pedido",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ordenes_compra",
        help_text="Cuando la compra nace de un pedido sin stock",
    )
    notas = models.TextField(blank=True)

    class Meta:
        verbose_name = "orden de compra"
        verbose_name_plural = "órdenes de compra"
        ordering = ("-fecha", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "numero"), name="orden_compra_unica_por_numero"
            )
        ]
        permissions = [("aprobar_ordencompra", "Puede aprobar órdenes de compra")]

    def __str__(self):
        return f"{self.numero} — {self.proveedor}"

    def recalcular_totales(self, guardar=True):
        subtotal = sum((redondear(l.subtotal) for l in self.lineas.all()), Decimal("0"))
        impuestos = sum((redondear(l.impuesto) for l in self.lineas.all()), Decimal("0"))
        self.subtotal = redondear(subtotal)
        self.impuestos = redondear(impuestos)
        self.total = self.subtotal + self.impuestos
        if guardar:
            self.save(update_fields=["subtotal", "impuestos", "total", "actualizado_en"])
        return self.total


class LineaOrdenCompra(ModeloEmpresa):
    orden = models.ForeignKey(OrdenCompra, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey(
        "catalogo.Producto", on_delete=models.PROTECT, related_name="lineas_compra"
    )
    cantidad = models.DecimalField(max_digits=14, decimal_places=4)
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    tasa_impuesto = models.DecimalField(max_digits=5, decimal_places=4, default=IGV)
    cantidad_recibida = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    class Meta:
        verbose_name = "línea de orden de compra"
        verbose_name_plural = "líneas de orden de compra"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cantidad__gt=0), name="linea_oc_cantidad_positiva"
            )
        ]

    def __str__(self):
        return f"{self.cantidad} × {self.producto}"

    @property
    def subtotal(self):
        return self.cantidad * self.precio_unitario

    @property
    def impuesto(self):
        if self.producto.afectacion_igv != "10":
            return Decimal("0")
        return self.subtotal * self.tasa_impuesto

    @property
    def pendiente_recepcion(self):
        return self.cantidad - self.cantidad_recibida


class Recepcion(ModeloEmpresa):
    """Ingreso físico de mercadería. Genera los movimientos de stock de entrada."""

    numero = models.CharField(max_length=30, db_index=True)
    orden = models.ForeignKey(
        OrdenCompra, on_delete=models.PROTECT, related_name="recepciones", null=True, blank=True
    )
    almacen = models.ForeignKey(
        "inventario.Almacen", on_delete=models.PROTECT, related_name="recepciones"
    )
    fecha = models.DateField()
    guia_proveedor = models.CharField(max_length=60, blank=True)
    recibido_por = models.ForeignKey(
        "core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "recepción"
        verbose_name_plural = "recepciones"
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "numero"), name="recepcion_unica_por_numero"
            )
        ]

    def __str__(self):
        return self.numero


class LineaRecepcion(ModeloEmpresa):
    recepcion = models.ForeignKey(Recepcion, on_delete=models.CASCADE, related_name="lineas")
    linea_orden = models.ForeignKey(
        LineaOrdenCompra,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recepciones",
    )
    producto = models.ForeignKey(
        "catalogo.Producto", on_delete=models.PROTECT, related_name="lineas_recepcion"
    )
    cantidad = models.DecimalField(max_digits=14, decimal_places=4)
    costo_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    lote = models.ForeignKey(
        "inventario.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "línea de recepción"
        verbose_name_plural = "líneas de recepción"

    def __str__(self):
        return f"{self.cantidad} × {self.producto}"
