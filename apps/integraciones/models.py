"""Capa de conectores: bitácora, credenciales, mapeos y cola de trabajos.

Se construye desde la fase 1 aunque todavía no haya nada que conectar. Si se deja
para el final, cada integración termina metida a la fuerza dentro de un módulo.
"""
from django.db import models
from django.utils import timezone

from apps.core.models import ModeloEmpresa, SelloDeTiempo


class ServicioExterno(ModeloEmpresa):
    """Un servicio de afuera y sus credenciales. Una llave por integración."""

    class Codigo(models.TextChoices):
        OSE = "ose", "OSE / facturación electrónica"
        PASARELA = "pasarela", "Pasarela de pagos"
        BANCO = "banco", "Banco"
        CRM = "crm", "CRM de marketing"
        WHATSAPP = "whatsapp", "WhatsApp Business"
        ADS = "ads", "Meta o Google Ads"
        TIENDA = "tienda", "Tienda en línea"
        DRIVE = "drive", "Google Workspace"
        AUTOMATIZACION = "automatizacion", "n8n, Make o Zapier"

    codigo = models.CharField(max_length=20, choices=Codigo, db_index=True)
    nombre = models.CharField(max_length=120)
    proveedor = models.CharField(max_length=80, blank=True, help_text="Nubefact, Culqi, Shopify…")
    url_base = models.URLField(blank=True)
    credenciales = models.JSONField(
        default=dict, blank=True, help_text="Token, llave o datos de OAuth del servicio"
    )
    secreto_webhook = models.CharField(
        max_length=200, blank=True, help_text="Con esto se valida la firma de lo que llega"
    )
    modo_simulado = models.BooleanField(
        default=True, help_text="Mientras esté activo no se llama al servicio real"
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "servicio externo"
        verbose_name_plural = "servicios externos"
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "codigo", "nombre"), name="servicio_unico_por_empresa"
            )
        ]

    def __str__(self):
        return f"{self.nombre} ({self.get_codigo_display()})"


class MapeoCampo(SelloDeTiempo):
    """Campo del ERP ↔ campo externo. Traducir aquí evita ensuciar el núcleo."""

    servicio = models.ForeignKey(
        ServicioExterno, on_delete=models.CASCADE, related_name="mapeos"
    )
    entidad = models.CharField(max_length=60, help_text="tercero, producto, pedido, comprobante…")
    campo_erp = models.CharField(max_length=80)
    campo_externo = models.CharField(max_length=120)
    transformacion = models.CharField(
        max_length=80, blank=True, help_text="mayusculas, fecha_iso, solo_digitos…"
    )

    class Meta:
        verbose_name = "mapeo de campo"
        verbose_name_plural = "mapeos de campo"
        constraints = [
            models.UniqueConstraint(
                fields=("servicio", "entidad", "campo_erp"), name="mapeo_unico_por_campo"
            )
        ]

    def __str__(self):
        return f"{self.campo_erp} → {self.campo_externo}"


class RegistroIntegracion(ModeloEmpresa):
    """Bitácora de cada envío y cada recepción. Sin esto no hay forma de auditar."""

    class Direccion(models.TextChoices):
        SALIDA = "salida", "Sale del ERP"
        ENTRADA = "entrada", "Entra al ERP"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        ENVIADO = "enviado", "Enviado"
        EXITO = "exito", "Con respuesta correcta"
        ERROR = "error", "Con error"
        DESCARTADO = "descartado", "Descartado tras agotar reintentos"

    servicio = models.ForeignKey(
        ServicioExterno, on_delete=models.PROTECT, related_name="registros"
    )
    direccion = models.CharField(max_length=8, choices=Direccion, db_index=True)
    operacion = models.CharField(max_length=80, help_text="emitir_comprobante, crear_contacto…")

    entidad = models.CharField(max_length=60, blank=True)
    objeto_id = models.CharField(max_length=50, blank=True, db_index=True)
    id_externo = models.CharField(max_length=120, blank=True, db_index=True)

    contenido_enviado = models.JSONField(default=dict, blank=True)
    contenido_recibido = models.JSONField(default=dict, blank=True)
    codigo_http = models.PositiveSmallIntegerField(null=True, blank=True)

    estado = models.CharField(
        max_length=12, choices=Estado, default=Estado.PENDIENTE, db_index=True
    )
    intentos = models.PositiveSmallIntegerField(default=0)
    mensaje_error = models.TextField(blank=True)
    duracion_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "registro de integración"
        verbose_name_plural = "registros de integración"
        ordering = ("-creado_en",)
        indexes = [models.Index(fields=("servicio", "estado"))]

    def __str__(self):
        return f"{self.operacion} → {self.servicio} ({self.estado})"


class TrabajoIntegracion(ModeloEmpresa):
    """Cola de trabajos: nunca se llama al servicio externo mientras el usuario espera.

    La `llave_idempotencia` evita que el mismo envío se procese dos veces, y el
    reintento sigue los tiempos del diseño: 1, 5 y 15 minutos.
    """

    ESPERAS_MINUTOS = (1, 5, 15)
    MAX_INTENTOS = 3

    class Estado(models.TextChoices):
        EN_COLA = "en_cola", "En cola"
        EN_PROCESO = "en_proceso", "En proceso"
        HECHO = "hecho", "Hecho"
        REINTENTAR = "reintentar", "Esperando reintento"
        FALLIDO = "fallido", "En la cola de errores"

    servicio = models.ForeignKey(
        ServicioExterno, on_delete=models.PROTECT, related_name="trabajos"
    )
    operacion = models.CharField(max_length=80, db_index=True)
    carga = models.JSONField(default=dict, blank=True)

    entidad = models.CharField(max_length=60, blank=True)
    objeto_id = models.CharField(max_length=50, blank=True, db_index=True)

    llave_idempotencia = models.CharField(max_length=120, db_index=True)
    estado = models.CharField(
        max_length=12, choices=Estado, default=Estado.EN_COLA, db_index=True
    )
    intentos = models.PositiveSmallIntegerField(default=0)
    ejecutar_despues_de = models.DateTimeField(default=timezone.now, db_index=True)
    ultimo_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "trabajo de integración"
        verbose_name_plural = "trabajos de integración"
        ordering = ("ejecutar_despues_de",)
        constraints = [
            models.UniqueConstraint(
                fields=("servicio", "llave_idempotencia"), name="trabajo_idempotente"
            )
        ]

    def __str__(self):
        return f"{self.operacion} #{self.objeto_id} ({self.estado})"

    def programar_reintento(self, error=""):
        """Devuelve el trabajo a la cola, o lo manda a la cola de errores al tercer fallo."""
        self.intentos += 1
        self.ultimo_error = error
        if self.intentos >= self.MAX_INTENTOS:
            self.estado = self.Estado.FALLIDO
        else:
            espera = self.ESPERAS_MINUTOS[min(self.intentos - 1, len(self.ESPERAS_MINUTOS) - 1)]
            self.estado = self.Estado.REINTENTAR
            self.ejecutar_despues_de = timezone.now() + timezone.timedelta(minutes=espera)
        self.save(
            update_fields=[
                "intentos",
                "ultimo_error",
                "estado",
                "ejecutar_despues_de",
                "actualizado_en",
            ]
        )
        return self.estado


class EventoWebhook(ModeloEmpresa):
    """Lo que llega de afuera, guardado crudo antes de procesarlo.

    Se guarda primero y se procesa después: si el proceso falla, el evento no se
    pierde y se puede reprocesar.
    """

    servicio = models.ForeignKey(
        ServicioExterno, on_delete=models.PROTECT, related_name="webhooks"
    )
    evento = models.CharField(max_length=80, db_index=True)
    id_evento_externo = models.CharField(max_length=140, blank=True, db_index=True)
    cuerpo = models.JSONField(default=dict, blank=True)
    cabeceras = models.JSONField(default=dict, blank=True)
    firma_valida = models.BooleanField(default=False)
    procesado = models.BooleanField(default=False, db_index=True)
    mensaje_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "evento de webhook"
        verbose_name_plural = "eventos de webhook"
        ordering = ("-creado_en",)
        constraints = [
            # La unicidad cubre solo los eventos con firma válida: los que llegan
            # sin firmar se guardan todos, para poder investigar quién los mandó,
            # y no deben poder ocupar el id de un aviso legítimo posterior.
            models.UniqueConstraint(
                fields=("servicio", "id_evento_externo"),
                condition=~models.Q(id_evento_externo="") & models.Q(firma_valida=True),
                name="webhook_no_duplicado",
            )
        ]

    def __str__(self):
        return f"{self.evento} de {self.servicio}"
