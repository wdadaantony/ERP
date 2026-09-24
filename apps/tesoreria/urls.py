from django.urls import path

from apps.tesoreria import vistas

app_name = "tesoreria"

urlpatterns = [
    path("", vistas.cobranza, name="cobranza"),
    path("cobrar/<int:pk>/", vistas.cobrar, name="cobrar"),
    path("movimientos/<int:pk>/extornar/", vistas.extornar, name="extornar"),
    path("movimientos/", vistas.movimientos, name="movimientos"),
    path("conciliacion/", vistas.conciliacion, name="conciliacion"),
]
