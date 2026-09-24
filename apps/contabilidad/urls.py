from django.urls import path

from apps.contabilidad import vistas

app_name = "contabilidad"

urlpatterns = [
    path("", vistas.asientos, name="asientos"),
    path("asiento/<int:pk>/", vistas.asiento, name="asiento"),
    path("balance/", vistas.balance, name="balance"),
]
