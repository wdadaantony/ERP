"""Productos y servicios. Comparten ficha; solo los bienes mueven stock."""
from django.db import models

from apps.core.models import ModeloEmpresa


class AfectacionIGV(models.TextChoices):
    """Catálogo 07 de SUNAT, reducido a los casos de uso habituales."""

    GRAVADO = "10", "Gravado - operación onerosa"
    EXONERADO = "20", "Exonerado"
    INAFECTO = "30", "Inafecto"
    BONIFICACION = "11", "Gravado - retiro por bonificación"


class UnidadMedida(ModeloEmpresa):
    """Código del catálogo 03 de SUNAT: NIU, ZZ, KGM, MTR…"""

    codigo = models.CharField(max_length=5)
    nombre = models.CharField(max_length=60)

    class Meta:
        verbose_name = "unidad de medida"
        verbose_name_plural = "unidades de medida"
        constraints = [
            models.UniqueConstraint(fields=("empresa", "codigo"), name="unidad_unica_por_empresa")
        ]

    def __str__(self):
        return self.codigo


class CategoriaProducto(ModeloEmpresa):
    nombre = models.CharField(max_length=100)
    padre = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="hijas"
    )

    class Meta:
        verbose_name = "categoría de producto"
        verbose_name_plural = "categorías de producto"

    def __str__(self):
        return self.nombre


class Producto(ModeloEmpresa):
    class Tipo(models.TextChoices):
        BIEN = "bien", "Bien"
        SERVICIO = "servicio", "Servicio"

    codigo = models.CharField(max_length=40, db_index=True)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    tipo = models.CharField(max_length=10, choices=Tipo, default=Tipo.BIEN)
    categoria = models.ForeignKey(
        CategoriaProducto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="productos",
    )
    unidad_medida = models.ForeignKey(
        UnidadMedida, on_delete=models.PROTECT, related_name="productos"
    )

    precio_lista = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    costo_promedio = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=0,
        help_text="Lo recalcula el módulo de inventario en cada ingreso",
    )
    afectacion_igv = models.CharField(
        max_length=2, choices=AfectacionIGV, default=AfectacionIGV.GRAVADO
    )

    controla_stock = models.BooleanField(default=True)
    controla_lotes = models.BooleanField(default=False)
    controla_series = models.BooleanField(default=False)
    stock_minimo = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    ids_externos = models.JSONField(default=dict, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "producto"
        verbose_name_plural = "productos"
        ordering = ("codigo",)
        constraints = [
            models.UniqueConstraint(fields=("empresa", "codigo"), name="producto_unico_por_codigo"),
            models.CheckConstraint(
                condition=models.Q(tipo="bien") | models.Q(controla_stock=False),
                name="servicio_no_controla_stock",
            ),
        ]

    def __str__(self):
        return f"{self.codigo} — {self.nombre}"

    def save(self, *args, **kwargs):
        if self.tipo == self.Tipo.SERVICIO:
            self.controla_stock = False
            self.controla_lotes = False
            self.controla_series = False
        super().save(*args, **kwargs)


class ListaPrecios(ModeloEmpresa):
    nombre = models.CharField(max_length=100)
    moneda = models.CharField(max_length=3, default="PEN")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "lista de precios"
        verbose_name_plural = "listas de precios"

    def __str__(self):
        return self.nombre


class PrecioProducto(ModeloEmpresa):
    lista = models.ForeignKey(ListaPrecios, on_delete=models.CASCADE, related_name="precios")
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="precios")
    precio = models.DecimalField(max_digits=14, decimal_places=4)
    cantidad_minima = models.DecimalField(max_digits=14, decimal_places=4, default=1)

    class Meta:
        verbose_name = "precio de producto"
        verbose_name_plural = "precios de producto"
        constraints = [
            models.UniqueConstraint(
                fields=("lista", "producto", "cantidad_minima"), name="precio_unico_por_escala"
            )
        ]

    def __str__(self):
        return f"{self.producto} @ {self.precio}"
