"""Los archivos privados se descargan exclusivamente por vistas con permiso."""
from pathlib import Path
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from apps.core.alcance import acotar


def validar_adjunto(archivo):
    if archivo.size > 10 * 1024 * 1024:
        raise ValidationError('El archivo no puede superar 10 MB.')
    if Path(archivo.name).suffix.lower() not in {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.txt', '.csv', '.xlsx', '.docx'}:
        raise ValidationError('Formato no permitido. Usa PDF, imagen, TXT, CSV, XLSX o DOCX.')
    return archivo


@login_required
def descargar(request, tipo, pk):
    if not request.empresa:
        raise PermissionDenied
    if tipo == 'lead':
        from apps.crm.models import ArchivoLead
        if not request.user.has_perm('crm.view_lead'):
            raise PermissionDenied
        objeto = get_object_or_404(acotar(ArchivoLead.objects.all(), request, 'crm', 'lead__vendedor'), pk=pk)
        archivo = objeto.archivo
    elif tipo in ('pdf', 'xml', 'cdr'):
        from apps.facturacion.vistas import _mis_comprobantes
        if not request.user.has_perm('facturacion.view_comprobante'):
            raise PermissionDenied
        objeto = get_object_or_404(_mis_comprobantes(request), pk=pk)
        archivo = getattr(objeto, {'pdf': 'pdf', 'xml': 'xml_firmado', 'cdr': 'cdr'}[tipo])
    else:
        raise Http404
    if not archivo:
        raise Http404
    try:
        respuesta = FileResponse(archivo.open('rb'), as_attachment=True, filename=Path(archivo.name).name)
    except FileNotFoundError:
        raise Http404
    respuesta['Cache-Control'] = 'private, no-store'
    respuesta['X-Content-Type-Options'] = 'nosniff'
    return respuesta
