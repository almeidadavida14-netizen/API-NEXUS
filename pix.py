"""Local BR Code (Pix) payload and QR generation helpers."""

from __future__ import annotations

import re
import unicodedata
from io import BytesIO

import qrcode


MAX_PIX_AMOUNT_CENTS = 999_999_999_999


def parse_brl_cents(raw_value: str) -> int:
    value = raw_value.strip().upper().replace("R$", "").replace(" ", "")
    if not value or value.count(",") > 1:
        raise ValueError("Informe um valor em reais, como 100,00.")

    if "," in value:
        whole, fraction = value.split(",", 1)
        if not re.fullmatch(r"(?:\d+|\d{1,3}(?:\.\d{3})+)", whole):
            raise ValueError("Use um valor válido, como 100,00 ou 1.250,00.")
        if not re.fullmatch(r"\d{1,2}", fraction):
            raise ValueError("Use no máximo duas casas decimais.")
        whole_digits = whole.replace(".", "")
    else:
        if value.count(".") > 1:
            raise ValueError("Use vírgula para separar os centavos, como 100,00.")
        whole, separator, fraction = value.partition(".")
        if not whole.isdigit() or (separator and not re.fullmatch(r"\d{1,2}", fraction)):
            raise ValueError("Informe um valor em reais, como 100,00.")
        whole_digits = whole

    amount = int(whole_digits) * 100 + int((fraction + "00")[:2])
    if amount <= 0:
        raise ValueError("O valor da premiação precisa ser maior que zero.")
    if amount > MAX_PIX_AMOUNT_CENTS:
        raise ValueError("O valor excede o limite aceito pelo QR Code Pix.")
    return amount


def format_brl(amount_cents: int | None) -> str:
    if amount_cents is None:
        return "Não definido"
    whole, cents = divmod(amount_cents, 100)
    return f"R$ {whole:,}".replace(",", ".") + f",{cents:02d}"


def _ascii_receiver_field(
    raw_value: str,
    *,
    field_name: str,
    max_length: int,
    uppercase: bool,
) -> str:
    normalized = unicodedata.normalize("NFKD", raw_value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"[^A-Za-z0-9 .,'&()/-]", " ", ascii_value)
    ascii_value = " ".join(ascii_value.split())
    if uppercase:
        ascii_value = ascii_value.upper()
    if not ascii_value:
        raise ValueError(f"Informe {field_name}.")
    if len(ascii_value.encode("ascii")) > max_length:
        raise ValueError(
            f"{field_name.capitalize()} deve ter no máximo {max_length} caracteres."
        )
    return ascii_value


def _tlv(tag: str, value: str) -> str:
    encoded_length = len(value.encode("utf-8"))
    if encoded_length > 99:
        raise ValueError("Campo Pix maior que o limite do BR Code.")
    return f"{tag}{encoded_length:02d}{value}"


def crc16_ccitt_false(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def build_pix_payload(
    pix_key: str,
    recipient_name: str,
    recipient_city: str,
    amount_cents: int | None,
) -> str:
    key = pix_key.strip()
    if not key or any(ord(char) < 32 for char in key):
        raise ValueError("Informe uma chave Pix válida.")
    try:
        encoded_key = key.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("A chave Pix deve usar caracteres ASCII.") from error
    if len(encoded_key) > 77:
        raise ValueError("A chave Pix excede o limite de 77 bytes do BR Code.")
    if amount_cents is not None and not 0 < amount_cents <= MAX_PIX_AMOUNT_CENTS:
        raise ValueError("O valor não é aceito pelo QR Code Pix.")

    name = _ascii_receiver_field(
        recipient_name,
        field_name="nome do titular",
        max_length=25,
        uppercase=False,
    )
    city = _ascii_receiver_field(
        recipient_city,
        field_name="cidade",
        max_length=15,
        uppercase=True,
    )
    pix_account = _tlv("00", "br.gov.bcb.pix") + _tlv("01", key)

    payload = _tlv("00", "01") + _tlv("26", pix_account)
    payload += _tlv("52", "0000") + _tlv("53", "986")
    if amount_cents is not None:
        whole, cents = divmod(amount_cents, 100)
        amount_text = f"{whole}.{cents:02d}"
        if len(amount_text) > 13:
            raise ValueError("O valor excede o limite aceito pelo QR Code Pix.")
        payload += _tlv("54", amount_text)
    payload += _tlv("58", "BR")
    payload += _tlv("59", name)
    payload += _tlv("60", city)
    payload += _tlv("62", _tlv("05", "***"))

    payload_with_crc_tag = payload + "6304"
    checksum = crc16_ccitt_false(payload_with_crc_tag.encode("ascii"))
    return f"{payload_with_crc_tag}{checksum:04X}"


def generate_pix_qr_png(payload: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
