from django.urls import path

from apps.ventas import vistas

app_name = "ventas"

urlpatterns = [
    path("", vistas.lista, name="lista"),
    path("nuevo/", vistas.editar, name="nuevo"),
    path("<int:pk>/", vistas.detalle, name="detalle"),
    path("<int:pk>/editar/", vistas.editar, name="editar"),
    path("<int:pk>/enviar/", vistas.enviar, name="enviar"),
    path("<int:pk>/confirmar/", vistas.confirmar, name="confirmar"),
    path("<int:pk>/reintentar/", vistas.reintentar, name="reintentar"),
    path("<int:pk>/despachar/", vistas.despachar, name="despachar"),
    path("<int:pk>/facturar/", vistas.facturar, name="facturar"),
    path("<int:pk>/cancelar/", vistas.cancelar, name="cancelar"),
    path("<int:pk>/comprar-faltante/", vistas.comprar_faltante, name="comprar_faltante"),
    path("<int:pk>/guia/", vistas.guia, name="guia"),
    path("productos/buscar/", vistas.buscar_productos, name="buscar_productos"),
]
