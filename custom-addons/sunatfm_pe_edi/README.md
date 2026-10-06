# Facturación electrónica Perú — SUNAT

Base limpia para una integración propia de Odoo 19 Community con SUNAT.

## Estado

La versión actual crea configuración por empresa, una cola auditable de
documentos y un borrador local UBL 2.1 para facturas contabilizadas. Incluye
prechequeo fiscal local, validación contra el XSD UBL 2.1 publicado por SUNAT,
firma XMLDSig con PKCS#12 en memoria y transporte SOAP `sendBill` para Beta.
El envío productivo permanece bloqueado hasta completar la conformidad fiscal y
las pruebas controladas.

## Principios

- Sin dependencia de PSE/OSE ni de módulos de terceros.
- Sin reutilizar código, vistas, reportes o recursos del módulo legado.
- Implementación contra la documentación pública de SUNAT y las APIs de Odoo 19.
- Las credenciales y certificados se incorporarán únicamente con el mecanismo de
  firma y seguridad correspondiente.

## Próximas etapas

1. Modelo fiscal, catálogos y validaciones CPE.
2. Completar agregados SUNAT específicos: leyendas, catálogos y tipos de CPE.
3. Validar fixtures reales en Beta, CDR, reintentos y trazabilidad.
4. Resumen, bajas, detracciones completas y GRE remitente 2.0.

## Alcance actual de CPE

El prechequeo local exige RUC válido del emisor, tipo 01/03, RUC del receptor
para facturas, moneda, unidades de medida y códigos SUNAT en los impuestos. El
resultado no reemplaza las validaciones de esquema ni reglas de negocio de SUNAT.

## Detracciones por producto

El catálogo `Detracciones SUNAT` incluye un catálogo base de los Anexos I, II y
III de SUNAT, con código, descripción y porcentaje. En los Anexos I y II la
aplicación depende además de la subpartida y de las condiciones de la
operación; por ello no se debe interpretar esta tabla como el catálogo
completo de clasificación arancelaria. Cada producto puede seleccionar una
detracción en su ficha; al agregarlo a una factura, el módulo calcula el
monto y propone la operación 1001. Las tasas del Anexo III fueron contrastadas
con el cuadro vigente de SUNAT el 14/09/2026: mantenimiento y reparación de
bienes muebles, intermediación laboral, otros servicios empresariales y demás
servicios gravados con IGV usan 12%; arrendamiento, movimiento de carga,
comisión mercantil, fabricación por encargo y transporte de personas usan 10%;
contratos de construcción usan 4%. La página histórica de apéndices de SUNAT
todavía muestra algunos porcentajes anteriores, por lo que prevalece el cuadro
vigente publicado en el resumen del Sistema de Detracciones. Las tasas están
versionadas como datos iniciales y deben revisarse si SUNAT publica una
modificación. La configuración
de empresa debe incluir
la cuenta de detracciones del Banco de la Nación; el XML preliminar incorpora
`PaymentMeans` y `PaymentTerms` con código, tasa e importe en PEN.

## Transporte Beta

El módulo ya prepara el archivo ZIP y el payload base64 requerido por `sendBill`,
y puede extraer un CDR ZIP. La llamada SOAP permanece desactivada hasta
configurar el certificado y las credenciales SOL de pruebas.

## Firma digital

El firmador acepta un certificado PKCS#12 en memoria, genera XMLDSig enveloped
con RSA-SHA256 y permite verificar la firma. No se guardan certificados ni
contraseñas en archivos del módulo o Git. La interfaz de configuración los
restringe a administradores para las pruebas locales/Beta; producción deberá
usar un gestor de secretos.

El módulo incluye los XSD UBL 2.1 publicados por SUNAT y bloquea el envío si el
XML no supera la validación de esquema local. La firma se inserta dentro de la
primera `ext:UBLExtension`, como requiere el formato UBL de SUNAT, y se verifica
localmente antes de una prueba de transporte.

Cuando una factura tiene detracción, el XML incluye el código del bien o
servicio en `cac:PaymentTerms/cbc:ID`, el medio de pago, la cuenta del Banco de
la Nación, porcentaje, importe y la leyenda SUNAT `languageLocaleID="2006"`.
También se valida que todas las líneas estén sujetas al mismo concepto, que la
operación supere S/ 700 y que el tipo de operación corresponda al catálogo.

## Fuentes oficiales revisadas

- [SUNAT: Sistema de Detracciones del IGV](https://emprender.sunat.gob.pe/principales-impuestos/impuesto-general-las-ventas-igv/sistema-detracciones-igv)
- [SUNAT: Apéndices del Sistema de Detracciones](https://orientacion.sunat.gob.pe/apendices-del-sistema-de-detracciones)
- [SUNAT: Servicios web para comprobantes electrónicos](https://orientacion.sunat.gob.pe/guias-manuales-y-servicios-web)
- [SUNAT: Catálogo de tipos de operación (código 51)](https://www.sunat.gob.pe/legislacion/superin/2023/anexo-000005-2023.pdf)

Estas fuentes validan tasas, códigos de operación y endpoints. La conformidad
completa de cada CPE/GRE todavía requiere pruebas oficiales en Beta con el
certificado y las credenciales de la empresa; generar XML localmente no la
garantiza.
