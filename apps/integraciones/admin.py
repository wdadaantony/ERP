from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin
from django.utils import timezone

from apps.integraciones.models import (
    EventoWebhook,
    MapeoCampo,
    RegistroIntegracion,
    ServicioExterno,
    TrabajoIntegracion,
)


class MapeoCampoInline(admin.TabularInline):
    model = MapeoCampo
    extra = 0


@admin.register(ServicioExterno)
class ServicioExternoAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    list_display = ("nombre", "codigo", "proveedor", "modo_simulado", "activo", "empresa")
    list_filter = ("empresa", "codigo", "modo_simulado", "activo")
    inlines = (MapeoCampoInline,)


@admin.register(RegistroIntegracion)
class RegistroIntegracionAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    """Bitácora de solo lectura: es la prueba de qué se mandó y a dónde."""

    list_display = (
        "creado_en",
        "servicio",
        "direccion",
        "operacion",
        "objeto_id",
        "estado",
        "codigo_http",
        "duracion_ms",
    )
    list_filter = ("empresa", "servicio", "direccion", "estado")
    search_fields = ("operacion", "objeto_id", "id_externo")
    date_hierarchy = "creado_en"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TrabajoIntegracion)
class TrabajoIntegracionAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = (
        "operacion",
        "servicio",
        "objeto_id",
        "estado",
        "intentos",
        "ejecutar_despues_de",
    )
    list_filter = ("empresa", "servicio", "estado")
    search_fields = ("operacion", "objeto_id", "llave_idempotencia")

    @admin.action(description="Reencolar para ejecutar ahora")
    def reencolar(self, request, queryset):
        actualizados = queryset.update(
            estado=TrabajoIntegracion.Estado.EN_COLA,
            intentos=0,
            ejecutar_despues_de=timezone.now(),
        )
        self.message_user(request, f"{actualizados} trabajo(s) reencolado(s).")

    actions = ("reencolar",)


@admin.register(EventoWebhook)
class EventoWebhookAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("creado_en", "servicio", "evento", "firma_valida", "procesado")
    list_filter = ("empresa", "servicio", "procesado", "firma_valida")
    search_fields = ("evento", "id_evento_externo")
    date_hierarchy = "creado_en"
