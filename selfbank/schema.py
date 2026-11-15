"""Comprobador de JSON Schema (subconjunto) sin dependencias.

Soporta: type (incluida la lista de tipos), enum, pattern, minimum, maximum, minLength, maxLength,
required, properties, additionalProperties: false, items, minItems, maxItems.
Basta para validar el contrato de la API y las consultas del modelo.
"""

import re

TYPES = {
    "string": str, "number": (int, float), "integer": int,
    "null": type(None), "object": dict, "array": list, "boolean": bool,
}


def type_ok(value, name) -> bool:
    if name in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, TYPES[name])


def check(value, schema: dict, path: str = "$") -> list:
    """Devuelve la lista de errores (vacía si el valor cumple el esquema)."""
    errors = []
    declared = schema.get("type")
    if declared is not None:
        names = [declared] if isinstance(declared, str) else declared
        if not any(type_ok(value, n) for n in names):
            return [f"{path}: tipo {type(value).__name__} no permitido {names}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} no está en {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} no cumple {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: longitud {len(value)} < {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longitud {len(value)} > {schema['maxLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < mínimo {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > máximo {schema['maximum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        errors += [f"{path}: falta {key}" for key in schema.get("required", []) if key not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{path}: campo no permitido {key}" for key in value if key not in props]
        for key, item in value.items():
            if key in props:
                errors += check(item, props[key], f"{path}.{key}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: {len(value)} elementos < {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: {len(value)} elementos > {schema['maxItems']}")
        if "items" in schema:
            for index, item in enumerate(value):
                errors += check(item, schema["items"], f"{path}[{index}]")
    return errors
