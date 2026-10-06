{
    "name": "SUNAT Peru E-Invoice",
    "summary": "Electronic invoicing and delivery guides for Peru",
    "version": "19.0.1.4.0",
    "category": "Accounting/Localizations",
    "author": "Freddy Aldair Muñoz Huasasquiche",
    "support": "freddymunoz.dev@gmail.com",
    "license": "LGPL-3",
    "price": 85.00,
    "currency": "USD",
    "description": """
SUNAT Peru E-Invoice adds electronic invoicing workflows for Odoo 19 Community.

Features include local validation, UBL 2.1 generation, XML signature support,
SUNAT Beta transport, electronic document tracking, QR codes, detracciones,
withholdings and electronic delivery guides.

This module does not include SUNAT credentials, certificates or third-party
PSE/OSE services. Customers must configure their own company credentials and
certificates and validate their fiscal setup with SUNAT.
""",
    "depends": [
        "account",
        "account_debit_note",
        "l10n_pe",
        "sale",
        "purchase",
        "stock",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/sunatfm_detraction_data.xml",
        "data/sunatfm_detraction_rates_update.xml",
        "data/sunatfm_gre_sequence.xml",
        "data/sunatfm_report_paperformat.xml",
        "data/sunatfm_report_assets.xml",
        "views/account_move_views.xml",
        "views/partner_views.xml",
        "views/sunatfm_void_wizard_views.xml",
        "views/product_views.xml",
        "views/sunatfm_detraction_views.xml",
        "views/sunatfm_edi_configuration_views.xml",
        "views/sunatfm_company_views.xml",
        "views/sunatfm_bank_views.xml",
        "views/sunatfm_vehicle_views.xml",
        "views/stock_picking_views.xml",
        "views/sunatfm_edi_document_views.xml",
        "views/sunatfm_edi_menus.xml",
        "views/sunatfm_report_layout.xml",
        "views/sale_order_report.xml",
        "views/sale_order_views.xml",
        "views/purchase_order_report.xml",
        "views/electronic_document_reports.xml",
        "views/sunatfm_delivery_report.xml",
    ],
    "application": False,
    "installable": True,
}
