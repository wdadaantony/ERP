"""Asientos contables.

La contabilidad nace del documento, no se digita aparte: cada asiento apunta al
comprobante o movimiento que lo originó, y las reglas por tipo de documento
dicen qué cuentas se usan.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from apps.core.models import ModeloEmpresa


class CuentaContable(ModeloEmpresa):
    """Cuenta del plan contable. El árbol se arma con `padre`."""

    class Naturaleza(models.TextChoices):
        DEUDORA = "deudora", "Deudora"
        ACREEDORA = "acreedora", "Acreedora"

    codigo = models.CharField(max_length=20, db_index=True)
    nombre = models.CharField(max_length=200)
    padre = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="hijas"
    )
    naturaleza = models.CharField(max_length=10, choices=Naturaleza, default=Naturaleza.DEUDORA)
    acepta_movimiento = models.BooleanField(
        default=True, help_text="Las cuentas de agrupación no reciben asientos"
    )
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "cuenta contable"
        verbose_name_plural = "cuentas contables"
        ordering = ("codigo",)
        constraints = [
            models.UniqueConstraint(fields=("empresa", "codigo"), name="cuenta_unica_por_codigo")
        ]

    def __str__(self):
        return f"{self.codigo} — {self.nombre}"


class PeriodoContable(ModeloEmpresa):
    anio = models.PositiveSmallIntegerField()
    mes = models.PositiveSmallIntegerField()
    cerrado = models.BooleanField(
        default=False, help_text="Un periodo cerrado ya no admite asientos nuevos"
    )

    class Meta:
        verbose_name = "periodo contable"
        verbose_name_plural = "periodos contables"
        ordering = ("-anio", "-mes")
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "anio", "mes"), name="periodo_unico_por_empresa"
            )
        ]

    def __str__(self):
        return f"{self.anio}-{self.mes:02d}"


class TipoCambioCierre(ModeloEmpresa):
    periodo = models.ForeignKey(PeriodoContable, on_delete=models.PROTECT, related_name="tipos_cambio")
    moneda = models.CharField(max_length=3)
    tasa = models.DecimalField(max_digits=10, decimal_places=6)

    class Meta:
        verbose_name = "tipo de cambio de cierre"
        verbose_name_plural = "tipos de cambio de cierre"
        constraints = [models.UniqueConstraint(
            fields=("empresa", "periodo", "moneda"), name="tipo_cambio_cierre_unico")]


class Asiento(ModeloEmpresa):
    numero = models.CharField(max_length=30, db_index=True)
    fecha = models.DateField(db_index=True)
    periodo = models.ForeignKey(
        PeriodoContable, on_delete=models.PROTECT, related_name="asientos", null=True, blank=True
    )
    glosa = models.CharField(max_length=255)

    # De dónde salió el asiento: nunca se digita a mano si hay documento detrás.
    comprobante = models.ForeignKey(
        "facturacion.Comprobante",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="asientos",
    )
    movimiento_tesoreria = models.ForeignKey(
        "tesoreria.Movimiento",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="asientos",
    )
    extorna_a = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="extorno",
        help_text="Asiento original revertido por este asiento",
    )

    generado_automaticamente = models.BooleanField(default=True)
    clave_automatica = models.CharField(max_length=120, blank=True, db_index=True)
    creado_por = models.ForeignKey(
        "core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "asiento contable"
        verbose_name_plural = "asientos contables"
        ordering = ("-fecha", "-id")
        constraints = [
            models.UniqueConstraint(fields=("empresa", "numero"), name="asiento_unico_por_numero"),
            models.UniqueConstraint(fields=("empresa", "clave_automatica"),
                condition=~models.Q(clave_automatica=""), name="asiento_clave_automatica_unica"),
        ]

    def __str__(self):
        return f"{self.numero} — {self.glosa}"

    @property
    def total_debe(self):
        return self.lineas.aggregate(t=Sum("debe"))["t"] or Decimal("0")

    @property
    def total_haber(self):
        return self.lineas.aggregate(t=Sum("haber"))["t"] or Decimal("0")

    @property
    def cuadra(self):
        return abs(self.total_debe - self.total_haber) < Decimal("0.005")

    def validar_cuadre(self):
        if not self.cuadra:
            raise ValidationError(
                f"El asiento no cuadra: debe {self.total_debe} contra haber {self.total_haber}."
            )


class LineaAsiento(ModeloEmpresa):
    asiento = models.ForeignKey(Asiento, on_delete=models.CASCADE, related_name="lineas")
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, related_name="lineas")
    tercero = models.ForeignKey(
        "terceros.Tercero", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    glosa = models.CharField(max_length=255, blank=True)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = "línea de asiento"
        verbose_name_plural = "líneas de asiento"
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(debe=0) | models.Q(haber=0)),
                name="linea_asiento_debe_o_haber",
            ),
            models.CheckConstraint(
                condition=models.Q(debe__gte=0) & models.Q(haber__gte=0),
                name="linea_asiento_sin_negativos",
            ),
        ]

    def __str__(self):
        lado = f"debe {self.debe}" if self.debe else f"haber {self.haber}"
        return f"{self.cuenta.codigo} {lado}"


class ReglaContable(ModeloEmpresa):
    """Qué cuentas usar según el tipo de documento. Esto hace el asiento automático."""

    tipo_documento = models.CharField(
        max_length=40, help_text="venta_factura, venta_boleta, cobro, pago, compra…"
    )
    cuenta_debe = models.ForeignKey(
        CuentaContable, on_delete=models.PROTECT, related_name="reglas_debe"
    )
    cuenta_haber = models.ForeignKey(
        CuentaContable, on_delete=models.PROTECT, related_name="reglas_haber"
    )
    descripcion = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "regla contable"
        verbose_name_plural = "reglas contables"
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "tipo_documento"), name="regla_unica_por_tipo_documento"
            )
        ]

    def __str__(self):
        return f"{self.tipo_documento}: {self.cuenta_debe.codigo} / {self.cuenta_haber.codigo}"
