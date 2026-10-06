import re

from stdnum.pe import ruc as pe_ruc

# The prefix check keeps the local message useful while ``stdnum`` applies the
# official Peruvian RUC check digit (mod-11).  A string with eleven digits is
# not necessarily a valid RUC, which was the source of false positives here.
RUC_PATTERN = re.compile(r"^(?:10|15|16|17|20)\d{9}$")


def is_valid_ruc(value):
    """Return whether *value* is a valid Peruvian RUC.

    ``stdnum.pe.ruc`` is already a dependency of Odoo's ``base_vat`` module,
    which is loaded by the Peruvian localization.  Keeping the check here
    avoids accepting arbitrary eleven-digit values before the XML is built.
    """
    value = (value or "").strip().replace(" ", "")
    return bool(RUC_PATTERN.fullmatch(value) and pe_ruc.is_valid(value))


def validate_customer_invoice(move):
    """Return business validation messages for the first CPE scope.

    This is intentionally a local preflight check, not a substitute for SUNAT's
    schema and business-rule validation.
    """
    errors = []
    document_code = move.l10n_latam_document_type_id.code
    if document_code not in {"01", "03", "07"}:
        errors.append("Seleccione Factura (01), Boleta (03) o Nota de crédito electrónica (07).")
    if not is_valid_ruc(move.company_id.partner_id.vat):
        errors.append("El RUC de la empresa emisora no es válido (11 dígitos y dígito verificador SUNAT).")
    if document_code in {"01", "07"} and not is_valid_ruc(move.partner_id.vat):
        errors.append("Una factura electrónica requiere un RUC válido del cliente (11 dígitos y dígito verificador SUNAT).")
    if document_code == "07":
        origin = move.reversed_entry_id
        if not origin:
            errors.append("La nota de crédito debe estar vinculada al comprobante que modifica.")
        elif origin.l10n_latam_document_type_id.code not in {"01", "03"}:
            errors.append("La nota de crédito debe referenciar una factura (01) o boleta (03).")
    if not move.currency_id.name:
        errors.append("La factura debe tener una moneda definida.")

    invoice_lines = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
    for line in invoice_lines:
        if not line.product_uom_id:
            errors.append(f"La línea '{line.name}' debe tener una unidad de medida.")
        if not line.tax_ids:
            errors.append(f"La línea '{line.name}' debe tener al menos un impuesto SUNAT.")
        elif not line.tax_ids.filtered("l10n_pe_edi_tax_code"):
            errors.append(f"El impuesto de la línea '{line.name}' no tiene código SUNAT configurado.")
    return errors
