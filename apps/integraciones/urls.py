from django.urls import path

from apps.integraciones import vistas, vistas_panel

app_name = "integraciones"

urlpatterns = [
    path("webhook/<int:servicio_id>/", vistas.recibir, name="webhook"),
    path("", vistas_panel.panel, name="panel"),
    path("registros/", vistas_panel.registros, name="registros"),
    path("trabajo/<int:pk>/reencolar/", vistas_panel.reencolar, name="reencolar"),
    path("procesar/", vistas_panel.procesar_ahora, name="procesar_ahora"),
]
