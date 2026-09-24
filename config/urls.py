"""Rutas del ERP."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "ERP — Administración"
admin.site.site_title = "ERP"
admin.site.index_title = "Datos maestros y configuración"

urlpatterns = [
    path('gestion/', include('apps.gestion.urls')),
    path("", include("apps.core.urls")),
    path("cuenta/", include("apps.core.urls_cuenta")),
    path("crm/", include("apps.crm.urls")),
    path("ventas/", include("apps.ventas.urls")),
    path("terceros/", include("apps.terceros.urls")),
    path("productos/", include("apps.catalogo.urls")),
    path("inventario/", include("apps.inventario.urls")),
    path("compras/", include("apps.compras.urls")),
    path("comprobantes/", include("apps.facturacion.urls")),
    path("cobranza/", include("apps.tesoreria.urls")),
    path("contabilidad/", include("apps.contabilidad.urls")),
    path("integraciones/", include("apps.integraciones.urls")),
    path("admin/", admin.site.urls),
]

# Los documentos privados se sirven por core:archivo, incluso en desarrollo.
