# Migración de facturación electrónica de Odoo 13 a Odoo 19

## Estado

La fuente de referencia está en el árbol legado `remsina_odoov13/addons/`. No debe
instalarse ni añadirse directamente al `addons_path`. La implementación saneada y
migrada se mantiene como el módulo reutilizable `custom-addons/fact_pe_fe/`.

La base operativa `inacion_db` no se utilizará para pruebas destructivas. La
instalación y actualización del módulo se validará primero en una base temporal.

Validación técnica actual: el módulo está instalado en `inacion_db` y se
instala y actualiza también en una base aislada sobre Odoo 19 Community. Se
migraron las vistas, reportes, datos de unidades, impuestos, acciones
programadas y el punto de entrada de publicación de facturas a
`account.move.action_post`. Se validaron la carga del módulo, la creación de
una factura en borrador y el flujo de interfaz de detracción; no se ha enviado
ningún documento a SUNAT.

La migración aún **no está certificada funcionalmente de extremo a extremo**.
En particular, quedan por verificar XML/UBL, firma, CDR, resumen, baja, GRE,
permisos y cada escenario contable con datos y credenciales de prueba. La
instalación correcta no demuestra por sí sola conformidad con SUNAT.

Pendiente antes de producción: configurar certificados y credenciales SOL de
prueba, asignar el plan contable e impuestos del cliente, y ejecutar los escenarios
controlados de CPE y GRE contra SUNAT.

## Alcance funcional encontrado

- Factura y boleta UBL 2.1.
- Notas de crédito y débito.
- Resumen diario de boletas.
- Comunicación de baja.
- Consulta de estado y descarga de CDR/XML.
- Detracciones y cuotas de pago.
- Guía de remisión electrónica del remitente.
- Envío directo a SUNAT y código parcial para OSE.
- Reportes PDF y envío por correo.

## Dependencias

Dependencias oficiales disponibles en Odoo 19 Community:

- `account`, `sale_management`, `stock`, `sale_stock` y `purchase`.
- `l10n_latam_base`, `l10n_pe`, `account_debit_note` y
  `account_tax_python`.

Dependencias antiguas que no deben copiarse sin adaptación:

- El complemento legado de ubicaciones: Odoo 19 ya incluye `res.city` y
  `l10n_pe.res.city.district`; se reemplazarán los campos legados.
- La consulta externa de RUC/DNI: integración opcional con un proveedor externo;
  no es requisito técnico para emitir CPE.
- La integración de tipo de cambio: funcionalidad opcional y acoplada a `web_notify` de
  Odoo 13; se separará del núcleo de facturación.
- `web_notify`: su cliente web de Odoo 13 no es compatible con OWL/Odoo 19.

Dependencia Python pendiente en la imagen: `signxml`. Debe fijarse en el build y
probarse junto con las versiones de `cryptography` y `lxml` de la imagen.

## Hallazgos que impiden una copia directa

- Aproximadamente 25 000 líneas y más de 160 archivos en el módulo principal.
- Vistas con `attrs`, `states`, `<tree>` y selectores heredados de Odoo 13.
- Uso extensivo de `account.move.type`; en Odoo 19 corresponde `move_type`.
- Acciones con `view_mode="tree,form"`; Odoo 19 usa vistas `list`.
- Métodos y campos contables, de impuestos, pagos, secuencias, reportes y stock
  modificados entre ambas versiones.
- JavaScript/QWeb legado que debe migrarse a los assets y componentes actuales.
- Catálogos y ubicaciones duplicados respecto de `l10n_pe` 19.
- Solicitudes HTTP sin tiempo límite consistente ni manejo uniforme de errores.
- Código de demostración con una clave privada, certificado, usuario y contraseña
  incrustados. Esos valores no se copiarán al módulo migrado.
- Credenciales SOL y claves privadas almacenadas como texto normal en modelos; el
  diseño de seguridad debe revisarse antes de producción.

## Estrategia

1. Sanear la fuente y construir un grafo mínimo de dependencias.
2. Portar modelos y seguridad sobre las APIs de Odoo 19.
3. Reutilizar los catálogos y campos de `l10n_pe` siempre que sean equivalentes.
4. Portar las vistas, acciones, tareas programadas, assets y reportes.
5. Actualizar firma XML, transporte HTTP y procesamiento de CDR.
6. Adaptar GRE a la plataforma REST vigente de SUNAT.
7. Añadir pruebas unitarias para XML, firma y respuestas, sin credenciales reales.
8. Instalar en una base temporal y ejecutar escenarios completos.
9. Validar con credenciales de prueba/controladas antes de habilitar producción.

## Criterios de aceptación

- Instalación limpia en Odoo 19 Community sin errores ni modificaciones al core.
- Factura, boleta, notas, resumen y baja generan XML válido.
- Firma verificable con un certificado de prueba.
- Respuestas CDR aceptadas, observadas y rechazadas quedan trazables.
- GRE remitente genera el XML y completa el flujo REST vigente.
- Ningún secreto o certificado queda versionado ni aparece en logs.
- Reintentos idempotentes: no duplican el envío de un CPE.
- Pruebas funcionales ejecutadas antes de instalar en `inacion_db`.
