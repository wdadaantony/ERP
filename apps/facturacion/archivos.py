"""Contrato de recepción de documentos de un adaptador confiable del OSE."""
import base64
import binascii
import hashlib
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile


def guardar_archivos(comprobante, archivos):
    if not archivos:
        return
    if not isinstance(archivos, dict):
        raise ValidationError('Los archivos del proveedor deben ser un objeto.')
    campos = {'pdf': ('pdf', '.pdf'), 'xml': ('xml_firmado', '.xml'), 'cdr': ('cdr', '.zip')}
    preparados = []
    for tipo, contenido in archivos.items():
        if tipo not in campos or not isinstance(contenido, str) or len(contenido) > 15 * 1024 * 1024:
            raise ValidationError('Archivo del proveedor inválido o demasiado grande.')
        try:
            datos = base64.b64decode(contenido, validate=True)
        except (ValueError, binascii.Error):
            raise ValidationError('El archivo recibido no contiene base64 válido.')
        if not datos or len(datos) > 10 * 1024 * 1024:
            raise ValidationError('Cada archivo admite hasta 10 MB.')
        if tipo == 'pdf' and not datos.startswith(b'%PDF-'):
            raise ValidationError('El PDF recibido no tiene cabecera PDF.')
        if tipo == 'cdr' and not datos.startswith(b'PK'):
            raise ValidationError('El CDR debe enviarse como ZIP.')
        if tipo == 'xml' and not datos.lstrip().startswith(b'<'):
            raise ValidationError('El XML recibido no tiene formato XML.')
        campo, extension = campos[tipo]
        archivo = getattr(comprobante, campo)
        if archivo:
            with archivo.open('rb') as anterior:
                if hashlib.sha256(anterior.read()).digest() != hashlib.sha256(datos).digest():
                    raise ValidationError('El proveedor devolvió un archivo distinto al ya guardado. Requiere revisión.')
            continue
        preparados.append((campo, extension, datos))
    for campo, extension, datos in preparados:
        nombre = f'{comprobante.empresa_id}-{comprobante.pk}-{hashlib.sha256(datos).hexdigest()[:20]}{extension}'
        getattr(comprobante, campo).save(nombre, ContentFile(datos), save=False)
    if preparados:
        comprobante.save(update_fields=[c for c, _, _ in preparados] + ['actualizado_en'])
