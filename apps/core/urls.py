from django.urls import path

from apps.core import vistas
from apps.core.archivos import descargar
from apps.core.salud import salud

app_name = "core"

urlpatterns = [
    path('salud/', salud, name='salud'),
    path('archivo/<str:tipo>/<int:pk>/', descargar, name='archivo'),
    path("", vistas.inicio, name="inicio"),
    path("empresa/cambiar/", vistas.cambiar_empresa, name="cambiar_empresa"),
    path("empresa/ninguna/", vistas.sin_empresa, name="sin_empresa"),
]
