"""Pedido de venta: el documento que amarra CRM, almacén y facturación."""
from decimal import Decimal
from apps.core.monedas import redondear

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import ModeloEmpresa, Moneda
from apps.core.transacciones import operacion_serializada

IGV = Decimal("0.18")


class EstadoPedido(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    ENVIADA = "enviada", "Cotización enviada"
    CONFIRMADO = "confirmado", "Confirmado"
    RESERVADO = "reservado", "Reservado"
    ENTREGADO = "entregado", "Entregado"
    FACTURADO = "facturado", "Facturado"
    PAGADO = "pagado", "Pagado"
    CERRADO = "cerrado", "Cerrado"
    # Salidas alternas
    CANCELADO = "cancelado", "Cancelado"
    VENCIDA = "vencida", "Vencida"
    EN_ESPERA = "en_espera", "En espera de stock"


#: Qué saltos son legales. Un pedido no se edita libremente: avanza por aquí.
TRANSICIONES = {
    EstadoPedido.BORRADOR: {EstadoPedido.ENVIADA, EstadoPedido.CONFIRMADO, EstadoPedido.CANCELADO},
    EstadoPedido.ENVIADA: {EstadoPedido.CONFIRMADO, EstadoPedido.VENCIDA, EstadoPedido.CANCELADO},
    EstadoPedido.CONFIRMADO: {
        EstadoPedido.RESERVADO,
        EstadoPedido.EN_ESPERA,
        EstadoPedido.CANCELADO,
    },
    EstadoPedido.EN_ESPERA: {EstadoPedido.RESERVADO, EstadoPedido.CANCELADO},
    EstadoPedido.RESERVADO: {EstadoPedido.ENTREGADO, EstadoPedido.CANCELADO},
    EstadoPedido.ENTREGADO: {EstadoPedido.FACTURADO},
    EstadoPedido.FACTURADO: {EstadoPedido.PAGADO},
    EstadoPedido.PAGADO: {EstadoPedido.CERRADO},
    EstadoPedido.CERRADO: set(),
    EstadoPedido.CANCELADO: set(),
    EstadoPedido.VENCIDA: {EstadoPedido.BORRADOR},
}

ESTADOS_FINALES = {EstadoPedido.CERRADO, EstadoPedido.CANCELADO}


class TransicionInvalida(ValidationError):
    """Se intentó un salto de estado que el diseño no permite."""


class Pedido(ModeloEmpresa):
    numero = models.CharField(max_length=30, db_index=True)
    tercero = models.ForeignKey(
        "terceros.Tercero", on_delete=models.PROTECT, related_name="pedidos"
    )
    direccion_entrega = models.ForeignKey(
        "terceros.DireccionEntrega",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pedidos",
    )
    vendedor = models.ForeignKey(
        "core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="pedidos"
    )
    almacen = models.ForeignKey(
        "inventario.Almacen", on_delete=models.PROTECT, related_name="pedidos"
    )

    estado = models.CharField(
        max_length=15, choices=EstadoPedido, default=EstadoPedido.BORRADOR, db_index=True
    )
    fecha = models.DateField()
    valido_hasta = models.DateField(
        null=True, blank=True, help_text="Vencida esta fecha la cotización pasa a «vencida»"
    )

    moneda = models.CharField(max_length=3, choices=Moneda, default=Moneda.PEN)
    tipo_cambio = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("1"))

    origen = models.CharField(
        max_length=60, blank=True, help_text="tienda web, whatsapp, campaña, mostrador…"
    )
    campania = models.CharField(max_length=120, blank=True)
    utm = models.JSONField(default=dict, blank=True)

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impuestos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    notas = models.TextField(blank=True)
    ids_externos = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "pedido de venta"
        verbose_name_plural = "pedidos de venta"
        ordering = ("-fecha", "-id")
        constraints = [
            models.UniqueConstraint(fields=("empresa", "numero"), name="pedido_unico_por_numero")
        ]
        indexes = [models.Index(fields=("empresa", "estado"))]
        permissions = [("ver_todo", "Puede ver los pedidos de todos los vendedores")]

    def __str__(self):
        return f"{self.numero} — {self.tercero}"

    def puede_pasar_a(self, nuevo_estado):
        return nuevo_estado in TRANSICIONES.get(self.estado, set())

    @operacion_serializada
    def transicionar(self, nuevo_estado, usuario=None):
        """Cambia de estado validando el salto y dejando rastro en la auditoría."""
        from apps.core.models import RegistroAuditoria

        if not self.puede_pasar_a(nuevo_estado):
            raise TransicionInvalida(
                f"No se puede pasar de «{self.get_estado_display()}» a «{nuevo_estado}»."
            )
        anterior = self.estado
        self.estado = nuevo_estado
        self.save(update_fields=["estado", "actualizado_en"])
        RegistroAuditoria.objects.create(
            empresa=self.empresa,
            usuario=usuario,
            modelo="ventas.Pedido",
            objeto_id=str(self.pk),
            accion=RegistroAuditoria.Accion.TRANSICION,
            valores_antes={"estado": anterior},
            valores_despues={"estado": nuevo_estado},
        )
        return self

    def recalcular_totales(self, guardar=True):
        subtotal = sum((redondear(linea.subtotal) for linea in self.lineas.all()), Decimal("0"))
        impuestos = sum((redondear(linea.impuesto) for linea in self.lineas.all()), Decimal("0"))
        self.subtotal = redondear(subtotal)
        self.impuestos = redondear(impuestos)
        self.total = self.subtotal + self.impuestos
        if guardar:
            self.save(update_fields=["subtotal", "impuestos", "total", "actualizado_en"])
        return self.total


class LineaPedido(ModeloEmpresa):
    """El detalle vive aparte para poder entregar o facturar parcialmente."""

    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey(
        "catalogo.Producto", on_delete=models.PROTECT, related_name="lineas_pedido"
    )
    descripcion = models.CharField(max_length=255, blank=True)
    cantidad = models.DecimalField(max_digits=14, decimal_places=4)
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    descuento_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tasa_impuesto = models.DecimalField(max_digits=5, decimal_places=4, default=IGV)

    cantidad_entregada = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    cantidad_facturada = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    class Meta:
        verbose_name = "línea de pedido"
        verbose_name_plural = "líneas de pedido"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cantidad__gt=0), name="linea_cantidad_positiva"
            ),
            models.CheckConstraint(
                condition=models.Q(descuento_pct__gte=0) & models.Q(descuento_pct__lte=100),
                name="descuento_entre_0_y_100",
            ),
        ]

    def __str__(self):
        return f"{self.cantidad} × {self.producto}"

    @property
    def subtotal(self):
        bruto = self.cantidad * self.precio_unitario
        return bruto * (Decimal("1") - self.descuento_pct / Decimal("100"))

    @property
    def impuesto(self):
        if self.producto.afectacion_igv != "10":
            return Decimal("0")
        return self.subtotal * self.tasa_impuesto

    @property
    def total(self):
        return self.subtotal + self.impuesto

    @property
    def pendiente_entrega(self):
        return self.cantidad - self.cantidad_entregada

    @property
    def pendiente_facturacion(self):
        return self.cantidad - self.cantidad_facturada
