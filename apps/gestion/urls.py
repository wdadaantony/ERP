from django.urls import path
from . import vistas
from .documentos import cotizacion
from .contabilidad import ajuste, flujo_caja
app_name = 'gestion'
urlpatterns = [
    path('ajuste/', ajuste, name='ajuste'),
    path('flujo-caja/', flujo_caja, name='flujo_caja'),
    path('importar/', vistas.importar, name='importar'),
    path('cotizacion/<int:pk>/', cotizacion, name='cotizacion'),
    path('preparacion/', vistas.preparacion, name='preparacion'),
    path('despacho/<int:pk>/', vistas.despacho, name='despacho'),
    path('proveedores/', vistas.proveedores, name='proveedores'),
    path('proveedores/nueva/', vistas.nueva_factura, name='nueva_factura'),
    path('proveedores/<int:pk>/', vistas.factura, name='factura'),
    path('proveedores/<int:pk>/aprobar/', vistas.aprobar, name='aprobar'),
    path('proveedores/<int:pk>/pagar/', vistas.pagar, name='pagar'),
    path('inventario/', vistas.inventario, name='inventario'),
    path('inventario/nueva/', vistas.nueva_operacion, name='nueva_operacion'),
    path('inventario/<int:pk>/aprobar/', vistas.aprobar_operacion, name='aprobar_operacion'),
    path('cierre/', vistas.cierre, name='cierre'),
    path('agenda/', vistas.agenda, name='agenda'),
    path('agenda/<int:pk>/completar/', vistas.completar_actividad, name='completar_actividad'),
    path('reportes/', vistas.reportes, name='reportes'),
]
