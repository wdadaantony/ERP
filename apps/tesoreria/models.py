"""Cobros, pagos y conciliación bancaria.

La `referencia_externa` es la pieza clave: es lo que permite cruzar un cobro del
ERP con la línea del extracto del banco o con el aviso de la pasarela.
"""
from decimal import Decimal

from django.db import models

from apps.core.models import ModeloEmpresa, Moneda


class MetodoPago(models.TextChoices):
    EFECTIVO = "efectivo", "Efectivo"
    TRANSFERENCIA = "transferencia", "Transferencia"
    DEPOSITO = "deposito", "Depósito"
    TARJETA = "tarjeta", "Tarjeta"
    PASARELA = "pasarela", "Pasarela de pagos"
    YAPE_PLIN = "yape_plin", "Yape o Plin"
    LETRA = "letra", "Letra o canje"


class CuentaBancaria(ModeloEmpresa):
    banco = models.CharField(max_length=80)
    numero = models.CharField(max_length=40)
    cci = models.CharField(max_length=20, blank=True)
    moneda = models.CharField(max_length=3, choices=Moneda, default=Moneda.PEN)
    cuenta_contable = models.CharField(max_length=20, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "cuenta bancaria"
        verbose_name_plural = "cuentas bancarias"
        constraints = [
            models.UniqueConstraint(fields=("empresa", "numero"), name="cuenta_unica_por_numero")
        ]

    def __str__(self):
        return f"{self.banco} {self.numero}"


class Movimiento(ModeloEmpresa):
    """Un cobro o un pago. El signo lo da el sentido, no el monto."""

    class Sentido(models.TextChoices):
        COBRO = "cobro", "Cobro (entra dinero)"
        PAGO = "pago", "Pago (sale dinero)"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        CONFIRMADO = "confirmado", "Confirmado"
        CONCILIADO = "conciliado", "Conciliado con el banco"
        ANULADO = "anulado", "Anulado"

    sentido = models.CharField(max_length=6, choices=Sentido, db_index=True)
    tercero = models.ForeignKey(
        "terceros.Tercero", on_delete=models.PROTECT, related_name="movimientos_tesoreria"
    )
    comprobante = models.ForeignKey(
        "facturacion.Comprobante",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cobros",
    )
    extorna_a = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="extorno",
        help_text="Movimiento original que este pago inverso extorna",
    )
    metodo = models.CharField(max_length=15, choices=MetodoPago)
    cuenta = models.ForeignKey(
        CuentaBancaria, on_delete=models.PROTECT, null=True, blank=True, related_name="movimientos"
    )

    monto = models.DecimalField(max_digits=14, decimal_places=2)
    comision = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    moneda = models.CharField(max_length=3, choices=Moneda, default=Moneda.PEN)
    tipo_cambio = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("1"))
    fecha = models.DateField(db_index=True)

    estado = models.CharField(
        max_length=12, choices=Estado, default=Estado.PENDIENTE, db_index=True
    )
    referencia_externa = models.CharField(
        max_length=120,
        blank=True,
        db_index=True,
        help_text="Id de la pasarela u operación bancaria, para conciliar",
    )
    notas = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "cobro o pago"
        verbose_name_plural = "cobros y pagos"
        ordering = ("-fecha", "-id")
        constraints = [
            models.CheckConstraint(condition=models.Q(monto__gt=0), name="monto_positivo"),
            models.UniqueConstraint(fields=("empresa", "sentido", "referencia_externa"),
                condition=~models.Q(referencia_externa=""), name="movimiento_referencia_unica"),
        ]

    def __str__(self):
        return f"{self.get_sentido_display()} {self.monto} {self.moneda}"


class LineaExtractoBancario(ModeloEmpresa):
    """Línea cruda del extracto del banco, antes de emparejarla con un movimiento."""

    cuenta = models.ForeignKey(
        CuentaBancaria, on_delete=models.CASCADE, related_name="lineas_extracto"
    )
    fecha = models.DateField(db_index=True)
    descripcion = models.CharField(max_length=255)
    monto = models.DecimalField(
        max_digits=14, decimal_places=2, help_text="Negativo si es cargo"
    )
    referencia = models.CharField(max_length=120, blank=True, db_index=True)
    movimiento = models.ForeignKey(
        Movimiento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lineas_extracto",
    )
    conciliada = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = "línea de extracto bancario"
        verbose_name_plural = "líneas de extracto bancario"
        ordering = ("-fecha",)

    def __str__(self):
        return f"{self.fecha} {self.descripcion} {self.monto}"
