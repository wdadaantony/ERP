"""Un solo registro de tercero sirve a CRM, ventas, compras y contabilidad."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import ModeloEmpresa, TipoDocumentoIdentidad


class CondicionPago(ModeloEmpresa):
    nombre = models.CharField(max_length=80)
    dias = models.PositiveSmallIntegerField(default=0, help_text="0 = contado")

    class Meta:
        verbose_name = "condición de pago"
        verbose_name_plural = "condiciones de pago"

    def __str__(self):
        return self.nombre


class Tercero(ModeloEmpresa):
    """Cliente, proveedor o ambos. Las dos banderas pueden estar activas a la vez."""

    es_cliente = models.BooleanField(default=True)
    es_proveedor = models.BooleanField(default=False)

    tipo_documento = models.CharField(
        max_length=1, choices=TipoDocumentoIdentidad, default=TipoDocumentoIdentidad.DNI
    )
    numero_documento = models.CharField(max_length=15, db_index=True)
    razon_social = models.CharField(max_length=200)
    nombre_comercial = models.CharField(max_length=200, blank=True)

    direccion_fiscal = models.CharField(max_length=255, blank=True)
    ubigeo = models.CharField(max_length=6, blank=True)
    email = models.EmailField(blank=True, db_index=True)
    telefono = models.CharField(max_length=30, blank=True)

    condicion_pago = models.ForeignKey(
        CondicionPago, on_delete=models.SET_NULL, null=True, blank=True, related_name="terceros"
    )
    linea_credito = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vendedor_asignado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clientes",
    )

    ids_externos = models.JSONField(
        default=dict,
        blank=True,
        help_text="Id en cada servicio externo, por ejemplo hubspot o shopify",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "tercero"
        verbose_name_plural = "terceros"
        ordering = ("razon_social",)
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "tipo_documento", "numero_documento"),
                name="tercero_unico_por_documento",
            )
        ]
        indexes = [models.Index(fields=("empresa", "es_cliente"))]
        permissions = [("ver_todo", "Puede ver los terceros de todos los vendedores")]

    def __str__(self):
        return f"{self.numero_documento} — {self.razon_social}"

    def clean(self):
        if not (self.es_cliente or self.es_proveedor):
            raise ValidationError("El tercero debe ser cliente, proveedor o ambos.")
        if self.tipo_documento == TipoDocumentoIdentidad.RUC and len(self.numero_documento) != 11:
            raise ValidationError({"numero_documento": "El RUC debe tener 11 dígitos."})
        if self.tipo_documento == TipoDocumentoIdentidad.DNI and len(self.numero_documento) != 8:
            raise ValidationError({"numero_documento": "El DNI debe tener 8 dígitos."})


class Contacto(ModeloEmpresa):
    """Personas dentro de un tercero: quien compra, quien paga, quien recibe."""

    tercero = models.ForeignKey(Tercero, on_delete=models.CASCADE, related_name="contactos")
    nombre = models.CharField(max_length=150)
    cargo = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=30, blank=True)

    class Meta:
        verbose_name = "contacto"
        verbose_name_plural = "contactos"

    def __str__(self):
        return self.nombre


class DireccionEntrega(ModeloEmpresa):
    tercero = models.ForeignKey(Tercero, on_delete=models.CASCADE, related_name="direcciones")
    etiqueta = models.CharField(max_length=80, help_text="Almacén central, tienda Miraflores…")
    direccion = models.CharField(max_length=255)
    ubigeo = models.CharField(max_length=6, blank=True)
    referencia = models.CharField(max_length=255, blank=True)
    es_principal = models.BooleanField(default=False)

    class Meta:
        verbose_name = "dirección de entrega"
        verbose_name_plural = "direcciones de entrega"

    def __str__(self):
        return f"{self.etiqueta}: {self.direccion}"
