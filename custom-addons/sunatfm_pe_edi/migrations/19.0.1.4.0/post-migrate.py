from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Apply the current SUNAT Annex III rates to existing databases."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    rates = {
        "sunatfm_detraction_001": 12.0,
        "sunatfm_detraction_003": 12.0,
        "sunatfm_detraction_005": 12.0,
        "sunatfm_detraction_010": 12.0,
    }
    for xml_id, rate in rates.items():
        record = env.ref(f"sunatfm_pe_edi.{xml_id}", raise_if_not_found=False)
        if record:
            record.write({"rate": rate})
