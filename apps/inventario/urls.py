from django.urls import path

from apps.inventario import vistas

app_name = "inventario"

urlpatterns = [
    path("", vistas.stock, name="stock"),
    path("movimientos/", vistas.movimientos, name="movimientos"),
    path("guias/", vistas.guias, name="guias"),
]
