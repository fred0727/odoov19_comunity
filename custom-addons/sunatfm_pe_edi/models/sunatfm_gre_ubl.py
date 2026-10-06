"""Minimal UBL 2.1 DespatchAdvice builder for SUNAT GRE remitente."""

from datetime import datetime
import re
from zoneinfo import ZoneInfo

from lxml import etree

CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
NSMAP = {"cbc": CBC, "cac": CAC, "ext": EXT}


def _el(parent, namespace, name, value=None, **attrs):
    node = etree.SubElement(parent, "{%s}%s" % (namespace, name), **attrs)
    if value is not None:
        node.text = str(value)
    return node


def _party(parent, tag, partner, vat_code="6"):
    party = _el(parent, CAC, tag)
    ident = _el(party, CAC, "PartyIdentification")
    _el(ident, CBC, "ID", partner.vat or "-", schemeID=vat_code)
    legal = _el(party, CAC, "PartyLegalEntity")
    _el(legal, CBC, "RegistrationName", partner.name or "-")
    return party


def _unit_code(uom):
    """Return the SUNAT/UNECE unit code instead of Odoo's display name."""
    name = (uom.name or "").strip().lower() if uom else ""
    mapping = {
        "unidades": "NIU", "unidad": "NIU", "und": "NIU", "uds": "NIU",
        "kilogramos": "KGM", "kilogramo": "KGM", "kg": "KGM",
        "metros": "MTR", "metro": "MTR", "m": "MTR",
        "litros": "LTR", "litro": "LTR", "l": "LTR",
    }
    return mapping.get(name, "NIU")


def _plate_for_sunat(plate):
    """Keep Odoo's user-friendly plate format but send SUNAT's compact form."""
    return re.sub(r"[^A-Za-z0-9]", "", (plate or "")).upper()


def _dni_for_sunat(dni):
    """SUNAT accepts a conductor DNI as exactly eight numeric digits."""
    return re.sub(r"\D", "", dni or "")


def build_gre_draft(picking):
    picking.ensure_one()
    issue_now = datetime.now(ZoneInfo("America/Lima"))
    start_date = picking.sunatfm_gre_fecha_inicio or picking.scheduled_date.date()
    if not picking.sunatfm_gre_number:
        raise ValueError("La guía no tiene serie y correlativo GRE asignados.")
    if not picking.move_ids.filtered(lambda move: move.product_id and move.quantity):
        raise ValueError("La guía debe incluir al menos una línea con cantidad trasladada.")
    company = picking.company_id.partner_id
    customer = picking.partner_id
    if not customer:
        raise ValueError("La guía debe tener un destinatario.")
    if not picking.sunatfm_gre_ubigeo_partida or not picking.sunatfm_gre_ubigeo_llegada:
        raise ValueError("La guía requiere ubigeo de partida y de llegada.")
    if not picking.sunatfm_gre_direccion_partida or not picking.sunatfm_gre_direccion_llegada:
        raise ValueError("La guía requiere dirección de partida y de llegada.")
    if not picking.sunatfm_gre_peso_bruto or picking.sunatfm_gre_peso_bruto <= 0:
        raise ValueError("La guía requiere un peso bruto total mayor que cero.")
    if start_date < issue_now.date():
        raise ValueError(
            "La Fecha de inicio de traslado debe ser igual o posterior a la fecha de emisión "
            "de la GRE (%s)." % issue_now.date()
        )
    motivo_description = (picking.sunatfm_gre_observations or "").strip()
    if picking.sunatfm_gre_motivo_traslado == "13":
        if not motivo_description:
            raise ValueError(
                "Para el motivo de traslado Otros debe detallar el motivo en Observaciones GRE."
            )
        if not 3 <= len(motivo_description) <= 100:
            raise ValueError(
                "La descripción para el motivo de traslado Otros debe tener entre 3 y 100 caracteres."
            )
    if picking.sunatfm_gre_modalidad == "02" and not (
        picking.sunatfm_gre_vehicle_id or picking.sunatfm_gre_vehiculo_placa
    ):
        raise ValueError("Para transporte privado debe indicar el vehículo y su placa.")
    if picking.sunatfm_gre_modalidad == "02" and not picking.sunatfm_gre_conductor_id:
        raise ValueError("Para transporte privado debe indicar el conductor principal.")
    conductor_dni = _dni_for_sunat(picking.sunatfm_gre_conductor_id.vat)
    if picking.sunatfm_gre_modalidad == "02" and not conductor_dni:
        raise ValueError(
            "El conductor principal '%s' no tiene DNI en su campo Nro. documento."
            % picking.sunatfm_gre_conductor_id.display_name
        )
    if picking.sunatfm_gre_modalidad == "02" and len(conductor_dni) != 8:
        raise ValueError("El DNI del conductor principal debe contener exactamente 8 dígitos.")
    if (
        picking.sunatfm_gre_modalidad == "02"
        and picking.sunatfm_gre_conductor_id.l10n_latam_identification_type_id.l10n_pe_vat_code != "1"
    ):
        raise ValueError("El conductor principal debe tener el tipo de documento DNI.")
    if picking.sunatfm_gre_modalidad == "02" and not picking.sunatfm_gre_conductor_licencia:
        raise ValueError("Para transporte privado debe indicar la licencia del conductor principal.")
    if picking.sunatfm_gre_modalidad == "01" and not picking.sunatfm_gre_transportista_id:
        raise ValueError("Para transporte público debe indicar la empresa transportista.")
    root = etree.Element(
        "{urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2}DespatchAdvice",
        nsmap={None: "urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2", **NSMAP},
    )
    extensions = _el(root, EXT, "UBLExtensions")
    _el(_el(extensions, EXT, "UBLExtension"), EXT, "ExtensionContent")
    _el(root, CBC, "UBLVersionID", "2.1")
    _el(root, CBC, "CustomizationID", "2.0")
    _el(root, CBC, "ID", picking.sunatfm_gre_number)
    _el(root, CBC, "IssueDate", issue_now.date())
    _el(root, CBC, "IssueTime", issue_now.strftime("%H:%M:%S"))
    _el(root, CBC, "DespatchAdviceTypeCode", "09")
    if picking.sunatfm_gre_observations:
        _el(root, CBC, "Note", picking.sunatfm_gre_observations)
    # UBL 2.1 requires AdditionalDocumentReference before the parties.  It is
    # tempting to put it next to Shipment, but SUNAT then rejects the XML at
    # schema level (in particular when a reference is used with motive 13).
    for reference in picking.sunatfm_gre_reference_ids:
        issuer_ruc = re.sub(r"\D", "", reference.issuer_ruc or company.vat or "")
        if len(issuer_ruc) != 11:
            raise ValueError(
                "El documento relacionado %s debe tener el RUC de su emisor con 11 dígitos."
                % reference.document_number
            )
        related = _el(root, CAC, "AdditionalDocumentReference")
        _el(related, CBC, "ID", reference.document_number)
        _el(
            related, CBC, "DocumentTypeCode", reference.document_type,
            listAgencyName="PE:SUNAT",
            listName="Documento relacionado al transporte",
            listURI="urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo61",
        )
        if reference.description:
            _el(related, CBC, "DocumentDescription", reference.description)
        issuer_party = _el(related, CAC, "IssuerParty")
        issuer_identification = _el(issuer_party, CAC, "PartyIdentification")
        _el(
            issuer_identification, CBC, "ID", issuer_ruc,
            schemeID="6",
            schemeName="Documento de Identidad",
            schemeAgencyName="PE:SUNAT",
            schemeURI="urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo06",
        )
    supplier = _el(root, CAC, "DespatchSupplierParty")
    _party(supplier, "Party", company)
    customer_party = _el(root, CAC, "DeliveryCustomerParty")
    _party(customer_party, "Party", customer)
    shipment = _el(root, CAC, "Shipment")
    _el(shipment, CBC, "ID", picking.sunatfm_gre_number)
    _el(shipment, CBC, "HandlingCode", picking.sunatfm_gre_motivo_traslado or "13")
    if picking.sunatfm_gre_motivo_traslado == "13":
        _el(shipment, CBC, "HandlingInstructions", motivo_description)
    _el(shipment, CBC, "GrossWeightMeasure", "%.3f" % picking.sunatfm_gre_peso_bruto, unitCode=picking.sunatfm_gre_unidad_peso or "KGM")
    if picking.sunatfm_gre_numero_bultos:
        _el(shipment, CBC, "TotalTransportHandlingUnitQuantity", picking.sunatfm_gre_numero_bultos)
    # ShipmentStage must precede Delivery in the UBL Shipment sequence.
    stage = _el(shipment, CAC, "ShipmentStage")
    _el(stage, CBC, "ID", "1")
    _el(stage, CBC, "TransportModeCode", picking.sunatfm_gre_modalidad or "02")
    transit = _el(stage, CAC, "TransitPeriod")
    _el(transit, CBC, "StartDate", start_date)
    if picking.sunatfm_gre_modalidad == "01" and picking.sunatfm_gre_transportista_id:
        _party(stage, "CarrierParty", picking.sunatfm_gre_transportista_id)
    sunat_plate = _plate_for_sunat(picking.sunatfm_gre_vehiculo_placa)
    if picking.sunatfm_gre_modalidad == "02" and sunat_plate:
        means = _el(stage, CAC, "TransportMeans")
        road_transport = _el(means, CAC, "RoadTransport")
        _el(road_transport, CBC, "LicensePlateID", sunat_plate)
    if picking.sunatfm_gre_modalidad == "02" and picking.sunatfm_gre_conductor_id:
        # A private transfer has a driver, not a carrier/transport company.
        # SUNAT rejects CarrierParty for transport mode 02 (error 3347).
        driver_name = (picking.sunatfm_gre_conductor_id.name or "").strip().split(None, 1)
        driver = _el(stage, CAC, "DriverPerson")
        _el(driver, CBC, "ID", conductor_dni, schemeID="1")
        _el(driver, CBC, "FirstName", driver_name[0] if driver_name else "-")
        _el(driver, CBC, "FamilyName", driver_name[1] if len(driver_name) > 1 else "-")
        _el(driver, CBC, "JobTitle", "Principal")
        identity = _el(driver, CAC, "IdentityDocumentReference")
        _el(identity, CBC, "ID", picking.sunatfm_gre_conductor_licencia)
    delivery = _el(shipment, CAC, "Delivery")
    destination = _el(delivery, CAC, "DeliveryAddress")
    _el(destination, CBC, "ID", picking.sunatfm_gre_ubigeo_llegada or customer.zip or "")
    destination_line = _el(destination, CAC, "AddressLine")
    _el(destination_line, CBC, "Line", picking.sunatfm_gre_direccion_llegada or customer.street or "")
    origin = _el(delivery, CAC, "Despatch")
    origin_address = _el(origin, CAC, "DespatchAddress")
    _el(origin_address, CBC, "ID", picking.sunatfm_gre_ubigeo_partida or company.zip or "")
    origin_line = _el(origin_address, CAC, "AddressLine")
    _el(origin_line, CBC, "Line", picking.sunatfm_gre_direccion_partida or company.street or "")
    transport = _el(shipment, CAC, "TransportHandlingUnit")
    if sunat_plate:
        transport_vehicle = _el(transport, CAC, "TransportEquipment")
        _el(transport_vehicle, CBC, "ID", sunat_plate)
    for index, move in enumerate(picking.move_ids.filtered(lambda line: line.product_id and line.quantity), 1):
        line = _el(root, CAC, "DespatchLine")
        _el(line, CBC, "ID", index)
        _el(line, CBC, "DeliveredQuantity", "%.6f" % move.quantity, unitCode=_unit_code(move.product_uom))
        order_line = _el(line, CAC, "OrderLineReference")
        _el(order_line, CBC, "LineID", index)
        item = _el(line, CAC, "Item")
        _el(item, CBC, "Description", move.product_id.display_name)
        identification = _el(item, CAC, "SellersItemIdentification")
        _el(identification, CBC, "ID", move.product_id.default_code or str(move.product_id.id))
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)
