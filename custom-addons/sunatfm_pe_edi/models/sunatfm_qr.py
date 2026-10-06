"""QR helpers for SUNAT printable representations."""

import base64
import io

import qrcode


def qr_png_base64(value):
    """Return a PNG QR using SUNAT's required Q correction level."""
    if not value:
        return False
    code = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
        box_size=5,
        border=2,
    )
    code.add_data(value)
    code.make(fit=True)
    buffer = io.BytesIO()
    code.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
