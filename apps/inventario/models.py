"""Almacenes y movimientos de stock.

Regla del diseño: el stock no es un campo editable. Es la suma de los movimientos
de un producto en una ubicación. Por eso no existe un `Producto.stock`.
"""
from decimal import Decimal

from django.db import models
from django.db.models import Sum

from apps.core.models import ModeloEmpresa


class Almacen(ModeloEmpresa):
    codigo = models.CharField(max_length=20)
    nombre = models.CharField(max_length=120)
    direccion = models.CharField(max_length=255, blank=True)
    ubigeo = models.CharField(max_length=6, blank=True)
    responsable = models.ForeignKey(
        "core.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="almacenes",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "almacén"
        verbose_name_plural = "almacenes"
        constraints = [
            models.UniqueConstraint(fields=("empresa", "codigo"), name="almacen_unico_por_codigo")
        ]

    def __str__(self):
        return self.nombre


class Ubicacion(ModeloEmpresa):
    """Posición dentro de un almacén. Todo movimiento sale de una y entra a otra.

    Las ubicaciones virtuales (proveedor, cliente, ajuste) son la contrapartida
    contable de los movimientos: así toda entrada y salida cuadra por partida doble.
    """

    class Tipo(models.TextChoices):
        INTERNA = "interna", "Interna"
        PROVEEDOR = "proveedor", "Virtual — proveedor"
        CLIENTE = "cliente", "Virtual — cliente"
        AJUSTE = "ajuste", "Virtual — ajuste de inventario"
        TRANSITO = "transito", "Virtual — en tránsito"

    almacen = models.ForeignKey(
        Almacen, on_delete=models.CASCADE, related_name="ubicaciones", null=True, blank=True
    )
    codigo = models.CharField(max_length=30)
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=15, choices=Tipo, default=Tipo.INTERNA)

    class Meta:
        verbose_name = "ubicación"
        verbose_name_plural = "ubicaciones"
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "codigo"), name="ubicacion_unica_por_codigo"
            )
        ]

    def __str__(self):
        return f"{self.codigo} — {self.nombre}"

    @property
    def es_fisica(self):
        return self.tipo == self.Tipo.INTERNA


class Lote(ModeloEmpresa):
    producto = models.ForeignKey(
        "catalogo.Producto", on_delete=models.CASCADE, related_name="lotes"
    )
    codigo = models.CharField(max_length=60)
    fecha_vencimiento = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "lote"
        verbose_name_plural = "lotes"
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "producto", "codigo"), name="lote_unico_por_producto"
            )
        ]

    def __str__(self):
        return self.codigo


class MovimientoStock(ModeloEmpresa):
    """Cada movimiento queda amarrado al documento que lo originó.

    `documento_origen` guarda el texto legible (PED-0001, OC-0012) y
    `documento_tipo`/`documento_id` permiten volver al registro exacto.
    """

    producto = models.ForeignKey(
        "catalogo.Producto", on_delete=models.PROTECT, related_name="movimientos"
    )
    cantidad = models.DecimalField(
        max_digits=14, decimal_places=4, help_text="Siempre positiva: el sentido lo dan las ubicaciones"
    )
    origen = models.ForeignKey(
        Ubicacion, on_delete=models.PROTECT, related_name="movimientos_salida"
    )
    destino = models.ForeignKey(
        Ubicacion, on_delete=models.PROTECT, related_name="movimientos_entrada"
    )
    lote = models.ForeignKey(
        Lote, on_delete=models.PROTECT, null=True, blank=True, related_name="movimientos"
    )
    numero_serie = models.CharField(max_length=80, blank=True)

    costo_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    fecha = models.DateTimeField(db_index=True)

    documento_origen = models.CharField(max_length=60, blank=True, db_index=True)
    documento_tipo = models.CharField(max_length=40, blank=True)
    documento_id = models.CharField(max_length=40, blank=True)

    usuario = models.ForeignKey(
        "core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "movimiento de stock"
        verbose_name_plural = "movimientos de stock"
        ordering = ("-fecha",)
        constraints = [
            models.CheckConstraint(condition=models.Q(cantidad__gt=0), name="cantidad_positiva"),
            models.CheckConstraint(
                condition=~models.Q(origen=models.F("destino")), name="origen_distinto_de_destino"
            ),
        ]
        indexes = [models.Index(fields=("producto", "fecha"))]

    def __str__(self):
        return f"{self.cantidad} × {self.producto} → {self.destino}"


class ReservaStock(ModeloEmpresa):
    """Stock apartado al confirmar un pedido, antes de que salga físicamente.

    Resuelve el caso «dos vendedores venden el último producto»: la reserva se
    toma al confirmar, no al facturar.
    """

    class Estado(models.TextChoices):
        ACTIVA = "activa", "Activa"
        CONSUMIDA = "consumida", "Consumida por la salida"
        LIBERADA = "liberada", "Liberada"

    producto = models.ForeignKey(
        "catalogo.Producto", on_delete=models.PROTECT, related_name="reservas"
    )
    ubicacion = models.ForeignKey(Ubicacion, on_delete=models.PROTECT, related_name="reservas")
    cantidad = models.DecimalField(max_digits=14, decimal_places=4)
    estado = models.CharField(max_length=12, choices=Estado, default=Estado.ACTIVA, db_index=True)

    documento_origen = models.CharField(max_length=60, blank=True, db_index=True)
    linea_pedido = models.ForeignKey(
        "ventas.LineaPedido",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reservas",
    )

    class Meta:
        verbose_name = "reserva de stock"
        verbose_name_plural = "reservas de stock"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cantidad__gt=0), name="reserva_cantidad_positiva"
            )
        ]

    def __str__(self):
        return f"Reserva {self.cantidad} × {self.producto} ({self.estado})"


def stock_en_mano(producto, ubicacion=None, empresa=None):
    """Existencia física: entradas menos salidas, según los movimientos."""
    entradas = MovimientoStock.objects.filter(producto=producto)
    salidas = MovimientoStock.objects.filter(producto=producto)
    if ubicacion is not None:
        entradas = entradas.filter(destino=ubicacion)
        salidas = salidas.filter(origen=ubicacion)
    else:
        entradas = entradas.filter(destino__tipo=Ubicacion.Tipo.INTERNA)
        salidas = salidas.filter(origen__tipo=Ubicacion.Tipo.INTERNA)
    if empresa is not None:
        entradas = entradas.filter(empresa=empresa)
        salidas = salidas.filter(empresa=empresa)

    suma = lambda qs: qs.aggregate(t=Sum("cantidad"))["t"] or Decimal("0")
    return suma(entradas) - suma(salidas)


def stock_disponible(producto, ubicacion=None, empresa=None):
    """Lo que realmente se puede comprometer: en mano menos lo ya reservado."""
    reservas = ReservaStock.objects.filter(
        producto=producto, estado=ReservaStock.Estado.ACTIVA
    )
    if ubicacion is not None:
        reservas = reservas.filter(ubicacion=ubicacion)
    if empresa is not None:
        reservas = reservas.filter(empresa=empresa)
    reservado = reservas.aggregate(t=Sum("cantidad"))["t"] or Decimal("0")
    return stock_en_mano(producto, ubicacion, empresa) - reservado


class GuiaRemision(ModeloEmpresa):
    """Guía de remisión electrónica del despacho.

    Se genera al despachar un pedido y, como cualquier comprobante, se encola
    hacia el OSE. Los bienes que traslada son los movimientos de salida del pedido.
    """

    class Motivo(models.TextChoices):
        VENTA = "01", "Venta"
        COMPRA = "02", "Compra"
        TRASLADO = "04", "Traslado entre establecimientos"
        DEVOLUCION = "06", "Devolución"
        OTROS = "13", "Otros"

    class Modalidad(models.TextChoices):
        PUBLICO = "01", "Transporte público"
        PRIVADO = "02", "Transporte privado"

    class Estado(models.TextChoices):
        BORRADOR = "borrador", "Borrador"
        POR_ENVIAR = "por_enviar", "En cola de envío"
        ENVIADO = "enviado", "Enviado, esperando CDR"
        ACEPTADO = "aceptado", "Aceptado por SUNAT"
        RECHAZADO = "rechazado", "Rechazado por SUNAT"

    serie = models.CharField(max_length=4)
    correlativo = models.PositiveIntegerField()
    pedido = models.ForeignKey(
        "ventas.Pedido", on_delete=models.PROTECT, related_name="guias", null=True, blank=True
    )
    destinatario = models.ForeignKey(
        "terceros.Tercero", on_delete=models.PROTECT, related_name="guias"
    )
    fecha_emision = models.DateField()
    fecha_traslado = models.DateField()
    motivo = models.CharField(max_length=2, choices=Motivo, default=Motivo.VENTA)
    modalidad = models.CharField(max_length=2, choices=Modalidad, default=Modalidad.PRIVADO)

    punto_partida = models.CharField(max_length=255)
    punto_llegada = models.CharField(max_length=255)
    transportista = models.CharField(max_length=150, blank=True)
    placa = models.CharField(max_length=10, blank=True)
    conductor_documento = models.CharField(max_length=15, blank=True)
    peso_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    bultos = models.PositiveIntegerField(default=1)

    estado = models.CharField(max_length=12, choices=Estado, default=Estado.BORRADOR, db_index=True)
    id_externo = models.CharField(max_length=100, blank=True)
    mensaje_respuesta = models.TextField(blank=True)
    observaciones = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "guía de remisión"
        verbose_name_plural = "guías de remisión"
        ordering = ("-fecha_emision", "-correlativo")
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "serie", "correlativo"), name="guia_unica_por_correlativo"
            )
        ]

    def __str__(self):
        return self.numero_completo

    @property
    def numero_completo(self):
        return f"{self.serie}-{self.correlativo:08d}"

    @property
    def movimientos(self):
        """Los bienes trasladados: las salidas del pedido al que pertenece."""
        if self.pedido is None:
            return MovimientoStock.objects.none()
        return MovimientoStock.objects.filter(
            documento_tipo="ventas.Pedido", documento_id=str(self.pedido_id)
        ).select_related("producto")
