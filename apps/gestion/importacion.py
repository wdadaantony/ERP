"""Carga de nuevos productos con validación completa antes de escribir."""
import csv
import io
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from apps.catalogo.models import Producto, UnidadMedida
from apps.core.transacciones import operacion_serializada
from apps.core.auditoria import registrar


def analizar_productos(empresa, contenido):
    if len(contenido) > 2 * 1024 * 1024:
        raise ValidationError('El CSV no puede superar 2 MB.')
    try:
        lector = csv.DictReader(io.StringIO(contenido.decode('utf-8-sig')))
        requeridas = {'codigo', 'nombre', 'unidad', 'tipo', 'precio_lista', 'stock_minimo'}
        if not lector.fieldnames or set(lector.fieldnames) != requeridas:
            raise ValidationError('Columnas requeridas: codigo,nombre,unidad,tipo,precio_lista,stock_minimo')
        unidades = {u.codigo: u for u in UnidadMedida.objects.filter(empresa=empresa)}
        existentes = set(Producto.objects.filter(empresa=empresa).values_list('codigo', flat=True))
        filas, vistos = [], set()
        for numero, fila in enumerate(lector, 2):
            if numero > 1001:
                raise ValidationError('Máximo 1000 productos por archivo.')
            if None in fila or any(v is None for v in fila.values()):
                raise ValidationError(f'Fila {numero}: número de columnas incorrecto.')
            datos = {k: v.strip() for k, v in fila.items()}
            codigo = datos['codigo']
            if not codigo or codigo in vistos or codigo in existentes:
                raise ValidationError(f'Fila {numero}: código vacío o duplicado ({codigo}). No se sobrescriben productos.')
            if datos['unidad'] not in unidades:
                raise ValidationError(f'Fila {numero}: la unidad no está configurada en esta empresa.')
            producto = Producto(empresa=empresa, codigo=codigo, nombre=datos['nombre'], tipo=datos['tipo'],
                unidad_medida=unidades[datos['unidad']], precio_lista=Decimal(datos['precio_lista']),
                stock_minimo=Decimal(datos['stock_minimo']), controla_stock=datos['tipo'] != 'servicio')
            if not producto.precio_lista.is_finite() or not producto.stock_minimo.is_finite() or producto.precio_lista < 0 or producto.stock_minimo < 0:
                raise ValidationError(f'Fila {numero}: importes no válidos.')
            producto.full_clean()
            filas.append(producto)
            vistos.add(codigo)
        if not filas:
            raise ValidationError('El CSV está vacío.')
        return filas
    except (UnicodeDecodeError, csv.Error, InvalidOperation):
        raise ValidationError('CSV inválido: utiliza UTF-8, comas y punto decimal.')


@operacion_serializada
def importar_productos(empresa, contenido, usuario):
    filas = analizar_productos(empresa, contenido)
    for producto in filas:
        producto.save()
        registrar(producto, usuario, 'crear', despues={'codigo': producto.codigo, 'origen': 'importacion_csv'})
    return len(filas)
