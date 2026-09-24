"""Política única de acceso a cada ruta operativa. Rutas nuevas: denegar."""
from django.core.exceptions import PermissionDenied
from django.utils.deprecation import MiddlewareMixin

RUTAS = {}


def registrar(namespace, nombres, *permisos):
    for nombre in nombres.split():
        RUTAS[f"{namespace}:{nombre}"] = permisos


registrar('gestion', 'proveedores factura', 'gestion.view_facturaproveedor')
registrar('gestion', 'nueva_factura', 'gestion.add_facturaproveedor')
registrar('gestion', 'aprobar', 'gestion.view_facturaproveedor', 'gestion.aprobar_factura')
registrar('gestion', 'pagar', 'gestion.view_facturaproveedor', 'gestion.pagar_factura')
registrar('gestion', 'inventario', 'gestion.view_operacioninventario')
registrar('gestion', 'nueva_operacion', 'gestion.add_operacioninventario')
registrar('gestion', 'aprobar_operacion', 'gestion.aprobar_inventario', 'gestion.view_operacioninventario')
registrar('gestion', 'cierre', 'contabilidad.change_periodocontable')
registrar('gestion', 'despacho', 'ventas.view_pedido', 'inventario.add_movimientostock')
registrar('gestion', 'preparacion', 'gestion.view_perfilempresa')
registrar('gestion', 'importar', 'catalogo.add_producto', 'catalogo.view_producto')
registrar('gestion', 'cotizacion', 'ventas.view_pedido')
registrar('gestion', 'ajuste', 'contabilidad.add_asiento')
registrar('gestion', 'flujo_caja', 'tesoreria.view_movimiento', 'contabilidad.view_asiento')
registrar('gestion', 'agenda', 'crm.view_lead')
registrar('gestion', 'completar_actividad', 'crm.view_lead', 'crm.change_actividad')
registrar('gestion', 'reportes', 'contabilidad.view_asiento', 'facturacion.view_comprobante', 'ventas.ver_todo')


registrar("ventas", "lista detalle", "ventas.view_pedido")
registrar("ventas", "nuevo", "ventas.add_pedido")
registrar("ventas", "editar enviar confirmar cancelar reintentar", "ventas.change_pedido")
registrar("ventas", "despachar", "ventas.view_pedido", "inventario.add_movimientostock")
registrar("ventas", "guia", "ventas.view_pedido", "inventario.add_guiaremision")
registrar("ventas", "facturar", "ventas.view_pedido", "facturacion.add_comprobante")
registrar("ventas", "comprar_faltante", "ventas.view_pedido", "compras.add_ordencompra")
registrar("ventas", "buscar_productos", "catalogo.view_producto")
registrar("compras", "lista detalle", "compras.view_ordencompra")
registrar("compras", "nueva", "compras.add_ordencompra")
registrar("compras", "editar enviar", "compras.change_ordencompra")
registrar("compras", "aprobar", "compras.aprobar_ordencompra")
registrar("compras", "recibir", "compras.view_ordencompra", "compras.add_recepcion")
registrar("catalogo", "lista", "catalogo.view_producto")
registrar("catalogo", "nuevo", "catalogo.add_producto")
registrar("catalogo", "editar", "catalogo.change_producto")
registrar("terceros", "lista detalle", "terceros.view_tercero")
registrar("terceros", "nuevo", "terceros.add_tercero")
registrar("terceros", "editar", "terceros.change_tercero")
registrar("crm", "embudo lista detalle", "crm.view_lead")
registrar("crm", "nuevo", "crm.add_lead")
registrar("crm", "editar mover cerrar", "crm.change_lead")
registrar("crm", "a_cliente", "crm.change_lead", "terceros.add_tercero")
registrar("crm", "a_pedido", "crm.change_lead", "terceros.add_tercero", "ventas.add_pedido")
registrar("crm", "subir_archivo", "crm.change_lead")
registrar("crm", "guardar_filtro eliminar_filtro", "crm.view_lead")
registrar("inventario", "stock movimientos", "inventario.view_movimientostock")
registrar("inventario", "guias", "inventario.view_guiaremision")
registrar("facturacion", "lista detalle", "facturacion.view_comprobante")
registrar("facturacion", "nota_credito", "facturacion.view_comprobante", "facturacion.add_comprobante")
registrar("facturacion", "reenviar", "facturacion.change_comprobante")
registrar("tesoreria", "cobranza movimientos", "tesoreria.view_movimiento")
registrar("tesoreria", "cobrar", "tesoreria.add_movimiento", "facturacion.view_comprobante")
registrar("tesoreria", "extornar", "tesoreria.change_movimiento", "facturacion.view_comprobante")
registrar("tesoreria", "conciliacion", "tesoreria.view_lineaextractobancario")
registrar("contabilidad", "asientos asiento balance", "contabilidad.view_asiento")
registrar("integraciones", "panel registros", "integraciones.view_registrointegracion")
registrar("integraciones", "procesar_ahora reencolar", "integraciones.change_trabajointegracion")


class PermisosERPMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        ns, nombre = match.namespace, match.url_name
        if ns in ("admin", "cuenta", "core") or match.view_name == "integraciones:webhook":
            return None
        if not request.user.is_authenticated:
            return None  # Las vistas redirigen al inicio de sesión.
        if request.empresa is None or not request.empresa.activa:
            raise PermissionDenied("No tienes una empresa activa.")
        requeridos = RUTAS.get(match.view_name)
        if requeridos is None:
            raise PermissionDenied("Esta operación todavía no tiene permisos configurados.")
        extras = []
        if request.method == "POST" and ns == "crm" and nombre == "detalle":
            extras = ["crm.add_actividad", "crm.change_lead"]
        if request.method == "POST" and ns == "tesoreria" and nombre == "conciliacion":
            extras = ["tesoreria.change_lineaextractobancario"]
            if "importar" in request.POST:
                extras.append("tesoreria.add_lineaextractobancario")
        if not request.user.has_perms([*requeridos, *extras]):
            raise PermissionDenied("Tu rol no permite esta operación.")


def crear_roles():
    """Idempotente; no asigna privilegios a usuarios automáticamente."""
    from django.contrib.auth.models import Group, Permission

    lectura = {p for permisos in RUTAS.values() for p in permisos if ".view_" in p}
    comunes = {"catalogo.view_producto", "terceros.view_tercero"}
    ventas = comunes | {"ventas.view_pedido", "ventas.add_pedido", "ventas.change_pedido",
        "terceros.add_tercero", "terceros.change_tercero", "crm.view_lead", "crm.add_lead",
        "crm.change_lead", "crm.add_actividad", "inventario.view_movimientostock"}
    caja = comunes | {"ventas.view_pedido", "ventas.ver_todo", "terceros.ver_todo",
        "facturacion.view_comprobante", "facturacion.add_comprobante", "facturacion.change_comprobante",
        "tesoreria.view_movimiento", "tesoreria.add_movimiento", "tesoreria.change_movimiento"}
    almacen = comunes | {"ventas.view_pedido", "ventas.ver_todo", "compras.view_ordencompra",
        "compras.add_recepcion", "inventario.view_movimientostock", "inventario.add_movimientostock",
        "inventario.view_guiaremision", "inventario.add_guiaremision"}
    compras = comunes | {"terceros.ver_todo", "terceros.add_tercero", "terceros.change_tercero",
        "compras.view_ordencompra", "compras.add_ordencompra", "compras.change_ordencompra",
        "inventario.view_movimientostock", "ventas.view_pedido", "ventas.ver_todo"}
    contabilidad = comunes | {"contabilidad.view_asiento", "contabilidad.view_cuentacontable",
        "contabilidad.view_periodocontable", "facturacion.view_comprobante", "ventas.ver_todo",
        "tesoreria.view_movimiento", "tesoreria.view_lineaextractobancario",
        "tesoreria.add_lineaextractobancario", "tesoreria.change_lineaextractobancario"}
    ventas.add('crm.change_actividad')
    almacen.update({'gestion.view_operacioninventario', 'gestion.add_operacioninventario'})
    compras.update({'gestion.view_facturaproveedor', 'gestion.add_facturaproveedor'})
    caja.update({'gestion.view_facturaproveedor', 'gestion.pagar_factura'})
    contabilidad.update({'gestion.view_facturaproveedor', 'gestion.aprobar_factura', 'contabilidad.change_periodocontable', 'contabilidad.add_asiento'})
    gerencia = set().union(ventas, caja, almacen, compras, contabilidad, lectura,
        {"compras.aprobar_ordencompra", "crm.ver_todo", "catalogo.add_producto", "catalogo.change_producto", 'gestion.aprobar_inventario'})
    roles = {"Ventas": ventas, "Almacén": almacen, "Caja": caja, "Compras": compras,
        "Contabilidad": contabilidad, "Gerencia": gerencia,
        "Consulta": lectura - {"integraciones.view_registrointegracion"} | {
            "ventas.ver_todo", "terceros.ver_todo", "crm.ver_todo"}}
    existentes = {f"{p.content_type.app_label}.{p.codename}": p
        for p in Permission.objects.select_related("content_type").order_by("pk")}
    for nombre, permisos in roles.items():
        grupo, _ = Group.objects.get_or_create(name=f"ERP · {nombre}")
        grupo.permissions.set([existentes[p] for p in permisos])
    return roles
