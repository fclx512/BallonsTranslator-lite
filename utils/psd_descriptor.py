"""Photoshop Action Manager descriptor serialization.

Port of Koharu's ``descriptor.rs``.

Descriptors are the structured metadata format used throughout Photoshop
files — in particular for the TySh (Type Tool) section that makes text
layers editable.

Each descriptor value has a 4-byte OSType signature:

+-----------------+----------+-----------------------------------------+
| Variant         | Signature| Binary format                           |
+-----------------+----------+-----------------------------------------+
| Text            | ``TEXT`` | Unicode string with padding             |
| Enum            | ``enum`` | Two 4-byte class IDs (type + value)     |
| Integer         | ``long`` | ``i32`` big-endian                      |
| Double          | ``doub`` | ``f64`` big-endian                      |
| UnitPixels      | ``UntF`` | ``#Pxl`` + ``f64`` big-endian           |
| Raw             | ``tdta`` | ``u32(len)`` + raw bytes                |
| Object          | ``Objc`` | Nested descriptor                       |
+-----------------+----------+-----------------------------------------+
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .psd_binary_writer import PsdBinaryWriter


class PsdDescriptorError(ValueError):
    """Raised on invalid descriptor data."""


# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------


@dataclass
class DescriptorObject:
    """A named, typed collection of key-value pairs."""

    name: str = ""
    class_id: str = ""
    items: List[DescriptorItem] = field(default_factory=list)

    def with_item(self, key: str, value: DescriptorValue) -> DescriptorObject:
        """Return self with an additional item (builder pattern)."""
        self.items.append(DescriptorItem(key=key, value=value))
        return self


@dataclass
class DescriptorItem:
    """A single key-value entry inside a ``DescriptorObject``."""

    key: str
    value: DescriptorValue


class DescriptorValue:
    """Typed descriptor value.  Use the static constructors to create."""

    # Internal variants, distinguished by a type tag
    def __init__(self, tag: str, payload: object) -> None:
        self._tag = tag
        self._payload = payload

    @staticmethod
    def text(s: str) -> DescriptorValue:
        """UTF-16 Unicode string."""
        return DescriptorValue("text", s)

    @staticmethod
    def enum(type_id: str, value: str) -> DescriptorValue:
        """Two class IDs (type + enumerated value)."""
        return DescriptorValue("enum", (type_id, value))

    @staticmethod
    def integer(i: int) -> DescriptorValue:
        """32-bit signed integer."""
        return DescriptorValue("int", i)

    @staticmethod
    def double(f: float) -> DescriptorValue:
        """64-bit floating point."""
        return DescriptorValue("double", f)

    @staticmethod
    def unit_pixels(f: float) -> DescriptorValue:
        """PSD pixel-unit value."""
        return DescriptorValue("unit_pixels", f)

    @staticmethod
    def raw(data: bytes) -> DescriptorValue:
        """Raw byte blob."""
        return DescriptorValue("raw", data)

    @staticmethod
    def object(obj: DescriptorObject) -> DescriptorValue:
        """Nested sub-descriptor."""
        return DescriptorValue("object", obj)


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------


def _validate_id(value: str) -> None:
    if not value:
        raise PsdDescriptorError("descriptor IDs must not be empty")
    if not value.isascii():
        raise PsdDescriptorError(
            f"descriptor IDs must be ASCII: {value!r}"
        )


def _validate_key(value: str) -> None:
    _validate_id(value)


# ------------------------------------------------------------------
# Writing
# ------------------------------------------------------------------


def write_versioned_descriptor(
    writer: PsdBinaryWriter,
    descriptor: DescriptorObject,
) -> None:
    """Write a versioned descriptor (version ``u32(16)`` + body)."""
    writer.write_u32(16)
    _write_descriptor_object(writer, descriptor)


def bounds_descriptor(
    class_id: str,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> DescriptorObject:
    """Create a bounds descriptor with the four pixel-unit edges."""
    return (
        DescriptorObject("", class_id)
        .with_item("Left", DescriptorValue.unit_pixels(left))
        .with_item("Top ", DescriptorValue.unit_pixels(top))
        .with_item("Rght", DescriptorValue.unit_pixels(right))
        .with_item("Btom", DescriptorValue.unit_pixels(bottom))
    )


def _write_descriptor_object(
    writer: PsdBinaryWriter,
    descriptor: DescriptorObject,
) -> None:
    _validate_id(descriptor.class_id)
    writer.write_unicode_string_with_padding(descriptor.name)
    writer.write_ascii_or_class_id(descriptor.class_id)
    writer.write_u32(len(descriptor.items))

    for item in descriptor.items:
        _validate_key(item.key)
        writer.write_ascii_or_class_id(item.key)
        _write_descriptor_value(writer, item.value)


def _write_descriptor_value(
    writer: PsdBinaryWriter,
    value: DescriptorValue,
) -> None:
    tag = value._tag
    payload = value._payload

    if tag == "text":
        writer.write_signature("TEXT")
        writer.write_unicode_string_with_padding(payload)

    elif tag == "enum":
        type_id, val = payload
        _validate_id(type_id)
        _validate_id(val)
        writer.write_signature("enum")
        writer.write_ascii_or_class_id(type_id)
        writer.write_ascii_or_class_id(val)

    elif tag == "int":
        writer.write_signature("long")
        writer.write_i32(payload)

    elif tag == "double":
        writer.write_signature("doub")
        writer.write_f64(payload)

    elif tag == "unit_pixels":
        writer.write_signature("UntF")
        writer.write_signature("#Pxl")
        writer.write_f64(payload)

    elif tag == "raw":
        writer.write_signature("tdta")
        writer.write_u32(len(payload))
        writer.write_bytes(payload)

    elif tag == "object":
        writer.write_signature("Objc")
        _write_descriptor_object(writer, payload)

    else:
        raise PsdDescriptorError(f"unknown descriptor value tag: {tag}")
