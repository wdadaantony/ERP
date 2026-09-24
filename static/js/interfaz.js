/* Mejoras de presentación compartidas, sin alterar los formularios de negocio. */
(() => {
  const menu = document.querySelector('.menu-toggle');
  const lateral = document.querySelector('.lateral');
  const cerrar = () => {
    document.body.classList.remove('menu-abierto');
    menu?.setAttribute('aria-expanded', 'false');
  };
  menu?.addEventListener('click', () => {
    const abierto = document.body.classList.toggle('menu-abierto');
    menu.setAttribute('aria-expanded', String(abierto));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.body.classList.contains('menu-abierto')) {
      cerrar();
      menu.focus();
    }
  });
  document.addEventListener('click', event => {
    if (!lateral?.contains(event.target) && !menu?.contains(event.target)) cerrar();
  });
  document.querySelectorAll('.lateral a.activo').forEach(link => link.setAttribute('aria-current', 'page'));
  document.querySelectorAll('table.datos').forEach(table => {
    if (table.closest('.envuelve')) return;
    const contenedor = document.createElement('div');
    contenedor.className = 'envuelve';
    contenedor.tabIndex = 0;
    contenedor.setAttribute('role', 'region');
    contenedor.setAttribute('aria-label', 'Tabla de datos, desplazamiento horizontal');
    table.before(contenedor);
    contenedor.append(table);
  });
})();
