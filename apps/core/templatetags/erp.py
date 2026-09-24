"""Filtros de plantilla del ERP."""
from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def puede(usuario, ruta):
    from apps.core.permisos import RUTAS
    permisos = RUTAS.get(ruta)
    return permisos is not None and usuario.has_perms(permisos)


@register.filter
def get_item(diccionario, clave):
    try:
        return diccionario.get(clave, "")
    except AttributeError:
        return ""


@register.filter
def dinero(valor, moneda="PEN"):
    """S/ 1,234.50 — con separador de miles y dos decimales."""
    if valor is None or valor == "":
        return "—"
    simbolo = {"PEN": "S/", "USD": "US$", "EUR": "€"}.get(moneda, moneda)
    try:
        numero = Decimal(str(valor))
    except Exception:  # noqa: BLE001
        return valor
    return f"{simbolo} {numero:,.2f}"


@register.filter
def cantidad(valor):
    """Cantidades sin ceros de más: 3, 2.5, 0.125."""
    try:
        numero = Decimal(str(valor)).normalize()
    except Exception:  # noqa: BLE001
        return valor
    texto = f"{numero:f}"
    return texto if "." not in texto else texto.rstrip("0").rstrip(".")
