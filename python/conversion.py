# python/conversion.py
#!/usr/bin/env python3
# ==============================================================================
# conversion.py – Version 1.0.0
#   - binary_file_to_base64_file : binary → .basechunk text file
#   - base64_file_to_binary_file : .basechunk text file → binary
# ==============================================================================

import base64


def binary_file_to_base64_file(input_path: str, output_path: str) -> None:
    """Read a binary file and write a .basechunk text file containing its
    standard base64 representation."""
    with open(input_path, 'rb') as f:
        data = f.read()
    encoded = base64.b64encode(data).decode('ascii')
    with open(output_path, 'w', encoding='ascii') as f:
        f.write(encoded)


def base64_file_to_binary_file(input_path: str, output_path: str) -> None:
    """Read a .basechunk text file and write back the original binary file."""
    with open(input_path, 'r', encoding='ascii') as f:
        encoded = f.read()
    data = base64.b64decode(encoded)
    with open(output_path, 'wb') as f:
        f.write(data)