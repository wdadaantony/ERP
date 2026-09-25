"""Indicadores del dashboard con alcance de empresa y permisos del usuario."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum

from apps.core.monedas import a_moneda_base


def indicadores(request, comprobantes, pedidos, hoy):
    datos = {
        "serie_facturacion": [],
        "distribucion_pedidos": [],
        "saldo_proveedores": None,
        "proveedores_vencidos": 0,
        "pagos_proximos": [],
        "cantidad_proveedores": 0,
        "margen_estimado": None,
        "margen_estimado_pct": None,
        "ventas_pendientes_entrega": 0,
        "cotizaciones_por_vencer": [],
        "dias_cobranza_promedio": None,
        "alertas_ejecutivas": [],
    }
    if request.user.has_perm("facturacion.view_comprobante"):
        meses = [hoy.replace(day=1)]
        for _ in range(5):
            meses.insert(0, (meses[0] - timedelta(days=1)).replace(day=1))
        totales = {mes: Decimal("0") for mes in meses}
        ingreso, costo = Decimal("0"), Decimal("0")
        dias_cobranza, docs_cobrados = 0, 0
        for c in comprobantes.filter(fecha_emision__gte=meses[0], fecha_emision__lte=hoy).select_related("empresa").prefetch_related("lineas__producto"):
            total_base = a_moneda_base(c.total, c)
            totales[c.fecha_emision.replace(day=1)] += total_base
            ingreso += total_base
            for linea in c.lineas.all():
                costo += a_moneda_base(linea.producto.costo_promedio * linea.cantidad, c)
            if c.total_cobrado >= c.importe_vigente and c.fecha_vencimiento:
                dias_cobranza += max((c.fecha_vencimiento - c.fecha_emision).days, 0)
                docs_cobrados += 1
        maximo = max(totales.values()) or Decimal("1")
        datos["serie_facturacion"] = [
            dict(mes=mes, total=total, altura=int(total / maximo * 100))
            for mes, total in totales.items()
        ]
        if ingreso:
            datos["margen_estimado"] = ingreso - costo
            datos["margen_estimado_pct"] = (ingreso - costo) / ingreso * 100
        if docs_cobrados:
            datos["dias_cobranza_promedio"] = round(dias_cobranza / docs_cobrados)
    if request.user.has_perm("ventas.view_pedido"):
        from apps.ventas.models import EstadoPedido

        filas = list(pedidos.values("estado").annotate(n=Count("pk")).order_by("-n"))
        total = sum(f["n"] for f in filas)
        nombres = dict(EstadoPedido.choices)
        datos["distribucion_pedidos"] = [
            dict(nombre=nombres[f["estado"]], cantidad=f["n"], ancho=round(f["n"] / total * 100))
            for f in filas
        ] if total else []
        datos["ventas_pendientes_entrega"] = pedidos.filter(
            estado__in=(EstadoPedido.CONFIRMADO, EstadoPedido.RESERVADO, EstadoPedido.EN_ESPERA)
        ).count()
        limite = hoy + timedelta(days=7)
        datos["cotizaciones_por_vencer"] = pedidos.filter(
            estado=EstadoPedido.ENVIADA,
            valido_hasta__gte=hoy,
            valido_hasta__lte=limite,
        ).select_related("tercero").order_by("valido_hasta")[:5]
    if request.user.has_perm("gestion.view_facturaproveedor"):
        from apps.gestion.models import FacturaProveedor

        facturas = FacturaProveedor.objects.filter(
            empresa=request.empresa,
            estado="aprobada",
        ).select_related("empresa", "proveedor").annotate(abonado=Sum("pagos__movimiento__monto"))
        saldo = Decimal("0")
        for f in facturas:
            pendiente = f.total - (f.abonado or Decimal("0"))
            if pendiente <= 0:
                continue
            saldo += a_moneda_base(pendiente, f)
            datos["cantidad_proveedores"] += 1
            datos["proveedores_vencidos"] += int(f.vencimiento < hoy)
            if len(datos["pagos_proximos"]) < 5:
                datos["pagos_proximos"].append(dict(factura=f, saldo=pendiente, vencida=f.vencimiento < hoy))
        datos["saldo_proveedores"] = saldo
    if datos["proveedores_vencidos"]:
        datos["alertas_ejecutivas"].append(f"{datos['proveedores_vencidos']} proveedor(es) vencido(s)")
    if datos["ventas_pendientes_entrega"]:
        datos["alertas_ejecutivas"].append(f"{datos['ventas_pendientes_entrega']} venta(s) esperando entrega o stock")
    if datos["cotizaciones_por_vencer"]:
        datos["alertas_ejecutivas"].append(f"{len(datos['cotizaciones_por_vencer'])} cotizacion(es) vencen en 7 dias")
    return datos
