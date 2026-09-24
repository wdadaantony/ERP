"""Importes: centavos, tipo de cambio expresado en moneda base por unidad."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN

from django.core.exceptions import ValidationError


def decimal_finito(valor):
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Introduce un importe numérico válido.")
    if not numero.is_finite():
        raise ValidationError("El importe debe ser un número finito.")
    return numero


def redondear(valor):
    try:
        return decimal_finito(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValidationError("El importe excede la precisión admitida.")


def repartir_importe(total, pesos):
    """Reparte centavos por restos mayores sin perder ni crear un centavo."""
    total = redondear(total)
    suma = sum(pesos, Decimal("0"))
    if not suma:
        if total:
            raise ValidationError("No hay una base válida para repartir el importe.")
        return [Decimal("0.00") for _ in pesos]
    exactos = [total * peso / suma for peso in pesos]
    partes = [x.quantize(Decimal("0.01"), rounding=ROUND_DOWN) for x in exactos]
    centavos = int((total - sum(partes)) * 100)
    orden = sorted(range(len(pesos)), key=lambda i: exactos[i] - partes[i], reverse=True)
    for i in orden[:centavos]:
        partes[i] += Decimal("0.01")
    return partes


def factor_cambio(moneda, empresa, tasa):
    if moneda == empresa.moneda_base:
        return Decimal("1")
    factor = decimal_finito(tasa)
    if factor <= 0:
        raise ValidationError("El tipo de cambio debe ser mayor que cero.")
    return factor


def a_moneda_base(importe, documento):
    return redondear(decimal_finito(importe) * factor_cambio(
        documento.moneda, documento.empresa, documento.tipo_cambio
    ))
