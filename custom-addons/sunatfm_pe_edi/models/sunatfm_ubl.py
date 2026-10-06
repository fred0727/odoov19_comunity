"""Small, dependency-free UBL 2.1 draft builder.

This module deliberately generates a draft only. XML signing, transport and CDR
handling will be implemented separately after conformance tests.
"""

from datetime import datetime
import re

from lxml import etree


UBL_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CREDIT_NOTE_NS = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
CBC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
EXT_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
SAC_NS = "urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"
NSMAP = {"cbc": CBC_NS, "cac": CAC_NS, "ext": EXT_NS, "sac": SAC_NS, "ds": DS_NS}


def _root_nsmap(document_namespace):
    """Use the document UBL namespace as the XML default namespace."""
    return {None: document_namespace, **NSMAP}


def _cbc(parent, name, value, **attributes):
    element = etree.SubElement(parent, f"{{{CBC_NS}}}{name}", **attributes)
    element.text = str(value or "")
    return element


def _party(parent, role, partner, establishment_code=None):
    party_wrapper = etree.SubElement(parent, f"{{{CAC_NS}}}{role}")
    party = etree.SubElement(party_wrapper, f"{{{CAC_NS}}}Party")
    identification = etree.SubElement(party, f"{{{CAC_NS}}}PartyIdentification")
    _cbc(
        identification,
        "ID",
        partner.vat or "",
        schemeID="6" if partner.vat else "1",
        schemeName="Documento de Identidad",
        schemeAgencyName="PE:SUNAT",
    )
    legal_entity = etree.SubElement(party, f"{{{CAC_NS}}}PartyLegalEntity")
    _cbc(legal_entity, "RegistrationName", partner.name or "")
    if role == "AccountingSupplierParty":
        address = etree.SubElement(legal_entity, f"{{{CAC_NS}}}RegistrationAddress")
        # SUNAT 3030 requires the establishment/local annex code.  0000 is
        # the code for the principal establishment when no branch is used.
        _cbc(address, "AddressTypeCode", establishment_code or "0000")


def _append_signature(root, company):
    """Add the UBL commercial signature reference used by SUNAT."""
    signature = etree.SubElement(root, f"{{{CAC_NS}}}Signature")
    _cbc(signature, "ID", company.partner_id.vat or "SIGNATURE")
    signatory = etree.SubElement(signature, f"{{{CAC_NS}}}SignatoryParty")
    identification = etree.SubElement(signatory, f"{{{CAC_NS}}}PartyIdentification")
    _cbc(identification, "ID", company.partner_id.vat or "")
    party_name = etree.SubElement(signatory, f"{{{CAC_NS}}}PartyName")
    _cbc(party_name, "Name", company.partner_id.name or "")
    attachment = etree.SubElement(signature, f"{{{CAC_NS}}}DigitalSignatureAttachment")
    reference = etree.SubElement(attachment, f"{{{CAC_NS}}}ExternalReference")
    _cbc(reference, "URI", "#placeholder")


def _tax_values(tax):
    """Return SUNAT tax identifiers from Odoo's localized tax fields."""
    code = tax.l10n_pe_edi_tax_code or "1000"
    category = tax.l10n_pe_edi_unece_category or "S"
    names = {"1000": ("IGV", "VAT"), "9995": ("EXP", "FRE"),
             "9996": ("GRA", "FRE"), "9997": ("EXO", "VAT"),
             "9998": ("INA", "FRE")}
    name, tax_type = names.get(code, (tax.name or "IGV", "VAT"))
    return code, category, name, tax_type, float(tax.amount or 0.0)


def _tax_exemption_reason(code):
    """Return the SUNAT catalog 07 IGV-affectation code for a tax scheme.

    ``TaxScheme/ID`` and ``TaxExemptionReasonCode`` are two different SUNAT
    catalogs.  The former identifies the tax (for example, 9997 for an
    exempt IGV operation); the latter identifies the IGV affectation (20 for
    an exempt onerous operation).  Sending 9997 in this element leads to
    SUNAT validation error 4252.
    """
    return {
        "1000": "10",  # Gravado - operación onerosa
        "1016": "17",  # Gravado - IVAP
        "2000": "10",  # ISC on a taxable operation
        "7152": "10",  # ICBPER on a taxable operation
        "9995": "40",  # Exportación
        "9996": "21",  # Exonerado - transferencia gratuita
        "9997": "20",  # Exonerado - operación onerosa
        "9998": "30",  # Inafecto - operación onerosa
        "9999": "30",  # Otros tributos: operación inafecta
    }.get(code, "10")


def _append_tax_total(parent, lines, currency):
    taxes = lines.mapped("tax_ids").filtered("l10n_pe_edi_tax_code")
    total = etree.SubElement(parent, f"{{{CAC_NS}}}TaxTotal")
    amount = sum(
        (0.0 if any(_tax_values(item)[0] == "9996" for item in tax_ids(line)) else line.price_subtotal)
        * (tax.amount or 0.0) / 100.0
        for line in lines for tax in tax_ids(line)
    )
    _cbc(total, "TaxAmount", f"{amount:.2f}", currencyID=currency)
    for tax in taxes:
        base = sum(
            line.price_subtotal for line in lines
            if tax in line.tax_ids and not any(_tax_values(item)[0] == "9996" for item in tax_ids(line))
        )
        tax_amount = base * (tax.amount or 0.0) / 100.0
        code, category, name, tax_type, rate = _tax_values(tax)
        subtotal = etree.SubElement(total, f"{{{CAC_NS}}}TaxSubtotal")
        _cbc(subtotal, "TaxableAmount", f"{base:.2f}", currencyID=currency)
        _cbc(subtotal, "TaxAmount", f"{tax_amount:.2f}", currencyID=currency)
        tax_category = etree.SubElement(subtotal, f"{{{CAC_NS}}}TaxCategory")
        _cbc(tax_category, "ID", category)
        _cbc(tax_category, "Percent", f"{rate:.2f}")
        _cbc(tax_category, "TaxExemptionReasonCode", _tax_exemption_reason(code))
        scheme = etree.SubElement(tax_category, f"{{{CAC_NS}}}TaxScheme")
        _cbc(scheme, "ID", code)
        _cbc(scheme, "Name", name)
        _cbc(scheme, "TaxTypeCode", tax_type)
    return total


def tax_ids(line):
    return line.tax_ids.filtered("l10n_pe_edi_tax_code")


def _document_identifier(move):
    """Return the SUNAT document ID without Odoo's display-only spacing."""
    number = (move.l10n_latam_document_number or "").strip()
    match = re.fullmatch(r"([A-Za-z0-9]+)[- ](\d+)", number)
    if not match:
        return move.name
    series, correlativo = match.groups()
    prefix = move.l10n_latam_document_type_id.doc_code_prefix or "F"
    return f"{prefix}{series}-{correlativo}"


def _unit_code(line):
    """Return a conservative UN/ECE unit code for a product UoM.

    Odoo's display name is not itself a SUNAT unit code.  The complete
    catalogue mapping belongs to the fiscal catalogue phase; these common
    values keep the local draft deterministic until that catalogue exists.
    """
    name = (line.product_uom_id.name or "").strip().lower()
    if getattr(line.product_id, "type", False) == "service":
        return "ZZ"
    return {"unidad": "NIU", "unidades": "NIU", "und": "NIU", "kg": "KGM", "kilogramo": "KGM", "litro": "LTR", "l": "LTR"}.get(name, "NIU")


def _tax_reference(move):
    for line in move.invoice_line_ids.filtered(lambda item: item.display_type == "product"):
        for tax in line.tax_ids:
            if tax.l10n_pe_edi_tax_code:
                return tax
    return None


def _detraction_configuration(move):
    company_bank_account = move.company_id._sunatfm_get_detraction_bank_account()
    company_account = company_bank_account.acc_number if company_bank_account else getattr(move.company_id, "sunatfm_edi_detraction_account", False)
    company_payment_code = getattr(move.company_id, "sunatfm_edi_detraction_payment_means_code", False)
    if company_account or company_payment_code:
        return type("CompanyDetractionConfiguration", (), {
            "detraction_account": company_account,
            "detraction_payment_means_code": company_payment_code or "001",
        })()
    env = getattr(move, "env", None)
    if not env:
        return None
    return env["sunatfm.pe.edi.configuration"].search(
        [("company_id", "=", move.company_id.id), ("active", "=", True)], limit=1
    )


def _append_detraction_payment(root, move):
    detraction = getattr(move, "sunatfm_edi_detraction_id", None)
    if not detraction or getattr(move, "sunatfm_edi_operation_type", "0101") not in {"1001", "1002", "1003", "1004"}:
        return
    amount = abs(getattr(move, "sunatfm_edi_detraction_amount", 0.0))
    configuration = _detraction_configuration(move)
    means_code = configuration.detraction_payment_means_code if configuration else "001"

    payment_means = etree.SubElement(root, f"{{{CAC_NS}}}PaymentMeans")
    _cbc(payment_means, "ID", "Detraccion")
    _cbc(payment_means, "PaymentMeansCode", means_code)
    if configuration and configuration.detraction_account:
        account = etree.SubElement(payment_means, f"{{{CAC_NS}}}PayeeFinancialAccount")
        _cbc(account, "ID", configuration.detraction_account)

    payment_terms = etree.SubElement(root, f"{{{CAC_NS}}}PaymentTerms")
    # SUNAT validates the detracción group using this exact relationship:
    # ``ID = Detraccion`` identifies the group and ``PaymentMeansID`` carries
    # the Catalog 54 code of the good/service.  Reversing both values makes
    # SUNAT read ``010`` as a group ID and returns error 3127.
    _cbc(payment_terms, "ID", "Detraccion")
    _cbc(payment_terms, "PaymentMeansID", detraction.code)
    _cbc(payment_terms, "PaymentPercent", f"{detraction.rate:.2f}")
    _cbc(payment_terms, "Amount", f"{amount:.2f}", currencyID="PEN")


def _payment_installments(move):
    """Return the original Odoo payment schedule for a customer invoice.

    Odoo creates one receivable journal item per payment-term maturity.  Those
    entries are the authoritative schedule after posting and map directly to
    SUNAT's ``Cuota001``, ``Cuota002``, ... payment terms.
    """
    receivable_lines = move.line_ids.filtered(
        lambda line: line.account_id.account_type == "asset_receivable" and not line.display_type
    ).sorted(key=lambda line: (line.date_maturity or move.invoice_date_due or move.invoice_date, line.id))
    installments = []
    for line in receivable_lines:
        amount = abs(line.amount_currency) or abs(line.balance)
        if amount:
            installments.append((line.date_maturity or move.invoice_date_due or move.invoice_date, amount))
    if not installments:
        # A posted customer invoice normally always has its receivable item,
        # but retain a deterministic fallback for older/custom journal setups.
        installments.append((move.invoice_date_due or move.invoice_date, abs(move.amount_total)))
    return installments


def _append_payment_terms(root, move):
    """Emit SUNAT payment form and, for credit, its mandatory installments."""
    product_lines = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
    if product_lines and all(
        any(_tax_values(tax)[0] == "9996" for tax in tax_ids(line))
        for line in product_lines
    ):
        return
    installments = _payment_installments(move)
    issue_date = move.invoice_date
    is_credit = bool(
        move.invoice_date_due
        and issue_date
        and move.invoice_date_due != issue_date
    )

    payment_terms = etree.SubElement(root, f"{{{CAC_NS}}}PaymentTerms")
    _cbc(payment_terms, "ID", "FormaPago")
    _cbc(payment_terms, "PaymentMeansID", "Credito" if is_credit else "Contado")

    if is_credit:
        _cbc(payment_terms, "Amount", f"{abs(move.amount_total):.2f}", currencyID=move.currency_id.name)
        for sequence, (due_date, amount) in enumerate(installments, 1):
            installment = etree.SubElement(root, f"{{{CAC_NS}}}PaymentTerms")
            _cbc(installment, "ID", "FormaPago")
            _cbc(installment, "PaymentMeansID", f"Cuota{sequence:03d}")
            _cbc(installment, "Amount", f"{amount:.2f}", currencyID=move.currency_id.name)
            _cbc(installment, "PaymentDueDate", due_date)


def build_invoice_draft(move, draft=True):
    """Return a minimal UBL 2.1 invoice draft for a posted customer invoice.

    The result is intentionally marked as a draft artifact and must not be sent
    until the upcoming validation, signing and transport stages are complete.
    """
    root = etree.Element(f"{{{UBL_NS}}}Invoice", nsmap=_root_nsmap(UBL_NS))
    # UBL requires the extension container before all business content.  SUNAT
    # uses this single extension for the XMLDSig signature and fiscal metadata.
    extensions = etree.SubElement(root, f"{{{EXT_NS}}}UBLExtensions")
    extension = etree.SubElement(extensions, f"{{{EXT_NS}}}UBLExtension")
    extension_content = etree.SubElement(extension, f"{{{EXT_NS}}}ExtensionContent")
    # The UBL extension content model requires at least one element from a
    # namespace other than the extension namespace.  This SUNAT namespace
    # container is intentionally kept small; legends and catalog aggregates
    # will be populated by the fiscal-data layer as they are implemented.
    etree.SubElement(extension_content, f"{{{SAC_NS}}}AdditionalInformation")
    _cbc(root, "UBLVersionID", "2.1")
    _cbc(root, "CustomizationID", "2.0")
    operation_type = getattr(move, "sunatfm_edi_operation_type", False) or "0101"
    detraction = getattr(move, "sunatfm_edi_detraction_id", None) if operation_type in {"1001", "1002", "1003", "1004"} else None
    _cbc(
        root,
        "ProfileID",
        operation_type,
        schemeName="Tipo de operación",
        schemeAgencyName="PE:SUNAT",
        schemeURI="urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo51",
    )
    _cbc(root, "ID", _document_identifier(move))
    _cbc(root, "IssueDate", move.invoice_date or datetime.now().date())
    _cbc(
        root,
        "InvoiceTypeCode",
        move.l10n_latam_document_type_id.code or "01",
        listID=operation_type,
        # Catalog 01 metadata required by SUNAT.  The longer label used here
        # previously generated CDR observation 4252 even though the invoice
        # itself was accepted.
        listName="Tipo de Documento",
        listAgencyName="PE:SUNAT",
        listURI="urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo01",
    )
    if draft:
        _cbc(root, "Note", "BORRADOR - NO ENVIAR A SUNAT")
    if detraction:
        legend = "Operación sujeta al Sistema de Pago de Obligaciones Tributarias con el Gobierno Central"
        if detraction.annex == "II" and detraction.code == "001":
            legend += " - Recursos hidrobiológicos"
        _cbc(root, "Note", legend, languageLocaleID="2006")
    _cbc(root, "DocumentCurrencyCode", move.currency_id.name)

    _append_signature(root, move.company_id)

    _party(
        root,
        "AccountingSupplierParty",
        move.company_id.partner_id,
        getattr(move.company_id, "sunatfm_edi_establishment_code", None),
    )
    _party(root, "AccountingCustomerParty", move.partner_id)

    _append_detraction_payment(root, move)

    _append_payment_terms(root, move)

    # SUNAT's retention of IGV is a global deduction (catalog 53 code 62),
    # not an item-level discount.  Sending code 02 under InvoiceLine causes
    # observation 4268 even though the XML passes the generic UBL XSD.
    if getattr(move, "sunatfm_edi_withholding_applicable", False):
        allowance_charge = etree.SubElement(root, f"{{{CAC_NS}}}AllowanceCharge")
        _cbc(allowance_charge, "ChargeIndicator", "false")
        _cbc(allowance_charge, "AllowanceChargeReasonCode", "62")
        _cbc(
            allowance_charge,
            "MultiplierFactorNumeric",
            f"{move.sunatfm_edi_withholding_rate / 100:.5f}",
        )
        _cbc(
            allowance_charge,
            "Amount",
            f"{move.sunatfm_edi_withholding_amount:.2f}",
            currencyID=move.currency_id.name,
        )
        _cbc(
            allowance_charge,
            "BaseAmount",
            f"{move.sunatfm_edi_withholding_base:.2f}",
            currencyID=move.currency_id.name,
        )

    lines = move.invoice_line_ids.filtered(lambda item: item.display_type == "product")
    free_lines = lines.filtered(
        lambda item: any(_tax_values(tax)[0] == "9996" for tax in tax_ids(item))
    )
    is_free_operation = bool(lines) and len(free_lines) == len(lines)
    _append_tax_total(root, lines, move.currency_id.name)

    monetary_total = etree.SubElement(root, f"{{{CAC_NS}}}LegalMonetaryTotal")
    total_untaxed = 0.0 if is_free_operation else move.amount_untaxed
    total_amount = 0.0 if is_free_operation else move.amount_total
    _cbc(monetary_total, "LineExtensionAmount", f"{total_untaxed:.2f}", currencyID=move.currency_id.name)
    _cbc(monetary_total, "TaxInclusiveAmount", f"{total_amount:.2f}", currencyID=move.currency_id.name)
    _cbc(monetary_total, "PayableAmount", f"{total_amount:.2f}", currencyID=move.currency_id.name)

    for sequence, line in enumerate(lines, 1):
        invoice_line = etree.SubElement(root, f"{{{CAC_NS}}}InvoiceLine")
        _cbc(invoice_line, "ID", sequence)
        _cbc(invoice_line, "InvoicedQuantity", line.quantity, unitCode=_unit_code(line))
        line_is_free = any(_tax_values(tax)[0] == "9996" for tax in tax_ids(line))
        line_extension = 0.0 if line_is_free else line.price_subtotal
        _cbc(invoice_line, "LineExtensionAmount", f"{line_extension:.2f}", currencyID=move.currency_id.name)

        quantity = line.quantity or 1.0
        net_price = line.price_subtotal / quantity
        line_tax = next(iter(tax_ids(line)), None)
        if line_tax:
            code, category, name, tax_type, rate = _tax_values(line_tax)
            pricing = etree.SubElement(invoice_line, f"{{{CAC_NS}}}PricingReference")
            alternative = etree.SubElement(pricing, f"{{{CAC_NS}}}AlternativeConditionPrice")
            # In Odoo, ``price_unit`` can be tax-included depending on the
            # tax configuration. SUNAT expects AlternativeConditionPrice to
            # be the unit amount actually paid (with IGV), not a second
            # application of the tax rate.
            # SUNAT's beta validator rejects a positive referential amount
            # when the tax catalog is not consistently propagated by the
            # selected tax. For code 9996 keep the free-operation price at
            # zero; the actual charged amount is already zero as required.
            gross_price = 0.0 if code == "9996" else line.price_total / quantity
            _cbc(alternative, "PriceAmount", f"{gross_price:.2f}", currencyID=move.currency_id.name)
            _cbc(alternative, "PriceTypeCode", "01" if code not in {"9996"} else "02")

            line_tax_total = etree.SubElement(invoice_line, f"{{{CAC_NS}}}TaxTotal")
            line_tax_amount = 0.0 if line_is_free else line.price_subtotal * rate / 100.0
            _cbc(line_tax_total, "TaxAmount", f"{line_tax_amount:.2f}", currencyID=move.currency_id.name)
            subtotal = etree.SubElement(line_tax_total, f"{{{CAC_NS}}}TaxSubtotal")
            _cbc(subtotal, "TaxableAmount", f"{0.0 if line_is_free else line.price_subtotal:.2f}", currencyID=move.currency_id.name)
            _cbc(subtotal, "TaxAmount", f"{line_tax_amount:.2f}", currencyID=move.currency_id.name)
            tax_category = etree.SubElement(subtotal, f"{{{CAC_NS}}}TaxCategory")
            _cbc(tax_category, "ID", category)
            _cbc(tax_category, "Percent", f"{rate:.2f}")
            _cbc(tax_category, "TaxExemptionReasonCode", _tax_exemption_reason(code))
            tax_scheme = etree.SubElement(tax_category, f"{{{CAC_NS}}}TaxScheme")
            _cbc(tax_scheme, "ID", code)
            _cbc(tax_scheme, "Name", name)
            _cbc(tax_scheme, "TaxTypeCode", tax_type)
        item = etree.SubElement(invoice_line, f"{{{CAC_NS}}}Item")
        _cbc(item, "Description", line.name or line.product_id.display_name)
        tax_reference = line_tax
        if tax_reference:
            classified_tax = etree.SubElement(item, f"{{{CAC_NS}}}ClassifiedTaxCategory")
            _cbc(classified_tax, "ID", tax_reference.l10n_pe_edi_unece_category or "S")
            tax_scheme = etree.SubElement(classified_tax, f"{{{CAC_NS}}}TaxScheme")
            _cbc(tax_scheme, "ID", tax_reference.l10n_pe_edi_tax_code)
            _cbc(tax_scheme, "Name", _tax_values(tax_reference)[2])
            _cbc(tax_scheme, "TaxTypeCode", _tax_values(tax_reference)[3])
        price = etree.SubElement(invoice_line, f"{{{CAC_NS}}}Price")
        # For a free transfer (SUNAT code 9996), the amount in cac:Price is
        # the amount actually charged and must be zero.  The referential value
        # remains in PricingReference/AlternativeConditionPrice above.
        reported_price = 0.0 if line_tax and _tax_values(line_tax)[0] == "9996" else net_price
        _cbc(price, "PriceAmount", f"{reported_price:.2f}", currencyID=move.currency_id.name)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)


def build_credit_note_draft(move, draft=True):
    """Build SUNAT's UBL 2.1 CreditNote from an Odoo reversal."""
    origin = move.reversed_entry_id
    if not origin:
        raise ValueError("La nota de crédito debe estar vinculada al comprobante que modifica.")

    # The tax, totals, party and line structures are common to both CPEs.
    root = etree.fromstring(build_invoice_draft(move, draft=draft))
    credit_root = etree.Element(
        f"{{{CREDIT_NOTE_NS}}}CreditNote",
        nsmap=_root_nsmap(CREDIT_NOTE_NS),
    )
    credit_root.attrib.update(root.attrib)
    credit_root.extend(list(root))
    root = credit_root
    reason_code = getattr(move, "sunatfm_edi_note_reason_code", False) or "01"
    for element in root.iter():
        if element.tag == f"{{{CBC_NS}}}InvoiceTypeCode":
            element.tag = f"{{{CBC_NS}}}CreditNoteTypeCode"
            element.text = reason_code
            element.attrib.clear()
            element.attrib.update({
                "listAgencyName": "PE:SUNAT",
                "listName": "Tipo de nota de crédito",
                "listURI": "urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo09",
            })
        elif element.tag == f"{{{CAC_NS}}}InvoiceLine":
            element.tag = f"{{{CAC_NS}}}CreditNoteLine"
        elif element.tag == f"{{{CBC_NS}}}InvoicedQuantity":
            element.tag = f"{{{CBC_NS}}}CreditedQuantity"

    # Payment terms describe the payment of a sale.  A credit note adjusts a
    # previously issued CPE and SUNAT's CPE 07 rules do not accept invoice
    # values such as ``Contado`` or ``Cuota001`` in this document.  They are
    # therefore deliberately omitted rather than copied from the invoice
    # draft used as the common base.
    for element in list(root):
        if element.tag in {
            f"{{{CAC_NS}}}PaymentMeans",
            f"{{{CAC_NS}}}PaymentTerms",
        }:
            root.remove(element)

    # SUNAT's CreditNote sequence is: DocumentCurrencyCode,
    # DiscrepancyResponse, BillingReference, Signature,
    # AccountingSupplierParty.  The invoice builder already placed the
    # commercial signature before the supplier, therefore references must be
    # inserted *before* that signature, not immediately before the supplier.
    signature = root.find(f"{{{CAC_NS}}}Signature")
    if signature is None:
        raise ValueError("No se pudo ubicar la firma comercial en la nota de crédito UBL.")
    position = list(root).index(signature)
    reason_text = dict(move._sunatfm_edi_credit_note_reason_selection()).get(
        reason_code, "Anulación de la operación"
    )
    discrepancy = etree.Element(f"{{{CAC_NS}}}DiscrepancyResponse")
    _cbc(discrepancy, "ReferenceID", _document_identifier(origin))
    _cbc(discrepancy, "ResponseCode", reason_code)
    _cbc(discrepancy, "Description", reason_text)
    root.insert(position, discrepancy)
    position += 1
    billing_reference = etree.Element(f"{{{CAC_NS}}}BillingReference")
    reference = etree.SubElement(billing_reference, f"{{{CAC_NS}}}InvoiceDocumentReference")
    _cbc(reference, "ID", _document_identifier(origin))
    _cbc(reference, "DocumentTypeCode", origin.l10n_latam_document_type_id.code or "01")
    root.insert(position, billing_reference)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)
