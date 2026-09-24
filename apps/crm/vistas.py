"""Embudo, ficha del lead y conversión a cliente y pedido."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from apps.core.transacciones import vista_serializada
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.alcance import acotar
from apps.crm.models import ETAPAS_EMBUDO, Actividad, ArchivoLead, EstadoLead, FiltroGuardado, Lead, Prioridad
from apps.crm.servicios import (
    asignar_vendedor, convertir_en_pedido, convertir_en_tercero, marcar_ganado, marcar_perdido,
)
from apps.inventario.models import Almacen


class FormularioLead(forms.ModelForm):
    class Meta:
        model = Lead
        fields = (
            "nombre", "empresa_lead", "email", "telefono", "movil", "numero_documento",
            "direccion", "ciudad", "region", "pais", "idioma",
            "vendedor", "equipo_ventas", "prioridad", "etiquetas",
            "valor_estimado", "probabilidad", "cierre_esperado",
            "origen", "campania", "medio", "referido_por",
            "interes", "notas_internas",
        )
        widgets = {
            "interes": forms.Textarea(attrs={"rows": 3}),
            "notas_internas": forms.Textarea(attrs={"rows": 3}),
            "cierre_esperado": forms.DateInput(format='%Y-%m-%d', attrs={"type": "date"}),
        }

    etiquetas = forms.CharField(
        required=False, help_text="Separadas por coma: cliente caliente, inversionista",
    )

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendedor"].queryset = empresa.usuarios.filter(is_active=True)
        self.fields["vendedor"].required = False
        self.fields["vendedor"].help_text = "Vacío = se asigna solo al vendedor con menos leads abiertos"
        if self.instance and self.instance.pk:
            self.initial["etiquetas"] = ", ".join(self.instance.etiquetas or [])

    def clean_etiquetas(self):
        crudo = self.cleaned_data.get("etiquetas", "")
        return [e.strip() for e in crudo.split(",") if e.strip()]


class FormularioActividad(forms.ModelForm):
    class Meta:
        model = Actividad
        fields = ("tipo", "detalle", "programada_para", "hecha")
        widgets = {"programada_para": forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={"type": "datetime-local"}), "detalle": forms.Textarea(attrs={"rows": 2, "placeholder": "Qué se habló, qué sigue…"})}


class FormularioArchivo(forms.ModelForm):
    def clean_archivo(self):
        from apps.core.archivos import validar_adjunto
        return validar_adjunto(self.cleaned_data['archivo'])

    class Meta:
        model = ArchivoLead
        fields = ("archivo", "nombre")


def _mis_leads(request):
    return acotar(Lead.objects.all(), request, "crm", "vendedor").select_related("vendedor", "tercero")


#: Campos de filtro que entienden tanto el embudo como la lista, y que por lo
#: tanto se pueden guardar como filtro propio y reaplicar en cualquiera de las
#: dos vistas.
_FILTROS_COMUNES = ("q", "vendedor", "origen", "prioridad", "ciudad", "equipo", "etiqueta", "desde", "hasta")


def _filtrar_leads(leads, request):
    datos = {campo: request.GET.get(campo, "").strip() for campo in _FILTROS_COMUNES}
    if datos["q"]:
        leads = leads.filter(
            Q(nombre__icontains=datos["q"]) | Q(empresa_lead__icontains=datos["q"])
            | Q(email__icontains=datos["q"]) | Q(telefono__icontains=datos["q"])
            | Q(origen__icontains=datos["q"])
        )
    if datos["vendedor"]:
        leads = leads.filter(vendedor_id=datos["vendedor"])
    if datos["origen"]:
        leads = leads.filter(origen__icontains=datos["origen"])
    if datos["prioridad"]:
        leads = leads.filter(prioridad=datos["prioridad"])
    if datos["ciudad"]:
        leads = leads.filter(ciudad__icontains=datos["ciudad"])
    if datos["equipo"]:
        leads = leads.filter(equipo_ventas__icontains=datos["equipo"])
    if datos["etiqueta"]:
        leads = leads.filter(etiquetas__icontains=datos["etiqueta"])
    if datos["desde"]:
        leads = leads.filter(creado_en__date__gte=datos["desde"])
    if datos["hasta"]:
        leads = leads.filter(creado_en__date__lte=datos["hasta"])
    return leads, datos


def _contexto_filtros(request, datos, vista):
    """Lo que necesitan las plantillas para dibujar la barra de filtros y los
    filtros guardados: opciones de los selects y la query string actual, para
    que «Guardar este filtro» no tenga que adivinarla."""
    return {
        **{f"f_{k}": v for k, v in datos.items()},
        "vendedores": request.empresa.usuarios.filter(is_active=True),
        "prioridades": Prioridad.choices,
        "querystring_actual": request.GET.urlencode(),
        "filtros_guardados": FiltroGuardado.objects.filter(
            empresa=request.empresa, usuario=request.user, vista=vista
        ),
        "vista": vista,
        "url_vista": f"crm:{vista}",
    }


@login_required
@vista_serializada
def embudo(request):
    leads, datos = _filtrar_leads(_mis_leads(request), request)
    leads = leads.filter(estado__in=ETAPAS_EMBUDO)
    columnas = []
    for etapa in ETAPAS_EMBUDO:
        de_etapa = list(leads.filter(estado=etapa)[:40])
        columnas.append({
            "etapa": etapa, "nombre": EstadoLead(etapa).label, "leads": de_etapa,
            "total": leads.filter(estado=etapa).count(),
            "valor": leads.filter(estado=etapa).aggregate(v=Sum("valor_estimado"))["v"] or 0,
        })
    contexto = {"columnas": columnas, **_contexto_filtros(request, datos, "embudo")}
    return render(request, "crm/embudo.html", contexto)


@login_required
@vista_serializada
def lista(request):
    leads, datos = _filtrar_leads(_mis_leads(request), request)
    estado = request.GET.get("estado", "")
    if estado:
        leads = leads.filter(estado=estado)
    pagina = Paginator(leads, 40).get_page(request.GET.get("pagina"))
    qs_sin_pagina = request.GET.copy()
    qs_sin_pagina.pop("pagina", None)
    qs_sin_estado = qs_sin_pagina.copy()
    qs_sin_estado.pop("estado", None)
    contexto = {
        "page_obj": pagina, "estado": estado,
        "estados": EstadoLead.choices,
        "consulta": qs_sin_pagina.urlencode(),
        "querystring_sin_estado": qs_sin_estado.urlencode(),
        **_contexto_filtros(request, datos, "lista"),
    }
    return render(request, "crm/lista.html", contexto)


@login_required
@require_POST
def guardar_filtro(request):
    nombre = request.POST.get("nombre", "").strip()
    vista = request.POST.get("vista", "lista")
    querystring = request.POST.get("querystring", "")
    if nombre:
        FiltroGuardado.objects.create(
            empresa=request.empresa, usuario=request.user, vista=vista,
            nombre=nombre, querystring=querystring,
        )
        messages.success(request, f"Filtro «{nombre}» guardado.")
    return redirect(f"crm:{vista}")


@login_required
@require_POST
def eliminar_filtro(request, pk):
    filtro = get_object_or_404(
        FiltroGuardado, pk=pk, empresa=request.empresa, usuario=request.user
    )
    vista = filtro.vista
    filtro.delete()
    return redirect(f"crm:{vista}")


@login_required
@vista_serializada
def detalle(request, pk):
    lead = get_object_or_404(_mis_leads(request), pk=pk)
    if request.method == "POST":
        form = FormularioActividad(request.POST)
        if form.is_valid():
            actividad = form.save(commit=False)
            actividad.empresa = lead.empresa
            actividad.lead = lead
            actividad.usuario = request.user
            actividad.save()
            if lead.estado == EstadoLead.NUEVO:
                lead.estado = EstadoLead.CONTACTADO
                lead.save(update_fields=["estado", "actualizado_en"])
            messages.success(request, "Actividad registrada.")
            return redirect("crm:detalle", pk=pk)
    else:
        form = FormularioActividad()
    return render(request, "crm/detalle.html", {
        "lead": lead,
        "actividades": lead.actividades.select_related("usuario"),
        "form": form,
        "form_archivo": FormularioArchivo(),
        "archivos": lead.archivos.all(),
        "almacenes": Almacen.objects.filter(empresa=lead.empresa, activo=True),
        "etapas": [(e, EstadoLead(e).label) for e in ETAPAS_EMBUDO],
        "E": EstadoLead,
    })


@login_required
@vista_serializada
@require_POST
def subir_archivo(request, pk):
    lead = get_object_or_404(_mis_leads(request), pk=pk)
    form = FormularioArchivo(request.POST, request.FILES)
    if form.is_valid():
        archivo = form.save(commit=False)
        archivo.empresa = lead.empresa
        archivo.lead = lead
        archivo.subido_por = request.user
        archivo.save()
        messages.success(request, "Archivo adjuntado.")
    else:
        messages.error(request, "No se pudo adjuntar el archivo.")
    return redirect("crm:detalle", pk=pk)


@login_required
@vista_serializada
def editar(request, pk=None):
    lead = get_object_or_404(_mis_leads(request), pk=pk) if pk else None
    if request.method == "POST":
        form = FormularioLead(request.POST, instance=lead, empresa=request.empresa)
        if form.is_valid():
            nuevo = form.save(commit=False)
            nuevo.empresa = request.empresa
            nuevo.save()
            if not nuevo.vendedor:
                asignar_vendedor(nuevo)
            messages.success(request, f"Lead «{nuevo.nombre}» guardado" + (f", asignado a {nuevo.vendedor}." if nuevo.vendedor else "."))
            return redirect("crm:detalle", pk=nuevo.pk)
    else:
        form = FormularioLead(instance=lead, empresa=request.empresa)
    return render(request, "crm/editar.html", {"form": form, "lead": lead})


@login_required
@vista_serializada
@require_POST
def mover(request, pk):
    lead = get_object_or_404(_mis_leads(request), pk=pk)
    etapa = request.POST.get("etapa")
    movido = etapa in ETAPAS_EMBUDO and lead.esta_abierto
    if movido:
        lead.estado = etapa
        lead.save(update_fields=["estado", "actualizado_en"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": movido, "etapa": lead.estado})
    return redirect(request.POST.get("volver") or "crm:embudo")


@login_required
@vista_serializada
@require_POST
def a_cliente(request, pk):
    lead = get_object_or_404(_mis_leads(request), pk=pk)
    try:
        tercero = convertir_en_tercero(lead, numero_documento=request.POST.get("numero_documento", "").strip())
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("crm:detalle", pk=pk)
    messages.success(request, f"Cliente {tercero.razon_social} listo.")
    return redirect("crm:detalle", pk=pk)


@login_required
@vista_serializada
@require_POST
def a_pedido(request, pk):
    lead = get_object_or_404(_mis_leads(request), pk=pk)
    almacen = get_object_or_404(Almacen, pk=request.POST.get("almacen"), empresa=lead.empresa)
    try:
        pedido = convertir_en_pedido(lead, almacen, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("crm:detalle", pk=pk)
    messages.success(request, f"Cotización {pedido.numero} creada. Agrega las líneas.")
    return redirect("ventas:editar", pk=pedido.pk)


@login_required
@vista_serializada
@require_POST
def cerrar(request, pk):
    lead = get_object_or_404(_mis_leads(request), pk=pk)
    if request.POST.get("resultado") == "ganado":
        marcar_ganado(lead)
        messages.success(request, "Lead marcado como ganado.")
    else:
        marcar_perdido(lead, request.POST.get("motivo", ""))
        messages.info(request, "Lead marcado como perdido.")
    return redirect("crm:detalle", pk=pk)
