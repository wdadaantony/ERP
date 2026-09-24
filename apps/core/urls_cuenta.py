"""Inicio y cierre de sesión."""
from django.contrib.auth import views as auth
from django.urls import path
from django.urls import reverse_lazy

app_name = "cuenta"

urlpatterns = [
    path('recuperar/', auth.PasswordResetView.as_view(template_name='cuenta/recuperar.html',
        email_template_name='cuenta/recuperar_email.txt', subject_template_name='cuenta/recuperar_asunto.txt',
        success_url=reverse_lazy('cuenta:recuperar_enviado')), name='recuperar'),
    path('recuperar/enviado/', auth.PasswordResetDoneView.as_view(template_name='cuenta/recuperar_enviado.html'), name='recuperar_enviado'),
    path('restablecer/<uidb64>/<token>/', auth.PasswordResetConfirmView.as_view(template_name='cuenta/restablecer.html',
        success_url=reverse_lazy('cuenta:restablecido')), name='restablecer'),
    path('restablecido/', auth.PasswordResetCompleteView.as_view(template_name='cuenta/restablecido.html'), name='restablecido'),
    path(
        "entrar/",
        auth.LoginView.as_view(template_name="cuenta/entrar.html", redirect_authenticated_user=True),
        name="login",
    ),
    path("salir/", auth.LogoutView.as_view(), name="logout"),
]
