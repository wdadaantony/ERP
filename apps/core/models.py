"""Empresa, usuarios y las piezas base que comparten todos los módulos."""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from apps.core.transacciones import operacion_serializada


class Moneda(models.TextChoices):
    PEN = "PEN", "Soles"
    USD = "USD", "Dólares"
    EUR = "EUR", "Euros"


class TipoDocumentoIdentidad(models.TextChoices):
    """Catálogo 06 de SUNAT."""

    DNI = "1", "DNI"
    CARNET_EXTRANJERIA = "4", "Carné de extranjería"
    RUC = "6", "RUC"
    PASAPORTE = "7", "Pasaporte"
    OTRO = "0", "Otro"


class SelloDeTiempo(models.Model):
    """Fecha de alta y última modificación. La heredan casi todos los modelos."""

    creado_en = models.DateTimeField(auto_now_add=True, db_index=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ModeloEmpresa(SelloDeTiempo):
    """Todo registro operativo pertenece a una empresa: la instalación es multiempresa."""

    empresa = models.ForeignKey(
        "core.Empresa", on_delete=models.PROTECT, related_name="%(class)s_set"
    )

    class Meta:
        abstract = True


class Empresa(SelloDeTiempo):
    razon_social = models.CharField(max_length=200)
    nombre_comercial = models.CharField(max_length=200, blank=True)
    ruc = models.CharField(max_length=11, unique=True)
    direccion_fiscal = models.CharField(max_length=255, blank=True)
    moneda_base = models.CharField(max_length=3, choices=Moneda, default=Moneda.PEN)
    plan_contable = models.CharField(
        max_length=50, default="PCGE", help_text="Plan contable que rige los asientos"
    )
    logo = models.ImageField(upload_to="logos/", blank=True, null=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "empresa"
        verbose_name_plural = "empresas"
        ordering = ("razon_social",)

    def __str__(self):
        return self.razon_social


class Serie(ModeloEmpresa):
    """Series y correlativos por tipo de documento. El correlativo nunca se salta."""

    tipo_documento = models.CharField(
        max_length=3, help_text="Catálogo 01 SUNAT: 01 factura, 03 boleta, 07 NC, 08 ND; PED/REC internos"
    )
    serie = models.CharField(max_length=12)
    correlativo_actual = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "serie"
        verbose_name_plural = "series"
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "tipo_documento", "serie"), name="serie_unica_por_empresa"
            )
        ]

    def __str__(self):
        return f"{self.serie} ({self.tipo_documento})"

    @operacion_serializada
    def siguiente_numero(self):
        """Reserva el siguiente correlativo de forma atómica.

        Se apoya en un UPDATE con F() para que dos usuarios emitiendo a la vez
        no obtengan el mismo número.
        """
        actualizados = Serie.objects.filter(pk=self.pk).update(
            correlativo_actual=models.F("correlativo_actual") + 1
        )
        if not actualizados:  # pragma: no cover - la serie fue borrada
            raise RuntimeError("La serie ya no existe")
        self.refresh_from_db(fields=["correlativo_actual"])
        return self.correlativo_actual


class Usuario(AbstractUser):
    """Usuario del ERP. Puede acceder a varias empresas, con una activa por defecto."""

    empresas = models.ManyToManyField(Empresa, related_name="usuarios", blank=True)
    empresa_actual = models.ForeignKey(
        Empresa, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    telefono = models.CharField(max_length=30, blank=True)

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"

    def __str__(self):
        return self.get_full_name() or self.username


class LimiteAprobacion(SelloDeTiempo):
    """Hasta dónde puede aprobar cada usuario sin visto bueno de otro.

    Pasado el límite, el documento queda esperando aprobación (sección 8 del diseño).
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="limites"
    )
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="limites")
    descuento_maximo_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    monto_maximo_venta = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    monto_maximo_compra = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    puede_vender_al_credito = models.BooleanField(default=False)

    class Meta:
        verbose_name = "límite de aprobación"
        verbose_name_plural = "límites de aprobación"
        constraints = [
            models.UniqueConstraint(
                fields=("usuario", "empresa"), name="limite_unico_por_usuario_empresa"
            )
        ]

    def __str__(self):
        return f"Límites de {self.usuario} en {self.empresa}"


class RegistroAuditoria(SelloDeTiempo):
    """Rastro de «quién cambió qué y cuándo», para resolver los «yo no fui»."""

    class Accion(models.TextChoices):
        CREAR = "crear", "Creación"
        EDITAR = "editar", "Edición"
        ELIMINAR = "eliminar", "Eliminación"
        TRANSICION = "transicion", "Cambio de estado"

    empresa = models.ForeignKey(
        Empresa, on_delete=models.CASCADE, related_name="auditoria", null=True, blank=True
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    modelo = models.CharField(max_length=100, db_index=True)
    objeto_id = models.CharField(max_length=50, db_index=True)
    accion = models.CharField(max_length=20, choices=Accion)
    valores_antes = models.JSONField(default=dict, blank=True)
    valores_despues = models.JSONField(default=dict, blank=True)
    canal = models.CharField(
        max_length=50, default="web", help_text="web, api, portal, tarea programada"
    )

    class Meta:
        verbose_name = "registro de auditoría"
        verbose_name_plural = "registros de auditoría"
        ordering = ("-creado_en",)
        indexes = [models.Index(fields=("modelo", "objeto_id"))]

    def __str__(self):
        return f"{self.accion} {self.modelo}#{self.objeto_id}"
