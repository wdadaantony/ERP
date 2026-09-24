"""CRM: leads, embudo y actividades.

El lead es la puerta de entrada del proceso. Trae el origen y las UTM de la
campaña que lo generó, para que cuando se convierta en pedido y luego en venta
real, esa conversión pueda volver a la plataforma de anuncios con el valor
verdadero (paso 17 del diagrama de carriles).
"""
from django.conf import settings
from django.db import models

from apps.core.models import ModeloEmpresa


class EstadoLead(models.TextChoices):
    NUEVO = "nuevo", "Nuevo"
    CONTACTADO = "contactado", "Contactado"
    CALIFICADO = "calificado", "Calificado"
    PROPUESTA = "propuesta", "Con propuesta"
    GANADO = "ganado", "Ganado"
    PERDIDO = "perdido", "Perdido"


#: Columnas del embudo, en orden. Ganado y perdido son los cierres.
ETAPAS_EMBUDO = (
    EstadoLead.NUEVO,
    EstadoLead.CONTACTADO,
    EstadoLead.CALIFICADO,
    EstadoLead.PROPUESTA,
    EstadoLead.GANADO,
)


class Prioridad(models.IntegerChoices):
    SIN_PRIORIDAD = 0, "Sin prioridad"
    BAJA = 1, "Baja"
    MEDIA = 2, "Media"
    ALTA = 3, "Alta"


class Lead(ModeloEmpresa):
    nombre = models.CharField(max_length=150, help_text="Persona o empresa que dejó sus datos")
    empresa_lead = models.CharField("empresa del contacto", max_length=150, blank=True)
    email = models.EmailField(blank=True, db_index=True)
    telefono = models.CharField(max_length=30, blank=True, db_index=True)
    movil = models.CharField("móvil", max_length=30, blank=True, help_text="Número celular, si es distinto del de contacto")
    numero_documento = models.CharField(max_length=15, blank=True, db_index=True)

    direccion = models.CharField(max_length=255, blank=True)
    ciudad = models.CharField(max_length=100, blank=True)
    region = models.CharField("estado / región", max_length=100, blank=True)
    pais = models.CharField(max_length=100, blank=True)
    idioma = models.CharField(max_length=40, blank=True, help_text="Idioma del cliente, ej. Español")

    estado = models.CharField(
        max_length=12, choices=EstadoLead, default=EstadoLead.NUEVO, db_index=True
    )
    vendedor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    equipo_ventas = models.CharField(max_length=100, blank=True, db_index=True)
    prioridad = models.PositiveSmallIntegerField(choices=Prioridad, default=Prioridad.SIN_PRIORIDAD)
    etiquetas = models.JSONField(default=list, blank=True, help_text="Lista de etiquetas propias, ej. [\"cliente caliente\"]")

    valor_estimado = models.DecimalField("ingreso esperado", max_digits=14, decimal_places=2, default=0)
    probabilidad = models.PositiveSmallIntegerField(
        default=0, help_text="Probabilidad estimada de cierre, de 0 a 100"
    )
    cierre_esperado = models.DateField(null=True, blank=True, help_text="Fecha prevista de la venta")

    origen = models.CharField(
        max_length=60, blank=True, db_index=True, help_text="meta_ads, google_ads, web, whatsapp, referido…"
    )
    campania = models.CharField(max_length=120, blank=True)
    medio = models.CharField(max_length=60, blank=True, help_text="Facebook, Google, email, etc.")
    referido_por = models.CharField(max_length=150, blank=True, help_text="Persona o canal que originó el lead")
    utm = models.JSONField(default=dict, blank=True)
    id_externo = models.CharField(max_length=120, blank=True, db_index=True)

    interes = models.TextField(blank=True, help_text="Qué quiere, en sus palabras")
    notas_internas = models.TextField(blank=True, help_text="Comentarios del vendedor, no visibles para el cliente")
    motivo_perdida = models.CharField(max_length=120, blank=True)

    tercero = models.ForeignKey(
        "terceros.Tercero", on_delete=models.SET_NULL, null=True, blank=True, related_name="leads"
    )
    pedido = models.ForeignKey(
        "ventas.Pedido", on_delete=models.SET_NULL, null=True, blank=True, related_name="leads"
    )
    conversion_enviada = models.BooleanField(
        default=False, help_text="Ya se avisó a la plataforma de anuncios con el valor real"
    )

    class Meta:
        verbose_name = "lead"
        verbose_name_plural = "leads"
        ordering = ("-creado_en",)
        permissions = [
            ("ver_todo", "Puede ver los leads de todos los vendedores"),
            ("recibir_leads", "Entra en el reparto automático de leads"),
        ]

    def __str__(self):
        return self.nombre

    @property
    def esta_abierto(self):
        return self.estado not in (EstadoLead.GANADO, EstadoLead.PERDIDO)


class Actividad(ModeloEmpresa):
    """Llamada, correo, reunión o nota sobre un lead."""

    class Tipo(models.TextChoices):
        LLAMADA = "llamada", "Llamada"
        WHATSAPP = "whatsapp", "WhatsApp"
        CORREO = "correo", "Correo"
        REUNION = "reunion", "Reunión"
        NOTA = "nota", "Nota"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="actividades")
    tipo = models.CharField(max_length=10, choices=Tipo, default=Tipo.NOTA)
    detalle = models.TextField()
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    programada_para = models.DateTimeField(
        null=True, blank=True, help_text="Para agendar el próximo contacto: «llamar el jueves»"
    )
    hecha = models.BooleanField(default=True)
    es_entrante = models.BooleanField(
        default=False, help_text="Lo escribió el cliente, no nosotros"
    )

    class Meta:
        verbose_name = "actividad"
        verbose_name_plural = "actividades"
        ordering = ("-creado_en",)

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.lead}"


class ArchivoLead(ModeloEmpresa):
    """Contrato, PDF, imagen u otro adjunto de un lead."""

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="archivos")
    archivo = models.FileField(upload_to="crm/archivos/%Y/%m/")
    nombre = models.CharField(max_length=150, blank=True)
    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "archivo del lead"
        verbose_name_plural = "archivos del lead"
        ordering = ("-creado_en",)

    def __str__(self):
        return self.nombre or self.archivo.name


class FiltroGuardado(ModeloEmpresa):
    """Una búsqueda que el propio usuario guardó para repetirla con un clic.

    Guarda la query string tal cual (``vendedor=3&prioridad=3``), así que
    cualquier combinación de filtros que la vista sepa leer queda reutilizable
    sin tocar código: es lo que hace configurable el filtrado por el usuario.
    """

    class Vista(models.TextChoices):
        LISTA = "lista", "Lista"
        EMBUDO = "embudo", "Embudo"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="filtros_crm"
    )
    vista = models.CharField(max_length=10, choices=Vista, default=Vista.LISTA)
    nombre = models.CharField(max_length=80)
    querystring = models.CharField(max_length=500, blank=True)

    class Meta:
        verbose_name = "filtro guardado"
        verbose_name_plural = "filtros guardados"
        ordering = ("nombre",)

    def __str__(self):
        return self.nombre
